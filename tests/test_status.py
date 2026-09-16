from pathlib import Path
from pipeline.status import inspect_episode, format_status


def test_status_empty_dir(tmp_path: Path):
    status = inspect_episode(tmp_path)
    assert status.current_step == "01 选题"
    assert status.is_blocked is True
    assert "01-topic.md" in status.block_reason


def test_status_topic_only(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "02 脚本写作"
    assert status.is_blocked is False
    assert "check_script" in status.next_command


def test_status_script_unreviewed(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "02.5 人审改稿"
    assert status.is_blocked is True
    assert "02.5" in status.block_reason


def test_status_script_reviewed(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    (tmp_path / "02-diff.patch").write_text("--- a\n+++ b\n", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "03 语音合成"
    assert status.is_blocked is False
    assert "pipeline.tts" in status.next_command


def test_status_audio_ready(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert "04 排片" in status.current_step
    assert "pipeline.clips" in status.next_command


def test_status_clips_unapproved(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "05 审时间码"
    assert status.is_blocked is True
    assert "--approve" in status.next_command


def test_status_clips_approved(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text("[]", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "06 本地渲染"
    assert status.is_blocked is False
    assert "pipeline.render" in status.next_command


def test_status_rendered(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text("[]", encoding="utf-8")
    (tmp_path / "05-final.mp4").write_text("video", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "07 自动质检"
    assert "pipeline.qc" in status.next_command


def test_status_qc_passed(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text("[]", encoding="utf-8")
    (tmp_path / "05-final.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "06-check.log").write_text("PASS", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "08 封面与标题候选"
    assert "pipeline.cover" in status.next_command


def test_status_publish_ready(tmp_path: Path):
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text("[]", encoding="utf-8")
    (tmp_path / "05-final.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "06-check.log").write_text("PASS", encoding="utf-8")
    (tmp_path / "07-titles.md").write_text("# Titles", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "09 人工发布"
    assert status.is_blocked is True
    formatted = format_status(status)
    assert "【当前阶段】 09 人工发布" in formatted


def _stage_to_rendered(tmp_path: Path):
    """公共前置：推进到「渲染完成、未质检」状态。"""
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text("[]", encoding="utf-8")
    (tmp_path / "05-final.mp4").write_text("video", encoding="utf-8")


def test_status_draft_only_is_step02(tmp_path: Path):
    """有草稿无定稿 = 还在 02（审计 F7）。旧状态机会穿透到 02.5，
    让人去对一份不存在的 02-script.md 提 diff。
    变异：删掉 draft-only 分支 → 本用例断言 current_step 变红。"""
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.draft.md").write_text("# Draft", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert "02 脚本写作" in status.current_step
    assert status.is_blocked is False
    assert "draft" in status.next_action


def test_status_qc_failed_not_reported_as_passed(tmp_path: Path):
    """qc 失败也写 06-check.log——存在不等于通过（审计 F8）。
    变异：has_qc 改回只认文件存在 → 本用例红。"""
    _stage_to_rendered(tmp_path)
    (tmp_path / "06-check.log").write_text(
        "PASS  成片时长\nFAIL  无 >0.5s 纯黑\n", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert "07" in status.current_step and "未通过" in status.current_step
    assert "07 质检" not in status.completed_steps
    assert "pipeline.qc" in status.next_command


def test_status_cover_dir_without_index_not_done(tmp_path: Path):
    """cover.build() 一开始就建 07-cover/，筛空崩掉也留目录——
    认目录会误报 08 完成，认 index.html 才算（审计 F8）。
    变异：has_cover 改回认目录 → 本用例红。"""
    _stage_to_rendered(tmp_path)
    (tmp_path / "06-check.log").write_text("PASS 全部通过\n", encoding="utf-8")
    (tmp_path / "07-cover").mkdir()          # 失败的 cover 运行留下的空目录
    status = inspect_episode(tmp_path)
    assert status.current_step == "08 封面与标题候选"
    assert "08 封面标题" not in status.completed_steps


def test_status_035_is_advisory_not_blocked(tmp_path: Path):
    """03.5 是 runbook 的【建议】停机点：机器不阻塞，但建议必须在文案里显眼。
    变异：把建议从 next_action 删掉 → 本用例红。"""
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    audio_dir = tmp_path / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "manifest.json").write_text("{}", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.is_blocked is False
    assert status.block_reason is None          # 建议不是阻塞，别往 block_reason 塞 🛑
    assert "03.5" in status.next_action
