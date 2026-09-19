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


def build_status_card(ep_dir: Path | str, status: EpisodeStatus | None = None) -> str:
    """构建注入 system prompt 的紧凑状态卡（纯函数，目标 ≤ 400 字符）。"""
    d = Path(ep_dir).resolve()
    if status is None:
        status = inspect_episode(d)

    scope = scope_of(status)
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
        f"期名: {name} | 工序: {step} | 阻塞: {blocked_str} | scope: {scope} | 人时: {ht_min:.1f}m\n"
        f"推荐命令: {next_cmd}\n"
        f"产物: {checklist}\n"
        f"提示: {advisories_str}"
    )

    # 返回前对整串做一次 RESTRICTED_EGRESS_PATTERNS 清洗（casefold 判定，命中替换为 [已脱敏]）
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        escaped = re.escape(pattern)
        card = re.sub(escaped, "[已脱敏]", card, flags=re.IGNORECASE)

    return card
