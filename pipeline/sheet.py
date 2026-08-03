"""联系表：把一堆帧拼成一张大图。

**给人（或 agent）看图用的，不产出任何判据。** 视觉这条线上有好几处判断只有看画面
才能做——切点是不是真的切点、tagger 认的角色对不对、画面检索命中的是不是那个氛围——
这些都不该由代码替人拍板（CLAUDE.md「把审美判断包装成机器判据，比不做更糟」）。

拼成大图而不是逐张看，纯粹为了省 token 和省点击：一张 6×4 的表顶 24 次单张打开。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BG = (20, 22, 26)
FG = (210, 214, 220)
LABEL_H = 18


def build(cells: list[tuple[Path, str]], dest: Path, cols: int = 6,
          cell_w: int = 320, ratio: float = 9 / 16) -> Path:
    """`cells` 是 (图片路径, 标签)。缺图的格子留黑并把标签照写——

    **不要静默跳过缺图**：跳过会让后面每一格前移，标签与画面整体错位一格，
    而错位之后每一格看着都「正常」，没有任何地方报错。
    """
    if not cells:
        raise SystemExit("FAIL 没有可拼的帧")
    rows = (len(cells) + cols - 1) // cols
    cell_h = int(cell_w * ratio)
    img = Image.new("RGB", (cols * cell_w, rows * (cell_h + LABEL_H)), BG)
    d = ImageDraw.Draw(img)
    for i, (path, label) in enumerate(cells):
        x, y = (i % cols) * cell_w, (i // cols) * (cell_h + LABEL_H)
        if path is not None and Path(path).exists():
            try:
                img.paste(Image.open(path).resize((cell_w, cell_h)), (x, y))
            except (OSError, ValueError):
                pass                      # 只吞「这一帧读不出来」，见 cover._metrics 同源注释
        d.text((x + 4, y + cell_h + 3), label, fill=FG)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, quality=88)
    return dest
