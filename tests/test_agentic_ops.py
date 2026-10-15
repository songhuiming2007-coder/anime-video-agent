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


def test_ao3_still_微动推满全片():
    """D84：12 s（微单元上限）、缓推到末帧才到顶。

    旧写法 `min(zoom+0.0015,1.15)` 在第 100 帧就到顶，6 s（144 帧）里最后 44 帧不动
    （实测相邻帧差均值：第 100–109 帧 0.001，新写法首尾都 ≈0.8）。
    """
    import re
    assert A.STILL_SECONDS == 12.0
    frames = round(12.0 * 24000 / 1001)                 # 288（成片帧率 24000/1001）
    vf = A.still_argv(Path("in.png"), Path("out.mp4"))
    vf = vf[vf.index("-vf") + 1]
    m = re.search(r"zoompan=z='1\+([0-9.]+)\*on':d=(\d+):", vf)
    assert m, vf
    step, d = float(m.group(1)), int(m.group(2))
    assert d == frames
    assert abs(1 + step * (frames - 1) - A.STILL_ZOOM_END) < 1e-6    # 末帧刚好到顶
    assert 1 + step * (frames - 2) < A.STILL_ZOOM_END                 # 倒数第二帧还在推


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


class TestStillFit:
    """D89：非 16:9 的图（图表 / 方图 / 扁长图）完整放进安全框，补边色取四角（人选 C）。
    《束胸力学》1:1 的环向应力图、2.5:1 的肺容量图被「铺满 + 裁」切掉四边的标注。"""

    def test_宽高比分流(self):
        assert A.still_fills((1920, 1080)) and A.still_fills((1280, 720)) and A.still_fills(None)
        assert A.still_fills((1920, 1040))                       # 差 4%：仍算横屏截图
        assert not A.still_fills((2048, 2048)) and not A.still_fills((1940, 781))

    def test_图表走安全框补边_截图照旧铺满(self):
        vf = lambda size: (lambda a: a[a.index("-vf") + 1])(A.still_argv(Path("i.png"), Path("o.mp4"), size=size, pad="0x123456"))
        sq = vf((2048, 2048))
        assert f"scale={A.STILL_SAFE_W}:{A.STILL_SAFE_H}:force_original_aspect_ratio=decrease" in sq
        assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:0x123456" in sq and "crop" not in sq
        assert vf((1920, 1080)) == vf(None), "横屏截图与旧行为逐字节一致"
        # 安全框 × 推镜终点不超出画面：推到顶时内容仍完整
        assert A.STILL_SAFE_W * A.STILL_ZOOM_END <= 1920 and A.STILL_SAFE_H * A.STILL_ZOOM_END <= 1080

    def test_补边色取四角平均(self, tmp_path):
        from PIL import Image
        im = Image.new("RGB", (100, 60), (255, 255, 255))
        im.putpixel((2, 2), (0, 0, 0))                           # 一角黑，其余三角白 → 平均偏白
        im.save(tmp_path / "x.png")
        size, pad = A.still_look(tmp_path / "x.png")
        assert size == (100, 60) and pad == "0xbfbfbf"

    @needs_ffmpeg
    def test_真转码_方图推到顶时四边标注仍在(self, tmp_path):
        """白底 1:1 图画一圈红边：转出视频的末帧（推得最近）四边都还看得到红边。
        变异：图表也走「铺满 + 裁」→ 上下红边被裁掉，本条红。"""
        from PIL import Image, ImageDraw
        src = tmp_path / "sq.png"
        im = Image.new("RGB", (1000, 1000), (255, 255, 255))
        # 红框往里收 30 px：四角留白（真实图表的四角是底色，补边色就取它）
        ImageDraw.Draw(im).rectangle([30, 30, 969, 969], outline=(255, 0, 0), width=24)
        im.save(src)
        out = A.still_to_clip(src, tmp_path / "sq.mp4")
        last = tmp_path / "last.png"
        subprocess.run(["ffmpeg", "-v", "error", "-sseof", "-0.2", "-i", str(out), "-frames:v", "1",
                        "-update", "1", str(last)], check=True)
        f = Image.open(last).convert("RGB")
        red = lambda x, y: (lambda p: p[0] > 180 and p[1] < 90 and p[2] < 90)(f.getpixel((x, y)))
        rows = [y for y in range(1080) if red(960, y)]
        cols = [x for x in range(1920) if red(x, 540)]
        assert rows and rows[0] > 0 and rows[-1] < 1079, "上下红边都在画面内"
        assert cols and min(cols) > 0 and max(cols) < 1919, "左右红边都在画面内"
        assert not red(5, 540) and f.getpixel((5, 540))[0] > 200, "补边是白色（取自四角）"


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
    assert db["POOL"]["SP01"]["still"] is True  # D84：排片的微动物证豁免认这个标
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


def test_ao2_gate_register_forget免卡(repo: Path):
    """ADR-0030 决策 2：素材库登记 / 台账移走免卡；带 --waive 的照旧弹卡（见下一条）。"""
    ep = _episode(repo)
    assert _review("acquire gate data/library/incoming/x.mp4", ep).action == "allow"
    assert _review("acquire register data/library/incoming/x.mp4 --pool P", ep).action == "allow"
    assert _review("acquire forget 2", ep).action == "allow"


@pytest.mark.parametrize("flag", ['--waive "人看过"', "--waive=人看过", '--wai "人看过"'])
def test_ao2_waive卡上标门禁豁免(repo: Path, flag: str):
    """门禁豁免 = 越过人工关卡：ADR-0030 决策 2 逐条弹卡，卡上标明。"""
    v = _review(f"acquire register x.mp4 --pool P {flag}", _episode(repo))
    assert v.action == "ask" and "[门禁豁免]" in v.request.card_text
    assert "写素材库 data/library/" in v.request.card_text


def test_ao2_to_patch卡面写挪进补丁池(repo: Path):
    """--to-patch 本身免卡（ADR-0030）；卡面渲染仍对（带 --waive 时会出卡）。"""
    from pipeline.agent.status_card import render_approval_card

    ep = _episode(repo)
    v = _review(f"acquire register data/library/incoming/x.mp4 --to-patch {ep}", ep)
    assert v.action == "allow"
    ok, _msg, argv = validate_pipeline_command(f"acquire register data/library/incoming/x.mp4 --to-patch {ep}", ep_dir=ep)
    card = render_approval_card("run_pipeline", {"command": "acquire register x --to-patch"}, argv=argv)
    assert ok and "patch_assets/" in card


# ---------- 批 2：Phase 0 资产链 ----------


@pytest.mark.parametrize("command", [
    "ingest probe a.mkv", "ingest intact a.mkv b.mkv", "ingest verify a.mkv a.ass",
    "vindex status --anime 东京喰种", "vindex search 雨中 --anime 东京喰种", "subindex search 便当",
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
def test_ao2_phase0写盘子命令免卡(repo: Path, command: str):
    """ADR-0030 决策 2：Phase 0 写素材库的子命令免卡（产物即状态、可重跑）。"""
    v = _review(command, _episode(repo))
    assert v.action == "allow", v


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


# ---------- 批 3：期内补料 ----------


def test_ao6_reset_旧补丁池进attic_逐字节在(repo: Path):
    from pipeline import ingest_patch

    ep = _episode(repo)
    (ep / "04-patch" / "shots").mkdir(parents=True)
    (ep / "04-patch" / "pool.json").write_text('{"pool": "p"}', encoding="utf-8")
    (ep / "04-patch" / "shots" / "p_SP01.json").write_bytes(b"\x00table")
    dest = ingest_patch.reset_pool(ep)
    assert not (ep / "04-patch").exists()
    assert dest.parent == ep / "04-patch.attic"
    assert (dest / "pool.json").read_text(encoding="utf-8") == '{"pool": "p"}'
    assert (dest / "shots" / "p_SP01.json").read_bytes() == b"\x00table"


def test_ao6_reset_没有补丁池什么也不做(repo: Path):
    from pipeline import ingest_patch

    assert ingest_patch.reset_pool(_episode(repo)) is None


@needs_ffmpeg
def test_ao3_补丁池图片自动转码_幂等(repo: Path):
    from pipeline import ingest_patch

    ep = _episode(repo)
    (ep / "patch_assets").mkdir()
    _png(ep / "patch_assets" / "物证.png", 1800, 1800)
    made = ingest_patch.convert_stills(ep)
    assert [f.name for f in made] == ["物证-still.mp4"]
    assert ingest_patch.convert_stills(ep) == []  # 重跑不重转
    assert [f.name for f in ingest_patch.pending_assets(ep)] == ["物证-still.mp4"]


@pytest.mark.parametrize("module", ["scout", "ingest_patch"])
def test_ao9_自动补期目录(repo: Path, module: str):
    ep = _episode(repo)
    ok, msg, argv = validate_pipeline_command(module, ep_dir=ep)
    assert ok, msg
    assert argv[3] == str(ep.resolve())


def test_ao9_scout_type值不被当成期目录(repo: Path):
    ep = _episode(repo)
    ok, msg, argv = validate_pipeline_command("scout --type patch", ep_dir=ep)
    assert ok, msg
    assert argv[3:] == [str(ep.resolve()), "--type", "patch"]


def test_ao2_补料命令免卡(repo: Path):
    ep = _episode(repo)
    assert _review("ingest_patch --reset", ep).action == "allow"
    assert _review("scout", ep).action == "allow"


@needs_ffmpeg
def test_ao3_ingest_patch入口先转码再扫描(repo: Path):
    """经 ingest 主入口：图片先被转成 -still.mp4，再进候选（借 99 上限报错观察候选名，不跑云端）。"""
    from pipeline import ingest_patch

    ep = _episode(repo)
    (ep / "04-patch").mkdir()
    assets = {f"SP{i:02d}": {"path": f"/tmp/{i}.mp4", "duration": 1.0} for i in range(1, 100)}
    (ep / "04-patch" / "pool.json").write_text(json.dumps({"pool": "p", "assets": assets}), encoding="utf-8")
    (ep / "patch_assets").mkdir()
    _png(ep / "patch_assets" / "scan.png", 1800, 1800)
    with pytest.raises(SystemExit, match=r"尝试添加 scan-still\.mp4"):
        ingest_patch.ingest(ep, floor=0.60, local=True)


# ---------- 批 4：运维 ----------


def _lock(ep: Path, content: str) -> Path:
    f = ep / "03-audio" / ".apply_patch.lock"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return f


def test_ao7_持锁进程活着不清(repo: Path):
    import os

    from pipeline import corrections

    lock = _lock(_episode(repo), str(os.getpid()))  # 本进程必然活着
    with pytest.raises(SystemExit, match="还在运行"):
        corrections.clear_stale_apply_lock(lock.parent.parent)
    assert lock.exists()


def test_ao7_持锁进程已死才清(repo: Path):
    from pipeline import corrections

    dead = subprocess.Popen(["true"])  # 跑完即退并回收：拿一个确定已不存在的 pid
    dead.wait()
    lock = _lock(_episode(repo), str(dead.pid))
    assert corrections.clear_stale_apply_lock(lock.parent.parent) == 0
    assert not lock.exists()


def test_ao7_没锁报无锁_内容不是pid拒(repo: Path, capsys):
    from pipeline import corrections

    ep = _episode(repo)
    assert corrections.clear_stale_apply_lock(ep) == 0
    assert "没有残留锁" in capsys.readouterr().out
    lock = _lock(ep, "garbage")
    with pytest.raises(SystemExit, match="不是 pid"):
        corrections.clear_stale_apply_lock(ep)
    assert lock.exists()


def test_ao7_清锁命令自动补期目录_免卡(repo: Path):
    ep = _episode(repo)
    ok, msg, argv = validate_pipeline_command("tts --clear-stale-lock", ep_dir=ep)
    assert ok, msg
    assert argv[3:] == [str(ep.resolve()), "--clear-stale-lock"]
    assert _review("tts --clear-stale-lock", ep).action == "allow"  # ADR-0030：清锁前已核实持锁进程已死


def test_ao8_改道脚本_先判软链再动_幂等():
    from pipeline import cloud

    s = cloud.relocate_data_script("/root/anime-video-agent")
    assert s.index("[ -L /root/anime-video-agent/data ]") < s.index("mv ")
    assert "exit 0" in s.split("mv ")[0]  # 已是软链：在任何写命令之前退出
    assert "CONFLICT" in s and "ln -s /root/autodl-tmp/data /root/anime-video-agent/data" in s


@pytest.mark.parametrize("sub,script_marker", [("relocate-data", "ln -s"), ("clean-frames", "*_cap")])
def test_ao8_远端维护_有任务在跑就拒_否则发脚本(monkeypatch, sub: str, script_marker: str):
    import argparse

    from pipeline import cloud

    sent: list[str] = []

    class _Res:
        returncode, stdout, stderr = 0, "MOVED", ""

    monkeypatch.setattr(cloud, "load_cloud_config", lambda: ({"remote_root": "/root/r"}, {}))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    monkeypatch.setattr(cloud, "_ssh", lambda cmd, **kw: (sent.append(cmd), _Res())[1])
    fn = cloud.cmd_relocate_data if sub == "relocate-data" else cloud.cmd_clean_frames

    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host: ["ava-tts"])
    with pytest.raises(SystemExit, match="还有任务在跑"):
        fn(argparse.Namespace())
    assert sent == []

    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host: [])
    assert fn(argparse.Namespace()) == 0
    assert len(sent) == 1 and script_marker in sent[0]


def test_ao8_远端维护命令放行且弹卡(repo: Path):
    ep = _episode(repo)
    for sub in ("relocate-data", "clean-frames"):
        v = _review(f"cloud {sub}", ep)
        assert v.action == "ask" and "云端" in v.request.card_text
    assert not validate_pipeline_command("cloud exec --fg ls")[0]


def test_ao8_fix_env命令_模型只来自配置清单():
    from pipeline import cloud

    cmds = cloud.fix_env_commands(remote_root="/root/r", models_root="/root/m", repo_missing=True,
                                  bad_models=[("BAAI/bge-m3", "bge-m3")])
    assert cmds[0].endswith("git clone " + cloud.REMOTE_REPO_URL + " /root/r")
    assert cmds[1].endswith("""-c 'from huggingface_hub import snapshot_download; """
                            """snapshot_download("BAAI/bge-m3", local_dir="/root/m/bge-m3")'""")
    assert cloud.fix_env_commands(remote_root="/r", models_root="/m", repo_missing=False, bad_models=[]) == []


def test_ao8_fix_env_只重下缺的(monkeypatch):
    import argparse

    from pipeline import cloud

    sent: list[str] = []

    class _Res:
        def __init__(self, rc=0, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_ssh(cmd, **kw):
        sent.append(cmd)
        if cmd.startswith("test -d"):
            return _Res(0)                       # 仓库在
        if cmd.startswith("du -s") and "good" in cmd:
            return _Res(0, "9")
        if cmd.startswith("du -s"):
            return _Res(0, "")                   # 缺失
        return _Res(0, "OK")

    cfg = {"remote_root": "/root/r", "remote_models": "/root/m",
           "models": {"org/good": {"dir": "good", "min_gb": 1}, "org/miss": {"dir": "miss", "min_gb": 1}}}
    monkeypatch.setattr(cloud, "load_cloud_config", lambda: (cfg, {}))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host: [])
    monkeypatch.setattr(cloud, "_ssh", fake_ssh)
    monkeypatch.setattr(cloud, "parse_model_structure_result", lambda out, rc: ("OK", ""))
    assert cloud.cmd_fix_env(argparse.Namespace()) == 0
    downloads = [c for c in sent if "snapshot_download" in c]
    assert len(downloads) == 1 and "org/miss" in downloads[0]
    assert not any("git clone" in c for c in sent)


def test_d102_cloud_connect逐条弹卡不给本会话批准(repo: Path):
    """D102：connect 写 cloud.local.json 的连接三项（Code Freeze 第 ④ 条例外），与另三条同一口径：
    每次弹卡，卡上不开放「本会话都批准」（allow_key 为 None）；对照 cloud up 是可「本会话都批准」的计费卡。"""
    ep = _episode(repo)
    v = _review('cloud connect "ssh -p 34557 root@connect.weste.seetacloud.com"', ep)
    assert v.action == "ask" and v.allow_key is None
    up = _review("cloud up --mode gpu", ep)
    assert up.action == "ask" and up.allow_key == "run_pipeline:cloud up"
