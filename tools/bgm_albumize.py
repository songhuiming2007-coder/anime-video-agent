#!/usr/bin/env python3
"""把平铺的 bgm/<番>/ 重排成「桶/专辑」两级目录，并同步 config/bgm.json。

规则（docs/runbook/01-topic.md 2026-11-08 约定）：曲目按来源分桶
`单曲/专辑/现场/原声带/原曲-<来源>/伴奏/合作-feat/_未归类`，桶下再按
专辑或单曲名一层。归属由 `--map` 给的 JSON 决定，脚本不猜：

    {"曲名": "单曲/六等星の夜", ...}

未在 map 里的曲子落 `_未归类/` 并在报告里列出（不静默丢）。

    uv run python tools/bgm_albumize.py EGOIST --map /tmp/egoist.json            # 预览
    uv run python tools/bgm_albumize.py EGOIST --map /tmp/egoist.json --apply     # 落盘
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import paths  # noqa: E402


def load_cfg() -> dict:
    return json.loads((paths.CONFIG / "bgm.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("anime")
    ap.add_argument("--map", required=True, type=Path, help="曲名 → 桶/专辑 的 JSON")
    ap.add_argument("--apply", action="store_true", help="不给我只预览")
    a = ap.parse_args()

    mapping: dict[str, str] = json.loads(a.map.read_text(encoding="utf-8"))
    cfg = load_cfg()
    pool = cfg.get(a.anime, {})
    tracks = pool.get("tracks", {})
    if not tracks:
        raise SystemExit(f"FAIL config/bgm.json 里没有 {a.anime} 的 tracks")

    plan: list[tuple[str, Path, Path]] = []
    unclassified: list[str] = []
    for name, rec in tracks.items():
        src = ROOT / rec["path"] if not rec["path"].startswith("/") else Path(rec["path"])
        parent = src.parent
        if parent.name == "raw" or parent.parent.name == a.anime:
            pass  # 平铺期：父目录就是池根
        # map 没给的保留原有 album（老池重排时不会把已分好的桶冲进 _未归类）
        bucket = mapping.get(name) or rec.get("album")
        if not bucket:
            bucket = "_未归类"
            unclassified.append(name)
        dest_dir = paths.DATA / "library" / "bgm" / a.anime / bucket
        plan.append((name, src, dest_dir / src.name))

    moved = 0
    for name, src, dest in plan:
        if not src.exists():
            print(f"跳过（源不存在）{name}: {src}")
            continue
        if src.resolve() == dest.resolve():
            continue
        if a.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        moved += 1
        print(f"{'移' if a.apply else '将移'} {a.anime}/{src.name} → {dest.parent.name}/")

    if a.apply:
        for name, _src, dest in plan:
            rec = tracks.get(name)
            if rec is not None and dest.exists():
                rec["path"] = str(dest.relative_to(ROOT)) if str(dest).startswith(str(ROOT)) else str(dest)
                rec["album"] = dest.parent.name
        (paths.CONFIG / "bgm.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_readme(a.anime, tracks)
        print(f"\n已登记 {moved} 条 → config/bgm.json")
    else:
        print(f"\n预览：{moved} 条待移（加 --apply 落盘）")
    if unclassified:
        print(f"未归类 {len(unclassified)} 条：{', '.join(unclassified[:8])}{' …' if len(unclassified) > 8 else ''}")
    return 0


def write_readme(anime: str, tracks: dict) -> None:
    """桶 → 曲名 → 时长/伴唱 的索引，给人查。"""
    by_album: dict[str, list[tuple[str, dict]]] = {}
    for name, rec in tracks.items():
        by_album.setdefault(rec.get("album", "_未归类"), []).append((name, rec))
    lines = [f"# {anime} 曲库索引", "", "自动生成，按目录桶分组；重新登记后重跑 `tools/bgm_albumize.py` 会刷新。", ""]
    for album in sorted(by_album):
        lines.append(f"## {album}")
        lines.append("")
        lines.append("| 曲名 | 时长 | 伴唱 | 来源 |")
        lines.append("|---|---|---|---|")
        for name, rec in sorted(by_album[album]):
            dur = rec.get("dur")
            dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}" if isinstance(dur, (int, float)) else "?"
            vocal = {True: "有", False: "无", None: "未知"}.get(rec.get("vocal"), "未知")
            lines.append(f"| {name} | {dur_s} | {vocal} | {rec.get('from') or ''} |")
        lines.append("")
    out = paths.DATA / "library" / "bgm" / anime / "README.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"索引 → {out}")


if __name__ == "__main__":
    raise SystemExit(main())
