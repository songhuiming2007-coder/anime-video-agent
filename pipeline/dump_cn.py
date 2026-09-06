#!/usr/bin/env python3
"""从 .Chs&Jap.ass 提取 CN 样式对白行（含时间码），供笔记加厚取证用。

    python -m pipeline.dump_cn <字幕.ass>       # 只收 CN/TITLE/Default 样式
    python -m pipeline.dump_cn <字幕.ass> jp    # 连 JP 样式一起收

2026-09-05 从 tools/ 收编进 pipeline/（审计批次3：tools/ 目录不在目录约定里，
全库零引用的孤儿脚本要么收编要么删——这个在 9/3 笔记加厚里真在用）。
"""
import re, sys

def main(path, want_jp=False):
    styles = {"CN", "TITLE", "Default", "Title"} | ({"JP"} if want_jp else set())
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        if not line.startswith("Dialogue"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10 or parts[3] not in styles:
            continue
        text = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", " ").strip()
        if not text:
            continue
        out.append((parts[1], parts[3], text))
    out.sort()
    for t, st, tx in out:
        print(f"{t}\t{st}\t{tx}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("FAIL 用法：python -m pipeline.dump_cn <字幕.ass> [jp]")
    main(sys.argv[1], len(sys.argv) > 2)
