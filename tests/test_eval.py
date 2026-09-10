"""纯函数测试：评估体系与快照抽取（tests/test_eval.py）。

对齐 STANDARD 第八节与 M1a 返修要求：
- 纯函数测试，内联构造 fixture dict，绝不读 data/，保持 pytest 3 秒可跑。
- 每条断言注释说明防范的具体错误。
- 覆盖：
  1. CER 分布计算（空值、单值、多值）与 attempts 均值
  2. status 直方图与 rung_hist（锚点段 rung=1 恒定，必须排除防污染）
  3. 缺文件 null 路径与 Schema 完整性（S4/S9 绝不填 0 装死）
  4. 字节相同≠零错配判定（ADR-0005 核心判据）
  5. diff 判定与回退分类（有门限阻断 ✗ 回退 vs 无门限观测 ⚠ 变差，自比持平）
  6. annotate 完整/部分回填 reason 动态清理与越界拦截
  7. load/find 损坏快照 stderr 报警不静默吞异常
  8. get_metric_value 通用点分路径与特例解析
- 变异检验已通过：故意改坏被测逻辑可立即触发断言失败。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import eval as ev


# ---------------------------------------------------------------------------
# 1. CER 分布与重试均值计算
# ---------------------------------------------------------------------------


def test_cer_distribution_empty():
    """空列表必须返回 (None, None)，防范 ZeroDivisionError 或假填 0.0。"""
    med, p90 = ev.compute_cer_distribution([])
    assert med is None, "空列表 median 必须为 None，不可假填 0.0 装作无错字"
    assert p90 is None, "空列表 p90 必须为 None，不可假填 0.0"


def test_cer_distribution_single():
    """单元素列表应返回该元素本身，防范分位数计算崩溃。"""
    med, p90 = ev.compute_cer_distribution([0.05])
    assert med == 0.05, "单元素中位数应为该元素自身"
    assert p90 == 0.05, "单元素 P90 应为该元素自身"


def test_cer_distribution_multiple():
    """多样本 CER 计算中位数与 90 分位数，防范分位数边界索引计算偏移。"""
    cers = [0.01 * i for i in range(1, 11)]
    med, p90 = ev.compute_cer_distribution(cers)
    assert med == 0.055, "中位数应为居中两数均值 0.055"
    assert p90 == 0.091, "P90 需与 inclusive 分位数口径一致"


def test_attempts_mean():
    """重试次数均值计算，防范整数除法截断。"""
    assert ev.compute_attempts_mean([]) is None, "无样本时必须返回 None"
    att = [1, 2, 3]
    assert ev.compute_attempts_mean(att) == 2.0, "均值应为浮点数 2.0"
    att2 = [1, 1, 2]
    assert ev.compute_attempts_mean(att2) == 1.3333, "浮点保留 4 位有效小数"


# ---------------------------------------------------------------------------
# 2. Clips 状态直方图、rung 阶梯（排除锚点段）与素材比例
# ---------------------------------------------------------------------------


def test_status_hist():
    """status 直方图统计，防范漏计状态种类。"""
    statuses = ["ok", "ok", "ok_extended", "no_match"]
    hist = ev.compute_status_hist(statuses)
    assert hist == {"no_match": 1, "ok": 2, "ok_extended": 1}, (
        "直方图键必须包含全部状态且键有序"
    )


def test_rung_hist_filters_anchor_channel():
    """检索阶梯 rung 直方图必须排除 channel=='anchor' 的段，防范硬编码 rung=1 污染深度读数。"""
    segments = [
        {"channel": "anchor", "rung": 1},  # 锚点段硬编码 rung=1，必须被忽略
        {"channel": "anchor", "rung": 1},
        {"channel": "line", "rung": 1},    # 台词检索命中 top-1
        {"channel": "line", "rung": 2},    # 台词检索走备选 rung=2
        {"channel": "scene", "rung": 3},   # 场景检索走配音 rung=3
        {"channel": "line", "rung": None}, # 未设 rung 忽略
    ]
    hist = ev.compute_rung_hist(segments)
    assert hist == {"1": 1, "2": 1, "3": 1}, (
        "只应统计 channel in {line, scene} 的段落，anchor 段不计入"
    )


def test_rung_hist_all_anchor():
    """纯锚点排片期（如 EGOIST 前两期），rung 直方图应为空字典，诚实反映无语义检索阶梯。"""
    segments = [{"channel": "anchor", "rung": 1} for _ in range(34)]
    hist = ev.compute_rung_hist(segments)
    assert hist == {}, "全锚点期不应计入伪造的 rung=1 统计"


def test_materials_ratio():
    """素材 anime vs sp 占比计算，防范 sp=True 标记漏识别或总时长为 0 除零。"""
    zero_data = {"segments": [{"clips": [{"dur": 0.0}]}]}
    assert ev.compute_materials_ratio(zero_data) == (None, None), (
        "总时长为 0 时必须返回 (None, None)"
    )

    data = {
        "segments": [
            {"clips": [{"dur": 30.0, "sp": False}, {"dur": 10.0, "sp": True}]},
            {"clips": [{"dur": 60.0}]},
        ]
    }
    anime_r, sp_r = ev.compute_materials_ratio(data)
    assert anime_r == 0.9, "30+60=90 秒动画在 100 秒中占比应为 0.9"
    assert sp_r == 0.1, "10 秒 SP 在 100 秒中占比应为 0.1"


# ---------------------------------------------------------------------------
# 3. 字节相同≠零错配判定与 Clips Diff（ADR-0005 核心判据）
# ---------------------------------------------------------------------------


def test_clip_diff_bytes_equal_without_notes():
    """两文件字节相同且无人工注记时，必须置 null 并附带 ADR-0005 理由，防范将未审误判为 0 错配。"""
    machine = {
        "segments": [
            {
                "index": 1,
                "clips": [{"source": "s1", "start": 0.0, "dur": 5.0}],
            }
        ]
    }
    approved = {
        "segments": [
            {
                "index": 1,
                "clips": [{"source": "s1", "start": 0.0, "dur": 5.0}],
            }
        ]
    }
    diff_count, mismatch_rate, reason = ev.compute_clip_diff(
        machine, approved, bytes_equal=True
    )
    assert diff_count is None, "字节相同且无注记不可给出 0 改动，必须为 None"
    assert mismatch_rate is None, "错配率不可假报为 0.0，必须为 None"
    assert "ADR-0005" in reason, "原因必须注明依据 ADR-0005 判定为人审未介入"


def test_clip_diff_bytes_equal_with_manual_notes():
    """字节相同但段落中带有 _manual_fix 注记时，必须识别为已介入并计算错配。"""
    machine = {
        "segments": [
            {
                "index": 1,
                "clips": [{"source": "s1", "start": 0.0, "dur": 5.0}],
            }
        ]
    }
    approved = {
        "segments": [
            {
                "index": 1,
                "_manual_fix": "人工确认此处时间码微调后写回",
                "clips": [{"source": "s1", "start": 0.0, "dur": 5.0}],
            }
        ]
    }
    diff_count, mismatch_rate, reason = ev.compute_clip_diff(
        machine, approved, bytes_equal=True
    )
    assert diff_count == 1, "带 _manual_fix 注记段必须被认出为 annotated_bad 改动"
    assert mismatch_rate == 1.0, "1 段中有 1 段注记改动，错配率应为 1.0"
    assert reason is None, "有明确注记时不应置为不可用"


def test_clip_diff_different_content():
    """机器与人审片段镜头不同时，正确统计改动段数与错配率。"""
    machine = {
        "segments": [
            {
                "index": 1,
                "clips": [{"source": "ep1", "start": 10.0, "dur": 4.0}],
            },
            {
                "index": 2,
                "clips": [{"source": "ep1", "start": 20.0, "dur": 3.0}],
            },
        ]
    }
    approved = {
        "segments": [
            {
                "index": 1,
                "clips": [{"source": "ep1", "start": 10.0, "dur": 4.0}],
            },
            {
                "index": 2,
                "clips": [{"source": "ep2", "start": 99.0, "dur": 3.0}],
            },
        ]
    }
    diff_count, mismatch_rate, reason = ev.compute_clip_diff(
        machine, approved, bytes_equal=False
    )
    assert diff_count == 1, "第 2 段更换了 source，diff_count 必须为 1"
    assert mismatch_rate == 0.5, "2 段中改动 1 段，错配率必须为 0.5"
    assert reason is None


# ---------------------------------------------------------------------------
# 4. 缺文件 null 路径与 Schema 完整性（S4/S9 绝不填 0 装死）
# ---------------------------------------------------------------------------


def test_extract_snapshot_all_missing():
    """所有前置产物均缺失时，各字段组置 null 并写明原因，绝不假填 0 装死。"""
    snap = ev.extract_snapshot(
        episode="ep_test",
        tag="test-tag",
        frozen_at="2026-09-10",
        manifest_data=None,
        clips_data=None,
        approved_data=None,
        qc_log_text=None,
    )
    assert snap["episode"] == "ep_test"
    assert snap["tag"] == "test-tag"
    assert snap["tts"]["cer_median"] is None, "缺 manifest 时 CER 中位数必须为 None"
    assert snap["clips"]["segments"] is None, "缺 clips 时段数必须为 None"
    assert snap["qc"]["pass"] is None, "缺 qc.log 时 pass 必须为 None，不可判 False 也不可判 True"
    assert snap["materials"]["anime"] is None, "缺 clips 时素材占比必须为 None"
    assert "tts" in snap["unavailable_reasons"], "缺失原因必须登记在 unavailable_reasons"
    assert "clips" in snap["unavailable_reasons"]
    assert "qc" in snap["unavailable_reasons"]


def test_extract_snapshot_normal_complete():
    """正常全量数据抽取，验证字段结构与 Schema 完全匹配。"""
    manifest = {
        "segments": [
            {"index": 1, "label": "1", "cer": 0.05, "attempts": 1},
            {"index": 2, "label": "2", "cer": 0.15, "attempts": 2, "qc_skip": "asr-blind"},
        ]
    }
    clips = {
        "segments": [
            {"index": 1, "channel": "line", "status": "ok", "rung": 1, "clips": [{"dur": 10.0, "sp": False}]},
            {"index": 2, "channel": "scene", "status": "ok_extended", "rung": 2, "clips": [{"dur": 5.0, "sp": True}]},
        ]
    }
    qc_log = "PASS  成片时长 2–4 分钟        180s\nPASS  音画时长对齐              差 10ms\n"

    snap = ev.extract_snapshot(
        episode="ep_ok",
        tag="test-tag",
        frozen_at="2026-09-10",
        manifest_data=manifest,
        clips_data=clips,
        approved_data=clips,
        qc_log_text=qc_log,
        clips_bytes_equal=True,
    )
    # TTS
    assert snap["tts"]["cer_median"] == 0.1, "CER [0.05, 0.15] 中位数应为 0.1"
    assert snap["tts"]["qc_skip_segments"] == [2], "标有 qc_skip 的段号 2 必须被收录"
    assert snap["tts"]["dur_ratio_out_of_band"] is None, "dur_ratio 必须为 None 避免失真"
    # Clips: 字节相同且无注记
    assert snap["clips"]["mismatch_rate"] is None, "字节相同且无注记，错配率必须置 null"
    assert snap["clips"]["status"] == {"ok": 1, "ok_extended": 1}
    assert snap["clips"]["rung_hist"] == {"1": 1, "2": 1}
    # QC
    assert snap["qc"]["pass"] is True, "两项全 PASS 时 pass 必须为 True"
    assert snap["qc"]["violations"] == []
    # Materials
    assert snap["materials"]["anime"] == 0.6667, "10/15 秒约为 0.6667"
    assert snap["materials"]["sp"] == 0.3333, "5/15 秒约为 0.3333"
    assert snap["materials"]["live"] is None, "细分素材无法推导，必须为 None"


def test_qc_log_parsing_with_violations():
    """质检日志存在 FAIL 与 SKIP 时正确记录违规项，pass 判 False。"""
    qc_log = (
        "PASS  成片时长 2–4 分钟        180s\n"
        "FAIL  字幕不超宽               超宽 35.0 > 31.11\n"
        "SKIP  字幕每卡一行              跳过：缺 04-clips.approved.json\n"
    )
    passed, violations = ev.parse_qc_log(qc_log)
    assert passed is False, "存在 FAIL 或 SKIP 时门禁必须判定为未通过"
    assert len(violations) == 2, "FAIL 与 SKIP 均属于违规项"
    assert any("FAIL  字幕不超宽" in v for v in violations)
    assert any("SKIP  字幕每卡一行" in v for v in violations)


# ---------------------------------------------------------------------------
# 5. Diff 比对判定（有门限阻断 ✗ 回退 vs 无门限观测 ⚠ 变差）
# ---------------------------------------------------------------------------


def test_judge_metric_diff_with_threshold_blocking_regression():
    """带明确门限的指标（如 CER 中位数）：A 侧及格且 B 侧变差判定为阻断级的 ✗ 回退。"""
    key = "tts.cer_median"  # 门限 0.20
    # A=0.05 (及格), B=0.08 (变差)
    delta, verdict = ev.judge_metric_diff(key, 0.05, 0.08)
    assert delta == "+0.03"
    assert verdict == "✗ 回退", "有门限指标及格后变差必须标为 ✗ 回退"

    # A=0.25 (已不及格), B=0.30 (继续变差)
    delta, verdict = ev.judge_metric_diff(key, 0.25, 0.30)
    assert verdict == "✗ 持续不及格"

    # 改善
    delta, verdict = ev.judge_metric_diff(key, 0.08, 0.05)
    assert verdict == "✓ 提升"


def test_judge_metric_diff_without_threshold_warning_only():
    """未标定门限的指标（如 mismatch_rate / cer_p90 / attempts_mean）：变差只标为 ⚠ 变差，不阻断。"""
    # mismatch_rate 无门限
    delta, verdict = ev.judge_metric_diff("clips.mismatch_rate", 0.20, 0.35)
    assert verdict == "⚠ 变差", "无实测门限指标变差必须标为观测级 ⚠ 变差，不可妄称 ✗ 回退"

    # cer_p90 无门限
    delta, verdict = ev.judge_metric_diff("tts.cer_p90", 0.15, 0.30)
    assert verdict == "⚠ 变差", "cer_p90 无硬门禁，变差应为 ⚠ 变差"

    # attempts_mean 无门限
    delta, verdict = ev.judge_metric_diff("tts.attempts_mean", 1.2, 2.0)
    assert verdict == "⚠ 变差", "attempts_mean 无硬门禁，变差应为 ⚠ 变差"


def test_diff_report_excludes_warning_from_regression_count():
    """diff 汇总报告中，⚠ 变差不得计入阻断性回退计数（total_regressions）。"""
    snap_a = {
        "episode": "ep1", "tag": "v1", "frozen_at": "2026-09-10",
        "tts": {"cer_median": 0.05, "cer_p90": 0.15, "attempts_mean": 1.2, "qc_skip_segments": []},
        "clips": {"segments": 30, "status": {"ok": 30}, "ep_fell_back": 0, "mismatch_rate": 0.20},
        "qc": {"pass": True, "violations": []},
    }
    # snap_b: cer_p90 与 mismatch_rate 变差（无门限），但 cer_median / qc 全正常（有门限无回退）
    snap_b = {
        "episode": "ep1", "tag": "v2", "frozen_at": "2026-09-10",
        "tts": {"cer_median": 0.05, "cer_p90": 0.30, "attempts_mean": 1.8, "qc_skip_segments": []},
        "clips": {"segments": 30, "status": {"ok": 30}, "ep_fell_back": 0, "mismatch_rate": 0.35},
        "qc": {"pass": True, "violations": []},
    }
    report = ev.build_diff_report([snap_a], [snap_b], "v1", "v2")
    assert "回退 0 项" in report, "无门限变差不可计入回退项"
    assert "另有观测级变差" in report, "必须显式标明观测级变差项数"
    assert "✓  无回退项。" in report, "无阻断回退时结论行应为全绿通过"


def test_judge_metric_diff_incomparable():
    """任一侧为 None 时，必须判定为 — 不可比，不裁决。"""
    delta, verdict = ev.judge_metric_diff("tts.cer_median", None, 0.05)
    assert verdict == "— 不可比"

    delta, verdict = ev.judge_metric_diff("tts.cer_median", 0.05, None)
    assert verdict == "— 不可比"


def test_self_diff_report():
    """自比（tagA 对自身）：所有有效指标必须为持平，无回退项。"""
    snap = {
        "episode": "ep_self",
        "tag": "v1",
        "frozen_at": "2026-09-10",
        "tts": {"cer_median": 0.05, "cer_p90": 0.12, "attempts_mean": 1.2, "qc_skip_segments": []},
        "clips": {"segments": 30, "status": {"ok": 30}, "ep_fell_back": 0},
        "qc": {"pass": True, "violations": []},
        "human_review": {"prosody": 2},
        "materials": {"anime": 0.8, "sp": 0.2},
    }
    report_text = ev.build_diff_report([snap], [snap], "v1", "v1")
    assert "✓  无回退项。" in report_text, "自比必须判定为无回退项"
    assert "回退 0 项" in report_text


# ---------------------------------------------------------------------------
# 6. Annotate 边界校验与过期 reason 清理（B2 返修）
# ---------------------------------------------------------------------------


def test_annotate_full_backfill_clears_reason():
    """五个维度全齐回填后，unavailable_reasons['human_review'] 必须被 pop 掉，消除状态自相矛盾。"""
    snap = {
        "human_review": {"voice_stability": None, "prosody": None, "misread": None, "imagery_fit": None, "rhythm": None},
        "unavailable_reasons": {"human_review": "待人工回填"},
    }
    all_five = {
        "voice_stability": 4,
        "prosody": 2,
        "misread": 4,
        "imagery_fit": 3,
        "rhythm": 3,
    }
    res = ev.annotate_snapshot(snap, all_five)
    assert "human_review" not in res["unavailable_reasons"], (
        "五个维度均已回填，unavailable_reasons.human_review 必须被彻底删除"
    )
    assert res["human_review"]["_source"] == "manual_backfill"


def test_annotate_partial_backfill_updates_remaining_reason():
    """部分回填时，unavailable_reasons['human_review'] 必须更新为指名剩余待填项。"""
    snap = {
        "human_review": {"voice_stability": None, "prosody": None, "misread": None, "imagery_fit": None, "rhythm": None},
        "unavailable_reasons": {"human_review": "待人工回填"},
    }
    # 仅回填 prosody
    res = ev.annotate_snapshot(snap, {"prosody": 2})
    remaining_text = res["unavailable_reasons"]["human_review"]
    assert "待人工回填：剩余" in remaining_text
    assert "imagery_fit" in remaining_text
    assert "prosody" not in remaining_text, "已回填的 prosody 不可再出现在剩余列表中"


def test_annotate_invalid_key_rejected():
    """非法的非 human_review 键名必须触发 SystemExit 拦截。"""
    snap = {"human_review": {}}
    with pytest.raises(SystemExit) as exc:
        ev.annotate_snapshot(snap, {"not_a_valid_key": 3})
    assert "只许修改 human_review.*" in str(exc.value)


def test_annotate_out_of_bounds_rejected():
    """超出 1~5 范围的值必须触发 SystemExit 拦截。"""
    snap = {"human_review": {}}
    with pytest.raises(SystemExit) as exc:
        ev.annotate_snapshot(snap, {"prosody": 0})
    assert "取值必须为 1~5 的整数" in str(exc.value)

    with pytest.raises(SystemExit) as exc:
        ev.annotate_snapshot(snap, {"prosody": 6})
    assert "取值必须为 1~5 的整数" in str(exc.value)


# ---------------------------------------------------------------------------
# 7. S1/S2: 点分通用提取与异常向 stderr 报警
# ---------------------------------------------------------------------------


def test_get_metric_value_generic_and_special():
    """测试通用点分提取与特例提取器（len 提取、status 计数）。"""
    snap = {
        "tts": {"cer_median": 0.05, "qc_skip_segments": [1, 2, 3]},
        "clips": {"status": {"ok": 28, "no_match": 2}},
        "qc": {"violations": ["FAIL 1", "FAIL 2"]},
        "unavailable_reasons": {"clips.mismatch": "人审未介入"},
    }
    # 通用点分
    v, r = ev.get_metric_value(snap, "tts.cer_median")
    assert v == 0.05 and r is None

    # 特例：len(qc_skip_segments)
    v, r = ev.get_metric_value(snap, "tts.qc_skip_count")
    assert v == 3 and r is None

    # 特例：status 计数
    v, r = ev.get_metric_value(snap, "clips.ok_count")
    assert v == 28 and r is None

    v, r = ev.get_metric_value(snap, "clips.no_match_count")
    assert v == 2 and r is None

    # 特例：len(violations)
    v, r = ev.get_metric_value(snap, "qc.violations_count")
    assert v == 2 and r is None

    # None + 别名原因匹配 (clips.mismatch_rate -> clips.mismatch)
    v, r = ev.get_metric_value(snap, "clips.mismatch_rate")
    assert v is None and r == "人审未介入"


def test_load_tag_snapshots_warns_on_corrupted_json(tmp_path: Path, monkeypatch, capsys):
    """当快照 JSON 损坏时，不许静默吞掉，必须向 stderr 打出警告。"""
    monkeypatch.setattr(ev.paths, "DATA", tmp_path)
    (tmp_path / "library").mkdir()  # 满足 paths.require_data 骨架要求
    eval_dir = tmp_path / "eval" / "test-corrupted"
    eval_dir.mkdir(parents=True)
    corrupted_file = eval_dir / "bad.json"
    corrupted_file.write_text("{invalid json", encoding="utf-8")

    good_file = eval_dir / "good.json"
    good_file.write_text(json.dumps({"episode": "good"}), encoding="utf-8")

    snaps = ev.load_tag_snapshots("test-corrupted")
    assert len(snaps) == 1 and snaps[0]["episode"] == "good"

    captured = capsys.readouterr()
    assert "WARN 快照文件损坏或无法解析" in captured.err, "必须向 stderr 报告损坏的文件名"
    assert "bad.json" in captured.err
