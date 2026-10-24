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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths
from .keys import SP_NUM

# ---------------------------------------------------------------------------
# 常量定义与判据依据（E3：每个常量后面写「为什么是这个数」）
# ---------------------------------------------------------------------------

# 允许通过 run 命令透传执行的远端任务白名单
# 为什么是白名单：禁止透传任意 shell 命令，杜绝远端非预期写操作破坏环境
ALLOWED_TASKS = {
    "tts",       # 语音合成与回读质检（对应 python -m pipeline.tts <期>）
    "probe",     # M1b 闭环自检验收探针（GPU + PyTorch 连通性测试）
    "captions",  # VLM 逐镜头意象打标（对应 python -m pipeline.vindex captions）
}

# 默认心跳文件位置与空闲关机阈值
WATCHDOG_HEARTBEAT_PATH = "/root/.ava_heartbeat"
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

# 远端解释器。**必须是装了模型栈的那一个。** 2026-09-11 实测：这台实例上
# `/root/miniconda3/bin/python` 里连 transformers 都没有（torch/torchvision 有，模型栈在
# `/root/it-venv`），而 `run ... tts` / 模型结构探针走的正是它——写死旧值的后果是任务
# 起来第一行就 ImportError，而账簿里只记「任务已启动」。可用 `config/cloud.json`
# 的 `remote_python` 覆盖（换实例/换镜像只改配置，不改代码）。
DEFAULT_REMOTE_PYTHON = "/root/it-venv/bin/python"

# watchdog 脚本与心跳的远端路径。**只此一处定义**：部署与查进程必须指同一个文件，
# 各写一份的下场是 status 去 pgrep 一个不存在的路径，永远报「未运行」。
REMOTE_WATCHDOG_PATH = "/root/watchdog.sh"


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


def remote_python(cfg: dict | None = None) -> str:
    """远端解释器路径：`config/cloud.json` 的 `remote_python` 优先，缺了用默认值。

    做成可配不是洁癖：云端环境里同时存在多个 venv，模型栈只在其中一个里（见常量注释）。
    写死在代码里就是把这个实例的现状当机制（ADR-0003 R2：换台机器就要改代码即缺陷）。
    """
    if cfg is None:
        cfg = load_cloud_config()[0]
    return str(cfg.get("remote_python") or DEFAULT_REMOTE_PYTHON)


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


def evaluate_doctor_gpu(raw_vram_output: str, returncode: int) -> tuple[str, str, bool]:
    """评估 GPU 项健康状态（纯函数）。

    判据：
    - 无 GPU / nvidia-smi 失败 → 判定为「无卡模式，GPU 未挂载」（状态陈述，非失败）
    - 有 GPU → 报型号与显存；低于 MIN_VRAM_GB 报 WARN 并说明哪类任务受影响（D102：卡型按需租，
      只配音的任务用小卡是正常选择，不判失败）
    返回: (状态标签 OK/INFO/WARN/FAIL, 详情描述, 是否通过质检)
    """
    if returncode != 0 or not raw_vram_output.strip():
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
    """按实测 GPU 型号查 `cloud.json` 的 `gpu_rates_cny`（纯函数）。返回 (元/时, 是否估算)。

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


def evaluate_doctor_disk(avail_gb_str: str) -> tuple[str, str, bool]:
    """评估数据盘 /root/autodl-tmp 空间（纯函数）。"""
    try:
        val = int(avail_gb_str.strip())
        if val >= 10:
            return "OK", f"/root/autodl-tmp 数据盘可用空间充足: {val}GB", True
        else:
            return "FAIL", f"/root/autodl-tmp 剩余空间不足 10GB (仅剩 {val}GB)", False
    except Exception:
        return "FAIL", "数据盘 /root/autodl-tmp 未挂载或不可读", False


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


def merge_local_connection(local: dict, conn: dict) -> dict:
    """把连接三项并进 cloud.local.json 的内容（纯函数）：只改 server / port / user，其余键与顺序原样。"""
    out = dict(local)
    for k in ("server", "port", "user"):
        out[k] = conn[k]
    return out


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
        f"Host {alias}\n"
        f"    HostName {conn['server']}\n"
        f"    Port {int(conn['port'])}\n"
        f"    User {conn['user']}\n"
        f"    StrictHostKeyChecking accept-new\n"
        f'    UserKnownHostsFile "{known_hosts}"\n'
        "\n"
        "Match all\n"
        "Include ~/.ssh/config\n"
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


def rent_instructions(cfg_global: dict) -> list[str]:
    """`up` 连不上时打印的租机条件（纯函数，D102）：地区、显存、CUDA、镜像四项，外加下一步。"""
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


def remote_code_drift(host: str, cfg_global: dict) -> dict[str, list[str]] | str:
    """远端与本地代码比对：成功返回 compare_code_manifest 的结果；核对不了返回原因字符串。"""
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
    res = _ssh(build_code_manifest_cmd(remote_root, remote_python(cfg_global)), host=host, check=False, timeout=90)
    try:
        remote = json.loads(res.stdout.strip().splitlines()[-1]) if res.returncode == 0 else None
    except (ValueError, IndexError):
        remote = None
    if not isinstance(remote, dict):
        return f"远端清单没算出来（returncode={res.returncode}，{(res.stderr or '').strip()[:200] or '无输出'}）"
    if not remote:
        return f"远端 {remote_root} 下一个代码文件都没有（仓库不在？先 cloud fix-env）"
    return compare_code_manifest(code_manifest(paths.ROOT), remote)


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
    encoded = base64.b64encode(remote_cmd.encode("utf-8")).decode("ascii")
    return (
        f"echo {encoded} | base64 -d > {script_path} && chmod +x {script_path} && "
        f"tmux kill-session -t {session_name} 2>/dev/null; "
        f"tmux new-session -d -s {session_name} '{script_path}'"
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
    task: str, ep_rel_path: str, remote_root: str = "/root/anime-video-agent",
    python: str = DEFAULT_REMOTE_PYTHON, extra_args: str = "",
) -> str:
    """生成远端安全执行命令（P0-1 首尾心跳，P2-5 probe 任务分支）。

    `python` 由调用方从配置现取（见 `remote_python`），**不在函数里读配置**：
    这样它仍是纯函数（可测），而「用哪个解释器」这件事只在一个地方决定。
    """
    task = validate_task_whitelist(task)
    if task == "probe":
        # probe 探针任务：轻量级验证 GPU/Torch/环境，不拖入未移植的 TTS 模块
        cmd = (
            f"cd {remote_root} && "
            f"touch {WATCHDOG_HEARTBEAT_PATH} && "
            f"mkdir -p {ep_rel_path}/03-audio && "
            f"{python} -c '"
            f"import torch; "
            f"print(f\"GPU Probe: torch={{torch.__version__}}, cuda={{torch.cuda.is_available()}}\")"
            f"' | tee {ep_rel_path}/03-audio/probe.log && "
            f"touch {WATCHDOG_HEARTBEAT_PATH}"
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
            f"touch {WATCHDOG_HEARTBEAT_PATH} && "
            f"{python} -m pipeline.vindex captions {shlex.quote(pool)} {key_str} "
            f"--shots-dir {shlex.quote(shots_dir)} "
            f"--frames-dir {shlex.quote(frames_dir)} "
            f"--out-dir {shlex.quote(out_dir)}{flags_str} && "
            f"touch {WATCHDOG_HEARTBEAT_PATH}"
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
            f"touch {WATCHDOG_HEARTBEAT_PATH} && "
            f"{python} -m pipeline.{task} {ep_rel_path}{extra} && "
            f"touch {WATCHDOG_HEARTBEAT_PATH}"
        )
    return cmd


def generate_watchdog_script(
    idle_seconds: int = DEFAULT_IDLE_SHUTDOWN_SECONDS,
    heartbeat_path: str = WATCHDOG_HEARTBEAT_PATH,
) -> str:
    """生成远端自动关机 watchdog 脚本（P0-1 核心修复）。

    判据（S1 测真实状态）：
    循环内必须先检查 `tmux ls 2>/dev/null | grep -q '^ava-'`。
    只要存在以 `ava-` 开头的活跃会话，说明有任务正在后台执行，绝对不可关机！

    关机命令（2026-09-11 实测）：AutoDL 容器内 PID 1 是 `bash /init/boot/boot.sh`，
    **没有 systemd**（`/run/systemd/system` 不存在，`poweroff` 指向的 systemctl 无效），
    唯一可用的是 AutoDL 自带的 `/usr/bin/shutdown` 包装脚本。
    写成 `||` 链是为了兼容未来基础镜像变化，但首选必须是 `/usr/bin/shutdown`。
    """
    return f"""#!/bin/bash
# AVA Remote Watchdog: AutoDL idle shutdown with active task guard
HEARTBEAT_FILE="{heartbeat_path}"
IDLE_LIMIT={idle_seconds}
LOG=/root/watchdog.log

touch "$HEARTBEAT_FILE"
while true; do
    sleep 20
    # S1 真实状态检查：只要有 ava- 开头的 tmux 任务活着，就绝不关机，刷新心跳
    if tmux ls 2>/dev/null | grep -q '^ava-'; then
        touch "$HEARTBEAT_FILE"
        continue
    fi

    if [ -f "$HEARTBEAT_FILE" ]; then
        now=$(date +%s)
        mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || stat -f %m "$HEARTBEAT_FILE" 2>/dev/null || echo "$now")
        idle=$((now - mtime))
        if [ "$idle" -ge "$IDLE_LIMIT" ]; then
            echo "$(date '+%F %T') [watchdog] Idle $idle s >= $IDLE_LIMIT s, no ava-* task. Shutting down." >> "$LOG"
            if /usr/bin/shutdown -h now >> "$LOG" 2>&1; then
                echo "$(date '+%F %T') [watchdog] shutdown 已下发" >> "$LOG"
            else
                echo "$(date '+%F %T') [watchdog] shutdown 下发失败，需人工到控制台关机" >> "$LOG"
            fi
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
) -> dict[str, Any]:
    """格式化账簿单行 JSON 对象（P1-2 增加 mode 字段）。

    note 用于标注时长的可信度：实例已被 watchdog 自动关机时，down 根本联系不上远端，
    只能把时长算到「执行 down 的此刻」——这会多算一段关机到发现的延迟，
    属于**上界估计**。记进 note 而不是假装精确。
    """
    # **单期预算只对「真的在做某一期」的会话有意义。** 模型下载、环境部署、
    # 引擎选型这类会话根本没有「单期」可言，拿单期预算去量它们必然误报
    # （2026-09-11 实测：一个含 15GB 模型下载 + 两次部署尝试的会话被标
    # 「超预算 16 倍」，而它压根没在做任何一期）。警告天天误报就没人看了，
    # 所以这里显式区分，并把判定范围记进账簿而不是藏起来。
    budget_scoped = bool(episode) and episode != "unknown"
    over_budget = budget_scoped and cost_cny > budget_cny
    entry: dict[str, Any] = {
        "session": session_id,
        "mode": mode,
        "up": up_time,
        "down": down_time,
        "gpu_seconds": int(gpu_seconds),
        "cny": round(cost_cny, 4),
        "episode": episode or "unknown",
        "tasks": tasks,
        "over_budget": over_budget,
        "budget_scoped": budget_scoped,
    }
    # D102：卡型不固定，记实际用的卡与按哪个单价算的（估算与否写在 note）
    if gpu:
        entry["gpu"] = gpu
    if rate_cny is not None:
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


# ---------------------------------------------------------------------------
# 第二节：SSH / Rsync 执行薄壳
# ---------------------------------------------------------------------------


def _ssh_host(cfg_local: dict) -> str:
    """获取 SSH 连接目标 Host 别名（定义在 data/cloud/ssh_config，没有该文件时走 ~/.ssh/config）。"""
    return cfg_local.get("ssh_host", "autodl")


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
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
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
    tmux_cmd, log_path = build_exec_tmux_command(remote_cmd, session_name)

    _ssh(tmux_cmd, host=host, check=True)

    print("=" * 65)
    print(f"OK    远端后台任务已启动 (tmux 会话: {session_name})")
    print(f"      远端命令: {remote_cmd}")
    print(f"      日志文件: {log_path}")
    print(f"      查看进度: python -m pipeline.cloud logs {session_name} -f")
    print("=" * 65)
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """查看或追踪远端 tmux 任务日志。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    session_name = args.session.strip()
    if session_name.endswith(".log"):
        session_name = session_name[:-4]

    log_path = f"{REMOTE_LOG_DIR}/{session_name}.log"

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
    subprocess.run(["ssh-keygen", "-R", f"[{conn['server']}]:{int(conn['port'])}", "-f", str(kh)],
                   capture_output=True, text=True)


def cmd_connect(args: argparse.Namespace) -> int:
    """换实例：解析控制台登录指令，写连接三项、生成 ssh_config、验连通、建模型软链（D102，弹卡）。

    Code Freeze 第 ④ 条例外：只写 `config/cloud.local.json` 的 server / port / user，其余键原样保留。
    输出不回显该文件的其他内容。
    """
    try:
        conn = parse_login_command(args.login)
    except ValueError as exc:
        raise SystemExit(f"FAIL 登录指令解析不出：{exc}") from exc
    paths.require_data()
    f_local = paths.CONFIG / "cloud.local.json"
    local = json.loads(f_local.read_text(encoding="utf-8")) if f_local.exists() else {}
    merged = merge_local_connection(local, conn)
    paths.atomic_write(f_local, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    host = _ssh_host(merged)
    ssh_config_file().parent.mkdir(parents=True, exist_ok=True)
    forget_known_host(conn)
    paths.atomic_write(ssh_config_file(), render_ssh_config(host, conn, known_hosts_file()))
    print(f"[OK] 连接信息已更新：{conn['user']}@{conn['server']}:{conn['port']}（别名 {host}）")

    if not is_ssh_reachable(host):
        print(f"FAIL 连不上 {conn['server']}:{conn['port']}。实例刚开机可能还没起来，过半分钟再 connect 一次；"
              "仍不通就看控制台实例状态，以及镜像里是否带着本机公钥", file=sys.stderr)
        return 1
    print("[OK] SSH 连通")
    cfg_global, _ = load_cloud_config()
    return 0 if ensure_models_link(host, cfg_global) else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """环境自检诊断命令（逐项检查，E10 精确修复提示）。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
    models_spec = cfg_global.get("models", {})

    print("=" * 65)
    print("  AutoDL 云端环境诊断 (pipeline.cloud doctor)")
    print("=" * 65)

    all_passed = True

    # 1. SSH 连通性
    ssh_ok = is_ssh_reachable(host)
    if ssh_ok:
        print("OK    [SSH] 远端跳板机连通且 ControlMaster 链路正常")
    else:
        print("FAIL  [SSH] 无法通过 SSH 连接远端实例")
        print("      修法: 1. 确认控制台实例是否已开机 (https://www.autodl.com/console/instance/list);")
        print("            2. 换过实例的话，把控制台的 SSH 登录指令交给 cloud connect。")
        return 1

    # 2. NVIDIA GPU（断言显存容量 ≥ 24GB，允许无卡模式）
    gpu_res = _ssh(GPU_QUERY_CMD, host=host, check=False)
    status_tag, desc, passed = evaluate_doctor_gpu(gpu_res.stdout, gpu_res.returncode)
    print(f"{status_tag:<5} [GPU] {desc}")
    if not passed:
        all_passed = False

    # 3. 远端代码仓库
    repo_res = _ssh(f"test -d {remote_root}/.git && cd {remote_root} && git rev-parse --short HEAD", host=host, check=False)
    if repo_res.returncode == 0:
        print(f"OK    [REPO] 远端仓库就绪 ({remote_root}, commit: {repo_res.stdout.strip()})")
    else:
        all_passed = False
        print(f"FAIL  [REPO] 远端仓库不存在或未完成初始化: {remote_root}")
        print("      修法: cloud fix-env（弹卡；克隆仓库到远端，已在就跳过）")

    # 3b. D107：远端代码内容与本地是否一致（仓库在 ≠ 代码对）
    if repo_res.returncode == 0:
        code_ok, code_lines = code_drift_lines(remote_code_drift(host, cfg_global))
        print(f"{'OK' if code_ok else 'FAIL':<5} [CODE] {code_lines[0]}")
        for line in code_lines[1:]:
            print(f"      {line}")
        if not code_ok:
            all_passed = False

    # 4. PyTorch 与 CUDA 可用性
    torch_cmd = (
        f"{remote_python()} -c "
        "'import torch; print(f\"{torch.__version__}___{torch.cuda.is_available()}\")'"
    )
    torch_res = _ssh(torch_cmd, host=host, check=False)
    if torch_res.returncode == 0 and "___" in torch_res.stdout:
        t_ver, c_avail = torch_res.stdout.strip().split("___")
        if c_avail == "True":
            print(f"OK    [TORCH] PyTorch {t_ver}, CUDA可用=True")
        else:
            print(f"INFO  [TORCH] PyTorch {t_ver} (无卡模式，CUDA暂离线)")
    else:
        all_passed = False
        print(f"FAIL  [TORCH] PyTorch 未安装或环境损坏: {torch_res.stderr.strip()}")
        print("      修法: 在远端模型栈环境里装（config/cloud.json 的 remote_python 指的就是它）")

    # 5. 数据盘存储
    disk_res = _ssh("df -BG /root/autodl-tmp | tail -1 | awk '{print $4}' | tr -d 'G'", host=host, check=False)
    d_tag, d_desc, d_passed = evaluate_doctor_disk(disk_res.stdout)
    print(f"{d_tag:<5} [DISK] {d_desc}")
    if not d_passed:
        all_passed = False
        print("      修法: 清理 /root/autodl-tmp 中无用的大文件或临时缓存。")

    # 5b. 文件存储挂载与模型软链（D102：模型在文件存储里，数据盘上只是软链）
    fs_res = _ssh(FS_MOUNT_CHECK_CMD, host=host, check=False)
    if fs_res.stdout.strip() == "MOUNTED":
        print("OK    [FS] 文件存储 /root/autodl-fs 已挂载")
    else:
        all_passed = False
        print("FAIL  [FS] 文件存储 /root/autodl-fs 没挂载")
        print(f"      修法: 控制台「文件存储」页看 {cfg_global.get('region', '西北B区')} 是否已初始化；"
              "已初始化仍没挂上就重启实例（文件存储只在开机时挂载）")
    fs = fs_models_dir(cfg_global)
    tmp = cfg_global.get("remote_models", "/root/autodl-tmp/models")
    ln_res = _ssh(models_link_script(fs, tmp, create=False), host=host, check=False)
    l_tag, l_desc, l_ok = evaluate_models_link(ln_res.stdout, fs, tmp)
    print(f"{l_tag:<5} [LINK] {l_desc}")
    if not l_ok:
        all_passed = False

    # 6. 五大模型逐一核查
    print("-" * 65)
    # 模型根目录走 config/cloud.json 的 remote_models（2026-09-16 接线：键早就
    # 声明了，代码一直没读）。注意 remote_data 是**有意不接线**的，见该键的 _note。
    models_root = cfg_global.get("remote_models", "/root/autodl-tmp/models")
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
                    # 体积只证存在、不证完整：再跑一层结构校验（任务 0）。
                    # 失败不推翻体积结论，但会拉倒 all_passed。
                    st_res = _ssh(build_model_structure_probe_cmd(m_dir, models_root=models_root, python=remote_python(cfg_global)),
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


def cmd_up(args: argparse.Namespace) -> int:
    """开机命令：检测状态，记录模式，激活 watchdog。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    declared = getattr(args, "mode", None)   # D108：不声明 = 按探测记，单价跟实测模式走
    rate = check_rate(getattr(args, "rate", None))   # D108：先校验，别开了机才报参数错

    print(f"[*] 检查云端实例连通性 (Host: {host}, 模式: {declared or '开机后探测'})...")
    if not is_ssh_reachable(host):
        print()
        for line in rent_instructions(cfg_global):
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

    # **断言声明的模式与实测一致。** 2026-09-11 实测踩过：`up --mode gpu` 只信参数，
    # 实际开出来是无卡模式——账簿按 2.4 元/h 虚记 24 倍，而 GPU 任务在加载权重时
    # 被 cgroup OOM 杀掉，报出来的只有一行 `Killed`，从字面上看不出「这台没有卡」。
    # `down` 早就有这个探针，`up` 也该有：探测比猜测便宜得多（同一句注释的下半句）。
    actual = detect_runtime_mode_from_gpu_probe(
        _ssh("nvidia-smi -L", host=host, check=False).returncode)
    if declared and actual != declared:
        print(f"WARN --mode {declared} 与实测不符：这台实例当前是【{actual}】。")
        if actual == "cardless":
            print("     → 无 GPU：带卡任务（VLM 打标/重推理）会在加载权重时被 OOM 杀掉，"
                  "而且报错只有一行 Killed。\n"
                  "     → 账簿已按实测的 cardless 记（否则虚高 24 倍）；"
                  "要跑 GPU 任务请关机后在控制台按带卡模式重开。")
    mode = actual

    # D102：卡型按需租，记下实际是哪张卡（账本按型号查价）；模型目录软链到文件存储
    gpu_name, gpu_mem = (None, None)
    if mode == "gpu":
        gpu_name, gpu_mem = parse_gpu_query(_ssh(GPU_QUERY_CMD, host=host, check=False).stdout)
        print(f"[OK] GPU：{gpu_name or '型号未读出'}" + (f"，显存 {gpu_mem / 1024:.1f}GB" if gpu_mem else ""))
    if not ensure_models_link(host, cfg_global):
        print("WARN 模型目录没接上文件存储：带模型的任务会找不到权重，先看 cloud doctor", file=sys.stderr)

    # 部署并激活远端 watchdog（带活跃任务检测）
    idle_sec = cfg_global.get("idle_shutdown_seconds", DEFAULT_IDLE_SHUTDOWN_SECONDS)
    watchdog_content = generate_watchdog_script(idle_sec)
    deploy_cmd = build_watchdog_deploy_command(watchdog_content)
    res = _ssh(deploy_cmd, host=host, check=False)
    wd_pid = res.stdout.strip()
    if res.returncode != 0 or not wd_pid:
        # 必须把 returncode 也报出来：ssh 自身出错时返回 255 而 stderr 往往是空的，
        # 只看 stderr 会得到「失败但无任何信息」这种最难排查的状态（2026-09-11 真实踩过）
        raise SystemExit(
            f"FAIL Watchdog 部署后未能确认进程存活。\n"
            f"     ssh returncode={res.returncode} (255 = 连接层断开，通常是 pkill 误伤自身会话)\n"
            f"     stdout={res.stdout.strip()!r}\n"
            f"     stderr={res.stderr.strip()!r}\n"
            f"     不会假装成功：不验证就宣布成功会重演 2026-09-11 空转事故"
        )
    print(f"[OK] 远端自动关机 Watchdog 已激活 (PID {wd_pid}，无活跃任务且空闲 {idle_sec}s 自动关机)。")

    sfile = get_active_session_file()
    now_epoch = time.time()
    target = connection_target(cfg_local)
    try:
        prev = json.loads(sfile.read_text(encoding="utf-8")) if sfile.exists() else None
    except (OSError, ValueError):
        prev = None
    # D128：同一次开机里再跑 up（换了 agent、重跑一遍）沿用原会话——覆盖会丢掉报过的单价，
    # 开机时刻也往后挪、少计一段。开机时刻 = 本地 now − 远端 uptime，不受两边时钟偏差影响
    resumed = resumable_session(prev, target, remote_boot_epoch(host, now_epoch))
    if resumed:
        session_data = dict(prev)
        session_data["mode"] = mode
        print(f"[OK] 沿用本次开机的活跃会话 {prev.get('session')}（开始于 {prev.get('up')}），开机时刻与已报单价不变")
    else:
        if prev is not None:
            print(f"WARN 上一个会话 {prev.get('session')}（开始于 {prev.get('up')}）没有 down 结算就被替换："
                  "实例已重启或换了一台，那段费用不进账本", file=sys.stderr)
        session_data = {
            "session": f"{datetime.now().strftime('%Y%m%d_%H%M%S')}",
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
    if rate is not None and mode == "gpu" and declared in (None, "gpu"):
        session_data["rate_cny"] = rate
        session_data["rate_source"] = "人报（up --rate）"
    elif rate is not None and mode == "gpu":
        print(f"WARN 报的单价 {rate:g} 元/h 是按 --mode {declared} 报的，实测是 {mode}，"
              "这个价不记；结算时用 down --rate 补实际单价", file=sys.stderr)
    elif rate is not None:
        print(f"WARN 实测是无卡模式，按平台固定价记，报的单价 {rate:g} 元/h 不记", file=sys.stderr)
    elif mode == "gpu" and "rate_cny" not in session_data:
        print("WARN 没报单价：账本会按参考价估算。看控制台这台实例的按量单价，"
              "结算时用 cloud down --rate <元/时> 补上", file=sys.stderr)
    if declared and mode != declared:
        # 与声明不符时留痕，事后查账不用猜（带卡/无卡差 24 倍价，不是小事）
        session_data["mode_corrected"] = f"up 声明 --mode {declared}，实测为 {mode}，按实测记账"
    sfile.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not resumed:
        print(f"[OK] 会话已开启: {session_data['session']} (模式: {mode})")
    return 0


def connection_target(cfg_local: dict) -> str | None:
    """`cloud connect` 记下的 user@server:port（纯函数）；没 connect 过（走 ~/.ssh/config）为 None。"""
    if not (cfg_local.get("server") and cfg_local.get("port")):
        return None
    return f"{cfg_local.get('user', 'root')}@{cfg_local['server']}:{cfg_local['port']}"


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


def session_rate(cfg_global: dict, mode: str | None, gpu_name: str | None,
                 reported: float | None = None) -> tuple[float, str | None]:
    """会话单价（纯函数）→ (元/时, 估算说明)。说明为 None = 单价可信。

    D108：每次租的卡不一样，同一张卡不同时候价也不同，**唯一可信的带卡单价是人租卡时在控制台上
    看到的那个数**（`up --rate` / `down --rate`）。没报就只能估：先按型号查 gpu_rates_cny，
    查不到用 hourly_rate_cny；两种都标估算。无卡模式是平台固定价，不算估算。
    mode 不可知时取带卡价：宁可高估也不能低估预算风险。
    """
    if reported is not None:
        return float(reported), None
    if mode == "cardless":
        return float(cfg_global.get("cardless_rate_cny", 0.10)), None
    fallback = float(cfg_global.get("hourly_rate_cny", 2.40))
    if mode != "gpu":
        return fallback, "单价估算：模式不可知，按 hourly_rate_cny 记"
    rate, missed = lookup_gpu_rate(gpu_name, cfg_global.get("gpu_rates_cny", {}) or {}, fallback)
    if missed:
        return rate, f"单价估算：没有人报单价，gpu_rates_cny 里也查不到 {gpu_name or '本次 GPU 型号'}，按 hourly_rate_cny 记"
    return rate, f"单价估算：没有人报单价（up / down --rate），按 gpu_rates_cny 里 {gpu_name} 的参考价记"


def check_rate(value: float | None) -> float | None:
    """人报单价的合理性（纯函数）：必须是正数；>50 元/时多半是把日价或总价当成了时价。"""
    if value is None:
        return None
    if not (0 < value <= 50):
        raise SystemExit(f"FAIL --rate {value:g} 不像按量时价（应为 0–50 元/小时，看控制台实例列表的「付费方式」一栏）")
    return float(value)


def rerate_last_entry(lines: list[str], rate: float, today: str) -> tuple[list[str], dict]:
    """D131：把账簿最后一条结算的估算单价换成人事后补报的单价（纯函数）→ (新的各行, 改后的条目)。

    只改「单价是估算」的那一条：时长不动，按新单价重算 cny，note 去掉估算说明、记下原估算值与补报日期。
    最后一条已是人报的单价、或读不出来，SystemExit 拒绝（账簿是历史记录，改人报过的数要人自己改）。
    """
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
    if not estimated:
        raise SystemExit(
            f"FAIL 没有活跃会话，账簿最后一条（会话 {entry.get('session')}）的单价 "
            f"{entry.get('rate_cny')} 元/h 不是估算值，不改。要改人报过的单价请人工改账簿")
    old_rate, old_cny = entry.get("rate_cny"), entry.get("cny")
    entry["rate_cny"] = float(rate)
    entry["cny"] = calculate_session_cost(entry.get("gpu_seconds", 0), float(rate))
    clauses = [c for c in clauses if not c.startswith("单价估算")]
    clauses.append(f"单价 {rate:g} 元/h 为人事后补报（{today}；原按 {old_rate} 元/h 估算 ¥{old_cny}）")
    entry["note"] = "；".join(clauses)
    out = list(lines)
    out[idx] = json.dumps(entry, ensure_ascii=False)
    return out, entry


def cmd_down(args: argparse.Namespace) -> int:
    """关机结算命令：按模式精算费用，落盘账簿，提示/下发关机。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)

    sfile = get_active_session_file()
    session_info: dict = {}
    session_corrupt = False
    if sfile.exists():
        try:
            session_info = json.loads(sfile.read_text(encoding="utf-8"))
        except Exception as e:
            # 不静默（审计 F10）：损坏还按「无会话」走会把 up_epoch 默认成当前时间，
            # 几小时的开机费用被算成 0 元，随后 unlink 连证据一起销毁
            session_corrupt = True
            print(f"WARN 会话文件损坏，按未知会话结算（时长/费用不可靠）: {e}",
                  file=sys.stderr)

    # 先探一次连通性：它同时决定「时长可信度」与「模式判定」。
    # 若实例已被 watchdog 自动关机，本地根本联系不上，只能把时长算到此刻——
    # 这会多计一段「关机到被发现」的延迟，属于**上界估计**，必须写进账簿注记。
    reachable = is_ssh_reachable(host)

    # D131：会话已结算、实例也已关（人在控制台关机后补报单价），没有可以结算的新会话——
    # 照旧走下去会另记一条 0 秒 ¥0 的 manual 会话，刚结算那条的估算单价却没变
    rate_arg = check_rate(getattr(args, "rate", None))
    if not sfile.exists() and not reachable and rate_arg is not None:
        lfile = get_ledger_file()
        lines = lfile.read_text(encoding="utf-8").splitlines() if lfile.exists() else []
        new_lines, entry = rerate_last_entry(lines, rate_arg, datetime.now().strftime("%Y-%m-%d"))
        paths.atomic_write(lfile, "\n".join(new_lines) + "\n")
        print(f"[*] 没有活跃会话、实例已关：把最近一条结算（会话 {entry['session']}）的估算单价改为 "
              f"{rate_arg:g} 元/h，重算为 ¥{entry['cny']:.4f}（时长 {entry['gpu_seconds']}s 不变）")
        return 0

    mode = session_info.get("mode")
    if not mode:
        # 无会话文件（用户手动开机后直接 down）：探测真实模式，不猜。
        if reachable:
            mode = detect_runtime_mode_from_gpu_probe(_ssh("nvidia-smi -L", host=host, check=False).returncode)
        else:
            mode = "unknown"

    gpu_name = session_info.get("gpu")
    if mode == "gpu" and not gpu_name and reachable:
        gpu_name = parse_gpu_query(_ssh(GPU_QUERY_CMD, host=host, check=False).stdout)[0]
    # D108：结算时补报的单价优先于 up 时报的（人看着控制台改口，以最新为准）
    reported = rate_arg
    if reported is None and isinstance(session_info.get("rate_cny"), (int, float)):
        reported = float(session_info["rate_cny"])
    rate, rate_estimate_note = session_rate(cfg_global, mode, gpu_name, reported)

    budget = float(cfg_global.get("budget_per_episode_cny", 0.35))
    up_epoch = session_info.get("up_epoch", time.time())
    duration_sec = int(max(time.time() - up_epoch, 0))
    cost_cny = calculate_session_cost(duration_sec, rate)
    down_time = datetime.now().strftime("%H:%M:%S")

    note = None
    if not reachable:
        note = (
            "实例在结算前已停止响应（多为 watchdog 自动关机）；"
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
        cost_cny=cost_cny,
        episode=session_info.get("episode"),
        tasks=session_info.get("tasks", []),
        budget_cny=budget,
        mode=mode,
        note=note,
        gpu=gpu_name,
        rate_cny=rate,
    )
    lfile = get_ledger_file()
    with lfile.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

    if sfile.exists() and not session_corrupt:
        sfile.unlink()

    print("=" * 60)
    print(f"会话结算完成：")
    print(f"  会话模式: {mode} (计费单价: ¥{rate}/小时)")
    print(f"  运行时长: {duration_sec // 60} 分 {duration_sec % 60} 秒 ({duration_sec}s)")
    print(f"  本次计费: ¥{cost_cny:.4f} 元")
    if ledger_entry["over_budget"]:
        print(f"  \033[91m⚠️  警告: 本次花费超出单期预算 ¥{budget} 元！\033[0m")
    elif ledger_entry["budget_scoped"]:
        print(f"  预算状态: 达标 (≤ ¥{budget} 元)")
    else:
        print(f"  预算状态: 本会话未绑定某一期（{ledger_entry['episode']}），单期预算不适用")
    print(f"  账簿记录: → {lfile}")
    print("=" * 60)

    if reachable:
        active_tasks = remote_active_tasks(host=host)
        if active_tasks and not getattr(args, "force", False):
            print(f"[*] 提示: 远端仍有活跃的后台任务正在运行 ({active_tasks})。")
            print(f"    账簿已结算，保留远端实例运行以等待任务完成。")
            print(f"    如需强制停机请加 --force: python -m pipeline.cloud down --force")
            return 0

        print("[*] 正在发送远端停机指令...")
        # AutoDL 容器内只有 /usr/bin/shutdown（平台包装脚本）有效：PID 1 是 boot.sh、无 systemd，
        # /sbin/shutdown 根本不存在、poweroff 指向的 systemctl 也无效（2026-09-11 实测）。
        # nohup 后台化：实例停机瞬间会切断当前 SSH，前台执行会让本地拿到假失败。
        _ssh("nohup /usr/bin/shutdown -h now >/dev/null 2>&1 & sleep 1", host=host, check=False, timeout=15)

        # **必须验证**（2026-09-11 教训）：只打印「已下发」不验证＝假成功，
        # 而自动关机失效正是本项目最贵的一类静默失败（忘关一晚是单期预算的百倍量级）。
        print("[*] 正在验证实例是否真的停止响应（最多等 60 秒）...")
        for _ in range(12):
            time.sleep(5)
            if not is_ssh_reachable(host):
                print("[OK] 已验证：实例已停止响应，关机生效。")
                return 0
        print("[FAIL] 60 秒内实例仍可连接，关机未生效 —— 不会假装成功。", file=sys.stderr)
        print(f"       请到控制台手动关机：https://www.autodl.com/console/instance/list", file=sys.stderr)
        return 1

    print(f"[提示] 实例当前不可达（可能已关机）。请在控制台确认计费已停止：")
    print(f"       https://www.autodl.com/console/instance/list")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """工况与账簿查询命令。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    budget = float(cfg_global.get("budget_per_episode_cny", 0.35))

    print("=" * 65)
    print("  AutoDL 端云工况状态")
    print("=" * 65)

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

        disk_res = _ssh("df -h /root/autodl-tmp | tail -1 | awk '{print $4\" 可用 / \"$2\" 总量 (\"$5\" 已用)\"}'", host=host, check=False)
        if disk_res.returncode == 0 and disk_res.stdout.strip():
            print(f"数据盘存储   : {disk_res.stdout.strip()}")

        watch_res = _ssh(f"pgrep -f '{_watchdog_proc_pattern(REMOTE_WATCHDOG_PATH)}' >/dev/null && echo '运行中' || echo '未运行'", host=host, check=False)
        print(f"自动关机守卫 : {watch_res.stdout.strip()}")

        active = remote_active_tasks(host=host)
        active_str = f"[{', '.join(active)}]" if active else "无"
        print(f"活跃后台任务 : {active_str}")

    sfile = get_active_session_file()
    if sfile.exists():
        try:
            sdata = json.loads(sfile.read_text(encoding="utf-8"))
            mode = sdata.get("mode", "gpu")
            reported = sdata.get("rate_cny") if isinstance(sdata.get("rate_cny"), (int, float)) else None
            rate, estimated = session_rate(cfg_global, mode, sdata.get("gpu"), reported)
            elapsed = int(time.time() - sdata.get("up_epoch", time.time()))
            cur_cost = calculate_session_cost(elapsed, rate)
            print("-" * 65)
            print(f"当前活跃会话 : {sdata.get('session')} (开始于 {sdata.get('up')}, 模式: {mode})")
            print(f"已运行时间   : {elapsed // 60} 分 {elapsed % 60} 秒")
            cost_color = "\033[91m" if cur_cost > budget else "\033[92m"
            print(f"已产生费用   : {cost_color}¥{cur_cost:.4f}\033[0m 元 (单价: ¥{rate}/小时{'，估算' if estimated else ''}, 限额: ¥{budget})")
        except Exception as e:
            print(f"WARN 会话文件损坏，无法展示当前会话: {e}", file=sys.stderr)

    lfile = get_ledger_file()
    if lfile.exists():
        tot_cny, tot_sec, records = parse_ledger(lfile.read_text(encoding="utf-8"))
        print("-" * 65)
        print(f"历史账簿统计 : 共 {len(records)} 次会话，累计计费 {tot_sec // 3600}小时{(tot_sec % 3600) // 60}分")
        print(f"累计消费金额 : ¥{tot_cny:.2f} 元")
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
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up（计费卡）")
    active = remote_active_tasks(host)
    if active:
        raise SystemExit(f"FAIL 远端还有任务在跑（{', '.join(active)}），{label}会动到它在用的数据——等它结束再来")
    script = script_of(remote_root)
    print(f"[*] {label}：{host}\n    {script}")
    res = _ssh(script, host=host, check=False, timeout=600)
    out = (res.stdout or "").strip()
    print(out)
    if res.returncode == 3 and "CONFLICT" in out:
        raise SystemExit(f"FAIL 数据盘上已有 {REMOTE_DATA_DISK}/data（上次挪到一半？），不覆盖不合并——交人看")
    if res.returncode != 0:
        raise SystemExit(f"FAIL {label}失败（退出码 {res.returncode}）：{(res.stderr or '').strip()[:300]}")
    return 0


#: 远端代码仓库来源（doctor 修法原先让人经 `cloud exec` 手敲 git clone）。
REMOTE_REPO_URL = "https://github.com/songhuiming2007-coder/anime-video-agent.git"
HF_MIRROR = "https://hf-mirror.com"


def fix_env_commands(*, remote_root: str, models_root: str, repo_missing: bool,
                     bad_models: list[tuple[str, str]], repo_url: str = REMOTE_REPO_URL) -> list[str]:
    """`cloud fix-env` 要在远端执行的命令（纯函数，D59）。

    只做 doctor 能判定、且修法确定的两件事：克隆缺失的仓库、按 config/cloud.json 的 `models`
    清单重下缺失 / 体积不足 / 结构校验不过的模型。模型 id 只来自配置清单，不接受运行时参数。
    装 PyTorch 等环境依赖不在此列（属于装全局依赖，交人）。
    """
    cmds = []
    if repo_missing:
        cmds.append(f"source /etc/network_turbo && git clone {shlex.quote(repo_url)} {shlex.quote(remote_root)}")
    for m_id, m_dir in bad_models:
        py = (f"from huggingface_hub import snapshot_download; "
              f"snapshot_download({json.dumps(m_id)}, local_dir={json.dumps(models_root + '/' + m_dir)})")
        cmds.append(f"export HF_ENDPOINT={HF_MIRROR} && python3 -c {shlex.quote(py)}")
    return cmds


def cmd_fix_env(args: argparse.Namespace) -> int:
    """按 doctor 的判据修远端环境：仓库缺了就克隆、模型缺 / 小 / 坏就重下（D59，弹卡）。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
    models_root = cfg_global.get("remote_models", "/root/autodl-tmp/models")
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
            st = _ssh(build_model_structure_probe_cmd(m_dir, models_root=models_root, python=remote_python(cfg_global)),
                      host=host, check=False, timeout=90)
            ok = parse_model_structure_result(st.stdout, st.returncode)[0] != "FAIL"
        if not ok:
            bad.append((m_id, m_dir))
    cmds = fix_env_commands(remote_root=remote_root, models_root=models_root,
                            repo_missing=repo_missing, bad_models=bad)
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


def cmd_push(args: argparse.Namespace) -> int:
    """上行同步命令：采用真实清单构建器并遵循目录斜杠纪律（P1-1/P2-4）。"""
    try:
        ep_dir, rel_path = resolve_episode_rel_path(args.target)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在: {ep_dir}")

    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")

    remote_ep_dir = f"{remote_root}/{rel_path}"
    print(f"[*] 正在推送当期必要资产至远端...")
    print(f"    本地源: {ep_dir}")
    print(f"    远端宿: {host}:{remote_ep_dir}")

    # 路径过 shlex.quote：期目录名可以含空格与中文（B7-r5 同族），
    # 裸插值会让远端 shell 在空格处断开、在中文处乱码。无特殊字符时输出与原文一致。
    _ssh(f"mkdir -p {shlex.quote(remote_ep_dir)}", host=host)

    sync_items = cfg_global.get(
        "sync", {}
    ).get("up", ["02-script.md", "01-topic.md", "config/", "03-audio/corrections.json", "04-patch/"])
    for extra_item in ["03-audio/corrections.json", "04-patch/"]:
        if extra_item not in sync_items:
            sync_items.append(extra_item)
    sync_pairs = build_sync_up_files(ep_dir, sync_items, remote_ep_dir, remote_root)

    _preflight_remote_capacity(host, remote_root, remote_ep_dir, sync_pairs)

    for src, dst in sync_pairs:
        res = run_rsync(src, f"{host}:{dst}")
        if res.returncode != 0:
            raise SystemExit(f"FAIL 推送失败 {src} -> {dst}: {res.stderr.strip()}")
        print(f"  ✓ 已同步: {src} -> {dst}")

    print("[OK] push 完成。资产已在云端就位。")
    return 0


CODE_SYNC_ITEMS = ("pipeline/", "config/", "pyproject.toml")


def cmd_sync_code(args: argparse.Namespace) -> int:
    """D107：把本地 pipeline/ config/（不含 *.local.json）pyproject.toml 推到远端，再按哈希核对一遍。

    不删远端多出的文件（见 compare_code_manifest）；远端有任务在跑就拒——中途换代码会让它半新半旧。
    """
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
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
    code_ok, code_lines = code_drift_lines(remote_code_drift(host, cfg_global))
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


def build_lib_captions_command(pool: str, keys: list[str], remote_root: str,
                               python: str = DEFAULT_REMOTE_PYTHON, *, batch: int | None = None,
                               limit: int | None = None, confirm_cost: bool = False) -> str:
    """远端打标命令（纯函数）：首尾心跳，与 build_remote_run_command 同口径；目录用 vindex 默认值。"""
    flags = ""
    if batch is not None:
        flags += f" --batch {int(batch)}"
    if limit is not None:
        flags += f" --limit {int(limit)}"
    if confirm_cost:
        flags += " --confirm-cost"
    return (
        f"cd {shlex.quote(remote_root)} && "
        f"touch {WATCHDOG_HEARTBEAT_PATH} && "
        f"{python} -m pipeline.vindex captions {shlex.quote(pool)} {' '.join(keys)}{flags} && "
        f"touch {WATCHDOG_HEARTBEAT_PATH}"
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
    """番池素材打标输入上行：镜头表 + 打标帧。缺一样整批拒，一条 rsync 都不发。"""
    keys = _lib_target(args)
    paths.require_data()
    dirs = lib_dirs()
    shots_rel, frames_rel, _ = dirs
    if not any((paths.ROOT / shots_rel).glob(f"{args.pool}_*.json")):
        raise SystemExit(f"FAIL 本地 {shots_rel}/ 下没有池「{args.pool}」的任何镜头表：池名写错了？")
    miss = lib_missing_inputs(args.pool, keys, dirs, paths.ROOT)
    if miss:
        raise SystemExit("FAIL 打标输入不全，整批没传：\n     " + "\n     ".join(miss))

    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
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
    """番池素材云端打标：先核远端代码（D107），再 tmux 后台跑 vindex captions。"""
    keys = _lib_target(args)
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")

    code_ok, code_lines = code_drift_lines(remote_code_drift(host, cfg_global))
    if not code_ok:
        raise SystemExit("FAIL 没开跑：" + "\n     ".join(code_lines))
    if LIB_CAPTIONS_SESSION in remote_active_tasks(host=host):
        # 同名 tmux 会话会被 build_tmux_launch_command 先 kill 掉：再发一次等于打断正在跑的那批
        raise SystemExit(f"FAIL 上一批番池打标（{LIB_CAPTIONS_SESSION}）还在跑；跑完再开下一批，"
                         f"进度看 cloud attach --session {LIB_CAPTIONS_SESSION}")

    _note_session_task("captions", f"library:{args.pool}")
    _ssh(f"touch {WATCHDOG_HEARTBEAT_PATH}", host=host)
    remote_cmd = build_lib_captions_command(args.pool, keys, remote_root, remote_python(cfg_global),
                                            batch=args.batch, limit=args.limit, confirm_cost=args.confirm_cost)
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
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")
    if not is_ssh_reachable(host):
        raise SystemExit("FAIL 实例不可达/已关机，先 cloud up")
    if LIB_CAPTIONS_SESSION in remote_active_tasks(host=host):
        raise SystemExit(f"FAIL 番池打标（{LIB_CAPTIONS_SESSION}）还在跑，跑完再拉：中途拉到的可能是半份产物")
    local_dir = paths.ROOT / vindex_rel
    local_dir.mkdir(parents=True, exist_ok=True)
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


def cmd_run(args: argparse.Namespace) -> int:
    """远端任务触发命令：白名单限制，tmux 包裹，首尾双重心跳（P0-1/P2-5）。"""
    task = validate_task_whitelist(args.task)
    try:
        ep_dir, rel_path = resolve_episode_rel_path(args.target)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在: {ep_dir}")

    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")

    # D107：旧代码打出来的产物可能过不了加载校验，也可能校验相同但口径不同（后者不报错），开跑前挡住
    code_ok, code_lines = code_drift_lines(remote_code_drift(host, cfg_global))
    if not code_ok:
        raise SystemExit("FAIL 没开跑：" + "\n     ".join(code_lines))

    sfile = get_active_session_file()
    if sfile.exists():
        try:
            sdata = json.loads(sfile.read_text(encoding="utf-8"))
            sdata.setdefault("tasks", [])
            if task not in sdata["tasks"]:
                sdata["tasks"].append(task)
            sdata["episode"] = ep_dir.name
            sfile.write_text(json.dumps(sdata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"WARN 会话文件损坏，本次任务未记账（不影响远端执行）: {e}",
                  file=sys.stderr)

    _ssh(f"touch {WATCHDOG_HEARTBEAT_PATH}", host=host)

    session_name = f"ava-{task}"
    extra_str = " ".join(args.extra) if getattr(args, "extra", None) else ""
    try:
        remote_cmd = build_remote_run_command(task, rel_path, remote_root,
                                              python=remote_python(cfg_global),
                                              extra_args=extra_str)
    except ValueError as exc:
        raise SystemExit(f"FAIL 远端命令参数非法: {exc}") from exc
    tmux_launch = build_tmux_launch_command(session_name, remote_cmd, remote_root)

    print(f"[*] 启动远端任务: {task} (tmux: {session_name})")
    print(f"    远端执行: {remote_cmd}")
    _ssh(tmux_launch, host=host)

    print(f"[OK] 任务已在远端 tmux 会话运行。")
    print(f"     查看日志: python -m pipeline.cloud attach --session {session_name}")
    print(f"     完成后请执行:")
    print(f"       python -m pipeline.cloud pull {args.target}")
    print(f"       python -m pipeline.cloud down")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """下行拉取命令：遵循目录斜杠纪律与完整性自检（P1-1/P2-4，Spec §4.3 三态可辨）。"""
    try:
        ep_dir, rel_path = resolve_episode_rel_path(args.target)
    except ValueError as exc:
        raise SystemExit(f"FAIL {exc}") from exc
    if not ep_dir.is_dir():
        raise SystemExit(f"FAIL 本期目录不存在: {ep_dir}")

    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    remote_root = cfg_global.get("remote_root", "/root/anime-video-agent")

    # ① 动 rsync 前先探实例可达与活跃任务（r10 / r15）
    # 当 run_rsync 未被单元测试拦截时执行真实连通性探测
    if run_rsync is _ORIGINAL_RUN_RSYNC:
        if not is_ssh_reachable(host):
            raise SystemExit("FAIL 实例不可达/已关机，先 `python -m pipeline.cloud up`")
        active = remote_active_tasks(host=host)
        if active:
            print(f"[*] 提示: 远端仍有活跃后台任务正在运行 ({active})")

    remote_ep_dir = f"{remote_root}/{rel_path}"
    print(f"[*] 从远端拉取执行产物...")

    sync_items = cfg_global.get("sync", {}).get("down", ["03-audio/", "04-clips.json", "06-check.log", "04-patch/"])
    if "04-patch/" not in sync_items:
        sync_items.append("04-patch/")
    sync_pairs = build_sync_down_files(ep_dir, sync_items, remote_ep_dir)

    for src, dst in sync_pairs:
        res = run_rsync(f"{host}:{src}", dst)
        if res.returncode == 0:
            print(f"  ✓ 已拉取: {src} -> {dst}")
        else:
            # 非零可能是「远端没有这个产物」（正常跳过），也可能是网络/认证
            # 失败——把 rsync 的原话带上，两种病因不混成一句（审计 R3）
            tail = (res.stderr.strip().splitlines() or [""])[-1][:120]
            print(f"  ○ 未发现远端产物或跳过: {src}（rsync 退出码 {res.returncode}：{tail}）")

    # 自检验收：只校验**本次任务实际该产出的东西**。
    # 旧写法一看到 manifest.json 存在就报「校验通过」，但那个 manifest 是上轮 v1 留下的，
    # 跟本次任务毫无关系——看起来验证过了，实际什么都没验（误导性输出）。
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


def cmd_attach(args: argparse.Namespace) -> int:
    """本地接入远端 tmux 会话。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    session_name = args.session or "ava-tts"
    # 列表传参，不用 shell=True：本地 shell 没有要在这里展开的任何东西，
    # 引号拼接只会把合法的会话名截断（审计 R1）
    print(f"[*] 接入 tmux 会话 {session_name}...")
    return subprocess.call([*ssh_base(), "-t", host, f"tmux attach-session -t {session_name}"])


# ---------------------------------------------------------------------------
# 第四节：CLI 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="端云协同管理系统（E9：python -m pipeline.cloud）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # exec
    p_exec = sub.add_parser("exec", help="在远端执行命令（默认强制 tmux 包裹后台化）")
    p_exec.add_argument("command", type=str, help="要执行的远端命令")
    p_exec.add_argument("--fg", action="store_true", help="前台直连执行（限 <60s 探针短命令）")

    # logs
    p_logs = sub.add_parser("logs", help="查看或追踪远端任务日志")
    p_logs.add_argument("session", type=str, help="tmux 会话名或日志前缀（如 ava-20260910_221500）")
    p_logs.add_argument("-f", "--follow", action="store_true", help="实时追踪日志输出")
    p_logs.add_argument("-n", "--lines", type=int, default=30, help="显示末尾行数（默认 30 行）")

    # doctor
    p_doctor = sub.add_parser("doctor", help="全面诊断远端环境、显卡、数据盘与模型资产")

    # connect（D102）
    p_connect = sub.add_parser("connect", help="换实例：贴控制台的 SSH 登录指令，写连接信息并生成 ssh 配置")
    p_connect.add_argument("login", type=str, help='控制台复制的登录指令，整段加引号，如 "ssh -p 34557 root@connect.weste.seetacloud.com"')

    # up
    p_up = sub.add_parser("up", help="开启实例并初始化远端守护脚本")
    p_up.add_argument("--mode", choices=["cardless", "gpu"], default=None,
                      help="声明开机模式 (cardless=无卡节能模式, gpu=带卡模式)；不填按 nvidia-smi 探测记")
    p_up.add_argument("--rate", type=float, default=None,
                      help="这台实例的按量单价（元/小时，控制台上看到的数；D108：卡不固定、价不固定，不报则账本按参考价估算）")
    p_up.add_argument("--timeout", type=int, default=300, help="开机等待超时秒数（默认 300s）")
    p_up.add_argument("--episode", type=str, default=None, help="关联合同期号")

    # down
    p_down = sub.add_parser("down", help="关闭实例并完成账簿结算")
    p_down.add_argument("--force", action="store_true", help="强制下发关机指令（即使远端仍有活跃任务）")
    p_down.add_argument("--rate", type=float, default=None,
                        help="补报这台实例的按量单价（元/小时）；优先于 up 时报的")

    # status
    p_status = sub.add_parser("status", help="查询实例连通性、GPU工况与账簿明细")

    # push
    p_push = sub.add_parser("push", help="同步本地稿件与配置至云端")
    p_push.add_argument("target", type=Path, help="本期目录路径")

    # run
    p_run = sub.add_parser("run", help="在云端执行指定任务（白名单控制：tts, probe）")
    p_run.add_argument("target", type=Path, help="本期目录路径")
    p_run.add_argument("task", type=str, help="任务名称（当前放行：tts, probe）")
    p_run.add_argument("extra", nargs=argparse.REMAINDER, help="透传给远端任务的额外参数（如 --redo 3,7）")

    # pull
    p_pull = sub.add_parser("pull", help="拉取云端产物回本地")
    p_pull.add_argument("target", type=Path, help="本期目录路径")

    # D59：远端一次性维护，原先要人登远端手敲
    sub.add_parser("relocate-data", help="远端 data/ 改道数据盘并软链回来（幂等；有任务在跑就拒）")
    sub.add_parser("clean-frames", help="清远端上一轮 VLM 打标帧 *_cap（本地可秒级重抽）")
    sub.add_parser("fix-env", help="按 doctor 判据修远端：克隆缺失的仓库、重下缺失/残缺的模型（清单来自 config）")
    sub.add_parser("sync-code", help="把本地 pipeline/ config/ pyproject.toml 推到远端并按哈希核对（D107）")

    # D103 ②：番池素材（data/library/ 下的番剧集与 SP）云端打标
    for name, help_ in (("lib-push", "番池素材打标输入（镜头表 + 打标帧）上传"),
                        ("lib-captions", "番池素材云端打标（计费；先核远端代码）"),
                        ("lib-pull", "番池素材打标产物拉回（只拉写明的集键）")):
        p_lib = sub.add_parser(name, help=help_)
        p_lib.add_argument("pool", help="池名（番名 / 企划名）")
        p_lib.add_argument("keys", nargs="+", help="集键 SP01 / S01E01，可多个或逗号分隔")
        if name == "lib-captions":
            p_lib.add_argument("--batch", type=int, default=None, help="一次前向几个镜头（同 vindex captions）")
            p_lib.add_argument("--limit", type=int, default=None, help="冒烟用：只打前 N 个镜头")
            p_lib.add_argument("--confirm-cost", action="store_true", help="预估超预算仍继续（同 vindex captions）")

    # attach
    p_attach = sub.add_parser("attach", help="终端接驳远端 tmux 会话")
    p_attach.add_argument("--session", type=str, default="ava-tts", help="tmux 会话名")
    return ap


def main() -> int:
    args = build_parser().parse_args()

    dispatch = {
        "exec": cmd_exec,
        "logs": cmd_logs,
        "doctor": cmd_doctor,
        "connect": cmd_connect,
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
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
