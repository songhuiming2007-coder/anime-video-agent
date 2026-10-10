"""上下文压缩的会话层（D62 PR2–4，spec §六 判据 1、3、6–14 的会话部分）。

全部在 tmp 假仓库跑；`chat_complete` 用脚本替身（摘要器请求按系统指令识别），
脱敏用例走**真** `chat_complete`（只替换 `urlopen`），让出网断言真的在场。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pipeline.agent import compact as cp
from pipeline.agent import llm as llm_mod
from pipeline.agent import protocol as proto
from pipeline.agent import session_log as slog
from pipeline.agent.assembly import SessionContextTracker
from pipeline.agent.session import AgentSession, SessionHost
from pipeline.agent.session_log import EpisodeLease, acquire_idea_lease
from tests.test_agent_session import (  # noqa: F401 - 夹具按名注入
    FakeChannel,
    _clean_leases,
    _no_real_popen,
    _rebuild,
    episode,
    root,
)


def _cfg(root: Path, *, window: int = 1000, auto: bool = False, tail_ratio: float = 0.15) -> None:
    """窗口 1000、可靠 1.0 → 触发线 600 token、尾部预算 150 token（字数 ÷1）。"""
    (root / "config" / "agent" / "compact.json").write_text(json.dumps({
        "auto_trigger": auto, "trigger_ratio": 0.6, "tail_ratio": tail_ratio, "tail_max_messages": 20,
        "models": {"mock-model": {"window": window, "reliable_ratio": 1.0}},
    }), encoding="utf-8")


class Script:
    """chat_complete 替身：主循环请求按 `replies` 依次答；摘要器请求按 `summaries` 依次答。"""

    def __init__(self, replies=(), summaries=(), usage=()) -> None:
        self.replies = list(replies)
        self.summaries = list(summaries)
        self.usage = list(usage)
        self.main: list[dict] = []
        self.summarizer: list[dict] = []
        self.order: list[str] = []   # "main" / "summary"，按发生顺序

    def __call__(self, messages, tools=None, **kw):
        call = {"messages": copy.deepcopy(messages), "tools": tools, **kw}
        if messages and messages[0].get("content") == cp.SUMMARIZER_INSTRUCTION:
            self.summarizer.append(call)
            self.order.append("summary")
            item = self.summaries.pop(0)
        else:
            self.main.append(call)
            self.order.append("main")
            item = self.replies.pop(0)
            llm_mod._LAST_USAGE.set(self.usage.pop(0) if self.usage else None)
        if isinstance(item, BaseException):
            raise item
        return item


MID = {"window": 10_000, "auto": True, "tail_ratio": 0.05}   # 触发线 6000、尾部 500 token（装得下一组工具调用）
HIGH = {"prompt_tokens": 7000}                                 # 中段实测越线；回合起点的字数估算远不到


def _say(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _read_call(call_id: str = "c1") -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": "read_artifact", "arguments": json.dumps({"path": "01-topic.md"})},
    }]}


def _main(root: Path, episode: Path, monkeypatch, script: Script, *, persist: bool = True):
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    channel = FakeChannel()
    host = SessionHost(episode, root=root, channel=channel)
    host.ensure_lease()
    messages: list[dict] = [{"role": "system", "content": "常驻层"}]   # REPL 形态：messages[0] 不落盘
    if persist:
        host.bind_main(messages)
    session = host.session(persist=persist, scope_mode="creative")
    return host, session, messages, SessionContextTracker()


def _turns(session, messages, tracker, root, n: int) -> None:
    for i in range(n):
        session.run_turn(f"问题{i}", messages=messages, scope="creative", status=None,
                         tracker=tracker, root=root)


def _records(episode: Path) -> list[dict]:
    return [json.loads(line) for line in (episode / "session.jsonl").read_text(encoding="utf-8").splitlines()]


# ---- PR2：手动压缩 ----

def test_manual_compact_rewrites_in_place_and_resume_projects(root, episode, monkeypatch):
    """判据 3、4、11、13：原地改写、摘要带标记、独立 240 s 超时、重建 = 压缩态。"""
    _cfg(root)
    script = Script(replies=[_say("答" * 100) for _ in range(5)], summaries=[_say("摘要正文")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 5)
    alias = messages
    before_last = copy.deepcopy(messages[-2:])

    result = session.compact(messages, tracker=tracker, root=root, scope="creative")

    assert result["ok"], result
    assert alias is messages and host.main_messages is messages
    assert messages[1][cp.COMPACT_KEY] == cp.MARK_SUMMARY and messages[1]["content"].endswith("摘要正文")
    assert messages[-2:] == before_last, "尾部原样保留"
    call = script.summarizer[0]
    assert call["timeout"] == cp.COMPACT_TIMEOUT_S == 240 and call["tools"] is None
    assert "notice" in [k for k, _p in host.channel.shown] and "compacted" in host.channel.codes()
    # resume 投影：重建结果与内存压缩态恒等（变异：rebuild 不认 compaction → 全文回归 → 红）
    assert messages[1:] == _rebuild(episode, host.sid)
    record = [r for r in _records(episode) if r.get("k") == "compaction"][0]
    assert record["trigger"] == "manual" and record["compacted"] == result["compacted"]
    assert record["turn_id"].startswith("compact-")


def test_compact_right_after_resume_writes_into_resumed_session(root, episode, monkeypatch):
    """--continue 后还没跑回合就 /compact：事件必须记在被恢复的会话名下（先写 segment_start），
    否则 sid=null 的事件会被 load_session 滤掉、下次恢复又是全文（变异：不先开段 → 红）。"""
    from pipeline.agent.session import prepare_resume

    _cfg(root)
    script = Script(replies=[_say("答" * 100) for _ in range(4)], summaries=[_say("摘要")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 4)
    sid = host.sid
    host.lease.close()
    EpisodeLease._reset_for_testing()

    host2 = SessionHost(episode, root=root, channel=FakeChannel())
    host2.ensure_lease()
    state = prepare_resume(host2, sid)
    tracker2 = SessionContextTracker(resident_prompt="常驻层")
    resumed = [{"role": "system", "content": "常驻层"}, *state["messages"]]
    host2.bind_main(resumed)
    session2 = host2.session(persist=True, scope_mode="creative")
    assert session2.compact(resumed, tracker=tracker2, root=root, scope="creative")["ok"]
    assert host2.sid == sid
    assert resumed[1:] == _rebuild(episode, sid)


def test_resume_after_compaction_forgets_compacted_docs(root, episode, monkeypatch):
    """审查 #1：被压进摘要的规程，--continue 后也不能再算「已注入」，否则这期剩下的时间模型都看不到它
    （变异：prepare_resume 不认 compaction → 红）。"""
    from pipeline.agent.session import prepare_resume

    _cfg(root)
    (root / "docs").mkdir()
    (root / "docs" / "old.md").write_text("旧规程正文" * 10, encoding="utf-8")
    script = Script(replies=[_say("答" * 100) for _ in range(4)], summaries=[_say("摘要")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 1)
    session._commit({"role": "user", "content": "旧规程正文" * 10}, "injection",
                    docs=[{"path": "docs/old.md", "sha256": "x"}])
    tracker.injected_paths = {"docs/old.md"}
    _turns(session, messages, tracker, root, 3)
    assert session.compact(messages, tracker=tracker, root=root, scope="creative")["ok"]
    assert tracker.injected_paths == set()
    sid = host.sid
    host.lease.close()
    EpisodeLease._reset_for_testing()
    host2 = SessionHost(episode, root=root, channel=FakeChannel())
    host2.ensure_lease()
    state = prepare_resume(host2, sid)
    assert "docs/old.md" not in state["docs"] and state["step_key"] is None


def test_compact_on_empty_history_is_nothing_not_crash(root, episode, monkeypatch):
    """审查 #4：新会话还没对话就 /compact → 「没东西可压」，不许 StopIteration 把 REPL 带崩。"""
    _cfg(root)
    host, session, messages, tracker = _main(root, episode, monkeypatch, Script())
    messages.clear()
    assert session.compact(messages, tracker=tracker, root=root, scope="creative")["reason"] == "nothing"
    assert cp.choose_cut([], token_budget=10, max_messages=5) == 1


def test_auto_compaction_records_estimate_source(root, episode, monkeypatch):
    """审查 #5：服务商从没给过 usage 时，压缩事件不许把字数估算记成实测（变异：读数口径丢失 → 红）。"""
    _cfg(root, auto=True)
    script = Script(replies=[_say("答" * 150) for _ in range(5)], summaries=[_say("摘要")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 5)
    record = [r for r in _records(episode) if r.get("k") == "compaction"][0]
    assert record["tokens_before_source"] == "estimate" and record["tokens_after_source"] == "estimate"


def test_turn_after_compaction_appends_and_still_rebuilds(root, episode, monkeypatch):
    _cfg(root)
    script = Script(replies=[_say("答" * 100) for _ in range(5)], summaries=[_say("摘要")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 4)
    assert session.compact(messages, tracker=tracker, root=root, scope="creative")["ok"]
    session.run_turn("压缩后再问", messages=messages, scope="creative", status=None,
                     tracker=tracker, root=root)
    sent = script.main[-1]["messages"]
    assert sent[1][cp.COMPACT_KEY] == cp.MARK_SUMMARY, "压缩后的下一次请求从摘要开始"
    assert messages[1:] == _rebuild(episode, host.sid)


@pytest.mark.parametrize("summary, reason", [
    (llm_mod.LLMError("上游 502"), "error"),
    (_say(""), "empty"),
    (_say("长" * 10_000), "bloated"),
])
def test_summarizer_failure_leaves_history_and_disables_auto(root, episode, monkeypatch, summary, reason):
    """判据 7：失败 / 空摘要 / 膨胀 → 历史不动、本会话禁自动压缩（变异：失败照压 → 红）。"""
    _cfg(root)
    script = Script(replies=[_say("答" * 100) for _ in range(4)], summaries=[summary])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 4)
    snapshot = copy.deepcopy(messages)
    result = session.compact(messages, tracker=tracker, root=root, scope="creative")
    assert not result["ok"] and result["reason"] == reason
    assert messages == snapshot and session.auto_compact_disabled
    assert not any(r.get("k") == "compaction" for r in _records(episode))


def test_unknown_model_window_is_refused_not_guessed(root, episode, monkeypatch):
    (root / "config" / "agent" / "compact.json").write_text(json.dumps({"models": {}}), encoding="utf-8")
    script = Script(replies=[_say("答" * 100) for _ in range(3)])
    _host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    result = session.compact(messages, tracker=tracker, root=root, scope="creative")
    assert result["reason"] == "no_window" and "mock-model" in result["text"]
    assert script.summarizer == []


def test_nothing_to_compact_when_all_fits_tail(root, episode, monkeypatch):
    _cfg(root, window=1_000_000)
    script = Script(replies=[_say("短")])
    _host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 1)
    assert session.compact(messages, tracker=tracker, root=root, scope="creative")["reason"] == "nothing"


def test_subsession_cannot_compact(root, episode, monkeypatch):
    """判据 12：persist=False 子会话不压缩，也不装自动触发点（变异：子会话也挂 compact → 红）。"""
    _cfg(root, auto=True)
    script = Script()
    _host, session, messages, tracker = _main(root, episode, monkeypatch, script, persist=False)
    assert session.compact(messages, tracker=tracker, root=root)["reason"] == "not_main"
    control = session._control(turn_id="t", status=None, root=root, approve_cb=None)
    assert control.compact is None


def test_compacted_runbook_is_unregistered_for_reinjection(root, episode, monkeypatch):
    """压缩发现 #1：注入过的规程压进摘要后，`injected_paths` 必须清掉它（变异：不清 → 红），
    还留在尾部里的照旧登记（变异：全清 → 红）。"""
    _cfg(root)
    (root / "docs").mkdir()
    (root / "docs" / "old.md").write_text("旧规程正文" * 10, encoding="utf-8")
    (root / "docs" / "kept.md").write_text("近期规程", encoding="utf-8")
    script = Script(replies=[_say("答" * 100) for _ in range(4)], summaries=[_say("摘要")])
    _host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 1)
    messages.insert(1, {"role": "user", "content": "旧规程正文" * 10})
    _turns(session, messages, tracker, root, 3)
    messages.append({"role": "user", "content": "[系统提示更新]\n\n近期规程"})
    tracker.injected_paths = {"docs/old.md", "docs/kept.md"}
    tracker.active_step_key = "02"
    assert session.compact(messages, tracker=tracker, root=root, scope="creative")["ok"]
    assert tracker.injected_paths == {"docs/kept.md"}
    assert tracker.active_step_key is None


def test_input_side_scrub_lets_real_egress_assert_pass(root, episode, monkeypatch):
    """判据 9：历史含受限文件名也能压缩；摘要无脏串且带注记。走真 chat_complete + 真出网断言，
    只换 urlopen（变异：输入不脱敏 → 断言抛 PermissionError → reason=error → 红）。"""
    _cfg(root)
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    sent: list[dict] = []

    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout=None):
        sent.append({"body": json.loads(request.data.decode("utf-8")), "timeout": timeout})
        return _Resp(json.dumps({"choices": [{"message": {"role": "assistant", "content": "摘要：读过 [已脱敏]"}}]}).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    channel = FakeChannel()
    host = SessionHost(episode, root=root, channel=channel)
    host.ensure_lease().begin("sid-scrub")
    host.sid = "sid-scrub"
    messages = [{"role": "system", "content": "常驻层"},
                {"role": "user", "content": "看看 03-audio/manifest.json 里第三句" + "字" * 300},
                _say("好" * 300), {"role": "user", "content": "继续"}, _say("嗯")]
    host.bind_main(messages)
    session = host.session(persist=True, scope_mode="creative")
    result = session.compact(messages, tracker=SessionContextTracker(), root=root, scope="creative")
    assert result["ok"], result
    assert "manifest.json" not in json.dumps(sent[0]["body"], ensure_ascii=False)
    assert sent[0]["timeout"] == 240
    assert "[注]" in messages[1]["content"] and "manifest.json" not in messages[1]["content"]


# ---- PR3：自动双触发 ----

def test_auto_trigger_off_by_default(root, episode, monkeypatch):
    """人裁决④：实测前不自动压缩，哪怕早已越线（变异：默认开 → 红）。"""
    (root / "config" / "agent" / "compact.json").write_text(json.dumps({   # 不写 auto_trigger：测缺省值
        "models": {"mock-model": {"window": 1000, "reliable_ratio": 1.0}},
    }), encoding="utf-8")
    script = Script(replies=[_say("答" * 400) for _ in range(4)], usage=[{"prompt_tokens": 10_000}] * 4)
    _host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 4)
    assert script.summarizer == [] and not any(cp.is_marked(m) for m in messages)


def test_auto_mid_loop_compaction_with_card_and_turn_end_fields(root, episode, monkeypatch):
    """判据 1、6、7a：工具结果闭环写史后、下一次请求前压缩；重注入卡来自磁盘、只插一次；
    当场 notice + turn_end 带三字段；重建 = 压缩态。"""
    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), _say("看完了")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH, {"prompt_tokens": 200}]
    script = Script(replies=replies, summaries=[_say("自动摘要")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    disk = {"n": 0}

    def card_from_disk(scope, status):
        disk["n"] += 1
        return f"待审批: 3 项（卡第 {disk['n']} 次从磁盘重建）"

    monkeypatch.setattr(session, "_status_card", card_from_disk)
    _turns(session, messages, tracker, root, 3)
    assert script.summarizer == []
    outcome = session.run_turn("读一下选题", messages=messages, scope="creative", status=None,
                               tracker=tracker, root=root)

    assert outcome["stopped"] == "done" and outcome["compacted"]
    assert script.order == ["main"] * 4 + ["summary", "main"], "压缩发生在工具结果之后、下一次请求之前"
    last_request = script.main[-1]["messages"]
    assert last_request[1][cp.COMPACT_KEY] == cp.MARK_SUMMARY
    cards = [m for m in last_request if cp.is_marked(m, cp.MARK_CARD)]
    assert len(cards) == 1 and "待审批: 3 项" in cards[0]["content"], "判据 8：卡含待审批行"
    # 压缩发生在工具结果之后：tool 消息与它的调用都在尾部、配对完整
    assert cp.is_closed(last_request)
    turn_end = [r for r in _records(episode) if r.get("k") == "turn_end"][-1]
    assert turn_end["compacted"] is True
    assert turn_end["tokens_before"] == outcome["tokens_before"] and turn_end["tokens_after"] == outcome["tokens_after"]
    assert turn_end["tokens_before"] > turn_end["tokens_after"]
    assert "compacted" in host.channel.codes()
    assert messages[1:] == _rebuild(episode, host.sid)


def test_auto_trigger_at_turn_start_uses_estimate_when_usage_none(root, episode, monkeypatch):
    """判据 1：服务商不给 usage → 字数估算照样触发（变异：None 直接不触发 → 红）。"""
    _cfg(root, auto=True)
    script = Script(replies=[_say("答" * 150) for _ in range(5)], summaries=[_say("摘要")])
    _host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 5)
    assert len(script.summarizer) >= 1
    assert any(cp.is_marked(m, cp.MARK_SUMMARY) for m in messages)


def test_auto_failure_aborts_turn_without_wrapup(root, episode, monkeypatch):
    """判据 7：自动压缩失败 → 回合停（compact_failed）、不做收尾请求、本会话禁自动压缩。"""
    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH]
    script = Script(replies=replies, summaries=[llm_mod.LLMError("超时")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    outcome = session.run_turn("读", messages=messages, scope="creative", status=None,
                               tracker=tracker, root=root)
    assert outcome["stopped"] == "compact_failed" and outcome["wrapup"] == "skipped"
    assert script.order == ["main"] * 4 + ["summary"], "失败后不许再发收尾请求"
    assert session.auto_compact_disabled and "compact_failed" in host.channel.codes()
    assert not any(cp.is_marked(m) for m in messages)
    assert cp.is_closed(messages)


def test_rollback_goes_to_compaction_point(root, episode, monkeypatch):
    """判据 10（spec §九 偏离 10）：压缩后同回合撞出网断言 → 只回滚到压缩点；回合起点的快照已失效，
    照它 del 是空操作 = 假回滚（变异：不用压缩点水位 → 红）。内存与重建恒等、当场告诉人。"""
    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), PermissionError("拦截出网请求")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH]
    script = Script(replies=replies, summaries=[_say("摘要")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    outcome = session.run_turn("读", messages=messages, scope="creative", status=None,
                               tracker=tracker, root=root)
    assert outcome["stopped"] == "blocked" and outcome["compacted"] is True
    assert cp.is_marked(messages[1], cp.MARK_SUMMARY)
    assert messages[-1]["role"] == "tool" and messages[-1]["tool_call_id"] == "c1", "压缩点之后什么都没提交，停在压缩点"
    assert "rollback_to_compaction" in host.channel.codes()
    assert any(r.get("k") == "turn_rollback" for r in _records(episode))
    assert messages[1:] == _rebuild(episode, host.sid)


def test_rollback_to_compaction_point_drops_later_messages(root, episode, monkeypatch):
    """压缩点之后又提交了消息再撞断言：那些消息被回滚掉，压缩保留；重建同样滤掉它们。"""
    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), _read_call("c2"),
                                                       PermissionError("拦截出网请求")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH, {"prompt_tokens": 200}]
    script = Script(replies=replies, summaries=[_say("摘要")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    session.run_turn("读", messages=messages, scope="creative", status=None, tracker=tracker, root=root)
    ids = [m.get("tool_call_id") for m in messages if m.get("role") == "tool"]
    assert ids == ["c1"], "c2 那组在压缩点之后提交，必须随回滚消失"
    assert cp.is_marked(messages[1], cp.MARK_SUMMARY)
    assert messages[1:] == _rebuild(episode, host.sid)


def test_auto_compaction_refused_when_result_would_fail_egress(root, episode, monkeypatch):
    """审查 #2：模型在工具参数里写了受限文件名（参数不脱敏，ADR-0026 双保险）——压缩后它留在尾部、
    请求必被拦。预检不过就不压，撞断言时照常整回合回滚自愈（变异：不预检 → 红）。"""
    _cfg(root, **MID)
    dirty = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "c1", "type": "function",
        "function": {"name": "read_artifact", "arguments": json.dumps({"path": "03-audio/manifest.json"})},
    }]}
    replies = [_say("答" * 150) for _ in range(3)] + [dirty, PermissionError("拦截出网请求")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH]
    script = Script(replies=replies, summaries=[_say("摘要")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    before = copy.deepcopy(messages)
    outcome = session.run_turn("读", messages=messages, scope="creative", status=None,
                               tracker=tracker, root=root)
    assert script.summarizer == [], "预检在摘要器之前：过不了就不白调一次"
    assert not any(r.get("k") == "compaction" for r in _records(episode))
    assert outcome["stopped"] == "blocked" and outcome["compacted"] is False
    assert messages == before, "没压缩 → 整回合回滚，脏串随之消失"
    assert "compact_skipped" in host.channel.codes()
    # 复核 B：预检不过只跳过这一次，不关本会话的自动压缩（变异：照样禁用 → 红）
    assert session.auto_compact_disabled is False


def test_sigint_landing_mid_write_keeps_disk_and_memory_together(root, episode, monkeypatch):
    """审查 #3：真 SIGINT 落在压缩事件写盘那一刻：改写与记账照样完成，中断随后才浮出；
    turn_end 如实记 compacted、内存 = 重建、没有重复的工具结果（变异：去掉 SIGINT 屏蔽 → 红）。"""
    import os
    import signal

    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), _say("收尾")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH, None]
    script = Script(replies=replies, summaries=[_say("摘要")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    real_append = host.lease.append

    def append(record):
        real_append(record)
        if record.get("k") == "compaction":
            os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(host.lease, "append", append)
    # 复核 A：进程里另有线程时，发给进程的 SIGINT 可能由那个线程接收（REPL 常驻事件发布线程）
    import threading

    stop = threading.Event()
    sidecar = threading.Thread(target=stop.wait, daemon=True)
    sidecar.start()
    try:
        outcome = session.run_turn("读", messages=messages, scope="creative", status=None,
                                   tracker=tracker, root=root)
    finally:
        stop.set()
    assert outcome["stopped"] == "interrupted" and outcome["compacted"] is True
    assert cp.is_marked(messages[1], cp.MARK_SUMMARY)
    assert [m["tool_call_id"] for m in messages if m.get("role") == "tool"] == ["c1"]
    turn_end = [r for r in _records(episode) if r.get("k") == "turn_end"][-1]
    assert turn_end["compacted"] is True
    assert messages[1:] == _rebuild(episode, host.sid)


def test_interrupt_during_auto_compaction_synthesizes_nothing(root, episode, monkeypatch):
    """压缩中途被中断：触发点处已没有悬着的调用，落点表不许再补合成结果（否则同 id 两条 tool → 400）。"""
    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), _say("收尾")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH, None]
    script = Script(replies=replies, summaries=[KeyboardInterrupt()], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    outcome = session.run_turn("读", messages=messages, scope="creative", status=None,
                               tracker=tracker, root=root)
    assert outcome["stopped"] == "interrupted"
    tool_ids = [m["tool_call_id"] for m in messages if m.get("role") == "tool"]
    assert tool_ids == ["c1"]
    assert messages[1:] == _rebuild(episode, host.sid)


# ---- PR4：idea 会话 ----

def test_idea_auto_compaction_skips_card(root, monkeypatch):
    """判据 14：idea 会话同一套触发与切片，跳过重注入（静态卡本就在 messages[0]；变异：照插卡 → 红）。"""
    _cfg(root, **MID)
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), _say("好")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH, None]
    script = Script(replies=replies, summaries=[_say("选题摘要")], usage=usage)
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    channel = FakeChannel()
    host = SessionHost(None, root=root, channel=channel, log_dir=root / "data" / "_idea")
    host.lease = acquire_idea_lease(root)
    messages: list[dict] = [{"role": "system", "content": "常驻层"}]
    host.bind_main(messages)
    session = host.session(persist=True, scope_mode="idea")
    tracker = SessionContextTracker()
    for i in range(4):
        session.run_turn(f"想法{i}", messages=messages, scope="idea", status=None,
                         tracker=tracker, root=root, ep_dir=None)
    assert script.order == ["main"] * 4 + ["summary", "main"]
    assert cp.is_marked(messages[1], cp.MARK_SUMMARY)
    assert not any(cp.is_marked(m, cp.MARK_CARD) for m in messages)
    assert messages[1:] == _rebuild(root / "data" / "_idea", host.sid)


# ---- 日志投影（纯函数） ----

def _rec(seq, k, **kw):
    return {"sid": "s", "seq": seq, "k": k, **kw}


def test_rebuild_projection_and_repairs_after_compaction():
    """判据 11：压缩前的修复锚点作废；plan_repairs 只修压缩之后的未配对调用（先投影再修）。"""
    summary = cp.summary_message("摘")
    card = cp.card_message("卡")
    records = [
        _rec(1, "session_start", schema=slog.SCHEMA),
        _rec(2, "msg", turn_id="t1", message={"role": "user", "content": "旧"}),
        _rec(3, "msg", turn_id="t1", message=_read_call("old")),
        _rec(4, "repair_tool_results", after_seq=3, results=[{"tool_call_id": "old", "content": "{}"}]),
        _rec(5, "compaction", summary=summary, card=card, tail=[{"role": "user", "content": "尾"}]),
        _rec(6, "msg", turn_id="t2", message=_read_call("new")),
    ]
    loaded = slog.LoadedSession("s", records)
    assert slog.rebuild_messages(loaded) == [summary, card, {"role": "user", "content": "尾"}, _read_call("new")]
    # 压缩前那个调用缺结果（去掉它的修复行）：也不许补——补出来插不回去（变异：照补 → 红）
    unrepaired = slog.LoadedSession("s", [r for r in records if r["k"] != "repair_tool_results"])
    repairs = slog.plan_repairs(unrepaired)
    fixed = [r for r in repairs if r["k"] == "repair_tool_results"]
    assert [item["tool_call_id"] for r in fixed for item in r["results"]] == ["new"]


# ---- 协议 ----

class _Writer:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def send(self, frame: dict) -> None:
        self.frames.append(frame)


def test_protocol_compact_command(root, episode, monkeypatch):
    _cfg(root)
    assert "compact" in proto._COMMANDS
    script = Script(replies=[_say("答" * 100) for _ in range(4)], summaries=[_say("摘要")])
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 4)
    writer = _Writer()
    proto._run_command(host, writer, {"scope_override": None}, {"name": "compact"}, "r1",
                       messages=messages, tracker=tracker, ep_dir=episode)
    result = writer.frames[-1]
    assert result["t"] == "command_result" and result["name"] == "compact" and result["ok"] is True
    assert result["rid"] == "r1"
    assert cp.is_marked(messages[1], cp.MARK_SUMMARY)


def test_protocol_turn_finished_carries_compaction_fields(root, episode, monkeypatch):
    """审查 #6：desktop 读数消费的是协议帧 turn_finished，不是 session.jsonl（变异：帧里不带 → 红）。"""
    from pipeline.agent.session import TurnInterrupt

    _cfg(root, **MID)
    replies = [_say("答" * 150) for _ in range(3)] + [_read_call("c1"), _say("看完了")]
    usage = [{"prompt_tokens": 100}] * 3 + [HIGH, {"prompt_tokens": 200}]
    script = Script(replies=replies, summaries=[_say("摘要")], usage=usage)
    host, session, messages, tracker = _main(root, episode, monkeypatch, script)
    _turns(session, messages, tracker, root, 3)
    writer = _Writer()
    slots = {"turn_id": None, "closed_requests": set(), "scope_override": None, "in_flight": True,
             "interrupt": TurnInterrupt()}
    proto._run_turn(host, writer, slots, messages, tracker, episode, {"text": "读"}, None)
    finished = [f for f in writer.frames if f.get("t") == "turn_finished"][-1]
    assert finished["compacted"] is True
    assert finished["tokens_before"] > finished["tokens_after"] > 0
