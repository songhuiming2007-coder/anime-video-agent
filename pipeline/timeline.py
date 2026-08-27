"""时间网格：把一集切成对白块 / 无台词块，块边界吸附镜头切点。

番剧笔记场景级时间码的钉桩工具（ADR-0008 后，写稿 `锚点:` 的来源）。
**零语义**——只算时间，不拿台词内容做任何判断。「这块是什么场景」
由笔记 agent 对照网搜场景序列标注（WORKFLOW 番剧笔记节）；
从字幕反推剧情是老错路，本工具不产剧情，只产时间结构。

    python -m pipeline.timeline <番>                    # 全部已索引集
    python -m pipeline.timeline <番> --episode S01E01   # 只出一集
    python -m pipeline.timeline <番> --gap 10           # 无台词切块阈值（秒）

产物 `data/library/timeline/<番>_SxxEyy.md`——per-番资产，落 data 不落仓库。
"""

from __future__ import annotations

import argparse
import bisect
import sys
from pathlib import Path

from . import paths, shots
from .subindex import INDEX_DIR, _fmt, load_units

# 相邻台词间隔小于这个秒数并成同一个对白块。依据：动画正常对话的换气远小于它，
# 超过它多半是真换场或进沉默段落。按番敏感（对白密度不同），用 --gap 调，
# 先不写自适应——等天气之子 + 一部 24 集番各验一次再决定要不要进 config。
DEFAULT_GAP = 8.0

# 提示列的台词截断长度。只是给 agent 的定位线索（这句原话在哪），不是内容摘要。
HINT_CHARS = 24


def _line_runs(units: list, gap: float) -> list[tuple]:
    """滑窗单元 → 行级台词并块 [(start, end, first_text, last_text)]。

    **单元是 WINDOW=2、step=1 的滑窗**（subindex.py）：units[i] 覆盖第 i、i+1
    两行字幕，相邻单元永远重叠一行——直接拿单元算间隔永远算不出沉默
    （2026-08-27 天气之子实测：1763 个单元并成 1 块）。行级真间隔要还原：
    第 k 行的终点 = units[k-1].end，第 k+1 行的起点 = units[k+1].start。
    首行终点与末行起点不可还原，首尾各行恒并入邻块（那本来就是废话粒度）。
    **耦合 WINDOW=2**：subindex 改窗口大小时这里要跟着改。
    """
    n = len(units)
    if n < 3:
        return [(units[0].start, units[-1].end,
                 units[0].text, units[-1].text)] if units else []
    # 在第 k 行后切开：line k 终点 = units[k-1].end，line k+1 起点 = units[k+1].start
    splits = [k for k in range(1, n - 1)
              if units[k + 1].start - units[k - 1].end >= gap]
    runs = []
    for a, b in zip([-1] + splits, splits + [n]):
        lo, hi = a + 1, b                       # 行号闭区间 [lo, hi]
        runs.append((units[lo].start,
                     units[hi - 1].end,          # 行 hi 的终点（hi ≥ 1 恒成立）
                     units[lo].text,
                     units[min(hi, n - 1)].text))
    return runs


def blocks(units: list, cuts: list[float], duration: float, gap: float) -> list[dict]:
    """台词单元 + 镜头切点 → 时间网格块。纯函数，零语义。

    合并：相邻**台词行**间隔 < gap 并成同一对白块；≥ gap 切开，中间记无台词块。
    吸附与 `clips._anchor_candidate` 同源：对白块起点 floor 到含首句的镜头切点
    （不切半镜）、终点 ceil 到含末句的镜头尾，无台词块取剩余区间。
    沉默发生在长镜头内部（无切点可吸）时块边界会重叠——钳成单调不重叠，
    那样的沉默本来就切不出来，不值得为它留一块。
    """
    units = sorted(units, key=lambda u: u.start)
    runs = _line_runs(units, gap)
    if not runs:
        return []

    edges = sorted(cuts) + [duration]

    def floor_cut(t: float) -> float:
        if not cuts:
            return t
        return edges[max(0, bisect.bisect_right(edges, t) - 1)]

    def ceil_cut(t: float) -> float:
        if not cuts:
            return t
        return edges[min(bisect.bisect_left(edges, t), len(edges) - 1)]

    out: list[dict] = []
    prev_end = 0.0
    for rstart, rend, first, last in runs:
        start = max(floor_cut(rstart), prev_end)   # 钳住：不与上一块重叠
        end = max(ceil_cut(rend), start)
        if end - start < 0.05:
            # 整块被上一块的吸附跨度吞掉（沉默在长镜头内部，无切点可吸）——
            # 并入上一块，不产生零长度块
            if out:
                out[-1]["end"] = prev_end = max(out[-1]["end"], ceil_cut(rend))
                if out[-1]["kind"] == "对白":
                    out[-1]["last"] = last[:HINT_CHARS]
            continue
        if start > prev_end + 0.05:                # 微隙不切块
            out.append({"start": prev_end, "end": start, "kind": "无台词",
                        "first": "", "last": ""})
        out.append({"start": start, "end": end, "kind": "对白",
                    "first": first[:HINT_CHARS], "last": last[:HINT_CHARS]})
        prev_end = end
    if prev_end < duration - 0.05:
        out.append({"start": prev_end, "end": duration, "kind": "无台词",
                    "first": "", "last": ""})
    return out


def render_md(anime: str, key: str, grid: list[dict], gap: float,
              n_units: int, n_cuts: int, snapped: bool) -> str:
    head = [
        f"# {anime} {key} 时间网格",
        "",
        f"> gap={gap:g}s；台词单元 {n_units}，镜头切点 {n_cuts}"
        + ("" if snapped else "（**缺镜头表，边界未吸附切点**）") + "。",
        "> 「无台词」= 索引里无台词记录（含 OP/ED/插曲/纯音乐/真沉默），**不等于静音**；"
        "是什么场景要对照网搜剧情序列标注，本表不做判断。",
        "",
        "| # | 起 | 止 | 时长 | 类型 | 内容提示 |",
        "|---|---|---|---|---|---|",
    ]
    rows = []
    for i, b in enumerate(grid, 1):
        hint = f"{b['first']} 〜 {b['last']}" if b["kind"] == "对白" else ""
        rows.append(f"| {i} | {_fmt(b['start'])} | {_fmt(b['end'])} "
                    f"| {b['end'] - b['start']:.0f}s | {b['kind']} | {hint} |")
    return "\n".join(head + rows) + "\n"


def run(anime: str, episode: str | None = None, gap: float = DEFAULT_GAP,
        index_dir: Path = INDEX_DIR,
        out_dir: Path = paths.DATA / "library" / "timeline") -> list[Path]:
    units = load_units(index_dir, anime)
    by_ep: dict[tuple[int, int], list] = {}
    for u in units:
        by_ep.setdefault((u.season, u.episode), []).append(u)

    written = []
    for (s, e), us in sorted(by_ep.items()):
        key = f"S{s:02d}E{e:02d}"
        if episode and key != episode:
            continue
        try:
            d = shots.load(anime, key)
            cuts = [sh["start"] for sh in d["shots"]]
            duration = d["meta"]["duration"]
            snapped = True
        except SystemExit:
            # 缺镜头表不是停工理由：网格不吸附照样能钉桩，但必须在头部标出来
            src = shots.SHOTS_DIR / f"{anime}_{key}.json"
            print(f"WARN {key} 缺镜头表（{src}），本集边界不吸附切点——"
                  f"先跑 `python -m pipeline.shots build`", file=sys.stderr)
            from .ingest import load_sources
            duration = load_sources(anime)[key]["duration"]
            cuts, snapped = [], False
        grid = blocks(us, cuts, duration, gap)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{anime}_{key}.md"
        dest.write_text(render_md(anime, key, grid, gap, len(us), len(cuts), snapped),
                        encoding="utf-8")
        talk = sum(1 for b in grid if b["kind"] == "对白")
        print(f"{key}: {len(grid)} 块（对白 {talk} / 无台词 {len(grid) - talk}）→ {dest}")
        written.append(dest)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("anime")
    ap.add_argument("--episode", help="只出某一集，如 S01E01")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP,
                    help=f"无台词切块阈值秒数（默认 {DEFAULT_GAP:g}）")
    ap.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    a = ap.parse_args()
    run(a.anime, a.episode, a.gap, a.index_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
