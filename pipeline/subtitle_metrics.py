"""字幕的真实像素宽度：按 libass 实际选用的字体与缩放量（D146）。

render 的 `line_width` 是折行约定：一个汉字记 1 格（= 字号 54 px），中文标点与 ASCII
记半格。它定的是「一行放多少字」，不是像素。2026-10-25 在 Hiragino Sans GB 上实测：

- libass 按字体 OS/2 的 winAscent + winDescent 等于字号来缩放，汉字实际宽约 46.5 px，不是 54 px；
- 中文标点在行中占满一个汉字宽（「一，」×10 与「一」×20 墨迹同宽），不是半格；
- 拉丁字母、数字按字形各宽各的：小写约 0.58、大写约 0.75、数字约 0.66、空格约 0.33 个汉字宽。

所以约定宽度不能拿来判「会不会超出边距」：汉字被高估，标点、大写、数字被低估，标点或大写
多的行会真溢出，libass 在空格处自动折行（WrapStyle 0），qc 却照约定判一行放得下。
这里给 qc 的「字幕不超宽」「字幕每卡一行」用：字符相对宽度取 fontconfig 解析出的同一个字体
文件（libass 也经 fontconfig 找字体），汉字的像素宽用 libass 实渲一次标定。
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path

from . import render

# 标定用的参照串：同一个汉字 20 个与 40 个，墨迹宽之差 = 20 个汉字的步进宽，两端字侧留白相消
_REF = "一"


@functools.lru_cache(maxsize=1)
def font_file() -> tuple[str, int]:
    """fontconfig 对 `render.FONT` 解析出的字体文件与 TTC 序号（libass 走同一条路）。"""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}|%{index}", render.FONT],
                             capture_output=True, text=True, check=True).stdout.strip()
    except FileNotFoundError:
        raise SystemExit("FAIL 量字幕宽度要 fc-match（fontconfig），本机没有：brew install fontconfig")
    path, _, idx = out.partition("|")
    if not path or not Path(path).exists():
        raise SystemExit(f"FAIL fontconfig 解析不出字体「{render.FONT}」：{out!r}")
    return path, int(idx or 0)


@functools.lru_cache(maxsize=1)
def _font():
    from PIL import ImageFont
    path, idx = font_file()
    return ImageFont.truetype(path, 1000, index=idx)


def _ink_width(text: str, work: Path) -> int:
    """用 libass 把一行渲在黑底上，量白色像素的水平跨度。"""
    import numpy as np
    from PIL import Image
    ass = work / "m.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {render.W * 2}
PlayResY: {render.H}
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: M,{render.FONT},{render.FONT_SIZE},&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
Dialogue: 0,0:00:00.00,0:00:01.00,M,,0,0,0,,{text}
""", encoding="utf-8")
    png = work / "m.png"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=black:s={render.W * 2}x{render.H}:d=0.1",
                    "-vf", f"ass={ass.name}", "-frames:v", "1", png.name],
                   check=True, capture_output=True, cwd=work)
    cols = np.where((np.asarray(Image.open(png).convert("L")) > 128).any(axis=0))[0]
    if not len(cols):
        raise SystemExit(f"FAIL libass 没渲出字（字体「{render.FONT}」）——先查 fc-list 认不认得这个名字")
    return int(cols[-1] - cols[0] + 1)


@functools.lru_cache(maxsize=1)
def cjk_px() -> float:
    """libass 渲染时一个汉字的步进宽（像素）。"""
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        return (_ink_width(_REF * 40, w) - _ink_width(_REF * 20, w)) / 20


def text_px(text: str) -> float:
    """一行字在 libass 眼里的像素宽：前面各字按步进累加，最后一个字只算到墨迹右沿。

    libass 判「超没超边距」看末字的墨迹右沿而不是步进（2026-10-25 实测：带空格的一行
    步进 1686 px，末字是「一」就折行，末字是「，」墨迹右沿 1647 px 就不折）。
    字体里的比例 × libass 标定的汉字像素宽。
    """
    if not text:
        return 0.0
    f = _font()
    return (f.getlength(text[:-1]) + _ink(text[-1])[1]) / f.getlength(_REF) * cjk_px()


def _ink(ch: str) -> tuple[float, float]:
    """单个字形墨迹的左沿、右沿（字体单位，从笔位原点量）。`getbbox` 给的是排版框，不是墨迹。"""
    mask, (x, _y) = _font().getmask2(ch)
    bb = mask.getbbox()
    return (x + bb[0], x + bb[2]) if bb else (0.0, 0.0)


def max_px() -> int:
    """边距之内能放的像素宽；超过它 libass 就自动折行（有空格）或冲出边距（没空格）。"""
    return render.W - render.MARGIN_LR * 2
