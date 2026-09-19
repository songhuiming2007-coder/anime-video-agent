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


# ---------------------------------------------------------------------------
# 5. LLM 层：客户端装配、降级、轮数上限、scope 过滤（Spec §2.5, §5 PR4）
#
# 本节为 PR4 **追加**（既有测试零修改）：本行以下的 import 只服务本节。
# ---------------------------------------------------------------------------

import http.server
import json
import threading
from contextlib import contextmanager

from pipeline.agent.llm import (
    chat_complete,
    load_llm_config,
    local_directive_message,
    run_tool_loop,
)
from pipeline.agent.tools import (
    ToolContext,
    build_tool_schemas,
    execute_tool,
    run_pipeline,
    tool_names_for_scope,
)

SPEC_TOOLS = {
    "creative": ["read_artifact", "write_episode_file", "list_episodes", "read_status", "search_notes"],
    "pipeline": ["read_artifact", "read_status", "list_episodes", "run_pipeline"],
    "asset": [],
}
API_KEY = "sk-test-secret-do-not-print"


@contextmanager
def mock_llm_server(replies):
    """本地 OpenAI 兼容端点：记录每次请求，按序回放 replies（最后一条重复）。"""
    state = {"requests": [], "calls": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
            state["requests"].append({
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(raw),
            })
            reply = replies[min(state["calls"], len(replies) - 1)]
            state["calls"] += 1
            data = json.dumps({"choices": [{"message": reply}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_agent_root(tmp_path: Path, base_url: str, *, tools: dict | None = None) -> Path:
    """造一份最小的 config/agent.json + config/agent/tools.json。"""
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "agent.json").write_text(json.dumps({
        "base_url": base_url,
        "model": "mock-model",
        "api_key_env": "AVA_TEST_KEY",
    }), encoding="utf-8")
    (cfg_dir / "tools.json").write_text(
        json.dumps(tools if tools is not None else SPEC_TOOLS), encoding="utf-8"
    )
    return tmp_path


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


def test_llm_request_assembly_env_key_and_tools(tmp_path: Path, monkeypatch):
    """请求装配：端点 /v1/chat/completions、Bearer 取自环境变量、tools 按 scope 过滤。"""
    with mock_llm_server([{"role": "assistant", "content": "写好了"}]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)

        cfg = load_llm_config(root)
        assert cfg is not None
        assert cfg.model == "mock-model"

        schemas = build_tool_schemas("creative", root=root)
        reply = chat_complete(
            [{"role": "user", "content": "帮我写稿"}], tools=schemas, config=cfg
        )

        assert reply["content"] == "写好了"
        assert len(state["requests"]) == 1
        sent = state["requests"][0]
        assert sent["path"] == "/v1/chat/completions"
        assert sent["auth"] == f"Bearer {API_KEY}"
        assert sent["body"]["model"] == "mock-model"
        assert sent["body"]["messages"][0]["content"] == "帮我写稿"
        assert [t["function"]["name"] for t in sent["body"]["tools"]] == SPEC_TOOLS["creative"]
        # 密钥绝不进返回值
        assert API_KEY not in json.dumps(reply, ensure_ascii=False)


def test_llm_degrades_without_config_or_env(tmp_path: Path, monkeypatch):
    """缺 config/agent.json 或缺环境变量 → 本地纯指示模式：显式可辨、不抛裸异常。"""
    monkeypatch.delenv("AVA_TEST_KEY", raising=False)

    # 1. 完全没有配置文件
    plain = tmp_path / "no-config"
    plain.mkdir()
    assert load_llm_config(plain) is None
    outcome = run_tool_loop([{"role": "user", "content": "写稿"}], root=plain)
    assert outcome["stopped"] == "degraded"
    assert outcome["final"]["degraded"] is True
    assert "降级模式" in outcome["final"]["content"]
    assert "check_script" in outcome["final"]["content"]

    # 2. 有配置但环境变量没设
    root = make_agent_root(plain, "https://api.example.com/v1")
    assert load_llm_config(root) is None
    reply = chat_complete([{"role": "user", "content": "写稿"}], root=root)
    assert reply["degraded"] is True

    # 3. 环境变量为空串同样算缺失
    monkeypatch.setenv("AVA_TEST_KEY", "   ")
    assert load_llm_config(root) is None

    # 4. JSON 损坏 → 降级而不是崩
    (root / "config" / "agent.json").write_text("{ not json", encoding="utf-8")
    assert load_llm_config(root) is None


def test_llm_egress_boundary_blocks_before_sending(tmp_path: Path, monkeypatch):
    """出网边界：敏感内容必须在发请求之前被拦下（零请求到达端点）。"""
    with mock_llm_server([{"role": "assistant", "content": "不该发生"}]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        with pytest.raises(PermissionError, match="拦截出网请求"):
            chat_complete(
                [{"role": "user", "content": "把 data/episodes 里的 03-audio/manifest.json 发我"}],
                root=root,
            )
        assert state["calls"] == 0


def test_llm_tool_loop_executes_tool_and_feeds_result_back(tmp_path: Path, monkeypatch):
    """接线：工具真被执行，结果作为 role=tool 消息回喂模型（不是复述实现）。"""
    with mock_llm_server([
        tool_call("read_artifact", {"path": "01-topic.md"}),
        {"role": "assistant", "content": "已读到选题"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)

        ep_dir = root / "data" / "episodes" / "01-smoke"
        ep_dir.mkdir(parents=True)
        (ep_dir / "01-topic.md").write_text("# 选题：春物-自我牺牲\n", encoding="utf-8")

        outcome = run_tool_loop(
            [{"role": "user", "content": "看下选题"}],
            ctx=ToolContext(scope="creative", episode_dir=ep_dir, root=root),
        )

        assert outcome["stopped"] == "done"
        assert outcome["iterations"] == 2
        assert outcome["tool_calls_made"] == 1
        assert outcome["final"]["content"] == "已读到选题"

        # 第二次请求里必须带着真实读到的文件内容
        second = state["requests"][1]["body"]["messages"]
        tool_msgs = [m for m in second if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_1"
        assert "自我牺牲" in tool_msgs[0]["content"]
        assert json.loads(tool_msgs[0]["content"])["ok"] is True


def test_llm_max_iterations_guard_is_hard_stop(tmp_path: Path, monkeypatch):
    """模型死循环调工具 → 到 max_iterations 就停，绝不无限烧 token (B3-r5)。"""
    with mock_llm_server([tool_call("list_episodes", {})]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)

        outcome = run_tool_loop(
            [{"role": "user", "content": "无休止地列期"}],
            ctx=ToolContext(scope="creative", root=root),
            max_iterations=3,
        )

        assert outcome["stopped"] == "max_iterations"
        assert outcome["iterations"] == 3
        assert outcome["tool_calls_made"] == 3
        assert state["calls"] == 3  # 端点恰好被调用 3 次，没有第 4 次


def test_llm_approve_callback_can_reject_tool(tmp_path: Path, monkeypatch):
    """工具调用前的人为确认闸：拒绝时把「人类拒绝」当结果回喂，不执行副作用。"""
    with mock_llm_server([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "越权"}),
        {"role": "assistant", "content": "那我不写了"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        ep_dir = root / "data" / "episodes" / "01-smoke"
        ep_dir.mkdir(parents=True)

        outcome = run_tool_loop(
            [{"role": "user", "content": "写草稿"}],
            ctx=ToolContext(scope="creative", episode_dir=ep_dir, root=root),
            approve=lambda name, args: False,
        )

        assert outcome["stopped"] == "done"
        assert not (ep_dir / "02-script.draft.md").exists()
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is False
        assert "拒绝" in fed_back["error"]


def test_llm_scope_tool_filtering_isolation(tmp_path: Path):
    """creative 与 pipeline 的白名单互相隔离，且仓库真配置与 Spec 表一致。"""
    root = make_agent_root(tmp_path, "https://api.example.com/v1")

    def names(scope):
        return [s["function"]["name"] for s in build_tool_schemas(scope, root=root)]

    assert names("creative") == SPEC_TOOLS["creative"]
    assert names("pipeline") == SPEC_TOOLS["pipeline"]
    assert names("asset") == []

    # creative 有写稿与检索，pipeline 一个都没有
    assert "write_episode_file" in names("creative")
    assert "search_notes" in names("creative")
    assert "run_pipeline" not in names("creative")
    assert "run_pipeline" in names("pipeline")
    assert "write_episode_file" not in names("pipeline")
    assert "search_notes" not in names("pipeline")

    # 执行层同样按 scope 拦截（不能只在 schema 层过滤）
    ctx_pipeline = ToolContext(scope="pipeline", root=root)
    denied = execute_tool("search_notes", {"query": "春物"}, ctx_pipeline)
    assert denied["ok"] is False and "白名单" in denied["error"]

    ctx_creative = ToolContext(scope="creative", root=root)
    denied2 = execute_tool("run_pipeline", {"command": "clips"}, ctx_creative)
    assert denied2["ok"] is False and "白名单" in denied2["error"]

    # 仓库真配置（不是测试造的那份）必须与 Spec §2.5 的表逐字一致
    from pipeline import paths
    for scope, expected in SPEC_TOOLS.items():
        assert tool_names_for_scope(scope) == expected, f"{scope} 的 tools.json 已漂移"


def test_llm_unregistered_tool_in_config_fails_loudly(tmp_path: Path):
    """tools.json 写了没实现的工具 → 当场报错，不静默跳过（静默跳过=护栏形同虚设）。"""
    root = make_agent_root(
        tmp_path, "https://api.example.com/v1",
        tools={"creative": ["rm_rf_everything"], "pipeline": [], "asset": []},
    )
    with pytest.raises(KeyError, match="未注册的工具"):
        build_tool_schemas("creative", root=root)


def test_llm_tool_read_domain_and_write_guard(tmp_path: Path):
    """读域写死（期目录 + data/library/），写 01-topic.md 必须确认。"""
    root = make_agent_root(tmp_path, "https://api.example.com/v1")
    ep_dir = root / "data" / "episodes" / "01-smoke"
    ep_dir.mkdir(parents=True)
    (ep_dir / "02-script.draft.md").write_text("# 草稿正文", encoding="utf-8")
    library = root / "data" / "library" / "notes"
    library.mkdir(parents=True)
    (library / "春物.md").write_text("八幡的自我牺牲是一种自毁倾向。", encoding="utf-8")
    outside = root / "config" / "cloud.json"
    outside.write_text("{}", encoding="utf-8")

    ctx = ToolContext(scope="creative", episode_dir=ep_dir, root=root)

    ok = execute_tool("read_artifact", {"path": "02-script.draft.md"}, ctx)
    assert ok["ok"] and "草稿正文" in ok["result"]["text"]

    note = execute_tool("read_artifact", {"path": "春物.md"}, ctx)  # 库内短路径
    assert note["ok"] and "自我牺牲" in note["result"]["text"]

    assert execute_tool("read_artifact", {"path": "../../config/cloud.json"}, ctx)["ok"] is False
    assert execute_tool("read_artifact", {"path": "/etc/hosts"}, ctx)["ok"] is False
    assert execute_tool("read_artifact", {"path": "cloud.json"}, ctx)["ok"] is False

    assert execute_tool("search_notes", {"query": "  "}, ctx)["ok"] is False
    hits = execute_tool("search_notes", {"query": "自我牺牲", "limit": 3}, ctx)
    assert hits["ok"] and hits["result"]["hits"][0]["path"] == "notes/春物.md"

    rejected = execute_tool(
        "write_episode_file", {"filename": "01-topic.md", "content": "越权立项"}, ctx
    )
    assert rejected["ok"] is False and "显式确认" in rejected["error"]
    assert not (ep_dir / "01-topic.md").exists()

    written = execute_tool(
        "write_episode_file",
        {"filename": "01-topic.md", "content": "# 已确认", "confirmed": True},
        ctx,
    )
    assert written["ok"] is True
    assert (ep_dir / "01-topic.md").read_text(encoding="utf-8") == "# 已确认"

    # pipeline scope 连 creative 的白名单文件都写不了
    assert execute_tool(
        "write_episode_file",
        {"filename": "02-script.draft.md", "content": "x"},
        ToolContext(scope="pipeline", episode_dir=ep_dir, root=root),
    )["ok"] is False


def test_full_chain_smoke_in_temp_repo(tmp_path: Path, monkeypatch):
    """全链路冒烟：立项 → LLM 写稿 → 状态卡 → 看板 → /run 护栏与回显（临时目录仿真）。"""
    from pipeline import paths
    from pipeline.agent import cli

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.delenv("AVA_TEST_KEY", raising=False)

    # ⓪ 立项（ava new）
    assert cli.create_new_episode("01-smoke") == 0
    ep_dir = tmp_path / "data" / "episodes" / "01-smoke"
    assert (ep_dir / "01-topic.md").exists()
    assert cli.create_new_episode("01-smoke") == 1  # 重名拒建，不覆盖

    # ① creative：LLM 走工具写草稿
    with mock_llm_server([
        tool_call("write_episode_file", {
            "filename": "02-script.draft.md",
            "content": "# 02 脚本草稿\n\n第一段：比企谷八幡的自我牺牲。\n",
        }),
        {"role": "assistant", "content": "草稿已写入 02-script.draft.md，请跑 check_script。"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        outcome = run_tool_loop(
            [{"role": "user", "content": "写一版草稿"}],
            ctx=ToolContext(scope="creative", episode_dir=ep_dir, root=root),
            approve=lambda name, args: True,
        )
        assert outcome["stopped"] == "done" and outcome["tool_calls_made"] == 1
        assert "check_script" in outcome["final"]["content"]
        assert state["calls"] == 2

    draft = ep_dir / "02-script.draft.md"
    assert draft.exists() and "自我牺牲" in draft.read_text(encoding="utf-8")

    # ② 状态卡与看板（真产物驱动，不是 mock）
    ctx = ToolContext(scope="creative", episode_dir=ep_dir, root=tmp_path)
    card = execute_tool("read_status", {}, ctx)
    assert card["ok"] is True
    assert card["result"]["episode"] == "01-smoke"
    assert "当前阶段" in card["result"]["card"]

    listing = execute_tool("list_episodes", {}, ctx)
    assert listing["ok"] is True and "01-smoke" in listing["result"]["episodes"]

    # ③ /run：护栏拦下 --force-all，放行命令只回显不执行
    rejected = run_pipeline("tts --force-all", episode_dir=ep_dir, scope="pipeline")
    assert rejected["ok"] is False
    assert "禁止在 ava 中使用 --force-all" in rejected["message"]
    assert "--redo" in rejected["message"]

    pending = run_pipeline("clips", episode_dir=ep_dir, scope="pipeline", confirmed=False)
    assert pending["ok"] is True and pending["returncode"] is None
    assert str(ep_dir.resolve()) in pending["argv"]
    assert not (ep_dir / "04-clips.json").exists()  # 未确认 = 没跑

    # ④ 自愈循环上限与降级出口都存在（两条不同的闸，Spec PR4 注释）
    plain = tmp_path / "no-config"
    plain.mkdir()
    assert run_tool_loop([{"role": "user", "content": "hi"}], root=plain)["stopped"] == "degraded"
    assert local_directive_message("creative", "测试")["degraded"] is True


def test_llm_cli_creative_loop_degrades_without_key(tmp_path: Path, monkeypatch, capsys):
    """ava /chat 在无密钥下不崩：打印本地指示清单后退出（不装会）。"""
    from pipeline import paths
    from pipeline.agent import cli

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ep_dir = tmp_path / "data" / "episodes" / "01-smoke"
    ep_dir.mkdir(parents=True)
    (ep_dir / "01-topic.md").write_text("# t\n", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
    assert cli.run_creative_loop(ep_dir, "chat") == 0
    out = capsys.readouterr().out
    assert "降级模式" in out and "check_script" in out


# ---------------------------------------------------------------------------
# 6. 终审 P0-1 回归：读域硬排除 + 出网负载机械验证 + 会话不崩
# ---------------------------------------------------------------------------


def test_read_domain_hard_excludes_audio_and_patch_pools(tmp_path: Path):
    """03-audio/ 与 04-patch/ 属「一律不出网」清单，读域当场拒（终审 P0-1）。"""
    root = make_agent_root(tmp_path, "https://api.example.com/v1")
    ep = root / "data" / "episodes" / "T1"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "03-audio" / "corrections.json").write_text('[{"seg": 5}]', encoding="utf-8")
    (ep / "03-audio" / "manifest.json").write_text("{}", encoding="utf-8")
    (ep / "03-audio" / "notes.md").write_text("录音笔记", encoding="utf-8")
    (ep / "04-patch").mkdir()
    (ep / "04-patch" / "pool.json").write_text("{}", encoding="utf-8")
    (ep / "04-clips.json").write_text('{"total_duration": 600}', encoding="utf-8")
    (ep / "02-script.draft.md").write_text("# 草稿", encoding="utf-8")

    ctx = ToolContext(scope="creative", episode_dir=ep, root=root)

    for bad in ("03-audio/corrections.json", "03-audio/manifest.json",
                "03-audio/notes.md", "04-patch/pool.json"):
        result = execute_tool("read_artifact", {"path": bad}, ctx)
        assert result["ok"] is False, f"{bad} 不该读得出来"
        assert "硬排除" in result["error"]

    # 二进制产物连读出都不给
    assert execute_tool("read_artifact", {"path": "05-final.mp4"}, ctx)["ok"] is False
    # 但别把整个期目录一并封死：排片产物不在禁列，照常可读
    assert execute_tool("read_artifact", {"path": "04-clips.json"}, ctx)["ok"] is True
    assert execute_tool("read_artifact", {"path": "02-script.draft.md"}, ctx)["ok"] is True


def test_egress_payload_never_contains_restricted_content(tmp_path: Path, monkeypatch):
    """§5 PR1 验收项的机械验证：交付给端点的请求体里没有 03-audio/config/pipeline 内容。

    这是 Y2-r19 的正向判据——不是「断言函数能拦字符串」，而是「实际发出去的字节里没有」。
    """
    with mock_llm_server([
        tool_call("read_artifact", {"path": "03-audio/corrections.json"}),
        tool_call("read_artifact", {"path": "02-script.draft.md"}, "call_2"),
        tool_call("read_status", {}, "call_3"),
        {"role": "assistant", "content": "做不到的部分我就不读了"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)

        ep = root / "data" / "episodes" / "T1"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "03-audio" / "corrections.json").write_text(
            '{"secret": "SEGRET-AUDIO-CORRECTIONS"}', encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(
            '{"secret": "SEGRET-AUDIO-MANIFEST"}', encoding="utf-8")
        (root / "config" / "cloud.local.json").write_text(
            '{"secret": "SEGRET-CONFIG"}', encoding="utf-8")
        (root / "pipeline").mkdir(exist_ok=True)
        (root / "pipeline" / "leak.py").write_text("SEGRET-PIPELINE", encoding="utf-8")
        (ep / "02-script.draft.md").write_text("# 正常草稿", encoding="utf-8")

        outcome = run_tool_loop(
            [{"role": "user", "content": "看看上期配音改了什么，再读读配置和源码"}],
            ctx=ToolContext(scope="creative", episode_dir=ep, root=root),
            approve=lambda name, args: True,
        )

        assert outcome["stopped"] == "done"
        assert state["calls"] >= 1
        for request in state["requests"]:
            wire = json.dumps(request["body"], ensure_ascii=False)
            for marker in ("SEGRET-AUDIO-CORRECTIONS", "SEGRET-AUDIO-MANIFEST",
                           "SEGRET-CONFIG", "SEGRET-PIPELINE"):
                assert marker not in wire, f"受限内容出网了: {marker}"


def test_creative_loop_survives_egress_block(tmp_path: Path, monkeypatch, capsys):
    """出网闸触发时会话不许带 traceback 崩退（终审 P0-1 第二症状）。"""
    from pipeline import paths
    from pipeline.agent import cli

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.delenv("AVA_TEST_KEY", raising=False)

    with mock_llm_server([{"role": "assistant", "content": "不该到达"}]) as (url, state):
        make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        ep = tmp_path / "data" / "episodes" / "T1"
        ep.mkdir(parents=True)
        (ep / "01-topic.md").write_text("# t", encoding="utf-8")

        # 用户输入本身含受限标记 → chat_complete 内断言拦下，会话应继续而非崩退
        with patch_inputs(["cloud.local.json 里写了什么", "/quit"]):
            assert cli.run_creative_loop(ep, "chat") == 0

        out = capsys.readouterr().out
        assert "[BLOCKED] 出网被拦截" in out
        assert state["calls"] == 0


@contextmanager
def patch_inputs(values):
    """把 builtins.input 依次喂成给定值（用尽即 EOFError）。"""
    from unittest.mock import patch
    with patch("builtins.input", side_effect=[*values, EOFError()]):
        yield


def test_degrade_message_is_scope_aware():
    """降级清单随 scope 变，不把 creative 的清单念给 pipeline scope（终审观察项）。"""
    creative = local_directive_message("creative", "无密钥")["content"]
    pipeline = local_directive_message("pipeline", "无密钥")["content"]

    assert "01-topic.md" in creative
    assert "01-topic.md" not in pipeline
    assert "pipeline scope" in pipeline
    assert "docs/WORKFLOW.md" in pipeline
    # 两条都必须显式标降级，且都指向人工停机点
    for text in (creative, pipeline):
        assert "降级模式" in text and "02.5 / 03.5 / 05 / 09" in text


# ---------------------------------------------------------------------------
# 7. 终审二轮 P0 回归：读域与发送闸的大小写口径（APFS 默认大小写不敏感）
# ---------------------------------------------------------------------------


def _fs_is_case_insensitive(root: Path) -> bool:
    probe = root / "CaseProbe"
    probe.mkdir(exist_ok=True)
    (probe / "probe.TXT").write_text("x", encoding="utf-8")
    try:
        return (probe / "PROBE.txt").exists()
    finally:
        for item in probe.iterdir():
            item.unlink()
        probe.rmdir()


def test_read_deny_dir_is_case_insensitive(tmp_path: Path):
    """大小写变体不得绕过读域硬排除（终审二轮 P0）。"""
    from pipeline.agent.tools import deny_dir_hit

    assert deny_dir_hit(Path("/x/03-AUDIO/manifest.json")) == "03-audio"
    assert deny_dir_hit(Path("/x/03-Audio/Corrections.JSON")) == "03-audio"
    assert deny_dir_hit(Path("/x/04-PATCH/pool.json")) == "04-patch"
    assert deny_dir_hit(Path("/x/02-script.md")) is None
    assert deny_dir_hit(Path("/x/notes/03-audioish.md")) is None  # 前缀相似不算命中

    root = make_agent_root(tmp_path, "https://api.example.com/v1")
    ep = root / "data" / "episodes" / "T1"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "03-audio" / "manifest.json").write_text('{"engine":"SECRET-ENGINE"}', encoding="utf-8")
    ctx = ToolContext(scope="creative", episode_dir=ep, root=root)

    for bad in ("03-AUDIO/manifest.json", "03-Audio/MANIFEST.JSON", "04-PATCH/pool.json"):
        result = execute_tool("read_artifact", {"path": bad}, ctx)
        assert result["ok"] is False, f"{bad} 不该读得出来"
        assert "SECRET-ENGINE" not in json.dumps(result, ensure_ascii=False)

    if _fs_is_case_insensitive(tmp_path):
        # 部署语义（macOS/APFS）：必须报「硬排除」而不是「文件不存在」——
        # 说明拦住它的是护栏，不是巧合找不到文件
        assert "硬排除" in execute_tool(
            "read_artifact", {"path": "03-AUDIO/manifest.json"}, ctx
        )["error"]


def test_egress_boundary_matching_is_case_insensitive():
    """发送闸与读域同一判定口径：大小写变体同样命中（终审二轮 P0 的第二半）。"""
    from pipeline.agent.tools import assert_egress_boundary

    assert_egress_boundary("https://api.openai.com", {"k": "普通内容"})
    for bad in ("Cloud.Local.JSON", "03-AUDIO/MANIFEST.JSON", "03-Audio/Voice.json"):
        with pytest.raises(PermissionError, match="拦截出网请求"):
            assert_egress_boundary("https://api.openai.com", {"k": bad})


def test_egress_payload_blocks_case_variant_audio_read(tmp_path: Path, monkeypatch):
    """大小写变体路径的两层防线：读域拒了；标记一旦进入会话，发送闸把整轮掐掉。

    fail-closed 的实际形状：模型提议读 03-AUDIO/manifest.json → 读域拒绝 → 那条
    提议（含受限路径标记）留在会话里 → 下一轮请求被发送闸拦下。宁可整轮不发，
    也不给「半扇门」留缝。
    """
    with mock_llm_server([
        tool_call("read_artifact", {"path": "03-AUDIO/manifest.json"}),
        {"role": "assistant", "content": "读不到就算了"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        ep = root / "data" / "episodes" / "T1"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "03-audio" / "manifest.json").write_text(
            '{"secret": "SEGRET-ENGINE-UPPER"}', encoding="utf-8"
        )

        with pytest.raises(PermissionError, match="拦截出网请求"):
            run_tool_loop(
                [{"role": "user", "content": "读一下那段音频清单"}],
                ctx=ToolContext(scope="creative", episode_dir=ep, root=root),
            )

        # 第一轮请求发出去了，但不含任何文件内容
        assert state["calls"] == 1
        wire = json.dumps(state["requests"][0]["body"], ensure_ascii=False)
        assert "SEGRET-ENGINE-UPPER" not in wire
        # 含受限标记的那一轮根本没发出去
        assert "03-audio/manifest.json" not in wire.casefold()


def test_case_variant_read_of_unpatterned_audio_file_never_leaves(tmp_path: Path, monkeypatch):
    """corrections.json 不在发送闸的字面量模式里 → 只能靠读域拦。

    这是终审二轮 P0 的原始形态：发送闸只认三个字面量文件名（cloud.local.json /
    03-audio/manifest.json / 03-audio/voice.json），读域一旦被大小写变体破开，
    未列入模式的文件就是真泄漏。本用例只在读域真的拦得住时才绿。
    """
    with mock_llm_server([
        tool_call("read_artifact", {"path": "03-AUDIO/corrections.json"}),
        {"role": "assistant", "content": "那我不读了"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        ep = root / "data" / "episodes" / "T1"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "03-audio" / "corrections.json").write_text(
            '{"secret": "SEGRET-CORRECTIONS-UPPER"}', encoding="utf-8"
        )

        outcome = run_tool_loop(
            [{"role": "user", "content": "看看上期的纠错记录"}],
            ctx=ToolContext(scope="creative", episode_dir=ep, root=root),
        )

        # 没被发送闸掐掉（会话里没出现受限标记）= 拦住它的是读域
        assert outcome["stopped"] == "done"
        assert state["calls"] == 2
        for request in state["requests"]:
            assert "SEGRET-CORRECTIONS-UPPER" not in json.dumps(
                request["body"], ensure_ascii=False
            )
