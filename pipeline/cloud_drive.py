"""Google Drive 共享队列传输层（`colab-drive` 后端）。

当本机与 Colab 之间不走 SSH 隧道时，通过本地可访问的 Google Drive 同步目录
（如 Google Drive for Desktop 挂载盘或本地测试目录）与 Colab Worker 交换代码、
任务请求、日志与产物。

目录布局（`<drive_root>/`）：
  code/                 同步的 pipeline/ config/ pyproject.toml 与 code_manifest.json
  staging/<rel_path>/   push / lib-push 暂存的当期或番池输入
  jobs/<job_id>/        request.json, status.json, worker.log, output/
  heartbeat/worker.json Colab worker 周期上报的心跳、GPU、显存、磁盘与活跃任务
  control/hold.json     保持标记（对应 cloud hold / release）
  control/shutdown.flag 释放运行时信号（对应 cloud down）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

DRIVE_QUEUE_DIRS = ("code", "staging", "jobs", "heartbeat", "control")
HEARTBEAT_REL = Path("heartbeat") / "worker.json"
HOLD_REL = Path("control") / "hold.json"
SHUTDOWN_REL = Path("control") / "shutdown.flag"
CODE_MANIFEST_REL = Path("code") / "code_manifest.json"

#: 2 分钟未刷新心跳即视为 Worker 离线/已断开（吸收 Drive 客户端同步延迟）
HEARTBEAT_STALE_SECONDS = 120

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

_SYNC_EXCLUDE_NAMES = {".git", "__pycache__", ".DS_Store", "attic"}


def detect_drive_root(subpath: str = "ava_cloud") -> Path | None:
    """尝试探测 macOS Google Drive for Desktop 的本地挂载目录；未挂载返回 None。"""
    cloud_storage = Path.home() / "Library" / "CloudStorage"
    if not cloud_storage.is_dir():
        return None
    for entry in sorted(cloud_storage.iterdir()):
        if not entry.name.startswith("GoogleDrive-") or not entry.is_dir():
            continue
        my_drive = entry / "My Drive"
        if my_drive.is_dir():
            return my_drive / subpath
    return None


def validate_drive_root(raw: str) -> Path:
    """校验并展开用户传入的 Drive 同步目录路径。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Drive 同步目录不能为空")
    expanded = Path(os.path.expanduser(text))
    if not expanded.is_absolute():
        raise ValueError(f"Drive 同步目录必须是绝对路径（可用 ~ 开头），实际为: {raw!r}")
    if expanded.exists() and not expanded.is_dir():
        raise ValueError(f"Drive 路径存在但不是目录: {expanded}")
    if not expanded.exists() and not expanded.parent.is_dir():
        raise ValueError(f"Drive 上级目录不存在（请先确认 Google Drive 客户端已挂载）: {expanded.parent}")
    return expanded


def ensure_drive_layout(drive_root: Path) -> None:
    """确保共享队列目录结构完整（幂等）。"""
    drive_root.mkdir(parents=True, exist_ok=True)
    for sub in DRIVE_QUEUE_DIRS:
        (drive_root / sub).mkdir(parents=True, exist_ok=True)


def is_drive_reachable(drive_root: Path | None) -> bool:
    """本地 Drive 目录存在且可读写。"""
    if drive_root is None:
        return False
    try:
        return drive_root.is_dir() and os.access(drive_root, os.R_OK | os.W_OK)
    except OSError:
        return False


def _copy_tree_filtered(src: Path, dst: Path, *, exclude_attic: bool = True) -> list[str]:
    """递归复制文件到 dst，过滤本地机密（*.local.json）与缓存文件，返回复制的相对路径列表。"""
    copied: list[str] = []
    if src.is_file():
        if src.name.endswith(".local.json") or src.name in _SYNC_EXCLUDE_NAMES or src.suffix == ".pyc":
            return copied
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(src.name)
        return copied

    if not src.is_dir():
        return copied

    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        parts = set(rel.parts)
        if parts & (_SYNC_EXCLUDE_NAMES if exclude_attic else (_SYNC_EXCLUDE_NAMES - {"attic"})):
            continue
        if item.name.endswith(".local.json") or item.suffix == ".pyc":
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(rel.as_posix())
    return copied


def sync_code_to_drive(drive_root: Path, repo_root: Path, manifest: dict[str, str]) -> int:
    """同步代码清单列出的文件（pipeline/、config/ 非 local、pyproject.toml）到 Drive `code/`。"""
    ensure_drive_layout(drive_root)
    code_dir = drive_root / "code"
    synced = 0
    for rel_posix in sorted(manifest):
        src = repo_root / rel_posix
        if not src.is_file():
            continue
        dst = code_dir / rel_posix
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        synced += 1
    paths.atomic_write(
        drive_root / CODE_MANIFEST_REL,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return synced


def read_drive_code_manifest(drive_root: Path) -> dict[str, str] | str:
    """读取 Drive `code/code_manifest.json`；缺失或损坏返回说明字符串。"""
    mf = drive_root / CODE_MANIFEST_REL
    if not mf.is_file():
        return f"Drive 里还没有 {CODE_MANIFEST_REL}（先 cloud sync-code）"
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Drive 代码清单损坏: {exc}"
    if not isinstance(data, dict) or not data:
        return f"Drive 代码清单为空（先 cloud sync-code）"
    return {str(k): str(v) for k, v in data.items()}


def stage_episode_inputs(
    drive_root: Path,
    ep_dir: Path,
    rel_path: str,
    sync_items: list[str],
    repo_root: Path,
    *,
    with_audio: bool = False,
) -> list[str]:
    """把当期上行输入复制到 `<drive_root>/staging/<rel_path>/`（config 同步进 `code/config/`）。"""
    ensure_drive_layout(drive_root)
    staged: list[str] = []
    items = list(sync_items)
    if with_audio and "03-audio/" not in items:
        items.append("03-audio/")

    staging_ep = drive_root / "staging" / rel_path
    for item in items:
        is_config = item.startswith("config")
        item_clean = item.rstrip("/")
        src = repo_root / item_clean if is_config else ep_dir / item_clean
        if not src.exists():
            continue
        dst = (drive_root / "code" / item_clean) if is_config else (staging_ep / item_clean)
        if src.is_dir():
            files = _copy_tree_filtered(src, dst, exclude_attic=True)
            staged.extend(f"{item_clean}/{f}" for f in files)
        else:
            if _copy_tree_filtered(src, dst):
                staged.append(item_clean)
    return staged


def stage_lib_inputs(
    drive_root: Path,
    pool: str,
    keys: list[str],
    shots_rel: str,
    frames_rel: str,
    repo_root: Path,
) -> list[str]:
    """把番池打标输入（镜头表 + *_cap 打标帧）复制到 `<drive_root>/staging/`。"""
    ensure_drive_layout(drive_root)
    staged: list[str] = []
    for k in keys:
        shot_rel = f"{shots_rel}/{pool}_{k}.json"
        shot_src = repo_root / shot_rel
        shot_dst = drive_root / "staging" / shot_rel
        if _copy_tree_filtered(shot_src, shot_dst):
            staged.append(shot_rel)

        cap_rel = f"{frames_rel}/{pool}_{k}_cap"
        cap_src = repo_root / cap_rel
        cap_dst = drive_root / "staging" / cap_rel
        files = _copy_tree_filtered(cap_src, cap_dst)
        staged.extend(f"{cap_rel}/{f}" for f in files)
    return staged


def parse_worker_heartbeat(
    drive_root: Path,
    now_epoch: float | None = None,
    stale_seconds: int = HEARTBEAT_STALE_SECONDS,
) -> dict[str, Any] | None:
    """读取 Colab Worker 心跳文件，并附带 `online` 与 `stale_s` 判定。"""
    hb_file = drive_root / HEARTBEAT_REL
    if not hb_file.is_file():
        return None
    try:
        data = json.loads(hb_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    now = time.time() if now_epoch is None else now_epoch
    ts = float(data.get("ts") or 0.0)
    stale_s = max(0.0, now - ts)
    out = dict(data)
    out["stale_s"] = round(stale_s, 1)
    out["online"] = ts > 0 and stale_s <= stale_seconds
    return out


def submit_drive_job(
    drive_root: Path,
    *,
    job_id: str,
    task: str,
    ep_rel_path: str,
    extra_args: str = "",
    lib_pool: str | None = None,
    lib_keys: list[str] | None = None,
    code_sha: str = "",
) -> Path:
    """写入任务请求 `jobs/<job_id>/request.json` 与初始 `status.json`。"""
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"非法 job_id: {job_id!r}")
    ensure_drive_layout(drive_root)
    job_dir = drive_root / "jobs" / job_id
    (job_dir / "output").mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now().isoformat(timespec="seconds")
    req: dict[str, Any] = {
        "job_id": job_id,
        "task": task,
        "ep_rel_path": ep_rel_path,
        "extra_args": extra_args,
        "code_sha": code_sha,
        "created_at": now_iso,
        "created_epoch": time.time(),
    }
    if lib_pool is not None:
        req["lib_pool"] = lib_pool
        req["lib_keys"] = list(lib_keys or [])
    status = {
        "job_id": job_id,
        "state": "pending",
        "updated_at": now_iso,
    }
    paths.atomic_write(job_dir / "request.json", json.dumps(req, ensure_ascii=False, indent=2) + "\n")
    paths.atomic_write(job_dir / "status.json", json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    return job_dir


def list_drive_jobs(drive_root: Path) -> list[dict[str, Any]]:
    """列出 Drive 队列中的所有任务（按创建时间降序）。"""
    jobs_dir = drive_root / "jobs"
    if not jobs_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in jobs_dir.iterdir():
        if not entry.is_dir():
            continue
        req_f = entry / "request.json"
        st_f = entry / "status.json"
        if not req_f.is_file():
            continue
        try:
            req = json.loads(req_f.read_text(encoding="utf-8"))
            st = json.loads(st_f.read_text(encoding="utf-8")) if st_f.is_file() else {"state": "unknown"}
        except Exception:
            continue
        out.append({"job_id": entry.name, "dir": entry, "request": req, "status": st})
    out.sort(key=lambda item: float(item["request"].get("created_epoch", 0.0)), reverse=True)
    return out


def active_drive_jobs(drive_root: Path) -> list[str]:
    """返回仍处于 pending / running 状态的 job_id 列表。"""
    return [
        item["job_id"]
        for item in list_drive_jobs(drive_root)
        if item["status"].get("state") in ("pending", "running")
    ]


def latest_job_for(
    drive_root: Path,
    *,
    ep_rel_path: str | None = None,
    lib_pool: str | None = None,
    task: str | None = None,
) -> dict[str, Any] | None:
    """查找最近一次匹配本期或番池的任务记录。"""
    for item in list_drive_jobs(drive_root):
        req = item["request"]
        if task is not None and req.get("task") != task:
            continue
        if ep_rel_path is not None and req.get("ep_rel_path") != ep_rel_path:
            continue
        if lib_pool is not None and req.get("lib_pool") != lib_pool:
            continue
        return item
    return None


def read_drive_job_logs(drive_root: Path, job_id: str, lines: int = 30) -> str | None:
    """读取指定任务的末尾日志行。"""
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"非法 job_id: {job_id!r}")
    log_file = drive_root / "jobs" / job_id / "worker.log"
    if not log_file.is_file():
        return None
    content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if lines > 0:
        content = content[-lines:]
    return "\n".join(content) + ("\n" if content else "")


def pull_drive_outputs(
    drive_root: Path,
    ep_dir: Path,
    rel_path: str,
    sync_items: list[str],
    job_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """从 Drive 任务输出目录拉回本期产物，返回 (命中 job_id, 已拉回项列表)。"""
    item = (
        next((j for j in list_drive_jobs(drive_root) if j["job_id"] == job_id), None)
        if job_id
        else latest_job_for(drive_root, ep_rel_path=rel_path)
    )
    if not item:
        return None, []
    out_ep = item["dir"] / "output" / rel_path
    pulled: list[str] = []
    if not out_ep.is_dir():
        return item["job_id"], pulled
    for sync_item in sync_items:
        clean = sync_item.rstrip("/")
        src = out_ep / clean
        if not src.exists():
            continue
        dst = ep_dir / clean
        if src.is_dir():
            files = _copy_tree_filtered(src, dst, exclude_attic=False)
            if files:
                pulled.append(f"{clean}/ ({len(files)} 文件)")
        else:
            if _copy_tree_filtered(src, dst):
                pulled.append(clean)
    return item["job_id"], pulled


def pull_drive_lib_outputs(
    drive_root: Path,
    pool: str,
    keys: list[str],
    vindex_rel: str,
    local_vindex_dir: Path,
) -> tuple[str | None, list[str], list[str]]:
    """拉回番池打标产物（*.captions.json），返回 (job_id, 成功列表, 缺失列表)。"""
    item = latest_job_for(drive_root, lib_pool=pool, task="captions")
    if not item:
        return None, [], [f"{k}（Drive 里没有 {pool} 的打标任务记录）" for k in keys]
    out_vindex = item["dir"] / "output" / vindex_rel
    local_vindex_dir.mkdir(parents=True, exist_ok=True)
    ok_list: list[str] = []
    missing: list[str] = []
    for k in keys:
        name = f"{pool}_{k}.captions.json"
        src = out_vindex / name
        if src.is_file():
            shutil.copy2(src, local_vindex_dir / name)
            ok_list.append(name)
        else:
            missing.append(f"{k}（输出目录未见 {name}）")
    return item["job_id"], ok_list, missing


def write_drive_hold(drive_root: Path, seconds: int, now_epoch: float | None = None) -> tuple[int, int]:
    """写入 Drive hold 标记；未到期续期时保留原 start。"""
    ensure_drive_layout(drive_root)
    now = int(time.time() if now_epoch is None else now_epoch)
    hold_file = drive_root / HOLD_REL
    start = now
    if hold_file.is_file():
        try:
            old = json.loads(hold_file.read_text(encoding="utf-8"))
            if isinstance(old.get("start"), int) and isinstance(old.get("until"), int) and now < old["until"]:
                start = old["start"]
        except Exception:
            pass
    until = now + int(seconds)
    paths.atomic_write(hold_file, json.dumps({"start": start, "until": until}, indent=2) + "\n")
    return start, until


def clear_drive_hold(drive_root: Path) -> None:
    """删除 Drive hold 标记。"""
    hold_file = drive_root / HOLD_REL
    if hold_file.exists():
        hold_file.unlink()


def read_drive_hold(drive_root: Path, now_epoch: float | None = None) -> tuple[int, int, int] | None:
    """读取 Drive hold 状态 → (start, until, now)；无或损坏返回 None。"""
    hold_file = drive_root / HOLD_REL
    if not hold_file.is_file():
        return None
    try:
        data = json.loads(hold_file.read_text(encoding="utf-8"))
        start = int(data["start"])
        until = int(data["until"])
    except Exception:
        return None
    now = int(time.time() if now_epoch is None else now_epoch)
    return start, until, now


def request_drive_shutdown(drive_root: Path) -> Path:
    """写入释放运行时标记，通知 Colab Worker 在无活跃任务后调用 unassign()。"""
    ensure_drive_layout(drive_root)
    flag = drive_root / SHUTDOWN_REL
    paths.atomic_write(flag, json.dumps({"requested_at": time.time()}, indent=2) + "\n")
    return flag
