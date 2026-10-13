"""企划跨卷检查：镜头与配乐跨卷重复（D81 C-1 / C-2，`docs/dev/plans/2026-10-12-project-layer-spec.md` §9）。

纯规则，**报警告不拦截**：有意的首尾呼应是合法的，由人逐条判「真重复 / 有意呼应 / 误报」。

- C-1 镜头：每卷读 `04-clips.approved.json`（没有则 `04-clips.json`，报告注明用的哪份），两卷间
  `clips.same_footage` 为真且时间区间交集 > 0 → 命中。区间与 `clips._overlaps` 同口径：
  `[start, start + span)`，没有 span 用 dur，`extend` 不计。两段都满足 `_overlaps` 的微动物证条件
  （`sp` 且 `limit ≤ 8`）的命中照报，单列一节。
- C-2 配乐：每卷 `music.parse_script_music(02-script.md)` 的非铺底块（前景或片尾），曲名 NFKC + 去首尾空白后
  在两卷以上出现 → 命中。

卷取自卷表（合集期不参与）。拿不到产物的卷列入「未检查」（判据 9：跳过不是通过）。

用法：python -m pipeline.crosscheck <企划目录> [--json] [--write]
  默认只读：只打印。--write 才写 `<企划>/00-crosscheck.md`（覆盖前留底到 `_agent/file-history/`）。
退出码：0 = 跑完（有命中也是 0）；2 = 不是企划 / 企划文件缺失或不可解析 / 卷表为空。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import project as project_mod

REPORT = "00-crosscheck.md"
HISTORY_DIR = ("_agent", "file-history")   # 与 agent 写期文件的留底同一目录（tools.FILE_HISTORY_DIR）
CLIP_FILES = ("04-clips.approved.json", "04-clips.json")
SCRIPT = "02-script.md"
# 状态卡读这一行（build_project_card）；改格式要同步 SUMMARY_RE
SUMMARY_RE = re.compile(r"^汇总：命中 (\d+) 条（镜头 (\d+) · 微动物证 (\d+) · 配乐 (\d+)）；未检查 (\d+) 卷$", re.M)


@dataclass
class ClipRef:
    volume: str
    segment: object          # 段号（04-clips 的 index）
    anime: str | None
    season: int | None
    episode: int | None
    start: float
    end: float


@dataclass
class ClipHit:
    a: ClipRef
    b: ClipRef
    overlap: float
    micro: bool


@dataclass
class MusicUse:
    volume: str
    block: str
    after: str
    t0: float
    t1: float | None


@dataclass
class MusicHit:
    title: str
    uses: list[MusicUse]


@dataclass
class Result:
    project: str
    clip_sources: dict[str, str] = field(default_factory=dict)   # 卷 → 用的哪份 04-clips
    clip_hits: list[ClipHit] = field(default_factory=list)
    music_hits: list[MusicHit] = field(default_factory=list)
    unchecked: list[tuple[str, str, str]] = field(default_factory=list)   # (卷, 镜头 / 配乐, 原因)

    @property
    def micro_hits(self) -> list[ClipHit]:
        return [h for h in self.clip_hits if h.micro]

    @property
    def shot_hits(self) -> list[ClipHit]:
        return [h for h in self.clip_hits if not h.micro]

    @property
    def unchecked_volumes(self) -> list[str]:
        return list(dict.fromkeys(v for v, _k, _r in self.unchecked))


class CrosscheckError(Exception):
    """企划不成立（不是企划、企划文件缺失 / 不可解析、卷表为空）：退 2。"""


def _span(c: dict) -> tuple[float, float]:
    # 与 clips._overlaps 同口径：没有 span 用 dur；extend 不计（_overlaps 也不计）
    start = float(c["start"])
    return start, start + float(c.get("span", c.get("dur", 0.0)))


def _is_micro(c: dict) -> bool:
    # clips._overlaps 的微动物证豁免条件原样照抄（spec §9.3 ②：豁免不进 same_footage 谓词）
    return bool(c.get("sp")) and c.get("limit", 999) <= 8.0


def load_clips(vol: Path) -> tuple[str, list[tuple[object, dict]]]:
    """卷的片段清单 [(段号, clip)] 与来源文件名；两份都没有 / 读不了抛 ValueError（原因进「未检查」）。"""
    for name in CLIP_FILES:
        path = vol / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out: list[tuple[object, dict]] = []
            for seg in data["segments"]:
                for c in seg.get("clips") or []:
                    if not isinstance(c, dict) or not {"season", "episode", "start"} <= c.keys():
                        raise ValueError(f"段 {seg.get('index')} 的片段缺 season / episode / start")
                    _span(c)   # start / span / dur 不是数 → 这里抛
                    out.append((seg.get("index"), c))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{name} 读不了：{exc}") from None
        return name, out
    raise ValueError(f"没有 {CLIP_FILES[0]} 也没有 {CLIP_FILES[1]}")


def check_clips(vols: list[tuple[str, list[tuple[object, dict]]]]) -> list[ClipHit]:
    from .clips import same_footage   # clips 拖着索引栈，只在真检查时 import

    hits: list[ClipHit] = []
    for i, (va, clips_a) in enumerate(vols):
        for vb, clips_b in vols[i + 1:]:
            for seg_a, a in clips_a:
                for seg_b, b in clips_b:
                    if not same_footage(a, b):
                        continue
                    a0, a1 = _span(a)
                    b0, b1 = _span(b)
                    overlap = min(a1, b1) - max(a0, b0)
                    if overlap <= 0:   # 交集 > 0 才算（相邻不算）；阈值取 0 的理由见 spec §9.4
                        continue
                    hits.append(ClipHit(
                        ClipRef(va, seg_a, a.get("anime"), a["season"], a["episode"], a0, a1),
                        ClipRef(vb, seg_b, b.get("anime"), b["season"], b["episode"], b0, b1),
                        round(overlap, 3), _is_micro(a) and _is_micro(b),
                    ))
    return hits


def _title_key(title: str) -> str:
    return unicodedata.normalize("NFKC", title).strip()


def check_music(vols: list[tuple[str, list]]) -> list[MusicHit]:
    by_title: dict[str, list[MusicUse]] = {}
    shown: dict[str, str] = {}
    for vname, blocks in vols:
        for b in blocks:
            if b.bgm_only:   # 铺底块不算（只查前景与片尾）
                continue
            key = _title_key(b.title)
            shown.setdefault(key, b.title.strip())
            by_title.setdefault(key, []).append(MusicUse(vname, b.label, b.after, b.t0, b.t1))
    return [MusicHit(shown[k], uses) for k, uses in by_title.items()
            if len({u.volume for u in uses}) >= 2]


def run(project_dir: Path) -> Result:
    from . import music

    pdir = Path(project_dir)
    if project_mod.classify_dir(pdir) != "project":
        raise CrosscheckError(f"不是企划目录：{pdir}")
    doc = project_mod.load(pdir)
    if doc is None:
        raise CrosscheckError(f"企划 {pdir.name} 没有 {project_mod.PROJECT_FILE}（卷表是检查哪几卷的唯一依据）")
    if isinstance(doc, project_mod.ParseError):
        raise CrosscheckError(f"企划 {pdir.name} 的 {project_mod.PROJECT_FILE} 解析失败：{doc}")
    if not doc.volumes:
        raise CrosscheckError(f"企划 {pdir.name} 的卷表为空")

    res = Result(project=doc.name)
    clip_vols: list[tuple[str, list[tuple[object, dict]]]] = []
    music_vols: list[tuple[str, list]] = []
    for v in sorted(doc.volumes, key=lambda v: v.seq):
        vol = pdir / v.dir   # 字面路径：镜像企划里的软链卷照读（只读）
        if not vol.is_dir():
            res.unchecked.append((v.dir, "镜头", "目录不存在"))
            res.unchecked.append((v.dir, "配乐", "目录不存在"))
            continue
        try:
            src, clips = load_clips(vol)
            res.clip_sources[v.dir] = src
            clip_vols.append((v.dir, clips))
        except ValueError as exc:
            res.unchecked.append((v.dir, "镜头", str(exc)))
        script = vol / SCRIPT
        if not script.is_file():
            res.unchecked.append((v.dir, "配乐", f"没有 {SCRIPT}（草稿不算）"))
            continue
        try:
            music_vols.append((v.dir, music.parse_script_music(script)))
        except SystemExit as exc:   # parse_script_music 格式错走 SystemExit：只记这一卷，不中断其他卷
            res.unchecked.append((v.dir, "配乐", f"音乐段格式错：{exc}"))
        except (OSError, UnicodeDecodeError) as exc:
            res.unchecked.append((v.dir, "配乐", f"{SCRIPT} 读不了：{exc}"))
    res.clip_hits = check_clips(clip_vols)
    res.music_hits = check_music(music_vols)
    return res


def _ep_label(r: ClipRef) -> str:
    ep = f"S{r.season:02d}E{r.episode:02d}" if isinstance(r.season, int) and isinstance(r.episode, int) else (
        f"SP{r.episode:02d}" if r.season is None and isinstance(r.episode, int) else f"{r.season}/{r.episode}")
    return f"{r.anime or '（未知番）'} {ep}"


def _clip_line(h: ClipHit) -> str:
    return (f"- {_ep_label(h.a)}：`{h.a.volume}` 段 {h.a.segment} [{h.a.start:.2f}, {h.a.end:.2f}) ↔ "
            f"`{h.b.volume}` 段 {h.b.segment} [{h.b.start:.2f}, {h.b.end:.2f})，交集 {h.overlap:.2f} s")


def _fmt_t(t: float | None) -> str:
    if t is None:
        return "结束"
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def summary_line(res: Result) -> str:
    n_shot, n_micro, n_music = len(res.shot_hits), len(res.micro_hits), len(res.music_hits)
    return (f"汇总：命中 {n_shot + n_micro + n_music} 条（镜头 {n_shot} · 微动物证 {n_micro} · 配乐 {n_music}）；"
            f"未检查 {len(res.unchecked_volumes)} 卷")


def render(res: Result, now: str) -> str:
    out = [f"# 00 跨卷检查 · {res.project}", "",
           "> 警告不拦截：每条命中由人判「真重复 / 有意呼应 / 误报」。", "",
           f"生成：{now}", summary_line(res), ""]
    if res.clip_sources:
        out.append("镜头清单来源：" + "；".join(f"`{v}` {s}" for v, s in res.clip_sources.items()))
        out.append("")
    out.append(f"## 镜头（{len(res.shot_hits)}）")
    out += [_clip_line(h) for h in res.shot_hits] or ["- 无"]
    out += ["", f"## 微动物证（单卷内豁免的那一类，{len(res.micro_hits)}）"]
    out += [_clip_line(h) for h in res.micro_hits] or ["- 无"]
    out += ["", f"## 配乐（{len(res.music_hits)}）"]
    for h in res.music_hits:
        uses = "；".join(f"`{u.volume}` 块 {u.block}（{u.after}，{_fmt_t(u.t0)}-{_fmt_t(u.t1)}）" for u in h.uses)
        out.append(f"- `{h.title}`：{uses}")
    if not res.music_hits:
        out.append("- 无")
    out += ["", f"## 未检查（{len(res.unchecked_volumes)} 卷；跳过不是通过）"]
    out += [f"- `{v}` {k}：{why}" for v, k, why in res.unchecked] or ["- 无"]
    return "\n".join(out) + "\n"


def to_json(res: Result) -> dict:
    return {
        "project": res.project,
        "clip_sources": res.clip_sources,
        "shots": [asdict(h) for h in res.shot_hits],
        "micro": [asdict(h) for h in res.micro_hits],
        "music": [asdict(h) for h in res.music_hits],
        "unchecked": [{"volume": v, "check": k, "reason": r} for v, k, r in res.unchecked],
        "summary": summary_line(res),
    }


def write_report(project_dir: Path, text: str) -> Path:
    """写 `<企划>/00-crosscheck.md`；已存在的先留底到 `_agent/file-history/<UTC 纳秒>-00-crosscheck.md`。"""
    dest = Path(project_dir) / REPORT
    if dest.exists():
        hist = Path(project_dir).joinpath(*HISTORY_DIR)
        hist.mkdir(parents=True, exist_ok=True)
        ns = time.time_ns()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(ns // 1_000_000_000)) + f"{ns % 1_000_000_000:09d}"
        shutil.copy2(dest, hist / f"{stamp}-{REPORT}")
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest


def read_summary(project_dir: Path) -> str | None:
    """企划状态卡用：上次 `--write` 的汇总行（「N 条命中 / 未检查 M 卷」）。没有报告 → None。"""
    path = Path(project_dir) / REPORT
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        return f"{REPORT} 读不了：{exc}"
    m = SUMMARY_RE.search(text)
    if m is None:
        return f"{REPORT} 里找不到汇总行（被手改过？重跑 crosscheck --write）"
    stamp = re.search(r"^生成：(.+)$", text, re.M)
    return (f"{m.group(1)} 条命中（镜头 {m.group(2)} · 微动物证 {m.group(3)} · 配乐 {m.group(4)}）/ "
            f"未检查 {m.group(5)} 卷" + (f"（{stamp.group(1).strip()}）" if stamp else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.crosscheck", description=__doc__.splitlines()[0],
                                 allow_abbrev=False)   # --wr 不被认作 --write（只读判定按完整旗标做）
    ap.add_argument("project", type=Path, help="企划目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--write", action="store_true", help=f"把报告写进 <企划>/{REPORT}（默认只读，只打印）")
    a = ap.parse_args(argv)
    try:
        res = run(a.project)
    except CrosscheckError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    text = render(res, time.strftime("%Y-%m-%d %H:%M"))
    print(json.dumps(to_json(res), ensure_ascii=False, indent=2) if a.json else text, end="" if not a.json else "\n")
    if a.write:
        dest = write_report(a.project, text)
        print(f"[OK] 已写 {dest.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
