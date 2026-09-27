"""ava 统一 CLI 宿主、REPL 交互与工序调度器（Spec §2.3, §2.4, §2.6）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from pipeline import paths
from pipeline.agent.resolver import scope_of
from pipeline.agent.scopes import get_scopes_dir, load_scope
from pipeline.agent.session import (
    CRITICAL_TOOLS,
    AgentSession,
    HumanAnswer,
    HumanRequest,
    SessionHost,
    prepare_resume,
    prompt_for,
    review_tool_call,
)
from pipeline.agent.session_log import (
    LOG_NAME,
    EpisodeLease,
    SessionLocked,
    SessionLogBroken,
    list_sessions,
    read_log,
    resume_target,
)
from pipeline.agent.status_card import (
    build_status_card,
    log_approval_decision,
    render_approval_card,
)
from pipeline.agent.tools import resolve_episode_dir, run_pipeline
from pipeline.approvals import HUMAN_STOPS, human_stop_of
from pipeline.status import EpisodeStatus, format_status, inspect_episode

# 选期与子命令共用的关键词唯一真源（Spec §2.4）
IDEA_KEYWORD = "idea"


def check_code_freeze() -> bool:
    """检查 pipeline/ 源码是否存在未提交改动（Spec §2.4 Code Freeze 护栏）。

    git 命令失败或有修改时打印 WARN 横幅，绝不静默放行（B4-r6）。
    """
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--", "pipeline/"],
            cwd=paths.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0 or res.stdout.strip():
            print("\n" + "=" * 68, file=sys.stderr)
            print("⚠️  [CODE FREEZE WARN] pipeline/ 源码存在未提交改动或 git 检查异常！", file=sys.stderr)
            print("   制片流水线应处于代码冻结保护状态，严禁私自修改 pipeline 源码。", file=sys.stderr)
            if res.stdout.strip():
                print("   改动文件清单:\n" + res.stdout.strip(), file=sys.stderr)
            print("=" * 68 + "\n", file=sys.stderr)
            return False
        return True
    except Exception as exc:
        print(f"\n⚠️  [CODE FREEZE WARN] 无法检查 git 状态: {exc}\n", file=sys.stderr)
        return False


def record_human_time(ep_dir: Path, stop: str, entered_at: float, left_at: float,
                      *, source: str | None = None) -> float:
    """记录人类停机点墙钟时间（Spec §2.6 追加式写入 human_time.json），返回本段分钟数。

    `source`（Spec 11 §3.2）：桌面端条目传 `"desktop"`，终端条目不传——下游消费
    只读 `minutes`/`stop`，形状兼容；不传时落盘字节与从前完全一致。
    """
    # Spec §7.4 / §4: scout 条目小于 0.1 分钟（6 秒）噪音过滤不落盘（连敲等噪音）
    if stop == "scout" and (left_at - entered_at) / 60.0 < 0.1:
        return 0.0

    minutes = round((left_at - entered_at) / 60.0, 2)
    ht_path = ep_dir / "human_time.json"
    records: list[dict] = []
    if ht_path.exists():
        try:
            records = json.loads(ht_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []

    entry = {
        "stop": stop,
        "entered_at": datetime.fromtimestamp(entered_at).isoformat(),
        "left_at": datetime.fromtimestamp(left_at).isoformat(),
        "minutes": max(0.0, minutes),
    }
    if source:
        entry["source"] = source
    records.append(entry)
    paths.atomic_write(ht_path, json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    return max(0.0, minutes)


def park_stop_of(current_step: str) -> str | None:
    """REPL 停留记账用的停机点：03.5 的墙钟由 /voice 自己记，此处排除以免双记。"""
    stop = human_stop_of(current_step)
    return None if stop == "03.5" else stop


def _print_pending_approvals(
    ep_dir: Path,
    displayed_ids: dict[str, str] | None = None,
) -> None:
    """打印当期挂起的停机点审批对象（REPL 与裸形态 /approvals 共用）。"""
    from pipeline import approvals

    pendings = approvals.list_pending(ep_dir)
    if not pendings:
        print("[approvals] 当前无挂起的停机点审批对象。")
        return
    for item in pendings:
        if displayed_ids is not None:
            displayed_ids[item.type] = item.approval_id
        arts = ", ".join(a.path for a in item.artifacts) or "无"
        opts = "/".join(item.options)
        note_suffix = f" | {item.note}" if item.note else ""
        print(
            f"[pending] {item.approval_id} | 停机点: {item.type} | "
            f"创建: {item.created_at} | 产物: {arts} | 可选项: {opts}{note_suffix}"
        )


def _sync_repl_displayed_approval(
    ep_dir: Path,
    status: EpisodeStatus,
    displayed_ids: dict[str, str],
) -> None:
    """REPL 每轮比对 ensure_pending 返回值与本会话已展示 id，打印更新提示（Spec 3 §4.2 R-1）。"""
    from pipeline import approvals

    ensured = approvals.ensure_pending(ep_dir, status)
    if ensured is None:
        return
    old_id = displayed_ids.get(ensured.type)
    if old_id is None:
        print(f"[approvals] {ensured.type} 待审批已更新：{ensured.approval_id}")
        displayed_ids[ensured.type] = ensured.approval_id
    elif old_id != ensured.approval_id:
        print(
            f"[approvals] {ensured.type} 待审批已更新：{ensured.approval_id}"
            f"（产物已变更，旧 {old_id} 作废）"
        )
        displayed_ids[ensured.type] = ensured.approval_id


def _resolve_repl_approval_id(
    stop: str,
    explicit_id: str | None,
    displayed_ids: dict[str, str],
) -> str:
    """REPL 不带 --id 时只做 stop -> 本会话最近展示过 id 的映射，不判断对象状态（Spec 3 §4.2 R-1 / R7-1）。"""
    from pipeline.approvals import ApprovalError

    if explicit_id is not None:
        return explicit_id
    resolved = displayed_ids.get(stop)
    if resolved is None:
        raise ApprovalError(f"停机点 {stop} 尚未在本会话展示，请先 /approvals")
    return resolved


def _consume_one_shlex_token(text: str) -> tuple[str, str]:
    """从 text 开头按 POSIX shlex 规则取一个 token，并返回 (token, 剩余整行原文)。"""
    s = text.lstrip(" \t")
    if not s:
        raise ValueError("missing token")
    i = 0
    n = len(s)
    in_single = False
    in_double = False
    escaped = False
    while i < n:
        ch = s[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\" and not in_single:
            escaped = True
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch in (" ", "\t") and not in_single and not in_double:
            break
        i += 1
    if in_single or in_double or escaped:
        raise ValueError("unclosed quote or escape")
    raw_token = s[:i]
    parsed = shlex.split(raw_token)
    if len(parsed) != 1:
        raise ValueError("invalid token")
    rest = s[i:].lstrip(" \t")
    return (parsed[0], rest)


def _parse_repl_approve(line: str) -> tuple[str, str | None, dict[str, str] | None]:
    """解析 REPL `/approve <stop> [--id <approval_id>] [--cover <路径> --title <标题原文>]`。

    `--cover` / `--title` 仅 09 接受、两个都要给（S3-R12）；`--cover` 后取 shlex 单 token
    （含空格的路径用引号包住），`--title` 之后取整行原文（同 C.3 的 problem 口径）。
    """
    cmd, rest = _consume_one_shlex_token(line)
    if cmd != "/approve":
        raise ValueError("invalid command")
    stop, rest = _consume_one_shlex_token(rest)
    if stop not in HUMAN_STOPS:
        raise ValueError("invalid stop")

    approval_id: str | None = None
    if rest:
        token, after = _consume_one_shlex_token(rest)
        if token == "--id":
            approval_id, rest = _consume_one_shlex_token(after)
            if not approval_id.strip():
                raise ValueError("empty approval_id")

    finalize: dict[str, str] | None = None
    if rest:
        token, after = _consume_one_shlex_token(rest)
        if token != "--cover" or stop != "09":
            raise ValueError("invalid /approve syntax")
        cover, rest = _consume_one_shlex_token(after)
        if not cover.strip():
            raise ValueError("empty cover")
        token, title = _consume_one_shlex_token(rest)
        if token != "--title" or not title.strip():
            raise ValueError("missing --title")
        finalize = {"cover": cover, "title": title}
    return (stop, approval_id, finalize)


def _parse_repl_reject(line: str) -> tuple[str, str | None, str, str]:
    """解析 REPL `/reject <stop> [--id <approval_id>] <哪段> <问题…>`（Spec 3 §4.2 C.3）。

    --id 仅在 stop 之后的固定位置识别；target 用 shlex 取一个 token，
    problem 取 target 之后的整行原文（不压缩空格、不剥引号）。
    """
    shlex.split(line)
    cmd, rest = _consume_one_shlex_token(line)
    if cmd != "/reject":
        raise ValueError("invalid command")
    stop, rest = _consume_one_shlex_token(rest)
    if stop not in HUMAN_STOPS:
        raise ValueError("invalid stop")
    approval_id: str | None = None
    next_tok, after_next = _consume_one_shlex_token(rest)
    if next_tok == "--id":
        approval_id, rest = _consume_one_shlex_token(after_next)
        if not approval_id.strip():
            raise ValueError("empty approval_id")
        target, problem = _consume_one_shlex_token(rest)
    else:
        target, problem = next_tok, after_next
    if not target.strip() or not problem.strip():
        raise ValueError("empty target or problem")
    return (stop, approval_id, target, problem)


def _handle_repl_approve(
    ep_dir: Path,
    line: str,
    displayed_ids: dict[str, str],
) -> int:
    """执行 REPL `/approve` 命令，返回契约状态码（0=成功，1=ApprovalError，2=用法错误）。"""
    from pipeline import approvals
    from pipeline.approvals import ApprovalError

    try:
        stop, explicit_id, finalize = _parse_repl_approve(line)
    except ValueError:
        print(
            "[ERROR] 用法: /approve <02.5|03.5|05|09> [--id <approval_id>] "
            "[--cover <07-cover 相对路径> --title <标题>]（cover/title 仅 09，两个都要给）",
            file=sys.stderr,
        )
        return 2

    try:
        target_id = _resolve_repl_approval_id(stop, explicit_id, displayed_ids)
        res = approvals.approve(
            ep_dir, stop, approval_id=target_id, source="repl", finalize=finalize  # type: ignore[arg-type]
        )
        print(f"[OK] 已批准 {res.type} ({res.approval_id})")
        return 0
    except ApprovalError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def _handle_repl_reject(
    ep_dir: Path,
    line: str,
    displayed_ids: dict[str, str],
) -> int:
    """执行 REPL `/reject` 命令，返回契约状态码（0=成功，1=ApprovalError，2=用法错误）。"""
    from pipeline import approvals
    from pipeline.approvals import ApprovalError

    try:
        stop, explicit_id, target, problem = _parse_repl_reject(line)
    except ValueError:
        print(
            "[ERROR] 用法: /reject <02.5|03.5|05|09> [--id <approval_id>] <哪段> <问题...>",
            file=sys.stderr,
        )
        return 2

    try:
        target_id = _resolve_repl_approval_id(stop, explicit_id, displayed_ids)
        res = approvals.reject(
            ep_dir,
            stop,  # type: ignore[arg-type]
            {"target": target, "problem": problem},
            approval_id=target_id,
            source="repl",
        )
        print(f"[OK] 已驳回 {res.type} ({res.approval_id}) → _agent/approval_feedback.md")
        return 0
    except ApprovalError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def run_voice_session(ep_dir: Path) -> int:
    """03.5 停机点墙钟记账包装（Spec §2.6）。

    顺听正常走完才记账：起不来（没稿）或中途异常退出的默认不写，避免留下假读数。
    """
    if not (ep_dir / "02-script.md").exists():
        return run_voice_loop(ep_dir)
    started = time.time()
    code = run_voice_loop(ep_dir)
    minutes = record_human_time(ep_dir, "03.5", started, time.time())
    print(f"[人时] 停机点 03.5 墙钟 {minutes:.1f} 分钟 → human_time.json")
    return code


def get_episodes_list(root: Path | None = None) -> tuple[list[Path], int]:
    """获取所有期目录，排除 '.' 与 '_' 前缀，按 mtime 降序排列（Spec §2.3）。

    root 可注入，便于测试与 LLM 工具（list_episodes）复用同一份扫描逻辑。
    """
    ep_root = (root or paths.ROOT) / "data" / "episodes"
    if not ep_root.exists():
        return [], 0

    visible: list[Path] = []
    hidden_underscore = 0

    for d in ep_root.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        if d.name.startswith("_"):
            hidden_underscore += 1
            continue

        # 若子目录包含子期目录（如 EGOIST-传奇企划志-V2/03-终局葬礼），展平纳入
        sub_eps = [
            sd for sd in d.iterdir()
            if sd.is_dir() and not sd.name.startswith((".", "_")) and (sd / "01-topic.md").exists()
        ]
        if sub_eps:
            visible.extend(sub_eps)
            hidden_underscore += len([
                sd for sd in d.iterdir()
                if sd.is_dir() and sd.name.startswith("_")
            ])
        else:
            visible.append(d)

    visible.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return visible, hidden_underscore


def print_board(episodes: list[Path], hidden_count: int = 0) -> None:
    """打印根目录看板（Spec §2.3 看板与人时读数）。"""
    print("\n" + "=" * 70)
    print("  anime-video-agent-ava 统一制片工作台 · 期看板")
    print("=" * 70)
    if not episodes:
        print("  [INFO] 暂无期目录。可用 'ava new <期号>' 创建新期。")
        print("=" * 70)
        return

    for i, ep in enumerate(episodes, 1):
        status = inspect_episode(ep)
        line = f" [{i:2d}] {ep.name:<24} | {status.current_step}"
        if status.is_blocked:
            line += " [🛑 停机点]"

        # 人时读数
        ht_file = ep / "human_time.json"
        ht_info = ""
        if ht_file.exists():
            try:
                ht_data = json.loads(ht_file.read_text(encoding="utf-8"))
                if isinstance(ht_data, list):
                    tot_m = sum(r.get("minutes", 0.0) for r in ht_data if isinstance(r, dict))
                    ht_info = f" | 人时: {tot_m:.1f}m"
            except Exception:
                pass
        line += ht_info
        print(line)

        # 打印告警
        for adv in status.advisories:
            print(f"      ⚠️  {adv}")

    if hidden_count > 0:
        print(f"\n  (已隐藏 {hidden_count} 个下划线验证/临时目录)")

    print("=" * 70)


def select_episode_interactive(episodes: list[Path]) -> Path | str | None:
    """交互选择期目录（Spec §2.3 输入语义：回车=1，数字=序号，字符串=期名，idea=选题会话）。"""
    if not episodes:
        return None

    while True:
        prompt = f"请选择期目录 [回车默认选 1: {episodes[0].name}, {IDEA_KEYWORD}=选题会话]: "
        try:
            choice = input(prompt).strip()
        except EOFError:
            return None

        if not choice:
            return episodes[0]

        if choice == IDEA_KEYWORD:
            return IDEA_KEYWORD

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(episodes):
                return episodes[idx - 1]
            print(f"[ERROR] 输入序号超限 (1..{len(episodes)})，请重新输入。")
            continue

        # 尝试匹配名称
        matched = [ep for ep in episodes if ep.name == choice or choice in ep.name]
        if len(matched) == 1:
            return matched[0]
        elif len(matched) > 1:
            print(f"[ERROR] 存在多个包含 '{choice}' 的期，请输入精确全名或序号。")
            continue

        print(f"[ERROR] 未找到匹配 '{choice}' 的期，请重新输入。")


_RE_EP_NAME_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _episode_name_problem(ep_name: str) -> str | None:
    r"""期名不合规的具体原因，合规返回 None（Spec 10 C10-R1 §3.7 第 2 步）。

    每条都有来历：`.`/`_` 前缀会被 `get_episodes_list` 藏起来（建了看不见）；
    `-` 开头会与 `ava --continue` 这类开关歧义；`/`、`\`、NUL 与控制字符
    会让 `mkdir` 建到期根之外或不存在的路径；255 字节是 APFS 单段名上限。
    """
    if not ep_name or ep_name != ep_name.strip():
        return "不许为空或首尾带空白"
    if ep_name in (".", ".."):
        return f"不许是 {ep_name!r}"
    if ep_name[0] in "._":
        return f"不许以 {ep_name[0]!r} 开头（期列表会把它藏起来）"
    if ep_name.startswith("-"):
        return "不许以 '-' 开头（会与命令行开关混淆）"
    if any(ch in ep_name for ch in ("/", "\\", "\0")):
        return "不许含 '/'、'\\' 或 NUL"
    ctrl = _RE_EP_NAME_CTRL.search(ep_name)
    if ctrl:
        return f"不许含控制字符（U+{ord(ctrl.group()):04X}）"
    if len(ep_name.encode("utf-8")) > 255:
        return "过长（UTF-8 超过 255 字节，APFS 单段名上限）"
    return None


def create_new_episode(ep_name: str) -> int:
    """创建新期目录与 01-topic.md 模板（Spec 10 C10-R1 §3.7；Spec §5 PR0/PR1 B3-r9）。

    **检查与写入同源**：`data` 在调用时取 `paths.ROOT / "data"`，所以检查落在
    即将写入的那个 `data/` 上（曾用 import 时算好的 `paths.DATA`，测试替换
    `paths.ROOT` 后检查跑到真实盘、写入落在临时根，甚至建到期根之外）。
    `data/` 不可达或 `data/episodes/` 缺席一律退 2、**绝不创建**
    （AGENTS.md 五：脚本绝不自动创建 `data/`）。退出码：0 成功 / 1 已存在 / 2 不合规。
    """
    data = paths.ROOT / "data"
    try:
        paths.require_data_at(data)
    except SystemExit as e:
        print(e.code if isinstance(e.code, str) else str(e), file=sys.stderr)
        return 2
    ep_root = data / "episodes"
    if not ep_root.is_dir():
        print(f"FAIL {ep_root} 不存在：先跑 ./pipeline/preflight.sh --init", file=sys.stderr)
        return 2

    problem = _episode_name_problem(ep_name)
    if problem is not None:
        print(f"[ERROR] 期名不合规：{problem}", file=sys.stderr)
        return 2

    target_dir = ep_root / ep_name
    if target_dir.exists():
        print(f"[ERROR] 期目录已存在：{target_dir}，拒绝覆盖！", file=sys.stderr)
        return 1

    target_dir.mkdir(parents=False, exist_ok=False)
    if target_dir.resolve().parent != ep_root.resolve():
        # 纵深防御：名字规则已挡住分隔符与符号链接，正常不可达
        target_dir.rmdir()
        print(f"[ERROR] 期目录会落在 {ep_root} 之外，已删除：{target_dir}", file=sys.stderr)
        return 2

    topic_content = (
        f"# {ep_name} 选题配置\n\n"
        "番：\n"
        "类型：\n"
        "模式：\n"
        "张力：\n"
        "锚点：\n"
    )
    topic_file = target_dir / "01-topic.md"
    paths.atomic_write(topic_file, topic_content)
    print(f"[OK] 已立项新期：{target_dir}")
    print(f"     已生成初始选题模板：{topic_file}")
    return 0


# /voice 顺听指令表（全部 fullmatch，认小数段号，Spec §3.1）
RE_PLAY_SEG = re.compile(r"^听\s*(\d+(?:\.\d+)?)$")
RE_STOP = re.compile(r"^停$")
RE_PLAY_ALL = re.compile(r"^听$")
RE_REVERT = re.compile(r"^回滚\s*(\d+(?:\.\d+)?)$")
RE_RETRACT = re.compile(r"^撤回\s*(\d+)$")
RE_DONE = re.compile(r"^/?done$")


class VoicePlayer:
    """后台顺序播放器，支持随时终止整个播放序列（Spec §3.1）。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._stop_requested = False
        self._thread: threading.Thread | None = None

    def play_all(self, wav_files: list[Path]) -> None:
        self.stop()
        self._stop_requested = False

        def _run() -> None:
            for wav in wav_files:
                if self._stop_requested:
                    break
                if not wav.exists():
                    continue
                cmd = ["afplay", str(wav)] if sys.platform == "darwin" else ["aplay", str(wav)]
                try:
                    self._proc = subprocess.Popen(cmd)
                    self._proc.wait()
                except Exception:
                    break
                finally:
                    self._proc = None

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        self._thread = None


def run_voice_loop(ep_dir: Path) -> int:
    """顺听极简纠错交互闭环（Spec §3.1）。"""
    from pipeline import corrections, g2p, tts

    script_path = ep_dir / "02-script.md"
    if not script_path.exists():
        print(f"[ERROR] 找不到稿件：{script_path}", file=sys.stderr)
        return 1

    segs = tts.parse_script(script_path)
    audio_dir = ep_dir / "03-audio"

    # 1. 打印段号清单（label -> seg-NN.wav 映射表，Y4）
    print(f"\n[*] 顺听纠错模式已就绪：{ep_dir.name}")
    print("--------------------------------------------------")
    print("段落与音频映射表：")
    label_to_file: dict[str, Path] = {}
    total_duration = 0.0
    for s in segs:
        wav_file = audio_dir / f"seg-{s.index:02d}.wav"
        label_to_file[str(s.label)] = wav_file
        dur_str = ""
        if wav_file.exists():
            try:
                dur = tts.probe_duration(wav_file)
                total_duration += dur
                dur_str = f" ({dur:.1f}s)"
            except Exception:
                pass
        print(f"  段 {s.label:<4} -> {wav_file.name}{dur_str}")
    print("--------------------------------------------------")

    # 2. 打印 g2p.scan_heteronyms 预检清单
    full_text = "\n".join(s.text for s in segs)
    hetero = g2p.scan_heteronyms(full_text)
    if hetero:
        print(f"多音字预检提示（共 {len(hetero)} 处，仅供关注，非错误）：")
        for h in hetero[:10]:
            print(f"  · 字 '{h['char']}' 候选: {', '.join(h.get('readings', [])[:3])}")
        if len(hetero) > 10:
            print(f"  · ... 另有 {len(hetero) - 10} 处多音字")
        print("--------------------------------------------------")

    print("顺听指令:")
    print("  听            顺序播放全量音频序列（输入 '停' 可随时终止）")
    print("  听 <段号>     QuickTime 打开该段音频（例如：听 5）")
    print("  停            停止当前音频播放序列")
    print("  回滚 <段号>   恢复该段上一版音频（例如：回滚 5）")
    print("  撤回 <id>     删除指定纠错条目（例如：撤回 2）")
    print("  done          退出纠错并应用补丁 (--apply-patch)")
    print("  /quit         退出纠错模式\n")

    player = VoicePlayer()

    try:
        while True:
            try:
                line = input(f"ava [{ep_dir.name}] (/voice) > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[退出 /voice 模式]")
                player.stop()
                return 0

            if not line:
                continue

            if line in ("/quit", "/exit", "quit", "exit"):
                player.stop()
                print("[退出 /voice 模式]")
                return 0

            # 路由优先级（写死）：先全串匹配内建指令表，不中才进纠错解析器（Spec §3.1）
            if RE_STOP.fullmatch(line):
                player.stop()
                print("[*] 已停止顺听播放序列。")
                continue

            m_play_seg = RE_PLAY_SEG.fullmatch(line)
            if m_play_seg:
                target_label = m_play_seg.group(1)
                wav_path = label_to_file.get(target_label)
                if not wav_path or not wav_path.exists():
                    print(f"[ERROR] 段 {target_label} 的音频文件不存在 ({wav_path})")
                    continue
                print(f"[*] 正在打开段 {target_label} ({wav_path.name}) ...")
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "-a", "QuickTime Player", str(wav_path)])
                else:
                    subprocess.Popen(["xdg-open", str(wav_path)])
                continue

            if RE_PLAY_ALL.fullmatch(line):
                print(f"[*] 开始顺序播放全量音频序列（预计总时长: {total_duration:.1f}s / {total_duration/60:.1f}分钟）。")
                print("    输入 '停' 可随时终止播放。")
                wav_list = [label_to_file[str(s.label)] for s in segs if label_to_file[str(s.label)].exists()]
                player.play_all(wav_list)
                continue

            m_rev = RE_REVERT.fullmatch(line)
            if m_rev:
                player.stop()
                target_label = m_rev.group(1)
                try:
                    corrections.revert_segment(ep_dir, target_label)
                except SystemExit as ex:
                    print(f"{ex}")
                continue

            m_ret = RE_RETRACT.fullmatch(line)
            if m_ret:
                player.stop()
                target_id = int(m_ret.group(1))
                try:
                    corrections.retract_correction(ep_dir, target_id)
                except SystemExit as ex:
                    print(f"{ex}")
                continue

            if RE_DONE.fullmatch(line):
                player.stop()
                print("[*] 退出纠错录入，准备应用补丁...")
                entries, _ = corrections.load_corrections_raw(ep_dir)
                pending = [c for c in entries if not c.get("applied", False)]
                if not pending:
                    print("[*] 当前没有待应用的纠错条目。")
                    return 0

                mf_path = audio_dir / "manifest.json"
                if mf_path.exists():
                    try:
                        mf = json.loads(mf_path.read_text(encoding="utf-8"))
                        eng = mf.get("engine", "")
                        if "cuda" in eng:
                            print(f"[*] 提示：本期配音引擎为云端引擎 ({eng})，请去云端执行 apply-patch。")
                    except Exception:
                        pass

                try:
                    confirm = input("是否立即执行增量重配 (--apply-patch)? [Y/n]: ").strip().lower()
                except EOFError:
                    # Spec 11 §2.7 / RF17-C1（Spec 9 RF-17 的处置）：EOF 一律取「否」，
                    # 与其余确认类 EOF 默认否的口径一致。TTY 下 EOF 只在 Ctrl-D 时发生。
                    print("[CANCEL] 未获确认，未执行增量重配。")
                    return 0

                if confirm in ("", "y", "yes"):
                    tts.run(ep_dir, apply_patch=True)
                return 0

            # 未命中内建指令表，送入纠错文法解析器
            try:
                patch = corrections.parse_correction(line, segs)
            except corrections.PatchError as pe:
                print(f"[ERROR] {pe}")
                continue

            print("\n--------------------------------------------------")
            if patch.action == "inject":
                print(f"[纠错补丁] 段落: {patch.segment}  范围: {patch.scope}")
                print(f"  目标词:   {patch.word}")
                print(f"  听成:     {patch.heard or '（未指定）'}")
                print(f"  目标读音: {patch.target_tone3}")
            else:
                print(f"[听感补丁] 段落: {patch.segment}  范围: {patch.scope}")
                print(f"  听感现象: {patch.issue}")
                print(f"  调整动作: 钉新种子 (pin_seed)")
            if patch.scope == "global":
                print("  ⚠️  【全局生效】该拼音注入将影响全期所有包含该词的段落！")
            print("--------------------------------------------------")

            try:
                confirm = input("确认落盘? [y/N]: ").strip().lower()
            except EOFError:
                confirm = "n"

            if confirm == "y":
                try:
                    entry = corrections.append_correction(ep_dir, patch)
                    act_desc = f"pin_seed ({entry.get('seed_pin')})" if entry['action'] == 'pin_seed' else f"inject ({entry.get('target_tone3')})"
                    print(f"[OK] 已落盘 #{entry['id']} 段{entry['segment']} {act_desc}")
                    print(f"     提示: 回滚按段号（如: 回滚 {entry['segment']}），撤回按条目 id（如: 撤回 {entry['id']}）\n")
                except SystemExit as ex:
                    print(f"{ex}")
            else:
                print("[CANCEL] 已放弃本次纠错落盘。\n")

    finally:
        player.stop()

    return 0


# ---------------------------------------------------------------------------
# 停机点深度组件（Spec 11 §3.1）：core 裸形态子命令全集
#
# 八个确定性人令：桌面端按钮点击 → host spawn → 这里。分派与 `/approve`、`/reject`
# 同级（按 argv 位置无损取参、不过 `validate_pipeline_command`），终端 REPL 与裸形态
# 共用同一份分派。**不进 LLM 工具表、不扩 `write_episode_file` 白名单**：模型写草稿、
# 人写正稿，两条通道分开（ADR-0024 决策 1）。
# 退出码：0 成 / 1 败 / 2 用法错；落盘一律经 `paths.atomic_write`。
# ---------------------------------------------------------------------------

STOP_POINT_COMMANDS = (
    "/save-script", "/seal-script", "/voice-info", "/voice-parse", "/voice-add",
    "/voice-revert", "/voice-retract", "/record-time",
)
SAVE_SCRIPT_MAX_BYTES = 1024 * 1024      # 真实稿件是 KB 级，余量两个数量级
VOICE_PARSE_MAX_BYTES = 64 * 1024        # 纠错原文是一行文法
RECORD_TIME_CLOCK_TOLERANCE_S = 60.0     # host 与 core 同机同时钟，只防取整边界


class _UsageError(Exception):
    """本族子命令的用法错（CLI 退 2，与未识别子命令同码）。"""


def _valued_flags(rest: list[str], names: tuple[str, ...]) -> dict[str, str]:
    """`--name=value` 等号旗标 → {name: value}；缺项/多项/裸名一律 _UsageError。

    等号形式是本族命令的约定（Spec 11 §3.1）：无分词歧义，也不需要 POSIX 引号。
    """
    out: dict[str, str] = {}
    for token in rest:
        name, sep, value = token.partition("=")
        if not sep or name not in names or name in out or not value:
            raise _UsageError(f"无法识别的参数 {token!r}（本族命令用 --name=value 等号形式）")
        out[name] = value
    missing = [n for n in names if n not in out]
    if missing:
        raise _UsageError(f"缺少参数：{'、'.join(missing)}")
    return out


def _flag_number(raw: str, flag: str, cast: Callable[[str], Any]) -> Any:
    try:
        return cast(raw)
    except ValueError:
        raise _UsageError(f"{flag} 需要数字，收到 {raw!r}")


def _read_stdin_text(limit: int) -> str | None:
    """读 stdin 全文；超 limit（UTF-8 字节）返回 None，调用方退 2（Spec 11 §3.1）。"""
    text = sys.stdin.read(limit)
    if sys.stdin.read(1) or len(text.encode("utf-8")) > limit:
        return None
    return text


# 图片导入（Spec 12 §3.1）：与 Spec 11 八命令同级（argv 位置取参、不过
# validate_pipeline_command、不进 LLM 工具表），但**不进 Spec 11 的八命令表**。
IMPORT_COVER_MAX_BYTES = 32 * 1024 * 1024


def _read_stdin_bytes(limit: int) -> bytes | None:
    """读 stdin 二进制全文；超 limit 返回 None，调用方退 2。

    限流冻结在分派层（Spec 12 🔵-10）：`_cmd_import_cover` 收到的是已限流的 bytes。
    """
    data = sys.stdin.buffer.read(limit + 1)
    return None if len(data) > limit else data


def _cmd_import_cover(ep_dir: Path | str, name: str | None, data: bytes) -> int:
    """校验并原子落盘导入图，stdout 单行 JSON（Spec 12 §3.1，data 已限流）。"""
    from pipeline import cover_edit

    try:
        resolved = resolve_episode_dir(ep_dir)
        out = cover_edit.import_cover(resolved, name, data)
    except (cover_edit.CoverEditError, PermissionError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _dispatch_import_cover(ep_dir: Path, argv: list[str], *, from_stdin: bool) -> int | None:
    """`/import-cover` 的统一分派（裸形态 / REPL 共用）。非本命令返回 None。

    裸形态（from_stdin=True）：`ava <期> /import-cover --name=<原始文件名>`，图片字节走 stdin；
    REPL（from_stdin=False）：`/import-cover <源图片路径>`，由 core 读文件——REPL 内没有
    stdin 管道，这是两种形态唯一的行为差异，校验与落盘同一函数。
    """
    if not argv or argv[0] != "/import-cover":
        return None
    rest = argv[1:]
    try:
        if from_stdin:
            name: str | None = None
            if rest:
                if len(rest) != 1 or not rest[0].startswith("--name="):
                    raise _UsageError(
                        "用法: ava <期> /import-cover --name=<原始文件名>（图片字节走 stdin）"
                    )
                name = rest[0][len("--name="):]
            data = _read_stdin_bytes(IMPORT_COVER_MAX_BYTES)
            if data is None:
                raise _UsageError(f"图片字节超过 {IMPORT_COVER_MAX_BYTES} 字节上限，拒收。")
            return _cmd_import_cover(ep_dir, name, data)

        if len(rest) != 1:
            raise _UsageError("用法: /import-cover <源图片路径>")
        src = Path(rest[0]).expanduser()
        try:
            if src.stat().st_size > IMPORT_COVER_MAX_BYTES:
                raise _UsageError(f"图片字节超过 {IMPORT_COVER_MAX_BYTES} 字节上限，拒收。")
            data = src.read_bytes()
        except OSError as exc:
            print(f"[ERROR] 读不到源图片：{src}（{exc}）", file=sys.stderr)
            return 1
        return _cmd_import_cover(ep_dir, src.name, data)
    except _UsageError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


def _episode_file(ep_dir: Path | str, name: str, root: Path | None = None) -> Path:
    """期目录内的固定文件名 → 绝对路径（越界抛 PermissionError）。

    期目录解析复用 `tools.resolve_episode_dir`（与 LLM 写工具同一条纪律）；这里只多
    一层「文件名不得是逃出期目录的符号链接」。
    """
    resolved_ep = resolve_episode_dir(ep_dir, root=root)
    target = (resolved_ep / name).resolve()
    if target.parent != resolved_ep:
        raise PermissionError(f"目标路径越界：{target} 不在当期根目录 {resolved_ep} 下")
    return target


def _pid_alive(pid: int) -> bool:
    """锁内 pid 是否存活（确定性事实，供面板区分「应用进行中」与「锁残留」）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True      # 进程在，只是不属于当前用户
    return True


def _cmd_save_script(ep_dir: Path | str, expect_size: int, expect_mtime_ns: int,
                     stdin_text: str) -> int:
    """`/save-script`：指纹相符才写 `02-script.md`，stdout 回新指纹 JSON（Spec 11 §2.2）。

    指纹不符退 1 且**一字不写**：并发编辑没有安全出路，拒存让人看见冲突后自己决定。
    `--expect-size=-1 --expect-mtime-ns=-1` 表示断言文件**不存在**（「从草稿新建」）。
    """
    target = _episode_file(ep_dir, "02-script.md")
    want_missing = (expect_size, expect_mtime_ns) == (-1, -1)
    if want_missing and target.exists():
        print("[ERROR] 磁盘版本已变：02-script.md 已存在，未保存。", file=sys.stderr)
        return 1
    if not want_missing:
        if not target.exists():
            print("[ERROR] 磁盘版本已变：02-script.md 不存在，未保存。", file=sys.stderr)
            return 1
        st = target.stat()
        if (st.st_size, st.st_mtime_ns) != (expect_size, expect_mtime_ns):
            print(
                f"[ERROR] 磁盘版本已变（实际 size={st.st_size} mtime_ns={st.st_mtime_ns}，"
                f"期望 size={expect_size} mtime_ns={expect_mtime_ns}），未保存。",
                file=sys.stderr,
            )
            return 1

    paths.atomic_write(target, stdin_text)
    st = target.stat()
    print(json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns}))
    return 0


def _cmd_seal_script(ep_dir: Path | str) -> int:
    """`/seal-script`：core 内跑终端同一条 `git diff --no-index`，空 diff 拒封（§2.2）。

    产物与终端手工封板字节一致：同一个 git 二进制、同一组参数、cwd 同为期目录。
    shell 重定向无法被 `shell: false` 的 spawn 闭集表达，所以才有了这个子命令。
    """
    script = _episode_file(ep_dir, "02-script.md")
    draft = _episode_file(ep_dir, "02-script.draft.md")
    if not script.exists():
        print("[ERROR] 找不到 02-script.md，无法封板。", file=sys.stderr)
        return 1
    if not draft.exists():
        print("[ERROR] 找不到 02-script.draft.md（早期目录可能没有草稿），无法封板。", file=sys.stderr)
        return 1

    proc = subprocess.run(
        ["git", "diff", "--no-index", draft.name, script.name],
        cwd=script.parent,
        capture_output=True,
    )
    if proc.returncode == 0:
        print(
            "[ERROR] 未做任何修改，无封板痕迹可留：未写 02-diff.patch（零改动不可封板）。",
            file=sys.stderr,
        )
        return 1
    if proc.returncode != 1:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        print(f"[ERROR] git diff 失败（退出码 {proc.returncode}）：{err}", file=sys.stderr)
        return 1

    paths.atomic_write(script.parent / "02-diff.patch", proc.stdout.decode("utf-8"))
    print(len(proc.stdout))
    return 0


def voice_info_payload(ep_dir: Path | str) -> dict:
    """`/voice-info` 的 payload（Spec 11 §3.3 v1 schema，冻结）。纯读。

    `segments` 由 core 单源供给（TS 不解析稿件、不拼 wav 文件名，RF-1）；`has_attic`
    是「回滚」按钮可用性的确定性依据；`apply_patch_lock` 区分「应用进行中」与
    「锁残留」（SIGKILL/断电后残余）。**不含时长**：由 renderer 的 `<audio>` 自读 metadata。
    """
    from pipeline import corrections, g2p, tts

    from_ep = resolve_episode_dir(ep_dir)
    segs = tts.parse_script(from_ep / "02-script.md")
    audio_dir = from_ep / "03-audio"

    engine = ""
    mf_path = audio_dir / "manifest.json"
    if mf_path.exists():
        try:
            engine = str(json.loads(mf_path.read_text(encoding="utf-8")).get("engine", ""))
        except Exception:
            engine = ""          # 清单损坏不归本命令管，空串即可

    entries, _ = corrections.load_corrections_raw(from_ep)

    segments = []
    for s in segs:
        wav = f"seg-{s.index:02d}.wav"
        segments.append({
            "label": str(s.label),
            "index": s.index,
            "wav": wav,
            "wav_exists": (audio_dir / wav).exists(),
            "text": s.text,
            "has_attic": corrections.find_attic_snapshot(from_ep, str(s.label)) is not None,
        })

    hetero = g2p.scan_heteronyms("\n".join(s.text for s in segs))
    pid: int | None = None
    pid_alive: bool | None = None
    lock = audio_dir / ".apply_patch.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
            pid_alive = _pid_alive(pid)
        except (OSError, ValueError):
            pid = pid_alive = None

    return {
        "v": 1,
        "engine": engine,
        "engine_cloud": "cuda" in engine,
        "segments": segments,
        "heteronyms": [
            {"char": h["char"], "readings": list(h.get("readings", []))[:3]}
            for h in hetero[:10]        # 条数与 readings 截断同终端（cli.py 异读预检）
        ],
        "pending_corrections": [c for c in entries if not c.get("applied", False)],
        "apply_patch_lock": {
            "exists": lock.exists(),
            "pid": pid,
            "pid_alive": pid_alive,
        },
    }


def _cmd_voice_info(ep_dir: Path | str) -> int:
    """`/voice-info`：纯读（零写入），stdout 单行 JSON。"""
    if not _episode_file(ep_dir, "02-script.md").exists():
        print("[ERROR] 找不到 02-script.md（同 /voice 前置）。", file=sys.stderr)
        return 1
    print(json.dumps(voice_info_payload(ep_dir), ensure_ascii=False))
    return 0


def _cmd_voice_parse(ep_dir: Path | str, stdin_text: str) -> int:
    """`/voice-parse`：纯算（零写入）——终端同一个 `parse_correction`，stdout 单行 JSON。

    输出是 Patch 的 8 个字段：**不含 `raw`**（原文就在输入框，回传只增帧面），
    **不含 `seed_pin`**（它在 `append_correction` 落盘时才生成）。
    """
    from pipeline import corrections, tts

    script = _episode_file(ep_dir, "02-script.md")
    if not script.exists():
        print("[ERROR] 找不到 02-script.md（同 /voice 前置）。", file=sys.stderr)
        return 1
    try:
        patch = corrections.parse_correction(stdin_text, tts.parse_script(script))
    except corrections.PatchError as exc:
        print(f"{exc}", file=sys.stderr)      # 终端同款错误原文
        return 1
    print(json.dumps({
        "v": 1,
        "segment": patch.segment,
        "kind": patch.kind,
        "word": patch.word,
        "heard": patch.heard,
        "target_tone3": patch.target_tone3,
        "issue": patch.issue,
        "action": patch.action,
        "scope": patch.scope,
    }, ensure_ascii=False))
    return 0


def _cmd_voice_add(ep_dir: Path | str, stdin_text: str) -> int:
    """`/voice-add`：**重新解析** stdin 原文再落盘（§2.3）：不信任跨进程往返的解析结果。

    解析与落盘都对同一文法函数求值，等价且防篡改；stdout 为 `append_correction`
    返回的条目 dict 原样（含 `id`，pin_seed 条目含 `seed_pin`）。
    """
    from pipeline import corrections, tts

    script = _episode_file(ep_dir, "02-script.md")
    if not script.exists():
        print("[ERROR] 找不到 02-script.md（同 /voice 前置）。", file=sys.stderr)
        return 1
    try:
        patch = corrections.parse_correction(stdin_text, tts.parse_script(script))
        entry = corrections.append_correction(script.parent, patch)
    except corrections.PatchError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:                  # 单写者锁 / corrections.json 损坏 / 回读失败
        print(f"{exc}", file=sys.stderr)
        return 1
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def _cmd_voice_revert(ep_dir: Path | str, label: str) -> int:
    """`/voice-revert <段号>`：`corrections.revert_segment`（终端「回滚」同函数）。"""
    from pipeline import corrections

    try:
        corrections.revert_segment(resolve_episode_dir(ep_dir), label)
    except SystemExit as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_voice_retract(ep_dir: Path | str, item_id: int) -> int:
    """`/voice-retract <id>`：`corrections.retract_correction`（终端「撤回」同函数）。"""
    from pipeline import corrections

    try:
        corrections.retract_correction(resolve_episode_dir(ep_dir), item_id)
    except SystemExit as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_record_time(ep_dir: Path | str, stop: str, entered: float, left: float) -> int:
    """`/record-time`：墙钟人时条目落 `human_time.json`（复用 `record_human_time`，§2.4）。

    审阅面可见即计时、失焦照算、**不设时长下限**（与终端停机点停留口径一致，两端可比）。
    """
    if stop not in HUMAN_STOPS:
        print(
            f"[ERROR] 停机点必须是 {'|'.join(sorted(HUMAN_STOPS))} 之一，收到 {stop!r}",
            file=sys.stderr,
        )
        return 2
    if not entered < left:
        print("[ERROR] --entered 必须早于 --left。", file=sys.stderr)
        return 2
    if left > time.time() + RECORD_TIME_CLOCK_TOLERANCE_S:
        print(
            f"[ERROR] --left 晚于当前时刻（超过 {RECORD_TIME_CLOCK_TOLERANCE_S:g} s 钟差容差）。",
            file=sys.stderr,
        )
        return 2

    ep_resolved = resolve_episode_dir(ep_dir)
    try:
        minutes = record_human_time(ep_resolved, stop, entered, left, source="desktop")
    except OSError as exc:
        print(f"[ERROR] 人时落盘失败：{exc}", file=sys.stderr)
        return 1

    from pipeline.jobs import EventType, get_publisher
    get_publisher().emit(
        EventType.HUMAN_TIME_RECORDED,
        {"stop": stop, "minutes": minutes},
        episode_dir=ep_resolved,
    )
    print(f"[OK] 停机点 {stop} 墙钟 {minutes:.1f} 分钟 → human_time.json")
    return 0


def _dispatch_stop_point(ep_dir: Path, argv: list[str]) -> int | None:
    """Spec 11 §3.1 八个子命令的统一分派（裸形态与 REPL 共用）。非本族命令返回 None。

    只路由，不做业务：每个子命令一个 `_cmd_*` 私有函数。"""
    if not argv or argv[0] not in STOP_POINT_COMMANDS:
        return None
    cmd, rest = argv[0], argv[1:]

    try:
        if cmd == "/save-script":
            flags = _valued_flags(rest, ("--expect-size", "--expect-mtime-ns"))
            text = _read_stdin_text(SAVE_SCRIPT_MAX_BYTES)
            if text is None:
                raise _UsageError(f"正文超过 {SAVE_SCRIPT_MAX_BYTES} 字节上限，拒收。")
            return _cmd_save_script(
                ep_dir,
                _flag_number(flags["--expect-size"], "--expect-size", int),
                _flag_number(flags["--expect-mtime-ns"], "--expect-mtime-ns", int),
                text,
            )
        if cmd == "/seal-script":
            if rest:
                raise _UsageError("用法: ava <期> /seal-script")
            return _cmd_seal_script(ep_dir)
        if cmd == "/voice-info":
            if rest:
                raise _UsageError("用法: ava <期> /voice-info")
            return _cmd_voice_info(ep_dir)
        if cmd == "/voice-parse":
            if rest:
                raise _UsageError("用法: ava <期> /voice-parse（纠错原文走 stdin）")
            text = _read_stdin_text(VOICE_PARSE_MAX_BYTES)
            if text is None:
                raise _UsageError(f"纠错原文超过 {VOICE_PARSE_MAX_BYTES} 字节上限，拒收。")
            return _cmd_voice_parse(ep_dir, text)
        if cmd == "/voice-add":
            if rest:
                raise _UsageError("用法: ava <期> /voice-add（纠错原文走 stdin）")
            text = _read_stdin_text(VOICE_PARSE_MAX_BYTES)
            if text is None:
                raise _UsageError(f"纠错原文超过 {VOICE_PARSE_MAX_BYTES} 字节上限，拒收。")
            return _cmd_voice_add(ep_dir, text)
        if cmd == "/voice-revert":
            if len(rest) != 1:
                raise _UsageError("用法: ava <期> /voice-revert <段号>")
            return _cmd_voice_revert(ep_dir, rest[0])
        if cmd == "/voice-retract":
            if len(rest) != 1:
                raise _UsageError("用法: ava <期> /voice-retract <id>")
            return _cmd_voice_retract(ep_dir, _flag_number(rest[0], "<id>", int))
        if cmd == "/record-time":
            if not rest:
                raise _UsageError(
                    "用法: ava <期> /record-time <02.5|03.5|05|09> "
                    "--entered=<epoch_s> --left=<epoch_s>"
                )
            flags = _valued_flags(rest[1:], ("--entered", "--left"))
            return _cmd_record_time(
                ep_dir, rest[0],
                _flag_number(flags["--entered"], "--entered", float),
                _flag_number(flags["--left"], "--left", float),
            )
    except _UsageError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except PermissionError as exc:              # 期目录越界 / 外置盘未挂载
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return None


def classify_input(line: str) -> str:
    """判断输入类型（Spec §1.1）：以 '/' 开头走快捷键路由，其余进 Director 对话。"""
    return "shortcut" if line.startswith("/") else "chat"


def assemble_system_prompt(
    ep_dir: Path | None,
    scope: str,
    status: EpisodeStatus | None = None,
    extra_prompt: str = "",
    root: Path | None = None,
) -> str:
    """重组装 messages[0]：由 assembly.py 单源供给常驻层 + 状态卡（Spec 1 PR2 收编）。"""
    from pipeline.agent.assembly import assemble_resident_prompt

    resident = assemble_resident_prompt(scope, root=root, extra_prompt=extra_prompt)
    if ep_dir is None:
        from pipeline.agent.status_card import build_idea_card
        card = build_idea_card()
    else:
        card = build_status_card(ep_dir, status, scope=scope)
    return f"{resident.content}\n\n---\n\n{card}"


def _candidates_brief(raw: Any) -> str:
    """提取 candidates 数组中的 title 摘要（以「、」拼接，截断 120 字符，不含 URL）。"""
    if not isinstance(raw, list):
        return ""
    titles: list[str] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        raw_t = str(c.get("title", "")).strip()
        first = raw_t.splitlines()[0] if raw_t else ""
        clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", first).strip()
        if clean:
            titles.append(clean)
    return "、".join(titles)[:120]


class TtyChannel:
    """终端通道（Spec 9 §2.6）：打印 + `input()`，EOF 视为否。

    内核不打印、不提问；所有面向人的文本都从这里出去。文案与改造前逐字一致——
    金样本（TT-1）就是尺子，改文案得单独提出来（RF-13）。
    """

    name = "tty"

    def is_tty(self) -> bool:
        return sys.stdin.isatty()

    def show(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "stderr":
            print(payload.get("text", ""), file=sys.stderr)
            return
        if kind in ("echo", "tool"):
            # `tool` 只在协议下承载帧；终端只认 `echo`（回显原文）
            if kind == "echo" or payload.get("echo"):
                print(payload.get("echo") or payload.get("text", ""))
            return
        if kind == "card":
            print(f"\n{payload.get('text', '')}", end="")
            return
        print(payload.get("text", ""))

    def ask(self, request: HumanRequest) -> HumanAnswer:
        if request.kind == "memory_ack":
            print(request.card_text)
            print("\n以上是「上次确认版本 → 当前文件」的改动与当前全文。按 y 确认，其它任意键取消（默认 N）：")
        else:
            self.show("card", {"text": f"{request.card_text}\n{prompt_for(request.kind)}"})
        started = time.time()
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = "n"
        latency = time.time() - started
        decision = request.options[0] if answer == "y" else request.options[-1]
        return HumanAnswer(request.request_id, decision, None, "tty", latency)


# 进程内唯一登记（Spec 9 §2.6）：只经 activate_host 设置，finally 里注销并释放租约。
_SESSION_HOST: SessionHost | None = None
_HOST_DEPTH = 0
_HOST_LOCK = threading.Lock()


@contextlib.contextmanager
def activate_host(
    ep_dir: Path | str | None,
    *,
    root: Path | None = None,
    channel: Any = None,
) -> Iterator[SessionHost]:
    """登记一个 SessionHost（可重入）。

    范围正好覆盖 `run_repl` / `_run_repl_body` / `run_agent_loop` 的执行期：`finally` 里
    注销并释放租约，任何异常路径都不会把登记留在进程里。已有同一个 `ep_dir` 的活动登记
    只计数、不替换（`/chat`、`/script` 在 REPL 循环内再进一次）；`ep_dir` 不同的嵌套进入
    视为错误（现有代码中不存在）。
    """
    global _SESSION_HOST, _HOST_DEPTH
    resolved = Path(ep_dir).resolve() if ep_dir is not None else None
    with _HOST_LOCK:
        current = _SESSION_HOST
        if current is not None and current.ep_dir == resolved:
            _HOST_DEPTH += 1
            reentrant = True
        else:
            if current is not None:
                raise RuntimeError(f"activate_host 的期目录不同的嵌套进入: {resolved} vs {current.ep_dir}")
            _SESSION_HOST = SessionHost(resolved, root=root, channel=channel or TtyChannel())
            _HOST_DEPTH = 1
            reentrant = False
    if reentrant:
        try:
            yield _SESSION_HOST  # type: ignore[misc]
        finally:
            with _HOST_LOCK:
                _HOST_DEPTH -= 1
        return
    host = _SESSION_HOST
    assert host is not None
    try:
        yield host
    finally:
        with _HOST_LOCK:
            _HOST_DEPTH -= 1
            if _HOST_DEPTH <= 0:
                _SESSION_HOST = None
        if host.lease is not None:
            host.lease.close()


def _default_approve(
    name: str,
    args: dict[str, Any],
    *,
    ep_dir: Path | None = None,
    scope: str = "creative",
    status: EpisodeStatus | None = None,
    root: Path | None = None,
) -> tuple[bool, str]:
    """薄包装（Spec 9 §2.6）：签名与返回类型不变，内部改走内核的工具审查。

    真正的审查逻辑在 `session.review_tool_call`（M15a/M16-1/M16-2/M20 的锚点）。
    """
    channel = TtyChannel()
    verdict = review_tool_call(
        name, args, ep_dir=ep_dir, scope=scope, status=status, root=root
    )
    if verdict.action == "reject":
        print(f"[REJECT] {verdict.reason}")
        return (False, verdict.reason)
    if verdict.action == "allow":
        if verdict.echo:
            print(verdict.echo)
        return (True, "")
    request = verdict.request
    assert request is not None
    answer = channel.ask(request)
    target = str(request.fields.get("target", ""))
    if answer.decision != "approve":
        log_approval_decision(ep_dir, name, target, "n", latency_s=answer.latency_s, channel="tty")
        print("[CANCEL] 已取消执行")
        return (False, "人类拒绝执行该工具调用")
    log_approval_decision(ep_dir, name, target, "y", latency_s=answer.latency_s, channel="tty")
    return (True, "")


def _dispatch_agent_turn(
    line: str,
    messages: list[dict[str, Any]],
    ep_dir: Path | None,
    scope: str,
    status: EpisodeStatus | None = None,
    extra_prompt: str = "",
    root: Path | None = None,
    approve_cb: Callable[[str, dict], bool] | None = None,
    tracker: SessionContextTracker | None = None,
) -> dict[str, Any]:
    """执行单轮 Director 对话（Spec 9 §2.6 的薄包装，签名不变）。

    四个调用点（idea / `/chat` / `/script` / `/memory digest` / 主 REPL）一律经模块属性调用
    这里，参数与签名不变；是否落盘不看参数，看**对象同一性**：`messages is host.main_messages`。
    进程里没有登记（或登记的是别的期）时，走「没有登记」的契约：原地修改调用方传入的
    `messages` 与 `tracker`，`persist=False`，不取租约、不写 `session.jsonl`。
    """
    host = _SESSION_HOST
    if host is None or not host.matches(ep_dir):
        host = SessionHost(ep_dir, root=root, channel=TtyChannel(), ephemeral=True)
    outcome = host.dispatch(
        line, messages, ep_dir, scope, status, extra_prompt, root, approve_cb, tracker
    )
    channel = host.channel
    if outcome.get("rollback"):
        if outcome.get("messages") is not messages:
            messages.pop()
    elif outcome.get("messages") is not messages:
        messages.clear()
        messages.extend(outcome["messages"])

    stopped = outcome.get("stopped")
    if stopped == "degraded":
        # 降级消息已经在装配里打过（与改造前一样只打一次）
        return outcome
    if stopped == "blocked":
        channel.show("stdout", {"text": f"[BLOCKED] 出网被拦截：{outcome.get('error', '')}"})
        channel.show("stdout", {
            "text": "          本次请求未发出。请改问不含受限内容（密钥/音频清单/补片素材）的问题。"
        })
    elif stopped == "error" and outcome.get("error"):
        channel.show("stdout", {"text": f"[FAIL] {outcome['error']}"})
    else:
        content = (outcome.get("final") or {}).get("content") or ""
        if content:
            channel.show("stdout", {"text": f"\n{content}"})
    if outcome.get("local_note"):
        # 本地确定性说明（Spec 9 §2.3 第 4 条）：只呈现，不进 messages
        channel.show("stdout", {"text": f"\n{outcome['local_note']}"})
    return outcome


def run_agent_loop(
    ep_dir: Path | None,
    scope_mode: str = "auto",
    extra_prompt: str = "",
    *,
    root: Path | None = None,
    on_step: Callable[[str], None] | None = None,
) -> int:
    """登记 SessionHost 后进真正的循环（Spec 9 §2.6：范围覆盖整个执行期）。"""
    with activate_host(ep_dir, root=root):
        return _run_agent_loop_body(
            ep_dir, scope_mode, extra_prompt, root=root, on_step=on_step
        )


def _run_agent_loop_body(
    ep_dir: Path | None,
    scope_mode: str = "auto",
    extra_prompt: str = "",
    *,
    root: Path | None = None,
    on_step: Callable[[str], None] | None = None,
) -> int:
    """统一 Agent 对话循环（Spec §1.1, §1.2, §2.1, §6 PR5）。

    - scope_mode="auto": 主会话 REPL，每轮 scope_of(inspect_episode(ep_dir)) 热推导；
    - scope_mode="creative": /chat /script 聚焦模式，拥有独立 messages，退出后主会话不受污染；
    - scope_mode="idea": 无期选题会话，独立 messages，写权限为零。
    全仓库只保留这一份聊天循环实现。
    """
    if scope_mode == "auto":
        if ep_dir is None:
            raise ValueError("auto 模式必须提供 ep_dir")
        return _run_repl_body(ep_dir, on_step=on_step, root=root)

    if scope_mode == "idea":
        from pipeline.agent.llm import load_llm_config, local_directive_message

        if load_llm_config(root) is None:
            print(local_directive_message("idea", "缺少 config/agent.json 或环境变量密钥")["content"])
            return 0

        print("\n" + "=" * 68)
        print("  ava 选题会话（idea scope · 无期目录）")
        print("  - 写权限为零（机制保证，不修改任何文件）")
        print("  - 可通过 read_status 查看既有期状态，或通过 search_notes 查阅番剧笔记")
        print("  - 讨论定稿后退出本会话，运行 'ava new <期名>' 创建新期")
        print("=" * 68)

        from pipeline.agent.assembly import SessionContextTracker, assemble_resident_prompt

        tracker = SessionContextTracker()
        tracker.resident_prompt = assemble_resident_prompt("idea", root=root).content

        sub_messages: list[dict[str, Any]] = []
        while True:
            try:
                line = input("\nava [选题] (idea) > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[退出 idea 模式]")
                return 0
            if not line:
                continue
            if line in ("/quit", "/exit", "quit", "exit", "/done"):
                print("[退出 idea 模式]")
                return 0

            outcome = _dispatch_agent_turn(
                line,
                sub_messages,
                None,
                "idea",
                None,
                extra_prompt=extra_prompt,
                root=root,
                tracker=tracker,
            )
            if outcome.get("stopped") == "degraded":
                return 0

    # 聚焦子模式（如 creative scope 独立子循环）
    from pipeline.agent.assembly import SessionContextTracker, assemble_resident_prompt
    from pipeline.agent.llm import load_llm_config, local_directive_message

    if load_llm_config(root) is None:
        print(local_directive_message(scope_mode, "缺少 config/agent.json 或环境变量密钥")["content"])
        return 0

    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt(
        scope_mode, root=root, extra_prompt=extra_prompt
    ).content

    sub_messages: list[dict[str, Any]] = []
    while True:
        status = inspect_episode(ep_dir)
        current_scope = scope_mode
        if on_step:
            on_step(status.current_step)

        try:
            line = input(f"\nava [{ep_dir.name}] ({current_scope}) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n[退出 {current_scope} 模式]")
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "quit", "exit", "/done"):
            print(f"[退出 {current_scope} 模式]")
            return 0

        outcome = _dispatch_agent_turn(
            line,
            sub_messages,
            ep_dir,
            current_scope,
            status,
            extra_prompt=extra_prompt,
            root=root,
            tracker=tracker,
        )
        if outcome.get("stopped") == "degraded":
            return 0


def run_creative_loop(ep_dir: Path, mode: str = "chat", root: Path | None = None) -> int:
    """向后兼容别名：调用泛化后的 run_agent_loop（Spec §1.1）。"""
    extra = "" if mode == "chat" else (
        "\n\n本轮聚焦写稿：产出只许写 02-script.draft.md；写完提示跑 check_script。"
    )
    return run_agent_loop(ep_dir, scope_mode="creative", extra_prompt=extra, root=root)


def run_repl(ep_dir: Path) -> int:
    """REPL 入口：包一层停机点墙钟记账（Spec §2.6），机身在 _run_repl_body。

    §2.6 的读数只在**人类停机点**计：进入某停机点 scope 到离开为止的墙钟。
    没有这一层，human_time.json 永远是空的，status 的人时观测行就成了死数据（终审 P0-2）。
    """
    span: dict[str, object] = {"stop": None, "at": time.time()}

    def close_stop(target_dir: Path | None = None) -> None:
        d = target_dir or ep_dir
        stop = span["stop"]
        if stop:
            minutes = record_human_time(d, str(stop), float(span["at"]), time.time())
            print(f"\n[人时] 停机点 {stop} 墙钟 {minutes:.1f} 分钟 → human_time.json")
        span["stop"] = None

    def on_step(current_step: str) -> None:
        """工序变化 = 停机点切换：先结算上一段，再开新的一段。"""
        new_stop = park_stop_of(current_step)
        if new_stop == span["stop"]:
            return
        close_stop()
        span["stop"], span["at"] = new_stop, time.time()

    with activate_host(ep_dir):
        try:
            return _run_repl_body(ep_dir, on_step, close_stop=close_stop)
        finally:
            close_stop()


def _exec_scout(ep_dir: Path, args: list[str]) -> int:
    """进程内直调 pipeline.scout（包含 render_ticket）生成并打印工单。"""
    from pipeline import scout
    try:
        return scout.main([str(ep_dir)] + args)
    except SystemExit as exc:
        msg = str(exc)
        if msg and msg != "0":
            print(f"[ERROR] {msg}", file=sys.stderr)
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        print(f"[ERROR] scout 生成异常: {exc}", file=sys.stderr)
        return 1


MEMORY_DIGEST_PROMPT = (
    "以下是各期人工驳回的结构化反馈（哪一段、什么问题）。请归纳出可复用的经验候选，"
    "每条只写模式 + 证据期 + 适用边界，不要写成规则，不要臆断频度。"
    "需要落盘时用 write_memory（add / revise / merge）。\n\n"
)


def run_memory_ack(ep_dir: Path | None, *, root: Path | None = None) -> bool:
    """REPL /memory ack：展示「上次确认版本 → 当前文件」的 diff 与全文，按 y 才对账（Spec 7 §2.4）。"""
    from pipeline.agent import memory

    state: dict[str, float] = {"latency": 0.0}

    def confirm(text: str) -> tuple[bool, float]:
        print(text)
        print("\n以上是「上次确认版本 → 当前文件」的改动与当前全文。按 y 确认，其它任意键取消（默认 N）：")
        started = time.time()
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = "n"
        state["latency"] = time.time() - started
        return (answer == "y", state["latency"])

    ok = memory.ack_external(root=root, confirm=confirm)
    if ok:
        # 记账只为审批疲劳观测；记忆 ack 不是 approval 对象，不发 APPROVAL_RESOLVED（Spec 7 §6.2）
        log_approval_decision(
            ep_dir, "memory_ack", "memory.md", "y", latency_s=state["latency"], emit_event=False
        )
    return ok


def run_memory_digest(ep_dir: Path | None, *, root: Path | None = None) -> None:
    """REPL /memory digest：独立子会话聚合驳回反馈（Spec 7 §2.10）。"""
    from pipeline.agent import memory
    from pipeline.agent.assembly import SessionContextTracker, assemble_resident_prompt
    from pipeline.agent.llm import load_llm_config, local_directive_message

    try:
        digest = memory.feedback_digest(root=root)
    except RuntimeError as exc:
        print(f"[memory digest] {exc}")
        return
    print(
        f"[memory digest] {len(digest.included)} 期有驳回反馈"
        f"（可见 {digest.visible_episodes} / 归档 {digest.archived_episodes}）"
    )
    if digest.omitted:
        print(f"[memory digest] 超出 {memory.DIGEST_MAX_CHARS} 字符未纳入：{list(digest.omitted)}")
    if not digest.included:
        return
    if load_llm_config(root) is None:
        print(local_directive_message("creative", "缺少 config/agent.json 或环境变量密钥")["content"])
        return

    # 独立子会话：自建 messages 与 tracker，结束即丢（主 REPL 的 messages 长度不变）
    messages: list[dict[str, Any]] = []
    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt("creative", root=root).content
    tracker.active_scope = "creative"
    status = inspect_episode(ep_dir) if ep_dir else None
    _dispatch_agent_turn(
        MEMORY_DIGEST_PROMPT + digest.text, messages, ep_dir, "creative", status,
        root=root, tracker=tracker,
    )


def run_memory_command(line: str, ep_dir: Path | None, *, root: Path | None = None) -> None:
    """`/memory` 四个子命令的路由（Spec 7 §4.4）。"""
    from pipeline.agent import memory

    sub = line[len("/memory"):].strip()
    if sub in ("", "show"):
        memory.main(["show"], root=root)
        return
    if sub == "check":
        memory.main(["check"], root=root)
        return
    if sub == "ack":
        run_memory_ack(ep_dir, root=root)
        return
    if sub == "digest":
        run_memory_digest(ep_dir, root=root)
        return
    print(
        f"[ERROR] 未知的 /memory 子命令: '{sub}'。可用: /memory [show|check|ack|digest]",
        file=sys.stderr,
    )


def _seed_resume(
    ep_dir: Path, messages: list[dict[str, Any]], tracker: SessionContextTracker, root: Path | None
) -> None:
    """`--continue`：把历史填回主会话（`messages[0]` 按**当前**文件重建，§2.7）。"""
    host = _SESSION_HOST
    state = host.resume_state if host is not None else None
    if not state or state.get("status") != "resumed":
        # 普通启动开新会话：存在更早可恢复的会话时打印一行提示，**不提问**（§2.5 用户裁决）
        if host is not None and host.ep_dir is not None:
            target, _candidates = resume_target(list_sessions(read_log(host.ep_dir)))
            if target is not None:
                print(
                    f"[会话] 上次会话 {target.sid[:8]} · {target.messages} 条消息 · "
                    f"{target.last_activity}，可用 ava {ep_dir.name} --continue 恢复"
                )
        return
    from pipeline.agent.assembly import assemble_resident_prompt

    status = inspect_episode(ep_dir)
    scope = scope_of(status)
    tracker.resident_prompt = assemble_resident_prompt(scope, root=root).content
    tracker.active_scope = scope  # §2.7 第 3 条：active_scope 从空开始
    tracker.injected_paths = set((state.get("docs") or {}).keys())
    tracker.active_step_key = state.get("step_key")
    messages.append({
        "role": "system",
        "content": tracker.get_initial_system_prompt(
            build_status_card(ep_dir, status, scope=scope)
        ),
    })
    messages.extend(state.get("messages") or [])
    count = len(state.get("messages") or [])
    print(f"[会话] 已恢复 {str(state.get('sid', ''))[:8]}，重放 {count} 条消息。")


def _run_repl_body(
    ep_dir: Path,
    on_step: Callable[[str], None] | None = None,
    root: Path | None = None,
    *,
    close_stop: Callable[..., None] | None = None,
) -> int:
    """REPL 交互循环（Spec §1.1, §1.2, §2.4）。on_step 用于停机点墙钟记账（§2.6）。"""
    check_code_freeze()
    print(f"\n已就绪：{ep_dir.name}")
    print("输入 /help 查看命令，输入 /status 查看状态，输入 /quit 退出。")

    _close_stop = close_stop or (lambda *a, **kw: None)
    scout_entered_at: float | None = None

    def _settle_scout() -> None:
        nonlocal scout_entered_at
        if scout_entered_at is None:
            return
        minutes = record_human_time(ep_dir, "scout", scout_entered_at, time.time())
        if minutes > 0:
            print(f"\n[人时] 停机点 scout 墙钟 {minutes:.1f} 分钟 → human_time.json")
        scout_entered_at = None

    from pipeline.agent.assembly import SessionContextTracker

    tracker = SessionContextTracker()
    displayed_approvals: dict[str, str] = {}
    session_ready = False
    scope_override: str | None = None
    messages: list[dict[str, Any]] = []

    while True:
        status = inspect_episode(ep_dir)
        scope = scope_override or scope_of(status)
        if not session_ready:
            # 主会话的 messages **就在这一层**（M11 锚点不动）；登记这个列表对象之后，
            # 是否落盘只看 `messages is host.main_messages`，与参数无关。
            session_ready = True
            if _SESSION_HOST is not None:
                _SESSION_HOST.bind_main(messages)
                _seed_resume(ep_dir, messages, tracker, root)
        try:
            _sync_repl_displayed_approval(ep_dir, status, displayed_approvals)
        except Exception:
            pass
        if on_step and scout_entered_at is None:
            on_step(status.current_step)

        try:
            line = input(f"\nava [{ep_dir.name}] ({scope}) > ").strip()
        except (EOFError, KeyboardInterrupt):
            _settle_scout()
            print("\n[退出]")
            return 0

        # 🟡-1: 裸回车结算 scout span 并恢复停机点（用户肌肉记忆「我回来了」）
        if not line:
            if scout_entered_at is not None:
                _settle_scout()
            continue

        # 检查是否离开 scout 计时 span：下一条非 /scout 命令结算工时并恢复停机点
        if scout_entered_at is not None:
            is_scout_cmd = (
                line == "/scout"
                or line.startswith("/scout ")
                or ((line == "/patch" or line.startswith("/patch ")) and "--type" not in line)
            )
            if not is_scout_cmd:
                _settle_scout()
            else:
                # 连敲 /scout 噪音过滤：结算上一段（噪音由 record_human_time 过滤），重新开始新计时
                _settle_scout()
                scout_entered_at = time.time()

        if line in ("/quit", "/exit", "exit", "quit"):
            print("[退出]")
            return 0

        kind = classify_input(line)
        if kind == "shortcut":
            if line == "/help":
                print("\n支持的命令路由:")
                print("  /status      查看当期阶段状态与推荐命令")
                print("  /approvals   查看当期挂起的停机点审批对象")
                print("  /approve     批准停机点: /approve <stop> [--id <approval_id>]")
                print("  /reject      驳回停机点: /reject <stop> [--id <approval_id>] <哪段> <问题>")
                print("  /board       查看全局期看板")
                print("  /run <cmd>   安全执行白名单 pipeline 命令（先回显、按 y 确认）")
                print("  /voice       顺听极简纠错模式")
                print("  /save-script 停机点组件子命令族（等 8 个）: /save-script /seal-script /voice-info /voice-parse /voice-add /voice-revert /voice-retract /record-time")
                print("  /import-cover 导入封面图（REPL 形态）: /import-cover <源图片路径>")
                print("  /memory      查看跨期记忆全文（/memory show）")
                print("  /memory check 记忆自检（退出码 0 合法 / 1 不合法 / 2 不可达 / 3 合法但来源未确认）")
                print("  /memory ack  确认 ava 之外的改动并重新对齐 sha（仅交互终端）")
                print("  /memory digest 聚合各期驳回反馈，在独立子会话里提议记忆条目")
                print("  /scout       生成 pi 侦察派工单（缺料/缺笔记/标题候选）")
                print("  /patch       临时补料派工单（/scout --type patch 别名）")
                print("  /asset       切换至 asset scope (Phase 0 资产与云端调度)")
                print("  /pipeline    切回流水线工序模式")
                print("  /chat        creative scope 选题发散")
                print("  /script      聚焦写稿 (02-script.draft.md)")
                print("  /quit        退出 ava\n")
                print("  Ctrl-C       回合中按一次：停止本轮、先收尾再回到提示符；在提示符处按一次：退出 ava")
                print("  --continue   恢复本期的会话: ava <期> --continue [<会话号前缀>]（ava <期> --sessions 列出全部会话）")
                print("  提示: 手工指定含空格的路径参数时请用引号包裹（如 '/Volumes/Samsung T7/...'）。\n")
                continue

            if line == "/status":
                print(format_status(inspect_episode(ep_dir)))
                continue

            if line == "/approvals":
                try:
                    _print_pending_approvals(ep_dir, displayed_approvals)
                except Exception as exc:
                    print(f"[WARN] 读取审批队列失败: {exc}")
                continue

            if line == "/approve" or line.startswith("/approve "):
                _handle_repl_approve(ep_dir, line, displayed_approvals)
                continue

            if line == "/reject" or line.startswith("/reject "):
                _handle_repl_reject(ep_dir, line, displayed_approvals)
                continue

            if line == "/board":
                eps, hidden = get_episodes_list(root=root)
                print_board(eps, hidden)
                continue

            if line == "/voice":
                run_voice_session(ep_dir)
                continue

            if line == "/memory" or line.startswith("/memory "):
                run_memory_command(line, ep_dir, root=root)
                continue

            if line == "/patch" or line.startswith("/patch "):
                print("[*] 进入临时补料模式...")
                extra = line[6:].strip().split() if line.startswith("/patch ") else []
                if any(arg == "--type" or arg.startswith("--type=") for arg in extra):
                    print("[ERROR] /patch 别名已固定为 --type patch，如需指定其他类型请使用 /scout。", file=sys.stderr)
                    continue
                _close_stop(ep_dir)
                scout_entered_at = time.time()
                rc = _exec_scout(ep_dir, ["--type", "patch"] + extra)
                if rc != 0:
                    _settle_scout()
                continue

            if line == "/scout" or line.startswith("/scout "):
                _close_stop(ep_dir)
                scout_entered_at = time.time()
                extra = line[6:].strip().split() if line.startswith("/scout ") else []
                rc = _exec_scout(ep_dir, extra)
                if rc != 0:
                    _settle_scout()
                continue

            if line == "/asset":
                scope_override = "asset"
                print("[*] 已进入 asset scope（放行 Phase 0 资产与云端调度命令，输入 /pipeline 可切回）")
                continue

            if line == "/pipeline":
                scope_override = None
                print("[*] 已切回自动推导工序模式")
                continue

            if line == "/chat":
                run_agent_loop(ep_dir, scope_mode="creative", extra_prompt="", root=root)
                continue

            if line == "/script":
                run_agent_loop(
                    ep_dir,
                    scope_mode="creative",
                    extra_prompt="本轮聚焦写稿：产出只许写 02-script.draft.md；写完提示跑 check_script。",
                    root=root,
                )
                continue

            if line.startswith("/run"):
                cmd_part = line[4:].strip()
                if not cmd_part:
                    print("[ERROR] /run 需要指定命令，例如: /run tts --redo 3")
                    continue

                # 校验命令并自动补齐当前期目录参数（🔴 1 修复；/run 与 LLM 工具表共用
                # run_pipeline 这一个入口，防止校验器两处实现分叉）
                outcome = run_pipeline(cmd_part, episode_dir=ep_dir, scope=scope)
                if not outcome["ok"]:
                    print(f"[REJECT] {outcome['message']}")
                    continue

                # 停机点标签判定
                stop = human_stop_of(status.current_step)
                stop_label = None
                if stop is not None:
                    module_hit = any(
                        m in outcome["argv"] or f"pipeline.{m}" in outcome["argv"]
                        for m in ("tts", "clips", "render")
                    )
                    if module_hit:
                        stop_label = f"[{stop}] 当前处于停机点 {status.current_step}"

                # 命令回显与二次确认（Spec §2.4, §3.2 统一审批卡片）
                card = render_approval_card(
                    "run_pipeline",
                    {"command": cmd_part},
                    argv=outcome["argv"],
                    stop_label=stop_label,
                    episode_dir=ep_dir,
                )
                cmd_str = " ".join(outcome["argv"])
                print(f"\n  待执行: {cmd_str}")
                print(card, end="")
                t_card = time.time()
                try:
                    confirm = input().strip().lower()
                except EOFError:
                    print("\n[CANCEL] 已取消执行")
                    log_approval_decision(ep_dir, "run_pipeline", cmd_str, "n", latency_s=time.time() - t_card)
                    continue

                latency_s = time.time() - t_card
                norm_confirm = "y" if confirm == "y" else "n"
                log_approval_decision(ep_dir, "run_pipeline", cmd_str, norm_confirm, latency_s=latency_s)
                if confirm != "y":
                    print("[CANCEL] 已取消执行")
                    continue

                check_code_freeze()
                cmd_str = " ".join(outcome["argv"])
                print(f"[*] 正在执行: {cmd_str} ...")
                result = run_pipeline(cmd_part, episode_dir=ep_dir, scope=scope, confirmed=True)
                if result["ok"]:
                    print(f"[OK] 执行完成（耗时: {result['duration_s']:.1f}s）")
                else:
                    print(f"[FAIL] 执行失败，退出码: {result['returncode']}")
                continue

            # Spec 11 §3.1：停机点组件子命令与裸形态共用同一份分派
            rc = _dispatch_stop_point(ep_dir, line.split())
            if rc is not None:
                continue

            # Spec 12 §3.1：图片导入（REPL 形态：源路径由 core 读文件）
            rc = _dispatch_import_cover(ep_dir, line.split(), from_stdin=False)
            if rc is not None:
                continue

            print(f"[ERROR] 未知命令: '{line}'。输入 /help 查看命令列表。")
            continue

        # classify_input == "chat" -> Director 回合
        _dispatch_agent_turn(
            line,
            messages,
            ep_dir,
            scope,
            status,
            extra_prompt="",
            root=root,
            tracker=tracker,
        )


def _print_sessions(ep_dir: Path) -> None:
    """`ava <期> --sessions`：列出全部会话（含不可恢复的，如实标注）。"""
    summaries = list_sessions(read_log(ep_dir))
    if not summaries:
        print(f"[会话] {ep_dir.name} 没有任何会话记录。")
        return
    print(f"[会话] {ep_dir.name} 共 {len(summaries)} 个会话：")
    for summary in summaries:
        mark = "" if summary.resumable else "（无 assistant 消息，不能作 --continue 默认目标）"
        print(f"  {summary.sid[:8]} · {summary.messages} 条消息 · 最后活动 {summary.last_activity} {mark}")


def _latest_episode_with_log() -> Path | None:
    """`ava --continue`（不带期）：选 session.jsonl 最近写入的可见期。"""
    episodes, _hidden = get_episodes_list()
    best: tuple[float, Path] | None = None
    for episode in episodes:
        log = episode / LOG_NAME
        try:
            mtime = log.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, episode)
    return best[1] if best else None


def _continue_repl(ep_dir: Path, prefix: str | None) -> int:
    """`--continue [<前缀>]`：**读文件之前**先取租约（§2.5），拿不到就退出 3。"""
    summaries = list_sessions(read_log(ep_dir))
    target, candidates = resume_target(summaries, prefix)
    if candidates:
        print("[会话] 前缀不唯一，候选：", file=sys.stderr)
        for summary in candidates:
            print(f"  {summary.sid[:8]} · {summary.messages} 条消息 · {summary.last_activity}",
                  file=sys.stderr)
        return 2
    if target is None:
        print("[会话] 该期没有可恢复的会话，本次开新会话。")
    with activate_host(ep_dir) as host:
        try:
            host.lease = EpisodeLease.acquire(ep_dir)
        except (SessionLocked, SessionLogBroken) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 3
        if target is not None:
            state = prepare_resume(host, target.sid)
            if state["status"] != "resumed":
                print(f"[会话] 无法恢复（{state['status']}），本次开新会话。")
                host.resume_state = None
        return _run_repl_body(ep_dir)


def resolve_episode_target(target: str) -> Path | None:
    """将参数解析为期目录。"""
    p = Path(target)
    if p.exists() and p.is_dir():
        return p.resolve()

    candidate = paths.ROOT / "data" / "episodes" / target
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()

    # 尝试在两级子目录下寻找（如 EGOIST 企划子期）
    ep_root = paths.ROOT / "data" / "episodes"
    if ep_root.exists():
        for matched in ep_root.glob(f"*/{target}"):
            if matched.is_dir():
                return matched.resolve()
        for matched in ep_root.rglob(target):
            if matched.is_dir():
                return matched.resolve()

    return None


def _print_idea_non_tty_help() -> None:
    """非 TTY 环境下打印 idea 会话说明（Spec §3.3）。"""
    print(
        "ava idea: 无期选题会话（idea scope）\n"
        "说明: 该模式为交互式选题与立项讨论，写权限为零，需在交互终端（TTY）中运行。\n"
        "等价手动路径: 人工阅读 data/library/notes/ 中的番剧笔记，确定选题与张力后，运行 'ava new <期名>' 创建新期。"
    )


def main(argv: list[str] | None = None) -> int:
    """ava 统一入口。"""
    args = argv if argv is not None else sys.argv[1:]

    # 子命令 0: ava --continue [<会话号前缀>] / ava --sessions（不带期）
    if args and args[0] in ("--continue", "--sessions"):
        if not sys.stdin.isatty():
            print(f"[ERROR] {args[0]} 只在交互终端可用（非 TTY 不进入 REPL）", file=sys.stderr)
            return 2
        if args[0] == "--sessions":
            if len(args) != 1:
                print("[ERROR] 用法: ava --sessions（列期请用 ava --sessions 之外的形式）", file=sys.stderr)
                return 2
            print("[ERROR] 用法: ava <期> --sessions", file=sys.stderr)
            return 2
        if len(args) > 2:
            print("[ERROR] 用法: ava --continue [<会话号前缀>]", file=sys.stderr)
            return 2
        ep_dir = _latest_episode_with_log()
        if ep_dir is None:
            print("[会话] 没有任何期存在会话记录。")
            return 0
        print(f"[会话] 最近写入的期：{ep_dir.name}")
        return _continue_repl(ep_dir, args[1] if len(args) > 1 else None)

    # 子命令 1: ava new <期名>
    if args and args[0] == "new":
        if len(args) != 2:
            print("[ERROR] 用法: ava new <期名>（期名恰好一个）", file=sys.stderr)
            return 2
        rc = create_new_episode(args[1])
        if rc != 0:
            return rc
        if not sys.stdin.isatty():
            return 0
        new_ep_dir = paths.ROOT / "data" / "episodes" / args[1]
        return run_repl(new_ep_dir)

    # 无参数：看板模式
    if not args:
        ep_root = paths.ROOT / "data" / "episodes"
        if not ep_root.exists():
            print(
                f"[ERROR] data 目录不可达：{ep_root}\n"
                "可能外置硬盘未挂载或尚未初始化。请挂载硬盘或显式指定期目录路径：\n"
                "  ava <期目录路径>",
                file=sys.stderr,
            )
            return 2

        episodes, hidden_count = get_episodes_list()
        print_board(episodes, hidden_count)

        # 非 tty 降级（Spec §2.3 B2-r5）
        if not sys.stdin.isatty():
            return 0

        target = select_episode_interactive(episodes)
        if target == IDEA_KEYWORD:
            return run_agent_loop(None, scope_mode="idea")
        if not target:
            return 0
        return run_repl(target)

    # 子命令 2: ava idea (无期选题会话)
    if args[0] == IDEA_KEYWORD:
        if len(args) > 1:
            print(f"[ERROR] '{IDEA_KEYWORD}' 不接受多余参数: {' '.join(args[1:])}", file=sys.stderr)
            return 1
        if not sys.stdin.isatty():
            _print_idea_non_tty_help()
            return 0
        return run_agent_loop(None, scope_mode="idea")

    # 传了期目录或期号
    target_arg = args[0]
    ep_dir = resolve_episode_target(target_arg)
    if not ep_dir:
        print(f"[ERROR] 目标期目录不存在：{target_arg}", file=sys.stderr)
        return 1

    # 带子命令，例如 `ava <期> /voice` 或 `ava <期> /run tts`
    if len(args) > 1:
        # Spec 11 §3.1：停机点深度组件的八个子命令按 argv 位置无损取参（等号形式），
        # 与 /approve、/reject 同级，不经 validate_pipeline_command、不进 LLM 工具表。
        rc = _dispatch_stop_point(ep_dir, args[1:])
        if rc is not None:
            return rc

        # Spec 12 §3.1：图片导入（裸形态：图片字节走 stdin；不进 Spec 11 的八命令表）
        rc = _dispatch_import_cover(ep_dir, args[1:], from_stdin=True)
        if rc is not None:
            return rc

        # Spec 9 §2.5：`ava <期> --sessions` 与 `ava <期> --continue [前缀]`
        if args[1] == "--sessions":
            if len(args) != 2:
                print("[ERROR] 用法: ava <期> --sessions", file=sys.stderr)
                return 2
            _print_sessions(ep_dir)
            return 0
        if args[1] == "--continue":
            if len(args) > 3:
                print("[ERROR] 用法: ava <期> --continue [<会话号前缀>]", file=sys.stderr)
                return 2
            if not sys.stdin.isatty():
                print("[ERROR] --continue 只在交互终端可用（非 TTY 不进入 REPL）", file=sys.stderr)
                return 2
            return _continue_repl(ep_dir, args[2] if len(args) > 2 else None)

        # Spec 3 §4.2 (S3-R1/R2/R6)：裸形态 /approvals、/approve、/reject 必须在
        # `sub_cmd = " ".join(args[1:])` 之前按 argv 位置无损取参
        if args[1] == "/approvals":
            if len(args) != 2:
                print("[ERROR] 用法: ava <期> /approvals", file=sys.stderr)
                return 2
            _print_pending_approvals(ep_dir)
            return 0

        if args[1] == "/approve":
            ok_bare = (
                len(args) == 5
                and args[2] in HUMAN_STOPS
                and args[3] == "--id"
                and bool(args[4].strip())
            )
            # S3-R12：09 定稿形态——空格固定位置（与 REPL 同一套语法；shell:false 下
            # argv 元素天然逐字节无损）。args[5:9] = --cover / <路径> / --title / <标题整元素>
            ok_09 = (
                len(args) == 9
                and args[2] == "09"
                and args[3] == "--id"
                and bool(args[4].strip())
                and args[5] == "--cover"
                and args[7] == "--title"
            )
            if not (ok_bare or ok_09):
                print(
                    "[ERROR] 用法: ava <期> /approve <02.5|03.5|05|09> --id <approval_id>"
                    " [--cover <07-cover 相对路径> --title <标题>]（cover/title 仅 09，两个都要给）",
                    file=sys.stderr,
                )
                return 2
            # 结构由 argv 位置定死，语义（封面是否存在、标题长度）一律交领域层拒
            cli_finalize = {"cover": args[6], "title": args[8]} if ok_09 else None
            from pipeline import approvals
            from pipeline.approvals import ApprovalError

            try:
                res = approvals.approve(
                    ep_dir,
                    args[2],  # type: ignore[arg-type]
                    approval_id=args[4],
                    source="cli",
                    finalize=cli_finalize,
                )
                print(f"[OK] 已批准 {res.type} ({res.approval_id})")
                return 0
            except ApprovalError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                return 1

        if args[1] == "/reject":
            if (
                len(args) < 7
                or args[2] not in HUMAN_STOPS
                or args[3] != "--id"
                or not args[4].strip()
                or not args[5].strip()
            ):
                print(
                    "[ERROR] 用法: ava <期> /reject <02.5|03.5|05|09> --id <approval_id> <哪段> <问题...>",
                    file=sys.stderr,
                )
                return 2
            problem_str = " ".join(args[6:])
            if not problem_str.strip():
                print(
                    "[ERROR] 用法: ava <期> /reject <02.5|03.5|05|09> --id <approval_id> <哪段> <问题...>",
                    file=sys.stderr,
                )
                return 2
            from pipeline import approvals
            from pipeline.approvals import ApprovalError

            try:
                res = approvals.reject(
                    ep_dir,
                    args[2],  # type: ignore[arg-type]
                    {"target": args[5], "problem": problem_str},
                    approval_id=args[4],
                    source="cli",
                )
                print(f"[OK] 已驳回 {res.type} ({res.approval_id}) → _agent/approval_feedback.md")
                return 0
            except ApprovalError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                return 1

        sub_cmd = " ".join(args[1:])
        if sub_cmd == "/status":
            print(format_status(inspect_episode(ep_dir)))
            return 0
        if sub_cmd.startswith("/run"):
            status = inspect_episode(ep_dir)
            scope = scope_of(status)
            check_code_freeze()
            outcome = run_pipeline(
                sub_cmd[4:].strip(), episode_dir=ep_dir, scope=scope, confirmed=True
            )
            if not outcome["ok"] and outcome["returncode"] is None:
                print(f"[REJECT] {outcome['message']}", file=sys.stderr)
                return 1
            if not outcome["ok"]:
                print(f"[FAIL] {outcome['message']}", file=sys.stderr)
            return outcome["returncode"] or 0
        if sub_cmd == "/voice":
            return run_voice_session(ep_dir)
        if sub_cmd == "/patch" or sub_cmd.startswith("/patch "):
            extra = sub_cmd[6:].strip().split() if sub_cmd.startswith("/patch ") else []
            if any(arg == "--type" or arg.startswith("--type=") for arg in extra):
                print("[ERROR] /patch 别名已固定为 --type patch，如需指定其他类型请使用 /scout。", file=sys.stderr)
                return 1
            return _exec_scout(ep_dir, ["--type", "patch"] + extra)
        if sub_cmd == "/scout" or sub_cmd.startswith("/scout "):
            extra = sub_cmd[6:].strip().split() if sub_cmd.startswith("/scout ") else []
            return _exec_scout(ep_dir, extra)

        # Spec 3 §4.2 S3-R5：stdin 非 TTY 且带了未分派的子命令时，报错退出 2，不落入 REPL
        if not sys.stdin.isatty():
            print(
                f"[ERROR] 未识别的子命令：{args[1:]}（非交互环境不进入 REPL）",
                file=sys.stderr,
            )
            return 2

    return run_repl(ep_dir)


if __name__ == "__main__":
    raise SystemExit(main())
