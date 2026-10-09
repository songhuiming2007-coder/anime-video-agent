"""D59：「人在终端跑」收进 agent（spec `docs/dev/plans/2026-10-09-agentic-ops-spec.md`，AO-*）。

全部用临时夹具：`paths.ROOT` / `paths.DATA` / `ingest.SOURCES` 指向 tmp，不碰外置盘上的真实 data/。
需要真 ffmpeg 的用例（图片转码、完整性校验）现场造几秒的小文件，本机没有 ffmpeg 就跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import acquire as A
from pipeline import ingest, paths
from pipeline.agent.session import review_tool_call
from pipeline.agent.tools import validate_pipeline_command

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="本机没有 ffmpeg / ffprobe")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    data = root / "data"
    for d in ("episodes", "library/incoming", "library/raw"):
        (data / d).mkdir(parents=True)
    (data / "library" / "incoming" / "fetched.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.setattr(paths, "DATA", data)
    monkeypatch.setattr(ingest, "SOURCES", data / "library" / "sources.json")
    monkeypatch.chdir(tmp_path)  # cmd_register 会 chdir 到 ROOT；用例结束由 monkeypatch 复原
    return root


def _episode(root: Path, name: str = "01-smoke") -> Path:
    ep = root / "data" / "episodes" / name
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    return ep


def _png(path: Path, w: int, h: int) -> Path:
    from PIL import Image

    Image.new("RGB", (w, h), (180, 40, 40)).save(path)
    return path


def _mp4(path: Path, w: int = 1280, h: int = 720, seconds: int = 2) -> Path:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={w}x{h}:rate=24000/1001:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _ledger(root: Path) -> list[dict]:
    return json.loads((root / "data/library/incoming/fetched.json").read_text(encoding="utf-8"))


def _set_ledger(root: Path, entries: list[dict]) -> None:
    (root / "data/library/incoming/fetched.json").write_text(json.dumps(entries, ensure_ascii=False),
                                                              encoding="utf-8")


# ---------- AO-4 自动取号（纯函数） ----------


class TestNextKey:
    def test_取已登记与文件名前缀的最大号加一(self):
        assert A.next_key({"SP01", "SP03"}, ["SP05-x.mp4", "foo.mp4"]) == 6
        assert A.next_key({"SP07"}, []) == 8

    def test_空池从一起(self):
        assert A.next_key(set(), []) == 1

    def test_三位数前缀不算(self):
        assert A.next_key(set(), ["SP100-x.mp4"]) == 1

    def test_到九十九报错不扩位(self):
        with pytest.raises(SystemExit, match="SP99"):
            A.next_key({"SP99"}, [])


# ---------- AO-3 图片转码 ----------


def test_ao3_still_argv_四样锁死():
    argv = A.still_argv(Path("in.png"), Path("out.mp4"))
    assert argv[0] == "ffmpeg" and argv[-1] == "out.mp4"
    assert "-an" in argv
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert argv[argv.index("-r") + 1] == A.STILL_FPS
    assert "crop=1920:1080" in argv[argv.index("-vf") + 1]
    assert "-n" in argv  # 不覆盖已有文件


@needs_ffmpeg
def test_ao3_still_to_clip_真转码(tmp_path: Path):
    out = A.still_to_clip(_png(tmp_path / "a.png", 2400, 1700), tmp_path / "a.mp4")
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(out)],
        capture_output=True, text=True, check=True).stdout)
    streams = probe["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]  # 无音轨
    v = streams[0]
    assert (v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == (1920, 1080, "yuv420p", A.STILL_FPS)
    assert abs(float(probe["format"]["duration"]) - A.STILL_SECONDS) < 0.1


# ---------- register：自动取号 / 扫图 / 豁免 ----------


@needs_ffmpeg
def test_ao4_扫图登记_自动取号_转码_原图保留(repo: Path):
    inc = repo / "data/library/incoming"
    img = _png(inc / "booklet.png", 2400, 1700)
    _set_ledger(repo, [{"url": "https://e.x/b.png", "title": "场刊扫图", "type": "scan",
                        "candidate": 1, "file": str(img), "as": None}])
    assert A.cmd_register(img, pool="POOL") == 0
    made = sorted((repo / "data/library/raw/POOL").iterdir())
    assert [p.name.split("-", 1)[0] for p in made] == ["SP01"] and made[0].suffix == ".mp4"
    db = json.loads((repo / "data/library/sources.json").read_text(encoding="utf-8"))
    assert list(db["POOL"]) == ["SP01"]
    assert img.exists()  # 原图保留
    led = _ledger(repo)[0]
    assert led["as"] == "SP01" and led["still_from"] == "booklet.png"


@needs_ffmpeg
def test_ao4_已有号往后接(repo: Path):
    (repo / "data/library/sources.json").write_text(
        json.dumps({"POOL": {"SP02": {"path": "x"}}}), encoding="utf-8")
    clip = _mp4(repo / "data/library/incoming/live.mp4")
    assert A.cmd_register(clip, pool="POOL") == 0
    assert "SP03" in json.loads((repo / "data/library/sources.json").read_text(encoding="utf-8"))["POOL"]


@needs_ffmpeg
def test_ao4_门禁不过拒_给理由才登记(repo: Path, capsys):
    inc = repo / "data/library/incoming"
    img = _png(inc / "small.png", 1500, 1500)  # 短边 < 1600
    _set_ledger(repo, [{"url": "u", "title": "小图", "type": "scan", "candidate": 1,
                        "file": str(img), "as": None}])
    with pytest.raises(SystemExit, match="--waive"):
        A.cmd_register(img, pool="POOL")
    assert not (repo / "data/library/sources.json").exists()
    assert A.cmd_register(img, pool="POOL", waive="唯一一张实物照，人看过可用") == 0
    assert "[门禁豁免] 理由：唯一一张实物照" in capsys.readouterr().out
    assert _ledger(repo)[0]["waived"] == "唯一一张实物照，人看过可用"


# ---------- AO-5 --to-patch ----------


def test_ao5_挪进本期补丁池_不登记(repo: Path):
    ep = _episode(repo)
    f = repo / "data/library/incoming/clip.mp4"
    f.write_bytes(b"\0" * 10)
    _set_ledger(repo, [{"url": "u", "candidate": 1, "file": str(f), "as": None}])
    assert A.cmd_to_patch(f, ep) == 0
    assert (ep / "patch_assets/clip.mp4").read_bytes() == b"\0" * 10 and not f.exists()
    assert not (repo / "data/library/sources.json").exists()
    assert _ledger(repo)[0]["to_patch"] == ep.name


def test_ao5_只收incoming里的文件_只认期目录(repo: Path):
    ep = _episode(repo)
    outside = repo / "data/library/raw/x.mp4"
    outside.write_bytes(b"\0")
    with pytest.raises(SystemExit, match="incoming"):
        A.cmd_to_patch(outside, ep)
    f = repo / "data/library/incoming/clip.mp4"
    f.write_bytes(b"\0")
    with pytest.raises(SystemExit, match="期目录"):
        A.cmd_to_patch(f, repo / "data/library")
    assert f.exists() and outside.exists()


# ---------- forget ----------


def test_forget_台账移走_文件进attic_留痕(repo: Path):
    inc = repo / "data/library/incoming"
    f = inc / "dl.mp4"
    f.write_bytes(b"abc")
    _set_ledger(repo, [{"url": "u1", "candidate": 1, "file": str(f), "as": None},
                       {"url": "u2", "candidate": 2, "file": "x", "as": "SP04"}])
    assert A.cmd_forget(1) == 0
    assert [e["candidate"] for e in _ledger(repo)] == [2]
    moved = list((inc / "attic").rglob("dl.mp4"))
    assert len(moved) == 1 and moved[0].read_bytes() == b"abc"
    log = json.loads((inc / "forgotten.json").read_text(encoding="utf-8"))
    assert log[0]["url"] == "u1" and log[0]["moved_to"] == str(moved[0])


def test_forget_已登记的拒(repo: Path):
    _set_ledger(repo, [{"url": "u2", "candidate": 2, "file": "x", "as": "SP04"}])
    with pytest.raises(SystemExit, match="SP04"):
        A.cmd_forget(2)
    assert len(_ledger(repo)) == 1


# ---------- AO-1 / AO-2 白名单与审卡 ----------


@pytest.mark.parametrize("command", [
    "acquire gate data/library/incoming/x.mp4",
    "acquire register data/library/incoming/x.mp4 --pool EGOIST",
    "acquire register x.png --to-patch data/episodes/01",
    "acquire forget 3",
])
def test_ao1_acquire新子命令放行(command: str):
    ok, msg, _argv = validate_pipeline_command(command)
    assert ok, msg


def test_ao1b_register_force照拒():
    ok, msg, _ = validate_pipeline_command("acquire register x.mp4 --pool P --force")
    assert not ok and "--force" in msg


def _review(command: str, ep: Path):
    return review_tool_call("run_pipeline", {"command": command}, ep_dir=ep, scope="asset", root=None)


def test_ao2_gate免卡_register与forget弹卡(repo: Path):
    ep = _episode(repo)
    assert _review("acquire gate data/library/incoming/x.mp4", ep).action == "allow"
    v = _review("acquire register data/library/incoming/x.mp4 --pool P", ep)
    assert v.action == "ask" and "写素材库 data/library/" in v.request.card_text
    assert "[门禁豁免]" not in v.request.card_text
    assert _review("acquire forget 2", ep).action == "ask"


@pytest.mark.parametrize("flag", ['--waive "人看过"', "--waive=人看过", '--wai "人看过"'])
def test_ao2_waive卡上标门禁豁免(repo: Path, flag: str):
    v = _review(f"acquire register x.mp4 --pool P {flag}", _episode(repo))
    assert v.action == "ask" and "[门禁豁免]" in v.request.card_text


def test_ao2_to_patch卡上写挪进补丁池(repo: Path):
    ep = _episode(repo)
    v = _review(f"acquire register data/library/incoming/x.mp4 --to-patch {ep}", ep)
    assert v.action == "ask" and "patch_assets/" in v.request.card_text


# ---------- 批 2：Phase 0 资产链 ----------


@pytest.mark.parametrize("command", [
    "ingest probe a.mkv", "ingest intact a.mkv b.mkv", "ingest verify a.mkv a.ass",
    "vindex status 东京喰种", "vindex search 雨中 --anime 东京喰种", "subindex search 便当",
])
def test_ao2_phase0只读子命令免卡(repo: Path, command: str):
    assert _review(command, _episode(repo)).action == "allow"


@pytest.mark.parametrize("command", [
    "ingest subs a.mkv -o a.srt", "ingest run a.mkv --anime X --episode 1",
    "ingest sources a.mkv --anime X --season 1 --episode 1",
    "shots calibrate a.mkv", "shots calibrate a.mkv --sheet 10", "shots rebuild --anime X --episode S01E01",
    "shots gallery X S01E01", "vindex presence X S01E01", "subindex build a.srt --anime X --episode 1",
    "vprobe tagger X", "timeline 东京喰种",
])
def test_ao2_phase0写盘子命令弹卡(repo: Path, command: str):
    v = _review(command, _episode(repo))
    assert v.action == "ask", v
    if not command.startswith(("vprobe", "timeline")):
        assert "写素材库 data/library/" in v.request.card_text


@pytest.mark.parametrize("command", ["vindex scene X S01E01", "ingest phase1 a.mkv", "eval report x"])
def test_ao1b_白名单外照拒(command: str):
    ok, _msg, _ = validate_pipeline_command(command)
    assert not ok


@needs_ffmpeg
def test_ao_patch_gallery_代表帧单抽_画廊落盘(repo: Path):
    from pipeline import shots

    ep = _episode(repo)
    patch = ep / "04-patch"
    (patch / "shots").mkdir(parents=True)
    video = _mp4(ep / "clip.mp4", 640, 360, seconds=3)
    (patch / "pool.json").write_text(json.dumps({"pool": "01-smoke-patch", "assets": {}}), encoding="utf-8")
    table = {"meta": {"scene_threshold": 12.0, "min_shot": 0.5, "fps": "24000/1001", "duration": 3.0,
                      "source": str(video)},
             "shots": [{"i": 0, "start": 0.0, "end": 1.5, "rep": 0.75},
                       {"i": 1, "start": 1.5, "end": 3.0, "rep": 2.25}]}
    (patch / "shots" / "01-smoke-patch_SP01.json").write_text(json.dumps(table), encoding="utf-8")
    # 打标帧目录里张数与镜头数不等（长镜头三帧），画廊不许拿它
    (patch / "frames" / "01-smoke-patch_SP01").mkdir(parents=True)
    out = shots.patch_gallery(ep, "SP01")
    assert out == patch / "shots" / "01-smoke-patch_SP01_gallery.html"
    assert len(list((patch / "rep-frames" / "01-smoke-patch_SP01").glob("*.jpg"))) == 2
    assert "锚点: 01-smoke-patch SP01 00:00.00" in out.read_text(encoding="utf-8")


def test_ao_patch_gallery_没有补丁池报错(repo: Path):
    from pipeline import shots

    with pytest.raises(SystemExit, match="ingest_patch"):
        shots.patch_gallery(_episode(repo), "SP01")


@needs_ffmpeg
def test_ao_frames_残留帧自己清掉(repo: Path):
    from pipeline import shots

    ep = _episode(repo)
    sd, fd = repo / "s", repo / "f"
    sd.mkdir()
    video = _mp4(ep / "clip.mp4", 640, 360, seconds=2)
    table = {"meta": {"scene_threshold": 12.0, "min_shot": 0.5, "fps": "24000/1001", "duration": 2.0,
                      "source": str(video)},
             "shots": [{"i": 0, "start": 0.0, "end": 2.0, "rep": 1.0}]}
    (sd / "P_SP01.json").write_text(json.dumps(table), encoding="utf-8")
    (fd / "P_SP01").mkdir(parents=True)
    (fd / "P_SP01" / "00009.jpg").write_bytes(b"old")  # 上一次跑剩下的
    out = shots.frames("P", "SP01", out_dir=sd, dest_dir=fd, check_config=False)
    assert [f.name for f in out.glob("*.jpg")] == ["00001.jpg"]
