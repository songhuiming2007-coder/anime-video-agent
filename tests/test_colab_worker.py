"""test_colab_worker.py：Colab Drive Worker 与 SSH Release Sentinel 单元测试。"""

import json
import sys
import time
from pathlib import Path

import pytest

from pipeline import cloud_drive
from tools import colab_worker


def test_build_job_argv_白名单与参数校验():
    probe_argv = colab_worker.build_job_argv(
        {"task": "probe", "ep_rel_path": "data/episodes/ep01"},
        "/content/venv/bin/python",
    )
    assert probe_argv[:2] == ["/content/venv/bin/python", "-c"]

    tts_argv = colab_worker.build_job_argv(
        {"task": "tts", "ep_rel_path": "data/episodes/ep01", "extra_args": "--redo 3,7"},
        "/content/venv/bin/python",
    )
    assert tts_argv == [
        "/content/venv/bin/python", "-m", "pipeline.tts", "data/episodes/ep01",
        "--config", "config/voice.cloud.json", "--redo", "3,7",
    ]

    lib_argv = colab_worker.build_job_argv(
        {"task": "captions", "lib_pool": "demo", "lib_keys": ["SP01", "SP02"], "extra_args": "--limit 10"},
        "/content/venv/bin/python",
    )
    assert lib_argv == [
        "/content/venv/bin/python", "-m", "pipeline.vindex", "captions",
        "demo", "SP01", "SP02", "--limit", "10",
    ]

    with pytest.raises(SystemExit, match="白名单"):
        colab_worker.build_job_argv({"task": "bash", "ep_rel_path": "x"}, "python3")

    with pytest.raises(ValueError, match="不支持或未授权的远端参数旗标"):
        colab_worker.build_job_argv(
            {"task": "tts", "ep_rel_path": "data/episodes/ep01", "extra_args": "--force"},
            "python3",
        )


def test_process_one_job与should_release_drive(tmp_path):
    drive_root = tmp_path / "ava_cloud"
    work_dir = tmp_path / "work"
    models_root = tmp_path / "models"
    work_dir.mkdir()
    models_root.mkdir()
    cloud_drive.ensure_drive_layout(drive_root)
    (drive_root / "code" / "pipeline").mkdir(parents=True, exist_ok=True)
    (drive_root / "code" / "pipeline" / "__init__.py").write_text("", encoding="utf-8")

    job_dir = cloud_drive.submit_drive_job(
        drive_root,
        job_id="ep_probe_test1",
        task="probe",
        ep_rel_path="data/episodes/ep-test",
    )

    # 覆盖 build_job_argv 执行一个无 GPU 依赖的轻量脚本，真实验证子进程执行、日志与输出回拷
    orig = colab_worker.build_job_argv
    try:
        def _fake_argv(req, py_exe):
            code = (
                "import pathlib; "
                "p = pathlib.Path('data/episodes/ep-test/03-audio/probe.log'); "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text('GPU Probe: ok\\n', encoding='utf-8'); "
                "print('probe-done')"
            )
            return [py_exe, "-c", code]

        colab_worker.build_job_argv = _fake_argv  # type: ignore[assignment]
        state = colab_worker.process_one_job(drive_root, job_dir, work_dir, sys.executable, models_root)
    finally:
        colab_worker.build_job_argv = orig  # type: ignore[assignment]

    assert state == "succeeded"
    assert "probe-done" in (job_dir / "worker.log").read_text(encoding="utf-8")
    assert (job_dir / "output" / "data" / "episodes" / "ep-test" / "03-audio" / "probe.log").is_file()

    # hold 未过期时不释放；shutdown.flag 一旦存在立刻释放
    now = time.time()
    cloud_drive.write_drive_hold(drive_root, 3600, now_epoch=now)
    rel, reason = colab_worker.should_release_drive(drive_root, last_activity_epoch=now - 1000, idle_seconds=300, now_epoch=now)
    assert not rel and reason == "hold"

    cloud_drive.clear_drive_hold(drive_root)
    rel_idle, reason_idle = colab_worker.should_release_drive(drive_root, last_activity_epoch=now - 350, idle_seconds=300, now_epoch=now)
    assert rel_idle and "idle" in reason_idle

    cloud_drive.request_drive_shutdown(drive_root)
    rel_flag, reason_flag = colab_worker.should_release_drive(drive_root, last_activity_epoch=now, idle_seconds=300, now_epoch=now)
    assert rel_flag and reason_flag == "shutdown.flag"


def test_find_matching_flash_attn_wheel_精确匹配与版本排序():
    assets = [
        {
            "name": "flash_attn-2.7.4.post1+cu12torch2.8cxx11abiFALSE-cp312-cp312-linux_x86_64.whl",
            "browser_download_url": "https://example.com/2.7.4.post1.whl",
        },
        {
            "name": "flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp312-cp312-linux_x86_64.whl",
            "browser_download_url": "https://example.com/2.8.3.whl",
        },
        {
            "name": "flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl",
            "browser_download_url": "https://example.com/abi_true.whl",
        },
    ]
    assert (
        colab_worker.find_matching_flash_attn_wheel(
            assets, cu_tag="12", torch_mm="2.8", cxx11_abi="FALSE", py_tag="cp312"
        )
        == "https://example.com/2.8.3.whl"
    )
    # 不匹配（如 torch 2.15 + cu13）必须返回 None，不跨版本瞎猜
    assert (
        colab_worker.find_matching_flash_attn_wheel(
            assets, cu_tag="13", torch_mm="2.15", cxx11_abi="FALSE", py_tag="cp312"
        )
        is None
    )


def test_try_install_flash_attn_wheel_精确轮子走only_binary否则秒级跳过(monkeypatch, tmp_path):
    import io
    import subprocess
    import urllib.request

    venv_py = tmp_path / "venv" / "bin" / "python"
    cmds: list[list[str]] = []

    def make_fake_run(probe_info: dict):
        def _fake_run(cmd, **_kw):
            cmds.append(cmd)
            if len(cmd) >= 3 and cmd[1] == "-c" and "has_flash" in cmd[2]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(probe_info) + "\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        return _fake_run

    release_payload = json.dumps({
        "assets": [
            {
                "name": "flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp312-cp312-linux_x86_64.whl",
                "browser_download_url": "https://example.com/fa-2.8.3.whl",
            }
        ]
    }).encode("utf-8")

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=8.0: _FakeResp(release_payload))

    # 1. 匹配到轮子（L4 + CUDA 12.8 + torch 2.8）→ --only-binary :all: 安装并校验
    monkeypatch.setattr(
        subprocess,
        "run",
        make_fake_run({
            "has_flash": False,
            "cuda": "12.8",
            "torch": "2.8.0+cu128",
            "abi": "FALSE",
            "py": "cp312",
            "plat": "linux_x86_64",
            "sm_major": 8,
        }),
    )
    assert colab_worker.try_install_flash_attn_wheel(venv_py) is True
    uv_inst = [c for c in cmds if c[:3] == ["uv", "pip", "install"]]
    assert len(uv_inst) == 1
    assert "--only-binary" in uv_inst[0] and ":all:" in uv_inst[0]
    assert "https://example.com/fa-2.8.3.whl" in uv_inst[0]

    # 2. torch 2.15 + cu132 无匹配轮子 → 直接跳过，绝不调 uv install
    cmds.clear()
    monkeypatch.setattr(
        subprocess,
        "run",
        make_fake_run({
            "has_flash": False,
            "cuda": "13.2",
            "torch": "2.15.0+cu132",
            "abi": "FALSE",
            "py": "cp312",
            "plat": "linux_x86_64",
            "sm_major": 8,
        }),
    )
    assert colab_worker.try_install_flash_attn_wheel(venv_py) is False
    assert not [c for c in cmds if c[:3] == ["uv", "pip", "install"]]
