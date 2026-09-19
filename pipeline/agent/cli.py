"""ava 统一 CLI 宿主、REPL 交互与工序调度器（Spec §2.3, §2.4, §2.6）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pipeline import paths
from pipeline.agent.resolver import scope_of
from pipeline.agent.scopes import load_scope
from pipeline.agent.tools import validate_pipeline_command
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


def record_human_time(ep_dir: Path, stop: str, entered_at: float, left_at: float) -> None:
    """记录人类停机点墙钟时间（Spec §2.6 追加式写入 human_time.json）。"""
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


def get_episodes_list() -> tuple[list[Path], int]:
    """获取所有期目录，排除 '.' 与 '_' 前缀，按 mtime 降序排列（Spec §2.3）。"""
    ep_root = paths.ROOT / "data" / "episodes"
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


def run_repl(ep_dir: Path) -> int:
    """REPL 交互循环（Spec §2.4）。"""
    check_code_freeze()
    print(f"\n已就绪：{ep_dir.name}")
    print("输入 /help 查看命令，输入 /status 查看状态，输入 /quit 退出。")

    scope_override: str | None = None

    while True:
        status = inspect_episode(ep_dir)
        scope = scope_override or scope_of(status)

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
            continue

        if line == "/status":
            print(format_status(inspect_episode(ep_dir)))
            continue

        if line == "/board":
            eps, hidden = get_episodes_list()
            print_board(eps, hidden)
            continue

        if line == "/voice":
            print(f"[*] 进入顺听纠错模式 (PR2 实现)...")
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
            print(f"[*] 进入 creative 对话模式 (当前 scope: {scope})...")
            continue

        if line == "/script":
            print(f"[*] 进入聚焦写稿模式 (产出只许 02-script.draft.md)...")
            continue

        if line.startswith("/run"):
            cmd_part = line[4:].strip()
            if not cmd_part:
                print("[ERROR] /run 需要指定命令，例如: /run tts --redo 3")
                continue

            # 校验命令并自动补齐当前期目录参数（🔴 1 修复）
            valid, msg, norm_cmd = validate_pipeline_command(cmd_part, scope=scope, ep_dir=ep_dir)
            if not valid:
                print(f"[REJECT] {msg}")
                continue

            # 命令回显与二次确认（Spec §2.4）
            cmd_str = " ".join(norm_cmd)
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
            try:
                res = subprocess.run(norm_cmd, cwd=paths.ROOT)
            except FileNotFoundError as exc:
                print(f"[FAIL] 执行器未找到: {exc}")
                continue
            t_end = time.time()
            if res.returncode == 0:
                print(f"[OK] 执行完成（耗时: {t_end - t_start:.1f}s）")
            else:
                print(f"[FAIL] 执行失败，退出码: {res.returncode}")
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
            valid, msg, norm_cmd = validate_pipeline_command(sub_cmd[4:].strip(), scope=scope, ep_dir=ep_dir)
            if not valid:
                print(f"[REJECT] {msg}", file=sys.stderr)
                return 1
            check_code_freeze()
            try:
                res = subprocess.run(norm_cmd, cwd=paths.ROOT)
                return res.returncode
            except FileNotFoundError as exc:
                print(f"[FAIL] 执行器未找到: {exc}", file=sys.stderr)
                return 1
        if sub_cmd == "/voice":
            print(f"[*] 直达顺听纠错模式 (PR2)...")
            return 0
        if sub_cmd == "/patch":
            print(f"[*] 直达临时补料模式 (PR3)...")
            return 0

    return run_repl(ep_dir)


if __name__ == "__main__":
    raise SystemExit(main())
