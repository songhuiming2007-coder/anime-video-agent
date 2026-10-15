import pytest
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
    # 审看页就绪（不早于排片）才给批准命令
    (tmp_path / "04-review.html").write_text("<html></html>", encoding="utf-8")
    status = inspect_episode(tmp_path)
    assert status.current_step == "05 审时间码"
    assert status.is_blocked is True
    assert "--approve" in status.next_command


def _at05(tmp_path: Path) -> None:
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    (tmp_path / "03-audio").mkdir()
    (tmp_path / "03-audio" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")


def test_status_05_审看页缺失先出页不给批准(tmp_path: Path):
    """D74（2026-10-11 伪恋期）：agent 只跑 clips 不跑 review，照 status 说「去看 04-review.html」，页面根本不存在。"""
    _at05(tmp_path)
    status = inspect_episode(tmp_path)
    assert status.current_step == "05 审时间码", "工序路由与停机点按这个串走，不能变"
    assert status.is_blocked is True
    assert status.next_command.split()[:3] == ["python", "-m", "pipeline.review"]
    assert "--approve" not in status.next_command
    assert "还没生成" in status.next_action


def test_status_05_审看页比排片旧也要重出(tmp_path: Path):
    import os

    _at05(tmp_path)
    page = tmp_path / "04-review.html"
    page.write_text("<html></html>", encoding="utf-8")
    clips = tmp_path / "04-clips.json"
    st = clips.stat()
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns - 1_000_000_000))  # 页面早于排片 1 s：排片重跑过
    status = inspect_episode(tmp_path)
    assert "--approve" not in status.next_command
    assert "比 04-clips.json 旧" in status.next_action


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


# ---------------------------------------------------------------------------
# D90：09 交付清单——没齐不许说「齐备」
# ---------------------------------------------------------------------------

FULL_TITLES = "# 标题与简介候选\n\n## 定稿标题\n\nX\n\n## 简介（长视频 / B 站版）\n\n正文\n\n## 标签\n\n伪恋\n"


def _stage_09(tmp_path: Path, titles: str = FULL_TITLES, covers=("08-cover.jpg", "08-cover-vertical.jpg")) -> Path:
    (tmp_path / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (tmp_path / "02-script.md").write_text("# Script", encoding="utf-8")
    (tmp_path / "03-audio").mkdir()
    (tmp_path / "03-audio" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "04-clips.json").write_text("[]", encoding="utf-8")
    (tmp_path / "04-clips.approved.json").write_text("[]", encoding="utf-8")
    (tmp_path / "05-final.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "06-check.log").write_text("PASS", encoding="utf-8")
    (tmp_path / "07-titles.md").write_text(titles, encoding="utf-8")
    for name in covers:
        (tmp_path / name).write_bytes(b"jpg")
    return tmp_path


def test_publish_gaps_none_when_complete(tmp_path: Path):
    from pipeline.status import publish_gaps
    d = _stage_09(tmp_path)
    assert publish_gaps(d) == []
    status = inspect_episode(d)
    assert status.current_step == "09 人工发布" and status.is_blocked
    assert "交付物已齐备" in status.block_reason
    assert "07-cover/" in status.next_action, "提醒 09 卡的封面路径在 07-cover/ 下"


@pytest.mark.parametrize("missing, expect", [
    ("08-cover.jpg", "横版定稿封面"),
    ("08-cover-vertical.jpg", "竖版定稿封面"),
])
def test_publish_gaps_missing_cover(tmp_path: Path, missing: str, expect: str):
    from pipeline.status import publish_gaps
    covers = [c for c in ("08-cover.jpg", "08-cover-vertical.jpg") if c != missing]
    d = _stage_09(tmp_path, covers=covers)
    gaps = publish_gaps(d)
    assert len(gaps) == 1 and expect in gaps[0]
    status = inspect_episode(d)
    assert "齐备" not in status.block_reason and "还缺 1 项" in status.block_reason
    assert expect in status.next_action


def test_publish_gaps_accepts_jpeg_and_png(tmp_path: Path):
    from pipeline.status import publish_gaps
    d = _stage_09(tmp_path, covers=("08-cover.jpeg", "08-cover-vertical.png"))
    assert publish_gaps(d) == []


@pytest.mark.parametrize("titles, expect", [
    ("# T\n\n## 定稿标题\n\nX\n\n## 标签\n\nY\n", "简介"),
    ("# T\n\n## 定稿标题\n\nX\n\n## 简介\n\n正文里提到标签两个字\n", "标签"),
    ("# T\n\n正文里写了简介和标签，但没有对应的节\n", "简介"),
])
def test_publish_gaps_titles_sections(tmp_path: Path, titles: str, expect: str):
    from pipeline.status import publish_gaps
    gaps = publish_gaps(_stage_09(tmp_path, titles=titles))
    assert any(expect in g for g in gaps), gaps


@pytest.mark.parametrize("titles", [
    "## 简介\n\nA\n\n## 标签\n\nB\n",                       # 7 月写法
    "## 视频简介\n\nA\n\n## 平台标签 / Tags\n\nB\n",        # 8 月中下旬写法
    "## 简介（长视频 / B 站版）\n\nA\n\n## 标签\n\nB\n",   # 10 月写法
    "## 简介\n\nA\n\n## Tags\n\nB\n",                     # 只写英文 Tags
])
def test_publish_gaps_accepts_past_heading_styles(tmp_path: Path, titles: str):
    from pipeline.status import publish_gaps
    assert publish_gaps(_stage_09(tmp_path, titles=titles)) == []


def test_给AVA的提示不喊口号() -> None:
    """D101 / 1.7（2026-10-15）：status 的提示会被模型当说话样板，规则照旧、语气中性。
    停机点仍是停机点、--force 仍拒收，只是不再写「🛑…！」「严禁…！」。"""
    import ast

    from pipeline import paths

    src = (paths.ROOT / "pipeline" / "status.py").read_text(encoding="utf-8")
    texts = [n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    loud = [t for t in texts if any(m in t for m in ("🛑", "严禁", "！"))]
    assert loud == [], loud
