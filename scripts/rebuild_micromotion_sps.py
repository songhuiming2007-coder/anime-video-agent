#!/usr/bin/env python3
"""EGOIST 24 条「照片微动」类素材画面布局重构与元数据错位修正脚本。"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "library" / "raw" / "EGOIST"
SOURCES_PATH = REPO_ROOT / "data" / "library" / "sources.json"
SHOTS_DIR = REPO_ROOT / "data" / "library" / "shots"


def make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000):
    return (
        f"split[bg][fg];"
        f"[bg]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"boxblur=25:5,colorchannelmixer=aa=1:rr={rr}:gg={gg}:bb={bb}[bg2];"
        f"[fg]scale=-2:{h}[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"zoompan=z='min(zoom+0.0003,1.03)':d=144:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s=1920x1080:fps=23.976"
    )


def make_cropped_filter(crop_str, rr=0.6, gg=0.6, bb=0.6, h=940):
    return (
        f"{crop_str},split[bg][fg];"
        f"[bg]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"boxblur=25:5,colorchannelmixer=aa=1:rr={rr}:gg={gg}:bb={bb}[bg2];"
        f"[fg]scale=-2:{h}[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"zoompan=z='min(zoom+0.0003,1.03)':d=144:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s=1920x1080:fps=23.976"
    )


TASKS = [
    # 1. SP06 supercell 海选公告传单 (612x495)
    {
        "sp": "SP06",
        "src": "/tmp/sp06_card_only.png",
        "vf": make_square_cover_filter(rr=0.5, gg=0.5, bb=0.5, h=1000),
        "target_name": "SP06-2011_supercell_两千人海选公告传单_微动.mp4",
    },
    # 2. SP07 chelly 独立 reche 概念视觉图 (860x860)
    {
        "sp": "SP07",
        "src": "/tmp/sp07_logo.png",
        "vf": make_square_cover_filter(rr=0.6, gg=0.6, bb=0.6, h=1000),
        "target_name": "SP07-2021_chelly_独立_reche_概念视觉图_微动.mp4",
    },
    # 3. SP08 2023 活动终了公告
    {
        "sp": "SP08",
        "src": "/tmp/shots2/end.png",
        "vf": make_cropped_filter("crop=1500:850:340:440", rr=0.6, gg=0.6, bb=0.6, h=960),
        "target_name": "SP08-2023_活动终了公告_微动.mp4",
    },
    # 4. SP09 Departures 封面扫图 (square)
    {
        "sp": "SP09",
        "src": "/Volumes/Samsung T7/EGOIST/Departures.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP09-Departures_单曲_CD_实体扫图_微动.mp4",
    },
    # 5. SP10 The Everlasting Guilty Crown 封面扫图 (square)
    {
        "sp": "SP10",
        "src": "/Volumes/Samsung T7/EGOIST/The Everlasting Guilty Crown.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP10-The_Everlasting_Guilty_Crown_单曲实体扫图_微动.mp4",
    },
    # 6. SP11 首专 EBBE 实体扫图 (square)
    {
        "sp": "SP11",
        "src": "/Volumes/Samsung T7/EGOIST/Extra terrestrial Biological Entities.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP11-首专_EBBE_实体扫图_微动.mp4",
    },
    # 7. SP12 伊藤计划三部曲《リローデッド》封面扫图 (square)
    {
        "sp": "SP12",
        "src": "/Volumes/Samsung T7/EGOIST/Reloaded.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP12-リローデッド_单曲封面扫图_微动.mp4",
    },
    # 8. SP13 《KABANERI OF THE IRON FORTRESS》封面扫图 (square)
    {
        "sp": "SP13",
        "src": "/Volumes/Samsung T7/EGOIST(chelly)/2016 - KABANERI OF THE IRON FORTRESS (甲铁城的卡巴内利 OP)/cover.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP13-KABANERI_OF_THE_IRON_FORTRESS_单曲封面扫图_微动.mp4",
    },
    # 9. SP14 《英雄 運命の詩》单曲实体封面超清扫图 (3000x3000)
    {
        "sp": "SP14",
        "src": "/Volumes/Samsung T7/EGOIST/英雄 運命の詩.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP14-英雄_運命_詩_单曲实体封面超清扫图_微动.mp4",
    },
    # 10. SP15 最後の花弁 封面扫图 (square 3000x3000)
    {
        "sp": "SP15",
        "src": "/Volumes/Samsung T7/EGOIST/最後の花弁.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP15-最後_花弁_单曲封面扫图_微动.mp4",
    },
    # 11. SP16 BANG!!! 迷你专辑封面扫图 (square 3000x3000)
    {
        "sp": "SP16",
        "src": "/Volumes/Samsung T7/EGOIST/BANG!!!.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP16-BANG_迷你专辑封面扫图_微动.mp4",
    },
    # 12. SP17 Gold 单曲封面扫图 (square 1400x1400)
    {
        "sp": "SP17",
        "src": "/Volumes/Samsung T7/EGOIST(chelly)/2022 - Gold (BUILD-DIVIDE OP)/Cover_01.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP17-Gold_单曲封面扫图_微动.mp4",
    },
    # 13. SP24 redjuice 绘《咲かせや咲かせ》单曲封面原画超清扫图 (2882x1650 横屏)
    {
        "sp": "SP24",
        "src": str(REPO_ROOT / "data/library/incoming/redjuice_绘_咲_咲_单曲封面原画超清扫图.jpg"),
        "vf": (
            "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            "zoompan=z='min(zoom+0.0003,1.03)':d=144:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            "s=1920x1080:fps=23.976"
        ),
        "target_name": "SP24-redjuice_绘_咲_咲_单曲封面原画超清扫图.mp4",
    },
    # 14. SP25 redjuice 绘 Greatest Hits 黑白旗袍祈妹概念图 (2150x3000 竖屏)
    {
        "sp": "SP25",
        "src": str(REPO_ROOT / "data/library/incoming/redjuice_官方画册收录_EGOIST_楪祈立绘超清扫图.jpg"),
        "vf": make_square_cover_filter(rr=0.35, gg=0.35, bb=0.35, h=1000),
        "target_name": "SP25-redjuice_官方画册收录_EGOIST_楪祈立绘超清扫图.mp4",
    },
    # 15. SP49 英雄 運命の詩 官方单曲封面 (600x595)
    {
        "sp": "SP49",
        "src": "/Volumes/Samsung T7/EGOIST/英雄 運命の詩.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP49-EGOIST_英雄運命_詩_官方单曲封面扫描_微动.mp4",
    },
    # 16. SP50 GREATEST HITS ALTER EGO 精选集封面扫描 (4000x4000)
    {
        "sp": "SP50",
        "src": "/Volumes/Samsung T7/EGOIST/GREATEST HITS 2011-2017 “ALTER EGO”.jpg",
        "vf": make_square_cover_filter(rr=0.4, gg=0.4, bb=0.4, h=1000),
        "target_name": "SP50-EGOIST_GREATEST_HITS_ALTER_EGO_精选集封面扫描_微动.mp4",
    },
    # 17. SP51 2012 Oricon 日榜第6位新闻
    {
        "sp": "SP51",
        "src": "/tmp/shots/otasuke_2012.png",
        "vf": make_cropped_filter("crop=720:435:170:240", rr=0.6, gg=0.6, bb=0.6, h=940),
        "target_name": "SP51-EGOIST_名前_怪物_日榜六位当时报道_2012_微动.mp4",
    },
    # 18. SP52 2015 Oricon 日榜第7位新闻
    {
        "sp": "SP52",
        "src": "/tmp/shots/otasuke_reloaded_7i.png",
        "vf": make_cropped_filter("crop=770:420:100:150", rr=0.6, gg=0.6, bb=0.6, h=940),
        "target_name": "SP52-EGOIST_伊藤计划三部曲合并_日榜七位报道_2015_微动.mp4",
    },
    # 19. SP53 2016 Oricon 日榜第1位新闻
    {
        "sp": "SP53",
        "src": "/tmp/shots/otasuke_kabaneri_1i.png",
        "vf": make_cropped_filter("crop=780:550:100:150", rr=0.6, gg=0.6, bb=0.6, h=940),
        "target_name": "SP53-EGOIST_KABANERI_甲铁城OP_日榜一位报道_2016_微动.mp4",
    },
    # 20. SP54 EGOIST 单曲 Oricon 一览表
    {
        "sp": "SP54",
        "src": "/tmp/shots/c4_tall.png",
        "vf": make_cropped_filter("crop=882:475:0:110", rr=0.6, gg=0.6, bb=0.6, h=940),
        "target_name": "SP54-EGOIST_单曲公信榜最高位表_微动.mp4",
    },
    # 21. SP55 EGOIST 专辑 Oricon 一览表
    {
        "sp": "SP55",
        "src": "/tmp/shots/c6_wikialbum.png",
        "vf": make_cropped_filter("crop=1400:845:0:135", rr=0.6, gg=0.6, bb=0.6, h=940),
        "target_name": "SP55-EGOIST_专辑公信榜最高位表_微动.mp4",
    },
    # 22. SP56 日本高校空教室 (1280x960 4:3)
    {
        "sp": "SP56",
        "src": "/tmp/hs_class.jpg",
        "vf": make_square_cover_filter(rr=0.5, gg=0.5, bb=0.5, h=1000),
        "target_name": "SP56-EGOIST_日本高校空教室_写实空镜_微动.mp4",
    },
    # 23. SP57 告别巡演售罄公告
    {
        "sp": "SP57",
        "src": "/tmp/shots2/tour.png",
        "vf": make_cropped_filter("crop=1900:1400:100:1140", rr=0.6, gg=0.6, bb=0.6, h=960),
        "target_name": "SP57-EGOIST_2023_日程与售罄公告_官方页_微动.mp4",
    },
    # 24. SP58 官方活动终了公告
    {
        "sp": "SP58",
        "src": "/tmp/shots2/end.png",
        "vf": make_cropped_filter("crop=1500:760:340:480", rr=0.6, gg=0.6, bb=0.6, h=960),
        "target_name": "SP58-EGOIST_2023活动终了公告原文_官方页_微动.mp4",
    },
]


def render_video(task):
    src = Path(task["src"])
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    target = RAW_DIR / task["target_name"]
    tmp_out = Path(f"/tmp/rebuild_{task['sp']}.mp4")

    cmd = [
        "ffmpeg",
        "-loop", "1",
        "-i", str(src),
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "slow",
        "-t", "6.006",
        "-vf", task["vf"],
        "-pix_fmt", "yuv420p",
        "-r", "23.976",
        "-an",
        "-y",
        str(tmp_out)
    ]
    print(f"[{task['sp']}] Rendering -> {tmp_out.name} ...")
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"Error rendering {task['sp']}:\n{res.stderr[-500:]}")
        sys.exit(1)

    # Move to target location
    shutil.move(str(tmp_out), str(target))
    print(f"[{task['sp']}] OK -> {target.name}")


def handle_renames():
    # SP12: old was SP12-Door_单曲封面扫图_微动.mp4
    old_sp12 = RAW_DIR / "SP12-Door_单曲封面扫图_微动.mp4"
    if old_sp12.exists():
        old_sp12.unlink()
        print("Removed old SP12 file")

    # SP13: old was SP13-Ghost_of_a_smile_单曲封面扫图_微动.mp4
    old_sp13 = RAW_DIR / "SP13-Ghost_of_a_smile_单曲封面扫图_微动.mp4"
    if old_sp13.exists():
        old_sp13.unlink()
        print("Removed old SP13 file")

    # SP14: old was SP14-Ninelie_单曲封面扫图_微动.mp4
    old_sp14 = RAW_DIR / "SP14-Ninelie_单曲封面扫图_微动.mp4"
    if old_sp14.exists():
        old_sp14.unlink()
        print("Removed old SP14 file")

    # SP18: rename without re-rendering
    old_sp18 = RAW_DIR / "SP18-演唱会视频_带B站弹幕误入库版_待重采.mp4"
    new_sp18 = RAW_DIR / "SP18-EGOIST_演唱会现场视频_含字幕纯净版.mp4"
    if old_sp18.exists():
        shutil.move(str(old_sp18), str(new_sp18))
        print("Renamed SP18 -> SP18-EGOIST_演唱会现场视频_含字幕纯净版.mp4")


def update_sources_json():
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    egoist = data["EGOIST"]
    
    # SP12
    egoist["SP12"]["path"] = "data/library/raw/EGOIST/SP12-リローデッド_单曲封面扫图_微动.mp4"
    egoist["SP12"]["title"] = "伊藤计划三部曲《リローデッド》单曲封面扫图 微动"

    # SP13
    egoist["SP13"]["path"] = "data/library/raw/EGOIST/SP13-KABANERI_OF_THE_IRON_FORTRESS_单曲封面扫图_微动.mp4"
    egoist["SP13"]["title"] = "《KABANERI OF THE IRON FORTRESS》单曲封面扫图 微动"

    # SP14
    egoist["SP14"]["path"] = "data/library/raw/EGOIST/SP14-英雄_運命_詩_单曲实体封面超清扫图_微动.mp4"
    egoist["SP14"]["title"] = "《英雄 運命の詩》单曲实体封面超清扫图 微动"

    # SP18
    egoist["SP18"]["path"] = "data/library/raw/EGOIST/SP18-EGOIST_演唱会现场视频_含字幕纯净版.mp4"
    egoist["SP18"]["title"] = "EGOIST 演唱会现场视频（含字幕、无弹幕版）"
    egoist["SP18"]["note"] = "抽帧复核：实测画面仅含字幕，无B站滚屏弹幕，可用"

    with open(SOURCES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Updated data/library/sources.json")


def update_shots_meta():
    mapping = {
        "EGOIST_SP12": "data/library/raw/EGOIST/SP12-リローデッド_单曲封面扫图_微动.mp4",
        "EGOIST_SP13": "data/library/raw/EGOIST/SP13-KABANERI_OF_THE_IRON_FORTRESS_单曲封面扫图_微动.mp4",
        "EGOIST_SP14": "data/library/raw/EGOIST/SP14-英雄_運命_詩_单曲实体封面超清扫图_微动.mp4",
        "EGOIST_SP18": "data/library/raw/EGOIST/SP18-EGOIST_演唱会现场视频_含字幕纯净版.mp4",
    }
    for key, new_source in mapping.items():
        p = SHOTS_DIR / f"{key}.json"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            d["meta"]["source"] = new_source
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            print(f"Updated {p.name} meta.source")


def main():
    print("=== Rebuilding 24 Micro-motion SPs ===")
    for task in TASKS:
        render_video(task)

    print("=== Handling Renames ===")
    handle_renames()

    print("=== Updating Metadata ===")
    update_sources_json()
    update_shots_meta()
    print("=== Done ===")


if __name__ == "__main__":
    main()
