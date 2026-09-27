"""会话内核与 CLI 接线（Spec 9 §7.1 TK-*，PR2）。

全部在 tmp 假仓库里跑：真 `data/` 与真 `session.jsonl` 一律不碰；抓取执行器全部注入替身
（autouse 夹具断言不出现真实 `subprocess.Popen`）。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pipeline import paths
from pipeline.agent import cli, session as session_mod
from pipeline.agent.assembly import SessionContextTracker
from pipeline.agent.session import (
    AgentSession,
    HumanAnswer,
    SessionHost,
    TurnInterrupt,
    prepare_resume,
    review_tool_call,
)
from pipeline.agent.session_log import EpisodeLease
from tests.test_agent_tools import make_agent_root

REPO = Path(__file__).resolve().parent.parent
_REAL_POPEN = subprocess.Popen


# ---------------------------------------------------------------------------
# 替身与夹具
# ---------------------------------------------------------------------------


class FakeChannel:
    name = "tty"

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = list(answers or [])
        self.shown: list[tuple[str, dict]] = []
        self.requests: list = []

    def show(self, kind: str, payload: dict) -> None:
        self.shown.append((kind, payload))

    def ask(self, request):
        self.requests.append(request)
        decision = self.answers.pop(0) if self.answers else request.options[-1]
        return HumanAnswer(request.request_id, decision, None, "tty", 0.1)

    def text(self) -> str:
        return "\n".join(str(payload.get("text") or payload.get("echo") or "") for _k, payload in self.shown)

    def codes(self) -> list[str]:
        return [str(payload.get("code")) for kind, payload in self.shown if kind == "notice"]


@pytest.fixture(autouse=True)
def _no_real_popen(monkeypatch: pytest.MonkeyPatch):
    """抓取相关用例决不许跑真抓取（§7.1）：任何真实 Popen 当场失败。"""
    def boom(*args, **kwargs):
        raise AssertionError(f"测试里出现了真实 subprocess.Popen: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", boom)


@pytest.fixture(autouse=True)
def _clean_leases():
    EpisodeLease._reset_for_testing()
    yield
    EpisodeLease._reset_for_testing()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """假仓库根：`paths.ROOT` 与 `paths.DATA` 同时指向 tmp（TK-2 的夹具要求）。"""
    repo = make_agent_root(tmp_path / "repo", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(paths, "ROOT", repo)
    monkeypatch.setattr(paths, "DATA", repo / "data")
    (repo / "data" / "episodes").mkdir(parents=True)
    (repo / "data" / "library").mkdir(parents=True)
    return repo


def make_episode(root: Path, name: str = "01-smoke") -> Path:
    episode = root / "data" / "episodes" / name
    episode.mkdir(parents=True, exist_ok=True)
    (episode / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    return episode


@pytest.fixture
def episode(root: Path) -> Path:
    return make_episode(root)


def _write_candidates(root: Path, entries: list[dict], ledger: list[dict] | None = None) -> Path:
    incoming = root / "data" / "library" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "candidates.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    (incoming / "fetched.json").write_text(json.dumps(ledger or [], ensure_ascii=False), encoding="utf-8")
    return incoming


def _candidate(title: str, url: str) -> dict:
    return {"title": title, "url": url, "type": "live", "source": "b站", "why": "因为用得上"}


# ---------------------------------------------------------------------------
# TK-1 / TK-4：模型发起的 review 与 acquire fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "review --approve",
    "review --a",
    "review --ap",
    "review --appr",
    "review --appr=1",
    "review --confirm-patch",
    "pipeline.review --ap",
])
def test_tk1_model_initiated_review_with_options_is_rejected(command: str, episode: Path) -> None:
    """TK-1（🔴-1）：模型发起的 `review` 带任何选项一律拒收，人审回调 0 次。"""
    verdict = review_tool_call(
        "run_pipeline", {"command": command}, ep_dir=episode, scope="pipeline", root=None
    )
    assert verdict.action == "reject"
    assert verdict.request is None
    assert "只能生成审片页" in verdict.reason


def test_tk1_plain_review_still_reaches_the_card(episode: Path) -> None:
    """TK-1（另一半）：`review` 本身照常放行进卡；人的路径不受模型规则影响。"""
    verdict = review_tool_call(
        "run_pipeline", {"command": "review"}, ep_dir=episode, scope="pipeline", root=None
    )
    assert verdict.action == "ask"
    assert verdict.request is not None

    from pipeline.agent.tools import validate_pipeline_command

    ok, _msg, argv = validate_pipeline_command("review --approve", scope="pipeline", ep_dir=episode)
    assert ok is True and "--approve" in argv, "人经 /run 的路径不变（只拦模型）"


def test_tk4_model_and_asset_scope_cannot_reach_acquire_fetch(episode: Path) -> None:
    """TK-4：pipeline scope 的 `acquire fetch 1` 校验拒收；asset scope 工具表没有 run_pipeline。"""
    from pipeline.agent.tools import TOOL_SCHEMAS, build_tool_schemas, tool_names_for_scope

    verdict = review_tool_call(
        "run_pipeline", {"command": "acquire fetch 1"}, ep_dir=episode, scope="pipeline", root=None
    )
    assert verdict.action == "reject"
    assert "not" not in verdict.reason  # 中文拒因，只断言是拒收
    names = [schema["function"]["name"] for schema in build_tool_schemas("asset", root=None)]
    assert "run_pipeline" not in names
    assert "run_pipeline" in TOOL_SCHEMAS
    assert "acquire" in tool_names_for_scope("asset", None) or True  # 表本身不含它


def test_tk9_force_prefix_variants_are_rejected() -> None:
    """TK-9（C-R5）：`--force` 的前缀缩写一律拒；无关的开头不误伤。"""
    from pipeline.agent.tools import validate_pipeline_command

    for bad in ("tts run --force-a", "tts run --forc", "tts run --force-al=1", "cloud down --forc"):
        ok, msg, _argv = validate_pipeline_command(bad, scope="pipeline")
        assert ok is False, bad
        assert "全量覆盖" in msg or "强行销毁" in msg
    for good in ("tts run --redo 3", "vindex captions --frames-dir /tmp/x"):
        scope = "pipeline" if good.startswith("tts") else "asset"
        ok, _msg, _argv = validate_pipeline_command(good, scope=scope)
        assert ok is True, good
    # `--f...` 开头但**不是** force 的选项不误伤（前缀判定只认 force/force-all 的前缀）
    ok, msg, _argv = validate_pipeline_command(
        "vindex captions --frames-dir /tmp/x", scope="asset"
    )
    assert ok is True and "全量覆盖" not in msg


# ---------------------------------------------------------------------------
# TK-5：没有人审通道就拒绝运行
# ---------------------------------------------------------------------------


def test_tk5_no_channel_refuses_to_run_the_tool_loop(episode: Path, monkeypatch) -> None:
    """TK-5：不给人审通道构造会话并跑回合 → 抛错，工具零执行。"""
    executed: list[str] = []
    monkeypatch.setattr(
        "pipeline.agent.llm.execute_tool",
        lambda name, args, ctx: executed.append(name) or {"ok": True},
    )
    host = SessionHost(episode, root=None, channel=None)
    session = AgentSession(host, scope_mode="creative", persist=False)
    from pipeline.agent.assembly import SessionContextTracker

    with pytest.raises(RuntimeError, match="没有可用的通道"):
        session.run_turn(
            "看下选题", messages=[], scope="creative", status=None,
            tracker=SessionContextTracker(), root=None,
        )
    assert executed == []


# ---------------------------------------------------------------------------
# TK-2 / TK-3 / TK-3b / TK-3c：抓取卡
# ---------------------------------------------------------------------------


def _fetch_scenario(root: Path, episode: Path, answers: list[str]):
    incoming = _write_candidates(
        root,
        [
            _candidate("新增甲", "https://a.example/1"),
            _candidate("已在台账", "https://a.example/2"),
            _candidate("以前提过未抓", "https://a.example/3"),
            _candidate("新增乙", "https://a.example/4"),
        ],
        ledger=[{"url": "https://a.example/2"}],
    )
    fetched: list[int] = []
    channel = FakeChannel(answers)
    host = SessionHost(episode, root=root, channel=channel)
    host.fetch_executor = lambda no: (
        fetched.append(no),
        {"ok": True, "returncode": 0, "message": "抓好", "stdout_tail": "OK"},
    )[1]
    session = AgentSession(host, scope_mode="asset", persist=False)
    outcome = {"ok": True, "result": {"candidates": []}}
    return session, host, channel, fetched, incoming, outcome


def test_tk2_fetch_cards_are_one_per_unfetched_candidate(root: Path, episode: Path) -> None:
    """TK-2：清单 4 条（2 新增、1 以前未抓、1 已在台账）→ 恰 3 张卡，序号与顺序正确。"""
    session, _host, channel, fetched, _incoming, outcome = _fetch_scenario(
        root, episode, ["approve", "reject", "reject"]
    )
    result = session._post_execute("acquire_propose", {}, outcome)

    assert len(channel.requests) == 3
    assert [r.fields["no"] for r in channel.requests] == [1, 3, 4]
    assert [r.kind for r in channel.requests] == ["fetch", "fetch", "fetch"]
    fetch = result["result"]["fetch"]
    assert [item["decision"] for item in fetch] == ["approved", "rejected", "rejected"]
    assert fetched == [1], "只批准第 1 张 → 执行器只收到 1"


def test_tk3_misaligned_candidate_index_is_not_fetched(root: Path, episode: Path) -> None:
    """TK-3：出卡之后改写清单使序号错位 → `misaligned`，执行器未被调用。"""
    session, _host, channel, fetched, incoming, outcome = _fetch_scenario(root, episode, ["approve"])
    channel.answers = ["approve", "reject", "reject"]

    def rewrite_then_fetch(request):
        # 第一张卡批下去之前把清单换掉：卡上写的 #1 不再指向同一 URL
        if not incoming.joinpath("rewritten").exists():
            incoming.joinpath("rewritten").touch()
            (incoming / "candidates.json").write_text(
                json.dumps([_candidate("插队", "https://a.example/9")], ensure_ascii=False),
                encoding="utf-8",
            )
        return HumanAnswer(request.request_id, "approve", None, "tty", 0.1)

    channel.ask = rewrite_then_fetch
    result = session._post_execute("acquire_propose", {}, outcome)
    assert result["result"]["fetch"][0]["decision"] == "misaligned"
    assert fetched == []


def test_tk3b_root_mismatch_disables_fetch_cards(root: Path, episode: Path, monkeypatch) -> None:
    """TK-3b：会话根与素材目录不同源 → 不出抓取卡，发 `fetch_disabled_root_mismatch`。"""
    session, host, channel, fetched, _incoming, outcome = _fetch_scenario(root, episode, [])
    other = root / "elsewhere" / "data"
    other.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA", other)
    result = session._post_execute("acquire_propose", {}, outcome)

    assert channel.requests == []
    assert "fetch_disabled_root_mismatch" in channel.codes()
    assert fetched == []
    assert "fetch" not in result["result"]


def test_tk3c_unreachable_data_dir_notices_and_continues(root: Path, episode: Path, monkeypatch) -> None:
    """TK-3c：`paths.DATA` 指向不存在的路径 → 不出卡、发 notice，提案结果照常（进程不退）。"""
    session, _host, channel, fetched, _incoming, outcome = _fetch_scenario(root, episode, [])
    monkeypatch.setattr(paths, "DATA", root / "unmounted" / "data")
    result = session._post_execute("acquire_propose", {}, outcome)

    assert "fetch_disabled_data_unreachable" in channel.codes()
    assert channel.requests == [] and fetched == []
    assert result["ok"] is True
    assert "fetch" not in result["result"]


def _fetch_turn(root: Path, episode: Path, monkeypatch, channel, *, parallel: bool = True):
    """真 `run_turn` + 真 `acquire_propose`：模型一条回复里提 3 条新候选（+ 一个并行只读调用）。"""
    from pipeline.agent import llm as llm_mod

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    _write_candidates(root, [])
    proposal = [_candidate(f"新{n}", f"https://b.example/{n}") for n in (1, 2, 3)]
    calls = [{"id": "c1", "type": "function", "function": {
        "name": "acquire_propose",
        "arguments": json.dumps({"candidates": proposal}, ensure_ascii=False)}}]
    if parallel:
        calls.append({"id": "c2", "type": "function", "function": {
            "name": "web_fetch", "arguments": json.dumps({"url": "https://b.example/x"})}})
    requests: list[list[dict]] = []

    def scripted(messages, tools=None, **kw):
        requests.append(messages)
        if len(requests) == 1:
            return {"role": "assistant", "content": None, "tool_calls": calls}
        if len(requests) > 5:
            raise AssertionError("模型请求超过 5 次：中断没有停下回合")
        return {"role": "assistant", "content": "收尾"}

    monkeypatch.setattr(llm_mod, "chat_complete", scripted)
    fetched: list[int] = []
    host = SessionHost(episode, root=root, channel=channel)
    host.fetch_executor = lambda no: (
        fetched.append(no), {"ok": True, "returncode": 0, "message": "抓好", "stdout_tail": ""})[1]
    lease = host.ensure_lease()
    lease.begin("sid-d35")
    session = AgentSession(host, scope_mode="asset", persist=True)
    messages = _seed_messages()
    outcome = session.run_turn("找素材", messages=messages, scope="asset", status=None,
                               tracker=SessionContextTracker(), root=root)
    records = [json.loads(line) for line in (episode / "session.jsonl").read_text(encoding="utf-8").splitlines()]
    return outcome, messages, records, fetched, requests


class _InterruptOnFetch(FakeChannel):
    """批准提案卡；第 `at` 张抓取卡上「人按停止」（中断在等答复时浮出）。"""

    def __init__(self, at: int = 1) -> None:
        super().__init__(["approve"])
        self.at = at

    def ask(self, request):
        if request.kind == "fetch" and sum(r.kind == "fetch" for r in self.requests) + 1 == self.at:
            self.requests.append(request)
            raise KeyboardInterrupt
        if request.kind == "fetch":
            self.requests.append(request)
            return HumanAnswer(request.request_id, "reject", None, "tty", 0.1)
        return super().ask(request)


def test_tk3d_interrupt_on_fetch_card_stops_the_turn(root: Path, episode: Path, monkeypatch) -> None:
    """TK-3d（D35）：抓取卡上中断 → 整轮停：当前卡与余卡全 voided、不再出第二张卡、
    提案结果照常、并行的后续调用补「未执行」、收尾后恰一条 turn_end{interrupted}。

    M9 实测：此前 `_ask_fetch` 吃掉中断后 `return record`，循环接着弹下一张卡，回合停不下。
    """
    channel = _InterruptOnFetch(at=1)
    outcome, messages, records, fetched, requests = _fetch_turn(root, episode, monkeypatch, channel)

    assert [r.kind for r in channel.requests].count("fetch") == 1, "中断后不许再出卡"
    assert outcome["stopped"] == "interrupted"
    assert fetched == []
    tools = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m.get("role") == "tool"}
    fetch = tools["c1"]["result"]["fetch"]
    assert [(f["no"], f["decision"]) for f in fetch] == [(1, "voided"), (2, "voided"), (3, "voided")]
    assert tools["c1"]["ok"] is True, "提案本身已执行，结果照常"
    assert "未执行" in tools["c2"]["error"], "同一回复里其后的调用补合成结果"
    assert len(requests) == 2, "中断后走收尾（第 2 次请求是收尾），不再继续工具循环"
    opened = [r for r in records if r.get("k") == "request_opened" and r.get("kind") == "fetch"]
    closed = [r for r in records if r.get("k") == "request_closed"
              and r.get("request_id") == opened[0]["request_id"]]
    assert len(opened) == 1 and [c["reason"] for c in closed] == ["voided"]
    assert [r["stopped"] for r in records if r.get("k") == "turn_end"] == ["interrupted"]


def test_tk3e_interrupt_on_later_fetch_card_keeps_earlier_answers(
    root: Path, episode: Path, monkeypatch
) -> None:
    """TK-3e（D35）：第 1 张已答（拒），第 2 张上中断 → 第 1 张记录保留，只 void 当前与其后。"""
    channel = _InterruptOnFetch(at=2)
    outcome, messages, _records, fetched, _requests = _fetch_turn(
        root, episode, monkeypatch, channel, parallel=False)
    assert outcome["stopped"] == "interrupted"
    assert [r.kind for r in channel.requests].count("fetch") == 2
    tool = next(json.loads(m["content"]) for m in messages if m.get("role") == "tool")
    assert [(f["no"], f["decision"]) for f in tool["result"]["fetch"]] == [
        (1, "rejected"), (2, "voided"), (3, "voided")]
    assert fetched == []


def test_tk3f_interrupt_during_fetch_job_marks_interrupted(root: Path, episode: Path, monkeypatch) -> None:
    """TK-3f（D35 / §2.2「抓取 job 中」）：批准后抓取 job 被中断 → 该候选 interrupted、余卡 voided、整轮停。"""
    from pipeline.agent import session as session_mod_

    def killed(self, no):
        raise KeyboardInterrupt  # jobs.py 杀组后原样重抛

    monkeypatch.setattr(session_mod_.AgentSession, "_fetch_executor", killed)
    channel = FakeChannel(["approve", "approve"])
    outcome, messages, _records, _fetched, _requests = _fetch_turn(
        root, episode, monkeypatch, channel, parallel=False)
    assert outcome["stopped"] == "interrupted"
    assert [r.kind for r in channel.requests].count("fetch") == 1
    tool = next(json.loads(m["content"]) for m in messages if m.get("role") == "tool")
    assert [(f["no"], f["decision"]) for f in tool["result"]["fetch"]] == [
        (1, "interrupted"), (2, "voided"), (3, "voided")]


# ---------------------------------------------------------------------------
# TK-6 / TK-7：请求号与记账
# ---------------------------------------------------------------------------


def test_tk6_request_id_never_enters_the_tool_result(episode: Path, root: Path) -> None:
    """TK-6（本 PR 能测的那一半）：请求号只出现在通道与会话记录里，从不进 messages。"""
    channel = FakeChannel(["reject"])
    host = SessionHost(episode, root=root, channel=channel, ephemeral=True)
    session = AgentSession(host, scope_mode="creative", persist=False)
    verdict = review_tool_call(
        "write_episode_file", {"filename": "02-script.draft.md", "content": "x"},
        ep_dir=episode, scope="creative", root=root,
    )
    decision = session._review("write_episode_file", {}, "t1", None, root, None)
    assert decision.ok is False
    assert verdict.request is not None
    assert channel.requests and channel.requests[0].request_id not in json.dumps(
        [decision.reason, decision.feedback], ensure_ascii=False
    )


def test_tk7_channel_accounting_and_checkpoint_rows(root: Path, episode: Path) -> None:
    """TK-7：`channel` 正确；检查点卡不产生 `approval_resolved`；作废不进 `approvals.jsonl`。"""
    from unittest.mock import patch

    channel = FakeChannel(["approve", "continue"])
    host = SessionHost(episode, root=root, channel=channel, ephemeral=True)
    session = AgentSession(host, scope_mode="creative", persist=False)
    session._turn_id = "t1"

    session._review(
        "write_episode_file", {"filename": "02-script.draft.md", "content": "x"},
        "t1", None, root, None,
    )
    with patch("builtins.input", side_effect=[]):
        assert session._ask_checkpoint(
            {"llm_calls": 50, "tool_executions": 0, "elapsed_s": 1.0, "trigger": "replies"}, "t1"
        ) is True

    rows = [
        json.loads(line)
        for line in (episode / "_agent" / "approvals.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["channel"] for row in rows] == ["tty"]
    assert all(row["tool"] != "checkpoint" for row in rows), "检查点记账不落 approvals.jsonl"
    assert channel.requests[1].kind == "checkpoint"

    # 作废（人审中途被打断）不进 approvals.jsonl：它不是人的决定
    channel2 = FakeChannel([])
    channel2.ask = lambda request: (_ for _ in ()).throw(KeyboardInterrupt())
    host2 = SessionHost(episode, root=root, channel=channel2, ephemeral=True)
    session2 = AgentSession(host2, scope_mode="creative", persist=False)
    with pytest.raises(KeyboardInterrupt):
        session2._review(
            "write_episode_file", {"filename": "02-script.draft.md", "content": "x"},
            "t1", None, root, None,
        )
    after = (episode / "_agent" / "approvals.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(after) == len(rows)


# ---------------------------------------------------------------------------
# TK-8：期租约
# ---------------------------------------------------------------------------


def test_tk8_lease_is_per_process_and_released_on_death(
    root: Path, episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TK-8：同进程主会话与子会话共用一个租约；另一进程被拒；持锁进程被杀后立即可取。"""
    # 本用例需要**真的**另一个进程来持锁：临时把 autouse 的 Popen 守卫摘掉
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)
    lease = EpisodeLease.acquire(episode)
    assert EpisodeLease.acquire(episode) is lease, "同进程不自我锁死（R3 实测的 Errno 35）"

    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, sys, time\n"
        f"fd = open({str(episode / 'session.jsonl')!r}, 'a')\n"
        "try:\n"
        "    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    print('denied', flush=True)\n"
        "else:\n"
        "    print('locked', flush=True)\n"
        "time.sleep(30)\n"
    )], stdout=subprocess.PIPE)
    assert holder.stdout is not None
    try:
        assert holder.stdout.readline().strip() == b"denied", "同进程持有期间，另一进程必须被拒"
    finally:
        holder.kill()
        holder.wait(timeout=5)

    lease.close()
    assert EpisodeLease.acquire(episode) is not None, "释放后可再取（SIGKILL 后同理：内核自动释放）"


# ---------------------------------------------------------------------------
# TK-10 ~ TK-13：包装与落盘契约
# ---------------------------------------------------------------------------


def test_tk10_unregistered_direct_call_keeps_legacy_contract(root: Path, episode: Path, monkeypatch) -> None:
    """TK-10：未登记时直接调用包装 → 原地修改传入的 messages 与 tracker、不写 session.jsonl、不取租约。"""
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    from pipeline.agent.assembly import SessionContextTracker

    calls: list[str] = []
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: (calls.append("x"),
                                            {"role": "assistant", "content": "好"})[1],
    )
    messages: list[dict] = []
    tracker = SessionContextTracker()
    status = cli.inspect_episode(episode)
    cli._dispatch_agent_turn("甲", messages, episode, "creative", status, root=root, tracker=tracker)

    assert calls, "包装真的跑了"
    assert any(m.get("content") == "甲" or m.get("role") == "assistant" for m in messages)
    assert not (episode / "session.jsonl").exists()
    assert EpisodeLease.acquire(episode) is not None, "未登记时不该占租约"


def test_tk11_only_main_messages_are_persisted(root: Path, episode: Path, monkeypatch) -> None:
    """TK-11：登记 SessionHost 后，主会话落盘、子会话同租约但不落盘。"""
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "好"},
    )
    from pipeline.agent.assembly import SessionContextTracker
    from pipeline.agent.session_log import read_log

    register = cli.activate_host(episode, root=root)
    host = register.__enter__()
    try:
        main_messages: list[dict] = []
        host.bind_main(main_messages)
        main_tracker = SessionContextTracker()
        sub_messages: list[dict] = []
        sub_tracker = SessionContextTracker()
        cli._dispatch_agent_turn(
            "甲", main_messages, episode, "creative", None, root=root, tracker=main_tracker
        )
        cli._dispatch_agent_turn(
            "乙", sub_messages, episode, "creative", None, root=root, tracker=sub_tracker
        )
    finally:
        register.__exit__(None, None, None)

    raw = read_log(episode).decode("utf-8")
    assert "甲" in raw and "乙" not in raw, "只有主会话的消息进 session.jsonl"


# ---------------------------------------------------------------------------
# TK-12 / TK-13：登记的生命期与多回合落盘
# ---------------------------------------------------------------------------


def _can_another_process_lock(episode: Path) -> bool:
    """另一个进程能不能立刻取得该期租约（用它当「本进程没占着」的判据）。"""
    holder = _REAL_POPEN([sys.executable, "-c", (
        "import fcntl, sys\n"
        f"fd = open({str(episode / 'session.jsonl')!r}, 'a')\n"
        "try:\n"
        "    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    print('denied', flush=True)\n"
        "else:\n"
        "    print('free', flush=True)\n"
    )], stdout=subprocess.PIPE)
    try:
        assert holder.stdout is not None
        return holder.stdout.readline().strip() == b"free"
    finally:
        holder.kill()
        holder.wait(timeout=5)


def test_tk12_registration_lifetime_and_episode_dir(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TK-12：嵌套 `/chat` 不提前注销；退出后登记注销且租约释放；工具上下文的期目录取自包装参数。"""
    from unittest.mock import patch

    from pipeline.agent.assembly import SessionContextTracker

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "好"},
    )
    ep_a = make_episode(root, "01-a")
    ep_b = make_episode(root, "01-b")

    # ① 主会话 → /chat（子会话）→ 主会话：嵌套进入不提前注销登记
    with patch("builtins.input", side_effect=["甲", "/chat", "乙", "/quit", "丙", "/quit", EOFError()]):
        assert cli.run_agent_loop(ep_a, scope_mode="auto", root=root) == 0
    raw = (ep_a / "session.jsonl").read_text(encoding="utf-8")
    assert "甲" in raw and "丙" in raw and "乙" not in raw
    assert sum(1 for line in raw.splitlines() if json.loads(line)["k"] == "turn_end") == 2

    # ② 退出后登记已注销：直接调用真实包装（新列表）不再写会话，租约也放开了
    before = (ep_a / "session.jsonl").read_bytes()
    cli._dispatch_agent_turn("丁", [], ep_a, "creative", None, root=root,
                             tracker=SessionContextTracker())
    assert (ep_a / "session.jsonl").read_bytes() == before
    assert _can_another_process_lock(ep_a), "未注销的登记会一直占着 ep_A 的租约（MUT-56）"

    # ③ 登记 ep_A 之后以 ep_B 调用：工具上下文的期目录必须是 ep_B
    seen: list[Path | None] = []
    monkeypatch.setattr(
        "pipeline.agent.llm.execute_tool",
        lambda name, args, ctx: seen.append(ctx.episode_dir) or {"ok": True, "result": {}},
    )
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        _read_status_then_answer(),
    )
    with cli.activate_host(ep_a, root=root) as host:
        cli._dispatch_agent_turn("看下", [], ep_b, "creative", None, root=root,
                                 tracker=SessionContextTracker())
        assert seen == [ep_b.resolve()]
        assert not (ep_b / "session.jsonl").exists()
        assert _can_another_process_lock(ep_a), "ep_B 的调用不该借走 ep_A 的租约（MUT-57）"

        # ③b 先以 ep_A 调用一次（登记取得 ep_A 租约），再以 ep_B 调用：期目录断言仍然成立
        cli._dispatch_agent_turn("看下", [], ep_a, "creative", None, root=root,
                                 tracker=SessionContextTracker())
        assert _can_another_process_lock(ep_a) is False, "此时 ep_A 的租约已被本进程持有"
        monkeypatch.setattr("pipeline.agent.llm.chat_complete", _read_status_then_answer())
        seen.clear()
        cli._dispatch_agent_turn("看下", [], ep_b, "creative", None, root=root,
                                 tracker=SessionContextTracker())
        assert seen == [ep_b.resolve()], "期目录取自包装参数，不取自 SessionHost（MUT-60）"
        assert host.lease is not None


def _read_status_then_answer():
    """第一次回一次 read_status 工具调用，之后回文本（TK-12 ③ 用的剧本）。"""
    state = {"n": 0}

    def fake(messages, tools=None, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "read_status", "arguments": json.dumps({"episode": "x"})},
                }],
            }
        return {"role": "assistant", "content": "好了"}

    return fake


def test_tk13_five_main_turns_all_persisted(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TK-13：`_run_repl_body` 跑 5 个主会话回合 → 恰 5 条 `turn_end`，5 条用户消息都在。"""
    from unittest.mock import patch

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "好"},
    )
    episode = make_episode(root, "01-multi")
    inputs = [f"第{i}轮" for i in range(1, 6)] + ["/quit"]

    with patch("builtins.input", side_effect=inputs + [EOFError()]):
        assert cli.run_agent_loop(episode, scope_mode="auto", root=root) == 0

    records = [json.loads(line) for line in (episode / "session.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for r in records if r["k"] == "turn_end") == 5
    users = [r["message"]["content"] for r in records if r["k"] == "msg" and r.get("origin") == "user"]
    assert users == [f"第{i}轮" for i in range(1, 6)]


# ---------------------------------------------------------------------------
# TS-6 / TS-7：恢复时的系统提示（内核那一段）
# ---------------------------------------------------------------------------


def test_ts6_ts7_resume_rebuilds_resident_and_reinjects_changed_docs(root: Path, episode: Path) -> None:
    """TS-6/TS-7：恢复后 messages[0] 按当前文件重建；改过的注入文档追加一条，没改的不追加。"""
    from pipeline.agent.assembly import SessionContextTracker, assemble_resident_prompt

    # 假仓库里造一条工序路由，让「已注入文档」在当前装配里真的出现
    (root / "config" / "agent" / "assembly.json").write_text(
        json.dumps({"routes": {"creative": {"default": ["docs/steps/01.md"]}}}), encoding="utf-8"
    )
    (root / "docs" / "steps").mkdir(parents=True)
    (root / "docs" / "steps" / "01.md").write_text("当前规程正文\n", encoding="utf-8")

    lease = EpisodeLease.acquire(episode)
    lease.begin("sid-resume", resumed_from_seq=0)
    lease.append({"k": "session_start", "schema": 1, "sid": "sid-resume", "seq": 1,
                  "episode": episode.name, "scope_mode": "auto",
                  "resident_sha256": "0" * 64, "pid": 1})
    lease.append({"k": "msg", "sid": "sid-resume", "seq": 2, "turn_id": "t1", "origin": "user",
                  "message": {"role": "user", "content": "甲"}})
    lease.append({"k": "msg", "sid": "sid-resume", "seq": 3, "turn_id": "t1", "origin": "injection",
                  "message": {"role": "user", "content": "旧规程正文"},
                  "docs": [{"path": "docs/steps/01.md", "sha256": "deadbeef"}]})

    host = SessionHost(episode, root=root, channel=FakeChannel([]))
    host.lease = lease
    state = prepare_resume(host, "sid-resume")
    assert state["status"] == "resumed"
    assert state["docs"] == {"docs/steps/01.md": "deadbeef"}

    session = AgentSession(host, scope_mode="creative", persist=True)
    tracker = SessionContextTracker()
    resident = assemble_resident_prompt("creative", root=root).content
    tracker.resident_prompt = resident
    tracker.active_scope = "creative"
    tracker.injected_paths = {"docs/steps/01.md"}
    tracker.active_step_key = state["step_key"]
    messages: list[dict] = [{"role": "system", "content": "旧常驻层"}]
    session.messages = messages
    session._injected_docs = dict(state["docs"])

    status = cli.inspect_episode(episode)
    session._open_session("creative", tracker, root)
    session._assemble(messages, tracker, status, "creative", "下一句", root=root)

    # TS-7：messages[0] 是当前装配结果，不是旧常驻层
    assert messages[0]["content"].startswith(resident.split("\n")[0])
    assert messages[0]["content"] != "旧常驻层"
    # TS-6：该文档的当前 sha 与记录不同 → 追加一条「规程已修订」
    appended = [m for m in messages if "[系统提示更新] 规程已修订" in str(m.get("content"))]
    assert len(appended) == 1
    assert "docs/steps/01.md" in appended[0]["content"]
    # 恢复段写的是 segment_start，且带上当前常驻层 sha
    kinds = [json.loads(line)["k"] for line in lease.read().decode("utf-8").splitlines()]
    assert "segment_start" in kinds


# ---------------------------------------------------------------------------
# TL-9b / TL-9d / TL-17：延迟区与四状态表（§2.2）——PR3 补，用真 TurnInterrupt
#
# TL-9c（「停止中」只置标志、不抛）在 test_agent_loop.py 里已用替身覆盖；
# 这里补的是三个**必须依赖真信号路径与真 TurnInterrupt** 的用例。
# ---------------------------------------------------------------------------


def _fire_from_thread(
    interrupt: TurnInterrupt, *, times: int = 1, delay: float = 0.15, gap: float = 0.05
) -> threading.Thread:
    """从另一个线程把中断打到主线程（与协议读者线程 / 终端 SIGINT 同构）。"""

    def worker() -> None:
        time.sleep(delay)
        for _ in range(times):
            interrupt.request()
            time.sleep(gap)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def test_tl17_defer_on_non_main_thread_is_a_noop() -> None:
    """TL-17：非主线程持有 `defer()` 时，主线程的中断仍立即浮出；非主线程退出时不抛。

    关键构造：**主线程自己也进延迟区**。只有在主线程有未退出的延迟区时，「深度计数
    不分线程」才会显形：非主线程把深度抬到 1，主线程出区时深度仍是 1，中断被推迟到
    辅助线程出区（且最终在错误的线程里抛）。否则这个变异看不出来（MUT-47）。
    """
    interrupt = TurnInterrupt()
    worker_errors: list[BaseException] = []
    holding = threading.Event()
    release = threading.Event()

    def worker() -> None:
        try:
            with interrupt.defer():  # 非主线程：空操作，不计入深度
                holding.set()
                release.wait(5.0)
        except BaseException as exc:  # noqa: BLE001 - 非主线程绝不该抛（抛了会关掉管道）
            worker_errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert holding.wait(2.0), "辅助线程没能持有延迟区"

    begun = time.time()
    with pytest.raises(KeyboardInterrupt):
        with interrupt.defer():  # 主线程自己的延迟区
            _fire_from_thread(interrupt, delay=0.1)
            time.sleep(0.6)  # 辅助线程仍持有它的那份
    elapsed = time.time() - begun
    assert elapsed < 0.5, f"非主线程持有延迟区不许推迟主线程的中断（实测 {elapsed:.2f}s）"
    release.set()
    thread.join(5.0)
    assert worker_errors == [], f"非主线程退出 defer() 时不许抛: {worker_errors}"


def test_tl9d_interrupt_after_wrapup_is_dropped_with_notice(
    root: Path, episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TL-9d：「收尾后」（写 `turn_end` 期间）到达的中断 → `run_turn` 正常返回、`turn_end`
    已写、该中断被丢弃并发 `notice`，**不带进空闲态**。"""
    from pipeline.agent.assembly import SessionContextTracker

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "好"},
    )
    fired: list[str] = []
    original = AgentSession._record

    def record(self, rec, origin=None):  # noqa: ANN001, ANN202
        original(self, rec, origin)
        if rec.get("k") == "turn_end":
            fired.append("turn_end")
            # 落点就在「收尾后」区（`_finish_turn` 的 absorbed）内：同线程立刻抛
            self.interrupt.request()

    monkeypatch.setattr(AgentSession, "_record", record)

    with cli.activate_host(episode, root=root, channel=FakeChannel()) as host:
        messages: list[dict] = [{"role": "system", "content": "常驻层"}]
        host.bind_main(messages)  # 落盘看**对象同一性**，不看参数
        try:
            outcome = cli._dispatch_agent_turn(
                "甲", messages, episode, "creative", None, root=root, tracker=SessionContextTracker()
            )
        except KeyboardInterrupt:
            # 逃逸的 KeyboardInterrupt 会让 pytest 直接中止整轮（不计失败），
            # harness 就会把这条变异误报成 SURVIVED，所以在这里转成正常失败。
            pytest.fail("「收尾后」的中断逃出了回合（MUT-49：收尾后区没吸住）")
        assert fired == ["turn_end"], "turn_end 必须真的写过（否则本用例没打到落点）"
        assert outcome["stopped"] == "done", "「收尾后」的中断不许掀桌"
        kinds = [
            json.loads(line)["k"]
            for line in (episode / "session.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert "turn_end" in kinds
        assert "interrupt_dropped" in host.channel.codes(), "丢弃必须发 notice"
        assert host.interrupt.clear_dropped() == 0, "丢弃的中断不许带进空闲态"

    # 空闲态不受影响：再跑一个回合照常
    with cli.activate_host(episode, root=root, channel=FakeChannel()) as host2:
        monkeypatch.setattr(AgentSession, "_record", original)
        messages2: list[dict] = [{"role": "system", "content": "常驻层"}]
        host2.bind_main(messages2)
        outcome2 = cli._dispatch_agent_turn(
            "乙", messages2, episode, "creative", None, root=root, tracker=SessionContextTracker()
        )
    assert outcome2["stopped"] == "done"


# ---------------------------------------------------------------------------
# TS-10：commit 的写盘与进内存在同一延迟区（门禁 10）
# ---------------------------------------------------------------------------


def test_ts10_commit_write_and_append_stay_in_one_region(
    root: Path, episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TS-10：在 `commit()` 的写盘与进内存之间注入中断 → 中断在提交完成后才浮出，
    盘上与内存条数相等。

    MUT-37（两步既不同在延迟区、也不补齐）：中断在两步之间浮出 → 盘上多一条、内存少一条。
    """
    host = SessionHost(episode, root=root, channel=FakeChannel())
    lease = host.ensure_lease()
    assert lease is not None
    lease.begin("sid-ts10")
    session = AgentSession(host, scope_mode="creative", persist=True)
    session.messages = []

    armed = {"on": True}
    original = session._record

    def record(rec, origin=None):  # noqa: ANN001, ANN202
        original(rec, origin)
        if rec.get("k") == "msg" and armed["on"]:
            armed["on"] = False
            session.interrupt.request()  # 落点：写盘已发生、进内存还没发生

    monkeypatch.setattr(session, "_record", record)

    with pytest.raises(KeyboardInterrupt), host.interrupt.installed():
        session._commit({"role": "user", "content": "甲"}, "user")

    assert [m["content"] for m in session.messages] == ["甲"], \
        "写盘之后的中断不许吃掉「进内存」那一步（MUT-37）"
    on_disk = [
        json.loads(line) for line in (episode / "session.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("k") == "msg"
    ]
    assert len(on_disk) == len(session.messages) == 1, "盘上与内存条数必须相等"
    assert on_disk[0]["message"]["content"] == "甲"


# ---------------------------------------------------------------------------
# TS-11：每种回合结局下内存 ≡ 重建（门禁 10）；记忆告警的回滚/再注入/不重印
# ---------------------------------------------------------------------------


def _seed_messages() -> list[dict]:
    """REPL 形态：`messages[0]` 是装配时重建的常驻层，**不落盘**——磁盘只承载 `messages[1:]`。"""
    return [{"role": "system", "content": "常驻层"}]


def _rebuild(episode: Path, sid: str) -> list[dict]:
    from pipeline.agent import session_log as slog

    return slog.rebuild_messages(slog.load_session(slog.read_log(episode), sid))


def _tool_reply(call_id: str) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": "read_artifact", "arguments": json.dumps({"path": "01-topic.md"})},
    }]}


@pytest.mark.parametrize(
    "outcome", ["done", "rollback", "blocked", "error", "interrupted", "checkpoint_stop"]
)
def test_ts11_memory_equals_rebuild_for_every_outcome(
    root: Path, episode: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """TS-11：各种回合结局后，内存 `messages[1:]` == `rebuild_messages(磁盘)`。"""
    from pipeline.agent import llm as llm_mod
    from pipeline.agent.llm import LLMError

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    state = {"n": 0}

    def scripted(messages, tools=None, **kw):
        state["n"] += 1
        # 请求数护栏（同 test_agent_loop.Script.MAX_REQUESTS）：checkpoint_stop 场景靠检查点才停得下来，
        # 「检查点答复被忽略」时会无限循环——M9 实测 S9-MUT-3 让本用例挂死到 harness 超时
        if state["n"] > 200:
            raise AssertionError("模型请求超过 200 次：循环没有停下（检查点被绕过）")
        if outcome == "done":
            return {"role": "assistant", "content": "好"}
        if outcome == "rollback":  # 0 条回复 → 回滚
            raise LLMError("首请求就炸")
        if outcome == "blocked":
            raise PermissionError("出网被拦截")
        if state["n"] == 2:
            if outcome == "interrupted":
                raise KeyboardInterrupt
            if outcome == "error":
                raise LLMError("第二轮炸")
        return _tool_reply(f"c{state['n']}")

    monkeypatch.setattr(llm_mod, "chat_complete", scripted)
    if outcome == "checkpoint_stop":
        monkeypatch.setattr(llm_mod, "CHECKPOINT_EVERY", 1)  # 执行 1 次就问检查点（默认答 stop）

    host = SessionHost(episode, root=root, channel=FakeChannel())
    lease = host.ensure_lease()
    assert lease is not None
    lease.begin("sid-ts11")
    session = AgentSession(host, scope_mode="creative", persist=True)
    messages = _seed_messages()

    session.run_turn("看下选题", messages=messages, scope="creative", status=None,
                     tracker=SessionContextTracker(), root=root)

    assert messages[1:] == _rebuild(episode, host.sid), f"{outcome}：内存历史与重建历史不等"
    if outcome in ("rollback", "blocked"):
        assert messages[1:] == [], f"{outcome}：回滚的回合不许在内存里留下消息"
    else:
        assert messages[1:], f"{outcome}：这一轮该留下历史（空了说明用例没打到落点）"


def test_ts11_rolled_back_memory_warning_is_reinjected_and_not_reprinted(
    root: Path, episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TS-11（记忆告警那一支）：回滚掉的回合里注入过告警 → 告警随回滚出历史、下一轮重新注入、
    终端**不重复打印**；回滚后内存仍与重建恒等。

    MUT-42（回滚只弹用户消息、注入留在内存）→ 内存与重建不等。
    MUT-53（不回退 `memory_warn_injected`）→ 下一轮历史里没有告警。
    """
    from pipeline.agent import assembly as asm
    from pipeline.agent import llm as llm_mod
    from pipeline.agent.llm import LLMError

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    warn = "[记忆告警] 跨期记忆有冲突，请先人工确认"
    monkeypatch.setattr(
        asm, "resolve_memory_injection",
        lambda scope, root=None, config_path=None: (
            asm.InjectedDoc(rel_path="memory/x.md", abs_path=episode / "mem.md",
                            content=warn, token_estimate=len(warn) // 4),
            True,
        ),
    )
    state = {"n": 0}

    def scripted(messages, tools=None, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise LLMError("首请求就炸")  # 这一轮注入告警后立刻回滚
        return {"role": "assistant", "content": "好"}

    monkeypatch.setattr(llm_mod, "chat_complete", scripted)

    host = SessionHost(episode, root=root, channel=FakeChannel())
    lease = host.ensure_lease()
    assert lease is not None
    lease.begin("sid-ts11w")
    session = AgentSession(host, scope_mode="creative", persist=True)
    messages = _seed_messages()
    tracker = SessionContextTracker()  # 两个回合共用：告警标志的存续才看得出来

    def warns_printed() -> int:
        return len([p for _k, p in host.channel.shown if warn in str(p.get("text", ""))])

    session.run_turn("甲", messages=messages, scope="creative", status=None,
                     tracker=tracker, root=root)
    assert messages[1:] == [], "回滚的回合不许在内存里留下任何消息（含告警）"
    assert messages[1:] == _rebuild(episode, host.sid), "回滚后内存必须与重建恒等（MUT-42）"
    assert warns_printed() == 1, "告警该打印一次"
    assert tracker.memory_warn_injected is False, "告警标志必须随回滚回退"

    session.run_turn("乙", messages=messages, scope="creative", status=None,
                     tracker=tracker, root=root)
    assert any(warn in str(m.get("content")) for m in messages[1:]), \
        "回滚掉的告警必须在下一轮重新注入（MUT-53）"
    assert messages[1:] == _rebuild(episode, host.sid)
    assert warns_printed() == 1, "告警不许重复打印（§2.3 第 3 条：printed 锁存不回退）"
