"""D80 R1：会话标题（`pipeline/agent/session_titles.py` + 协议进程的标题线程 + `/list-sessions` / `/rename-session`）。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline import paths
from pipeline.agent import cli, llm as llm_mod, session_titles
from pipeline.agent.protocol import _maybe_title
from pipeline.agent.session_log import EpisodeLease, dumps

A, B = "aaaaaaaaaaaaaaa1", "bbbbbbbbbbbbbbb2"


@pytest.fixture(autouse=True)
def _clean_leases():
    EpisodeLease._reset_for_testing()
    yield
    EpisodeLease._reset_for_testing()


@pytest.fixture
def ep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    episode = tmp_path / "data" / "episodes" / "01-titles"
    episode.mkdir(parents=True)
    (tmp_path / "data" / "_idea").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return episode


def _log(d: Path, *sids: str) -> bytes:
    lines = []
    for i, sid in enumerate(sids):
        lines.append(dumps({"k": "session_start", "sid": sid, "seq": 0, "ts": f"2026-10-0{i + 1}T10:00:00Z", "schema": 1}))
        lines.append(dumps({"k": "msg", "sid": sid, "seq": 1, "ts": f"2026-10-0{i + 1}T10:00:01Z", "origin": "user",
                            "message": {"role": "user", "content": f"首条消息 {sid[:4]}"}}))
        lines.append(dumps({"k": "msg", "sid": sid, "seq": 2, "ts": f"2026-10-0{i + 1}T10:00:02Z", "origin": "assistant",
                            "message": {"role": "assistant", "content": "好"}}))
    raw = b"".join(lines)
    (d / "session.jsonl").write_bytes(raw)
    return raw


# ---------------- 校验与 CAS ----------------


@pytest.mark.parametrize(("given", "want"), [
    ("检查稿件字数", "检查稿件字数"),
    ("  「清理临时文件」 ", "清理临时文件"),
    ("“查素材”", "查素材"),
    ("改第三段。", "改第三段"),
    ("x" * session_titles.TITLE_MAX_CHARS, "x" * session_titles.TITLE_MAX_CHARS),
    ("x" * (session_titles.TITLE_MAX_CHARS + 1), None),  # 超长不截断：截出来的半句名比兜底更糟
    ("两行\n标题", None),
    ("带\x07控制符", None),
    ("", None),
    ("   ", None),
    ("「」", None),
    (None, None),
    (42, None),
])
def test_validate(given: Any, want: str | None) -> None:
    assert session_titles.validate(given) == want


def test_cas_人改的名不被迟到的生成名覆盖_反向则覆盖(tmp_path: Path) -> None:
    assert session_titles.write(tmp_path, A, "人起的名", "custom") is True
    assert session_titles.write(tmp_path, A, "模型起的名", "generated") is False
    assert session_titles.load(tmp_path)[A]["title"] == "人起的名"
    assert session_titles.write(tmp_path, B, "模型起的名", "generated") is True
    assert session_titles.write(tmp_path, B, "人改的名", "custom") is True
    assert session_titles.write(tmp_path, B, "又一个模型名", "generated") is False
    got = session_titles.load(tmp_path)
    assert {k: (v["title"], v["source"]) for k, v in got.items()} == {A: ("人起的名", "custom"), B: ("人改的名", "custom")}


def test_标题文件坏了_写者拒写不覆盖_读者当没有(tmp_path: Path) -> None:
    path = session_titles.titles_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(session_titles.TitlesBroken):
        session_titles.write(tmp_path, A, "名字", "custom")
    assert path.read_text(encoding="utf-8") == "{not json"
    assert session_titles.load(tmp_path) == {}


# ---------------- 生成 ----------------


def _cfg() -> llm_mod.LLMConfig:
    return llm_mod.LLMConfig(base_url="http://x", model="main", api_key="k", models={"light": "small"})


def test_generate_走light档_剥引号(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(llm_mod, "load_llm_config", lambda root=None: _cfg())

    def fake(messages, **kw):
        calls.append({"messages": messages, **kw})
        return {"role": "assistant", "content": "「检查稿件字数」"}

    monkeypatch.setattr(llm_mod, "chat_complete", fake)
    assert session_titles.generate("帮我看下字数" * 200, "字数正常", None, "creative") == "检查稿件字数"
    assert calls[0]["purpose"] == "light" and calls[0]["timeout"] == session_titles.TITLE_TIMEOUT_S
    assert "tools" not in calls[0]
    # 首问截在 EXCERPT_CHARS：整段长消息不全塞进起名请求
    assert len(calls[0]["messages"][1]["content"]) < session_titles.EXCERPT_CHARS + 50


@pytest.mark.parametrize("outcome", ["raise", "long", "none_cfg"])
def test_generate_任何失败都是None(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    monkeypatch.setattr(llm_mod, "load_llm_config", lambda root=None: None if outcome == "none_cfg" else _cfg())

    def fake(messages, **kw):
        if outcome == "raise":
            raise llm_mod.LLMError("LLM 请求失败: timed out")
        return {"role": "assistant", "content": "这是一个远远超过二十个字上限的模型不听话时给出的会话标题"}

    monkeypatch.setattr(llm_mod, "chat_complete", fake)
    assert session_titles.generate("a", "b", None, "creative") is None


# ---------------- 协议进程的标题线程 ----------------


class _Writer:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def send(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


def _run_title(ep: Path, monkeypatch: pytest.MonkeyPatch, *, reply: Any = "检查稿件字数", stopped: str = "done",
               slots: dict[str, Any] | None = None, before_write=None) -> tuple[_Writer, dict[str, Any]]:
    monkeypatch.setattr(llm_mod, "load_llm_config", lambda root=None: _cfg())

    def fake(messages, **kw):
        if isinstance(reply, Exception):
            raise reply
        llm_mod._LAST_USAGE.set({"prompt_tokens": 999_999})  # 标题请求的 usage：不许串到主回合读数
        if before_write is not None:
            before_write()
        return {"role": "assistant", "content": reply}

    monkeypatch.setattr(llm_mod, "chat_complete", fake)
    host = SimpleNamespace(sid=A, log_dir=ep, root=None)
    writer = _Writer()
    slots = {} if slots is None else slots
    _maybe_title(host, writer, slots, "帮我看下字数", "字数正常", stopped, "creative")
    thread = slots.get("title_thread")
    if thread is not None:
        thread.join(5)
    return writer, slots


def test_首轮正常结束_后台起名落盘并发帧_不碰会话记录_不串usage(ep: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _log(ep, A)
    llm_mod._LAST_USAGE.set({"prompt_tokens": 123})
    writer, _ = _run_title(ep, monkeypatch)
    assert writer.frames == [{"t": "session_title", "sid": A, "title": "检查稿件字数", "source": "generated"}]
    assert session_titles.load(ep)[A]["source"] == "generated"
    assert (ep / "session.jsonl").read_bytes() == raw  # 不进会话记录（rebuild_messages / 金样本不受影响）
    assert llm_mod._LAST_USAGE.get() == {"prompt_tokens": 123}  # 主回合的上下文读数不被标题请求改写


@pytest.mark.parametrize("stopped", ["interrupted", "error", "checkpoint", None])
def test_非正常结束不起名(ep: Path, monkeypatch: pytest.MonkeyPatch, stopped: Any) -> None:
    writer, slots = _run_title(ep, monkeypatch, stopped=stopped)
    assert writer.frames == [] and "title_thread" not in slots
    assert session_titles.load(ep) == {}


def test_已有记录不起名_同一进程同一会话只起一次(ep: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_titles.write(ep, A, "人起的名", "custom")
    writer, slots = _run_title(ep, monkeypatch)
    assert writer.frames == [] and "title_thread" not in slots
    session_titles.titles_path(ep).unlink()
    slots = {"titled": {A}}
    writer, slots = _run_title(ep, monkeypatch, slots=slots)
    assert writer.frames == [] and "title_thread" not in slots


def test_起名途中人改了名_生成名不覆盖也不发帧(ep: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer, _ = _run_title(ep, monkeypatch, before_write=lambda: session_titles.write(ep, A, "人改的名", "custom"))
    assert writer.frames == []
    assert session_titles.load(ep)[A] | {"at": None} == {"title": "人改的名", "source": "custom", "at": None}


@pytest.mark.parametrize("reply", [llm_mod.LLMError("LLM HTTP 500: boom"), "两行\n标题", ""])
def test_起名失败静默_不发帧不落盘(ep: Path, monkeypatch: pytest.MonkeyPatch, reply: Any) -> None:
    writer, _ = _run_title(ep, monkeypatch, reply=reply)
    assert writer.frames == []
    assert session_titles.load(ep) == {}


# ---------------- 人令 ----------------


def test_list_sessions_带标题与来源_无记录时兜底首条消息(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _log(ep, A, B)
    session_titles.write(ep, B, "清理临时文件", "generated")
    assert cli.main([str(ep), "/list-sessions"]) == 0
    rows = {r["sid"]: r for r in json.loads(capsys.readouterr().out)}
    assert (rows[A]["title"], rows[A]["title_source"]) == (rows[A]["first_user"], "first_input")
    assert (rows[B]["title"], rows[B]["title_source"]) == ("清理临时文件", "generated")


def test_rename_session_会话在跑也能改_标题不合格2_会话不存在1(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import subprocess
    import sys
    import textwrap

    _log(ep, A)
    # 另一个进程持期租约（活会话；同进程内重复取租约测不出「改名不取租约」）
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import time
            from pipeline.agent.session_log import EpisodeLease
            EpisodeLease.acquire({str(ep)!r}); print('held', flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        assert cli.main([str(ep), "/delete-session", f"--sid={A}"]) == 3  # 对照：租约确实被占着
        capsys.readouterr()
        assert cli.main([str(ep), "/rename-session", f"--sid={A}", "--title=修稿那次"]) == 0
        assert json.loads(capsys.readouterr().out) == {"title": "修稿那次", "source": "custom"}
    finally:
        holder.kill()
        holder.wait()
    assert session_titles.load(ep)[A]["source"] == "custom"
    assert cli.main([str(ep), "/rename-session", f"--sid={A}", "--title=" + "长" * 21]) == 2
    assert cli.main([str(ep), "/rename-session", f"--sid={A}"]) == 2
    assert cli.main([str(ep), "/rename-session", "--sid=XYZ", "--title=x"]) == 2
    assert cli.main([str(ep), "/rename-session", f"--sid={B}", "--title=x"]) == 1
    assert session_titles.load(ep)[A]["title"] == "修稿那次"


def test_选题会话同样能列出标题与改名(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    idea = paths.ROOT / "data" / "_idea"
    _log(idea, A)
    assert cli.main(["idea", "/rename-session", f"--sid={A}", "--title=挑选题"]) == 0
    capsys.readouterr()
    assert cli.main(["idea", "/list-sessions"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["title"] == "挑选题"


def test_删会话不动标题_回收站行按sid查回标题(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _log(ep, A, B)
    session_titles.write(ep, A, "修稿那次", "custom")
    assert cli.main([str(ep), "/delete-session", f"--sid={A}"]) == 0
    capsys.readouterr()
    assert cli.main([str(ep), "/list-trash"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["sid"], r["title"]) for r in rows] == [(A, "修稿那次")]
    assert session_titles.load(ep)[A]["title"] == "修稿那次"


def test_建期迁会话_标题跟着进新期(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    idea = paths.ROOT / "data" / "_idea"
    _log(idea, A)
    session_titles.write(idea, A, "挑选题", "generated")
    target = paths.ROOT / "data" / "episodes" / "02-new"
    target.mkdir(parents=True)
    rc, moved = cli.migrate_idea_session(target, A)
    assert (rc, moved) == (0, True)
    assert session_titles.load(target)[A]["title"] == "挑选题"
