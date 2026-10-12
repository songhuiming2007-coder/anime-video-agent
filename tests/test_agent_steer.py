"""D79：回合在途时随时发消息、先排队、工具批之后送达（照 Pi 的 steering）。

覆盖 spec `docs/dev/plans/2026-10-12-steer-queue-and-approval-spec.md` §一 ①②③⑤：
工具循环的送达时机、收工前有排队则续一轮、老化边界跳过排队消息、协议读者线程入队与回合收尾的退回 / 续跑。
全部跑在替身上（脚本化 chat_complete、记录型 execute_tool），不打网、不碰 data/。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from pipeline.agent import llm as llm_mod
from pipeline.agent import protocol as proto
from pipeline.agent.aging import AGED_TOOL_MAX_CHARS, age_tool_results
from pipeline.agent.llm import STEER_KEY, _wire_messages
from tests.test_agent_loop import (
    Script,
    assert_paired,
    build_control,
    patch_execute,
    root,  # noqa: F401  (pytest fixture)
    run,
)
from tests.test_agent_tools import tool_call


class Queue:
    """排队替身：第 n 次 drain 时交出预设的消息（模拟人在第 n 个落点之前打了字）。"""

    def __init__(self, plan: dict[int, list[str]]) -> None:
        self.plan = plan
        self.calls = 0
        self.delivered: list[tuple[list[str], str | None]] = []

    def drain(self) -> list[dict[str, str]]:
        self.calls += 1
        return [{"id": f"q{self.calls}-{i}", "text": t} for i, t in enumerate(self.plan.get(self.calls, []))]

    def on_steer(self, ids: list[str], answered: str | None) -> None:
        self.delivered.append((ids, answered))


def _control(messages, q: Queue):
    control = build_control(messages)
    control.drain_steer = q.drain
    control.on_steer = q.on_steer
    return control


def test_st1_steer_lands_after_tool_batch_before_next_request(root: Path, monkeypatch) -> None:
    """ST-1：第一批两个并行调用全部写史之后、第二次请求之前，排队消息作为 user 消息送达。"""
    first = tool_call("read_artifact", {"path": "a.md"}, call_id="a")
    first["tool_calls"] += tool_call("read_artifact", {"path": "b.md"}, call_id="b")["tool_calls"]
    script = Script([first, {"role": "assistant", "content": "改好了"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    messages = [{"role": "user", "content": "读两份"}]
    q = Queue({1: ["改成只读 a"]})
    outcome = run(messages, root, _control(messages, q))

    assert outcome["stopped"] == "done"
    second = script.requests[1]["messages"]
    roles = [m.get("role") for m in second]
    assert roles[-4:] == ["assistant", "tool", "tool", "user"], "排队消息排在这批 tool 结果之后"
    assert second[-1]["content"] == "改成只读 a"
    assert STEER_KEY not in _wire_messages(second, "default")[-1], "内部标记进请求体前剥掉"
    assert_paired(messages)
    assert q.delivered == [(["q1-0"], None)]


def test_st2_no_steer_no_extra_message(root: Path, monkeypatch) -> None:
    """ST-2：队列空 → 消息序列与改造前一致（不插空 user 消息、不多请求）。"""
    script = Script([tool_call("read_artifact", {"path": "a.md"}, call_id="a"),
                     {"role": "assistant", "content": "完"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    messages = [{"role": "user", "content": "读"}]
    q = Queue({})
    outcome = run(messages, root, _control(messages, q))
    assert outcome["llm_calls"] == 2
    assert [m.get("role") for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert q.delivered == []


def test_st3_queued_at_final_answer_continues_the_turn(root: Path, monkeypatch) -> None:
    """ST-3（Pi「有排队就不收工」）：模型已给最终答复时队列非空 → 送达并续一轮；被续上的答复交给通知上屏。"""
    script = Script([{"role": "assistant", "content": "初版答复"}, {"role": "assistant", "content": "按你说的改了"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    messages = [{"role": "user", "content": "说说"}]
    q = Queue({1: ["再短一点"]})
    outcome = run(messages, root, _control(messages, q))

    assert outcome["stopped"] == "done" and outcome["llm_calls"] == 2
    assert outcome["final"]["content"] == "按你说的改了"
    assert script.requests[1]["messages"][-1]["content"] == "再短一点"
    assert q.delivered == [(["q1-0"], "初版答复")]


def test_st4_wrapup_does_not_drain(root: Path, monkeypatch) -> None:
    """ST-4：检查点停 → 收尾请求之前不取队列（收尾 tool_choice=none，排队消息留给回合收尾退回）。"""
    script = Script([tool_call("read_artifact", {"path": "a.md"}, call_id="a"),
                     {"role": "assistant", "content": "收尾"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    monkeypatch.setattr(llm_mod, "CHECKPOINT_EVERY", 1)
    messages = [{"role": "user", "content": "读"}]
    q = Queue({})
    control = _control(messages, q)
    control.ask_checkpoint = lambda snapshot: False
    outcome = run(messages, root, control)
    assert outcome["stopped"] == "checkpoint_stop"
    drains_before_wrapup = q.calls
    assert drains_before_wrapup == 1, "只在工具批之后取过一次，收尾期间不取"


def test_st5_aging_boundary_skips_steer_messages() -> None:
    """ST-5：回合中途的排队消息不算回合边界——本回合前半段的大工具结果不被老化。"""
    big = "x" * (AGED_TOOL_MAX_CHARS + 10)
    convo = [
        {"role": "user", "content": "本回合"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "read_artifact", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": big},
        {"role": "user", "content": "插一句", STEER_KEY: True},
    ]
    aged = age_tool_results(convo)
    assert aged[2]["content"] == big
    # 变异对照：去掉标记，同一条消息就成了边界，前面的结果被老化
    plain = [*convo[:3], {"role": "user", "content": "插一句"}]
    assert age_tool_results(plain)[2]["content"] != big


# ---------------------------------------------------------------------------
# 协议层：读者线程入队、回合收尾
# ---------------------------------------------------------------------------


class _Writer:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def send(self, frame: dict) -> None:
        self.frames.append(frame)


def _slots(in_flight: bool) -> dict:
    return {
        "writer": None, "pending": {}, "in_flight": in_flight, "ready": True, "eof": False,
        "shutdown": False, "turn_id": "t1", "terminating": False, "out": queue.Queue(),
        "interrupt": None, "steer": [], "steer_seq": 0, "steer_lock": threading.Lock(),
    }


def _reader(slots: dict, writer: _Writer) -> proto.FrameReader:
    return proto.FrameReader(-1, writer, slots)  # type: ignore[arg-type]


def test_st6_user_message_in_flight_is_queued_not_busy() -> None:
    """ST-6：回合在途时 user_message 入队并回 queued（带 rid）；不进主循环队列。"""
    writer = _Writer()
    slots = _slots(in_flight=True)
    _reader(slots, writer)._dispatch("user_message", {"t": "user_message", "text": "改成 X"}, "r1")
    assert slots["steer"] == [{"id": "q1", "text": "改成 X"}]
    assert writer.frames == [{"t": "queued", "id": "q1", "text": "改成 X", "rid": "r1"}]
    assert slots["out"].empty()


@pytest.mark.parametrize("frame,kind", [
    ({"t": "user_message", "text": "/compact"}, "user_message"),
    ({"t": "command", "name": "compact", "arg": None}, "command"),
])
def test_st7_commands_still_busy_in_flight(frame: dict, kind: str) -> None:
    """ST-7：命令与 /compact 不当排队消息送给模型，在途时照旧 E_BUSY。"""
    writer = _Writer()
    slots = _slots(in_flight=True)
    _reader(slots, writer)._dispatch(kind, frame, "r1")
    assert slots["steer"] == []
    assert writer.frames[-1]["code"] == "E_BUSY"


def test_st8_idle_user_message_starts_a_turn() -> None:
    """ST-8：空闲时照旧开新回合（不入排队）。"""
    writer = _Writer()
    slots = _slots(in_flight=False)
    _reader(slots, writer)._dispatch("user_message", {"t": "user_message", "text": "你好"}, "r1")
    assert slots["in_flight"] is True and slots["steer"] == []
    assert slots["out"].get_nowait()[0] == "user_message"


class _Host:
    """`_run_turn` 只用到这几个属性；回合本身由 `session` 替身决定结局。"""

    def __init__(self, outcome: dict, during=None) -> None:
        self.steer_source = None
        self.on_steer_delivered = None
        self.root = None
        self._outcome = outcome
        self._during = during
        from pipeline.agent.session import TurnInterrupt

        self.interrupt = TurnInterrupt()

    def set_scope_override(self, _):  # noqa: ANN001
        pass

    def session(self, **_kw):  # noqa: ANN003
        host = self

        class _S:
            def run_turn(self, *_a, **_k):  # noqa: ANN002, ANN003
                if host._during:
                    host._during()
                return dict(host._outcome)

        return _S()


def _run(outcome: dict, during=None) -> tuple[dict, _Writer]:
    writer = _Writer()
    slots = _slots(in_flight=True)
    slots["closed_requests"] = set()
    host = _Host(outcome, during=lambda: during(slots) if during else None)
    proto._run_turn(host, writer, slots, [], None, None, {"text": "开始"}, None)
    return slots, writer


def _enqueue(slots: dict) -> None:
    slots["steer"].append({"id": "q9", "text": "最后才到的"})


def test_st9_leftover_after_done_starts_next_turn() -> None:
    """ST-9：模型收工那一刻才到的排队消息 → 报送达、作为下一回合的用户消息开跑，in_flight 保持为真。"""
    slots, writer = _run({"stopped": "done", "final": {"content": "完"}}, during=_enqueue)
    assert slots["in_flight"] is True
    assert slots["out"].get_nowait() == ("user_message", {"text": "最后才到的"}, None)
    kinds = [f["t"] for f in writer.frames]
    assert kinds.index("steer_delivered") > kinds.index("turn_finished"), "先结完本回合再报送达"
    assert "steer_returned" not in kinds


@pytest.mark.parametrize("stopped", ["interrupted", "error", "checkpoint_stop", "blocked"])
def test_st10_leftover_after_abnormal_stop_is_returned(stopped: str) -> None:
    """ST-10（Pi：abort 把排队还给编辑器）：非正常结束 → steer_returned 退回，in_flight 清掉，不续跑。"""
    slots, writer = _run({"stopped": stopped}, during=_enqueue)
    assert slots["in_flight"] is False and slots["out"].empty()
    returned = [f for f in writer.frames if f["t"] == "steer_returned"]
    assert returned == [{"t": "steer_returned", "items": [{"id": "q9", "text": "最后才到的"}]}]


def test_st11_turn_wires_and_unwires_steer_source() -> None:
    """ST-11：回合期间 host 挂上取队列函数与送达通知，回合结束后摘掉（空闲态不会有人取队列）。"""
    seen: dict = {}

    def during(slots: dict) -> None:
        slots["steer"].append({"id": "q1", "text": "a"})
        seen["drained"] = host_ref["h"].steer_source()
        host_ref["h"].on_steer_delivered(["q1"])

    host_ref: dict = {}
    writer = _Writer()
    slots = _slots(in_flight=True)
    slots["closed_requests"] = set()
    host = _Host({"stopped": "done"}, during=lambda: during(slots))
    host_ref["h"] = host
    proto._run_turn(host, writer, slots, [], None, None, {"text": "开始"}, None)
    assert seen["drained"] == [{"id": "q1", "text": "a"}]
    assert {"t": "steer_delivered", "turn_id": writer.frames[0]["turn_id"], "ids": ["q1"]} in writer.frames
    assert host.steer_source is None and host.on_steer_delivered is None
    assert slots["in_flight"] is False


def test_st12_steer_origin_is_user_in_history() -> None:
    assert proto._role_of("user_steer") == "user"
