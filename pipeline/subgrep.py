"""字幕逐字查询：notes_review 终审与补时间码的终端辅助（D61 后续）。

`subindex search` 是语义检索、agent 工具 `_search_subs` 是逐行子串匹配（±1 行上下文）；
本模块的匹配语义与引用核销完全一致（`_find_all`：整集拼接 + 归一化 + 找不到再折叠简繁），
查到的位置就是 `check_quote` 会认的位置。核销失败排查、多命中裁定、给笔记引文补时间码用它。

用法：
    python -m pipeline.subgrep <番> S01E03 涯大          # 逐字子串：每次命中带 ±3 行上下文
    python -m pipeline.subgrep <番> S01E03 涯大 5         # 上下文改为 ±5 行
    python -m pipeline.subgrep <番> S01E03 0:06:59 15     # 时间码模式：±15 秒窗口（默认 15）
"""

from __future__ import annotations

import argparse
import re
import sys

from . import paths
from .notes_review import _find_all, _mmss, norm, subtitle_lines, t2s, tc_seconds

_TC = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def hits_with_context(lines: list[tuple[float, str]], needle: str, ctx: int = 3
                      ) -> tuple[list[tuple[int, int, set[int]]], bool]:
    """needle 每次命中的上下文分组（与核销同语义：归一化；原样找不到再折叠简繁找一次）。

    返回 (groups, folded)：groups = [(起始行, 末行, 命中行集合)]，按行号，相邻命中的
    上下文重叠时合并成一组（「祈是 我的…」一集三命中那种裁定场景，免得同一幕重复打印）；
    folded=True 表示命中来自简繁折叠，展示仍用字幕原字形。
    """
    hits = _find_all(lines, norm(needle))
    folded = False
    if not hits:
        hits = _find_all([(t, t2s(s)) for t, s in lines], norm(t2s(needle)))
        folded = bool(hits)
    idx_of: dict[float, int] = {}
    for i, (t, _s) in enumerate(lines):
        idx_of.setdefault(t, i)
    groups: list[list] = []
    for h in hits:
        i = idx_of[h]
        lo, hi = max(0, i - ctx), min(len(lines) - 1, i + ctx)
        if groups and lo <= groups[-1][1] + 1:
            groups[-1][1] = max(groups[-1][1], hi)
            groups[-1][2].add(i)
        else:
            groups.append([lo, hi, {i}])
    return [(lo, hi, marks) for lo, hi, marks in groups], folded


def window(lines: list[tuple[float, str]], tc: float, span: float) -> list[tuple[float, str]]:
    return [(t, s) for t, s in lines if tc - span <= t <= tc + span]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.subgrep", description=__doc__.splitlines()[0])
    ap.add_argument("anime")
    ap.add_argument("key", help="S01E03 这种")
    ap.add_argument("query", help="逐字子串，或 h:mm:ss 时间码")
    ap.add_argument("extra", nargs="?", type=float, default=None,
                    help="子串模式：上下文行数（默认 3）；时间码模式：±秒（默认 15）")
    a = ap.parse_args(argv)
    paths.require_data()
    lines = subtitle_lines(a.anime, a.key.upper(), {})
    if lines is None:
        print(f"FAIL 没有字幕索引：{a.anime} {a.key}", file=sys.stderr)
        return 1
    if _TC.match(a.query):
        rows = window(lines, tc_seconds(a.query), a.extra if a.extra is not None else 15)
        for t, s in rows:
            print(f"{_mmss(t)} {s}")
        return 0 if rows else 1
    ctx = int(a.extra) if a.extra is not None else 3
    groups, folded = hits_with_context(lines, a.query, ctx)
    if not groups:
        print(f"找不到：{a.query!r}（全集归一化子串无命中，简繁折叠后也没有）")
        return 1
    if folded:
        print("（以下为简繁折叠后的命中，展示是字幕原字形；写引文要用原字形）")
    for lo, hi, marks in groups:
        print("——")
        for i in range(lo, hi + 1):
            t, s = lines[i]
            print(("* " if i in marks else "  ") + f"{_mmss(t)} {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
