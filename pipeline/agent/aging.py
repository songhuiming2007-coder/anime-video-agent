"""工具结果老化（D65，spec `docs/dev/plans/2026-10-10-tool-result-aging-spec.md`）。

**请求时纯函数投影**：发给模型前，把「上一用户回合及更早」且超过阈值的 tool 消息正文
替换为占位摘要；`session.jsonl` 与内存中的 `convo` 一字不动（日志即事实，全文可重读）。
compact.py 同款的「纯函数层只管算」纪律：本模块不碰会话对象、不写盘、不发请求。
"""

from __future__ import annotations

from typing import Any

# 阈值（spec §3.3）：2026-10-10 全量 session.jsonl 实测（含全部 sid、排除回滚消息）——
# 91 条 tool 消息，中位数 319 字符、P90 = 2,666；>4000 的恰 5 条（5.5%）占 67.8% 字符体量。
# 4000 落在 P90 与 P99 之间，恰好只切顶部 5%，常规检索/状态结果（≤3k）一律不碰。
AGED_TOOL_MAX_CHARS = 4_000

# 占位保留的原文头部（spec §3.2）：现存 7 类工具结果全是 `{"ok":…, "result":{…}}` JSON，
# `path`/`query`/`argv` 都在前 ~120 字符内，300 盖住身份区；再大就失去瘦身意义。
AGE_HEAD_CHARS = 300


def _wrapup_key() -> str:
    """收尾指令的内部标记，权威定义在 llm.py（§3.4：与 `_TIER_KEY`/`COMPACT_KEY` 同一先例）。

    llm 反向依赖本模块（`age_tool_results`），只能在函数内取（同 compact.py 取 llm 的先例）。
    """
    from pipeline.agent import llm

    return llm._WRAPUP_KEY


def _tool_names(messages: list[Any]) -> dict[str, str]:
    """`tool_call_id → 工具名`：扫描 assistant 消息的 tool_calls 建映射（spec §3.2）。"""
    names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            call_id = str((call or {}).get("id") or "")
            name = ((call or {}).get("function") or {}).get("name")
            if call_id and name:
                names[call_id] = str(name)
    return names


def _placeholder(tool_name: str | None, content: str) -> str:
    """占位串（spec §3.2，按工具分文案）。head = 原文前 300 字符，盖住结果的身份区。"""
    head = content[:AGE_HEAD_CHARS]
    n = len(content)
    if tool_name == "run_pipeline":
        # 执行型：重调 = 重新执行命令（tts run 实测单次可达 1500s，回合级判重挡不住跨回合重放），
        # 故不指引重调，指向 read_status 拿最新状态
        return (
            f"[工具结果已老化｜原 {n} 字符｜该结果来自 run_pipeline，需要最新状态请调 read_status]\n{head}"
        )
    if tool_name:
        return f"[工具结果已老化｜原 {n} 字符｜需要全文请重新调用 {tool_name}]\n{head}"
    # 查不到名字（修复插入的合成结果等）→ 通用文案
    return f"[工具结果已老化｜原 {n} 字符｜需要全文请重新调用同一工具]\n{head}"


def age_tool_results(
    messages: list[Any], *, exempt_last_turn: bool = True
) -> list[Any]:
    """老化投影（spec §3.1）：纯函数，返回新列表，只复制被替换的消息，不 mutate 入参。

    边界 = 最后一条不带 wrapup 标记的 user 消息；边界之后的全部原样（当前回合的工具结果
    模型正在用）；边界之前的 tool 消息超过 `AGED_TOOL_MAX_CHARS` 就换占位串。
    没有 user 消息 → 整条列表视为「当前回合之前」，全部按规则处理（防御分支）。
    `exempt_last_turn=False`（摘要器路径）：被压缩的 region 全是旧历史，没有当前回合可豁免。
    """
    wrapup_key = _wrapup_key()
    boundary: int | None = None
    if exempt_last_turn:
        for i in range(len(messages) - 1, -1, -1):
            message = messages[i]
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and wrapup_key not in message
            ):
                boundary = i
                break
    names = _tool_names(messages)
    aged: list[Any] = []
    for i, message in enumerate(messages):
        if (
            (boundary is None or i < boundary)
            and isinstance(message, dict)
            and message.get("role") == "tool"
            and isinstance(message.get("content"), str)
            and len(message["content"]) > AGED_TOOL_MAX_CHARS
        ):
            tool_name = names.get(str(message.get("tool_call_id") or ""))
            aged.append({**message, "content": _placeholder(tool_name, message["content"])})
        else:
            aged.append(message)
    return aged
