"""Tests for autonomous Google Colab boot (`pipeline.cloud_colab_boot` + `cloud up/connect`)."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess as sp
from pathlib import Path

import pytest

from pipeline import cloud, cloud_colab_boot


def _setup_cloud_env(monkeypatch, tmp_path: Path, local: dict | None = None):
    cfg = tmp_path / "config"
    data = tmp_path / "data"
    cfg.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    global_cfg = {
        "provider": "autodl",
        "ssh_host": "autodl",
        "fs_models": "/root/autodl-fs/models",
        "providers": {
            "autodl": {
                "transport": "ssh",
                "ssh_host": "autodl",
                "remote_root": "/root/anime-video-agent",
                "remote_models": "/root/autodl-tmp/models",
                "remote_python": "/root/autodl-tmp/venv/bin/python",
                "idle_shutdown_seconds": 300,
            },
            "colab": {
                "transport": "ssh",
                "ssh_host": "ava-colab",
                "notebook_url": "https://colab.research.google.com/github/songhuiming2007-coder/anime-video-agent/blob/main/tools/ava_colab_bootstrap.ipynb",
                "billing_unit": "cu",
                "cu_rate_fallback": 1.5,
                "cu_rates": {"T4": 1.3, "L4": 2.0},
                "remote_root": "/content/anime-video-agent",
                "remote_models": "/content/models",
                "remote_data_disk": "/content",
                "remote_log_dir": "/content/logs",
                "remote_python": "/content/venv/bin/python",
                "idle_shutdown_seconds": 300,
            },
        },
    }
    (cfg / "cloud.json").write_text(json.dumps(global_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg / "cloud.local.json").write_text(json.dumps(local or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(cloud.paths, "CONFIG", cfg)
    monkeypatch.setattr(cloud.paths, "DATA", data)
    monkeypatch.setattr(cloud.paths, "require_data", lambda: data)
    return cfg, data


def test_extract_colab_tunnel_host():
    ready_log = (
        "[*] 正在挂盘...\n"
        "AVA_COLAB_SSH_READY: root@alpha-beta-123.trycloudflare.com\n"
    )
    assert cloud_colab_boot.extract_colab_tunnel_host(ready_log) == "root@alpha-beta-123.trycloudflare.com"

    connect_log = (
        'cloudflared access ssh --hostname gamma-delta.trycloudflare.com\n'
    )
    assert cloud_colab_boot.extract_colab_tunnel_host(connect_log) == "root@gamma-delta.trycloudflare.com"
    assert cloud_colab_boot.extract_colab_tunnel_host("nothing ready yet") is None


def test_detect_colab_login_required():
    assert cloud_colab_boot.detect_colab_login_required(
        "https://accounts.google.com/v3/signin/identifier", ""
    )
    assert cloud_colab_boot.detect_colab_login_required(
        "https://colab.research.google.com/drive/123", "Please Sign in to continue"
    )
    assert cloud_colab_boot.detect_colab_login_required(
        "https://colab.research.google.com/github/a/b.ipynb", "File\nEdit\nRuntime\nSign in\nConnect"
    )
    assert not cloud_colab_boot.detect_colab_login_required(
        "https://colab.research.google.com/github/songhuiming2007-coder/anime-video-agent/blob/main/tools/ava_colab_bootstrap.ipynb",
        "RAM Disk Runtime Connected GPU",
    )


def test_resolve_colab_notebook_url():
    cfg_global = {"providers": {"colab": {"notebook_url": "https://colab.research.google.com/github/a/b.ipynb"}}}
    cfg_local = {"profiles": {"colab": {"notebook_url": "https://colab.research.google.com/drive/1AbCdEf"}}}

    assert (
        cloud_colab_boot.resolve_colab_notebook_url(cfg_global, cfg_local, "https://colab.research.google.com/drive/override")
        == "https://colab.research.google.com/drive/override"
    )
    assert (
        cloud_colab_boot.resolve_colab_notebook_url(cfg_global, cfg_local, None)
        == "https://colab.research.google.com/drive/1AbCdEf"
    )
    assert (
        cloud_colab_boot.resolve_colab_notebook_url(cfg_global, {}, None)
        == "https://colab.research.google.com/github/a/b.ipynb"
    )
    assert (
        cloud_colab_boot.resolve_colab_notebook_url({}, {}, None)
        == cloud_colab_boot.DEFAULT_COLAB_NOTEBOOK_URL
    )


def test_has_saved_browser_profile(tmp_path: Path):
    prof = tmp_path / "colab-browser-profile-firefox"
    assert not cloud_colab_boot.has_saved_browser_profile(prof)
    prof.mkdir()
    assert not cloud_colab_boot.has_saved_browser_profile(prof)
    (prof / "cookies.sqlite").write_bytes(b"sqlite-placeholder")
    assert cloud_colab_boot.has_saved_browser_profile(prof)


def test_cmd_connect_browser_login_only_supports_colab(monkeypatch, tmp_path: Path):
    _setup_cloud_env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as ei:
        cloud.cmd_connect(
            argparse.Namespace(
                provider="autodl",
                browser_login=True,
                login=None,
                drive_root=None,
                notebook_url=None,
                timeout=60,
            )
        )
    assert "--browser-login 仅支持 --provider colab" in str(ei.value)


def test_cmd_connect_browser_login_persists_notebook_url(monkeypatch, tmp_path: Path):
    cfg, data = _setup_cloud_env(monkeypatch, tmp_path, {"active_provider": "autodl"})
    called: dict[str, object] = {}

    def fake_login(nb_url: str, *, profile_dir: Path, timeout_s: int = 300, log_fn=print) -> str:
        called["nb_url"] = nb_url
        called["profile_dir"] = profile_dir
        called["timeout_s"] = timeout_s
        return nb_url

    monkeypatch.setattr(cloud_colab_boot, "interactive_browser_login", fake_login)
    custom_url = "https://colab.research.google.com/drive/my-saved-copy"
    rc = cloud.cmd_connect(
        argparse.Namespace(
            provider="colab",
            browser_login=True,
            login=None,
            drive_root=None,
            notebook_url=custom_url,
            timeout=120,
        )
    )
    assert rc == 0
    assert called["nb_url"] == custom_url
    assert called["profile_dir"] == data / "cloud" / "colab-browser-profile-firefox"
    assert called["timeout_s"] == 120
    local = json.loads((cfg / "cloud.local.json").read_text(encoding="utf-8"))
    assert local["active_provider"] == "autodl"
    assert local["profiles"]["colab"]["notebook_url"] == custom_url


def test_cmd_up_colab_auto_boot_refreshes_profile_and_ssh_config(monkeypatch, tmp_path: Path):
    cfg, data = _setup_cloud_env(
        monkeypatch,
        tmp_path,
        {
            "active_provider": "autodl",
            "profiles": {
                "colab": {
                    "server": "stale-old-tunnel.trycloudflare.com",
                    "port": 22,
                    "user": "root",
                    "proxy_type": "cloudflared",
                    "ssh_host": "ava-colab",
                }
            },
        },
    )
    prof_dir = data / "cloud" / "colab-browser-profile-firefox"
    prof_dir.mkdir(parents=True)
    (prof_dir / "cookies.sqlite").write_bytes(b"sqlite-placeholder")

    monkeypatch.setattr(cloud.shutil, "which", lambda name: "/opt/homebrew/bin/cloudflared" if name == "cloudflared" else None)

    reachable_hosts: list[str] = []

    def fake_is_ssh_reachable(host: str) -> bool:
        reachable_hosts.append(host)
        return len(reachable_hosts) >= 2

    monkeypatch.setattr(cloud, "is_ssh_reachable", fake_is_ssh_reachable)
    monkeypatch.setattr(
        cloud_colab_boot,
        "auto_boot_colab_ssh",
        lambda nb_url, *, profile_dir, timeout_s=300, headless=True, log_fn=print: "root@fresh-new-tunnel.trycloudflare.com",
    )

    def fake_ssh(cmd: str, *, host: str = "autodl", check: bool = True, capture_output: bool = True):
        if "nvidia-smi -L" in cmd:
            return sp.CompletedProcess(["ssh"], 0, "GPU 0: NVIDIA L4\n", "")
        if "nvidia-smi --query-gpu" in cmd:
            return sp.CompletedProcess(["ssh"], 0, "NVIDIA L4, 23034\n", "")
        if "watchdog" in cmd or "nohup" in cmd:
            return sp.CompletedProcess(["ssh"], 0, "4321\n", "")
        return sp.CompletedProcess(["ssh"], 0, "", "")

    monkeypatch.setattr(cloud, "_ssh", fake_ssh)
    monkeypatch.setattr(cloud, "forget_known_host", lambda conn: None)

    rc = cloud.cmd_up(
        argparse.Namespace(
            provider="colab",
            mode=None,
            rate=None,
            timeout=120,
            episode=None,
            no_auto_boot=False,
            notebook_url=None,
        )
    )
    assert rc == 0

    local = json.loads((cfg / "cloud.local.json").read_text(encoding="utf-8"))
    assert local["active_provider"] == "autodl"
    assert local["profiles"]["colab"]["server"] == "fresh-new-tunnel.trycloudflare.com"
    assert local["profiles"]["colab"]["proxy_type"] == "cloudflared"

    ssh_conf = (data / "cloud" / "ssh_config").read_text(encoding="utf-8")
    assert "Host ava-colab" in ssh_conf
    assert "HostName fresh-new-tunnel.trycloudflare.com" in ssh_conf
    assert "cloudflared access ssh --hostname %h" in ssh_conf


def test_cmd_up_colab_no_auto_boot_skips_browser(monkeypatch, tmp_path: Path):
    _setup_cloud_env(
        monkeypatch,
        tmp_path,
        {"profiles": {"colab": {"ssh_host": "ava-colab", "proxy_type": "cloudflared"}}},
    )
    monkeypatch.setattr(cloud.shutil, "which", lambda name: "/opt/homebrew/bin/cloudflared")
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: False)
    monkeypatch.setattr(cloud_colab_boot, "has_saved_browser_profile", lambda d: True)

    def boom(*args, **kwargs):
        raise AssertionError("auto_boot_colab_ssh should not be called when --no-auto-boot is set")

    monkeypatch.setattr(cloud_colab_boot, "auto_boot_colab_ssh", boom)
    monkeypatch.setattr(cloud.time, "sleep", lambda s: None)

    with pytest.raises(SystemExit) as ei:
        cloud.cmd_up(
            argparse.Namespace(
                provider="colab",
                mode=None,
                rate=None,
                timeout=0,
                episode=None,
                no_auto_boot=True,
                notebook_url=None,
            )
        )
    assert "未能成功连接" in str(ei.value)


def test_cmd_up_colab_auto_boot_failure_raises_clear_error(monkeypatch, tmp_path: Path):
    _, data = _setup_cloud_env(
        monkeypatch,
        tmp_path,
        {"profiles": {"colab": {"ssh_host": "ava-colab", "proxy_type": "cloudflared"}}},
    )
    prof_dir = data / "cloud" / "colab-browser-profile-firefox"
    prof_dir.mkdir(parents=True)
    (prof_dir / "cookies.sqlite").write_bytes(b"sqlite-placeholder")

    monkeypatch.setattr(cloud.shutil, "which", lambda name: "/opt/homebrew/bin/cloudflared")
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: False)

    def fake_fail(*args, **kwargs):
        raise RuntimeError("Colab 登录态失效，需要重新运行 --browser-login")

    monkeypatch.setattr(cloud_colab_boot, "auto_boot_colab_ssh", fake_fail)

    with pytest.raises(SystemExit) as ei:
        cloud.cmd_up(
            argparse.Namespace(
                provider="colab",
                mode=None,
                rate=None,
                timeout=60,
                episode=None,
                no_auto_boot=False,
                notebook_url=None,
            )
        )
    assert "Colab 自动拉起失败" in str(ei.value)


def test_bootstrap_notebook_single_code_cell_and_valid_python():
    nb_path = Path(__file__).resolve().parents[1] / "tools" / "ava_colab_bootstrap.ipynb"
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb["cells"]
    code_cells = [c for c in cells if c.get("cell_type") == "code"]
    assert len(code_cells) == 1, f"期望单格一键 Notebook 只有 1 个 code cell，实际 {len(code_cells)}"
    src = "".join(code_cells[0]["source"])
    assert "AVA_COLAB_SSH_READY:" in src
    assert "TASK_PROFILE" in src
    assert "/etc/ssh/sshd_config" in src
    ast.parse(src)
