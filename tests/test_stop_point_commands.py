"""Spec 11 §3.1：core 八个停机点裸形态子命令（TC-5~TC-12、TC-14）。

全部夹具期都在 tmp_path 的假仓库根下（`monkeypatch paths.ROOT`），**绝不碰真实
`data/`**；`/voice-info`、`/voice-parse` 另加目录树哈希前后不変断言（纯读/纯算）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pipeline import corrections, g2p, paths, tts
from pipeline.agent import cli

SCRIPT = """## 段落 1

配音：雪乃和大老师都没想到。

画面：
  查询: 甲

## 段落 2

配音：团子说得对。

画面：
  查询: 乙
"""

DRAFT = SCRIPT.replace("都没想到。", "都没想到吧。")

# 合规纠错原文（词必须在段内：parse_correction 会校验）
PATCH_LINE = "1段 雪乃 改成 xuě nǎi"
TIMBRE_LINE = "2段 语气发飘 换种子"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """假仓库根：data/episodes/ep-test（稿件 + 草稿 + 03-audio）。"""
    ep = tmp_path / "data" / "episodes" / "ep-test"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    (ep / "02-script.draft.md").write_text(DRAFT, encoding="utf-8")
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return ep


def run_cli(ep: Path, *argv: str, stdin: str | None = None,
            monkeypatch: pytest.MonkeyPatch | None = None) -> int:
    """in-process 调 `cli.main`；stdin 给出时以 StringIO 顶替（模拟 host 的 pipe）。"""
    if stdin is not None and monkeypatch is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return cli.main([str(ep), *argv])


def tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """目录树指纹：相对路径 → (size, mtime_ns)。"""
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def twin_repo(repo: Path, name: str) -> Path:
    """复制一个双胞胎期目录（同一夹具、同一时刻状态），用于「CLI vs 直调」等价对拍。"""
    import shutil

    twin = repo.parent / name
    shutil.copytree(repo, twin)
    return twin


# ---------------------------------------------------------------------------
# TC-5 /save-script
# ---------------------------------------------------------------------------


class TestSaveScript:
    def test_指纹相符则落盘并回新指纹(self, repo, monkeypatch, capsys):
        script = repo / "02-script.md"
        st = script.stat()
        new_text = "## 段落 1\n\n配音：改写后的正文。\n"
        rc = run_cli(
            repo, "/save-script", f"--expect-size={st.st_size}",
            f"--expect-mtime-ns={st.st_mtime_ns}", stdin=new_text, monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert script.read_text(encoding="utf-8") == new_text
        now = script.stat()
        assert json.loads(capsys.readouterr().out.strip()) == {
            "size": now.st_size, "mtime_ns": now.st_mtime_ns
        }

    def test_同长异mtime拒存且一字不写(self, repo, monkeypatch, capsys):
        """MUT-4：指纹比对退化成只比 size 时本用例变红（显式构造同长异 mtime）。"""
        script = repo / "02-script.md"
        st = script.stat()
        os.utime(script, ns=(st.st_mtime_ns + 5_000_000_000, st.st_mtime_ns + 5_000_000_000))
        before = script.read_bytes()

        rc = run_cli(
            repo, "/save-script", f"--expect-size={st.st_size}",
            f"--expect-mtime-ns={st.st_mtime_ns}", stdin="改坏了", monkeypatch=monkeypatch,
        )
        assert rc == 1
        assert script.read_bytes() == before
        assert "磁盘版本已变" in capsys.readouterr().err

    def test_断言不存在但文件在则拒存(self, repo, monkeypatch):
        rc = run_cli(repo, "/save-script", "--expect-size=-1", "--expect-mtime-ns=-1",
                     stdin="新正文", monkeypatch=monkeypatch)
        assert rc == 1

    def test_从草稿新建_断言不存在且文件缺席则新建(self, repo, monkeypatch):
        (repo / "02-script.md").unlink()
        rc = run_cli(repo, "/save-script", "--expect-size=-1", "--expect-mtime-ns=-1",
                     stdin="# 新稿\n", monkeypatch=monkeypatch)
        assert rc == 0
        assert (repo / "02-script.md").read_text(encoding="utf-8") == "# 新稿\n"

    def test_正文超上限退2(self, repo, monkeypatch):
        blob = "x" * (cli.SAVE_SCRIPT_MAX_BYTES + 10)
        rc = run_cli(repo, "/save-script", "--expect-size=-1", "--expect-mtime-ns=-1",
                     stdin=blob, monkeypatch=monkeypatch)
        assert rc == 2

    def test_期目录越界退1(self, repo, monkeypatch, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        rc = run_cli(outside, "/save-script", "--expect-size=-1", "--expect-mtime-ns=-1",
                     stdin="x", monkeypatch=monkeypatch)
        assert rc == 1

    def test_缺参数退2(self, repo, monkeypatch):
        assert run_cli(repo, "/save-script", "--expect-size=1", monkeypatch=monkeypatch) == 2


# ---------------------------------------------------------------------------
# TC-6 /seal-script
# ---------------------------------------------------------------------------


class TestSealScript:
    def test_patch与手工git命令逐字节一致(self, repo, capsys):
        assert run_cli(repo, "/seal-script") == 0
        patch = repo / "02-diff.patch"
        expected = subprocess.run(
            ["git", "diff", "--no-index", "02-script.draft.md", "02-script.md"],
            cwd=repo, capture_output=True,
        ).stdout
        assert patch.read_bytes() == expected
        assert capsys.readouterr().out.strip() == str(len(expected))

    def test_空diff拒封且既有patch不被清空(self, repo, monkeypatch, capsys):
        """MUT-6：空 diff 时写空文件 / 或改用 difflib 都会被这里挡住。"""
        (repo / "02-diff.patch").write_text("上一轮的封板痕迹\n", encoding="utf-8")
        (repo / "02-script.draft.md").write_text(SCRIPT, encoding="utf-8")   # 与正稿一致
        rc = run_cli(repo, "/seal-script")
        assert rc == 1
        assert (repo / "02-diff.patch").read_text(encoding="utf-8") == "上一轮的封板痕迹\n"
        assert "未做任何修改" in capsys.readouterr().err

    def test_缺draft退1(self, repo, capsys):
        (repo / "02-script.draft.md").unlink()
        assert run_cli(repo, "/seal-script") == 1
        assert not (repo / "02-diff.patch").exists()

    def test_缺正稿退1(self, repo):
        (repo / "02-script.md").unlink()
        assert run_cli(repo, "/seal-script") == 1


# ---------------------------------------------------------------------------
# TC-7 /voice-info
# ---------------------------------------------------------------------------


class TestVoiceInfo:
    def test_schema逐键对拍与纯读(self, repo, capsys):
        before = tree_snapshot(repo)
        assert run_cli(repo, "/voice-info") == 0
        assert tree_snapshot(repo) == before
        info = json.loads(capsys.readouterr().out.strip())

        segs = tts.parse_script(repo / "02-script.md")
        assert info["v"] == 1
        assert info["engine"] == "" and info["engine_cloud"] is False
        assert [(s["label"], s["index"], s["text"]) for s in info["segments"]] == [
            (str(s.label), s.index, s.text) for s in segs
        ]
        assert [s["wav"] for s in info["segments"]] == ["seg-01.wav", "seg-02.wav"]
        assert [s["wav_exists"] for s in info["segments"]] == [False, False]
        assert [s["has_attic"] for s in info["segments"]] == [False, False]
        assert info["pending_corrections"] == []
        assert info["apply_patch_lock"] == {"exists": False, "pid": None, "pid_alive": None}
        hetero = g2p.scan_heteronyms("\n".join(s.text for s in segs))
        assert info["heteronyms"] == [
            {"char": h["char"], "readings": list(h.get("readings", []))[:3]}
            for h in hetero[:10]
        ]
        assert len(info["heteronyms"]) <= 10

    def test_云端引擎与锁残留字段(self, repo, capsys):
        (repo / "03-audio" / "manifest.json").write_text(
            json.dumps({"engine": "cuda-cloud-v1", "segments": []}), encoding="utf-8")
        (repo / "03-audio" / ".apply_patch.lock").write_text("999999999", encoding="utf-8")
        (repo / "03-audio" / "corrections.json").write_text(
            json.dumps([{"id": 1, "segment": "1", "applied": False, "word": "雪乃"}]),
            encoding="utf-8")

        assert run_cli(repo, "/voice-info") == 0
        info = json.loads(capsys.readouterr().out.strip())
        assert info["engine"] == "cuda-cloud-v1" and info["engine_cloud"] is True
        assert info["pending_corrections"] == [
            {"id": 1, "segment": "1", "applied": False, "word": "雪乃"}
        ]
        assert info["apply_patch_lock"] == {
            "exists": True, "pid": 999999999, "pid_alive": False
        }

    def test_has_attic由快照供给(self, repo, capsys):
        snap = repo / "03-audio" / "attic" / "20260101-000000"
        snap.mkdir(parents=True)
        (snap / "manifest.json").write_text(
            json.dumps({"segments": [{"label": "1", "file": "seg-01.wav"}]}), encoding="utf-8")
        (snap / "seg-01.wav").write_bytes(b"RIFF-fake")

        assert run_cli(repo, "/voice-info") == 0
        info = json.loads(capsys.readouterr().out.strip())
        assert [s["has_attic"] for s in info["segments"]] == [True, False]

    def test_无稿件退1(self, repo):
        (repo / "02-script.md").unlink()
        assert run_cli(repo, "/voice-info") == 1


# ---------------------------------------------------------------------------
# TC-8 /voice-parse（纯算）+ /voice-add 契约断言
# ---------------------------------------------------------------------------


class TestVoiceParse:
    def test_与parse_correction直调同结构且纯算(self, repo, monkeypatch, capsys):
        before = tree_snapshot(repo)
        assert run_cli(repo, "/voice-parse", stdin=PATCH_LINE, monkeypatch=monkeypatch) == 0
        assert tree_snapshot(repo) == before
        got = json.loads(capsys.readouterr().out.strip())

        patch = corrections.parse_correction(PATCH_LINE, tts.parse_script(repo / "02-script.md"))
        assert got == {
            "v": 1, "segment": patch.segment, "kind": patch.kind, "word": patch.word,
            "heard": patch.heard, "target_tone3": patch.target_tone3, "issue": patch.issue,
            "action": patch.action, "scope": patch.scope,
        }
        assert "raw" not in got and "seed_pin" not in got

    def test_PatchError文案与终端一致(self, repo, monkeypatch, capsys):
        bad = "99段 重叠 改成 zhòng dié"
        assert run_cli(repo, "/voice-parse", stdin=bad, monkeypatch=monkeypatch) == 1
        err = capsys.readouterr().err.strip()
        with pytest.raises(corrections.PatchError) as exc:
            corrections.parse_correction(bad, tts.parse_script(repo / "02-script.md"))
        assert err == str(exc.value)

    def test_add只按文法原文处理_与直调逐键一致(self, repo, monkeypatch, capsys):
        """TC-8 契约断言（替代已删除的 MUT-8）：stdin 不被当 JSON 消费。"""
        assert run_cli(repo, "/voice-add", stdin=PATCH_LINE, monkeypatch=monkeypatch) == 0
        landed = json.loads(capsys.readouterr().out.strip())

        twin = twin_repo(repo, "ep-direct")
        # 双胞胎在原期已被写入，回退成同一初始状态
        (twin / "03-audio" / "corrections.json").unlink(missing_ok=True)
        direct = corrections.append_correction(
            twin, corrections.parse_correction(PATCH_LINE, tts.parse_script(twin / "02-script.md")))

        drop = {"created_at", "seed_pin"}     # 时间戳与随机种子天然不同
        assert {k: v for k, v in landed.items() if k not in drop} == {
            k: v for k, v in direct.items() if k not in drop
        }
        # 落盘文件同样只差 created_at（id/内容必须逐键一致）
        strip = lambda es: [{k: v for k, v in e.items() if k != "created_at"} for e in es]
        assert strip(corrections.load_corrections_raw(repo)[0]) == \
            strip(corrections.load_corrections_raw(twin)[0])

    def test_解析失败退1且零写盘(self, repo, monkeypatch):
        assert run_cli(repo, "/voice-add", stdin="乱写一句", monkeypatch=monkeypatch) == 1
        assert not (repo / "03-audio" / "corrections.json").exists()


# ---------------------------------------------------------------------------
# TC-9 /voice-add
# ---------------------------------------------------------------------------


class TestVoiceAdd:
    def test_连续两次录入产生两个不同id(self, repo, monkeypatch, capsys):
        assert run_cli(repo, "/voice-add", stdin=PATCH_LINE, monkeypatch=monkeypatch) == 0
        first = json.loads(capsys.readouterr().out.strip())
        assert run_cli(repo, "/voice-add", stdin=TIMBRE_LINE, monkeypatch=monkeypatch) == 0
        second = json.loads(capsys.readouterr().out.strip())

        assert (first["id"], second["id"]) == (1, 2)
        entries, _ = corrections.load_corrections_raw(repo)
        assert [e["id"] for e in entries] == [1, 2]
        assert entries[1]["issue"] == "发飘" and "seed_pin" in entries[1]
        assert all(e["applied"] is False for e in entries)

    def test_锁存在时退1(self, repo, monkeypatch, capsys):
        (repo / "03-audio" / ".apply_patch.lock").write_text("1", encoding="utf-8")
        assert run_cli(repo, "/voice-add", stdin=PATCH_LINE, monkeypatch=monkeypatch) == 1
        assert "应用纠错进行中" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# TC-10 /voice-revert、/voice-retract
# ---------------------------------------------------------------------------


def make_attic_fixture(ep: Path, applied: bool = True) -> None:
    """造一份可回滚的期：当前 wav/manifest + 旧快照 + 绑段 1 的纠错条目。"""
    audio = ep / "03-audio"
    (audio / "seg-01.wav").write_bytes(b"NEW-TAKE")
    (audio / "seg-02.wav").write_bytes(b"TAKE-2")
    (audio / "manifest.json").write_text(json.dumps({
        "engine": "local", "segments": [{"label": "1", "file": "seg-01.wav"}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    snap = audio / "attic" / "20260101-000000"
    snap.mkdir(parents=True)
    (snap / "seg-01.wav").write_bytes(b"OLD-TAKE")
    (snap / "manifest.json").write_text(json.dumps({
        "segments": [{"label": "1", "file": "seg-01.wav", "take": "old"}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (audio / "corrections.json").write_text(json.dumps([{
        "id": 3, "segment": "1", "kind": "pronunciation", "scope": "segment",
        "word": "雪乃", "heard": None, "target_tone3": "xue3nai3", "action": "inject",
        "raw": PATCH_LINE, "applied": applied, "applied_at": "2026-01-01T00:00:00",
        "affected": ["1"], "done_segments": ["1"], "created_at": "2026-01-01T00:00:00",
    }], ensure_ascii=False, indent=2), encoding="utf-8")


class TestVoiceRevertRetract:
    def test_revert与直调等价(self, repo, capsys):
        make_attic_fixture(repo)
        twin = twin_repo(repo, "ep-direct-revert")

        assert run_cli(repo, "/voice-revert", "1") == 0
        assert "已从快照" in capsys.readouterr().out
        corrections.revert_segment(twin, "1")

        assert (repo / "03-audio" / "seg-01.wav").read_bytes() == b"OLD-TAKE"
        for rel in ("03-audio/seg-01.wav", "03-audio/manifest.json", "03-audio/corrections.json"):
            assert (repo / rel).read_bytes() == (twin / rel).read_bytes(), rel
        entries, _ = corrections.load_corrections_raw(repo)
        assert entries[0]["applied"] is False and entries[0]["done_segments"] == []

    def test_revert不存在段退1且stderr为原消息(self, repo, capsys):
        make_attic_fixture(repo)
        assert run_cli(repo, "/voice-revert", "9") == 1
        assert "未找到包含段 9 的 attic 快照" in capsys.readouterr().err

    def test_retract与直调等价(self, repo, capsys):
        make_attic_fixture(repo)
        twin = twin_repo(repo, "ep-direct-retract")

        assert run_cli(repo, "/voice-retract", "3") == 0
        assert "已撤回纠错 #3" in capsys.readouterr().out
        corrections.retract_correction(twin, 3)

        assert corrections.load_corrections_raw(repo)[0] == corrections.load_corrections_raw(twin)[0] == []
        assert (repo / "03-audio" / "corrections.json").read_bytes() == \
            (twin / "03-audio" / "corrections.json").read_bytes()

    def test_retract不存在条目退1(self, repo, capsys):
        make_attic_fixture(repo)
        assert run_cli(repo, "/voice-retract", "99") == 1
        assert "未找到 id=99 的纠错条目" in capsys.readouterr().err

    def test_retract非整数退2(self, repo):
        assert run_cli(repo, "/voice-retract", "abc") == 2


# ---------------------------------------------------------------------------
# TC-11 /record-time
# ---------------------------------------------------------------------------


class TestRecordTime:
    def test_条目形状与直调一致且含source(self, repo, monkeypatch):
        left = time.time() - 1
        entered = left - 300                      # 5 分钟
        assert run_cli(repo, "/record-time", "02.5", f"--entered={entered}",
                       f"--left={left}", monkeypatch=monkeypatch) == 0
        landed = json.loads((repo / "human_time.json").read_text(encoding="utf-8"))

        twin = twin_repo(repo, "ep-direct-time")
        (twin / "human_time.json").unlink(missing_ok=True)
        cli.record_human_time(twin, "02.5", entered, left, source="desktop")
        direct = json.loads((twin / "human_time.json").read_text(encoding="utf-8"))

        assert landed == direct
        assert landed[0]["source"] == "desktop"
        assert landed[0]["minutes"] == 5.0

    def test_短区间不丢数据(self, repo, monkeypatch):
        """MUT-11 的 core 半：采集端不设时长下限（0.5 s 照落盘，与终端口径一致）。"""
        left = time.time() - 1
        entered = left - 0.5
        assert run_cli(repo, "/record-time", "03.5", f"--entered={entered}",
                       f"--left={left}", monkeypatch=monkeypatch) == 0
        landed = json.loads((repo / "human_time.json").read_text(encoding="utf-8"))
        assert len(landed) == 1 and landed[0]["stop"] == "03.5"

    def test_成功后emit事件(self, repo, monkeypatch):
        """MUT-10：漏 emit 时本用例是唯一变红点。"""
        left = time.time() - 1
        assert run_cli(repo, "/record-time", "05", f"--entered={left - 60}",
                       f"--left={left}", monkeypatch=monkeypatch) == 0

        from pipeline.jobs import get_publisher
        get_publisher().close(timeout=1.0)
        events = [json.loads(line) for line in
                  (repo / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        recorded = [e for e in events if e["type"] == "human_time_recorded"]
        assert len(recorded) == 1
        assert recorded[0]["payload"] == {"stop": "05", "minutes": 1.0}

    @pytest.mark.parametrize("argv", [
        ("/record-time", "07", f"--entered={time.time() - 60}", f"--left={time.time() - 1}"),
        ("/record-time", "02.5", f"--entered={time.time()}", f"--left={time.time() - 1}"),
        ("/record-time", "02.5", f"--entered={time.time()}", f"--left={time.time() + 3600}"),
    ])
    def test_校验失败一律退2(self, repo, argv):
        assert run_cli(repo, *argv) == 2
        assert not (repo / "human_time.json").exists()


# ---------------------------------------------------------------------------
# TC-12 RF17-C1：/voice done 的 EOF 默认改「否」
# ---------------------------------------------------------------------------


def test_voice_done_EOF取否不执行apply_patch(repo, monkeypatch, capsys):
    """TC-12 / MUT-13：EOF 不再默认「是」；TTY 以外的 stdin 到不了 apply-patch。"""
    make_attic_fixture(repo, applied=False)
    monkeypatch.setattr("pipeline.tts.run",
                        lambda *a, **k: pytest.fail("EOF 分支不得执行 apply-patch"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("done\n"))

    assert cli.main([str(repo), "/voice"]) == 0
    assert "未获确认，未执行增量重配" in capsys.readouterr().out
    assert not (repo / "03-audio" / ".apply_patch.lock").exists()


# ---------------------------------------------------------------------------
# TC-14 纯洁性回归
# ---------------------------------------------------------------------------

def test_停机点子命令不引入重依赖():
    """core 新增代码只用 stdlib：import 后 sys.modules 不得含 numpy/torch/mlx。"""
    probe = (
        "import sys, pipeline.agent.cli, pipeline.check_script; "
        "bad = [m for m in ('numpy', 'torch', 'mlx') if m in sys.modules]; "
        "assert not bad, bad"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                       cwd=paths.ROOT)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# §3.1：REPL 与裸形态共用同一份分派
# ---------------------------------------------------------------------------


def test_repl_也路由停机点子命令(repo, capsys):
    from unittest.mock import patch

    with patch("builtins.input", side_effect=["/voice-info", "/quit"]):
        assert cli.run_repl(repo) == 0
    out = capsys.readouterr().out
    assert '"v": 1' in out
    assert "未知命令" not in out


# ---------------------------------------------------------------------------
# §5.2 子进程纯洁性：env -i PATH=<白名单> 下行为不变（A3 的回归化）
# ---------------------------------------------------------------------------

def _cleanenv_cli(root: Path, *argv: str, stdin: str | None = None, path: str) -> subprocess.CompletedProcess:
    """在最小环境下跑 core 子命令（真子进程；root 在进程内改 paths.ROOT）。"""
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from pipeline import paths\n"
        "paths.ROOT = Path(sys.argv[1])\n"
        "from pipeline.agent import cli\n"
        "sys.exit(cli.main([sys.argv[2]] + sys.argv[3:]))\n"
    )
    import os
    return subprocess.run(
        [sys.executable, "-c", code, str(root), str(argv[0]), *argv[1:]],
        capture_output=True, text=True, input=stdin, cwd=paths.ROOT,
        env={"PATH": path, "HOME": os.environ.get("HOME", "/tmp"), "PYTHONUTF8": "1"},
    )


def test_停机点子命令在最小_PATH_下行为不变(tmp_path):
    """白名单 PATH（无 /opt/homebrew，无 shell 环境）下：/save-script 与 /voice-info 照常，/seal-script 靠 /usr/bin/git。"""
    ep = tmp_path / "data" / "episodes" / "clean-env"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    (ep / "02-script.draft.md").write_text(DRAFT, encoding="utf-8")
    st = (ep / "02-script.md").stat()

    saved = _cleanenv_cli(
        tmp_path,
        str(ep), "/save-script",
        f"--expect-size={st.st_size}", f"--expect-mtime-ns={st.st_mtime_ns}",
        stdin=SCRIPT.replace("都没想到。", "都没想到！！"),
        path="/usr/bin:/bin:/usr/sbin:/sbin",
    )
    assert saved.returncode == 0, saved.stderr
    assert "都没想到！！" in (ep / "02-script.md").read_text(encoding="utf-8")

    info = _cleanenv_cli(tmp_path, str(ep), "/voice-info", path="/usr/bin:/bin")
    assert info.returncode == 0, info.stderr
    assert '"wav_exists"' in info.stdout

    sealed = _cleanenv_cli(tmp_path, str(ep), "/seal-script", path="/usr/bin:/bin:/usr/sbin:/sbin")
    assert sealed.returncode == 0, sealed.stderr
    assert (ep / "02-diff.patch").exists()


# ---------------------------------------------------------------------------
# D48：批准 02.5 时自动封板（封板缺失或过期才封；零改动拒且不批准）
# ---------------------------------------------------------------------------


@pytest.fixture
def ep_025(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ep = tmp_path / "data" / "episodes" / "ep-025"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# 选题\n类型：杂谈\n", encoding="utf-8")
    (ep / "02-script.draft.md").write_text(DRAFT, encoding="utf-8")
    (ep / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return ep


def _pending_025(ep: Path) -> str:
    from pipeline import approvals

    item = approvals.ensure_pending(ep)
    assert item is not None and item.type == "02.5", item
    return item.approval_id


def _status_of(ep: Path, approval_id: str) -> str:
    from pipeline import approvals

    return str(approvals.get(approval_id, ep).status)


class TestD48ApproveSeals:
    def test_裸形态批准时自动封板(self, ep_025, capsys):
        aid = _pending_025(ep_025)
        assert not (ep_025 / "02-diff.patch").exists()
        assert run_cli(ep_025, "/approve", "02.5", "--id", aid) == 0
        expected = subprocess.run(
            ["git", "diff", "--no-index", "02-script.draft.md", "02-script.md"], cwd=ep_025, capture_output=True,
        ).stdout
        assert (ep_025 / "02-diff.patch").read_bytes() == expected
        assert "已封板" in capsys.readouterr().out
        assert _status_of(ep_025, aid).endswith("APPROVED")

    def test_REPL批准时自动封板(self, ep_025):
        aid = _pending_025(ep_025)
        assert cli._handle_repl_approve(ep_025, f"/approve 02.5 --id {aid}", {}) == 0
        assert (ep_025 / "02-diff.patch").stat().st_size > 0

    def test_零改动拒封且不批准(self, ep_025, capsys):
        (ep_025 / "02-script.draft.md").write_text(SCRIPT, encoding="utf-8")
        aid = _pending_025(ep_025)
        assert run_cli(ep_025, "/approve", "02.5", "--id", aid) == 1
        assert "未做任何修改" in capsys.readouterr().err
        assert not (ep_025 / "02-diff.patch").exists()
        assert not _status_of(ep_025, aid).endswith("APPROVED")

    def test_封板新鲜时不重封(self, ep_025):
        aid = _pending_025(ep_025)  # 先有待审项（封板后状态就越过 02.5 了）
        assert run_cli(ep_025, "/seal-script") == 0
        patch = ep_025 / "02-diff.patch"
        patch.write_text(patch.read_text(encoding="utf-8") + "# 人手工加的一行\n", encoding="utf-8")
        before = patch.read_bytes()
        assert run_cli(ep_025, "/approve", "02.5", "--id", aid) == 0
        assert patch.read_bytes() == before, "新鲜的封板不该被覆盖"
