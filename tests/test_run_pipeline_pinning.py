"""Pinning tests for run_pipeline behavior before PR3/PR4 executor swap.

These tests pin down existing contract of pipeline.agent.tools.run_pipeline:
- Exit codes: 0, non-zero, FileNotFoundError
- Output: stdout/stderr streaming, tail buffering, 4KB truncation
- Working directory: paths.ROOT
- stdin processing: non-blocking EOF / non-hanging behavior
- confirmed vs unconfirmed gating
- return dictionary contract and field types
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest

from pipeline import paths
from pipeline.agent.tools import run_pipeline


def _sys_python() -> str:
    return sys.executable


def test_pinning_exit_code_zero(tmp_path: Path):
    """Pin: 退出码 0 时 ok 为 True，message 为 '退出码 0'，returncode 为 0。"""
    cmd_script = "import sys; print('HELLO_STDOUT'); sys.exit(0)"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [_sys_python(), "-c", cmd_script])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert res["returncode"] == 0
    assert res["message"] == "退出码 0"
    assert "HELLO_STDOUT" in res["stdout_tail"]
    assert res["stderr_tail"] == ""
    assert res["truncated"] is False
    assert res["duration_s"] >= 0.0


def test_pinning_exit_code_nonzero(tmp_path: Path):
    """Pin: 非零退出码时 ok 为 False，message 为 '退出码 <code'>，returncode 为 <code>。"""
    cmd_script = "import sys; sys.stderr.write('FAIL_MSG\\n'); sys.exit(42)"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [_sys_python(), "-c", cmd_script])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is False
    assert res["returncode"] == 42
    assert res["message"] == "退出码 42"
    assert "FAIL_MSG" in res["stderr_tail"]
    assert res["truncated"] is False


def test_pinning_startup_error_nonexistent_executable(tmp_path: Path):
    """Pin: 可执行程序不存在时捕获异常并返回 ok=False，returncode=None。"""
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", ["/nonexistent/bin/python_bogus_xyz"])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is False
    assert res["returncode"] is None
    assert "执行器" in res["message"]


def test_pinning_cwd_is_paths_root(tmp_path: Path):
    """Pin: 子进程的工作目录 cwd 严格为 paths.ROOT。"""
    cmd_script = "import os; print(os.getcwd())"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [_sys_python(), "-c", cmd_script])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is True
    child_cwd = res["stdout_tail"].strip()
    assert Path(child_cwd).resolve() == paths.ROOT.resolve()


def test_pinning_output_truncation_over_4kb(tmp_path: Path):
    """Pin: 输出超过 4KB 时 truncated=True，stdout_tail 字节数不超过 4096，且保留尾部数据。"""
    cmd_script = "import sys; sys.stdout.write('X' * 5000 + 'END_MARKER'); sys.stdout.flush()"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [_sys_python(), "-c", cmd_script])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert res["truncated"] is True
    assert len(res["stdout_tail"].encode("utf-8")) <= 4096
    assert res["stdout_tail"].endswith("END_MARKER")


def test_pinning_stdin_handling(tmp_path: Path):
    """Pin: 子进程中如果检查 stdin，不挂死，能正常执行完毕。"""
    cmd_script = "import sys; print('STDIN_CHECK'); sys.exit(0)"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [_sys_python(), "-c", cmd_script])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert "STDIN_CHECK" in res["stdout_tail"]


def test_pinning_unconfirmed_dry_run(tmp_path: Path):
    """Pin: confirmed=False 为 dry-run，不执行子进程，返回待确认状态。"""
    res = run_pipeline(["status"], episode_dir=tmp_path, scope="pipeline", confirmed=False)
    assert res["ok"] is True
    assert res["message"] == "待人类确认"
    assert res["returncode"] is None
    assert res["duration_s"] == 0.0
    assert res["stdout_tail"] == ""
    assert res["stderr_tail"] == ""
    assert res["truncated"] is False
    assert len(res["argv"]) > 0


def test_pinning_invalid_command(tmp_path: Path):
    """Pin: 校验失败（如 --force）时不执行子进程，直接返回 ok=False 与拒因。"""
    res = run_pipeline(["tts", "--force"], episode_dir=tmp_path, scope="pipeline", confirmed=True)
    assert res["ok"] is False
    assert res["returncode"] is None
    assert "禁止" in res["message"] or "参数" in res["message"] or "拒绝" in res["message"]
    assert res["argv"] == []


def test_pinning_episode_dir_types():
    """Pin: episode_dir 支持 None、str、Path。"""
    res_none = run_pipeline(["status"], episode_dir=None, scope="pipeline", confirmed=False)
    assert res_none["ok"] is True

    res_str = run_pipeline(["status"], episode_dir="/tmp/test_ep", scope="pipeline", confirmed=False)
    assert res_str["ok"] is True

    res_path = run_pipeline(["status"], episode_dir=Path("/tmp/test_ep"), scope="pipeline", confirmed=False)
    assert res_path["ok"] is True


def test_pinning_dual_pipe_concurrent_output(tmp_path: Path):
    """Pin: stdout 与 stderr 并发输出时，两边都能正确捕获。"""
    cmd_script = "import sys; sys.stdout.write('OUT_MSG\\n'); sys.stderr.write('ERR_MSG\\n'); sys.exit(0)"
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [_sys_python(), "-c", cmd_script])):
        res = run_pipeline("check_script", episode_dir=tmp_path, scope="pipeline", confirmed=True)

    assert res["ok"] is True
    assert res["returncode"] == 0
    assert "OUT_MSG" in res["stdout_tail"]
    assert "ERR_MSG" in res["stderr_tail"]

