"""一次性迁移：EGOIST 池 SP01–46 文件名加语义后缀 + 回填 sources.json title。

改名只动 SPxx.mp4 → SPxx-slug.mp4（同目录 rename，原子），同步修三处引用：
sources.json 的 path/title、shots 镜头表的 meta.source、抓取台账 fetched.json 的 file。
vindex captions / index / 文档锚点全部按 SP 数字主键寻址，不受影响。

幂等：已带后缀的文件跳过。名字来源：SP01–18 出自 01-assets-video.md 与
00-production-lessons.md 的阅卷记录，SP19–46 出自台账 title。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import paths  # noqa: E402
from pipeline.acquire import slugify  # noqa: E402

TITLES = {
    "SP01": "名前のない怪物 官方原版 MV",
    "SP02": "当事者 官方 MV（2023 终局封门单曲）",
    "SP03": "咲かせや咲かせ 官方动画 MV",
    "SP04": "EGOIST LIVE 2020 线上全息演唱会",
    "SP05": "EGOIST LIVE 2023 横滨终场全场",
    "SP06": "2011 supercell 两千人海选公告传单 微动",
    "SP07": "2021 chelly 独立 reche 概念视觉图 微动",
    "SP08": "2023 活动终了公告 微动",
    "SP09": "Departures 单曲 CD 实体扫图 微动",
    "SP10": "The Everlasting Guilty Crown 单曲实体扫图 微动",
    "SP11": "首专 EBBE 实体扫图 微动",
    "SP12": "Door 单曲封面扫图 微动",
    "SP13": "Ghost of a smile 单曲封面扫图 微动",
    "SP14": "Ninelie 单曲封面扫图 微动",
    "SP15": "最後の花弁 单曲封面扫图 微动",
    "SP16": "BANG!!! 迷你专辑封面扫图 微动",
    "SP17": "Gold 单曲封面扫图 微动",
    "SP40": "Drawing with Wacom 画师 redjuice 绘制楪祈插画过程实录",
    "SP18": "演唱会视频 带B站弹幕误入库版 待重采",
}
LEDGER = json.loads((paths.DATA / "library" / "incoming" / "fetched.json")
                    .read_text(encoding="utf-8"))
TITLES.update({e["as"]: e["title"] for e in LEDGER if e.get("as")})


def main() -> None:
    raw = paths.DATA / "library" / "raw" / "EGOIST"
    src_p = paths.DATA / "library" / "sources.json"
    db = json.loads(src_p.read_text(encoding="utf-8"))
    led_p = paths.DATA / "library" / "incoming" / "fetched.json"
    renamed = skipped = 0
    for key in sorted(db["EGOIST"]):
        title = TITLES.get(key)
        if not title:
            print(f"!! {key} 没有名字来源，跳过（请人工补 TITLES 后重跑）")
            continue
        entry = db["EGOIST"][key]
        entry["title"] = title
        old = raw / f"{key}.mp4"
        new = raw / f"{key}-{slugify(title)}.mp4"
        rel = str(new.relative_to(paths.ROOT))
        if entry["path"] == rel and not old.exists():
            skipped += 1
            continue
        if old.exists():
            if new.exists():
                raise SystemExit(f"FAIL 目标已存在 {new}，先人看一眼")
            old.rename(new)
            renamed += 1
        elif not new.exists():
            raise SystemExit(f"FAIL {key} 新旧文件都不在，sources.json 指向 {entry['path']}")
        entry["path"] = rel
        sp = int(re.fullmatch(r"SP(\d+)", key).group(1))
        shots_p = paths.DATA / "library" / "shots" / f"EGOIST_SP{sp:02d}.json"
        if shots_p.exists():
            d = json.loads(shots_p.read_text(encoding="utf-8"))
            d["meta"]["source"] = rel
            paths.atomic_write(shots_p, json.dumps(d, ensure_ascii=False, indent=2))
        for e in LEDGER:
            if e.get("as") == key:
                e["file"] = str(new)
        print(f"  {key} → {new.name}")
    paths.atomic_write(src_p, json.dumps(db, ensure_ascii=False, indent=2))
    paths.atomic_write(led_p, json.dumps(LEDGER, ensure_ascii=False, indent=2))
    print(f"OK 改名 {renamed}，已是最新 {skipped}；sources.json / shots / 台账已同步")


if __name__ == "__main__":
    main()
