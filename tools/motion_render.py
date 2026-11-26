#!/usr/bin/env python3
"""动效讲解素材生成器：把一张场景页（HTML）逐帧截图，合成补料用的 `*-motion.mp4`。

常用入口：
    # 1. 开工前抽相邻镜头与配乐情绪，产出视觉上下文参考图 + JSON
    uv run python tools/motion_render.py --episode data/episodes/<期> --seg 153 \\
        --context <scratch 目录>

    # 2. 写好场景页后出关键帧（自动附产带时间戳的接触表），--debug 叠上下安全区
    uv run python tools/motion_render.py <场景.html> --episode data/episodes/<期> --seg 153 \\
        --stills 0.5,2.5,5.0,7.5 --stills-dir <scratch 目录> --debug

    # 3. 构图确认后渲染合约成片并做复现检查（可选顺带输出前后连切预览与接缝抽帧）
    uv run python tools/motion_render.py <场景.html> --episode data/episodes/<期> --seg 153 \\
        --out data/episodes/<期>/patch_assets/<名>-motion.mp4 --verify-repro \\
        [--splice <scratch 目录>/splice.mp4 [--splice-bgm]]

    # 4. 对已有动效成片单独生成前后连切预览（自动附产 <名>-seams.png 供看图自查）
    uv run python tools/motion_render.py --episode data/episodes/<期> --seg 153 \\
        --motion data/episodes/<期>/patch_assets/<名>-motion.mp4 \\
        --splice <scratch 目录>/splice.mp4 [--splice-bgm]

场景页约定（完整规范见 `skills/make-motion-asset/SKILL.md`）：
- 定义全局 `renderFrame(t)`，t 是秒（可选异步 `initScene()` 在字体就绪后执行一次）；支持 Canvas 2D / WebGL2（`window.motionGL`）/ SVG。
- 运行时拦截 `Math.random`、`Date`、`performance.now`、`requestAnimationFrame`、CSS 动画与过渡、联网请求。
- `window.MOTION` 注入画幅、帧率、时长、逐句起点、`safeTop`/`safeBottom`、`hasChapters`、`fonts`、`params`。
- `window.motion`（`tools/motion/runtime.js`）提供确定性缓动、弹簧、微震、2D 相机、离屏细颗粒与带种子随机数。
- `window.motionGL`（`tools/motion/gl.js`）提供 WebGL2 上下文初始化、Shader 编译、全屏四边形与 `mat4`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import paths  # noqa: E402
from pipeline.chapters import BAR_HEIGHT  # noqa: E402
from pipeline.qc import BLACK_MAX  # noqa: E402

W = paths.conf("video.width", 1920)
H = paths.conf("video.height", 1080)
FPS = Fraction(paths.conf("video.fps", "24000/1001"))

# 顶部章节顶栏高度（对齐 pipeline/chapters.py BAR_HEIGHT = 64.0）
CHAPTER_BAR_H = int(round(BAR_HEIGHT))

# 字幕安全区高度：render.py 一张卡只放一行（卡片不折行），字压在 MarginV 上方。
# 1.5 倍字号 = 行高加描边与阴影的余量。画面主体不进这条带子，字幕才不压在关键元素上。
SUB_BAND = paths.conf("subtitle.margin_v", 90) + round(paths.conf("subtitle.size", 54) * 1.5)

FONT_FAMILY = "MotionSans"
FONT_FILE = paths.DATA / "fonts" / "SourceHanSansSC-Bold.otf"

# 多字体栈声明表：(CSS 字体族名, 相对 data/fonts/ 候选文件名列表, 是否必需)
# 必需项缺失立刻失败；可选项存在才加载，实际加载成功的族名写入 MOTION.fonts。
FONT_SPECS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("MotionSans", ("SourceHanSansSC-Bold.otf",), True),
    ("MotionSerif", ("Songti.ttc", "SourceHanSerifSC-Regular.otf", "SourceHanSerifSC-Bold.otf"), False),
    ("MotionSansRegular", ("SourceHanSansSC-Regular.otf", "SourceHanSansSC-Medium.otf"), False),
    ("MotionMono", ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Medium.ttf", "Menlo.ttc"), False),
)

RUNTIME_JS = Path(__file__).resolve().parent / "motion" / "runtime.js"
GL_JS = Path(__file__).resolve().parent / "motion" / "gl.js"

# 素材在 06 还要再编一次码；平涂色块与细线在 crf 18 下就能看出色带与振铃，
# 这里先留足质量，避免两代有损叠加。
CRF = "12"
PRESET = "slow"

# 上下文亮度分区阈值（ITU-R BT.709 均值 0–255）
LUMA_DARK_MAX = 75.0
LUMA_MID_MAX = 165.0

# 渲染期间禁用的非确定性来源：调用即抛错，不静默替换成固定值——
# 替换会让「页面写错了」变成「画面不动」，后者肉眼才看得出。
GUARD_JS = """
(() => {
  const ban = (name) => () => {
    throw new Error(`motion: 渲染期间不能调用 ${name}，画面只能是 renderFrame(t) 的 t 的函数`);
  };
  Math.random = ban('Math.random');
  const RealDate = Date;
  globalThis.Date = new Proxy(RealDate, {
    construct(target, args) {
      if (!args.length) ban('new Date()')();
      return Reflect.construct(target, args);
    },
    apply() { ban('Date()')(); },
  });
  RealDate.now = ban('Date.now');
  performance.now = ban('performance.now');
  window.requestAnimationFrame = ban('requestAnimationFrame');
  window.MOTION = __MOTION__;
})();
"""

_RE_CHAP_END = re.compile(r"^##\s*章节结束(?:\s|$)")
_RE_CHAP = re.compile(r"^##\s*章节(?:\s*[·:：].*)?$")
_RE_SUB = re.compile(r"^###\s*子节\s*[·:：]\s*(.+?)\s*$")
_RE_MUSIC = re.compile(r"^##\s*音乐段\s*(.+?)\s*$")
_RE_SEG = re.compile(r"^##\s*段落\s*(\d+)\s*$")
_RE_FIELD = re.compile(r"^\s*(配音|情绪|语速|锚点|查询|备选|场景|音乐|源片原声|状态|曲名|过渡)[：:]\s*(.+?)\s*$")
_RE_THUMB = re.compile(r"^s\d+c(\d+)(?:.*-(\d+))?\.jpg$")
_RE_TOPIC_BGM_HEAD = re.compile(r"^\s*BGM\s*[:：]\s*$")
_RE_TOPIC_BGM_ITEM = re.compile(r"^-\s*(.+?)\s*$")
_RE_TOPIC_BGM_SLOT = re.compile(r"^\s*(BGM正文|BGM中段|BGM结尾)\s*[:：]\s*(.+?)\s*$")


def fail(msg: str) -> None:
    raise SystemExit(f"FAIL {msg}")


# ---------- 纯函数层（全部可脱离浏览器与媒体做单测） ----------


def frame_count(duration: float, fps: Fraction = FPS) -> int:
    """覆盖 duration 所需的帧数，向上取整：素材短了会被 04 排片末帧定格补，长了只会被截掉。"""
    return math.ceil(Fraction(str(duration)) * fps)


def script_has_chapters(script_text: str) -> bool:
    """稿件是否启用了 `## 章节` 顶部进度条（对齐 pipeline.chapters 规则）。"""
    return any(_RE_CHAP.match(line) for line in script_text.replace("\r\n", "\n").splitlines())


def _apply_music_block(pending: dict[str, str], active_bed: dict[str, str]) -> dict[str, str]:
    """按 pipeline/music.py 语义把一个已读完的 `## 音乐段` 块归并到当前背景铺底床。

    - `源片原声`（音画同源槽）：只让路，不覆盖既有背景床；
    - `状态: 背景铺底` 或 `过渡` 含 `降为 BGM` / `自然播放` / `自然结束`：接管为新背景床；
    - 前景试听且 `过渡` 含 `淡出` / `自然进入下一首`（或 `状态` 含 `前景` 且未降为 BGM）：清空当前床。
    """
    if not pending:
        return active_bed
    if "源片原声" in pending:
        return active_bed
    status = pending.get("状态", "")
    trans = pending.get("过渡", "")
    if "背景铺底" in status:
        return dict(pending)
    if any(k in trans for k in ("降为 BGM", "自然播放", "自然结束")):
        return dict(pending)
    if trans and any(k in trans for k in ("淡出", "自然进入下一首")):
        return {}
    if "前景" in status:
        return {}
    return dict(pending)


def parse_topic_bgm(topic_text: str) -> dict[str, Any] | None:
    """从 `01-topic.md` 解析普通期的 BGM 声明（`BGM:` 列表或传统 `BGM正文`/`BGM结尾` 槽位）。"""
    lines = topic_text.replace("\r\n", "\n").splitlines()
    for i, line in enumerate(lines):
        if _RE_TOPIC_BGM_HEAD.match(line):
            tracks: list[str] = []
            for follow in lines[i + 1 :]:
                s = follow.strip()
                if not s:
                    continue
                m = _RE_TOPIC_BGM_ITEM.match(s)
                if not m:
                    break
                tracks.append(m.group(1).strip())
            if tracks:
                return {
                    "heading": "01-topic.md BGM",
                    "音乐": " / ".join(tracks),
                    "状态": "普通期铺底",
                    "source": "01-topic.md",
                    "tracks": tracks,
                }
            break

    slots: list[str] = []
    for line in lines:
        if m := _RE_TOPIC_BGM_SLOT.match(line):
            slots.append(f"{m.group(1)}={m.group(2).strip()}")
    if slots:
        return {
            "heading": "01-topic.md BGM",
            "音乐": " / ".join(slots),
            "状态": "普通期铺底",
            "source": "01-topic.md",
            "tracks": slots,
        }
    return None


def parse_script_context(script_text: str, seg: int) -> dict[str, Any]:
    """从稿件文本提取第 seg 段及其前后相邻段的上下文（配乐床、章节、情绪、语速、配音、锚点）。"""
    lines = script_text.replace("\r\n", "\n").splitlines()
    cur_chapter = ""
    cur_sub = ""
    active_bed: dict[str, str] = {}
    pending_music: dict[str, str] | None = None
    has_music_blocks = False
    cur_seg_idx: int | None = None
    segs: dict[int, dict[str, Any]] = {}

    def _flush_music() -> None:
        nonlocal active_bed, pending_music
        if pending_music is not None:
            active_bed = _apply_music_block(pending_music, active_bed)
            pending_music = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _RE_CHAP_END.match(line):
            _flush_music()
            cur_seg_idx = None
            cur_chapter = ""
            cur_sub = ""
            continue
        if _RE_CHAP.match(line):
            _flush_music()
            cur_seg_idx = None
            parts = re.split(r"[·:：]", line, maxsplit=1)
            cur_chapter = parts[1].strip() if len(parts) > 1 else ""
            cur_sub = ""
            continue
        if m_sub := _RE_SUB.match(line):
            _flush_music()
            cur_sub = m_sub.group(1).strip()
            continue
        if m_mus := _RE_MUSIC.match(line):
            _flush_music()
            has_music_blocks = True
            cur_seg_idx = None
            pending_music = {"heading": m_mus.group(1).strip()}
            continue
        if m_seg := _RE_SEG.match(line):
            _flush_music()
            cur_seg_idx = int(m_seg.group(1))
            segs[cur_seg_idx] = {
                "index": cur_seg_idx,
                "chapter": cur_chapter,
                "subchapter": cur_sub,
                "music": dict(active_bed),
            }
            continue
        if line.startswith("## "):
            _flush_music()
            cur_seg_idx = None
            continue

        if m_f := _RE_FIELD.match(raw):
            k, v = m_f.group(1), m_f.group(2).strip()
            if pending_music is not None:
                pending_music[k] = v
            elif cur_seg_idx is not None:
                segs[cur_seg_idx][k] = v

    _flush_music()

    def _brief(idx: int) -> dict[str, Any] | None:
        item = segs.get(idx)
        if not item:
            return None
        return {
            "index": idx,
            "text": item.get("配音", ""),
            "emotion": item.get("情绪", ""),
            "speed": item.get("语速", ""),
            "anchor": item.get("锚点", ""),
            "query": item.get("查询", ""),
            "scene": item.get("场景", ""),
            "chapter": item.get("chapter", ""),
            "subchapter": item.get("subchapter", ""),
            "music": item.get("music", {}),
        }

    cur = _brief(seg)
    doc_has_chapters = script_has_chapters(script_text)
    seg_has_chapters = bool(cur["chapter"]) if cur is not None else doc_has_chapters

    return {
        "seg": seg,
        "has_chapters": seg_has_chapters,
        "doc_has_chapters": doc_has_chapters,
        "has_music_blocks": has_music_blocks,
        "current": cur,
        "prev": _brief(seg - 1),
        "next": _brief(seg + 1),
    }


def neighbor_clip_targets(
    clips_data: dict[str, Any],
    seg: int,
    pad: float = 3.0,
    motion_name: str | None = None,
) -> dict[str, dict[str, Any] | None]:
    """从 04-clips(.approved).json 解析切入前与切出后的真实相邻镜头时间点。

    若本段含多锚点且能定位动效片段，则优先取本段内的紧邻兄弟片段（同段音频偏移），
    否则取 seg-1 末片尾部与 seg+1 首片头部。
    """
    by_idx = {int(s["index"]): s for s in clips_data.get("segments", []) if "index" in s}
    out: dict[str, dict[str, Any] | None] = {"prev": None, "next": None, "motion_clip": None}

    cur_seg = by_idx.get(seg)
    cur_clips = (cur_seg or {}).get("clips") or []
    if len(cur_clips) > 1:
        k: int | None = None
        if motion_name:
            k = next(
                (i for i, c in enumerate(cur_clips) if Path(str(c.get("source", ""))).name == motion_name),
                None,
            )
        if k is None:
            k = next(
                (i for i, c in enumerate(cur_clips) if str(c.get("source", "")).endswith("-motion.mp4")),
                None,
            )
        if k is not None:
            before_dur = sum(float(c["dur"]) for c in cur_clips[:k])
            cur_dur = float(cur_clips[k]["dur"])
            out["motion_clip"] = {"index": k, "dur": cur_dur, "audio_offset": before_dur}
            if k > 0:
                c = cur_clips[k - 1]
                start, dur = float(c["start"]), float(c["dur"])
                sp_dur = min(float(pad), dur)
                out["prev"] = {
                    "seg": seg,
                    "same_seg": True,
                    "source": str(c["source"]),
                    "start": start,
                    "dur": dur,
                    "frame_t": start + max(0.0, dur - min(0.5, dur / 2.0)),
                    "splice_start": start + max(0.0, dur - sp_dur),
                    "splice_dur": sp_dur,
                    "audio_offset": max(0.0, before_dur - sp_dur),
                }
            if k + 1 < len(cur_clips):
                c = cur_clips[k + 1]
                start, dur = float(c["start"]), float(c["dur"])
                sp_dur = min(float(pad), dur)
                out["next"] = {
                    "seg": seg,
                    "same_seg": True,
                    "source": str(c["source"]),
                    "start": start,
                    "dur": dur,
                    "frame_t": start + min(0.5, dur / 2.0),
                    "splice_start": start,
                    "splice_dur": sp_dur,
                    "audio_offset": before_dur + cur_dur,
                }

    if out["prev"] is None:
        prev_seg = by_idx.get(seg - 1)
        if prev_seg and prev_seg.get("clips"):
            c = prev_seg["clips"][-1]
            start = float(c["start"])
            dur = float(c["dur"])
            sp_dur = min(float(pad), dur)
            out["prev"] = {
                "seg": seg - 1,
                "same_seg": False,
                "source": str(c["source"]),
                "start": start,
                "dur": dur,
                "frame_t": start + max(0.0, dur - min(0.5, dur / 2.0)),
                "splice_start": start + max(0.0, dur - sp_dur),
                "splice_dur": sp_dur,
                "audio_offset": None,
            }

    if out["next"] is None:
        next_seg = by_idx.get(seg + 1)
        if next_seg and next_seg.get("clips"):
            c = next_seg["clips"][0]
            start = float(c["start"])
            dur = float(c["dur"])
            sp_dur = min(float(pad), dur)
            out["next"] = {
                "seg": seg + 1,
                "same_seg": False,
                "source": str(c["source"]),
                "start": start,
                "dur": dur,
                "frame_t": start + min(0.5, dur / 2.0),
                "splice_start": start,
                "splice_dur": sp_dur,
                "audio_offset": 0.0,
            }
    return out


def resolve_segment_wav(
    episode: Path,
    seg_idx: int,
    manifest_data: dict[str, Any] | None = None,
) -> Path | None:
    """定位第 seg_idx 段配音 WAV：优先用 manifest.json 的 file 字段，再按零填充与非零填充回退。"""
    audio_dir = episode / "03-audio"
    if manifest_data:
        for s in manifest_data.get("segments") or []:
            if s.get("index") == seg_idx and s.get("file"):
                cand = audio_dir / str(s["file"])
                if cand.is_file():
                    return cand
    for name in (f"seg-{seg_idx:02d}.wav", f"seg-{seg_idx}.wav"):
        cand = audio_dir / name
        if cand.is_file():
            return cand
    return None


def find_fallback_thumb(thumbs_dir: Path, seg_idx: int, want_last: bool) -> Path | None:
    """在 04-thumbs/ 中定位第 seg_idx 段的缩略图（兼容零填充 s07c0 与非零填充 s153c0 并按数值排序）。"""
    if not thumbs_dir.is_dir():
        return None
    seen: set[Path] = set()
    for pat in (f"s{seg_idx:02d}c*.jpg", f"s{seg_idx}c*.jpg"):
        seen.update(thumbs_dir.glob(pat))
    if not seen:
        return None

    def _sort_key(p: Path) -> tuple[int, int, str]:
        m = _RE_THUMB.match(p.name)
        if m:
            return (int(m.group(1)), int(m.group(2) or 0), p.name)
        return (10**9, 10**9, p.name)

    ordered = sorted(seen, key=_sort_key)
    return ordered[-1] if want_last else ordered[0]


def pick_ordinary_bgm_at(
    plan: list[tuple[str, Path, float, float, float, float]],
    win_start: float,
) -> tuple[str, Path, float, float] | None:
    """从普通期 BGM 计划 [(title, path, dur, lufs, at, offset)] 选出 win_start 时刻生效的曲目与曲内起点（纯函数）。"""
    if not plan:
        return None
    chosen = plan[0]
    for entry in plan:
        if entry[4] <= win_start:
            chosen = entry
        else:
            break
    title, path, dur, lufs, at, offset = chosen
    eff = dur - offset
    elapsed = max(0.0, win_start - at)
    track_pos = (offset + (elapsed % eff)) if eff > 1e-3 else max(0.0, offset)
    return (title, path, track_pos, lufs)


def parse_cli_params(raw_params: list[str]) -> dict[str, str]:
    """解析并校验 `--param KEY=VALUE` 列表（纯函数）。"""
    out: dict[str, str] = {}
    for item in raw_params:
        if "=" not in item:
            raise ValueError(f"--param 参数缺少 '='：{item}（应为 KEY=VALUE）")
        k, v = item.split("=", 1)
        if not k.strip():
            raise ValueError(f"--param 键名不能为空：{item}")
        out[k] = v
    return out


def parse_stills_times(raw: str) -> list[float]:
    """解析并校验 `--stills` 逗号分隔时间点（纯函数）。"""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("--stills 至少需要一个时间点（秒），如 --stills 0.5,2.5,5.0")
    out: list[float] = []
    for p in parts:
        try:
            t = float(p)
        except ValueError as exc:
            raise ValueError(f"--stills 时间点不是合法数字：{p}") from exc
        if not math.isfinite(t) or t < 0:
            raise ValueError(f"--stills 时间点必须为非负有限数：{p}")
        out.append(t)
    return out


def discover_fonts(fonts_dir: Path) -> list[tuple[str, Path, bool]]:
    """根据 FONT_SPECS 探测本地字体文件，返回 (family, path, required) 列表。

    必需项即使缺失也保留在列表中，由加载层统一报出明确缺文件错；
    可选项仅在磁盘实际存在时返回，不把不存在的字体假装放进列表（S4/S7）。
    """
    found: list[tuple[str, Path, bool]] = []
    for family, candidates, required in FONT_SPECS:
        hit = next((fonts_dir / name for name in candidates if (fonts_dir / name).is_file()), None)
        if hit is not None:
            found.append((family, hit, required))
        elif required:
            found.append((family, fonts_dir / candidates[0], True))
    return found


def build_motion_payload(
    *,
    duration: float,
    sentences: list[dict[str, Any]],
    has_chapters: bool,
    params: dict[str, str],
    fonts: list[str] | None = None,
    width: int = W,
    height: int = H,
    fps: Fraction = FPS,
    sub_band: int = SUB_BAND,
    chapter_bar_h: int = CHAPTER_BAR_H,
) -> dict[str, Any]:
    """构造注入场景页的 `window.MOTION` 字典（纯函数）。"""
    return {
        "width": width,
        "height": height,
        "fps": str(fps),
        "duration": duration,
        "sentences": sentences,
        "hasChapters": bool(has_chapters),
        "chapterBarHeight": chapter_bar_h,
        "safeTop": chapter_bar_h if has_chapters else 0,
        "safeBottom": height - sub_band,
        "fonts": list(fonts or []),
        "params": dict(params),
    }


def contact_grid(count: int) -> tuple[int, int]:
    """关键帧接触表列数与行数（纯函数）：2–3 张横排，4 张 2×2，5+ 张按 3 列折行。"""
    if count <= 0:
        return (1, 1)
    if count <= 3:
        return (count, 1)
    if count == 4:
        return (2, 2)
    cols = 3
    rows = math.ceil(count / cols)
    return (cols, rows)


def analyze_frame_palette(img: Image.Image, n_colors: int = 5) -> dict[str, Any]:
    """统计单帧平均亮度（BT.709）、明暗分区与按像素占比排序的 Top-N 主色（纯函数）。"""
    rgb = img.convert("RGB").resize((160, 90), Image.Resampling.BILINEAR)
    raw = rgb.tobytes()
    n_px = len(raw) // 3
    if n_px == 0:
        return {"mean_luma": 0.0, "tone": "dark", "palette": []}
    mean_luma = (
        sum(0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2] for i in range(0, len(raw), 3))
        / n_px
    )
    tone = "dark" if mean_luma < LUMA_DARK_MAX else ("mid" if mean_luma < LUMA_MID_MAX else "light")

    quant = rgb.quantize(colors=max(1, n_colors), method=Image.Quantize.MEDIANCUT)
    raw_pal = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], key=lambda x: -x[0])
    total = sum(c for c, _ in counts) or 1
    palette = []
    for cnt, idx in counts[:n_colors]:
        base = idx * 3
        if base + 2 < len(raw_pal):
            r, g, b = raw_pal[base], raw_pal[base + 1], raw_pal[base + 2]
            palette.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "ratio": round(cnt / total, 3)})
    return {"mean_luma": round(mean_luma, 1), "tone": tone, "palette": palette}


def is_inside_patch_assets(path: Path) -> bool:
    """路径是否落在 patch_assets 目录内（兼容大小写不敏感文件系统与目录内软链）。"""
    parts = {p.casefold() for p in (*path.parts, *path.resolve().parts)}
    return "patch_assets" in parts


def _load_pil_font(size: int, font_path: Path | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cand = font_path or FONT_FILE
    if cand and cand.is_file():
        try:
            return ImageFont.truetype(str(cand), size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_px: int) -> str:
    """按渲染像素宽截断文本，防止超长曲名或台词画出图像边界。"""
    if not text or max_px <= 20:
        return text
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_px:
        return text
    lo, hi = 1, len(text)
    best = text[:1] + "…"
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid] + "…"
        b = draw.textbbox((0, 0), cand, font=font)
        if b[2] - b[0] <= max_px:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def render_contact_sheet(
    items: list[tuple[str, Image.Image]],
    out_path: Path,
    *,
    max_width: int = 1920,
    font_path: Path | None = None,
) -> Path:
    """把多张关键帧拼成带时间戳角标的接触表 PNG。"""
    if not items:
        raise ValueError("items 不能为空")
    cols, rows = contact_grid(len(items))
    gap = 10
    tile_w = (max_width - gap * (cols + 1)) // cols
    w0, h0 = items[0][1].size
    tile_h = max(1, round(tile_w * (h0 / max(1, w0))))
    sheet_w = gap + cols * (tile_w + gap)
    sheet_h = gap + rows * (tile_h + gap)
    canvas = Image.new("RGB", (sheet_w, sheet_h), (20, 22, 26))
    draw = ImageDraw.Draw(canvas)
    font = _load_pil_font(max(16, tile_w // 28), font_path)

    for idx, (label, im) in enumerate(items):
        r, c = divmod(idx, cols)
        x = gap + c * (tile_w + gap)
        y = gap + r * (tile_h + gap)
        resized = im.convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
        canvas.paste(resized, (x, y))
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 10, 6
        draw.rectangle([x + 8, y + 8, x + 8 + tw + pad_x * 2, y + 8 + th + pad_y * 2], fill=(16, 18, 22))
        draw.text((x + 8 + pad_x, y + 8 + pad_y - bbox[1]), label, fill=(244, 239, 228), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    return out_path


def render_context_sheet(
    ctx_info: dict[str, Any],
    prev_img: Image.Image | None,
    next_img: Image.Image | None,
    prev_stats: dict[str, Any] | None,
    next_stats: dict[str, Any] | None,
    out_path: Path,
    *,
    font_path: Path | None = None,
) -> Path:
    """生成上下文视觉锚定图：上方前后相邻帧 + 中段明度与 5 色主色带 + 下方配乐/情绪/台词摘要。"""
    sheet_w = 1920
    pad = 32
    tile_w = (sheet_w - pad * 3) // 2  # 912
    tile_h = round(tile_w * 9 / 16)    # 513
    header_h = 56
    swatch_h = 92
    footer_h = 210
    sheet_h = header_h + tile_h + swatch_h + footer_h + pad * 2

    canvas = Image.new("RGB", (sheet_w, sheet_h), (24, 26, 31))
    draw = ImageDraw.Draw(canvas)
    f_title = _load_pil_font(26, font_path)
    f_body = _load_pil_font(22, font_path)
    f_small = _load_pil_font(18, font_path)

    seg = ctx_info["seg"]
    cur = ctx_info.get("current") or {}
    chap_str = " / ".join(x for x in (cur.get("chapter"), cur.get("subchapter")) if x) or "无章节标记"
    max_header_px = sheet_w - pad * 2
    header_txt = _truncate_text(
        draw,
        f"CONTEXT REF · SEG {seg}   |   章节: {chap_str}   |   顶栏 safeTop={CHAPTER_BAR_H if ctx_info.get('has_chapters') else 0}px  底栏 safeBottom={H - SUB_BAND}px",
        f_title,
        max_header_px,
    )
    draw.text((pad, 14), header_txt, fill=(242, 238, 228), font=f_title)

    slots = [
        ("PREV", ctx_info.get("prev_label") or f"PREV · SEG {seg - 1}", prev_img, prev_stats, pad),
        ("NEXT", ctx_info.get("next_label") or f"NEXT · SEG {seg + 1}", next_img, next_stats, pad * 2 + tile_w),
    ]
    y_frame = header_h
    y_swatch = header_h + tile_h + 12

    for tag, slot_label, im, st, x0 in slots:
        if im is not None:
            rgb_im = im.convert("RGB")
            iw, ih = rgb_im.size
            scale = min(tile_w / max(1, iw), tile_h / max(1, ih))
            nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
            fitted = rgb_im.resize((nw, nh), Image.Resampling.LANCZOS)
            draw.rectangle([x0, y_frame, x0 + tile_w, y_frame + tile_h], fill=(16, 18, 22))
            canvas.paste(fitted, (x0 + (tile_w - nw) // 2, y_frame + (tile_h - nh) // 2))
        else:
            draw.rectangle([x0, y_frame, x0 + tile_w, y_frame + tile_h], fill=(38, 41, 48), outline=(90, 95, 106), width=2)
            draw.text((x0 + 28, y_frame + tile_h // 2 - 14), f"{slot_label} 无可用画面帧（尚未排片或无相邻段）", fill=(180, 185, 195), font=f_body)

        badge = slot_label
        if st:
            badge += f"  |  luma={st['mean_luma']} ({st['tone']})"
        badge = _truncate_text(draw, badge, f_small, tile_w - 40)
        bb = draw.textbbox((0, 0), badge, font=f_small)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        draw.rectangle([x0 + 10, y_frame + 10, x0 + 10 + bw + 20, y_frame + 10 + bh + 14], fill=(16, 18, 22))
        draw.text((x0 + 20, y_frame + 17 - bb[1]), badge, fill=(244, 239, 228), font=f_small)

        # 5 色色卡条
        pal = (st or {}).get("palette") or []
        if pal:
            sw_w = tile_w // len(pal)
            for k, entry in enumerate(pal):
                hx = entry["hex"]
                rgb = tuple(int(hx[j : j + 2], 16) for j in (1, 3, 5))
                sx0 = x0 + k * sw_w
                sx1 = x0 + tile_w if k == len(pal) - 1 else sx0 + sw_w - 4
                draw.rectangle([sx0, y_swatch, sx1, y_swatch + 46], fill=rgb, outline=(60, 64, 72), width=1)
                draw.text((sx0 + 6, y_swatch + 52), f"{hx} ({int(entry['ratio']*100)}%)", fill=(205, 210, 218), font=f_small)
        else:
            draw.text((x0 + 6, y_swatch + 22), "主色未提取", fill=(140, 145, 155), font=f_small)

    # 底部配乐、情绪、配音摘要
    y_foot = y_swatch + swatch_h + 12
    draw.rectangle([pad, y_foot, sheet_w - pad, y_foot + footer_h - 10], fill=(32, 35, 42), outline=(66, 72, 84), width=2)
    max_foot_px = sheet_w - pad * 2 - 40
    mus = cur.get("music") or {}
    mus_line = "  |  ".join(x for x in (mus.get("heading"), mus.get("音乐") or mus.get("源片原声"), mus.get("状态")) if x) or "（无当前背景配乐床）"
    dur_val = ctx_info.get("duration")
    sent_cnt = len(ctx_info.get("sentences") or [])
    dur_tag = f"   时长: {dur_val:.2f}s ({sent_cnt} 句)" if isinstance(dur_val, (int, float)) else ""
    emo_line = f"情绪: {cur.get('emotion') or '未标'}   语速: {cur.get('speed') or '未标'}{dur_tag}   锚点: {cur.get('anchor') or '未标'}"
    vo_text = cur.get("text") or "（稿件未读到本段配音）"
    prev_a = (ctx_info.get("prev") or {}).get("anchor") or "—"
    next_a = (ctx_info.get("next") or {}).get("anchor") or "—"
    draw.text((pad + 20, y_foot + 18), _truncate_text(draw, f"音乐段: {mus_line}", f_body, max_foot_px), fill=(240, 198, 108), font=f_body)
    draw.text((pad + 20, y_foot + 60), _truncate_text(draw, emo_line, f_body, max_foot_px), fill=(180, 212, 232), font=f_body)
    draw.text((pad + 20, y_foot + 106), _truncate_text(draw, f"配音: {vo_text}", f_body, max_foot_px), fill=(244, 240, 232), font=f_body)
    draw.text((pad + 20, y_foot + 152), _truncate_text(draw, f"相邻锚点: PREV={prev_a}   |   NEXT={next_a}", f_small, max_foot_px), fill=(165, 172, 184), font=f_small)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    return out_path


# ---------- I/O、媒体与无头浏览器执行层 ----------


def _episode_script_text(episode: Path) -> str:
    for name in ("02-script.md", "02-script.draft.md"):
        p = episode / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def _episode_topic_text(episode: Path) -> str:
    p = episode / "01-topic.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _load_episode_manifest(episode: Path) -> dict[str, Any] | None:
    mf = episode / "03-audio" / "manifest.json"
    if not mf.is_file():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_episode_clips(episode: Path) -> dict[str, Any] | None:
    for name in ("04-clips.approved.json", "04-clips.json"):
        p = episode / name
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def _extract_video_frame(source: Path, t: float) -> Image.Image | None:
    if not source.is_file():
        return None
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(source),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    from io import BytesIO
    return Image.open(BytesIO(r.stdout)).convert("RGB")


def _fallback_thumb(episode: Path, seg_idx: int, want_last: bool) -> Image.Image | None:
    pick = find_fallback_thumb(episode / "04-thumbs", seg_idx, want_last=want_last)
    if pick is None:
        return None
    try:
        return Image.open(pick).convert("RGB")
    except OSError:
        return None


def run_context(episode: Path, seg: int, out_dir: Path) -> int:
    """提取相邻段落真实画面、主色/亮度、配乐情绪与配音句轴，产出视觉上下文参考图与 JSON。"""
    script_text = _episode_script_text(episode)
    ctx_info = parse_script_context(script_text, seg)

    # 普通期无 `## 音乐段` 时，回退读取 01-topic.md 的 BGM 声明（稿件有 `## 音乐段` 时只认稿件）
    if not ctx_info.get("has_music_blocks"):
        topic_bgm = parse_topic_bgm(_episode_topic_text(episode))
        if topic_bgm:
            ctx_info["topic_bgm"] = topic_bgm
            if ctx_info.get("current") is not None and not ctx_info["current"].get("music"):
                ctx_info["current"]["music"] = dict(topic_bgm)

    # 若 03-audio/manifest.json 已存在，顺带提取本段实测时长与分句起点写入 context-segN.json，
    # 供 AVA 在不直接访问 03-audio/ 的前提下精确对齐分句节奏与 --stills 时刻。
    manifest_data = _load_episode_manifest(episode)
    seg_duration: float | None = None
    seg_sentences: list[dict[str, Any]] = []
    if manifest_data:
        hit = next((s for s in manifest_data.get("segments") or [] if s.get("index") == seg), None)
        if hit is not None:
            seg_duration = float(hit["duration"])
            seg_sentences = [
                {"text": x["text"], "start": float(x["start"]), "duration": float(x["duration"])}
                for x in hit.get("sentences") or []
            ]
    ctx_info["duration"] = seg_duration
    ctx_info["sentences"] = seg_sentences

    clips_data = _load_episode_clips(episode)
    targets = neighbor_clip_targets(clips_data, seg) if clips_data else {"prev": None, "next": None, "motion_clip": None}

    frames: dict[str, Image.Image | None] = {"prev": None, "next": None}
    stats: dict[str, dict[str, Any] | None] = {"prev": None, "next": None}
    sources_used: dict[str, str] = {"prev": "unavailable", "next": "unavailable"}

    for key, want_last in (("prev", True), ("next", False)):
        tgt = targets[key]
        img = None
        if tgt is not None:
            if tgt.get("same_seg"):
                ctx_info[f"{key}_label"] = f"{key.upper()} · SEG {seg} (同段相邻镜头)"
            src_path = paths.ROOT / Path(tgt["source"])
            img = _extract_video_frame(src_path, tgt["frame_t"])
            if img is not None:
                tag = "同段兄弟镜头 " if tgt.get("same_seg") else ""
                sources_used[key] = f"{tag}{tgt['source']} @ {tgt['frame_t']:.2f}s"
        if img is None:
            neighbor_idx = seg - 1 if key == "prev" else seg + 1
            img = _fallback_thumb(episode, neighbor_idx, want_last=want_last)
            if img is not None:
                sources_used[key] = f"04-thumbs (seg {neighbor_idx})"
        frames[key] = img
        if img is not None:
            stats[key] = analyze_frame_palette(img)

    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"context-seg{seg}.png"
    json_path = out_dir / f"context-seg{seg}.json"

    render_context_sheet(ctx_info, frames["prev"], frames["next"], stats["prev"], stats["next"], png_path)
    payload = {
        **ctx_info,
        "safeTop": CHAPTER_BAR_H if ctx_info["has_chapters"] else 0,
        "safeBottom": H - SUB_BAND,
        "neighbors": {
            "prev": {"source": sources_used["prev"], "stats": stats["prev"]},
            "next": {"source": sources_used["next"], "stats": stats["next"]},
        },
        "sheet": str(png_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {png_path}")
    print(f"[OK] {json_path}")
    if seg_duration is not None:
        beats = ", ".join(f"{s['start']:.2f}s" for s in seg_sentences) if seg_sentences else "无分句明细"
        print(f"    TIME: {seg_duration:.3f}s ({len(seg_sentences)} 句，起点: {beats})")
    else:
        print("    TIME: 尚未配音（缺少 03-audio/manifest.json）")
    for key in ("prev", "next"):
        st = stats[key]
        if st:
            sw = " ".join(p["hex"] for p in st["palette"])
            print(f"    {key.upper():4s}: {sources_used[key]}  luma={st['mean_luma']} ({st['tone']})  {sw}")
        else:
            print(f"    {key.upper():4s}: 无可用相邻画面帧")
    return 0


def _media_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True,
    ).strip()
    return float(out)


def _normalize_video_piece(src: Path, start: float, dur: float, dest: Path) -> None:
    # tpad=stop_mode=clone 防止源片尾端短于 dur 时视频提前截断导致后段音画漂移
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{max(0.0, start):.3f}", "-i", str(src),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,tpad=stop_mode=clone:stop_duration={dur:.3f},fps={FPS}",
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-an",
        str(dest),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"连切视频段归一化失败 ({src.name}): {r.stderr.strip()}")


def _normalize_audio_piece(wav: Path | None, start: float, dur: float, dest: Path) -> None:
    if wav is not None and wav.is_file():
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-ss", f"{max(0.0, start):.3f}", "-i", str(wav), "-t", f"{dur:.3f}",
            "-af", f"apad=whole_dur={dur:.3f}", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            str(dest),
        ]
    else:
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{dur:.3f}",
            "-c:a", "pcm_s16le", str(dest),
        ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"连切音轨准备失败: {r.stderr.strip()}")


def _resolve_splice_bgm(
    episode: Path,
    seg: int,
    prev_dur: float,
    manifest_data: dict[str, Any] | None,
) -> tuple[str, Path, float, float] | None:
    """解析连切预览窗口对应的参考 BGM：(曲名, 音频绝对路径, 曲内起点秒, 实测 LUFS)。"""
    script_text = _episode_script_text(episode)
    ctx_info = parse_script_context(script_text, seg)

    # 1. 含 `## 音乐段` 的稿件：复用 pipeline.music.build_timeline 精确对齐到毫秒
    if ctx_info.get("has_music_blocks") and manifest_data:
        try:
            from pipeline import music as music_mod
            bgm_db = music_mod.load_tracks_multi(episode)
            script_path = episode / "02-script.md"
            if not script_path.is_file():
                script_path = episode / "02-script.draft.md"
            tl = music_mod.build_timeline(episode, manifest_data, bgm_db, script=script_path)
            seg_entry = next((s for s in tl["segments"] if s["index"] == seg), None)
            if seg_entry is None:
                return None
            seg_start = float(seg_entry["start"])
            win_start = max(0.0, seg_start - prev_dur)
            for tr in tl["tracks"]:
                for ev in tr["events"]:
                    at0 = float(ev["at"])
                    at1 = at0 + (float(ev["t1"]) - float(ev["t0"]))
                    if at0 <= seg_start < at1:
                        track_pos = max(0.0, float(ev["t0"]) + (win_start - at0))
                        return (
                            str(tr["name"]),
                            paths.ROOT / Path(tr["path"]),
                            track_pos,
                            float(tr.get("lufs") if tr.get("lufs") is not None else -14.0),
                        )
        except (SystemExit, OSError, KeyError, ValueError) as exc:
            print(f"[WARN] --splice-bgm 解析音乐段失败，回退纯人声：{exc}")
            return None
        return None

    # 2. 普通期（无 `## 音乐段`）：先查 `BGM:` 列表，再回退 `BGM正文:` 槽位与番级默认正文曲，
    #    并按 manifest.json 前序段落累计时长换算该段对应的曲内位置。
    try:
        from pipeline import bgm as bgm_mod
        anime = bgm_mod.anime_of(episode)
        if not anime:
            return None
        pools = list(dict.fromkeys([anime] + bgm_mod.animes_of(episode)))
        seg_start = 0.0
        if manifest_data:
            seg_start = sum(
                float(s["duration"])
                for s in manifest_data.get("segments") or []
                if int(s.get("index", 10**9)) < seg
            )
        win_start = max(0.0, seg_start - prev_dur)

        plan: list[tuple[str, Path, float, float, float, float]] = []
        seq = bgm_mod.topic_bgm_list(episode)
        if seq:
            for title, at, offset in seq:
                rec = bgm_mod.resolve(pools, "列表", title)
                if not rec:
                    continue
                t_path = paths.ROOT / Path(rec["path"])
                if not t_path.is_file():
                    continue
                t_dur = float(rec.get("dur") or 0.0)
                t_lufs = float(rec["lufs"]) if rec.get("lufs") is not None else -14.0
                t_off = float(offset or 0.0)
                if at is None:
                    at = plan[-1][4] + max(0.0, plan[-1][2] - plan[-1][5]) if plan else 0.0
                plan.append((title, t_path, t_dur, t_lufs, float(at), t_off))
        else:
            choice = bgm_mod.episode_choice(episode, "正文")
            rec = bgm_mod.resolve(pools, "正文", choice)
            if rec:
                t_path = paths.ROOT / Path(rec["path"])
                if t_path.is_file():
                    t_dur = float(rec.get("dur") or 0.0)
                    t_lufs = float(rec["lufs"]) if rec.get("lufs") is not None else -14.0
                    plan.append((choice or t_path.stem, t_path, t_dur, t_lufs, 0.0, 0.0))

        return pick_ordinary_bgm_at(plan, win_start)
    except (SystemExit, OSError, KeyError, ValueError) as exc:
        print(f"[WARN] --splice-bgm 解析 01-topic.md BGM 失败，回退纯人声：{exc}")
    return None


def _prepare_bgm_bed(track_path: Path, track_start: float, total_dur: float, lufs: float, dest: Path) -> bool:
    """切出参考 BGM 并按实测 LUFS 压到 -28 LUFS（与成片人声下铺底接近），首尾加 0.25s 淡入淡出。"""
    gain_db = max(-36.0, min(0.0, -28.0 - lufs))
    fade_out_st = max(0.0, total_dur - 0.25)
    af = (
        f"volume={gain_db:.1f}dB,"
        f"afade=t=in:st=0:d=0.25,"
        f"afade=t=out:st={fade_out_st:.3f}:d=0.25,"
        f"apad=whole_dur={total_dur:.3f}"
    )
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{max(0.0, track_start):.3f}", "-i", str(track_path), "-t", f"{total_dur:.3f}",
        "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
        str(dest),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and dest.is_file()


def run_splice_preview(
    episode: Path,
    seg: int,
    motion_mp4: Path,
    out_mp4: Path,
    pad: float = 3.0,
    include_bgm: bool = False,
) -> int:
    """把切入前镜头 + 本段动效（配原声）+ 切出后镜头拼成连切预览视频，并自动产 4 格接缝抽帧图。"""
    if not motion_mp4.is_file():
        fail(f"找不到动效视频 {motion_mp4}")
    if is_inside_patch_assets(out_mp4):
        fail(f"--splice 预览视频不能落盘到 patch_assets/（会污染补丁池）：{out_mp4}")

    clips_data = _load_episode_clips(episode)
    if not clips_data:
        fail(f"{episode} 下找不到 04-clips(.approved).json，无法自动切出相邻镜头")
    manifest_data = _load_episode_manifest(episode)
    targets = neighbor_clip_targets(clips_data, seg, pad=pad, motion_name=motion_mp4.name)
    motion_dur = _media_duration(motion_mp4)
    motion_clip = targets.get("motion_clip")
    motion_audio_offset = float(motion_clip["audio_offset"]) if motion_clip else 0.0
    if motion_clip and targets["next"] and targets["next"].get("same_seg"):
        # 多锚点段内后接同段兄弟镜头：按 04 实际分配时长切出，还原真实切缝
        motion_play_dur = min(motion_dur, float(motion_clip["dur"]))
    else:
        motion_play_dur = motion_dur

    # (role, label, video_src, v_start, dur, wav_src_or_none, a_start)
    pieces: list[tuple[str, str, Path, float, float, Path | None, float]] = []
    if prev := targets["prev"]:
        src = paths.ROOT / Path(prev["source"])
        if src.is_file():
            wav = resolve_segment_wav(episode, int(prev["seg"]), manifest_data)
            if wav is None:
                print(f"[WARN] 未找到段落 {prev['seg']} 配音 WAV，连切前段使用静音占位")
                a_start = 0.0
            elif prev.get("same_seg"):
                a_start = float(prev.get("audio_offset") or 0.0)
            else:
                w_dur = _media_duration(wav)
                a_start = max(0.0, w_dur - prev["splice_dur"])
            lbl = f"SEG {prev['seg']} (同段前镜)" if prev.get("same_seg") else f"SEG {prev['seg']}"
            pieces.append(("prev", lbl, src, prev["splice_start"], prev["splice_dur"], wav, a_start))

    cur_wav = resolve_segment_wav(episode, seg, manifest_data)
    if cur_wav is None:
        print(f"[WARN] 未找到段落 {seg} 配音 WAV，连切本段使用静音占位")
    pieces.append(("motion", f"SEG {seg} (MOTION)", motion_mp4, 0.0, motion_play_dur, cur_wav, motion_audio_offset))

    if nxt := targets["next"]:
        src = paths.ROOT / Path(nxt["source"])
        if src.is_file():
            wav = resolve_segment_wav(episode, int(nxt["seg"]), manifest_data)
            if wav is None:
                print(f"[WARN] 未找到段落 {nxt['seg']} 配音 WAV，连切后段使用静音占位")
            a_start = float(nxt.get("audio_offset") or 0.0)
            lbl = f"SEG {nxt['seg']} (同段后镜)" if nxt.get("same_seg") else f"SEG {nxt['seg']}"
            pieces.append(("next", lbl, src, nxt["splice_start"], nxt["splice_dur"], wav, a_start))

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    seams_png = out_mp4.with_name(f"{out_mp4.stem}-seams.png")
    prev_dur = next((p[4] for p in pieces if p[0] == "prev"), 0.0)

    with tempfile.TemporaryDirectory(prefix="ava_splice_") as td:
        tmp = Path(td)
        v_files: list[Path] = []
        a_files: list[Path] = []
        timeline: list[str] = []
        seam_items: list[tuple[str, Image.Image]] = []
        cursor = 0.0
        for idx, (role, label, v_src, v_start, dur, wav_src, a_start) in enumerate(pieces):
            vp = tmp / f"v_{idx}.mp4"
            ap = tmp / f"a_{idx}.wav"
            _normalize_video_piece(v_src, v_start, dur, vp)
            _normalize_audio_piece(wav_src, a_start, dur, ap)
            v_files.append(vp)
            a_files.append(ap)
            timeline.append(f"{cursor:.2f}–{cursor + dur:.2f}s {label}")
            cursor += dur

            # 从归一化后的临时切片提取接缝帧，保证竖屏/异构源片与成片比例一致
            if role == "prev":
                im = _extract_video_frame(vp, max(0.0, dur - 0.12))
                if im is not None:
                    seam_items.append((f"PREV 尾 ({label})", im))
            elif role == "motion":
                im_head = _extract_video_frame(vp, min(0.12, dur * 0.25))
                im_tail = _extract_video_frame(vp, max(0.0, dur - 0.12))
                if im_head is not None:
                    seam_items.append((f"MOTION 首 ({label})", im_head))
                if im_tail is not None:
                    seam_items.append((f"MOTION 尾 ({label})", im_tail))
            elif role == "next":
                im = _extract_video_frame(vp, min(0.12, dur * 0.25))
                if im is not None:
                    seam_items.append((f"NEXT 首 ({label})", im))

        # 使用同目录相对文件名，避免 TMPDIR 含单引号时触发 ffmpeg concat 解析失败
        v_list = tmp / "v.txt"
        a_list = tmp / "a.txt"
        v_list.write_text("\n".join(f"file '{p.name}'" for p in v_files), encoding="utf-8")
        a_list.write_text("\n".join(f"file '{p.name}'" for p in a_files), encoding="utf-8")

        bgm_note = ""
        bgm_wav = tmp / "bgm_bed.wav"
        use_bgm_file: Path | None = None
        if include_bgm:
            bgm_hit = _resolve_splice_bgm(episode, seg, prev_dur, manifest_data)
            if bgm_hit is not None:
                title, track_path, track_pos, lufs = bgm_hit
                if _prepare_bgm_bed(track_path, track_pos, cursor, lufs, bgm_wav):
                    use_bgm_file = bgm_wav
                    bgm_note = f"  [BGM: {title} @{track_pos:.1f}s]"
                else:
                    print(f"[WARN] --splice-bgm 切轨失败（{title}），回退纯人声")
            else:
                print("[WARN] --splice-bgm 未找到本段对应的可用 BGM，回退纯人声")

        if use_bgm_file is not None:
            vo_full = tmp / "vo_full.wav"
            r_vo = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(a_list),
                 "-c:a", "pcm_s16le", str(vo_full)],
                capture_output=True, text=True,
            )
            if r_vo.returncode != 0:
                fail(f"连切人声合并失败: {r_vo.stderr.strip()}")
            cmd = [
                "ffmpeg", "-v", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(v_list),
                "-i", str(vo_full), "-i", str(use_bgm_file),
                "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(out_mp4),
            ]
        else:
            cmd = [
                "ffmpeg", "-v", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(v_list),
                "-f", "concat", "-safe", "0", "-i", str(a_list),
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(out_mp4),
            ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"连切预览合成失败: {r.stderr.strip()}")

        if seam_items:
            render_contact_sheet(seam_items, seams_png)
            print(f"[OK] 接缝抽帧 {seams_png}  ({len(seam_items)} 帧)")

    print(f"[OK] 连切预览 {out_mp4}  (" + " | ".join(timeline) + f"){bgm_note}")
    return 0


def segment_timing(episode: Path, seg: int) -> tuple[float, list[dict]]:
    """第 seg 段配音的总时长与逐句起点（03 按句合成后 ffprobe 复核过的值）。"""
    mf = episode / "03-audio" / "manifest.json"
    if not mf.is_file():
        fail(f"找不到 {mf}（--episode 要指向已配音的期目录）")
    segs = [s for s in json.loads(mf.read_text(encoding="utf-8"))["segments"] if s["index"] == seg]
    if not segs:
        fail(f"{mf} 里没有段落 {seg}")
    s = segs[0]
    sentences = [{"text": x["text"], "start": x["start"], "duration": x["duration"]}
                 for x in s.get("sentences") or []]
    return s["duration"], sentences


def open_scene(p, scene: Path, motion: dict, debug: bool):
    """起无头浏览器、装好禁令与运行时（Canvas 2D + WebGL2）、载入场景页与字体栈，返回 (browser, page, errors)。"""
    errors: list[str] = []
    font_entries = discover_fonts(paths.DATA / "fonts")
    for _fam, fpath, required in font_entries:
        if required and not fpath.is_file():
            fail(f"找不到必需字体 {fpath}")

    # 在 add_init_script 前预填预期可用字体族，确保顶层 <script> 探测 MOTION.fonts 时不为空数组；
    # 实际 FontFace 加载完毕后再同步精确列表，并调用可选 initScene() 钩子。
    available_fonts = [fam for fam, fpath, _ in font_entries if fpath.is_file()]
    init_motion = {**motion, "fonts": available_fonts}

    browser = p.chromium.launch(args=["--allow-file-access-from-files"])
    try:
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)

        def on_route(route):
            url = route.request.url
            if url.startswith(("file:", "data:", "blob:")):
                route.continue_()
            else:
                errors.append(f"联网请求 {url}")
                route.abort()

        page.route("**/*", on_route)
        page.on("pageerror", lambda e: errors.append(f"页面异常 {e}"))
        page.add_init_script(GUARD_JS.replace("__MOTION__", json.dumps(init_motion, ensure_ascii=False)))
        page.add_init_script(RUNTIME_JS.read_text(encoding="utf-8"))
        if GL_JS.is_file():
            page.add_init_script(GL_JS.read_text(encoding="utf-8"))
        page.goto(scene.resolve().as_uri(), wait_until="load")
        page.add_style_tag(content="html,body{margin:0;overflow:hidden}")

        loaded_fonts: list[str] = []
        for family, fpath, required in font_entries:
            if not fpath.is_file():
                continue
            status = page.evaluate(
                """async ([family, url]) => {
                    const f = new FontFace(family, `url(${url})`);
                    try { await f.load(); } catch (e) { return String(e); }
                    document.fonts.add(f);
                    return f.status;
                }""",
                [family, fpath.resolve().as_uri()],
            )
            if status == "loaded":
                loaded_fonts.append(family)
            elif required:
                fail(f"字体 {fpath} 没加载上：{status}")

        from playwright.sync_api import Error as PlaywrightError

        try:
            page.evaluate(
                """async (fonts) => {
                    window.MOTION.fonts = fonts;
                    if (document.fonts && document.fonts.ready) {
                        await document.fonts.ready;
                    }
                    if (typeof initScene === 'function') {
                        await initScene();
                    }
                }""",
                loaded_fonts,
            )
        except PlaywrightError as e:
            fail(f"initScene 出错：{str(e).splitlines()[0]}")

        if errors:                         # 加载期或 initScene 违规（如调了 requestAnimationFrame），先报它
            fail("；".join(errors))
        if not page.evaluate("typeof renderFrame === 'function'"):
            fail(f"{scene} 没有定义全局 renderFrame(t)")
        if debug:
            page.evaluate(
                """([subBand, safeTop]) => {
                    const b = document.createElement('div');
                    b.style.cssText = `position:fixed;left:0;right:0;bottom:0;height:${subBand}px;` +
                        'background:rgba(255,0,80,.22);border-top:2px dashed #ff0050;z-index:2147483647;' +
                        'color:#ff0050;font:700 14px monospace;padding:6px 12px;box-sizing:border-box;pointer-events:none';
                    b.textContent = `SUBTITLE SAFE BAND (${subBand}px)`;
                    document.body.appendChild(b);
                    if (safeTop > 0) {
                        const top = document.createElement('div');
                        top.style.cssText = `position:fixed;left:0;right:0;top:0;height:${safeTop}px;` +
                            'background:rgba(255,180,0,.22);border-bottom:2px dashed #ffb400;z-index:2147483647;' +
                            'color:#ffb400;font:700 14px monospace;padding:6px 12px;box-sizing:border-box;pointer-events:none';
                        top.textContent = `CHAPTER HUD SAFE BAND (${safeTop}px)`;
                        document.body.appendChild(top);
                    }
                }""",
                [SUB_BAND, init_motion.get("safeTop", 0)],
            )
        if errors:
            fail("；".join(errors))
    except BaseException:
        browser.close()
        raise
    return browser, page, errors


def shoot(page, errors: list[str], t: float) -> bytes:
    from playwright.sync_api import Error as PlaywrightError

    try:
        page.evaluate("t => renderFrame(t)", t)
    except PlaywrightError as e:       # renderFrame 里抛的错（含禁令）：报页面的原话，不出 Python 堆栈
        fail(f"t={t:.3f}s：renderFrame 出错：{str(e).splitlines()[0]}")
    if errors:
        fail(f"t={t:.3f}s：" + "；".join(errors))

    # WebGL2 同步保底：检查上下文丢失并强制 flush，避免 SwiftShader 延迟出帧导致黑帧或复现漂移
    gl_issue = page.evaluate(
        """() => {
            const list = window.__MOTION_GL_CONTEXTS__ || [];
            for (const gl of list) {
                if (gl.isContextLost && gl.isContextLost()) return 'WebGL 上下文丢失';
                if (gl.finish) gl.finish();
            }
            return null;
        }"""
    )
    if gl_issue:
        fail(f"t={t:.3f}s：{gl_issue}")

    n = page.evaluate("document.getAnimations().length")
    if n:
        fail(f"t={t:.3f}s：页面里有 {n} 个 CSS 动画 / 过渡；画面只能由 renderFrame(t) 设定")
    return page.screenshot(type="png")


def render_video(p, scene: Path, motion: dict, n: int, out: Path) -> list[str]:
    """逐帧截图直接送 ffmpeg 合成，返回每帧 PNG 的 sha256（复现检查用）。"""
    browser, page, errors = open_scene(p, scene, motion, debug=False)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-c:v", "png",
           "-framerate", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p",
           "-an", "-movflags", "+faststart", "-f", "mp4", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    hashes = []
    write_err: OSError | None = None
    try:
        for i in range(n):
            png = shoot(page, errors, float(i / FPS))
            hashes.append(hashlib.sha256(png).hexdigest())
            try:
                assert enc.stdin is not None
                enc.stdin.write(png)
            except OSError as exc:
                write_err = exc
                break
            if i % 48 == 0:
                print(f"    帧 {i}/{n}", flush=True)
    finally:
        try:
            if enc.stdin and not enc.stdin.closed:
                enc.stdin.close()
        except OSError:
            pass
        rc = enc.wait()
        browser.close()
    if rc != 0 or write_err is not None:
        detail = f"（管道中断：{write_err}）" if write_err else ""
        fail(f"ffmpeg 合成失败，退出码 {rc}{detail}")
    return hashes


def frame_hashes(p, scene: Path, motion: dict, n: int) -> list[str]:
    browser, page, errors = open_scene(p, scene, motion, debug=False)
    try:
        return [hashlib.sha256(shoot(page, errors, float(i / FPS))).hexdigest() for i in range(n)]
    finally:
        browser.close()


def check_output(out: Path, n: int) -> None:
    """产物合约：分辨率、帧率、帧数、无音轨、无黑场。任何一项不过就失败退出。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate,nb_read_packets", "-of", "json", str(out)],
        capture_output=True, text=True, check=True)
    streams = json.loads(r.stdout)["streams"]
    video = [s for s in streams if s["codec_type"] == "video"]
    problems = []
    if len(video) != 1 or len(streams) != 1:
        problems.append(f"流应只有一条视频，实际 {[s['codec_type'] for s in streams]}")
    if video:
        v = video[0]
        if (v["width"], v["height"]) != (W, H):
            problems.append(f"分辨率 {v['width']}x{v['height']}，应为 {W}x{H}")
        if Fraction(v["r_frame_rate"]) != FPS:
            problems.append(f"帧率 {v['r_frame_rate']}，应为 {FPS}")
        if int(v["nb_read_packets"]) != n:
            problems.append(f"帧数 {v['nb_read_packets']}，应为 {n}")
    # 与 07 质检同一组参数（qc.py：blackdetect d=BLACK_MAX pic_th=0.98）。
    # 07 对 SP 素材放宽到 BLACK_MAX_SP，这里按更严的 BLACK_MAX 卡：生成画面没有理由出黑场。
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(out), "-vf",
                        f"blackdetect=d={BLACK_MAX}:pic_th=0.98", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        problems.append(f"blackdetect 跑不动，退出码 {r.returncode}")
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
    if blacks:
        problems.append(
            "黑场 " + ", ".join(f"{a}–{b}s" for a, b in blacks)
            + "（暗调底色低于 #1c1e26 / luma<28 且亮像素占比不足 2% 会触发 blackdetect，可抬高底色或加氛围微光）"
        )
    if problems:
        fail(f"{out.name} 不合约：" + "；".join(problems))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scene", nargs="?", type=Path, help="场景页 HTML（仅跑 --context 或独立 --splice 时可省）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--episode", type=Path, help="期目录；配合 --seg 读该段配音时长与逐句起点")
    src.add_argument("--duration", type=float, help="不跟配音走时直接给时长（秒），如片头")
    ap.add_argument("--seg", type=int, help="段落号（--episode 时必填）")
    ap.add_argument("--chapters", action="store_true", help="--duration 模式下显式启用顶部 64px 章节安全带")
    ap.add_argument("--context", type=Path, help="仅提取相邻段落画面、主色与配乐情绪，输出参考图与 JSON 到此目录")
    ap.add_argument("--motion", type=Path, help="配合 --splice 独立生成连切预览时指定已有 *-motion.mp4")
    ap.add_argument("--splice", type=Path, help="输出前后连切预览 .mp4 与 <名>-seams.png 接缝图（禁止落在 patch_assets/）")
    ap.add_argument("--splice-pad", type=float, default=3.0, help="连切预览截取前后相邻段的秒数（默认 3.0）")
    ap.add_argument("--splice-bgm", action="store_true", help="连切预览同时混入低音量参考 BGM 铺底（按当前音乐段或 01-topic 匹配）")
    ap.add_argument("--out", type=Path, help="产物路径，文件名须以 -motion.mp4 结尾")
    ap.add_argument("--stills", help="只出这些时刻（秒，逗号分隔）的关键帧 PNG，不合成视频")
    ap.add_argument("--stills-dir", type=Path, help="关键帧 PNG 输出目录")
    ap.add_argument("--no-contact-sheet", action="store_true", help="配合 --stills 跳过自动接触表合成")
    ap.add_argument("--debug", action="store_true", help="关键帧上叠上下安全区参考带（只配合 --stills）")
    ap.add_argument("--verify-repro", action="store_true", help="再渲一遍，逐帧比对截图哈希")
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                    help="传给页面 MOTION.params，同一场景出变体用，可重复")
    a = ap.parse_args()

    if a.splice_pad <= 0 or not math.isfinite(a.splice_pad):
        fail(f"--splice-pad 必须大于 0（当前 {a.splice_pad}）")

    # 模式 1：上下文参考抽帧（开工前置，不需要 HTML）
    if a.context is not None:
        if a.episode is None or a.seg is None:
            fail("--context 必须配 --episode 和 --seg")
        return run_context(a.episode, a.seg, a.context)

    # 模式 2：对已有动效成片单独生成前后连切预览（不需要 HTML）
    if a.splice is not None and a.scene is None:
        if a.episode is None or a.seg is None or a.motion is None:
            fail("独立跑 --splice 必须配 --episode、--seg 与 --motion")
        return run_splice_preview(
            a.episode, a.seg, a.motion, a.splice, pad=a.splice_pad, include_bgm=a.splice_bgm
        )

    if a.scene is None:
        fail("缺少场景页 HTML 路径（只有 --context 或独立 --splice 模式可省略）")
    if not a.scene.is_file():
        fail(f"找不到场景页 {a.scene}")

    if a.episode is not None:
        if a.seg is None:
            fail("--episode 要配 --seg")
        duration, sentences = segment_timing(a.episode, a.seg)
        ctx = parse_script_context(_episode_script_text(a.episode), a.seg)
        has_chapters = bool(ctx["has_chapters"])
    else:
        if a.duration is None or not math.isfinite(a.duration) or a.duration <= 0:
            fail(f"--duration 必须大于 0（当前 {a.duration}）")
        duration, sentences = a.duration, []
        has_chapters = bool(a.chapters)

    try:
        params = parse_cli_params(a.param)
    except ValueError as exc:
        fail(str(exc))
    motion = build_motion_payload(
        duration=duration,
        sentences=sentences,
        has_chapters=has_chapters,
        params=params,
    )
    n = frame_count(duration)

    from playwright.sync_api import sync_playwright

    # 模式 3：关键帧与自动接触表
    if a.stills:
        if not a.stills_dir:
            fail("--stills 要配 --stills-dir")
        try:
            times = parse_stills_times(a.stills)
        except ValueError as exc:
            fail(str(exc))
        a.stills_dir.mkdir(parents=True, exist_ok=True)
        sheet_items: list[tuple[str, Image.Image]] = []
        with sync_playwright() as p:
            browser, page, errors = open_scene(p, a.scene, motion, debug=a.debug)
            try:
                from io import BytesIO
                for t in times:
                    raw_png = shoot(page, errors, t)
                    dest = a.stills_dir / f"{a.scene.stem}-t{t:06.3f}.png"
                    dest.write_bytes(raw_png)
                    print(f"[OK] {dest}")
                    if not a.no_contact_sheet and len(times) >= 2:
                        sheet_items.append((f"t={t:.2f}s", Image.open(BytesIO(raw_png)).convert("RGB")))
            finally:
                browser.close()
        if sheet_items:
            sheet_path = a.stills_dir / f"{a.scene.stem}-contact.png"
            render_contact_sheet(sheet_items, sheet_path)
            print(f"[OK] {sheet_path}")
        return 0

    # 模式 4：渲染完整合约成片（可选顺带输出连切预览）
    if a.debug:
        fail("--debug 只用于 --stills，安全区参考线不能进成片")
    if a.out is None or not a.out.name.endswith("-motion.mp4"):
        fail("--out 必填，且文件名以 -motion.mp4 结尾（-still.mp4 会被 ingest_patch 登记成图片微动）")
    if not a.out.parent.is_dir():
        fail(f"输出目录不存在 {a.out.parent}")
    if a.splice is not None:
        if a.episode is None or a.seg is None:
            fail("--splice 必须配 --episode 和 --seg")
        if is_inside_patch_assets(a.splice):
            fail(f"--splice 预览视频不能落盘到 patch_assets/：{a.splice}")

    # 先写隐藏的半成品，合约检查全过才改名：ingest_patch 跳过点开头的文件，
    # 不合格的产物不会被误登记进补丁池。
    partial = a.out.with_name(f".{a.out.name}.partial")
    print(f"[*] {a.scene.name}: {duration:.3f}s → {n} 帧 @ {FPS}")
    with sync_playwright() as p:
        hashes = render_video(p, a.scene, motion, n, partial)
        check_output(partial, n)
        if a.verify_repro:
            print("[*] 复现检查：再渲一遍逐帧比对")
            again = frame_hashes(p, a.scene, motion, n)
            diff = [i for i, (x, y) in enumerate(zip(hashes, again)) if x != y]
            if diff:
                fail(f"两次渲染不一致：{len(diff)} 帧不同，第一帧 #{diff[0]}"
                     f"（t={float(diff[0] / FPS):.3f}s）；半成品留在 {partial}")
    partial.replace(a.out)
    digest = hashlib.sha256("".join(hashes).encode()).hexdigest()[:16]
    print(f"[OK] {a.out}  {n} 帧  逐帧摘要 {digest}" + ("  复现一致" if a.verify_repro else ""))

    if a.splice is not None:
        run_splice_preview(
            a.episode, a.seg, a.out, a.splice, pad=a.splice_pad, include_bgm=a.splice_bgm
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
