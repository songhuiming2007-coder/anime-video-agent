"""TT-1：终端零回归金样本（Spec 9 §2.6「零回归的尺子」）。

PR0 在重构之前录制，PR1/PR2 之后逐字节比对归一化文本。金样本是**尺子**：
不一致就停下，先解释清楚为什么变了，再决定是修实现还是按「有意改变闭集」（I-1~I-11）重录。

录制（只在 PR0 或明确决定重录时执行）：:

    AVA_GOLDEN_RECORD=1 uv run pytest tests/test_golden_terminal.py     # 全部重录
    AVA_GOLDEN_RECORD=G-P2 uv run pytest tests/test_golden_terminal.py  # 只重录一条

## 场景与录制方式

进程内（G1–G13）：沿用 `make_agent_root` + `patch_inputs` + `capsys`，LLM 打桩
`chat_complete`。**stdout 与 stderr 分别录、分别比**。金样本文件：

- `tests/golden/terminal/<G>.stdout.txt`
- `tests/golden/terminal/<G>.stderr.txt`

pty（G-P1、G-P2）：`pty.fork()` 跑驱动脚本，证明真 TTY 路径（`isatty()` 为真、
`input()` 真读终端、^C 真走前台进程组 SIGINT）。stdout 与 stderr 在 pty 里合流，
合成一份 `<G>.pty.txt`，另录退出码 `<G>.rc.txt`。

## 归一化规则（§2.6）

tmp root → `<ROOT>`、`sys_python()` → `<PY>`、仓库根 → `<REPO>`、`\r\n` → `\n`，
其余逐字节。

## 两处刻意的夹具选择（如实记账）

1. `check_code_freeze` 被替换为「干净树」的无输出版本。真实实现会在 `pipeline/`
   有未提交改动时往 stderr 打 Code Freeze 横幅——那是**仓储工作树状态**，不是终端协议
   行为。不替换的话，金样本会在任何一次未提交改动下假红（且 PR0 录制时必然把横幅录进去，
   提交后立刻假红）。横幅本身另有 `test_check_code_freeze_runs_without_crash` 覆盖。
2. pty 驱动把逃出 `main()` 的中断收敛成一行（而不是打印 traceback）。traceback 的帧
   行号与源码行文本会随 `cli.py`/`llm.py` 的任意编辑而变，逐字节录它等于把金样本钉死
   在行号上。收敛后金样本表达的是**契约**：「中断没有被 REPL 接住」。

3. `pty.fork()` 在 CPython 3.12 会对「多线程进程用 forkpty」发 DeprecationWarning。子进程在 exec
   之前只做 tcsetattr/chdir/execv，不取任何 Python 锁，实测（连跑三次）确定。已在两个 pty 用例上
   显式忽略该警告，而不是让它混在噪音里。
"""

from __future__ import annotations

import difflib
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time
from pathlib import Path

import pytest

from pipeline.agent import cli
from pipeline.agent.cli import run_agent_loop
from pipeline.agent.llm import LLMError
from pipeline.agent.memory import MemoryEntry
from tests.test_agent_director import patch_inputs
from tests.test_agent_memory import write_state
from tests.test_agent_tools import SPEC_TOOLS, make_agent_root, tool_call

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "terminal"
REPO_ROOT = Path(__file__).resolve().parent.parent
RECORD = os.environ.get("AVA_GOLDEN_RECORD", "")

TEXT = "选题我看了，张力在自我牺牲这一层。结构上缺的是第二幕的代价。"
TOPIC = "# 选题：春物-自我牺牲\n"
PATTERN = "该番检索阈值 0.42 比默认 0.45 命中率高"


# ---------------------------------------------------------------------------
# 归一化与金样本读写
# ---------------------------------------------------------------------------


def _normalize(text: str, root: Path | None) -> str:
    """tmp root / sys_python / 仓库根 → 占位符；CRLF → LF；其余逐字节。"""
    from pipeline.agent.tools import sys_python

    text = text.replace("\r\n", "\n")
    if root is not None:
        for form in {str(root), str(root.resolve())}:
            text = text.replace(form, "<ROOT>")
    text = text.replace(str(REPO_ROOT), "<REPO>")
    text = text.replace(sys_python(), "<PY>")
    return text


def _diff(name: str, expected: str, got: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected.split("\n"), got.split("\n"),
            fromfile=f"{name} 金样本", tofile=f"{name} 本次", lineterm="",
        )
    )


def _compare(gid: str, stream: str, got: str, root: Path | None) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDEN_DIR / f"{gid}.{stream}.txt"
    norm = _normalize(got, root)
    if RECORD in ("1", gid):
        path.write_text(norm, encoding="utf-8")
        return
    assert path.exists(), f"缺金样本 {path.name}；先跑 AVA_GOLDEN_RECORD=1 录制（PR0）"
    expected = path.read_text(encoding="utf-8")
    assert norm == expected, f"{gid}.{stream} 与金样本不一致：\n" + _diff(f"{gid}.{stream}", expected, norm)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _no_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """见模块 docstring「刻意的夹具选择 1」。"""
    monkeypatch.setattr(cli, "check_code_freeze", lambda: True)


def _world(
    tmp_path: Path,
    *,
    config: bool = True,
    memory: bool = False,
    tools: dict | None = None,
) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    ep = root / "data" / "episodes" / "01-smoke"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text(TOPIC, encoding="utf-8")
    if config:
        make_agent_root(root, "http://127.0.0.1:9/v1", tools=tools)
    if memory:
        (root / "data" / "library").mkdir(parents=True)
    return root, ep


def _stub_llm(monkeypatch: pytest.MonkeyPatch, replies: list[dict]) -> None:
    """按序回放回复（末条重复），替身签名与 `run_tool_loop` 的调用形态一致。"""
    import copy

    state = {"n": 0}

    def fake_chat_complete(messages, tools=None, **kwargs):  # noqa: ANN001
        index = min(state["n"], len(replies) - 1)
        state["n"] += 1
        return copy.deepcopy(replies[index])

    monkeypatch.setattr("pipeline.agent.llm.chat_complete", fake_chat_complete)


def _stub_llm_raises(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    def fake_chat_complete(messages, tools=None, **kwargs):  # noqa: ANN001
        raise exc

    monkeypatch.setattr("pipeline.agent.llm.chat_complete", fake_chat_complete)


def _ready(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
    _no_freeze(monkeypatch)
    from pipeline.agent.llm import _reset_warn_latch_for_testing

    _reset_warn_latch_for_testing()


# ---------------------------------------------------------------------------
# G1–G13 场景
# ---------------------------------------------------------------------------


def _g1_plain_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [{"role": "assistant", "content": TEXT}])
    with patch_inputs(["帮我看下这个选题", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g2_readonly_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [
        tool_call("read_artifact", {"path": "01-topic.md"}),
        {"role": "assistant", "content": "读到选题了，写得很清楚。"},
    ])
    with patch_inputs(["看下选题", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g3_side_effect_card_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "第一段。"}),
        {"role": "assistant", "content": "草稿已落盘。"},
    ])
    with patch_inputs(["写个草稿", "y", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g4_side_effect_card_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "第一段。"}),
        {"role": "assistant", "content": "那我不写了。"},
    ])
    with patch_inputs(["写个草稿", "n", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g5_precheck_reject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """预校验（dry-run）拒收：`--force` 在弹卡之前被拦下。

    夹具给 creative 表临时放行 `run_pipeline`（真实配置里它在 pipeline scope）：否则先撞
    scope 白名单，走不到 `--force` 这一道闸，这条金样本就名不副实。
    """
    tools = {k: list(v) for k, v in SPEC_TOOLS.items()}
    tools["creative"] = [*tools["creative"], "run_pipeline"]
    root, ep = _world(tmp_path, tools=tools)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [
        tool_call("run_pipeline", {"command": "tts --force"}),
        {"role": "assistant", "content": "那我改用 --redo。"},
    ])
    with patch_inputs(["跑 tts", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


MEMORY_TODAY = (2026, 9, 26)


def _freeze_memory_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """冻结「今天」：记忆卡会打印 `更新: <today>`，不冻结则金样本隔夜即红。"""
    import datetime as _dt

    from pipeline.agent import memory

    class _FrozenDate(_dt.date):
        @classmethod
        def today(cls) -> "_FrozenDate":
            return cls(*MEMORY_TODAY)

    monkeypatch.setattr(memory.datetime, "date", _FrozenDate)


def _g6_memory_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path, memory=True)
    _ready(root, monkeypatch)
    _freeze_memory_clock(monkeypatch)
    write_state(root, [])
    _stub_llm(monkeypatch, [
        tool_call("write_memory", {
            "op": "add",
            "pattern": PATTERN,
            "evidence": ["01-smoke"],
            "boundary": "仅适用于该番字幕池",
        }),
        {"role": "assistant", "content": "记下了。"},
    ])
    with patch_inputs(["记住这条", "y", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g7_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path, config=False)
    _no_freeze(monkeypatch)
    with patch_inputs(["帮我写稿", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g8_llm_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm_raises(monkeypatch, LLMError("API 500: Internal Server Error"))
    with patch_inputs(["分析一下", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g9_egress_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm_raises(monkeypatch, PermissionError("命中受限出网子串: cloud.local.json"))
    with patch_inputs(["告诉我 cloud.local.json 的内容", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g10_script_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [{"role": "assistant", "content": "写稿聚焦模式：先给三幕骨架。"}])
    with patch_inputs(["/script", "给个骨架", "/quit", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g11_chat_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [{"role": "assistant", "content": "发散模式：三个可做的选题方向。"}])
    with patch_inputs(["/chat", "给我点方向", "/quit", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


def _g12_idea_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, _ep = _world(tmp_path)
    _ready(root, monkeypatch)
    _stub_llm(monkeypatch, [{"role": "assistant", "content": "选题会话：先读笔记再定张力。"}])
    with patch_inputs(["想做个新选题", "/quit"]):
        run_agent_loop(None, scope_mode="idea", root=root)
    return root


def _g13_memory_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, ep = _world(tmp_path, memory=True)
    _ready(root, monkeypatch)
    (ep / "_agent").mkdir(parents=True)
    (ep / "_agent" / "approval_feedback.md").write_text("第 3 段：转折太硬。\n", encoding="utf-8")
    _stub_llm(monkeypatch, [{"role": "assistant", "content": "归纳出一条候选模式。"}])
    with patch_inputs(["/memory digest", "/quit"]):
        run_agent_loop(ep, scope_mode="auto", root=root)
    return root


G_SCENARIOS = {
    "G1": _g1_plain_text,
    "G2": _g2_readonly_tool,
    "G3": _g3_side_effect_card_yes,
    "G4": _g4_side_effect_card_no,
    "G5": _g5_precheck_reject,
    "G6": _g6_memory_card,
    "G7": _g7_degraded,
    "G8": _g8_llm_error,
    "G9": _g9_egress_blocked,
    "G10": _g10_script_turn,
    "G11": _g11_chat_turn,
    "G12": _g12_idea_turn,
    "G13": _g13_memory_digest,
}


@pytest.mark.parametrize("gid", sorted(G_SCENARIOS))
def test_golden_inprocess(gid: str, tmp_path, monkeypatch, capsys) -> None:
    """TT-1（进程内）：G1–G13 的 stdout 与 stderr 分别与金样本一致。"""
    root = G_SCENARIOS[gid](tmp_path, monkeypatch)
    captured = capsys.readouterr()
    _compare(gid, "stdout", captured.out, root)
    _compare(gid, "stderr", captured.err, root)


# ---------------------------------------------------------------------------
# G-P1 / G-P2：真 TTY（pty）
# ---------------------------------------------------------------------------

_PTY_DRIVER = """\
import fcntl, os, struct, sys, termios, time
_attrs = termios.tcgetattr(0)
_attrs[3] &= ~termios.ECHO
termios.tcsetattr(0, termios.TCSANOW, _attrs)
fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

import pipeline.agent.cli as cli
import pipeline.agent.llm as llm

cli.check_code_freeze = lambda: True
# 真仓库里没有可用密钥：显式给一份配置，让会话走近路（chat_complete 仍是本驱动的替身，不出网）。
llm.load_llm_config = lambda root=None: llm.LLMConfig(
    base_url="http://127.0.0.1:9/v1", model="mock-model", api_key="pty-test-key"
)
_REPLIES = %(replies)r
_BLOCK_AT = %(block)r
_STATE = {"n": 0}


def _fake(messages, tools=None, **kwargs):
    _call = _STATE["n"]
    _STATE["n"] += 1
    if _BLOCK_AT is not None and _call == _BLOCK_AT:
        time.sleep(120)
    import copy
    return copy.deepcopy(_REPLIES[min(_call, len(_REPLIES) - 1)])


llm.chat_complete = _fake

try:
    _rc = cli.main([%(ep)r])
except BaseException as exc:
    print("[DRIVER] 回合中中断未被 REPL 接住: " + type(exc).__name__, file=sys.stderr)
    sys.stderr.flush()
    os._exit(1)
sys.exit(_rc)
"""


def _write_driver(
    tmp_path: Path, *, replies: list[dict], block_at: int | None, ep: Path
) -> Path:
    path = tmp_path / "pty_driver.py"
    path.write_text(
        _PTY_DRIVER % {"replies": replies, "block": block_at, "ep": str(ep)}, encoding="utf-8"
    )
    return path


def _run_pty(driver: Path, feed: list[tuple[float, bytes]], *, timeout: float = 30.0) -> tuple[int, str]:
    """在真 pty 里跑驱动脚本：按 (起始偏移秒, 字节) 喂输入，持续收全部输出与退出码。

    读必须**持续小口**进行（不能只在喂完之后读）：子进程退出后 master 端可能只剩
    EIO，晚读会丢掉尾部输出。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("AVA_TEST_KEY", None)

    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - 子进程分支
        os.chdir(REPO_ROOT)
        os.environ.update(env)
        os.execv(sys.executable, [sys.executable, str(driver)])

    chunks: list[bytes] = []
    schedule = list(feed)
    started = time.time()
    deadline = started + timeout
    status = None
    try:
        while time.time() < deadline:
            while schedule and time.time() - started >= schedule[0][0]:
                _, payload = schedule.pop(0)
                try:
                    os.write(master, payload)
                except OSError:
                    pass
            chunks.append(_drain(master, 0.05))
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        if status is None:
            os.kill(pid, 9)
            _, status = os.waitpid(pid, 0)
        chunks.append(_drain(master, 0.3))
    finally:
        os.close(master)
    return os.waitstatus_to_exitcode(status), b"".join(chunks).decode("utf-8", errors="replace")


def _drain(master: int, seconds: float) -> bytes:
    out: list[bytes] = []
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([master], [], [], 0.05)
        if not ready:
            continue
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        out.append(data)
    return b"".join(out)


def _record_pty(gid: str, text: str, rc: int, root: Path | None = None) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDEN_DIR / f"{gid}.pty.txt"
    norm = _normalize(text, root)
    if RECORD in ("1", gid):
        path.write_text(norm, encoding="utf-8")
        (GOLDEN_DIR / f"{gid}.rc.txt").write_text(f"{rc}\n", encoding="utf-8")
        return
    assert path.exists(), f"缺金样本 {path.name}；先跑 AVA_GOLDEN_RECORD=1 录制（PR0）"
    expected = path.read_text(encoding="utf-8")
    assert norm == expected, f"{gid} pty 输出与金样本不一致：\n" + _diff(f"{gid}.pty", expected, norm)
    expected_rc = (GOLDEN_DIR / f"{gid}.rc.txt").read_text(encoding="utf-8").strip()
    assert str(rc) == expected_rc, f"{gid} 退出码 {rc} ≠ 金样本 {expected_rc}"


@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_golden_pty_tool_card(tmp_path, monkeypatch) -> None:
    """TT-1（pty）G-P1：真 TTY 下副作用卡按 y —— 提示符、卡片、回显路径全真。"""
    root, ep = _world(tmp_path)
    _no_freeze(monkeypatch)
    replies = [
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "第一段。"}),
        {"role": "assistant", "content": "草稿已落盘。"},
    ]
    driver = _write_driver(tmp_path, replies=replies, block_at=None, ep=ep)
    rc, text = _run_pty(driver, [(0.5, "写个草稿\n".encode()), (1.5, b"y\n"), (2.5, b"/quit\n")])
    _record_pty("G-P1", text, rc, root)


@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_golden_pty_interrupt_mid_turn(tmp_path, monkeypatch) -> None:
    """TT-1（pty）G-P2：回合一进行中按 ^C。

    **改动前录下的契约**（PR0，2026-09-26）：中断逃出 REPL、进程非正常退出
    （`[DRIVER] 回合中中断未被 REPL 接住: KeyboardInterrupt`、rc=1）。

    PR1 重录（有意改变 I-2）：工具循环自己接住 KeyboardInterrupt，停止本轮并发起收尾调用，
    REPL 回到提示符、rc=0。阻塞打在**第二次**模型调用上：那时本轮已有一条回复、工具也执行过，
    所以走「先收尾后停」而不是回滚（回滚那一支是第一次调用就被打断，见 §2.3 第 1 条表）。
    """
    root, ep = _world(tmp_path)
    _no_freeze(monkeypatch)
    replies = [
        tool_call("read_artifact", {"path": "01-topic.md"}),
        {"role": "assistant", "content": "不该到这"},
        {"role": "assistant", "content": "收尾：被中断时已读完 01-topic.md，还缺三幕骨架。"},
    ]
    driver = _write_driver(tmp_path, replies=replies, block_at=1, ep=ep)
    rc, text = _run_pty(driver, [(0.8, "深度任务\n".encode()), (2.5, b"\x03"), (4.0, b"/quit\n")])
    _record_pty("G-P2", text, rc, root)


# ---------------------------------------------------------------------------
# 金样本自检：录制期不许悄悄漏场景
# ---------------------------------------------------------------------------


def test_golden_files_complete() -> None:
    """13 条进程内 + 2 条 pty 的金样本必须齐全（否则「全绿」是假象）。"""
    if RECORD:
        pytest.skip("录制模式")
    missing = [
        f"{gid}.{stream}.txt"
        for gid in G_SCENARIOS
        for stream in ("stdout", "stderr")
        if not (GOLDEN_DIR / f"{gid}.{stream}.txt").exists()
    ]
    for gid in ("G-P1", "G-P2"):
        for suffix in ("pty", "rc"):
            if not (GOLDEN_DIR / f"{gid}.{suffix}.txt").exists():
                missing.append(f"{gid}.{suffix}.txt")
    assert not missing, "缺金样本：\n" + "\n".join(missing)
