"""PR6 审批卡片、执行回喂与状态机护栏测试（Spec §3, §4.5, §5, §6）。

覆盖变异清单：
- M3: 审批拦截与 tuple 签归一化（零副作用保证）
- M8: 执行失败 stderr_tail 回喂模型
- M9: 尾部环形缓冲 ≤ 4 KB 截断与 truncated 标记
- M14: 审批卡片危险标记静态规则（cloud/render/覆盖/停机点）
- M15: run_pipeline 预校验拒收不弹卡且回喂具体拒因
- M16: side_effect 标志 fail-closed 分流与 schema 干净性
- M17: 审批事件记账至 _agent/approvals.jsonl
- M20: 未注册 / 超 scope 工具预校验拒收不弹卡
"""

from __future__ import annotations

import collections
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipeline.agent import cli
from pipeline.agent.llm import ToolContext, run_tool_loop
from pipeline.agent.status_card import (
    log_approval_decision,
    render_approval_card,
)
from pipeline.agent.tools import (
    TOOL_SCHEMAS,
    build_tool_schemas,
    run_pipeline,
    sys_python,
    write_episode_file,
)
from pipeline.status import EpisodeStatus, inspect_episode
from tests.test_agent_director import patch_inputs
from tests.test_agent_tools import make_agent_root, mock_llm_server, tool_call


# ===========================================================================
# 1. render_approval_card 纯函数单元测试 (M14)
# ===========================================================================


def test_m14_render_approval_card_cloud_billing():
    """M14: cloud up/run/push/pull 渲染计费审批版式且打 ☁计费 标记 (Spec §3.2, §5.1 M14)。"""
    argv = [sys_python(), "-m", "pipeline.cloud", "up"]
    card = render_approval_card("run_pipeline", {"command": "cloud up"}, argv=argv)

    assert "执行审批" in card or "计费审批" in card
    assert "pipeline.cloud up" in card
    assert "☁" in card
    assert "☁计费" in card or "[计费]" in card
    assert "实例开机将产生费用" in card
    assert "执行? [y/N]:" in card


def test_m14_render_approval_card_render_long_task():
    """M14: render 渲染长任务危险标记 (Spec §3.2, §5.1 M14)。"""
    argv = [sys_python(), "-m", "pipeline.render", "data/episodes/01-test"]
    card = render_approval_card("run_pipeline", {"command": "render"}, argv=argv)

    assert "run_pipeline" in card
    assert "[长任务]" in card
    assert "渲染耗时较长" in card


def test_m14_render_approval_card_stop_point_from_approve_flag():
    """M14: argv 含 --approve 时打 [停机点] 标记 (Spec §3.2, §5.1 M14)。"""
    argv = [sys_python(), "-m", "pipeline.review", "data/episodes/01-test", "--approve"]
    card = render_approval_card("run_pipeline", {"command": "review --approve"}, argv=argv)

    assert "[停机点]" in card
    assert "批准操作" in card


def test_m14_render_approval_card_stop_point_from_stop_label():
    """M14: 推进停机点命令（如 03.5 配音顺听）打 [停机点] 标记 (Spec §3.2, §5.1 M14)。"""
    argv = [sys_python(), "-m", "pipeline.clips", "data/episodes/01-test"]
    card = render_approval_card(
        "run_pipeline",
        {"command": "clips"},
        argv=argv,
        stop_label="03.5 配音顺听",
    )

    assert "[停机点]" in card
    assert "03.5 配音顺听" in card


def test_m14_render_approval_card_write_overwrite():
    """M14: write_episode_file 目标文件存在时打 [覆盖] 标记 (Spec §3.2, §5.1 M14)。"""
    card = render_approval_card(
        "write_episode_file",
        {"filename": "02-script.draft.md", "content": "新草稿内容"},
        target_exists=True,
    )

    assert "写入审批" in card
    assert "02-script.draft.md" in card
    assert "覆盖现有文件" in card
    assert "[覆盖]" in card


def test_m14_render_approval_card_write_new_file_no_danger():
    """M14: write_episode_file 新建文件时无危险标记 (Spec §3.2, §5.1 M14)。"""
    card = render_approval_card(
        "write_episode_file",
        {"filename": "01-topic.md", "content": "选题"},
        target_exists=False,
    )

    assert "写入审批" in card
    assert "01-topic.md" in card
    assert "新建文件" in card
    assert "危险标记: 无" in card


# ===========================================================================
# 2. M3: 审批拦截、tuple 签归一化与零副作用验收
# ===========================================================================


def test_m3_reject_on_n_produces_no_file_and_no_popen(tmp_path: Path, monkeypatch):
    """M3: 弹卡后按 N 时既不产生子进程也不落盘 (Spec §5.1 M3)。"""
    with mock_llm_server([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "草稿内容"}),
        {"role": "assistant", "content": "已取消写入"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-m3"
        ep_dir.mkdir(parents=True)

        # 模拟人类在终端敲 'n'
        monkeypatch.setattr("builtins.input", lambda *args: "n")
        mock_popen = MagicMock()
        monkeypatch.setattr(subprocess, "Popen", mock_popen)

        status = inspect_episode(ep_dir)
        outcome = cli._dispatch_agent_turn(
            "帮我写草稿",
            [],
            ep_dir,
            "creative",
            status,
            root=root,
        )

        assert mock_popen.call_count == 0
        assert not (ep_dir / "02-script.draft.md").exists()
        # 回喂给模型的 tool 消息应记录拒绝
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is False
        assert "拒绝" in fed_back["error"]


def test_m3_tuple_signature_false_reason_zero_side_effects(tmp_path: Path, monkeypatch):
    """M3: approve 返回 (False, '拒因') 时消费端归一化拦截，零副作用（防元组真值漏闸）(Spec §3.2-2, §5.1 M3)。"""
    with mock_llm_server([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "越权"}),
        {"role": "assistant", "content": "收到拒绝"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-tuple"
        ep_dir.mkdir(parents=True)

        custom_reason = "自定义护栏拦截：格式不合规"
        # 显式返回 (False, reason) 元组——若消费端写成 not approve(...)，非空元组为真，not 之后为 False，会误放行！
        outcome = run_tool_loop(
            [{"role": "user", "content": "写入草稿"}],
            ctx=ToolContext(scope="creative", episode_dir=ep_dir, root=root),
            approve=lambda name, args: (False, custom_reason),
        )

        assert not (ep_dir / "02-script.draft.md").exists()
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is False
        assert fed_back["error"] == custom_reason


def test_m3_approve_on_y_executes_write_with_confirmed(tmp_path: Path, monkeypatch):
    """M3: 弹卡后按 y 正常执行写入且具备 confirmed=True 权限 (Spec §5.1 M3)。"""
    with mock_llm_server([
        tool_call("write_episode_file", {"filename": "02-script.draft.md", "content": "定稿草稿"}),
        {"role": "assistant", "content": "写入成功"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-approve-y"
        ep_dir.mkdir(parents=True)

        monkeypatch.setattr("builtins.input", lambda *args: "y")
        status = inspect_episode(ep_dir)

        cli._dispatch_agent_turn(
            "帮我写草稿",
            [],
            ep_dir,
            "creative",
            status,
            root=root,
        )

        draft_path = ep_dir / "02-script.draft.md"
        assert draft_path.exists()
        assert draft_path.read_text(encoding="utf-8") == "定稿草稿"


# ===========================================================================
# 3. M8 & M9: run_pipeline Popen 执行回喂与环形缓冲截断
# ===========================================================================


def test_m8_nonzero_exit_includes_stderr_tail(tmp_path: Path):
    """M8: 非零退出时工具结果包含 stderr_tail 与退出码 (Spec §3.3, §5.1 M8)。"""
    # 构造必定失败的命令（通过 validate 并在执行中抛 stderr）
    failing_cmd = f"{sys_python()} -c \"import sys; sys.stderr.write('CRITICAL_SYNTAX_ERROR\\n'); sys.exit(42)\""
    outcome = run_pipeline(
        ["check_script", "02-script.md"],  # validate_pipeline 放行 check_script
        episode_dir=tmp_path,
        scope="pipeline",
        confirmed=False,
    )
    # mock argv 让它执行我们失败的命令
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", "import sys; sys.stderr.write('CRITICAL_SYNTAX_ERROR\\n'); sys.exit(42)"])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is False
    assert res["returncode"] == 42
    assert "CRITICAL_SYNTAX_ERROR" in res["stderr_tail"]
    assert res["duration_s"] >= 0.0


def test_m8_model_receives_stderr_tail_in_next_turn(tmp_path: Path, monkeypatch):
    """M8: 模型在下一轮收到包含 stderr_tail 的 tool 回喂消息 (Spec §5.1 M8)。"""
    with mock_llm_server([
        tool_call("run_pipeline", {"command": "check_script"}),
        {"role": "assistant", "content": "我看到脚本报错了"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-m8"
        ep_dir.mkdir(parents=True)

        with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", "import sys; sys.stderr.write('SYNTAX_ERR_LINE_42\\n'); sys.exit(1)"])):
            outcome = run_tool_loop(
                [{"role": "user", "content": "检查脚本"}],
                ctx=ToolContext(scope="pipeline", episode_dir=ep_dir, root=root, confirmed=True),
            )

        assert outcome["stopped"] == "done"
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is True
        result = fed_back["result"]
        assert result["ok"] is False
        assert result["returncode"] == 1
        assert "SYNTAX_ERR_LINE_42" in result["stderr_tail"]


def test_m9_buffer_exceeding_4kb_truncated_flag_and_bounded():
    """M9: stdout 输出超过 4 KB 时带 truncated: true 且长度有上界 (Spec §3.3, §5.1 M9)。"""
    # 产生 10 KB 的输出
    big_output_script = "import sys; sys.stdout.write('A' * 10240); sys.stdout.flush()"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", big_output_script])):
        res = run_pipeline("check_script", scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert res["truncated"] is True
    assert len(res["stdout_tail"].encode("utf-8")) <= 4096
    assert len(res["stdout_tail"]) > 0


def test_m9_buffer_under_4kb_not_truncated():
    """M9: stdout 输出小于 4 KB 时 truncated 为 False 且内容完整 (Spec §3.3, §5.1 M9)。"""
    small_output_script = "import sys; sys.stdout.write('HELLO_AVA_WORLD\\n'); sys.stdout.flush()"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", small_output_script])):
        res = run_pipeline("check_script", scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert res["truncated"] is False
    assert "HELLO_AVA_WORLD" in res["stdout_tail"]


def test_popen_dual_pipe_concurrent_draining():
    """证明 stdout/stderr 双管并发排水：各写入 128 KB 数据不发生管道死锁。"""
    flood_script = (
        "import sys, threading\n"
        "def write_out(): sys.stdout.write('O' * 131072); sys.stdout.flush()\n"
        "def write_err(): sys.stderr.write('E' * 131072); sys.stderr.flush()\n"
        "t1 = threading.Thread(target=write_out)\n"
        "t2 = threading.Thread(target=write_err)\n"
        "t1.start(); t2.start()\n"
        "t1.join(); t2.join()\n"
    )
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", flood_script])):
        res = run_pipeline("check_script", scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert res["truncated"] is True
    assert len(res["stdout_tail"].encode("utf-8")) <= 4096
    assert len(res["stderr_tail"].encode("utf-8")) <= 4096


# ===========================================================================
# 4. M15 & M20: 预校验优先与拒收不弹卡
# ===========================================================================


def test_m15_validate_rejection_no_prompt_and_specific_reason(tmp_path: Path, monkeypatch):
    """M15: run_pipeline 命中 --force 等校验拒收时不弹卡（input 计数 0）且具体拒因回喂 (Spec §5.1 M15)。"""
    with mock_llm_server([
        tool_call("run_pipeline", {"command": "tts --force"}),
        {"role": "assistant", "content": "抱歉，那我改用增量"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-m15"
        ep_dir.mkdir(parents=True)

        input_mock = MagicMock()
        monkeypatch.setattr("builtins.input", input_mock)

        status = inspect_episode(ep_dir)
        cli._dispatch_agent_turn(
            "全量重跑 tts",
            [],
            ep_dir,
            "pipeline",
            status,
            root=root,
        )

        # 绝对不弹卡！
        assert input_mock.call_count == 0

        # 具体拒因回喂模型，而不是模糊的「人类拒绝执行该工具调用」
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is False
        assert "--force" in fed_back["error"]
        assert "人类拒绝" not in fed_back["error"]


def test_m20_unregistered_tool_rejected_no_card(tmp_path: Path, monkeypatch):
    """M20: 未注册工具在预校验第一步拦截，不弹卡，拒因回喂 (Spec §5.1 M20)。"""
    with mock_llm_server([
        tool_call("format_disk", {"target": "/"}),
        {"role": "assistant", "content": "好的我不格式化了"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-m20"
        ep_dir.mkdir(parents=True)

        input_mock = MagicMock()
        monkeypatch.setattr("builtins.input", input_mock)

        status = inspect_episode(ep_dir)
        cli._dispatch_agent_turn(
            "执行危险工具",
            [],
            ep_dir,
            "creative",
            status,
            root=root,
        )

        assert input_mock.call_count == 0
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is False
        assert "未注册的工具" in fed_back["error"]


def test_m20_out_of_scope_tool_rejected_no_card(tmp_path: Path, monkeypatch):
    """M20: 不在当前 scope 白名单内的工具预校验拦截，不弹卡 (Spec §5.1 M20)。"""
    with mock_llm_server([
        tool_call("run_pipeline", {"command": "clips"}),
        {"role": "assistant", "content": "creative 阶段不跑排片"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
        ep_dir = root / "data" / "episodes" / "01-test-m20-scope"
        ep_dir.mkdir(parents=True)

        input_mock = MagicMock()
        monkeypatch.setattr("builtins.input", input_mock)

        status = inspect_episode(ep_dir)
        cli._dispatch_agent_turn(
            "跑排片",
            [],
            ep_dir,
            "creative",  # creative scope 看不到 run_pipeline
            status,
            root=root,
        )

        assert input_mock.call_count == 0
        fed_back = json.loads(state["requests"][1]["body"]["messages"][-1]["content"])
        assert fed_back["ok"] is False
        assert "不在 creative scope 白名单内" in fed_back["error"]


# ===========================================================================
# 5. M16: side_effect 分流、免审批回显与 Fail-Closed
# ===========================================================================


def test_m16_side_effect_false_four_tools_bypass_prompt_with_echo(tmp_path: Path, monkeypatch, capsys):
    """M16①: side_effect=False 的四个只读工具免弹卡且保留一行 [tool] 回显 (Spec §5.1 M16)。"""
    ep = tmp_path / "data" / "episodes" / "01-test-m16"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    input_mock = MagicMock()
    monkeypatch.setattr("builtins.input", input_mock)

    for tool_name, sample_args in [
        ("read_artifact", {"path": "01-topic.md"}),
        ("read_status", {"episode": "01-test-m16"}),
        ("list_episodes", {}),
        ("search_notes", {"query": "春物"}),
    ]:
        capsys.readouterr()  # 清空 buffer
        decision = cli._default_approve(tool_name, sample_args, ep_dir=ep, scope="creative")
        ok, reason = (decision, None) if isinstance(decision, bool) else decision
        assert ok is True
        assert input_mock.call_count == 0
        out = capsys.readouterr().out
        assert f"[tool] {tool_name}" in out


def test_m16_monkeypatch_unregistered_side_effect_prompts_user_fail_closed(tmp_path: Path, monkeypatch):
    """M16②: monkeypatch 注入未显式声明 side_effect 的工具必须弹卡 (fail-closed) (Spec §5.1 M16)。"""
    ep = tmp_path / "data" / "episodes" / "01-test-fail-closed"
    ep.mkdir(parents=True)

    fake_tool_name = "add_correction"
    fake_tool_schema = {
        "name": fake_tool_name,
        "description": "候选测试工具（未声明 side_effect）",
        "parameters": {"type": "object", "properties": {}},
    }

    monkeypatch.setitem(TOOL_SCHEMAS, fake_tool_name, fake_tool_schema)
    monkeypatch.setattr("pipeline.agent.tools.tool_names_for_scope", lambda scope, root=None: [fake_tool_name])

    input_mock = MagicMock(return_value="n")
    monkeypatch.setattr("builtins.input", input_mock)

    decision = cli._default_approve(fake_tool_name, {}, ep_dir=ep, scope="creative")
    ok, _ = (decision, None) if isinstance(decision, bool) else decision

    # 必须弹卡询问人类！
    assert input_mock.call_count == 1
    assert ok is False


def test_m16_side_effect_not_leaked_into_llm_schemas(tmp_path: Path):
    """M16③: build_tool_schemas 必须剔除 side_effect 键，宿主元数据不得泄入 LLM payload (Spec §5.1 M16)。"""
    for scope in ["creative", "pipeline"]:
        schemas = build_tool_schemas(scope)
        serialized = json.dumps(schemas, ensure_ascii=False)
        assert "side_effect" not in serialized


# ===========================================================================
# 6. M17: 审批事件记账至 _agent/approvals.jsonl
# ===========================================================================


def test_m17_approval_decision_appends_to_approvals_jsonl(tmp_path: Path, monkeypatch):
    """M17: 每次审批决定向 _agent/approvals.jsonl 追加一行记账 (Spec §3.2-6, §5.1 M17)。"""
    ep_dir = tmp_path / "data" / "episodes" / "01-test-m17"
    ep_dir.mkdir(parents=True)

    log_approval_decision(ep_dir, "run_pipeline", "python -m pipeline.clips", "y")
    log_approval_decision(ep_dir, "write_episode_file", "02-script.draft.md", "n")

    log_file = ep_dir / "_agent" / "approvals.jsonl"
    assert log_file.exists()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    rec1 = json.loads(lines[0])
    assert rec1["tool"] == "run_pipeline"
    assert rec1["decision"] == "y"
    assert "pipeline.clips" in rec1["target"]
    assert "timestamp" in rec1

    rec2 = json.loads(lines[1])
    assert rec2["tool"] == "write_episode_file"
    assert rec2["decision"] == "n"
    assert rec2["target"] == "02-script.draft.md"


def test_repl_run_cloud_up_shows_card_and_records_approval(tmp_path: Path, monkeypatch, capsys):
    """验证 REPL /run cloud up 弹出云端计费卡片且正常记账。"""
    ep = tmp_path / "data" / "episodes" / "01-repl-cloud"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    # 在 asset scope 下运行 /run cloud up
    status = EpisodeStatus(
        episode_dir=str(ep),
        episode_name=ep.name,
        current_step="00 资产准备",
        is_blocked=False,
        block_reason=None,
        completed_steps=[],
        next_action="",
        next_command=None,
        docs_ref="",
    )
    with patch("pipeline.agent.cli.inspect_episode", return_value=status):
        with patch("pipeline.agent.cli.scope_of", return_value="asset"):
            with patch("pipeline.agent.cli.check_code_freeze"):
                with patch("pipeline.agent.cli.run_pipeline") as mock_rp:
                    # 第一次校验
                    mock_rp.side_effect = [
                        {"ok": True, "message": "待人类确认", "argv": [sys_python(), "-m", "pipeline.cloud", "up"], "returncode": None},
                        {"ok": True, "message": "退出码 0", "argv": [sys_python(), "-m", "pipeline.cloud", "up"], "returncode": 0, "duration_s": 1.2},
                    ]
                    with patch_inputs(["/run cloud up", "y", "/quit"]):
                        cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path)

    out = capsys.readouterr().out
    assert "☁" in out
    assert "实例开机将产生费用" in out or "☁计费" in out

    log_file = ep / "_agent" / "approvals.jsonl"
    assert log_file.exists()
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert rec["decision"] == "y"
    assert "cloud" in rec["target"]
