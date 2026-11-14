"""端云协同调度与 GPU 生命周期管理（M1b，E9：python -m pipeline.cloud）。

核心能力：
1. exec: 远端安全执行命令——默认强制 tmux 包裹后台化 + 日志落盘（防断连 SIGHUP）；
         仅 --fg 允许直连短命令（限 <60s 探针）
2. logs: 查看/跟踪远端任务日志
3. doctor: 逐项核验云端环境（SSH、显卡型号与显存/无卡模式、仓库、PyTorch+CUDA、数据盘、文件存储与模型软链、模型）
4. up/down/status: 云端实例生命周期管理与分模式成本核算（追加式账簿 data/cloud/ledger.jsonl，记实际 GPU 型号）
4b. connect: 换实例（D102）——解析控制台登录指令，写连接三项、生成 data/cloud/ssh_config
5. push/pull/run/attach: 资产同步与主干任务调度（白名单：tts、probe）

设计依据：
- ADR-0014 (端云计算与交互解耦)
- E3/E9/E10 规范
- SSH 性能治理：ControlMaster 复用 + 远端长任务必须 tmux 隔离
- S1 判据：Watchdog 检查真实 tmux 任务存活状态
"""

from __future__ import annotations

import argparse
import base64
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

from . import cloud_colab_boot, cloud_drive, paths
from .keys import SP_NUM

# ---------------------------------------------------------------------------
# 常量定义与判据依据（E3：每个常量后面写「为什么是这个数」）
# ---------------------------------------------------------------------------

# 可用 cloud fix-env / 医生检查或切换的提供商标识
VALID_PROVIDERS = ("autodl", "colab", "colab-drive")

# 允许通过 run 命令透传执行的远端任务白名单
# 为什么是白名单：禁止透传任意 shell 命令，杜绝远端非预期写操作破坏环境
ALLOWED_TASKS = {
    "tts",       # 语音合成与回读质检（对应 python -m pipeline.tts <期>）
    "probe",     # M1b 闭环自检验收探针（GPU + PyTorch 连通性测试）
    "captions",  # VLM 逐镜头意象打标（对应 python -m pipeline.vindex captions）
}

# 默认心跳文件位置与空闲关机阈值
WATCHDOG_HEARTBEAT_PATH = "/root/.ava_heartbeat"
COLAB_HEARTBEAT_PATH = "/content/.ava_heartbeat"
DEFAULT_IDLE_SHUTDOWN_SECONDS = 300  # 300s：无操作 5 分钟关机，给 pull 预留容错窗口

# 显存提示线（D102 起不再是硬门槛）：24GB 依据 Qwen3-VL-8B bf16 权重 17.5GB 加推理开销。
# 卡型改为按需租，只配音的任务（Qwen3-TTS 1.7B，权重约 4GB）不需要这么大的卡，所以低于它只报 WARN；
# 各任务的实测峰值等第一台新实例测过再写进配置（D102 施工图第五节 ③）。
MIN_VRAM_GB = 24.0

# D102：模型放区级文件存储（全区实例开机自动挂载、不随实例释放），数据盘上的模型目录软链过去。
# 可用 `config/cloud.json` 的 `fs_models` 覆盖。
DEFAULT_FS_MODELS = "/root/autodl-fs/models"
# 租机条件里的 CUDA 下限：镜像是 torch 2.8 + cu128，驱动支持不到 12.8 的机器起不来
RENT_MIN_CUDA = "12.8"

# 远端日志根目录
REMOTE_LOG_DIR = "/root/autodl-tmp/logs"
COLAB_LOG_DIR = "/content/logs"

# 远端解释器。**必须是装了模型栈的那一个。** 2026-09-11 实测：这台实例上
# `/root/miniconda3/bin/python` 里连 transformers 都没有（torch/torchvision 有，模型栈在
# `/root/it-venv`），而 `run ... tts` / 模型结构探针走的正是它——写死旧值的后果是任务
# 起来第一行就 ImportError，而账簿里只记「任务已启动」。可用 `config/cloud.json`
# 的 `remote_python` 覆盖（换实例/换镜像只改配置，不改代码）。
DEFAULT_REMOTE_PYTHON = "/root/it-venv/bin/python"
COLAB_REMOTE_PYTHON = "/content/venv/bin/python"

# watchdog 脚本与心跳的远端路径。**只此一处定义**：部署与查进程必须指同一个文件，
# 各写一份的下场是 status 去 pgrep 一个不存在的路径，永远报「未运行」。
REMOTE_WATCHDOG_PATH = "/root/watchdog.sh"
COLAB_WATCHDOG_PATH = "/content/watchdog.sh"

# D130（2026-10-23 人提、10-24 人定）：「配完不关机」的保持标记。一行两个远端 epoch 秒「开始 到期」，
# 看门狗见未到期的标记就刷新心跳；过期视同没有，从到期那一刻起照常计空闲。
# 2 h：人定的自动释放时长——一轮 03.5 顺听加点名重配够用，忘了 release 最多多计两小时；要更久就再 hold 续期。
# 可用 `config/cloud.json` 的 `hold_seconds` 覆盖
REMOTE_HOLD_PATH = "/root/.ava_hold"
COLAB_HOLD_PATH = "/content/.ava_hold"
COLAB_UNASSIGN_PATH = "/content/.ava_unassign"
DEFAULT_HOLD_SECONDS = 7200


# ---------------------------------------------------------------------------
# 第一节：纯函数计算层
# ---------------------------------------------------------------------------


def load_cloud_config(config_dir: Path | None = None) -> tuple[dict, dict]:
    """读取 config/cloud.json（机制）与 config/cloud.local.json（凭据）。"""
    cdir = config_dir or paths.CONFIG
    f_global = cdir / "cloud.json"
    f_local = cdir / "cloud.local.json"

    cfg_global = json.loads(f_global.read_text(encoding="utf-8")) if f_global.exists() else {}
    cfg_local = json.loads(f_local.read_text(encoding="utf-8")) if f_local.exists() else {}
    return cfg_global, cfg_local


def resolve_provider(
    cfg_global: dict | None = None,
    cfg_local: dict | None = None,
    cli_provider: str | None = None,
) -> str:
    """按优先级解析云算力后端（纯函数）：CLI --provider > AVA_CLOUD_PROVIDER > cloud.local.json > cloud.json > autodl。"""
    raw = (
        cli_provider
        or os.environ.get("AVA_CLOUD_PROVIDER")
        or (cfg_local or {}).get("active_provider")
        or (cfg_global or {}).get("provider")
        or "autodl"
    )
    provider = str(raw).strip()
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"不支持的云算力后端 {provider!r}，可选: {', '.join(VALID_PROVIDERS)}")
    return provider


def provider_settings(cfg_global: dict, provider: str | None = None) -> dict[str, Any]:
    """合并顶层 cloud.json 与 providers.<provider> 配置（纯函数）。"""
    p = provider or str(cfg_global.get("provider") or "autodl")
    merged = {k: v for k, v in cfg_global.items() if k != "providers"}
    sub = (cfg_global.get("providers") or {}).get(p)
    if isinstance(sub, dict):
        merged.update(sub)
    merged.setdefault("provider", p)
    if p == "autodl":
        merged.setdefault("transport", "ssh")
        merged.setdefault("billing_unit", "cny")
        merged.setdefault("remote_root", "/root/anime-video-agent")
        merged.setdefault("remote_models", "/root/autodl-tmp/models")
        merged.setdefault("fs_models", DEFAULT_FS_MODELS)
        merged.setdefault("remote_data_disk", "/root/autodl-tmp")
        merged.setdefault("remote_log_dir", REMOTE_LOG_DIR)
        merged.setdefault("remote_python", DEFAULT_REMOTE_PYTHON)
        merged.setdefault("ssh_host", "autodl")
    else:
        merged.setdefault("transport", "drive" if p == "colab-drive" else "ssh")
        merged.setdefault("billing_unit", "cu")
        merged.setdefault("remote_root", "/content/anime-video-agent")
        merged.setdefault("remote_models", "/content/models")
        merged.setdefault("remote_data_disk", "/content")
        merged.setdefault("remote_log_dir", COLAB_LOG_DIR)
        merged.setdefault("remote_python", COLAB_REMOTE_PYTHON)
        if p == "colab":
            merged.setdefault("ssh_host", "ava-colab")
    return merged


def provider_profile(cfg_local: dict, provider: str) -> dict[str, Any]:
    """读取 cloud.local.json 里指定后端的独立配置段，兼容旧版顶层 AutoDL 键（纯函数）。"""
    profiles = cfg_local.get("profiles") or {}
    prof = dict(profiles.get(provider) or {}) if isinstance(profiles.get(provider), dict) else {}
    if provider == "autodl":
        for k in ("ssh_host", "server", "port", "user"):
            if k in cfg_local and k not in prof:
                prof[k] = cfg_local[k]
        prof.setdefault("ssh_host", "autodl")
    elif provider == "colab":
        prof.setdefault("ssh_host", "ava-colab")
    elif provider == "colab-drive":
        prof.setdefault("drive_queue_subpath", "ava_cloud")
    return prof


def provider_remote_paths(cfg_global: dict, provider: str | None = None) -> dict[str, str]:
    """返回指定后端的远端工作/模型/心跳/hold 路径字典（纯函数）。"""
    p = provider or str(cfg_global.get("provider") or "autodl")
    st = provider_settings(cfg_global, p)
    is_colab = p in ("colab", "colab-drive")
    return {
        "remote_root": str(st["remote_root"]),
        "remote_models": str(st["remote_models"]),
        "fs_models": str(st.get("fs_models") or ""),
        "remote_data_disk": str(st["remote_data_disk"]),
        "remote_log_dir": str(st["remote_log_dir"]),
        "remote_python": str(st["remote_python"]),
        "heartbeat_path": COLAB_HEARTBEAT_PATH if is_colab else WATCHDOG_HEARTBEAT_PATH,
        "hold_path": COLAB_HOLD_PATH if is_colab else REMOTE_HOLD_PATH,
        "unassign_path": COLAB_UNASSIGN_PATH if is_colab else "",
        "watchdog_path": COLAB_WATCHDOG_PATH if is_colab else REMOTE_WATCHDOG_PATH,
    }


def provider_ssh_host(cfg_global: dict, cfg_local: dict, provider: str | None = None) -> str:
    """获取指定后端的 SSH Host 别名（纯函数）。"""
    p = provider or resolve_provider(cfg_global, cfg_local)
    prof = provider_profile(cfg_local, p)
    st = provider_settings(cfg_global, p)
    return str(prof.get("ssh_host") or st.get("ssh_host") or ("ava-colab" if p == "colab" else "autodl"))


def billing_unit_of(cfg_global: dict, provider: str | None = None) -> str:
    """返回指定后端的计费单位（'cny' 或 'cu'）。"""
    st = provider_settings(cfg_global, provider or str(cfg_global.get("provider") or "autodl"))
    return str(st.get("billing_unit") or ("cu" if st.get("provider") in ("colab", "colab-drive") else "cny"))


def remote_python(cfg: dict | None = None, *, provider: str | None = None) -> str:
    """远端解释器路径：`config/cloud.json` 的 `remote_python` 优先，缺了用默认值。

    做成可配不是洁癖：云端环境里同时存在多个 venv，模型栈只在其中一个里（见常量注释）。
    写死在代码里就是把这个实例的现状当机制（ADR-0003 R2：换台机器就要改代码即缺陷）。
    """
    if cfg is None:
        cfg_global, cfg_local = load_cloud_config()
        p = provider or resolve_provider(cfg_global, cfg_local)
        return str(provider_settings(cfg_global, p).get("remote_python") or DEFAULT_REMOTE_PYTHON)
    if provider is not None:
        return str(provider_settings(cfg, provider).get("remote_python") or (COLAB_REMOTE_PYTHON if provider != "autodl" else DEFAULT_REMOTE_PYTHON))
    if "remote_python" in cfg:
        return str(cfg["remote_python"] or DEFAULT_REMOTE_PYTHON)
    p = str(cfg.get("provider") or "autodl")
    return str(provider_settings(cfg, p).get("remote_python") or DEFAULT_REMOTE_PYTHON)


def build_exec_tmux_command(
    cmd: str,
    session_name: str,
    log_dir: str = REMOTE_LOG_DIR,
    heartbeat_path: str = WATCHDOG_HEARTBEAT_PATH,
) -> tuple[str, str]:
    """生成 exec 命令的 tmux 包裹字符串与远端日志落盘路径（纯函数）。

    机制说明（S1/P0-1）：
    远端长任务必须用 tmux 包裹并在后台执行（断开 SSH 不收 SIGHUP），
    首尾主动 touch 心跳文件，同时使用 tee 将 stdout/stderr 实时落地至数据盘日志。
    """
    log_path = f"{log_dir.rstrip('/')}/{session_name}.log"
    safe_cmd = cmd.replace("'", "'\\''")
    wrapped_inner = f"touch {heartbeat_path} && ({safe_cmd}); touch {heartbeat_path}"
    tmux_cmd = (
        f"mkdir -p {log_dir} && "
        f"tmux new-session -d -s {session_name} "
        f"'({wrapped_inner}) 2>&1 | tee {log_path}'"
    )
    return tmux_cmd, log_path


def parse_gpu_query(raw: str) -> tuple[str | None, float | None]:
    """解析 `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` 的首行（纯函数）。

    返回 (型号, 显存 MiB)；只有一列数字时当作旧格式（只查 memory.total），型号为 None。
    解析不出返回 (None, None)。
    """
    lines = raw.strip().splitlines()
    if not lines:
        return None, None
    cols = [c.strip() for c in lines[0].split(",")]
    try:
        mem = float(cols[-1])
    except ValueError:
        return None, None
    name = cols[0] if len(cols) >= 2 and cols[0] else None
    return name, mem


def evaluate_doctor_gpu(
    raw_vram_output: str,
    returncode: int,
    *,
    provider: str = "autodl",
) -> tuple[str, str, bool]:
    """评估 GPU 项健康状态（纯函数）。

    判据：
    - AutoDL 无 GPU / nvidia-smi 失败 → 判定为「无卡模式，GPU 未挂载」（状态陈述，非失败）
    - Colab 无 GPU → 判 FAIL，提示在 Colab 笔记本设置中选择 T4 / L4 / A100
    - 有 GPU → 报型号与显存；低于 MIN_VRAM_GB 报 WARN 并说明哪类任务受影响（D102：卡型按需租，
      只配音的任务用小卡是正常选择，不判失败）
    返回: (状态标签 OK/INFO/WARN/FAIL, 详情描述, 是否通过质检)
    """
    if returncode != 0 or not raw_vram_output.strip():
        if provider in ("colab", "colab-drive"):
            return "FAIL", "Colab 运行时未挂载 GPU（请在 Colab「修改笔记本设置」选择 T4/L4/A100）", False
        return "INFO", "无卡模式，GPU 未挂载（0.10元/h 节能维护模式）", True

    name, vram_mb = parse_gpu_query(raw_vram_output)
    if vram_mb is None:
        return "FAIL", f"解析 nvidia-smi 显存输出失败 ({raw_vram_output.strip()})", False
    vram_gb = round(vram_mb / 1024.0, 1)
    label = f"{name}，" if name else ""
    if vram_gb >= MIN_VRAM_GB:
        return "OK", f"{label}显存 {vram_gb}GB ≥ {MIN_VRAM_GB}GB（VLM 打标与配音都能跑）", True
    return "WARN", (
        f"{label}显存 {vram_gb}GB < {MIN_VRAM_GB}GB：VLM 打标（Qwen3-VL-8B，bf16 权重 17.5GB）"
        f"在这张卡上会显存不足；只配音不受影响。各任务实测峰值待补（D102 第五节 ③）"
    ), True


def lookup_gpu_rate(gpu_name: str | None, rates: dict, fallback: float) -> tuple[float, bool]:
    """按实测 GPU 型号查 `cloud.json` 的 `gpu_rates_cny` 或 `cu_rates`（纯函数）。返回 (费率, 是否估算)。

    表的键是算力市场的卡型名，nvidia-smi 报的名字常带厂商前缀（如 `NVIDIA GeForce RTX 4090`），
    所以按「键是型号名的子串」匹配、忽略大小写；多个键命中取最长的（`RTX 4090D` 不被 `RTX 4090` 抢走）。
    查不到就用 fallback，并标估算，账本照实记。
    """
    if gpu_name:
        low = gpu_name.lower()
        hits = [k for k in rates if not k.startswith("_") and k.lower() in low]
        if hits:
            return float(rates[max(hits, key=len)]), False
    return float(fallback), True


def evaluate_doctor_disk(avail_gb_str: str, disk_path: str = "/root/autodl-tmp") -> tuple[str, str, bool]:
    """评估数据盘可用空间（纯函数）。"""
    try:
        val = int(avail_gb_str.strip())
        if val >= 10:
            return "OK", f"{disk_path} 数据盘可用空间充足: {val}GB", True
        else:
            return "FAIL", f"{disk_path} 剩余空间不足 10GB (仅剩 {val}GB)", False
    except Exception:
        return "FAIL", f"数据盘 {disk_path} 未挂载或不可读", False


# ---------------------------------------------------------------------------
# D102：实例用完即弃——连接信息、ssh 配置、模型软链（纯函数）
# ---------------------------------------------------------------------------

# 写进 ssh_config 的三项只许这些字符：解析结果原样落进配置文件，放过空白或引号
# 就能塞进 ProxyCommand 之类的指令
_LOGIN_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")
_LOGIN_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,31}$")
# ssh 里要带值的单字母选项（OpenSSH man ssh 的 SYNOPSIS），解析时跳过它们的值
_SSH_VALUED_OPTS = set("BbcDEeFIiJLlmOoPpQRSWw")


def parse_login_command(text: str) -> dict[str, Any]:
    """解析控制台复制来的 SSH 登录指令（纯函数）：`ssh -p <端口> <用户>@<主机>` → {server, port, user}。

    认 `-p 端口` / `-p端口`、`-l 用户`、夹在中间的 `-o 选项` 与其他带值选项；缺端口、缺主机、
    多个主机或字符不合法一律 ValueError，消息里列出已经认出来的部分，方便人对照原文。
    缺端口不补 22：AutoDL 的登录指令总带 `-p`，缺了说明复制不完整。
    """
    try:
        tokens = shlex.split(text.strip())
    except ValueError as exc:
        raise ValueError(f"登录指令无法分词：{exc}") from exc
    found: dict[str, Any] = {}
    targets: list[str] = []
    if not tokens or tokens[0] != "ssh":
        raise ValueError(f"登录指令要以 ssh 开头，实际开头是 {tokens[0] if tokens else '（空）'!r}")
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("-") and len(t) >= 2:
            opt, inline = t[1], t[2:]
            if opt in _SSH_VALUED_OPTS:
                if inline:
                    val = inline
                    i += 1
                elif i + 1 < len(tokens):
                    val = tokens[i + 1]
                    i += 2
                else:
                    raise ValueError(f"选项 -{opt} 缺值；已认出：{found or '无'}")
                if opt == "p":
                    found["port"] = val
                elif opt == "l":
                    found["user"] = val
            else:
                i += 1
            continue
        targets.append(t)
        i += 1
    if len(targets) != 1:
        raise ValueError(f"要恰好一个「用户@主机」，实际 {targets or '没有'}；已认出：{found or '无'}")
    target = targets[0]
    if "@" in target:
        found["user"], found["server"] = target.rsplit("@", 1)
    else:
        found["server"] = target
    missing = [k for k in ("server", "port", "user") if not found.get(k)]
    if missing:
        raise ValueError(f"登录指令缺 {'、'.join(missing)}；已认出：{found}")
    port_s = str(found["port"])
    if not port_s.isdigit() or not 1 <= int(port_s) <= 65535:
        raise ValueError(f"端口不合法：{port_s!r}；已认出：{found}")
    if not _LOGIN_HOST_RE.match(found["server"]):
        raise ValueError(f"主机名含不允许的字符：{found['server']!r}")
    if not _LOGIN_USER_RE.match(found["user"]):
        raise ValueError(f"用户名含不允许的字符：{found['user']!r}")
    return {"server": found["server"], "port": int(port_s), "user": found["user"]}


def parse_colab_connect_target(text: str) -> dict[str, Any]:
    """解析 Colab 连接目标（纯函数）：支持 cloudflared 隧道、SSH `-p` 指令或 bare hostname。

    支持形态：
    1. `ssh -o ProxyCommand="cloudflared access ssh --hostname %h" root@xxx.trycloudflare.com`
    2. `root@xxx.trycloudflare.com` / `xxx.trycloudflare.com` / `https://xxx.trycloudflare.com`
    3. `ssh -p <端口> <用户>@<主机>`（与 AutoDL TCP 隧道相同，委托 parse_login_command）
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Colab 连接目标不能为空")

    # 形态 2：bare host / URL（不含 ssh 前缀，含 trycloudflare.com）
    if not raw.startswith("ssh ") and " " not in raw:
        cleaned = raw
        if "://" in cleaned:
            cleaned = cleaned.split("://", 1)[1]
        cleaned = cleaned.split("/", 1)[0]
        user = "root"
        host = cleaned
        if "@" in cleaned:
            user, host = cleaned.rsplit("@", 1)
        if not _LOGIN_HOST_RE.match(host):
            raise ValueError(f"Colab 主机名含不允许的字符：{host!r}")
        if not _LOGIN_USER_RE.match(user):
            raise ValueError(f"Colab 用户名含不允许的字符：{user!r}")
        return {"server": host, "port": 22, "user": user, "proxy_type": "cloudflared"}

    # 形态 1：带 cloudflared ProxyCommand 的 ssh 命令（通常不带 -p）
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"Colab 登录指令无法分词：{exc}") from exc

    has_cloudflared = any("cloudflared" in t or "trycloudflare.com" in t for t in tokens)
    if has_cloudflared and "-p" not in tokens and not any(t.startswith("-p") and len(t) > 2 for t in tokens):
        if tokens[0] != "ssh":
            raise ValueError(f"登录指令要以 ssh 开头，实际开头是 {tokens[0]!r}")
        targets: list[str] = []
        user_opt: str | None = None
        i = 1
        while i < len(tokens):
            t = tokens[i]
            if t.startswith("-") and len(t) >= 2:
                opt, inline = t[1], t[2:]
                if opt in _SSH_VALUED_OPTS:
                    val = inline if inline else (tokens[i + 1] if i + 1 < len(tokens) else None)
                    if val is None:
                        raise ValueError(f"选项 -{opt} 缺值")
                    if opt == "l":
                        user_opt = val
                    i += 1 if inline else 2
                else:
                    i += 1
                continue
            targets.append(t)
            i += 1
        if len(targets) != 1:
            raise ValueError(f"要恰好一个「用户@主机」，实际 {targets or '没有'}")
        target = targets[0]
        user, host = target.rsplit("@", 1) if "@" in target else (user_opt or "root", target)
        if not _LOGIN_HOST_RE.match(host):
            raise ValueError(f"主机名含不允许的字符：{host!r}")
        if not _LOGIN_USER_RE.match(user):
            raise ValueError(f"用户名含不允许的字符：{user!r}")
        return {"server": host, "port": 22, "user": user, "proxy_type": "cloudflared"}

    # 形态 3：标准 TCP SSH (`ssh -p ...`)
    conn = parse_login_command(raw)
    conn["proxy_type"] = "direct"
    return conn


def merge_local_connection(local: dict, conn: dict) -> dict:
    """把连接三项并进 cloud.local.json 的内容（纯函数）：只改 server / port / user，其余键与顺序原样。"""
    out = dict(local)
    for k in ("server", "port", "user"):
        out[k] = conn[k]
    return out


def merge_profile_connection(local: dict, provider: str, conn: dict, ssh_host: str | None = None) -> dict:
    """把指定后端的连接信息写入 `cloud.local.json`（纯函数），不覆盖其他后端。"""
    out = dict(local)
    if provider == "autodl":
        out = merge_local_connection(out, conn)
        if ssh_host:
            out["ssh_host"] = ssh_host
        if "profiles" in out:
            profiles = dict(out.get("profiles") or {})
            prof = dict(profiles.get("autodl") or {})
            for k in ("server", "port", "user"):
                prof[k] = conn[k]
            prof["ssh_host"] = ssh_host or out.get("ssh_host", "autodl")
            profiles["autodl"] = prof
            out["profiles"] = profiles
        return out

    profiles = dict(out.get("profiles") or {})
    prof = dict(profiles.get(provider) or {})
    if provider == "colab-drive":
        for k, v in conn.items():
            prof[k] = v
    else:
        for k in ("server", "port", "user"):
            if k in conn:
                prof[k] = conn[k]
        for opt_key in ("proxy_type", "notebook_url"):
            if conn.get(opt_key):
                prof[opt_key] = conn[opt_key]
        if ssh_host:
            prof["ssh_host"] = ssh_host
        else:
            prof.setdefault("ssh_host", "autodl" if provider == "autodl" else "ava-colab")
    profiles[provider] = prof
    out["profiles"] = profiles
    return out


def merge_active_provider(local: dict, provider: str) -> dict:
    """切换 `cloud.local.json` 的 `active_provider`（纯函数），保留所有 profiles。"""
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"不支持的云算力后端 {provider!r}，可选: {', '.join(VALID_PROVIDERS)}")
    out = dict(local)
    out["active_provider"] = provider
    return out


def _render_ssh_host_block(alias: str, conn: dict, known_hosts: Path) -> str:
    """生成单个 Host 块（纯函数），支持直连端口与 cloudflared ProxyCommand。"""
    lines = [
        f"Host {alias}",
        f"    HostName {conn['server']}",
        f"    User {conn.get('user', 'root')}",
    ]
    if conn.get("proxy_type") == "cloudflared":
        lines.append("    ProxyCommand cloudflared access ssh --hostname %h")
    else:
        lines.append(f"    Port {int(conn['port'])}")
    lines.extend([
        "    StrictHostKeyChecking accept-new",
        f'    UserKnownHostsFile "{known_hosts}"',
    ])
    return "\n".join(lines) + "\n"


def render_ssh_config(alias: str, conn: dict, known_hosts: Path) -> str:
    """生成 `data/cloud/ssh_config`（纯函数）。

    - `StrictHostKeyChecking accept-new`：新实例第一次连接自动记下主机密钥，之后密钥变了照常拒绝；
    - 主机密钥记在项目自己的 known_hosts，不写用户的 ~/.ssh/known_hosts；
    - 末尾 `Match all` + `Include ~/.ssh/config`：沿用用户原有的密钥、ControlMaster 等设置。
      ssh 对同一项取先读到的值，所以这里的 HostName / Port / User 优先于 ~/.ssh/config 里旧的 autodl 别名。
      不加 `Match all` 的话 Include 会落进上面的 Host 块，只在主机名匹配时才生效。
    """
    return (
        "# 由 python -m pipeline.cloud connect 生成（D102），换实例时重写；手改会被下次 connect 覆盖\n"
        + _render_ssh_host_block(alias, conn, known_hosts)
        + "\nMatch all\nInclude ~/.ssh/config\n"
    )


def render_all_ssh_configs(cfg_global: dict, cfg_local: dict, known_hosts: Path) -> str:
    """为所有已配置连接的 SSH 后端（autodl / colab）生成多 Host 块 `ssh_config`（纯函数）。"""
    blocks: list[str] = []
    seen_aliases: set[str] = set()
    for p in ("autodl", "colab"):
        prof = provider_profile(cfg_local, p)
        if not prof.get("server") or not prof.get("user"):
            continue
        if prof.get("proxy_type") != "cloudflared" and not prof.get("port"):
            continue
        alias = str(prof.get("ssh_host") or provider_settings(cfg_global, p).get("ssh_host") or p)
        if alias in seen_aliases:
            continue
        seen_aliases.add(alias)
        blocks.append(_render_ssh_host_block(alias, prof, known_hosts))
    if not blocks:
        return ""
    return (
        "# 由 python -m pipeline.cloud connect 生成（D102 / Colab），换实例时重写；手改会被下次 connect 覆盖\n"
        + "\n".join(blocks)
        + "\nMatch all\nInclude ~/.ssh/config\n"
    )


def models_link_script(fs_models: str, tmp_models: str, *, create: bool = True) -> str:
    """远端模型目录软链脚本（纯函数，D102）：`<数据盘模型目录>` → `<文件存储模型目录>`，幂等。

    输出首词是结论：ALREADY（已指向文件存储）/ LINKED（新建）/ RELINKED（原软链指向别处，改指）/
    REALDIR（已是实体目录，如 F29 那台，不动它）/ NOFS（文件存储里没有模型目录，没挂上或没拷）/
    CONFLICT（同名普通文件，交人看）。用 `ln -sfn` 前先分情况：对实体目录用它会在目录里面再建一个链接。
    `create=False` 是 doctor 用的只读版：该建 / 该改指的地方只报 MISSING / ELSEWHERE，不动远端。
    """
    fs, tmp = shlex.quote(fs_models), shlex.quote(tmp_models)
    relink = f"ln -sfn {fs} {tmp} && echo RELINKED" if create else "echo ELSEWHERE"
    link = f"mkdir -p \"$(dirname {tmp})\" && ln -s {fs} {tmp} && echo LINKED" if create else "echo MISSING"
    return (
        f"if [ -L {tmp} ]; then "
        f"  if [ \"$(readlink {tmp})\" = {fs} ]; then echo ALREADY; "
        f"  else {relink}; fi; "
        f"elif [ -d {tmp} ]; then echo REALDIR; "
        f"elif [ -e {tmp} ]; then echo CONFLICT; "
        f"elif [ ! -d {fs} ]; then echo NOFS; "
        f"else {link}; fi"
    )


def evaluate_models_link(stdout: str, fs_models: str, tmp_models: str) -> tuple[str, str, bool]:
    """解读 models_link_script 的输出（纯函数）→ (标签, 说明, 是否通过)。

    REALDIR 判通过：实体目录里的模型由 doctor 的逐模型检查核，这里只报一句。
    """
    word = (stdout.strip().split() or [""])[0]
    if word in ("ALREADY", "LINKED", "RELINKED"):
        verb = {"ALREADY": "已指向", "LINKED": "已新建软链指向", "RELINKED": "已改指"}[word]
        return "OK", f"{tmp_models} {verb} {fs_models}", True
    if word == "REALDIR":
        return "INFO", f"{tmp_models} 是这台实例数据盘上的实体目录，不动它（模型完整性见下方逐模型检查）", True
    if word == "NOFS":
        return "FAIL", f"文件存储里没有 {fs_models}（文件存储没挂上，或模型没拷进去）", False
    if word == "CONFLICT":
        return "FAIL", f"{tmp_models} 是普通文件，不覆盖，交人看", False
    if word in ("MISSING", "ELSEWHERE"):
        what = "还没有" if word == "MISSING" else "是指向别处的软链"
        return "FAIL", f"{tmp_models} {what}（cloud up / cloud connect 会建软链指向 {fs_models}）", False
    return "FAIL", f"软链检查没有给出结论（输出：{stdout.strip()[:120]!r}）", False


FS_MOUNT_CHECK_CMD = "mountpoint -q /root/autodl-fs && echo MOUNTED || echo NOT_MOUNTED"
GPU_QUERY_CMD = "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits"


def rent_instructions(cfg_global: dict, provider: str = "autodl") -> list[str]:
    """`up` 连不上时打印的接入指引（纯函数）：AutoDL 租机四项 / Colab 启动指南。"""
    if provider == "colab":
        return [
            "Colab SSH 隧道连不上。可用以下任一方式拉起 Colab 运行时：",
            "  方式 A（全自动，推荐）：首次执行 `cloud connect --provider colab --browser-login` 完成一次 Google 登录，之后 `cloud up --provider colab` 自动无头打开 Notebook、点「全部运行」并刷新隧道。",
            "  方式 B（手动接管）：在 Colab 打开 `tools/ava_colab_bootstrap.ipynb`，选好 GPU（TTS 选 T4；VLM 打标选 L4/A100）运行唯一代码格（`MODE = \"ssh\"`），把输出的隧道地址交给 `cloud connect \"<连接串>\" --provider colab`。",
            "  如隧道受阻，也可改用 `cloud use colab-drive` 走 Google Drive 共享队列模式。",
        ]
    if provider == "colab-drive":
        return [
            "Colab Drive Worker 未在线（未探测到最新心跳）。请确认：",
            "  1. 本机 Google Drive for Desktop 已运行且 `drive_root` 已通过 `cloud connect --drive-root <路径> --provider colab-drive` 绑定",
            "  2. Colab 中 `tools/ava_colab_bootstrap.ipynb` 已选 GPU 并运行唯一代码格（`MODE = \"drive\"`）",
            "  3. Drive 目录下的 `heartbeat/worker.json` 会在 15s 内更新，随后执行 `cloud up --provider colab-drive`",
        ]
    region = cfg_global.get("region", "西北B区")
    image = cfg_global.get("image", "（cloud.json 未写 image）")
    return [
        "实例连不上。实例用完即弃（D102），到算力市场新租一台：",
        f"  · 地区：{region}（模型在这个区的文件存储里，别的区挂不上）",
        f"  · 显存：VLM 打标选 ≥ {MIN_VRAM_GB:g}GB（Qwen3-VL-8B bf16 权重 17.5GB）；只配音不需要大卡（Qwen3-TTS 1.7B 权重约 4GB）",
        f"  · CUDA：≥ {RENT_MIN_CUDA}（镜像是 torch 2.8 + cu128）",
        f"  · 镜像：「我的镜像」里的 {image}",
        "  · 控制台：https://www.autodl.com/market/list",
        "开机后把控制台的 SSH 登录指令（ssh -p … root@…）和这台的按量单价（元/小时）交给 AVA，"
        "走 cloud connect，再 cloud up --rate <单价>（账本按这个数记，不报只能估算）。",
    ]


# 上行前的余量安全系数。为什么是 2×：
#   ① `rsync --partial` 把未传完的文件留在目标盘上，单个大文件在传输中的
#      峰值占用可达其体积的两倍（已存在的一份 + 传了一半的临时件）；
#   ② 目标盘同时躺着 OS、Python 环境与远端 checkout，「刚好够」迟早变成「刚好炸」。
UPLOAD_HEADROOM_FACTOR = 2.0


def sync_pairs_bytes(pairs) -> int:
    """估算上行清单的总体积（字节）。目录递归累加，文件取 stat 大小。"""
    total = 0
    for src, _dst in pairs:
        p = Path(str(src).rstrip("/"))
        try:
            if p.is_dir():
                total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            elif p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def evaluate_upload_capacity(
    upload_bytes: int, avail_gb_str: str, factor: float = UPLOAD_HEADROOM_FACTOR,
) -> tuple[str, str]:
    """上行体积 vs 远端目标盘余量（纯函数）。返回 (判定, 说明)。

    判定三态：
      · `ok`           —— 余量充足；
      · `insufficient` —— 拦下（这是我们要提前发现的那个失败）；
      · `unknown`      —— df 读不到数。**「不知道」不等于「有危险」**：不许
                          假装知道，但也不许拿它当借口拦人，由调用方打 WARN 放行。
    """
    try:
        avail_gb = float(str(avail_gb_str).strip())
    except (TypeError, ValueError):
        return "unknown", f"读不到远端余量（df 输出：{str(avail_gb_str).strip()!r}）"
    need_gb = upload_bytes / (1024 ** 3) * factor
    up_mb = upload_bytes / (1024 ** 2)
    if need_gb > avail_gb:
        return "insufficient", (
            f"本次上行 {up_mb:.1f}MB，按 {factor:g}× 安全系数需 {need_gb:.2f}GB，"
            f"远端仅剩 {avail_gb:.1f}GB"
        )
    return "ok", (
        f"本次上行 {up_mb:.1f}MB（按 {factor:g}× 需 {need_gb:.2f}GB）"
        f"< 远端余量 {avail_gb:.1f}GB"
    )


# 远端模型权重结构校验脚本（任务 0，2026-09-11）
#
# **为什么需要它**：原来的 doctor 只比 `du` 体积，而截断的权重照样能凑够体积——
# 例如 Qwen3-VL 四块共 17.5GB，少一块会掉到 12.6GB 被体积判据抓住，
# 但某一块内部截断 300MB 则体积照样过 15GB 阈值，doctor 一声不呵。
# 体积是存在性证据，不是完整性证据。
#
# 校验策略按格式分派：
#   .safetensors —— 读 8 字节头长 + header JSON，断言 8+header+max(data_offsets) 与文件字节数相等。
#                    这是**字节级**证明：任何截断都会让最大偏移越界。不加载权重，秒级完成。
#   .bin/.pt/.pth —— 新版 PyTorch 用 zip 容器存（torch>=1.6 默认）：is_zipfile 能读出
#                    中央目录即算通过（截断会先毁掉位于末尾的中央目录，自然落到损坏
#                    分支）。不跑 testzip，那要全量读盘（数 GB），而本项目的真实失效
#                    模式是「下载中断导致截断」，不是「字节被篡改导致 CRC 不符」。
#                    裸 pickle（\x80 开头）无内建完整性信息，诚实记为 unverifiable。
# 返回一行 JSON，由 parse_model_structure_result 解析。
_MODEL_STRUCT_PROBE_SCRIPT = r'''
import json, struct, sys, zipfile
from pathlib import Path

root = Path(sys.argv[1])
WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth"}
files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in WEIGHT_SUFFIXES)

checked, bad, unverifiable = 0, [], []
for p in files:
    try:
        if p.suffix == ".safetensors":
            with p.open("rb") as f:
                n = struct.unpack("<Q", f.read(8))[0]
                hdr = json.loads(f.read(n))
            max_end = max(
                (v["data_offsets"][1] for k, v in hdr.items() if k != "__metadata__"),
                default=0,
            )
            expect = 8 + n + max_end
            actual = p.stat().st_size
            if expect != actual:
                bad.append(f"{p.name}: 字节数不符 (期望 {expect}, 实得 {actual})")
            else:
                checked += 1
        elif zipfile.is_zipfile(p):
            size = p.stat().st_size
            with zipfile.ZipFile(p) as z:
                truncated = [
                    i.filename for i in z.infolist()
                    if i.header_offset + i.compress_size > size
                ]
            if truncated:
                bad.append(f"{p.name}: zip 条目越界（截断）{truncated[:2]}")
            else:
                checked += 1
        else:
            # 既不是可读的 zip，就看它到底是「裸 pickle」还是「压根就坏了」。
            # **这一步不能省**：zip 容器被截断时中央目录读不出来，is_zipfile 会返回 False，
            # 若一律归入 unverifiable，就把「损坏」当成「无法校验」报上去——
            # 而下载中断导致的截断正是本项目最真实的失效模式。
            with p.open("rb") as f:
                head = f.read(4)
            if len(head) < 4:
                bad.append(f"{p.name}: 文件过短")
            elif head[:2] == b"PK":
                bad.append(f"{p.name}: zip 容器中央目录不可读（截断）")
            elif head[:1] == b"\x80":
                unverifiable.append(p.name)   # 裸 pickle：无内建完整性信息，不猜
            else:
                bad.append(f"{p.name}: 既非 zip 也非 pickle，格式异常")
    except Exception as e:
        bad.append(f"{p.name}: {type(e).__name__} {e}")

print(json.dumps({"files": len(files), "checked": checked,
                  "bad": bad, "unverifiable": unverifiable}))
'''


def build_model_structure_probe_cmd(
    model_dir: str, models_root: str = "/root/autodl-tmp/models",
    python: str = DEFAULT_REMOTE_PYTHON,
) -> str:
    """生成远端模型结构校验命令（纯函数）。

    脚本经 base64 落地：内联走 ssh 时，脚本里的引号会在 shell → ssh → 远端 shell
    三层之间互相切断（M1b 踩过同样的坑），base64 是唯一不需要在三层引号间走钢练的做法。
    """
    encoded = base64.b64encode(_MODEL_STRUCT_PROBE_SCRIPT.encode("utf-8")).decode("ascii")
    return (
        f"echo {encoded} | base64 -d > /tmp/.ava_model_probe.py && "
        f"{python} /tmp/.ava_model_probe.py '{models_root}/{model_dir}'"
    )


def parse_model_structure_result(stdout: str, returncode: int) -> tuple[str, str]:
    """解析远端结构校验输出 → (状态标签, 描述)。纯函数。

    标签语义：OK=已验证完好；WARN=无法验证（不是「通过」，也不该判失败）；FAIL=确实损坏。
    **三者必须区分**：把 unverifiable 当 OK 是撒谎，当 FAIL 是冤枉。
    """
    if returncode != 0 or not stdout.strip():
        return "WARN", "结构校验未能执行（体积判据不受影响）"
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
    except Exception:
        return "WARN", "结构校验输出无法解析（体积判据不受影响）"

    n_files = int(data.get("files", 0))
    checked = int(data.get("checked", 0))
    bad = list(data.get("bad", []))
    unverifiable = list(data.get("unverifiable", []))

    if bad:
        return "FAIL", f"结构损坏 {len(bad)} 个: {'; '.join(bad[:3])}"
    if n_files == 0:
        return "WARN", "未发现任何权重文件（.safetensors/.bin/.pt/.pth）"
    if checked == 0 and unverifiable:
        return "WARN", f"{len(unverifiable)} 个 pickle 权重无内建完整性信息，无法静态校验"

    desc = f"结构校验 {checked} 文件通过"
    if unverifiable:
        desc += f"（另 {len(unverifiable)} 个 pickle 无法静态校验）"
    return "OK", desc


# ---------------------------------------------------------------------------
# D107：远端代码与本地是否一致（按文件内容哈希，不按 git HEAD）
# ---------------------------------------------------------------------------
# 为什么不比 HEAD：10-17 那台远端仓库停在 545 个 commit 前，靠 rsync 把本地代码推上去才跑通——
# rsync 不动 HEAD，本地又常有未推送 commit 与未提交改动，HEAD 两头都说明不了「跑的是哪份代码」。
# 只比远端任务真正会读的三类：pipeline 源码、config 下的 json（*.local.json 是本机私有，不上传不比）、
# pyproject.toml。脚本只此一份：本地 exec 它、远端 base64 落地后跑它，两头算法不可能分叉。
_CODE_MANIFEST_SCRIPT = r'''
import hashlib, json, sys
from pathlib import Path

GLOBS = ("pipeline/**/*.py", "config/**/*.json", "pyproject.toml")


def manifest(root):
    root = Path(root)
    out = {}
    for pat in GLOBS:
        for f in sorted(root.glob(pat)):
            if f.is_file() and not f.name.endswith(".local.json") and "__pycache__" not in f.parts:
                out[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


if __name__ == "__main__":
    print(json.dumps(manifest(sys.argv[1])))
'''
_manifest_ns: dict[str, Any] = {"__name__": "ava_code_manifest"}
exec(compile(_CODE_MANIFEST_SCRIPT, "<code_manifest>", "exec"), _manifest_ns)


def code_manifest(root: Path) -> dict[str, str]:
    """本地代码清单 {相对路径: sha256}（纯函数，与远端同一段脚本）。"""
    return _manifest_ns["manifest"](root)


def build_code_manifest_cmd(remote_root: str, python: str = DEFAULT_REMOTE_PYTHON) -> str:
    """远端算同一份清单的命令（base64 落地，理由同 build_model_structure_probe_cmd）。"""
    encoded = base64.b64encode(_CODE_MANIFEST_SCRIPT.encode("utf-8")).decode("ascii")
    return (f"echo {encoded} | base64 -d > /tmp/.ava_code_manifest.py && "
            f"{python} /tmp/.ava_code_manifest.py {shlex.quote(remote_root)}")


def compare_code_manifest(local: dict[str, str], remote: dict[str, str]) -> dict[str, list[str]]:
    """→ {missing: 远端缺, differ: 内容不同, extra: 远端多出}（纯函数）。

    extra 不算不一致：rsync 不删远端文件，本地删掉的旧模块会一直留在远端；它们没人 import，
    算失败的话同步完也永远绿不了。
    """
    return {
        "missing": sorted(k for k in local if k not in remote),
        "differ": sorted(k for k in local if k in remote and remote[k] != local[k]),
        "extra": sorted(k for k in remote if k not in local),
    }


def remote_code_drift(host: str, cfg_global: dict, *, provider: str | None = None) -> dict[str, list[str]] | str:
    """远端与本地代码比对：成功返回 compare_code_manifest 的结果；核对不了返回原因字符串。"""
    st = provider_settings(cfg_global, provider) if provider else cfg_global
    remote_root = str(st.get("remote_root", "/root/anime-video-agent"))
    py = remote_python(cfg_global, provider=provider)
    res = _ssh(build_code_manifest_cmd(remote_root, py), host=host, check=False, timeout=90)
    try:
        remote = json.loads(res.stdout.strip().splitlines()[-1]) if res.returncode == 0 else None
    except (ValueError, IndexError):
        remote = None
    if not isinstance(remote, dict):
        return f"远端清单没算出来（returncode={res.returncode}，{(res.stderr or '').strip()[:200] or '无输出'}）"
    if not remote:
        return f"远端 {remote_root} 下一个代码文件都没有（仓库不在？先 cloud fix-env）"
    return compare_code_manifest(code_manifest(paths.ROOT), remote)


def _check_remote_drift(host: str, cfg_global: dict, provider: str) -> dict[str, list[str]] | str:
    """兼容仅接受两参 (host, cfg_global) 的测试桩，仅非 AutoDL 后端传入 provider。"""
    if provider == "autodl":
        return remote_code_drift(host, cfg_global)
    return remote_code_drift(host, cfg_global, provider=provider)


def code_drift_lines(drift: dict[str, list[str]] | str) -> tuple[bool, list[str]]:
    """→ (是否一致, 给人看的行)。纯函数。"""
    if isinstance(drift, str):
        return False, [f"核对不了远端代码版本：{drift}"]
    bad = drift["missing"] + drift["differ"]
    extra = f"（远端另有 {len(drift['extra'])} 个本地已没有的旧文件，不影响）" if drift["extra"] else ""
    if not bad:
        return True, [f"远端代码与本地一致{extra}"]
    shown = [f"缺 {k}" for k in drift["missing"]] + [f"不同 {k}" for k in drift["differ"]]
    return False, [f"远端代码与本地不一致：缺 {len(drift['missing'])} 个、内容不同 {len(drift['differ'])} 个{extra}",
                   *("  " + x for x in shown[:10]), *(["  ……"] if len(shown) > 10 else []),
                   "修法: python -m pipeline.cloud sync-code（把本地 pipeline/ config/ pyproject.toml 推上去）"]


def build_sync_up_files(
    ep_dir: Path, sync_items: list[str], remote_ep_dir: str, remote_root: str
) -> list[tuple[str, str]]:
    """构建上行同步的 (src, dst) 清单（P1-1）。

    目录同步规则：src 必须带尾斜杠 '/'，dst 不带尾斜杠，
    防范 rsync 在远端创建 config/config/ 之类的多重嵌套。
    """
    pairs: list[tuple[str, str]] = []
    for item in sync_items:
        is_config = item.startswith("config")
        src_path = paths.ROOT / item if is_config else ep_dir / item
        if not src_path.exists():
            continue

        item_clean = item.rstrip("/")
        if src_path.is_dir():
            src_str = f"{str(src_path)}/"
            dst_str = f"{remote_root}/{item_clean}" if is_config else f"{remote_ep_dir}/{item_clean}"
        else:
            src_str = str(src_path)
            dst_str = f"{remote_root}/{item}" if is_config else f"{remote_ep_dir}/{item}"

        pairs.append((src_str, dst_str))
    return pairs


def build_sync_down_files(
    ep_dir: Path, sync_items: list[str], remote_ep_dir: str
) -> list[tuple[str, str]]:
    """构建下行拉取的 (src, dst) 清单（P1-1）。

    目录同步规则：远端 src 带尾斜杠 '/'，本地 dst 不带尾斜杠。
    """
    pairs: list[tuple[str, str]] = []
    for item in sync_items:
        item_clean = item.rstrip("/")
        is_dir_hint = item.endswith("/")
        if is_dir_hint:
            src_str = f"{remote_ep_dir.rstrip('/')}/{item_clean}/"
            dst_str = f"{str(ep_dir)}/{item_clean}"
        else:
            src_str = f"{remote_ep_dir.rstrip('/')}/{item}"
            dst_str = f"{str(ep_dir)}/{item}"
        pairs.append((src_str, dst_str))
    return pairs


def validate_task_whitelist(task: str) -> str:
    """校验远端任务白名单。"""
    t = task.strip().lower()
    if t not in ALLOWED_TASKS:
        raise SystemExit(
            f"FAIL 任务 '{task}' 不在允许的远端任务白名单内（当前放行：{sorted(ALLOWED_TASKS)}）。\n"
            f"     远端禁止透传任意 shell 命令，杜绝环境破坏。"
        )
    return t


def build_tmux_launch_command(session_name: str, remote_cmd: str, remote_root: str = "/root") -> str:
    """生成 tmux 后台任务启动命令（纯函数）。

    为什么要把 remote_cmd base64 落地成脚本再跑：
    `tmux new-session -d -s name '<cmd>'` 靠单引号把命令括成一个参数，
    而我们的远端命令里既有单引号（`python -c '...'`）又有双引号，嵌套会当场破裂。
    哪怕只用双引号包，python 字符串里的引号、$、反引号还会继续捣乱。
    base64 是唯一不需要在三个引号层级之间走钢练的做法，顺带还能把脚本留在远端供审计。
    """
    if not session_name or not session_name.startswith("ava-"):
        raise ValueError(f"tmux 会话名必须以 ava- 开头（watchdog 靠这个前缀判活跃）: {session_name!r}")
    script_path = f"{remote_root}/.ava_task_{session_name}.sh"
    # stdout/stderr tee 落盘：进程退出后 pane 一并消失，不留证据就只剩
    # 「跑到某段就没了」一个表象（D162：配音静默退出靠重跑单段才看到成因）。
    # 日志与脚本同路径前缀，tmux 单引号内无嵌套引号，不破坏 base64 方案。
    log_path = f"{remote_root}/.ava_task_{session_name}.log"
    encoded = base64.b64encode(remote_cmd.encode("utf-8")).decode("ascii")
    return (
        f"echo {encoded} | base64 -d > {script_path} && chmod +x {script_path} && "
        f"tmux kill-session -t {session_name} 2>/dev/null; "
        f"tmux new-session -d -s {session_name} '{script_path} 2>&1 | tee {log_path}'"
    )


EXTRA_ARG_RULES: dict[str, str] = {
    "--redo": r"^(?:[\d.,\s]+|stale)$",
    "--floor": r"^\d+(?:\.\d+)?$",
    "--batch": r"^\d+$",
    "--limit": r"^\d+$",
    "--shots-dir": r"^[A-Za-z0-9_./\-]+$",
    "--frames-dir": r"^[A-Za-z0-9_./\-]+$",
    "--out-dir": r"^[A-Za-z0-9_./\-]+$",
}
EXTRA_ARG_BOOLEAN: set[str] = {
    "--apply-patch",
    "--allow-engine-mix",
    "--dry-run",
    "--confirm-cost",
}


def validate_extra_args(extra_args: str | list[str]) -> str:
    """按 flag+值对白名单校验远端命令参数（Spec §2.4 Y3）。"""
    if not extra_args:
        return ""
    if isinstance(extra_args, str):
        if not extra_args.strip():
            return ""
        try:
            tokens = shlex.split(extra_args.strip())
        except ValueError as exc:
            raise ValueError(f"extra_args 无法解析: {exc}") from exc
    else:
        tokens = list(extra_args)

    validated: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        flag = token
        val: str | None = None
        if "=" in token and token.startswith("--"):
            flag, val = token.split("=", 1)

        if flag in EXTRA_ARG_RULES:
            if val is None:
                if i + 1 >= len(tokens):
                    raise ValueError(f"参数 {flag} 缺少值")
                val = tokens[i + 1]
                if val.startswith("--"):
                    raise ValueError(f"参数 {flag} 缺少值")
                i += 2
            else:
                i += 1
            pat = EXTRA_ARG_RULES[flag]
            if not re.fullmatch(pat, val.strip()):
                raise ValueError(f"参数 {flag} 的值非法: {val!r}")
            validated.extend([flag, val.strip()])
        elif flag in EXTRA_ARG_BOOLEAN:
            if val is not None:
                raise ValueError(f"布尔旗标 {flag} 不得带值: {val!r}")
            validated.append(flag)
            i += 1
        else:
            raise ValueError(f"不支持或未授权的远端参数旗标: {flag}")

    return " ".join(validated)


def build_remote_run_command(
    task: str,
    ep_rel_path: str,
    remote_root: str = "/root/anime-video-agent",
    python: str = DEFAULT_REMOTE_PYTHON,
    extra_args: str = "",
    *,
    provider: str = "autodl",
    models_root: str | None = None,
    heartbeat_path: str | None = None,
) -> str:
    """生成远端安全执行命令（P0-1 首尾心跳，P2-5 probe 任务分支，支持 Colab 环境变量注入）。

    `python` 由调用方从配置现取（见 `remote_python`），**不在函数里读配置**：
    这样它仍是纯函数（可测），而「用哪个解释器」这件事只在一个地方决定。
    """
    task = validate_task_whitelist(task)
    hb = heartbeat_path or (COLAB_HEARTBEAT_PATH if provider != "autodl" else WATCHDOG_HEARTBEAT_PATH)
    env_parts: list[str] = []
    if provider != "autodl":
        env_parts.append(f"AVA_CLOUD_PROVIDER={shlex.quote(provider)}")
    if models_root:
        env_parts.append(f"AVA_REMOTE_MODELS={shlex.quote(models_root)}")
    env_prefix = (" ".join(env_parts) + " ") if env_parts else ""

    if task == "probe":
        # probe 探针任务：轻量级验证 GPU/Torch/环境，不拖入未移植的 TTS 模块
        cmd = (
            f"cd {remote_root} && "
            f"touch {hb} && "
            f"mkdir -p {ep_rel_path}/03-audio && "
            f"{env_prefix}{python} -c '"
            f"import torch; "
            f"print(f\"GPU Probe: torch={{torch.__version__}}, cuda={{torch.cuda.is_available()}}\")"
            f"' | tee {ep_rel_path}/03-audio/probe.log && "
            f"touch {hb}"
        )
    elif task == "captions":
        # captions 专用分支（Spec §4.6 R1-r15）：
        # 调用 python -m pipeline.vindex captions，所有路径参数过 shlex.quote
        shots_dir = f"{ep_rel_path}/04-patch/shots"
        frames_dir = f"{ep_rel_path}/04-patch/frames"
        out_dir = f"{ep_rel_path}/04-patch/vindex"
        default_pool = re.sub(r'[^A-Za-z0-9_-]+', '-', Path(ep_rel_path).name) + "-patch"

        pool = default_pool
        keys: list[str] = []
        extra_flags: list[str] = []

        if extra_args:
            tokens = shlex.split(extra_args) if isinstance(extra_args, str) else list(extra_args)
            i = 0
            while i < len(tokens):
                t = tokens[i]
                if t.startswith("--"):
                    if t in EXTRA_ARG_BOOLEAN:
                        extra_flags.append(t)
                        i += 1
                    elif t in EXTRA_ARG_RULES:
                        if i + 1 >= len(tokens):
                            raise ValueError(f"参数 {t} 缺少值")
                        val = tokens[i + 1]
                        if not re.fullmatch(EXTRA_ARG_RULES[t], val):
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
                    if not re.fullmatch(r'^[A-Za-z0-9_-]+$', t):
                        raise ValueError(f"非法池名或集键: {t!r}")
                    if pool == default_pool and not keys:
                        pool = t
                    else:
                        keys.append(t)
                    i += 1

        if not keys:
            keys = ["SP01"]

        key_str = " ".join(shlex.quote(k) for k in keys)
        flags_str = (" " + " ".join(extra_flags)) if extra_flags else ""
        cmd = (
            f"cd {remote_root} && "
            f"touch {hb} && "
            f"{env_prefix}{python} -m pipeline.vindex captions {shlex.quote(pool)} {key_str} "
            f"--shots-dir {shlex.quote(shots_dir)} "
            f"--frames-dir {shlex.quote(frames_dir)} "
            f"--out-dir {shlex.quote(out_dir)}{flags_str} && "
            f"touch {hb}"
        )
    else:
        # tts 必须走云端覆盖层：本地 Mac 只有 mlx（qwen3_tts），云端只有 CUDA（indextts2），
        # 同一个 engine 字段喂不了两边。覆盖层只写差异，其余字段继承 voice.json。
        extra = " --config config/voice.cloud.json" if task == "tts" else ""
        if extra_args:
            validated = validate_extra_args(extra_args)
            if validated:
                extra += f" {validated}"
        cmd = (
            f"cd {remote_root} && "
            f"touch {hb} && "
            f"{env_prefix}{python} -m pipeline.{task} {ep_rel_path}{extra} && "
            f"touch {hb}"
        )
    return cmd


def watchdog_hold_function(hold_path: str = REMOTE_HOLD_PATH) -> str:
    """看门狗里判「人要求保持开机」的 bash 函数（D130）：标记存在、到期字段是整数、且没到期才算。

    单独生成是为了能在测试里真用 bash 跑它（看门狗主循环 sleep 20 跑不完）。标记写坏了按没有处理——
    宁可照常空闲关机，也不因为一个读不懂的文件让实例一直计费。
    """
    return f"""hold_active() {{
    [ -f "{hold_path}" ] || return 1
    local hold_start hold_until
    read -r hold_start hold_until < "{hold_path}" || return 1
    case "$hold_until" in ''|*[!0-9]*) return 1 ;; esac
    [ "$(date +%s)" -lt "$hold_until" ]
}}"""


def generate_watchdog_script(
    idle_seconds: int = DEFAULT_IDLE_SHUTDOWN_SECONDS,
    heartbeat_path: str = WATCHDOG_HEARTBEAT_PATH,
    hold_path: str = REMOTE_HOLD_PATH,
    *,
    unassign_path: str | None = None,
    log_path: str = "/root/watchdog.log",
) -> str:
    """生成远端自动关机 / Colab 运行时释放 watchdog 脚本（P0-1 核心修复）。

    判据（S1 测真实状态）：
    循环内必须先检查 `tmux ls 2>/dev/null | grep -q '^ava-'`。
    只要存在以 `ava-` 开头的活跃会话，说明有任务正在后台执行，绝对不可关机或释放！

    关机/释放命令：
    - AutoDL：`unassign_path` 为 None 时调 `/usr/bin/shutdown -h now`；
    - Colab：`unassign_path` 指定时写释放信号文件（由 Colab sentinel / worker 调 `google.colab.runtime.unassign()`）。
    """
    if unassign_path:
        action_block = (
            f'            touch "{unassign_path}"\n'
            f'            echo "$(date \'+%F %T\') [watchdog] unassign 信号已写入 {unassign_path}" >> "$LOG"'
        )
    else:
        action_block = (
            '            if /usr/bin/shutdown -h now >> "$LOG" 2>&1; then\n'
            '                echo "$(date \'+%F %T\') [watchdog] shutdown 已下发" >> "$LOG"\n'
            '            else\n'
            '                echo "$(date \'+%F %T\') [watchdog] shutdown 下发失败，需人工到控制台关机" >> "$LOG"\n'
            '            fi'
        )
    return f"""#!/bin/bash
# AVA Remote Watchdog: AutoDL/Colab idle shutdown with active task guard
HEARTBEAT_FILE="{heartbeat_path}"
IDLE_LIMIT={idle_seconds}
LOG="{log_path}"
{watchdog_hold_function(hold_path)}

touch "$HEARTBEAT_FILE"
while true; do
    sleep 20
    # S1 真实状态检查：只要有 ava- 开头的 tmux 任务活着，就绝不关机，刷新心跳
    if tmux ls 2>/dev/null | grep -q '^ava-'; then
        touch "$HEARTBEAT_FILE"
        continue
    fi
    # D130 人要求保持开机（cloud hold），未到期就刷新心跳；过期后从到期那一刻起照常计空闲
    if hold_active; then
        touch "$HEARTBEAT_FILE"
        continue
    fi

    if [ -f "$HEARTBEAT_FILE" ]; then
        now=$(date +%s)
        mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || stat -f %m "$HEARTBEAT_FILE" 2>/dev/null || echo "$now")
        idle=$((now - mtime))
        if [ "$idle" -ge "$IDLE_LIMIT" ]; then
            echo "$(date '+%F %T') [watchdog] Idle $idle s >= $IDLE_LIMIT s, no ava-* task. Shutting down." >> "$LOG"
{action_block}
            exit 0
        fi
    fi
done
"""


def _watchdog_proc_pattern(script_path: str) -> str:
    """返回精确锚定的 watchdog 进程命令行正则（纯函数）。

    必须锚定：包装部署命令的 `bash -c` 进程，其命令行里含有脚本路径文字，
    不锚定就会连同自己一起被 pkill 杀掉（ssh 返回 255，静默失效）。
    点号转义，避免正则通配。
    """
    import re as _re

    return "^/bin/bash " + _re.escape(script_path) + "$"


def build_watchdog_deploy_command(
    watchdog_content: str,
    heartbeat_path: str = WATCHDOG_HEARTBEAT_PATH,
    script_path: str = REMOTE_WATCHDOG_PATH,
    log_path: str = "/root/watchdog.log",
) -> str:
    """生成 watchdog 部署命令：**必须先 kill 旧进程再起新进程**（纯函数）。

    2026-09-11 实测事故（不可省略此步的理由）：
    bash 是按文件偏移量**流式**读取脚本的，不把整个脚本缓存在内存里。
    用 `pgrep -f watchdog.sh >/dev/null || (启动新进程)` 的写法时，脚本文件被 heredoc
    重写（截断+写入）而旧进程仍按旧偏移继续读 → 读到的是新文件里错位的内容，
    于是循环里反复执行 `touch ''`（HEARTBEAT_FILE 展开为空），永不进入关机分支。
    结果：下载任务 00:52 结束后 watchdog 该在 00:57 关机，实际空转到次日 08:28。
    教训：重写运行中的脚本文件本身就是错误操作，必须 kill 掉旧进程再起新的。

    另一个坑：`pkill -f` / `pgrep -f` 匹配的是**完整命令行**，而这条部署命令本身是通过
    `bash -c "<cmd>"` 执行的，命令字符串里就含有脚本路径文字 → pkill 会匹配到包装进程本身
    并把它杀掉，ssh 连接当场断开（表现为 ssh 返回码 255、stderr 为空，极难定位）。
    2026-09-11 实测：光把 pkill 自己的模式写成 `[b]ash` 字符类是不够的——命令里还有
    `setsid /bin/bash <script>` 这段裸文本，照样会自匹配。
    因此改用**锚定正则**：模式必须是整条命令行恰为 `/bin/bash <script>`，
    包装进程那串长命令行（以 `bash -c` 开头）就永远匹配不上了。

    必须用 setsid 而非 nohup（2026-09-11 实测）：nohup 只忽略 SIGHUP，但 sshd 在会话
    结束时清理的是**整个进程组**，后台进程会一起被杀 → watchdog 静默地根本没起来。
    setsid 开新会话、脱离该进程组，实测在 ssh 断开后仍存活。
    `< /dev/null` 也是必需的：否则新会话会继续持有 ssh 的 stdin。
    """
    pattern = "'" + _watchdog_proc_pattern(script_path) + "'"
    return (
        f"cat << 'AVA_WD_EOF' > {script_path}\n{watchdog_content}\nAVA_WD_EOF\n"
        f"chmod +x {script_path} && "
        f"pkill -f {pattern} 2>/dev/null; sleep 1; "
        f"touch {heartbeat_path} && "
        f"(setsid /bin/bash {script_path} >> {log_path} 2>&1 < /dev/null &) && "
        f"sleep 2 && pgrep -f {pattern}"
    )


def calculate_session_cost(duration_seconds: float, hourly_rate_cny: float) -> float:
    """计算会话实际成本（元），保留 4 位小数。"""
    return round(float(duration_seconds) * (hourly_rate_cny / 3600.0), 4)


def format_ledger_entry(
    session_id: str,
    up_time: str,
    down_time: str,
    gpu_seconds: int,
    cost_cny: float,
    episode: str | None,
    tasks: list[str],
    budget_cny: float,
    mode: str = "gpu",
    note: str | None = None,
    gpu: str | None = None,
    rate_cny: float | None = None,
    *,
    provider: str | None = None,
    billing_unit: str = "cny",
) -> dict[str, Any]:
    """格式化账簿单行 JSON 对象（支持 cny 与 Colab cu 双单位）。

    note 用于标注时长的可信度：实例已被 watchdog 自动关机时，down 根本联系不上远端，
    只能把时长算到「执行 down 的此刻」——这会多算一段关机到发现的延迟，
    属于**上界估计**。记进 note 而不是假装精确。
    """
    budget_scoped = bool(episode) and episode != "unknown"
    over_budget = budget_scoped and cost_cny > budget_cny
    is_cu = billing_unit == "cu"
    entry: dict[str, Any] = {
        "session": session_id,
        "mode": mode,
        "up": up_time,
        "down": down_time,
        "gpu_seconds": int(gpu_seconds),
        "cny": 0.0 if is_cu else round(cost_cny, 4),
        "episode": episode or "unknown",
        "tasks": tasks,
        "over_budget": over_budget,
        "budget_scoped": budget_scoped,
    }
    if is_cu:
        entry["unit"] = "cu"
        entry["cu"] = round(cost_cny, 4)
        entry["provider"] = provider or "colab"
    elif provider and provider != "autodl":
        entry["provider"] = provider
    # D102：卡型不固定，记实际用的卡与按哪个单价算的（估算与否写在 note）
    if gpu:
        entry["gpu"] = gpu
    if rate_cny is not None:
        if is_cu:
            entry["rate_cu"] = rate_cny
        else:
            entry["rate_cny"] = rate_cny
    if note:
        entry["note"] = note
    return entry


def parse_ledger(ledger_text: str) -> tuple[float, int, list[dict]]:
    """解析账簿文本，向 stderr 报告损坏行，不静默吞异常（P2-3）。"""
    records: list[dict] = []
    total_cny = 0.0
    total_sec = 0
    for line in ledger_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            records.append(r)
            total_cny += float(r.get("cny", 0.0))
            total_sec += int(r.get("gpu_seconds", 0))
        except Exception as e:
            print(f"WARN 账簿行损坏无法解析，已跳过: {line} ({e})", file=sys.stderr)
    return round(total_cny, 4), total_sec, records


def parse_ledger_summary(ledger_text: str) -> dict[str, Any]:
    """按双计费单位（cny / cu）与提供商汇总账簿记录（纯函数）。"""
    total_cny, total_sec, records = parse_ledger(ledger_text)
    total_cu = 0.0
    by_provider: dict[str, dict[str, Any]] = {}
    for r in records:
        p = str(r.get("provider") or "autodl")
        cu_val = float(r.get("cu", 0.0))
        cny_val = float(r.get("cny", 0.0))
        sec_val = int(r.get("gpu_seconds", 0))
        total_cu += cu_val
        bucket = by_provider.setdefault(p, {"sessions": 0, "seconds": 0, "cny": 0.0, "cu": 0.0})
        bucket["sessions"] += 1
        bucket["seconds"] += sec_val
        bucket["cny"] = round(bucket["cny"] + cny_val, 4)
        bucket["cu"] = round(bucket["cu"] + cu_val, 4)
    return {
        "total_cny": total_cny,
        "total_cu": round(total_cu, 4),
        "total_sec": total_sec,
        "records": records,
        "by_provider": by_provider,
    }


# ---------------------------------------------------------------------------
# 第二节：SSH / Rsync 执行薄壳
# ---------------------------------------------------------------------------


def _extract_provider_from_extra(extra: list[str]) -> tuple[str | None, list[str]]:
    """从透传参数 `extra` 里剥离 `--provider <p>` 或 `--provider=<p>`（纯函数），避免进入白名单参数检查。"""
    found: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(extra):
        tok = extra[i]
        if tok == "--provider":
            if i + 1 >= len(extra):
                raise ValueError("--provider 缺少值")
            found = extra[i + 1]
            i += 2
        elif tok.startswith("--provider="):
            found = tok.split("=", 1)[1]
            i += 1
        else:
            rest.append(tok)
            i += 1
    return found, rest


def _resolve_drive_root(st: dict, prof: dict) -> Path | None:
    """获取 colab-drive 的本地挂载根目录（profile 配置优先，其次自动探测 Google Drive for Desktop）。"""
    raw = prof.get("drive_root")
    if raw:
        return Path(os.path.expanduser(str(raw)))
    return cloud_drive.detect_drive_root(str(st.get("drive_queue_subpath") or "ava_cloud"))


def _cloud_context(args: argparse.Namespace | None = None, *, cli_provider: str | None = None) -> dict[str, Any]:
    """统一解析当前命令的提供商上下文：cfg_global, cfg_local, provider, settings, rpaths, profile, host, unit, drive_root。"""
    cfg_global, cfg_local = load_cloud_config()
    raw_cli = cli_provider if cli_provider is not None else getattr(args, "provider", None)
    try:
        provider = resolve_provider(cfg_global, cfg_local, raw_cli)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    st = provider_settings(cfg_global, provider)
    rpaths = provider_remote_paths(cfg_global, provider)
    prof = provider_profile(cfg_local, provider)
    host = provider_ssh_host(cfg_global, cfg_local, provider)
    unit = billing_unit_of(cfg_global, provider)
    drive_root = _resolve_drive_root(st, prof) if provider == "colab-drive" else None
    return {
        "cfg_global": cfg_global,
        "cfg_local": cfg_local,
        "provider": provider,
        "settings": st,
        "rpaths": rpaths,
        "profile": prof,
        "host": host,
        "unit": unit,
        "drive_root": drive_root,
    }


def _require_drive_root(ctx: dict[str, Any]) -> Path:
    """确保 colab-drive 的本地 Drive 目录可用；不可达则给出明确指引并 SystemExit。"""
    drive_root = ctx.get("drive_root")
    if not cloud_drive.is_drive_reachable(drive_root):
        hint = (
            f"当前 drive_root={drive_root}"
            if drive_root is not None
            else "未探测到 Google Drive for Desktop 挂载盘（~/Library/CloudStorage/GoogleDrive-*/My Drive）"
        )
        raise SystemExit(
            f"FAIL colab-drive 队列目录不可用（{hint}）。\n"
            "     修法: python -m pipeline.cloud connect --provider colab-drive --drive-root \"<GoogleDrive同步目录>/ava_cloud\""
        )
    assert drive_root is not None
    return drive_root


def _require_local_cloudflared(prof: dict) -> None:
    """若 Colab SSH 配置采用 cloudflared ProxyCommand，先检查本机是否已装 cloudflared。"""
    if prof.get("proxy_type") == "cloudflared" and not shutil.which("cloudflared"):
        raise SystemExit(
            "FAIL 本机未安装 cloudflared（Colab Cloudflare 隧道需要 ProxyCommand cloudflared）。\n"
            "     修法（macOS）: brew install cloudflared\n"
            "     或切换零隧道队列模式: python -m pipeline.cloud use colab-drive"
        )


def _ssh_host(cfg_local: dict, provider: str | None = None) -> str:
    """获取 SSH 连接目标 Host 别名（定义在 data/cloud/ssh_config，没有该文件时走 ~/.ssh/config）。"""
    if provider == "colab":
        return str(provider_profile(cfg_local, "colab").get("ssh_host") or "ava-colab")
    return str(cfg_local.get("ssh_host") or provider_profile(cfg_local, "autodl").get("ssh_host") or "autodl")


def ssh_config_file() -> Path:
    """`cloud connect` 生成的 ssh 配置（D102）。在 data/ 下：本机事实，不进 git。"""
    return paths.DATA / "cloud" / "ssh_config"


def known_hosts_file() -> Path:
    return paths.DATA / "cloud" / "known_hosts"


def ssh_base() -> list[str]:
    """所有 ssh 调用的开头：有 `data/cloud/ssh_config` 就 `-F` 指向它，没有就退回旧行为（~/.ssh/config）。

    每次调用现查文件：connect 重写之后，下一条 ssh 立刻连新实例。
    """
    try:
        present = ssh_config_file().is_file()
    except OSError:
        present = False
    return ["ssh", "-F", str(ssh_config_file())] if present else ["ssh"]


def _ssh(
    cmd: str,
    host: str = "autodl",
    check: bool = True,
    timeout: int = 60,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """subprocess 封装 ssh。

    Host 是 ssh_config 里的 autodl 别名（ControlMaster 复用在 config 层解决，代码不重复造）。
    """
    ssh_args = [*ssh_base(), "-o", "BatchMode=yes", host, cmd]
    res = subprocess.run(
        ssh_args,
        capture_output=capture_output,
        text=True,
        timeout=timeout,
    )
    if check and res.returncode != 0:
        err = res.stderr.strip() if res.stderr else f"Exit code {res.returncode}"
        raise SystemExit(f"FAIL 远端 SSH 执行失败: {err}\n     命令: {cmd}")
    return res


def is_ssh_reachable(host: str = "autodl") -> bool:
    """探测 SSH 连通性。"""
    try:
        res = _ssh("true", host=host, check=False, timeout=8)
        return res.returncode == 0
    except Exception:
        return False


def remote_active_tasks(host: str = "autodl") -> list[str]:
    """查询远端正在运行的 ava-* 后台 tmux 任务列表（纯查询，~8 行，Spec §4.3 r10）。"""
    tmux_check = _ssh("tmux ls 2>/dev/null | grep '^ava-' || true", host=host, check=False)
    return [ln.split(":")[0].strip() for ln in tmux_check.stdout.splitlines() if ln.strip().startswith("ava-")]


def run_rsync(
    src: str,
    dst: str,
    extra_excludes: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """执行安全 rsync 增量同步。"""
    excludes = [
        "*.local.json",
        ".git",
        "__pycache__",
        "*.pyc",
        ".DS_Store",
    ]
    if extra_excludes:
        excludes.extend(extra_excludes)

    exclude_args = []
    for ex in excludes:
        exclude_args.extend(["--exclude", ex])

    cmd = [
        "rsync",
        "-avz",
        "--partial",
        "--checksum",
        "-e",
        " ".join(shlex.quote(a) for a in ssh_base()),
        *exclude_args,
        src,
        dst,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


_ORIGINAL_RUN_RSYNC = run_rsync


# ---------------------------------------------------------------------------
# 第三节：CLI 命令实现
# ---------------------------------------------------------------------------


def get_active_session_file() -> Path:
    paths.require_data()
    cloud_dir = paths.DATA / "cloud"
    cloud_dir.mkdir(parents=True, exist_ok=True)
    return cloud_dir / "active_session.json"


def get_ledger_file() -> Path:
    paths.require_data()
    cloud_dir = paths.DATA / "cloud"
    cloud_dir.mkdir(parents=True, exist_ok=True)
    return cloud_dir / "ledger.jsonl"


def cmd_exec(args: argparse.Namespace) -> int:
    """远端执行核心：一律 tmux 包裹后台化，仅 --fg 允许直连短命令。"""
    ctx = _cloud_context(args)
    if ctx["provider"] == "colab-drive":
        raise SystemExit("FAIL colab-drive 为无隧道队列模式，不支持 exec；请改用 run / sync-code 或切换到 --provider colab")
    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    rpaths = ctx["rpaths"]
    remote_cmd = args.command.strip()

    if not is_ssh_reachable(host):
        raise SystemExit(f"FAIL 远端实例不可达 ({host})。请先运行 python -m pipeline.cloud up 确认开机。")

    # 1. 前台直连通道（严格限制 <60s 探针短命令）
    if args.fg:
        print(f"[*] 前台直连执行 (限短命令): {remote_cmd}")
        try:
            # BatchMode 与 _ssh 同标配（公钥失效必须立刻失败，不许挂交互）；
            # timeout 兑现上面「限 <60s」的承诺——裸 run 会把挂死的远端命令
            # 变成本地进程永久挂起（2026-09-16 审计 R2）
            p = subprocess.run([*ssh_base(), "-o", "BatchMode=yes", host, remote_cmd],
                               timeout=60)
        except subprocess.TimeoutExpired:
            print("FAIL 前台命令 60s 未返回，已掐断——长任务去掉 --fg 走 tmux 通道",
                  file=sys.stderr)
            return 124
        return p.returncode

    # 2. 默认后台 tmux 通道
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"ava-{ts}"
    tmux_cmd, log_path = build_exec_tmux_command(
        remote_cmd,
        session_name,
        log_dir=rpaths["remote_log_dir"],
        heartbeat_path=rpaths["heartbeat_path"],
    )

    _ssh(tmux_cmd, host=host, check=True)

    print("=" * 65)
    print(f"OK    远端后台任务已启动 (tmux 会话: {session_name})")
    print(f"      远端命令: {remote_cmd}")
    print(f"      日志文件: {log_path}")
    print(f"      查看进度: python -m pipeline.cloud logs {session_name} -f")
    print("=" * 65)
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """查看或追踪远端 tmux 任务日志（colab-drive 读取 Drive worker.log）。"""
    ctx = _cloud_context(args)
    session_name = args.session.strip()
    if session_name.endswith(".log"):
        session_name = session_name[:-4]

    if ctx["provider"] == "colab-drive":
        drive_root = _require_drive_root(ctx)
        jobs = cloud_drive.list_drive_jobs(drive_root)
        job_id = session_name
        if not any(j["job_id"] == job_id for j in jobs):
            # 允许传 task/ava-tts 等模糊前缀匹配最近任务
            task_hint = session_name.removeprefix("ava-")
            match = cloud_drive.latest_job_for(drive_root, task=task_hint) or (jobs[0] if jobs else None)
            if not match:
                print("FAIL Drive 队列中没有任何任务日志", file=sys.stderr)
                return 1
            job_id = match["job_id"]
        if not args.follow:
            content = cloud_drive.read_drive_job_logs(drive_root, job_id, lines=args.lines)
            if content is None:
                print(f"FAIL 未找到任务 {job_id} 的 worker.log（可能尚未被 Colab Worker 接单）", file=sys.stderr)
                return 1
            print(content, end="")
            return 0
        seen_len = 0
        while True:
            content = cloud_drive.read_drive_job_logs(drive_root, job_id, lines=0) or ""
            if len(content) > seen_len:
                print(content[seen_len:], end="", flush=True)
                seen_len = len(content)
            cur = next((j for j in cloud_drive.list_drive_jobs(drive_root) if j["job_id"] == job_id), None)
            if cur and cur["status"].get("state") in ("succeeded", "failed"):
                return 0 if cur["status"].get("state") == "succeeded" else 1
            time.sleep(3)

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    log_path = f"{ctx['rpaths']['remote_log_dir']}/{session_name}.log"

    tail_opts = f"-n {args.lines}"
    if args.follow:
        tail_opts += " -f"

    cmd = f"tail {tail_opts} {log_path}"
    if args.follow:
        return subprocess.call([*ssh_base(), host, cmd])
    else:
        res = _ssh(cmd, host=host, check=False)
        if res.returncode == 0:
            print(res.stdout, end="")
            return 0
        else:
            print(f"FAIL 无法读取远端日志 {log_path}: {res.stderr.strip()}", file=sys.stderr)
            return res.returncode


def fs_models_dir(cfg_global: dict) -> str:
    return str(cfg_global.get("fs_models") or DEFAULT_FS_MODELS)


def ensure_models_link(host: str, cfg_global: dict) -> bool:
    """远端建 / 核模型软链（D102 第三节 B），打印一行结论；返回是否可用。"""
    fs = fs_models_dir(cfg_global)
    tmp = cfg_global.get("remote_models", "/root/autodl-tmp/models")
    res = _ssh(models_link_script(fs, tmp), host=host, check=False)
    tag, desc, ok = evaluate_models_link(res.stdout, fs, tmp)
    print(f"{tag:<5} [MODELS] {desc}")
    return ok


def forget_known_host(conn: dict) -> None:
    """作废项目 known_hosts 里这个「主机:端口」的旧密钥。

    AutoDL 各实例共用同一个 connect 主机、按端口区分，端口回收后会分给别的实例；
    connect 本身表示人换了一台实例，旧密钥留着只会让 accept-new 当场拒连。
    """
    kh = known_hosts_file()
    if not kh.is_file():
        return
    if conn.get("proxy_type") == "cloudflared":
        subprocess.run(["ssh-keygen", "-R", conn["server"], "-f", str(kh)],
                       capture_output=True, text=True)
    else:
        subprocess.run(["ssh-keygen", "-R", f"[{conn['server']}]:{int(conn['port'])}", "-f", str(kh)],
                       capture_output=True, text=True)


def cmd_use(args: argparse.Namespace) -> int:
    """切换默认云算力后端（Code Freeze 例外 ④：只改 cloud.local.json 的 active_provider，保留各后端 profiles）。"""
    try:
        target = resolve_provider(cli_provider=args.provider_name)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    paths.require_data()
    f_local = paths.CONFIG / "cloud.local.json"
    local = json.loads(f_local.read_text(encoding="utf-8")) if f_local.exists() else {}
    merged = merge_active_provider(local, target)
    paths.atomic_write(f_local, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    cfg_global, _ = load_cloud_config()
    unit = billing_unit_of(cfg_global, target).upper()
    prof = provider_profile(merged, target)
    if target == "colab-drive":
        detail = prof.get("drive_root") or "待 cloud connect --drive-root 绑定 / 自动探测"
    else:
        detail = f"host={prof.get('ssh_host')}" + (f" ({prof['server']})" if prof.get("server") else "（尚未 connect）")
    print(f"[OK] 当前默认云端后端已切换为: {target}（计费单位: {unit}，{detail}）")
    return 0


def _apply_ssh_profile_connection(
    cfg_global: dict,
    local: dict,
    f_local: Path,
    provider: str,
    conn: dict,
    *,
    alias: str | None = None,
) -> tuple[dict, str, str]:
    """持久化 SSH/cloudflared 连接配置并重绘 data/cloud/ssh_config。"""
    resolved_alias = alias or str(
        provider_profile(local, provider).get("ssh_host")
        or provider_settings(cfg_global, provider).get("ssh_host")
        or ("ava-colab" if provider == "colab" else "autodl")
    )
    merged = merge_profile_connection(local, provider, conn, ssh_host=resolved_alias)
    paths.atomic_write(f_local, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    ssh_config_file().parent.mkdir(parents=True, exist_ok=True)
    forget_known_host(conn)
    rendered = (
        render_all_ssh_configs(cfg_global, merged, known_hosts_file())
        or render_ssh_config(resolved_alias, conn, known_hosts_file())
    )
    paths.atomic_write(ssh_config_file(), rendered)
    endpoint = (
        f"{conn['user']}@{conn['server']}"
        if conn.get("proxy_type") == "cloudflared"
        else f"{conn['user']}@{conn['server']}:{conn['port']}"
    )
    return merged, resolved_alias, endpoint


def cmd_connect(args: argparse.Namespace) -> int:
    """接入/换实例（D102 + Colab）：解析 SSH/cloudflared 或绑定 Drive 共享队列，写入对应 profile。

    Code Freeze 第 ④ 条例外：只写 `config/cloud.local.json` 的连接字段，其余键原样保留。
    输出不回显该文件的其他内容。
    """
    cfg_global, cfg_local = load_cloud_config()
    try:
        provider = resolve_provider(cfg_global, cfg_local, getattr(args, "provider", None))
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc

    drive_root_arg = getattr(args, "drive_root", None)
    if drive_root_arg and getattr(args, "provider", None) is None:
        provider = "colab-drive"

    paths.require_data()
    f_local = paths.CONFIG / "cloud.local.json"
    local = json.loads(f_local.read_text(encoding="utf-8")) if f_local.exists() else {}

    if provider == "colab-drive":
        st = provider_settings(cfg_global, "colab-drive")
        if drive_root_arg:
            try:
                drive_root = cloud_drive.validate_drive_root(drive_root_arg)
            except ValueError as exc:
                raise SystemExit(f"FAIL {exc}") from exc
        else:
            drive_root = cloud_drive.detect_drive_root(str(st.get("drive_queue_subpath") or "ava_cloud"))
            if drive_root is None:
                raise SystemExit(
                    "FAIL 未自动探测到 Google Drive for Desktop 本地目录。\n"
                    "     请显式传入: python -m pipeline.cloud connect --provider colab-drive --drive-root \"<Drive目录>/ava_cloud\""
                )
        cloud_drive.ensure_drive_layout(drive_root)
        merged = merge_profile_connection(local, "colab-drive", {"drive_root": str(drive_root)})
        paths.atomic_write(f_local, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
        print(f"[OK] Colab Drive 共享队列已绑定: {drive_root}")
        hb = cloud_drive.parse_worker_heartbeat(drive_root)
        if hb and hb.get("online"):
            print(f"[OK] Colab Worker 在线：GPU={hb.get('gpu') or '未知'}（心跳延迟 {hb.get('stale_s')}s）")
        else:
            print("[*] 当前未检测到在线 Worker 心跳，请确保 Colab Bootstrap Notebook (MODE=\"drive\") 已运行")
        if merged.get("active_provider", cfg_global.get("provider", "autodl")) != "colab-drive":
            print("[*] 提示: 默认后端仍未切换，如需默认走 Drive 队列请执行: python -m pipeline.cloud use colab-drive")
        return 0

    if getattr(args, "browser_login", False):
        if provider != "colab":
            raise SystemExit("FAIL --browser-login 仅支持 --provider colab")
        nb_url = cloud_colab_boot.resolve_colab_notebook_url(
            cfg_global, local, getattr(args, "notebook_url", None)
        )
        prof_dir = cloud_colab_boot.colab_browser_profile_dir()
        try:
            cloud_colab_boot.interactive_browser_login(
                nb_url,
                profile_dir=prof_dir,
                timeout_s=int(getattr(args, "timeout", 300) or 300),
            )
        except RuntimeError as exc:
            raise SystemExit(f"FAIL {exc}") from exc
        merged = merge_profile_connection(local, "colab", {"notebook_url": nb_url}, ssh_host=None)
        paths.atomic_write(f_local, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
        print(f"[OK] Google/Colab 登录态已保存到隔离浏览器目录: {prof_dir}")
        print("[*] 之后执行 `python -m pipeline.cloud up --provider colab` 可自动无头拉起 Colab 并接通 SSH。")
        return 0

    login_text = (getattr(args, "login", None) or "").strip()
    if not login_text:
        extra = "；Colab 首次开启自动拉起可运行: cloud connect --provider colab --browser-login" if provider == "colab" else ""
        raise SystemExit(f"FAIL {provider} 需要提供控制台登录指令或隧道主机，如: cloud connect \"ssh -p ...\" --provider {provider}{extra}")
    try:
        conn = parse_colab_connect_target(login_text) if provider == "colab" else parse_login_command(login_text)
    except ValueError as exc:
        raise SystemExit(f"FAIL 登录指令解析不出：{exc}") from exc

    nb_override = (getattr(args, "notebook_url", None) or "").strip()
    if nb_override and provider == "colab":
        conn["notebook_url"] = nb_override

    merged, alias, endpoint = _apply_ssh_profile_connection(cfg_global, local, f_local, provider, conn)
    print(f"[OK] {provider} 连接信息已更新：{endpoint}（别名 {alias}）")

    _require_local_cloudflared(conn)
    if not is_ssh_reachable(alias):
        print(f"FAIL 连不上 {endpoint}。实例刚开机可能还没起来，过半分钟再 connect 一次；"
              "仍不通就确认隧道服务（sshd / cloudflared）是否已就绪", file=sys.stderr)
        return 1
    print("[OK] SSH 连通")
    if provider == "autodl":
        return 0 if ensure_models_link(alias, cfg_global) else 1
    gpu_res = _ssh(GPU_QUERY_CMD, host=alias, check=False)
    tag, desc, ok = evaluate_doctor_gpu(gpu_res.stdout, gpu_res.returncode, provider=provider)
    print(f"{tag:<5} [GPU] {desc}")
    if merged.get("active_provider", cfg_global.get("provider", "autodl")) != "colab":
        print("[*] 提示: 如需默认走 Colab，请执行: python -m pipeline.cloud use colab")
    return 0 if ok else 1


def _doctor_colab_drive(ctx: dict[str, Any]) -> int:
    """colab-drive 后端诊断：本地 Drive 目录、Worker 心跳、代码一致性、GPU 与模型报告。"""
    cfg_global = ctx["cfg_global"]
    drive_root = ctx.get("drive_root")
    print("=" * 65)
    print("  Colab Drive 队列诊断 (pipeline.cloud doctor --provider colab-drive)")
    print("=" * 65)
    all_passed = True

    if cloud_drive.is_drive_reachable(drive_root):
        cloud_drive.ensure_drive_layout(drive_root)
        print(f"OK    [DRIVE] Drive 队列目录可读写: {drive_root}")
    else:
        print(f"FAIL  [DRIVE] Drive 队列目录不可用: {drive_root}")
        print("      修法: 确认 Google Drive for Desktop 已运行，并执行 cloud connect --provider colab-drive --drive-root <路径>")
        return 1

    mf = cloud_drive.read_drive_code_manifest(drive_root)
    if isinstance(mf, str):
        all_passed = False
        print(f"FAIL  [CODE] {mf}")
    else:
        drift = compare_code_manifest(code_manifest(paths.ROOT), mf)
        code_ok, code_lines = code_drift_lines(drift)
        print(f"{'OK' if code_ok else 'FAIL':<5} [CODE] {code_lines[0]}")
        for line in code_lines[1:]:
            print(f"      {line}")
        if not code_ok:
            all_passed = False

    hb = cloud_drive.parse_worker_heartbeat(drive_root)
    if not hb:
        all_passed = False
        print("FAIL  [WORKER] 尚未检测到 Colab Worker 心跳 (heartbeat/worker.json)")
        print("      修法: 在 Colab 运行 tools/ava_colab_bootstrap.ipynb 唯一代码格 (MODE = \"drive\")")
    elif not hb.get("online"):
        all_passed = False
        print(f"FAIL  [WORKER] Colab Worker 心跳已过期（最后上报于 {hb.get('stale_s')}s 前）")
        print("      修法: 检查 Colab 运行时是否已断开，并在 Colab 重跑 Bootstrap 唯一代码格")
    else:
        print(f"OK    [WORKER] Colab Worker 在线（心跳延迟 {hb.get('stale_s')}s，活跃任务: {hb.get('active_job') or '无'}）")
        gpu_str = str(hb.get("gpu") or "")
        vram_mib = hb.get("gpu_mem_mib")
        raw_gpu = f"{gpu_str}, {int(vram_mib)}" if (gpu_str and vram_mib) else ""
        g_tag, g_desc, g_ok = evaluate_doctor_gpu(raw_gpu, 0 if raw_gpu else 1, provider="colab-drive")
        print(f"{g_tag:<5} [GPU] {g_desc}")
        if not g_ok:
            all_passed = False
        if hb.get("torch_cuda"):
            print(f"OK    [TORCH] PyTorch {hb.get('torch_version', '?')}, CUDA可用=True")
        else:
            all_passed = False
            print(f"FAIL  [TORCH] PyTorch CUDA 不可用 ({hb.get('torch_version', '未知')})")
        if hb.get("disk_free_gb") is not None:
            d_tag, d_desc, d_ok = evaluate_doctor_disk(str(hb["disk_free_gb"]), disk_path=ctx["rpaths"]["remote_data_disk"])
            print(f"{d_tag:<5} [DISK] {d_desc}")
            if not d_ok:
                all_passed = False
        reported_models = hb.get("models") or {}
        for m_id in cfg_global.get("models", {}):
            st_m = reported_models.get(m_id)
            if st_m == "ok":
                print(f"OK    [MODEL] {m_id}")
            elif st_m:
                all_passed = False
                print(f"FAIL  [MODEL] {m_id}: {st_m}")
            else:
                print(f"INFO  [MODEL] {m_id}（尚未上报；Colab 按需从 HuggingFace 拉取）")

    print("=" * 65)
    print("[✓] Drive 队列诊断完成。" if all_passed else "[✗] 存在未通过检查项，请依据上方提示处理。")
    return 0 if all_passed else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """环境自检诊断命令（逐项检查，支持 AutoDL / Colab SSH / Colab Drive）。"""
    ctx = _cloud_context(args)
    provider = ctx["provider"]
    if provider == "colab-drive":
        return _doctor_colab_drive(ctx)

    cfg_global = ctx["cfg_global"]
    rpaths = ctx["rpaths"]
    host = ctx["host"]
    remote_root = rpaths["remote_root"]
    models_root = rpaths["remote_models"]
    py_exe = rpaths["remote_python"]
    disk_path = rpaths["remote_data_disk"]
    models_spec = cfg_global.get("models", {})

    _require_local_cloudflared(ctx["profile"])
    title = "Google Colab SSH" if provider == "colab" else "AutoDL"
    print("=" * 65)
    print(f"  {title} 云端环境诊断 (pipeline.cloud doctor)")
    print("=" * 65)

    all_passed = True

    # 1. SSH 连通性
    ssh_ok = is_ssh_reachable(host)
    if ssh_ok:
        print(f"OK    [SSH] 远端连通 ({host})")
    else:
        print(f"FAIL  [SSH] 无法通过 SSH 连接远端实例 ({host})")
        if provider == "colab":
            print("      修法: 1. 直接运行 cloud up --provider colab 自动无头拉起（需先运行过一次 --browser-login）；")
            print("            2. 或手动在 Colab 运行 tools/ava_colab_bootstrap.ipynb 后 `cloud connect \"<隧道串>\" --provider colab`;")
            print("            3. 或切换无隧道队列: cloud use colab-drive。")
        else:
            print("      修法: 1. 确认控制台实例是否已开机 (https://www.autodl.com/console/instance/list);")
            print("            2. 换过实例的话，把控制台的 SSH 登录指令交给 cloud connect。")
        return 1

    # 2. NVIDIA GPU
    gpu_res = _ssh(GPU_QUERY_CMD, host=host, check=False)
    status_tag, desc, passed = evaluate_doctor_gpu(gpu_res.stdout, gpu_res.returncode, provider=provider)
    print(f"{status_tag:<5} [GPU] {desc}")
    if not passed:
        all_passed = False

    # 3. 远端代码仓库与内容核对
    repo_res = _ssh(f"test -d {remote_root}/.git && cd {remote_root} && git rev-parse --short HEAD", host=host, check=False)
    if repo_res.returncode == 0:
        print(f"OK    [REPO] 远端仓库就绪 ({remote_root}, commit: {repo_res.stdout.strip()})")
    else:
        all_passed = False
        print(f"FAIL  [REPO] 远端仓库不存在或未完成初始化: {remote_root}")
        print("      修法: cloud fix-env（弹卡；克隆仓库到远端，已在就跳过）")

    if repo_res.returncode == 0:
        code_ok, code_lines = code_drift_lines(_check_remote_drift(host, cfg_global, provider))
        print(f"{'OK' if code_ok else 'FAIL':<5} [CODE] {code_lines[0]}")
        for line in code_lines[1:]:
            print(f"      {line}")
        if not code_ok:
            all_passed = False

    # 4. PyTorch 与 CUDA 可用性
    torch_cmd = (
        f"{py_exe} -c "
        "'import torch; print(f\"{torch.__version__}___{torch.cuda.is_available()}\")'"
    )
    torch_res = _ssh(torch_cmd, host=host, check=False)
    if torch_res.returncode == 0 and "___" in torch_res.stdout:
        t_ver, c_avail = torch_res.stdout.strip().split("___")
        if c_avail == "True":
            print(f"OK    [TORCH] PyTorch {t_ver}, CUDA可用=True")
        elif provider == "colab":
            all_passed = False
            print(f"FAIL  [TORCH] Colab PyTorch {t_ver} CUDA 不可用")
        else:
            print(f"INFO  [TORCH] PyTorch {t_ver} (无卡模式，CUDA暂离线)")
    else:
        all_passed = False
        print(f"FAIL  [TORCH] PyTorch 未安装或环境损坏: {torch_res.stderr.strip()}")
        print(f"      修法: 在 {py_exe} 对应 venv 安装依赖")

    # 5. 数据盘 / Colab 运行盘存储
    disk_res = _ssh(f"df -BG {shlex.quote(disk_path)} | tail -1 | awk '{{print $4}}' | tr -d 'G'", host=host, check=False)
    d_tag, d_desc, d_passed = evaluate_doctor_disk(disk_res.stdout, disk_path=disk_path)
    print(f"{d_tag:<5} [DISK] {d_desc}")
    if not d_passed:
        all_passed = False

    # 5b. AutoDL 文件存储软链 / Colab Drive 缓存检查
    if provider == "autodl":
        fs_res = _ssh(FS_MOUNT_CHECK_CMD, host=host, check=False)
        if fs_res.stdout.strip() == "MOUNTED":
            print("OK    [FS] 文件存储 /root/autodl-fs 已挂载")
        else:
            all_passed = False
            print("FAIL  [FS] 文件存储 /root/autodl-fs 没挂载")
            print(f"      修法: 控制台「文件存储」页看 {cfg_global.get('region', '西北B区')} 是否已初始化；"
                  "已初始化仍没挂上就重启实例（文件存储只在开机时挂载）")
        fs = fs_models_dir(cfg_global)
        ln_res = _ssh(models_link_script(fs, models_root, create=False), host=host, check=False)
        l_tag, l_desc, l_ok = evaluate_models_link(ln_res.stdout, fs, models_root)
        print(f"{l_tag:<5} [LINK] {l_desc}")
        if not l_ok:
            all_passed = False
    else:
        drive_cache = str(ctx["settings"].get("drive_cache_dir") or "/content/drive/MyDrive/ava_cache")
        dc_res = _ssh(f"test -d {shlex.quote(drive_cache)} && echo MOUNTED || echo MISSING", host=host, check=False)
        if dc_res.stdout.strip() == "MOUNTED":
            print(f"OK    [DRIVE] Drive 依赖缓存已挂载: {drive_cache}")
        else:
            print(f"INFO  [DRIVE] 未挂载 Drive 缓存 {drive_cache}（冷启动可跑，但无法复用 venv 缓存）")

    # 6. 模型资产核查
    print("-" * 65)
    print(f"模型资产在位核查 ({models_root}):")
    for m_id, spec in models_spec.items():
        m_dir = spec.get("dir", m_id)
        min_gb = float(spec.get("min_gb", 1.0))
        desc = spec.get("description", "")
        chk_cmd = (
            f"if [ -d '{models_root}/{m_dir}' ]; then "
            f"  du -s -BG '{models_root}/{m_dir}' | awk '{{print $1}}' | tr -d 'G'; "
            f"else echo 'MISSING'; fi"
        )
        m_res = _ssh(chk_cmd, host=host, check=False)
        sz_str = m_res.stdout.strip()
        if sz_str and sz_str != "MISSING":
            try:
                sz_gb = float(sz_str)
                if sz_gb >= min_gb:
                    print(f"OK    [MODEL] {m_id:<30} ({sz_gb:.1f}GB ≥ {min_gb}GB) - {desc}")
                    st_res = _ssh(build_model_structure_probe_cmd(m_dir, models_root=models_root, python=py_exe),
                                  host=host, check=False, timeout=90)
                    st_tag, st_desc = parse_model_structure_result(st_res.stdout, st_res.returncode)
                    print(f"{st_tag:<5} [STRUCT] {m_id:<30} {st_desc}")
                    if st_tag == "FAIL":
                        all_passed = False
                        print("      修法: cloud fix-env（弹卡；重新下载结构校验不过的模型）")
                else:
                    all_passed = False
                    print(f"FAIL  [MODEL] {m_id:<30} 体积不足 ({sz_gb:.1f}GB < {min_gb}GB) - {desc}")
                    print("      修法: cloud fix-env（弹卡；补下体积不足的模型）")
            except ValueError:
                all_passed = False
                print(f"FAIL  [MODEL] {m_id:<30} 状态异常 - {desc}")
        else:
            if provider == "colab":
                print(f"INFO  [MODEL] {m_id:<30} 尚未预载到 {models_root}（Colab 支持运行时从 HF 直拉，或先 cloud fix-env 预载）")
            else:
                all_passed = False
                print(f"FAIL  [MODEL] {m_id:<30} 缺失 - {desc}")
                print("      修法: cloud fix-env（弹卡；下载缺失的模型）")

    print("=" * 65)
    if all_passed:
        print("[✓] 全部环境与模型指标通过，系统处于可直接出片状态。")
        return 0
    else:
        print("[✗] 存在未通过检查项，请依据上方命令修复后重新运行 doctor。")
        return 1


def _rate_label(unit: str) -> str:
    return "CU/小时" if unit == "cu" else "元/小时"


def _cost_prefix(unit: str) -> str:
    return "CU " if unit == "cu" else "¥"


def _session_unit(cfg_global: dict, sdata: dict | None, fallback_provider: str | None = None) -> tuple[str, str]:
    p = str((sdata or {}).get("provider") or fallback_provider or "autodl")
    u = str((sdata or {}).get("billing_unit") or billing_unit_of(cfg_global, p))
    return p, u


def cmd_up(args: argparse.Namespace) -> int:
    """开机/接入命令：检测 AutoDL 或 Colab 运行时状态、激活 watchdog 并开启计费会话。"""
    ctx = _cloud_context(args)
    cfg_global, cfg_local = ctx["cfg_global"], ctx["cfg_local"]
    provider, st, rpaths, host, unit = (
        ctx["provider"], ctx["settings"], ctx["rpaths"], ctx["host"], ctx["unit"],
    )
    declared = getattr(args, "mode", None)
    rate = check_rate(getattr(args, "rate", None), unit=unit)

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        cloud_drive.ensure_drive_layout(drive_root)
        print(f"[*] 检查 Colab Drive Worker 在线状态 (目录: {drive_root})...")
        hb = cloud_drive.parse_worker_heartbeat(drive_root)
        if not hb or not hb.get("online"):
            print()
            for line in rent_instructions(cfg_global, "colab-drive"):
                print(f"[!] {line}")
            print(f"[*] 轮询等待 Colab Worker 心跳上线（超时 {args.timeout} 秒）...")
            start_wait = time.time()
            while time.time() - start_wait < args.timeout:
                hb = cloud_drive.parse_worker_heartbeat(drive_root)
                if hb and hb.get("online"):
                    break
                time.sleep(5)
            if not hb or not hb.get("online"):
                raise SystemExit(f"FAIL 在 {args.timeout} 秒内未检测到在线 Colab Drive Worker 心跳。")
        gpu_name = hb.get("gpu")
        gpu_mem = hb.get("gpu_mem_mib")
        mode = "gpu" if gpu_name else "cardless"
        print(f"[OK] Colab Worker 在线：GPU={gpu_name or '未知'}" + (f"，显存 {float(gpu_mem) / 1024:.1f}GB" if gpu_mem else ""))
        sfile = get_active_session_file()
        now_epoch = time.time()
        target = f"drive:{drive_root}"
        boot_ep = hb.get("boot_epoch")
        try:
            prev = json.loads(sfile.read_text(encoding="utf-8")) if sfile.exists() else None
        except (OSError, ValueError):
            prev = None
        resumed = resumable_session(prev, target, float(boot_ep) if boot_ep is not None else None)
        if resumed and prev is not None:
            session_data = dict(prev)
            print(f"[OK] 沿用当前 Colab 会话 {prev.get('session')}（开始于 {prev.get('up')}）")
        else:
            session_data = {
                "session": datetime.now().strftime("%Y%m%d_%H%M%S"),
                "provider": "colab-drive",
                "billing_unit": "cu",
                "mode": mode,
                "up": datetime.now().strftime("%H:%M:%S"),
                "up_epoch": now_epoch,
                "episode": getattr(args, "episode", None),
                "tasks": [],
                "target": target,
            }
        if gpu_name:
            session_data["gpu"] = gpu_name
        if gpu_mem:
            session_data["gpu_mem_mib"] = gpu_mem
        if rate is not None:
            session_data["rate_cu"] = rate
            session_data["rate_source"] = "人报（up --rate）"
        sfile.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
        if not resumed:
            print(f"[OK] 会话已开启: {session_data['session']} (后端: colab-drive, 计费单位: CU)")
        return 0

    _require_local_cloudflared(ctx["profile"] or ({"proxy_type": "cloudflared"} if provider == "colab" else {}))
    print(f"[*] 检查云端实例连通性 (后端: {provider}, Host: {host}, 模式: {declared or '开机后探测'})...")
    if not is_ssh_reachable(host):
        if provider == "colab" and not getattr(args, "no_auto_boot", False):
            prof_dir = cloud_colab_boot.colab_browser_profile_dir()
            if cloud_colab_boot.has_saved_browser_profile(prof_dir):
                nb_url = cloud_colab_boot.resolve_colab_notebook_url(
                    cfg_global, ctx["cfg_local"], getattr(args, "notebook_url", None)
                )
                print(f"[*] Colab 尚未连通，尝试通过隔离浏览器自动无头拉起：{nb_url}")
                try:
                    tunnel_target = cloud_colab_boot.auto_boot_colab_ssh(
                        nb_url,
                        profile_dir=prof_dir,
                        timeout_s=int(args.timeout),
                    )
                except RuntimeError as exc:
                    raise SystemExit(f"FAIL Colab 自动拉起失败: {exc}") from exc
                conn = parse_colab_connect_target(tunnel_target)
                conn["notebook_url"] = nb_url
                paths.require_data()
                merged, host, endpoint = _apply_ssh_profile_connection(
                    cfg_global,
                    ctx["cfg_local"],
                    paths.CONFIG / "cloud.local.json",
                    "colab",
                    conn,
                    alias=host,
                )
                ctx["cfg_local"] = merged
                ctx["profile"] = provider_profile(merged, "colab")
                print(f"[OK] Colab 自动拉起成功，隧道已刷新：{endpoint}（别名 {host}）")
            else:
                print("[*] 尚未检测到 Colab 隔离浏览器登录态；如需无人值守自动开机，可先执行一次：")
                print("    python -m pipeline.cloud connect --provider colab --browser-login")

        if not is_ssh_reachable(host):
            print()
            for line in rent_instructions(cfg_global, provider):
                print(f"[!] {line}")
            print(f"[*] 轮询等待 SSH 可达中（超时 {args.timeout} 秒；期间 connect 换了连接也会接上）...")

            start_wait = time.time()
            connected = False
            while time.time() - start_wait < args.timeout:
                if is_ssh_reachable(host):
                    connected = True
                    break
                time.sleep(5)

            if not connected:
                raise SystemExit(f"FAIL 实例在 {args.timeout} 秒内未能成功连接，请核查实例是否已开机。")

    print("[OK] SSH 连通就绪。")

    actual = detect_runtime_mode_from_gpu_probe(
        _ssh("nvidia-smi -L", host=host, check=False).returncode)
    if declared and actual != declared:
        print(f"WARN --mode {declared} 与实测不符：这台实例当前是【{actual}】。")
        if actual == "cardless":
            print("     → 无 GPU：带卡任务（VLM 打标/重推理）会在加载权重时被 OOM 杀掉，"
                  "而且报错只有一行 Killed。\n"
                  "     → 账簿已按实测的 cardless 记；"
                  "要跑 GPU 任务请更换带卡运行时。")
    mode = actual

    gpu_name, gpu_mem = (None, None)
    if mode == "gpu":
        gpu_name, gpu_mem = parse_gpu_query(_ssh(GPU_QUERY_CMD, host=host, check=False).stdout)
        print(f"[OK] GPU：{gpu_name or '型号未读出'}" + (f"，显存 {gpu_mem / 1024:.1f}GB" if gpu_mem else ""))
    if provider == "autodl" and not ensure_models_link(host, cfg_global):
        print("WARN 模型目录没接上文件存储：带模型的任务会找不到权重，先看 cloud doctor", file=sys.stderr)

    # 部署并激活远端 watchdog（AutoDL 写 /usr/bin/shutdown，Colab 写 .ava_unassign 触发 runtime.unassign）
    idle_sec = int(st.get("idle_shutdown_seconds") or DEFAULT_IDLE_SHUTDOWN_SECONDS)
    wd_log = "/content/watchdog.log" if provider == "colab" else "/root/watchdog.log"
    watchdog_content = generate_watchdog_script(
        idle_sec,
        heartbeat_path=rpaths["heartbeat_path"],
        hold_path=rpaths["hold_path"],
        unassign_path=rpaths["unassign_path"] or None,
        log_path=wd_log,
    )
    deploy_cmd = build_watchdog_deploy_command(
        watchdog_content,
        heartbeat_path=rpaths["heartbeat_path"],
        script_path=rpaths["watchdog_path"],
        log_path=wd_log,
    )
    res = _ssh(deploy_cmd, host=host, check=False)
    wd_pid = res.stdout.strip()
    if res.returncode != 0 or not wd_pid:
        raise SystemExit(
            f"FAIL Watchdog 部署后未能确认进程存活。\n"
            f"     ssh returncode={res.returncode} (255 = 连接层断开，通常是 pkill 误伤自身会话)\n"
            f"     stdout={res.stdout.strip()!r}\n"
            f"     stderr={res.stderr.strip()!r}\n"
            f"     不会假装成功：不验证就宣布成功会重演 2026-09-11 空转事故"
        )
    wd_action = "写入释放信号 (unassign)" if provider == "colab" else "自动关机"
    print(f"[OK] 远端守卫 Watchdog 已激活 (PID {wd_pid}，无活跃任务且空闲 {idle_sec}s {wd_action})。")

    sfile = get_active_session_file()
    now_epoch = time.time()
    target = connection_target(cfg_local, provider=provider)
    try:
        prev = json.loads(sfile.read_text(encoding="utf-8")) if sfile.exists() else None
    except (OSError, ValueError):
        prev = None
    resumed = resumable_session(prev, target, remote_boot_epoch(host, now_epoch))
    if resumed and prev is not None:
        session_data = dict(prev)
        session_data["mode"] = mode
        print(f"[OK] 沿用本次开机的活跃会话 {prev.get('session')}（开始于 {prev.get('up')}），开机时刻与已报单价不变")
    else:
        if prev is not None:
            print(f"WARN 上一个会话 {prev.get('session')}（开始于 {prev.get('up')}）没有 down 结算就被替换："
                  "实例已重启或换了一台，那段费用不进账本", file=sys.stderr)
        session_data = {
            "session": f"{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "provider": provider,
            "billing_unit": unit,
            "mode": mode,
            "up": datetime.now().strftime("%H:%M:%S"),
            "up_epoch": now_epoch,
            "episode": getattr(args, "episode", None),
            "tasks": [],
        }
    if target:
        session_data["target"] = target
    if gpu_name:
        session_data["gpu"] = gpu_name
    if gpu_mem:
        session_data["gpu_mem_mib"] = gpu_mem
    rate_key = "rate_cu" if unit == "cu" else "rate_cny"
    if rate is not None and mode == "gpu" and declared in (None, "gpu"):
        session_data[rate_key] = rate
        session_data["rate_source"] = "人报（up --rate）"
    elif rate is not None and mode == "gpu":
        print(f"WARN 报的单价 {rate:g} {_rate_label(unit)} 是按 --mode {declared} 报的，实测是 {mode}，"
              "这个价不记；结算时用 down --rate 补实际单价", file=sys.stderr)
    elif rate is not None:
        print(f"WARN 实测是无卡模式，按平台固定价记，报的单价 {rate:g} {_rate_label(unit)} 不记", file=sys.stderr)
    elif mode == "gpu" and rate_key not in session_data:
        print(f"WARN 没报单价：账本会按参考价估算（{_rate_label(unit)}）。"
              "结算时可用 cloud down --rate <单价> 补上", file=sys.stderr)
    if declared and mode != declared:
        session_data["mode_corrected"] = f"up 声明 --mode {declared}，实测为 {mode}，按实测记账"
    sfile.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not resumed:
        print(f"[OK] 会话已开启: {session_data['session']} (后端: {provider}, 模式: {mode})")
    return 0


def connection_target(cfg_local: dict, *, provider: str | None = None) -> str | None:
    """`cloud connect` 记下的目标字符串（纯函数）；没 connect 过为 None。"""
    p = provider or "autodl"
    prof = provider_profile(cfg_local, p)
    if p == "colab-drive":
        return f"drive:{prof['drive_root']}" if prof.get("drive_root") else None
    if not prof.get("server"):
        return None
    if prof.get("proxy_type") == "cloudflared":
        return f"{prof.get('user', 'root')}@{prof['server']}"
    if not prof.get("port"):
        return None
    return f"{prof.get('user', 'root')}@{prof['server']}:{prof['port']}"


def remote_boot_epoch(host: str, now_epoch: float) -> float | None:
    """远端本次开机的本地时刻：now − /proc/uptime。读不出为 None（调用方按「不能沿用」处理）。"""
    res = _ssh("cat /proc/uptime", host=host, check=False)
    try:
        return now_epoch - float(res.stdout.split()[0])
    except (ValueError, IndexError):
        return None


def resumable_session(prev: dict | None, target: str | None, boot_epoch: float | None,
                      slack: float = 30.0) -> bool:
    """上一个活跃会话能否沿用（纯函数）：同一个连接目标，且远端开机早于会话开始（没重启过）。

    slack 只吸收 ssh 往返的几秒；重启过的实例开机时刻必然晚于会话开始，差距至少是关机到重开的间隔。
    """
    if not prev or boot_epoch is None:
        return False
    up = prev.get("up_epoch")
    if not isinstance(up, (int, float)) or prev.get("target") != target:
        return False
    return boot_epoch <= up + slack


def detect_runtime_mode_from_gpu_probe(returncode: int) -> str:
    """由 nvidia-smi 探针返回码判定当前运行模式（纯函数）。

    为什么要探测而不是猜：无卡 ¥0.10/h 与带卡 ¥2.40/h 相差 24 倍。
    旧代码在缺少会话文件（用户手动开机后直接跑 down）时一律默认 gpu，
    若实例实际是无卡就会把账目虚高 24 倍。探测比猜测便宜得多。
    """
    return "gpu" if returncode == 0 else "cardless"


def session_rate(
    cfg_global: dict,
    mode: str | None,
    gpu_name: str | None,
    reported: float | None = None,
    *,
    provider: str = "autodl",
) -> tuple[float, str | None]:
    """会话单价（纯函数）→ (元/时 或 CU/时, 估算说明)。说明为 None = 单价可信。

    AutoDL 按 `gpu_rates_cny` / `hourly_rate_cny`（元/h）；Colab 按 `providers.colab.cu_rates` / `cu_rate_fallback`（CU/h）。
    """
    if reported is not None:
        return float(reported), None
    if provider in ("colab", "colab-drive"):
        st = provider_settings(cfg_global, provider)
        fallback = float(st.get("cu_rate_fallback", 1.5))
        cu_rates = st.get("cu_rates", {}) or {}
        if mode != "gpu":
            return fallback, f"单价估算：Colab 按参考 fallback {fallback} CU/h 记"
        rate, missed = lookup_gpu_rate(gpu_name, cu_rates, fallback)
        if missed:
            return rate, f"单价估算：cu_rates 里查不到 {gpu_name or '本次 GPU 型号'}，按 {fallback} CU/h 记"
        return rate, f"单价估算：按 cu_rates 里 {gpu_name} 参考耗电率（{rate} CU/h）记"

    if mode == "cardless":
        return float(cfg_global.get("cardless_rate_cny", 0.10)), None
    fallback = float(cfg_global.get("hourly_rate_cny", 2.40))
    if mode != "gpu":
        return fallback, "单价估算：模式不可知，按 hourly_rate_cny 记"
    rate, missed = lookup_gpu_rate(gpu_name, cfg_global.get("gpu_rates_cny", {}) or {}, fallback)
    if missed:
        return rate, f"单价估算：没有人报单价，gpu_rates_cny 里也查不到 {gpu_name or '本次 GPU 型号'}，按 hourly_rate_cny 记"
    return rate, f"单价估算：没有人报单价（up / down --rate），按 gpu_rates_cny 里 {gpu_name} 的参考价记"


def check_rate(value: float | None, *, unit: str = "cny") -> float | None:
    """人报单价的合理性（纯函数）：必须是正数；>50 多半是误把总价/月费当成了时价。"""
    if value is None:
        return None
    if not (0 < value <= 50):
        u_label = "CU/小时" if unit == "cu" else "元/小时"
        raise SystemExit(f"FAIL --rate {value:g} 不像按量时价（应为 0–50 {u_label}）")
    return float(value)


def rerate_last_entry(lines: list[str], rate: float, today: str) -> tuple[list[str], dict]:
    """D131：把账簿最后一条结算的估算单价换成人事后补报的单价（纯函数）→ (新的各行, 改后的条目)。"""
    idx = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if idx is None:
        raise SystemExit("FAIL 账簿是空的，没有可以补单价的结算")
    try:
        entry = json.loads(lines[idx])
    except json.JSONDecodeError as e:
        raise SystemExit(f"FAIL 账簿最后一行读不出来，不改：{e}") from None
    note = entry.get("note") or ""
    clauses = [c for c in note.split("；") if c]
    estimated = [c for c in clauses if c.startswith("单价估算")]
    is_cu = entry.get("unit") == "cu"
    rate_key = "rate_cu" if is_cu else "rate_cny"
    cost_key = "cu" if is_cu else "cny"
    u_label = "CU/h" if is_cu else "元/h"
    cost_prefix = "CU " if is_cu else "¥"
    if not estimated:
        raise SystemExit(
            f"FAIL 没有活跃会话，账簿最后一条（会话 {entry.get('session')}）的单价 "
            f"{entry.get(rate_key)} {u_label} 不是估算值，不改。要改人报过的单价请人工改账簿")
    old_rate, old_cost = entry.get(rate_key), entry.get(cost_key)
    entry[rate_key] = float(rate)
    entry[cost_key] = calculate_session_cost(entry.get("gpu_seconds", 0), float(rate))
    clauses = [c for c in clauses if not c.startswith("单价估算")]
    clauses.append(f"单价 {rate:g} {u_label} 为人事后补报（{today}；原按 {old_rate} {u_label} 估算 {cost_prefix}{old_cost}）")
    entry["note"] = "；".join(clauses)
    out = list(lines)
    out[idx] = json.dumps(entry, ensure_ascii=False)
    return out, entry


def cmd_down(args: argparse.Namespace) -> int:
    """关机/释放结算命令：按后端双单位精算费用，落盘账簿，下发关机或 Colab 运行时释放。"""
    ctx = _cloud_context(args)
    cfg_global = ctx["cfg_global"]
    provider = ctx["provider"]
    host = ctx["host"]
    rpaths = ctx["rpaths"]
    st = ctx["settings"]
    drive_root = ctx.get("drive_root")

    sfile = get_active_session_file()
    session_info: dict = {}
    session_corrupt = False
    if sfile.exists():
        try:
            session_info = json.loads(sfile.read_text(encoding="utf-8"))
        except Exception as e:
            session_corrupt = True
            print(f"WARN 会话文件损坏，按未知会话结算（时长/费用不可靠）: {e}",
                  file=sys.stderr)

    # 若未显式传 --provider，优先继承活跃会话自己的 provider/unit，防止切默认后端后算错账
    if getattr(args, "provider", None) is None and session_info.get("provider") in VALID_PROVIDERS:
        provider = session_info["provider"]
        st = provider_settings(cfg_global, provider)
        rpaths = provider_remote_paths(cfg_global, provider)
        prof = provider_profile(ctx["cfg_local"], provider)
        host = provider_ssh_host(cfg_global, ctx["cfg_local"], provider)
        drive_root = _resolve_drive_root(st, prof) if provider == "colab-drive" else None

    unit = billing_unit_of(cfg_global, provider)
    if provider == "colab-drive":
        reachable = bool(drive_root and cloud_drive.is_drive_reachable(drive_root)
                         and (hb := cloud_drive.parse_worker_heartbeat(drive_root)) and hb.get("online"))
    else:
        _require_local_cloudflared(provider_profile(ctx["cfg_local"], provider))
        reachable = is_ssh_reachable(host)

    rate_arg = check_rate(getattr(args, "rate", None), unit=unit)
    if not sfile.exists() and not reachable and rate_arg is not None:
        lfile = get_ledger_file()
        lines = lfile.read_text(encoding="utf-8").splitlines() if lfile.exists() else []
        new_lines, entry = rerate_last_entry(lines, rate_arg, datetime.now().strftime("%Y-%m-%d"))
        paths.atomic_write(lfile, "\n".join(new_lines) + "\n")
        cost_val = entry.get("cu") if entry.get("unit") == "cu" else entry.get("cny", 0.0)
        print(f"[*] 没有活跃会话、实例已关：把最近一条结算（会话 {entry['session']}）的估算单价改为 "
              f"{rate_arg:g} {_rate_label(entry.get('unit', 'cny'))}，重算为 {_cost_prefix(entry.get('unit', 'cny'))}{cost_val:.4f}"
              f"（时长 {entry['gpu_seconds']}s 不变）")
        return 0

    mode = session_info.get("mode")
    if not mode:
        if provider == "colab-drive":
            mode = "gpu" if reachable else "unknown"
        elif reachable:
            mode = detect_runtime_mode_from_gpu_probe(_ssh("nvidia-smi -L", host=host, check=False).returncode)
        else:
            mode = "unknown"

    gpu_name = session_info.get("gpu")
    if mode == "gpu" and not gpu_name and reachable:
        if provider == "colab-drive" and drive_root is not None:
            hb_gpu = cloud_drive.parse_worker_heartbeat(drive_root) or {}
            gpu_name = hb_gpu.get("gpu")
        else:
            gpu_name = parse_gpu_query(_ssh(GPU_QUERY_CMD, host=host, check=False).stdout)[0]

    reported = rate_arg
    if reported is None:
        r_key = "rate_cu" if unit == "cu" else "rate_cny"
        if isinstance(session_info.get(r_key), (int, float)):
            reported = float(session_info[r_key])
    rate, rate_estimate_note = session_rate(cfg_global, mode, gpu_name, reported, provider=provider)

    budget = float(st.get("budget_per_episode_cu", 1.5) if unit == "cu" else st.get("budget_per_episode_cny", 0.35))
    up_epoch = session_info.get("up_epoch", time.time())
    duration_sec = int(max(time.time() - up_epoch, 0))
    cost_val = calculate_session_cost(duration_sec, rate)
    down_time = datetime.now().strftime("%H:%M:%S")

    note = None
    if not reachable:
        note = (
            "实例在结算前已停止响应（多为 watchdog 自动释放/关机）；"
            "gpu_seconds 为上界估计，含关机到本地发现之间的延迟"
        )
    if session_corrupt:
        note = (note + "；" if note else "") + "会话文件损坏，时长从 0 起算，费用严重低估"
    if rate_estimate_note:
        note = (note + "；" if note else "") + rate_estimate_note

    ledger_entry = format_ledger_entry(
        session_id=session_info.get("session", f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        up_time=session_info.get("up", down_time),
        down_time=down_time,
        gpu_seconds=duration_sec,
        cost_cny=cost_val,
        episode=session_info.get("episode"),
        tasks=session_info.get("tasks", []),
        budget_cny=budget,
        mode=mode,
        note=note,
        gpu=gpu_name,
        rate_cny=rate,
        provider=provider,
        billing_unit=unit,
    )
    lfile = get_ledger_file()
    with lfile.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

    if sfile.exists() and not session_corrupt:
        sfile.unlink()

    c_pref = _cost_prefix(unit)
    u_name = "CU" if unit == "cu" else "元"
    print("=" * 60)
    print(f"会话结算完成（后端: {provider}）：")
    print(f"  会话模式: {mode} (计费单价: {c_pref}{rate}/{_rate_label(unit).split('/')[1]})")
    print(f"  运行时长: {duration_sec // 60} 分 {duration_sec % 60} 秒 ({duration_sec}s)")
    print(f"  本次计费: {c_pref}{cost_val:.4f} {u_name}")
    if ledger_entry["over_budget"]:
        print(f"  \033[91m⚠️  警告: 本次花费超出单期预算 {c_pref}{budget} {u_name}！\033[0m")
    elif ledger_entry["budget_scoped"]:
        print(f"  预算状态: 达标 (≤ {c_pref}{budget} {u_name})")
    else:
        print(f"  预算状态: 本会话未绑定某一期（{ledger_entry['episode']}），单期预算不适用")
    print(f"  账簿记录: → {lfile}")
    print("=" * 60)

    if provider == "colab-drive" and drive_root is not None:
        cloud_drive.clear_drive_hold(drive_root)
        active_jobs = cloud_drive.active_drive_jobs(drive_root)
        if active_jobs and not getattr(args, "force", False):
            print(f"[*] 提示: Drive 队列仍有未完成任务 ({active_jobs})，暂不发送释放信号。")
            print("    如需强制释放请加 --force: python -m pipeline.cloud down --force --provider colab-drive")
            return 0
        flag = cloud_drive.request_drive_shutdown(drive_root)
        print(f"[*] 已写入 Colab 运行时释放信号: {flag}")
        if not reachable:
            print("[提示] Worker 当前已离线；请在 Colab 页面确认运行时已断开以停止 CU 消耗。")
            return 0
        print("[*] 等待 Colab Worker 响应并断开运行时（最多 60 秒）...")
        for _ in range(12):
            time.sleep(5)
            hb_now = cloud_drive.parse_worker_heartbeat(drive_root)
            if not hb_now or not hb_now.get("online"):
                print("[OK] Worker 已停止心跳，Colab 运行时已释放。")
                return 0
        print("[!] 60 秒内 Worker 仍在心跳（Drive 同步可能有延迟），请到 Colab 界面确认断开连接。")
        return 0

    if reachable:
        _ssh(f"rm -f {rpaths['hold_path']}", host=host, check=False)
        active_tasks = remote_active_tasks(host=host)
        if active_tasks and not getattr(args, "force", False):
            print(f"[*] 提示: 远端仍有活跃的后台任务正在运行 ({active_tasks})。")
            print(f"    账簿已结算，保留远端实例运行以等待任务完成。")
            print(f"    如需强制停机请加 --force: python -m pipeline.cloud down --force")
            return 0

        if provider == "colab":
            print("[*] 正在通知 Colab 运行时释放（runtime.unassign）...")
            unassign = rpaths["unassign_path"]
            _ssh(
                f"touch {shlex.quote(unassign)} && "
                f"(nohup {shlex.quote(rpaths['remote_python'])} -c 'from google.colab import runtime; runtime.unassign()' >/dev/null 2>&1 &); sleep 1",
                host=host,
                check=False,
                timeout=15,
            )
            print("[*] 正在验证 Colab 隧道是否已断开（最多等 60 秒）...")
            for _ in range(12):
                time.sleep(5)
                if not is_ssh_reachable(host):
                    print("[OK] 已验证：Colab SSH 隧道已断开，运行时释放信号已生效。")
                    return 0
            print("[WARN] 60 秒内 SSH 仍可达（若通过终端直跑未走 Notebook 内核，runtime.unassign 需由 Bootstrap sentinel 执行）；请在 Colab 页面点「断开连接并删除运行时」。", file=sys.stderr)
            return 1

        print("[*] 正在发送远端停机指令...")
        _ssh("nohup /usr/bin/shutdown -h now >/dev/null 2>&1 & sleep 1", host=host, check=False, timeout=15)

        print("[*] 正在验证实例是否真的停止响应（最多等 60 秒）...")
        for _ in range(12):
            time.sleep(5)
            if not is_ssh_reachable(host):
                print("[OK] 已验证：实例已停止响应，关机生效。")
                return 0
        print("[FAIL] 60 秒内实例仍可连接，关机未生效 —— 不会假装成功。", file=sys.stderr)
        print(f"       请到控制台手动关机：https://www.autodl.com/console/instance/list", file=sys.stderr)
        return 1

    if provider == "colab":
        print("[提示] Colab 运行时当前不可达（可能已释放）。请在 Colab 页面确认 CU 计费已停止。")
    else:
        print(f"[提示] 实例当前不可达（可能已关机）。请在控制台确认计费已停止：")
        print(f"       https://www.autodl.com/console/instance/list")
    return 0


def hold_write_command(seconds: int, hold_path: str = REMOTE_HOLD_PATH) -> str:
    """远端写保持标记的命令（纯函数，D130）：到期 = 远端此刻 + seconds；还没到期就续期、保留最初的开始时间。

    用远端时钟算，`status` 也在远端取「此刻」，两边钟差不影响剩余时长。命令最后打印标记与远端此刻供核对。
    """
    return (
        f"now=$(date +%s); start=$now; "
        f"if [ -f {hold_path} ] && read -r s u < {hold_path}; then "
        f"case \"$s\" in ''|*[!0-9]*) ;; *) case \"$u\" in ''|*[!0-9]*) ;; *) "
        f"[ \"$now\" -lt \"$u\" ] && start=$s ;; esac ;; esac; fi; "
        f"echo \"$start $((now + {int(seconds)}))\" > {hold_path} && cat {hold_path} && date +%s"
    )


def parse_hold(text: str) -> tuple[int, int, int] | None:
    """`cat 标记; date +%s` 的输出 → (开始, 到期, 远端此刻)；没有标记或读不懂返回 None（纯函数）。"""
    nums = re.findall(r"\d+", text or "")
    if len(nums) < 3:
        return None
    return int(nums[0]), int(nums[1]), int(nums[-1])


def hold_status_line(hold: tuple[int, int, int] | None, rate: float, *, unit: str = "cny") -> str:
    """status 的「保持开机」一行（纯函数）：剩余多久、已保持多久、保持期间按单价的估算花费。"""
    if hold is None:
        return "未保持（配完照常空闲关机/释放）"
    start, until, now = hold
    held = max(now - start, 0)
    cost = calculate_session_cost(min(held, max(until - start, 0)), rate)
    pref = _cost_prefix(unit)
    u_lbl = _rate_label(unit)
    if now >= until:
        return (f"已过期 {(now - until) // 60} 分钟（共保持 {(until - start) // 60} 分钟，约 {pref}{cost:.2f}），"
                "看门狗已恢复空闲关机/释放")
    return (f"保持中，剩余 {(until - now) // 60} 分钟；已保持 {held // 60} 分钟，约 {pref}{cost:.2f}"
            f"（{pref}{rate}/{u_lbl.split('/')[1]}）。用完 cloud release，或直接 cloud down")


def _watchdog_supports_hold(host: str, watchdog_path: str = REMOTE_WATCHDOG_PATH) -> bool:
    return _ssh(f"grep -q hold_active {watchdog_path}", host=host, check=False).returncode == 0


def cmd_hold(args: argparse.Namespace) -> int:
    """D130：人要求配完不关机。写保持标记（再次 hold 即续期），看门狗到期前不做空闲关机/释放。"""
    ctx = _cloud_context(args)
    cfg_global, provider, host, rpaths = ctx["cfg_global"], ctx["provider"], ctx["host"], ctx["rpaths"]
    seconds = int(ctx["settings"].get("hold_seconds", DEFAULT_HOLD_SECONDS))

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        now = int(time.time())
        start, until = cloud_drive.write_drive_hold(drive_root, seconds, now_epoch=now)
        renewed = start < now
        print(f"[OK] Drive 保持标记已写入，有效至 {datetime.fromtimestamp(until).strftime('%H:%M')}（本机时间）"
              f"（{(until - now) // 60} 分钟后自动释放{'；已续期，已保持 ' + str((now - start) // 60) + ' 分钟' if renewed else ''}）。"
              "用完 cloud release，或直接 cloud down")
        return 0

    _require_local_cloudflared(ctx["profile"])
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例联系不上：没开机，或已经被看门狗关掉了，hold 无从谈起")
    wd_path = rpaths["watchdog_path"]
    hold_path = rpaths["hold_path"]
    if not _watchdog_supports_hold(host, wd_path):
        idle_sec = int(ctx["settings"].get("idle_shutdown_seconds", DEFAULT_IDLE_SHUTDOWN_SECONDS))
        wd_log = "/content/watchdog.log" if provider == "colab" else "/root/watchdog.log"
        wd_script = generate_watchdog_script(
            idle_sec,
            heartbeat_path=rpaths["heartbeat_path"],
            hold_path=hold_path,
            unassign_path=rpaths["unassign_path"] or None,
            log_path=wd_log,
        )
        res = _ssh(build_watchdog_deploy_command(
            wd_script,
            heartbeat_path=rpaths["heartbeat_path"],
            script_path=wd_path,
            log_path=wd_log,
        ), host=host, check=False)
        if res.returncode != 0 or not res.stdout.strip():
            raise SystemExit(f"FAIL 远端看门狗是旧版、不认保持标记，换新版没成功（returncode={res.returncode}，"
                             f"stderr={res.stderr.strip()!r}）；不写标记，免得以为保住了")
        print(f"[*] 远端看门狗是旧版，已换成认保持标记的新版（PID {res.stdout.strip()}）")
    res = _ssh(hold_write_command(seconds, hold_path=hold_path), host=host, check=False)
    hold = parse_hold(res.stdout) if res.returncode == 0 else None
    if hold is None:
        raise SystemExit(f"FAIL 保持标记没写上（returncode={res.returncode}，stderr={res.stderr.strip()!r}）")
    start, until, now = hold
    renewed = start < now
    print(f"[OK] 保持开机到 {datetime.fromtimestamp(until).strftime('%H:%M')}（本机时间）"
          f"（{(until - now) // 60} 分钟后自动释放{'；已续期，从第一次 hold 起算已保持 ' + str((now - start) // 60) + ' 分钟' if renewed else ''}）。"
          "实例这段时间一直计费；用完 cloud release，或直接 cloud down")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """D130：撤掉保持标记，看门狗恢复空闲关机/释放。"""
    ctx = _cloud_context(args)
    provider, host, rpaths, st = ctx["provider"], ctx["host"], ctx["rpaths"], ctx["settings"]
    idle_sec = int(st.get("idle_shutdown_seconds", DEFAULT_IDLE_SHUTDOWN_SECONDS))
    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        cloud_drive.clear_drive_hold(drive_root)
        print(f"[OK] 已撤 Drive 保持标记：没有活跃任务时，Worker 约 {idle_sec} 秒后自动释放（要马上释放用 cloud down）")
        return 0
    _require_local_cloudflared(ctx["profile"])
    if not is_ssh_reachable(host):
        print("[*] 实例联系不上，已经关了，没有要撤的保持")
        return 0
    res = _ssh(f"rm -f {rpaths['hold_path']}", host=host, check=False)
    if res.returncode != 0:
        raise SystemExit(f"FAIL 保持标记没删掉（returncode={res.returncode}，stderr={res.stderr.strip()!r}）")
    print(f"[OK] 已撤保持：没有 ava- 任务时，看门狗约 {idle_sec} 秒后关机/释放（要马上结算用 cloud down）")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """工况与账簿查询命令（支持 AutoDL / Colab SSH / Colab Drive 及双单位账簿汇总）。"""
    ctx = _cloud_context(args)
    cfg_global, provider, host, rpaths, st, unit = (
        ctx["cfg_global"], ctx["provider"], ctx["host"], ctx["rpaths"], ctx["settings"], ctx["unit"],
    )
    budget = float(st.get("budget_per_episode_cu", 1.5) if unit == "cu" else st.get("budget_per_episode_cny", 0.35))

    print("=" * 65)
    print(f"  端云工况状态 (当前后端: {provider} | 计费单位: {unit.upper()})")
    print("=" * 65)

    if provider == "colab-drive":
        drive_root = ctx.get("drive_root")
        reachable_fs = cloud_drive.is_drive_reachable(drive_root)
        hb = cloud_drive.parse_worker_heartbeat(drive_root) if reachable_fs and drive_root else None
        online = bool(hb and hb.get("online"))
        print(f"Drive 队列目录: {drive_root or '未绑定'}")
        print(f"Worker 在线状态: {'\033[92m● 在线 (心跳延迟 ' + str(hb.get('stale_s')) + 's)\033[0m' if online else '\033[90m○ 离线 / 未启动\033[0m'}")
        if hb:
            print(f"显卡与显存   : {hb.get('gpu') or '未知'}" + (f", {hb.get('gpu_mem_mib')} MiB" if hb.get("gpu_mem_mib") else ""))
            active_jobs = cloud_drive.active_drive_jobs(drive_root) if drive_root else []
            print(f"活跃队列任务 : {active_jobs or '无'}")
            hold = cloud_drive.read_drive_hold(drive_root) if drive_root else None
            rate_now, _ = session_rate(cfg_global, "gpu", hb.get("gpu"), provider="colab-drive")
            print(f"保持开机     : {hold_status_line(hold, rate_now, unit='cu')}")
    else:
        _require_local_cloudflared(ctx["profile"])
        reachable = is_ssh_reachable(host)
        print(f"实例连通状态 : {'\033[92m● 运行中 (SSH在线)\033[0m' if reachable else '\033[90m○ 未开机 / 离线\033[0m'}")

        if reachable:
            gpu_res = _ssh(
                "nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader",
                host=host,
                check=False,
            )
            if gpu_res.returncode == 0 and gpu_res.stdout.strip():
                print(f"显卡与显存   : {gpu_res.stdout.strip()}")
            else:
                print("显卡与显存   : 无卡模式，未挂载独立 GPU")

            disk_res = _ssh(f"df -h {shlex.quote(rpaths['remote_data_disk'])} | tail -1 | awk '{{print $4\" 可用 / \"$2\" 总量 (\"$5\" 已用)\"}}'", host=host, check=False)
            if disk_res.returncode == 0 and disk_res.stdout.strip():
                print(f"数据盘存储   : {disk_res.stdout.strip()}")

            watch_res = _ssh(f"pgrep -f '{_watchdog_proc_pattern(rpaths['watchdog_path'])}' >/dev/null && echo '运行中' || echo '未运行'", host=host, check=False)
            print(f"自动关机守卫 : {watch_res.stdout.strip()}")

            active = remote_active_tasks(host=host)
            active_str = f"[{', '.join(active)}]" if active else "无"
            print(f"活跃后台任务 : {active_str}")

            hold_res = _ssh(f"cat {rpaths['hold_path']} 2>/dev/null; date +%s", host=host, check=False)
            hold = parse_hold(hold_res.stdout) if hold_res.returncode == 0 else None
            sdata_rate: dict = {}
            try:
                sdata_rate = json.loads(get_active_session_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            sp, su = _session_unit(cfg_global, sdata_rate, provider)
            r_key = "rate_cu" if su == "cu" else "rate_cny"
            reported = sdata_rate.get(r_key) if isinstance(sdata_rate.get(r_key), (int, float)) else None
            rate_now, _ = session_rate(cfg_global, sdata_rate.get("mode", "gpu"), sdata_rate.get("gpu"), reported, provider=sp)
            print(f"保持开机     : {hold_status_line(hold, rate_now, unit=su)}")

    sfile = get_active_session_file()
    if sfile.exists():
        try:
            sdata = json.loads(sfile.read_text(encoding="utf-8"))
            sp, su = _session_unit(cfg_global, sdata, provider)
            s_st = provider_settings(cfg_global, sp)
            s_budget = float(s_st.get("budget_per_episode_cu", 1.5) if su == "cu" else s_st.get("budget_per_episode_cny", 0.35))
            mode = sdata.get("mode", "gpu")
            r_key = "rate_cu" if su == "cu" else "rate_cny"
            reported = sdata.get(r_key) if isinstance(sdata.get(r_key), (int, float)) else None
            rate, estimated = session_rate(cfg_global, mode, sdata.get("gpu"), reported, provider=sp)
            elapsed = int(time.time() - sdata.get("up_epoch", time.time()))
            cur_cost = calculate_session_cost(elapsed, rate)
            pref = _cost_prefix(su)
            u_word = "CU" if su == "cu" else "元"
            print("-" * 65)
            print(f"当前活跃会话 : {sdata.get('session')} (后端: {sp}, 开始于 {sdata.get('up')}, 模式: {mode})")
            print(f"已运行时间   : {elapsed // 60} 分 {elapsed % 60} 秒")
            cost_color = "\033[91m" if cur_cost > s_budget else "\033[92m"
            print(f"已产生费用   : {cost_color}{pref}{cur_cost:.4f}\033[0m {u_word} (单价: {pref}{rate}/小时{'，估算' if estimated else ''}, 限额: {pref}{s_budget})")
        except Exception as e:
            print(f"WARN 会话文件损坏，无法展示当前会话: {e}", file=sys.stderr)

    lfile = get_ledger_file()
    if lfile.exists():
        summary = parse_ledger_summary(lfile.read_text(encoding="utf-8"))
        records = summary["records"]
        tot_sec = summary["total_sec"]
        print("-" * 65)
        print(f"历史账簿统计 : 共 {len(records)} 次会话，累计计费 {tot_sec // 3600}小时{(tot_sec % 3600) // 60}分")
        print(f"累计消费统计 : AutoDL ¥{summary['total_cny']:.2f} 元 | Colab {summary['total_cu']:.4f} CU")
        over_budget_count = sum(1 for r in records if r.get("over_budget"))
        if over_budget_count > 0:
            print(f"\033[91m⚠️  历史超预算会话: {over_budget_count} 次！\033[0m")
        else:
            print(f"预算合规记录 : 全部会话均在预算控制范围内。")
    print("=" * 65)
    return 0


def _preflight_remote_capacity(host: str, remote_root: str, remote_ep_dir: str,
                              sync_pairs) -> None:
    """上行前的远端余量 pre-flight（Spec §4.6）。

    **为什么单开一条闸：仓库里所有 `df` 都只查数据盘 `autodl-tmp`**
    （doctor:920 / status:1216），但上行实际落在**系统盘**——远端 `data/` 是
    `/root/anime-video-agent/data` 的实体目录，`config/cloud.json` 的 `remote_data`
    是有意不接线的。于是 doctor 会打「数据盘空间充足 ✓」的同时，让全季帧
    （~550MB）或 2h Live（~1GB）把系统盘写满。判据一直在回答另一个问题。

    失败模式的代价不是钱（停机不计数）：是**那次开机任务跑到一半才炸**。
    所以放在入闸处拒，而不是等 rsync 写爆。
    """
    upload_bytes = sync_pairs_bytes(sync_pairs)
    if upload_bytes <= 0:
        return
    res = _ssh(
        f"df -BG {shlex.quote(remote_ep_dir)} | tail -1 | awk '{{print $4}}' | tr -d 'G'",
        host=host, check=False,
    )
    verdict, detail = evaluate_upload_capacity(upload_bytes, res.stdout)
    if verdict == "ok":
        print(f"  ✓ 远端余量充足：{detail}")
        return
    if verdict == "unknown":
        print(f"WARN 无法确认远端余量（{detail}），继续上行", file=sys.stderr)
        return
    cleanup_cmd = "cloud clean-frames（弹卡；清的是可秒级重抽的 *_cap 打标帧）"
    raise SystemExit(
        f"FAIL 远端目标盘余量不足，拒绝上行：{detail}\n"
        f"     上行落在 {remote_ep_dir}，而远端 data/ 在**系统盘**上\n"
        f"     （config/cloud.json 的 remote_data 有意未接线）。两条修法任选：\n"
        f"       ① 清掉上一轮打标帧（本地 shots caption-frames 可秒级重抽）：\n"
        f"          {cleanup_cmd}\n"
        f"       ② 全季打标前把远端 data/ 改道数据盘（一次性）：cloud relocate-data（弹卡，幂等）\n"
        f"     详情：docs/dev/postmortems/workflow-history.md「云端中间物」"
    )


#: 远端数据盘挂载点（AutoDL 约定；doctor / status 查的就是它）。
REMOTE_DATA_DISK = "/root/autodl-tmp"


def relocate_data_script(remote_root: str, data_disk: str = REMOTE_DATA_DISK) -> str:
    """把远端 `<仓库>/data` 改道到数据盘并软链回来的 shell 脚本（纯函数，D59）。

    取代 WORKFLOW「阶段 0 前置」里要人登远端手敲的 mv + ln -s。幂等：已经是软链直接报
    ALREADY 退出 0；数据盘上已有同名目录（上次挪到一半？）拒，交人看，不覆盖不合并。
    """
    src, dst = shlex.quote(f"{remote_root}/data"), shlex.quote(f"{data_disk}/data")
    return (
        f"set -e; "
        f"if [ -L {src} ]; then echo ALREADY $(readlink {src}); exit 0; fi; "
        f"if [ -e {dst} ]; then echo CONFLICT; exit 3; fi; "
        f"if [ -e {src} ]; then mv {src} {dst}; else mkdir -p {dst}; fi; "
        f"ln -s {dst} {src}; echo MOVED; df -BG / | tail -1"
    )


def clean_frames_script(remote_root: str) -> str:
    """清远端上一轮 VLM 打标帧（`*_cap`，本地 `shots caption-frames` 可秒级重抽）的脚本（D59）。

    取代余量闸报错里让人敲的 `cloud exec --fg "rm -rf …"`（agent 不许用 exec）。
    """
    frames = shlex.quote(f"{remote_root}/data/library/shots/frames")
    return f"cd {frames} 2>/dev/null || {{ echo NONE; exit 0; }}; du -sh -- *_cap 2>/dev/null | tail -5; rm -rf -- *_cap; echo CLEANED"


def _remote_maintenance(args: argparse.Namespace, script_of, label: str) -> int:
    """relocate-data / clean-frames 共用的前置：实例可达、没有活跃任务，再执行脚本。"""
    ctx = _cloud_context(args)
    if ctx["provider"] == "colab-drive":
        print(f"[*] {label} 仅适用于 SSH 挂载实例，colab-drive 队列模式无需此维护操作。")
        return 0
    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    rpaths = ctx["rpaths"]
    remote_root = rpaths["remote_root"]
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up（计费卡）")
    active = remote_active_tasks(host)
    if active:
        raise SystemExit(f"FAIL 远端还有任务在跑（{', '.join(active)}），{label}会动到它在用的数据——等它结束再来")
    script = script_of(remote_root) if label != "远端 data/ 改道数据盘" else relocate_data_script(remote_root, rpaths["remote_data_disk"])
    print(f"[*] {label}：{host}\n    {script}")
    res = _ssh(script, host=host, check=False, timeout=600)
    out = (res.stdout or "").strip()
    print(out)
    if res.returncode == 3 and "CONFLICT" in out:
        raise SystemExit(f"FAIL 数据盘上已有 {rpaths['remote_data_disk']}/data（上次挪到一半？），不覆盖不合并——交人看")
    if res.returncode != 0:
        raise SystemExit(f"FAIL {label}失败（退出码 {res.returncode}）：{(res.stderr or '').strip()[:300]}")
    return 0


#: 远端代码仓库来源（doctor 修法原先让人经 `cloud exec` 手敲 git clone）。
REMOTE_REPO_URL = "https://github.com/songhuiming2007-coder/anime-video-agent.git"
HF_MIRROR = "https://hf-mirror.com"


def fix_env_commands(
    *,
    remote_root: str,
    models_root: str,
    repo_missing: bool,
    bad_models: list[tuple[str, str]],
    repo_url: str = REMOTE_REPO_URL,
    provider: str = "autodl",
    python: str = "python3",
) -> list[str]:
    """`cloud fix-env` 要在远端执行的命令（纯函数，D59）。

    只做 doctor 能判定、且修法确定的两件事：克隆缺失的仓库、按 config/cloud.json 的 `models`
    清单重下缺失 / 体积不足 / 结构校验不过的模型。模型 id 只来自配置清单，不接受运行时参数。
    AutoDL 走 network_turbo 与 hf-mirror；Colab 直连 GitHub 与 HuggingFace 官方源。
    """
    is_colab = provider in ("colab", "colab-drive")
    cmds = []
    if repo_missing:
        clone_cmd = f"git clone {shlex.quote(repo_url)} {shlex.quote(remote_root)}"
        cmds.append(clone_cmd if is_colab else f"source /etc/network_turbo && {clone_cmd}")
    py_exe = python if is_colab else "python3"
    for m_id, m_dir in bad_models:
        py = (f"from huggingface_hub import snapshot_download; "
              f"snapshot_download({json.dumps(m_id)}, local_dir={json.dumps(models_root + '/' + m_dir)})")
        dl_cmd = f"{py_exe} -c {shlex.quote(py)}"
        cmds.append(dl_cmd if is_colab else f"export HF_ENDPOINT={HF_MIRROR} && {dl_cmd}")
    return cmds


def cmd_fix_env(args: argparse.Namespace) -> int:
    """按 doctor 的判据修远端环境：仓库缺了就克隆、模型缺 / 小 / 坏就重下（D59，弹卡）。"""
    ctx = _cloud_context(args)
    provider = ctx["provider"]
    if provider == "colab-drive":
        print("[*] colab-drive 队列模式的模型与环境由 Colab Bootstrap Cell 1 就地安装；如需预检请在 Colab 运行 Cell 1。")
        return 0
    _require_local_cloudflared(ctx["profile"])
    cfg_global = ctx["cfg_global"]
    rpaths = ctx["rpaths"]
    host = ctx["host"]
    remote_root = rpaths["remote_root"]
    models_root = rpaths["remote_models"]
    py_exe = rpaths["remote_python"]
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up（计费卡）")
    active = remote_active_tasks(host)
    if active:
        raise SystemExit(f"FAIL 远端还有任务在跑（{', '.join(active)}），等它结束再修环境")
    repo_missing = _ssh(f"test -d {shlex.quote(remote_root)}/.git", host=host, check=False).returncode != 0
    bad: list[tuple[str, str]] = []
    for m_id, spec in cfg_global.get("models", {}).items():
        m_dir = spec.get("dir", m_id)
        min_gb = float(spec.get("min_gb", 1.0))
        res = _ssh(f"du -s -BG {shlex.quote(models_root + '/' + m_dir)} 2>/dev/null | awk '{{print $1}}' | tr -d 'G'",
                   host=host, check=False)
        try:
            ok = float(res.stdout.strip()) >= min_gb
        except ValueError:
            ok = False
        if ok:
            st = _ssh(build_model_structure_probe_cmd(m_dir, models_root=models_root, python=py_exe),
                      host=host, check=False, timeout=90)
            ok = parse_model_structure_result(st.stdout, st.returncode)[0] != "FAIL"
        if not ok:
            bad.append((m_id, m_dir))
    cmds = fix_env_commands(remote_root=remote_root, models_root=models_root,
                            repo_missing=repo_missing, bad_models=bad,
                            provider=provider, python=py_exe)
    if not cmds:
        print("OK 仓库与模型都在位，无需修复")
        return 0
    for cmd in cmds:
        print(f"[*] {cmd}")
        res = _ssh(cmd, host=host, check=False, timeout=3 * 3600, capture_output=False)
        if res.returncode != 0:
            raise SystemExit(f"FAIL 远端修复失败（退出码 {res.returncode}）：{cmd}")
    print("OK 修复完成；再跑一次 cloud doctor 确认")
    return 0


def cmd_relocate_data(args: argparse.Namespace) -> int:
    return _remote_maintenance(args, relocate_data_script, "远端 data/ 改道数据盘")


def cmd_clean_frames(args: argparse.Namespace) -> int:
    return _remote_maintenance(args, clean_frames_script, "清远端打标帧")


#: `push --with-audio` 带上的本地配音目录（wav、manifest；attic 留档不传）
AUDIO_SYNC_ITEM = "03-audio/"


def cmd_push(args: argparse.Namespace) -> int:
    """上行同步命令：SSH 后端走 rsync；colab-drive 暂存到 Drive `staging/`。"""
    try:
        ep_dir, rel_path = resolve_episode_rel_path(args.target)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在: {ep_dir}")

    ctx = _cloud_context(args)
    cfg_global = ctx["cfg_global"]
    provider = ctx["provider"]
    sync_items = list(cfg_global.get(
        "sync", {}
    ).get("up", ["02-script.md", "01-topic.md", "config/", "03-audio/corrections.json", "04-patch/"]))
    for extra_item in ["03-audio/corrections.json", "04-patch/"]:
        if extra_item not in sync_items:
            sync_items.append(extra_item)
    with_audio = bool(getattr(args, "with_audio", False))
    if with_audio:
        if not (ep_dir / "03-audio" / "manifest.json").exists():
            raise SystemExit(f"FAIL --with-audio：本地没有 {ep_dir.name}/03-audio/manifest.json，没有可上行的配音")
        if provider != "colab-drive":
            sync_items = [*sync_items, AUDIO_SYNC_ITEM]

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        staged = cloud_drive.stage_episode_inputs(
            drive_root, ep_dir, rel_path, sync_items, paths.ROOT, with_audio=with_audio
        )
        print(f"[OK] push 完成（Drive 暂存 {len(staged)} 项）→ {drive_root / 'staging' / rel_path}")
        return 0

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = ctx["rpaths"]["remote_root"]
    remote_ep_dir = f"{remote_root}/{rel_path}"
    print(f"[*] 正在推送当期必要资产至远端（后端: {provider}）...")
    print(f"    本地源: {ep_dir}")
    print(f"    远端宿: {host}:{remote_ep_dir}")

    sync_pairs = build_sync_up_files(ep_dir, sync_items, remote_ep_dir, remote_root)
    dirs = [remote_ep_dir] + sorted({dst.rsplit("/", 1)[0] for src, dst in sync_pairs
                                     if not src.endswith("/")} - {remote_ep_dir})
    _ssh("mkdir -p " + " ".join(shlex.quote(d) for d in dirs), host=host)

    _preflight_remote_capacity(host, remote_root, remote_ep_dir, sync_pairs)

    for src, dst in sync_pairs:
        excl = ["attic/"] if src.rstrip("/").endswith("/" + AUDIO_SYNC_ITEM.rstrip("/")) else None
        res = run_rsync(src, f"{host}:{dst}", extra_excludes=excl)
        if res.returncode != 0:
            raise SystemExit(f"FAIL 推送失败 {src} -> {dst}: {res.stderr.strip()}")
        print(f"  ✓ 已同步: {src} -> {dst}")

    print("[OK] push 完成。资产已在云端就位。")
    return 0


CODE_SYNC_ITEMS = ("pipeline/", "config/", "pyproject.toml")


def cmd_sync_code(args: argparse.Namespace) -> int:
    """D107：把本地 pipeline/ config/（不含 *.local.json）pyproject.toml 推到远端或 Drive 队列，再按哈希核对一遍。"""
    ctx = _cloud_context(args)
    cfg_global = ctx["cfg_global"]
    provider = ctx["provider"]

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        active = cloud_drive.active_drive_jobs(drive_root)
        if active:
            raise SystemExit(f"FAIL Drive 队列有未完成任务（{', '.join(active)}），跑完再 sync-code")
        local_mf = code_manifest(paths.ROOT)
        synced = cloud_drive.sync_code_to_drive(drive_root, paths.ROOT, local_mf)
        print(f"[OK] 已同步 {synced} 个代码文件至 Drive {drive_root / 'code'}")
        return 0

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = ctx["rpaths"]["remote_root"]
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up")
    active = remote_active_tasks(host=host)
    if active:
        raise SystemExit(f"FAIL 远端有任务在跑（{', '.join(active)}），这时换代码会让它半新半旧；跑完再同步")
    for item in CODE_SYNC_ITEMS:
        res = run_rsync(str(paths.ROOT / item) + ("/" if item.endswith("/") else ""),
                        f"{host}:{remote_root}/{item}")
        if res.returncode != 0:
            raise SystemExit(f"FAIL 同步 {item} 失败: {res.stderr.strip()}")
        print(f"  ✓ 已同步 {item}")
    code_ok, code_lines = code_drift_lines(_check_remote_drift(host, cfg_global, provider))
    print(("[OK] " if code_ok else "FAIL ") + "\n     ".join(code_lines))
    return 0 if code_ok else 1


# ---------------------------------------------------------------------------
# D103 ②：番池素材（data/library/ 下的番剧集与 SP）的云端打标
# ---------------------------------------------------------------------------
# push / run / pull 的 captions 分支把目录钉在 `<期>/04-patch/` 下，番池素材上不了云；cloud exec 对 AVA 永久禁用。
# 这三条命令只搬 library 下打标要的两样输入（镜头表、打标帧）与一样产物，远端路径与本地同一相对路径，
# 远端 `vindex captions` 不传目录参数即用默认路径（10-17 supercell 实跑核过只要这两样）。
LIB_CAPTIONS_SESSION = "ava-lib-captions"
_LIB_KEY = re.compile(rf"SP{SP_NUM}|S\d{{2}}E\d{{2}}")


def lib_dirs() -> tuple[str, str, str]:
    """(镜头表, 打标帧, 打标产物) 相对仓库根的路径：取自 shots / vindex 的常量，不另写一份。"""
    from . import shots, vindex

    def rel(p: Path) -> str:
        return p.relative_to(paths.ROOT).as_posix()

    return rel(shots.SHOTS_DIR), rel(shots.FRAMES_DIR), rel(vindex.VINDEX_DIR)


def validate_lib_target(pool: str, keys: list[str]) -> list[str]:
    """池名与集键校验（纯函数）→ 去重后的集键。池名允许中文；集键 `SPnn` / `SxxEyy`，可逗号分隔。"""
    if (not pool or pool in (".", "..") or pool.startswith("-") or "/" in pool or "\\" in pool
            or any(c.isspace() or ord(c) < 32 for c in pool)):
        raise ValueError(f"池名不合法：{pool!r}（不能含 / 、空白或控制字符，不能以 - 开头）")
    out: list[str] = []
    for raw in keys:
        for k in (x.strip() for x in raw.split(",")):
            if not k:
                continue
            if not _LIB_KEY.fullmatch(k):
                raise ValueError(f"集键不合法：{k!r}（写 SP01、SP100 或 S01E01）")
            if k not in out:
                out.append(k)
    if not out:
        raise ValueError("没有给集键：要打哪些集写在命令里（如 SP01 SP02）")
    return out


def lib_missing_inputs(pool: str, keys: list[str], dirs: tuple[str, str, str], root: Path) -> list[str]:
    """本地缺的打标输入（纯函数）→ 给人看的行；空表示齐全。"""
    shots_rel, frames_rel, _ = dirs
    miss: list[str] = []
    for k in keys:
        if not (root / shots_rel / f"{pool}_{k}.json").is_file():
            miss.append(f"{k}：没有镜头表 {shots_rel}/{pool}_{k}.json（先 shots build）")
        cap = root / frames_rel / f"{pool}_{k}_cap"
        if not cap.is_dir() or not any(cap.iterdir()):
            miss.append(f"{k}：没有打标帧 {frames_rel}/{pool}_{k}_cap/（先 shots caption-frames {pool} {k}）")
    return miss


def build_lib_up_pairs(pool: str, keys: list[str], remote_root: str,
                       dirs: tuple[str, str, str], root: Path) -> list[tuple[str, str]]:
    """上行 (src, dst) 清单（纯函数）：镜头表文件 + 打标帧目录（src 带尾斜杠、dst 不带，同 build_sync_up_files）。"""
    shots_rel, frames_rel, _ = dirs
    pairs: list[tuple[str, str]] = []
    for k in keys:
        pairs.append((str(root / shots_rel / f"{pool}_{k}.json"), f"{remote_root}/{shots_rel}/{pool}_{k}.json"))
        pairs.append((f"{root / frames_rel / f'{pool}_{k}_cap'}/", f"{remote_root}/{frames_rel}/{pool}_{k}_cap"))
    return pairs


def build_lib_captions_command(
    pool: str,
    keys: list[str],
    remote_root: str,
    python: str = DEFAULT_REMOTE_PYTHON,
    *,
    batch: int | None = None,
    limit: int | None = None,
    confirm_cost: bool = False,
    provider: str = "autodl",
    models_root: str | None = None,
    heartbeat_path: str | None = None,
) -> str:
    """远端打标命令（纯函数）：首尾心跳，与 build_remote_run_command 同口径；目录用 vindex 默认值。"""
    hb = heartbeat_path or (COLAB_HEARTBEAT_PATH if provider != "autodl" else WATCHDOG_HEARTBEAT_PATH)
    env_parts: list[str] = []
    if provider != "autodl":
        env_parts.append(f"AVA_CLOUD_PROVIDER={shlex.quote(provider)}")
    if models_root:
        env_parts.append(f"AVA_REMOTE_MODELS={shlex.quote(models_root)}")
    env_prefix = (" ".join(env_parts) + " ") if env_parts else ""
    flags = ""
    if batch is not None:
        flags += f" --batch {int(batch)}"
    if limit is not None:
        flags += f" --limit {int(limit)}"
    if confirm_cost:
        flags += " --confirm-cost"
    return (
        f"cd {shlex.quote(remote_root)} && "
        f"touch {hb} && "
        f"{env_prefix}{python} -m pipeline.vindex captions {shlex.quote(pool)} {' '.join(keys)}{flags} && "
        f"touch {hb}"
    )


def _lib_target(args: argparse.Namespace) -> list[str]:
    try:
        return validate_lib_target(args.pool, list(args.keys))
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc


def _note_session_task(task: str, label: str) -> None:
    """把任务记进活跃会话文件（账本按它列本次跑过什么）；文件坏了只警告，不挡远端执行。"""
    sfile = get_active_session_file()
    if not sfile.exists():
        return
    try:
        sdata = json.loads(sfile.read_text(encoding="utf-8"))
        sdata.setdefault("tasks", [])
        if task not in sdata["tasks"]:
            sdata["tasks"].append(task)
        sdata["episode"] = label
        sfile.write_text(json.dumps(sdata, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"WARN 会话文件损坏，本次任务未记账（不影响远端执行）: {e}", file=sys.stderr)


def cmd_lib_push(args: argparse.Namespace) -> int:
    """番池素材打标输入上行：镜头表 + 打标帧。缺一样整批拒，不发送。"""
    keys = _lib_target(args)
    paths.require_data()
    dirs = lib_dirs()
    shots_rel, frames_rel, _ = dirs
    if not any((paths.ROOT / shots_rel).glob(f"{args.pool}_*.json")):
        raise SystemExit(f"FAIL 本地 {shots_rel}/ 下没有池「{args.pool}」的任何镜头表：池名写错了？")
    miss = lib_missing_inputs(args.pool, keys, dirs, paths.ROOT)
    if miss:
        raise SystemExit("FAIL 打标输入不全，整批没传：\n     " + "\n     ".join(miss))

    ctx = _cloud_context(args)
    provider = ctx["provider"]
    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        staged = cloud_drive.stage_lib_inputs(drive_root, args.pool, keys, shots_rel, frames_rel, paths.ROOT)
        print(f"[OK] lib-push 完成（Drive 暂存 {len(staged)} 项）：{args.pool} {' '.join(keys)}。下一步 cloud lib-captions {args.pool} {' '.join(keys)} --provider colab-drive")
        return 0

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = ctx["rpaths"]["remote_root"]
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up")
    remote_shots = f"{remote_root}/{shots_rel}"
    _ssh(f"mkdir -p {shlex.quote(remote_shots)} {shlex.quote(f'{remote_root}/{frames_rel}')}", host=host)
    pairs = build_lib_up_pairs(args.pool, keys, remote_root, dirs, paths.ROOT)
    _preflight_remote_capacity(host, remote_root, remote_shots, pairs)
    for src, dst in pairs:
        res = run_rsync(src, f"{host}:{dst}")
        if res.returncode != 0:
            raise SystemExit(f"FAIL 上传失败 {src} -> {dst}: {res.stderr.strip()}")
        print(f"  ✓ 已同步: {src} -> {dst}")
    print(f"[OK] lib-push 完成：{args.pool} {' '.join(keys)}。下一步 cloud lib-captions {args.pool} {' '.join(keys)}（计费）")
    return 0


def cmd_lib_captions(args: argparse.Namespace) -> int:
    """番池素材云端打标：先核远端/Drive 代码（D107），再提交任务。"""
    keys = _lib_target(args)
    ctx = _cloud_context(args)
    cfg_global = ctx["cfg_global"]
    provider = ctx["provider"]
    rpaths = ctx["rpaths"]

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        mf = cloud_drive.read_drive_code_manifest(drive_root)
        if isinstance(mf, str):
            raise SystemExit(f"FAIL 没开跑：{mf}")
        code_ok, code_lines = code_drift_lines(compare_code_manifest(code_manifest(paths.ROOT), mf))
        if not code_ok:
            raise SystemExit("FAIL 没开跑：" + "\n     ".join(code_lines))
        active = cloud_drive.active_drive_jobs(drive_root)
        if active:
            raise SystemExit(f"FAIL Drive 队列仍有未完成任务（{', '.join(active)}），请等待完成后再发")
        _note_session_task("captions", f"library:{args.pool}")
        extra_tokens: list[str] = list(keys)
        if args.batch is not None:
            extra_tokens.extend(["--batch", str(int(args.batch))])
        if args.limit is not None:
            extra_tokens.extend(["--limit", str(int(args.limit))])
        if args.confirm_cost:
            extra_tokens.append("--confirm-cost")
        job_id = f"lib_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        cloud_drive.submit_drive_job(
            drive_root,
            job_id=job_id,
            task="captions",
            ep_rel_path="",
            extra_args=" ".join(shlex.quote(t) for t in extra_tokens),
            lib_pool=args.pool,
            lib_keys=keys,
        )
        print(f"[OK] 已提交 Drive 队列番池打标任务: {job_id}")
        print(f"     查看日志: python -m pipeline.cloud logs {job_id} -f --provider colab-drive")
        print(f"     跑完后:   python -m pipeline.cloud lib-pull {args.pool} {' '.join(keys)} --provider colab-drive")
        return 0

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = rpaths["remote_root"]
    code_ok, code_lines = code_drift_lines(_check_remote_drift(host, cfg_global, provider))
    if not code_ok:
        raise SystemExit("FAIL 没开跑：" + "\n     ".join(code_lines))
    if LIB_CAPTIONS_SESSION in remote_active_tasks(host=host):
        raise SystemExit(f"FAIL 上一批番池打标（{LIB_CAPTIONS_SESSION}）还在跑；跑完再开下一批，"
                         f"进度看 cloud attach --session {LIB_CAPTIONS_SESSION}")

    _note_session_task("captions", f"library:{args.pool}")
    _ssh(f"touch {rpaths['heartbeat_path']}", host=host)
    remote_cmd = build_lib_captions_command(
        args.pool,
        keys,
        remote_root,
        rpaths["remote_python"],
        batch=args.batch,
        limit=args.limit,
        confirm_cost=args.confirm_cost,
        provider=provider,
        models_root=rpaths["remote_models"] if provider != "autodl" else None,
        heartbeat_path=rpaths["heartbeat_path"],
    )
    print(f"[*] 启动远端番池打标 (tmux: {LIB_CAPTIONS_SESSION})")
    print(f"    远端执行: {remote_cmd}")
    _ssh(build_tmux_launch_command(LIB_CAPTIONS_SESSION, remote_cmd, remote_root), host=host)
    print("[OK] 已在远端后台运行。")
    print(f"     查看进度: python -m pipeline.cloud attach --session {LIB_CAPTIONS_SESSION}")
    print(f"     跑完后:   python -m pipeline.cloud lib-pull {args.pool} {' '.join(keys)}，再本地 vindex embed {args.pool}")
    return 0


def cmd_lib_pull(args: argparse.Namespace) -> int:
    """番池打标产物下行：只拉命令里写的集键；远端没有的列出来，退出码非 0。"""
    keys = _lib_target(args)
    paths.require_data()
    _, _, vindex_rel = lib_dirs()
    ctx = _cloud_context(args)
    provider = ctx["provider"]
    local_dir = paths.ROOT / vindex_rel
    local_dir.mkdir(parents=True, exist_ok=True)

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        if cloud_drive.active_drive_jobs(drive_root):
            raise SystemExit("FAIL Drive 队列仍有未完成任务，跑完再 lib-pull")
        job_id, ok_list, missing = cloud_drive.pull_drive_lib_outputs(
            drive_root, args.pool, keys, vindex_rel, local_dir
        )
        for name in ok_list:
            print(f"  ✓ 已从 Drive ({job_id}) 拉取: {vindex_rel}/{name}")
        if missing:
            print("FAIL Drive 未见产物：" + "；".join(missing), file=sys.stderr)
            return 1
        print(f"[OK] lib-pull 完成。下一步本地 vindex embed {args.pool}，再 vindex status --anime {args.pool} 对账")
        return 0

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = ctx["rpaths"]["remote_root"]
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up")
    if LIB_CAPTIONS_SESSION in remote_active_tasks(host=host):
        raise SystemExit(f"FAIL 番池打标（{LIB_CAPTIONS_SESSION}）还在跑，跑完再拉：中途拉到的可能是半份产物")
    missing: list[str] = []
    for k in keys:
        name = f"{args.pool}_{k}.captions.json"
        dst = local_dir / name
        existed = dst.exists()
        res = run_rsync(f"{host}:{remote_root}/{vindex_rel}/{name}", str(dst))
        if res.returncode == 0:
            print(f"  ✓ 已拉取{'（覆盖本地旧文件）' if existed else ''}: {vindex_rel}/{name}")
        else:
            tail = (res.stderr.strip().splitlines() or [""])[-1][:120]
            missing.append(f"{k}（rsync 退出码 {res.returncode}：{tail}）")
    if missing:
        print("FAIL 远端没拉到：" + "；".join(missing) + "。打标没跑完或失败，看 cloud logs / attach", file=sys.stderr)
        return 1
    print(f"[OK] lib-pull 完成。下一步本地 vindex embed {args.pool}，再 vindex status --anime {args.pool} 对账")
    return 0


def resolve_episode_rel_path(target: Any) -> tuple[Path, str]:
    """把本期目录参数解析为 (绝对路径, 相对仓库根的路径)。纯函数。

    **绝不能用 Path.resolve()**：仓库的 `data/` 是指向外置硬盘的符号链接
    （本机为 /Volumes/Samsung T7/anime-video-data），resolve() 会穿透它，
    结果不再位于仓库根之下，旧写法于是静默回退成 `data/episodes/<name>`，
    **丢掉中间层级**（如企划名 EGOIST-传奇企划志），在远端造出错位目录树。
    静默 fallback 把错误藏起来是最坏的做法，这里改为显式报错：宁可失败也不猜。
    """
    raw = Path(target)
    ep_dir = raw if raw.is_absolute() else (paths.ROOT / raw)
    ep_dir = Path(os.path.normpath(ep_dir))
    try:
        rel = ep_dir.relative_to(paths.ROOT)
    except ValueError as exc:
        raise ValueError(
            f"本期目录必须位于仓库内 ({paths.ROOT})，实际得到 {ep_dir}。\n"
            f"       注意：不要用 Path.resolve()，它会穿透 data/ 符号链接。"
        ) from exc
    return ep_dir, str(rel)


def _verify_pulled_outputs(ep_dir: Path) -> int:
    """统一校验从云端拉回的本期产物（SSH 与 Drive 后端共用）。"""
    session_tasks: list[str] = []
    sfile = get_active_session_file()
    if sfile.exists():
        try:
            session_tasks = json.loads(sfile.read_text(encoding="utf-8")).get("tasks", [])
        except Exception as e:
            print(f"WARN 会话文件损坏，无法确定本次应产出何种产物: {e}", file=sys.stderr)

    probe_log = ep_dir / "03-audio" / "probe.log"
    mf = ep_dir / "03-audio" / "manifest.json"
    verified_any = False
    has_failure = False

    if "probe" in session_tasks or not session_tasks:
        if probe_log.exists():
            print(f"[OK] 03-audio/probe.log 已拉回: {probe_log.read_text(encoding='utf-8').strip()[:80]}")
            verified_any = True
        else:
            print("FAIL 本次 probe 任务未拉回 03-audio/probe.log", file=sys.stderr)
            has_failure = True

    if "tts" in session_tasks:
        if mf.exists():
            try:
                json.loads(mf.read_text(encoding="utf-8"))
                print("[OK] 03-audio/manifest.json 结构校验通过。")
                verified_any = True
            except Exception as e:
                print(f"FAIL 拉回的 manifest.json 损坏: {e}", file=sys.stderr)
                has_failure = True
        else:
            print("FAIL 本次 tts 任务未拉回 03-audio/manifest.json", file=sys.stderr)
            has_failure = True

    if "captions" in session_tasks:
        cap_dir = ep_dir / "04-patch" / "vindex"
        cap_files = list(cap_dir.glob("*.captions.json")) if cap_dir.is_dir() else []
        if cap_files:
            total_pending = 0
            for cf in cap_files:
                try:
                    cdata = json.loads(cf.read_text(encoding="utf-8"))
                    rows = cdata.get("captions", [])
                    total_pending += sum(1 for r in rows if r.get("status") not in ("ok", "failed"))
                except Exception:
                    total_pending += 1
            if total_pending > 0:
                print(f"FAIL 拉回的 captions 尚有 {total_pending} 个镜头未完成（pending）", file=sys.stderr)
                has_failure = True
            else:
                print("[OK] 04-patch/vindex/ captions 校验通过（无 pending 行）。")
                verified_any = True
        else:
            print("FAIL 本次 captions 任务未拉回 04-patch/vindex/*.captions.json", file=sys.stderr)
            has_failure = True

    if not verified_any:
        print(f"WARN 未验证到任何本次任务产物（本次任务: {session_tasks or '未知'}）", file=sys.stderr)

    if verified_any and not has_failure:
        print("[OK] pull 完成。")
        return 0
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """远端任务触发命令：白名单限制，支持 AutoDL/Colab SSH (tmux) 与 Colab Drive 队列。"""
    task = validate_task_whitelist(args.task)
    try:
        ep_dir, rel_path = resolve_episode_rel_path(args.target)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在: {ep_dir}")

    raw_extra = list(getattr(args, "extra", None) or [])
    try:
        extra_provider, clean_extra = _extract_provider_from_extra(raw_extra)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc

    cli_prov = extra_provider or getattr(args, "provider", None)
    ctx = _cloud_context(args, cli_provider=cli_prov)
    cfg_global = ctx["cfg_global"]
    provider = ctx["provider"]
    rpaths = ctx["rpaths"]

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        mf = cloud_drive.read_drive_code_manifest(drive_root)
        if isinstance(mf, str):
            raise SystemExit(f"FAIL 没开跑：{mf}")
        code_ok, code_lines = code_drift_lines(compare_code_manifest(code_manifest(paths.ROOT), mf))
        if not code_ok:
            raise SystemExit("FAIL 没开跑：" + "\n     ".join(code_lines))
        if task == "tts" and "--apply-patch" in clean_extra:
            staged_mf = drive_root / "staging" / rel_path / "03-audio" / "manifest.json"
            if not staged_mf.is_file():
                raise SystemExit(
                    "FAIL 没开跑：Drive 暂存区没有 03-audio/manifest.json，--apply-patch 会整期重配。\n"
                    f"     先 `python -m pipeline.cloud push {args.target} --with-audio --provider colab-drive`"
                )
        extra_str = " ".join(clean_extra)
        try:
            # 用纯函数提前校验 extra_args 合法性
            build_remote_run_command(
                task, rel_path, rpaths["remote_root"], python=rpaths["remote_python"],
                extra_args=extra_str, provider="colab-drive",
                models_root=rpaths["remote_models"], heartbeat_path=rpaths["heartbeat_path"],
            )
        except ValueError as exc:
            raise SystemExit(f"FAIL 远端命令参数非法: {exc}") from exc
        _note_session_task(task, ep_dir.name)
        job_id = f"ep_{task}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        cloud_drive.submit_drive_job(
            drive_root,
            job_id=job_id,
            task=task,
            ep_rel_path=rel_path,
            extra_args=extra_str,
        )
        print(f"[OK] Drive 队列任务已提交: {job_id} (task={task})")
        print(f"     查看日志: python -m pipeline.cloud logs {job_id} -f --provider colab-drive")
        print(f"     完成后请执行:")
        print(f"       python -m pipeline.cloud pull {args.target} --provider colab-drive")
        print(f"       python -m pipeline.cloud down --provider colab-drive")
        return 0

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = rpaths["remote_root"]

    code_ok, code_lines = code_drift_lines(_check_remote_drift(host, cfg_global, provider))
    if not code_ok:
        raise SystemExit("FAIL 没开跑：" + "\n     ".join(code_lines))

    if task == "tts" and "--apply-patch" in clean_extra:
        remote_mf = f"{remote_root}/{rel_path}/03-audio/manifest.json"
        probe = _ssh(f"test -f {shlex.quote(remote_mf)} && echo present || echo absent",
                     host=host, check=False)
        if (probe.stdout or "").strip() == "absent":
            raise SystemExit(
                "FAIL 没开跑：远端没有 03-audio/manifest.json（多半是换了实例），--apply-patch 会整期重配。\n"
                f"     先 `python -m pipeline.cloud push {args.target} --with-audio` 把本地配音带上去")

    _note_session_task(task, ep_dir.name)
    _ssh(f"touch {rpaths['heartbeat_path']}", host=host)

    session_name = f"ava-{task}"
    extra_str = " ".join(clean_extra)
    try:
        remote_cmd = build_remote_run_command(
            task,
            rel_path,
            remote_root,
            python=rpaths["remote_python"],
            extra_args=extra_str,
            provider=provider,
            models_root=rpaths["remote_models"] if provider != "autodl" else None,
            heartbeat_path=rpaths["heartbeat_path"],
        )
    except ValueError as exc:
        raise SystemExit(f"FAIL 远端命令参数非法: {exc}") from exc
    tmux_launch = build_tmux_launch_command(session_name, remote_cmd, remote_root)

    print(f"[*] 启动远端任务: {task} (后端: {provider}, tmux: {session_name})")
    print(f"    远端执行: {remote_cmd}")
    _ssh(tmux_launch, host=host)

    print(f"[OK] 任务已在远端 tmux 会话运行。")
    print(f"     查看日志: python -m pipeline.cloud attach --session {session_name}")
    print(f"     完成后请执行:")
    print(f"       python -m pipeline.cloud pull {args.target}")
    print(f"       python -m pipeline.cloud down")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """下行拉取命令：支持 AutoDL/Colab SSH (rsync) 与 Colab Drive 队列输出复制。"""
    try:
        ep_dir, rel_path = resolve_episode_rel_path(args.target)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在: {ep_dir}")

    ctx = _cloud_context(args)
    cfg_global = ctx["cfg_global"]
    provider = ctx["provider"]
    sync_items = list(cfg_global.get("sync", {}).get("down", ["03-audio/", "04-clips.json", "06-check.log", "04-patch/"]))
    if "04-patch/" not in sync_items:
        sync_items.append("04-patch/")

    if provider == "colab-drive":
        drive_root = _require_drive_root(ctx)
        active = cloud_drive.active_drive_jobs(drive_root)
        if active:
            print(f"[*] 提示: Drive 队列仍有未完成任务 ({active})")
        job_id, pulled = cloud_drive.pull_drive_outputs(drive_root, ep_dir, rel_path, sync_items)
        if not job_id:
            print(f"[*] Drive 队列尚未找到 {rel_path} 的任务输出。")
        else:
            for item in pulled:
                print(f"  ✓ 已从 Drive ({job_id}) 拉取: {item}")
        return _verify_pulled_outputs(ep_dir)

    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    remote_root = ctx["rpaths"]["remote_root"]

    if run_rsync is _ORIGINAL_RUN_RSYNC:
        if not is_ssh_reachable(host):
            raise SystemExit("FAIL 实例不可达/已关机，先 `python -m pipeline.cloud up`")
        active = remote_active_tasks(host=host)
        if active:
            print(f"[*] 提示: 远端仍有活跃后台任务正在运行 ({active})")

    remote_ep_dir = f"{remote_root}/{rel_path}"
    print(f"[*] 从远端拉取执行产物（后端: {provider}）...")

    sync_pairs = build_sync_down_files(ep_dir, sync_items, remote_ep_dir)
    for src, dst in sync_pairs:
        res = run_rsync(f"{host}:{src}", dst)
        if res.returncode == 0:
            print(f"  ✓ 已拉取: {src} -> {dst}")
        else:
            tail = (res.stderr.strip().splitlines() or [""])[-1][:120]
            print(f"  ○ 未发现远端产物或跳过: {src}（rsync 退出码 {res.returncode}：{tail}）")

    return _verify_pulled_outputs(ep_dir)


def cmd_attach(args: argparse.Namespace) -> int:
    """本地接入远端 tmux 会话（colab-drive 自动回退为实时跟踪 Drive 任务日志）。"""
    ctx = _cloud_context(args)
    session_name = args.session or "ava-tts"
    if ctx["provider"] == "colab-drive":
        return cmd_logs(argparse.Namespace(
            provider="colab-drive",
            session=session_name,
            follow=True,
            lines=50,
        ))
    _require_local_cloudflared(ctx["profile"])
    host = ctx["host"]
    print(f"[*] 接入 tmux 会话 {session_name} ({host})...")
    return subprocess.call([*ssh_base(), "-t", host, f"tmux attach-session -t {session_name}"])


# ---------------------------------------------------------------------------
# 第四节：CLI 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    provider_parent = argparse.ArgumentParser(add_help=False)
    provider_parent.add_argument(
        "--provider",
        choices=list(VALID_PROVIDERS),
        default=argparse.SUPPRESS,
        help="覆盖默认云算力后端（autodl / colab / colab-drive）",
    )

    ap = argparse.ArgumentParser(
        description="端云协同管理系统（E9：python -m pipeline.cloud）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--provider",
        choices=list(VALID_PROVIDERS),
        default=None,
        help="覆盖默认云算力后端（autodl / colab / colab-drive）",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # use
    p_use = sub.add_parser("use", parents=[provider_parent], help="切换默认云算力后端（autodl / colab / colab-drive，互不覆盖配置）")
    p_use.add_argument("provider_name", choices=list(VALID_PROVIDERS), help="目标云算力后端")

    # exec
    p_exec = sub.add_parser("exec", parents=[provider_parent], help="在远端执行命令（默认强制 tmux 包裹后台化）")
    p_exec.add_argument("command", type=str, help="要执行的远端命令")
    p_exec.add_argument("--fg", action="store_true", help="前台直连执行（限 <60s 探针短命令）")

    # logs
    p_logs = sub.add_parser("logs", parents=[provider_parent], help="查看或追踪远端/Drive 队列任务日志")
    p_logs.add_argument("session", type=str, help="tmux 会话名或 Drive job_id（如 ava-20260910_221500）")
    p_logs.add_argument("-f", "--follow", action="store_true", help="实时追踪日志输出")
    p_logs.add_argument("-n", "--lines", type=int, default=30, help="显示末尾行数（默认 30 行）")

    # doctor
    sub.add_parser("doctor", parents=[provider_parent], help="全面诊断远端环境、显卡、存储与模型资产")

    # connect（D102 + Colab）
    p_connect = sub.add_parser("connect", parents=[provider_parent], help="换实例或接入 Colab：写连接配置并生成 ssh_config / 绑定 Drive 队列")
    p_connect.add_argument("login", nargs="?", default=None, type=str, help='SSH/cloudflared 登录指令（如 "ssh -p ... root@..." 或 trycloudflare 域名）')
    p_connect.add_argument("--drive-root", type=str, default=None, help="colab-drive 本机 Google Drive 队列目录（含 ava_cloud）")
    p_connect.add_argument("--browser-login", action="store_true", help="打开隔离浏览器完成一次性 Google/Colab 登录，供后续 cloud up 自动无头开机")
    p_connect.add_argument("--notebook-url", type=str, default=None, help="覆盖保存的 Colab Bootstrap Notebook URL（如 Drive 里的副本链接）")
    p_connect.add_argument("--timeout", type=int, default=300, help="browser-login 等待登录完成超时秒数（默认 300s）")

    # up
    p_up = sub.add_parser("up", parents=[provider_parent], help="开启实例 / 自动拉起 Colab 运行时并激活守护脚本")
    p_up.add_argument("--mode", choices=["cardless", "gpu"], default=None,
                      help="声明开机模式 (cardless=无卡节能模式, gpu=带卡模式)；不填按 nvidia-smi 探测记")
    p_up.add_argument("--rate", type=float, default=None,
                      help="本次单价（AutoDL 元/小时；Colab CU/小时）")
    p_up.add_argument("--timeout", type=int, default=300, help="开机等待超时秒数（默认 300s）")
    p_up.add_argument("--episode", type=str, default=None, help="关联合同期号")
    p_up.add_argument("--no-auto-boot", action="store_true", help="Colab 模式下禁用自动无头拉起，仅等待手动开机")
    p_up.add_argument("--notebook-url", type=str, default=None, help="临时覆盖本次自动拉起使用的 Colab Notebook URL")

    # down
    p_down = sub.add_parser("down", parents=[provider_parent], help="关闭实例或释放 Colab 运行时并完成账簿结算")
    p_down.add_argument("--force", action="store_true", help="强制停机/释放（即使远端仍有活跃任务）")
    p_down.add_argument("--rate", type=float, default=None,
                        help="补报本次单价（AutoDL 元/小时；Colab CU/小时）；优先于 up 时报的")

    # status
    sub.add_parser("status", parents=[provider_parent], help="查询实例连通性、GPU工况与双单位账簿明细")

    # D130：配完不关机
    sub.add_parser("hold", parents=[provider_parent], help="保持开机（看门狗暂停空闲关机/释放，2 小时后自动恢复；再次 hold 即续期）")
    sub.add_parser("release", parents=[provider_parent], help="撤掉 hold，看门狗恢复空闲关机/释放")

    # push
    p_push = sub.add_parser("push", parents=[provider_parent], help="同步本地稿件与配置至云端")
    p_push.add_argument("target", type=Path, help="本期目录路径")
    p_push.add_argument("--with-audio", action="store_true",
                        help="连同本地 03-audio/（已配好的 wav 与 manifest，不含 attic）上行；"
                             "换了实例要 --apply-patch 点名重配前用（D143）")

    # run
    p_run = sub.add_parser("run", parents=[provider_parent], help="在云端执行指定任务（白名单控制：tts, captions, probe）")
    p_run.add_argument("target", type=Path, help="本期目录路径")
    p_run.add_argument("task", type=str, help="任务名称（当前放行：tts, captions, probe）")
    p_run.add_argument("extra", nargs=argparse.REMAINDER, help="透传给远端任务的额外参数（如 --redo 3,7）")

    # pull
    p_pull = sub.add_parser("pull", parents=[provider_parent], help="拉取云端产物回本地")
    p_pull.add_argument("target", type=Path, help="本期目录路径")

    # D59：远端一次性维护
    sub.add_parser("relocate-data", parents=[provider_parent], help="远端 data/ 改道数据盘并软链回来（幂等；有任务在跑就拒）")
    sub.add_parser("clean-frames", parents=[provider_parent], help="清远端上一轮 VLM 打标帧 *_cap（本地可秒级重抽）")
    sub.add_parser("fix-env", parents=[provider_parent], help="按 doctor 判据修远端：克隆缺失的仓库、重下缺失/残缺的模型（清单来自 config）")
    sub.add_parser("sync-code", parents=[provider_parent], help="把本地 pipeline/ config/ pyproject.toml 推到远端并按哈希核对（D107）")

    # D103 ②：番池素材（data/library/ 下的番剧集与 SP）云端打标
    for name, help_ in (("lib-push", "番池素材打标输入（镜头表 + 打标帧）上传"),
                        ("lib-captions", "番池素材云端打标（计费；先核远端代码）"),
                        ("lib-pull", "番池素材打标产物拉回（只拉写明的集键）")):
        p_lib = sub.add_parser(name, parents=[provider_parent], help=help_)
        p_lib.add_argument("pool", help="池名（番名 / 企划名）")
        p_lib.add_argument("keys", nargs="+", help="集键 SP01 / S01E01，可多个或逗号分隔")
        if name == "lib-captions":
            p_lib.add_argument("--batch", type=int, default=None, help="一次前向几个镜头（同 vindex captions）")
            p_lib.add_argument("--limit", type=int, default=None, help="冒烟用：只打前 N 个镜头")
            p_lib.add_argument("--confirm-cost", action="store_true", help="预估超预算仍继续（同 vindex captions）")

    # attach
    p_attach = sub.add_parser("attach", parents=[provider_parent], help="终端接驳远端 tmux 会话")
    p_attach.add_argument("--session", type=str, default="ava-tts", help="tmux 会话名")
    return ap


def main() -> int:
    args = build_parser().parse_args()

    dispatch = {
        "use": cmd_use,
        "exec": cmd_exec,
        "logs": cmd_logs,
        "doctor": cmd_doctor,
        "connect": cmd_connect,
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "hold": cmd_hold,
        "release": cmd_release,
        "push": cmd_push,
        "run": cmd_run,
        "pull": cmd_pull,
        "attach": cmd_attach,
        "relocate-data": cmd_relocate_data,
        "clean-frames": cmd_clean_frames,
        "fix-env": cmd_fix_env,
        "sync-code": cmd_sync_code,
        "lib-push": cmd_lib_push,
        "lib-captions": cmd_lib_captions,
        "lib-pull": cmd_lib_pull,
    }

    fn = dispatch.get(args.cmd)
    if fn:
        return fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
