"""段级时长不变量（B4）：每段 Σclip.dur == manifest 段时长（±SEG_TOL）。

「产物即状态」的校验面：不变量不再只活在 `clips.size()` 的生成路径里，
而是任何时刻拿 04-clips*.json + manifest.json 就能验。approve（review.py）
与 render（render.py）双重拦截，人审改画面后用 `clips --refit` 重排版。

**为什么单独一个模块而不是放 clips.py：** clips.py 顶部 import 了
`subindex`（sentence_transformers/torch 全家桶），review 的 approve 是纯
JSON 校验、render 的这道闸也不碰检索——把校验挂在这两个模块里等于让
纯文件操作无辜背上 ML 依赖（import pipeline.review 连带加载 ~1800 个重
模块，任何一个坏都崩）。本模块零第三方依赖，谁都可以 import，无环。
"""

from __future__ import annotations

# 段级不变量容差。为什么是 0.05：clips.size() 自己的接受线就是 drift ≤ 0.05
# 判 ok（见 size() 末尾 return 分支），机器产物满足 |Σdur − need| ≤ 0.05；而
# 人手改 start/dur 造成的漂移通常 ≥ 0.1s，两边分得开。收紧到 0.01 会误伤机器
# 产物的 0.001 舍入累积（每片 ≤0.0005，多片叠加）。
SEG_TOL = 0.05

# refit 的末片伸缩下界，与 clips.MIN_CLIP 同值。不从 clips import（会拖起
# 整个 ML 栈），也不让 clips 从这里 import（clips 有自己的 MIN_CLIP 语义：
# 「比这更短会闪，观众来不及看清画面」——这里是「重排版时不许把末片压到
# 闪帧」的同一条纪律在编辑路径上的落实）。两处必须同步改，各自注释里都
# 指向对方。
REFIT_MIN_CLIP = 2.5


def verify_alignment(segments: list[dict], audio: list[dict]) -> list[str]:
    """返回违例描述列表，空 = 通过。

    status != "ok" 的段没有 clips（render 本就拒收），跳过；但段数与 manifest
    对不上必须报——zip 会静默吞掉错位（判据 9：跳过不是通过，错位更不是）。
    """
    violations: list[str] = []
    if len(segments) != len(audio):
        violations.append(
            f"段数不齐：04-clips.json {len(segments)} 段 / manifest {len(audio)} 段")
        return violations
    for seg, a in zip(segments, audio):
        if seg.get("status") != "ok":
            continue
        clips = seg.get("clips") or []
        if not clips:
            violations.append(f"段{seg['index']}: status=ok 但 clips 为空")
            continue
        got = sum(c["dur"] for c in clips)
        need = a["duration"]
        d = got - need
        if abs(d) > SEG_TOL:
            violations.append(f"段{seg['index']}: 画面 {got:.2f}s / 配音 {need:.2f}s 差 {d:+.2f}s")
    return violations


def refit(segments: list[dict], audio: list[dict],
          sources: dict) -> tuple[list[dict], list[str]]:
    """人审改过 start/source 之后，把每段 dur 重排到满足段级不变量。

    规则：漂移由**本段最后一个片段**吸收。为什么是末片：dur 是排版结果、
    不是画面身份（clips.py 的 OVERLAP_GAP 注释里已立过这条），人刚挑的画面
    身份（start/source）一个不动；末片的伸缩边界由片源时长（截取守卫同款
    判据）和 REFIT_MIN_CLIP 夹住，吸收不了就报错——诚实失败，不静默截断。

    机器产物（已对齐）调它是幂等 no-op。返回（segments, 调整报告行）；
    改不了的段抛 SystemExit，带段号和三个数（Σdur / need / 差多少）。
    手写/人改的 clip 缺 season/episode 键时给出能定位的报错，不裸 KeyError。
    """
    report: list[str] = []
    for seg, a in zip(segments, audio):
        if seg.get("status") != "ok" or not seg.get("clips"):
            continue
        clips = seg["clips"]
        need = a["duration"]
        got = sum(c["dur"] for c in clips)
        drift = round(need - got, 3)
        if abs(drift) <= SEG_TOL:
            continue          # 已对齐，no-op
        last = clips[-1]
        key = f"S{last.get('season', 0):02d}E{last.get('episode', 0):02d}"
        src = sources.get(key)
        if src is None or "season" not in last or "episode" not in last:
            raise SystemExit(
                f"FAIL 段{seg['index']} 的末片缺 season/episode 或片源未登记"
                f"（{last}），refit 拿不到伸缩边界")
        limit = src["duration"]
        old_dur = last["dur"]
        new_dur = round(min(max(old_dur + drift, REFIT_MIN_CLIP), limit - last["start"]), 3)
        new_total = round(got - old_dur + new_dur, 3)
        if abs(need - new_total) > SEG_TOL:
            raise SystemExit(
                f"FAIL 段{seg['index']} refit 不了：Σdur {got:.2f}s / need {need:.2f}s "
                f"差 {drift:+.2f}s，末片 headroom 夹不住"
                f"（{old_dur:.2f}s → clamp 后 {new_dur:.2f}s，仍差 {need - new_total:+.2f}s）")
        last["dur"] = new_dur
        report.append(
            f"段{seg['index']}: 末片 {old_dur:.2f}s → {new_dur:.2f}s"
            f"（漂移 {drift:+.2f}s 由末片吸收）")
    return segments, report
