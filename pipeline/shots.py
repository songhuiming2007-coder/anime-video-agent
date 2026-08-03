"""镜头切分与代表帧（Phase 0 动作，一部番做一次）。

    python -m pipeline.shots calibrate <视频>          # 定阈值，先跑这个
    python -m pipeline.shots build <视频> --anime 春物 --season 1 --episode 3
    python -m pipeline.shots frames 春物 S01E03        # 每镜头抽一张代表帧

**检索单元是镜头，不是帧**（ADR-0003）。字幕索引的单元是「2 行滑窗 + 时间码」，
是时间区间；视觉索引若以帧为单位，两边对不上，融合时要做区间到点的映射，凭空多一层。
镜头同样是时间区间，天然可交。而且镜头才是排片真正要的东西——
切一个镜头出来是完整的，切半个镜头是穿帮。

产物 `data/library/shots/<番>_SxxEyy.json`，与字幕索引、片源登记表并列。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from . import paths, sheet
from .ingest import load_sources

SHOTS_DIR = paths.DATA / "library" / "shots"
FRAMES_DIR = SHOTS_DIR / "frames"

# **扫描阈值，不是判定阈值。** 用一个比任何可能采用的判定阈值都低的数扫一遍，
# 把候选切点连同分数全存下来；判定阈值随后在这份数据上过滤即可。
#
# 这样做的依据是实测出来的一条性质：**scdet 低阈值的输出是高阈值输出的严格超集，
# 且同一个切点的分数一字不差**（2026-08-03 在 S01E01 的 30 秒窗口上比对：
# threshold=3 出 6 条，threshold=10 出 4 条，那 4 条的 score 完全相同）。
#
# 它值钱的地方在于成本：一集解码要一分钟，41 集四十分钟。没有这条性质，
# `calibrate` 试六个阈值就要解码六遍，而阈值本来就是要反复试的——
# ADR-0003 写着「镜头切分阈值要实测定，不许抄默认值」。
SCAN_THRESHOLD = 3.0

# 标定时对比的阈值档位。不是候选答案清单，是让人看见「密度随阈值怎么变」的取样。
CALIBRATE_GRID = (5.0, 8.0, 10.0, 12.0, 15.0, 20.0)

# scdet 的日志行。**不能按行切**：ffmpeg 的进度行会和它挤在同一行里
# （实测 `frame= 346 fps=0.0 ... [scdet @ 0x...] lavfi.scd.score: ...`），
# 按行切会漏掉挤在一起的那些。所以在整段输出上 finditer。
SCD = re.compile(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)")

# 代表帧的宽度。448 是 WD-Tagger 的输入边长；画面通道的 CLIP 只要 224，
# 取大的那个，小的自己缩——反过来就要重抽。
FRAME_W = 448


def threshold() -> float:
    """镜头切分阈值。**没有默认值，缺配置就失败。**

    ADR-0003：「镜头切分用 ffmpeg 的场景检测。阈值要实测定，不许抄默认值」。

    这与 `paths.conf` 那条「每个调用点都必须给出 default」不冲突——那条约束的是
    **原来写死过的值**，目的是让缺配置时行为与从前完全一致。这里从来没有过默认值，
    编一个出来才是错的：抄来的阈值会静默地切出过碎或过粗的镜头，而两者都不报错。
    """
    t = paths.conf("visual.scene_threshold")
    if t is None:
        raise SystemExit(
            "FAIL config/project.json 里没有 visual.scene_threshold。\n"
            "     阈值必须实测定（ADR-0003）：\n"
            "     python -m pipeline.shots calibrate <一集视频>\n"
            "     看完对照表把选定的数写进 config/project.json 的 visual.scene_threshold"
        )
    return float(t)


def min_shot() -> float:
    """短于这个秒数的镜头并进前一个镜头。

    **代价要说清楚：合并会把一段时间划给邻居的角色集合**，于是「这个镜头里有 X」
    这个判断在被合并的那一小段上可能是假的。角色过滤整条链建在「检测到 X 可信」上，
    所以这里取值要小——只吃掉几帧的闪帧和转场残留，不吃真镜头。

    0.5 秒的依据是动画的镜头长度分布：单镜头通常 2–5 秒（`cover.MIN_GAP` 同源），
    半秒以下基本不是叙事镜头，是切换过程里被判成一刀的中间帧。
    """
    return float(paths.conf("visual.min_shot", 0.5))


def scan(video: Path, duration: float | None = None) -> list[tuple[float, float]]:
    """解码全片跑场景检测，返回 [(时间, 分数)]，按时间升序。

    一集约一分钟（实测 120 秒素材 5.06 秒挂钟，瓶颈在 10bit HEVC 解码；
    先 `scale` 降分辨率不会更快，因为省的是滤镜的钱不是解码的钱）。
    """
    cmd = ["ffmpeg", "-nostdin", "-v", "info", "-nostats", "-i", str(video),
           "-an", "-sn", "-dn", "-vf", f"scdet=threshold={SCAN_THRESHOLD}", "-f", "null", "-"]
    if duration is not None:
        cmd[6:6] = ["-t", f"{duration:.3f}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL ffmpeg 场景检测失败：{video}\n{r.stderr[-800:]}")
    cuts = [(float(t), float(s)) for s, t in SCD.findall(r.stderr)]
    cuts.sort()
    return cuts


def cut(cuts: list[tuple[float, float]], thr: float, duration: float,
        floor: float) -> list[dict]:
    """候选切点 → 镜头表。纯函数，不碰 ffmpeg。

    切点报的是**新镜头的第一帧**，所以镜头是 [切点, 下一个切点)，
    首尾分别补上 0 和片长。

    短于 `floor` 的镜头并进前一个（首个镜头无处可并，就并进后一个）。
    """
    marks = sorted(t for t, s in cuts if s >= thr and 0.0 < t < duration)
    bounds = [0.0] + marks + [duration]

    shots: list[list[float]] = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < floor and shots:
            shots[-1][1] = b            # 太短，并进前一个
        else:
            shots.append([a, b])
    # 首个镜头太短时上面并不掉（没有前一个），这里并进后一个
    while len(shots) > 1 and shots[0][1] - shots[0][0] < floor:
        shots[1][0] = shots[0][0]
        shots.pop(0)

    return [{"i": i, "start": round(a, 3), "end": round(b, 3),
             "rep": round((a + b) / 2, 3)}
            for i, (a, b) in enumerate(shots)]


def _key(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def build(video: Path, anime: str, season: int, episode: int,
          out_dir: Path = SHOTS_DIR) -> dict:
    """切分一集，落 `<番>_SxxEyy.json`。

    **候选切点连同分数一起存。** 判定阈值改了不必重新解码——直接在存下来的
    `cuts` 上重算即可（`rebuild`）。阈值本来就是要反复试的，而重解码一集要一分钟。
    """
    src = load_sources(anime).get(_key(season, episode))
    if src is None:
        raise SystemExit(
            f"FAIL 片源登记表里没有 {anime} {_key(season, episode)}，先跑 `ingest phase0`")

    t0 = time.perf_counter()
    cuts = scan(video)
    shots = cut(cuts, threshold(), src["duration"], min_shot())
    elapsed = time.perf_counter() - t0

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{anime}_{_key(season, episode)}.json"
    dest.write_text(json.dumps({
        "meta": meta(anime, season, episode, src),
        # 全部候选切点（>= SCAN_THRESHOLD），供改阈值时免解码重算
        "cuts": [[round(t, 3), round(s, 3)] for t, s in cuts],
        "shots": shots,
    }, ensure_ascii=False), encoding="utf-8")
    return {"path": dest, "shots": len(shots), "cuts": len(cuts), "sec": elapsed}


def rebuild(anime: str, key: str, out_dir: Path = SHOTS_DIR) -> dict:
    """阈值改了之后，在已存的候选切点上重算镜头表，不重新解码。"""
    dest = out_dir / f"{anime}_{key}.json"
    if not dest.exists():
        raise SystemExit(f"FAIL 没有 {dest}，先跑 `shots build`")
    d = json.loads(dest.read_text(encoding="utf-8"))
    d["shots"] = cut([(t, s) for t, s in d["cuts"]], threshold(),
                     d["meta"]["duration"], min_shot())
    d["meta"]["scene_threshold"] = threshold()
    d["meta"]["min_shot"] = min_shot()
    dest.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return {"path": dest, "shots": len(d["shots"])}


def meta(anime: str, season: int, episode: int, src: dict) -> dict:
    """镜头表的自描述元信息。

    视觉索引加载时拿它对账：**切分参数变了，索引里的镜头编号就不再指向同一段时间**，
    而这种错不会崩——余弦照样算得出来，分数照样正常，切出来的画面对不上。
    """
    return {
        "kind": "shots",
        "anime": anime, "season": season, "episode": episode,
        "detector": "ffmpeg-scdet",
        "scan_threshold": SCAN_THRESHOLD,
        "scene_threshold": threshold(),
        "min_shot": min_shot(),
        "duration": src["duration"],
        "fps": src["fps"],
        "source": src["path"],
    }


def load(anime: str, key: str, out_dir: Path = SHOTS_DIR) -> dict:
    """读一集的镜头表，并校验切分参数与当前配置一致。"""
    dest = out_dir / f"{anime}_{key}.json"
    if not dest.exists():
        raise SystemExit(
            f"FAIL 没有 {anime} {key} 的镜头表，先跑：\n"
            f"     python -m pipeline.shots build <该集视频> --anime {anime} ...")
    d = json.loads(dest.read_text(encoding="utf-8"))
    m = d["meta"]
    if abs(m["scene_threshold"] - threshold()) > 1e-9 or abs(m["min_shot"] - min_shot()) > 1e-9:
        raise SystemExit(
            f"FAIL {dest.name} 的切分参数与当前配置不一致："
            f"文件 threshold={m['scene_threshold']} min_shot={m['min_shot']}，"
            f"配置 threshold={threshold()} min_shot={min_shot()}\n"
            f"     python -m pipeline.shots rebuild --anime {anime} --episode {key}")
    return d


def at(shots: list[dict], t: float) -> dict | None:
    """时刻 t 落在哪个镜头里。镜头按时间升序且首尾相接，二分即可。"""
    lo, hi = 0, len(shots) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s = shots[mid]
        if t < s["start"]:
            hi = mid - 1
        elif t >= s["end"]:
            lo = mid + 1
        else:
            return s
    return None


def between(shots: list[dict], start: float, end: float) -> list[dict]:
    """与区间 [start, end) 相交的所有镜头。台词单元跨镜头是常态，所以返回列表。"""
    return [s for s in shots if s["start"] < end and start < s["end"]]


def _frame_no(t: float, fps: str) -> int:
    num, den = (int(x) for x in fps.split("/"))
    return int(round(t * num / den))


def frames(anime: str, key: str, out_dir: Path = SHOTS_DIR,
           dest_dir: Path = FRAMES_DIR) -> Path:
    """一趟解码抽出全部代表帧。

    **不要逐个 `-ss` 抽。** 一集五百个镜头就是五百次 ffmpeg 调用，41 集两万次，
    光进程开销就是几小时；而 `select` 一次解码全部抽完，成本与场景检测同量级。

    帧号用 `round(t × fps)` 精确算，不用时间窗匹配：源片是 CFR（实测全部 24000/1001），
    帧号是精确的，而 `lt(abs(t-r),eps)` 这种窗口在 0.0417 秒的帧距上不是漏就是重。

    **抽完核对张数。** 输出文件按选中顺序命名，与镜头顺序一一对应；
    少抽一张，后面每一张都会错位一个镜头，而这种错位不会报错——
    它表现为「角色过滤偶尔选错镜头」，查起来极难。
    """
    d = load(anime, key, out_dir)
    shots, m = d["shots"], d["meta"]
    out = dest_dir / f"{anime}_{key}"

    _extract(Path(m["source"]), [_frame_no(s["rep"], m["fps"]) for s in shots],
             out, "%05d.jpg")

    # 这里用 glob 而不是按预期文件名数，是**故意的**：上一次跑剩下的多余帧同样要报错。
    # 张数对不上的两种成因（这次少抽了 / 上次剩下的没清）后果一样，都是整体错位。
    got = sorted(out.glob("*.jpg"))
    if len(got) != len(shots):
        raise SystemExit(
            f"FAIL {key} 应抽 {len(shots)} 张代表帧，实得 {len(got)} 张。"
            f"张数对不上就会整体错位一个镜头且不报错，不许继续。\n"
            f"     删掉 {out} 重跑；仍不对说明该集不是恒定帧率，要改用逐帧号定位")
    return out


def frame_path(anime: str, key: str, i: int, dest_dir: Path = FRAMES_DIR) -> Path:
    """第 i 个镜头的代表帧。编号从 1 开始（ffmpeg 的 `%05d` 从 1 开始）。"""
    return dest_dir / f"{anime}_{key}" / f"{i + 1:05d}.jpg"


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def calibrate(video: Path, duration: float | None = None) -> list[dict]:
    """一趟解码，报各阈值下的镜头密度。**这是定阈值的动作，不是建索引的动作。**

    只给数，不给推荐值：镜头切得对不对要看画面，看不出来的量不该由代码替人拍板
    （CLAUDE.md「一个量能用来卡门槛，不代表它能用来排序」的同源纪律）。
    """
    cuts = scan(video, duration)
    total = duration if duration is not None else _probe_duration(video)
    rows = []
    for thr in CALIBRATE_GRID:
        shots = cut(cuts, thr, total, min_shot())
        lens = [s["end"] - s["start"] for s in shots]
        rows.append({
            "threshold": thr, "shots": len(shots),
            "per_min": len(shots) / (total / 60.0),
            "p10": _pct(lens, 0.10), "median": _pct(lens, 0.50), "p90": _pct(lens, 0.90),
            "max": max(lens) if lens else 0.0,
        })
    return rows


def _probe_duration(video: Path) -> float:
    return _probe(video)[0]


def _probe(video: Path) -> tuple[float, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    return float(d["format"]["duration"]), d["streams"][0]["r_frame_rate"]


def _extract(video: Path, frame_nos: list[int], out: Path, pattern: str) -> None:
    """一趟解码抽指定帧号。帧号必须升序且去重，`select` 按解码顺序命中。"""
    out.mkdir(parents=True, exist_ok=True)
    expr = "+".join(rf"eq(n\,{n})" for n in frame_nos)
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video),
         "-an", "-sn", "-dn", "-vf", f"select='{expr}',scale={FRAME_W}:-2",
         "-fps_mode", "passthrough", "-q:v", "3", str(out / pattern)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL 抽帧失败：{video}\n{r.stderr[-800:]}")


def cut_sheet(video: Path, thr: float, n: int = 24,
              dest_dir: Path = SHOTS_DIR / "calibrate") -> Path:
    """切点抽检：每个切点出「前一帧 / 切点帧」两张并排。

    **表格只能证明数字稳，证明不了切得对。** 阈值 8–15 上镜头数只差 8%（实测 S01E01：
    382 / 372 / 364），光看这条平曲线会以为随便取——但它没回答两个问题：
    这些切点是不是真的切点，以及低分那批（溶解、摇镜）是不是被漏掉的真切点。
    这两个问题只有看画面能答。

    判据是明确的，不是审美：**真切点的前后两帧应当完全不同**；两帧看着几乎一样，
    说明那一刀切在镜头内部（快速运动、闪光、字幕弹出）。
    """
    duration, fps = _probe(video)
    marks = [t for t, s in scan(video) if s >= thr and 0 < t < duration]
    if not marks:
        raise SystemExit(f"FAIL 阈值 {thr} 下一个切点都没有")
    step = max(1, len(marks) // n)
    picked = marks[::step][:n]

    nos: list[int] = []
    for t in picked:
        k = _frame_no(t, fps)
        nos += [max(0, k - 1), k]
    nos = sorted(set(nos))

    frames_dir = dest_dir / "frames"
    _extract(video, nos, frames_dir, "cut-%03d.jpg")
    # **按预期文件名取，不 glob。** glob 会把上一次跑剩下的多余帧一起收进来，
    # 而多出来的那张会把后面每一格都顶偏一位——顶偏之后每一格看着都正常。
    cells = []
    for i, t in enumerate(picked):
        a, b = frames_dir / f"cut-{2 * i + 1:03d}.jpg", frames_dir / f"cut-{2 * i + 2:03d}.jpg"
        cells += [(a, f"{i + 1} 前 {t:.2f}s"), (b, f"{i + 1} 切点")]
    return sheet.build(cells, dest_dir / f"cuts-t{thr:g}.jpg", cols=6)


def long_sheet(video: Path, thr: float, n: int = 8,
               dest_dir: Path = SHOTS_DIR / "calibrate") -> Path:
    """漏切抽检：最长的 n 个镜头各出首 / 中 / 尾三帧。

    **过切和漏切的代价不对称，所以这两张表要一起看。**

    - 过切（把一个镜头切成两个）：两个镜头内容几乎相同，各自都会被正确打标，
      对「这个镜头里有没有 X」这个布尔判断毫无影响。代价只是索引多几行。
    - 漏切（两场戏并成一个镜头）：角色集合变成两场戏的并集，于是
      **「检测到 X」不再意味着「X 在这段时间里出现」**——而整条角色过滤链
      正是建在「检测到 X 可信」上的（ADR-0003「检测的不对称」）。

    所以拿不准时应当往**低**阈值取，而不是往高取。这张表回答的就是高阈值那一侧
    的风险：一个 19 秒的「镜头」到底是长镜头，还是三场戏被并成了一个。
    """
    duration, fps = _probe(video)
    shots = cut(scan(video), thr, duration, min_shot())
    top = sorted(shots, key=lambda s: s["start"] - s["end"])[:n]
    top.sort(key=lambda s: s["start"])

    picks: list[tuple[dict, list[float]]] = []
    for s in top:
        a, b = s["start"], s["end"]
        picks.append((s, [a + 0.2, (a + b) / 2, b - 0.2]))

    nos = sorted({_frame_no(t, fps) for _, ts in picks for t in ts})
    frames_dir = dest_dir / "long"
    _extract(video, nos, frames_dir, "long-%03d.jpg")

    cells, k = [], 1
    for s, ts in picks:
        for tag, t in zip(("首", "中", "尾"), ts):
            cells.append((frames_dir / f"long-{k:03d}.jpg",
                          f"{s['start']:.1f}s {s['end'] - s['start']:.1f}秒 {tag}"))
            k += 1
    return sheet.build(cells, dest_dir / f"long-t{thr:g}.jpg", cols=6)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("calibrate", help="定阈值：一趟解码报各阈值下的镜头密度")
    c.add_argument("video", type=Path)
    c.add_argument("--seconds", type=float, help="只跑前 N 秒（试手用，定阈值请跑整集）")
    c.add_argument("--sheet", type=float, metavar="阈值",
                   help="出切点抽检联系表：该阈值下每个切点的前一帧与切点帧并排（查过切）")
    c.add_argument("--long", type=float, metavar="阈值",
                   help="出漏切抽检联系表：最长的几个镜头各出首中尾三帧（查漏切）")

    b = sub.add_parser("build", help="切分一集")
    b.add_argument("video", type=Path)
    b.add_argument("--anime", default=paths.conf("anime.default", "春物"))
    b.add_argument("--season", type=int, required=True)
    b.add_argument("--episode", type=int, required=True)

    r = sub.add_parser("rebuild", help="阈值改了，在已存切点上重算，不重新解码")
    r.add_argument("--anime", default=paths.conf("anime.default", "春物"))
    r.add_argument("--episode", required=True, help="SxxEyy")

    f = sub.add_parser("frames", help="抽代表帧，每镜头一张")
    f.add_argument("anime")
    f.add_argument("episode", help="SxxEyy")

    a = ap.parse_args()
    paths.require_data()

    if a.cmd == "calibrate":
        if a.sheet is not None:
            p = cut_sheet(a.video, a.sheet)
            print(f"OK 切点抽检 → {p}\n"
                  f"   判据：真切点的前后两帧应当完全不同。两帧几乎一样 = 这一刀切在镜头内部。")
            return 0
        if a.long is not None:
            p = long_sheet(a.video, a.long)
            print(f"OK 漏切抽检 → {p}\n"
                  f"   判据：同一个镜头的首中尾三帧应当是同一场戏（人物动、场景不换）。"
                  f"三帧换了场景 = 这里漏切了。")
            return 0
        rows = calibrate(a.video, a.seconds)
        print(f"{'阈值':>6} {'镜头数':>7} {'每分钟':>7} {'p10':>7} {'中位':>7} "
              f"{'p90':>7} {'最长':>8}")
        for r_ in rows:
            print(f"{r_['threshold']:6.1f} {r_['shots']:7d} {r_['per_min']:7.1f} "
                  f"{r_['p10']:7.2f} {r_['median']:7.2f} {r_['p90']:7.2f} {r_['max']:8.1f}")
        print("-" * 60)
        print("动画单镜头通常 2–5 秒。中位数明显低于 2 秒说明切碎了（转场被当成切点），"
              "明显高于 5 秒说明漏切。\n"
              "定下来写进 config/project.json 的 visual.scene_threshold，再跑 build。")
        return 0

    if a.cmd == "build":
        out = build(a.video, a.anime, a.season, a.episode)
        print(f"OK {out['shots']} 个镜头（候选切点 {out['cuts']}，"
              f"{out['sec']:.1f}s）→ {out['path']}")
        return 0

    if a.cmd == "rebuild":
        out = rebuild(a.anime, a.episode)
        print(f"OK {out['shots']} 个镜头 → {out['path']}")
        return 0

    out = frames(a.anime, a.episode)
    print(f"OK 代表帧 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
