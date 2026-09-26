"""Spec 12（09 封面与标题协作）：图片导入校验与确定性的封面叠字渲染。

契约见 `docs/dev/plans/2026-09-26-cover-title-collaboration-spec.md` §2.1 / §2.2 / §3.1 / §3.2。

三条纪律：
1. **顶层仅 stdlib + `pipeline.paths`**（红线 7）：PIL / io 一律函数级延迟 import，
   `pipeline.agent.tools`、`pipeline.agent.cli` 的热路径不背 Pillow；
2. **同参数同字节**：字体文件与 index 钉死在 `config/project.json`，渲染无随机、无时间、
   无环境读入，输出 PNG 走 `paths.atomic_write`（字节级可复现，基线用例即尺子）；
3. **候选不覆盖**：导入图与渲染产物一律「目标已存在就换名/报错」，定稿路径永远指向同一份字节。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pipeline import paths


class CoverEditError(RuntimeError):
    """受控领域异常：工具层转结构化错误回喂 LLM，CLI 层退 1。"""


# ---- 常量（每个都写依据；改这里要重跑渲染基线） ----

IMPORT_MAX_BYTES = 32 * 1024 * 1024
"""stdin 图片字节上限（§3.1）。封面图实际 1–5 MiB 量级，上限是防御性的。"""

MAX_IMAGE_PIXELS = 40_000_000
"""宽×高上限（§2.1 ③）。8K 图约 3300 万像素，余量够；Pillow 默认 MAX_IMAGE_PIXELS
实测 89,478,485 太宽，此处显式收紧，拦解压炸弹。"""

IMPORT_FORMAT_EXT: dict[str, str] = {"PNG": ".png", "JPEG": ".jpg"}
"""Pillow 报出的格式 → 落盘后缀。`.jpeg` 一律规范化为 `.jpg`（§2.1 命名冻结）。"""

MAX_LINES = 6
MAX_TEXT_CHARS = 40
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 400
MAX_OFFSET_PX = 4000
MAX_STROKE_WIDTH = 40
EDIT_MAX_FILES = 64
"""单期 `edit-*.png` 上限（§2.2）。真实迭代一期 <10 版，64 是两个数量级余量（RF-4）。"""

MARGIN_PX = 48
"""角/边锚点的固定边距（§2.2 第 4 桩）。初值，按真实封面效果可经修订调整。"""

DEFAULT_FONT_FILE = "data/fonts/SourceHanSansSC-Bold.otf"
DEFAULT_FONT_INDEX = 0

SOURCE_RE = re.compile(r"^07-cover/(?:import/)?[^/]+\.(?:png|jpg)$", re.IGNORECASE)
OUTPUT_RE = re.compile(r"^07-cover/edit-[a-z0-9][a-z0-9-]{0,38}\.png$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# 九宫锚点 → (Pillow anchor, x 档位 0=左/1=中/2=右, y 档位 0=上/1=中/2=下)
_ANCHORS: dict[str, tuple[str, int, int]] = {
    "tl": ("la", 0, 0), "tc": ("ma", 1, 0), "tr": ("ra", 2, 0),
    "cl": ("lm", 0, 1), "cc": ("mm", 1, 1), "cr": ("rm", 2, 1),
    "bl": ("ld", 0, 2), "bc": ("md", 1, 2), "br": ("rd", 2, 2),
}

_PARAM_KEYS = {"source", "output", "lines"}
_LINE_KEYS = {"text", "size", "anchor", "dx", "dy", "color", "stroke_width", "stroke_color"}
_STEM_KEEP = re.compile(r"[0-9A-Za-z_\-\u4e00-\u9fff]")


# ---- 字体（唯一真源：config/project.json 的 cover 段） ----


def load_cover_font(size: int) -> Any:
    """按 config 的 `cover.font_file` / `cover.font_index` 加载字体。

    字体缺席 → `CoverEditError`（含 config 键与期望路径），**绝不 fallback 系统字体**：
    fallback 会让同参数在不同机器产出不同字节，且用「渲染成功了」掩盖字体缺席（RF-1）。
    """
    from PIL import ImageFont

    rel, index = _font_config()
    path = Path(paths.ROOT) / rel
    if not path.is_file():
        raise CoverEditError(
            f"字体文件缺席：config cover.font_file={rel}"
            f"（期望路径 {path}）。请把开源字体放入 data/fonts/（文件不进 git），"
            f"或改 config/project.json 的 cover.font_file。"
        )
    try:
        return ImageFont.truetype(str(path), size, index=index)
    except OSError as exc:
        raise CoverEditError(
            f"字体加载失败（config cover.font_file={rel}、cover.font_index={index}）：{exc}"
        ) from exc


def _font_config() -> tuple[str, int]:
    rel = str(paths.conf("cover.font_file", DEFAULT_FONT_FILE))
    raw_index = paths.conf("cover.font_index", DEFAULT_FONT_INDEX)
    if isinstance(raw_index, bool) or not isinstance(raw_index, int):
        raise CoverEditError(f"config cover.font_index 必须是整数，收到 {raw_index!r}")
    return rel, raw_index


# ---- 导入（§2.1 / §3.1） ----


def sanitize_stem(name: str | None) -> str:
    """原始文件名 → 落盘 stem：保留字母/数字/CJK/`-`/`_`，其余折叠为单个 `-`，截 40 字符。

    人给的 `--name` 只提供 stem 建议，不直接成为路径——目标文件名闭集由本函数生成。
    """
    raw = Path(str(name or "")).stem
    kept = "".join(ch if _STEM_KEEP.fullmatch(ch) else "-" for ch in raw)
    kept = re.sub(r"-{2,}", "-", kept)[:40]
    return kept or "img"


def probe_image(data: bytes) -> dict[str, Any]:
    """导入字节的诚实校验（不信后缀）。顺序冻结（🔵-12）：open 头部 → 格式与像素闸 → verify + load。

    verify 与 load 的分工实测（Pillow 12.3.0）：文件级截断与 CRC 错由 open/verify 拦，
    非 zlib 乱字节 IDAT 与非行对齐短缺由 load 拦；**行对齐短缺两者都放**（缺行静默补黑，
    已知上限 RF-11，由人预览兜底）。
    """
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
    except Exception as exc:
        raise CoverEditError(f"图片解码失败（Pillow 无法识别）：{exc}") from exc

    if fmt not in IMPORT_FORMAT_EXT:
        raise CoverEditError(f"只接受 PNG/JPEG，收到 {fmt or '未知格式'}")
    if width * height > MAX_IMAGE_PIXELS:
        raise CoverEditError(
            f"像素数 {width * height}（{width}×{height}）超过上限 {MAX_IMAGE_PIXELS}，拒收"
        )

    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise CoverEditError(f"图片校验失败（文件级损坏）：{exc}") from exc
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
    except Exception as exc:
        raise CoverEditError(f"图片完整解码失败：{exc}") from exc

    return {"format": fmt, "width": width, "height": height}


def import_cover(ep_dir: Path | str, name: str | None, data: bytes) -> dict[str, Any]:
    """校验并原子落盘导入图（§2.1）。返回 `{"path","width","height","format"}`。

    `ep_dir` 必须是已 resolve 的期目录（调用方过 `tools.resolve_episode_dir`）；
    期目录不存在 → `CoverEditError`，**绝不 mkdir 期目录**（`07-cover/import/` 子目录
    在期目录已确认存在的前提下按需创建，同 status_card 建 `_agent/` 的先例）。
    """
    info = probe_image(data)
    ep = Path(ep_dir)
    if not ep.is_dir():
        raise CoverEditError(f"期目录不存在：{ep}")

    target_dir = ep / "07-cover" / "import"
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_stem(name)
    ext = IMPORT_FORMAT_EXT[info["format"]]

    seq = 1
    while True:
        cand = target_dir / (f"{stem}{ext}" if seq == 1 else f"{stem}-{seq}{ext}")
        if not cand.exists():
            break
        seq += 1

    paths.atomic_write(cand, data)
    return {
        "path": cand.relative_to(ep).as_posix(),
        "width": info["width"],
        "height": info["height"],
        "format": info["format"],
    }


# ---- 渲染（§2.2 / §3.2） ----


def render_cover(ep_dir: Path | str, params: dict[str, Any]) -> dict[str, Any]:
    """按 §3.2 schema 渲染并原子落盘。返回 `{"path","width","height","sha256"}`。

    源只读、目标不覆盖、同参数同字节。参数校验与渲染在同一处：工具层与直接调用
    走的是同一条闸（调用前「已校验」不能成为省略校验的理由）。
    """
    spec = _validate_params(params)
    ep = Path(ep_dir)
    if not ep.is_dir():
        raise CoverEditError(f"期目录不存在：{ep}")

    src = _resolve_under_cover(ep, spec["source"], must_exist=True)
    cover_dir = (ep / "07-cover").resolve()
    dest = ep / spec["output"]
    if dest.parent.resolve() != cover_dir:
        raise CoverEditError(f"输出路径越界：{spec['output']}")
    if dest.exists():
        raise CoverEditError(f"输出目标已存在，绝不覆盖：{spec['output']}（换一个 output 名）")
    if len(list(cover_dir.glob("edit-*.png"))) >= EDIT_MAX_FILES:
        raise CoverEditError(
            f"单期 edit-*.png 已达上限 {EDIT_MAX_FILES}，拒收（先清理不再要的版本）"
        )

    from io import BytesIO

    from PIL import Image, ImageDraw

    try:
        with Image.open(src) as img:
            img.load()                      # 完整解码：坏图当场报错（RF-8）
            canvas = img.convert("RGBA")
    except Exception as exc:
        raise CoverEditError(f"源图解码失败：{spec['source']}（{exc}）") from exc

    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    for line in spec["lines"]:
        font = load_cover_font(line["size"])
        anchor, x_slot, y_slot = _ANCHORS[line["anchor"]]
        x = (MARGIN_PX if x_slot == 0 else width // 2 if x_slot == 1 else width - MARGIN_PX)
        y = (MARGIN_PX if y_slot == 0 else height // 2 if y_slot == 1 else height - MARGIN_PX)
        draw.text(
            (x + line["dx"], y + line["dy"]),
            line["text"],
            font=font,
            fill=line["color"],
            anchor=anchor,
            stroke_width=line["stroke_width"],
            stroke_fill=line["stroke_color"],
        )

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    data = buf.getvalue()
    paths.atomic_write(dest, data)
    return {
        "path": spec["output"],
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _resolve_under_cover(ep: Path, rel: str, *, must_exist: bool) -> Path:
    cover_root = (ep / "07-cover").resolve()
    target = (ep / rel).resolve()
    if cover_root not in target.parents:
        raise CoverEditError(f"路径越出 07-cover/：{rel}")
    if must_exist and not target.is_file():
        raise CoverEditError(f"源图不存在：{rel}")
    return target


def _validate_params(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise CoverEditError("cover_edit 参数必须是对象")
    unknown = sorted(set(params) - _PARAM_KEYS)
    if unknown:
        raise CoverEditError(f"未知参数：{unknown}（additionalProperties: false）")

    source = params.get("source")
    if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
        raise CoverEditError(
            f"source 必须是 07-cover/（可含 import/）下的 .png/.jpg 相对路径，收到 {source!r}"
        )
    output = params.get("output")
    if not isinstance(output, str) or not OUTPUT_RE.fullmatch(output):
        raise CoverEditError(
            f"output 必须匹配 ^07-cover/edit-[a-z0-9][a-z0-9-]{{0,38}}\\.png$，收到 {output!r}"
        )
    lines = params.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise CoverEditError(f"lines 必须是 1–{MAX_LINES} 条的数组，收到 {lines!r}")

    return {
        "source": source,
        "output": output,
        "lines": [_validate_line(ln, i) for i, ln in enumerate(lines)],
    }


def _validate_line(line: Any, index: int) -> dict[str, Any]:
    where = f"lines[{index}]"
    if not isinstance(line, dict):
        raise CoverEditError(f"{where} 必须是对象")
    unknown = sorted(set(line) - _LINE_KEYS)
    if unknown:
        raise CoverEditError(f"{where} 未知键：{unknown}（additionalProperties: false）")

    text = line.get("text")
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT_CHARS:
        raise CoverEditError(f"{where}.text 必须是 1–{MAX_TEXT_CHARS} 字符的字符串")

    size = line.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not MIN_FONT_SIZE <= size <= MAX_FONT_SIZE:
        raise CoverEditError(f"{where}.size 必须是 {MIN_FONT_SIZE}–{MAX_FONT_SIZE} 的整数")

    anchor = line.get("anchor")
    if anchor not in _ANCHORS:
        raise CoverEditError(f"{where}.anchor 必须是 {'|'.join(_ANCHORS)} 之一")

    out = {"text": text, "size": size, "anchor": anchor}
    for key, lo, hi in (("dx", -MAX_OFFSET_PX, MAX_OFFSET_PX),
                        ("dy", -MAX_OFFSET_PX, MAX_OFFSET_PX),
                        ("stroke_width", 0, MAX_STROKE_WIDTH)):
        value = line.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise CoverEditError(f"{where}.{key} 必须是 {lo}–{hi} 的整数")
        out[key] = value

    for key, default in (("color", "#FFFFFF"), ("stroke_color", "#000000")):
        value = line.get(key, default)
        if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
            raise CoverEditError(f"{where}.{key} 必须是 ^#[0-9a-fA-F]{{6}}$，收到 {value!r}")
        out[key] = value
    return out
