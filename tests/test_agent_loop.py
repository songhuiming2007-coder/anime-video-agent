"""工具循环（Spec 9 §7.1 TL-*，PR1）。

一切都在替身上跑：`chat_complete` 换成脚本机，`execute_tool` 换成记录器，人审与检查点
由 `LoopControl` 注入。**不打真网、不碰真 data/**。

PR1 覆盖 TL-1~TL-16；TL-9b（真 `TurnInterrupt` + 真信号）与 TL-9d/17（会话级）
在 PR3 补齐，见 `tests/test_agent_session.py` 与文件末尾。
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import signal
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from pipeline.agent import llm as llm_mod
from pipeline.agent.llm import (
    CHECKPOINT_EVERY,
    WRAPUP_INSTRUCTION,
    Decision,
    LLMError,
    LoopControl,
    chat_complete,
    run_tool_loop,
)
from pipeline.agent.session import TurnInterrupt
from pipeline.agent.tools import ToolContext
from tests.test_agent_tools import make_agent_root, tool_call

ROOT_URL = "http://127.0.0.1:9/v1"
CFG = llm_mod.LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="sk-test")


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class Script:
    """脚本化 `chat_complete`：按序回放，末条重复；记录每次请求的上下文。"""

    def __init__(self, steps: list) -> None:
        self.steps = list(steps)
        self.requests: list[dict] = []

    def __call__(self, messages, tools=None, **kwargs):  # noqa: ANN001
        self.requests.append({
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "tool_choice": kwargs.get("tool_choice"),
        })
        step = self.steps[min(len(self.requests) - 1, len(self.steps) - 1)]
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            step = step(len(self.requests))
        return copy.deepcopy(step)


class Reviewer:
    """记录每次人审调用；默认放行（`card_free`：免卡，不清空判重表）。"""

    def __init__(self, decide=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._decide = decide

    def __call__(self, name: str, args: dict) -> Decision:
        self.calls.append((name, copy.deepcopy(args)))
        if self._decide is not None:
            return self._decide(name, args)
        return Decision(ok=True, provenance="card_free")


class Interrupt:
    """`TurnInterrupt` 的最小替身：defer 区内到达的中断推迟到退出时浮出（§2.2 第 3 条）。"""

    def __init__(self) -> None:
        self.wrapup_started = False
        self.skip_wrapup = False
        self.depth = 0
        self.pending = 0
        self._absorb_depth = 0
        self.dropped = 0

    @contextlib.contextmanager
    def defer(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if self.depth == 0 and self.pending:
                count, self.pending = self.pending, 0
                if self._absorb_depth:
                    self.dropped += count
                else:
                    raise KeyboardInterrupt

    @contextlib.contextmanager
    def absorb(self):
        """「只置标志、不抛」区：区内到达的中断在回合结束时丢弃（§2.2 状态表）。"""
        self._absorb_depth += 1
        try:
            yield
        finally:
            self._absorb_depth -= 1

    @contextlib.contextmanager
    def absorbed(self):
        with self.absorb():
            with self.defer():
                yield

    def request(self) -> None:
        """模拟信号落点：延迟区内只置 pending；区外等价于直接打断当前帧。"""
        if self.depth:
            self.pending += 1
        else:
            raise KeyboardInterrupt


def build_control(
    messages: list[dict],
    *,
    review=None,
    ask_checkpoint=None,
    interrupt=None,
    critical_tools=frozenset(),
    post_execute=None,
    on_exec_started=None,
    on_trace=None,
) -> LoopControl:
    """按 Spec 9 §4.1 组一个 control；`commit` 与循环共用同一个列表对象（§2.5）。"""
    commits: list[tuple[str, dict]] = []

    def commit(message: dict, origin: str) -> None:
        commits.append((origin, copy.deepcopy(message)))
        messages.append(message)

    control = LoopControl(
        interrupt=interrupt,
        commit=commit,
        review=review or Reviewer(),
        ask_checkpoint=ask_checkpoint or (lambda snapshot: True),
        post_execute=post_execute,
        on_exec_started=on_exec_started,
        on_trace=on_trace,
        critical_tools=critical_tools,
    )
    control.commits = commits  # type: ignore[attr-defined]
    return control


def patch_execute(monkeypatch, handler=None):
    """换掉 `execute_tool`：记录 (name, args, confirmed)，默认返回成功。"""
    executed: list[tuple[str, dict, bool]] = []

    def fake_execute(name: str, args: dict, ctx) -> dict:
        executed.append((name, copy.deepcopy(args), ctx.confirmed))
        if handler is not None:
            return handler(name, args, ctx)
        return {"ok": True, "result": {"echo": name}}

    monkeypatch.setattr(llm_mod, "execute_tool", fake_execute)
    return executed


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_agent_root(tmp_path / "repo", ROOT_URL)


def run(messages: list[dict], root: Path, control: LoopControl) -> dict:
    return run_tool_loop(
        messages, ctx=ToolContext(scope="creative", root=root), config=CFG, control=control
    )


def tool_messages(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") == "tool"]


def assert_paired(messages: list[dict]) -> None:
    """每条带 tool_calls 的 assistant，紧随其后的连续 tool 消息恰好覆盖它的全部 id。"""
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = message.get("tool_calls") or []
        if calls:
            ids = [str(c.get("id")) for c in calls]
            following: list[str] = []
            cursor = index + 1
            while cursor < len(messages) and messages[cursor].get("role") == "tool":
                following.append(messages[cursor].get("tool_call_id"))
                cursor += 1
            assert following == ids, (following, ids)
            index = cursor
            continue
        index += 1


def _replies(count: int, *, name: str = "read_artifact", prefix: str = "c") -> list[dict]:
    """count 条各带 1 个调用的回复，参数互不相同（避开判重）。"""
    return [
        tool_call(name, {"path": f"{prefix}{i}.md"}, call_id=f"{prefix}{i}")
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# TL-1 / TL-2 / TL-2b / TL-2c：检查点
# ---------------------------------------------------------------------------


def test_tl1_sixty_replies_then_done(root: Path, monkeypatch) -> None:
    """TL-1：60 条工具回复后给最终答复；检查点答「继续」→ done、llm_calls == 61。"""
    replies = _replies(60) + [{"role": "assistant", "content": "最终答复"}]
    script = Script(replies)
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    asked: list[dict] = []

    def ask(snapshot: dict) -> bool:
        asked.append(snapshot)
        return True  # 继续

    messages = [{"role": "user", "content": "干到完成"}]
    outcome = run(messages, root, build_control(messages, ask_checkpoint=ask))

    assert outcome["stopped"] == "done"
    assert outcome["llm_calls"] == 61
    assert outcome["checkpoints"] == 1
    assert outcome["tool_executions"] == 60
    assert asked[0]["llm_calls"] == CHECKPOINT_EVERY
    assert asked[0]["trigger"] == "replies"
    assert outcome["final"]["content"] == "最终答复"
    assert_paired(messages)


def test_tl2_checkpoint_stop_wraps_up_without_tools(root: Path, monkeypatch) -> None:
    """TL-2：无限回复 + 检查点答「停止」→ 提问时恰 50，收尾带 tool_choice:"none"。"""
    script = Script(_replies(50, prefix="loop") + [{"role": "assistant", "content": "收尾答复"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    asked: list[dict] = []

    def ask(snapshot: dict) -> bool:
        asked.append(snapshot)
        assert len(asked) == 1, "检查点只该问一次"
        return False  # 停止

    messages = [{"role": "user", "content": "无限循环"}]
    outcome = run(messages, root, build_control(messages, ask_checkpoint=ask))

    assert outcome["stopped"] == "checkpoint_stop"
    assert asked[0]["llm_calls"] == 50
    assert asked[0]["trigger"] == "replies"
    assert outcome["wrapup"] == "ok"
    assert outcome["final"]["role"] == "assistant"
    assert outcome["final"]["content"] == "收尾答复"
    assert outcome["llm_calls"] == 51

    wrapup_request = script.requests[-1]
    assert wrapup_request["tool_choice"] == "none"
    assert wrapup_request["tools"] == script.requests[-2]["tools"], "收尾保留同一份 tools（前缀缓存）"
    assert any(
        m.get("role") == "user" and m.get("content") == WRAPUP_INSTRUCTION.replace("{reason}", "检查点停止")
        for m in wrapup_request["messages"]
    )
    assert_paired(messages)


def test_tl2b_execution_checkpoint_triggers_inside_reply(root: Path, monkeypatch) -> None:
    """TL-2b：每条回复 7 个并行只读调用（参数各异）→ 第 8 条回复内、第 51 次执行之前提问。"""
    def reply(turn: int) -> dict:
        if turn > 8:
            return {"role": "assistant", "content": "收尾"}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": f"p{turn}_{i}", "type": "function",
                 "function": {"name": "read_artifact",
                              "arguments": json.dumps({"path": f"p{turn}_{i}.md"})}}
                for i in range(7)
            ],
        }

    script = Script([reply])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    asked: list[dict] = []

    def ask(snapshot: dict) -> bool:
        asked.append(snapshot)
        return False

    messages = [{"role": "user", "content": "并行轰炸"}]
    outcome = run(messages, root, build_control(messages, ask_checkpoint=ask))

    assert asked[0]["trigger"] == "executions"
    assert asked[0]["tool_executions"] == CHECKPOINT_EVERY
    assert asked[0]["llm_calls"] == 8, "第 8 条回复内触发"
    assert outcome["stopped"] == "checkpoint_stop"
    assert outcome["tool_executions"] == CHECKPOINT_EVERY


def test_tl2c_parallel_calls_are_constrained_one_by_one(root: Path, monkeypatch) -> None:
    """TL-2c：一条回复 60 个并行调用；第 51 次执行之前提问，其余补「未执行：检查点停止」。"""
    calls = [tool_call("read_artifact", {"path": f"x{i}.md"}, call_id=f"x{i}") for i in range(60)]
    script = Script([{"role": "assistant", "content": None, "tool_calls": [c["tool_calls"][0] for c in calls]},
                     {"role": "assistant", "content": "收尾"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    executed = patch_execute(monkeypatch)

    asked: list[dict] = []

    def ask(snapshot: dict) -> bool:
        asked.append(snapshot)
        return False

    messages = [{"role": "user", "content": "一条回复里 60 个调用"}]
    outcome = run(messages, root, build_control(messages, ask_checkpoint=ask))

    assert asked[0]["trigger"] == "executions"
    assert asked[0]["tool_executions"] == CHECKPOINT_EVERY
    assert len(executed) == CHECKPOINT_EVERY
    contents = [json.loads(m["content"]) for m in tool_messages(messages)]
    assert all("error" not in item for item in contents[:50])
    tail = [item["error"] for item in contents[50:]]
    assert tail == ["未执行：检查点停止"] * 10
    assert len(tool_messages(messages)) == 60
    assert_paired(messages)


# ---------------------------------------------------------------------------
# TL-3 / TL-4 / TL-4b / TL-5：本轮判重
# ---------------------------------------------------------------------------


def test_tl3_duplicate_key_ignores_order_and_whitespace(root: Path, monkeypatch) -> None:
    """TL-3：同轮重复调用（键序不同、带首尾空格）→ 只执行 1 次。"""
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "d1", "type": "function",
             "function": {"name": "search_notes", "arguments": json.dumps({"query": " 阈值 ", "limit": 5})}},
            {"id": "d2", "type": "function",
             "function": {"name": "search_notes", "arguments": json.dumps({"limit": 5, "query": "阈值"})}},
        ],
    }
    script = Script([reply, {"role": "assistant", "content": "好"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    executed = patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "查两次一样的东西"}]
    outcome = run(messages, root, build_control(messages))

    assert len(executed) == 1
    assert outcome["duplicates_rejected"] == 1
    assert "重复调用" in json.loads(tool_messages(messages)[1]["content"])["error"]
    assert_paired(messages)


def test_tl4_human_approved_side_effect_clears_dedup(root: Path, monkeypatch) -> None:
    """TL-4：人已批准并执行的副作用会清空判重表 → 之后的同参只读调用照常执行。"""
    script = Script([
        tool_call("read_status", {"episode": "01-smoke"}, call_id="r1"),
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"}, call_id="w1"),
        tool_call("read_status", {"episode": "01-smoke"}, call_id="r2"),
        {"role": "assistant", "content": "好"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    executed = patch_execute(monkeypatch)
    review = Reviewer(
        lambda name, args: Decision(ok=True, provenance="human")
        if name == "write_episode_file"
        else Decision(ok=True, provenance="card_free")
    )

    messages = [{"role": "user", "content": "读→写→再读"}]
    outcome = run(messages, root, build_control(messages, review=review))

    assert [name for name, _args, _c in executed] == ["read_status", "write_episode_file", "read_status"]
    assert outcome["duplicates_rejected"] == 0
    assert outcome["tool_executions"] == 3


def test_tl4b_card_free_write_does_not_clear_dedup(root: Path, monkeypatch) -> None:
    """TL-4b：免卡写入（write_memory op=cite）不清空判重表 → 第二次 read_status 被判重。"""
    script = Script([
        tool_call("read_status", {"episode": "01-smoke"}, call_id="r1"),
        tool_call("write_memory", {"op": "cite", "ids": ["M001"], "evidence": ["01-smoke"]}, call_id="m1"),
        tool_call("read_status", {"episode": "01-smoke"}, call_id="r2"),
        {"role": "assistant", "content": "好"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    executed = patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "读→免卡写→再读"}]
    outcome = run(messages, root, build_control(messages))

    assert [name for name, _args, _c in executed] == ["read_status", "write_memory"]
    assert outcome["duplicates_rejected"] == 1


def test_tl5_human_rejection_is_recorded_and_reason_fed_back(root: Path, monkeypatch) -> None:
    """TL-5：人拒绝后原样再请求 → 人审回调只 1 次；拒因**具体**回喂（M15b）。"""
    script = Script([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"}, call_id="w1"),
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"}, call_id="w2"),
        {"role": "assistant", "content": "好"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    review = Reviewer(
        lambda name, args: Decision(
            ok=False, provenance="human", reason="人类拒绝执行该工具调用：这一段不该写"
        )
    )

    messages = [{"role": "user", "content": "写草稿"}]
    outcome = run(messages, root, build_control(messages, review=review))

    assert len(review.calls) == 1, "第二次是判重命中，不该再问人"
    assert outcome["duplicates_rejected"] == 1
    assert outcome["tool_executions"] == 0
    first = json.loads(tool_messages(messages)[0]["content"])
    assert first["error"] == "人类拒绝执行该工具调用：这一段不该写"


# ---------------------------------------------------------------------------
# TL-6 ~ TL-9：中断与收尾
# ---------------------------------------------------------------------------


def test_tl6_interrupt_on_third_request_wraps_up(root: Path, monkeypatch) -> None:
    """TL-6：第 3 次请求时注入中断 → 收尾被调用、interrupted、配对通过。"""
    script = Script([
        tool_call("read_artifact", {"path": "a.md"}, call_id="a1"),
        tool_call("read_artifact", {"path": "b.md"}, call_id="b1"),
        KeyboardInterrupt(),
        {"role": "assistant", "content": "收尾答复"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "深挖"}]
    outcome = run(messages, root, build_control(messages, interrupt=Interrupt()))

    assert outcome["stopped"] == "interrupted"
    assert outcome["wrapup"] == "ok"
    assert len(script.requests) == 4, "第 3 次中断 + 第 4 次收尾"
    assert script.requests[-1]["tool_choice"] == "none"
    assert outcome["final"]["content"] == "收尾答复"
    assert_paired(messages)


def test_tl7_interrupt_during_first_execution(root: Path, monkeypatch) -> None:
    """TL-7：一条回复 3 个调用，第 1 个执行时中断 → 3 个 id 都有 tool 消息。"""
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"e{i}", "type": "function",
             "function": {"name": "read_artifact", "arguments": json.dumps({"path": f"e{i}.md"})}}
            for i in range(3)
        ],
    }
    script = Script([reply, {"role": "assistant", "content": "收尾"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    def handler(name, args, ctx):
        raise KeyboardInterrupt

    patch_execute(monkeypatch, handler)
    messages = [{"role": "user", "content": "三个一起做"}]
    outcome = run(messages, root, build_control(messages, interrupt=Interrupt()))

    contents = [json.loads(m["content"])["error"] for m in tool_messages(messages)]
    assert contents == ["已中断：执行被打断，结果未知", "未执行：人类中断", "未执行：人类中断"]
    assert outcome["tool_executions"] == 0
    assert outcome["wrapup"] == "ok"
    assert_paired(messages)


def test_tl8_interrupt_while_waiting_for_answer(root: Path, monkeypatch) -> None:
    """TL-8：等人答复时中断 → 该调用作废、执行计数 0。"""
    script = Script([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"}, call_id="w1"),
        {"role": "assistant", "content": "收尾"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    executed = patch_execute(monkeypatch)

    def review(name, args):
        raise KeyboardInterrupt

    messages = [{"role": "user", "content": "写草稿"}]
    outcome = run(messages, root, build_control(messages, review=review, interrupt=Interrupt()))

    assert executed == []
    assert json.loads(tool_messages(messages)[0]["content"])["error"] == "人审请求因人类中断作废，未执行"
    assert outcome["stopped"] == "interrupted"
    assert_paired(messages)


def test_tl9_interrupt_during_wrapup_aborts_it(root: Path, monkeypatch) -> None:
    """TL-9：收尾请求进行中再注入中断 → wrapup == "aborted"、final is None。"""
    script = Script([
        tool_call("read_artifact", {"path": "a.md"}, call_id="a1"),
        KeyboardInterrupt(),
        KeyboardInterrupt(),
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "深挖"}]
    outcome = run(messages, root, build_control(messages, interrupt=Interrupt()))

    assert outcome["stopped"] == "interrupted"
    assert outcome["wrapup"] == "aborted"
    assert outcome["final"] is None
    assert outcome["local_note"] is None


def test_tl9c_interrupt_while_stopping_does_not_raise(root: Path, monkeypatch) -> None:
    """TL-9c：「停止中」（补合成结果的各次 commit 之间）注入中断 → 不抛、合成结果齐全。"""
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"e{i}", "type": "function",
             "function": {"name": "read_artifact", "arguments": json.dumps({"path": f"e{i}.md"})}}
            for i in range(3)
        ],
    }
    script = Script([reply, {"role": "assistant", "content": "收尾"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch, handler=lambda name, args, ctx: (_ for _ in ()).throw(KeyboardInterrupt()))

    messages = [{"role": "user", "content": "三个一起做"}]
    control = build_control(messages, interrupt=Interrupt())
    # 「停止中」：第一条合成结果写完后再来一次中断，只置标志、不抛
    original_commit = control.commit

    def commit(message, origin):
        original_commit(message, origin)
        if message.get("role") == "tool":
            control.interrupt.pending += 1  # 不抛，只置标志（§2.2 状态表「停止中」）

    control.commit = commit
    outcome = run(messages, root, control)

    assert outcome["stopped"] == "interrupted"
    assert outcome["wrapup"] == "ok"
    assert len(tool_messages(messages)) == 3
    assert_paired(messages)


# ---------------------------------------------------------------------------
# TL-10 ~ TL-14：失败路径与计数
# ---------------------------------------------------------------------------


def test_tl10_wrapup_reply_with_tool_calls_is_not_executed(root: Path, monkeypatch) -> None:
    """TL-10（A1 不成立）：收尾回复仍带 tool_calls → 不执行、补合成结果、配对通过。"""
    monkeypatch.setattr(llm_mod, "CHECKPOINT_EVERY", 2)
    script = Script([
        tool_call("read_artifact", {"path": "a.md"}, call_id="a1"),
        tool_call("read_artifact", {"path": "b.md"}, call_id="b1"),
        tool_call("read_artifact", {"path": "c.md"}, call_id="c1"),
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    executed = patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "干到检查点"}]
    outcome = run(messages, root, build_control(messages, ask_checkpoint=lambda s: False))

    assert outcome["stopped"] == "checkpoint_stop"
    assert len(executed) == 2, "收尾阶段那个调用没有被执行"
    assert json.loads(tool_messages(messages)[-1]["content"])["error"] == "未执行：收尾阶段禁止调用工具"
    assert_paired(messages)


def test_tl11_error_with_failing_wrapup_yields_local_note(root: Path, monkeypatch) -> None:
    """TL-11：已有回复后 LLMError、收尾也失败 → error、wrapup "failed"、无本地说明 assistant 消息。"""
    script = Script([
        tool_call("read_artifact", {"path": "a.md"}, call_id="a1"),
        LLMError("boom"),
        LLMError("wrapup boom"),
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "深挖"}]
    outcome = run(messages, root, build_control(messages))

    assert outcome["stopped"] == "error"
    assert outcome["wrapup"] == "failed"
    assert outcome["local_note"] and outcome["local_note"].startswith("[收尾·本地]")
    # 名称×次数要**跨本轮全部回复**累计（真机手验发现的回归：只看最后一条回复会写成「（无）」）
    assert "read_artifact×1" in outcome["local_note"]
    assert not any(
        m.get("role") == "assistant" and "[收尾·本地]" in str(m.get("content") or "")
        for m in messages
    ), "本地说明只呈现，不进 messages（MUT-10）"


def test_tl12_first_request_error_rolls_back(root: Path, monkeypatch) -> None:
    """TL-12：首次请求即 LLMError → rollback、请求计数 1（不做收尾）。"""
    script = Script([LLMError("boom")])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    messages = [{"role": "user", "content": "开场"}]
    outcome = run(messages, root, build_control(messages))

    assert outcome["stopped"] == "error"
    assert outcome["rollback"] is True
    assert outcome["wrapup"] == "none"
    assert len(script.requests) == 1
    assert outcome["llm_calls"] == 0


def test_tl13_second_request_blocked_rolls_back(root: Path, monkeypatch) -> None:
    """TL-13：第 2 次请求触发出网断言 → blocked、rollback、请求计数 2。"""
    script = Script([
        tool_call("read_artifact", {"path": "a.md"}, call_id="a1"),
        PermissionError("命中受限出网子串: 03-audio/manifest.json"),
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)

    messages = [{"role": "user", "content": "深挖"}]
    outcome = run(messages, root, build_control(messages))

    assert outcome["stopped"] == "blocked"
    assert outcome["rollback"] is True
    assert len(script.requests) == 2
    assert "拦截出网请求" in outcome["error"] or "受限出网子串" in outcome["error"]


def test_tl14_fixed_script_exact_counts(root: Path, monkeypatch) -> None:
    """TL-14：固定剧本，各计数精确等于剧本值。"""
    script = Script([
        tool_call("read_status", {"episode": "01-smoke"}, call_id="r1"),
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "r2", "type": "function",
             "function": {"name": "read_status", "arguments": json.dumps({"episode": "01-smoke"})}},
            {"id": "w1", "type": "function",
             "function": {"name": "write_episode_file",
                          "arguments": json.dumps({"filename": "02-script.draft.md", "content": "x"})}},
        ]},
        tool_call("read_status", {"episode": "01-smoke"}, call_id="r3"),
        {"role": "assistant", "content": "完事"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    review = Reviewer(
        lambda name, args: Decision(ok=True, provenance="human")
        if name == "write_episode_file"
        else Decision(ok=True, provenance="card_free")
    )

    messages = [{"role": "user", "content": "剧本"}]
    outcome = run(messages, root, build_control(messages, review=review))

    assert outcome["stopped"] == "done"
    assert outcome["llm_calls"] == 4
    assert outcome["tool_calls_made"] == 4
    assert outcome["tool_executions"] == 3
    assert outcome["duplicates_rejected"] == 1
    assert outcome["checkpoints"] == 0
    assert outcome["wrapup"] == "none"
    assert outcome["error"] is None
    assert outcome["rollback"] is False
    assert_paired(messages)


# ---------------------------------------------------------------------------
# TL-15 / TL-16
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_tl15_tool_choice_absent_by_default(monkeypatch) -> None:
    """TL-15：不传 `tool_choice` 时请求体不含该键——与加参数之前逐字节相同。"""
    sent: list[dict] = []

    def fake_urlopen(request, timeout=None):
        sent.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(json.dumps({"choices": [{"message": {"role": "assistant", "content": "好"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    convo = [{"role": "user", "content": "你好"}]
    tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]

    chat_complete(convo, tools=tools, config=CFG)
    chat_complete(convo, tools=tools, config=CFG, tool_choice="none")

    assert "tool_choice" not in sent[0]
    assert sent[1]["tool_choice"] == "none"
    stripped = {k: v for k, v in sent[1].items() if k != "tool_choice"}
    assert json.dumps(stripped, sort_keys=True, ensure_ascii=False) == json.dumps(
        sent[0], sort_keys=True, ensure_ascii=False
    )


def test_tl16_critical_tool_finishes_before_interrupt_is_handled(root: Path, monkeypatch, tmp_path: Path) -> None:
    """TL-16：中断到达时正在执行 `CRITICAL_TOOLS` 里的工具 → 工具体跑完才处理中断。"""
    marker = tmp_path / "marker.txt"
    interrupt = Interrupt()
    script = Script([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"}, call_id="w1"),
        {"role": "assistant", "content": "收尾答复"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    def handler(name, args, ctx):
        marker.write_text("done", encoding="utf-8")
        interrupt.request()  # 延迟区内：只置 pending，退出时才抛
        return {"ok": True, "result": {"written": name}}

    patch_execute(monkeypatch, handler)
    review = Reviewer(lambda name, args: Decision(ok=True, provenance="human"))

    messages = [{"role": "user", "content": "写草稿"}]
    outcome = run(
        messages, root,
        build_control(messages, review=review, interrupt=interrupt,
                      critical_tools=frozenset({"write_episode_file"})),
    )

    assert marker.read_text(encoding="utf-8") == "done", "临界区内不许被中断拆开（MUT-12）"
    assert outcome["stopped"] == "interrupted"
    assert outcome["tool_executions"] == 1
    result = json.loads(tool_messages(messages)[0]["content"])
    assert result["ok"] is True, "临界区工具用**真实结果**，不是「结果未知」"
    assert outcome["wrapup"] == "ok"
    assert_paired(messages)


def test_tl9b_two_interrupts_before_surfacing_merge(root: Path, monkeypatch) -> None:
    """TL-9b：首个中断尚未浮出时再注入一次（crawl 慢浮出）→ 两次合并为**一次**浮出，
    收尾不被第二次中断打断（`wrapup == "ok"`，不是 `"aborted"`）。

    这是真 `TurnInterrupt` + 真 SIGINT（替身的区内 `request()` 只置标志、从不抛，
    写得出「合并」但覆盖不到生产代码）。两次信号都打在临界区里：第一次浮出之前。
    """
    interrupt = TurnInterrupt()
    script = Script([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"}, call_id="w1"),
        {"role": "assistant", "content": "收尾答复"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    def handler(name, args, ctx):
        time.sleep(0.6)  # 临界区内：两次 SIGINT 都落在这里
        return {"ok": True, "result": {"written": name}}

    patch_execute(monkeypatch, handler)
    review = Reviewer(lambda name, args: Decision(ok=True, provenance="human"))

    messages = [{"role": "user", "content": "写草稿"}]
    control = build_control(messages, review=review, interrupt=interrupt,
                            critical_tools=frozenset({"write_episode_file"}))
    both_sent = threading.Event()

    def fire() -> None:
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.12)
        os.kill(os.getpid(), signal.SIGINT)
        both_sent.set()

    thread = threading.Thread(target=fire, daemon=True)
    thread.start()
    outcome = run(messages, root, control)
    # 两次信号都该在回合内送达；万一时序漂了，挡在用例之外（否则会打到 pytest 自己）
    previous = signal.signal(signal.SIGINT, lambda *_args: None)
    try:
        both_sent.wait(3.0)
    finally:
        signal.signal(signal.SIGINT, previous)
        thread.join(3.0)

    assert outcome["stopped"] == "interrupted"
    assert outcome["wrapup"] == "ok", "两次中断合并 → 收尾没被打断（MUT-45）"
    assert outcome["final"] is not None
    assert interrupt._pending == 0, "合并之后不许再留一次待处理中断"
    assert_paired(messages)
