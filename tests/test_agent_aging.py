"""工具结果老化（D65，spec `docs/dev/plans/2026-10-10-tool-result-aging-spec.md` §六）。

纯函数用例（§六 1–9）直接打 `aging.age_tool_results`；组合与会话级用例（10/11/13/14）
走真 session / run_tool_loop，LLM 全用脚本替身，不打真网、不碰真 data/。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pipeline.agent import aging, compact as cp
from pipeline.agent import llm as llm_mod
from pipeline.agent import session_log as slog
from pipeline.agent.aging import AGE_HEAD_CHARS, AGED_TOOL_MAX_CHARS, age_tool_results
from pipeline.agent.llm import _WRAPUP_KEY, run_tool_loop
from pipeline.agent.tools import ToolContext
from tests.test_agent_compact_session import Script as CompactScript
from tests.test_agent_compact_session import _cfg as _compact_cfg
from tests.test_agent_compact_session import _main as _compact_main
from tests.test_agent_loop import CFG as LOOP_CFG
from tests.test_agent_loop import Interrupt
from tests.test_agent_loop import Script as LoopScript
from tests.test_agent_loop import build_control, patch_execute
from tests.test_agent_session import (  # noqa: F401 - 夹具按名注入
    FakeChannel,
    _clean_leases,
    _no_real_popen,
    episode,
    root,
)


def _user(text: str, **extra) -> dict:
    return {"role": "user", "content": text, **extra}


def _call(call_id: str, name: str = "read_artifact") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps({"path": "01-topic.md"})},
        }],
    }


def _tool(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _big(n: int, marker: str = "") -> str:
    """造一个长度恰为 n 的大正文；marker（若给）放在末尾，头部是身份区。"""
    head = '{"ok":true,"result":{"path":"01-topic.md","data":"'
    body = head + "填" * (n - len(head) - len(marker))
    assert len(body) + len(marker) == n
    return body + marker


# ---- §六.1 阈值边界 ----

def test_1_threshold_boundary() -> None:
    """恰 4000 不动、4001 老化（变异① ±1 的杀手）。"""
    exact = _big(AGED_TOOL_MAX_CHARS)
    over = _big(AGED_TOOL_MAX_CHARS + 1)
    for content, expect_aged in ((exact, False), (over, True)):
        msgs = [_user("第一回合"), _call("c1"), _tool("c1", content), _user("第二回合")]
        aged = age_tool_results(msgs)
        if expect_aged:
            assert aged[2]["content"].startswith("[工具结果已老化｜原 4001 字符｜")
        else:
            assert aged[2] is msgs[2], "恰在阈值上的消息必须原样（同一对象）"
            assert aged[2]["content"] == exact


# ---- §六.2 当前回合豁免 ----

def test_2_current_turn_exempt() -> None:
    """最后一条 user 之后的 tool 结果（哪怕 100k）原样保留（变异② 的杀手）。"""
    big = _big(100_000)
    msgs = [_user("本回合"), _call("c1"), _tool("c1", big)]
    aged = age_tool_results(msgs)
    assert aged[2] is msgs[2] and aged[2]["content"] == big


# ---- §六.3 wrapup 边界（红队 F1） ----

def test_3_wrapup_message_is_not_a_turn_boundary() -> None:
    """收尾指令（带 _WRAPUP_KEY）不作边界：本回合大结果不被老化；后面有真 user 则按真 user 划界（变异③ 的杀手）。"""
    big = _big(5_000)
    wrapup = _user("（收尾指令）", **{_WRAPUP_KEY: True})
    msgs = [_user("本回合"), _call("c1"), _tool("c1", big), wrapup]
    aged = age_tool_results(msgs)
    assert aged[2] is msgs[2], "wrapup 之后无真 user：收尾指令不算回合边界，大结果豁免"

    msgs2 = [*msgs, {"role": "assistant", "content": "收尾答复"}, _user("下一回合")]
    aged2 = age_tool_results(msgs2)
    assert aged2[2]["content"].startswith("[工具结果已老化｜"), "wrapup 之后有真 user：按真 user 划界"


# ---- §六.4 单调老化 ----

def test_4_monotone_and_stable_projection() -> None:
    """上一回合大结果老化、小结果不动；同一列表两次投影逐字节相等。"""
    big = _big(60_000)
    small = _tool("c2", '{"ok":true,"result":"小结果"}')
    msgs = [
        _user("回合一"), _call("c1"), _tool("c1", big),
        _user("回合二"), _call("c2"), small,
        _user("回合三"),
    ]
    aged = age_tool_results(msgs)
    assert aged[2]["content"].startswith("[工具结果已老化｜原 60000 字符｜")
    assert aged[5] is small, "小结果不动"
    again = age_tool_results(msgs)
    assert json.dumps(aged, ensure_ascii=False) == json.dumps(again, ensure_ascii=False), "同一输入恒得同一输出"
    twice = age_tool_results(aged)
    assert json.dumps(aged, ensure_ascii=False) == json.dumps(twice, ensure_ascii=False), "单调：已老化不再变"


# ---- §六.5 不变异入参 ----

def test_5_input_not_mutated() -> None:
    """投影后原列表每个对象与调用前逐字节相等（变异④ 的杀手）。"""
    big = _big(50_000)
    msgs = [_user("回合一"), _call("c1"), _tool("c1", big), _user("回合二")]
    snapshot = copy.deepcopy(msgs)
    aged = age_tool_results(msgs)
    assert aged is not msgs
    assert msgs == snapshot
    assert all(aged[i] is msgs[i] for i in (0, 1, 3)), "未被老化的消息复用原对象，不是复制件"
    assert aged[2] is not msgs[2], "被老化的那条是复制件"


# ---- §六.6 占位分文案（红队 F4） ----

def test_6_placeholder_copy_per_tool() -> None:
    """run_pipeline 指向 read_status 且不含「重新调用」；只读工具带工具名；查不到名字落通用文案（变异⑤⑥ 的杀手）。"""
    rp = _big(5_000)
    msgs = [
        _user("回合一"),
        _call("c1", "run_pipeline"), _tool("c1", rp),
        _call("c2", "read_artifact"), _tool("c2", _big(5_000)),
        _tool("orphan", _big(5_000)),   # 修复插入的合成结果：tool_call_id 查不到名字
        _user("回合二"),
    ]
    aged = age_tool_results(msgs)
    assert "该结果来自 run_pipeline，需要最新状态请调 read_status" in aged[2]["content"]
    assert "重新调用" not in aged[2]["content"]
    assert "需要全文请重新调用 read_artifact" in aged[4]["content"]
    assert "需要全文请重新调用同一工具" in aged[5]["content"]
    for i in (2, 4, 5):
        assert f"原 5000 字符" in aged[i]["content"]
        assert aged[i]["content"].endswith(_big(5_000)[:AGE_HEAD_CHARS]), "占位必须保留原文前 300 字符"


# ---- §六.7 非 tool 消息免疫 ----

def test_7_non_tool_messages_immune() -> None:
    """大正文 user / assistant 原样保留；assistant 的 tool_calls 参数不动（本 spec 明确不动它）。"""
    big_args = json.dumps({"path": "02-script.md", "content": "稿" * 60_000}, ensure_ascii=False)
    assistant = {
        "role": "assistant", "content": "长回复" * 30_000,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "write_episode_file", "arguments": big_args}}],
    }
    msgs = [_user("旧" * 50_000), assistant, _user("当前回合")]
    aged = age_tool_results(msgs)
    assert aged[0] is msgs[0] and aged[1] is msgs[1]
    assert aged[1]["tool_calls"][0]["function"]["arguments"] == big_args


# ---- §六.8 无 user 消息 ----

def test_8_no_user_message_ages_everything() -> None:
    """整条列表视为「当前回合之前」（防御分支）。"""
    msgs = [{"role": "system", "content": "常驻层"}, _call("c1"), _tool("c1", _big(5_000))]
    aged = age_tool_results(msgs)
    assert aged[0] is msgs[0]
    assert aged[2]["content"].startswith("[工具结果已老化｜")


# ---- §六.9 pre-D52 内容（红队 F3） ----

def test_9_restricted_literal_in_head_is_not_rescrubbed() -> None:
    """含受限字面量的大结果：占位 head = 原文前 300 字符，不额外脱敏（脱敏不是老化的职责，断言层兜底）。

    本用例只锁「老化不引入新内容」：占位里出现的每个字符都来自原文。
    """
    head_zone = '{"ok":true,"path":"03-audio/manifest.json","data":"'
    content = head_zone + "填" * (5_000 - len(head_zone))
    assert "03-audio/manifest.json" in content[:AGE_HEAD_CHARS]
    msgs = [_user("回合一"), _call("c1"), _tool("c1", content), _user("回合二")]
    aged = age_tool_results(msgs)
    placeholder = aged[2]["content"]
    assert placeholder.endswith(content[:AGE_HEAD_CHARS])
    assert "[已脱敏]" not in placeholder, "老化不做脱敏，也不伪造脱敏标记"


# ---- 会话级组合件 ----

def _history_big_then_recent(big: str, *, filler_pairs: int = 10, recent_pairs: int = 2) -> list[dict]:
    """「一条 63.7k 老化结果 + 多条近期小消息」：大结果在较早回合（最后一条 user 之前）才会被老化。

    filler 把切口留在中间（region 非空）；recent 是近回合小消息。小消息 ~3 字符、
    老化占位 ~340 字符、assistant 调用 ~37 字符（CHARS_PER_TOKEN=1，先跑实现再断言切口）。
    """
    msgs: list[dict] = []
    for i in range(filler_pairs):
        msgs += [_user(f"旧{i}"), {"role": "assistant", "content": f"答{i}"}]
    msgs += [_user("开工"), _call("c1"), _tool("c1", big)]
    for i in range(recent_pairs):
        msgs += [_user(f"近{i}"), {"role": "assistant", "content": f"答近{i}"}]
    return msgs


def _compact_once(root: Path, episode: Path, monkeypatch, history: list[dict], summary_text: str):
    """在假仓库里对手造历史跑一次真 `session.compact`（摘要器走替身）。"""
    script = CompactScript(summaries=[{"role": "assistant", "content": summary_text}])
    host, session, messages, tracker = _compact_main(root, episode, monkeypatch, script)
    messages.extend(history)
    result = session.compact(messages, tracker=tracker, root=root, scope="creative",
                             reinject_card="状态卡正文")
    return host, session, messages, script, result


# ---- §六.10 组合：压缩投影之后（D62） ----

def test_10_rebuild_after_compaction_then_age(root: Path, episode: Path, monkeypatch) -> None:
    """先经 rebuild_messages（含 compaction 事件）重建、再老化：不炸；摘要/重注入卡能充当边界。"""
    _compact_cfg(root, window=8_400, tail_ratio=0.05)   # 尾部预算 420：老化占位进尾部、全文进不去
    big = _big(63_698, marker="BIGTAIL")
    host, session, messages, script, result = _compact_once(
        root, episode, monkeypatch, _history_big_then_recent(big), "摘要正文"
    )
    assert result["ok"], result

    loaded = slog.load_session((episode / "session.jsonl").read_bytes(), host.sid)
    rebuilt = slog.rebuild_messages(loaded)
    assert rebuilt[0].get(cp.COMPACT_KEY) == cp.MARK_SUMMARY
    assert any(m.get(cp.COMPACT_KEY) == cp.MARK_CARD for m in rebuilt), "重注入卡重建出来"
    big_msg = next(m for m in rebuilt if m.get("role") == "tool")
    assert big_msg["content"] == big, "重建不动保留尾部的原文"

    aged = age_tool_results(rebuilt)
    summary_aged = next(m for m in aged if isinstance(m, dict) and m.get(cp.COMPACT_KEY) == cp.MARK_SUMMARY)
    assert summary_aged["content"].endswith("摘要正文"), "摘要内容不被老化碰"
    tool_aged = next(m for m in aged if isinstance(m, dict) and m.get("role") == "tool")
    assert tool_aged["content"].startswith("[工具结果已老化｜原 63698 字符｜"), \
        "重建后大结果在最后一条真 user（近1）之前 → 老化投影照常生效（组合不炸）"

    # 摘要/重注入卡充当边界：压缩后尚无新 user 时，带 COMPACT_KEY 的 user 消息就是回合起点
    hand = [
        cp.summary_message("摘要正文"),
        cp.card_message("状态卡正文"),
        _call("c9"), _tool("c9", _big(5_000)), {"role": "assistant", "content": "接着干"},
    ]
    aged_hand = age_tool_results(hand)
    assert aged_hand[3]["content"] == hand[3]["content"], "重注入卡是边界：其后的大结果豁免"
    aged_hand2 = age_tool_results([*hand, _user("新回合")])
    assert aged_hand2[3]["content"].startswith("[工具结果已老化｜"), "真 user 出现后按它划界"


# ---- §六.11 compact 口径（红队 F2a/F2b） ----

def test_11_compact_uses_aged_sizes_for_cut_and_summarizer(root: Path, episode: Path, monkeypatch) -> None:
    """choose_cut 按老化后尺寸计（尾部留下更多）；摘要器 region 里大结果已是占位形态（变异⑦⑧ 的杀手）。"""
    _compact_cfg(root, window=8_400, tail_ratio=0.05)   # 尾部预算 420
    big = _big(63_698, marker="BIGTAIL")
    history = _history_big_then_recent(big)

    # 对拍两把尺（先算清期望值再进断言）：老化后切口留下 tool_big，未老化切口把它挤进摘要区
    budget = 420
    probe = [{"role": "system", "content": "常驻层"}, *history]
    cut_aged = cp.choose_cut(aging.age_tool_results(probe), token_budget=budget, max_messages=20)
    cut_raw = cp.choose_cut(copy.deepcopy(probe), token_budget=budget, max_messages=20)
    assert cut_aged < cut_raw, "夹具必须能区分两把尺：老化后切口更靠前（尾部更长）"
    assert any(m.get("role") == "tool" for m in probe[cut_aged:]), "老化口径：tool_big 进保留尾部"
    assert not any(m.get("role") == "tool" for m in probe[cut_raw:]), "未老化口径：tool_big 被挤出尾部"

    host, session, messages, script, result = _compact_once(root, episode, monkeypatch, history, "摘要正文")
    assert result["ok"], result
    assert result["kept"] == len(probe) - cut_aged, "保留条数按老化后切口"

    request = script.summarizer[0]["messages"]
    body = request[1]["content"]
    assert "[工具结果已老化｜原 63698 字符｜" not in body, "tool_big 在保留尾部，不进摘要区"
    assert "BIGTAIL" not in body and "填" * 100 not in body, "摘要器绝不吃到 63.7k 全文"
    # F2b 的另一面：若 region 没走投影，这里会看见全文——用一条**进摘要区**的大结果锁它
    assert len(script.summarizer) == 1

    assert any(m.get("role") == "tool" and m.get("content") == big for m in messages), \
        "老化后口径下 tool_big 留在保留尾部，全文不动"


def test_11b_summarizer_region_gets_placeholder(root: Path, episode: Path, monkeypatch) -> None:
    """F2b 正对：进摘要区的大结果，摘要器拿到的是占位串而不是全文（变异⑧ 的杀手）。"""
    _compact_cfg(root, window=6_000, tail_ratio=0.05)   # 尾部预算 300：老化占位也进不了尾部
    big_old = _big(20_000, marker="OLDTAIL")
    history = _history_big_then_recent(big_old)
    host, session, messages, script, result = _compact_once(root, episode, monkeypatch, history, "摘要正文")
    assert result["ok"], result
    body = script.summarizer[0]["messages"][1]["content"]
    assert "[工具结果已老化｜原 20000 字符｜" in body, "摘要区里的大结果以占位形态进摘要器"
    assert "OLDTAIL" not in body, "摘要器不吃到被压大结果的全文"

# ---- §六.13 is_bloated 口径（红队 F2 第三处） ----

def test_13_is_bloated_measures_aged_region(root: Path, episode: Path, monkeypatch) -> None:
    """region 全是大结果（老化后 ~1/40）：按老化后尺判膨胀、拒压缩，fail 文案含老化口径（变异⑨ 的杀手）。"""
    _compact_cfg(root, window=100_000, tail_ratio=0.15)   # 预算 15000，尾部上限 20 条
    history = [
        _user("开工"),
        *[m for i in range(5) for m in (_call(f"c{i}"), _tool(f"c{i}", _big(10_000, marker=f"T{i}")))],
        *[m for i in range(11) for m in (_user(f"近{i}"), {"role": "assistant", "content": f"答{i}"})],
    ]
    summary_text = "摘" * 3_000

    # 先证明夹具区分两把尺
    probe = [{"role": "system", "content": "常驻层"}, *history]
    aged_probe = aging.age_tool_results(probe)
    cut = cp.choose_cut(aged_probe, token_budget=15_000, max_messages=20)
    aged_region, raw_region = aged_probe[1:cut], probe[1:cut]
    assert cp.is_bloated(summary_text, aged_region) is True, "老化后 region ~2k，3k 摘要判膨胀"
    assert cp.is_bloated(summary_text, raw_region) is False, "未老化 region ~50k，同一摘要不判膨胀"

    script = CompactScript(summaries=[{"role": "assistant", "content": summary_text}])
    host, session, messages, tracker = _compact_main(root, episode, monkeypatch, script)
    snapshot = copy.deepcopy(history)
    messages.extend(history)
    result = session.compact(messages, tracker=tracker, root=root, scope="creative")
    assert result["ok"] is False and result["reason"] == "bloated"
    assert "老化" in result["text"] and "历史未改动" in result["text"], "fail 文案按老化口径说人话"
    assert messages[1:] == snapshot, "护栏拒压缩：历史不动"


# ---- §六.14 与 llm 集成（含 F1 回归锁） ----

def test_14_run_tool_loop_ages_across_turns_but_wrapup_sees_fresh_result(
    root: Path, monkeypatch,
) -> None:
    """两回合脚本：第二回合首请求里上一回合的 64k 结果已占位、内存 convo 原文一字未动；
    中断触发 _wrapup 后，收尾请求里本回合刚拿到的大结果仍是全文（红队 F1）。
    """
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    big1 = "上回合大结果" + "填" * 64_000
    big2 = "本回合大结果" + "填" * 64_000
    calls = iter([
        {"ok": True, "result": {"data": big1}},
        {"ok": True, "result": {"data": big2}},
    ])
    patch_execute(monkeypatch, lambda name, args, ctx: next(calls))

    # 回合一：user → 工具调用(64k 结果) → 文本收尾
    convo: list[dict] = [{"role": "system", "content": "常驻层"}, _user("回合一")]
    script1 = LoopScript([
        _call("c1"),
        {"role": "assistant", "content": "回合一完成"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script1)
    control1 = build_control(convo)
    out1 = run_tool_loop(convo, ctx=ToolContext(scope="creative", root=root),
                         config=LOOP_CFG, control=control1)
    assert out1["stopped"] == "done"
    req2_tools = [m for m in script1.requests[1]["messages"] if m.get("role") == "tool"]
    assert len(req2_tools) == 1 and big1 in req2_tools[0]["content"], "当回合大结果全量发送（豁免）"

    # 回合二：新 user → 又一次大工具结果 → 模型请求被中断 → wrapup 收尾
    convo.append(_user("回合二"))
    script2 = LoopScript([
        _call("c2"),
        KeyboardInterrupt(),
        {"role": "assistant", "content": "收尾总结"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script2)
    control2 = build_control(convo, interrupt=Interrupt())
    out2 = run_tool_loop(convo, ctx=ToolContext(scope="creative", root=root),
                         config=LOOP_CFG, control=control2)
    assert out2["stopped"] == "interrupted" and out2["wrapup"] == "ok", out2["error"]

    turn2_first = script2.requests[0]["messages"]
    aged_tool = next(m for m in turn2_first if m.get("role") == "tool")
    assert aged_tool["content"].startswith("[工具结果已老化｜原 "), "跨回合：上一回合大结果已占位"
    assert big1 not in aged_tool["content"]

    wrapup_req = script2.requests[-1]
    assert wrapup_req["tool_choice"] == "none"
    wrapup_tools = [m for m in wrapup_req["messages"] if m.get("role") == "tool"]
    assert any(big2 in m["content"] for m in wrapup_tools), "F1：收尾请求里本回合刚拿到的大结果仍是全文"
    aged_in_wrapup = next(m for m in wrapup_tools if m["content"].startswith("[工具结果已老化"))
    assert big1 not in aged_in_wrapup["content"], "上回合的老化结果在收尾里照样占位"
    # 替身绕过了真 chat_complete 的 _wire_messages，剥离在这里显式补做后再断言
    wired = llm_mod._wire_messages(wrapup_req["messages"], "reasoning")
    assert all(_WRAPUP_KEY not in m for m in wired), "内部标记不进请求体"

    # 落盘原文一字未动（loop 级证据：commit 进 convo 的就是会写盘的对象，投影从不改写它）
    on_disk_tools = [m for m in convo if m.get("role") == "tool"]
    assert any(m["content"].endswith(big1) or big1 in m["content"] for m in on_disk_tools)
    assert any(big2 in m["content"] for m in on_disk_tools)
