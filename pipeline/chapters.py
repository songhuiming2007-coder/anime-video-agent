"""章节进度条（方案 C 纪实窄顶栏）解析、宽度检验、ASS 生成与 `07-chapters.txt` 导出。

纯粹标准库叶子模块：只依赖 stdlib + `pipeline.paths` / `pipeline.project`
（供 `check_script`、`render`、`assemble` 共用，严禁引入 numpy / PIL / torch）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, project

DEFAULT_MAX_CHAPTERS = 8
BAR_HEIGHT = 44.0
SIDE_PAD = 24.0
BADGE_TRACK_START = 190.0
BADGE_X = 92.0
BADGE_DIVIDER_X = 180.0
SLOT_GAP = 3.0
MIN_SLOT_WIDTH = 72.0
MIN_RENDER_SLOT_W = 64.0
MAX_BADGE_PX = 164.0
MAX_SUB_TITLE_PX = 1200.0

# 方案 C 配色（ASS &HBBGGRR）
C_WHITE = "&HEEF4F7"
C_SUBTLE = "&HD8DEE4"
C_GOLD = "&H75D8FF"
C_DARK = "&H120E0B"
C_BAR_BG = "&H221C18"

_HEADING_END = re.compile(r"^##\s*章节结束(.*)$")
_HEADING_END_BAD = re.compile(r"^#{3,}\s*章节结束")
_HEADING_CHAP = re.compile(r"^##\s*章节(.*)$")
_HEADING_CHAP_BAD = re.compile(r"^#{3,}\s*章节(?:\s|[·:：]|$)")
_HEADING_SUB = re.compile(r"^###\s*子节(.*)$")
_HEADING_SUB_BAD = re.compile(r"^(?:##|#{4,})\s*子节(?:\s|[·:：]|$)")
_HEADING_TITLE_BODY = re.compile(r"^\s*[·:：]\s*(.+?)\s*$")
_HEADING_SEG = re.compile(r"^##\s*段落\s*(\S+)\s*$")
_HEADING_MUSIC = re.compile(r"^##\s*音乐段\s*(\S+)")
_ANY_CHAPTER_HINT = re.compile(r"^#{2,}\s*(?:章节结束|章节|子节)", re.M)
_BLOCK_BOUNDARY = re.compile(r"^(?:##\s+|#{2,}\s*(?:章节结束|章节|子节))")
_STATUS_BGM = re.compile(r"^状态[：:]\s*背景铺底", re.M)
_VO_LINE = re.compile(r"^配音[：:]\s*(.+)$", re.M)
_AVSYNC = re.compile(r"^源片原声[：:]\s*`.+?`\s*(\d+):(\d{2}(?:\.\d+)?)-(\d+):(\d{2}(?:\.\d+)?)", re.M)
_TIME_RANGE = re.compile(r"`.+?`\s*.*?(\d+):(\d{2})-(?:(\d+):(\d{2})|结束)")
_BADGE_LINE = re.compile(r"^\s*进度条铭牌\s*[:：]\s*(.*?)\s*$")
_BADGE_HINT = re.compile(r"^\s*进度条铭牌\b")

_ASS_NEWLINE = re.compile(r"[\r\n]+")


@dataclass(frozen=True)
class AnchorRef:
    """时间轴锚点：段落号 (`seg`, int)、前景音乐块 (`music`, label) 或正文收尾 (`vo_fg_end`, "")。"""

    kind: str
    key: int | str


@dataclass(frozen=True)
class SubChapterSpec:
    title: str
    anchor: AnchorRef


@dataclass(frozen=True)
class ChapterSpecItem:
    title: str
    anchor: AnchorRef
    subs: tuple[SubChapterSpec, ...] = ()


@dataclass(frozen=True)
class ChapterSpec:
    chapters: tuple[ChapterSpecItem, ...]
    end_anchor: AnchorRef | None = None
    weights_hint: tuple[float, ...] = ()


@dataclass(frozen=True)
class ResolvedSubChapter:
    title: str
    start: float
    end: float


@dataclass(frozen=True)
class ResolvedChapter:
    title: str
    start: float
    end: float
    subs: tuple[ResolvedSubChapter, ...] = ()


@dataclass(frozen=True)
class ResolvedChapters:
    chapters: tuple[ResolvedChapter, ...]
    badge: str | None
    bar_end: float
    total_duration: float
    explicit_end: bool = False


def ass_escape(text: str) -> str:
    """转义用户输入进 ASS 事件的语法字符，与 render._ass_escape 同口径。"""
    s = _ASS_NEWLINE.sub(" ", text).strip()
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def ass_time(t: float) -> str:
    """秒 → ASS 时间戳 `H:MM:SS.cc`，按厘秒取整杜绝 `:60.00`。"""
    total_cs = max(0, round(t * 100))
    h, rem = divmod(total_cs, 360000)
    m, cs = divmod(rem, 6000)
    return f"{h}:{m:02d}:{cs // 100:02d}.{cs % 100:02d}"


def _max_chapters() -> int:
    return int(paths.conf("hud.max_chapters", DEFAULT_MAX_CHAPTERS))


def _estimate_music_dur(body: str) -> float:
    if av := _AVSYNC.search(body):
        t0 = int(av.group(1)) * 60 + float(av.group(2))
        t1 = int(av.group(3)) * 60 + float(av.group(4))
        return max(1.0, t1 - t0)
    if tm := _TIME_RANGE.search(body):
        t0 = int(tm.group(1)) * 60 + int(tm.group(2))
        if tm.group(3) is None:
            return 40.0
        t1 = int(tm.group(3)) * 60 + int(tm.group(4))
        return max(1.0, t1 - t0)
    return 20.0


@dataclass
class _PendingMark:
    kind: str       # "chapter" | "sub" | "end"
    title: str | None


def _parse_heading_events(
    text: str, cpm: float | None = None
) -> tuple[list[tuple[str, object]], list[tuple[AnchorRef, float]]]:
    """扫描稿件内全部结构标题与实体时间轴节点（跳过 `状态: 背景铺底` 音乐块）。"""
    lines = text.replace("\r\n", "\n").split("\n")
    events: list[tuple[str, object]] = []
    entities: list[tuple[AnchorRef, float]] = []
    eff_cpm = float(cpm if cpm is not None else (paths.conf("script.cpm", 257) or 257))

    i = 0
    while i < len(lines):
        line = lines[i]
        if _HEADING_END_BAD.match(line):
            raise SystemExit(f"FAIL 章节结束层级错误，应为 `## 章节结束`：{line.strip()!r}")
        if _HEADING_CHAP_BAD.match(line):
            raise SystemExit(f"FAIL 章节标题层级错误，应为 `## 章节 · 标题`：{line.strip()!r}")
        if _HEADING_SUB_BAD.match(line):
            raise SystemExit(f"FAIL 子节标题层级错误，应为 `### 子节 · 标题`：{line.strip()!r}")

        if m_end := _HEADING_END.match(line):
            tail = m_end.group(1).strip()
            if tail and not tail.startswith("#"):
                raise SystemExit(f"FAIL `## 章节结束` 后面有多余文字：{line.strip()!r}")
            events.append(("mark", _PendingMark("end", None)))
            i += 1
            continue

        if m_ch := _HEADING_CHAP.match(line):
            m_title = _HEADING_TITLE_BODY.match(m_ch.group(1))
            if not m_title or not m_title.group(1).strip():
                raise SystemExit(f"FAIL `## 章节` 格式错误，应为 `## 章节 · 标题`：{line.strip()!r}")
            events.append(("mark", _PendingMark("chapter", m_title.group(1).strip())))
            i += 1
            continue

        if m_sub := _HEADING_SUB.match(line):
            m_title = _HEADING_TITLE_BODY.match(m_sub.group(1))
            if not m_title or not m_title.group(1).strip():
                raise SystemExit(f"FAIL `### 子节` 格式错误，应为 `### 子节 · 标题`：{line.strip()!r}")
            events.append(("mark", _PendingMark("sub", m_title.group(1).strip())))
            i += 1
            continue

        if m_seg := _HEADING_SEG.match(line):
            j = i + 1
            while j < len(lines) and not _BLOCK_BOUNDARY.match(lines[j]):
                j += 1
            body = "\n".join(lines[i + 1:j])
            try:
                seg_idx: int | str = int(m_seg.group(1))
            except ValueError:
                seg_idx = m_seg.group(1)
            vo = _VO_LINE.search(body)
            chars = len(re.sub(r"\s+", "", vo.group(1))) if vo else 0
            est_sec = max(2.0, chars / (eff_cpm / 60.0))
            ref = AnchorRef("seg", seg_idx)
            entities.append((ref, est_sec))
            events.append(("entity", (ref, est_sec)))
            i = j
            continue

        if m_mus := _HEADING_MUSIC.match(line):
            j = i + 1
            while j < len(lines) and not _BLOCK_BOUNDARY.match(lines[j]):
                j += 1
            body = "\n".join(lines[i + 1:j])
            if not _STATUS_BGM.search(body):
                ref = AnchorRef("music", m_mus.group(1))
                est_sec = _estimate_music_dur(body)
                entities.append((ref, est_sec))
                events.append(("entity", (ref, est_sec)))
            i = j
            continue

        i += 1

    return events, entities


def parse_script_chapters(script_path: Path, cpm: float | None = None) -> ChapterSpec | None:
    """解析 `02-script.md` 的章节标记；未写任何章节标记返回 None。"""
    text = script_path.read_text(encoding="utf-8")
    if not _ANY_CHAPTER_HINT.search(text):
        return None

    events, entities = _parse_heading_events(text, cpm=cpm)
    if not entities:
        raise SystemExit("FAIL 稿件写了章节标记，但没有任何 `## 段落` 或前景 `## 音乐段`")

    # 把每个结构标记绑定到其后第一个实体节点
    pending: list[_PendingMark] = []
    anchored: list[tuple[_PendingMark, AnchorRef | None, int]] = []
    ent_idx = 0
    for kind, payload in events:
        if kind == "mark":
            pending.append(payload)  # type: ignore[arg-type]
        else:
            ref, _ = payload  # type: ignore[misc]
            for mk in pending:
                anchored.append((mk, ref, ent_idx))
            pending.clear()
            ent_idx += 1
    for mk in pending:
        anchored.append((mk, None, len(entities)))

    chapters_raw: list[dict] = []
    end_anchor: AnchorRef | None = None
    end_ent_idx: int | None = None
    seen_end = False

    for mk, ref, e_idx in anchored:
        if seen_end:
            raise SystemExit("FAIL `## 章节结束` 之后不能再出现 `## 章节` 或 `### 子节`")
        if mk.kind == "end":
            seen_end = True
            end_ent_idx = e_idx
            end_anchor = ref if ref is not None else AnchorRef("vo_fg_end", "")
            continue
        if ref is None:
            label = "子节" if mk.kind == "sub" else "章节"
            raise SystemExit(f"FAIL {label}「{mk.title}」后面没有任何段落或前景音乐段，无法定位起点")
        if mk.kind == "chapter":
            assert mk.title is not None
            if chapters_raw and chapters_raw[-1]["ent_idx"] == e_idx:
                raise SystemExit(
                    f"FAIL 章节「{chapters_raw[-1]['title']}」与「{mk.title}」起点重合"
                    "（两章之间没有口播段或前景音乐段）"
                )
            chapters_raw.append({"title": mk.title, "anchor": ref, "ent_idx": e_idx, "subs": []})
        else:
            assert mk.title is not None
            if not chapters_raw:
                raise SystemExit(f"FAIL `### 子节 · {mk.title}` 必须写在某个 `## 章节` 之后")
            subs = chapters_raw[-1]["subs"]
            if subs and subs[-1]["ent_idx"] == e_idx:
                raise SystemExit(
                    f"FAIL 章节「{chapters_raw[-1]['title']}」内的子节「{subs[-1]['title']}」"
                    f"与「{mk.title}」起点重合（中间没有口播段或前景音乐段）"
                )
            subs.append({"title": mk.title, "anchor": ref, "ent_idx": e_idx})

    if not chapters_raw:
        raise SystemExit("FAIL 写了 `## 章节结束`，但前面没有任何 `## 章节`")
    if len(chapters_raw) < 2:
        raise SystemExit("FAIL 至少需要 2 个 `## 章节` 才能组成进度条（当前仅 1 个）")
    max_ch = _max_chapters()
    if len(chapters_raw) > max_ch:
        raise SystemExit(
            f"FAIL 顶栏一级 `## 章节` 最多 {max_ch} 个（当前 {len(chapters_raw)} 个，"
            "格宽过窄会导致文字不可读）——请合并为大章并在章内用 `### 子节 · 标题` 标细分节点"
        )

    stop_idx = end_ent_idx if end_ent_idx is not None else len(entities)
    if stop_idx <= chapters_raw[-1]["ent_idx"]:
        raise SystemExit(
            f"FAIL `## 章节结束` 位于末章「{chapters_raw[-1]['title']}」之前或与之重合，导致末章零时长"
        )

    weights: list[float] = []
    chap_items: list[ChapterSpecItem] = []
    durs: list[float] = []
    for idx, ch in enumerate(chapters_raw):
        lo = ch["ent_idx"]
        hi = chapters_raw[idx + 1]["ent_idx"] if idx + 1 < len(chapters_raw) else stop_idx
        durs.append(sum(dur for _, dur in entities[lo:hi]))
        chap_items.append(ChapterSpecItem(
            title=ch["title"],
            anchor=ch["anchor"],
            subs=tuple(SubChapterSpec(s["title"], s["anchor"]) for s in ch["subs"]),
        ))
    total_est = sum(durs) or 1.0
    weights = [d / total_est for d in durs]
    return ChapterSpec(tuple(chap_items), end_anchor=end_anchor, weights_hint=tuple(weights))


def verify_hud_fonts() -> Path:
    """校验 `hud.fonts_dir` 及其下必需字体文件均存在且非空，返回目录真实路径。"""
    fonts_cfg = str(paths.conf("hud.fonts_dir", "data/fonts"))
    required = list(paths.conf("hud.required_files", ["Songti.ttc", "SourceHanSansSC-Bold.otf"]))
    fonts_dir = Path(fonts_cfg) if Path(fonts_cfg).is_absolute() else (paths.ROOT / fonts_cfg)
    if not fonts_dir.is_dir():
        raise SystemExit(f"FAIL 启用章节顶栏需要字体目录 {fonts_cfg}（缺少 {', '.join(required)}）")
    missing = [
        name for name in required
        if not ((fonts_dir / name).is_file() and (fonts_dir / name).stat().st_size > 0)
    ]
    if missing:
        raise SystemExit(f"FAIL 启用章节顶栏需要字体目录 {fonts_cfg}（缺少 {', '.join(missing)}）")
    return fonts_dir.resolve()


def resolve_badge(episode: Path) -> str | None:
    """解析顶栏左侧铭牌：`01-topic.md` 的 `进度条铭牌:` 优先，否则按企划卷序自动推断。"""
    badge: str | None = None
    topic = episode / "01-topic.md"
    matched_topic = False
    if topic.is_file():
        for raw_line in topic.read_text(encoding="utf-8").splitlines():
            m = _BADGE_LINE.match(raw_line)
            if m:
                val = re.sub(r"\s+#.*$", "", m.group(1)).strip()
                if not val:
                    raise SystemExit(
                        "FAIL `进度条铭牌` 不能为空（不需要铭牌可删去该行，或写 `进度条铭牌: 无`）"
                    )
                if val in ("无", "关"):
                    return None
                badge = val if (val.startswith("[") and val.endswith("]")) else f"[ {val} ]"
                matched_topic = True
                break
            if _BADGE_HINT.match(raw_line):
                raise SystemExit("FAIL `进度条铭牌` 缺少冒号与内容，应为 `进度条铭牌: VOL.02` 或 `进度条铭牌: 无`")

    if not matched_topic:
        parent = episode.parent
        if project.classify_dir(episode) == "volume" or (parent / project.PROJECT_FILE).is_file():
            seq = project.volume_seq(parent, episode.name)
            if seq is not None:
                badge = f"[ VOL.{seq:02d} ]"

    if badge is not None:
        px = estimate_text_px(badge, 20, fsp=1.2, bold=True)
        if px > MAX_BADGE_PX:
            raise SystemExit(
                f"FAIL 进度条铭牌「{badge}」过宽（约 {px:.0f}px > 上限 {MAX_BADGE_PX:.0f}px），会侵入章节轨"
            )
    return badge


def estimate_text_px(text: str, font_size: int, fsp: float = 0.5, *, bold: bool = False) -> float:
    """按 `subtitle_metrics.py` 实测字符比例估算单行像素宽（纯标准库）。"""
    if not text:
        return 0.0
    w = 0.0
    for ch in text:
        code = ord(ch)
        if ch == " ":
            w += 0.33
        elif code > 0x2E80:
            w += 1.0
        elif "A" <= ch <= "Z":
            w += 0.75
        elif "0" <= ch <= "9":
            w += 0.66
        elif "a" <= ch <= "z":
            w += 0.58
        else:
            w += 0.55
    scale = 1.08 if bold else 1.0
    return w * font_size * scale + max(0, len(text) - 1) * fsp


def slot_font_size(slot_w: float) -> int:
    """顶栏单格字号（下限钉死 16px，绝不退化成不可读小字）。"""
    if slot_w >= 180.0:
        return 21
    if slot_w >= 135.0:
        return 18
    return 16


def validate_sub_titles(chapters: tuple[ChapterSpecItem, ...] | tuple[ResolvedChapter, ...]) -> list[str]:
    """检查子节左下角悬浮标签在 22px 粗体下是否过长。"""
    violations: list[str] = []
    for ch in chapters:
        for sub in ch.subs:
            px = estimate_text_px(sub.title, 22, fsp=1.0, bold=True)
            if px > MAX_SUB_TITLE_PX:
                violations.append(
                    f"子节「{sub.title}」在 22px 粗体下约宽 {px:.0f}px > 上限 {MAX_SUB_TITLE_PX:.0f}px，可能超出画面"
                )
    return violations


def validate_slot_widths(
    titles: list[str],
    weights: list[float],
    *,
    has_badge: bool,
    width: int = 1920,
    safety_px: float = 16.0,
) -> list[str]:
    """核对方案 C 顶栏各格宽是否容得下章名。返回违例列表（空 = 通过）。"""
    x_start = BADGE_TRACK_START if has_badge else SIDE_PAD
    x_end = width - SIDE_PAD
    avail = (x_end - x_start) - SLOT_GAP * max(0, len(titles) - 1)
    violations: list[str] = []
    for idx, (title, weight) in enumerate(zip(titles, weights), 1):
        sw = avail * weight
        fs = slot_font_size(sw)
        fsp = 0.5 if sw < 140.0 else 1.2
        need = estimate_text_px(title, fs, fsp=fsp, bold=True) + safety_px
        if sw < MIN_SLOT_WIDTH or need > sw:
            violations.append(
                f"第 {idx} 章「{title}」约需 {need:.0f}px（字号 {fs}px），"
                f"但按篇幅只分到 {sw:.0f}px 槽宽——请精简章名或并入大章的 `### 子节`"
            )
    return violations


def check_script_chapters(script_path: Path, cpm: float | None = None) -> tuple[bool, str] | None:
    """供 `check_script` 调用的章节顶栏检查；未声明章节返回 None。"""
    try:
        spec = parse_script_chapters(script_path, cpm=cpm)
    except SystemExit as exc:
        return (False, str(exc).removeprefix("FAIL ").strip())
    if spec is None:
        return None
    try:
        badge = resolve_badge(script_path.parent)
    except SystemExit as exc:
        return (False, str(exc).removeprefix("FAIL ").strip())

    titles = [c.title for c in spec.chapters]
    errs = validate_slot_widths(
        titles, list(spec.weights_hint), has_badge=badge is not None, safety_px=22.0,
    )
    errs.extend(validate_sub_titles(spec.chapters))
    if errs:
        return (False, "；".join(errs[:2]))
    n_subs = sum(len(c.subs) for c in spec.chapters)
    detail = f"{len(spec.chapters)} 章" + (f"（含 {n_subs} 个子节）" if n_subs else "")
    if badge:
        detail += f"，铭牌 {badge}"
    return (True, detail)


def _anchor_time(
    ref: AnchorRef,
    seg_starts: dict[int, float],
    music_starts: dict[str, float],
    vo_fg_end: float,
) -> float:
    if ref.kind == "seg":
        try:
            idx = int(ref.key)
        except (TypeError, ValueError):
            raise SystemExit(f"FAIL 章节锚定的段落号 {ref.key!r} 不是整数") from None
        if idx not in seg_starts:
            raise SystemExit(f"FAIL 章节锚定的段落 {idx} 不在成片时间轴内")
        return seg_starts[idx]
    if ref.kind == "music":
        lbl = str(ref.key)
        if lbl not in music_starts:
            raise SystemExit(f"FAIL 章节锚定的音乐段 {lbl} 不在成片时间轴内")
        return music_starts[lbl]
    if ref.kind == "vo_fg_end":
        return vo_fg_end
    raise SystemExit(f"FAIL 未知章节锚点类型：{ref.kind}")


def resolve_chapters(
    episode: Path,
    spec: ChapterSpec,
    seg_span: dict[int, tuple[float, float]],
    music_plan: dict | None,
    total_duration: float,
    *,
    check_widths: bool = True,
) -> ResolvedChapters:
    """将章节结构映射到真实成片时间轴（秒）。"""
    seg_starts = {idx: span[0] for idx, span in seg_span.items()}
    music_starts = {
        b["label"]: float(b["start"])
        for b in (music_plan or {}).get("blocks", [])
    }
    vo_fg_end = max(
        [*(span[1] for span in seg_span.values()),
         *(float(b["start"]) + float(b["dur"]) for b in (music_plan or {}).get("blocks", []))],
        default=total_duration,
    )

    if spec.end_anchor is not None:
        bar_end = _anchor_time(spec.end_anchor, seg_starts, music_starts, vo_fg_end)
        explicit_end = True
    else:
        bar_end = vo_fg_end
        explicit_end = bar_end < total_duration - 0.5

    if bar_end > total_duration + 1e-3:
        raise SystemExit(f"FAIL 章节顶栏终点 {bar_end:.2f}s 超过成片总长 {total_duration:.2f}s")

    ch_starts = [
        _anchor_time(ch.anchor, seg_starts, music_starts, vo_fg_end)
        for ch in spec.chapters
    ]
    resolved_list: list[ResolvedChapter] = []
    for i, ch in enumerate(spec.chapters):
        st = ch_starts[i]
        ed = ch_starts[i + 1] if i + 1 < len(spec.chapters) else bar_end
        if ed <= st + 1e-3:
            raise SystemExit(f"FAIL 章节「{ch.title}」成片时长非正（{st:.2f}–{ed:.2f}s）")
        sub_starts = [
            _anchor_time(sub.anchor, seg_starts, music_starts, vo_fg_end)
            for sub in ch.subs
        ]
        res_subs: list[ResolvedSubChapter] = []
        for j, sub in enumerate(ch.subs):
            sst = sub_starts[j]
            sed = sub_starts[j + 1] if j + 1 < len(ch.subs) else ed
            if sst < st - 1e-3 or sed > ed + 1e-3 or sed <= sst + 1e-3:
                raise SystemExit(
                    f"FAIL 子节「{sub.title}」时间区间 [{sst:.2f}, {sed:.2f}] "
                    f"未落在所属章节「{ch.title}」[{st:.2f}, {ed:.2f}] 内"
                )
            res_subs.append(ResolvedSubChapter(sub.title, sst, sed))
        resolved_list.append(ResolvedChapter(ch.title, st, ed, tuple(res_subs)))

    badge = resolve_badge(episode)
    span_total = bar_end - ch_starts[0]
    weights = [(c.end - c.start) / span_total for c in resolved_list]
    if check_widths:
        errs = validate_slot_widths(
            [c.title for c in resolved_list], weights, has_badge=badge is not None, safety_px=16.0,
        )
        errs.extend(validate_sub_titles(tuple(resolved_list)))
        if errs:
            raise SystemExit("FAIL 章节顶栏渲染越界：\n  " + "\n  ".join(errs))

    return ResolvedChapters(
        chapters=tuple(resolved_list),
        badge=badge,
        bar_end=bar_end,
        total_duration=total_duration,
        explicit_end=explicit_end,
    )


def _rect_evt(
    layer: int, t0: float, t1: float,
    x0: float, y0: float, x1: float, y1: float,
    color: str, alpha: str = "00", blur: float = 0.0, extra_tags: str = "",
) -> str:
    b = f"\\blur{blur:.1f}" if blur > 0 else ""
    coords = f"m {x0:.1f} {y0:.1f} l {x1:.1f} {y0:.1f} l {x1:.1f} {y1:.1f} l {x0:.1f} {y1:.1f}"
    return (
        f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},HUD_DRAW,,0,0,0,,"
        f"{{\\an7\\pos(0,0)\\bord0\\shad0{b}\\1c{color}\\1a&H{alpha}&{extra_tags}\\p1}}{coords}{{\\p0}}"
    )


def _text_evt(
    layer: int, style: str, t0: float, t1: float,
    x: float, y: float, text: str, *,
    size: int, color: str = C_WHITE, alpha: str = "00",
    fsp: float = 1.0, an: int = 7, bord: float = 0.8, shad: float = 0.8,
    bold: int | None = None, extra_tags: str = "",
) -> str:
    b_tag = f"\\b{bold}" if bold is not None else ""
    return (
        f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,"
        f"{{\\an{an}\\pos({x:.1f},{y:.1f})\\fs{size}\\fsp{fsp:.1f}{b_tag}"
        f"\\1c{color}\\1a&H{alpha}&\\3c{C_DARK}\\3a&H58&"
        f"\\4c{C_DARK}\\4a&H70&\\bord{bord:.1f}\\shad{shad:.1f}{extra_tags}}}{ass_escape(text)}"
    )


def _glow_text_evts(
    layer: int, style: str, t0: float, t1: float,
    x: float, y: float, text: str, *,
    size: int, fsp: float = 1.0, fade_tags: str = "",
) -> list[str]:
    escaped = ass_escape(text)
    halo = (
        f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,"
        f"{{\\an7\\pos({x:.1f},{y:.1f})\\fs{size}\\fsp{fsp:.1f}"
        f"\\1c{C_DARK}\\1a&H70&\\3c{C_DARK}\\3a&H70&"
        f"\\bord4.6\\shad0\\blur4.5{fade_tags}}}{escaped}"
    )
    fg = _text_evt(
        layer + 1, style, t0, t1, x, y, text,
        size=size, color=C_WHITE, alpha="20", fsp=fsp, bord=1.1, shad=1.2, extra_tags=fade_tags,
    )
    return [halo, fg]


def _fit_title_to_slot(
    title: str, sw: float, fs: int, fsp: float, *, bold: bool = True,
) -> str:
    """合集大幅裁切使单格变窄时自动截断并加省略号，正常过检稿件原样返回。"""
    if estimate_text_px(title, fs, fsp=fsp, bold=bold) + 12.0 <= sw:
        return title
    budget = max(12.0, sw - 12.0)
    out = title
    while len(out) > 1 and estimate_text_px(out + "…", fs, fsp=fsp, bold=bold) > budget:
        out = out[:-1]
    return (out + "…") if out != title else title


def build_hud_ass(resolved: ResolvedChapters, width: int = 1920) -> tuple[str, list[str]]:
    """生成方案 C 纪实窄顶栏的 ASS 样式定义与事件列表。"""
    sans_font = str(paths.conf("hud.sans_font", "Source Han Sans SC"))
    serif_font = str(paths.conf("hud.serif_font", "Songti SC"))
    styles = (
        f"Style: HUD_SANS,{sans_font},20,&H00EEF4F7,&H60120E0B,&H80120E0B,-1,0,0,0,100,100,0,0,1,0.8,0.8,5,0,0,0,1\n"
        f"Style: HUD_SERIF,{serif_font},21,&H00EEF4F7,&H55120E0B,&H85120E0B,-1,0,0,0,100,100,0,0,1,0.8,0.8,5,0,0,0,1\n"
        f"Style: HUD_DRAW,{sans_font},20,&H00EEF4F7,&H00120E0B,&H00120E0B,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
    )

    chaps = resolved.chapters
    if not chaps:
        return styles, []
    bar_start = chaps[0].start
    bar_end = resolved.bar_end
    fade_in_ms = 400 if bar_start > 0.05 else 0
    fade_out_ms = 800 if resolved.explicit_end and bar_end < resolved.total_duration - 0.1 else 0
    base_fad = f"\\fad({fade_in_ms},{fade_out_ms})" if (fade_in_ms or fade_out_ms) else ""

    events: list[str] = [
        _rect_evt(10, bar_start, bar_end, 0, 0, width, BAR_HEIGHT, C_BAR_BG, alpha="42", extra_tags=base_fad),
        _rect_evt(11, bar_start, bar_end, 0, BAR_HEIGHT - 1.0, width, BAR_HEIGHT, C_WHITE, alpha="D0", extra_tags=base_fad),
    ]
    if resolved.badge:
        events.append(_text_evt(
            13, "HUD_SANS", bar_start, bar_end, BADGE_X, 22, resolved.badge,
            size=20, color=C_GOLD, alpha="08", fsp=1.2, an=5, bord=0.8, shad=0.5, extra_tags=base_fad,
        ))
        events.append(_rect_evt(
            12, bar_start, bar_end, BADGE_DIVIDER_X, 9, BADGE_DIVIDER_X + 1.5, BAR_HEIGHT - 9,
            C_WHITE, alpha="B8", extra_tags=base_fad,
        ))

    x_start = BADGE_TRACK_START if resolved.badge else SIDE_PAD
    x_end = width - SIDE_PAD
    n = len(chaps)
    avail = (x_end - x_start) - SLOT_GAP * (n - 1)
    span_total = max(1e-3, bar_end - bar_start)

    raw_widths = [avail * (max(1e-3, ch.end - ch.start) / span_total) for ch in chaps]
    if n > 1 and avail >= n * MIN_RENDER_SLOT_W and any(w < MIN_RENDER_SLOT_W for w in raw_widths):
        adj = [max(w, MIN_RENDER_SLOT_W) for w in raw_widths]
        overflow = sum(adj) - avail
        slack = sum(max(0.0, w - MIN_RENDER_SLOT_W) for w in adj)
        if overflow > 0.0 and slack > 1e-6:
            raw_widths = [
                MIN_RENDER_SLOT_W if w <= MIN_RENDER_SLOT_W
                else w - overflow * ((w - MIN_RENDER_SLOT_W) / slack)
                for w in adj
            ]

    slots: list[tuple[float, float]] = []
    cx = x_start
    for sw in raw_widths:
        slots.append((cx, cx + sw))
        cx += sw + SLOT_GAP

    for idx in range(n - 1):
        _, sx1 = slots[idx]
        events.append(_rect_evt(
            12, bar_start, bar_end, sx1 + 1.0, 11, sx1 + 2.0, BAR_HEIGHT - 11,
            C_WHITE, alpha="C6", extra_tags=base_fad,
        ))

    for cur_idx, cur_ch in enumerate(chaps):
        t0, t1 = cur_ch.start, cur_ch.end
        is_first = cur_idx == 0 and fade_in_ms > 0
        is_last = cur_idx == n - 1 and fade_out_ms > 0
        win_fad = f"\\fad({fade_in_ms if is_first else 0},{fade_out_ms if is_last else 0})" if (is_first or is_last) else ""

        for j, ch in enumerate(chaps):
            sx0, sx1 = slots[j]
            sw = sx1 - sx0
            mid = (sx0 + sx1) / 2.0
            fsp = 0.5 if sw < 140.0 else 1.2
            fs = slot_font_size(sw)

            if j == cur_idx:
                active_title = _fit_title_to_slot(ch.title, sw, fs, fsp, bold=True)
                events.append(_rect_evt(11, t0, t1, sx0, 1, sx1, BAR_HEIGHT - 1, C_WHITE, alpha="E8", extra_tags=win_fad))
                events.append(_text_evt(
                    14, "HUD_SERIF", t0, t1, mid, 21, active_title,
                    size=fs, color=C_WHITE, alpha="00", fsp=fsp, an=5, bord=0.8, shad=0.8, bold=900, extra_tags=win_fad,
                ))
                if not ch.subs:
                    events.append(_rect_evt(12, t0, t1, sx0, BAR_HEIGHT - 4.0, sx1, BAR_HEIGHT, C_WHITE, alpha="CA", extra_tags=win_fad))
                    dur_ms = max(1, round((t1 - t0) * 1000))
                    ix0, ix1 = round(sx0), round(sx1)
                    clip_anim = f"\\clip({ix0},39,{ix0},45)\\t(0,{dur_ms},\\clip({ix0},39,{ix1},45))"
                    events.append(_rect_evt(13, t0, t1, sx0, BAR_HEIGHT - 4.0, sx1, BAR_HEIGHT, C_GOLD, alpha="00", extra_tags=clip_anim + win_fad))
                else:
                    sub_gap = 3.0
                    m_subs = len(ch.subs)
                    sub_avail = max(10.0, (sw - 16.0) - sub_gap * (m_subs - 1))
                    ch_dur = max(1e-3, ch.end - ch.start)
                    sub_boxes: list[tuple[float, float]] = []
                    tx = sx0 + 8.0
                    for sub in ch.subs:
                        sub_w = sub_avail * ((sub.end - sub.start) / ch_dur)
                        sub_boxes.append((tx, tx + sub_w))
                        tx += sub_w + sub_gap

                    for s_idx, sub in enumerate(ch.subs):
                        st0, st1 = sub.start, sub.end
                        s_fad_out = fade_out_ms if (is_last and s_idx == m_subs - 1) else 0
                        s_fad_in = fade_in_ms if (is_first and s_idx == 0) else 0
                        sfad = f"\\fad({s_fad_in},{s_fad_out})" if (s_fad_in or s_fad_out) else ""
                        for k_idx, (bx0, bx1) in enumerate(sub_boxes):
                            if k_idx < s_idx:
                                events.append(_rect_evt(13, st0, st1, bx0, BAR_HEIGHT - 4.0, bx1, BAR_HEIGHT, C_WHITE, alpha="55", extra_tags=sfad))
                            elif k_idx == s_idx:
                                events.append(_rect_evt(13, st0, st1, bx0, BAR_HEIGHT - 4.0, bx1, BAR_HEIGHT, C_WHITE, alpha="B8", extra_tags=sfad))
                                sdur_ms = max(1, round((st1 - st0) * 1000))
                                ibx0, ibx1 = round(bx0), round(bx1)
                                sclip = f"\\clip({ibx0},39,{ibx0},45)\\t(0,{sdur_ms},\\clip({ibx0},39,{ibx1},45))"
                                events.append(_rect_evt(14, st0, st1, bx0, BAR_HEIGHT - 4.0, bx1, BAR_HEIGHT, C_GOLD, alpha="00", extra_tags=sclip + sfad))
                            else:
                                events.append(_rect_evt(13, st0, st1, bx0, BAR_HEIGHT - 4.0, bx1, BAR_HEIGHT, C_WHITE, alpha="C2", extra_tags=sfad))

                        sub_fade = f"\\fad({s_fad_in or 200},{s_fad_out or 200})"
                        events.extend(_glow_text_evts(
                            14, "HUD_SANS", st0, st1, 44.0, BAR_HEIGHT + 14.0, sub.title,
                            size=22, fsp=1.0, fade_tags=sub_fade,
                        ))
            elif j < cur_idx:
                dim_fs = max(14, fs - 2)
                dim_title = _fit_title_to_slot(ch.title, sw, dim_fs, 0.5, bold=False)
                events.append(_rect_evt(12, t0, t1, sx0, BAR_HEIGHT - 2.0, sx1, BAR_HEIGHT, C_WHITE, alpha="88", extra_tags=win_fad))
                events.append(_text_evt(
                    14, "HUD_SANS", t0, t1, mid, 21, dim_title,
                    size=dim_fs, color=C_SUBTLE, alpha="48", fsp=0.5, an=5, bord=0.6, shad=0.5, bold=0, extra_tags=win_fad,
                ))
            else:
                dim_fs = max(14, fs - 2)
                dim_title = _fit_title_to_slot(ch.title, sw, dim_fs, 0.5, bold=False)
                events.append(_text_evt(
                    14, "HUD_SANS", t0, t1, mid, 21, dim_title,
                    size=dim_fs, color=C_SUBTLE, alpha="7C", fsp=0.5, an=5, bord=0.6, shad=0.5, bold=0, extra_tags=win_fad,
                ))

    return styles, events


def hud_strip_height(video_height: int) -> int:
    """计算需留存的顶部净画面条带像素高度（1080p → 96px，按 16 像素对齐）。"""
    return max(16, min(video_height, ((math.ceil(video_height * 96 / 1080) + 15) // 16) * 16))


def write_standalone_hud_ass(
    resolved: ResolvedChapters, dest: Path, width: int = 1920, height: int = 1080,
) -> Path:
    """写出仅含方案 C 顶栏的独立 ASS 文件（1920×1080 虚坐标，供合集局部重烧使用）。"""
    del width, height  # libass 按 PlayResX/PlayResY=1920×1080 等比缩放到任意目标分辨率
    styles, events = build_hud_ass(resolved, 1920)
    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"{styles}\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        + "\n".join(events)
        + ("\n" if events else "")
    )
    dest.write_text(content, encoding="utf-8")
    return dest


def episode_chapter_spans(resolved: ResolvedChapters) -> list[tuple[float, float, str]]:
    """从已解析章节提取平铺 `(起点秒, 终点秒, 标题)` 列表，供跨切口映射使用。"""
    spans: list[tuple[float, float, str]] = []
    for ch in resolved.chapters:
        if not ch.subs:
            spans.append((ch.start, ch.end, ch.title))
            continue
        if ch.subs[0].start - ch.start >= 5.0:
            spans.append((ch.start, ch.subs[0].start, ch.title))
            for sub in ch.subs:
                spans.append((sub.start, sub.end, sub.title))
        else:
            for idx, sub in enumerate(ch.subs):
                title = sub.title if idx > 0 or ch.title in sub.title else f"{ch.title} · {sub.title}"
                spans.append((sub.start, sub.end, title))
    return spans


def episode_chapter_entries(resolved: ResolvedChapters) -> list[tuple[float, str]]:
    """从已解析章节提取平铺 `(起点秒, 标题)` 列表供 `07-chapters.txt` 使用。"""
    return [(st, title) for st, _ed, title in episode_chapter_spans(resolved)]


def _cum_kept(t: float, kept: list[tuple[float, float]]) -> float:
    acc = 0.0
    for a, c in kept:
        if t <= a:
            break
        if t >= c:
            acc += c - a
        else:
            acc += t - a
            break
    return acc


def map_chapter_to_kept(
    st: float,
    ed: float,
    kept: list[tuple[float, float]],
    *,
    seam: float = 0.5,
    tail_fade: float = 0.0,
) -> tuple[float, float] | None:
    """将章节/子节区间 `[st, ed)` 投影到保留窗口输出时间轴；被彻底切除返回 None。"""
    if ed <= st:
        return None
    n_win = len(kept)
    usable = 0.0
    for idx, (a, c) in enumerate(kept):
        rf = seam if idx + 1 < n_win else tail_fade
        eff_hi = max(a, c - rf)
        ov = min(ed, eff_hi) - max(st, a)
        if ov > 0.0:
            usable += ov
    if usable < 0.3:
        return None

    m_st = _cum_kept(st, kept)
    acc = 0.0
    for idx, (a, c) in enumerate(kept):
        if idx + 1 < n_win and seam > 0.0 and max(a, c - seam) - 1e-3 <= st < c:
            later_usable = sum(
                max(0.0, min(ed, max(a2, c2 - (seam if j + 1 < n_win else tail_fade))) - max(st, a2))
                for j, (a2, c2) in enumerate(kept[idx + 1:], idx + 1)
            )
            if later_usable >= 0.3:
                m_st = acc + (c - a)
            break
        acc += c - a

    m_ed = _cum_kept(ed, kept)
    if m_ed - m_st < 0.3:
        return None
    return m_st, m_ed


def map_source_time_to_kept(
    t: float, kept: list[tuple[float, float]], *, seam: float = 0.5,
) -> float | None:
    """将卷成片时间 `t` 映射到保留窗口拼接后的偏移秒数；落入切除区间返回 None。"""
    acc = 0.0
    for a, c in kept:
        if a - 1e-3 <= t < c - seam:
            return acc + max(0.0, t - a)
        acc += c - a
    return None


def project_chapters_to_kept(
    resolved: ResolvedChapters,
    kept: list[tuple[float, float]],
    *,
    seam: float = 0.5,
    tail_fade: float = 0.0,
) -> ResolvedChapters | None:
    """按人定保留窗口重算本卷顶栏章节与子节；全被切空返回 None。"""
    kept_total = sum(c - a for a, c in kept)
    if kept_total <= 0.3:
        return None

    proj_items: list[tuple[str, float, float, list[list]]] = []
    for ch in resolved.chapters:
        mapped = map_chapter_to_kept(ch.start, ch.end, kept, seam=seam, tail_fade=tail_fade)
        if mapped is None:
            continue
        pst, ped = mapped
        sub_list: list[list] = []
        for sub in ch.subs:
            sm = map_chapter_to_kept(sub.start, sub.end, kept, seam=seam, tail_fade=tail_fade)
            if sm is None:
                continue
            sst = max(pst, min(ped, sm[0]))
            sed = max(sst, min(ped, sm[1]))
            if sed - sst >= 0.3:
                sub_list.append([sub.title, sst, sed])
        if sub_list:
            sub_list[0][1] = pst
            for k in range(len(sub_list) - 1):
                sub_list[k][2] = sub_list[k + 1][1]
            sub_list[-1][2] = ped
        proj_items.append((ch.title, pst, ped, sub_list))

    if not proj_items:
        return None

    items = [list(x) for x in proj_items]
    for k in range(len(items) - 1):
        nxt_st = items[k + 1][1]
        items[k][2] = nxt_st
        if items[k][3]:
            items[k][3][-1][2] = nxt_st

    res_chaps: list[ResolvedChapter] = []
    for title, st, ed, sub_list in items:
        if ed <= st + 1e-3:
            continue
        subs_tuple = tuple(ResolvedSubChapter(stitle, sst, sed) for stitle, sst, sed in sub_list if sed > sst + 1e-3)
        res_chaps.append(ResolvedChapter(title, st, ed, subs_tuple))
    if not res_chaps:
        return None

    bar_end = res_chaps[-1].end
    explicit_end = (bar_end < kept_total - 0.5) or (tail_fade > 0.0 and bar_end >= kept_total - 0.5)
    return ResolvedChapters(
        chapters=tuple(res_chaps),
        badge=resolved.badge,
        bar_end=bar_end,
        total_duration=kept_total,
        explicit_end=explicit_end,
    )


def _fmt_stamp(sec: int, use_hours: bool) -> str:
    if use_hours:
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    m, s = divmod(sec, 60)
    return f"{m:02d}:{s:02d}"


def format_chapters_txt(entries: list[tuple[float, str]], total_duration: float) -> str:
    """格式化 `07-chapters.txt`（B站看点 / YouTube Chapters 通用）：首条 0 起、同秒去重递增。"""
    if not entries:
        return ""
    use_hours = total_duration >= 3600.0
    normalized: list[tuple[int, str]] = []
    for raw_t, raw_title in sorted(entries, key=lambda x: x[0]):
        sec = 0 if raw_t < 1.0 else int(raw_t)
        title = raw_title.strip()
        if not title:
            continue
        if normalized and normalized[-1][0] == sec:
            prev_t, prev_title = normalized[-1]
            if title in prev_title:
                merged = prev_title
            elif prev_title in title:
                merged = title
            else:
                merged = f"{prev_title} · {title}"
            normalized[-1] = (prev_t, merged)
        else:
            normalized.append((sec, title))
    if not normalized:
        return ""
    if normalized[0][0] > 0:
        normalized.insert(0, (0, "序幕"))
    return "\n".join(f"{_fmt_stamp(sec, use_hours)} {title}" for sec, title in normalized) + "\n"
