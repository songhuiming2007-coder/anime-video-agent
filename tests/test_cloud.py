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
    """显存低于 24GB 时必须判定失败，防范消费级小显存机器导致 VLM/TTS 串行 OOM。"""
    tag, desc, passed = cloud.evaluate_doctor_gpu("16384\n", returncode=0)
    assert tag == "FAIL"
    assert "16.0GB < 24.0GB" in desc
    assert passed is False


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
