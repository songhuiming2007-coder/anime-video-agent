"""流水线状态诊断与工序路由工具。

根据当期落盘物理产物诊断当前阶段，识别是否处于人工停机点，并给出下一步唯一执行命令与操作文档。
杜绝 Agent 越过未批准节点盲目串联。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class EpisodeStatus:
    episode_dir: str
    episode_name: str
    current_step: str
    is_blocked: bool
    block_reason: str | None
    completed_steps: list[str]
    next_action: str
    next_command: str | None
    docs_ref: str


def inspect_episode(ep_dir: Path) -> EpisodeStatus:
    d = ep_dir.resolve()
    name = d.name

    has_topic = (d / "01-topic.md").exists()
    has_script_draft = (d / "02-script.draft.md").exists()
    has_script = (d / "02-script.md").exists()
    has_diff = (d / "02-diff.patch").exists()
    has_audio = (d / "03-audio" / "manifest.json").exists()
    has_clips = (d / "04-clips.json").exists()
    has_approved = (d / "04-clips.approved.json").exists()
    has_final = (d / "05-final.mp4").exists()
    has_qc = (d / "06-check.log").exists()
    has_cover = (d / "07-titles.md").exists() or (d / "07-cover").exists()

    completed: list[str] = []
    if has_topic:
        completed.append("01 选题")
    if has_script:
        completed.append("02 写稿")
    if has_audio:
        completed.append("03 配音")
    if has_clips:
        completed.append("04 排片")
    if has_approved:
        completed.append("05 审时间码")
    if has_final:
        completed.append("06 渲染")
    if has_qc:
        completed.append("07 质检")
    if has_cover:
        completed.append("08 封面标题")

    # 1. 未立项
    if not has_topic:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="01 选题",
            is_blocked=True,
            block_reason="缺少 01-topic.md 选题配置",
            completed_steps=[],
            next_action="请人类创建 01-topic.md 并填写番剧、类型、锚点与张力。",
            next_command=None,
            docs_ref="docs/runbook/01-topic.md",
        )

    # 2. 待写稿
    if not has_script and not has_script_draft:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="02 脚本写作",
            is_blocked=False,
            block_reason=None,
            completed_steps=completed,
            next_action="调 skills/write-script 撰写 02-script.md，并执行 check_script 机检。",
            next_command=f"python -m pipeline.check_script {d}/02-script.md",
            docs_ref="docs/runbook/02-script.md",
        )

    # 3. 待人审改稿 (02.5 停机点)
    if not has_audio:
        if not has_diff:
            return EpisodeStatus(
                episode_dir=str(d),
                episode_name=name,
                current_step="02.5 人审改稿",
                is_blocked=True,
                block_reason="🛑 处于人工停机点 1（02.5 人审改稿）！未见 02-diff.patch 封板确认。",
                completed_steps=completed,
                next_action="必须由人类总监通读 02-script.md，精修事实与张力，并提取 diff 封板：\n"
                f"  git diff --no-index {d}/02-script.draft.md {d}/02-script.md > {d}/02-diff.patch",
                next_command=f"python -m pipeline.tts {d} (人审确认后方可执行)",
                docs_ref="docs/runbook/02.5-human-review.md",
            )
        else:
            return EpisodeStatus(
                episode_dir=str(d),
                episode_name=name,
                current_step="03 语音合成",
                is_blocked=False,
                block_reason=None,
                completed_steps=completed,
                next_action="人审已封板。执行增量配音合成，严禁擅自使用 --force 全量覆盖！",
                next_command=f"python -m pipeline.tts {d}",
                docs_ref="docs/runbook/03-tts.md",
            )

    # 4. 配音已出，待顺听 / 待排片
    if not has_clips:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="03.5 配音顺听 / 04 排片",
            is_blocked=False,
            block_reason="🛑 人工停机点 2（03.5 顺听）：建议人耳抽检开头与最长段音频无错字发飘。",
            completed_steps=completed,
            next_action="顺听确认语速与音色正常后，启动三通道画面排片分派。",
            next_command=f"python -m pipeline.clips {d}",
            docs_ref="docs/runbook/04-clips.md",
        )

    # 5. 排片已出，待审时间码 (05 停机点)
    if not has_approved:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="05 审时间码",
            is_blocked=True,
            block_reason="🛑 处于人工停机点 3（05 审时间码）！排片已生成，未获 --approve 批准。",
            completed_steps=completed,
            next_action="严禁直接启动渲染！请在浏览器打开 04-review.html 抽检画面与台词，确认无画外音错配后执行批准：",
            next_command=f"python -m pipeline.review {d} --approve",
            docs_ref="docs/runbook/05-timecode.md",
        )

    # 6. 时间码已获批，待渲染
    if not has_final:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="06 本地渲染",
            is_blocked=False,
            block_reason=None,
            completed_steps=completed,
            next_action="时间码已获批。启动本地渲染流水线（切片、拼装、混 BGM、烧录字幕）。",
            next_command=f"python -m pipeline.render {d}",
            docs_ref="docs/runbook/06-render.md",
        )

    # 7. 渲染完成，待质检
    if not has_qc:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="07 自动质检",
            is_blocked=False,
            block_reason=None,
            completed_steps=completed,
            next_action="成片已出。执行 11 项机器硬指标质检门禁。",
            next_command=f"python -m pipeline.qc {d}",
            docs_ref="docs/runbook/07-qc.md",
        )

    # 8. 质检完成，待出封面与标题
    if not has_cover:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="08 封面与标题候选",
            is_blocked=False,
            block_reason=None,
            completed_steps=completed,
            next_action="质检已通过。生成封面候选帧联系表与 5 条标题候选（严禁自行定稿！）。",
            next_command=f"python -m pipeline.cover {d}",
            docs_ref="docs/runbook/08-cover-title.md",
        )

    # 9. 所有机器步骤完成，待人工发布 (09 停机点)
    return EpisodeStatus(
        episode_dir=str(d),
        episode_name=name,
        current_step="09 人工发布",
        is_blocked=True,
        block_reason="🛑 处于人工停机点 4（09 人工发布）！所有产物已齐备，待人类拍板发布。",
        completed_steps=completed,
        next_action="人类从 07-cover/ 中挑选 1 张封面，从 07-titles.md 中挑选 1 条标题，手动上传发布各平台。",
        next_command=None,
        docs_ref="docs/runbook/09-publish.md",
    )


def format_status(status: EpisodeStatus) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append(f"【期号】 {status.episode_name}")
    lines.append(f"【目录】 {status.episode_dir}")
    lines.append(f"【当前阶段】 {status.current_step}")

    if status.completed_steps:
        lines.append(f"【已完成】   {', '.join(status.completed_steps)}")
    else:
        lines.append("【已完成】   无")

    if status.is_blocked:
        lines.append(f"【状态】     {status.block_reason}")
    else:
        lines.append("【状态】     正常进行中（机器可执行）")

    lines.append(f"【下一步动作】\n  {status.next_action}")

    if status.next_command:
        lines.append(f"【推荐命令】\n  {status.next_command}")

    lines.append(f"【参考文档】 {status.docs_ref}")
    lines.append("=" * 64)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="anime-video-agent 流水线状态诊断与工序路由")
    parser.add_argument("target", nargs="?", default=None, help="期目录路径或期号名称")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出状态")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent

    target_path: Path | None = None
    if args.target:
        p = Path(args.target)
        if p.exists():
            target_path = p
        else:
            # 尝试在 data/episodes 下寻找
            candidate = root / "data" / "episodes" / args.target
            if candidate.exists():
                target_path = candidate
            else:
                target_path = p  # 传给 inspect 报错
    else:
        episodes_dir = root / "data" / "episodes"
        if not episodes_dir.exists():
            print(
                f"[ERROR] data 目录不可达：{episodes_dir}\n"
                "可能外置硬盘未挂载或尚未初始化。请挂载硬盘或显式指定期目录路径：\n"
                "  python -m pipeline.status <期目录路径>",
                file=sys.stderr,
            )
            return 2

        # 找最近修改的期目录
        subdirs = [
            d
            for d in episodes_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        if not subdirs:
            print(f"[INFO] {episodes_dir} 下暂无期目录。", file=sys.stderr)
            return 0
        subdirs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        target_path = subdirs[0]

    if not target_path.exists():
        print(f"[ERROR] 目标期目录不存在：{target_path}", file=sys.stderr)
        return 1

    status = inspect_episode(target_path)
    if args.json:
        print(json.dumps(asdict(status), ensure_ascii=False, indent=2))
    else:
        print(format_status(status))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
