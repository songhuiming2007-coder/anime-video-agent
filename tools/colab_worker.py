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
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
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


def atomic_write_drive_file(dest: Path, content: str) -> None:
    """用主机名+PID 隔离的临时文件原子写入 Drive 共享文件，避免多实例并发写同一文件名互相踩踏。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    node = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.uname().nodename)
    tmp = dest.with_name(f".{dest.name}.{node}_{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def compute_file_sha256(path: Path) -> tuple[str, int]:
    """流式计算文件 SHA256 与字节大小。"""
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total


def _parse_sha256_sidecar(meta_path: Path) -> tuple[str, int] | None:
    try:
        parts = meta_path.read_text(encoding="utf-8").strip().split()
    except OSError:
        return None
    if len(parts) < 2:
        return None
    sha, size_s = parts[0].lower(), parts[1]
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha) or not size_s.isdigit():
        return None
    return sha, int(size_s)


def verify_venv_tarball(
    tar_path: Path,
    *,
    wait_sync_s: float = 0.0,
    poll_s: float = 1.0,
) -> tuple[bool, str]:
    """校验 Drive 共享 `ava_venv_*.tar.gz` 完整性，防止并发实例读到未写完/未同步完的半截压缩包。"""
    if not tar_path.is_file():
        return False, "缓存文件不存在"
    meta_path = Path(f"{tar_path}.sha256")
    if not meta_path.is_file() and wait_sync_s > 0:
        sidecar_deadline = time.monotonic() + min(float(wait_sync_s), 3.0)
        while tar_path.is_file() and not meta_path.is_file() and time.monotonic() < sidecar_deadline:
            time.sleep(min(poll_s, 0.5, max(0.05, sidecar_deadline - time.monotonic())))
    if meta_path.is_file():
        parsed = _parse_sha256_sidecar(meta_path)
        if parsed is None:
            return False, "sha256 校验文件损坏"
        exp_sha, exp_size = parsed
        deadline = time.monotonic() + max(0.0, float(wait_sync_s))
        while tar_path.is_file() and tar_path.stat().st_size != exp_size and time.monotonic() < deadline:
            time.sleep(min(poll_s, max(0.05, deadline - time.monotonic())))
        if not tar_path.is_file():
            return False, "缓存文件在等待同步期间被替换或移除"
        actual_size = tar_path.stat().st_size
        if actual_size != exp_size:
            return False, f"文件大小不符 ({actual_size} != {exp_size}，可能仍在同步)"
        actual_sha, _ = compute_file_sha256(tar_path)
        if actual_sha != exp_sha:
            return False, "SHA256 校验不符（可能仍在同步或已损坏）"
        return True, "sha256 verified"

    try:
        if tar_path.stat().st_size <= 0:
            return False, "缓存文件为空"
        with tarfile.open(tar_path, "r:gz") as tf:
            members = sum(1 for _ in tf)
        if members == 0:
            return False, "tar 归档为空"
        return True, "tar stream verified"
    except Exception as exc:
        return False, f"tar.gz 完整性校验失败: {exc}"


def restore_colab_venv_cache(
    tar_path: Path,
    content_root: Path = Path("/content"),
    *,
    wait_sync_s: float = 10.0,
    poll_s: float = 1.0,
) -> bool:
    """只读一次 FUSE 拷贝至本地暂存包再校验并原子恢复到 `<content_root>/venv`，不留半截目录。"""
    if not tar_path.is_file():
        print("[!] Drive venv 缓存不存在，改走本地构建", flush=True)
        return False
    meta_path = Path(f"{tar_path}.sha256")
    if wait_sync_s > 0 and not meta_path.is_file():
        sidecar_deadline = time.monotonic() + min(float(wait_sync_s), 3.0)
        while tar_path.is_file() and not meta_path.is_file() and time.monotonic() < sidecar_deadline:
            time.sleep(min(poll_s, 0.5, max(0.05, sidecar_deadline - time.monotonic())))

    meta_text: str | None = None
    if meta_path.is_file():
        parsed = _parse_sha256_sidecar(meta_path)
        if parsed is None:
            print("[!] Drive venv .sha256 校验文件损坏，跳过缓存改走本地构建", flush=True)
            return False
        exp_sha, exp_size = parsed
        deadline = time.monotonic() + max(0.0, float(wait_sync_s))
        while tar_path.is_file() and tar_path.stat().st_size != exp_size and time.monotonic() < deadline:
            time.sleep(min(poll_s, max(0.05, deadline - time.monotonic())))
        if not tar_path.is_file() or tar_path.stat().st_size != exp_size:
            print("[!] Drive venv 缓存大小未与 .sha256 对齐（可能仍在同步），跳过缓存改走本地构建", flush=True)
            return False
        meta_text = f"{exp_sha} {exp_size}\n"

    content_root.mkdir(parents=True, exist_ok=True)
    local_tar = content_root / f".ava_venv_restore_{os.getpid()}.tar.gz"
    local_meta = Path(f"{local_tar}.sha256")
    stage_dir = content_root / f".venv_restore_{os.getpid()}"
    target_venv = content_root / "venv"
    try:
        shutil.copyfile(tar_path, local_tar)
        if meta_text is not None:
            local_meta.write_text(meta_text, encoding="utf-8")
        ok, reason = verify_venv_tarball(local_tar, wait_sync_s=0.0)
        if not ok:
            print(f"[!] Drive venv 缓存不可用或校验失败 ({reason})，跳过缓存改走本地构建", flush=True)
            return False

        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        stage_dir.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["tar", "-xzf", str(local_tar), "-C", str(stage_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        staged_venv = stage_dir / "venv"
        if res.returncode != 0 or not (staged_venv / "bin" / "python").exists():
            print(f"[!] 解压 Drive venv 缓存失败，改走本地构建: {(res.stderr or '').strip()[:160]}", flush=True)
            return False
        if target_venv.is_symlink() or target_venv.is_file():
            target_venv.unlink()
        elif target_venv.exists():
            shutil.rmtree(target_venv, ignore_errors=True)
        os.replace(staged_venv, target_venv)
        return (target_venv / "bin" / "python").exists()
    except Exception as exc:
        print(f"[!] 恢复 Drive venv 缓存异常，改走本地构建: {exc}", flush=True)
        return False
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        local_tar.unlink(missing_ok=True)
        local_meta.unlink(missing_ok=True)


def publish_colab_venv_cache(
    venv_dir: Path,
    tar_path: Path,
    *,
    scratch_dir: Path = Path("/tmp"),
    force: bool = False,
) -> bool:
    """先在本地容器盘打包计算 SHA256，再用唯一临时名原子替换到 Drive 并写 `.sha256` 伴随校验文件。"""
    meta_path = Path(f"{tar_path}.sha256")
    if not force and tar_path.is_file() and meta_path.is_file() and verify_venv_tarball(tar_path)[0]:
        return True

    scratch_dir.mkdir(parents=True, exist_ok=True)
    node = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.uname().nodename)
    local_tar = scratch_dir / f"ava_venv_{os.getpid()}.tar.gz"
    drive_tar_tmp = tar_path.with_name(f".{tar_path.name}.{node}_{os.getpid()}.tmp")
    drive_meta_tmp = meta_path.with_name(f".{meta_path.name}.{node}_{os.getpid()}.tmp")
    try:
        subprocess.run(
            ["tar", "-czf", str(local_tar), "-C", str(venv_dir.parent), venv_dir.name],
            check=True,
        )
        sha, size = compute_file_sha256(local_tar)
        if not force and tar_path.is_file() and meta_path.is_file() and verify_venv_tarball(tar_path)[0]:
            return True
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        with local_tar.open("rb") as src_f, drive_tar_tmp.open("wb") as dst_f:
            shutil.copyfileobj(src_f, dst_f, length=1024 * 1024)
            dst_f.flush()
            os.fsync(dst_f.fileno())
        with drive_meta_tmp.open("w", encoding="utf-8") as mf:
            mf.write(f"{sha} {size}\n")
            mf.flush()
            os.fsync(mf.fileno())
        # 先清旧校验伴随文件，再连续原子替换 tar 与新 .sha256，避免新 tar 配旧校验窗口
        meta_path.unlink(missing_ok=True)
        os.replace(drive_tar_tmp, tar_path)
        os.replace(drive_meta_tmp, meta_path)
        return True
    except Exception as exc:
        print(f"[!] 备份 venv 到 Drive 缓存失败（不影响本次运行）: {exc}", flush=True)
        return False
    finally:
        for leftover in (local_tar, drive_tar_tmp, drive_meta_tmp):
            if leftover.exists():
                try:
                    leftover.unlink()
                except OSError:
                    pass


FLASH_ATTN_RELEASE_API = "https://api.github.com/repos/Dao-AILab/flash-attention/releases?per_page=5"


def _wheel_version_key(name: str) -> tuple[int, ...]:
    prefix = name.split("+", 1)[0].removeprefix("flash_attn-")
    return tuple(int(x) for x in re.findall(r"\d+", prefix))


def _collect_release_assets(release_data: Any) -> list[dict[str, Any]]:
    """聚合多个正式 Release 的资产清单（跳过 draft 与 prerelease），避免新发版窗口漏轮子。"""
    assets: list[dict[str, Any]] = []
    if isinstance(release_data, list):
        for item in release_data:
            if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
                continue
            assets.extend(a for a in (item.get("assets") or []) if isinstance(a, dict))
        return assets
    if isinstance(release_data, dict):
        if release_data.get("draft") or release_data.get("prerelease"):
            return []
        return [a for a in (release_data.get("assets") or []) if isinstance(a, dict)]
    return assets


def find_matching_flash_attn_wheel(
    assets: list[dict[str, Any]],
    *,
    cu_tag: str,
    torch_mm: str,
    cxx11_abi: str,
    py_tag: str,
    plat: str = "linux_x86_64",
) -> str | None:
    """从 Release 资产清单中精确匹配预编译 `flash_attn` wheel URL（不跨版本猜测）。"""
    needle = f"+cu{cu_tag}torch{torch_mm}cxx11abi{cxx11_abi}-{py_tag}-{py_tag}-{plat}.whl"
    matches: list[tuple[tuple[int, ...], str]] = []
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name.startswith("flash_attn-") and name.endswith(needle) and url:
            matches.append((_wheel_version_key(name), url))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def ensure_colab_flash_attn(venv_python: Path, *, timeout_s: float = 8.0) -> tuple[bool, bool]:
    """探测并维护 `venv` 中的预编译 `flash-attn`，返回 `(usable, venv_mutated)`。

    - 若已可正常导入，直接返回 `(True, False)`；
    - 若检测到损坏或 ABI 失配残留（`find_spec` 存在但 `import flash_attn` 失败），先执行
      `uv pip uninstall` 清理，避免污染同环境中依赖 `is_flash_attn_2_available()` 的推理链路；
    - 仅在 `SM >= 8` 且 GitHub Release 存在精确匹配的预编译 wheel 时秒装（绝不源码编译）。
    """
    probe_code = (
        "import importlib.util, json, platform, sys, torch; "
        "spec_exists = importlib.util.find_spec('flash_attn') is not None\n"
        "has_flash = False\n"
        "try:\n"
        "    import flash_attn\n"
        "    has_flash = True\n"
        "except Exception:\n"
        "    pass\n"
        "sm = torch.cuda.get_device_capability(0)[0] if torch.cuda.is_available() else 0; "
        "print(json.dumps({"
        "'spec_exists': spec_exists, "
        "'has_flash': has_flash, "
        "'cuda': torch.version.cuda or '', "
        "'torch': torch.__version__, "
        "'abi': 'TRUE' if bool(getattr(torch._C, '_GLIBCXX_USE_CXX11_ABI', False)) else 'FALSE', "
        "'py': f'cp{sys.version_info.major}{sys.version_info.minor}', "
        "'plat': f'{platform.system().lower()}_{platform.machine()}', "
        "'sm_major': sm"
        "}))"
    )
    try:
        res = subprocess.run(
            [str(venv_python), "-c", probe_code],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return False, False
        info = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception as exc:
        print(f"[*] 探测 PyTorch/CUDA 环境失败，跳过 flash-attn 安装（配音自动启用 PyTorch SDPA）: {exc}", flush=True)
        return False, False

    if info.get("has_flash"):
        return True, False

    mutated = False
    if bool(info.get("spec_exists", False)):
        try:
            uninst = subprocess.run(
                ["uv", "pip", "uninstall", "--python", str(venv_python), "flash-attn"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if uninst.returncode == 0:
                mutated = True
                print("[!] 检测到损坏或 ABI 失配的 flash-attn 残留，已从 venv 卸载清理", flush=True)
        except Exception:
            pass

    cuda_ver = str(info.get("cuda") or "").strip()
    sm_major = int(info.get("sm_major") or 0)
    if not cuda_ver or sm_major < 8:
        print("[*] 当前 GPU/CUDA 环境不需要或不支持 flash-attn2，跳过安装（配音自动启用 PyTorch SDPA）", flush=True)
        return False, mutated

    cu_tag = cuda_ver.split(".")[0]
    torch_base = str(info.get("torch") or "").split("+", 1)[0]
    torch_parts = torch_base.split(".")
    if len(torch_parts) < 2:
        return False, mutated
    torch_mm = f"{torch_parts[0]}.{torch_parts[1]}"

    try:
        req = urllib.request.Request(
            FLASH_ATTN_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ava-colab-bootstrap"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            release_data = json.loads(resp.read().decode("utf-8"))
        assets = _collect_release_assets(release_data)
    except Exception as exc:
        print(f"[*] 查询 flash-attn 预编译 wheel 失败（{exc}），跳过安装（配音自动启用 PyTorch SDPA）", flush=True)
        return False, mutated

    wheel_url = find_matching_flash_attn_wheel(
        assets,
        cu_tag=cu_tag,
        torch_mm=torch_mm,
        cxx11_abi=str(info.get("abi") or "FALSE"),
        py_tag=str(info.get("py") or ""),
        plat=str(info.get("plat") or "linux_x86_64"),
    )
    if not wheel_url:
        print("[*] 未找到匹配当前环境的预编译 flash-attn wheel，跳过安装（配音自动启用 PyTorch SDPA）", flush=True)
        return False, mutated

    print(f"[*] 尝试秒装预编译 flash-attn wheel: {wheel_url}", flush=True)
    try:
        inst = subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_python), "--only-binary", ":all:", "--no-deps", wheel_url],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if inst.returncode != 0:
            print("[*] 安装预编译 flash-attn wheel 失败，跳过（配音自动启用 PyTorch SDPA）", flush=True)
            return False, mutated
        verify = subprocess.run(
            [str(venv_python), "-c", "import flash_attn"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if verify.returncode != 0:
            subprocess.run(
                ["uv", "pip", "uninstall", "--python", str(venv_python), "flash-attn"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            print("[*] flash-attn 导入校验未通过，已卸载并改用 PyTorch SDPA", flush=True)
            return False, mutated
        print("[OK] 已安装预编译 flash-attn wheel", flush=True)
        return True, True
    except Exception as exc:
        subprocess.run(
            ["uv", "pip", "uninstall", "--python", str(venv_python), "flash-attn"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        print(f"[*] 安装 flash-attn wheel 异常（{exc}），已跳过并改用 PyTorch SDPA", flush=True)
        return False, mutated


def try_install_flash_attn_wheel(venv_python: Path, *, timeout_s: float = 8.0) -> bool:
    """仅当 GitHub Release 存在精确匹配的预编译 wheel 时秒装 `flash-attn`，绝不触发源码编译。"""
    return ensure_colab_flash_attn(venv_python, timeout_s=timeout_s)[0]


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
