"""端云协同调度与 GPU 生命周期管理（M1b，E9：python -m pipeline.cloud）。

核心能力：
1. exec: 远端安全执行命令——默认强制 tmux 包裹后台化 + 日志落盘（防断连 SIGHUP）；
         仅 --fg 允许直连短命令（限 <60s 探针）
2. logs: 查看/跟踪远端任务日志
3. doctor: 逐项核验云端环境（SSH、显卡≥24GB/无卡模式、仓库、PyTorch+CUDA、数据盘、五大模型）
4. up/down/status: 云端实例生命周期管理与分模式成本核算（追加式账簿 data/cloud/ledger.jsonl）
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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

# ---------------------------------------------------------------------------
# 常量定义与判据依据（E3：每个常量后面写「为什么是这个数」）
# ---------------------------------------------------------------------------

# 允许通过 run 命令透传执行的远端任务白名单
# 为什么是白名单：禁止透传任意 shell 命令，杜绝远端非预期写操作破坏环境
ALLOWED_TASKS = {
    "tts",    # 语音合成与回读质检（对应 python -m pipeline.tts <期>）
    "probe",  # M1b 闭环自检验收探针（GPU + PyTorch 连通性测试）
}

# 默认心跳文件位置与空闲关机阈值
WATCHDOG_HEARTBEAT_PATH = "/root/.ava_heartbeat"
DEFAULT_IDLE_SHUTDOWN_SECONDS = 300  # 300s：无操作 5 分钟关机，给 pull 预留容错窗口

# 工业化多模态视频流水线显存门槛
MIN_VRAM_GB = 24.0  # 24GB：承载 8B VLM 镜头打标与 7B TTS 流匹配模型串行加载的底线显存

# 远端日志根目录
REMOTE_LOG_DIR = "/root/autodl-tmp/logs"


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


def evaluate_doctor_gpu(raw_vram_output: str, returncode: int) -> tuple[str, str, bool]:
    """评估 GPU 项健康状态（纯函数）。

    判据：
    - 无 GPU / nvidia-smi 失败 → 判定为「无卡模式，GPU 未挂载」（状态陈述，非失败）
    - 有 GPU → 断言显存容量 ≥ 24GB（断言容量，不断言型号名）
    返回: (状态标签 OK/INFO/FAIL, 详情描述, 是否通过质检)
    """
    if returncode != 0 or not raw_vram_output.strip():
        return "INFO", "无卡模式，GPU 未挂载（0.10元/h 节能维护模式）", True

    try:
        first_line = raw_vram_output.strip().splitlines()[0]
        vram_mb = float(first_line.strip())
        vram_gb = round(vram_mb / 1024.0, 1)
        if vram_gb >= MIN_VRAM_GB:
            return "OK", f"显存容量: {vram_gb}GB ≥ {MIN_VRAM_GB}GB (满足多模态模型栈要求)", True
        else:
            return "FAIL", f"显存容量: {vram_gb}GB < {MIN_VRAM_GB}GB (不足以承载 8B VLM 与 7B TTS 串行推理)", False
    except Exception as e:
        return "FAIL", f"解析 nvidia-smi 显存输出失败: {e} ({raw_vram_output})", False


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
    model_dir: str, models_root: str = "/root/autodl-tmp/models"
) -> str:
    """生成远端模型结构校验命令（纯函数）。

    脚本经 base64 落地：内联走 ssh 时，脚本里的引号会在 shell → ssh → 远端 shell
    三层之间互相切断（M1b 踩过同样的坑），base64 是唯一不需要在三层引号间走钢练的做法。
    """
    encoded = base64.b64encode(_MODEL_STRUCT_PROBE_SCRIPT.encode("utf-8")).decode("ascii")
    return (
        f"echo {encoded} | base64 -d > /tmp/.ava_model_probe.py && "
        f"/root/miniconda3/bin/python /tmp/.ava_model_probe.py '{models_root}/{model_dir}'"
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


def build_remote_run_command(
    task: str, ep_rel_path: str, remote_root: str = "/root/anime-video-agent"
) -> str:
    """生成远端安全执行命令（P0-1 首尾心跳，P2-5 probe 任务分支）。"""
    task = validate_task_whitelist(task)
    if task == "probe":
        # probe 探针任务：轻量级验证 GPU/Torch/环境，不拖入未移植的 TTS 模块
        cmd = (
            f"cd {remote_root} && "
            f"touch {WATCHDOG_HEARTBEAT_PATH} && "
            f"mkdir -p {ep_rel_path}/03-audio && "
            f"/root/miniconda3/bin/python -c '"
            f"import torch; "
            f"print(f\"GPU Probe: torch={{torch.__version__}}, cuda={{torch.cuda.is_available()}}\")"
            f"' | tee {ep_rel_path}/03-audio/probe.log && "
            f"touch {WATCHDOG_HEARTBEAT_PATH}"
        )
    else:
        # tts 必须走云端覆盖层：本地 Mac 只有 mlx（qwen3_tts），云端只有 CUDA（indextts2），
        # 同一个 engine 字段喂不了两边。覆盖层只写差异，其余字段继承 voice.json。
        extra = " --config config/voice.cloud.json" if task == "tts" else ""
        cmd = (
            f"cd {remote_root} && "
            f"touch {WATCHDOG_HEARTBEAT_PATH} && "
            f"/root/miniconda3/bin/python -m pipeline.{task} {ep_rel_path}{extra} && "
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
    script_path: str = "/root/watchdog.sh",
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
) -> dict[str, Any]:
    """格式化账簿单行 JSON 对象（P1-2 增加 mode 字段）。

    note 用于标注时长的可信度：实例已被 watchdog 自动关机时，down 根本联系不上远端，
    只能把时长算到「执行 down 的此刻」——这会多算一段关机到发现的延迟，
    属于**上界估计**。记进 note 而不是假装精确。
    """
    over_budget = cost_cny > budget_cny
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
    }
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
    """获取 SSH 连接目标 Host 别名（走 ~/.ssh/config 的 autodl）。"""
    return cfg_local.get("ssh_host", "autodl")


def _ssh(
    cmd: str,
    host: str = "autodl",
    check: bool = True,
    timeout: int = 60,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """subprocess 封装 ssh。

    Host 走 ~/.ssh/config 的 autodl 别名（ControlMaster 复用在 config 层解决，代码不重复造）。
    """
    ssh_args = ["ssh", "-o", "BatchMode=yes", host, cmd]
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
        "ssh",
        *exclude_args,
        src,
        dst,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


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
        p = subprocess.run(["ssh", host, remote_cmd])
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
        return subprocess.call(["ssh", host, cmd])
    else:
        res = _ssh(cmd, host=host, check=False)
        if res.returncode == 0:
            print(res.stdout, end="")
            return 0
        else:
            print(f"FAIL 无法读取远端日志 {log_path}: {res.stderr.strip()}", file=sys.stderr)
            return res.returncode


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
        print("            2. 检查 ~/.ssh/config 中 Host autodl 的端口是否与控制台一致。")
        return 1

    # 2. NVIDIA GPU（断言显存容量 ≥ 24GB，允许无卡模式）
    gpu_res = _ssh(
        "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits",
        host=host,
        check=False,
    )
    status_tag, desc, passed = evaluate_doctor_gpu(gpu_res.stdout, gpu_res.returncode)
    print(f"{status_tag:<5} [GPU] {desc}")
    if not passed:
        all_passed = False
        print("      修法: 请在控制台更换为显存 ≥ 24GB 的机型（如 RTX 4090 或 RTX 6000D）。")

    # 3. 远端代码仓库
    repo_res = _ssh(f"test -d {remote_root}/.git && cd {remote_root} && git rev-parse --short HEAD", host=host, check=False)
    if repo_res.returncode == 0:
        print(f"OK    [REPO] 远端仓库就绪 ({remote_root}, commit: {repo_res.stdout.strip()})")
    else:
        all_passed = False
        print(f"FAIL  [REPO] 远端仓库不存在或未完成初始化: {remote_root}")
        print(f"      修法: python -m pipeline.cloud exec 'source /etc/network_turbo && git clone https://github.com/songhuiming2007-coder/anime-video-agent.git {remote_root}'")

    # 4. PyTorch 与 CUDA 可用性
    torch_cmd = (
        "/root/miniconda3/bin/python -c "
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
        print("      修法: /root/miniconda3/bin/pip install torch torchvision torchaudio")

    # 5. 数据盘存储
    disk_res = _ssh("df -BG /root/autodl-tmp | tail -1 | awk '{print $4}' | tr -d 'G'", host=host, check=False)
    d_tag, d_desc, d_passed = evaluate_doctor_disk(disk_res.stdout)
    print(f"{d_tag:<5} [DISK] {d_desc}")
    if not d_passed:
        all_passed = False
        print("      修法: 清理 /root/autodl-tmp 中无用的大文件或临时缓存。")

    # 6. 五大模型逐一核查
    print("-" * 65)
    print("模型资产在位核查 (/root/autodl-tmp/models):")
    for m_id, spec in models_spec.items():
        m_dir = spec.get("dir", m_id)
        min_gb = float(spec.get("min_gb", 1.0))
        desc = spec.get("description", "")
        chk_cmd = (
            f"if [ -d '/root/autodl-tmp/models/{m_dir}' ]; then "
            f"  du -s -BG '/root/autodl-tmp/models/{m_dir}' | awk '{{print $1}}' | tr -d 'G'; "
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
                    st_res = _ssh(build_model_structure_probe_cmd(m_dir), host=host, check=False, timeout=90)
                    st_tag, st_desc = parse_model_structure_result(st_res.stdout, st_res.returncode)
                    print(f"{st_tag:<5} [STRUCT] {m_id:<30} {st_desc}")
                    if st_tag == "FAIL":
                        all_passed = False
                        print("      修法: 重新下载该模型（权重截断/损坏，体积判据抓不到）")
                else:
                    all_passed = False
                    print(f"FAIL  [MODEL] {m_id:<30} 体积不足 ({sz_gb:.1f}GB < {min_gb}GB) - {desc}")
                    print(f"      下载修复命令:")
                    print(f"        python -m pipeline.cloud exec 'export HF_ENDPOINT=https://hf-mirror.com && python3 -c \"from huggingface_hub import snapshot_download; snapshot_download(\\\"{m_id}\\\", local_dir=\\\"/root/autodl-tmp/models/{m_dir}\\\")\"'")
            except ValueError:
                all_passed = False
                print(f"FAIL  [MODEL] {m_id:<30} 状态异常 - {desc}")
        else:
            all_passed = False
            print(f"FAIL  [MODEL] {m_id:<30} 缺失 - {desc}")
            print(f"      下载修复命令:")
            print(f"        python -m pipeline.cloud exec 'export HF_ENDPOINT=https://hf-mirror.com && python3 -c \"from huggingface_hub import snapshot_download; snapshot_download(\\\"{m_id}\\\", local_dir=\\\"/root/autodl-tmp/models/{m_dir}\\\")\"'")

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
    mode = getattr(args, "mode", "gpu")

    print(f"[*] 检查云端实例连通性 (Host: {host}, 模式: {mode})...")
    if not is_ssh_reachable(host):
        inst_id = cfg_local.get("instance_id", "未配置")
        region = cfg_local.get("region", "西北B区")
        print(f"\n[!] 实例当前不可达。")
        print(f"    请前往 AutoDL 控制台手动开机实例（模式选择：{mode}）：")
        print(f"      实例 ID : {inst_id} ({region})")
        print(f"      控制台  : https://www.autodl.com/console/instance/list")
        print(f"[*] 轮询等待 SSH 可达中（超时 {args.timeout} 秒）...")

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
    now_time = datetime.now().strftime("%H:%M:%S")
    session_data = {
        "session": f"{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "mode": mode,
        "up": now_time,
        "up_epoch": time.time(),
        "episode": getattr(args, "episode", None),
        "tasks": [],
    }
    sfile.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 会话已开启: {session_data['session']} (模式: {mode})")
    return 0


def detect_runtime_mode_from_gpu_probe(returncode: int) -> str:
    """由 nvidia-smi 探针返回码判定当前运行模式（纯函数）。

    为什么要探测而不是猜：无卡 ¥0.10/h 与带卡 ¥2.40/h 相差 24 倍。
    旧代码在缺少会话文件（用户手动开机后直接跑 down）时一律默认 gpu，
    若实例实际是无卡就会把账目虚高 24 倍。探测比猜测便宜得多。
    """
    return "gpu" if returncode == 0 else "cardless"


def cmd_down(args: argparse.Namespace) -> int:
    """关机结算命令：按模式精算费用，落盘账簿，提示/下发关机。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)

    sfile = get_active_session_file()
    session_info: dict = {}
    if sfile.exists():
        try:
            session_info = json.loads(sfile.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 先探一次连通性：它同时决定「时长可信度」与「模式判定」。
    # 若实例已被 watchdog 自动关机，本地根本联系不上，只能把时长算到此刻——
    # 这会多计一段「关机到被发现」的延迟，属于**上界估计**，必须写进账簿注记。
    reachable = is_ssh_reachable(host)

    mode = session_info.get("mode")
    if not mode:
        # 无会话文件（用户手动开机后直接 down）：探测真实模式，不猜。
        if reachable:
            mode = detect_runtime_mode_from_gpu_probe(_ssh("nvidia-smi -L", host=host, check=False).returncode)
        else:
            mode = "unknown"

    if mode == "cardless":
        rate = float(cfg_global.get("cardless_rate_cny", 0.10))
    elif mode == "gpu":
        rate = float(cfg_global.get("hourly_rate_cny", 2.40))
    else:
        # mode 不可知时取较贵的费率：宁可高估也不能低估预算风险
        rate = float(cfg_global.get("hourly_rate_cny", 2.40))

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
    )
    lfile = get_ledger_file()
    with lfile.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

    if sfile.exists():
        sfile.unlink()

    print("=" * 60)
    print(f"会话结算完成：")
    print(f"  会话模式: {mode} (计费单价: ¥{rate}/小时)")
    print(f"  运行时长: {duration_sec // 60} 分 {duration_sec % 60} 秒 ({duration_sec}s)")
    print(f"  本次计费: ¥{cost_cny:.4f} 元")
    if ledger_entry["over_budget"]:
        print(f"  \033[91m⚠️  警告: 本次花费超出单期预算 ¥{budget} 元！\033[0m")
    else:
        print(f"  预算状态: 达标 (≤ ¥{budget} 元)")
    print(f"  账簿记录: → {lfile}")
    print("=" * 60)

    if reachable:
        tmux_check = _ssh("tmux ls 2>/dev/null | grep '^ava-' || true", host=host, check=False)
        active_tasks = [ln.split(":")[0] for ln in tmux_check.stdout.splitlines() if ln.startswith("ava-")]
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
    rate_gpu = float(cfg_global.get("hourly_rate_cny", 2.40))
    rate_cardless = float(cfg_global.get("cardless_rate_cny", 0.10))
    budget = float(cfg_global.get("budget_per_episode_cny", 0.35))

    print("=" * 65)
    print(f"  AutoDL 端云工况状态 ({cfg_global.get('gpu', 'RTX 6000D')})")
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

        watch_res = _ssh(f"pgrep -f '{_watchdog_proc_pattern()}' >/dev/null && echo '运行中' || echo '未运行'", host=host, check=False)
        print(f"自动关机守卫 : {watch_res.stdout.strip()}")

    sfile = get_active_session_file()
    if sfile.exists():
        try:
            sdata = json.loads(sfile.read_text(encoding="utf-8"))
            mode = sdata.get("mode", "gpu")
            rate = rate_cardless if mode == "cardless" else rate_gpu
            elapsed = int(time.time() - sdata.get("up_epoch", time.time()))
            cur_cost = calculate_session_cost(elapsed, rate)
            print("-" * 65)
            print(f"当前活跃会话 : {sdata.get('session')} (开始于 {sdata.get('up')}, 模式: {mode})")
            print(f"已运行时间   : {elapsed // 60} 分 {elapsed % 60} 秒")
            cost_color = "\033[91m" if cur_cost > budget else "\033[92m"
            print(f"已产生费用   : {cost_color}¥{cur_cost:.4f}\033[0m 元 (基准单价: ¥{rate}/小时, 限额: ¥{budget})")
        except Exception:
            pass

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

    _ssh(f"mkdir -p {remote_ep_dir}", host=host)

    sync_items = cfg_global.get("sync", {}).get("up", ["02-script.md", "01-topic.md", "config/"])
    sync_pairs = build_sync_up_files(ep_dir, sync_items, remote_ep_dir, remote_root)

    for src, dst in sync_pairs:
        res = run_rsync(src, f"{host}:{dst}")
        if res.returncode != 0:
            raise SystemExit(f"FAIL 推送失败 {src} -> {dst}: {res.stderr.strip()}")
        print(f"  ✓ 已同步: {src} -> {dst}")

    print("[OK] push 完成。资产已在云端就位。")
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

    sfile = get_active_session_file()
    if sfile.exists():
        try:
            sdata = json.loads(sfile.read_text(encoding="utf-8"))
            sdata.setdefault("tasks", [])
            if task not in sdata["tasks"]:
                sdata["tasks"].append(task)
            sdata["episode"] = ep_dir.name
            sfile.write_text(json.dumps(sdata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    _ssh(f"touch {WATCHDOG_HEARTBEAT_PATH}", host=host)

    session_name = f"ava-{task}"
    remote_cmd = build_remote_run_command(task, rel_path, remote_root)
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
    """下行拉取命令：遵循目录斜杠纪律与完整性自检（P1-1/P2-4）。"""
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
    print(f"[*] 从远端拉取执行产物...")

    sync_items = cfg_global.get("sync", {}).get("down", ["03-audio/", "04-clips.json", "06-check.log"])
    sync_pairs = build_sync_down_files(ep_dir, sync_items, remote_ep_dir)

    for src, dst in sync_pairs:
        res = run_rsync(f"{host}:{src}", dst)
        if res.returncode == 0:
            print(f"  ✓ 已拉取: {src} -> {dst}")
        else:
            print(f"  ○ 未发现远端产物或跳过: {src}")

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

    if "probe" in session_tasks or not session_tasks:
        if probe_log.exists():
            print(f"[OK] 03-audio/probe.log 已拉回: {probe_log.read_text(encoding='utf-8').strip()[:80]}")
            verified_any = True
        else:
            print("FAIL 本次 probe 任务未拉回 03-audio/probe.log", file=sys.stderr)

    if "tts" in session_tasks:
        if mf.exists():
            try:
                json.loads(mf.read_text(encoding="utf-8"))
                print("[OK] 03-audio/manifest.json 结构校验通过。")
                verified_any = True
            except Exception as e:
                print(f"FAIL 拉回的 manifest.json 损坏: {e}", file=sys.stderr)
        else:
            print("FAIL 本次 tts 任务未拉回 03-audio/manifest.json", file=sys.stderr)

    if not verified_any:
        print(f"WARN 未验证到任何本次任务产物（本次任务: {session_tasks or '未知'}）", file=sys.stderr)

    print("[OK] pull 完成。")
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    """本地接入远端 tmux 会话。"""
    cfg_global, cfg_local = load_cloud_config()
    host = _ssh_host(cfg_local)
    session_name = args.session or "ava-tts"
    cmd = f"ssh -t {host} 'tmux attach-session -t {session_name}'"
    print(f"[*] 接入 tmux 会话 {session_name}...")
    return subprocess.call(cmd, shell=True)


# ---------------------------------------------------------------------------
# 第四节：CLI 入口
# ---------------------------------------------------------------------------


def main() -> int:
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

    # up
    p_up = sub.add_parser("up", help="开启实例并初始化远端守护脚本")
    p_up.add_argument("--mode", choices=["cardless", "gpu"], default="cardless", help="开机模式 (cardless=无卡节能模式0.1元/h, gpu=带卡模式2.4元/h)")
    p_up.add_argument("--timeout", type=int, default=300, help="开机等待超时秒数（默认 300s）")
    p_up.add_argument("--episode", type=str, default=None, help="关联合同期号")

    # down
    p_down = sub.add_parser("down", help="关闭实例并完成账簿结算")
    p_down.add_argument("--force", action="store_true", help="强制下发关机指令（即使远端仍有活跃任务）")

    # status
    p_status = sub.add_parser("status", help="查询实例连通性、GPU工况与账簿明细")

    # push
    p_push = sub.add_parser("push", help="同步本地稿件与配置至云端")
    p_push.add_argument("target", type=Path, help="本期目录路径")

    # run
    p_run = sub.add_parser("run", help="在云端执行指定任务（白名单控制：tts, probe）")
    p_run.add_argument("target", type=Path, help="本期目录路径")
    p_run.add_argument("task", type=str, help="任务名称（当前放行：tts, probe）")

    # pull
    p_pull = sub.add_parser("pull", help="拉取云端产物回本地")
    p_pull.add_argument("target", type=Path, help="本期目录路径")

    # attach
    p_attach = sub.add_parser("attach", help="终端接驳远端 tmux 会话")
    p_attach.add_argument("--session", type=str, default="ava-tts", help="tmux 会话名")

    args = ap.parse_args()

    dispatch = {
        "exec": cmd_exec,
        "logs": cmd_logs,
        "doctor": cmd_doctor,
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "push": cmd_push,
        "run": cmd_run,
        "pull": cmd_pull,
        "attach": cmd_attach,
    }

    fn = dispatch.get(args.cmd)
    if fn:
        return fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
