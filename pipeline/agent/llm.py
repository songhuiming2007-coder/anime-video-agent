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

import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Sequence

from pipeline import paths
from pipeline.agent.tools import (
    LOOKUP_KINDS,
    ToolContext,
    assert_egress_boundary,
    build_tool_schemas,
    execute_tool,
    lookup_kind,
    scrub_restricted,
)

REQUEST_TIMEOUT = 60

# 检查点间隔（Spec 9 §2.3 第 6 条）：本轮自上次检查点起，模型回复满 CHECKPOINT_EVERY 条
# **或**工具执行满 CHECKPOINT_EVERY 次（先到先停）就问一次人。防失控靠「人中断 + 本轮判重
# + 检查点」，**不再有固定轮数硬上限**（2026-09-24 用户裁决，D28）。
CHECKPOINT_EVERY = 50

# 收尾指令（Spec 9 §3.5，逐字冻结；reason 用 replace 注入，不用 format——正文里有花括号）。
WRAPUP_INSTRUCTION = (
    "[系统收尾] 本轮因{reason}停止，不再执行任何工具。请只基于上文已获得的信息作答："
    "① 目前能确定的结论；② 还缺什么信息、为什么没拿到；③ 需要人做什么决定或操作。"
    "不要假装已完成未完成的步骤。"
)

# 停止原因 → 收尾指令里的中文短语。
_STOP_REASON_TEXT = {
    "interrupted": "人类中断",
    "error": "出错",
    "checkpoint_stop": "检查点停止",
}

# 合成工具结果（Spec 9 §2.2.4 表，逐字）。
_SYNTH_HUMAN_VOIDED = "人审请求因人类中断作废，未执行"
_SYNTH_HUMAN_UNEXECUTED = "未执行：人类中断"
_SYNTH_UNKNOWN = "已中断：执行被打断，结果未知"
_SYNTH_CHECKPOINT = "未执行：检查点停止"
_SYNTH_WRAPUP = "未执行：收尾阶段禁止调用工具"

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


def _load_agent_cfg(root: Path | None) -> tuple[dict[str, Any], Path, bool, Path] | None:
    """读生效的 agent 配置：`(data, cfg_file, using_local, local_cfg)`；读不到/损坏返回 None。

    local 整文件优先，其次 agent.json。四个值一起返回，是为了让调用方**只读一次**：
    取名字与取密钥必须来自同一份数据（两次读取之间配置被改会拿到互相矛盾的组合）。
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
    return data, cfg_file, using_local, local_cfg


def _env_name_of(data: dict[str, Any]) -> str | None:
    """三个字段缺一即 None——`api_key_env_name` 与 `load_llm_config` 共用这一段。"""
    base_url = str(data.get("base_url") or "").strip()
    model = str(data.get("model") or "").strip()
    env_name = str(data.get("api_key_env") or "").strip()
    if not (base_url and model and env_name):
        return None
    return env_name


def api_key_env_name(root: Path | None = None) -> str | None:
    """当前生效配置指名的密钥环境变量名（只读配置，**不**读环境变量）。

    Spec 10 C10-R2：桌面端从钥匙串取值前先问 core「该注入哪个名字」，TS 侧
    因此不需要第二份「local 优先」规则。`base_url` / `model` / `api_key_env`
    缺一即 None，与 `load_llm_config` 同一段选择逻辑。
    """
    loaded = _load_agent_cfg(root)
    if loaded is None:
        return None
    return _env_name_of(loaded[0])


def load_llm_config(root: Path | None = None) -> LLMConfig | None:
    """读 config/agent.local.json（优先）或 config/agent.json + 指名环境变量。

    无配置 / JSON 损坏 / 字段不全 / 密钥空 → None。没有 models 段时行为与单模型完全一致。
    """
    loaded = _load_agent_cfg(root)
    if loaded is None:
        return None
    data, cfg_file, using_local, local_cfg = loaded
    cfg_dir = cfg_file.parent
    base_url = str(data.get("base_url") or "").strip()
    model = str(data.get("model") or "").strip()
    env_name = _env_name_of(data)  # 与 api_key_env_name 同一段规则，但不再重读配置文件
    if env_name is None:
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
    tool_choice: str | None = None,
    egress_trusted: Sequence[str] = (),
) -> dict[str, Any]:
    """发一次 chat/completions，返回 `choices[0].message`。

    配置缺失 → 降级消息（不抛）；网络/HTTP/协议失败 → `LLMError`。
    `tool_choice=None`（默认）时请求体**不含该键**——与加这个参数之前逐字节相同（TL-15）。
    `egress_trusted`：出网断言的可信文本集（仓库规程正文，Spec 16 §5.2），原样交给断言。
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
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    assert_egress_boundary(cfg.endpoint, payload, trusted_texts=egress_trusted)  # 出网前最后一道断言

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


@dataclass(frozen=True)
class Decision:
    """结构化的人审/放行决定（Spec 9 §4.1）。

    `provenance` 是判重清空规则的唯一依据：只有 `provenance == "human"` 且真的执行了，
    才清空本轮判重表（§2.3 第 5 条）。`feedback` 是协议下自由文本拒绝的回喂内容。
    """

    ok: bool
    reason: str = ""
    provenance: Literal["human", "auto", "card_free", "precheck", "voided"] = "auto"
    feedback: str | None = None


@contextlib.contextmanager
def _stopping(control: "LoopControl") -> Iterator[None]:
    """「停止中」（§2.2 状态表）：补合成结果 / 决定收尾期间到达的中断只置标志、不抛。

    这里**只吸收、不放延迟区**：`defer()` 会把异常挡在区内，代价是中止它包住的那段语句，
    合成结果就不齐了（实测：24 个调用只写出 9 条 tool 消息）。真正的临界区是 `commit()`
    自己的延迟区（§2.2 第 3 条的延迟区闭集）——信号落在那里被接住、按吸收标志丢弃，
    循环继续跑下一个。**收尾请求不在区内**：「收尾中」到达的中断照旧要能放弃收尾（TL-9）。
    """
    interrupt = control.interrupt
    if interrupt is None:
        yield
        return
    with interrupt.absorb():  # type: ignore[union-attr]
        yield


@dataclass
class LoopControl:
    """工具循环的外部控制面（Spec 9 §4.1）。

    `llm.py` 不认识 `session.py`，只认这里的鸭子类型（import 方向固定 session → llm，TG-7）。
    """

    interrupt: Any
    commit: Callable[[dict[str, Any], str], None]
    """唯一提交入口：写盘 → 追加进内存 `messages`（Spec 9 §2.5）。

    实现必须把消息追加进 `run_tool_loop` 收到的**同一个**列表对象，循环直接拿它当上下文。
    """
    review: Callable[[str, dict[str, Any]], Decision]
    ask_checkpoint: Callable[[dict[str, Any]], bool]
    post_execute: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    fetch_executor: Callable[[int], dict[str, Any]] | None = None
    on_trace: Callable[[dict[str, Any]], None] | None = None
    on_exec_started: Callable[[str, str], None] | None = None
    critical_tools: frozenset[str] = frozenset()


def dedup_key(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """本轮判重键（Spec 9 §2.3 第 5 条）：工具名 + 规范化参数串。

    顶层字符串 `strip()`；**不做 URL 或语义归一**（与 Spec 6 §2.3 同口径）。参数微扰
    （URL 加 `?`、`#`）挡不住，这是裁决时已知并接受的代价（RF-15），由检查点兜底。
    """
    normalized = {
        str(key): (value.strip() if isinstance(value, str) else value)
        for key, value in (args or {}).items()
    }
    return (
        name,
        json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
    )


def _never_continue(snapshot: dict[str, Any]) -> bool:
    """裸循环的检查点答复：一律「停止」——有界，随后照常收尾。"""
    return False


def _bare_review(
    approve: Callable[[str, dict[str, Any]], bool | tuple[bool, str]] | None,
) -> Callable[[str, dict[str, Any]], Decision]:
    """裸循环的 review：旧 `approve` 适配器（approve 为 None → 直接执行，与改造前相同）。"""

    def review(name: str, args: dict[str, Any]) -> Decision:
        if approve is None:
            return Decision(ok=True, provenance="auto")
        raw = approve(name, args)
        if isinstance(raw, bool):
            if raw:
                return Decision(ok=True, provenance="auto")
            return Decision(ok=False, provenance="human", reason="人类拒绝执行该工具调用")
        ok, reason = raw
        if ok:
            return Decision(ok=True, provenance="auto")
        return Decision(
            ok=False, provenance="human", reason=reason or "人类拒绝执行该工具调用"
        )

    return review


def _tool_message(call: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """工具结果进会话的唯一序列化点。受限字样先脱敏（D52）：作业输出常带
    `…/03-audio/manifest.json` 这类路径，不脱敏的话下一次请求整轮被出网断言拦下回滚。
    人看的工具帧走 `_trace`，是原始结果；assistant 的 tool_calls 参数不脱敏（双保险，ADR-0026）。
    """
    return {
        "role": "tool",
        "tool_call_id": str(call.get("id", "")),
        "content": scrub_restricted(json.dumps(outcome, ensure_ascii=False)),
    }


def _reject_outcome(decision: Decision) -> dict[str, Any]:
    """被拒时的合成工具结果（M15b：拒因**具体**回喂，不是一句固定文案）。"""
    if decision.feedback:
        return {"ok": False, "error": f"人类拒绝执行该工具调用：{decision.feedback}"}
    return {"ok": False, "error": decision.reason or "人类拒绝执行该工具调用"}


def _duplicate_outcome(name: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"重复调用：本轮已用完全相同的参数调用过 {name}（上次结果见上文）。"
            "换一条路，或基于已有信息作答。"
        ),
    }


def _trace(
    control: "LoopControl",
    index: int,
    name: str,
    args: dict[str, Any],
    *,
    content: Any = None,
    duplicate: bool = False,
) -> None:
    """工具帧轨迹（§3.1 的 `tool`）：start 在每次调用之前，end 在结果提交之前。

    `summary`/`ok`/`observation` 的判定在 session 侧做一次（core 只做一次），这里只给原始材料。
    """
    if control.on_trace is None:
        return
    frame: dict[str, Any] = {"index": index, "name": name, "duplicate": duplicate}
    if content is None:
        frame["phase"] = "start"
        frame["args"] = args
    else:
        frame["phase"] = "end"
        frame["content"] = content
    control.on_trace(frame)


def _loop_result(
    convo: list[dict[str, Any]],
    *,
    final: dict[str, Any] | None = None,
    local_note: str | None = None,
    stopped: str,
    rollback: bool = False,
    llm_calls: int = 0,
    tool_calls_made: int = 0,
    tool_executions: int = 0,
    duplicates_rejected: int = 0,
    checkpoints: int = 0,
    wrapup: str = "none",
    error: str | None = None,
    prompt_chars: int = 0,
    elapsed_s: float = 0.0,
    lookups: dict[str, int] | None = None,
) -> dict[str, Any]:
    """返回契约（Spec 9 §3.4）。`iterations` 保留旧键名 = `llm_calls`。"""
    return {
        "messages": convo,
        "final": final,
        "local_note": local_note,
        "stopped": stopped,
        "rollback": rollback,
        "iterations": llm_calls,
        "llm_calls": llm_calls,
        "tool_calls_made": tool_calls_made,
        "tool_executions": tool_executions,
        "duplicates_rejected": duplicates_rejected,
        "checkpoints": checkpoints,
        "wrapup": wrapup,
        "error": error,
        "prompt_chars": prompt_chars,
        "elapsed_s": elapsed_s,
        "lookups": dict(lookups) if lookups else _zero_lookups(),
    }


def _zero_lookups() -> dict[str, int]:
    return {k: 0 for k in LOOKUP_KINDS}


def run_tool_loop(
    messages: list[dict[str, Any]],
    *,
    ctx: ToolContext | None = None,
    scope: str = "creative",
    config: LLMConfig | None = None,
    root: Path | None = None,
    approve: Callable[[str, dict[str, Any]], bool | tuple[bool, str]] | None = None,
    control: LoopControl | None = None,
    egress_trusted: Sequence[str] = (),
) -> dict[str, Any]:
    """多轮 tool_calls 状态机（Spec 9 §2.3、§4.7）。**没有固定轮数上限。**

    防失控三件套：人中断、本轮判重、检查点（回复或工具执行满 `CHECKPOINT_EVERY` 次先到先停）。
    任何非正常停止都先做一次 `tool_choice:"none"` 的无工具收尾（除非本轮一条回复都没拿到，
    或本轮被出网断言拒绝——那时收尾没有意义，直接回滚）。

    `control is None` 时是**裸循环**（仅测试与兼容用途，生产调用点必须传 `control`，TG-6）：
    不装中断处理器、不判重、有检查点且一律「停止」、`review` 是旧 `approve` 的适配器。
    「没有人审通道就拒绝运行」由内核（`AgentSession`）执行，不在这里（TK-5）。
    """
    context = ctx or ToolContext(scope=scope, root=root)
    # root 未显式给定时用 ctx 的（会话绑定的仓库根），否则 ctx.root 会被静默忽略
    effective_root = root if root is not None else context.root
    cfg = config if config is not None else load_llm_config(effective_root)

    if cfg is None:
        final = local_directive_message(context.scope, "缺少 config/agent.json 或环境变量密钥")
        return _loop_result(list(messages), final=final, stopped="degraded")

    tools = build_tool_schemas(effective_root)
    # 同一次工具循环的所有迭代用同一档（Spec 7 §2.7 决策 7a）
    purpose = purpose_for_scope(context.scope)

    bare = control is None
    # 裸循环照旧复制（调用方随后 clear+extend，与改造前一致）；有 control 时用**调用方的那个
    # 列表对象**：commit 的契约就是「写盘 → 追加进内存 messages」（Spec 9 §2.5）。
    convo = list(messages) if bare else messages
    if bare:
        control = LoopControl(
            interrupt=None,
            commit=lambda message, origin: convo.append(message),
            review=_bare_review(approve),
            ask_checkpoint=_never_continue,
            critical_tools=frozenset(),
        )

    dedup: dict[tuple[str, str], None] = {}
    turn_tally: dict[str, int] = {}   # 本轮调用名 → 次数（本地说明用，§2.3 第 4 条）
    llm_calls = tool_calls_made = tool_executions = duplicates_rejected = checkpoints = 0
    replies_since_cp = execs_since_cp = 0
    prompt_chars = 0
    lookups = _zero_lookups()  # D48 ①：本回合实际执行的查证调用，按类计数
    started = time.monotonic()
    # 中断落点（Spec 9 §2.2 第 4 条的表）：handler 据此决定补哪一条合成结果。
    live: dict[str, Any] = {
        "calls": [], "index": 0, "call": None, "stage": None, "outcome": None, "name": "",
    }

    def _chat(tool_choice: str | None = None) -> dict[str, Any]:
        nonlocal prompt_chars
        prompt_chars = sum(
            len(str(m.get("content") or "")) for m in convo if isinstance(m, dict)
        )
        return chat_complete(
            convo,
            tools=tools or None,
            config=cfg,
            scope=context.scope,
            purpose=purpose,
            tool_choice=tool_choice,
            egress_trusted=egress_trusted,
        )

    def _checkpoint_snapshot(trigger: str) -> dict[str, Any]:
        return {
            "llm_calls": llm_calls,
            "tool_executions": tool_executions,
            "elapsed_s": time.monotonic() - started,
            "trigger": trigger,
        }

    def _synthesize_rest(start: int, text: str) -> None:
        for rest in live["calls"][start:]:
            control.commit(  # type: ignore[union-attr]
                _tool_message(rest, {"ok": False, "error": text}), "synthetic_tool"
            )

    def _local_note(reason: str, wrapup_error: str | None) -> str:
        # 本轮**全部**调用（跨回复累计）：只看最后一条回复会在多轮后写成「（无）」
        detail = "、".join(f"{name}×{count}" for name, count in turn_tally.items()) or "无"
        why = f"失败（{wrapup_error}）" if wrapup_error else "未产生可用答复"
        return (
            f"[收尾·本地] 本轮在第 {llm_calls} 次模型调用后因{_STOP_REASON_TEXT.get(reason, reason)}"
            f"停止；工具调用 {tool_calls_made} 次（{detail}）；收尾调用{why}。"
            "已执行的工具结果保留在会话中。"
        )

    def _wrapup(reason: str) -> tuple[str, dict[str, Any] | None, str | None, str | None]:
        """收尾调用（§2.3 第 2 条）。返回 (wrapup 状态, final, local_note, error)。"""
        nonlocal llm_calls
        if control.interrupt is not None:  # type: ignore[union-attr]
            with contextlib.suppress(AttributeError):
                control.interrupt.wrapup_started = True  # type: ignore[union-attr]
        control.commit(  # type: ignore[union-attr]
            {
                "role": "user",
                "content": WRAPUP_INSTRUCTION.replace(
                    "{reason}", _STOP_REASON_TEXT.get(reason, reason)
                ),
            },
            "wrapup_instruction",
        )
        try:
            reply = _chat(tool_choice="none")
        except KeyboardInterrupt:
            return "aborted", None, None, None
        except Exception as exc:  # noqa: BLE001 - 收尾失败必须如实降级，不许假装答完
            return "failed", None, _local_note(reason, f"{type(exc).__name__}: {exc}"), str(exc)
        llm_calls += 1
        control.commit(reply, "assistant")  # type: ignore[union-attr]
        calls = reply.get("tool_calls") or []
        if not calls:
            return "ok", reply, None, None
        # A1 不成立：服务商忽略了 tool_choice:"none"。不执行，逐个补合成结果。
        for call in calls:
            control.commit(  # type: ignore[union-attr]
                _tool_message(call, {"ok": False, "error": _SYNTH_WRAPUP}), "synthetic_tool"
            )
        if str(reply.get("content") or ""):
            return "ok", reply, None, None
        return "failed", None, _local_note(reason, None), "收尾回复不含可用答复"

    def _stop(reason: str, *, error: str | None = None):
        """非正常停止的收尾闸：本轮一条回复都没拿到 → 回滚；否则收尾。"""
        if getattr(control.interrupt, "skip_wrapup", False):
            # SIGTERM（§2.2 第 5 条）：host 的 SIGKILL 倒计时只有 5 s，收尾最长 60 s → 跳过收尾
            return _loop_result(
                convo, stopped=reason, llm_calls=llm_calls, tool_calls_made=tool_calls_made,
                tool_executions=tool_executions, duplicates_rejected=duplicates_rejected,
                checkpoints=checkpoints, wrapup="skipped", error=error,
                prompt_chars=prompt_chars, lookups=lookups, elapsed_s=time.monotonic() - started,
            )
        if llm_calls == 0:
            return _loop_result(
                convo, stopped=reason, rollback=True, llm_calls=llm_calls,
                tool_calls_made=tool_calls_made, tool_executions=tool_executions,
                duplicates_rejected=duplicates_rejected, checkpoints=checkpoints,
                error=error, prompt_chars=prompt_chars, lookups=lookups, elapsed_s=time.monotonic() - started,
            )
        state, final, note, wrapup_error = _wrapup(reason)
        return _loop_result(
            convo, final=final, local_note=note, stopped=reason, llm_calls=llm_calls,
            tool_calls_made=tool_calls_made, tool_executions=tool_executions,
            duplicates_rejected=duplicates_rejected, checkpoints=checkpoints,
            wrapup=state, error=error or wrapup_error,
            prompt_chars=prompt_chars, lookups=lookups, elapsed_s=time.monotonic() - started,
        )

    try:
        while True:
            if replies_since_cp >= CHECKPOINT_EVERY or execs_since_cp >= CHECKPOINT_EVERY:
                trigger = "replies" if replies_since_cp >= CHECKPOINT_EVERY else "executions"
                checkpoints += 1
                if not control.ask_checkpoint(_checkpoint_snapshot(trigger)):  # type: ignore[union-attr]
                    live["call"] = None
                    return _stop("checkpoint_stop")
                replies_since_cp = execs_since_cp = 0

            live["call"] = None
            live["calls"] = []
            live["index"] = 0
            live["stage"] = "model"
            reply = _chat()
            control.commit(reply, "assistant")  # type: ignore[union-attr]
            llm_calls += 1
            replies_since_cp += 1

            calls = reply.get("tool_calls") or []
            if not calls:
                return _loop_result(
                    convo, final=reply, stopped="done", llm_calls=llm_calls,
                    tool_calls_made=tool_calls_made, tool_executions=tool_executions,
                    duplicates_rejected=duplicates_rejected, checkpoints=checkpoints,
                    prompt_chars=prompt_chars, lookups=lookups, elapsed_s=time.monotonic() - started,
                )

            live["calls"] = calls
            for index, call in enumerate(calls):
                live["index"] = index
                live["call"] = call
                function = call.get("function") or {}
                name = str(function.get("name", ""))
                args = _parse_tool_args(function.get("arguments"))
                live["name"] = name
                tool_calls_made += 1
                turn_tally[name] = turn_tally.get(name, 0) + 1

                # 执行计数**每次执行之前**查（🟡-A）：同一条回复里的并行调用也逐个受约束
                if execs_since_cp >= CHECKPOINT_EVERY:
                    checkpoints += 1
                    if not control.ask_checkpoint(_checkpoint_snapshot("executions")):  # type: ignore[union-attr]
                        _synthesize_rest(index, _SYNTH_CHECKPOINT)
                        live["call"] = None
                        return _stop("checkpoint_stop")
                    replies_since_cp = execs_since_cp = 0

                if bare:
                    key: tuple[str, str] | None = None
                else:
                    key = dedup_key(name, args)
                    if key in dedup:
                        duplicates_rejected += 1
                        _trace(control, index, name, args, duplicate=True)
                        control.commit(  # type: ignore[union-attr]
                            _tool_message(call, _duplicate_outcome(name)), "synthetic_tool"
                        )
                        _trace(control, index, name, args, content=_duplicate_outcome(name),
                               duplicate=True)
                        continue
                    dedup[key] = None  # 预登记：中断落在这里也算「已调过」

                live["stage"] = "review"
                decision = control.review(name, args)  # type: ignore[union-attr]
                live["stage"] = "tool"
                if not decision.ok:
                    rejected = _reject_outcome(decision)
                    _trace(control, index, name, args)
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, rejected), "synthetic_tool"
                    )
                    _trace(control, index, name, args, content=rejected)
                    continue

                _trace(control, index, name, args)
                if control.on_exec_started is not None:  # type: ignore[union-attr]
                    control.on_exec_started(str(call.get("id", "")), name)  # type: ignore[union-attr]
                live["outcome"] = None
                exec_ctx = (
                    replace(context, confirmed=True) if (approve is not None or not bare) else context
                )
                if control.interrupt is not None and name in control.critical_tools:  # type: ignore[union-attr]
                    with control.interrupt.defer():  # type: ignore[union-attr]
                        live["outcome"] = execute_tool(name, args, exec_ctx)
                else:
                    live["outcome"] = execute_tool(name, args, exec_ctx)
                outcome = live["outcome"]
                live["outcome"] = None
                tool_executions += 1
                execs_since_cp += 1
                kind = lookup_kind(name, args)
                if kind is not None:
                    lookups[kind] += 1

                if name == "acquire_propose" and outcome.get("ok") and control.post_execute is not None:  # type: ignore[union-attr]
                    live["stage"] = "hook"
                    try:
                        outcome = control.post_execute(name, args, outcome)  # type: ignore[union-attr]
                    except KeyboardInterrupt as exc:
                        # 抓取钩子被中断（D35）：提案已执行、已计数，结果照常（带抓取记录）
                        live["outcome"] = getattr(exc, "outcome", None) or outcome
                        raise
                    live["stage"] = "tool"
                _trace(control, index, name, args, content=outcome)
                if decision.provenance == "human" or (name == "write_episode_file" and outcome.get("ok")):
                    # 经人批准的执行完成后清空（§2.3 第 5 条）。D48：成功写期文件后也清——D44 让草稿写入免卡后，
                    # 「改完再跑同一条 check_script」被判重复，模型换着写路径绕过去。只清写入后：只读检查免卡执行不清，
                    # 原地反复跑同一条检查仍判重
                    dedup.clear()
                control.commit(_tool_message(call, outcome), "tool")  # type: ignore[union-attr]

    except KeyboardInterrupt:
        # 落点表（§2.2 第 4 条）：补合成结果 → 收尾或回滚。
        # 「停止中」：这期间到达的中断只置标志、不抛（合成结果必须齐全，不许丢）。
        # 随后的 `_stop` 在吸收区**之外**，所以「收尾中」的中断照旧能放弃收尾（TL-9）。
        with _stopping(control):
            call = live["call"]
            if call is not None:
                if live["stage"] == "hook":
                    # 抓取钩子里的中断：真实结果照常配对（执行计数在钩子之前已加过）
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, live["outcome"]), "tool"
                    )
                    live["outcome"] = None
                elif live["stage"] == "tool" and live["outcome"] is not None:
                    # 临界区工具已经跑完，中断在延迟区退出时才浮出：用**真实结果**，配对不受影响
                    tool_executions += 1
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, live["outcome"]), "tool"
                    )
                    live["outcome"] = None
                else:
                    text = _SYNTH_HUMAN_VOIDED if live["stage"] == "review" else _SYNTH_UNKNOWN
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, {"ok": False, "error": text}), "synthetic_tool"
                    )
                _synthesize_rest(live["index"] + 1, _SYNTH_HUMAN_UNEXECUTED)
                live["call"] = None
        return _stop("interrupted")
    except PermissionError as exc:
        # 出网断言拒绝（llm.py::assert_egress_boundary）：不做收尾（同一份历史必然再被拒），回滚
        return _loop_result(
            convo, stopped="blocked", rollback=True, llm_calls=llm_calls,
            tool_calls_made=tool_calls_made, tool_executions=tool_executions,
            duplicates_rejected=duplicates_rejected, checkpoints=checkpoints, error=str(exc),
            prompt_chars=prompt_chars, lookups=lookups, elapsed_s=time.monotonic() - started,
        )
    except LLMError as exc:
        return _stop("error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - 意外异常也走「先收尾后停」，不假装无事发生
        return _stop("error", error=f"{type(exc).__name__}: {exc}")
