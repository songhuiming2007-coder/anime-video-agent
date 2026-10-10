"""D45：会话管理的两个裸形态人令（`/list-sessions`、`/delete-session`）与 `move_session_to_trash`。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import paths
from pipeline.agent import cli
from pipeline.agent.session_log import (
    EpisodeLease,
    dumps,
    list_sessions,
    read_log,
    resume_target,
)

A, B, C = "aaaaaaaaaaaaaaa1", "bbbbbbbbbbbbbbb2", "ccccccccccccccc3"


@pytest.fixture(autouse=True)
def _clean_leases():
    EpisodeLease._reset_for_testing()
    yield
    EpisodeLease._reset_for_testing()


@pytest.fixture
def ep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    episode = tmp_path / "data" / "episodes" / "01-sessions"
    episode.mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return episode


def _msg(sid: str, seq: int, role: str, content: str, ts: str) -> bytes:
    return dumps({"k": "msg", "sid": sid, "seq": seq, "ts": ts,
                  "message": {"role": role, "content": content}, "origin": role})


def _write_log(ep: Path, *, torn: bytes = b"") -> bytes:
    """三个会话交错（恢复较早会话时段尾追加），中间夹一行坏行。"""
    lines = [
        dumps({"k": "session_start", "sid": A, "seq": 0, "ts": "2026-09-26T10:00:00Z", "schema": 1}),
        _msg(A, 1, "user", "董香人物志第二期该做到哪一步了，我忘了" * 4, "2026-09-26T10:00:01Z"),
        _msg(A, 2, "assistant", "在 02", "2026-09-26T10:00:02Z"),
        dumps({"k": "session_start", "sid": B, "seq": 0, "ts": "2026-09-27T10:00:00Z", "schema": 1}),
        _msg(B, 1, "user", "  改段落\n三  ", "2026-09-27T10:00:01Z"),
        b"{not json\n",
        _msg(A, 3, "user", "接着聊", "2026-10-08T10:00:00Z"),
        dumps({"k": "session_start", "sid": C, "seq": 0, "ts": "2026-10-01T10:00:00Z", "schema": 1}),
        _msg(C, 1, "user", "第三个", "2026-10-01T10:00:01Z"),
        _msg(C, 2, "assistant", "好", "2026-10-01T10:00:02Z"),
    ]
    raw = b"".join(lines)
    (ep / "session.jsonl").write_bytes(raw + torn)
    return raw


def _lines_of(raw: bytes, sid: str | None) -> list[bytes]:
    out = []
    for line in raw.split(b"\n")[:-1]:
        try:
            owner = json.loads(line).get("sid")
        except json.JSONDecodeError:
            owner = None
        if owner == sid:
            out.append(line + b"\n")
    return out


def test_list_sessions_json(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_log(ep)
    assert cli.main([str(ep), "/list-sessions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["sid"] for r in rows] == [A, B, C]
    assert rows[0]["first_user"] == ("董香人物志第二期该做到哪一步了，我忘了" * 4)[:60]
    assert len(rows[0]["first_user"]) == 60
    assert rows[1]["first_user"] == "改段落 三", "空白应折叠"
    assert rows[1]["resumable"] is False and rows[2]["resumable"] is True
    assert rows[0]["last_activity"] == "2026-10-08T10:00:00Z"
    assert cli.main([str(ep), "/list-sessions", "x"]) == 2


def test_list_sessions_empty_episode(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(ep), "/list-sessions"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_delete_moves_only_target_lines_to_trash(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = _write_log(ep)
    assert cli.main([str(ep), "/delete-session", f"--sid={A}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["moved"] == 4
    trash = Path(out["trash"])
    assert trash == ep / "_agent" / "session-trash" / f"{A}.jsonl"
    assert trash.read_bytes() == b"".join(_lines_of(raw, A))
    after = (ep / "session.jsonl").read_bytes()
    # 其它会话与坏行逐字节保留、原序不变
    expected = b"".join(line + b"\n" for line in raw.split(b"\n")[:-1] if line + b"\n" not in _lines_of(raw, A))
    assert after == expected
    assert b"{not json\n" in after
    assert [s.sid for s in list_sessions(read_log(ep))] == [B, C]
    # 被删的会话不会再被 --continue 选中，前缀也找不到
    assert resume_target(list_sessions(read_log(ep)))[0].sid == C
    assert resume_target(list_sessions(read_log(ep)), A[:6]) == (None, [])


def test_delete_twice_keeps_both_trash_files(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_log(ep)
    assert cli.main([str(ep), "/delete-session", f"--sid={C}"]) == 0
    _write_log(ep)  # 同一个 sid 又出现（例如手工从回收站放回后再删）
    assert cli.main([str(ep), "/delete-session", f"--sid={C}"]) == 0
    assert len(list((ep / "_agent" / "session-trash").iterdir())) == 2


def test_delete_truncates_torn_tail_first(ep: Path) -> None:
    raw = _write_log(ep, torn=b'{"k": "msg", "sid": "bbbb')
    assert cli.main([str(ep), "/delete-session", f"--sid={B}"]) == 0
    after = (ep / "session.jsonl").read_bytes()
    assert after.endswith(b"\n") and b'"sid": "bbbb' not in after.split(b"\n")[-1]
    assert after == b"".join(line + b"\n" for line in raw.split(b"\n")[:-1]
                             if line + b"\n" not in _lines_of(raw, B))


@pytest.mark.parametrize("argv, rc", [
    (["/delete-session"], 2),
    (["/delete-session", "--sid="], 2),
    (["/delete-session", "--sid=ABCDEF0123456789"], 2),
    (["/delete-session", "--sid=../../etc/passwd"], 2),
    (["/delete-session", f"--sid={A}", "--force"], 2),
    (["/delete-session", "--sid=ddddddddddddddd4"], 1),
])
def test_delete_rejections_write_nothing(ep: Path, argv: list[str], rc: int) -> None:
    raw = _write_log(ep)
    assert cli.main([str(ep), *argv]) == rc
    assert (ep / "session.jsonl").read_bytes() == raw
    assert not (ep / "_agent" / "session-trash").exists() or not any((ep / "_agent" / "session-trash").iterdir())


def test_delete_refused_while_episode_has_live_session(ep: Path) -> None:
    import subprocess
    import sys
    import textwrap

    raw = _write_log(ep)
    # 另一个进程持租约（活会话）
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            from pipeline.agent.session_log import EpisodeLease
            EpisodeLease.acquire({str(ep)!r}); print('held', flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        assert cli.main([str(ep), "/delete-session", f"--sid={A}"]) == 3
        assert (ep / "session.jsonl").read_bytes() == raw
    finally:
        holder.kill()
        holder.wait()


def test_lease_released_after_delete(ep: Path) -> None:
    _write_log(ep)
    assert cli.main([str(ep), "/delete-session", f"--sid={A}"]) == 0
    lease = EpisodeLease.acquire(ep)  # 删除进程不得残留租约
    lease.append({"k": "msg", "message": {"role": "user", "content": "新"}})
    assert b'"content": "\xe6\x96\xb0"' in (ep / "session.jsonl").read_bytes()


def test_d57_idea_list_sessions(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """D57：`ava idea /list-sessions` 只读列出选题会话（同一行形状），不需要期目录；没有记录 → []。"""
    assert cli.main(["idea", "/list-sessions"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    idea = ep.parents[1] / "_idea"
    idea.mkdir()
    raw = b"".join([
        dumps({"k": "session_start", "sid": C, "seq": 0, "ts": "2026-10-09T06:32:52Z", "schema": 1}),
        _msg(C, 1, "user", "我需要尼古喵喵的素材", "2026-10-09T06:32:53Z"),
        _msg(C, 2, "assistant", "本地资料库没有", "2026-10-09T06:34:57Z"),
    ])
    (idea / "session.jsonl").write_bytes(raw)
    assert cli.main(["idea", "/list-sessions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["sid"], r["first_user"], r["last_activity"], r["resumable"]) for r in rows] == [
        (C, "我需要尼古喵喵的素材", "2026-10-09T06:34:57Z", True)]
    assert (idea / "session.jsonl").read_bytes() == raw, "只读：不取租约、不截断、不改一个字节"


# ---------------------------------------------------------------------------
# D66：回收站查看（/list-trash）与选择性彻底清空（/purge-trash）
# ---------------------------------------------------------------------------

D = "ddddddddddddddd4"
E = "eeeeeeeeeeeeeee5"


def _trash_dir(ep: Path) -> Path:
    return ep / "_agent" / "session-trash"


def _trash_file(ep: Path, name: str, raw: bytes) -> Path:
    d = _trash_dir(ep)
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(raw)
    return f


def _seg(sid: str, user: str = "问题", ts: str = "2026-10-10T01:00:00Z") -> bytes:
    return b"".join([
        dumps({"k": "session_start", "sid": sid, "seq": 0, "ts": ts, "schema": 1}),
        _msg(sid, 1, "user", user, ts),
        _msg(sid, 2, "assistant", "答", ts),
    ])


def test_list_trash_empty_dir_or_missing(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(ep), "/list-trash"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    _trash_dir(ep).mkdir(parents=True)
    assert cli.main([str(ep), "/list-trash"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main([str(ep), "/list-trash", "x"]) == 2


def test_list_trash_two_segments(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f1 = _trash_file(ep, f"{D}.jsonl", _seg(D, "第一段", "2026-10-09T08:00:00Z"))
    f2 = _trash_file(ep, f"{E}-20261010T010203000000.jsonl", _seg(E, "第二段", "2026-10-10T02:00:00Z"))
    assert cli.main([str(ep), "/list-trash"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["file"] for r in rows] == sorted([f1.name, f2.name])
    by_file = {r["file"]: r for r in rows}
    assert by_file[f1.name]["sid"] == D and by_file[f1.name]["message_count"] == 2
    assert by_file[f1.name]["last_ts"] == "2026-10-09T08:00:00Z"
    assert by_file[f1.name]["bytes"] == f1.stat().st_size
    assert by_file[f2.name]["sid"] == E, "带时间戳后缀的同名段照常聚合出 sid"
    assert "empty" not in by_file[f1.name] and "parse_error" not in by_file[f1.name]
    assert "partial" not in by_file[f1.name]


def test_list_trash_empty_and_parse_error(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _trash_file(ep, f"{D}.jsonl", b"")
    _trash_file(ep, f"{E}.jsonl", b"\xe4\xbd\xa0\xe5\xa5\xbd this is not jsonl at all\n")
    assert cli.main([str(ep), "/list-trash"]) == 0
    rows = {r["file"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[f"{D}.jsonl"]["empty"] is True and rows[f"{D}.jsonl"]["sid"] is None
    assert rows[f"{E}.jsonl"]["parse_error"] is True and rows[f"{E}.jsonl"]["sid"] is None


def test_list_trash_partial_two_forms(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """半残两种形态（红队发现 4 + 二轮 R1）：末尾残行、中间坏行——都出摘要且带 partial。"""
    good = _seg(D)
    _trash_file(ep, f"{D}.jsonl", good + b'{"k": "msg", "sid": "ddd')          # 末尾残行
    mid_bad = _seg(E, "前", "2026-10-10T01:00:00Z") + b"{not json\n" + _msg(E, 9, "user", "后", "2026-10-10T03:00:00Z")
    _trash_file(ep, f"{E}.jsonl", mid_bad)                                        # 中间坏行
    assert cli.main([str(ep), "/list-trash"]) == 0
    rows = {r["file"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[f"{D}.jsonl"]["partial"] is True and rows[f"{D}.jsonl"]["sid"] == D
    assert rows[f"{E}.jsonl"]["partial"] is True and rows[f"{E}.jsonl"]["sid"] == E
    assert rows[f"{E}.jsonl"]["message_count"] == 3, "中间坏行不吞条数"


def test_purge_trash_happy_path(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import hashlib

    raw = _write_log(ep)
    before = hashlib.sha256((ep / "session.jsonl").read_bytes()).hexdigest()
    f = _trash_file(ep, f"{D}.jsonl", _seg(D))
    assert cli.main([str(ep), "/purge-trash", f"--file={f.name}"]) == 0
    assert json.loads(capsys.readouterr().out) == {"purged": f.name}
    assert not f.exists()
    assert hashlib.sha256((ep / "session.jsonl").read_bytes()).hexdigest() == before, "主日志零接触"
    assert cli.main([str(ep), "/purge-trash", f"--file={f.name}"]) == 1, "重复删如实报错，不幂等"


@pytest.mark.parametrize("name", ["../../x.jsonl", "/etc/passwd", "x.txt", "..", "a/b.jsonl"])
def test_purge_trash_rejects_bad_names(ep: Path, name: str) -> None:
    f = _trash_file(ep, f"{D}.jsonl", _seg(D))
    assert cli.main([str(ep), "/purge-trash", f"--file={name}"]) == 2
    assert f.exists(), "参数非法：一字不动"


def test_purge_trash_rejects_symlink_escape(ep: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_seg(D))
    link = _trash_dir(ep)
    link.mkdir(parents=True, exist_ok=True)
    (link / f"{E}.jsonl").symlink_to(outside)
    assert cli.main([str(ep), "/purge-trash", f"--file={E}.jsonl"]) == 2
    assert outside.exists(), "软链指向回收站外：拒删，目标毫发无伤"


def test_purge_trash_lock_held_exits_3(ep: Path) -> None:
    """红队发现 1 用例：另一进程持 session-trash.lock 时 purge 退出码 3（与 /delete-session 同码）。"""
    import subprocess
    import sys
    import textwrap

    f = _trash_file(ep, f"{D}.jsonl", _seg(D))
    lock = _trash_dir(ep).parent / "session-trash.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import fcntl, os, time
            fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print('held', flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        assert cli.main([str(ep), "/purge-trash", f"--file={f.name}"]) == 3
        assert f.exists(), "锁被占：拒删"
    finally:
        holder.kill()
        holder.wait()
    assert cli.main([str(ep), "/purge-trash", f"--file={f.name}"]) == 0, "锁释放后正常删"


def test_purge_trash_never_creates_main_log(ep: Path) -> None:
    """回归：该期原本没有主日志时，purge 任何路径都不得创建它（EpisodeLease 的 O_CREAT 陷阱）。"""
    f = _trash_file(ep, f"{D}.jsonl", _seg(D))
    assert not (ep / "session.jsonl").exists()
    assert cli.main([str(ep), "/purge-trash", f"--file={f.name}"]) == 0
    assert not (ep / "session.jsonl").exists()
    assert cli.main([str(ep), "/purge-trash", "--file=nope.jsonl"]) == 1
    assert not (ep / "session.jsonl").exists()


def test_d66_idea_list_and_purge_trash(ep: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """选题回收站：data/_idea/_agent/session-trash/，同期会话同一形状与退出码。"""
    idea_trash = ep.parents[1] / "_idea" / "_agent" / "session-trash"
    idea_trash.mkdir(parents=True)
    (idea_trash / f"{D}.jsonl").write_bytes(_seg(D, "选题段"))
    assert cli.main(["idea", "/list-trash"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["file"], r["sid"], r["message_count"]) for r in rows] == [(f"{D}.jsonl", D, 2)]
    assert cli.main(["idea", "/purge-trash", f"--file={D}.jsonl"]) == 0
    assert json.loads(capsys.readouterr().out) == {"purged": f"{D}.jsonl"}
    assert not (idea_trash / f"{D}.jsonl").exists()
    assert cli.main(["idea", "/list-trash"]) == 0
    assert json.loads(capsys.readouterr().out) == []
