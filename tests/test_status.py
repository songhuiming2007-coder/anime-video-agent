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
