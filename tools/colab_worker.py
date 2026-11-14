"""Colab 运行时 Worker 与 Sentinel 守护进程（`colab-drive` 队列模式 + `colab` SSH 释放哨兵）。

在 Google Colab Notebook Cell 2 中运行：
1. `--mode drive`: 轮询 `<drive_root>/jobs/`，校验任务白名单与参数，同步 `code/` 与 `staging/`，
   在 `/content/anime-video-agent` 执行任务并将产物写回 `output/`，持续上报心跳与硬件信息；
   收到 `control/shutdown.flag` 或空闲超时（且无未过期 `hold.json`）时自动调用 `runtime.unassign()`。
2. `--mode ssh`: 在 Colab SSH 模式下作为 Notebook 内核主进程常驻，监听 `/content/.ava_unassign`
   信号或空闲看门狗判定，触发 `google.colab.runtime.unassign()` 释放运行时、停止 CU 计费。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 允许直接 `python tools/colab_worker.py` 或 `python -m tools.colab_worker` 运行
REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from pipeline import cloud, cloud_drive


def trigger_colab_unassign() -> bool:
    """尝试调用 `google.colab.runtime.unassign()` 释放 Colab 运行时。"""
    try:
        from google.colab import runtime  # type: ignore[import-not-found]

        runtime.unassign()
        return True
    except Exception:
        return False


def _copy_dir_contents(src: Path, dst: Path) -> None:
    """递归复制目录（覆盖目标文件），忽略 __pycache__ 与 *.local.json。"""
    if not src.exists():
        return
    if src.is_file():
        if src.name.endswith(".local.json") or src.suffix == ".pyc":
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    for item in sorted(src.rglob("*")):
        if "__pycache__" in item.parts or item.name.endswith(".local.json") or item.suffix == ".pyc":
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def sync_drive_code_to_work(drive_root: Path, work_dir: Path) -> None:
    """把 Drive `code/`（pipeline、config 非 local、pyproject.toml）同步到本地工作目录。"""
    code_dir = drive_root / "code"
    if not code_dir.is_dir():
        return
    for rel_item in ("pipeline", "config", "tools"):
        src = code_dir / rel_item
        if src.is_dir():
            _copy_dir_contents(src, work_dir / rel_item)
    pyproj = code_dir / "pyproject.toml"
    if pyproj.is_file():
        shutil.copy2(pyproj, work_dir / "pyproject.toml")


def copy_staging_to_work(drive_root: Path, req: dict[str, Any], work_dir: Path) -> None:
    """将当前任务所需输入从 Drive `staging/` 复制到工作目录。"""
    staging = drive_root / "staging"
    ep_rel = str(req.get("ep_rel_path") or "").strip()
    if ep_rel:
        src_ep = staging / ep_rel
        if src_ep.is_dir():
            _copy_dir_contents(src_ep, work_dir / ep_rel)

    lib_pool = req.get("lib_pool")
    lib_keys = list(req.get("lib_keys") or [])
    if lib_pool and lib_keys:
        shots_rel, frames_rel, _ = cloud.lib_dirs()
        for key in lib_keys:
            shot_file = staging / shots_rel / f"{lib_pool}_{key}.json"
            if shot_file.is_file():
                _copy_dir_contents(shot_file, work_dir / shots_rel / f"{lib_pool}_{key}.json")
            cap_dir = staging / frames_rel / f"{lib_pool}_{key}_cap"
            if cap_dir.is_dir():
                _copy_dir_contents(cap_dir, work_dir / frames_rel / f"{lib_pool}_{key}_cap")


def copy_outputs_to_drive(req: dict[str, Any], job_dir: Path, work_dir: Path) -> list[str]:
    """将任务产物从工作目录复制到 `jobs/<job_id>/output/`，返回复制项清单。"""
    out_root = job_dir / "output"
    out_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    ep_rel = str(req.get("ep_rel_path") or "").strip()
    if ep_rel:
        ep_src = work_dir / ep_rel
        ep_dst = out_root / ep_rel
        for item in ("03-audio", "04-clips.json", "06-check.log", "04-patch"):
            src = ep_src / item
            if src.exists():
                _copy_dir_contents(src, ep_dst / item)
                copied.append(f"{ep_rel}/{item}")

    lib_pool = req.get("lib_pool")
    lib_keys = list(req.get("lib_keys") or [])
    if lib_pool and lib_keys:
        _, _, vindex_rel = cloud.lib_dirs()
        for key in lib_keys:
            fname = f"{lib_pool}_{key}.captions.json"
            src = work_dir / vindex_rel / fname
            if src.is_file():
                _copy_dir_contents(src, out_root / vindex_rel / fname)
                copied.append(f"{vindex_rel}/{fname}")
    return copied


def _parse_captions_extra_tokens(extra_args: str, ep_rel_path: str) -> list[str]:
    """校验并展开 ep 级 captions 额外参数（复用 cloud.EXTRA_ARG_RULES）。"""
    shots_dir = f"{ep_rel_path}/04-patch/shots"
    frames_dir = f"{ep_rel_path}/04-patch/frames"
    out_dir = f"{ep_rel_path}/04-patch/vindex"
    default_pool = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(ep_rel_path).name) + "-patch"
    pool = default_pool
    keys: list[str] = []
    extra_flags: list[str] = []

    tokens = shlex.split(extra_args) if extra_args else []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--"):
            if t in cloud.EXTRA_ARG_BOOLEAN:
                extra_flags.append(t)
                i += 1
            elif t in cloud.EXTRA_ARG_RULES:
                if i + 1 >= len(tokens):
                    raise ValueError(f"参数 {t} 缺少值")
                val = tokens[i + 1]
                if not re.fullmatch(cloud.EXTRA_ARG_RULES[t], val):
                    raise ValueError(f"参数 {t} 的值非法: {val!r}")
                if t == "--shots-dir":
                    shots_dir = val
                elif t == "--frames-dir":
                    frames_dir = val
                elif t == "--out-dir":
                    out_dir = val
                else:
                    extra_flags.extend([t, val])
                i += 2
            else:
                raise ValueError(f"不支持或未授权的远端参数旗标: {t}")
        else:
            if not re.fullmatch(r"^[A-Za-z0-9_-]+$", t):
                raise ValueError(f"非法池名或集键: {t!r}")
            if pool == default_pool and not keys:
                pool = t
            else:
                keys.append(t)
            i += 1
    if not keys:
        keys = ["SP01"]
    return [
        pool,
        *keys,
        "--shots-dir",
        shots_dir,
        "--frames-dir",
        frames_dir,
        "--out-dir",
        out_dir,
        *extra_flags,
    ]


def build_job_argv(req: dict[str, Any], python_exe: str) -> list[str]:
    """根据白名单任务请求构建安全的子进程 argv（不走 shell=True）。"""
    task = cloud.validate_task_whitelist(str(req.get("task") or ""))
    ep_rel = str(req.get("ep_rel_path") or "").strip()
    extra_args = str(req.get("extra_args") or "")

    if task == "probe":
        if not ep_rel:
            raise ValueError("probe 任务必须提供 ep_rel_path")
        code = (
            "import pathlib, torch; "
            f"out = pathlib.Path({ep_rel!r}) / '03-audio' / 'probe.log'; "
            "out.parent.mkdir(parents=True, exist_ok=True); "
            "msg = f'GPU Probe: torch={torch.__version__}, cuda={torch.cuda.is_available()}'; "
            "print(msg); "
            "out.write_text(msg + '\\n', encoding='utf-8')"
        )
        return [python_exe, "-c", code]

    if task == "tts":
        if not ep_rel:
            raise ValueError("tts 任务必须提供 ep_rel_path")
        validated = cloud.validate_extra_args(extra_args) if extra_args else ""
        return [
            python_exe,
            "-m",
            "pipeline.tts",
            ep_rel,
            "--config",
            "config/voice.cloud.json",
            *(shlex.split(validated) if validated else []),
        ]

    # captions（区分番池打标与当期补料打标）
    if req.get("lib_pool"):
        pool = str(req["lib_pool"])
        keys = cloud.validate_lib_target(pool, list(req.get("lib_keys") or []))
        tokens = shlex.split(extra_args) if extra_args else []
        flags: list[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in ("--batch", "--limit") and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                flags.extend([t, tokens[i + 1]])
                i += 2
            elif t == "--confirm-cost":
                flags.append(t)
                i += 1
            elif t in keys:
                i += 1
            else:
                raise ValueError(f"lib-captions 存在未授权参数: {t}")
        return [python_exe, "-m", "pipeline.vindex", "captions", pool, *keys, *flags]

    if not ep_rel:
        raise ValueError("captions 任务需提供 ep_rel_path 或 lib_pool")
    cap_args = _parse_captions_extra_tokens(extra_args, ep_rel)
    return [python_exe, "-m", "pipeline.vindex", "captions", *cap_args]


def probe_runtime_snapshot(
    models_root: Path,
    cfg_global: dict[str, Any],
    work_dir: Path,
    *,
    boot_epoch: float,
    active_job: str | None = None,
) -> dict[str, Any]:
    """采集当前 Worker 硬件与模型状态，用于写入 `heartbeat/worker.json`。"""
    gpu_name: str | None = None
    gpu_mem_mib: float | None = None
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            gpu_name, gpu_mem_mib = cloud.parse_gpu_query(res.stdout)
    except Exception:
        pass

    torch_ver = None
    torch_cuda = False
    try:
        import torch  # type: ignore[import-not-found]

        torch_ver = str(torch.__version__)
        torch_cuda = bool(torch.cuda.is_available())
    except Exception:
        pass

    free_gb = None
    try:
        usage = shutil.disk_usage(work_dir if work_dir.exists() else Path("/"))
        free_gb = int(usage.free // (1024 ** 3))
    except OSError:
        pass

    models_status: dict[str, str] = {}
    for m_id, spec in (cfg_global.get("models") or {}).items():
        m_dir = spec.get("dir", m_id)
        p = models_root / m_dir
        models_status[m_id] = "ok" if (p.is_dir() and any(p.iterdir())) else "missing"

    return {
        "ts": time.time(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "boot_epoch": boot_epoch,
        "gpu": gpu_name,
        "gpu_mem_mib": gpu_mem_mib,
        "torch_version": torch_ver,
        "torch_cuda": torch_cuda,
        "disk_free_gb": free_gb,
        "active_job": active_job,
        "models": models_status,
    }


def write_heartbeat(drive_root: Path, snapshot: dict[str, Any]) -> None:
    """将 Worker 心跳原子写入 `<drive_root>/heartbeat/worker.json`。"""
    hb_path = drive_root / cloud_drive.HEARTBEAT_REL
    hb_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = hb_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(hb_path)


def process_one_job(
    drive_root: Path,
    job_dir: Path,
    work_dir: Path,
    python_exe: str,
    models_root: Path,
) -> str:
    """接单并执行单个 Drive 队列任务；返回最终状态 (`succeeded` / `failed`)。"""
    req_file = job_dir / "request.json"
    st_file = job_dir / "status.json"
    log_file = job_dir / "worker.log"
    req = json.loads(req_file.read_text(encoding="utf-8"))
    job_id = str(req.get("job_id") or job_dir.name)

    def _set_status(state: str, **extra: Any) -> None:
        payload = {
            "job_id": job_id,
            "state": state,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            **extra,
        }
        tmp = st_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(st_file)

    _set_status("running", started_at=datetime.now().isoformat(timespec="seconds"))
    sync_drive_code_to_work(drive_root, work_dir)
    copy_staging_to_work(drive_root, req, work_dir)

    try:
        argv = build_job_argv(req, python_exe)
    except Exception as exc:
        log_file.write_text(f"FAIL 参数校验未通过: {exc}\n", encoding="utf-8")
        _set_status("failed", error=str(exc), returncode=2)
        return "failed"

    env = {
        **os.environ,
        "AVA_CLOUD_PROVIDER": "colab-drive",
        "AVA_REMOTE_MODELS": str(models_root),
        "PYTHONUNBUFFERED": "1",
    }
    with log_file.open("w", encoding="utf-8") as lf:
        lf.write(f"[worker] 启动任务 {job_id}: {' '.join(shlex.quote(a) for a in argv)}\n")
        lf.flush()
        proc = subprocess.run(
            argv,
            cwd=str(work_dir),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            text=True,
        )
    copied = copy_outputs_to_drive(req, job_dir, work_dir)
    final_state = "succeeded" if proc.returncode == 0 else "failed"
    _set_status(final_state, returncode=proc.returncode, outputs=copied)
    return final_state


def should_release_drive(
    drive_root: Path,
    *,
    last_activity_epoch: float,
    idle_seconds: int,
    now_epoch: float | None = None,
) -> tuple[bool, str]:
    """判定 Drive Worker 是否应结束循环并释放运行时（纯时间判定）。"""
    if (drive_root / cloud_drive.SHUTDOWN_REL).is_file():
        return True, "shutdown.flag"
    now = time.time() if now_epoch is None else now_epoch
    hold = cloud_drive.read_drive_hold(drive_root, now_epoch=now)
    if hold is not None and now < hold[1]:
        return False, "hold"
    if idle_seconds > 0 and (now - last_activity_epoch) >= idle_seconds:
        return True, f"idle>={idle_seconds}s"
    return False, "active"


def run_drive_loop(
    drive_root: Path,
    work_dir: Path,
    python_exe: str,
    models_root: Path,
    *,
    idle_seconds: int = cloud.DEFAULT_IDLE_SHUTDOWN_SECONDS,
    poll_interval: float = 5.0,
    unassign_on_exit: bool = True,
    max_polls: int | None = None,
) -> int:
    """Drive 队列主循环：上报心跳、处理 pending 任务、按空闲/关闭信号退出并释放运行时。"""
    cloud_drive.ensure_drive_layout(drive_root)
    flag_file = drive_root / cloud_drive.SHUTDOWN_REL
    if flag_file.exists():
        flag_file.unlink()

    boot_epoch = time.time()
    last_activity = boot_epoch
    cfg_global, _ = cloud.load_cloud_config(work_dir / "config")
    polls = 0

    while True:
        polls += 1
        jobs = cloud_drive.list_drive_jobs(drive_root)
        pending = [j for j in reversed(jobs) if j["status"].get("state") == "pending"]
        if pending:
            job = pending[0]
            snap = probe_runtime_snapshot(
                models_root, cfg_global, work_dir, boot_epoch=boot_epoch, active_job=job["job_id"]
            )
            write_heartbeat(drive_root, snap)
            process_one_job(drive_root, job["dir"], work_dir, python_exe, models_root)
            last_activity = time.time()

        snap = probe_runtime_snapshot(models_root, cfg_global, work_dir, boot_epoch=boot_epoch)
        write_heartbeat(drive_root, snap)

        release, reason = should_release_drive(
            drive_root, last_activity_epoch=last_activity, idle_seconds=idle_seconds
        )
        if release and not cloud_drive.active_drive_jobs(drive_root):
            print(f"[worker] 收到退出条件 ({reason})，结束循环。")
            if unassign_on_exit:
                trigger_colab_unassign()
            return 0

        if max_polls is not None and polls >= max_polls:
            return 0
        time.sleep(poll_interval)


def run_ssh_sentinel(
    *,
    unassign_file: Path = Path(cloud.COLAB_UNASSIGN_PATH),
    heartbeat_file: Path = Path(cloud.COLAB_HEARTBEAT_PATH),
    hold_file: Path = Path(cloud.COLAB_HOLD_PATH),
    idle_seconds: int = cloud.DEFAULT_IDLE_SHUTDOWN_SECONDS,
    poll_interval: float = 10.0,
    unassign_on_exit: bool = True,
    max_polls: int | None = None,
) -> int:
    """Colab SSH 模式下在 Notebook 内核中运行的释放哨兵：检测 unassign 信号或空闲超时并调 runtime.unassign()。"""
    if unassign_file.exists():
        unassign_file.unlink()
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.touch()

    polls = 0
    while True:
        polls += 1
        if unassign_file.exists():
            print(f"[sentinel] 检测到 {unassign_file}，释放 Colab 运行时。")
            if unassign_on_exit:
                trigger_colab_unassign()
            return 0

        # tmux 有 ava-* 活跃任务时刷新心跳
        has_tmux = subprocess.run(
            ["bash", "-lc", "tmux ls 2>/dev/null | grep -q '^ava-'"],
            capture_output=True,
        ).returncode == 0
        if has_tmux:
            heartbeat_file.touch()
        elif hold_file.is_file():
            try:
                parts = hold_file.read_text(encoding="utf-8").split()
                if len(parts) >= 2 and time.time() < int(parts[1]):
                    heartbeat_file.touch()
            except Exception:
                pass

        if idle_seconds > 0 and heartbeat_file.exists():
            idle = time.time() - heartbeat_file.stat().st_mtime
            if idle >= idle_seconds and not has_tmux:
                print(f"[sentinel] 空闲 {int(idle)}s >= {idle_seconds}s 且无活跃 ava-* 任务，释放运行时。")
                if unassign_on_exit:
                    trigger_colab_unassign()
                return 0

        if max_polls is not None and polls >= max_polls:
            return 0
        time.sleep(poll_interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="AVA Colab Worker / Sentinel")
    ap.add_argument("--mode", choices=["drive", "ssh"], required=True, help="运行模式：drive 共享队列或 ssh 哨兵")
    ap.add_argument("--drive-root", type=Path, default=Path("/content/drive/MyDrive/ava_cloud"))
    ap.add_argument("--work-dir", type=Path, default=REPO_DIR)
    ap.add_argument("--python", type=str, default=cloud.COLAB_REMOTE_PYTHON)
    ap.add_argument("--models-root", type=Path, default=Path("/content/models"))
    ap.add_argument("--idle-seconds", type=int, default=cloud.DEFAULT_IDLE_SHUTDOWN_SECONDS)
    ap.add_argument("--poll-interval", type=float, default=5.0)
    ap.add_argument("--no-unassign", action="store_true", help="调试用：退出时不调用 runtime.unassign()")
    a = ap.parse_args()

    if a.mode == "drive":
        return run_drive_loop(
            a.drive_root,
            a.work_dir,
            a.python,
            a.models_root,
            idle_seconds=a.idle_seconds,
            poll_interval=a.poll_interval,
            unassign_on_exit=not a.no_unassign,
        )
    return run_ssh_sentinel(
        idle_seconds=a.idle_seconds,
        poll_interval=max(5.0, a.poll_interval),
        unassign_on_exit=not a.no_unassign,
    )


if __name__ == "__main__":
    raise SystemExit(main())
