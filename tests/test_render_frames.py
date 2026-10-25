"""D145 画面轨按成片时间轴分帧、D144 放慢读到片尾。

D145：每段各按 `-t dur` 截，编码器把尾巴凑到整帧，单段多出的零头在 1 帧容差内、守卫放行，
拼接后逐段累积（卷三 191 段到片尾晚 3.6 s），合成按音频截尾又把多出的画面藏掉。
现在每段的帧数按计划时间轴的累计边界分（n = B(t1) − B(t0)），拼完、合成前核画面轨。
D144：放慢分支读到源片末尾时，末帧时长不随 setpts 拉长、视频流又比容器早结束，切出来短。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import render

FT = render._fps_to_frame_time(render.FPS)

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="需要 ffmpeg / ffprobe")

# 卷三 approved 抽样的真实片长（秒）：按 -t 截时 15 段累计多出 254 ms
DURS = [9.586, 4.008, 5.964, 5.402, 5.401, 4.273, 14.437, 8.879,
        6.190, 3.802, 9.198, 9.198, 9.952, 6.532, 7.534]


def _contiguous(durs: list[float], t0: float = 0.0) -> list[tuple[float, float]]:
    out, t = [], t0
    for d in durs:
        out.append((t, t + d))
        t += d
    return out


# ---------------------------------------------------------------------------
# 纯函数：分帧、段内末片、占位连续
# ---------------------------------------------------------------------------

class TestSpanFrames:
    def test_每段起点离计划不超过半帧(self):
        spans = _contiguous(DURS * 13)          # 195 段，同卷三量级
        pos = 0
        for t0, t1 in spans:
            assert abs(pos * FT - t0) <= FT / 2 + 1e-9
            pos += render.span_frames(t0, t1)
        assert abs(pos * FT - spans[-1][1]) <= FT / 2 + 1e-9

    def test_各段帧数之和等于全长分帧(self):
        spans = _contiguous(DURS, t0=12.345)
        total = sum(render.span_frames(a, b) for a, b in spans)
        assert total == render.span_frames(spans[0][0], spans[-1][1])

    def test_同样时长在不同位置可差一帧(self):
        # 0.06 s 不到 1.5 帧：落在哪儿决定占 1 帧还是 2 帧，累计边界才对得上
        got = {render.span_frames(t, t + 0.06) for t in (0.0, 0.01, 0.02, 0.03)}
        assert got == {1, 2}


class TestSegmentClipFrames:
    def test_末片钉在段落终点_吸收段内零头(self):
        # 段内 Σdur = 5.03，配音 5.00（在 SEG_TOL 内）：末片少分，下一段从 5.00 起
        segs = [{"index": 1, "clips": [{"dur": 2.0}, {"dur": 3.03}]},
                {"index": 2, "clips": [{"dur": 4.0}]}]
        span = {1: (0.0, 5.0), 2: (5.0, 9.0)}
        got = render.segment_clip_frames(segs, span)
        assert got == [render.span_frames(0.0, 2.0), render.span_frames(2.0, 5.0),
                       render.span_frames(5.0, 9.0)]
        assert sum(got) == render.span_frames(0.0, 9.0)

    def test_extend计入片长(self):
        segs = [{"index": 1, "clips": [{"dur": 1.0, "extend": 0.5}, {"dur": 2.0}]}]
        got = render.segment_clip_frames(segs, {1: (0.0, 3.5)})
        assert got[0] == render.span_frames(0.0, 1.5)

    def test_段落不在时间轴里报错(self):
        with pytest.raises(SystemExit, match="不在成片时间轴"):
            render.segment_clip_frames([{"index": 7, "clips": [{"dur": 1.0}]}], {})


class TestCheckTiling:
    def test_首尾相接通过_顺序无关(self):
        render.check_tiling([(2.0, 5.0, "b"), (0.0, 2.0, "a"), (5.0, 6.5, "c")], 6.5)

    @pytest.mark.parametrize("spans,total,msg", [
        ([(0.0, 2.0, "a"), (2.1, 5.0, "b")], 5.0, "不连续"),      # 空档
        ([(0.0, 2.0, "a"), (1.9, 5.0, "b")], 5.0, "不连续"),      # 重叠
        ([(0.0, 2.0, "a"), (2.0, 5.0, "b")], 6.0, "成片计划"),     # 没铺到总长
        ([(0.5, 2.0, "a")], 2.0, "不连续"),                       # 不从 0 起
    ])
    def test_不连续报错(self, spans, total, msg):
        with pytest.raises(SystemExit, match=msg):
            render.check_tiling(spans, total)


# ---------------------------------------------------------------------------
# 真 ffmpeg：切片帧数、拼接累积、放慢到片尾、定格段
# ---------------------------------------------------------------------------

def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture
def small(monkeypatch):
    """输出改小、编码改快：只测时间轴，不测画质。"""
    monkeypatch.setattr(render, "W", 160)
    monkeypatch.setattr(render, "H", 90)
    monkeypatch.setattr(render, "PRESET", "ultrafast")


def _src(tmp_path: Path, dur: float, audio: float | None = None) -> Path:
    """29.97 fps 测试片；audio 给了就带一条更长的音轨（容器时长 > 视频流）。"""
    src = tmp_path / "src.mp4"
    args = ["-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=30000/1001:duration={dur}"]
    if audio is not None:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio}", "-c:a", "aac"]
    _ff(*args, "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(src))
    return src


def _concat(parts: list[Path], dest: Path) -> Path:
    lst = dest.parent / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(lst), "-c", "copy", str(dest)],
                   check=True, capture_output=True, cwd=dest.parent)
    return dest


@needs_ffmpeg
def test_多段切片拼接_画面轨与计划差不过一帧(tmp_path: Path, small):
    """卷三那 15 个片长缩到 1/10 照切：旧的 -t 截法每段多出零头，这里按帧切、拼完核总长。"""
    src = _src(tmp_path, 20.0)
    durs = [round(d / 10, 3) for d in DURS]
    spans = _contiguous(durs)
    parts, t = [], 0.0
    for k, (d, (t0, t1)) in enumerate(zip(durs, spans)):
        dest = tmp_path / f"c{k:02d}.mp4"
        n = render.span_frames(t0, t1)
        render.cut({"source": str(src), "start": t, "dur": d}, dest, frames=n)
        assert render.frame_count(dest) == n
        parts.append(dest)
        t += d
    video = _concat(parts, tmp_path / "video.mp4")
    frames = sum(render.span_frames(a, b) for a, b in spans)
    render.check_video_track(video, frames, spans[-1][1])
    assert abs(render.duration(video) - spans[-1][1]) <= FT / 2 + 1e-3


@needs_ffmpeg
def test_画面轨多出两帧报错(tmp_path: Path, small):
    src = _src(tmp_path, 3.0)
    n = render.span_frames(0.0, 2.0)
    dest = tmp_path / "v.mp4"
    render.cut({"source": str(src), "start": 0.0, "dur": 2.0 + 2 * FT}, dest, frames=n + 2)
    with pytest.raises(SystemExit, match="画面轨与计划不符"):
        render.check_video_track(dest, n, 2.0)


@needs_ffmpeg
def test_放慢读到片尾_帧数不短(tmp_path: Path, small):
    """D144：视频流 2.0 s、音轨 2.3 s（容器 2.3 s），从 1.0 s 起放慢 1.42× 铺满。

    守卫 1 与放慢系数按容器时长算（与 D145 之前一致），视频流早结束的 0.3 s 与末帧
    放慢后都不够，旧截法短 ≈ 1.42 ×（0.3 s + 末帧 33 ms），远超 1 帧；现在末帧克隆补齐，
    帧数恰好。素材库实测容器比视频流最多长 0.209 s（SP45），卷三 SP05 是 31 ms。"""
    src = _src(tmp_path, 2.0, audio=2.3)
    src_dur = render.duration(src)
    slow = 1.42
    dur = (src_dur - 1.0) * slow
    n = render.span_frames(100.0, 100.0 + dur)
    dest = tmp_path / "m.mp4"
    render.cut({"source": str(src), "start": 1.0, "dur": dur}, dest, slow=slow, frames=n)
    assert render.frame_count(dest) == n


@needs_ffmpeg
def test_定格段按帧数出(tmp_path: Path, small):
    """没声明 `画面:` 的音乐段取上一段末帧定格，同样按成片时间轴分帧。"""
    src = _src(tmp_path, 2.0)
    seg = tmp_path / "c001.mp4"
    render.cut({"source": str(src), "start": 0.0, "dur": 1.0}, seg,
               frames=render.span_frames(0.0, 1.0))
    # 起点 1.01：按边界分是 19 帧，按时长 0.77 s 取整（`-t` 的行为）是 18 帧
    plan = {"segments": [{"index": 1, "start": 0.0, "dur": 1.01}],
            "blocks": [{"title": "x", "start": 1.01, "dur": 0.77, "visual": None}],
            "tracks": []}
    out = render._still_frames(tmp_path, plan, {1: [seg]}, tmp_path)
    (still, dur, start, n), = out
    assert (dur, start) == (0.77, 1.01)
    assert n == render.span_frames(1.01, 1.78) == 19
    assert render.frame_count(still) == n


# ---------------------------------------------------------------------------
# 普通期端到端：run 真的在合成前核了画面轨
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_普通期渲染_合成前按计划核画面轨(tmp_path: Path, small, monkeypatch):
    src = _src(tmp_path, 10.0)
    ep = tmp_path / "ep"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "01-topic.md").write_text("类型: 杂谈\n", encoding="utf-8")
    seg_durs = [1.537, 2.019, 1.263]
    man, segs, t = [], [], 0.0
    for i, d in enumerate(seg_durs, 1):
        wav = ep / "03-audio" / f"{i:02d}.wav"
        _ff("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", f"{d}",
            "-ac", "1", str(wav))
        d = render.duration(wav)
        man.append({"index": i, "file": wav.name, "duration": d, "text": f"第{i}段。",
                    "sentences": None})
        half = round(d / 2, 3)
        segs.append({"index": i, "status": "ok", "duration": d, "text": f"第{i}段。",
                     "clips": [{"source": str(src), "start": t, "dur": half},
                               {"source": str(src), "start": t + 3, "dur": round(d - half, 3)}]})
        t += 2.5
    (ep / "03-audio" / "manifest.json").write_text(json.dumps({"segments": man}), encoding="utf-8")
    (ep / "04-clips.approved.json").write_text(json.dumps({"segments": segs}), encoding="utf-8")

    calls = []
    real = render.check_video_track
    monkeypatch.setattr(render, "check_video_track",
                        lambda v, f, total: (calls.append((f, total)), real(v, f, total)))
    final = render.run(ep)
    total = sum(m["duration"] for m in man)
    assert calls == [(render.span_frames(0.0, total), total)]
    assert final.exists()
