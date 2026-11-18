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
    assert "ControlPath ~/.ssh/cm-%C" in ssh_conf


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


def test_colab_browser_profile_dir_and_lock(tmp_path: Path):
    import threading
    import time

    default_dir = cloud_colab_boot.colab_browser_profile_dir(tmp_path)
    assert default_dir == tmp_path / "cloud" / "colab-browser-profile-firefox"
    acct2 = cloud_colab_boot.colab_browser_profile_dir(tmp_path, "acct2")
    assert acct2 == tmp_path / "cloud" / "colab-browser-profile-firefox-acct2"

    with pytest.raises(ValueError, match="非法浏览器档案名"):
        cloud_colab_boot.colab_browser_profile_dir(tmp_path, "../escape")

    # 同进程不同文件描述符（os.open）同样受 flock 互斥约束，验证非阻塞与排队等待
    with cloud_colab_boot.colab_browser_lock(default_dir, blocking=False):
        with pytest.raises(RuntimeError, match="正被另一进程占用"):
            with cloud_colab_boot.colab_browser_lock(default_dir, blocking=False):
                pass
        with pytest.raises(RuntimeError, match="超时"):
            with cloud_colab_boot.colab_browser_lock(default_dir, timeout_s=0.12, poll_s=0.05, blocking=True, log_fn=lambda _: None):
                pass

    entered = threading.Event()
    release = threading.Event()

    def _holder():
        with cloud_colab_boot.colab_browser_lock(default_dir, blocking=True, log_fn=lambda _: None):
            entered.set()
            release.wait(timeout=2.0)

    t = threading.Thread(target=_holder)
    t.start()
    assert entered.wait(timeout=1.0)
    timer = threading.Timer(0.15, release.set)
    timer.start()
    try:
        logs: list[str] = []
        t0 = time.monotonic()
        with cloud_colab_boot.colab_browser_lock(default_dir, timeout_s=2.0, poll_s=0.05, blocking=True, log_fn=logs.append):
            assert time.monotonic() - t0 >= 0.1
        assert any("排队等待锁释放" in ln for ln in logs)
    finally:
        release.set()
        timer.cancel()
        t.join(timeout=2.0)


def test_resolve_colab_notebook_url_and_extract_tunnel_with_exclusions():
    shared_drive_url = "https://colab.research.google.com/drive/shared123"
    cfg_global = {"providers": {"colab": {"notebook_url": cloud_colab_boot.DEFAULT_COLAB_NOTEBOOK_URL}}}
    cfg_local = {
        "profiles": {
            "colab": {
                "notebook_url": shared_drive_url,
                "slots": {
                    "captions": {"notebook_url": "https://colab.research.google.com/drive/cap456"},
                },
            }
        }
    }
    assert cloud_colab_boot.resolve_colab_notebook_url(cfg_global, cfg_local, slot="default", default_slot_busy=False) == shared_drive_url
    assert cloud_colab_boot.resolve_colab_notebook_url(cfg_global, cfg_local, slot="captions", default_slot_busy=True) == "https://colab.research.google.com/drive/cap456"
    # default 正忙且并发槽位无独立 URL：绝不打开共享 /drive/ 副本以免把运行中内核接走
    assert cloud_colab_boot.resolve_colab_notebook_url(cfg_global, cfg_local, slot="tts", default_slot_busy=True) == cloud_colab_boot.DEFAULT_COLAB_NOTEBOOK_URL

    text = (
        "log: https://other-bare.trycloudflare.com\n"
        "AVA_COLAB_SSH_READY: root@busy-old.trycloudflare.com\n"
        "AVA_COLAB_SSH_READY: root@new-slot.trycloudflare.com\n"
    )
    assert cloud_colab_boot.extract_colab_tunnel_host(
        text, exclude_hosts={"busy-old.trycloudflare.com"}, ready_only=True
    ) == "root@new-slot.trycloudflare.com"
    assert cloud_colab_boot.extract_colab_tunnel_host(
        "AVA_COLAB_SSH_READY: root@busy-old.trycloudflare.com\nhttps://other-bare.trycloudflare.com",
        exclude_hosts={"busy-old.trycloudflare.com"},
        ready_only=True,
    ) is None


def test_auto_boot_ignores_excluded_and_does_not_interrupt_running_cell(monkeypatch, tmp_path: Path):
    import sys
    import types

    data_dir = tmp_path / "data"
    prof_dir = cloud_colab_boot.colab_browser_profile_dir(data_dir)
    (prof_dir / "cookies.sqlite").write_bytes(b"cookie")

    class _FakeLoc:
        def __init__(self, *, count_val: int = 0, visible: bool = False):
            self._c = count_val
            self._v = visible
            self.clicked = 0

        @property
        def first(self):
            return self

        def count(self):
            return self._c

        def is_visible(self):
            return self._v

        def click(self, timeout=0):
            self.clicked += 1

    # 1. _trigger_colab_run_all：检测到 Interrupt execution 时返回 already-running，绝不点停止/重跑
    interrupt_page = type("_Page", (), {
        "locator": lambda self, sel: _FakeLoc(count_val=1 if "Interrupt execution" in sel else 0, visible=True),
    })()
    assert cloud_colab_boot._trigger_colab_run_all(interrupt_page) == "already-running"

    # 2. auto_boot_colab_ssh：页面上出现 busy-slot + 执行态时也不会抓错旧隧道，只返回新信标
    class _FakePage:
        def __init__(self):
            self.url = cloud_colab_boot.DEFAULT_COLAB_NOTEBOOK_URL
            self.main_frame = object()
            self.frames = []
            self._step = 0
            self.keyboard = types.SimpleNamespace(press=lambda k: None)

        def goto(self, url, wait_until=None, timeout=0):
            self.url = url

        def locator(self, sel):
            if "Interrupt execution" in sel:
                return _FakeLoc(count_val=0)
            if "colab-run-button" in sel:
                return _FakeLoc(count_val=1, visible=True)
            return _FakeLoc(count_val=0)

        def get_by_role(self, role, name=None):
            return _FakeLoc(count_val=0)

        def inner_text(self, sel, timeout=0):
            self._step += 1
            if self._step <= 2:
                return "AVA_COLAB_SSH_READY: root@stale-initial.trycloudflare.com"
            return (
                "Executing...\n"
                "AVA_COLAB_SSH_READY: root@stale-initial.trycloudflare.com\n"
                "AVA_COLAB_SSH_READY: root@busy-other.trycloudflare.com\n"
                "AVA_COLAB_SSH_READY: root@fresh-tts.trycloudflare.com\n"
            )

        def screenshot(self, path=None, full_page=False):
            pass

    fake_page = _FakePage()

    class _FakeCtx:
        pages = [fake_page]
        def new_page(self):
            return fake_page
        def close(self):
            pass

    class _FakeSyncPW:
        def __enter__(self):
            return object()
        def __exit__(self, *a):
            return False

    fake_pw_mod = types.ModuleType("playwright.sync_api")
    fake_pw_mod.sync_playwright = lambda: _FakeSyncPW()
    fake_pw_pkg = types.ModuleType("playwright")
    fake_pw_pkg.sync_api = fake_pw_mod
    monkeypatch.setitem(sys.modules, "playwright", fake_pw_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_pw_mod)
    monkeypatch.setattr(cloud_colab_boot, "_launch_persistent_context", lambda pw, pdir, headless=True: _FakeCtx())
    monkeypatch.setattr(cloud_colab_boot.time, "sleep", lambda s: None)

    got = cloud_colab_boot.auto_boot_colab_ssh(
        cloud_colab_boot.DEFAULT_COLAB_NOTEBOOK_URL,
        profile_dir=prof_dir,
        timeout_s=30,
        slot="tts",
        exclude_hosts={"busy-other.trycloudflare.com"},
        log_fn=lambda _: None,
    )
    assert got == "root@fresh-tts.trycloudflare.com"


def test_colab_worker_venv_cache_integrity_and_atomic_restore(tmp_path: Path):
    from tools import colab_worker

    content_root = tmp_path / "content"
    venv_src = tmp_path / "build" / "venv"
    (venv_src / "bin").mkdir(parents=True)
    (venv_src / "bin" / "python").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (venv_src / "marker.txt").write_text("ava-venv", encoding="utf-8")

    tar_path = tmp_path / "drive" / "ava_venv_test.tar.gz"
    assert colab_worker.publish_colab_venv_cache(venv_src, tar_path, scratch_dir=tmp_path / "scratch") is True
    assert tar_path.is_file() and Path(f"{tar_path}.sha256").is_file()
    assert colab_worker.verify_venv_tarball(tar_path)[0] is True
    assert colab_worker.restore_colab_venv_cache(tar_path, content_root=content_root, wait_sync_s=0.0) is True
    assert (content_root / "venv" / "bin" / "python").is_file()

    # 模拟并发另一实例尚在写入/Drive 只同步了半截：大小或哈希不符时拒绝恢复且不留残缺 venv
    bad_content = tmp_path / "bad_content"
    orig_bytes = tar_path.read_bytes()
    tar_path.write_bytes(orig_bytes[:-10])
    ok_trunc, reason_trunc = colab_worker.verify_venv_tarball(tar_path, wait_sync_s=0.0)
    assert ok_trunc is False and "大小不符" in reason_trunc
    assert colab_worker.restore_colab_venv_cache(tar_path, content_root=bad_content, wait_sync_s=0.0) is False
    assert not (bad_content / "venv").exists()

    # 同等长度但内容损坏：SHA256 校验必须拦下
    tar_path.write_bytes(bytes([orig_bytes[0] ^ 0xFF]) + orig_bytes[1:])
    ok_sha, reason_sha = colab_worker.verify_venv_tarball(tar_path, wait_sync_s=0.0)
    assert ok_sha is False and "SHA256" in reason_sha

