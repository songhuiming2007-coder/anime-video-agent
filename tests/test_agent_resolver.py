"""StateResolver 纯函数与 Advisory 探测测试（Spec §2.1, §2.2, §2.6）。
"""

import json
from pathlib import Path
import pytest

from pipeline.agent.resolver import Scene, scope_of
from pipeline.status import EpisodeStatus, _detect_advisories, inspect_episode


ALL_12_STEPS = [
    ("01 选题", "creative"),
    ("02 脚本写作", "creative"),
    ("02 脚本写作（草稿待定稿）", "creative"),
    ("02.5 人审改稿", "creative"),
    ("03 语音合成", "pipeline"),
    ("03.5 配音顺听 / 04 排片", "pipeline"),
    ("05 审时间码", "pipeline"),
    ("06 本地渲染", "pipeline"),
    ("07 自动质检", "pipeline"),
    ("07 自动质检（未通过）", "pipeline"),
    ("08 封面与标题候选", "pipeline"),
    ("09 人工发布", "pipeline"),
]


@pytest.mark.parametrize("step_name,expected_scope", ALL_12_STEPS)
def test_scope_of_all_12_steps_exhaustive(step_name: str, expected_scope: str):
    """验证 scope_of 精确覆盖 status.py 产出的全部 12 种步骤枚举 (Spec §2.1, B6)。"""
    status = EpisodeStatus(
        episode_dir="/dummy",
        episode_name="dummy",
        current_step=step_name,
        is_blocked=False,
        block_reason=None,
        completed_steps=[],
        next_action="",
        next_command=None,
        docs_ref="",
    )
    assert scope_of(status) == expected_scope


def test_advisories_empty_by_default(tmp_path: Path):
    """干净期目录下无任何 advisory。"""
    adv = _detect_advisories(tmp_path)
    assert adv == []


def test_advisories_corrections_pending_local_and_cloud(tmp_path: Path):
    """测试 corrections.json 未应用条目检测与本地/云端指引区分 (Spec §2.2)。"""
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir(parents=True)
    corr_file = audio_dir / "corrections.json"

    # 1. 有 1 条未 applied
    corr_file.write_text(json.dumps([
        {"id": 1, "applied": False, "word": "测试", "affected": ["1"], "done_segments": []}
    ]), encoding="utf-8")

    # 无 manifest -> 降级提示
    adv = _detect_advisories(tmp_path)
    assert len(adv) == 1
    assert "1 条纠错待应用/待收尾" in adv[0]
    assert "先看配音 manifest 的 engine 字段" in adv[0]

    # 本地 manifest
    (audio_dir / "manifest.json").write_text(json.dumps({"engine": "qwen3_tts"}), encoding="utf-8")
    adv = _detect_advisories(tmp_path)
    assert "本地配音" in adv[0]

    # 云端 manifest (qwen3_tts_cuda)
    (audio_dir / "manifest.json").write_text(json.dumps({"engine": "qwen3_tts_cuda"}), encoding="utf-8")
    adv = _detect_advisories(tmp_path)
    assert "这期是云端配音，apply 走 `cloud run`" in adv[0]


def test_advisories_corrections_corrupted_json_resilient(tmp_path: Path):
    """坏 corrections.json 必须免疫，不崩溃只报警 (Spec §2.2 Y7)。"""
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "corrections.json").write_text("{corrupted json", encoding="utf-8")

    adv = _detect_advisories(tmp_path)
    assert len(adv) == 1
    assert "corrections.json 不可读" in adv[0]


def test_advisories_patch_assets_unregistered(tmp_path: Path):
    """patch_assets 有未在 04-patch/pool.json 登记的文件时报警 (Spec §2.2)。"""
    patch_dir = tmp_path / "patch_assets"
    patch_dir.mkdir(parents=True)
    (patch_dir / "clip1.mp4").write_text("dummy", encoding="utf-8")
    (patch_dir / "clip2.mp4").write_text("dummy", encoding="utf-8")

    # pool.json 不存在 -> 2 个未入库
    adv = _detect_advisories(tmp_path)
    assert any("补料挂起：2 个文件待入库" in a for a in adv)

    # 登记 clip1.mp4
    patch_out = tmp_path / "04-patch"
    patch_out.mkdir(parents=True)
    (patch_out / "pool.json").write_text(json.dumps({
        "assets": [{"path": "patch_assets/clip1.mp4"}]
    }), encoding="utf-8")

    adv = _detect_advisories(tmp_path)
    assert any("补料挂起：1 个文件待入库" in a for a in adv)


def test_advisories_clips_approved_diff_expired(tmp_path: Path):
    """04-clips.json 与 04-clips.approved.json 内容不一致时报过期 (Spec §2.2, R6)。"""
    (tmp_path / "04-clips.json").write_text(json.dumps({"segments": [{"id": 1, "clip": "A"}]}), encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text(json.dumps({"segments": [{"id": 1, "clip": "B"}]}), encoding="utf-8")

    adv = _detect_advisories(tmp_path)
    assert any("approved 已过期，必须重走 05" in a for a in adv)

    # 内容一致时消除
    (tmp_path / "04-clips.approved.json").write_text(json.dumps({"segments": [{"id": 1, "clip": "A"}]}), encoding="utf-8")
    adv = _detect_advisories(tmp_path)
    assert not any("approved 已过期" in a for a in adv)


def test_advisories_human_time_over_budget(tmp_path: Path):
    """human_time.json 超出 k × 片长 时触发报警 (Spec §2.6, Y1-r19, v1.20)。"""
    # 模拟片长 120 秒 (2 分钟)，预算 1.5 * 2 = 3 分钟
    (tmp_path / "04-clips.json").write_text(json.dumps({"total_duration": 120.0}), encoding="utf-8")

    # 人类耗时 5 分钟 (> 3 分钟)
    (tmp_path / "human_time.json").write_text(json.dumps([
        {"stop": "03.5", "minutes": 5.0}
    ]), encoding="utf-8")

    adv = _detect_advisories(tmp_path)
    assert any("人类耗时超预算" in a for a in adv)

    # 坏 human_time.json 免疫
    (tmp_path / "human_time.json").write_text("{bad json", encoding="utf-8")
    adv = _detect_advisories(tmp_path)
    assert any("human_time.json 不可读" in a for a in adv)
