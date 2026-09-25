"""流水线状态诊断与工序路由工具。

根据当期落盘物理产物诊断当前阶段，识别是否处于人工停机点，并给出下一步唯一执行命令与操作文档。
杜绝 Agent 越过未批准节点盲目串联。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .align import has_clips_approved_diff


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
    advisories: list[str] = field(default_factory=list)


def _detect_advisories(d: Path) -> list[str]:
    """常驻检测六条 advisory（Spec §2.2 + §2.6 + scout §4）。

    纪律：常驻性（解耦阶段）、坏文件免疫（各自 try/except）、零重依赖（内联轻逻辑）。
    """
    advisories: list[str] = []

    # 1. 03-audio/corrections.json 未完成条目检测
    corr_path = d / "03-audio" / "corrections.json"
    if corr_path.exists():
        try:
            items = json.loads(corr_path.read_text(encoding="utf-8"))
            if not isinstance(items, list):
                advisories.append("corrections.json 格式错误（非列表）")
            else:
                pending_count = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    applied = item.get("applied", False)
                    affected = item.get("affected")
                    done_segments = item.get("done_segments")
                    is_done = applied
                    if affected is not None and done_segments is not None:
                        if not set(affected).issubset(set(done_segments)):
                            is_done = False
                    if not is_done:
                        pending_count += 1
                if pending_count > 0:
                    mf_path = d / "03-audio" / "manifest.json"
                    engine_hint = "先看配音 manifest 的 engine 字段决定本地/云端"
                    if mf_path.exists():
                        try:
                            m_data = json.loads(mf_path.read_text(encoding="utf-8"))
                            engine = m_data.get("engine")
                            if engine == "qwen3_tts_cuda":
                                engine_hint = "这期是云端配音，apply 走 `cloud run`"
                            elif engine:
                                engine_hint = "本地配音，apply 走 `tts --apply-patch`"
                        except Exception:
                            pass
                    advisories.append(f"{pending_count} 条纠错待应用/待收尾（{engine_hint}）")
        except Exception as e:
            advisories.append(f"corrections.json 不可读：{e}")

    # 2. patch_assets/ 未入库文件检测
    patch_dir = d / "patch_assets"
    if patch_dir.is_dir():
        try:
            patch_files = [
                f for f in patch_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            if patch_files:
                pool_path = d / "04-patch" / "pool.json"
                registered_names: set[str] = set()
                if pool_path.exists():
                    try:
                        p_data = json.loads(pool_path.read_text(encoding="utf-8"))
                        assets = []
                        if isinstance(p_data, list):
                            assets = p_data
                        elif isinstance(p_data, dict):
                            raw_assets = p_data.get("assets", [])
                            if isinstance(raw_assets, dict):
                                assets = list(raw_assets.values())
                            elif isinstance(raw_assets, list):
                                assets = raw_assets
                        for a in assets:
                            if isinstance(a, dict) and "path" in a:
                                registered_names.add(Path(a["path"]).name)
                    except Exception:
                        pass
                unregistered = [f for f in patch_files if f.name not in registered_names]
                if unregistered:
                    advisories.append(f"补料挂起：{len(unregistered)} 个文件待入库")
        except Exception as e:
            advisories.append(f"patch_assets 检查失败：{e}")

    # 3. 04-clips.approved.json 与 04-clips.json 段级内容 diff 检测
    clips_path = d / "04-clips.json"
    appr_path = d / "04-clips.approved.json"
    if appr_path.exists() and clips_path.exists():
        try:
            if has_clips_approved_diff(d):
                advisories.append("approved 已过期，必须重走 05")
        except Exception as e:
            advisories.append(f"04-clips.json / approved 不可读：{e}")

    # 4. 人时观测（r19 / v1.20；2026-09-23 降为观测量：只呈现读数与参考线，不构成门禁）
    ht_path = d / "human_time.json"
    if ht_path.exists():
        try:
            ht_data = json.loads(ht_path.read_text(encoding="utf-8"))
            if isinstance(ht_data, list):
                total_human_min = sum(entry.get("minutes", 0.0) for entry in ht_data if isinstance(entry, dict))
                duration_sec: float | None = None
                if clips_path.exists():
                    try:
                        c_data = json.loads(clips_path.read_text(encoding="utf-8"))
                        if isinstance(c_data, dict) and "total_duration" in c_data:
                            duration_sec = float(c_data["total_duration"])
                    except Exception:
                        pass
                if duration_sec is None:
                    mf_path = d / "03-audio" / "manifest.json"
                    if mf_path.exists():
                        try:
                            m_data = json.loads(mf_path.read_text(encoding="utf-8"))
                            if isinstance(m_data, dict):
                                if "total_duration" in m_data:
                                    duration_sec = float(m_data["total_duration"])
                                elif "segments" in m_data:
                                    duration_sec = sum(float(s.get("duration", 0.0)) for s in m_data["segments"])
                        except Exception:
                            pass
                if duration_sec is not None and duration_sec > 0:
                    ep_duration_min = duration_sec / 60.0
                    k = 1.5  # 暂以 03.5 ≈ 1.5×片长 为基准锚点
                    budget_min = k * ep_duration_min
                    if total_human_min > budget_min:
                        advisories.append(
                            f"人类耗时：本期已记 {total_human_min:.1f} 分钟，"
                            f"参考线 {budget_min:.1f} 分钟（k={k} × {ep_duration_min:.1f} 分钟片长，仅观测）"
                        )
        except Exception as e:
            advisories.append(f"human_time.json 不可读：{e}")

    # 5 & 6. 排片缺口与素材番笔记检测（Spec §4 / scout.probe 单次探测，消灭重复 WARN）
    try:
        from . import scout
        probe_data = scout.probe(d)
        patchable = probe_data.get("patchable", [])
        unrescuable = probe_data.get("unrescuable", [])
        if patchable:
            advisories.append(
                f"{len(patchable)} 段排片落空可补料（REPL 内敲 /scout，或命令行 python -m pipeline.scout <期> 生成派工单）"
            )
        elif unrescuable:
            advisories.append(
                f"{len(unrescuable)} 段排片失败且补丁池救不了（改锚点或改稿）"
            )

        missing_notes = probe_data.get("missing_notes", [])
        if missing_notes:
            first = missing_notes[0]
            advisories.append(
                f"缺《{first}》等 {len(missing_notes)} 部番剧笔记（REPL 内敲 /scout，或命令行 python -m pipeline.scout <期> --type notes）"
            )
    except Exception:
        pass

    return advisories


def inspect_episode(ep_dir: Path) -> EpisodeStatus:
    d = ep_dir.resolve()
    status = _inspect_episode_core(d)
    status.advisories = _detect_advisories(d)
    return status


def _inspect_episode_core(d: Path) -> EpisodeStatus:
    name = d.name

    has_topic = (d / "01-topic.md").exists()
    has_script_draft = (d / "02-script.draft.md").exists()
    has_script = (d / "02-script.md").exists()
    has_diff = (d / "02-diff.patch").exists()
    has_audio = (d / "03-audio" / "manifest.json").exists()
    has_clips = (d / "04-clips.json").exists()
    has_approved = (d / "04-clips.approved.json").exists()
    has_final = (d / "05-final.mp4").exists()
    # 质检的判据是 log **内容**，不是 log 存在——qc 失败也写 06-check.log，
    # 只看存在会把 FAIL 的期报成「质检已通过」（审计 F8）。判据复用
    # eval.parse_qc_log（认 FAIL/SKIP 行），不在这里发明第二份解析。
    qc_log = d / "06-check.log"
    qc_failed: list[str] | None = None
    if qc_log.exists():
        try:
            from .eval import parse_qc_log
            ok, qc_failed = parse_qc_log(qc_log.read_text(encoding="utf-8"))
            if ok:
                qc_failed = None
        except Exception:
            qc_failed = ["06-check.log 读取或解析失败"]
    has_qc = qc_log.exists() and qc_failed is None
    # 封面同理：build() 一开始就建 07-cover/ 目录，筛空崩掉也留目录——
    # 认目录会误报 08 完成，认最终产物 index.html 才是「真出过候选」
    has_cover = (d / "07-titles.md").exists() or (d / "07-cover" / "index.html").exists()

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

    # 2.5. 草稿已出、定稿未成：还在 02，不是 02.5——02.5 的 diff 命令
    # 需要 02-script.md 存在，把它指到一份不存在的文件上是把人往沟里带（审计 F7）
    if not has_script:
        return EpisodeStatus(
            episode_dir=str(d),
            episode_name=name,
            current_step="02 脚本写作（草稿待定稿）",
            is_blocked=False,
            block_reason=None,
            completed_steps=completed,
            next_action="02-script.draft.md 已出。精修后落定为 02-script.md，并执行 check_script 机检。",
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
            # 03.5 是 runbook 里的【建议】人工停机点（非强制物理关卡，下游不消费
            # 打点产物），机器语义上不阻塞——但建议必须显眼，不能悄悄滑过去
            is_blocked=False,
            block_reason=None,
            completed_steps=completed,
            next_action="⚠️ 建议先过人耳停机点 03.5：抽检开头与最长段音频无错字发飘，\n"
            "  确认后启动三通道画面排片分派。",
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

    # 7. 渲染完成，待质检（质检未过 = 打回重修，不是「已完成」）
    if not has_qc:
        if qc_failed:
            return EpisodeStatus(
                episode_dir=str(d),
                episode_name=name,
                current_step="07 自动质检（未通过）",
                is_blocked=False,
                block_reason=None,
                completed_steps=completed,
                next_action="质检门禁未全绿，修复后重跑质检：\n  "
                + "\n  ".join(qc_failed[:5]),
                next_command=f"python -m pipeline.qc {d}",
                docs_ref="docs/runbook/07-qc.md",
            )
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

    if status.advisories:
        lines.append("【注意事项 / 告警】")
        for adv in status.advisories:
            lines.append(f"  ⚠️ {adv}")

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

        # 找最近修改的期目录（排除 . 与 _ 两种前缀，B4-r14）
        subdirs = [
            d
            for d in episodes_dir.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
        ]
        hidden_underscore = len([
            d
            for d in episodes_dir.iterdir()
            if d.is_dir() and d.name.startswith("_")
        ])
        if not subdirs:
            print(f"[INFO] {episodes_dir} 下暂无期目录。", file=sys.stderr)
            if hidden_underscore > 0:
                print(f"[INFO] 已隐藏 {hidden_underscore} 个下划线目录。", file=sys.stderr)
            return 0
        subdirs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        target_path = subdirs[0]
        if hidden_underscore > 0:
            print(f"[INFO] 已隐藏 {hidden_underscore} 个下划线目录。", file=sys.stderr)

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
