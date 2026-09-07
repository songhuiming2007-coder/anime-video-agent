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

import hashlib
import re
from pathlib import Path

# 台词哈希因果防线（Stale Artifact Guard）：
# 保证 04-clips 与 06-render 消费的配音资产与 02-script.md 的配音台词保持因果一致。
def compute_script_vo_hash(script_path: Path) -> str:
    """计算 02-script.md 中所有配音台词纯文本的 SHA-256 前 16 位。

    只提取配音台词文本，忽略锚点、画面、注释及微调说明，保证改画面不误伤配音有效性。
    """
    text = script_path.read_text(encoding="utf-8")
    vo_lines = []
    parts = re.split(r"^##\s*段落\s*\S+\s*$", text, flags=re.M)
    for block in parts[1::2]:
        m = re.search(r"^配音[：:]\s*(.+)$", block, re.M)
        if m:
            vo_lines.append(m.group(1).strip())
    if not vo_lines:
        vo_lines = [m.group(1).strip() for m in re.finditer(r"^配音[：:]\s*(.+)$", text, re.M)]
    combined = "\n".join(vo_lines)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def verify_script_vo_hash(script_path: Path, manifest_data: dict, force: bool = False) -> None:
    """因果守卫：校验 02-script.md 当前配音哈希与 manifest.json 记录的哈希是否一致。

    若不一致且未传 force，抛出显式 SystemExit 阻断流水线。
    若 manifest_data 中未记录 script_vo_hash（旧版本产物），为保证向后兼容，放行。
    """
    if force:
        return
    manifest_hash = manifest_data.get("script_vo_hash")
    if not manifest_hash:
        return
    current_hash = compute_script_vo_hash(script_path)
    if current_hash != manifest_hash:
        ep_dir = script_path.parent
        raise SystemExit(
            f"FAIL: 02-script.md 配音台词已被修改（Hash 校验不匹配），但 03-audio 配音尚未更新！\n"
            f"当前台词哈希: {current_hash} != 配音依据哈希: {manifest_hash}\n"
            f"请先重新生成配音：uv run python -m pipeline.tts \"{ep_dir}\"\n"
            f"（若确认无需重新配音，可传 --force 跳过检查）"
        )


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


def _key_of(c: dict) -> str | None:
    """clip → 片源登记键。season=None 是 SP 特典集（`SP01`）。

    与 clips._ep_key 同一条约定，这里抄一份而不是 import——本模块是零依赖叶子
    （见模块 docstring）。**不能用 season=0 占 SP 的位**：sources.json 里 S00E0x
    是 OVA 的既有登记（东京喰种/伪恋），0 被占了。缺 season/episode 键返回 None，
    由调用方报「缺键」错。
    """
    if "season" not in c or "episode" not in c:
        return None
    s, e = c["season"], c["episode"]
    return f"SP{e:02d}" if s is None else f"S{s:02d}E{e:02d}"


def verify_alignment(segments: list[dict], audio: list[dict]) -> list[str]:
    """返回违例描述列表，空 = 通过。

    status 不在可渲染终态（ok / ok_extended）的段没有 clips（render 本就拒收），
    跳过；但段数与 manifest 对不上必须报——zip 会静默吞掉错位（判据 9）。
    ok_extended 段（锚点段尾帧定格，2026-09-07）的总量是 Σ(dur + extend)。
    """
    violations: list[str] = []
    if len(segments) != len(audio):
        violations.append(
            f"段数不齐：04-clips.json {len(segments)} 段 / manifest {len(audio)} 段")
        return violations
    for seg, a in zip(segments, audio):
        if seg.get("status") not in ("ok", "ok_extended"):
            continue
        clips = seg.get("clips") or []
        if not clips:
            violations.append(f"段{seg['index']}: status={seg['status']} 但 clips 为空")
            continue
        got = sum(c["dur"] + c.get("extend", 0.0) for c in clips)
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

    ok_extended 段（锚点段尾帧定格）：漂移先由末片 `extend` 吸收——定格秒数
    本来就是排版产物，人审改的是画面身份（start/source），不动它；定格减光
    还不够时退回普通末片 dur 吸收，段状态同步降回 ok。
    """
    report: list[str] = []
    for seg, a in zip(segments, audio):
        if seg.get("status") not in ("ok", "ok_extended") or not seg.get("clips"):
            continue
        clips = seg["clips"]
        need = a["duration"]
        got = sum(c["dur"] + c.get("extend", 0.0) for c in clips)
        drift = round(need - got, 3)
        if abs(drift) <= SEG_TOL:
            continue          # 已对齐，no-op
        last = clips[-1]
        if seg["status"] == "ok_extended":
            ext = last.get("extend", 0.0)
            if ext + drift >= -SEG_TOL:        # 定格自己吸收得了（含刚好减到 0）
                new_ext = round(max(0.0, ext + drift), 3)
                if new_ext <= SEG_TOL:         # 定格减没了，退回普通段
                    last.pop("extend", None)
                    seg["status"] = "ok"
                else:
                    last["extend"] = new_ext
                report.append(
                    f"段{seg['index']}: 末片定格 {ext:.2f}s → {new_ext:.2f}s"
                    f"（漂移 {drift:+.2f}s 由定格吸收）")
                continue
            # 定格减光仍不够：剩余负漂移落到末片 dur，段退回 ok 走下面的末片吸收
            drift = round(ext + drift, 3)
            got = round(got - ext, 3)      # extend 已删，总量同步扣掉
            last.pop("extend", None)
            seg["status"] = "ok"
        key = _key_of(last)
        # 跨番产物（clip 带 anime 字段，2026-09-06）走复合键；单番/手写 clip
        # 平面键回退。内联而不 import ingest.sources_get：本模块是零依赖叶子
        # （见模块 docstring），ingest 会拖起整个 ML 栈，不许为它破例。
        src = (sources.get((last.get("anime"), key)) or sources.get(key)) if key else None
        if src is None:
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
