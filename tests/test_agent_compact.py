"""上下文压缩纯函数层（D62 PR1，spec §六 判据 1–5、7、9 的纯函数部分）。

每条断言都配过变异：把被测行为改坏，确认这里会红（变异记录见各测试的注释）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.agent import compact
from pipeline.agent.compact import (
    COMPACT_KEY,
    MARK_CARD,
    MARK_SUMMARY,
    CompactConfig,
    CompactConfigError,
    ModelWindow,
    annotate_redactions,
    apply_compaction,
    card_message,
    choose_cut,
    compacted_history,
    context_reading,
    decide_trigger,
    estimate_tokens,
    is_bloated,
    is_closed,
    load_compact_config,
    parse_compact_config,
    scrub_counted,
    strip_marked,
    summary_message,
    valid_cuts,
)
from pipeline.agent.llm import _wire_messages

ROOT = Path(__file__).resolve().parents[1]


def _sys(text: str = "S") -> dict:
    return {"role": "system", "content": text}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _say(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _call(*ids: str, args: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "read_status", "arguments": args}}
            for i in ids
        ],
    }


def _result(cid: str, text: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": text}


CFG = CompactConfig(models={"m": ModelWindow(window=1000, reliable_ratio=0.5)})


# ---- 配置 ----

def test_repo_config_parses_and_covers_both_effective_models():
    """仓里的 compact.json 必须能解析，且覆盖 spec §二 的两个生效模型（agent.json / agent.local.json）。"""
    cfg = load_compact_config(ROOT)
    assert cfg.window_for("gpt-4o") == ModelWindow(128000, 1.0)
    assert cfg.window_for("gemini-3.8-flash-high") == ModelWindow(1048576, 1.0)
    # agent.local.json 的 light 档（pipeline scope）：缺了它流水线模式既无分母也压不了（2026-10-10 实测补入）
    assert cfg.window_for("gemini-3.7-flash-high") == ModelWindow(1048576, 1.0)
    # spec §4.0：gpt-4o 的触发点 76.8k、尾部 ≈19k
    assert cfg.trigger_tokens("gpt-4o") == 76800
    assert cfg.tail_budget_tokens("gpt-4o") == 19200
    # 人裁决④：自动触发挂代理实测，实测前仓里必须是关的
    assert cfg.auto_trigger is False


def test_missing_config_means_no_window(tmp_path):
    cfg = load_compact_config(tmp_path)
    assert cfg.window_for("gpt-4o") is None
    assert cfg.trigger_tokens("gpt-4o") is None


@pytest.mark.parametrize("data, needle", [
    ([], "顶层"),
    ({"trigger_ratio": 0}, "trigger_ratio"),
    ({"trigger_ratio": 1.5}, "trigger_ratio"),
    ({"trigger_ratio": True}, "trigger_ratio"),
    ({"tail_ratio": 0.6, "trigger_ratio": 0.6}, "小于"),       # 压完立刻又够触发线
    ({"tail_max_messages": 0}, "tail_max_messages"),
    ({"models": {"m": {"window": 0}}}, "window"),
    ({"models": {"m": {"window": 1000, "reliable_ratio": 0}}}, "reliable_ratio"),
    ({"models": []}, "models"),
])
def test_bad_config_raises_not_silently_empty(data, needle):
    with pytest.raises(CompactConfigError, match=needle):
        parse_compact_config(data)


def test_corrupt_config_file_raises(tmp_path):
    (tmp_path / "config" / "agent").mkdir(parents=True)
    (tmp_path / "config" / "agent" / "compact.json").write_text("{", encoding="utf-8")
    with pytest.raises(CompactConfigError):
        load_compact_config(tmp_path)


# ---- 判据 1：触发 ----

def test_trigger_uses_usage_when_present():
    # 触发点 = 1000 × 0.5 × 0.6 = 300
    assert decide_trigger(CFG, "m", 300, [_user("x")]).fire
    under = decide_trigger(CFG, "m", 299, [_user("x" * 10_000)])
    # 服务商实测优先：字数再多，实测没到就不压（变异：估算优先 → 红）
    assert not under.fire and under.reason == "under" and under.reading.source == "usage"


def test_trigger_falls_back_to_estimate_when_usage_none():
    """变异：usage=None 直接不触发 → 红。"""
    decision = decide_trigger(CFG, "m", None, [_user("字" * 300)])
    assert decision.fire and decision.reading == compact.ContextReading(300, "estimate")
    assert not decide_trigger(CFG, "m", None, [_user("字" * 299)]).fire


def test_trigger_unknown_states_are_reported_not_guessed():
    assert decide_trigger(CFG, "other-model", 10**9, []).reason == "no_window"
    assert decide_trigger(CFG, "m", None, []).reason == "no_reading"
    assert context_reading(True, [_user("abc")]).source == "estimate"   # bool 不是计数


def test_estimate_counts_tool_call_arguments_and_errs_high_on_chinese():
    """写稿工具的参数就是整篇稿子：只数 content 会严重低估（变异：去掉参数计数 → 红）。"""
    big = _call("c1", args=json.dumps({"content": "稿" * 500}, ensure_ascii=False))
    assert estimate_tokens([big]) > 500
    # 实测中文 1.32–1.39 字/token：估算必须不低于真实值（变异：CHARS_PER_TOKEN=4 → 红）
    assert estimate_tokens([_user("字" * 1320)]) >= 1000


# ---- 判据 2：闭环切片 ----

def _parallel_history() -> list[dict]:
    """一条回复三个并行调用：纯条数切在结果中间就是孤儿 tool 消息 → 服务商 400。"""
    return [
        _sys(), _user("u1"), _say("a1"),
        _user("u2"), _call("c1", "c2", "c3"), _result("c1"), _result("c2"), _result("c3"),
        _say("a2"),
    ]


def test_valid_cuts_only_at_closed_boundaries():
    flags = valid_cuts(_parallel_history())
    assert [i for i, ok in enumerate(flags) if ok] == [0, 1, 2, 3, 4, 8, 9]


def test_cut_never_lands_inside_tool_group():
    """条数预算 3 → 纯条数会切在下标 6（c2 的结果）；必须往后挪到闭环边界 8（变异：纯条数切 → 红）。"""
    history = _parallel_history()
    cut = choose_cut(history, token_budget=10**6, max_messages=3)
    assert cut == 8
    assert history[cut]["role"] != "tool"


def test_cut_refuses_open_history():
    open_history = [_sys(), _user("u"), _call("c1", "c2"), _result("c1")]
    assert not is_closed(open_history)
    with pytest.raises(ValueError, match="未闭环"):
        choose_cut(open_history, token_budget=100, max_messages=20)


def test_valid_cuts_pairs_by_id_not_adjacency():
    """结果之间夹了别的消息也按 id 配对（不假设排列方式）。"""
    history = [_sys(), _call("c1"), _user("夹"), _result("c1"), _say("a")]
    assert valid_cuts(history) == [True, True, False, False, True, True]


def test_cut_never_starts_tail_with_orphan_tool():
    """配不上 call 的 tool 消息（病态历史）同样不能当尾部开头（变异：只看 pending → 红）。"""
    history = [_sys(), _user("u"), _result("ghost"), _say("a")]
    assert valid_cuts(history) == [True, True, False, True, True]


# ---- 判据 5：尾部预算双闸 ----

def test_tail_token_budget_binds_before_message_cap():
    """每条 100 字 = 100 token；预算 250 → 只留 2 条，哪怕条数上限 20（变异：只按条数 → 红）。"""
    history = [_sys()] + [_user("字" * 100) for _ in range(10)]
    assert choose_cut(history, token_budget=250, max_messages=20) == 9


def test_tail_message_cap_binds_before_token_budget():
    history = [_sys()] + [_user("x") for _ in range(30)]
    assert choose_cut(history, token_budget=10**6, max_messages=20) == 11


def test_oversized_tool_group_goes_entirely_to_summary():
    """预算装不下整组 → 切口往后挪（尾部可空），绝不往前挪突破预算。"""
    history = [_sys(), _user("u"), _call("c1"), _result("c1", "长" * 1000)]
    assert choose_cut(history, token_budget=100, max_messages=20) == 4


def test_system_card_never_compacted():
    history = [_sys(), _user("u")]
    assert choose_cut(history, token_budget=10**6, max_messages=20) == 1   # cut ≤ 1 = 没东西可压


# ---- 判据 3：原地改写 ----

def test_apply_compaction_keeps_list_identity():
    """变异：apply_compaction 里 `messages = …` 重绑 → 调用方看到的仍是旧历史 → 红。"""
    main_messages = _parallel_history()
    alias = main_messages
    apply_compaction(main_messages, 8, summary_message("摘要"), card_message("卡"))
    assert alias is main_messages
    assert [m["role"] for m in main_messages] == ["system", "user", "user", "assistant"]
    assert main_messages[0] == _sys()


def test_compacted_history_rejects_cut_inside_group():
    with pytest.raises(ValueError, match="闭环"):
        compacted_history(_parallel_history(), 6, summary_message("摘要"))
    with pytest.raises(ValueError, match="cut"):
        compacted_history(_parallel_history(), 1, summary_message("摘要"))
    with pytest.raises(ValueError, match="标记"):
        compacted_history(_parallel_history(), 8, _user("没标记的摘要"))


# ---- 判据 4：落史标记 ----

def test_marker_invisible_on_wire():
    history = [_sys(), summary_message("摘要"), card_message("卡"), _user("u")]
    wired = _wire_messages(history, "reasoning")
    assert all(COMPACT_KEY not in m for m in wired)
    assert wired[1]["content"].endswith("摘要") and wired[1]["role"] == "user"
    # 会话史本身不动（标记要落 session.jsonl，resume 投影靠它）
    assert history[1][COMPACT_KEY] == MARK_SUMMARY and history[2][COMPACT_KEY] == MARK_CARD


def test_unmarked_history_wires_as_same_object():
    """没有任何内部标记 → 原样返回同一个对象（请求体与改动前逐字节一致）。"""
    plain = [_sys(), _user("u")]
    assert _wire_messages(plain, "reasoning") is plain


def test_second_compaction_drops_old_summary_and_card():
    """变异：compacted_history 不滤旧标记 → 两份摘要套娃 → 红。"""
    history = [_sys(), _user("u0"), summary_message("旧"), card_message("旧卡"), _user("u1"), _say("a1")]
    # 切口在旧摘要之前：旧摘要与旧卡落在尾部里，仍必须被滤掉
    second = compacted_history(history, 2, summary_message("新"))
    summaries = [m for m in second if m.get(COMPACT_KEY) == MARK_SUMMARY]
    assert len(summaries) == 1 and summaries[0]["content"].endswith("新")
    assert not any(m.get(COMPACT_KEY) == MARK_CARD for m in second)
    assert strip_marked(second) == [_sys(), _user("u1"), _say("a1")]


# ---- 判据 7（纯函数部分）：膨胀 ----

def test_bloat_guard():
    region = [_user("字" * 100)]
    assert is_bloated("字" * 80, region)
    assert not is_bloated("字" * 79, region)


# ---- 判据 9（纯函数部分）：双侧脱敏 + 注记 ----

def test_scrub_counts_hits_case_insensitive():
    text, hits = scrub_counted("读 03-audio/manifest.json 和 03-AUDIO/Manifest.JSON")
    assert hits == 2 and "manifest" not in text.casefold()


def test_annotation_when_input_had_restricted_names():
    """输入侧命中 → 摘要附注记，即使摘要正文没提那个文件（变异：只看输出侧命中 → 红）。"""
    out = annotate_redactions("摘要正文", input_hits=1)
    assert out.startswith("摘要正文") and "[已脱敏]" in out and "read_status" in out


def test_output_side_scrubbed_too():
    out = annotate_redactions("模型自己写出 config/agent.local.json", input_hits=0)
    assert "agent.local.json" not in out and "[注]" in out


def test_clean_summary_untouched():
    assert annotate_redactions("干净摘要", input_hits=0) == "干净摘要"
