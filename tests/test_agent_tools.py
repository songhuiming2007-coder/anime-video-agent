"""受控工具注册表与 Pipeline 白名单执行器护栏测试（Spec §2.4, §2.5, §5 PR1）。
"""

from pathlib import Path
import subprocess
import sys
import pytest

from pipeline.agent.tools import (
    assert_egress_boundary,
    sys_python,
    validate_pipeline_command,
    write_episode_file,
)
from pipeline.cloud import validate_extra_args


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch):
    """构建受控仓库与合法期目录结构。"""
    from pipeline import paths
    ep_root = tmp_path / "data" / "episodes"
    ep_dir = ep_root / "01-test"
    ep_dir.mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return tmp_path, ep_dir


# ---------------------------------------------------------------------------
# 1. write_episode_file 受限书写工具与 Code Freeze 拦截测试
# ---------------------------------------------------------------------------


def test_write_episode_file_allows_draft_and_topic_with_confirm(fake_repo):
    """creative scope 放行 02-script.draft.md，以及在确认后的 01-topic.md。"""
    _, ep_dir = fake_repo
    draft = write_episode_file(ep_dir, "02-script.draft.md", "# Draft", scope="creative")
    assert draft.exists()
    assert draft.read_text(encoding="utf-8") == "# Draft"

    topic = write_episode_file(ep_dir, "01-topic.md", "# Topic", scope="creative", confirmed=True)
    assert topic.exists()
    assert topic.read_text(encoding="utf-8") == "# Topic"


def test_write_episode_file_rejects_topic_without_confirmation(fake_repo):
    """写 01-topic.md 未获显式确认时必须拦截 (Spec §2.4, B7)。"""
    _, ep_dir = fake_repo
    with pytest.raises(PermissionError, match="显式确认"):
        write_episode_file(ep_dir, "01-topic.md", "# Topic", scope="creative", confirmed=False)


def test_write_episode_file_rejects_out_of_whitelist_files(fake_repo):
    """拦截白名单外文件的写入（如 02-script.md、04-clips.json 等）。"""
    _, ep_dir = fake_repo
    with pytest.raises(PermissionError, match="白名单"):
        write_episode_file(ep_dir, "02-script.md", "# Final", scope="creative")

    with pytest.raises(PermissionError, match="白名单"):
        write_episode_file(ep_dir, "notes.txt", "abc", scope="creative")


def test_write_episode_file_rejects_parent_or_outside_directory(fake_repo):
    """拦截跨目录/父级目录写入（双端 resolve 防穿透，B1-r5）。"""
    _, ep_dir = fake_repo

    with pytest.raises(PermissionError):
        write_episode_file(ep_dir, "../02-script.draft.md", "bad", scope="creative")

    with pytest.raises(PermissionError):
        write_episode_file(ep_dir, "subdir/02-script.draft.md", "bad", scope="creative")


def test_write_episode_file_rejects_repo_root_and_system_tmp(fake_repo):
    """拦截写到仓库根或 /tmp 等任意非期目录路径 (🟡 1 纵深防御)。"""
    root, _ = fake_repo
    with pytest.raises(PermissionError, match="禁止"):
        write_episode_file(root, "02-script.draft.md", "bad", scope="creative")

    with pytest.raises(PermissionError, match="禁止"):
        write_episode_file(Path("/tmp"), "02-script.draft.md", "bad", scope="creative")


def test_write_episode_file_fail_closed_when_episodes_root_missing(tmp_path: Path, monkeypatch):
    """外置盘未挂载（data/episodes 不存在）时必须 fail-closed 拒绝写入 (🟡 新1)。"""
    from pipeline import paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    # 不创建 tmp_path / data / episodes
    arbitrary_dir = tmp_path / "somewhere" / "01"
    arbitrary_dir.mkdir(parents=True)

    with pytest.raises(PermissionError, match="不可达"):
        write_episode_file(arbitrary_dir, "02-script.draft.md", "bad", scope="creative")


def test_write_episode_file_rejects_non_creative_scope(fake_repo):
    """pipeline 与 asset scope 拥有零写权限。"""
    _, ep_dir = fake_repo
    with pytest.raises(PermissionError, match="零写权限"):
        write_episode_file(ep_dir, "02-script.draft.md", "# Draft", scope="pipeline")

    with pytest.raises(PermissionError, match="零写权限"):
        write_episode_file(ep_dir, "02-script.draft.md", "# Draft", scope="asset")


def test_write_episode_file_rejects_writing_pipeline_src(fake_repo):
    """绝不允许写入 pipeline/ 代码源码（Code Freeze 核心护栏）。"""
    from pipeline import paths
    with pytest.raises(PermissionError, match="Code Freeze"):
        write_episode_file(paths.ROOT / "pipeline", "01-topic.md", "bad", scope="creative", confirmed=True)


# ---------------------------------------------------------------------------
# 2. validate_pipeline_command 命令白名单与拒收测试
# ---------------------------------------------------------------------------


def test_validate_pipeline_command_rejects_force_and_guides():
    """/run 命令遇到 --force / --force-all / --force-all=true 必须当场拦截并指引增量参数 (Spec §2.4, B4-r18, 🟡2)。"""
    ok, msg, _ = validate_pipeline_command("tts data/episodes/01 --force")
    assert not ok
    assert "禁止在 ava 中使用 --force" in msg
    assert "--redo" in msg or "--apply-patch" in msg

    ok2, msg2, _ = validate_pipeline_command("python -m pipeline.tts data/episodes/01 --force-all")
    assert not ok2
    assert "禁止在 ava 中使用 --force-all" in msg2

    # 🟡 2 漏网变体防御
    ok3, msg3, _ = validate_pipeline_command("tts data/episodes/01 --force-all=true")
    assert not ok3
    assert "禁止在 ava 中使用 --force-all" in msg3


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
    """pipeline scope 下放行 9 个制片模块，且执行器使用 sys.executable (🔴 2)。"""
    for mod in ["check_script", "tts", "clips", "review", "render", "qc", "cover", "bgm", "status"]:
        ok, msg, norm = validate_pipeline_command(f"{mod} data/episodes/01", scope="pipeline")
        assert ok, f"模块 {mod} 应该被放行，却被拒: {msg}"
        assert norm[0] == sys.executable, f"执行器必须是 sys.executable，不能硬编码 python: {norm[0]}"
        assert norm[1:3] == ["-m", f"pipeline.{mod}"]


def test_validate_pipeline_command_injects_episode_dir(tmp_path: Path):
    """自动注入当期目录，解决文档级用法缺参数问题 (🔴 1)。"""
    ep_dir = tmp_path / "data" / "episodes" / "01"
    ep_dir.mkdir(parents=True)

    # 1. tts --redo 3 自动补位 ep_dir
    ok, msg, norm = validate_pipeline_command("tts --redo 3", scope="pipeline", ep_dir=ep_dir)
    assert ok
    assert str(ep_dir.resolve()) in norm
    assert norm == [sys.executable, "-m", "pipeline.tts", str(ep_dir.resolve()), "--redo", "3"]

    # 2. clips 自动补位 ep_dir
    ok, msg, norm = validate_pipeline_command("clips", scope="pipeline", ep_dir=ep_dir)
    assert ok
    assert norm == [sys.executable, "-m", "pipeline.clips", str(ep_dir.resolve())]

    # 3. check_script 自动补位当期脚本
    script_file = ep_dir / "02-script.md"
    script_file.write_text("# Script", encoding="utf-8")
    ok, msg, norm = validate_pipeline_command("check_script", scope="pipeline", ep_dir=ep_dir)
    assert ok
    assert norm == [sys.executable, "-m", "pipeline.check_script", str(script_file.resolve())]


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


def test_pipeline_command_wiring_real_cli_execution(tmp_path: Path):
    """接线级测试：放行的真实命令能在真实解释器下正常解析并返回 (接线防两张皮)。"""
    ep_dir = tmp_path / "data" / "episodes" / "01"
    ep_dir.mkdir(parents=True)

    # 1. 验证 tts 能真跑 --help
    ok, _, norm_cmd = validate_pipeline_command("tts --help", scope="pipeline", ep_dir=ep_dir)
    assert ok
    res = subprocess.run(norm_cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert "给一期稿件配音" in res.stdout or "run" in res.stdout

    # 2. 验证 clips 能真跑 --help
    ok, _, norm_cmd = validate_pipeline_command("clips --help", scope="pipeline", ep_dir=ep_dir)
    assert ok
    res = subprocess.run(norm_cmd, capture_output=True, text=True)
    assert res.returncode == 0


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
