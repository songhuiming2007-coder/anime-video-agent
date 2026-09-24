"""极简 OpenAI 兼容 LLM 客户端与工具调用循环（Spec §2.5, §5 PR4）。

零新依赖：只用 stdlib `urllib` POST `{base_url}/chat/completions`。代价是自己追
协议变化——接受，因为工具表 11 个、字段用量是协议的最小公约数（Y11）。

两条硬边界：
1. 密钥只从 `config/agent.json` 的 `api_key_env` 指名环境变量读，绝不落盘/打印/回传；
2. 本模块与 `pipeline.agent.web` 是仓库仅有的两条出网路径：发请求前都必须过 `assert_egress_boundary`。

配置或密钥缺失时不抛异常，降级为本地纯指示模式，且降级显式可辨（返回消息带
`degraded` 与原因）——静默换一条假回答是家规禁项。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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

# 模型按用途分层（Spec 7 §2.7）：档位由调用所在的 scope 查表决定，**不看内容**。
PURPOSES = ("reasoning", "light")
SCOPE_PURPOSE: dict[str, str] = {
    "idea": "reasoning",
    "creative": "reasoning",
    "asset": "reasoning",
    "pipeline": "light",
}

# 宿主内部档位标记：只进会话史，进请求体前由 _wire_messages 一律删除。
_TIER_KEY = "_ava_tier"

# 进程级 WARN 锁存（Spec 7 §1.1 🔵-1）：键 = 配置文件路径 + 问题描述。
_WARN_LATCH: set[tuple[str, str]] = set()


def _reset_warn_latch_for_testing() -> None:
    _WARN_LATCH.clear()


def purpose_for_scope(scope: str) -> str:
    """scope → 档位。未知 scope 回落到 reasoning（风险不对称：错配弱档是事故）。"""
    return SCOPE_PURPOSE.get(scope, "reasoning")


class LLMError(RuntimeError):
    """网络/HTTP/协议层失败（领域异常，不是裸 urllib 报错）。"""


def _warn_once(cfg_file: Path, kind: str, message: str) -> None:
    key = (str(cfg_file), kind)
    if key in _WARN_LATCH:
        return
    _WARN_LATCH.add(key)
    print(f"[WARN] {message}", file=sys.stderr)


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    models: dict[str, str] = field(default_factory=dict)

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def model_for(self, purpose: str | None) -> str:
        """purpose=None 或该档未配置 → 回落到单一 model。"""
        if purpose is None:
            return self.model
        return self.models.get(purpose) or self.model

    @property
    def tiering_active(self) -> bool:
        """只有两档真的配成不同模型时才算分档生效。"""
        return self.model_for("reasoning") != self.model_for("light")


def _parse_models(data: dict[str, Any], cfg_file: Path) -> dict[str, str]:
    """解析 models 段（Spec 7 §2.7 回落表）：非法即**整段作废**，不半信半疑。"""
    raw = data.get("models")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn_once(cfg_file, "models-shape", "models 段不是对象，整段作废，两档都回落到 model")
        return {}
    if set(raw) - set(PURPOSES) or any(not isinstance(v, str) for v in raw.values()):
        _warn_once(
            cfg_file, "models-keys",
            f"models 段含未知键或非字符串值，整段作废，两档都回落到 model（合法键: {list(PURPOSES)}）",
        )
        return {}
    parsed: dict[str, str] = {}
    for purpose in PURPOSES:
        value = str(raw.get(purpose, "")).strip()
        if value:
            parsed[purpose] = value
    return parsed


def load_llm_config(root: Path | None = None) -> LLMConfig | None:
    """读 config/agent.local.json（优先）或 config/agent.json + 指名环境变量。

    无配置 / JSON 损坏 / 字段不全 / 密钥空 → None。没有 models 段时行为与单模型完全一致。
    """
    cfg_dir = Path(root or paths.ROOT) / "config"
    local_cfg = cfg_dir / "agent.local.json"
    using_local = local_cfg.exists()
    cfg_file = local_cfg if using_local else (cfg_dir / "agent.json")
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

    if using_local and "models" not in data:
        # local 是整文件取代：写在 agent.json 的 models 会被静默埋掉，必须响亮
        try:
            peer = json.loads((cfg_dir / "agent.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            peer = None
        if isinstance(peer, dict) and "models" in peer:
            _warn_once(
                local_cfg, "models-shadowed",
                "models 段写在 agent.json，但本机 agent.local.json 整文件取代它，分档未生效",
            )

    return LLMConfig(
        base_url=base_url, model=model, api_key=api_key, models=_parse_models(data, cfg_file)
    )


def local_directive_message(scope: str, reason: str) -> dict[str, Any]:
    """降级输出：打开文件 + 打印 checklist，不假装有 LLM 在场。清单随 scope 变。"""
    if scope == "creative":
        steps = (
            "  1. 01-topic.md：番 / 类型 / 锚点 / 张力 是否填齐（张力是唯一编辑判断，不许代填）；\n"
            "  2. 02-script.draft.md：写完跑 `python -m pipeline.check_script` 至全绿；\n"
        )
    elif scope == "idea":
        steps = (
            "  1. 人工阅读 `data/library/notes/` 对应的番剧笔记，梳理候选张力与锚点；\n"
            "  2. 想好选题后运行 `ava new <名>` 创建新期并进入对话；\n"
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


def _standard_message(message: dict[str, Any]) -> dict[str, Any]:
    """跨档重放前的白名单字段复制（Spec 7 §2.7 决策 7b）。"""
    clean: dict[str, Any] = {"role": message.get("role"), "content": message.get("content")}
    calls = message.get("tool_calls")
    if calls:
        clean["tool_calls"] = [
            {
                "id": call.get("id"),
                "type": call.get("type"),
                "function": {
                    "name": (call.get("function") or {}).get("name"),
                    "arguments": (call.get("function") or {}).get("arguments"),
                },
            }
            for call in calls
        ]
    return clean


def _wire_messages(messages: list[dict[str, Any]], purpose: str | None) -> list[dict[str, Any]]:
    """请求体用的消息序列（Spec 7 §2.7 决策 7b）。

    没有任何消息带档位标记（= 未分档）→ **原样返回同一个列表对象**：请求体与现状字节一致。
    """
    if not any(isinstance(m, dict) and _TIER_KEY in m for m in messages):
        return messages
    wired: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            wired.append(message)
            continue
        tier = message.get(_TIER_KEY)
        clean = {k: v for k, v in message.items() if k != _TIER_KEY}
        if tier is not None and tier != purpose:
            clean = _standard_message(clean)
        wired.append(clean)
    return wired


def chat_complete(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    config: LLMConfig | None = None,
    root: Path | None = None,
    timeout: int = REQUEST_TIMEOUT,
    scope: str = "creative",
    purpose: str | None = None,
) -> dict[str, Any]:
    """发一次 chat/completions，返回 `choices[0].message`。

    配置缺失 → 降级消息（不抛）；网络/HTTP/协议失败 → `LLMError`。
    """
    cfg = config if config is not None else load_llm_config(root)
    if cfg is None:
        return local_directive_message(scope, "缺少 config/agent.json 或环境变量密钥")

    payload: dict[str, Any] = {
        "model": cfg.model_for(purpose),
        "messages": _wire_messages(messages, purpose),
    }
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
    approve: Callable[[str, dict[str, Any]], bool | tuple[bool, str]] | None = None,
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
    # 同一次工具循环的所有迭代用同一档（Spec 7 §2.7 决策 7a）
    purpose = purpose_for_scope(context.scope)

    for iteration in range(1, max_iterations + 1):
        reply = chat_complete(
            convo, tools=tools or None, config=cfg, scope=context.scope, purpose=purpose
        )
        if cfg.tiering_active:
            reply[_TIER_KEY] = purpose
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

            if approve is not None:
                decision = approve(name, args)
                ok, reason = (decision, None) if isinstance(decision, bool) else decision
                if not ok:
                    outcome: dict[str, Any] = {
                        "ok": False,
                        "error": reason if reason else "人类拒绝执行该工具调用",
                    }
                else:
                    from dataclasses import replace
                    exec_ctx = replace(context, confirmed=True)
                    outcome = execute_tool(name, args, exec_ctx)
            else:
                outcome = execute_tool(name, args, context)

            convo.append({
                "role": "tool",
                "tool_call_id": str(call.get("id", "")),
                "content": json.dumps(outcome, ensure_ascii=False),
            })

    return {"messages": convo, "final": convo[-1], "iterations": max_iterations,
            "stopped": "max_iterations", "tool_calls_made": tool_calls_made}
