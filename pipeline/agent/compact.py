"""上下文压缩的纯函数层（D62 PR1，spec `docs/dev/plans/2026-10-09-context-compaction-spec.md`）。

本模块只做「算」：窗口表、读数回落、闭环切片、落史标记、双侧脱敏、膨胀判定。
不发请求、不写盘、不碰会话对象——摘要器调用与落史在 PR2，自动触发在 PR3。

三条不变量（违反即缺陷）：

1. **切口只落在完整闭环边界**（§4.2）：切口之前的每个 tool_call 都已有结果、切口处不是
   tool 消息。否则尾部以孤儿 tool 消息开头，或摘要区吞掉一半配对——服务商直接 400。
2. **原地改写**（§4.3）：`apply_compaction` 用 `messages[:] = …`，绝不重绑列表对象。
   `persist = messages is main_messages`、commit 的 append、rollback 的 `del` 都认对象同一性。
3. **标记走宿主内部字段**（§4.4）：`_ava_compact` 进会话史、进 session.jsonl，进请求体前由
   `llm._wire_messages` 删除。模型看不见、也就仿写不出；不用文本前缀当机器标记。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pipeline import paths
from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS

# 宿主内部标记（对齐 llm._TIER_KEY 先例）：值是标记种类，不是布尔——
# 摘要与一次性重注入卡都要在下一次压缩时被滤掉，但得分得清谁是谁。
COMPACT_KEY = "_ava_compact"
MARK_SUMMARY = "summary"
MARK_CARD = "card"

# 回落估算的口径：每 token 折多少字符（§4.1 usage=None 时、§4.2 尾部预算一律用它）。
# spec 原写 ÷4，那是英文口径；本仓会话以中文为主，实测（伪恋期 session.jsonl 的 8 个 turn_end，
# gemini-3.8-flash-high，2026-10-09）content 字符 ÷ 服务商 prompt_tokens = 1.32–1.39。
# ÷4 会把 token 低估约 3 倍：gpt-4o 下回落触发要到真实 ≈225k token 才响，早已越过 128k 窗口。
# 估算只许偏大（偏大 = 早压、尾部略短；偏小 = 撞窗口），取 1.0：在实测下限 1.32 之下再留余量，
# 给没实测过的分词器（gpt-4o 的 o200k 等）兜底。
CHARS_PER_TOKEN = 1.0

# 膨胀护栏（§4.5，Gemini 护栏变体）：摘要 ≥ 被压缩区间 × 0.8 → 压缩没意义，本回合交人。
BLOAT_RATIO = 0.8

# 脱敏注记（§4.4，审查 #14）：摘要里出现 [已脱敏] 时随附，防模型把空锚补编成文件名。
# spec 原文是「用工具切片查 session.jsonl」——read_artifact 只放行 .md/.txt/.json/.patch/.log，
# session.jsonl 读不到，受限文件名本身也一律不出网；写一句做不到的指示等于让模型去撞墙。
REDACTION_NOTE = (
    "[注] 摘要中的「[已脱敏]」原是受限文件名（一律不出网清单）。"
    "不要猜测或补写原文；需要核对相关产物时用 read_status，或直接问人。"
)

# 摘要消息的人读引导语：给模型看懂这是什么。机器识别**只认** COMPACT_KEY，不认这段文字。
SUMMARY_LEAD = "[上下文压缩] 以下是本会话较早部分的摘要，原文已移出上下文；摘要之后是保留的近期原文。\n\n"


class CompactConfigError(ValueError):
    """config/agent/compact.json 存在但不合法：调用方必须当场告诉人，不许静默当成「没配」。"""


@dataclass(frozen=True)
class ModelWindow:
    window: int
    reliable_ratio: float

    @property
    def reliable(self) -> int:
        return int(self.window * self.reliable_ratio)


@dataclass(frozen=True)
class CompactConfig:
    trigger_ratio: float = 0.6
    tail_ratio: float = 0.15
    tail_max_messages: int = 20
    models: dict[str, ModelWindow] = field(default_factory=dict)

    def window_for(self, model: str) -> ModelWindow | None:
        """表里没有 → None：不自动压缩、不显示分母，**不猜窗口**（人裁决⑤）。"""
        return self.models.get(model)

    def trigger_tokens(self, model: str) -> int | None:
        w = self.window_for(model)
        return None if w is None else int(w.reliable * self.trigger_ratio)

    def tail_budget_tokens(self, model: str) -> int | None:
        w = self.window_for(model)
        return None if w is None else int(w.reliable * self.tail_ratio)


def _ratio(value: Any, name: str, *, upper_open: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompactConfigError(f"{name} 必须是数字: {value!r}")
    v = float(value)
    if not (0 < v < 1 if upper_open else 0 < v <= 1):
        raise CompactConfigError(f"{name} 越界（需 0 < x {'<' if upper_open else '≤'} 1）: {v}")
    return v


def parse_compact_config(data: Any) -> CompactConfig:
    """校验 → CompactConfig。非法一律抛 `CompactConfigError`，不半信半疑地取一部分。"""
    if not isinstance(data, dict):
        raise CompactConfigError("compact.json 顶层必须是对象")
    defaults = CompactConfig()
    trigger = _ratio(data.get("trigger_ratio", defaults.trigger_ratio), "trigger_ratio")
    tail = _ratio(data.get("tail_ratio", defaults.tail_ratio), "tail_ratio", upper_open=True)
    if tail >= trigger:
        # 尾部预算 ≥ 触发点 → 压完立刻又够触发线，每轮都压
        raise CompactConfigError(f"tail_ratio（{tail}）必须小于 trigger_ratio（{trigger}）")
    max_messages = data.get("tail_max_messages", defaults.tail_max_messages)
    if isinstance(max_messages, bool) or not isinstance(max_messages, int) or max_messages < 1:
        raise CompactConfigError(f"tail_max_messages 必须是正整数: {max_messages!r}")
    raw_models = data.get("models", {})
    if not isinstance(raw_models, dict):
        raise CompactConfigError("models 必须是对象（模型名 → {window, reliable_ratio}）")
    models: dict[str, ModelWindow] = {}
    for name, entry in raw_models.items():
        if not isinstance(entry, dict):
            raise CompactConfigError(f"models.{name} 必须是对象")
        window = entry.get("window")
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            raise CompactConfigError(f"models.{name}.window 必须是正整数: {window!r}")
        models[name] = ModelWindow(
            window=window,
            reliable_ratio=_ratio(entry.get("reliable_ratio", 1.0), f"models.{name}.reliable_ratio"),
        )
    return CompactConfig(
        trigger_ratio=trigger, tail_ratio=tail, tail_max_messages=max_messages, models=models
    )


def load_compact_config(root: Path | None = None) -> CompactConfig:
    """读 config/agent/compact.json。文件缺席 → 空窗口表（= 全部模型「表里没有」）；
    存在但损坏 → `CompactConfigError`。"""
    cfg_file = Path(root or paths.ROOT) / "config" / "agent" / "compact.json"
    if not cfg_file.exists():
        return CompactConfig()
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompactConfigError(f"{cfg_file.name} 读不了或不是合法 JSON: {exc}") from None
    return parse_compact_config(data)


# ---- 读数 ----

def message_chars(message: Any) -> int:
    """一条消息进请求体的文字量：content + tool_calls 的函数名与参数串。

    比 `prompt_chars`（只数 content）多数参数串：写稿工具的参数就是整篇稿子，不数会严重低估。
    """
    if not isinstance(message, dict):
        return 0
    total = len(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        function = (call or {}).get("function") or {}
        total += len(str(function.get("name") or "")) + len(str(function.get("arguments") or ""))
    return total


def estimate_tokens(messages: list[Any]) -> int:
    return math.ceil(sum(message_chars(m) for m in messages) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class ContextReading:
    tokens: int | None
    source: Literal["usage", "estimate"] | None


def context_reading(prompt_tokens: int | None, messages: list[Any]) -> ContextReading:
    """服务商实测优先；usage=None 回落字数估算；两者皆缺 → (None, None)，调用方要告诉人（§4.1）。"""
    if isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool) and prompt_tokens >= 0:
        return ContextReading(prompt_tokens, "usage")
    if messages:
        return ContextReading(estimate_tokens(messages), "estimate")
    return ContextReading(None, None)


@dataclass(frozen=True)
class TriggerDecision:
    fire: bool
    reason: Literal["over", "under", "no_window", "no_reading"]
    reading: ContextReading
    threshold: int | None


def decide_trigger(
    config: CompactConfig, model: str, prompt_tokens: int | None, messages: list[Any]
) -> TriggerDecision:
    """该不该压。`no_window` / `no_reading` 都是「我不知道」，不压，但调用方必须 notice 人——不静默。"""
    reading = context_reading(prompt_tokens, messages)
    threshold = config.trigger_tokens(model)
    if threshold is None:
        return TriggerDecision(False, "no_window", reading, None)
    if reading.tokens is None:
        return TriggerDecision(False, "no_reading", reading, threshold)
    fire = reading.tokens >= threshold
    return TriggerDecision(fire, "over" if fire else "under", reading, threshold)


# ---- 闭环切片 ----

def _call_ids(message: dict[str, Any]) -> list[str]:
    return [str((c or {}).get("id")) for c in message.get("tool_calls") or []]


def valid_cuts(messages: list[Any]) -> list[bool]:
    """`flags[i]`（0 ≤ i ≤ len）= 切口能否落在下标 i（保留 `messages[i:]`）。

    能落 ⇔ `messages[:i]` 里每个 tool_call 都已配上结果，且 `messages[i]` 不是 tool 消息。
    按 id 配对而不是按「assistant 后面紧跟 tool」：不对历史的排列方式做假设。
    """
    flags: list[bool] = []
    pending: set[str] = set()
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else None
        flags.append(not pending and role != "tool")
        if role == "assistant":
            pending.update(_call_ids(message))
        elif role == "tool":
            pending.discard(str(message.get("tool_call_id")))
    flags.append(not pending)
    return flags


def is_closed(messages: list[Any]) -> bool:
    """整段历史的 tool_call 是否全部闭环——压缩的前置条件（§4.1 触发点的定义）。"""
    return valid_cuts(messages)[-1]


def choose_cut(messages: list[Any], *, token_budget: int, max_messages: int) -> int:
    """尾部从哪条开始保留（§4.2）。返回 `cut`：`messages[1:cut]` 进摘要，`messages[cut:]` 原样保留。

    从最新往前累加，token 预算与条数任一撞线即停；再往**后**挪到最近的闭环边界——
    往前挪会突破预算，预算是硬的。一个超预算的工具组宁可整组进摘要。
    `messages[0]`（system 状态卡）永不进摘要。`cut ≤ 1` = 没东西可压。
    """
    if not is_closed(messages):
        raise ValueError("历史含未闭环的 tool_calls：压缩只许在工具结果全部写史之后")
    if token_budget < 0 or max_messages < 0:
        raise ValueError("token_budget 与 max_messages 不能为负")
    start = len(messages)
    tokens = 0
    while start > 1:
        cost = math.ceil(message_chars(messages[start - 1]) / CHARS_PER_TOKEN)
        if len(messages) - start + 1 > max_messages or tokens + cost > token_budget:
            break
        tokens += cost
        start -= 1
    flags = valid_cuts(messages)
    return next(i for i in range(max(start, 1), len(messages) + 1) if flags[i])


# ---- 落史标记 ----

def is_marked(message: Any, kind: str | None = None) -> bool:
    if not isinstance(message, dict) or COMPACT_KEY not in message:
        return False
    return kind is None or message.get(COMPACT_KEY) == kind


def strip_marked(messages: list[Any]) -> list[Any]:
    """滤掉旧摘要与旧重注入卡（多轮压缩防套娃，§4.4）。"""
    return [m for m in messages if not is_marked(m)]


def summary_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": SUMMARY_LEAD + text, COMPACT_KEY: MARK_SUMMARY}


def card_message(text: str) -> dict[str, Any]:
    """一次性重注入的状态卡（§4.6）：此后不刷新，下一次压缩时被滤掉。"""
    return {"role": "user", "content": text, COMPACT_KEY: MARK_CARD}


def compacted_history(
    messages: list[Any], cut: int, summary: dict[str, Any], card: dict[str, Any] | None = None
) -> list[Any]:
    """压缩后的历史：`[messages[0], 摘要, (重注入卡), 尾部去掉旧标记]`。"""
    if not is_marked(summary, MARK_SUMMARY):
        raise ValueError("摘要消息必须带 _ava_compact=summary 标记")
    if card is not None and not is_marked(card, MARK_CARD):
        raise ValueError("重注入卡必须带 _ava_compact=card 标记")
    if not 1 < cut <= len(messages):
        raise ValueError(f"切口越界：cut={cut}，历史 {len(messages)} 条（cut ≤ 1 表示没东西可压）")
    if not valid_cuts(messages)[cut]:
        raise ValueError(f"切口 {cut} 不在闭环边界")
    tail = strip_marked(messages[cut:])
    return [messages[0], summary, *([card] if card is not None else []), *tail]


def apply_compaction(
    messages: list[Any], cut: int, summary: dict[str, Any], card: dict[str, Any] | None = None
) -> None:
    """**原地**改写（§4.3）：调用前后 `messages` 是同一个列表对象。"""
    messages[:] = compacted_history(messages, cut, summary, card)


# ---- 脱敏与膨胀 ----

def scrub_counted(text: str) -> tuple[str, int]:
    """与 `tools.scrub_restricted` 同口径（同一张 RESTRICTED_EGRESS_PATTERNS 表、IGNORECASE），
    多返回命中数——注记要知道有没有、有几处。输入（送摘要器的历史）与输出（摘要）两侧都要过（§4.4）。"""
    hits = 0
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        text, n = re.subn(re.escape(pattern), "[已脱敏]", text, flags=re.IGNORECASE)
        hits += n
    return text, hits


def annotate_redactions(summary_text: str, input_hits: int) -> str:
    """摘要过输出侧脱敏；两侧任一有命中就附注记（输入侧命中而摘要没提到也要附：模型可能已改写成别的说法）。"""
    scrubbed, output_hits = scrub_counted(summary_text)
    if input_hits + output_hits == 0:
        return scrubbed
    return scrubbed.rstrip() + "\n\n" + REDACTION_NOTE


def is_bloated(summary_text: str, region: list[Any]) -> bool:
    """摘要 ≥ 被压缩区间 × BLOAT_RATIO → 压缩反而没省（§4.5）。同一把尺（字符 ÷ CHARS_PER_TOKEN）两边量。"""
    region_tokens = estimate_tokens(region)
    summary_tokens = math.ceil(len(summary_text) / CHARS_PER_TOKEN)
    return summary_tokens >= region_tokens * BLOAT_RATIO
