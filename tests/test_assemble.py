"""D81 PR-5（D）：`pipeline.assemble` 企划总装。

临时仓库根（paths.ROOT 指过去），夹具企划在 tmp_path 下，不碰真实 data/。ffmpeg 是真的（AGENTS.md：不 mock
ffmpeg），小样片由 `ffmpeg -f lavfi` 现造；需要制造故障的用例给 runner 套一层记录 / 改一处的外壳，
其余调用原样交给真实 ffmpeg。qc 在外壳里改成进程内调 `qc.check`（qc 子进程要真实 data/）。
"""

from __future__ import annotations

import ast
import fcntl
import json
import os
import subprocess
from pathlib import Path

import pytest

from pipeline import assemble as asm
from pipeline import paths

EGOIST_PROBE = [
    {"codec_name": "h264", "profile": "High", "codec_type": "video", "width": 1920, "height": 1080,
     "pix_fmt": "yuv420p", "level": 40, "r_frame_rate": "24000/1001"},
    {"codec_name": "aac", "profile": "LC", "codec_type": "audio", "sample_rate": "48000", "channels": 1,
     "channel_layout": "mono", "r_frame_rate": "0/0"},
]

# 一段旁白（1 s）+ 其后 3 s 前景试听：成片时间轴总长 4 s、旁白终点 1 s（期望值先在 music.build_timeline 上跑过）
SHORT_SCRIPT = ("## 段落 1\n\n配音：第一段。\n\n## 音乐段 M1\n\n音乐: `短曲` 完整版 00:00-00:03\n"
                "状态: 前景试听，旁白停止\n过渡: 结束后自然进入下一首\n")
SHORT_MANIFEST = {"segments": [{"index": 1, "duration": 1.0}]}

# tests/test_music.py 的三段稿：旁白终点 128 s、成片 268 s
LONG_SCRIPT = """## 音乐段 M1

音乐: `测试曲甲` 完整版 00:00-00:15
状态: 前景试听，旁白停止
过渡: 试听结束后自然降为 BGM，进入段落 1

## 段落 1

配音：第一段。

## 音乐段 M2

音乐: `测试曲甲` 完整版 04:23-04:46
状态: 前景试听，旁白停止
过渡: 结尾自然淡出

## 段落 2

配音：第二段。

## 音乐段 M3

音乐: `测试曲乙` 完整版 00:00-01:00
状态: 前景试听，旁白停止
过渡: 结束后自然进入下一首，不加解释性旁白

## 段落 3

配音：第三段。

音乐: `测试曲乙` 继续播放至完整版结束
状态: 旁白结束后不再说话，不淡出，让歌曲自然结束
"""
LONG_MANIFEST = {"segments": [{"index": i, "duration": 10.0} for i in (1, 2, 3)]}


def table(rows: list[tuple[str, str]], collection: str | None = "00-合集") -> str:
    head = "企划: 测试编年史\n" + (f"合集: {collection}\n" if collection else "")
    body = "".join(f"| {i} | {d} | 2011 | 1-2分钟 | {t} |\n" for i, (d, t) in enumerate(rows, 1))
    return head + "\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n" + body


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


_CACHE: dict[tuple, bytes] = {}


def make_video(dest: Path, seconds: float, channels: int = 1) -> None:
    key = (seconds, channels)
    if key not in _CACHE:
        tmp = dest.with_name(".gen.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=24000/1001",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds),
               *asm.TRIM_VIDEO_ARGS, "-pix_fmt", "yuv420p", "-color_range", "tv",
               "-c:a", "aac", "-b:a", "192k", "-ac", str(channels), "-ar", "48000", "-movflags", "+faststart",
               "-shortest", str(tmp))
        _CACHE[key] = tmp.read_bytes()
        tmp.unlink()
    dest.write_bytes(_CACHE[key])


def make_cover(dest: Path) -> None:
    if "cover" not in _CACHE:
        tmp = dest.with_name(".cover.jpg")
        ffmpeg("-f", "lavfi", "-i", "color=c=red:s=320x180", "-frames:v", "1", str(tmp))
        _CACHE["cover"] = tmp.read_bytes()  # type: ignore[index]
        tmp.unlink()
    dest.write_bytes(_CACHE["cover"])  # type: ignore[index]


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    (r / "data" / "episodes").mkdir(parents=True)
    (r / "config").mkdir()
    (r / "data" / "library" / "bgm").mkdir(parents=True)
    for n in ("s.flac", "x.flac", "y.flac"):
        (r / "data" / "library" / "bgm" / n).write_bytes(b"")
    (r / "config" / "bgm.json").write_text(json.dumps({"测试番": {"tracks": {
        "短曲": {"path": "data/library/bgm/s.flac", "dur": 10.0, "lufs": -10.0},
        "测试曲甲": {"path": "data/library/bgm/x.flac", "dur": 300.0, "lufs": -10.0},
        "测试曲乙": {"path": "data/library/bgm/y.flac", "dur": 200.0, "lufs": -8.0},
    }}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "ROOT", r)
    monkeypatch.setattr(paths, "DATA", r / "data")
    monkeypatch.setattr(paths, "CONFIG", r / "config")
    return r


def make_volume(p: Path, name: str, *, seconds: float = 4.0, channels: int = 1, script: str = SHORT_SCRIPT,
                manifest: dict = SHORT_MANIFEST) -> Path:
    v = p / name
    (v / "03-audio").mkdir(parents=True)
    (v / "01-topic.md").write_text("# 选题\n番: 测试番\n", encoding="utf-8")
    (v / "02-script.md").write_text(script, encoding="utf-8")
    (v / "03-audio" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    make_video(v / "05-final.mp4", seconds, channels)
    make_cover(v / "08-cover.jpg")
    return v


def make_project(root: Path, *, name: str = "P", vol2: str = "02-b", channels: int = 1) -> Path:
    """两卷 + 合集：01-a 余韵1秒（4 s 片，截到 2.00 s），第 2 卷完整保留（3 s 片）。"""
    p = root / "data" / "episodes" / name
    make_volume(p, "01-a", channels=channels)
    make_volume(p, vol2, seconds=3.0, channels=channels)
    c = p / "00-合集"
    c.mkdir()
    (c / "01-topic.md").write_text("# 选题\n时长目标: 1-2分钟\n", encoding="utf-8")
    (p / "00-project.md").write_text(table([("01-a", "余韵1秒"), (vol2, "完整保留")]), encoding="utf-8")
    return p


class Rec:
    """记录每次调用，其余原样交给真实 ffmpeg；`hook(argv)` 返回非 None 时用它代替真实调用。"""

    def __init__(self, hook=None) -> None:
        self.calls: list[list[str]] = []
        self.hook = hook
        self.qc_checks: list = []

    def __call__(self, argv: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
        argv = list(argv)
        self.calls.append(argv)
        if "pipeline.qc" in argv:
            from pipeline import qc

            self.qc_checks = qc.check(Path(argv[-1]) / "05-final.mp4")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if self.hook is not None:
            out = self.hook(argv)
            if out is not None:
                return out
        return asm.default_runner(argv, capture=capture)

    def ffmpeg_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "ffmpeg"]


def tree(d: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(d)): (p.lstat().st_size, p.lstat().st_mtime_ns) for p in d.rglob("*")}


def run_main(argv: list[str], rec: Rec) -> int:
    return asm.main(argv, runner=rec)


# ---------------------------------------------------------------- 黄金测试：与 assemble.sh 逐 token 相同


def test_黄金_三条ffmpeg命令与assemble_sh逐token相同() -> None:
    """期望 argv 由 assemble.sh 的 gen_card / trim_tail 原文把 ffmpeg 换成打印桩跑出（施工记录有据），路径换成占位。"""
    b = asm.base_from(EGOIST_PROBE)
    assert asm.card_argv(Path("COVER"), Path("CARDOUT"), b) == [
        "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "3", "-i", "COVER", "-f", "lavfi", "-t", "3",
        "-i", "anullsrc=r=48000:cl=mono", "-map", "0:v", "-map", "1:a", "-vf",
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=24000/1001,format=yuv420p,"
        "fade=t=in:st=0:d=0.5,fade=t=out:st=2.50:d=0.5",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "192k", "-ac", "1", "-ar", "48000", "-shortest", "CARDOUT"]
    for cut, st in (("889.53", "888.53"), ("866.74", "865.74")):
        assert asm.trim_argv(Path("SRC"), Path("TRIMOUT"), cut, b) == [
            "ffmpeg", "-y", "-loglevel", "error", "-ss", "0", "-to", cut, "-i", "SRC",
            "-vf", f"fade=t=out:st={st}:d=1.0", "-af", f"afade=t=out:st={st}:d=1.0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-r", "24000/1001", "-c:a", "aac", "-b:a", "192k", "-ac", "1", "-ar", "48000", "TRIMOUT"]
    assert asm.concat_argv(Path("LIST"), Path("OUT")) == [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "LIST", "-c", "copy", "OUT"]


def test_末卷完整保留_部件用原成片() -> None:
    from pipeline.project import ParseError, parse

    with pytest.raises(ParseError, match="末行"):
        parse(table([("01-a", "余韵18秒"), ("02-b", "余韵18秒")]))


# ---------------------------------------------------------------- 截断点 / 流参数


def test_截断点_旁白终点加余韵_不看时长预算(root: Path) -> None:
    p = root / "data" / "episodes" / "P"
    make_volume(p, "01-a", script=LONG_SCRIPT, manifest=LONG_MANIFEST)
    make_volume(p, "02-b", seconds=3.0)
    (p / "00-project.md").write_text(table([("01-a", "余韵18秒"), ("02-b", "完整保留")], None), encoding="utf-8")
    out = root / "data" / "episodes" / "out"
    out.mkdir()
    (out / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    pl = asm.plan(p, out, False)
    # build_timeline：M1 15 + 段1 10 + M2 23 + 段2 10 + M3 60 + 段3 10 → 旁白终点 128；+ 18 = 146（时长预算写的是 1-2 分钟）
    assert [(x.name, x.vo_end, x.cut) for x in pl.parts] == [("01-a", 128.0, "146.00"), ("02-b", None, None)]
    assert "part1_trimmed ← 01-a/05-final.mp4 截到 146.00s（旁白终点 128.00s + 余韵 18 秒，末 1s 淡出）" in "\n".join(asm.preview_lines(pl))


def test_截断点超过成片总长退2(root: Path, capsys: pytest.CaptureFixture) -> None:
    p = make_project(root)
    (p / "01-a" / "02-script.md").write_text("## 段落 1\n\n配音：只有一段。\n", encoding="utf-8")
    rec = Rec()
    assert run_main([str(p)], rec) == 2
    assert "超过成片总长" in capsys.readouterr().err and rec.ffmpeg_calls() == []


def test_流参数跟随基准_立体声企划(root: Path) -> None:
    p = make_project(root, channels=2)
    rec = Rec()
    assert run_main([str(p)], rec) == 0
    cards = [c for c in rec.ffmpeg_calls() if "-loop" in c]
    assert all("anullsrc=r=48000:cl=stereo" in c and c[c.index("-ac") + 1] == "2" for c in cards)
    sig = asm.signature(asm.probe_streams(p / "00-合集" / "05-final.mp4", asm.default_runner))
    assert [s[SIG_I["channels"]] for s in sig if s[0] == "audio"] == [2]


SIG_I = {k: i for i, k in enumerate(asm.SIG_FIELDS)}


# ---------------------------------------------------------------- 端到端小样


def test_端到端_账本连续_命名_qc读到账本(root: Path) -> None:
    p = make_project(root)
    rec = Rec()
    assert run_main([str(p)], rec) == 0
    out = p / "00-合集"
    led = json.loads((out / "04-assembly.json").read_text(encoding="utf-8"))
    assert [x["name"] for x in led["parts"]] == ["card_part1", "part1_trimmed", "card_part2", "02-b"]
    assert [x["kind"] for x in led["parts"]] == ["card", "part", "card", "part"]
    assert [x.get("src") for x in led["parts"]] == [None, str((p / "01-a").resolve()), None,
                                                   str((p / "02-b").resolve())]
    t = 0.0
    for x in led["parts"]:
        assert x["offset"] == pytest.approx(t, abs=1e-3)
        t += x["duration"]
    got = asm.probe_duration(out / "05-final.mp4", asm.default_runner)
    assert abs(got - t) < asm.DURATION_TOLERANCE_S and led["total_duration"] == got
    same = next(c for c in rec.qc_checks if c.name == "与排片一致")
    assert "总装账本 4 部件之和" in same.detail
    assert not (out / asm.TMP_FINAL).exists()


def test_concat清单转义_目录名带单引号(root: Path) -> None:
    p = make_project(root, vol2="02-it's")
    rec = Rec()
    assert run_main([str(p)], rec) == 0
    listing = (p / "00-合集" / "work" / "concat.txt").read_text(encoding="utf-8")
    assert "02-it'\\''s/05-final.mp4'" in listing
    led = json.loads((p / "00-合集" / "04-assembly.json").read_text(encoding="utf-8"))
    assert len(led["parts"]) == 4 and led["parts"][-1]["name"] == "02-it's"
    assert asm.concat_line(Path("/a/b'c")) == "file '/a/b'\\''c'\n"
    with pytest.raises(asm.AssembleError):
        asm.concat_line(Path("/a/b\nfile '/etc/passwd'"))


# ---------------------------------------------------------------- 前置检查：任何 ffmpeg 与文件创建之前


@pytest.mark.parametrize("bad", ["../x", "/abs", "a/b"])
def test_卷表目录越界_解析期拒(root: Path, bad: str, capsys: pytest.CaptureFixture) -> None:
    p = make_project(root)
    (p / "00-project.md").write_text(table([(bad, "余韵1秒"), ("02-b", "完整保留")]), encoding="utf-8")
    before = tree(p / "00-合集")
    rec = Rec()
    assert run_main([str(p)], rec) == 2
    assert "解析失败" in capsys.readouterr().err
    assert rec.calls == [] and tree(p / "00-合集") == before


@pytest.mark.parametrize("breakage", ["非成员", "缺成片", "缺封面", "缺manifest", "缺稿件"])
def test_缺件或非成员_退2_零ffmpeg_零创建(root: Path, breakage: str) -> None:
    p = make_project(root)
    v = p / "01-a"
    {
        "非成员": lambda: (v / "01-topic.md").unlink(),
        "缺成片": lambda: (v / "05-final.mp4").unlink(),
        "缺封面": lambda: (v / "08-cover.jpg").unlink(),
        "缺manifest": lambda: (v / "03-audio" / "manifest.json").unlink(),
        "缺稿件": lambda: (v / "02-script.md").unlink(),
    }[breakage]()
    before = tree(p / "00-合集")
    rec = Rec()
    assert run_main([str(p)], rec) == 2
    assert rec.calls == [] and tree(p / "00-合集") == before


def test_部件软链指出成员目录_退2_零ffmpeg(root: Path, tmp_path: Path) -> None:
    p = make_project(root)
    elsewhere = tmp_path / "elsewhere.mp4"
    (p / "01-a" / "05-final.mp4").rename(elsewhere)
    os.symlink(elsewhere, p / "01-a" / "05-final.mp4")
    rec = Rec()
    assert run_main([str(p)], rec) == 2
    assert rec.ffmpeg_calls() == [] and not (p / "00-合集" / "cards").exists()


def test_软链卷照用_部件在软链卷的真实目录内(root: Path, tmp_path: Path) -> None:
    """镜像企划的形状：卷本身是软链（指向别的企划的卷），部件按卷的 resolve() 判包含，放行。"""
    p = make_project(root)
    m = root / "data" / "episodes" / "_mirror"
    m.mkdir()
    (m / "00-project.md").write_text(table([("01-a", "余韵1秒"), ("02-b", "完整保留")], None), encoding="utf-8")
    for v in ("01-a", "02-b"):
        os.symlink(p / v, m / v)
    out = root / "data" / "episodes" / "_out"
    out.mkdir()
    (out / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    before = tree(p)
    assert run_main([str(m), "--out", str(out)], Rec()) == 0
    assert tree(p) == before and (out / "05-final.mp4").exists()


@pytest.mark.parametrize("where", ["卷内", "企划目录", "不存在", "无选题"])
def test_out目录不合规_退2(root: Path, where: str) -> None:
    p = make_project(root)
    target = {"卷内": p / "01-a" / "sub", "企划目录": p, "不存在": root / "nope", "无选题": root / "bare"}[where]
    if where == "卷内":
        target.mkdir()
        (target / "01-topic.md").write_text("x", encoding="utf-8")
    if where == "无选题":
        target.mkdir()
    rec = Rec()
    assert run_main([str(p), "--out", str(target)], rec) == 2 and rec.calls == []


# ---------------------------------------------------------------- 覆盖、自证、指纹、签名、并发


def _final(p: Path) -> Path:
    return p / "00-合集" / "05-final.mp4"


def test_已有成片不带replace_退2_字节不变(root: Path) -> None:
    p = make_project(root)
    _final(p).write_bytes(b"OLD")
    rec = Rec()
    assert run_main([str(p)], rec) == 2
    assert _final(p).read_bytes() == b"OLD" and rec.calls == []


def test_replace_自证失败_旧成片不变(root: Path) -> None:
    p = make_project(root)
    _final(p).write_bytes(b"OLD")

    def longer(argv):   # 临时成片的时长比部件之和多 0.2 s
        if argv[0] == "ffprobe" and argv[-1].endswith(asm.TMP_FINAL):
            real = asm.default_runner(argv)
            return subprocess.CompletedProcess(argv, 0, f"{float(real.stdout) + 0.2}\n", "")
        return None

    assert run_main([str(p), "--replace"], Rec(longer)) == 1
    assert _final(p).read_bytes() == b"OLD"
    assert not (p / "00-合集" / "04-assembly.json").exists() and not (p / "00-合集" / asm.TMP_FINAL).exists()


def test_replace_通过_新成片到位(root: Path) -> None:
    p = make_project(root)
    _final(p).write_bytes(b"OLD")
    assert run_main([str(p), "--replace"], Rec()) == 0
    assert _final(p).read_bytes()[:3] != b"OLD" and (p / "00-合集" / "04-assembly.json").exists()


def test_解码自证_部件尾部截掉若干字节_退1(root: Path) -> None:
    """流拷贝拼错不报错、只在解码时炸：末卷（-c copy 原样进拼接）截掉 300 字节，时长差 < 0.1 s 但解码报错。"""
    p = make_project(root)
    f = p / "02-b" / "05-final.mp4"
    os.truncate(f, f.stat().st_size - 300)
    _final(p).write_bytes(b"OLD")
    assert run_main([str(p), "--replace"], Rec()) == 1
    assert _final(p).read_bytes() == b"OLD" and not (p / "00-合集" / "04-assembly.json").exists()


def test_输入指纹_总装途中某卷被改_退1(root: Path) -> None:
    p = make_project(root)

    def touch_during_concat(argv):
        if argv[0] == "ffmpeg" and "concat" in argv:
            src = p / "02-b" / "05-final.mp4"
            st = src.stat()
            os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        return None

    assert run_main([str(p)], Rec(touch_during_concat)) == 1
    assert not _final(p).exists() and not (p / "00-合集" / "04-assembly.json").exists()


def test_签名不一致_退1_不拼接(root: Path, capsys: pytest.CaptureFixture) -> None:
    p = make_project(root)

    def bad_card(argv):
        if argv[0] == "ffprobe" and "stream=" + ",".join(asm.SIG_FIELDS) in argv and argv[-1].endswith("card_part2.mp4"):
            streams = [dict(s, pix_fmt="yuvj420p") if s["codec_type"] == "video" else dict(s, channels=2)
                       for s in json.loads(asm.default_runner(argv).stdout)["streams"]]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"streams": streams}), "")
        return None

    rec = Rec(bad_card)
    assert run_main([str(p)], rec) == 1
    err = capsys.readouterr().err
    assert "编码参数不一致" in err and "card_part2" in err and "yuvj420p" in err
    assert not any("concat" in c for c in rec.ffmpeg_calls()) and not _final(p).exists()


def test_并发_锁被占_退3_先到者产物不变(root: Path) -> None:
    p = make_project(root)
    out = p / "00-合集"
    (out / "_agent").mkdir()
    (out / "cards").mkdir()
    (out / "cards" / "card_part1.mp4").write_bytes(b"FIRST")
    fd = os.open(str(out / "_agent" / "assemble.lock"), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        rec = Rec()
        assert run_main([str(p)], rec) == 3
        assert rec.ffmpeg_calls() == [] and (out / "cards" / "card_part1.mp4").read_bytes() == b"FIRST"
    finally:
        os.close(fd)


def test_缩写不被认作replace(root: Path) -> None:
    p = make_project(root)
    _final(p).write_bytes(b"OLD")
    with pytest.raises(SystemExit) as exc:
        asm.main([str(p), "--rep"], runner=Rec())
    assert exc.value.code == 2 and _final(p).read_bytes() == b"OLD"


def test_静态_不经shell() -> None:
    tree_ = ast.parse(Path(asm.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree_):
        if isinstance(node, ast.keyword):
            assert node.arg != "shell", "assemble 一律 argv，不许 shell="
        if isinstance(node, ast.Attribute):
            assert (getattr(node.value, "id", None), node.attr) not in {
                ("os", "system"), ("os", "popen"), ("subprocess", "getoutput"), ("subprocess", "getstatusoutput")}


# ---------------------------------------------------------------- 审查层（卡、分流、bash）


def test_审查层_企划会话弹卡无allow_key_卡面是预览(root: Path) -> None:
    from pipeline.agent.session import review_tool_call

    p = make_project(root)

    def review(cmd: str, kind: str = "project", ep: Path = p):
        return review_tool_call("run_pipeline", {"command": cmd}, ep_dir=ep, scope="project", root=root,
                                session_kind=kind, project_dir=p)

    v = review("assemble")
    assert v.action == "ask" and v.allow_key is None
    assert "approve_session" not in v.request.options
    assert "part1_trimmed ← 01-a/05-final.mp4 截到 2.00s" in v.request.card_text
    _final(p).write_bytes(b"OLD")
    v = review("assemble")
    assert v.action == "reject" and "--replace" in v.reason
    v = review("assemble --replace")
    assert v.action == "ask" and v.allow_key is None and "⚠ 将覆盖已有 05-final.mp4（不留底）" in v.request.card_text
    v = review("assemble --rep")
    assert v.action == "reject" and "unrecognized arguments: --rep" in v.reason
    v = review("assemble", kind="volume", ep=p / "01-a")
    assert v.action == "reject" and "企划会话" in v.reason
    assert _final(p).read_bytes() == b"OLD" and not (p / "00-合集" / "cards").exists()


def test_bash跑总装也弹卡() -> None:
    from pipeline.agent.status_card import bash_danger_tags

    for cmd in ("python -m pipeline.assemble data/episodes/P", "uv run python pipeline/assemble.py P --replace"):
        assert "[须人审的流水线命令]" in bash_danger_tags(cmd), cmd
