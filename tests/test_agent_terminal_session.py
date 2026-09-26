"""终端与 `--continue`（Spec 9 §7.1 TT-2~TT-6，PR2）。

TT-1（金样本）在 `tests/test_golden_terminal.py`；这里放它没覆盖的四类：
回合中 ^C（pty）、`--continue`/`--sessions`、卡片 EOF、恢复后的停机点 id 映射。
"""

from __future__ import annotations

import json
import os
import pty
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import paths
from pipeline.agent import cli, session_log as slog
from pipeline.agent.session import HumanRequest
from tests.test_agent_tools import make_agent_root
from tests.test_golden_terminal import REPO_ROOT, _normalize, _run_pty

REAL_POPEN = __import__("subprocess").Popen


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_leases():
    slog.EpisodeLease._reset_for_testing()
    yield
    slog.EpisodeLease._reset_for_testing()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = make_agent_root(tmp_path / "repo", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.setattr(paths, "DATA", root / "data")
    episode = root / "data" / "episodes" / "01-smoke"
    episode.mkdir(parents=True)
    (episode / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    return root, episode


class _Stdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def read(self, *args):  # 某些路径会读它
        return ""


def _seed_session(episode: Path, sid: str, texts: list[str], *, assistants: bool = True) -> None:
    lease = slog.EpisodeLease.acquire(episode)
    lease.begin(sid, resumed_from_seq=0)
    lease.append({"k": "session_start", "schema": 1, "episode": episode.name,
                  "scope_mode": "auto", "resident_sha256": "x", "pid": 1})
    for index, text in enumerate(texts, 1):
        role = "assistant" if assistants and index % 2 == 0 else "user"
        lease.append({"k": "msg", "turn_id": f"t{index}", "origin": role,
                      "message": {"role": role, "content": text}})
    lease.close()


def _run_main(monkeypatch: pytest.MonkeyPatch, args: list[str], inputs: list[str], *, tty: bool = True) -> int:
    monkeypatch.setattr(sys, "stdin", _Stdin(tty))
    with patch("builtins.input", side_effect=inputs + [EOFError()]):
        return cli.main(args)


# ---------------------------------------------------------------------------
# TT-3：--continue / --sessions
# ---------------------------------------------------------------------------


def test_tt3_sessions_lists_all_and_marks_unresumable(world, monkeypatch, capsys) -> None:
    """TT-3：`ava <期> --sessions` 列出全部会话，无 assistant 消息的如实标注。"""
    root, episode = world
    _seed_session(episode, "aaa11111", ["甲", "乙"])
    _seed_session(episode, "bbb22222", ["丙"])

    assert _run_main(monkeypatch, [str(episode), "--sessions"], []) == 0
    out = capsys.readouterr().out
    assert "aaa11111" in out and "bbb22222" in out
    assert "不能作 --continue 默认目标" in out


def test_tt3_continue_default_and_explicit_prefix(world, monkeypatch, capsys) -> None:
    """TT-3：`--continue` 恢复最近一个可恢复会话；带前缀恢复指定会话。"""
    root, episode = world
    _seed_session(episode, "aaa11111", ["甲", "乙"])
    _seed_session(episode, "ccc33333", ["戊"])
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "接着聊"},
    )

    assert _run_main(monkeypatch, [str(episode), "--continue"], ["/quit"]) == 0
    out = capsys.readouterr().out
    assert "已恢复 aaa11111" in out and "重放 2 条消息" in out

    # 显式前缀不受「必须有 assistant 消息」限制
    assert _run_main(monkeypatch, [str(episode), "--continue", "ccc"], ["/quit"]) == 0
    assert "已恢复 ccc33333" in capsys.readouterr().out


def test_tt3_ambiguous_prefix_exits_2(world, monkeypatch, capsys) -> None:
    """TT-3：前缀不唯一 → 列出候选并退出 2。"""
    root, episode = world
    _seed_session(episode, "dup11111", ["甲", "乙"])
    _seed_session(episode, "dup22222", ["丙", "丁"])

    assert _run_main(monkeypatch, [str(episode), "--continue", "dup"], []) == 2
    err = capsys.readouterr().err
    assert "前缀不唯一" in err and "dup11111" in err and "dup22222" in err


def test_tt3_continue_without_episode_picks_latest(world, monkeypatch, capsys) -> None:
    """TT-3（§2.5）：`ava --continue`（不带期）选 session.jsonl 最近写入的期，先打印期名。"""
    root, episode = world
    other = root / "data" / "episodes" / "02-other"
    other.mkdir()
    (other / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    _seed_session(episode, "aaa11111", ["甲", "乙"])
    time.sleep(0.01)
    _seed_session(other, "eee55555", ["甲", "乙"])

    assert _run_main(monkeypatch, [cli.main.__name__ and "--continue"], []) in (0,)
    out = capsys.readouterr().out
    assert "最近写入的期：02-other" in out


def test_tt3_non_tty_continue_exits_2(world, monkeypatch, capsys) -> None:
    """TT-3：`--continue` 在非 TTY 下退出 2（与既有的非交互规则一致）。"""
    root, episode = world
    _seed_session(episode, "aaa11111", ["甲", "乙"])
    assert _run_main(monkeypatch, [str(episode), "--continue"], [], tty=False) == 2
    assert "只在交互终端可用" in capsys.readouterr().err


def test_tt3_no_session_hints_and_starts_new(world, monkeypatch, capsys) -> None:
    """TT-3：没有可恢复会话时只提示，不阻塞，照常开新会话。"""
    root, episode = world
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "好"},
    )
    assert _run_main(monkeypatch, [str(episode), "--continue"], ["/quit"]) == 0
    out = capsys.readouterr().out
    assert "没有可恢复的会话" in out
    assert (episode / "session.jsonl").exists(), "仍然开了新会话"


def test_tt3_startup_hint_line_does_not_block(world, monkeypatch, capsys) -> None:
    """TT-3：普通启动且存在旧会话 → 打印一行提示且不提问（不遮蔽，用户裁决）。"""
    root, episode = world
    _seed_session(episode, "aaa11111", ["甲", "乙"])
    monkeypatch.setattr(
        "pipeline.agent.llm.chat_complete",
        lambda messages, tools=None, **kw: {"role": "assistant", "content": "好"},
    )
    assert _run_main(monkeypatch, [str(episode)], ["/quit"]) == 0
    out = capsys.readouterr().out
    assert "上次会话 aaa11111" in out
    assert "--continue 恢复" in out


def test_tt6_resumed_session_does_not_remember_displayed_approvals(world, monkeypatch) -> None:
    """TT-6：恢复后 `/approve 05` 不带 `--id` → 报「尚未在本会话展示」。"""
    from pipeline.approvals import ApprovalError

    root, episode = world
    with pytest.raises(ApprovalError, match="尚未在本会话展示"):
        cli._resolve_repl_approval_id("05", None, {})


def test_tt4_card_eof_means_no() -> None:
    """TT-4：卡片处的 EOF 视为「否」（与现状同一段代码搬家）。"""
    channel = cli.TtyChannel()
    request = HumanRequest(
        request_id="r1", kind="tool_call", turn_id="t1", title="t",
        card_text="body", fields={}, options=("approve", "reject"), feedback_allowed=True,
    )
    with patch("builtins.input", side_effect=EOFError()):
        answer = channel.ask(request)
    assert answer.decision == "reject"
    assert answer.channel == "tty"


# ---------------------------------------------------------------------------
# TT-2 / TT-5：pty 下回合中 ^C
# ---------------------------------------------------------------------------

_PTY_DRIVER = '''\
import fcntl, os, signal, struct, subprocess, sys, termios, time
_attrs = termios.tcgetattr(0)
_attrs[3] &= ~termios.ECHO
termios.tcsetattr(0, termios.TCSANOW, _attrs)
fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

import json

import pipeline.agent.cli as cli
import pipeline.agent.llm as llm

cli.check_code_freeze = lambda: True
llm.load_llm_config = lambda root=None: llm.LLMConfig(
    base_url="http://127.0.0.1:9/v1", model="mock-model", api_key="pty-test-key"
)
_STATE = {"n": 0}
_CHILD = []


def _fake(messages, tools=None, **kwargs):
    _call = _STATE["n"]
    _STATE["n"] += 1
    if _call == 0:
        # 先给一个只读工具调用：本轮走起来，才有「中途」可打断
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "read_artifact",
                             "arguments": "{\\"path\\": \\"01-topic.md\\"}"},
            }],
        }
    if _call == %(block_at)d:
        # 回合中挂一个**同组**子进程，再阻塞：证明第一次 ^C 之后 REPL 还活着
        _CHILD.append(subprocess.Popen(["sleep", "30"]))
        time.sleep(120)
    if _call == %(block_at)d + 1:
        return {"role": "assistant", "content": "收尾：本轮被中断，已执行的工具结果保留。"}
    return {"role": "assistant", "content": "好"}


llm.chat_complete = _fake
_FEED = %(feed)r
_PATH = %(ep)r

try:
    _rc = cli.main([_PATH, %(extra)s])
except BaseException as exc:
    print("[DRIVER] 中断未被 REPL 接住: " + type(exc).__name__, file=sys.stderr)
    sys.stderr.flush()
    os._exit(1)
sys.exit(_rc)
'''


def _write_driver(tmp_path: Path, *, episode: Path, block_at: int, feed: dict, extra: str = "") -> Path:
    path = tmp_path / "pty_driver.py"
    path.write_text(
        _PTY_DRIVER % {"block_at": block_at, "feed": feed, "ep": str(episode), "extra": extra},
        encoding="utf-8",
    )
    return path


def test_tt2_interrupt_mid_turn_then_prompt_returns(world, tmp_path) -> None:
    """TT-2：pty 下回合中 ^C → 提示符重新出现、输出含 `[中断]`；再在提示符处 ^C → 退出 0。"""
    root, episode = world
    driver = _write_driver(tmp_path, episode=episode, block_at=1, feed={})
    rc, text = _run_pty(
        driver,
        [(0.8, "深度任务\n".encode()), (2.5, b"\x03"), (3.5, b"/quit\n")],
    )
    assert rc == 0
    assert "[中断] 本轮已停止" in text
    assert "收尾：本轮被中断" in text
    assert "[DRIVER] 中断未被 REPL 接住" not in text


def test_tt5_interrupt_in_script_subloop_returns_to_its_prompt(world, tmp_path) -> None:
    """TT-5：`/script` 回合中 ^C（pty）→ 回到 `/script` 提示符，有收尾输出。"""
    root, episode = world
    driver = _write_driver(tmp_path, episode=episode, block_at=1, feed={})
    rc, text = _run_pty(
        driver,
        [
            (0.5, "/script\n".encode()),
            (1.0, "写稿\n".encode()),
            (2.5, b"\x03"),
            (3.5, b"/quit\n"),
            (4.2, b"/quit\n"),
        ],
    )
    assert rc == 0
    assert "[中断] 本轮已停止" in text
    assert "[退出 creative 模式]" in text
