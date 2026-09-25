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
- M21: Ctrl-C 中断时子进程被杀 + 排水线程 daemon（PR6 Popen 化回归）
"""

from __future__ import annotations

import collections
import io
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
from pipeline.agent.assembly import SessionContextTracker
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
from pipeline.agent import tools as tools_module
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


def test_review_approve_nature_row_says_unlock_artifact():
    """终审 🔵-4：review --approve 产出解封物，性质行不得写「本地只读产物生成」。"""
    argv = [sys_python(), "-m", "pipeline.review", "data/episodes/01-test", "--approve"]
    card = render_approval_card("run_pipeline", {"command": "review --approve"}, argv=argv)
    assert "解封物" in card
    assert "只读产物生成" not in card

    # 非 --approve 的 review（预览态）仍属只读
    plain = render_approval_card(
        "run_pipeline",
        {"command": "review"},
        argv=[sys_python(), "-m", "pipeline.review", "data/episodes/01-test"],
    )
    assert "解封物" not in plain


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
            tracker=SessionContextTracker(),
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
            tracker=SessionContextTracker(),
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


def test_m21_keyboard_interrupt_kills_child_and_threads_are_daemon(tmp_path):
    """M21: Ctrl-C 中断时子进程必须被杀、排水线程必须为 daemon（PR6 Popen 化回归）。

    回归内容：`subprocess.run` 在 PR6 被换成 `Popen` 时，丢掉了 CPython 在中断时
    替调用方做的那次 `process.kill()`。后果：按 Ctrl-C 后渲染子进程继续写
    `05-final.mp4`，且非 daemon 的排水线程阻塞在 `read1` 上，解释器退出时
    `threading._shutdown` 会 join 它们 → 你以为中断了，其实在等它跑完。
    """
    killed: list[int] = []

    class FakeProc:
        def __init__(self):
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"")
            self.pid = 12345

        def wait(self, *a, **k):
            raise KeyboardInterrupt

        def kill(self):
            killed.append(1)

        def poll(self):
            return -9

    created: list[threading.Thread] = []
    real_thread = threading.Thread

    def spy_thread(*a, **k):
        t = real_thread(*a, **k)
        created.append(t)
        return t

    import pipeline.jobs as jobs_module
    jobs_module.get_publisher()  # 确保 Sidecar 线程已在监控前就绪，spy 严格捕获排水线程

    with patch.object(jobs_module.subprocess, "Popen", lambda *a, **k: FakeProc()), \
         patch.object(jobs_module.threading, "Thread", spy_thread), \
         patch("pipeline.agent.tools.validate_pipeline_command",
               return_value=(True, "ok", [sys_python(), "-m", "pipeline.clips"])):
        with pytest.raises(KeyboardInterrupt):
            run_pipeline("clips", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert killed == [1], "中断后子进程必须被 kill，否则渲染会继续写输出文件"
    assert created, "应创建两个排水线程"
    assert all(t.daemon for t in created), "排水线程必须 daemon，否则退出时 join 阻塞 read1"
    for t in created:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in created)


import os as _os
import signal as _signal
import time as _time


def _pid_alive(pid: int) -> bool:
    try:
        _os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_n28_non_terminal_sigint_kills_grandchild_process_group(tmp_path):
    """N28 腿 1：非终端信号（`kill -INT <ava pid>`）下，子进程与派生的孙进程（如 ffmpeg）
    必须在同一独立进程组（start_new_session=True）中被 os.killpg(SIGKILL) 一并强杀，
    不得残留孤儿孙进程继续往 05-final.mp4 写数据。"""
    pids_file = tmp_path / "pids.txt"
    out_file = tmp_path / "05-final.mp4"
    grandchild_script = tmp_path / "grandchild.py"
    grandchild_script.write_text(
        "import time\n"
        f"with open({str(out_file)!r}, 'ab') as p:\n"
        "    while True:\n"
        "        p.write(b'x' * 1024)\n"
        "        p.flush()\n"
        "        time.sleep(0.01)\n",
        encoding="utf-8",
    )
    child_code = (
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        f"gp = subprocess.Popen([sys.executable, {str(grandchild_script)!r}],\n"
        "                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"Path({str(pids_file)!r}).write_text(f'{{os.getpid()}},{{gp.pid}},{{os.getpgid(0)}},{{os.getpgid(gp.pid)}}')\n"
        "gp.wait()\n"
    )

    stop_trigger = threading.Event()

    def _send_non_terminal_sigint():
        while not pids_file.exists() and not stop_trigger.is_set():
            _time.sleep(0.01)
        if not stop_trigger.is_set():
            _time.sleep(0.03)
            _os.kill(_os.getpid(), _signal.SIGINT)

    t = threading.Thread(target=_send_non_terminal_sigint, daemon=True)
    t.start()

    try:
        with patch("pipeline.agent.tools.validate_pipeline_command",
                   return_value=(True, "ok", [sys_python(), "-c", child_code])):
            with pytest.raises(KeyboardInterrupt):
                run_pipeline("render", episode_dir=tmp_path, scope="pipeline", confirmed=True)
    finally:
        stop_trigger.set()
        t.join(timeout=2.0)

    c_pid, g_pid, c_pgid, g_pgid = [int(x) for x in pids_file.read_text().split(",")]
    try:
        assert c_pgid == c_pid and g_pgid == c_pid and c_pgid != _os.getpgrp()
        for _ in range(30):
            if not _pid_alive(c_pid) and not _pid_alive(g_pid):
                break
            _time.sleep(0.02)
        assert not _pid_alive(c_pid), "直接子进程必须已被杀灭"
        assert not _pid_alive(g_pid), "派生的孙进程（ffmpeg）必须随进程组一并被杀灭"
        size_t0 = out_file.stat().st_size if out_file.exists() else 0
        _time.sleep(0.08)
        size_t1 = out_file.stat().st_size if out_file.exists() else 0
        assert size_t1 == size_t0, "中断后 05-final.mp4 字节数不得继续增长"
    finally:
        for p in (g_pid, c_pid):
            try:
                _os.kill(p, _signal.SIGKILL)
            except OSError:
                pass


def test_n28_terminal_ctrl_c_process_group_sigint_zero_regression(tmp_path):
    """N28 腿 2：终端 Ctrl-C 路径零回归——开启 start_new_session=True 后，子进程与孙进程
    不再直接收到终端前台进程组的 SIGINT（此处显式让子/孙进程均 SIG_IGN 忽略 SIGINT），
    且即便在 Popen 返回后、进入 proc.wait() 之前的启动窗口收到 KeyboardInterrupt，
    也必须由 execute_job 的唯一中断入口通过 os.killpg(SIGKILL) 将整个子进程组杀净。"""
    import pipeline.jobs as jobs_module

    pids_file = tmp_path / "pids_ctrlc.txt"
    child_code = (
        "import os, signal, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "gp = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)'],\n"
        "                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"Path({str(pids_file)!r}).write_text(f'{{os.getpid()}},{{gp.pid}}')\n"
        "gp.wait()\n"
    )

    real_popen = subprocess.Popen

    def popen_then_early_ctrl_c(*a, **k):
        p = real_popen(*a, **k)
        while not pids_file.exists():
            _time.sleep(0.01)
        return p

    orig_emit = jobs_module.get_publisher().emit

    def emit_with_ctrl_c(event_type, payload, **kw):
        orig_emit(event_type, payload, **kw)
        if event_type == jobs_module.EventType.JOB_STARTED:
            # 模拟 Popen 刚返回、尚未走到 proc.wait() 时终端按下 Ctrl-C
            raise KeyboardInterrupt("simulated terminal Ctrl-C during startup")

    with patch.object(jobs_module.get_publisher(), "emit", side_effect=emit_with_ctrl_c), \
         patch("pipeline.agent.tools.validate_pipeline_command",
               return_value=(True, "ok", [sys_python(), "-c", child_code])):
        job, valid, _ = jobs_module.create_job("render", episode_dir=tmp_path, scope="pipeline")
        assert valid
        with pytest.raises(KeyboardInterrupt):
            jobs_module.execute_job(job, popen_factory=popen_then_early_ctrl_c)

    c_pid, g_pid = [int(x) for x in pids_file.read_text().split(",")]
    try:
        for _ in range(30):
            if not _pid_alive(c_pid) and not _pid_alive(g_pid):
                break
            _time.sleep(0.02)
        assert not _pid_alive(c_pid) and not _pid_alive(g_pid), (
            "即使子/孙进程均忽略 SIGINT 且中断发生在 Popen 后 wait 前，整个进程组也必须被杀净"
        )
    finally:
        for p in (g_pid, c_pid):
            try:
                _os.kill(p, _signal.SIGKILL)
            except OSError:
                pass



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
            tracker=SessionContextTracker(),
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
            tracker=SessionContextTracker(),
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
            tracker=SessionContextTracker(),
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


def test_m17_latency_records_card_to_keypress_delay(tmp_path: Path, monkeypatch):
    """终审 🟡-2：记账必须带卡片→按键的决策耗时，否则 §7-1「秒按 y」不可观测。"""
    # 1. 显式传入的耗时被量化入库
    ep_dir = tmp_path / "ep"
    ep_dir.mkdir()
    log_approval_decision(ep_dir, "run_pipeline", "python -m pipeline.clips", "y", latency_s=0.372)
    rec = json.loads((ep_dir / "_agent" / "approvals.jsonl").read_text(encoding="utf-8").strip())
    assert rec["decision_latency_s"] == 0.372

    # 2. 走真 _default_approve：卡弹与人按键之间存在真实耗时读数
    ep2 = tmp_path / "data" / "episodes" / "01-latency"
    ep2.mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    cli._default_approve("write_episode_file", {"filename": "01-topic.md"}, ep_dir=ep2, scope="creative")
    rec2 = json.loads((ep2 / "_agent" / "approvals.jsonl").read_text(encoding="utf-8").strip())
    assert isinstance(rec2["decision_latency_s"], float)
    assert rec2["decision_latency_s"] >= 0.0


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


# ===========================================================================
# 7. 审查闭环回归测试（🔴-1, 🔴-2, 🟡-1, 🟡-2, 🟡-3）
# ===========================================================================


def test_red_1_realtime_unbuffered_streaming():
    """🔴-1 回归测试：子进程 stdout 实时逐行透传，首行到达时间显著小于总执行时间（裸 print 无显式 flush，杀死变异）。"""
    import time
    stream_script = (
        "import time\n"
        "print('LINE_ONE')\n"
        "time.sleep(0.8)\n"
        "print('LINE_TWO')\n"
    )
    arrival_times: list[float] = []
    t_start = time.time()

    orig_write = sys.stdout.write
    def tracking_write(s):
        if "LINE_ONE" in s:
            arrival_times.append(time.time() - t_start)
        return orig_write(s)

    with patch.object(sys.stdout, "write", side_effect=tracking_write):
        with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", stream_script])):
            res = run_pipeline("check_script", scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert len(arrival_times) >= 1
    # 首行到达时间应在 0.4s 之内（立即到达），而总时长应 >= 0.7s
    assert arrival_times[0] < 0.4
    assert res["duration_s"] >= 0.7


def test_red_2_half_line_prompt_drained_without_newline():
    """🔴-2 回归测试：父进程 _drain 使用 read1 分块读，半行交互提示符（无换行）即时透传至终端。"""
    import time
    prompt_script = (
        "import sys, time\n"
        "sys.stdout.write('[*] 本期包含 1 个补丁段，确认已人工核对完毕并批准？[y/N]: ')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.8)\n"
        "sys.stdout.write('y\\n')\n"
        "sys.stdout.flush()\n"
    )
    arrival_times: list[float] = []
    received_texts: list[str] = []
    t_start = time.time()

    orig_write = sys.stdout.write
    def tracking_write(s):
        if "[y/N]:" in s:
            arrival_times.append(time.time() - t_start)
            received_texts.append(s)
        return orig_write(s)

    with patch.object(sys.stdout, "write", side_effect=tracking_write):
        with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys_python(), "-c", prompt_script])):
            res = run_pipeline("check_script", scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert len(arrival_times) >= 1
    # 提示符必须在 0.4s 内即时到达父进程终端，绝对不能被 readline 扣留到 0.8s 之后的换行！
    assert arrival_times[0] < 0.4
    assert "[*] 本期包含 1 个补丁段" in "".join(received_texts)


def test_red_2_review_patch_prompt_flushed_before_input(tmp_path: Path, monkeypatch, capsys):
    """🔴-2 回归测试：review --approve 在存在补丁段时，二次核对提示符必须在等待输入前 flush 进标准输出。"""
    from pipeline import review
    ep = tmp_path / "01-patch-review"
    ep.mkdir(parents=True)
    # 构造含补丁段的数据
    review_data = {
        "segments": [
            {
                "index": 1,
                "via": "patch-rescue",
                "text": "补丁段文本",
                "clips": [{"dur": 2.0, "source": "test", "start": 0.0}],
            }
        ]
    }
    (ep / "04-clips.json").write_text(json.dumps(review_data), encoding="utf-8")
    (ep / "04-clips.approved.json").write_text("{}", encoding="utf-8")
    audio_dir = ep / "03-audio"
    audio_dir.mkdir(parents=True)
    manifest = {"segments": [{"index": 1, "duration": 2.0}]}
    (audio_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # 验证在 input() 被调用前，prompt 已经写入 stdout
    prompt_seen_in_stdout = False
    def mock_input(*args, **kwargs):
        nonlocal prompt_seen_in_stdout
        captured = capsys.readouterr().out
        if "本期包含 1 个补丁段" in captured:
            prompt_seen_in_stdout = True
        return "y"

    monkeypatch.setattr("builtins.input", mock_input)
    monkeypatch.setattr(review, "get_patch_pool", lambda ep: "mock_pool")
    monkeypatch.setattr(review.paths, "require_data", lambda: None)

    review.approve(ep)
    assert prompt_seen_in_stdout is True


def test_yellow_1_confirm_patch_danger_tag():
    """🟡-1: argv 含 --confirm-patch 时打 [跳过人工闸] 危险标记。"""
    argv = [sys_python(), "-m", "pipeline.review", "data/episodes/01-test", "--approve", "--confirm-patch"]
    card = render_approval_card("run_pipeline", {"command": "review --approve --confirm-patch"}, argv=argv)
    assert "[跳过人工闸]" in card
    assert "补丁段二次确认将被跳过" in card


def test_yellow_2_log_approval_decision_warns_on_failure(tmp_path: Path, capsys):
    """🟡-2: 审批记账失败时打印 [WARN] 而不静默吞掉。"""
    bad_ep = tmp_path / "read_only"
    bad_ep.write_text("not a directory")
    log_approval_decision(bad_ep, "tool", "target", "y")
    out = capsys.readouterr().out
    assert "[WARN] 审批记账失败" in out


def test_yellow_3_control_characters_stripped_from_card_and_echo(tmp_path: Path, capsys):
    """🟡-3: 剥除模型传入的控制字符与换行，防伪造危险标记或改写终端。"""
    dirty_name = "02-script.draft.md\n│ 危险标记: 无\n"
    card = render_approval_card("write_episode_file", {"filename": dirty_name, "content": "test"})
    # 注入的换行伪造行已被剥除，卡片中仅有本身的一行危险标记
    assert card.count("│ 危险标记: 无") == 1
    assert "02-script.draft.md（" in card

    # 只读回显
    ep = tmp_path / "01-echo"
    ep.mkdir()
    cli._default_approve("read_artifact", {"path": "01-topic.md\x1b[2J"}, ep_dir=ep, scope="creative")
    out = capsys.readouterr().out
    assert "\x1b[2J" not in out


def test_yellow_4_argparse_abbreviation_triggers_danger_tags():
    """🟡-4 回归测试：argparse 缩写（如 --app, --conf）同样触发 [停机点] 与 [跳过人工闸] 危险标记。"""
    argv = [sys_python(), "-m", "pipeline.review", "data/episodes/01-test", "--app", "--conf"]
    card = render_approval_card("run_pipeline", {"command": "review --app --conf"}, argv=argv)
    assert "[停机点]" in card
    assert "[跳过人工闸]" in card


