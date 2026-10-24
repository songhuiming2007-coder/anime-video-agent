"""纯函数测试：端云协同与调度（tests/test_cloud.py）。

对齐 STANDARD 第八节与 M1b 返修要求：
- 纯函数测试，内存构造 fixture，不触碰片源/外部网络/真实 SSH，保持 pytest 3 秒可跑。
- 每条断言注释说明防范的具体错误。
- 覆盖：
  1. exec tmux 命令包裹与首尾心跳（纯函数）
  2. 任务白名单放行（tts 与 probe）与非法命令拦截
  3. Watchdog 活跃任务检测（tmux ava-* 存活不关机，S1 测真实状态）
  4. 计费核算、分模式（cardless/gpu）账簿格式化与超预算判定
  5. 账簿解析损坏行向 stderr 报警不静默吞异常
  6. rsync 目录同步斜杠语义（src 带斜杠，dst 不带斜杠，防嵌套）
  7. Doctor GPU 项评估（无卡模式 INFO 与显存容量断言）与数据盘断言
"""

from __future__ import annotations

from pathlib import Path
import pytest

from pipeline import cloud


# ---------------------------------------------------------------------------
# 1. exec 远端命令拼接与日志路径
# ---------------------------------------------------------------------------


def test_build_exec_tmux_command():
    """验证 exec 生成的 tmux 命令结构，首尾必须 touch 心跳，输出 tee 到日志文件。"""
    cmd = "python3 download.py"
    session = "ava-test123"
    tmux_cmd, log_path = cloud.build_exec_tmux_command(cmd, session, log_dir="/root/autodl-tmp/logs")

    assert log_path == "/root/autodl-tmp/logs/ava-test123.log"
    assert "mkdir -p /root/autodl-tmp/logs" in tmux_cmd, "必须确保日志目录存在"
    assert "tmux new-session -d -s ava-test123" in tmux_cmd, "必须以后台 -d 模式启动 tmux"
    assert f"touch {cloud.WATCHDOG_HEARTBEAT_PATH}" in tmux_cmd, "首尾必须包含心跳刷新"
    assert "tee /root/autodl-tmp/logs/ava-test123.log" in tmux_cmd, "必须 tee 到指定日志路径"


def test_build_exec_tmux_command_escaping():
    """命令中包含单引号时必须正确转义，防范 shell 语法报错。"""
    cmd = "echo 'hello world'"
    tmux_cmd, _ = cloud.build_exec_tmux_command(cmd, "ava-esc")
    assert "'\\''" in tmux_cmd, "内部单引号必须被正确转义"


# ---------------------------------------------------------------------------
# 2. 任务白名单与远端安全命令拼接（P2-5 支持 probe）
# ---------------------------------------------------------------------------


def test_validate_task_whitelist_allows_tts_and_probe():
    """验证 M1 阶段白名单任务 'tts' 与 'probe' 均正常放行。"""
    assert cloud.validate_task_whitelist("tts") == "tts"
    assert cloud.validate_task_whitelist("probe") == "probe"
    assert cloud.validate_task_whitelist("  PROBE  ") == "probe"


def test_validate_task_whitelist_rejects_arbitrary_commands():
    """非白名单任务或 shell 注入必须触发 SystemExit 拦截，防范远端执行非预期命令。"""
    with pytest.raises(SystemExit) as exc:
        cloud.validate_task_whitelist("rm -rf /")
    assert "不在允许的远端任务白名单内" in str(exc.value)

    with pytest.raises(SystemExit) as exc:
        cloud.validate_task_whitelist("bash")
    assert "不在允许的远端任务白名单内" in str(exc.value)


def test_build_remote_run_command_tts():
    """验证 tts 命令结构：切换目录、首尾双重心跳、调用指定 Python 解释器。"""
    cmd = cloud.build_remote_run_command(
        task="tts",
        ep_rel_path="data/episodes/test_ep",
        remote_root="/root/anime-video-agent",
    )
    assert "cd /root/anime-video-agent" in cmd, "命令必须先切换到远端仓库根目录"
    assert f"touch {cloud.WATCHDOG_HEARTBEAT_PATH}" in cmd, "命令必须显式 touch 心跳文件防止关机"
    assert (f"{cloud.DEFAULT_REMOTE_PYTHON} -m pipeline.tts data/episodes/test_ep" in cmd), (
        "远端解释器必须是装了模型栈的那个 venv（2026-09-11 实测 miniconda 里没有 transformers）")


def test_build_remote_run_command_probe():
    """验证 probe 探针命令结构：运行轻量 GPU/Torch 检测并写入 probe.log。"""
    cmd = cloud.build_remote_run_command(
        task="probe",
        ep_rel_path="data/episodes/test_ep",
        remote_root="/root/anime-video-agent",
    )
    assert "cd /root/anime-video-agent" in cmd
    assert "probe.log" in cmd, "probe 必须生成日志以供 pull 校验"
    assert "GPU Probe" in cmd


# ---------------------------------------------------------------------------
# 3. Watchdog 脚本生成与安全边界（P0-1 核心修复）
# ---------------------------------------------------------------------------


def test_generate_watchdog_script_has_active_task_guard():
    """Watchdog 循环内必须包含对活跃 tmux 会话的检测（S1 测真实状态），有任务活着绝不关机。"""
    script = cloud.generate_watchdog_script(idle_seconds=300, heartbeat_path="/root/.ava_heartbeat")
    assert "#!/bin/bash" in script
    assert "tmux ls 2>/dev/null | grep -q '^ava-'" in script, (
        "必须检查 tmux 是否有以 ava- 开头的活跃任务"
    )
    assert 'HEARTBEAT_FILE="/root/.ava_heartbeat"' in script
    assert "IDLE_LIMIT=300" in script


def test_generate_watchdog_script_uses_autodl_shutdown():
    """关机必须用 /usr/bin/shutdown。2026-09-11 实测：AutoDL 容器 PID 1 是 boot.sh、
    无 systemd，/sbin/shutdown 不存在、poweroff→systemctl 无效，只有 /usr/bin/shutdown 是平台包装脚本。"""
    script = cloud.generate_watchdog_script()
    assert "/usr/bin/shutdown -h now" in script, "必须调用 AutoDL 容器内真实存在的关机脚本"
    assert "/sbin/shutdown" not in script, "/sbin/shutdown 在 AutoDL 容器内不存在，写了也是白写"


def test_build_watchdog_deploy_command_kills_old_process():
    """部署命令必须先 pkill 旧 watchdog 再起新的。

    防的错误：bash 流式按偏移读脚本，重写运行中的脚本文件会让旧进程读到错位内容，
    2026-09-11 实测导致 watchdog 反复执行 touch ''、该关机时不关机、空转 7.5 小时。
    """
    cmd = cloud.build_watchdog_deploy_command("#!/bin/bash\necho hi\n")
    pat = "'^/bin/bash /root/watchdog\\.sh$'"
    assert f"pkill -f {pat}" in cmd, "必须先杀掉旧进程，否则新旧脚本错位"
    assert cmd.index("pkill") < cmd.index("setsid"), "pkill 必须发生在启动新进程之前"
    assert f"pgrep -f {pat}" in cmd, "必须回传新 PID 供调用方验证存活"
    assert "touch /root/.ava_heartbeat" in cmd, "部署时需刷新心跳作为初始基准"


def test_deploy_command_uses_setsid_not_nohup():
    """必须用 setsid 脱离进程组。

    防的错误：nohup 只忽略 SIGHUP，但 sshd 会话结束清理的是整个进程组，
    后台进程连带被杀 → watchdog 静默未启动（2026-09-11 实测：脚本写入了但无进程）。
    setsid 开新会话脱离进程组才是可靠的；< /dev/null 断掉继承的 stdin。
    """
    cmd = cloud.build_watchdog_deploy_command("#!/bin/bash\n")
    assert "setsid" in cmd, "必须用 setsid，nohup 在 ssh 会话结束时会被进程组清理杀掉"
    assert "nohup" not in cmd
    assert "< /dev/null" in cmd, "必须断开继承的 stdin"


def test_deploy_command_does_not_kill_itself():
    """部署命令不能 pkill 掉自己。

    防的错误（2026-09-11 实测，两次踩坑）：
    ① 早版本用 `[b]ash` 字符类防 pkill 自身，但命令里还有 `setsid /bin/bash <script>`
      这段裸文本，包装进程命令行依然含 `bash /root/watchdog.sh` → pkill 自匹配。
    ② 症状极具误导性：ssh 返回码 255、stderr 完全为空，看起来像网络问题，
      实际是把自己的 ssh 会话杀了。
    正解：pkill/pgrep 的模式必须**锚定**为整条命令行恰为 `/bin/bash <script>`，
    包装进程那串以 `bash -c` 开头的长命令行就永远匹配不上。
    """
    cmd = cloud.build_watchdog_deploy_command("#!/bin/bash\n")
    assert "'^/bin/bash /root/watchdog\\.sh$'" in cmd, "必须用锚定正则，否则会自匹配"
    assert "[b]ash" not in cmd, "字符类防法已被证伪：setsid 那段裸文本仍会自匹配"


def test_watchdog_proc_pattern_is_anchored_and_escaped():
    """锚定正则本身要正确；点号必须转义，避免正则通配。"""
    pat = cloud._watchdog_proc_pattern("/root/watchdog.sh")
    assert pat == "^/bin/bash /root/watchdog\\.sh$"
    assert cloud._watchdog_proc_pattern("/root/w.sh") == "^/bin/bash /root/w\\.sh$"


# ---------------------------------------------------------------------------
# 4. 计费核算、分模式账簿格式化与超预算判定（P1-2 / P2-3）
# ---------------------------------------------------------------------------


def test_calculate_session_cost():
    """根据秒数与单价计算费用，防范浮点舍入漂移。"""
    # 3600 秒，2.40 元/小时 = 2.40 元
    assert cloud.calculate_session_cost(3600, 2.40) == 2.40
    # 3600 秒，0.10 元/小时 = 0.10 元 (无卡模式)
    assert cloud.calculate_session_cost(3600, 0.10) == 0.10
    # 600 秒 (10 分钟)，0.10 元/小时 = 0.0167 元
    assert cloud.calculate_session_cost(600, 0.10) == 0.0167


def test_format_ledger_entry_mode_and_budget():
    """验证账簿 JSON 包含 mode 字段并准确计算 over_budget。"""
    # 无卡模式 (0.05 <= 0.35)
    r1 = cloud.format_ledger_entry(
        session_id="s1",
        up_time="10:00:00",
        down_time="10:30:00",
        gpu_seconds=1800,
        cost_cny=0.05,
        episode="ep1",
        tasks=["probe"],
        budget_cny=0.35,
        mode="cardless",
    )
    assert r1["mode"] == "cardless"
    assert r1["over_budget"] is False

    # 带卡超预算 (0.40 > 0.35)
    r2 = cloud.format_ledger_entry(
        session_id="s2",
        up_time="11:00:00",
        down_time="11:10:00",
        gpu_seconds=600,
        cost_cny=0.40,
        episode="ep2",
        tasks=["tts"],
        budget_cny=0.35,
        mode="gpu",
    )
    assert r2["mode"] == "gpu"
    assert r2["over_budget"] is True


def test_parse_ledger_warns_on_corrupted_line(capsys):
    """验证账簿损坏行向 stderr 输出警告，不静默吞异常（P2-3）。"""
    lines = (
        '{"session": "s1", "gpu_seconds": 300, "cny": 0.20, "over_budget": false}\n'
        'CORRUPTED_LINE_NOT_JSON\n'
    )
    tot_cny, tot_sec, records = cloud.parse_ledger(lines)
    assert tot_cny == 0.20 and tot_sec == 300 and len(records) == 1

    captured = capsys.readouterr()
    assert "WARN 账簿行损坏无法解析" in captured.err, "必须向 stderr 报告损坏行"
    assert "CORRUPTED_LINE_NOT_JSON" in captured.err


# ---------------------------------------------------------------------------
# 5. rsync 目录同步斜杠语义（P1-1 / P2-4）
# ---------------------------------------------------------------------------


def test_build_sync_up_files_directory_slash_semantics(tmp_path: Path):
    """上行同步规则：目录的 src 必须以 '/' 结尾，dst 不得以 '/' 结尾，防范远端产生嵌套目录。"""
    ep_dir = tmp_path / "ep1"
    ep_dir.mkdir()
    (ep_dir / "02-script.md").write_text("content", encoding="utf-8")
    (ep_dir / "01-topic.md").write_text("content", encoding="utf-8")

    sync_items = ["02-script.md", "01-topic.md", "config/"]
    pairs = cloud.build_sync_up_files(
        ep_dir=ep_dir,
        sync_items=sync_items,
        remote_ep_dir="/root/anime-video-agent/data/episodes/ep1",
        remote_root="/root/anime-video-agent",
    )

    pair_map = dict(pairs)
    # 文件类同步：两端均不带斜杠
    assert any(src.endswith("02-script.md") for src in pair_map)

    # 目录类同步（如 config/）：src 必须以 '/' 结尾，dst 不能以 '/' 结尾
    config_src = [src for src in pair_map if "config" in src][0]
    config_dst = pair_map[config_src]
    assert config_src.endswith("/"), "目录同步的 src 必须带尾斜杠"
    assert not config_dst.endswith("/"), "目录同步的 dst 绝不可带尾斜杠"
    assert config_dst == "/root/anime-video-agent/config"


def test_build_sync_down_files_directory_slash_semantics(tmp_path: Path):
    """下行拉取规则：目录的远端 src 必须以 '/' 结尾，本地 dst 不带尾斜杠。"""
    ep_dir = tmp_path / "ep1"
    sync_items = ["03-audio/", "04-clips.json"]
    pairs = cloud.build_sync_down_files(
        ep_dir=ep_dir,
        sync_items=sync_items,
        remote_ep_dir="/root/anime-video-agent/data/episodes/ep1",
    )

    pair_map = dict(pairs)
    # 03-audio/ 是目录
    audio_src = [src for src in pair_map if "03-audio" in src][0]
    audio_dst = pair_map[audio_src]
    assert audio_src.endswith("/"), "拉取目录的远端 src 必须带尾斜杠"
    assert not audio_dst.endswith("/"), "拉取目录的本地 dst 绝不带尾斜杠"
    assert audio_dst.endswith("03-audio")

    # 04-clips.json 是文件
    clips_src = [src for src in pair_map if "04-clips.json" in src][0]
    clips_dst = pair_map[clips_src]
    assert not clips_src.endswith("/")
    assert not clips_dst.endswith("/")


# ---------------------------------------------------------------------------
# 6. Doctor GPU 与存储评估逻辑
# ---------------------------------------------------------------------------


def test_evaluate_doctor_gpu_non_gpu_mode():
    """无卡模式下，GPU 项必须陈述状态为 INFO 并判定通过，不阻断环境准备工作。"""
    tag, desc, passed = cloud.evaluate_doctor_gpu("", returncode=1)
    assert tag == "INFO"
    assert "无卡模式" in desc
    assert passed is True


def test_evaluate_doctor_gpu_sufficient_vram():
    """带卡模式下，显存 ≥ 24GB 必须判定通过（断言容量，不断言型号名）。"""
    tag, desc, passed = cloud.evaluate_doctor_gpu("49152\n", returncode=0)
    assert tag == "OK"
    assert "48.0GB ≥ 24.0GB" in desc
    assert passed is True


def test_evaluate_doctor_gpu_insufficient_vram():
    """显存低于 24GB 报 WARN 不判失败（D102：卡型按需租，只配音用小卡是正常选择），
    但必须说清 VLM 打标在这张卡上跑不了。"""
    tag, desc, passed = cloud.evaluate_doctor_gpu("16384\n", returncode=0)
    assert tag == "WARN"
    assert "16.0GB < 24.0GB" in desc and "VLM 打标" in desc
    assert passed is True


def test_evaluate_doctor_gpu_报型号():
    """D102：doctor 的查询带上 name 列，结论里要有型号（账本与人都要知道这次租的是什么卡）。"""
    tag, desc, passed = cloud.evaluate_doctor_gpu("NVIDIA vGPU-32GB, 32760\n", returncode=0)
    assert (tag, passed) == ("OK", True)
    assert desc.startswith("NVIDIA vGPU-32GB，") and "32.0GB" in desc


def test_evaluate_doctor_disk():
    """数据盘可用容量断言（≥10GB 及格）。"""
    tag, desc, passed = cloud.evaluate_doctor_disk("50")
    assert tag == "OK" and passed is True

    tag, desc, passed = cloud.evaluate_doctor_disk("5")
    assert tag == "FAIL" and passed is False


def test_tmux_launch_base64_encodes_command():
    """tmux 启动必须走 base64 落地脚本：命令里的引号不能穿透到 tmux 的单引号参数里。

    防的错误：`tmux new-session -d -s ava-probe 'cd ... && python -c 'import torch''`
    —— 嵌套单引号会把命令截断，probe 静默跑错。
    """
    import base64 as _b64

    inner = "python -c 'import torch; print(\"cuda\")' && touch /root/.ava_heartbeat"
    launch = cloud.build_tmux_launch_command("ava-probe", inner)
    assert _b64.b64encode(inner.encode()).decode() in launch, "命令体必须 base64 编码"
    assert "base64 -d >" in launch
    assert "'/root/.ava_task_ava-probe.sh'" in launch, "tmux 执行的是脚本文件，不是内联命令"
    # 核心断言：里头的单引号绝不能出现在 tmux 参数位置上
    assert inner not in launch, "原文不得直接内联，否则引号嵌套破裂"


def test_tmux_launch_requires_ava_prefix():
    """会话名前缀是 watchdog 判活跃的唯一凭证，必须强制。"""
    import pytest as _pytest

    with _pytest.raises(ValueError):
        cloud.build_tmux_launch_command("probe", "echo hi")
    with _pytest.raises(ValueError):
        cloud.build_tmux_launch_command("", "echo hi")


def test_probe_command_creates_log_dir():
    """probe 任务自己建 03-audio 目录，不依赖先 push 原始素材。"""
    cmd = cloud.build_remote_run_command("probe", "data/episodes/A/01", "/root/anime-video-agent")
    assert "mkdir -p data/episodes/A/01/03-audio" in cmd
    assert "torch.cuda.is_available()" in cmd
    assert cmd.count("/root/.ava_heartbeat") == 2, "首尾双重心跳，否则 watchdog 会中途关机"


def test_resolve_episode_rel_path_keeps_symlinked_data_layer():
    """data/ 是指向外置盘的符号链接，解析必须保留中间层级，不能丢企划名那一层。

    防的错误：旧写法用 Path.resolve() 穿透符号链接到 /Volumes/...，
    relative_to(ROOT) 抛 ValueError 后静默回退成 data/episodes/<name>，
    于是远端把 EGOIST-传奇企划志/01-借躯降生 写成了 episodes/01-借躯降生。
    """
    ep, rel = cloud.resolve_episode_rel_path("data/episodes/EGOIST-传奇企划志/01-借躯降生")
    assert rel == "data/episodes/EGOIST-传奇企划志/01-借躯降生", "中间层级（企划名）不得丢失"
    assert "/Volumes/" not in str(ep), "不得穿透 data/ 符号链接"
    assert str(ep).startswith(str(cloud.paths.ROOT))


def test_resolve_episode_rel_path_rejects_outside_repo():
    """仓库外的路径必须显式报错，不能静默猜一个路径出来。"""
    import pytest as _pytest

    with _pytest.raises(ValueError, match="必须位于仓库内"):
        cloud.resolve_episode_rel_path("/tmp/not-in-repo/01")


def test_ledger_note_marks_upper_bound_estimate():
    """实例不可达时，时长只是上界估计，必须在账簿里标注而不是假装精确。

    场景：watchdog 自动关机后本地才发现，down 只能把时长算到「此刻」，
    必然多计一段延迟。旧代码默默写个数字，让账簿看起来精确。
    """
    with_note = cloud.format_ledger_entry(
        session_id="s1", up_time="10:00:00", down_time="10:10:00",
        gpu_seconds=600, cost_cny=0.4, episode="ep", tasks=["probe"],
        budget_cny=0.35, mode="gpu", note="上界估计",
    )
    assert with_note["note"] == "上界估计"
    assert with_note["over_budget"] is True

    without = cloud.format_ledger_entry(
        session_id="s2", up_time="10:00:00", down_time="10:05:00",
        gpu_seconds=300, cost_cny=0.2, episode=None, tasks=[],
        budget_cny=0.35, mode="cardless",
    )
    assert "note" not in without, "无注记时不该凭空多出字段"
    assert without["episode"] == "unknown", "episode 缺失应落 unknown 而不是 None"


def test_detect_runtime_mode_from_gpu_probe():
    """模式探测：nvidia-smi 成功=带卡，失败=无卡。

    防的错误：无会话文件时旧代码一律默认 gpu，若实例实际是无卡则账目虚高 24 倍
    （¥2.40/h vs ¥0.10/h）。
    """
    assert cloud.detect_runtime_mode_from_gpu_probe(0) == "gpu"
    assert cloud.detect_runtime_mode_from_gpu_probe(1) == "cardless"
    assert cloud.detect_runtime_mode_from_gpu_probe(127) == "cardless", "命令不存在（无卡模式）也归 cardless"


# ---------------------------------------------------------------------------
# 8. 模型权重结构校验（任务 0）：体积只证存在，结构才证完整
# ---------------------------------------------------------------------------

import json as _json
import struct as _struct
import subprocess as _subprocess
import sys as _sys


def _make_safetensors(path: Path, tensors: dict | None = None, truncate: int = 0) -> Path:
    """手工构造最小 safetensors：8 字节头长 + header JSON + 数据区。"""
    tensors = tensors or {"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}
    payload = b"\x00" * 8
    hdr = _json.dumps(tensors).encode("utf-8")
    blob = _struct.pack("<Q", len(hdr)) + hdr + payload
    if truncate:
        blob = blob[:-truncate]
    path.write_bytes(blob)
    return path


def _run_struct_probe(root: Path) -> dict:
    """把远端校验脚本拉下来在本地跑一遍（同样的代码、同样的判据）。"""
    script = root / "_probe.py"
    script.write_text(cloud._MODEL_STRUCT_PROBE_SCRIPT, encoding="utf-8")
    res = _subprocess.run(
        [_sys.executable, str(script), str(root)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return _json.loads(res.stdout.strip().splitlines()[-1])


def test_structure_probe_accepts_intact_safetensors(tmp_path):
    """完好的 safetensors 必须判通过——否则新门禁会冤枉好模型。"""
    _make_safetensors(tmp_path / "ok.safetensors")
    out = _run_struct_probe(tmp_path)
    assert out["checked"] == 1
    assert out["bad"] == []


def test_structure_probe_catches_truncated_safetensors(tmp_path):
    """截断的 safetensors 必须被抓住。

    这是本任务存在的**唯一理由**：du 体积在截断下照样可能过阈值，
    而 max(data_offsets) 越出文件末尾是字节级铁证。
    """
    _make_safetensors(tmp_path / "cut.safetensors", truncate=3)
    out = _run_struct_probe(tmp_path)
    assert out["checked"] == 0
    assert len(out["bad"]) == 1 and "字节数不符" in out["bad"][0]


def test_structure_probe_handles_zip_and_bare_pickle(tmp_path):
    """zip 容器查条目越界；裸 pickle 诚实记为 unverifiable（不假装通过也不冤枉）。"""
    import zipfile
    with zipfile.ZipFile(tmp_path / "model.bin", "w") as z:
        z.writestr("data.pkl", b"payload")
    (tmp_path / "legacy.pth").write_bytes(b"\x80\x02" + b"pickle-bytes-here")
    out = _run_struct_probe(tmp_path)
    assert out["checked"] == 1, "zip 容器应通过条目越界检查"
    assert out["bad"] == []
    assert out["unverifiable"] == ["legacy.pth"], "裸 pickle 无完整性信息，不能算通过"


def test_structure_probe_catches_truncated_zip_as_damage_not_unverifiable(tmp_path):
    """被截断的 zip 容器必须报「损坏」，不能报「无法校验」。

    防的错误（由变异检验抓出）：zip 截断后中央目录读不出来，is_zipfile 返回 False，
    若把它一律归入 unverifiable，doctor 看到的就是 WARN「无法静态校验」——
    而下载中断造成的截断正是本项目最真实的失效模式，必须是 FAIL。
    """
    import zipfile
    p = tmp_path / "cut.bin"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("data.pkl", b"x" * 4096)
    p.write_bytes(p.read_bytes()[:20])   # 砍到只剩本地文件头
    out = _run_struct_probe(tmp_path)
    assert out["checked"] == 0
    assert len(out["bad"]) == 1 and "截断" in out["bad"][0]
    assert out["unverifiable"] == [], "截断不得被当成「无法校验」"


def test_structure_probe_flags_garbage_file(tmp_path):
    """既非 zip 也非 pickle 的二进制不能放行。"""
    (tmp_path / "junk.pt").write_bytes(b"NOPE-not-a-real-weight-file")
    out = _run_struct_probe(tmp_path)
    assert out["checked"] == 0 and len(out["bad"]) == 1
    assert "格式异常" in out["bad"][0]


def test_parse_model_structure_result_three_way(tmp_path):
    """OK / WARN / FAIL 必须严格区分：unverifiable 既不能当通过也不能当损坏。"""
    ok = _json.dumps({"files": 3, "checked": 3, "bad": [], "unverifiable": []})
    assert cloud.parse_model_structure_result(ok, 0)[0] == "OK"

    mixed = _json.dumps({"files": 3, "checked": 2, "bad": [], "unverifiable": ["a.pth"]})
    tag, desc = cloud.parse_model_structure_result(mixed, 0)
    assert tag == "OK" and "无法静态校验" in desc

    only_bare = _json.dumps({"files": 1, "checked": 0, "bad": [], "unverifiable": ["a.pth"]})
    assert cloud.parse_model_structure_result(only_bare, 0)[0] == "WARN"

    broken = _json.dumps({"files": 2, "checked": 1, "bad": ["x.safetensors: 字节数不符"], "unverifiable": []})
    tag, desc = cloud.parse_model_structure_result(broken, 0)
    assert tag == "FAIL" and "结构损坏" in desc

    empty = _json.dumps({"files": 0, "checked": 0, "bad": [], "unverifiable": []})
    assert cloud.parse_model_structure_result(empty, 0)[0] == "WARN"


def test_parse_model_structure_result_failure_is_warn_not_fail():
    """脚本跑不起来时判 WARN，不能拉倒整个 doctor。

    防的错误：把「校验机制本身故障」当成「模型损坏」上报，
    用户会去重下 33GB——而问题其实只是远端缺 python。
    """
    assert cloud.parse_model_structure_result("", 1)[0] == "WARN"
    assert cloud.parse_model_structure_result("bash: python: not found", 127)[0] == "WARN"
    assert cloud.parse_model_structure_result("not json at all", 0)[0] == "WARN"


def test_build_model_structure_probe_cmd_is_base64_landed():
    """校验脚本必须 base64 落地再跑：内联会让引号在 shell→ssh→远端 shell 三层间互相切断。"""
    cmd = cloud.build_model_structure_probe_cmd("BAAI/bge-m3")
    assert "base64 -d >" in cmd
    assert "'/root/autodl-tmp/models/BAAI/bge-m3'" in cmd
    assert "json.loads" not in cmd, "脚本正文不得内联，否则引号会切断"


def test_tts_run_uses_cloud_config_overlay():
    """tts 任务必须显式传云端覆盖层配置。

    防的错误：本地 Mac 只有 mlx（qwen3_tts），云端只有 CUDA（indextts2），
    若沿用默认 voice.json，云端会去加载 mlx 引擎并当场崩——
    而报错会指向「模块不存在」，离病根（配置没切）隔了好几层。
    """
    cmd = cloud.build_remote_run_command("tts", "data/episodes/A/01", "/root/anime-video-agent")
    assert "--config config/voice.cloud.json" in cmd

    probe = cloud.build_remote_run_command("probe", "data/episodes/A/01", "/root/anime-video-agent")
    assert "--config" not in probe, "probe 不碰配音配置，不该带这个参数"


class TestLedgerBudgetScope:
    """单期预算的适用范围（2026-09-11 误报修正）。

    事故：一个含 15GB 模型下载 + 两次部署尝试的会话被标「超预算 16 倍」，
    而它压根没在做任何一期。警告天天误报就没人看了——这正是 S1 说的
    「判据测错了对象」在预算维度的翻版。
    """

    def test_未绑定某一期时不判超预算(self):
        e = cloud.format_ledger_entry(
            session_id="s", up_time="10:00:00", down_time="12:00:00",
            gpu_seconds=7200, cost_cny=5.63, episode="unknown", tasks=[],
            budget_cny=0.35, mode="gpu",
        )
        assert e["over_budget"] is False, "没在做某一期，单期预算不适用"
        assert e["budget_scoped"] is False

    def test_绑定某一期时照常判(self):
        e = cloud.format_ledger_entry(
            session_id="s", up_time="10:00:00", down_time="10:20:00",
            gpu_seconds=1200, cost_cny=0.80, episode="01-借躯降生", tasks=["tts"],
            budget_cny=0.35, mode="gpu",
        )
        assert e["over_budget"] is True
        assert e["budget_scoped"] is True

    def test_绑定某一期且未超时(self):
        e = cloud.format_ledger_entry(
            session_id="s", up_time="10:00:00", down_time="10:05:00",
            gpu_seconds=300, cost_cny=0.20, episode="01-借躯降生", tasks=["tts"],
            budget_cny=0.35, mode="gpu",
        )
        assert e["over_budget"] is False
        assert e["budget_scoped"] is True


def test_远端解释器从配置读且可覆盖():
    """解释器路径必须可配：这台实例上模型栈只在 /root/it-venv 里。

    2026-09-11 实测：/root/miniconda3/bin/python 有 torch/torchvision 但没有 transformers，
    而 `run ... tts` 走的就是它 → 任务起来第一行 ImportError，账簿里却只记「任务已启动」。
    """
    assert cloud.remote_python({"remote_python": "/opt/x/bin/python"}) == "/opt/x/bin/python"
    assert cloud.remote_python({}) == cloud.DEFAULT_REMOTE_PYTHON


def test_配置里记着本机实例的解释器():
    cfg, _ = cloud.load_cloud_config()
    assert cfg.get("remote_python"), "云端解释器是实例事实，必须落在 config/cloud.json"


def test_run_命令用配置里的解释器():
    cmd = cloud.build_remote_run_command("tts", "data/episodes/A/01",
                                         "/root/anime-video-agent",
                                         python="/root/it-venv/bin/python")
    assert cmd.startswith("cd /root/anime-video-agent")
    assert "/root/it-venv/bin/python -m pipeline.tts" in cmd


def test_status_查的是同一个_watchdog_脚本():
    """部署与查进程必须指同一个文件，否则 status 永远报「未运行」（2026-09-11 崩溃修复）。"""
    assert cloud.REMOTE_WATCHDOG_PATH == "/root/watchdog.sh"
    assert cloud.REMOTE_WATCHDOG_PATH in cloud.build_watchdog_deploy_command("#!/bin/bash\n")
    assert cloud._watchdog_proc_pattern(cloud.REMOTE_WATCHDOG_PATH) == "^/bin/bash /root/watchdog\\.sh$"


def test_up_会核验声明的模式(monkeypatch):
    """`up --mode gpu` 不能只信参数：2026-09-11 实际开出来是无卡模式，
    账簿按 2.4 元/h 虚记 24 倍，而 GPU 任务在加载权重时被 OOM 杀掉（报错只有一行 Killed）。

    修法：开机后探一次 nvidia-smi，与声明不符时按实测记账并显式 WARN。
    """
    assert cloud.detect_runtime_mode_from_gpu_probe(0) == "gpu"
    assert cloud.detect_runtime_mode_from_gpu_probe(1) == "cardless"
    # 声明 gpu、实测 cardless → 必须改判（否则账簿虚高 24 倍）
    assert cloud.detect_runtime_mode_from_gpu_probe(255) == "cardless"


# ---------------------------------------------------------------------------
# 8. SSH I/O 薄壳的调用点观测（2026-09-16 审计 R1/R2/R3）
#    测试标准允许的唯一例外：monkeypatch subprocess 调用点，观测真实参数。
# ---------------------------------------------------------------------------

import argparse


def _no_config(monkeypatch):
    """cmd_* 入口会读本机 config/cloud*.json——测试不许碰本机配置与凭据。
    D102 起 ssh 参数还取决于本机 data/cloud/ssh_config 在不在，一并指向不存在的路径。"""
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(cloud, "ssh_config_file", lambda: Path("/nonexistent/ava-test/ssh_config"))


def test_attach_列表传参不带shell(monkeypatch):
    """审计 R1：cmd_attach 旧实现是 `subprocess.call(f"ssh -t {host} '…'", shell=True)`。
    本地 shell 在这里没有要展开的任何东西，引号拼接只会把合法的会话名截断。
    变异：改回字符串 + shell=True → 本条红。"""
    _no_config(monkeypatch)
    got = {}
    monkeypatch.setattr(cloud.subprocess, "call",
                        lambda *a, **k: got.update(argv=a[0], kw=k) or 0)
    rc = cloud.cmd_attach(argparse.Namespace(session="ava-probe"))
    assert rc == 0
    assert isinstance(got["argv"], list), "不许再用字符串 + shell=True"
    assert got["kw"].get("shell") is not True
    assert got["argv"] == ["ssh", "-t", "autodl", "tmux attach-session -t ava-probe"]


def test_exec_fg_限时与BatchMode(monkeypatch):
    """审计 R2：docstring 承诺「严格限制 <60s 探针」，旧实现裸 subprocess.run
    无 timeout 无 BatchMode——远端挂死 = 本地永久挂起，公钥失效 = 挂交互。
    变异：去掉 timeout=60 或 BatchMode → 本条红。"""
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    got = {}
    monkeypatch.setattr(
        cloud.subprocess, "run",
        lambda *a, **k: got.update(argv=a[0], kw=k)
        or cloud.subprocess.CompletedProcess(a[0], 0))
    rc = cloud.cmd_exec(argparse.Namespace(command="nvidia-smi", fg=True))
    assert rc == 0
    assert "BatchMode=yes" in got["argv"], "公钥失效必须立刻失败，不许挂交互"
    assert got["kw"].get("timeout") == 60, "承诺的 <60s 探针必须真限时"


def test_exec_fg_超时返回124且指路(monkeypatch, capsys):
    """超时不是崩溃，是「你走错通道了」：返回 124 并指向 tmux 通道。"""
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)

    def _hang(*a, **k):
        raise cloud.subprocess.TimeoutExpired(cmd=a[0], timeout=60)

    monkeypatch.setattr(cloud.subprocess, "run", _hang)
    rc = cloud.cmd_exec(argparse.Namespace(command="sleep 999", fg=True))
    assert rc == 124
    assert "--fg" in capsys.readouterr().err


def test_pull_rsync失败带上原话(monkeypatch, capsys, tmp_path):
    """审计 R3：pull 旧实现把 rsync 非零退出码一律报成「未发现远端产物或跳过」——
    网络闪断与文件缺失混成一句，排查的人会被带偏。跳过消息必须带退出码与
    stderr 尾行。（单项失败仍不拦整批：兜底在尾部的产物验收。）"""
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "resolve_episode_rel_path",
                        lambda t: (tmp_path, "data/episodes/x"))
    monkeypatch.setattr(cloud, "get_active_session_file",
                        lambda: tmp_path / "no-session.json")
    monkeypatch.setattr(cloud, "run_rsync",
                        lambda s, d, **k: cloud.subprocess.CompletedProcess(
                            s, 12, "", "rsync: connection unexpectedly closed"))
    rc = cloud.cmd_pull(argparse.Namespace(target="x"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "退出码 12" in out and "connection unexpectedly closed" in out


def test_captions_task_allowed_and_builds_vindex_command():
    """云端路径连通性（Spec §4.6 R1-r15）：两件事钉在一条测试里。

    ① ALLOWED_TASKS 含 'captions'
    ② build_remote_run_command('captions', ...) 产出的是 python -m pipeline.vindex captions ...
       而非 python -m pipeline.captions。
    """
    assert "captions" in cloud.ALLOWED_TASKS, "ALLOWED_TASKS 必须包含 captions"
    cmd = cloud.build_remote_run_command(
        task="captions",
        ep_rel_path="data/episodes/EGOIST 03",
        remote_root="/root/anime-video-agent",
        extra_args="EGOIST--patch SP01 --batch 2",
    )
    assert "pipeline.vindex captions" in cmd, "captions 任务必须调用 pipeline.vindex captions"
    assert "pipeline.captions" not in cmd, "不可拼出不存在的 pipeline.captions 模块"
    assert "'data/episodes/EGOIST 03/04-patch/shots'" in cmd or "data/episodes/EGOIST\\ 03/04-patch/shots" in cmd
    assert "--shots-dir" in cmd and "--frames-dir" in cmd and "--out-dir" in cmd


def test_remote_active_tasks_and_status_display(monkeypatch, capsys):
    """Spec §4.3 r10：活跃任务提取与 cmd_status 展示。"""
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda h: True)

    class DummyRes:
        returncode = 0
        stdout = "ava-captions: 1 windows (created Mon)\nother-sess: 1 windows\nava-tts: 1 windows\n"
        stderr = ""

    monkeypatch.setattr(cloud, "_ssh", lambda cmd, **k: DummyRes())
    tasks = cloud.remote_active_tasks()
    assert tasks == ["ava-captions", "ava-tts"]

    cloud.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out
    assert "活跃后台任务" in out and "ava-captions" in out


def test_cmd_pull_unreachable_direct_fail(monkeypatch, tmp_path):
    """Spec §4.3 三态可辨 ①：实例不可达/关机时直接 FAIL，不降级为未发现产物。"""
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "resolve_episode_rel_path", lambda t: (tmp_path, "data/episodes/x"))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda h: False)

    with pytest.raises(SystemExit, match="实例不可达/已关机"):
        cloud.cmd_pull(argparse.Namespace(target="x"))



# ---------------------------------------------------------------------------
# 上行余量 pre-flight（Spec §4.6：判据必须落在系统盘，而不是数据盘）
# ---------------------------------------------------------------------------


class TestUploadCapacity:
    """纯函数：上行体积 vs 远端余量三态判定。"""

    def test_余量充足放行(self):
        verdict, detail = cloud.evaluate_upload_capacity(600 * 1024**2, "12")
        assert verdict == "ok"
        assert "1.17GB" in detail and "12.0GB" in detail

    def test_余量不足拦下(self):
        # 550MB 全季帧 / 系统盘只剩 0.5GB —— 正是「doctor 说数据盘充足、
        # 系统盘却在涨」那个盲区对应的场景
        verdict, detail = cloud.evaluate_upload_capacity(550 * 1024**2, "0.5")
        assert verdict == "insufficient"
        assert "仅剩 0.5GB" in detail

    def test_刚好够也判不够(self):
        # 「刚好够」的语义：upload == avail 时 2× 系数必须判拦，
        # 因为 rsync --partial 的临时件会让峰值占用翻倍
        verdict, _ = cloud.evaluate_upload_capacity(1024**3, "1")
        assert verdict == "insufficient"

    def test_读不到余量是unknown不是不足(self):
        # 「不知道」≠「有危险」：不许假装知道，但也不许拿它当借口拦人
        for bad in ("", "   ", "N/A", None):
            verdict, _ = cloud.evaluate_upload_capacity(1024, bad)
            assert verdict == "unknown", f"{bad!r} 应判 unknown"

    def test_零字节上行永远放行(self):
        # 空清单不该因为远端余量读不到而被拦
        verdict, _ = cloud.evaluate_upload_capacity(0, "")
        assert verdict == "unknown" or verdict == "ok"


class TestSyncPairsBytes:
    """估算上行清单体积：文件取大小、目录递归累加。"""

    def test_文件与目录都算进(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_bytes(b"x" * 1000)
        d = tmp_path / "frames"
        (d / "sub").mkdir(parents=True)
        (d / "f1.jpg").write_bytes(b"y" * 500)
        (d / "sub" / "f2.jpg").write_bytes(b"z" * 300)

        assert cloud.sync_pairs_bytes([(str(f), "remote/f")]) == 1000
        assert cloud.sync_pairs_bytes([(str(d) + "/", "remote/d")]) == 800
        assert cloud.sync_pairs_bytes([(str(f), "r"), (str(d) + "/", "r2")]) == 1800

    def test_清单为空返回零(self):
        assert cloud.sync_pairs_bytes([]) == 0


class TestPreflightGate:
    """接线测试：闸真的接在 cmd_push 的上行路径上。"""

    def _push_env(self, monkeypatch, tmp_path, df_out, *, pairs_bytes=550 * 1024**2,
                  rel_path="data/episodes/x"):
        _no_config(monkeypatch)
        monkeypatch.setattr(cloud, "resolve_episode_rel_path",
                            lambda t: (tmp_path, rel_path))
        ep = tmp_path / "02-script.md"
        ep.write_bytes(b"x")
        monkeypatch.setattr(cloud, "sync_pairs_bytes", lambda pairs: pairs_bytes)
        seen = {}

        class _Res:
            returncode = 0
            stdout = df_out
            stderr = ""

        def fake_ssh(cmd, **kw):
            seen.setdefault("cmds", []).append(cmd)
            return _Res()

        monkeypatch.setattr(cloud, "_ssh", fake_ssh)
        monkeypatch.setattr(cloud, "run_rsync", lambda s, d, **k: _Res())
        return seen

    def test_余量不足时拒绝上行(self, monkeypatch, tmp_path):
        seen = self._push_env(monkeypatch, tmp_path, "0.5")
        with pytest.raises(SystemExit) as exc:
            cloud.cmd_push(argparse.Namespace(target="x"))
        msg = str(exc.value)
        assert "远端目标盘余量不足，拒绝上行" in msg
        assert "系统盘" in msg, "报错必须点明上行落在系统盘（这是闸存在的理由）"
        assert "改道数据盘" in msg and "caption-frames" in msg, "两条修法都要给"
        # 拦在 rsync 之前：不许先传一半才发现
        assert not any("rsync" in c for c in seen.get("cmds", []))

    def test_余量充足时放行(self, monkeypatch, tmp_path, capsys):
        self._push_env(monkeypatch, tmp_path, "20")
        rc = cloud.cmd_push(argparse.Namespace(target="x"))
        assert rc == 0
        assert "远端余量充足" in capsys.readouterr().out

    def test_余量读不到时WARN放行(self, monkeypatch, tmp_path, capsys):
        self._push_env(monkeypatch, tmp_path, "")
        rc = cloud.cmd_push(argparse.Namespace(target="x"))
        assert rc == 0
        assert "无法确认远端余量" in capsys.readouterr().err

    def test_mkdir路径过引号防空格中文(self, monkeypatch, tmp_path):
        # 期目录名含空格与中文时，裸插值会让远端 shell 在空格处断开
        # （B7-r5 同族）。用真含空格的 rel_path 考它——无特殊字符时
        # shlex.quote 本就不加引号，那不是漏网。
        seen = self._push_env(monkeypatch, tmp_path, "20",
                              rel_path="data/episodes/EGOIST 三期")
        cloud.cmd_push(argparse.Namespace(target="x"))
        mkdir_cmds = [c for c in seen["cmds"] if c.startswith("mkdir -p")]
        assert mkdir_cmds, "必须下发 mkdir"
        assert "'" in mkdir_cmds[0], f"含空格的路径未经 shlex.quote: {mkdir_cmds[0]}"


# ---------------------------------------------------------------------------
# 9. D102 实例用完即弃：connect / ssh_config / 模型软链 / 租机提示 / 按型号记账
# ---------------------------------------------------------------------------

import json
import os
import subprocess as _sp
import time as _time

REAL_LOGIN = "ssh -p 34557 root@connect.weste.seetacloud.com"
REAL_CONN = {"server": "connect.weste.seetacloud.com", "port": 34557, "user": "root"}


@pytest.mark.parametrize("text", [
    REAL_LOGIN,
    "  ssh   -p   34557    root@connect.weste.seetacloud.com  ",
    "ssh -o StrictHostKeyChecking=no -p 34557 root@connect.weste.seetacloud.com",
    "ssh -p34557 root@connect.weste.seetacloud.com",
    "ssh -l root -p 34557 connect.weste.seetacloud.com",
    "ssh -p 34557 -o ServerAliveInterval=30 root@connect.weste.seetacloud.com",
])
def test_connect解析控制台登录指令及变体(text):
    """控制台原样格式与常见变体（多空格、夹 -o、-p 连写、-l 给用户）都解析成同一组三项。"""
    assert cloud.parse_login_command(text) == REAL_CONN


@pytest.mark.parametrize("text,needle", [
    ("ssh root@connect.weste.seetacloud.com", "缺 port"),           # 缺端口：不补 22
    ("ssh -p 34557 connect.weste.seetacloud.com", "缺 user"),
    ("ssh -p 34557", "恰好一个"),
    ("ssh -p 34557 root@a.com b.com", "恰好一个"),
    ("scp -P 34557 root@a.com", "以 ssh 开头"),
    ("ssh -p 99999 root@a.com", "端口不合法"),
    ("ssh -p 34557 root@a.com;touch${IFS}x", "主机名"),           # 落进 ssh_config 的字符必须干净
    ("ssh -p 34557 'ro ot'@a.com", "用户名"),
    ("ssh -p", "缺值"),
])
def test_connect解析不出就报错并列出认出的部分(text, needle):
    with pytest.raises(ValueError) as ei:
        cloud.parse_login_command(text)
    assert needle in str(ei.value)


def test_connect缺端口时列出已认出的主机与用户():
    with pytest.raises(ValueError) as ei:
        cloud.parse_login_command("ssh root@connect.weste.seetacloud.com")
    assert "connect.weste.seetacloud.com" in str(ei.value) and "root" in str(ei.value)


def test_合并连接三项其余键原样保留():
    local = {"_note": "n", "ssh_host": "autodl", "instance_id": "i-1", "server": "old",
             "port": 1, "user": "u", "api_token": "TOKEN-xyz"}
    out = cloud.merge_local_connection(local, REAL_CONN)
    assert list(out) == list(local), "键顺序不许变"
    assert out["api_token"] == "TOKEN-xyz" and out["instance_id"] == "i-1"
    assert (out["server"], out["port"], out["user"]) == ("connect.weste.seetacloud.com", 34557, "root")
    assert local["server"] == "old", "不许原地改入参"


def test_ssh_config内容(tmp_path):
    text = cloud.render_ssh_config("autodl", REAL_CONN, tmp_path / "known_hosts")
    lines = [ln.strip() for ln in text.splitlines()]
    assert "Host autodl" in lines and "HostName connect.weste.seetacloud.com" in lines
    assert "Port 34557" in lines and "User root" in lines
    assert "StrictHostKeyChecking accept-new" in lines
    assert f'UserKnownHostsFile "{tmp_path / "known_hosts"}"' in lines
    # Include 必须在 Match all 之后、且是最后一行：否则它落进 Host 块，或被放在前面抢走 HostName
    assert lines[-2:] == ["Match all", "Include ~/.ssh/config"]


def _fake_connect_env(monkeypatch, tmp_path, local: dict | None):
    cfg = tmp_path / "config"
    data = tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    if local is not None:
        (cfg / "cloud.local.json").write_text(json.dumps(local), encoding="utf-8")
    (cfg / "cloud.json").write_text(json.dumps({"fs_models": "/root/autodl-fs/models"}), encoding="utf-8")
    monkeypatch.setattr(cloud.paths, "CONFIG", cfg)
    monkeypatch.setattr(cloud.paths, "DATA", data)
    monkeypatch.setattr(cloud.paths, "require_data", lambda: data)
    return cfg, data


def test_cmd_connect写三项生成ssh_config且不回显token(monkeypatch, tmp_path, capsys):
    local = {"ssh_host": "autodl", "server": "old.host", "port": 1, "user": "x", "api_token": "TOKEN-secret-123"}
    cfg, data = _fake_connect_env(monkeypatch, tmp_path, local)
    seen: list[list[str]] = []

    def fake_run(argv, **kw):
        seen.append(argv)
        out = "LINKED\n" if "readlink" in argv[-1] else ""
        return _sp.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(cloud.subprocess, "run", fake_run)
    rc = cloud.cmd_connect(argparse.Namespace(login=REAL_LOGIN))
    assert rc == 0
    got = json.loads((cfg / "cloud.local.json").read_text(encoding="utf-8"))
    assert got == {**local, **REAL_CONN}
    conf = (data / "cloud" / "ssh_config").read_text(encoding="utf-8")
    assert "HostName connect.weste.seetacloud.com" in conf and "Port 34557" in conf
    # 生成之后的 ssh 调用都带 -F 指向它（验连通与建软链两次）
    ssh_calls = [a for a in seen if a[0] == "ssh"]
    assert len(ssh_calls) == 2 and all(a[1:3] == ["-F", str(data / "cloud" / "ssh_config")] for a in ssh_calls)
    out = capsys.readouterr()
    assert "TOKEN-secret-123" not in out.out + out.err
    assert "root@connect.weste.seetacloud.com:34557" in out.out


def test_cmd_connect解析失败不写任何文件(monkeypatch, tmp_path):
    local = {"server": "old.host", "port": 1, "user": "x", "api_token": "T"}
    cfg, data = _fake_connect_env(monkeypatch, tmp_path, local)
    before = (cfg / "cloud.local.json").read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        cloud.cmd_connect(argparse.Namespace(login="ssh root@h.com"))
    assert "缺 port" in str(ei.value)
    assert (cfg / "cloud.local.json").read_text(encoding="utf-8") == before
    assert not (data / "cloud" / "ssh_config").exists()


def test_cmd_connect连不上返回1但连接信息已写(monkeypatch, tmp_path):
    """实例刚开机常常还没起来：写入保留（人贴的就是这台），返回 1 让人过会儿再连。"""
    cfg, data = _fake_connect_env(monkeypatch, tmp_path, {"api_token": "T"})
    monkeypatch.setattr(cloud.subprocess, "run", lambda argv, **kw: _sp.CompletedProcess(argv, 255, "", ""))
    assert cloud.cmd_connect(argparse.Namespace(login=REAL_LOGIN)) == 1
    assert json.loads((cfg / "cloud.local.json").read_text(encoding="utf-8"))["port"] == 34557


def test_connect作废同一主机端口的旧主机密钥(monkeypatch, tmp_path):
    """端口会被回收给别的实例：connect 时只删这个 [主机]:端口 的旧密钥，别的条目不动（真跑 ssh-keygen）。"""
    monkeypatch.setattr(cloud, "known_hosts_file", lambda: tmp_path / "known_hosts")
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    (tmp_path / "known_hosts").write_text(
        f"[connect.weste.seetacloud.com]:34557 {key}\n[connect.weste.seetacloud.com]:40000 {key}\n",
        encoding="utf-8")
    cloud.forget_known_host(REAL_CONN)
    left = (tmp_path / "known_hosts").read_text(encoding="utf-8")
    assert ":34557" not in left and ":40000" in left


def test_ssh_base有配置文件才带F(monkeypatch, tmp_path):
    f = tmp_path / "ssh_config"
    monkeypatch.setattr(cloud, "ssh_config_file", lambda: f)
    assert cloud.ssh_base() == ["ssh"], "文件不存在时退回旧行为（~/.ssh/config 的别名）"
    f.write_text("Host autodl\n", encoding="utf-8")
    assert cloud.ssh_base() == ["ssh", "-F", str(f)]


def test_ssh与rsync与attach都带F(monkeypatch, tmp_path):
    d = tmp_path / "with space"
    d.mkdir()
    f = d / "ssh_config"
    f.write_text("Host autodl\n", encoding="utf-8")
    monkeypatch.setattr(cloud, "ssh_config_file", lambda: f)
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({}, {}))
    runs: list[list[str]] = []
    monkeypatch.setattr(cloud.subprocess, "run",
                        lambda argv, **kw: runs.append(argv) or _sp.CompletedProcess(argv, 0, "", ""))
    calls: list[list[str]] = []
    monkeypatch.setattr(cloud.subprocess, "call", lambda argv, **kw: calls.append(argv) or 0)

    cloud._ssh("true")
    assert runs[-1][:3] == ["ssh", "-F", str(f)]
    cloud.run_rsync("a", "autodl:b")
    e = runs[-1][runs[-1].index("-e") + 1]
    assert e == f"ssh -F '{f}'", "路径带空格必须加引号，rsync 才不会拆开"
    cloud.cmd_attach(argparse.Namespace(session="ava-tts"))
    cloud.cmd_logs(argparse.Namespace(session="ava-tts", lines=5, follow=True))
    assert all(c[:3] == ["ssh", "-F", str(f)] for c in calls) and len(calls) == 2


def test_up连不上时打印租机四项(monkeypatch, capsys):
    _no_config(monkeypatch)
    cfg = {"region": "西北B区", "image": "ava-py312-torch28-cu128-20261015"}
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: (cfg, {}))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: False)
    with pytest.raises(SystemExit):
        cloud.cmd_up(argparse.Namespace(mode="gpu", timeout=0, episode=None))
    out = capsys.readouterr().out
    assert "西北B区" in out and "显存" in out and "CUDA：≥ 12.8" in out
    assert "ava-py312-torch28-cu128-20261015" in out and "cloud connect" in out


def _bash(script: str) -> str:
    return _sp.run(["bash", "-c", script], capture_output=True, text=True, check=False).stdout.strip()


def test_模型软链脚本六种情形(tmp_path):
    """真跑脚本：建、幂等、改指、实体目录不动、文件存储缺、同名文件。"""
    fs = tmp_path / "autodl-fs" / "models"
    tmp = tmp_path / "autodl-tmp" / "models"
    fs.mkdir(parents=True)
    script = cloud.models_link_script(str(fs), str(tmp))
    assert _bash(script) == "LINKED" and tmp.is_symlink() and Path(os.readlink(tmp)) == fs
    assert _bash(script) == "ALREADY"
    tmp.unlink()
    other = tmp_path / "other"
    other.mkdir()
    tmp.symlink_to(other)
    assert _bash(script) == "RELINKED" and Path(os.readlink(tmp)) == fs
    tmp.unlink()
    tmp.mkdir()
    (tmp / "keep").write_text("x")
    assert _bash(script) == "REALDIR" and (tmp / "keep").exists() and not (tmp / "models").exists()
    (tmp / "keep").unlink()
    tmp.rmdir()
    tmp.write_text("x")
    assert _bash(script) == "CONFLICT"
    tmp.unlink()
    fs.rmdir()
    assert _bash(script) == "NOFS" and not tmp.exists()


def test_模型软链只读版不动远端(tmp_path):
    fs = tmp_path / "fs"
    tmp = tmp_path / "tmp" / "models"
    fs.mkdir()
    ro = cloud.models_link_script(str(fs), str(tmp), create=False)
    assert _bash(ro) == "MISSING" and not tmp.exists() and not tmp.is_symlink()
    tmp.parent.mkdir()
    tmp.symlink_to(tmp_path)
    assert _bash(ro) == "ELSEWHERE" and Path(os.readlink(tmp)) == tmp_path


@pytest.mark.parametrize("word,tag,ok", [
    ("ALREADY", "OK", True), ("LINKED", "OK", True), ("RELINKED", "OK", True),
    ("REALDIR", "INFO", True), ("NOFS", "FAIL", False), ("CONFLICT", "FAIL", False),
    ("MISSING", "FAIL", False), ("ELSEWHERE", "FAIL", False), ("", "FAIL", False),
])
def test_模型软链结论解读(word, tag, ok):
    t, _desc, passed = cloud.evaluate_models_link(word + "\n", "/fs", "/tmp/models")
    assert (t, passed) == (tag, ok)


def _remote_manifest(stale: bool = False) -> str:
    m = cloud.code_manifest(cloud.paths.ROOT)
    if stale:
        m["pipeline/vindex.py"] = "0" * 64
        m.pop("pipeline/shots.py")
    return json.dumps(m)


def _doctor_fake(gpu="NVIDIA vGPU-32GB, 32760", mount="MOUNTED", link="ALREADY", code_stale=False):
    def fake(cmd, host="autodl", check=True, timeout=60, capture_output=True):
        if cmd == "true":
            out = ""
        elif ".ava_code_manifest.py" in cmd:
            out = _remote_manifest(code_stale)
        elif cmd.startswith("nvidia-smi"):
            out = gpu
        elif "git rev-parse" in cmd:
            out = "abc1234"
        elif "import torch" in cmd:
            out = "2.8.0___True"
        elif cmd.startswith("df "):
            out = "40"
        elif cmd == cloud.FS_MOUNT_CHECK_CMD:
            out = mount
        elif "readlink" in cmd:
            assert "ln -s" not in cmd, "doctor 只读，不许建软链"
            out = link
        else:
            raise AssertionError(f"未预期的远端命令: {cmd}")
        return _sp.CompletedProcess(cmd, 0, out + "\n", "")
    return fake


@pytest.mark.parametrize("kw,rc,needle", [
    ({}, 0, "OK    [FS]"),
    ({"mount": "NOT_MOUNTED"}, 1, "FAIL  [FS]"),
    ({"link": "MISSING"}, 1, "FAIL  [LINK]"),
    ({"link": "REALDIR"}, 0, "INFO  [LINK]"),
    ({"gpu": "NVIDIA GeForce RTX 4080, 16376"}, 0, "WARN  [GPU] NVIDIA GeForce RTX 4080"),
    ({}, 0, "OK    [CODE] 远端代码与本地一致"),
    ({"code_stale": True}, 1, "FAIL  [CODE] 远端代码与本地不一致：缺 1 个、内容不同 1 个"),
])
def test_doctor三项新检查(monkeypatch, capsys, kw, rc, needle):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({"models": {}}, {}))
    monkeypatch.setattr(cloud, "_ssh", _doctor_fake(**kw))
    assert cloud.cmd_doctor(argparse.Namespace()) == rc
    assert needle in capsys.readouterr().out


@pytest.mark.parametrize("name,rate,est", [
    ("NVIDIA vGPU-32GB", 1.58, False),
    ("nvidia vgpu-32gb", 1.58, False),               # 大小写不同
    ("NVIDIA GeForce RTX 4090D", 1.9, False),        # 多个键命中取最长：4090D 不被 4090 抢走
    ("NVIDIA GeForce RTX 4090", 1.6, False),
    ("NVIDIA H800", 2.4, True),                      # 表里没有 → 兜底单价，标估算
    (None, 2.4, True),
])
def test_按型号查单价(name, rate, est):
    rates = {"_note": "x", "vGPU-32GB": 1.58, "RTX 4090": 1.6, "RTX 4090D": 1.9}
    assert cloud.lookup_gpu_rate(name, rates, 2.4) == (rate, est)


def test_会话单价分来源():
    """D108：只有人报的单价可信；按型号查到的参考价、兜底价都标估算。"""
    cfg = {"cardless_rate_cny": 0.1, "hourly_rate_cny": 2.4, "gpu_rates_cny": {"vGPU-32GB": 1.58}}
    assert cloud.session_rate(cfg, "cardless", "NVIDIA vGPU-32GB") == (0.1, None)
    assert cloud.session_rate(cfg, "gpu", "NVIDIA GeForce RTX 4080 SUPER", 1.58) == (1.58, None)
    rate, note = cloud.session_rate(cfg, "gpu", "NVIDIA vGPU-32GB")
    assert rate == 1.58 and "单价估算" in note and "参考价" in note
    rate, note = cloud.session_rate(cfg, "gpu", "NVIDIA GeForce RTX 4080 SUPER")
    assert rate == 2.4 and "查不到" in note
    rate, note = cloud.session_rate(cfg, "unknown", None)
    assert rate == 2.4 and note, "模式不可知取带卡价"


@pytest.mark.parametrize("v", [0, -1, 51, 158])
def test_人报单价不合理就拒(v):
    with pytest.raises(SystemExit, match="不像按量时价"):
        cloud.check_rate(v)


def _down_once(monkeypatch, tmp_path, session: dict, rate_arg=None) -> dict:
    _no_config(monkeypatch)
    cfg = {"hourly_rate_cny": 2.4, "gpu_rates_cny": {"vGPU-32GB": 1.58}}
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: (cfg, {}))
    sfile = tmp_path / "active_session.json"
    sfile.write_text(json.dumps({"session": "s1", "mode": "gpu", "up": "10:00:00",
                                 "up_epoch": _time.time() - 3600, "tasks": ["tts"], "episode": None,
                                 **session}), encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: sfile)
    monkeypatch.setattr(cloud, "get_ledger_file", lambda: ledger)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: False)
    assert cloud.cmd_down(argparse.Namespace(force=False, rate=rate_arg)) == 0
    return json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])


@pytest.mark.parametrize("session,rate_arg,rate,estimated", [
    ({"gpu": "NVIDIA GeForce RTX 4080 SUPER", "rate_cny": 1.58}, None, 1.58, False),   # 10-17 那台：up 时报了
    ({"gpu": "NVIDIA GeForce RTX 4080 SUPER"}, 1.58, 1.58, False),                     # 结算时补报
    ({"gpu": "NVIDIA GeForce RTX 4080 SUPER", "rate_cny": 2.0}, 1.58, 1.58, False),    # 补报的优先
    ({"gpu": "NVIDIA vGPU-32GB"}, None, 1.58, True),                                   # 只查到参考价：估算
    ({"gpu": "NVIDIA GeForce RTX 4080 SUPER"}, None, 2.4, True),                       # 什么都没有：兜底估算
])
def test_down账本记型号与单价(monkeypatch, tmp_path, session, rate_arg, rate, estimated):
    entry = _down_once(monkeypatch, tmp_path, session, rate_arg)
    assert entry["gpu"] == session["gpu"] and entry["rate_cny"] == rate
    assert abs(entry["cny"] - rate) < 0.01, "一小时就是一个单价"
    assert ("单价估算" in entry.get("note", "")) is estimated


def test_up把型号写进会话文件并建软链(monkeypatch, tmp_path, capsys):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    sfile = tmp_path / "active_session.json"
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: sfile)
    seen: list[str] = []

    def fake(cmd, host="autodl", check=True, timeout=60, capture_output=True):
        seen.append(cmd)
        if cmd == "nvidia-smi -L":
            out = "GPU 0: NVIDIA vGPU-32GB"
        elif cmd == cloud.GPU_QUERY_CMD:
            out = "NVIDIA vGPU-32GB, 32760"
        elif "readlink" in cmd:
            out = "LINKED"
        else:
            out = "4321"  # watchdog 部署回 pid
        return _sp.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(cloud, "_ssh", fake)
    assert cloud.cmd_up(argparse.Namespace(mode="gpu", timeout=0, episode=None, rate=1.58)) == 0
    data = json.loads(sfile.read_text(encoding="utf-8"))
    assert data["gpu"] == "NVIDIA vGPU-32GB" and data["gpu_mem_mib"] == 32760
    assert data["rate_cny"] == 1.58 and "人报" in data["rate_source"]
    assert any("ln -s" in c for c in seen), "up 要建软链（connect 之外的第二个入口）"

    sfile.unlink()
    assert cloud.cmd_up(argparse.Namespace(mode="gpu", timeout=0, episode=None, rate=None)) == 0
    assert "rate_cny" not in json.loads(sfile.read_text(encoding="utf-8"))
    assert "没报单价" in capsys.readouterr().err


def _fake_up_ssh(gpu: bool, uptime: str = "4321.0 100.0"):
    def fake(cmd, host="autodl", check=True, timeout=60, capture_output=True):
        if cmd == "nvidia-smi -L":
            return _sp.CompletedProcess(cmd, 0 if gpu else 9, "GPU 0: NVIDIA GeForce RTX 4080\n", "")
        if cmd == cloud.GPU_QUERY_CMD:
            return _sp.CompletedProcess(cmd, 0, "NVIDIA GeForce RTX 4080, 32760\n", "")
        if cmd == "cat /proc/uptime":
            return _sp.CompletedProcess(cmd, 0, uptime + "\n", "")
        return _sp.CompletedProcess(cmd, 0, ("LINKED" if "readlink" in cmd else "4321") + "\n", "")
    return fake


@pytest.mark.parametrize("declared,gpu,kept", [
    (None, True, True),          # 2026-10-17 原样：up --rate 不带 --mode，实测带卡 → 记
    ("gpu", True, True),
    ("cardless", True, False),   # 明确声明了别的模式：这个价可能不是这台的，不记
    (None, False, False),        # 无卡是平台固定价
])
def test_up单价跟实测模式走_D108(monkeypatch, tmp_path, capsys, declared, gpu, kept):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    sfile = tmp_path / "active_session.json"
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: sfile)
    monkeypatch.setattr(cloud, "_ssh", _fake_up_ssh(gpu))
    args = cloud.build_parser().parse_args(["up", "--rate", "2.78"] + (["--mode", declared] if declared else []))
    assert cloud.cmd_up(args) == 0
    data = json.loads(sfile.read_text(encoding="utf-8"))
    assert data["mode"] == ("gpu" if gpu else "cardless")
    assert (data.get("rate_cny") == 2.78) is kept
    assert ("mode_corrected" in data) is (declared is not None and declared != data["mode"])


def test_resumable_session_D128():
    prev = {"session": "s", "up_epoch": 1000.0, "target": "root@h:1"}
    assert cloud.resumable_session(prev, "root@h:1", 900.0)                 # 会话开始前就开着机
    assert cloud.resumable_session(prev, "root@h:1", 1020.0)                # ssh 往返几秒的误差
    assert not cloud.resumable_session(prev, "root@h:1", 1400.0)            # 会话开始后重启过
    assert not cloud.resumable_session(prev, "root@h:2", 900.0)             # 换了一台
    assert not cloud.resumable_session(prev, "root@h:1", None)              # uptime 读不出
    assert not cloud.resumable_session({"session": "s", "target": "root@h:1"}, "root@h:1", 900.0)
    assert not cloud.resumable_session(None, "root@h:1", 900.0)
    assert cloud.connection_target({"server": "h", "port": 1}) == "root@h:1"
    assert cloud.connection_target({}) is None


def test_同一次开机再跑up沿用会话与单价_D128(monkeypatch, tmp_path, capsys):
    """2026-10-20：up --rate 1.58 开出 091844，一分钟后又一次不带 --rate 的 up 开出 091944，结算回退 2.4。"""
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "load_cloud_config",
                        lambda *a, **k: ({}, {"server": "connect.westb.seetacloud.com", "port": 51175}))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    sfile = tmp_path / "active_session.json"
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: sfile)
    monkeypatch.setattr(cloud, "_ssh", _fake_up_ssh(True, uptime="50.0 10.0"))
    clock = [1_000_000.0]
    monkeypatch.setattr(cloud.time, "time", lambda: clock[0])
    assert cloud.cmd_up(cloud.build_parser().parse_args(["up", "--rate", "1.58"])) == 0
    first = json.loads(sfile.read_text(encoding="utf-8"))
    sfile.write_text(json.dumps({**first, "tasks": ["captions"]}), encoding="utf-8")

    clock[0] += 60
    monkeypatch.setattr(cloud, "_ssh", _fake_up_ssh(True, uptime="110.0 10.0"))  # 同一次开机
    assert cloud.cmd_up(cloud.build_parser().parse_args(["up"])) == 0
    again = json.loads(sfile.read_text(encoding="utf-8"))
    assert again["session"] == first["session"] and again["up_epoch"] == first["up_epoch"]
    assert again["rate_cny"] == 1.58 and again["tasks"] == ["captions"]
    assert "没报单价" not in capsys.readouterr().err

    clock[0] += 600
    monkeypatch.setattr(cloud, "_ssh", _fake_up_ssh(True, uptime="20.0 10.0"))   # 重启过
    assert cloud.cmd_up(cloud.build_parser().parse_args(["up"])) == 0
    fresh = json.loads(sfile.read_text(encoding="utf-8"))
    assert fresh["up_epoch"] == clock[0] and "rate_cny" not in fresh and fresh["tasks"] == []
    assert "没有 down 结算就被替换" in capsys.readouterr().err


def test_parse_gpu_query():
    assert cloud.parse_gpu_query("NVIDIA vGPU-32GB, 32760\n") == ("NVIDIA vGPU-32GB", 32760.0)
    assert cloud.parse_gpu_query("49152\n") == (None, 49152.0)
    assert cloud.parse_gpu_query("") == (None, None)
    assert cloud.parse_gpu_query("garbage") == (None, None)


def test_connect进白名单且逐条人审():
    """Code Freeze 第 ④ 条例外：写 config/，每次弹卡、不开放「本会话都批准」（与另三条同一口径）。"""
    from pipeline.agent.session import CLOUD_CARD_SUBCOMMANDS, CONFIG_WRITE_COMMANDS
    from pipeline.agent.tools import ASSET_COMMANDS, HUMAN_GATED_COMMANDS, validate_pipeline_command

    assert "connect" in ASSET_COMMANDS["cloud"]
    assert ("cloud", "connect") in CONFIG_WRITE_COMMANDS <= HUMAN_GATED_COMMANDS
    assert "connect" not in CLOUD_CARD_SUBCOMMANDS, "放进去就会被当成可「本会话都批准」的计费卡"
    ok, msg, argv = validate_pipeline_command(f'python -m pipeline.cloud connect "{REAL_LOGIN}"')
    assert ok, msg
    assert argv[-2:] == ["connect", REAL_LOGIN]


def test_bash跑connect也弹卡():
    from pipeline.agent.status_card import bash_danger_tags

    assert "[须人审的流水线命令]" in bash_danger_tags(f'python -m pipeline.cloud connect "{REAL_LOGIN}"')


# ---------- D107：远端代码一致性 ----------


def test_代码清单本地与远端脚本同一算法(tmp_path):
    """远端跑的是同一段脚本：真起一个子进程跑它，结果必须与本地 code_manifest 逐键相等。"""
    root = tmp_path / "repo"
    (root / "pipeline" / "agent").mkdir(parents=True)
    (root / "pipeline" / "__pycache__").mkdir()
    (root / "config" / "agent").mkdir(parents=True)
    (root / "pipeline" / "a.py").write_text("x = 1\n")
    (root / "pipeline" / "agent" / "b.py").write_text("y = 2\n")
    (root / "pipeline" / "__pycache__" / "a.cpython-312.py").write_text("junk")
    (root / "config" / "project.json").write_text("{}")
    (root / "config" / "agent" / "web.json").write_text("{}")
    (root / "config" / "cloud.local.json").write_text('{"api_token": "secret"}')
    (root / "pyproject.toml").write_text("[project]\n")
    (root / "README.md").write_text("不比")
    script = tmp_path / "m.py"
    script.write_text(cloud._CODE_MANIFEST_SCRIPT, encoding="utf-8")
    remote = json.loads(_sp.run([_sys.executable, str(script), str(root)], capture_output=True,
                                text=True, check=True).stdout)
    local = cloud.code_manifest(root)
    assert remote == local
    assert sorted(local) == ["config/agent/web.json", "config/project.json", "pipeline/a.py",
                             "pipeline/agent/b.py", "pyproject.toml"]


def test_代码清单比对():
    d = cloud.compare_code_manifest({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "X", "old": "9"})
    assert d == {"missing": ["c"], "differ": ["b"], "extra": ["old"]}
    ok, lines = cloud.code_drift_lines({"missing": [], "differ": [], "extra": ["old"]})
    assert ok and "旧文件" in lines[0]
    ok, lines = cloud.code_drift_lines("远端清单没算出来")
    assert not ok and "核对不了" in lines[0]


def test_run远端代码旧了不开跑(monkeypatch, tmp_path):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "resolve_episode_rel_path", lambda t: (tmp_path, "data/episodes/x"))
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({}, {}))
    launched: list[str] = []

    def fake(cmd, host="autodl", check=True, timeout=60, capture_output=True):
        if ".ava_code_manifest.py" in cmd:
            return _sp.CompletedProcess(cmd, 0, _remote_manifest(stale=True) + "\n", "")
        launched.append(cmd)
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cloud, "_ssh", fake)
    with pytest.raises(SystemExit, match="没开跑：远端代码与本地不一致") as exc:
        cloud.cmd_run(argparse.Namespace(task="tts", target=tmp_path, extra=[]))
    assert "sync-code" in str(exc.value)
    assert launched == [], "不一致时一条远端命令都不许发（心跳、tmux 都不行）"


def test_sync_code推三项且有任务在跑就拒(monkeypatch):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({"remote_root": "/r"}, {}))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host: ["ava-tts"])
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(cloud, "run_rsync",
                        lambda src, dst, extra_excludes=None: pushed.append((src, dst)) or _sp.CompletedProcess([], 0, "", ""))
    with pytest.raises(SystemExit, match="有任务在跑"):
        cloud.cmd_sync_code(argparse.Namespace())
    assert pushed == []

    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host: [])
    monkeypatch.setattr(cloud, "_ssh", lambda cmd, **kw: _sp.CompletedProcess(cmd, 0, _remote_manifest() + "\n", ""))
    assert cloud.cmd_sync_code(argparse.Namespace()) == 0
    assert [d.split(":", 1)[1] for _, d in pushed] == ["/r/pipeline/", "/r/config/", "/r/pyproject.toml"]
    assert pushed[0][0].endswith("pipeline/") and pushed[2][0].endswith("pyproject.toml")


def test_sync_code是计费卡():
    from pipeline.agent.session import CLOUD_CARD_SUBCOMMANDS
    from pipeline.agent.tools import validate_pipeline_command

    assert "sync-code" in CLOUD_CARD_SUBCOMMANDS
    assert validate_pipeline_command("python -m pipeline.cloud sync-code")[0]


# ---------- D103 ②：番池素材云端打标 ----------

LIB_DIRS = ("data/library/shots", "data/library/shots/frames", "data/library/vindex")


def test_lib路径取自shots与vindex常量():
    assert cloud.lib_dirs() == LIB_DIRS


@pytest.mark.parametrize("pool,keys,want", [
    ("supercell", ["SP01", "SP02,SP03", "SP01"], ["SP01", "SP02", "SP03"]),
    ("罪恶王冠", ["S01E01"], ["S01E01"]),
])
def test_lib目标校验_放行(pool, keys, want):
    assert cloud.validate_lib_target(pool, keys) == want


@pytest.mark.parametrize("pool,keys", [
    ("../x", ["SP01"]), ("a b", ["SP01"]), ("-rf", ["SP01"]), ("", ["SP01"]), ("..", ["SP01"]),
    ("supercell", ["SP1"]), ("supercell", ["SP01;rm"]), ("supercell", [","]),
])
def test_lib目标校验_拒收(pool, keys):
    with pytest.raises(ValueError):
        cloud.validate_lib_target(pool, keys)


def _lib_tree(root: Path, pool: str, keys: list[str], *, frames: bool = True) -> None:
    for k in keys:
        f = root / LIB_DIRS[0] / f"{pool}_{k}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
        if frames:
            cap = root / LIB_DIRS[1] / f"{pool}_{k}_cap"
            cap.mkdir(parents=True, exist_ok=True)
            (cap / "00001_0.jpg").write_bytes(b"x")


def test_lib上行清单与缺输入(tmp_path):
    _lib_tree(tmp_path, "超级池", ["SP01"])
    _lib_tree(tmp_path, "超级池", ["SP02"], frames=False)
    assert cloud.lib_missing_inputs("超级池", ["SP01"], LIB_DIRS, tmp_path) == []
    miss = cloud.lib_missing_inputs("超级池", ["SP01", "SP02", "SP03"], LIB_DIRS, tmp_path)
    assert len(miss) == 3 and "SP02：没有打标帧" in miss[0] and miss[1].startswith("SP03：没有镜头表")
    pairs = cloud.build_lib_up_pairs("超级池", ["SP01"], "/r", LIB_DIRS, tmp_path)
    assert pairs == [
        (str(tmp_path / "data/library/shots/超级池_SP01.json"), "/r/data/library/shots/超级池_SP01.json"),
        (f"{tmp_path / 'data/library/shots/frames/超级池_SP01_cap'}/", "/r/data/library/shots/frames/超级池_SP01_cap"),
    ]


def test_lib打标命令():
    cmd = cloud.build_lib_captions_command("超级池", ["SP01", "SP02"], "/r", "/py", batch=2, confirm_cost=True)
    assert "/py -m pipeline.vindex captions '超级池' SP01 SP02 --batch 2 --confirm-cost" in cmd
    assert "--shots-dir" not in cmd, "番池用 vindex 默认目录，与本地同一相对路径"
    assert cmd.count(cloud.WATCHDOG_HEARTBEAT_PATH) == 2


def _lib_env(monkeypatch, tmp_path, *, active=(), stale=False):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud.paths, "ROOT", tmp_path)
    monkeypatch.setattr(cloud.paths, "require_data", lambda *a, **k: None)
    monkeypatch.setattr(cloud, "lib_dirs", lambda: LIB_DIRS)
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({"remote_root": "/r"}, {}))
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host="autodl": list(active))
    monkeypatch.setattr(cloud, "remote_code_drift",
                        lambda host, cfg: {"missing": [], "differ": ["pipeline/vindex.py"] if stale else [], "extra": []})
    sent: list[str] = []
    monkeypatch.setattr(cloud, "_ssh", lambda cmd, **kw: sent.append(cmd) or _sp.CompletedProcess(cmd, 0, "40\n", ""))
    rsyncs: list[tuple[str, str]] = []
    monkeypatch.setattr(cloud, "run_rsync",
                        lambda src, dst, extra_excludes=None: rsyncs.append((src, dst)) or _sp.CompletedProcess([], 0, "", ""))
    sfile = tmp_path / "active_session.json"
    sfile.write_text(json.dumps({"session": "s", "mode": "gpu", "tasks": []}), encoding="utf-8")
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: sfile)
    return sent, rsyncs, sfile


def test_lib_push缺输入整批拒_一条都不传(monkeypatch, tmp_path):
    sent, rsyncs, _ = _lib_env(monkeypatch, tmp_path)
    _lib_tree(tmp_path, "supercell", ["SP01"])
    _lib_tree(tmp_path, "supercell", ["SP02"], frames=False)
    with pytest.raises(SystemExit, match="SP02：没有打标帧"):
        cloud.cmd_lib_push(argparse.Namespace(pool="supercell", keys=["SP01", "SP02"]))
    assert rsyncs == [] and sent == []
    with pytest.raises(SystemExit, match="池名写错了"):
        cloud.cmd_lib_push(argparse.Namespace(pool="supercel", keys=["SP01"]))
    assert cloud.cmd_lib_push(argparse.Namespace(pool="supercell", keys=["SP01"])) == 0
    assert [d for _, d in rsyncs] == ["autodl:/r/data/library/shots/supercell_SP01.json",
                                      "autodl:/r/data/library/shots/frames/supercell_SP01_cap"]
    assert any(c.startswith("mkdir -p") for c in sent)


def test_lib_captions代码旧了或上一批没跑完都不开跑(monkeypatch, tmp_path):
    ns = argparse.Namespace(pool="supercell", keys=["SP01"], batch=None, limit=None, confirm_cost=False)
    sent, _, _ = _lib_env(monkeypatch, tmp_path, stale=True)
    with pytest.raises(SystemExit, match="没开跑：远端代码与本地不一致"):
        cloud.cmd_lib_captions(ns)
    assert sent == []
    sent, _, _ = _lib_env(monkeypatch, tmp_path, active=[cloud.LIB_CAPTIONS_SESSION])
    with pytest.raises(SystemExit, match="还在跑"):
        cloud.cmd_lib_captions(ns)
    assert sent == []
    sent, _, sfile = _lib_env(monkeypatch, tmp_path)
    assert cloud.cmd_lib_captions(ns) == 0
    assert any("tmux new-session -d -s ava-lib-captions" in c for c in sent)
    data = json.loads(sfile.read_text(encoding="utf-8"))
    assert data["tasks"] == ["captions"] and data["episode"] == "library:supercell"


def test_lib_pull只拉写明的集_缺的报错(monkeypatch, tmp_path):
    _, rsyncs, _ = _lib_env(monkeypatch, tmp_path)

    def rsync(src, dst, extra_excludes=None):
        rsyncs.append((src, dst))
        rc = 23 if src.endswith("SP02.captions.json") else 0
        return _sp.CompletedProcess([], rc, "", "rsync: link_stat failed" if rc else "")

    monkeypatch.setattr(cloud, "run_rsync", rsync)
    assert cloud.cmd_lib_pull(argparse.Namespace(pool="supercell", keys=["SP01", "SP02"])) == 1
    assert [s.split(":", 1)[1] for s, _ in rsyncs] == [
        "/r/data/library/vindex/supercell_SP01.captions.json", "/r/data/library/vindex/supercell_SP02.captions.json"]
    assert all("*" not in s for s, _ in rsyncs), "不按通配整池拉"


def test_lib_pull打标还在跑就不拉(monkeypatch, tmp_path):
    _, rsyncs, _ = _lib_env(monkeypatch, tmp_path, active=[cloud.LIB_CAPTIONS_SESSION])
    with pytest.raises(SystemExit, match="半份产物"):
        cloud.cmd_lib_pull(argparse.Namespace(pool="supercell", keys=["SP01"]))
    assert rsyncs == []


def test_lib三命令进白名单_打标是计费卡():
    from pipeline.agent.session import CLOUD_CARD_SUBCOMMANDS
    from pipeline.agent.tools import validate_pipeline_command

    assert "lib-captions" in CLOUD_CARD_SUBCOMMANDS
    assert not {"lib-push", "lib-pull"} & CLOUD_CARD_SUBCOMMANDS
    for c in ("lib-push supercell SP01", "lib-captions supercell SP01 SP02 --batch 2", "lib-pull supercell SP01"):
        ok, msg, _ = validate_pipeline_command(f"python -m pipeline.cloud {c}")
        assert ok, msg


_D131_ENTRY = {"session": "20261023_202528", "mode": "gpu", "up": "20:25:28", "down": "21:27:49",
               "gpu_seconds": 3741, "cny": 2.494, "episode": "02-烟火绽放的至美之日", "tasks": ["tts"],
               "over_budget": True, "budget_scoped": True, "gpu": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
               "rate_cny": 2.4,
               "note": "实例在结算前已停止响应（多为 watchdog 自动关机）；gpu_seconds 为上界估计，含关机到本地发现之间的延迟；"
                       "单价估算：没有人报单价，gpu_rates_cny 里也查不到 NVIDIA RTX PRO 6000 Blackwell Server Edition，"
                       "按 hourly_rate_cny 记"}


def _down_no_session(monkeypatch, tmp_path, ledger_rows, rate_arg, reachable=False):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "load_cloud_config", lambda *a, **k: ({"hourly_rate_cny": 2.4}, {}))
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger_rows), encoding="utf-8")
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: tmp_path / "active_session.json")
    monkeypatch.setattr(cloud, "get_ledger_file", lambda: ledger)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: reachable)
    monkeypatch.setattr(cloud, "_ssh", lambda *a, **k: _sp.CompletedProcess("x", 1, "", ""))
    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host=None: "busy")
    rc = cloud.cmd_down(argparse.Namespace(force=False, rate=rate_arg))
    return rc, [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]


def test_d131_结算后补报单价_改写那一条不另开会话(monkeypatch, tmp_path):
    """2026-10-23 原样：up 没报价 → 人在控制台关机 → down 按 2.4 估算 → 人报 6.98 再跑 down --rate。"""
    older = {**_D131_ENTRY, "session": "20261020_231740", "rate_cny": 6.98, "note": None}
    rc, rows = _down_no_session(monkeypatch, tmp_path, [older, _D131_ENTRY], 6.98)
    assert rc == 0 and len(rows) == 2, "不许再记一条 0 秒的 manual 会话"
    assert rows[0] == older
    e = rows[1]
    assert e["rate_cny"] == 6.98 and e["cny"] == pytest.approx(7.2534) and e["gpu_seconds"] == 3741
    assert "单价估算" not in e["note"] and "watchdog" in e["note"]
    assert "人事后补报" in e["note"] and "原按 2.4 元/h 估算 ¥2.494" in e["note"]


def test_d131_最后一条已是人报单价_拒绝不改(monkeypatch, tmp_path):
    reported = {**_D131_ENTRY, "rate_cny": 6.98, "cny": 7.2534, "note": None}
    with pytest.raises(SystemExit, match="不是估算值，不改"):
        _down_no_session(monkeypatch, tmp_path, [reported], 5.0)
    assert json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8")) == reported


def test_d131_实例还连得上_是手动开的机_照旧新结算一条(monkeypatch, tmp_path):
    rc, rows = _down_no_session(monkeypatch, tmp_path, [_D131_ENTRY], 6.98, reachable=True)
    assert rc == 0 and len(rows) == 2 and rows[0] == _D131_ENTRY
    assert rows[1]["session"].startswith("manual_") and rows[1]["rate_cny"] == 6.98


# ---------------------------------------------------------------------------
# D130：cloud hold / release（配完不关机，2 h 自动释放）
# ---------------------------------------------------------------------------


def _bash(script: str) -> str:
    return _sp.run(["bash", "-c", script], capture_output=True, text=True, check=False).stdout.strip()


def test_d130_看门狗在tmux检查之后_空闲关机之前看保持标记():
    script = cloud.generate_watchdog_script(hold_path="/root/.ava_hold")
    loop = script[script.index("while true"):]
    assert loop.index("grep -q '^ava-'") < loop.index("if hold_active; then") < loop.index("IDLE_LIMIT")
    assert 'touch "$HEARTBEAT_FILE"\n        continue' in loop[loop.index("if hold_active"):]
    assert script.index("hold_active() {") < script.index("while true"), "函数要在主循环之前定义"


@pytest.mark.parametrize("content,held", [
    (lambda now: f"{now - 60} {now + 600}\n", True),       # 未到期
    (lambda now: f"{now - 7200} {now - 1}\n", False),      # 已过期：2 h 自动释放
    (lambda now: "垃圾内容\n", False),                     # 写坏了按没有处理，照常关机
    (lambda now: f"{now} \n", False),                      # 缺到期字段
    (None, False),                                         # 没有标记
])
def test_d130_保持判定用真bash跑(tmp_path, content, held):
    f = tmp_path / "hold"
    if content is not None:
        f.write_text(content(int(_time.time())), encoding="utf-8")
    done = _sp.run(["bash", "-c", cloud.watchdog_hold_function(str(f)) + "\nhold_active && echo HELD || echo FREE"],
                   capture_output=True, text=True, check=False)
    assert done.stdout.strip() == ("HELD" if held else "FREE")
    assert done.stderr == "", "看门狗每 20 秒判一次，写坏的标记不许往 watchdog.log 里刷报错"


def test_d130_hold续期保留开始时间_过期后重新起算(tmp_path):
    f = tmp_path / "hold"
    first = cloud.parse_hold(_bash(cloud.hold_write_command(7200, hold_path=str(f))))
    assert first is not None and first[1] - first[2] == 7200 and first[0] == first[2]
    now = int(_time.time())
    f.write_text(f"{now - 3000} {now + 100}\n", encoding="utf-8")
    renewed = cloud.parse_hold(_bash(cloud.hold_write_command(7200, hold_path=str(f))))
    assert renewed[0] == now - 3000 and renewed[1] - renewed[2] == 7200
    f.write_text(f"{now - 9000} {now - 10}\n", encoding="utf-8")
    restarted = cloud.parse_hold(_bash(cloud.hold_write_command(7200, hold_path=str(f))))
    assert restarted[0] >= now, "过期的标记不算续期"


def test_d130_status一行():
    assert "未保持" in cloud.hold_status_line(None, 6.98)
    line = cloud.hold_status_line((1000, 1000 + 7200, 1000 + 3600), 6.0)
    assert "剩余 60 分钟" in line and "已保持 60 分钟" in line and "¥6.00" in line
    expired = cloud.hold_status_line((1000, 1000 + 7200, 1000 + 7200 + 600), 6.0)
    assert "已过期 10 分钟" in expired and "共保持 120 分钟" in expired and "¥12.00" in expired
    assert cloud.parse_hold("") is None and cloud.parse_hold("1792839155") is None


def _hold_ssh(seen: list[str], watchdog_new: bool, now: int = 1_792_839_155):
    def fake(cmd, host="autodl", check=True, timeout=60, capture_output=True):
        seen.append(cmd)
        if cmd.startswith("grep -q hold_active"):
            return _sp.CompletedProcess(cmd, 0 if watchdog_new else 1, "", "")
        if "AVA_WD_EOF" in cmd:
            return _sp.CompletedProcess(cmd, 0, "4321\n", "")
        if cmd.startswith("now=$(date +%s)"):
            return _sp.CompletedProcess(cmd, 0, f"{now} {now + 7200}\n{now}\n", "")
        return _sp.CompletedProcess(cmd, 0, "", "")
    return fake


@pytest.mark.parametrize("watchdog_new", [True, False])
def test_d130_hold写标记_旧看门狗先换新(monkeypatch, capsys, watchdog_new):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    seen: list[str] = []
    monkeypatch.setattr(cloud, "_ssh", _hold_ssh(seen, watchdog_new))
    assert cloud.cmd_hold(argparse.Namespace()) == 0
    redeployed = any("AVA_WD_EOF" in c for c in seen)
    assert redeployed is (not watchdog_new)
    if redeployed:
        deploy = next(c for c in seen if "AVA_WD_EOF" in c)
        assert "hold_active" in deploy and seen.index(deploy) < next(
            i for i, c in enumerate(seen) if c.startswith("now=$(date +%s)"))
    assert "120 分钟后自动释放" in capsys.readouterr().out


def test_d130_hold_旧看门狗换不掉就不写标记(monkeypatch):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    seen: list[str] = []
    base = _hold_ssh(seen, watchdog_new=False)
    monkeypatch.setattr(cloud, "_ssh", lambda cmd, **k: _sp.CompletedProcess(cmd, 255, "", "")
                        if "AVA_WD_EOF" in cmd else base(cmd, **k))
    with pytest.raises(SystemExit, match="不写标记"):
        cloud.cmd_hold(argparse.Namespace())
    assert not any(c.startswith("now=$(date +%s)") for c in seen)


def test_d130_实例联系不上_hold拒绝(monkeypatch):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: False)
    with pytest.raises(SystemExit, match="联系不上"):
        cloud.cmd_hold(argparse.Namespace())


def test_d130_release与down都撤标记(monkeypatch, tmp_path):
    _no_config(monkeypatch)
    monkeypatch.setattr(cloud, "is_ssh_reachable", lambda host: True)
    seen: list[str] = []
    monkeypatch.setattr(cloud, "_ssh", _hold_ssh(seen, True))
    assert cloud.cmd_release(argparse.Namespace()) == 0
    assert f"rm -f {cloud.REMOTE_HOLD_PATH}" in seen

    seen.clear()
    monkeypatch.setattr(cloud, "get_active_session_file", lambda: tmp_path / "none.json")
    monkeypatch.setattr(cloud, "get_ledger_file", lambda: tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cloud, "remote_active_tasks", lambda host=None: ["ava-tts"])   # 有任务：结算后不关机
    assert cloud.cmd_down(argparse.Namespace(force=False, rate=None)) == 0
    assert f"rm -f {cloud.REMOTE_HOLD_PATH}" in seen


def test_d130_hold与release在命令表里():
    ap = cloud.build_parser()
    assert ap.parse_args(["hold"]).cmd == "hold" and ap.parse_args(["release"]).cmd == "release"


def test_d130_写标记命令遇到写坏的旧标记_重新起算且不报错(tmp_path):
    f = tmp_path / "hold"
    now = int(_time.time())
    for bad in (f"{now - 50} \n", "垃圾\n", f"x {now + 100}\n"):
        f.write_text(bad, encoding="utf-8")
        done = _sp.run(["bash", "-c", cloud.hold_write_command(7200, hold_path=str(f))],
                       capture_output=True, text=True, check=False)
        got = cloud.parse_hold(done.stdout)
        assert done.stderr == "" and got[0] >= now and got[1] - got[2] == 7200, bad
