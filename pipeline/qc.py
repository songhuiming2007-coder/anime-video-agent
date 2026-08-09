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
SILENCE_MAX = 2.0                      # 单段静音上限，秒
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

    # 黑帧。d= 设成上限值，只报超限的那些
    err = _run(["ffmpeg", "-nostdin", "-i", str(video), "-vf",
                f"blackdetect=d={BLACK_MAX}:pic_th=0.98", "-an", "-f", "null", "-"])
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)
    out.append(Check(f"无 >{BLACK_MAX}s 纯黑", not blacks,
                     "、".join(f"{float(a):.1f}-{float(b):.1f}s" for a, b in blacks[:3])
                     or "无"))

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
