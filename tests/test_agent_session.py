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

    ok, _msg, argv = validate_pipeline_command("review --approve", ep_dir=episode)
    assert ok is True and "--approve" in argv, "人经 /run 的路径不变（只拦模型）"


def test_d53_ta7_redo_with_spaces_rejected_before_card(episode: Path) -> None:
    """D53 TA-7：董香二期那条 `tts --redo 2 4 5 8 9 10` 在弹卡前就拒，并给出逗号写法。"""
    verdict = review_tool_call(
        "run_pipeline", {"command": "tts --redo 2 4 5 8 9 10"}, ep_dir=episode,
        scope="pipeline", root=None,
    )
    assert verdict.action == "reject"
    assert verdict.request is None, "不许弹卡让人白批一次"
    assert "tts --redo 2,4,5,8,9,10" in verdict.reason


_D51_SCRIPT = "## 段落 1\n\n配音：她强忍着肉体的排斥。\n\n画面：\n  查询: 便当\n"


def test_d51_tv9_bad_reading_rejected_before_card(episode: Path) -> None:
    """D51 TV-9：读音不成立的纠错（「肉惕」读 rou4 ti4）在弹卡前就拒，人不必为必然被拒的写入点卡。"""
    (episode / "02-script.md").write_text(_D51_SCRIPT, encoding="utf-8")
    verdict = review_tool_call(
        "run_pipeline",
        {"command": "corrections global --word 肉体 --homophone 肉惕 --expect rou4ti3"},
        ep_dir=episode, scope="pipeline", root=None,
    )
    assert verdict.action == "reject" and verdict.request is None
    assert "rou4 ti4" in verdict.reason


def test_d51_tv9_good_reading_card_shows_plan(episode: Path, tmp_path: Path, monkeypatch) -> None:
    """D51 TV-9：成立的全局写入照常弹卡，卡面带写入位置、全局标记、读音核对与本期受影响段。"""
    from pipeline import corrections

    voice = tmp_path / "voice.json"
    voice.write_text(json.dumps({"readings": {}, "pinyin_injections": {}}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    monkeypatch.setattr(corrections, "VOICE_CONFIG", voice)
    (episode / "02-script.md").write_text(_D51_SCRIPT, encoding="utf-8")
    verdict = review_tool_call(
        "run_pipeline", {"command": "corrections global --word 肉体 --pinyin rou4ti3"},
        ep_dir=episode, scope="pipeline", root=None,
    )
    assert verdict.action == "ask"
    card = verdict.request.card_text
    assert "[全局]" in card and "写读音表（全局 config/voice.json）" in card
    assert "「肉体」→ rou4ti3" in card and "✓ 通过" in card and "本期含该词的段：1" in card


def test_d51_check_subcommand_is_card_free(episode: Path) -> None:
    """`corrections check` 只读（不落盘），免卡；add / global 不免。"""
    verdict = review_tool_call(
        "run_pipeline", {"command": "corrections check --word 绚都 --pinyin xuan4du1"},
        ep_dir=episode, scope="pipeline", root=None,
    )
    assert verdict.action == "allow"


@pytest.mark.parametrize("command", [
    "vindex who 东京喰种 S02E07 --start 19:40 --end 20:05",
    "python -m pipeline.vindex who 东京喰种 S02E07",
])
def test_d54_vindex_who_is_allowed_and_card_free(command: str, episode: Path) -> None:
    """D54 ③（2026-10-09 TP-3/TP-4）：`vindex who` 只读在场索引，放行且免卡（任意 scope）。"""
    for scope in ("creative", "pipeline"):
        verdict = review_tool_call("run_pipeline", {"command": command}, ep_dir=episode, scope=scope, root=None)
        assert verdict.action == "allow", scope


def test_d54_other_vindex_subcommands_unchanged(episode: Path) -> None:
    """放行 `who` 不连带别的：`captions`（云端花钱）照旧弹卡，`search` 照旧不在放行集。"""
    verdict = review_tool_call(
        "run_pipeline", {"command": "vindex captions 东京喰种 S02E07"}, ep_dir=episode, scope="pipeline", root=None
    )
    assert verdict.action == "ask"
    verdict = review_tool_call(
        "run_pipeline", {"command": "vindex search 便当"}, ep_dir=episode, scope="pipeline", root=None
    )
    assert verdict.action == "reject" and "放行清单" in verdict.reason


def test_tk4_acquire_fetch_reachable_after_d43(episode: Path) -> None:
    """TK-4 按 D43 / Spec 17 改写：合表后任意 scope 可提议 `acquire fetch 1`（弹人审卡）；
    原「pipeline scope 拒收、asset 工具表没有 run_pipeline」语义已废除。"""
    verdict = review_tool_call(
        "run_pipeline", {"command": "acquire fetch 1"}, ep_dir=episode, scope="pipeline", root=None
    )
    assert verdict.action == "ask"  # 合表后合法 → 过人审卡，不再按 scope 拒


def test_tk9_force_prefix_variants_are_rejected() -> None:
    """TK-9（C-R5）：`--force` 的前缀缩写一律拒；无关的开头不误伤。"""
    from pipeline.agent.tools import validate_pipeline_command

    for bad in ("tts run --force-a", "tts run --forc", "tts run --force-al=1", "cloud down --forc"):
        ok, msg, _argv = validate_pipeline_command(bad)
        assert ok is False, bad
        assert "全量覆盖" in msg or "强行销毁" in msg
    for good in ("tts run --redo 3", "vindex captions --frames-dir /tmp/x"):
        ok, _msg, _argv = validate_pipeline_command(good)
        assert ok is True, good
    # `--f...` 开头但**不是** force 的选项不误伤（前缀判定只认 force/force-all 的前缀）
    ok, msg, _argv = validate_pipeline_command(
        "vindex captions --frames-dir /tmp/x"
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


# 本次 `acquire_propose` 的入参：4 条都在输入里（§2.4.4 第 2 条「以前提过未抓」= 本次又提了、
# 被 propose 按同 URL 跳过的那条；已在台账那条也在输入里，但不出卡）
_ALL_FOUR = {"candidates": [_candidate("x", f"https://a.example/{n}") for n in (1, 2, 3, 4)]}


def test_tk2_fetch_cards_are_one_per_unfetched_candidate(root: Path, episode: Path) -> None:
    """TK-2：清单 4 条（2 新增、1 以前未抓、1 已在台账）→ 恰 3 张卡，序号与顺序正确。"""
    session, _host, channel, fetched, _incoming, outcome = _fetch_scenario(
        root, episode, ["approve", "reject", "reject"]
    )
    result = session._post_execute("acquire_propose", _ALL_FOUR, outcome)

    assert len(channel.requests) == 3
    assert [r.fields["no"] for r in channel.requests] == [1, 3, 4]
    assert [r.kind for r in channel.requests] == ["fetch", "fetch", "fetch"]
    fetch = result["result"]["fetch"]
    assert [item["decision"] for item in fetch] == ["approved", "rejected", "rejected"]
    assert fetched == [1], "只批准第 1 张 → 执行器只收到 1"


def test_tk2b_only_this_calls_urls_get_cards(root: Path, episode: Path) -> None:
    """TK-2b（D34）：清单里有历史未抓候选，本轮只提案一条 → 只出这一张卡，序号取清单位置。

    M9 实测：本轮只提案清单第 7 条，弹出来的却是第 3 条（真人 YouTube 候选），批准即真抓。
    """
    session, _host, channel, fetched, _incoming, outcome = _fetch_scenario(root, episode, ["approve"])
    only_new = {"candidates": [_candidate("新增乙", " https://a.example/4 ")]}  # propose 会 strip
    result = session._post_execute("acquire_propose", only_new, outcome)

    assert [r.fields["no"] for r in channel.requests] == [4]
    assert [item["no"] for item in result["result"]["fetch"]] == [4]
    assert fetched == [4]


def test_tk2c_same_url_twice_in_one_call_is_one_card(root: Path, episode: Path) -> None:
    """TK-2c（D34）：同一 URL 在本次输入里出现两次 → 只一张卡；输入顺序决定出卡顺序。"""
    session, _host, channel, _fetched, _incoming, outcome = _fetch_scenario(
        root, episode, ["reject", "reject"])
    twice = {"candidates": [_candidate("乙", "https://a.example/4"), _candidate("甲", "https://a.example/1"),
                            _candidate("乙again", "https://a.example/4")]}
    session._post_execute("acquire_propose", twice, outcome)
    assert [r.fields["no"] for r in channel.requests] == [4, 1]


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
    result = session._post_execute("acquire_propose", _ALL_FOUR, outcome)
    assert result["result"]["fetch"][0]["decision"] == "misaligned"
    assert fetched == []


def test_tk3b_root_mismatch_disables_fetch_cards(root: Path, episode: Path, monkeypatch) -> None:
    """TK-3b：会话根与素材目录不同源 → 不出抓取卡，发 `fetch_disabled_root_mismatch`。"""
    session, host, channel, fetched, _incoming, outcome = _fetch_scenario(root, episode, [])
    other = root / "elsewhere" / "data"
    other.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA", other)
    result = session._post_execute("acquire_propose", _ALL_FOUR, outcome)

    assert channel.requests == []
    assert "fetch_disabled_root_mismatch" in channel.codes()
    assert fetched == []
    assert "fetch" not in result["result"]


def test_tk3c_unreachable_data_dir_notices_and_continues(root: Path, episode: Path, monkeypatch) -> None:
    """TK-3c：`paths.DATA` 指向不存在的路径 → 不出卡、发 notice，提案结果照常（进程不退）。"""
    session, _host, channel, fetched, _incoming, outcome = _fetch_scenario(root, episode, [])
    monkeypatch.setattr(paths, "DATA", root / "unmounted" / "data")
    result = session._post_execute("acquire_propose", _ALL_FOUR, outcome)

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
    verdict = review_tool_call(  # 07-titles.md 仍逐次弹卡（草稿前几次免卡，2026-10-08 spec §A）
        "write_episode_file", {"filename": "07-titles.md", "content": "x"},
        ep_dir=episode, scope="creative", root=root,
    )
    decision = session._review("write_episode_file", {"filename": "07-titles.md", "content": "x"}, "t1", None, root, None)
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
        "write_episode_file", {"filename": "07-titles.md", "content": "x"},
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
            "write_episode_file", {"filename": "07-titles.md", "content": "x"},
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


def test_n56_resume_reinjects_docs_changed_since_natural_injection(root: Path, episode: Path) -> None:
    """N56：生产路径首轮注入就记下 {path, sha256}；恢复后改过的那份追加「规程已修订」，没改的不追加。

    与 TS-6 的区别：会话记录全由生产代码写出，不手写带 docs 的记录。
    """
    from pipeline.agent.assembly import SessionContextTracker

    (root / "config" / "agent" / "assembly.json").write_text(
        json.dumps({"routes": {"creative": {"default": ["docs/steps/01.md", "docs/steps/02.md"]}}}), encoding="utf-8"
    )
    (root / "docs" / "steps").mkdir(parents=True)
    (root / "docs" / "steps" / "01.md").write_text("规程一·旧\n", encoding="utf-8")
    (root / "docs" / "steps" / "02.md").write_text("规程二·不变\n", encoding="utf-8")

    lease = EpisodeLease.acquire(episode)
    host = SessionHost(episode, root=root, channel=FakeChannel([]))
    host.lease = lease
    first = AgentSession(host, scope_mode="creative", persist=True)
    tracker = SessionContextTracker()
    messages: list[dict] = []
    first.messages = messages
    first._open_session("creative", tracker, root)
    first._assemble(messages, tracker, cli.inspect_episode(episode), "creative", "第一句", root=root)
    sid = host.sid

    (root / "docs" / "steps" / "01.md").write_text("规程一·新\n", encoding="utf-8")

    host2 = SessionHost(episode, root=root, channel=FakeChannel([]))
    host2.lease = lease
    state = prepare_resume(host2, sid)
    assert state["status"] == "resumed"
    assert sorted(state["docs"]) == ["docs/steps/01.md", "docs/steps/02.md"]
    second = AgentSession(host2, scope_mode="creative", persist=True)
    tracker2 = SessionContextTracker()
    tracker2.injected_paths = set(state["docs"])  # 与 protocol.py / cli.py 恢复时同一口径
    tracker2.active_step_key = state["step_key"]
    messages2 = list(state["messages"])
    second.messages = messages2
    second._open_session("creative", tracker2, root)
    second._assemble(messages2, tracker2, cli.inspect_episode(episode), "creative", "下一句", root=root)

    appended = [m["content"] for m in messages2 if "[系统提示更新] 规程已修订" in str(m.get("content"))]
    assert appended == ["[系统提示更新] 规程已修订：docs/steps/01.md\n\n规程一·新\n"]


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


# ---------------------------------------------------------------------------
# Spec 16（D30）：出网断言对仓库规程逐字副本做命中位置级豁免——会话层用例
#
# 断言在**真** `chat_complete` 里跑：只替换 `urlopen`（零出网），payload 照常装配、照常断言。
# 规程一律取**真实仓库**的文档（复制进假仓库），复现的就是 2026-10-06 那两份 runbook。
# ---------------------------------------------------------------------------

_STEP_03 = "03 语音合成"
_STEP_035 = "03.5 配音顺听 / 04 排片"
_STEP_05 = "05 审时间码"
_HIT = "03-audio/manifest.json"


class _Step:
    """最小 status 替身：装配只读 `current_step`（状态卡另行打桩）。"""

    def __init__(self, current_step: str) -> None:
        self.current_step = current_step


def _seed_repo_docs(root: Path) -> None:
    """把真实仓库的路由表、常驻层与全部被路由的规程复制进假仓库。"""
    import shutil

    shutil.copy(REPO / "config" / "agent" / "assembly.json", root / "config" / "agent" / "assembly.json")
    shutil.copytree(REPO / "config" / "agent" / "scopes", root / "config" / "agent" / "scopes", dirs_exist_ok=True)
    shutil.copytree(REPO / "docs" / "runbook", root / "docs" / "runbook", dirs_exist_ok=True)
    shutil.copy(REPO / "docs" / "WORKFLOW.md", root / "docs" / "WORKFLOW.md")
    (root / "skills" / "write-script").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "skills" / "write-script" / "SKILL.md", root / "skills" / "write-script" / "SKILL.md")
    shutil.copy(REPO / "AGENTS.md", root / "AGENTS.md")


class _LLMResp:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_LLMResp":
        return self

    def __exit__(self, *args) -> None:
        pass


@pytest.fixture
def egress_env(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """真实规程 + 假 urlopen（记下每个真正发出的请求体）+ 状态卡打桩。"""
    from pipeline.agent import llm as llm_mod

    _seed_repo_docs(root)
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    sent: list[dict] = []

    def fake_urlopen(request, timeout=None):
        sent.append(json.loads(request.data.decode("utf-8")))
        return _LLMResp({"choices": [{"message": {"role": "assistant", "content": "好"}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(AgentSession, "_status_card", lambda self, scope, status: "## 状态卡")
    return sent


def _turn(session: AgentSession, messages: list[dict], tracker, root: Path, step: str, line: str = "你好") -> dict:
    return session.run_turn(line, messages=messages, scope="creative", status=_Step(step),
                            tracker=tracker, root=root)


def _not_blocked(outcome: dict) -> None:
    assert outcome.get("stopped") != "blocked", f"本轮被出网断言拦下：{outcome.get('error')}"


def _payload_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("step", [_STEP_03, _STEP_035])
def test_td1_runbook_literal_no_longer_blocks_first_turn(root: Path, episode: Path, egress_env: list[dict], step: str) -> None:
    """TD-1（D30 复现转绿）：期目录处在 03 / 03.5，首轮请求照常发出，且请求体里仍含 runbook 原文与字面量。"""
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True)
    outcome = _turn(session, [], SessionContextTracker(), root, step)
    _not_blocked(outcome)
    assert len(egress_env) == 1
    body = _payload_text(egress_env[0])
    assert _HIT in body
    runbook = "03-tts.md" if step == _STEP_03 else "03.5-voice-check.md"
    first_line = (root / "docs" / "runbook" / runbook).read_text(encoding="utf-8").strip().splitlines()[0]
    assert first_line in body


def _runbook_line(root: Path, name: str, contains: str) -> str:
    """取 runbook 里含某子串的那一行（用来在请求体里认出整份规程确实还在）。"""
    text = (root / "docs" / "runbook" / name).read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if contains in line).strip()


def test_td1b_same_session_across_steps_keeps_sending(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """TD-1b（🟡-1）：同一会话 03 → 03.5 → 05，每轮首个请求都照常发出；第三轮请求体里两份旧规程仍在。"""
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True)
    tracker = SessionContextTracker()
    messages: list[dict] = []
    for n, step in enumerate((_STEP_03, _STEP_035, _STEP_05), 1):
        _not_blocked(_turn(session, messages, tracker, root, step))
        assert len(egress_env) == n, f"第 {n} 轮没有恰好发出 1 个请求"
    body = _payload_text(egress_env[-1])
    assert _runbook_line(root, "03-tts.md", _HIT) in body
    assert _runbook_line(root, "03.5-voice-check.md", _HIT) in body


def _resume_session(root: Path, episode: Path, history: list[dict]) -> tuple[AgentSession, list[dict], SessionContextTracker]:
    """照协议 `--continue` 的做法起一个**新进程视角**的会话：tracker 是空的，历史来自 session.jsonl。

    `history` 里每项是 `{"message": ..., "origin": ..., ["docs": ...]}`，原样写成 msg 记录。
    """
    from pipeline.agent.assembly import assemble_resident_prompt

    lease = EpisodeLease.acquire(episode)
    lease.begin("sid-d30", resumed_from_seq=0)
    lease.append({"k": "session_start", "schema": 1, "sid": "sid-d30", "seq": 1,
                  "episode": episode.name, "scope_mode": "auto",
                  "resident_sha256": "0" * 64, "pid": 1})
    for seq, item in enumerate(history, 2):
        lease.append({"k": "msg", "sid": "sid-d30", "seq": seq, "turn_id": "t1", **item})
    host = SessionHost(episode, root=root, channel=FakeChannel())
    host.lease = lease
    state = prepare_resume(host, "sid-d30")
    assert state["status"] == "resumed"
    # 协议进程（protocol.py ⑧）的恢复装配：常驻层在会话外预置，injected_paths 取记录里的 docs
    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt("creative", root=root).content
    tracker.active_scope = "creative"
    tracker.injected_paths = set((state.get("docs") or {}).keys())
    tracker.active_step_key = state.get("step_key")
    messages = [{"role": "system", "content": tracker.get_initial_system_prompt("## 状态卡")}]
    messages.extend(state["messages"])
    session = AgentSession(host, scope_mode="creative", persist=True)
    return session, messages, tracker


def _injection_record(root: Path, rel: str, step: str) -> dict:
    """生产路径写下的注入记录：正文由 render_step_injection 渲染，带 docs（N56 起）。"""
    from pipeline.agent.assembly import load_injected_doc, render_step_injection
    from pipeline.agent.session import _doc_shas

    doc = load_injected_doc(rel, root=root)
    assert doc is not None
    return {"origin": "injection", "docs": _doc_shas([doc]),
            "message": {"role": "user", "content": render_step_injection([doc], step_name=step)}}


def _chat_pair(text: str) -> list[dict]:
    return [{"origin": "user", "message": {"role": "user", "content": text}},
            {"origin": "assistant", "message": {"role": "assistant", "content": "好"}}]


def test_n57_preset_resident_with_restricted_literal_keeps_sending(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """N57：常驻层在会话外预置（protocol.py / cli.py 的做法），`_assemble` 不再 trust 常驻三件；
    常驻文档含受限字面量时首轮照常发出——这一路只靠可信集 (b) 收常驻三件（杀 D30-C 的 V4）。"""
    from pipeline.agent.assembly import assemble_resident_prompt

    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + f"\n- 配音清单在 `{_HIT}`。\n", encoding="utf-8")
    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt("creative", root=root).content
    tracker.active_scope = "creative"
    assert _HIT in tracker.resident_prompt
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True)
    _not_blocked(_turn(session, [], tracker, root, _STEP_03))
    assert len(egress_env) == 1
    assert f"配音清单在 `{_HIT}`" in _payload_text(egress_env[0])


def test_td1c_resumed_history_with_old_injections_keeps_sending(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """TD-1c（🟡-1）：新进程 `--continue` 恢复到 05，历史含 03 与 03.5 注入：首轮照常发出（tracker 为空，只靠 (b)）。"""
    history = [
        _injection_record(root, "docs/runbook/03-tts.md", _STEP_03), *_chat_pair("合成吧"),
        _injection_record(root, "docs/runbook/03.5-voice-check.md", _STEP_035), *_chat_pair("顺听"),
    ]
    session, messages, tracker = _resume_session(root, episode, history)
    assert tracker.trusted_doc_texts == []
    _not_blocked(_turn(session, messages, tracker, root, _STEP_05))
    assert len(egress_env) == 1
    body = _payload_text(egress_env[0])
    assert _runbook_line(root, "03-tts.md", _HIT) in body
    assert _runbook_line(root, "03.5-voice-check.md", _HIT) in body


def test_td1d_crlf_routed_doc_matches_with_same_reader(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """TD-1d（🟡-2(c)）：CRLF 换行、含受限字面量的路由文档。恢复后只有 (b) 能认出它：
    (b) 必须与注入同读法（`load_injected_doc`，不翻译换行），否则 `\\r\\n` 对不上。"""
    body_text = ("说明行，产物位置如下。\r\n" * 12) + f"- `data/episodes/<期号>/{_HIT}`\r\n" + ("尾行\r\n" * 12)
    (root / "docs" / "runbook" / "crlf.md").write_bytes(body_text.encode("utf-8"))
    cfg_path = root / "config" / "agent" / "assembly.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["routes"]["_base"]["03"] = ["docs/runbook/03-tts.md", "docs/runbook/crlf.md"]
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    history = [_injection_record(root, "docs/runbook/crlf.md", _STEP_03), *_chat_pair("合成吧")]
    assert "\\r\\n" in json.dumps(history[0]["message"]["content"])  # 注入正文确实带 CRLF
    session, messages, tracker = _resume_session(root, episode, history)
    _not_blocked(_turn(session, messages, tracker, root, _STEP_05))
    assert len(egress_env) == 1


def test_td1e_doc_edited_mid_process_old_version_still_trusted(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """TD-1e（🟡-1）：同一进程里 03 注入后 `03-tts.md` 被改了另一行，再推进到 03.5：
    历史里的旧版只有 (a) 认得，请求照常发出。"""
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True)
    tracker = SessionContextTracker()
    messages: list[dict] = []
    _not_blocked(_turn(session, messages, tracker, root, _STEP_03))
    runbook = root / "docs" / "runbook" / "03-tts.md"
    old = runbook.read_text(encoding="utf-8")
    first = old.splitlines()[0]
    assert _HIT not in first
    runbook.write_text(old.replace(first, first + "（修订版）", 1), encoding="utf-8")
    _not_blocked(_turn(session, messages, tracker, root, _STEP_035))
    assert len(egress_env) == 2
    assert first + "（修订版）" not in _payload_text(egress_env[-1])  # 历史里确实是旧版


def test_td2a_human_message_with_restricted_path_still_blocks(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """TD-2a：可信集里有规程，人打的话里含 `cloud.local.json` → 照旧拦，零发出。"""
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True)
    outcome = _turn(session, [], SessionContextTracker(), root, _STEP_035, line="看下 cloud.local.json 里写了啥")
    assert outcome.get("stopped") == "blocked"
    assert "cloud.local.json" in str(outcome.get("error"))
    assert egress_env == []


def _stub_memory(monkeypatch: pytest.MonkeyPatch, body: str) -> str:
    """模拟「R3 被绕过」：真读盘路径在读取时也做 R3（含受限串的 memory.md 只会渲染成告警），
    所以这里直接给渲染打桩，假设记忆层失守，验证出网断言这第二层仍不把记忆当可信文本。"""
    from pipeline.agent import memory

    content = f"{memory.INJECTION_HEADER}\n\n{body}"
    monkeypatch.setattr(memory, "render_injection", lambda root=None, **kw: content)
    return content


_BAD_MEMORY = ("- M001 模式：口播段落控制在两句以内，边界：只适用于人物志。\n" * 6) + "- M002 模式：先看 cloud.local.json 再动手\n"


def test_td2d_memory_with_restricted_string_blocks(root: Path, episode: Path, egress_env: list[dict],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """TD-2d：记忆正文含受限串（R3 失守）→ 照旧拦；记忆正文不进可信集。"""
    content = _stub_memory(monkeypatch, _BAD_MEMORY)
    assert len(content) >= 200  # 够长：门槛不能替它挡
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True)
    tracker = SessionContextTracker()
    outcome = _turn(session, [], tracker, root, _STEP_035)
    assert outcome.get("stopped") == "blocked"
    assert "cloud.local.json" in str(outcome.get("error"))
    assert egress_env == []
    assert all(content.strip() not in t and t not in content for t in tracker.trusted_doc_texts)


def test_td2e_extra_prompt_is_not_trusted(root: Path, episode: Path, egress_env: list[dict]) -> None:
    """TD-2e（🟡-2(a)）：`extra_prompt` 含 `cloud.local.json` → 拦。常驻层的可信正文是三份文件各自的原文，
    不是拼了 extra_prompt 的 resident_prompt。"""
    host = SessionHost(episode, root=root, channel=FakeChannel())
    session = AgentSession(host, scope_mode="creative", persist=True, extra_prompt="宿主附加：先读 cloud.local.json")
    tracker = SessionContextTracker()
    outcome = _turn(session, [], tracker, root, _STEP_035)
    assert outcome.get("stopped") == "blocked"
    assert "cloud.local.json" in str(outcome.get("error"))
    assert egress_env == []
    assert tracker.resident_prompt not in tracker.trusted_doc_texts


def test_td2f_reinjected_memory_is_not_trusted(root: Path, episode: Path, egress_env: list[dict],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """TD-2f（🟡-2(b)；按定向复审 🔵-R2-1 构造）：生产路径从不记 docs（N56），所以照 TS-6 手写一条
    带 `docs` 的记忆注入记录，让 `_reinject_changed` 真的触发；先断言「规程已修订：记忆」确实出现，
    再断言被拦、可信集里没有记忆正文。"""
    from pipeline.agent.assembly import route_trusted_texts
    from pipeline.agent.memory import MEMORY_REL_PATH

    content = _stub_memory(monkeypatch, _BAD_MEMORY)
    history = [
        {"origin": "injection", "message": {"role": "user", "content": "旧记忆：口播段落控制在两句以内"},
         "docs": [{"path": MEMORY_REL_PATH, "sha256": "0" * 64}]},
        *_chat_pair("继续"),
    ]
    session, messages, tracker = _resume_session(root, episode, history)
    outcome = _turn(session, messages, tracker, root, _STEP_05)

    records = [json.loads(line) for line in (episode / "session.jsonl").read_text(encoding="utf-8").splitlines()]
    reinjected = [r for r in records if r.get("k") == "msg"
                  and f"规程已修订：{MEMORY_REL_PATH}" in str(r["message"].get("content"))]
    assert len(reinjected) == 1, "重注入没有发生：用例构造失效，MUT-D11 不会被执行到"
    assert outcome.get("stopped") == "blocked"
    assert "cloud.local.json" in str(outcome.get("error"))
    assert egress_env == []
    for trusted in [*tracker.trusted_doc_texts, *route_trusted_texts("creative", root=root)]:
        assert "cloud.local.json" not in trusted
        assert content.strip() != trusted


def test_td5_trusted_set_excludes_non_whitelisted_docs(root: Path, tmp_path: Path) -> None:
    """TD-5（🟡-3 ③）：直接断言可信集本身。七种不该进集的文档各一例，对照组照常在。"""
    from pipeline.agent.assembly import is_trusted_doc_path, route_trusted_texts

    _seed_repo_docs(root)
    filler = "这是一份足够长的说明文字，用来越过可信文本的长度门槛。" * 10

    def put(rel: str, marker: str) -> str:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{marker}\n{filler}", encoding="utf-8")
        return marker

    cases: dict[str, str] = {}
    cases["docs/notes.txt"] = put("docs/notes.txt", "①非md")
    cases[f"docs/{_HIT}.md"] = put(f"docs/{_HIT}.md", "②路径含受限模式")
    outside = tmp_path / "outside" / "rules.md"
    outside.parent.mkdir(parents=True)
    outside.write_text(f"③仓库外\n{filler}", encoding="utf-8")
    cases[str(outside)] = "③仓库外"
    put("config/secret.md", "④软链目标")
    (root / "docs" / "link.md").symlink_to(root / "config" / "secret.md")
    cases["docs/link.md"] = "④软链目标"
    cases["data/episodes/01-smoke/01-topic.md"] = put("data/episodes/01-smoke/01-topic.md", "⑤期内可写")
    cases["data/library/memory.md"] = put("data/library/memory.md", "⑥记忆")
    cases["pipeline/notes.md"] = put("pipeline/notes.md", "⑦白名单根外")

    for rel, marker in cases.items():
        assert not is_trusted_doc_path(root, rel), f"{marker} 不该进可信集：{rel}"
    assert is_trusted_doc_path(root, "docs/runbook/03-tts.md")
    assert is_trusted_doc_path(root, "AGENTS.md")
    assert is_trusted_doc_path(root, "config/agent/scopes/director.md")
    assert is_trusted_doc_path(root, "skills/write-script/SKILL.md")

    # 收集函数层：把它们全部路由进去（resident 也指向一个），可信集里只有对照组
    cfg_path = root / "config" / "agent" / "assembly.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["routes"]["_base"]["03"] = ["docs/runbook/03-tts.md", *cases.keys()]
    cfg["resident"]["agents"] = "data/library/memory.md"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    texts = route_trusted_texts("creative", root=root)
    joined = "\n".join(texts)
    for marker in set(cases.values()):
        assert marker not in joined, f"{marker} 混进了可信集"
    assert (root / "docs" / "runbook" / "03-tts.md").read_text(encoding="utf-8").strip() in texts


# ---------------------------------------------------------------------------
# 2026-10-08 spec §A：按参数分级的工具审查（只读 pipeline 模块免卡、草稿写入限次免卡）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["check_script", "status", "check_script 02-script.draft.md"])
def test_readonly_pipeline_modules_are_card_free(episode: Path, root: Path, command: str) -> None:
    (episode / "02-script.draft.md").write_text("草稿", encoding="utf-8")
    verdict = review_tool_call(
        "run_pipeline", {"command": command}, ep_dir=episode, scope="creative", root=root,
    )
    assert verdict.action == "allow", verdict


@pytest.mark.parametrize("command", ["tts run", "clips", "render", "qc", "cover"])
def test_writing_pipeline_modules_still_ask(episode: Path, root: Path, command: str) -> None:
    verdict = review_tool_call(
        "run_pipeline", {"command": command}, ep_dir=episode, scope="creative", root=root,
    )
    assert verdict.action == "ask", verdict


def test_pipeline_module_is_read_from_normalized_argv() -> None:
    """只读判定看规范化 argv 的模块位，不看原始串（`pipeline.check_script` 写法同样识别）。"""
    assert session_mod._pipeline_module_of(["py", "-m", "pipeline.check_script", "x"]) == "check_script"
    assert session_mod._pipeline_module_of(["py", "-m", "pipeline.tts", "check_script"]) == "tts"
    assert session_mod._pipeline_module_of(["py", "check_script"]) is None


def test_draft_writes_free_up_to_limit_then_ask(episode: Path, root: Path) -> None:
    from pipeline.agent.tools import DRAFT_FREE_WRITES_PER_TURN

    args = {"filename": "02-script.draft.md", "content": "x"}
    for used in range(DRAFT_FREE_WRITES_PER_TURN):
        v = review_tool_call("write_episode_file", args, ep_dir=episode, scope="creative",
                             root=root, draft_writes_this_turn=used)
        assert v.action == "allow", used
    v = review_tool_call("write_episode_file", args, ep_dir=episode, scope="creative",
                         root=root, draft_writes_this_turn=DRAFT_FREE_WRITES_PER_TURN)
    assert v.action == "ask"
    assert f"本轮第 {DRAFT_FREE_WRITES_PER_TURN + 1} 次重写草稿" in str(v.request.fields["stop_label"])


@pytest.mark.parametrize("filename", ["01-topic.md", "07-titles.md"])
def test_other_episode_files_ask_from_first_write(episode: Path, root: Path, filename: str) -> None:
    v = review_tool_call("write_episode_file", {"filename": filename, "content": "x", "confirmed": True},
                         ep_dir=episode, scope="creative", root=root)
    assert v.action == "ask"


def test_draft_write_counter_through_real_turns(
    episode: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：一轮里连写 5 次草稿 → 第 4 次弹卡（人批准后清零）→ 第 5 次免卡；下一轮重新计数。"""
    from pipeline.agent import llm as llm_mod

    monkeypatch.setenv("AVA_TEST_KEY", "k")
    state = {"n": 0, "writes": 0}
    per_turn = {1: 5, 2: 3}

    def scripted(messages, tools=None, **kw):
        state["n"] += 1
        if state["n"] > 30:
            raise AssertionError("模型请求超过 30 次：脚本没有停下")
        turn = 1 if state["n"] <= per_turn[1] + 1 else 2
        done = state["writes"] - (0 if turn == 1 else per_turn[1])
        if done < per_turn[turn]:
            state["writes"] += 1
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"c{state['n']}", "type": "function", "function": {
                    "name": "write_episode_file",
                    "arguments": json.dumps({"filename": "02-script.draft.md",
                                             "content": f"第 {state['writes']} 版"}, ensure_ascii=False)}}]}
        return {"role": "assistant", "content": "写完了"}

    monkeypatch.setattr(llm_mod, "chat_complete", scripted)
    channel = FakeChannel(["approve"])
    host = SessionHost(episode, root=root, channel=channel)
    host.ensure_lease().begin("sid-draft-limit")
    session = AgentSession(host, scope_mode="creative", persist=True)
    messages = _seed_messages()
    tracker = SessionContextTracker()

    session.run_turn("改段落三", messages=messages, scope="creative", status=None, tracker=tracker, root=root)
    cards = [r for r in channel.requests if r.kind == "tool_call"]
    assert len(cards) == 1, "一轮 5 次写草稿只该在第 4 次弹一张卡"
    assert "本轮第 4 次重写草稿" in str(cards[0].fields["stop_label"])
    assert (episode / "02-script.draft.md").read_text(encoding="utf-8") == "第 5 版"

    session.run_turn("再改一下", messages=messages, scope="creative", status=None, tracker=tracker, root=root)
    assert len([r for r in channel.requests if r.kind == "tool_call"]) == 1, "新一轮计数应归零"
    assert (episode / "02-script.draft.md").read_text(encoding="utf-8") == "第 8 版"


# ---------------------------------------------------------------------------
# D47：02-script.md 的审查——每次弹卡（不进草稿免卡档）、前提不满足时弹卡前就拒
# ---------------------------------------------------------------------------


def test_d47_script_write_always_asks_with_diff(episode: Path, root: Path) -> None:
    (episode / "02-script.md").write_text("配音：旧\n", encoding="utf-8")
    args = {"filename": "02-script.md", "content": "配音：新\n危险标记: 无\n"}
    v = review_tool_call("write_episode_file", args, ep_dir=episode, scope="creative", root=root,
                         draft_writes_this_turn=0)
    assert v.action == "ask", "定稿写入不享受草稿的免卡档"
    assert "│ +配音：新" in v.request.card_text
    # 模型正文里的「危险标记:」字样不得顶替真标记（取第一行）
    assert v.request.fields["danger"].startswith("[覆盖]"), v.request.fields["danger"]


def test_d47_review_rejects_before_card(episode: Path, root: Path) -> None:
    v = review_tool_call("write_episode_file", {"filename": "02-script.md", "content": "x"},
                         ep_dir=episode, scope="creative", root=root)
    assert v.action == "reject" and "还不存在" in v.reason
    (episode / "02-script.md").write_text("定稿", encoding="utf-8")
    v = review_tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "x"},
                         ep_dir=episode, scope="creative", root=root, draft_writes_this_turn=0)
    assert v.action == "reject" and "基线" in v.reason


# ---------------------------------------------------------------------------
# D48：edits 不成立在弹卡前拒；工具行摘要不再是截断的 JSON
# ---------------------------------------------------------------------------


def test_d48_bad_edits_rejected_before_card(episode: Path, root: Path) -> None:
    (episode / "02-script.md").write_text("配音：甲。\n配音：甲。\n", encoding="utf-8")
    v = review_tool_call("write_episode_file",
                         {"filename": "02-script.md", "edits": [{"old": "配音：甲。", "new": "x"}]},
                         ep_dir=episode, scope="creative", root=root)
    assert v.action == "reject" and "出现了 2 次" in v.reason
    v = review_tool_call("write_episode_file",
                         {"filename": "02-script.md", "edits": [{"old": "配音：甲。\n配音：甲。", "new": "x"}]},
                         ep_dir=episode, scope="creative", root=root)
    assert v.action == "ask"


def test_d48_tool_summary_for_writes_and_pipeline() -> None:
    from pipeline.agent.session import tool_summary

    assert tool_summary("write_episode_file", {"filename": "02-script.md", "edits": [{}, {}]}) == "02-script.md · 局部 2 处"
    assert tool_summary("write_episode_file", {"filename": "02-script.draft.md", "content": "字" * 2600}) == (
        "02-script.draft.md · 整篇 7.6 KB"  # 2600 × 3 字节 = 7800 B ÷ 1024 = 7.62
    )
    assert tool_summary("run_pipeline", {"command": "check_script 02-script.draft.md"}) == "check_script 02-script.draft.md"
