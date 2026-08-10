"""成片质检（流程第 07 步）。

    python -m pipeline.qc data/episodes/<本期>

逐条实现 CLAUDE.md「质检门禁」，结果落 `06-check.log`，不达标非零退出。
**主观项（节奏、观感）不进门禁**——它们的根在稿子和排片上，
回第 02 / 05 步改，不在成片上补救。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths

# loudnorm 单遍归一的实测残差约 ±1，容差留点余量
LUFS_TARGET = paths.conf("audio.lufs", -16.0)
LUFS_TOL = paths.conf("audio.lufs_tolerance", 1.5)
TRUE_PEAK_MAX = paths.conf("audio.true_peak_max", -1.0)   # dBTP
AV_DRIFT_MAX = 0.5                     # 音画时长差，秒
BLACK_MAX = 0.5                        # 单段纯黑上限，秒
BLACK_MATCH_MIN = 0.35                 # 成片黑帧与源片黑段的重叠 ≥ 此值才算「源片自带」
                                        # 为什么不是 BLACK_MAX：切片 seek 有帧级误差
                                        # （0.04–0.08s），成片黑帧 0.5s 映射回源片最多
                                        # 偏出这么点，0.35 = 0.5 − 0.15 的容差。
                                        # 再低会把「源片有黑但没盖住成片黑」误认成内容。
FRAME_BOUND = 0.05                     # 单片段帧舍入误差上界，秒。编码器输出帧数 =
                                        # round(dur×fps) ±1 帧；常见帧率（23.976/24/29.97/
                                        # 30）一帧 ≤ 0.0417s，取 0.05 有余量。累积漂移是
                                        # 随机游走，最坏 = 每片同向舍入 → 到第 n 片累计
                                        # ≤ n×0.05。实测：42 片成片中间位置漂移 0.5s。
SILENCE_MAX = 2.0                      # 单段静音上限，秒
BLACK_SRC_BUF = 0.3                    # 对照源片时前后各放宽的秒数
                                        # ffmpeg 快速 seek 会落在关键帧上，实际解码起点
                                        # 可能早于请求位置；不放宽会漏检紧贴边界的黑段。
DUR_BAND = tuple(paths.conf("video.duration_band", [120.0, 240.0]))   # 成片默认带，2–4 分钟

# 同 check_script.py 的同名机制：`01-topic.md` 的 `时长目标` 字段（分钟）覆盖这条默认带。
# 两个模块各自维护一份两行的正则，没有抽共用模块——目前只有这两处用得到，
# 抽出来的复用收益小于多一层间接。
#     不锚 `$`，理由同 check_script.py 的同名正则：字段允许行尾带 `# 备注`。
DURATION_FIELD = re.compile(r"^\s*时长目标\s*[:：]\s*([\d.]+)\s*[-–~]\s*([\d.]+)\s*分钟", re.M)


def episode_duration_band(episode: Path) -> tuple[float, float] | None:
    """从这一期 `01-topic.md` 读 `时长目标`（分钟），换算成秒。没有就返回 None。"""
    topic = episode / "01-topic.md"
    if not topic.exists():
        return None
    m = DURATION_FIELD.search(topic.read_text(encoding="utf-8"))
    return (float(m.group(1)) * 60, float(m.group(2)) * 60) if m else None


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    skipped: bool = False   # 缺前置文件跑不了。**不算通过**，见下方 main


def _run(cmd: list[str]) -> str:
    """跑 ffmpeg 并回收 stderr——检测类滤镜的输出都在 stderr 上。"""
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stderr


def _probe(video: Path, entries: str, stream: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _duration(video: Path, stream: str) -> float:
    """时长。先问流，问不到退到容器。

    `stream=duration` 不是所有容器都填——某些 mkv/mp4 只在 format 上有。
    初版直接 `float(空串)` 抛 ValueError，报错信息对用户毫无意义
    （只会看到一行 `could not convert string to float: ''`）。
    """
    raw = _probe(video, "stream=duration", stream).split(",")[0].strip()
    if not raw or raw == "N/A":
        raw = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=True).stdout.strip()
    if not raw or raw == "N/A":
        raise SystemExit(f"FAIL 读不到 {video} 的 {stream} 时长，文件可能损坏")
    return float(raw)


def _map_black_to_sources(blacks: list[tuple[float, float]],
                          plan: dict) -> list[tuple[str, float, float, float, float, int, int]]:
    """成片黑帧区间 → 它覆盖的源片段引用。

    成片 = 各段 clips 按顺序拼接（与 render.py 同一份 plan、同一套顺序，
    时间轴才会对得上）。返回
    [(源路径, 源区间起点, 源区间终点, 成片区间起点, 成片区间终点, 累计片段序号, 黑帧序号)]；
    黑帧跨片段边界时一个黑帧映射出多条。片段序号从 1 起——它决定累积漂移
    窗口的大小（见 `_mapped_covered`）；黑帧序号用于把同一条黑在多个片段上的
    映射归拢起来判整体（见 `_black_defects`）。
    """
    out: list[tuple[str, float, float, float, float, int, int]] = []
    t = 0.0
    idx = 0
    for seg in plan["segments"]:
        for clip in seg["clips"]:
            idx += 1
            lo, hi = t, t + clip["dur"]
            for bi, (b0, b1) in enumerate(blacks):
                if b1 > lo and b0 < hi:                 # 有交集
                    out.append((
                        clip["source"],
                        clip["start"] + max(0.0, b0 - lo),
                        clip["start"] + min(clip["dur"], b1 - lo),
                        max(b0, lo), min(b1, hi),
                        idx, bi,
                    ))
            t = hi
    return out


def _source_black_covers(src_blacks: list[tuple[float, float]],
                         src_lo: float, src_hi: float) -> bool:
    """源片黑段与映射区间的重叠 ≥ 门槛，认定成片黑帧是源片自带。

    源片同样的黑场转场是内容——情绪停顿、转场黑场，成片原样带进来，
    不该算渲染缺陷。
    """
    return any(min(s, src_hi) - max(s0, src_lo) >= BLACK_MATCH_MIN
               for s0, s in src_blacks)


def _mapped_covered(span: tuple[str, float, float, float, float, int],
                    src_blacks: list[tuple[float, float]]) -> bool:
    """一条映射是否被源片黑段覆盖，含累积漂移窗口。

    成片时间轴相对排片轴有**累积帧舍入误差**：每个切片编码器输出帧数 =
    round(dur×fps) ±1 帧，拼接后漂移是随机游走，最坏 = 每片同向舍入，
    到第 n 片累计 ≤ n 帧（实测 42 片成片中间位置漂移 0.5s，终点 68ms——
    终点小只因误差互相抵消，中间照样大）。所以黑帧映射回源的位置允许
    偏出 idx×FRAME_BOUND——这是推导上界，不是调出来的数。
    """
    _, slo, shi, _, _, idx = span
    w = idx * FRAME_BOUND
    return _source_black_covers(src_blacks, slo - w, shi + w)


def _black_defects(mapped: list[tuple[str, float, float, float, float, int, int]],
                   covered: set[tuple]) -> list[tuple[str, float, float, float, float]]:
    """按「一条黑」聚合，返回未覆盖时长 ≥ BLACK_MAX 的黑场（的未覆盖映射）。

    判据要的是**一段连续黑的未覆盖部分 ≥ 门槛**，不是逐片段看。黑帧跨切片
    边界时，边界上一两帧的暗场可能落进相邻片段，而源片只在主片段里有转场黑：
    逐片段查会把整段源转场误判成缺陷（2026-08-10 实测：源片淡出黑 1.45s
    接邻片段首帧 0.13s 暗场，被判「源无黑」）。
    """
    by_black: dict[int, list] = {}
    for span in mapped:
        by_black.setdefault(span[6], []).append(span)
    defects: list[tuple[str, float, float, float, float]] = []
    for spans in by_black.values():
        un = [s for s in spans if s not in covered]
        if sum(f1 - f0 for _, _, _, f0, f1, _, _ in un) >= BLACK_MAX:
            defects.extend((src, flo, fhi, slo, shi)
                           for src, slo, shi, flo, fhi, _, _ in un)
    return defects


def check(video: Path, plan: dict | None = None,
          audio: list[dict] | None = None) -> list[Check]:
    out: list[Check] = []
    dur_band = episode_duration_band(video.parent) or DUR_BAND

    vdur = _duration(video, "v:0")
    adur = _duration(video, "a:0")

    out.append(Check(f"成片时长 {dur_band[0] / 60:g}–{dur_band[1] / 60:g} 分钟",
                     dur_band[0] <= vdur <= dur_band[1],
                     f"{vdur:.2f}s = {vdur / 60:.2f} 分钟"))
    out.append(Check("音画时长对齐",
                     abs(vdur - adur) < AV_DRIFT_MAX,
                     f"画面 {vdur:.3f}s / 音频 {adur:.3f}s 差 {abs(vdur - adur) * 1000:.0f}ms"))

    if plan:
        want = plan["total_duration"]
        out.append(Check("与排片一致",
                         abs(vdur - want) < AV_DRIFT_MAX,
                         f"排片 {want:.3f}s 成片 {vdur:.3f}s 差 {abs(vdur - want) * 1000:.0f}ms"))
    else:
        out.append(Check("与排片一致", False,
                         "跳过：没有 04-clips.approved.json", skipped=True))

    # 响度与削波：用第二遍 loudnorm 的测量结果，不改文件
    err = _run(["ffmpeg", "-nostdin", "-i", str(video), "-af",
                "loudnorm=print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", err, re.S)
    if m:
        d = json.loads(m.group(0))
        i, tp = float(d["input_i"]), float(d["input_tp"])
        out.append(Check(f"响度 {LUFS_TARGET}±{LUFS_TOL} LUFS",
                         abs(i - LUFS_TARGET) <= LUFS_TOL, f"{i:.2f} LUFS"))
        out.append(Check(f"真峰值 ≤ {TRUE_PEAK_MAX} dBTP",
                         tp <= TRUE_PEAK_MAX, f"{tp:.2f} dBTP"))
    else:
        out.append(Check("响度测量", False, "loudnorm 没吐出 JSON，检查 ffmpeg 构建"))

    # 黑帧。**判据：成片黑帧必须能对照回源片段。**
    # 动画的黑场转场是内容不是故障——源片转场原样带进成片，会被 blackdetect
    # 误报成缺陷（2026-08-09 实证：S01E07 两句台词间的情绪停顿黑场 0.54s、
    # S03E12 台词开口处的转场黑场 0.71s，源片同样黑）。
    # 所以成片黑帧映射回源区间，源片同样黑 = 内容，通过；成片独有黑 = 渲染
    # 缺陷，拦截。没 plan 时退回旧行为：无源可对照，宁严勿松。
    err = _run(["ffmpeg", "-nostdin", "-i", str(video), "-vf",
                f"blackdetect=d={BLACK_MAX}:pic_th=0.98", "-an", "-f", "null", "-"])
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)
    defects: list[tuple[str, float, float, float, float]] = []
    if blacks:
        if plan:
            mapped = _map_black_to_sources(
                [(float(a), float(b)) for a, b in blacks], plan)
            # 按源文件分组，一次 ffmpeg 覆盖该文件全部相关区间，不逐黑帧重开进程。
            # 区间本身再放宽 1 帧：ffmpeg 快速 seek 落在关键帧上，实际解码起点
            # 可能早于请求位置（BLACK_SRC_BUF 的职责在这里，常量已删，见下）。
            by_src: dict[str, list[tuple[str, float, float, float, float, int, int]]] = {}
            for span in mapped:
                src, slo, shi, flo, fhi, idx, bi = span
                by_src.setdefault(src, []).append(span)
            covered: set[tuple] = set()
            for src, spans in by_src.items():
                # 检测范围也要吃下漂移窗口（idx×FRAME_BOUND），否则源黑段在
                # 窗口内、检测起点外会整段漏检，判成「源片无黑」（实测踩过：
                # 黑段起点在映射区间 0.26s 前，d=0.5 检测不到）。
                lo = min(s - i * FRAME_BOUND for _, s, _, _, _, i, _ in spans) - BLACK_SRC_BUF
                hi = max(e + i * FRAME_BOUND for _, _, e, _, _, i, _ in spans) + BLACK_SRC_BUF
                err2 = _run(["ffmpeg", "-nostdin", "-ss", f"{lo:.3f}",
                             "-t", f"{hi - lo:.3f}", "-i", src, "-vf",
                             f"blackdetect=d={BLACK_MAX}:pic_th=0.98",
                             "-an", "-f", "null", "-"])
                # blackdetect 输出相对 -ss 起点，换算回源绝对时间
                src_blacks = [(lo + float(a), lo + float(b))
                              for a, b in re.findall(
                                  r"black_start:([\d.]+) black_end:([\d.]+)", err2)]
                covered |= {span for span in spans
                            if _mapped_covered(span[:6], src_blacks)}
            defects = _black_defects(mapped, covered)
        else:
            defects = [(str(video), float(a), float(b), float(a), float(b))
                       for a, b in blacks]
    detail = "无" if not defects else "、".join(
        f"{Path(s).name} 成片 {f0:.1f}-{f1:.1f}s（源 {s0:.1f}-{s1:.1f}s 无黑）"
        for s, f0, f1, s0, s1 in defects[:3])
    out.append(Check(f"无 >{BLACK_MAX}s 纯黑（源片转场除外）", not defects, detail))

    # 静音
    err = _run(["ffmpeg", "-nostdin", "-i", str(video), "-af",
                f"silencedetect=n=-45dB:d={SILENCE_MAX}", "-f", "null", "-"])
    sils = re.findall(r"silence_start: ([\d.]+)", err)
    out.append(Check(f"无 >{SILENCE_MAX}s 静音", not sils,
                     "、".join(f"{float(s):.1f}s" for s in sils[:3]) or "无"))

    # 字幕。烧录之后没法从成片反查，所以查它的来源——但**必须查渲染器真正会写出去的
    # 那份文本**，也就是过了 `render.wrap` 之后的每一行。
    #
    # 初版查的是「原文长度 ÷ 每行容量 ≤ 最大行数」，等于假设了折行一定会发生。
    # 2026-07-29 实测这个假设是错的：libass 对无空格的中文不折行，91 字的段落
    # 渲成一整行横贯画面、两端各切掉十几个字，而这条检查判了 PASS。
    # 检查项一旦测的不是真实产物，通过就毫无意义。
    if plan and audio:
        from .render import (FONT_SIZE, MARGIN_V, H, cards, line_width, per_line,
                             usable_width, wrap)
        cap = usable_width()          # 硬上限，不是折行目标
        max_lines = int((H - MARGIN_V * 2) // (FONT_SIZE * 1.2))
        empty = [s["index"] for s in plan["segments"] if not s["text"].strip()]
        # 验的必须是**真正会上屏的那些卡**，不是整段文本。
        # 渲染器按句分卡，这里若还按整段算，测的就又不是真实产物了。
        wrapped = {}
        for s, a in zip(plan["segments"], audio):
            for j, (card, _, _) in enumerate(cards(a, s["duration"])):
                wrapped[f"{s['index']}-{j + 1}"] = wrap(card.strip(), per_line())
        too_wide = [(i, round(max(line_width(x) for x in ls), 1))
                    for i, ls in wrapped.items() if any(line_width(x) > cap for x in ls)]
        too_tall = [(i, len(ls)) for i, ls in wrapped.items() if len(ls) > max_lines]
        widest = max(line_width(x) for ls in wrapped.values() for x in ls)
        tallest = max(len(ls) for ls in wrapped.values())
        out.append(Check("字幕无空段", not empty, f"空段 {empty}" if empty else "无"))
        out.append(Check("字幕不超宽", not too_wide,
                         f"超宽 {too_wide}" if too_wide
                         else f"{len(wrapped)} 张卡，最宽一行 {widest:.1f} 格 ≤ {cap:.2f}"))
        # 每张卡都该只有一行——分卡的目的就是不折行。折了说明单句太长，
        # 那是稿子的问题，得回去断句，不是这里放宽。
        multi = [k for k, ls in wrapped.items() if len(ls) > 1]
        out.append(Check("字幕每卡一行", not multi,
                         f"{len(multi)} 张卡折了行：{multi[:5]}" if multi
                         else f"最多 {tallest} 行"))
        out.append(Check("字幕不超高", not too_tall,
                         f"超高 {too_tall}" if too_tall
                         else f"最多 {tallest} 行 ≤ {max_lines}"))
    else:
        # **缺文件时必须把这四项显式列出来，不能悄悄不跑。**
        # 初版是 `if plan and audio:` 一个分支，缺了就少四项，最后打印「6/6 通过」——
        # 看着是满分，实际跳了三分之一，而字幕超框恰恰是最容易出事的那一类。
        # 2026-07-29 审计发现。跳过不是通过，退出码也要非零。
        why = "跳过：缺 " + " 和 ".join(
            x for x, ok in (("04-clips.approved.json", plan),
                            ("03-audio/manifest.json", audio)) if not ok)
        for n in ("字幕无空段", "字幕不超宽", "字幕每卡一行", "字幕不超高"):
            out.append(Check(n, False, why, skipped=True))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path, help="本期目录，或直接给 mp4")
    a = ap.parse_args()

    if a.target.is_dir():
        episode, video = a.target, a.target / "05-final.mp4"
    else:
        episode, video = a.target.parent, a.target
    if not video.exists():
        raise SystemExit(f"FAIL 找不到成片 {video}")

    plan_path = episode / "04-clips.approved.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None

    mf = episode / "03-audio" / "manifest.json"
    audio = json.loads(mf.read_text(encoding="utf-8"))["segments"] if mf.exists() else None
    checks = check(video, plan, audio)
    width = max(len(c.name) for c in checks) + 2
    lines = [f"{'SKIP' if c.skipped else 'PASS' if c.ok else 'FAIL'}  "
             f"{c.name:<{width}}{c.detail}" for c in checks]
    passed = sum(c.ok for c in checks)
    skipped = sum(c.skipped for c in checks)
    failed = len(checks) - passed - skipped
    lines.append("-" * 62)
    tail = f"{passed}/{len(checks)} 通过"
    if skipped:
        tail += f"，{skipped} 项因缺前置文件未跑——**未跑不是通过**"
    if failed:
        tail += f"，{failed} 项不合格"
    if not failed and not skipped:
        tail += "。主观项仍需人看：节奏、画文是否贴合。"
    lines.append(tail)

    log = episode / "06-check.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"→ {log}")
    # 跳过也算不合格：门禁的意义是「全过才放行」，少跑一项就不知道那一项行不行。
    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())
