"""ava 统一 CLI 宿主、REPL 交互与工序调度器（Spec §2.3, §2.4, §2.6）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from pipeline import paths
from pipeline.agent.resolver import scope_of
from pipeline.agent.scopes import load_scope
from pipeline.agent.tools import run_pipeline
from pipeline.status import format_status, inspect_episode

# 人类停机点集合（Spec §2.6）
HUMAN_STOPS: set[str] = {"02.5", "03.5", "05", "09"}


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


def record_human_time(ep_dir: Path, stop: str, entered_at: float, left_at: float) -> float:
    """记录人类停机点墙钟时间（Spec §2.6 追加式写入 human_time.json），返回本段分钟数。"""
    ht_path = ep_dir / "human_time.json"
    records: list[dict] = []
    if ht_path.exists():
        try:
            records = json.loads(ht_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []

    minutes = round((left_at - entered_at) / 60.0, 2)
    records.append({
        "stop": stop,
        "entered_at": datetime.fromtimestamp(entered_at).isoformat(),
        "left_at": datetime.fromtimestamp(left_at).isoformat(),
        "minutes": max(0.0, minutes),
    })
    paths.atomic_write(ht_path, json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    return max(0.0, minutes)


def human_stop_of(current_step: str) -> str | None:
    """current_step 字符串 → 停机点标签（HUMAN_STOPS 的唯一消费者）。

    「02.5 人审改稿」「03.5 配音顺听 / 04 排片」「05 审时间码」「09 人工发布」
    都是标签前缀形态；非停机点返回 None。
    """
    for stop in sorted(HUMAN_STOPS, key=len, reverse=True):
        if current_step.startswith(stop):
            return stop
    return None


def park_stop_of(current_step: str) -> str | None:
    """REPL 停留记账用的停机点：03.5 的墙钟由 /voice 自己记，此处排除以免双记。"""
    stop = human_stop_of(current_step)
    return None if stop == "03.5" else stop


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

    over_budget_streaks = 0
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
            if "超预算" in adv:
                over_budget_streaks += 1

    if hidden_count > 0:
        print(f"\n  (已隐藏 {hidden_count} 个下划线验证/临时目录)")

    if over_budget_streaks >= 3:
        print("\n  🛑 [警告] 连续多期超出人类时间预算！请停产复盘并优化工作流。")

    print("=" * 70)


def select_episode_interactive(episodes: list[Path]) -> Path | None:
    """交互选择期目录（Spec §2.3 输入语义：回车=1，数字=序号，字符串=期名）。"""
    if not episodes:
        return None

    while True:
        prompt = f"请选择期目录 [回车默认选 1: {episodes[0].name}]: "
        try:
            choice = input(prompt).strip()
        except EOFError:
            return None

        if not choice:
            return episodes[0]

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


def create_new_episode(ep_name: str) -> int:
    """创建新期目录与 01-topic.md 模板（Spec §5 PR0/PR1 B3-r9）。"""
    ep_root = paths.ROOT / "data" / "episodes"
    target_dir = ep_root / ep_name
    if target_dir.exists():
        print(f"[ERROR] 期目录已存在：{target_dir}，拒绝覆盖！", file=sys.stderr)
        return 1

    target_dir.mkdir(parents=True, exist_ok=False)
    topic_content = (
        f"# {ep_name} 选题配置\n\n"
        "- 番剧：\n"
        "- 类型：\n"
        "- 锚点：\n"
        "- 张力：\n"
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
                    confirm = "y"

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


def run_creative_loop(ep_dir: Path, mode: str = "chat") -> int:
    """creative scope 对话写稿闭环（Spec §2.4, §5 PR4）。

    LLM 不可用时当场降级为本地纯指示模式（不装作有 LLM 在场）。
    工具调用一律先经人确认（写 01-topic.md 是立项动作，必须人拍板）。
    """
    from pipeline.agent.llm import LLMError, load_llm_config, local_directive_message, run_tool_loop
    from pipeline.agent.tools import ToolContext

    extra = "" if mode == "chat" else (
        "\n\n本轮聚焦写稿：产出只许写 02-script.draft.md；写完提示跑 check_script。"
    )
    messages: list[dict] = [
        {"role": "system", "content": load_scope("creative").system_prompt + extra}
    ]
    ctx = ToolContext(scope="creative", episode_dir=ep_dir)

    if load_llm_config() is None:
        print(local_directive_message("creative", "缺少 config/agent.json 或环境变量密钥")["content"])
        return 0

    def _approve(name: str, args: dict) -> bool:
        print(f"\n  [工具请求] {name} {json.dumps(args, ensure_ascii=False)[:200]}")
        try:
            return input("  允许执行? [y/N]: ").strip().lower() == "y"
        except EOFError:
            return False

    while True:
        try:
            line = input(f"\nava [{ep_dir.name}] (creative) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出 creative 模式]")
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "quit", "exit", "/done"):
            print("[退出 creative 模式]")
            return 0

        messages.append({"role": "user", "content": line})
        try:
            outcome = run_tool_loop(messages, ctx=ctx, approve=_approve)
        except LLMError as exc:
            print(f"[FAIL] {exc}")
            messages.pop()
            continue
        except PermissionError as exc:
            # 出网闸在 chat_complete 里拦截（fail-closed）。这是内容问题不是程序崩，
            # 报清楚并留在会话里——整段 traceback 崩退会让人看不到真正原因。
            print(f"[BLOCKED] 出网被拦截：{exc}")
            print("          本次请求未发出。请改问不含受限内容（密钥/音频清单/补片素材）的问题。")
            messages.pop()
            continue
        messages = outcome["messages"]

        content = outcome["final"].get("content") or ""
        if content:
            print(f"\n{content}")
        if outcome["stopped"] == "max_iterations":
            print(f"[WARN] 工具调用已达上限 {outcome['iterations']} 轮，停止并交人接管。")
        elif outcome["stopped"] == "degraded":
            return 0


def run_repl(ep_dir: Path) -> int:
    """REPL 入口：包一层停机点墙钟记账（Spec §2.6），机身在 _run_repl_body。

    §2.6 的读数只在**人类停机点**计：进入某停机点 scope 到离开为止的墙钟。
    没有这一层，human_time.json 永远是空的，status 的第四条 advisory 与看板的
    「连续三期超预算」横幅就都是死判据（终审 P0-2）。
    """
    span: dict[str, object] = {"stop": None, "at": time.time()}

    def close_stop() -> None:
        stop = span["stop"]
        if stop:
            minutes = record_human_time(ep_dir, str(stop), float(span["at"]), time.time())
            print(f"\n[人时] 停机点 {stop} 墙钟 {minutes:.1f} 分钟 → human_time.json")
        span["stop"] = None

    def on_step(current_step: str) -> None:
        """工序变化 = 停机点切换：先结算上一段，再开新的一段。"""
        new_stop = park_stop_of(current_step)
        if new_stop == span["stop"]:
            return
        close_stop()
        span["stop"], span["at"] = new_stop, time.time()

    try:
        return _run_repl_body(ep_dir, on_step)
    finally:
        close_stop()


def _run_repl_body(ep_dir: Path, on_step) -> int:
    """REPL 交互循环（Spec §2.4）。on_step 用于停机点墙钟记账（§2.6）。"""
    check_code_freeze()
    print(f"\n已就绪：{ep_dir.name}")
    print("输入 /help 查看命令，输入 /status 查看状态，输入 /quit 退出。")

    scope_override: str | None = None

    while True:
        status = inspect_episode(ep_dir)
        scope = scope_override or scope_of(status)
        on_step(status.current_step)

        try:
            line = input(f"\nava [{ep_dir.name}] ({scope}) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            return 0

        if not line:
            continue

        if line in ("/quit", "/exit", "exit", "quit"):
            print("[退出]")
            return 0

        if line == "/help":
            print("\n支持的命令路由:")
            print("  /status      查看当期阶段状态与推荐命令")
            print("  /board       查看全局期看板")
            print("  /run <cmd>   安全执行白名单 pipeline 命令（先回显、按 y 确认）")
            print("  /voice       顺听极简纠错模式")
            print("  /patch       临时补料模式")
            print("  /asset       切换至 asset scope (Phase 0 资产与云端调度)")
            print("  /pipeline    切回流水线工序模式")
            print("  /chat        creative scope 选题发散")
            print("  /script      聚焦写稿 (02-script.draft.md)")
            print("  /quit        退出 ava\n")
            print("  提示: 手工指定含空格的路径参数时请用引号包裹（如 '/Volumes/Samsung T7/...'）。\n")
            continue

        if line == "/status":
            print(format_status(inspect_episode(ep_dir)))
            continue

        if line == "/board":
            eps, hidden = get_episodes_list()
            print_board(eps, hidden)
            continue

        if line == "/voice":
            run_voice_session(ep_dir)
            continue

        if line == "/patch":
            print(f"[*] 进入临时补料模式 (PR3 实现)...")
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
            run_creative_loop(ep_dir, "chat")
            continue

        if line == "/script":
            run_creative_loop(ep_dir, "script")
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

            # 命令回显与二次确认（Spec §2.4）
            cmd_str = " ".join(outcome["argv"])
            print(f"\n  待执行: {cmd_str}")
            try:
                confirm = input("  确认执行? [y/N]: ").strip().lower()
            except EOFError:
                print("\n[已取消]")
                continue

            if confirm != "y":
                print("[CANCEL] 已取消执行")
                continue

            check_code_freeze()
            print(f"[*] 正在执行: {cmd_str} ...")
            t_start = time.time()
            result = run_pipeline(cmd_part, episode_dir=ep_dir, scope=scope, confirmed=True)
            t_end = time.time()
            if result["ok"]:
                print(f"[OK] 执行完成（耗时: {t_end - t_start:.1f}s）")
            else:
                print(f"[FAIL] 执行失败，退出码: {result['returncode']}")
            continue

        print(f"[ERROR] 未知命令: '{line}'。输入 /help 查看命令列表。")


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


def main(argv: list[str] | None = None) -> int:
    """ava 统一入口。"""
    args = argv if argv is not None else sys.argv[1:]

    # 子命令 1: ava new <期号>
    if len(args) >= 2 and args[0] == "new":
        return create_new_episode(args[1])

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
        if not target:
            return 0
        return run_repl(target)

    # 传了期目录或期号
    target_arg = args[0]
    ep_dir = resolve_episode_target(target_arg)
    if not ep_dir:
        print(f"[ERROR] 目标期目录不存在：{target_arg}", file=sys.stderr)
        return 1

    # 带子命令，例如 `ava <期> /voice` 或 `ava <期> /run tts`
    if len(args) > 1:
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
        if sub_cmd == "/patch":
            print(f"[*] 直达临时补料模式 (PR3)...")
            return 0

    return run_repl(ep_dir)


if __name__ == "__main__":
    raise SystemExit(main())
