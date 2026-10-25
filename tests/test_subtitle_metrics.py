"""D146 字幕真实像素宽：qc「字幕不超宽」「字幕每卡一行」按 libass 实际渲染的宽度判。

render 的 `line_width` 是折行约定（汉字 1 格 = 54 px、标点与 ASCII 半格），不是像素：
2026-10-25 实测 Hiragino Sans GB 汉字约 46.5 px、中文标点满格、大写与数字比半格宽。
这里的期望值都拿 libass 真渲出来的墨迹宽度对照，不拿模型自己对自己。
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pipeline import qc, render
from pipeline import subtitle_metrics as m

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("fc-match")), reason="需要 ffmpeg 与 fontconfig")


def _render(text: str, d: Path, wrap_style: int = 0) -> np.ndarray:
    """照成片 VO 样式（边距、字号）用 libass 渲一行，返回白色像素掩码。"""
    ass = d / "t.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {render.W}
PlayResY: {render.H}
WrapStyle: {wrap_style}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: VO,{render.FONT},{render.FONT_SIZE},&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,{render.MARGIN_LR},{render.MARGIN_LR},{render.MARGIN_V},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
Dialogue: 0,0:00:00.00,0:00:01.00,VO,,0,0,0,,{text}
""", encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=black:s={render.W}x{render.H}:d=0.1",
                    "-vf", "ass=t.ass", "-frames:v", "1", "t.png"], check=True, cwd=d)
    return np.asarray(Image.open(d / "t.png").convert("L")) > 128


def _libass_wraps(text: str) -> bool:
    with tempfile.TemporaryDirectory() as d:
        rows = np.where(_render(text, Path(d)).any(axis=1))[0]
    return rows[-1] - rows[0] > render.FONT_SIZE      # 第二行出现，墨迹高超过一行


# 带空格（libass 只在空格处折）、长度卡在边距附近的行：末字墨迹右沿决定折不折
EDGE = [("一" * 17 + " " + "一" * 17 + tail) for tail in ("一", "，", "A")] + \
       [("一" * 18 + " " + "一" * 17 + tail) for tail in ("一", "，", "A")]


@pytest.mark.parametrize("text", EDGE)
def test_模型判超宽与libass真折行一致(text: str):
    assert (m.text_px(text) > m.max_px()) == _libass_wraps(text)


def test_边界样本两种结果都有():
    # 夹具真跨过了边界：不然上一条全判「不折」也会过
    assert {_libass_wraps(t) for t in EDGE} == {True, False}


@pytest.mark.parametrize("text", [
    "一" * 20,
    "一，" * 10,                                  # 中文标点满格
    "一" * 10 + "A" * 10,                          # 大写比半格宽
    "一" * 10 + "0" * 10,
    "一15岁的女中学生被拿来同前任主唱 Nagi 以及同期出道的歌手反复比较，",
])
def test_像素宽与libass墨迹一致(text: str):
    # 墨迹从首字左沿量起，模型从笔位原点量起，差首字「一」的左侧留白
    lead = m._ink("一")[0] / m._font().getlength("一") * m.cjk_px()
    with tempfile.TemporaryDirectory() as d:
        cols = np.where(_render(text, Path(d), wrap_style=2).any(axis=0))[0]
    assert abs(m.text_px(text) - lead - (cols[-1] - cols[0] + 1)) <= 3


def test_汉字像素宽不是字号():
    # libass 按 winAscent + winDescent 缩放，Hiragino Sans GB 上 54 × 1000 / 1162 ≈ 46.5
    assert 44 < m.cjk_px() < 49
    assert m.cjk_px() < render.FONT_SIZE


def _items(*texts: str):
    return [("", {"index": i, "duration": 3.0, "text": t}, {"text": t, "sentences": None})
            for i, t in enumerate(texts, 1)]


def _by_name(checks):
    return {c.name: c for c in checks}


def test_标点密集_约定一行放得下_实际溢出():
    dense = "一、" * 20                  # 约定宽 30 格 ≤ 30.5，真实 40 个汉字宽 ≈ 1856 px > 1680
    assert len(render.wrap(dense, render.per_line())) == 1
    got = _by_name(qc._subtitle_checks(_items(dense, "一" * 10)))
    assert not got["字幕不超宽"].ok and "1-1" in got["字幕不超宽"].detail
    assert not got["字幕每卡一行"].ok and "1-1" in got["字幕每卡一行"].detail


def test_普通整行照过():
    got = _by_name(qc._subtitle_checks(_items("一" * 30, "二" * 12)))
    assert got["字幕不超宽"].ok and got["字幕每卡一行"].ok
    assert "px" in got["字幕不超宽"].detail


def test_约定宽度超了照样算折行():
    # render 自己按约定插 \N 折行的卡（卷三 58-2 那类）仍报「每卡一行」：上屏确实是两行
    long = "一" * 32
    got = _by_name(qc._subtitle_checks(_items(long)))
    assert got["字幕不超宽"].ok
    assert not got["字幕每卡一行"].ok
