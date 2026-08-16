"""封面候选与标题原料（流程第 08 步）。

    python -m pipeline.cover data/episodes/<本期>

产物 `07-cover/`（候选帧 + 一页 HTML）与 `07-titles.md`（标题原料，标题本身由 agent 写）。

**分工是硬的：机器筛掉不能当封面的帧，不假装能判「哪张有张力」。**
清晰度、黑场、重复镜头有明确判据，机器筛；「吸引眼球」没有判据，
机器给 9 张候选，人在第 09 步点一张。把审美判断包装成机器判据，
等于把一个说不清的标准写死在代码里，比不做更糟。

**候选池可以先按「含本期主角」过滤**（`--character` 或 `01-topic.md` 的 `封面人物`）。
判据来自视觉索引（ADR-0003 第 1 层），不是这里自己算的。不写就是现状：不过滤。

这一处用**硬过滤**，与排片那边的软过滤不同：从几千帧里挑 9 张，高精度低召回正合适，
误杀无所谓。而排片一段只有几个候选，误杀就没片可用了。

**这不违反「机器不假装能判哪张有张力」**：在不在场是有明确判据的准入门槛，
和清晰度、黑场同类；它不给候选排名，9 张候选依然不排序，依然由人在第 09 步点一张。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from . import paths
from .ingest import load_sources

STEP = 0.5             # 抽帧间隔（秒）
KEEP = 9               # 最终给几张候选
THUMB_W = 640          # 候选图宽度，够判断构图
DARK, BRIGHT = 40, 215  # 平均亮度落在这个区间外就是黑场/过曝（0–255）
SHARP_MIN = 60.0       # 拉普拉斯方差下限，低于此判为转场或运动模糊
HASH_DIST = 8          # dHash 汉明距离，小于此视为同一个画面
MIN_GAP = 6.0          # 同一集里相隔不到这么多秒，算同一个镜头。动画单镜头通常 2–5 秒
EP_STEP = 12.0         # 锚点集通篇取样的间隔
SKIP_HEAD, SKIP_TAIL = 100.0, 110.0   # 跳过 OP / ED，它们是固定素材，做封面没信息量

# 联系表：给 agent 看的，不是给人看的。人只看最后 9 张。
SHEET_COLS, SHEET_ROWS, SHEET_W = 6, 5, 300


def _episode_points(anime: str, topic: Path) -> list[tuple[str, float, str]]:
    """在本期锚点集上通篇取样。

    **为什么要这一路：** 只从视频用上的片段里取，池子会系统性地偏。
    那些片段是靠字幕语义检索选的，命中的是「那句台词在哪」，
    而稿子引用的台词大多是**别人说给主角听的**——镜头自然在说话人身上。
    2026-07-29 实测：九张候选里**没有一张主角占主位**，三张是背影或侧脸，
    六张根本没有他。筛选救不了这个，池子里就没有想要的东西。

    「只用片中出现过的画面，否则是标题党」这条当初写在这里，用过头了。
    给一期讲主角的视频配主角的正脸不是标题党，是名副其实；
    真正算标题党的是拿一场不相干的戏去暗示别的内容。

    跳过片头片尾：OP/ED 是固定素材，做封面既没信息量又和本期无关。
    """
    if not topic.exists():
        return []
    anchors = _topic_episodes(topic)
    if not anchors:
        return []
    sources, pts = load_sources(anime), []
    for key in dict.fromkeys(anchors):
        rec = sources.get(key)
        if not rec:
            continue
        t, end = SKIP_HEAD, rec["duration"] - SKIP_TAIL
        while t < end:
            pts.append((rec["path"], round(t, 2), f"通篇 {key}"))
            t += EP_STEP
    return pts


def _sample_points(plan: dict) -> list[tuple[str, float, str]]:
    """候选帧的取样点：(源文件, 秒, 说明)。取本期实际用上的片段。"""
    pts = []
    for seg in plan["segments"]:
        for c in seg["clips"]:
            t, end = c["start"], c["start"] + c["dur"]
            while t < end:
                pts.append((c["source"], round(t, 2),
                            f'段{seg["index"]} S{c["season"]:02d}E{c["episode"]:02d}'))
                t += STEP
    return pts


def _notes_points(anime: str, topic: Path) -> list[tuple[str, float, str]]:
    """番剧笔记里本期锚点对应的名场面时间码。

    笔记里那些 `| 07:30 | 台词 |` 是**已经人工标注过有张力**的时刻，
    比从片段里盲抽强——它们本来就是被挑出来写进笔记的。
    只取本期 `01-topic.md` 锚点字段点名的那几集，不是整部番。

    片源表用 `ingest.load_sources`，**不要自己再解析一遍 `sources.json`**。
    初版就是自己写的，schema 猜错（以为记录里有 season/episode 字段，实际是
    `{"S02E02": {...}}` 这样按键分的），于是永远返回空——**而且不报错**，
    整条名场面通路静默失效，光看输出「抽 398 帧 → 候选 9 张」完全看不出来。
    """
    notes = paths.DATA / "library" / "notes" / f"{anime}.md"
    if not (notes.exists() and topic.exists()):
        return []
    anchors = set(_topic_episodes(topic))
    if not anchors:
        return []
    sources = load_sources(anime)

    pts, cur = [], None
    for line in notes.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^#{2,4}\s*S(\d)E(\d{1,2})", line.strip()):
            cur = f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
        elif cur in anchors and (m := re.match(r"^\|\s*(\d{1,2}):(\d{2})\s*\|", line)):
            rec = sources.get(cur)
            if rec:
                t = int(m.group(1)) * 60 + int(m.group(2))
                # 名场面时间码指的是那句台词开始，往后半秒画面更稳。
                # 截取守卫同样适用：超出片尾 ffmpeg 会静默给最后一帧。
                if t + 0.5 < rec["duration"]:
                    pts.append((rec["path"], t + 0.5, f"名场面 {cur}"))
    if anchors and not pts:
        print(f"  注意  锚点 {sorted(anchors)} 在笔记里没找到带时间码的名场面表，"
              f"本次只用片段抽帧")
    return pts


def _topic_character(topic: Path) -> str | None:
    """`01-topic.md` 的 `封面人物` 字段。不写就返回 None（不过滤，即现状）。

    与 `冠军` / `出场` 分开是因为它们答的是不同问题：`出场` 是这一期讲了谁，
    `封面人物` 是**封面上要有谁**。一期讲七个人，封面只能放一个。
    """
    if not topic.exists():
        return None
    for line in topic.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^封面人物\s*[:：]\s*(\S+)", line.strip()):
            return m.group(1)
    return None


def _by_character(cands: list[dict], anime: str, name: str) -> list[dict]:
    """候选池只留含该角色的帧。**硬过滤，为空则失败。**

    为空不退回：这里是「你明确要求了封面上有谁」，悄悄给一池子没有他的帧，
    等于把要求吞了——而人看到 9 张候选时并不知道过滤没生效。
    """
    from . import vindex

    pres = vindex.load_presence(anime)
    by_path = {rec["path"]: key for key, rec in load_sources(anime).items()}
    missing = {c["src"] for c in cands if c["src"] not in by_path}
    if missing:
        raise SystemExit(f"FAIL 片源登记表里找不到 {sorted(missing)[0]}，无法定位集号")

    kept = []
    for c in cands:
        key = by_path[c["src"]]
        season, episode = int(key[1:3]), int(key[4:6])
        if key not in pres.episodes():
            continue                   # 该集没建视觉索引，宁可不要也不蒙
        if pres.present(season, episode, c["t"], c["t"] + 0.001, name):
            kept.append(c)
    if not kept:
        raise SystemExit(
            f"FAIL 候选池里没有一帧检测到「{name}」（共 {len(cands)} 帧）。\n"
            f"     要么锚点集/封面集里他确实没出现，要么这几集还没建视觉索引：\n"
            f"     python -m pipeline.vindex status --anime {anime}")
    return kept


def _topic_episodes(topic: Path) -> list[str]:
    """`01-topic.md` 里点名的集号，按出现顺序去重。

    读两个字段，**`封面集` 排在前面**：

    | 字段 | 含义 |
    |---|---|
    | `锚点` | 稿子引用了哪几集。这是内容判据，稿件机检也看它 |
    | `封面集` | 封面该从哪几集取样。**可选**，不写就等于只用 `锚点` |

    分开是因为这本来是两件事。2026-07-30 要给「雪乃最适合大老师」配封面时，
    最好的素材在 S3E12（她告白那一集），而稿子一句都没引 S3E12——
    往 `锚点` 里塞它会污染那个字段的含义。
    CLAUDE.md 已经写明「给一期讲主角的视频配主角的正脸不是标题党，是名副其实」，
    所以封面池本来就不必局限于稿子引过的集。

    **OVA 记作该季的 E00**，与 `ingest.phase0` 的编号一致；
    笔记里写作「S1OVA」，这里两种写法都认。
    """
    eps: list[str] = []
    for line in topic.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not (s.startswith("封面集") or s.startswith("锚点")):
            continue
        for m in re.finditer(r"S(\d)\s*(?:E(\d{1,2})|(OVA))", s, re.I):
            ep = 0 if m.group(3) else int(m.group(2))
            key = f"S{int(m.group(1)):02d}E{ep:02d}"
            if key not in eps:
                eps.append(key)
    return eps


def _grab(src: str, t: float, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", src,
         "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", "-q:v", "3", str(dest)],
        check=False, capture_output=True)
    return dest if dest.exists() else None


def _metrics(p: Path) -> dict | None:
    """三个都有明确判据的量：亮度、清晰度、饱和度。都不涉及「好不好看」。"""
    try:
        im = Image.open(p).convert("RGB")
    except (OSError, ValueError):
        # 只吞「这一帧读不出来」——抽帧偶尔会写出截断的 jpg，跳过即可。
        # **别写成 `except Exception`**：那样 Pillow 没装好、内存不足这类真问题
        # 也会变成「这张跳过」，841 帧全跳过之后只会看到「候选 0 张」，
        # 完全看不出根因。2026-07-29 审计收窄。
        return None
    a = np.asarray(im, dtype=np.float32)
    g = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    # 拉普拉斯方差：清晰的边缘会让二阶差分幅度大。转场帧和运动模糊帧没有清晰边缘。
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1])
    # 饱和度：平台缩略图小，低饱和的帧会糊成一片灰。
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = float(np.mean(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)))

    # dHash：缩到 9x8 比较左右相邻像素，得 64 位。同一个镜头的相邻帧哈希几乎相同。
    small = np.asarray(im.convert("L").resize((9, 8), Image.BILINEAR), dtype=np.float32)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return {"bright": float(g.mean()), "sharp": float(lap.var()),
            "sat": sat, "hash": np.packbits(bits)}


def _dedup(cands: list[dict]) -> list[dict]:
    """同一个镜头只留最清晰的一张。

    **这是最要紧的一道筛**。按 0.5 秒抽帧，一个 4 秒的镜头出 8 张几乎一样的图；
    不去重的话 9 张候选可能全是同一个镜头，等于只给了人一个选择。

    两道判据，缺一不可：

    1. **同一源文件里相隔不到 `MIN_GAP` 秒 = 同一个镜头。** 镜头是时间上的连续区间，
       这是它的定义，直接按时间判最可靠。
    2. dHash 相近 = 同一个画面。管的是另一回事——同一个镜头在片中**不同时刻**复现
       （正反打来回切），时间判不出来，得靠画面判。

    初版只有第 2 条，结果九个候选里有四个是 S02E02 16:13/16:14/16:15/16:19，
    同一场戏几秒之内的四张几乎一样的图。**感知哈希对「同镜头内角色微动」不敏感**：
    人物挪一点、口型变一下，64 位里就能差出十几位。把阈值放宽到能合并它们，
    又会开始误合并真正不同的镜头。用错工具了，加宽阈值救不回来。
    """
    kept: list[dict] = []
    for c in sorted(cands, key=lambda x: -x["sharp"]):
        if any(c["src"] == k["src"] and abs(c["t"] - k["t"]) < MIN_GAP for k in kept):
            continue
        if all(int(np.unpackbits(c["hash"] ^ k["hash"]).sum()) > HASH_DIST for k in kept):
            kept.append(c)
    return kept


def _sheets(cands: list[dict], out_dir: Path) -> list[Path]:
    """把候选拼成联系表，**给 agent 看的，不是给人看的**。

    「谁在画面上、占不占主位」是认人，机器判不了（标准人脸模型对二次元无效），
    但 agent 能看图。所以分三层：机器做机械筛选出一个大池子 → agent 逐张看、
    按内容挑出符合本期主题的 → 人从最后 9 张里定一张。

    拼成大图而不是逐张看，纯粹是为了省 token：一张 6×5 的表顶 30 次单张读取。
    """
    from PIL import ImageDraw
    made, per = [], SHEET_COLS * SHEET_ROWS
    for s in range((len(cands) + per - 1) // per):
        batch = cands[s * per:(s + 1) * per]
        cell_h = int(SHEET_W * 9 / 16)
        sheet = Image.new("RGB", (SHEET_COLS * SHEET_W,
                                  SHEET_ROWS * (cell_h + 18)), (20, 22, 26))
        d = ImageDraw.Draw(sheet)
        for i, c in enumerate(batch):
            x, y = (i % SHEET_COLS) * SHEET_W, (i // SHEET_COLS) * (cell_h + 18)
            sheet.paste(Image.open(c["file"]).resize((SHEET_W, cell_h)), (x, y))
            d.text((x + 4, y + cell_h + 3),
                   f'{c["n"]}  {c["tag"]} {int(c["t"]) // 60:02d}:{int(c["t"]) % 60:02d}',
                   fill=(210, 214, 220))
        p = out_dir / f"sheet-{s + 1}.jpg"
        sheet.save(p, quality=88)
        made.append(p)
    return made


def _spread(cands: list[dict]) -> list[dict]:
    """从去重后的候选里挑 9 张，**按位置铺开，不按任何分数排**。

    **这里没有排序依据，所以不排。** 试过两版打分，两版都把正确答案压掉了：

        清晰度^0.5 × (0.5+饱和度)   把「叶山和八幡对视」压到第 5——它背景是淡色的
        纯清晰度                     选出运动服店的货架和图书室的书架，主角一个都没有

    第二版暴露了根因：**拉普拉斯方差测的是「画面里有多少细节」，不是「画面有多好」。**
    杂乱背景细节最多，按它排等于专挑最杂乱的画面，与封面要的「主体突出」正好相反。
    清晰度是**准入门槛**（滤掉转场和运动模糊），不是排序依据，两者不能混用。

    换掉它的不是更好的打分函数——饱和度、中心细节、对比度都是我自己发明的判据，
    没有数据支撑。真正该做的是**取一份有代表性的样本交给人**：

    1. 留 2 个坑给名场面（笔记里人工标注过有张力的时刻，来源本身就带判断）
    2. 其余按段落号均分成若干档，每档取最清晰的一张——保证候选覆盖全片，
       而不是挤在某一段

    机器只做「剔掉不能用的、去掉重复的、铺开取样」，一个审美判断都不做。
    """
    famous = sorted((c for c in cands if c["tag"].startswith("名场面")),
                    key=lambda c: -c["sharp"])[:2]
    rest = [c for c in cands if c not in famous]
    if not rest:
        return famous[:KEEP]

    n = KEEP - len(famous)
    order = sorted(rest, key=lambda c: (c["tag"], c["t"]))
    picks, step = [], max(1, len(order) // n)
    for i in range(n):
        chunk = order[i * step: (i + 1) * step] if i < n - 1 else order[i * step:]
        if chunk:
            picks.append(max(chunk, key=lambda c: c["sharp"]))
    return famous + picks


CSS = """
:root { color-scheme: dark light; }
body { margin:0; padding:24px; background:#14161a; color:#e6e8eb;
       font:15px/1.6 -apple-system,"PingFang SC",sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:#8b929c; font-size:13px; margin-bottom:20px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:16px; }
figure { margin:0; }
figure img { width:100%; border-radius:6px; display:block; }
figcaption { font-size:12px; color:#8b929c; margin-top:6px;
             font-variant-numeric:tabular-nums; }
figcaption b { color:#e6e8eb; font-weight:600; }
@media (prefers-color-scheme: light){
  body{background:#fff;color:#1a1c20} .meta,figcaption{color:#5f6672}
  figcaption b{color:#1a1c20}
}
"""


def build(episode: Path, anime: str | None = None,
          pick: list[int] | None = None,
          character: str | None = None) -> tuple[Path, int, int]:
    anime = anime or paths.conf("anime.default")
    if not anime:
        raise SystemExit("FAIL 没指定番名：给 --anime，或在 config/project.json 里设 anime.default")
    plan = episode / "04-clips.approved.json"
    if not plan.exists():
        raise SystemExit(f"FAIL 缺 {plan}，先过第 05 步（review --approve）")
    data = json.loads(plan.read_text(encoding="utf-8"))
    out_dir = episode / "07-cover"
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    clip_pts = _sample_points(data)
    notes_pts = _notes_points(anime, episode / "01-topic.md")
    ep_pts = _episode_points(anime, episode / "01-topic.md")
    pts = clip_pts + notes_pts + ep_pts
    print(f"  取样  片段 {len(clip_pts)} + 名场面 {len(notes_pts)} + 锚点集通篇 {len(ep_pts)}"
          f" = {len(pts)} 帧")

    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(
            lambda it: _grab(it[1][0], it[1][1], raw / f"f{it[0]:04d}.jpg"),
            enumerate(pts)))
        mets = list(pool.map(lambda f: _metrics(f) if f else None, files))

    cands = []
    for (src, t, tag), f, m in zip(pts, files, mets):
        if f is None or m is None:
            continue
        if not (DARK <= m["bright"] <= BRIGHT) or m["sharp"] < SHARP_MIN:
            continue
        cands.append({**m, "file": f, "src": src, "t": t, "tag": tag})

    total = len(pts)

    # **角色过滤排在去重之前。** 去重每个镜头只留最清晰的一张，若先去重，
    # 一个「主角在场但不是最清晰那张」的镜头会先被它的邻居顶掉，再被角色过滤放行——
    # 结果是这个镜头整个消失。先过滤再去重，去重是在已经含主角的帧里挑最清晰的。
    character = character or _topic_character(episode / "01-topic.md")
    if character:
        before = len(cands)
        cands = _by_character(cands, anime, character)
        print(f"  角色过滤  含「{character}」{len(cands)} / {before} 帧")

    deduped = _dedup(cands)
    deduped.sort(key=lambda c: (c["tag"], c["t"]))
    for n, c in enumerate(deduped, 1):
        c["n"] = n
    (out_dir / "pool.json").write_text(json.dumps(
        [{"n": c["n"], "src": c["src"], "t": c["t"], "tag": c["tag"],
          "file": str(c["file"])} for c in deduped], ensure_ascii=False), encoding="utf-8")
    sheets = _sheets(deduped, out_dir)
    print(f"  联系表 {len(sheets)} 张（给 agent 挑，人不用看）→ {out_dir}/sheet-*.jpg")

    if pick:
        by_n = {c["n"]: c for c in deduped}
        missing = [i for i in pick if i not in by_n]
        if missing:
            raise SystemExit(f"FAIL 池子里没有编号 {missing}（共 {len(deduped)} 张）")
        kept = [by_n[i] for i in pick]
    else:
        kept = _spread(deduped)

    if not kept:
        # 池子筛空 = 流程断了，不是「这期没有封面」的正常状态（2026-08-16
        # 审计 2-14）。旧实现静默写空 index.html、退出码 0——角色硬过滤
        # 为空早已显式失败（_by_character），无过滤的空池是同类「跳过不是
        # 通过」却放行。报出漏斗各层数字，让人看见是在哪一层筛空的。
        raise SystemExit(
            f"FAIL 候选池筛空：抽 {total} 帧 → 过筛 {len(cands)} → 去重 "
            f"{len(deduped)} → 取 0 张。\n"
            f"     全部帧被亮度/清晰度筛掉（整集过暗或糊？）或取样源全失效"
            f"（_grab 失败）。看 07-cover/raw/ 有没有图、pool.json 的取样数，"
            f"再查 04-clips.approved.json / 封面集 配置")

    # **只报告，不动手。** 中间产物（841 张原始帧 + 12 张联系表 ≈ 28M）确实该清，
    # 但清理时机是**人拍板最终版之后**，而且**每次要主动问过人**（CLAUDE.md「清理约定」）。
    # 初版写成 `--pick` 时自动删——那是把「什么时候算定稿」这个判断替人做了：
    # 挑完九张只是给人过目，人完全可能说「都不行，重挑」，那时缓存正好还要用。
    # 脚本报出体积和命令，删不删由人说。
    scratch = list(raw.glob("*.jpg")) + list(out_dir.glob("sheet-*.jpg"))
    if scratch:
        mb = sum(f.stat().st_size for f in scratch) / 2**20
        print(f"  中间产物 {len(scratch)} 个文件 / {mb:.0f}M（原始帧 + 联系表）。"
              f"定稿后可清，重跑约 2 分钟能重生成。")

    for n, c in enumerate(kept, 1):
        dest = out_dir / f"cover-{n}.jpg"
        dest.write_bytes(c["file"].read_bytes())
        c["out"] = dest

    body = "".join(
        f'<figure><img src="{c["out"].name}" loading="lazy">'
        f'<figcaption><b>{n}</b>　{html.escape(c["tag"])}　'
        f'{int(c["t"]) // 60:02d}:{int(c["t"]) % 60:02d}　'
        f'清晰 {c["sharp"]:.0f}　饱和 {c["sat"]:.2f}</figcaption></figure>'
        for n, c in enumerate(kept, 1))

    page = out_dir / "index.html"
    page.write_text(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(episode.name)} 封面候选</title>"
        f"<style>{CSS}</style>"
        f"<h1>{html.escape(episode.name)}　封面候选</h1>"
        f'<div class="meta">抽 {total} 帧 → 过筛 {len(cands)} → 去重 {len(deduped)} '
        f"→ 取前 {len(kept)}。<br>"
        f"机器只做三件事：<b>剔掉黑场/过曝/转场糊帧、去掉重复镜头、按位置铺开取样</b>。"
        f"<br>候选之间<b>没有排名</b>——序号只是编号。哪张有张力是你判，"
        f"机器没有这个判据，也不该假装有。<br>"
        + (f"已按「<b>{html.escape(character)}</b>」过滤：只留检测到他/她的帧。"
           f"检测会漏侧脸和背影，所以有些能用的镜头没进来；反过来，进来的基本都真的有他。"
           if character else
           "本次<b>没有</b>按角色过滤（`01-topic.md` 里写 `封面人物: 某某` 或加 "
           "`--character` 可以开启），空镜和没有主角的帧可能混在里面，你直接跳过。")
        + "</div>"
        f'<div class="grid">{body}</div>',
        encoding="utf-8")
    return page, total, len(kept)


def titles(episode: Path) -> Path:
    """抽标题原料。**标题本身不由代码生成**——那是写作，不是字符串处理。

    抽的是稿件里最锋利的三处：抬头的靶子、段落 1（张力）、最后一段（结论）。
    骨架第 2 节「亮判断」通常在段落 2，一并给出。
    """
    dest = episode / "07-titles.md"
    # **写过的标题不许覆盖。** 封面和标题在同一个命令里出，而封面往往要重跑几次
    # （调取样、调筛选），每次都重写这个文件就会把已经写好的标题冲掉——
    # 2026-07-29 实测冲掉过一次。已填表就只当没这回事。
    if dest.exists() and re.search(r"^\|\s*\d\s*\|\s*\S", dest.read_text(encoding="utf-8"), re.M):
        print("  标题已写过，跳过（要重出原料先删 07-titles.md）")
        return dest

    script = (episode / "02-script.md").read_text(encoding="utf-8")
    head = {}
    for line in script.splitlines()[:12]:
        if m := re.match(r"^(标题|选题|模式|靶子|张力)\s*[:：]\s*(.+)$", line.strip()):
            head[m.group(1)] = m.group(2).strip()
    h1 = next((l[2:].strip() for l in script.splitlines() if l.startswith("# ")), "")

    says = re.findall(r"^配音\s*[:：]\s*(.+)$", script, re.M)
    # 末段通常是收尾语（「本期视频就到这里，下期再见」），不是结论。
    # 初版直接取 says[-1]，抽出来的就是这句，等于把最没信息量的一句当成了全篇结论。
    # 收尾语的特征是短且带套话，按这两条剔。
    SIGNOFF = ("下期", "就到这里", "再见", "点个关注", "感谢观看")
    meat = [s for s in says if not (len(s) < 40 and any(w in s for w in SIGNOFF))]
    dest.write_text(
        f"# 标题与简介候选\n\n"
        f"> 本文件由 `pipeline.cover` 抽出原料，**标题由 agent 写**，写完覆盖本节。\n"
        f"> 判据见 CLAUDE.md「内容参数」：**金句式，不要论文式**——\n"
        f"> 这句话能不能脱离视频单独发出去。自己账号的两端实例见 `CLAUDE.local.md`。\n\n"
        f"## 候选（5 条，注明路子）\n\n"
        f"| # | 路子 | 标题 |\n|---|---|---|\n"
        + "".join(f"| {i} | | |\n" for i in range(1, 6))
        + f"\n## 简介\n\n（待写）\n\n## 标签\n\n（待写）\n\n---\n\n"
        f"## 原料\n\n"
        f"**稿件抬头**：{h1}\n\n"
        f"**靶子**：{head.get('靶子', '—')}\n\n"
        f"**张力（段落 1）**：{meat[0] if meat else '—'}\n\n"
        f"**亮判断（段落 2）**：{meat[1] if len(meat) > 1 else '—'}\n\n"
        f"**收束（末三段，去掉收尾语）**：\n\n"
        + "".join(f"- {s}\n" for s in meat[-3:]),
        encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="第 08 步：封面候选 + 标题原料")
    ap.add_argument("episode", type=Path)
    ap.add_argument("--anime", default=paths.conf("anime.default"))
    ap.add_argument("--pick", help="用池子里的编号定 9 张，逗号分隔（agent 看完联系表后用）")
    ap.add_argument("--character", help="只留含这个角色的帧；不给则读 01-topic.md 的 `封面人物`")
    a = ap.parse_args()
    paths.require_data()

    pick = [int(x) for x in a.pick.split(",")] if a.pick else None
    page, total, kept = build(a.episode, a.anime, pick, a.character)
    t = titles(a.episode)
    print(f"抽 {total} 帧 → 候选 {kept} 张\n  open '{page}'\n  标题原料 → {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
