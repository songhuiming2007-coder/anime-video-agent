"""评估体系与基线冻结工具（M1a，E9：python -m pipeline.eval）。

本模块实现系统离线评估与基线测量：
1. freeze: 抽取指标并落盘不可变快照（data/eval/<tag>/<期目录名>.json）
2. report: 输出指定 tag 下各期的全指标人读表
3. diff: 逐期逐指标对比 tagA 与 tagB，标定回退（✗ 回退）、观测变差（⚠ 变差）与不可比（— 不可比）
4. annotate: 结构化回填 03.5/05 人审主观评分（1-5 整数），打上 manual_backfill 来源

设计依据与判据：
- S1/S2/S4/S7/S9/S10
- E3/E9/E10
- ADR-0005: 字节相同不等于零错配，缺人审置 null
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import paths

# ---------------------------------------------------------------------------
# 常量定义与判据依据（E3：每个常量后面写「为什么是这个数」）
# ---------------------------------------------------------------------------

# 人评允许修改的五个维度键名（ADR-0014 与二.3 节定义）
ALLOWED_HUMAN_REVIEW_KEYS = {
    "voice_stability",  # 03.5 音色稳定性 (1-5)
    "prosody",          # 03.5 韵律起伏感 (1-5)
    "misread",          # 03.5 错字与误读 (1-5)
    "imagery_fit",      # 05 画面意象贴合度 (1-5)
    "rhythm",           # 05 视听剪辑节奏感 (1-5)
}

# 指标优劣方向（E3：每个方向一句为什么）
# "lower": 越低越好；"higher": 越高越好；"neutral": 中性描述指标，不作优劣裁决
METRIC_DIRECTIONS: dict[str, str] = {
    # [TTS 配音]
    "tts.cer_median": "lower",             # 越低越好：CER 中位数反映常规发音准确率，错字越少越准
    "tts.cer_p90": "lower",                # 越低越好：P90 反映长尾最差段落表现，控制极端发飘与杂音
    "tts.attempts_mean": "lower",          # 越低越好：平均重试次数越低，合成耗时与人机摩擦力越小
    "tts.qc_skip_count": "lower",          # 越低越好：ASR 盲区豁免段越少，需人耳顺听兜底的负担越轻
    "tts.dur_ratio_out_of_band": "lower",  # 越低越好：时长超出设计区间的段数越少，排片水填压力越小
    # [Clips 排片]
    "clips.mismatch_rate": "lower",        # 越低越好：人审改动比例越低，机器自动化排片精度越高
    "clips.human_changed_segments": "lower", # 越低越好：人审手工介入段数越少，人工成本越低
    "clips.no_match_count": "lower",       # 越低越好：检索落空段数越少，素材与台词/意象匹配覆盖度越全
    "clips.ep_fell_back": "lower",         # 越低越好：集范围回退越少，集内精准匹配度越高
    # [QC 质检]
    "qc.pass": "higher",                   # 越高越好（布尔）：门禁全绿是进入发布环节的硬性前置
    "qc.violations_count": "lower",        # 越低越好：门禁违规项越少越好，0 项为全绿及格
    # [Human Review 人评]
    "human_review.voice_stability": "higher", # 越高越好：主观音色稳定度 1-5 分，5 分为音色完全统一
    "human_review.prosody": "higher",      # 越高越好：主观韵律起伏 1-5 分，5 分为抑扬顿挫情感充沛
    "human_review.misread": "higher",      # 越高越好：主观错读字 1-5 分，5 分为无任何错字漏字
    "human_review.imagery_fit": "higher",  # 越高越好：画面意象贴合度 1-5 分，5 分为视听完美契合
    "human_review.rhythm": "higher",       # 越高越好：节奏与视听推进 1-5 分，5 分为观感极佳
    # [中性/规模指标]
    "clips.segments": "neutral",           # 中性指标：总段数由文案长度决定，属规模特征而非质量好坏
    "clips.ok_count": "neutral",           # 中性指标：正常排片段数随总段数变动
    "clips.ok_extended_count": "neutral",  # 中性指标：SP 定格延展段数，属于客观排片手法统计
    "materials.anime": "neutral",          # 中性指标：动画时长占比由题材形态决定（纪录片含非动画素材）
    "materials.sp": "neutral",             # 中性指标：SP 特典占比由期级内容策划与素材构成决定
    "materials.live": "neutral",           # 中性指标：Live 现场占比随素材策划变动
    "materials.mv": "neutral",             # 中性指标：MV 素材占比随素材策划变动
    "materials.scan": "neutral",           # 中性指标：扫图微动占比随素材策划变动
}

# 基线及格线（E3：为什么是这个数）
# 纪律（S7/E3）：门限只允许两种来源——
#   ① 仓库既有硬门禁/判据（必须引出处）；
#   ② 明确的实测数据上界（引实测样本）。
# 未标定门限的指标不设及格线；在 diff 比对中，无门限指标变差仅标为「⚠ 变差」（观测级，不阻断），
# 只有带门限指标在 A 侧及格且 B 侧变差时，才标为阻断级的「✗ 回退」。
PASSING_THRESHOLDS: dict[str, float | bool] = {
    # 来源 ①：仓库既有硬门禁与判据
    "tts.cer_median": 0.20,              # 0.20：对齐 pipeline/tts.py 的 MAX_CER 门槛（行 55）
    "tts.dur_ratio_out_of_band": 0,      # 0：对齐 pipeline/tts.py DUR_BAND 门禁，时长比越界不允许发生
    "clips.no_match_count": 0,           # 0：对齐 clips 检索三级阶梯落空即硬失败交 05 人工纪律（CLAUDE.md 第四节）
    "qc.pass": True,                     # True：成片质检门禁 11 项全过为及格
    "qc.violations_count": 0,            # 0：成片质检门禁 0 违规项为及格
    # 来源 ①（约定）：5 分制主观评价约定及格线
    "human_review.voice_stability": 3,   # 3 分：5 分制常规约定及格线（约定，非机器标定）
    "human_review.prosody": 3,           # 3 分：5 分制常规约定及格线（约定，非机器标定）
    "human_review.misread": 3,           # 3 分：5 分制常规约定及格线（约定，非机器标定）
    "human_review.imagery_fit": 3,       # 3 分：5 分制常规约定及格线（约定，非机器标定）
    "human_review.rhythm": 3,            # 3 分：5 分制常规约定及格线（约定，非机器标定）
    # 来源 ②：实测数据上界
    "tts.qc_skip_count": 15,             # 15：v1 两期实测最大 13 处的观测上界+余量，非质量判据
}

# 报告与比对的指标清单（按业务域分组）
METRIC_SPECS = [
    # (domain, key, display_name, format_type)
    ("tts", "tts.cer_median", "CER 中位数", "float4"),
    ("tts", "tts.cer_p90", "CER P90", "float4"),
    ("tts", "tts.attempts_mean", "平均重试次数", "float4"),
    ("tts", "tts.qc_skip_count", "ASR 盲区豁免段数", "int"),
    ("tts", "tts.dur_ratio_out_of_band", "时长比越界段数", "int"),

    ("clips", "clips.segments", "总段数", "int"),
    ("clips", "clips.mismatch_rate", "排片错配率", "percent"),
    ("clips", "clips.human_changed_segments", "人审改动段数", "int"),
    ("clips", "clips.ok_count", "status=ok 段数", "int"),
    ("clips", "clips.ok_extended_count", "status=ok_extended 段数", "int"),
    ("clips", "clips.no_match_count", "status=no_match 段数", "int"),
    ("clips", "clips.ep_fell_back", "集范围回退段数", "int"),

    ("qc", "qc.pass", "质检门禁通过", "bool"),
    ("qc", "qc.violations_count", "质检违规项数", "int"),

    ("human_review", "human_review.voice_stability", "音色稳定度 (1-5)", "int"),
    ("human_review", "human_review.prosody", "韵律起伏感 (1-5)", "int"),
    ("human_review", "human_review.misread", "读音正确度 (1-5)", "int"),
    ("human_review", "human_review.imagery_fit", "意象贴合度 (1-5)", "int"),
    ("human_review", "human_review.rhythm", "剪辑节奏感 (1-5)", "int"),

    ("materials", "materials.anime", "动画时长占比", "percent"),
    ("materials", "materials.sp", "SP 特典时长占比", "percent"),
    ("materials", "materials.live", "Live 现场时长占比", "percent"),
    ("materials", "materials.mv", "MV 时长占比", "percent"),
    ("materials", "materials.scan", "扫图时长占比", "percent"),
]


# ---------------------------------------------------------------------------
# 第一节：纯函数计算层（指标抽取、分布计算、直方图、diff 判定）
# ---------------------------------------------------------------------------


def compute_cer_distribution(cers: list[float]) -> tuple[float | None, float | None]:
    """计算 CER 中位数与 P90（90 分位数）。

    使用 statistics.quantiles(..., n=100, method='inclusive')，
    与 numpy.percentile(cers, 90) 口径精确一致，零第三方依赖。
    """
    if not cers:
        return None, None
    if len(cers) == 1:
        v = round(float(cers[0]), 4)
        return v, v
    med = round(float(statistics.median(cers)), 4)
    p90 = round(float(statistics.quantiles(cers, n=100, method="inclusive")[89]), 4)
    return med, p90


def compute_attempts_mean(attempts: list[int | float]) -> float | None:
    """计算 TTS 平均重试次数。"""
    if not attempts:
        return None
    return round(float(sum(attempts) / len(attempts)), 4)


def compute_status_hist(statuses: list[str]) -> dict[str, int]:
    """计算 clips status 直方图。"""
    out: dict[str, int] = {}
    for st in statuses:
        out[st] = out.get(st, 0) + 1
    return dict(sorted(out.items()))


def compute_rung_hist(segments: list[dict]) -> dict[str, int]:
    """计算检索阶梯 rung 分布直方图。

    只统计 channel ∈ {'line', 'scene'} 的检索段。
    锚点段（channel == 'anchor'）在 clips.py 中 rung 恒为 1，统计进去只会污染检索阶梯深度的读数。
    """
    out: dict[str, int] = {}
    for s in segments:
        channel = s.get("channel")
        if channel in {"line", "scene"}:
            r = s.get("rung")
            if r is not None:
                k = str(r)
                out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def compute_materials_ratio(clips_data: dict) -> tuple[float | None, float | None]:
    """根据 clips 计算 anime 与 sp 时长占比。"""
    segments = clips_data.get("segments") or []
    total_dur = 0.0
    sp_dur = 0.0
    anime_dur = 0.0
    for s in segments:
        for c in s.get("clips") or []:
            dur = float(c.get("dur", 0.0))
            total_dur += dur
            if c.get("sp"):
                sp_dur += dur
            else:
                anime_dur += dur
    if total_dur <= 0:
        return None, None
    return round(anime_dur / total_dur, 4), round(sp_dur / total_dur, 4)


def compute_clip_diff(
    machine_data: dict, approved_data: dict, bytes_equal: bool
) -> tuple[int | None, float | None, str | None]:
    """计算排片人审改动数与错配率。

    判据（ADR-0005）：
    两文件字节完全相同且无注记 → 判定为人审未介入或直接拷贝，不可视为零错配，置 null。
    只有存在人工修改或注记时，才返回改动段数和错配率。
    """
    # 延迟 import，防止顶层引入 ML 依赖链
    from . import recheck

    m_segs = machine_data.get("segments") or []
    a_segs = approved_data.get("segments") or []

    # 扫描人工注记（如 _manual_fix）
    notes_exist = any(
        recheck._annotation_notes(s) for s in (m_segs + a_segs)
    )

    if bytes_equal and not notes_exist:
        return (
            None,
            None,
            "04-clips.json 与 04-clips.approved.json 字节相同且无注记，判定为人审未介入或直接拷贝，依 ADR-0005 置 null",
        )

    m_by_idx = {s["index"]: s for s in m_segs}
    a_by_idx = {s["index"]: s for s in a_segs}
    common = sorted(set(m_by_idx) & set(a_by_idx))

    diff_count = 0
    for idx in common:
        b, _ = recheck.bucket_segment(m_by_idx[idx], a_by_idx[idx])
        if b != "unchanged":
            diff_count += 1

    total = len(m_by_idx)
    mismatch_rate = round(diff_count / total, 4) if total > 0 else None
    return diff_count, mismatch_rate, None


def parse_qc_log(qc_log_text: str) -> tuple[bool, list[str]]:
    """解析 06-check.log 提取通过状态与违规明细。"""
    violations: list[str] = []
    for raw_line in qc_log_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("FAIL") or line.startswith("SKIP"):
            violations.append(line)
    passed = len(violations) == 0
    return passed, violations


def extract_snapshot(
    episode: str,
    tag: str,
    frozen_at: str,
    manifest_data: dict | None,
    clips_data: dict | None,
    approved_data: dict | None,
    qc_log_text: str | None,
    clips_bytes_equal: bool = False,
) -> dict[str, Any]:
    """抽取指标纯函数，组合生成标准快照 Schema。

    若文件缺失则对应字段置 null 并写入 unavailable_reasons，绝不填 0 装死（S4/S9）。
    """
    unavailable_reasons: dict[str, str] = {}

    # 1. TTS 抽取
    if manifest_data is None:
        tts_info: dict[str, Any] = {
            "cer_median": None,
            "cer_p90": None,
            "attempts_mean": None,
            "qc_skip_segments": None,
            "dur_ratio_out_of_band": None,
            "unavailable_reason": "缺 03-audio/manifest.json",
        }
        unavailable_reasons["tts"] = "缺 03-audio/manifest.json"
    else:
        segs = manifest_data.get("segments") or []
        cers = [float(s["cer"]) for s in segs if "cer" in s and s["cer"] is not None]
        attempts = [
            int(s["attempts"])
            for s in segs
            if "attempts" in s and s["attempts"] is not None
        ]
        cer_med, cer_p90 = compute_cer_distribution(cers)
        att_mean = compute_attempts_mean(attempts)
        skips: list[Any] = []
        for s in segs:
            if s.get("qc_skip"):
                lbl = s.get("label", s.get("index"))
                skips.append(int(lbl) if str(lbl).isdigit() else lbl)

        tts_info = {
            "cer_median": cer_med,
            "cer_p90": cer_p90,
            "attempts_mean": att_mean,
            "qc_skip_segments": skips,
            "dur_ratio_out_of_band": None,
        }
        # dur_ratio_out_of_band 纪律：不引入 pypinyin 轻量假算，置 null
        unavailable_reasons["tts.dur_ratio_out_of_band"] = (
            "manifest 无此字段，纯函数重算需引入 pypinyin 会失真，依判据 S7/ADR-0005 设为 null"
        )

    # 2. Clips 抽取
    if clips_data is None:
        clips_info: dict[str, Any] = {
            "segments": None,
            "status": None,
            "human_changed_segments": None,
            "mismatch_rate": None,
            "rung_hist": None,
            "ep_fell_back": None,
            "unavailable_reason": "缺 04-clips.json",
        }
        unavailable_reasons["clips"] = "缺 04-clips.json"
    else:
        segs = clips_data.get("segments") or []
        statuses = [s.get("status", "unknown") for s in segs]
        ep_fell = sum(1 for s in segs if s.get("ep_fell_back"))

        if approved_data is None:
            changed_segs, mismatch, diff_reason = (
                None,
                None,
                "缺 04-clips.approved.json（尚未人审）",
            )
        else:
            changed_segs, mismatch, diff_reason = compute_clip_diff(
                clips_data, approved_data, clips_bytes_equal
            )

        if diff_reason:
            unavailable_reasons["clips.mismatch"] = diff_reason

        clips_info = {
            "segments": len(segs),
            "status": compute_status_hist(statuses),
            "human_changed_segments": changed_segs,
            "mismatch_rate": mismatch,
            "rung_hist": compute_rung_hist(segs),
            "ep_fell_back": ep_fell,
        }

    # 3. QC 抽取
    if qc_log_text is None:
        qc_info: dict[str, Any] = {
            "pass": None,
            "violations": None,
            "unavailable_reason": "缺 06-check.log",
        }
        unavailable_reasons["qc"] = "缺 06-check.log"
    else:
        qc_pass, violations = parse_qc_log(qc_log_text)
        qc_info = {"pass": qc_pass, "violations": violations}

    # 4. Human Review 抽取（初始化全 null，留待 annotate 回填）
    human_info: dict[str, Any] = {
        "voice_stability": None,
        "prosody": None,
        "misread": None,
        "imagery_fit": None,
        "rhythm": None,
        "_source": None,
    }
    unavailable_reasons["human_review"] = "待人工回填（使用 python -m pipeline.eval annotate）"

    # 5. Materials 抽取（仅计算 anime/sp，其余置 null）
    if clips_data is None:
        materials_info: dict[str, Any] = {
            "anime": None,
            "sp": None,
            "live": None,
            "mv": None,
            "scan": None,
        }
        unavailable_reasons["materials"] = "缺 04-clips.json，无法统计素材时长"
    else:
        anime_ratio, sp_ratio = compute_materials_ratio(clips_data)
        materials_info = {
            "anime": anime_ratio,
            "sp": sp_ratio,
            "live": None,
            "mv": None,
            "scan": None,
        }
        unavailable_reasons["materials.subcategories"] = (
            "live/mv/scan 细分在现有产物中不可推导，依判据 S7 置 null"
        )

    return {
        "episode": episode,
        "tag": tag,
        "frozen_at": frozen_at,
        "tts": tts_info,
        "clips": clips_info,
        "qc": qc_info,
        "human_review": human_info,
        "materials": materials_info,
        "unavailable_reasons": unavailable_reasons,
    }


# 特例提取映射：针对非直接点分属性（如 len() 或 status 直方图子键）
SPECIAL_METRIC_EXTRACTORS = {
    "tts.qc_skip_count": lambda s: (
        len(s["tts"]["qc_skip_segments"])
        if isinstance(s.get("tts"), dict)
        and isinstance(s["tts"].get("qc_skip_segments"), list)
        else None
    ),
    "clips.ok_count": lambda s: (
        s["clips"]["status"].get("ok", 0)
        if isinstance(s.get("clips"), dict)
        and isinstance(s["clips"].get("status"), dict)
        else None
    ),
    "clips.ok_extended_count": lambda s: (
        s["clips"]["status"].get("ok_extended", 0)
        if isinstance(s.get("clips"), dict)
        and isinstance(s["clips"].get("status"), dict)
        else None
    ),
    "clips.no_match_count": lambda s: (
        s["clips"]["status"].get("no_match", 0)
        if isinstance(s.get("clips"), dict)
        and isinstance(s["clips"].get("status"), dict)
        else None
    ),
    "qc.violations_count": lambda s: (
        len(s["qc"]["violations"])
        if isinstance(s.get("qc"), dict)
        and isinstance(s["qc"].get("violations"), list)
        else None
    ),
}

# 指标不可用原因别名映射：当指标为 None 时定向查找特定的 reason 键
REASON_KEY_ALIASES = {
    "clips.mismatch_rate": "clips.mismatch",
    "clips.human_changed_segments": "clips.mismatch",
    "materials.live": "materials.subcategories",
    "materials.mv": "materials.subcategories",
    "materials.scan": "materials.subcategories",
}


def get_metric_value(snapshot: dict, key: str) -> tuple[Any, str | None]:
    """根据点分路径从快照中提取指标值及其不可用原因。

    通用点分路径提取 + 特例表解析，消除繁琐的硬编码 if 链。
    """
    reasons = snapshot.get("unavailable_reasons") or {}

    # 1. 尝试特例提取器
    if key in SPECIAL_METRIC_EXTRACTORS:
        val = SPECIAL_METRIC_EXTRACTORS[key](snapshot)
    else:
        # 2. 通用点分路径提取
        cur: Any = snapshot
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        val = cur

    if val is not None:
        return val, None

    # 3. 提取失败时查找原因
    domain = key.split(".", 1)[0]
    reason = (
        reasons.get(key)
        or reasons.get(REASON_KEY_ALIASES.get(key, ""))
        or reasons.get(domain)
        or (
            snapshot.get(domain, {}).get("unavailable_reason")
            if isinstance(snapshot.get(domain), dict)
            else None
        )
    )
    return None, reason


def is_metric_passing(key: str, val: Any) -> bool:
    """判定某指标值是否达到及格线。"""
    if val is None:
        return False
    thresh = PASSING_THRESHOLDS.get(key)
    if thresh is None:
        return True  # 无显式门限者默认视为及格
    direction = METRIC_DIRECTIONS.get(key, "neutral")
    if direction == "lower":
        return float(val) <= float(thresh)
    if direction == "higher":
        if isinstance(thresh, bool):
            return bool(val) is bool(thresh)
        return float(val) >= float(thresh)
    return True


def judge_metric_diff(key: str, val_a: Any, val_b: Any) -> tuple[str, str]:
    """比对指标 A 侧与 B 侧，返回 (变化量字符串, 裁决结果字符串)。

    判据（S7/E3 返修）：
    - 任一侧为 null → 标「— 不可比」，不裁决
    - 两侧非 null 且数值相等 → 标「= 持平」
    - B 侧改善 → 标「✓ 提升」
    - B 侧变差：
      - 若该指标在 PASSING_THRESHOLDS 拥有门限，且 A 侧及格 → 标「✗ 回退」（阻断级）
      - 若该指标在 PASSING_THRESHOLDS 拥有门限，但 A 侧已不及格 → 标「✗ 持续不及格」
      - 若该指标无门限（如 mismatch_rate / cer_p90 / attempts_mean） → 标「⚠ 变差」（观测级，不阻断）
    - 中性指标 → 标「~ 变动」或「= 持平」
    """
    if val_a is None or val_b is None:
        return "—", "— 不可比"

    direction = METRIC_DIRECTIONS.get(key, "neutral")

    # 1. 布尔类型处理
    if isinstance(val_a, bool) or isinstance(val_b, bool):
        ba, bb = bool(val_a), bool(val_b)
        delta_str = f"{'PASS' if ba else 'FAIL'} → {'PASS' if bb else 'FAIL'}"
        if ba == bb:
            return "=", "= 持平"
        if bb:
            return delta_str, "✓ 提升"
        # 从 PASS 变为 FAIL 且 A 侧及格
        return delta_str, "✗ 回退" if is_metric_passing(key, ba) else "✗ 持续不及格"

    # 2. 数值类型处理
    try:
        fa, fb = float(val_a), float(val_b)
    except (ValueError, TypeError):
        return f"{val_a} → {val_b}", "— 不可比"

    diff = fb - fa
    delta_str = f"{diff:+.4f}".rstrip("0").rstrip(".") if abs(diff) > 1e-6 else "="

    if abs(diff) < 1e-6:
        return "=", "= 持平"

    if direction == "neutral":
        return delta_str, "~ 变动"

    is_better = (diff < 0) if direction == "lower" else (diff > 0)
    if is_better:
        return delta_str, "✓ 提升"

    # 变差分支：区分有门限阻断 vs 无门限观测
    has_threshold = key in PASSING_THRESHOLDS
    if not has_threshold:
        return delta_str, "⚠ 变差"

    a_is_passing = is_metric_passing(key, val_a)
    return delta_str, "✗ 回退" if a_is_passing else "✗ 持续不及格"


def annotate_snapshot(snapshot: dict, updates: dict[str, int]) -> dict:
    """人工回填结构化纯函数。

    校验键名必须为 human_review.* 五个键，值必须在 1~5 整数，打上 _source: manual_backfill。
    五个键全齐时清除 unavailable_reasons.human_review，部分回填时更新剩余待填项。
    """
    for k, v in updates.items():
        if k not in ALLOWED_HUMAN_REVIEW_KEYS:
            raise SystemExit(
                f"FAIL 只许修改 human_review.* 下的五个键（{', '.join(sorted(ALLOWED_HUMAN_REVIEW_KEYS))}），收到: {k}\n"
                f"     可执行格式示例: --set human_review.prosody=2"
            )
        if not isinstance(v, int) or v < 1 or v > 5:
            raise SystemExit(
                f"FAIL {k} 取值必须为 1~5 的整数，收到: {v}\n"
                f"     可执行格式示例: --set human_review.{k}=3"
            )

    out = json.loads(json.dumps(snapshot))
    hr = out.setdefault("human_review", {})
    for k, v in updates.items():
        hr[k] = v
    hr["_source"] = "manual_backfill"

    # 清理或更新 unavailable_reasons["human_review"]
    reasons = out.setdefault("unavailable_reasons", {})
    missing_keys = [k for k in sorted(ALLOWED_HUMAN_REVIEW_KEYS) if hr.get(k) is None]
    if not missing_keys:
        reasons.pop("human_review", None)
    else:
        reasons["human_review"] = f"待人工回填：剩余 {', '.join(missing_keys)}"

    return out


# ---------------------------------------------------------------------------
# 第二节：报告与 Diff 表格渲染层（纯文本渲染）
# ---------------------------------------------------------------------------


def _format_cell_value(val: Any, fmt_type: str) -> str:
    """根据指标展示格式转换显示字符串。"""
    if val is None:
        return "—"
    if fmt_type == "bool":
        return "PASS" if val else "FAIL"
    if fmt_type == "percent":
        return f"{float(val) * 100:.1f}%"
    if fmt_type == "float4":
        return f"{float(val):.4f}"
    if fmt_type == "int":
        return str(int(val))
    return str(val)


def build_report(snapshots: list[dict]) -> str:
    """生成该 tag 下所有期 × 全指标的人读表格字符串，含 reason 脚注。"""
    if not snapshots:
        return "（无快照数据）"

    episodes = [s.get("episode", f"ep_{i}") for i, s in enumerate(snapshots)]

    # 收集脚注：(reason -> index)
    footnote_map: dict[str, int] = {}
    footnotes: list[str] = []

    def get_fn(reason: str | None) -> str:
        if not reason:
            return ""
        if reason not in footnote_map:
            idx = len(footnotes) + 1
            footnote_map[reason] = idx
            footnotes.append(f"  [*{idx}] {reason}")
        return f" [*{footnote_map[reason]}]"

    # 构建每行数据
    tag_name = snapshots[0].get("tag", "unknown")
    header_title = f"Tag: {tag_name} ({len(snapshots)} 期)"

    # 计算各列宽度
    metric_col_width = 24
    ep_col_widths = [max(len(ep) + 4, 18) for ep in episodes]

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"  {header_title}")
    lines.append("=" * 78)

    # 表头
    header_row = f"{'指标':<{metric_col_width}}" + "".join(
        f"{ep:>{w}}" for ep, w in zip(episodes, ep_col_widths)
    )
    lines.append(header_row)
    lines.append("-" * 78)

    cur_domain = ""
    domain_labels = {
        "tts": "TTS 配音",
        "clips": "Clips 排片",
        "qc": "QC 质检",
        "human_review": "Human Review 人评",
        "materials": "Materials 素材",
    }

    for domain, key, disp_name, fmt in METRIC_SPECS:
        if domain != cur_domain:
            cur_domain = domain
            lines.append(f"[{domain_labels.get(domain, domain)}]")

        cells: list[str] = []
        for s, w in zip(snapshots, ep_col_widths):
            val, reason = get_metric_value(s, key)
            if val is None:
                fn_tag = get_fn(reason)
                cell_str = f"—{fn_tag}"
            else:
                cell_str = _format_cell_value(val, fmt)
            cells.append(f"{cell_str:>{w}}")
        row_str = f"  {disp_name:<{metric_col_width - 2}}" + "".join(cells)
        lines.append(row_str)

    if footnotes:
        lines.append("-" * 78)
        lines.append("脚注说明：")
        lines.extend(footnotes)
    lines.append("=" * 78)

    return "\n".join(lines)


def build_diff_report(
    snaps_a: list[dict], snaps_b: list[dict], tag_a: str, tag_b: str
) -> str:
    """生成 tagA 与 tagB 逐期逐指标对比表。"""
    map_a = {s.get("episode"): s for s in snaps_a}
    map_b = {s.get("episode"): s for s in snaps_b}
    common_eps = sorted(set(map_a) & set(map_b))

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"  Diff 评估对比: {tag_a}  →  {tag_b}")
    lines.append("=" * 78)

    if not common_eps:
        lines.append(f"未找到共同期！tag_a 期: {sorted(map_a.keys())}；tag_b 期: {sorted(map_b.keys())}")
        lines.append("=" * 78)
        return "\n".join(lines)

    total_regressions = 0
    total_warnings = 0
    total_improvements = 0
    total_ties = 0
    total_incomparable = 0

    for ep in common_eps:
        lines.append(f"\n### 期: {ep}")
        lines.append(
            f"{'指标':<22}{tag_a:>14}{tag_b:>14}{'变化量':>14}  {'裁决':<10}"
        )
        lines.append("-" * 78)
        sa = map_a[ep]
        sb = map_b[ep]

        cur_domain = ""
        for domain, key, disp_name, fmt in METRIC_SPECS:
            if domain != cur_domain:
                cur_domain = domain
                lines.append(f"[{domain}]")

            va, _ = get_metric_value(sa, key)
            vb, _ = get_metric_value(sb, key)

            delta_str, verdict = judge_metric_diff(key, va, vb)

            va_str = _format_cell_value(va, fmt)
            vb_str = _format_cell_value(vb, fmt)

            if "回退" in verdict:
                total_regressions += 1
            elif "变差" in verdict:
                total_warnings += 1
            elif "提升" in verdict:
                total_improvements += 1
            elif "持平" in verdict:
                total_ties += 1
            elif "不可比" in verdict:
                total_incomparable += 1

            lines.append(
                f"  {disp_name:<20}{va_str:>14}{vb_str:>14}{delta_str:>14}  {verdict:<10}"
            )

    lines.append("-" * 78)
    summary_msg = (
        f"全期汇总: 提升 {total_improvements} 项，回退 {total_regressions} 项"
    )
    if total_warnings > 0:
        summary_msg += f"（另有观测级变差 {total_warnings} 项）"
    summary_msg += f"，持平 {total_ties} 项，不可比 {total_incomparable} 项"
    lines.append(summary_msg)

    if total_regressions > 0:
        lines.append(f"⚠️  警告: 存在 {total_regressions} 项指标回退！改动不满足合并门禁。")
    else:
        lines.append("✓  无回退项。")
    lines.append("=" * 78)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 第三节：I/O 与 CLI 驱动层
# ---------------------------------------------------------------------------


def freeze_episode(target_dir: Path, tag: str, force: bool = False) -> Path:
    """冻结本期快照，追加式写入 data/eval/<tag>/<期目录名>.json。"""
    paths.require_data()
    ep_dir = target_dir.resolve()
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在：{ep_dir}")

    eval_dir = paths.DATA / "eval" / tag
    eval_dir.mkdir(parents=True, exist_ok=True)
    out_path = eval_dir / f"{ep_dir.name}.json"

    if out_path.exists() and not force:
        raise SystemExit(
            f"FAIL 快照已存在：{out_path}。\n"
            f"     基线不许改写（追加式纪律）。如需覆盖请显式加 --force：\n"
            f"     python -m pipeline.eval freeze {target_dir} --tag {tag} --force"
        )

    # 1. 读 manifest.json
    mf_path = ep_dir / "03-audio" / "manifest.json"
    manifest_data = (
        json.loads(mf_path.read_text(encoding="utf-8"))
        if mf_path.exists()
        else None
    )

    # 2. 读 04-clips.json 与 approved.json
    clips_path = ep_dir / "04-clips.json"
    app_path = ep_dir / "04-clips.approved.json"
    clips_data = (
        json.loads(clips_path.read_text(encoding="utf-8"))
        if clips_path.exists()
        else None
    )
    app_data = (
        json.loads(app_path.read_text(encoding="utf-8"))
        if app_path.exists()
        else None
    )

    clips_bytes_equal = False
    if clips_path.exists() and app_path.exists():
        clips_bytes_equal = clips_path.read_bytes() == app_path.read_bytes()

    # 3. 读 06-check.log
    qc_path = ep_dir / "06-check.log"
    qc_log_text = qc_path.read_text(encoding="utf-8") if qc_path.exists() else None

    # 抽取快照
    frozen_date = str(date.today())
    snapshot = extract_snapshot(
        episode=ep_dir.name,
        tag=tag,
        frozen_at=frozen_date,
        manifest_data=manifest_data,
        clips_data=clips_data,
        approved_data=app_data,
        qc_log_text=qc_log_text,
        clips_bytes_equal=clips_bytes_equal,
    )

    data_str = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    paths.atomic_write(out_path, data_str)
    print(f"OK 冻结快照落盘: {out_path}")
    return out_path


def load_tag_snapshots(tag: str) -> list[dict]:
    """读取指定 tag 下所有快照 JSON。不许静默吞异常，解析失败向 stderr 报警。"""
    paths.require_data()
    eval_dir = paths.DATA / "eval" / tag
    if not eval_dir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(eval_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"WARN 快照文件损坏或无法解析，已跳过: {p} ({e})", file=sys.stderr)
    return out


def find_snapshot_file(tag: str, slug: str) -> Path | None:
    """根据 tag 与 slug 查找快照文件。解析失败向 stderr 报警。"""
    paths.require_data()
    eval_dir = paths.DATA / "eval" / tag
    if not eval_dir.is_dir():
        return None

    # 1. 直接文件名匹配
    candidate = eval_dir / (slug if slug.endswith(".json") else f"{slug}.json")
    if candidate.exists():
        return candidate

    # 2. 匹配 stem 或 episode 字段
    for p in sorted(eval_dir.glob("*.json")):
        if p.stem == slug:
            return p
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("episode") == slug:
                return p
        except Exception as e:
            print(f"WARN 快照文件损坏或无法解析: {p} ({e})", file=sys.stderr)

    # 3. 子串匹配（唯一时放行）
    subs = [p for p in eval_dir.glob("*.json") if slug in p.stem]
    if len(subs) == 1:
        return subs[0]

    return None


def annotate_file(tag: str, slug: str, raw_sets: list[str]) -> Path:
    """人工回填主观评价并原子写回。"""
    paths.require_data()
    p = find_snapshot_file(tag, slug)
    if p is None:
        raise SystemExit(
            f"FAIL 找不到快照 data/eval/{tag}/{slug}.json。\n"
            f"     先运行 freeze 建立快照:\n"
            f"     python -m pipeline.eval freeze data/episodes/{slug} --tag {tag}"
        )

    updates: dict[str, int] = {}
    for item in raw_sets:
        if "=" not in item:
            raise SystemExit(
                f"FAIL --set 格式错误，应为 key=val，收到: {item}\n"
                f"     可执行格式示例: --set human_review.prosody=2"
            )
        k, v_str = item.split("=", 1)
        k = k.strip()
        sub_key = k[len("human_review.") :] if k.startswith("human_review.") else k
        try:
            v = int(v_str.strip())
        except ValueError:
            raise SystemExit(
                f"FAIL {k} 的值必须为整数 1~5，收到: {v_str}\n"
                f"     可执行格式示例: --set {k}=3"
            )
        updates[sub_key] = v

    data = json.loads(p.read_text(encoding="utf-8"))
    updated_data = annotate_snapshot(data, updates)
    paths.atomic_write(
        p, json.dumps(updated_data, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"OK 已回填 {tag}/{p.stem} 人评: {updates} (_source: manual_backfill)")
    return p


# ---------------------------------------------------------------------------
# 第四节：CLI 入口
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="评估体系（E9：python -m pipeline.eval）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # freeze
    p_freeze = sub.add_parser("freeze", help="抽取指标并冻结不可变快照")
    p_freeze.add_argument("target", type=Path, help="本期目录，如 data/episodes/...")
    p_freeze.add_argument("--tag", required=True, help="快照标签，如 v1-baseline")
    p_freeze.add_argument(
        "--force", action="store_true", help="强制覆盖已有快照（默认追加式拒绝覆盖）"
    )

    # report
    p_report = sub.add_parser("report", help="打印该 tag 下所有人读指标表")
    p_report.add_argument("--tag", required=True, help="快照标签，如 v1-baseline")
    p_report.add_argument("--json", action="store_true", help="输出原始 JSON 数据")

    # diff
    p_diff = sub.add_parser("diff", help="逐期逐指标对比 tagA 与 tagB")
    p_diff.add_argument("tag_a", help="基准 tag，如 v1-baseline")
    p_diff.add_argument("tag_b", help="对比 tag，如 v2-tag")
    p_diff.add_argument("--json", action="store_true", help="输出结构化对比结果")

    # annotate
    p_ann = sub.add_parser(
        "annotate", help="人工回填主观评价（03.5/05 结构化打点）"
    )
    p_ann.add_argument("tag", help="快照标签")
    p_ann.add_argument("slug", help="期目录名或 slug")
    p_ann.add_argument(
        "--set",
        action="append",
        required=True,
        dest="sets",
        help="设置项，格式 key=val，如 --set human_review.prosody=2",
    )

    args = ap.parse_args()

    if args.cmd == "freeze":
        freeze_episode(args.target, args.tag, force=args.force)
        return 0

    if args.cmd == "report":
        snaps = load_tag_snapshots(args.tag)
        if not snaps:
            raise SystemExit(
                f"FAIL 标签 {args.tag} 下未找到任何快照（目录 data/eval/{args.tag} 为空或不存在）。\n"
                f"     先运行 freeze 建立快照:\n"
                f"     python -m pipeline.eval freeze data/episodes/<期> --tag {args.tag}"
            )
        if args.json:
            print(json.dumps(snaps, ensure_ascii=False, indent=2))
        else:
            print(build_report(snaps))
        return 0

    if args.cmd == "diff":
        snaps_a = load_tag_snapshots(args.tag_a)
        snaps_b = load_tag_snapshots(args.tag_b)
        if not snaps_a:
            raise SystemExit(
                f"FAIL tagA ({args.tag_a}) 下未找到任何快照。\n"
                f"     先运行 freeze 建立快照:\n"
                f"     python -m pipeline.eval freeze data/episodes/<期> --tag {args.tag_a}"
            )
        if not snaps_b:
            raise SystemExit(
                f"FAIL tagB ({args.tag_b}) 下未找到任何快照。\n"
                f"     先运行 freeze 建立快照:\n"
                f"     python -m pipeline.eval freeze data/episodes/<期> --tag {args.tag_b}"
            )
        if args.json:
            map_a = {s.get("episode"): s for s in snaps_a}
            map_b = {s.get("episode"): s for s in snaps_b}
            common = sorted(set(map_a) & set(map_b))
            out_diff = []
            for ep in common:
                ep_rows = []
                for _, key, disp, _ in METRIC_SPECS:
                    va, _ = get_metric_value(map_a[ep], key)
                    vb, _ = get_metric_value(map_b[ep], key)
                    delta, verdict = judge_metric_diff(key, va, vb)
                    ep_rows.append(
                        {
                            "key": key,
                            "display": disp,
                            "val_a": va,
                            "val_b": vb,
                            "delta": delta,
                            "verdict": verdict,
                        }
                    )
                out_diff.append({"episode": ep, "metrics": ep_rows})
            print(json.dumps(out_diff, ensure_ascii=False, indent=2))
        else:
            print(build_diff_report(snaps_a, snaps_b, args.tag_a, args.tag_b))
        return 0

    if args.cmd == "annotate":
        annotate_file(args.tag, args.slug, args.sets)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
