"""极简 OpenAI 兼容 LLM 客户端与工具调用循环（Spec §2.5, §5 PR4）。

零新依赖：只用 stdlib `urllib` POST `{base_url}/chat/completions`。代价是自己追
协议变化——接受，因为工具表只有 6 个、字段用量是协议的最小公约数（Y11）。

两条硬边界：
1. 密钥只从 `config/agent.json` 的 `api_key_env` 指名环境变量读，绝不落盘/打印/回传；
2. 本模块是仓库唯一出网路径（§2.5 Y2-r19）：发请求前必须过 `assert_egress_boundary`。

配置或密钥缺失时不抛异常，降级为本地纯指示模式，且降级显式可辨（返回消息带
`degraded` 与原因）——静默换一条假回答是家规禁项。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline import paths
from pipeline.agent.tools import (
    ToolContext,
    assert_egress_boundary,
    build_tool_schemas,
    execute_tool,
)

DEFAULT_MAX_ITERATIONS = 10
REQUEST_TIMEOUT = 60


class LLMError(RuntimeError):
    """网络/HTTP/协议层失败（领域异常，不是裸 urllib 报错）。"""


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


def load_llm_config(root: Path | None = None) -> LLMConfig | None:
    """读 config/agent.json + 指名环境变量；无配置 / JSON 损坏 / 字段不全 / 密钥空 → None。"""
    cfg_file = Path(root or paths.ROOT) / "config" / "agent.json"
    if not cfg_file.exists():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    base_url = str(data.get("base_url") or "").strip()
    model = str(data.get("model") or "").strip()
    env_name = str(data.get("api_key_env") or "").strip()
    if not (base_url and model and env_name):
        return None
    api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        return None
    return LLMConfig(base_url=base_url, model=model, api_key=api_key)


def local_directive_message(scope: str, reason: str) -> dict[str, Any]:
    """降级输出：打开文件 + 打印 checklist，不假装有 LLM 在场。清单随 scope 变。"""
    if scope == "creative":
        steps = (
            "  1. 01-topic.md：番 / 类型 / 锚点 / 张力 是否填齐（张力是唯一编辑判断，不许代填）；\n"
            "  2. 02-script.draft.md：写完跑 `python -m pipeline.check_script` 至全绿；\n"
        )
    else:
        steps = (
            f"  1. 本 scope（{scope}）的清单见 docs/WORKFLOW.md 与对应 runbook，逐条人工核对；\n"
            "  2. 机器步骤用 `ava <期> /run <命令>` 推进（先回显、按 y 执行）；\n"
        )
    return {
        "role": "assistant",
        "degraded": True,
        "degrade_reason": reason,
        "content": (
            f"[降级模式·本地纯指示] LLM 不可用（{reason}）。\n"
            f"本回合不生成内容，请人工按 {scope} scope 的清单逐条走：\n"
            f"{steps}"
            "  3. 到达人工停机点（02.5 / 03.5 / 05 / 09）必须停下等人拍板。"
        ),
    }


def chat_complete(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    config: LLMConfig | None = None,
    root: Path | None = None,
    timeout: int = REQUEST_TIMEOUT,
    scope: str = "creative",
) -> dict[str, Any]:
    """发一次 chat/completions，返回 `choices[0].message`。

    配置缺失 → 降级消息（不抛）；网络/HTTP/协议失败 → `LLMError`。
    """
    cfg = config if config is not None else load_llm_config(root)
    if cfg is None:
        return local_directive_message(scope, "缺少 config/agent.json 或环境变量密钥")

    payload: dict[str, Any] = {"model": cfg.model, "messages": messages}
    if tools:
        payload["tools"] = tools
    assert_egress_boundary(cfg.endpoint, payload)  # 出网前最后一道断言

    request = urllib.request.Request(
        cfg.endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", errors="replace")
        raise LLMError(f"LLM HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"LLM 请求失败: {exc}") from None
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 响应非 JSON: {exc}") from None

    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 响应缺少 choices[0].message: {exc}") from None


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    """工具参数是 JSON 字符串；解析失败当空参数（由实现层自己报缺参）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_tool_loop(
    messages: list[dict[str, Any]],
    *,
    ctx: ToolContext | None = None,
    scope: str = "creative",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    config: LLMConfig | None = None,
    root: Path | None = None,
    approve: Callable[[str, dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """多轮 tool_calls 状态机，**轮数上限是硬闸**（§2.5 B3-r5）：到顶就停交人。

    返回 `{messages, final, iterations, stopped, tool_calls_made}`，stopped ∈
    done / max_iterations / degraded。approve 在每次工具执行前问人，返回 False 则把
    「人类拒绝」当结果回喂模型。
    """
    context = ctx or ToolContext(scope=scope, root=root)
    # root 未显式给定时用 ctx 的（会话绑定的仓库根），否则 ctx.root 会被静默忽略
    effective_root = root if root is not None else context.root
    cfg = config if config is not None else load_llm_config(effective_root)

    if cfg is None:
        final = local_directive_message(context.scope, "缺少 config/agent.json 或环境变量密钥")
        return {"messages": list(messages), "final": final, "iterations": 0,
                "stopped": "degraded", "tool_calls_made": 0}

    tools = build_tool_schemas(context.scope, effective_root)
    convo = list(messages)
    tool_calls_made = 0

    for iteration in range(1, max_iterations + 1):
        reply = chat_complete(convo, tools=tools or None, config=cfg, scope=context.scope)
        convo.append(reply)

        calls = reply.get("tool_calls") or []
        if not calls:
            return {"messages": convo, "final": reply, "iterations": iteration,
                    "stopped": "done", "tool_calls_made": tool_calls_made}

        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            args = _parse_tool_args(function.get("arguments"))
            tool_calls_made += 1

            if approve is not None and not approve(name, args):
                outcome: dict[str, Any] = {"ok": False, "error": "人类拒绝执行该工具调用"}
            else:
                outcome = execute_tool(name, args, context)

            convo.append({
                "role": "tool",
                "tool_call_id": str(call.get("id", "")),
                "content": json.dumps(outcome, ensure_ascii=False),
            })

    return {"messages": convo, "final": convo[-1], "iterations": max_iterations,
            "stopped": "max_iterations", "tool_calls_made": tool_calls_made}
