"""状态卡纯函数组装器（Spec §1.3, §2.2, §6 PR5）。

输入：期目录与 EpisodeStatus。
输出：注入 messages[0] 的紧凑状态卡字符串（目标 ≤ 400 字符）。
纪律：
1. 只放元数据，不放文件正文；
2. 产物存在性只报阶段名 + ✓/✗，不写受限路径与内容；
3. 返回前对整串做一次 RESTRICTED_EGRESS_PATTERNS casefold 清洗，命中替换为 [已脱敏]；
4. next_command 剥掉期目录绝对路径前缀。
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from pipeline.agent.resolver import scope_of
from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS
from pipeline.status import EpisodeStatus, inspect_episode


def _check_artifact_exists(d: Path, rel: str) -> bool:
    try:
        p = d / rel
        return p.exists()
    except Exception:
        return False


def _check_audio_exists(d: Path) -> bool:
    try:
        audio_dir = d / "03-audio"
        if not audio_dir.is_dir():
            return False
        if (audio_dir / "manifest.json").exists():
            return True
        return any(not f.name.startswith(".") for f in audio_dir.iterdir())
    except Exception:
        return False


def _read_human_time_minutes(d: Path) -> float:
    ht_path = d / "human_time.json"
    if not ht_path.exists():
        return 0.0
    try:
        records = json.loads(ht_path.read_text(encoding="utf-8"))
        if isinstance(records, list):
            return sum(r.get("minutes", 0.0) for r in records if isinstance(r, dict))
    except Exception:
        pass
    return 0.0


def _strip_episode_prefix(command: str | None, ep_dir: Path) -> str:
    if not command:
        return "无"
    ep_str = str(ep_dir.resolve())
    cleaned = command.replace(ep_str + "/", "").replace(ep_str, "")
    return re.sub(r"\s+", " ", cleaned).strip() or "无"


def build_status_card(
    ep_dir: Path | str,
    status: EpisodeStatus | None = None,
    scope: str | None = None,
) -> str:
    """构建注入 system prompt 的紧凑状态卡（纯函数，目标 ≤ 400 字符）。"""
    d = Path(ep_dir).resolve()
    if status is None:
        status = inspect_episode(d)

    effective_scope = scope or scope_of(status)
    name = status.episode_name or d.name
    step = status.current_step
    blocked_str = "是" if status.is_blocked else "否"
    if status.is_blocked and status.block_reason:
        blocked_str = f"是（{status.block_reason}）"

    next_cmd = _strip_episode_prefix(status.next_command, d)

    has_topic = _check_artifact_exists(d, "01-topic.md")
    has_draft = _check_artifact_exists(d, "02-script.draft.md")
    has_script = _check_artifact_exists(d, "02-script.md")
    has_audio = _check_audio_exists(d)
    has_clips = _check_artifact_exists(d, "04-clips.json")
    has_approved = _check_artifact_exists(d, "04-clips.approved.json")
    has_final = _check_artifact_exists(d, "05-final.mp4")

    checklist = (
        f"选题 {'✓' if has_topic else '✗'} / "
        f"草稿 {'✓' if has_draft else '✗'} / "
        f"定稿 {'✓' if has_script else '✗'} / "
        f"配音产物 {'✓' if has_audio else '✗'} / "
        f"排片 {'✓' if has_clips else '✗'} / "
        f"审片 {'✓' if has_approved else '✗'} / "
        f"成片 {'✓' if has_final else '✗'}"
    )

    ht_min = _read_human_time_minutes(d)
    advisories_str = "；".join(status.advisories) if status.advisories else "无"

    card = (
        f"[状态卡]\n"
        f"期名: {name} | 工序: {step} | 阻塞: {blocked_str} | scope: {effective_scope} | 人时: {ht_min:.1f}m\n"
        f"推荐命令: {next_cmd}\n"
        f"产物: {checklist}\n"
        f"提示: {advisories_str}"
    )

    # 返回前对整串做一次 RESTRICTED_EGRESS_PATTERNS 清洗（casefold 判定，命中替换为 [已脱敏]）
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        escaped = re.escape(pattern)
        card = re.sub(escaped, "[已脱敏]", card, flags=re.IGNORECASE)

    return card


def build_idea_card() -> str:
    """构建无期选题会话（idea scope）的静态状态卡（纯函数，目标 ≤ 400 字符）。"""
    return (
        "[状态卡]\n"
        "模式: 选题会话（无期） | scope: idea | 写权限: 无（机制保证）\n"
        "读域: data/library/ 与跨期 read_status\n"
        "产出落盘: 讨论定稿后运行 ava new <名>，在新期会话中完成写入"
    )



def _matches_flag_prefix(tokens: list[str], full_flag: str) -> bool:
    """匹配完整旗标或其 argparse 缩写前缀（如 --app 匹配 --approve）。"""
    for t in tokens:
        if t.startswith("--") and len(t) > 2 and full_flag.startswith(t):
            return True
    return False


def render_approval_card(
    name: str,
    args: dict[str, Any],
    argv: list[str] | None = None,
    stop_label: str | None = None,
    *,
    target_exists: bool | None = None,
    episode_dir: Path | str | None = None,
) -> str:
    """标准化人机审批卡片渲染纯函数（Spec §3.2, §6 PR6）。

    版式：
    1. 执行审批（run_pipeline 普通命令 / render 长任务）
    2. 计费审批（run_pipeline cloud up/run/push/pull）
    3. 写入审批（write_episode_file）
    危险标记由宿主静态规则打，不依赖模型自报。
    """
    args = dict(args or {})
    argv_list = list(argv) if argv is not None else []

    if name == "write_episode_file":
        raw_filename = str(args.get("filename", "")).strip()
        first_line = raw_filename.splitlines()[0] if raw_filename else ""
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", first_line).strip()
        content = args.get("content", "")
        content_bytes = len(content.encode("utf-8")) if isinstance(content, str) else 0
        size_kb = content_bytes / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb >= 0.1 else f"{content_bytes} B"

        is_overwrite = target_exists
        if is_overwrite is None:
            ep = episode_dir or args.get("episode_dir")
            if ep and filename:
                try:
                    is_overwrite = (Path(ep) / filename).exists()
                except Exception:
                    is_overwrite = False
            else:
                is_overwrite = False

        if is_overwrite:
            status_str = "覆盖现有文件"
            if "draft" in filename:
                danger_str = "[覆盖] 现有草稿将被替换"
            else:
                danger_str = "[覆盖] 现有文件将被替换"
        else:
            status_str = "新建文件"
            danger_str = "无"

        target_str = f"{filename}（{size_str}，{status_str}）" if filename else size_str
        lines = [
            "┌─ 写入审批 ──────────────────────────────────────────",
            f"│ 工具: {name}",
            f"│ 目标: {target_str}",
            f"│ 危险标记: {danger_str}",
            "└─ 执行? [y/N]: ",
        ]
        return "\n".join(lines)

    # run_pipeline 或通用执行类工具
    cmd_str = " ".join(argv_list) if argv_list else str(args.get("command", "")).strip()

    # 分析模块与子命令
    cloud_sub = None
    is_cloud = False
    is_render = False

    if "pipeline.cloud" in argv_list or "cloud" in argv_list:
        is_cloud = True
        idx = argv_list.index("pipeline.cloud") if "pipeline.cloud" in argv_list else argv_list.index("cloud")
        if idx + 1 < len(argv_list):
            cloud_sub = argv_list[idx + 1]
    elif "pipeline.render" in argv_list or "render" in argv_list:
        is_render = True
    else:
        # 从 cmd_str 或 args["command"] 分析
        raw_cmd = str(args.get("command", "")).strip()
        tokens = raw_cmd.split()
        if tokens:
            if tokens[0] == "cloud":
                is_cloud = True
                if len(tokens) > 1:
                    cloud_sub = tokens[1]
            elif tokens[0] == "render":
                is_render = True

    if "pipeline.render" in cmd_str:
        is_render = True

    is_billing = is_cloud and (cloud_sub in {"up", "run", "push", "pull"})

    danger_tags: list[str] = []
    if is_billing:
        danger_tags.append("[计费] ☁计费 实例开机将产生费用，关机才停止")
    if is_render:
        danger_tags.append("[长任务] 渲染耗时较长（分钟级）")

    all_tokens = argv_list + cmd_str.split()

    # [停机点] 判定：argv 含 --approve（或其缩写如 --app），或调用方传入 stop_label
    if _matches_flag_prefix(all_tokens, "--approve"):
        if stop_label:
            tag = stop_label if "[停机点]" in stop_label else f"[停机点] {stop_label}"
            danger_tags.append(tag)
        else:
            danger_tags.append("[停机点] 批准操作将产生解封物并推进工序")
    elif stop_label:
        tag = stop_label if "[停机点]" in stop_label else f"[停机点] {stop_label}"
        danger_tags.append(tag)

    # [跳过人工闸] 判定：argv 含 --confirm-patch（或其缩写如 --conf）
    if _matches_flag_prefix(all_tokens, "--confirm-patch"):
        danger_tags.append("[跳过人工闸] 补丁段二次确认将被跳过")

    danger_str = " ".join(danger_tags) if danger_tags else "无"

    # 解封物判定（🔵 终审：review --approve 产出 04-clips.approved.json，非「只读产物」）
    is_review_approve = _matches_flag_prefix(all_tokens, "--approve") and (
        "review" in cmd_str or any("review" in t for t in argv_list)
    )

    if is_cloud:
        nature = "☁ 云端计费动作（计费审批）"
    elif is_render:
        nature = "本地成片渲染 | 预计耗时较长（分钟级）"
    elif is_review_approve:
        nature = "产生解封物（推进工序，不可回退）"
    else:
        nature = "本地只读产物生成 | 预计分钟级"

    lines = [
        "┌─ 执行审批 ──────────────────────────────────────────",
        f"│ 工具: {name}",
        f"│ 命令: {cmd_str}",
        f"│ 性质: {nature}",
        f"│ 危险标记: {danger_str}",
        "└─ 执行? [y/N]: ",
    ]
    return "\n".join(lines)


def log_approval_decision(
    ep_dir: Path | str | None,
    tool_name: str,
    target: str,
    decision: str,
    latency_s: float | None = None,
) -> None:
    """追加一行审批记录到期目录 _agent/approvals.jsonl（Spec §3.2-6, §5 M17）。

    `latency_s` = 卡片弹出→人类按键的决策耗时（秒）。它是 §7-1「秒按 y」
    审批疲劳判据的唯一可读量：只有卡片时间戳无法区分「秒敲」与「读完后敲」。

    .jsonl 后缀天然在读域白名单外，不进读域。
    """
    if not ep_dir:
        return
    d = Path(ep_dir)
    agent_dir = d / "_agent"
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        log_file = agent_dir / "approvals.jsonl"
        norm_decision = "y" if decision.strip().lower() in ("y", "yes") else "n"
        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tool": tool_name,
            "target": target,
            "command": target,
            "decision": norm_decision,
            "decision_latency_s": round(latency_s, 3) if latency_s is not None else None,
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[WARN] 审批记账失败: {exc}")


