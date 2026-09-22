"""ava ↔ pi 侦察派工单生成器（纯函数内核与单机 CLI）。

模块级依赖白名单：仅标准库 + paths, bgm。
clips 与 ingest_patch 必须函数内延迟 import，防循环引用与热路径拖入 ML 栈。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

from . import bgm, paths

TICKET_START_MARKER = "════════ pi 派工单（连同首尾标记行整段复制） ════════"
TICKET_END_MARKER = "════════ 派工单结束 ════════"


def format_unrescuable_action(seg: dict) -> str:
    """格式化不可救段的人工指引（单源化，无硬编码）。"""
    from .clips import MIN_CLIP

    ch = seg.get("channel")
    st = seg.get("status")
    res_d = seg.get("residual", 0.0)
    if ch == "anchor":
        if st == "anchor_overlap":
            return "改 02-script.md 锚点去重，重跑 clips"
        return "改 02-script.md 锚点或检查集号，走 05 人审或改稿"
    return f"补丁通道不接管（缺口 {res_d}s < {MIN_CLIP}s），走 05 人审或改稿"


def probe(ep_dir: Path) -> dict:
    """单源只读探针，返回 {"patchable": [...], "unrescuable": [...], "missing_notes": [...]}。

    段可救 ⟺ channel ≠ anchor 且（status ∈ {no_match, no_source} 或 residual ≥ MIN_CLIP）
    """
    from .clips import MIN_CLIP

    patchable: list[dict] = []
    unrescuable: list[dict] = []
    clips_file = ep_dir / "04-clips.json"
    if clips_file.exists():
        try:
            data = json.loads(clips_file.read_text(encoding="utf-8"))
            for seg in data.get("segments", []):
                status = seg.get("status")
                if status in ("ok", "ok_extended"):
                    continue
                dur = float(seg.get("duration", 0.0))
                clips = seg.get("clips", [])
                cur_dur = sum(float(c.get("dur", 0.0)) for c in clips)
                residual = round(dur - cur_dur, 3)
                seg_info = dict(seg)
                seg_info["residual"] = residual

                channel = seg.get("channel")
                is_rescueable = (
                    channel != "anchor"
                    and (status in ("no_match", "no_source") or residual >= MIN_CLIP)
                )
                if is_rescueable:
                    patchable.append(seg_info)
                else:
                    unrescuable.append(seg_info)
        except Exception as e:
            print(f"WARN 04-clips.json 解析失败: {e}", file=sys.stderr)

    missing_notes: list[str] = []
    topic_file = ep_dir / "01-topic.md"
    if topic_file.exists():
        animes = bgm.animes_of(ep_dir)
        for anime in animes:
            note_path = paths.DATA / "library" / "notes" / f"{anime}.md"
            if not note_path.exists():
                missing_notes.append(anime)

    return {
        "patchable": patchable,
        "unrescuable": unrescuable,
        "missing_notes": missing_notes,
    }


def detect_type(ep_dir: Path, probe_data: dict | None = None) -> list[tuple[str, dict]]:
    """推断工单类型。按 clips patchable -> notes -> titles 顺序推断。"""
    p_data = probe_data if probe_data is not None else probe(ep_dir)
    results: list[tuple[str, dict]] = []

    has_failed = bool(p_data["patchable"] or p_data["unrescuable"])
    if has_failed:
        if p_data["patchable"]:
            results.append(("patch", p_data))
        else:
            print("WARN 04-clips.json 存在失败段但均不可救，不生成 patch 工单：", file=sys.stderr)
            for s in p_data["unrescuable"]:
                idx = s.get("index")
                st = s.get("status")
                act = format_unrescuable_action(s)
                print(f"  - 段{idx} ({st}): {act}", file=sys.stderr)

    if p_data["missing_notes"]:
        results.append(("notes", p_data))

    if not results and (ep_dir / "07-titles.md").exists():
        results.append(("titles", p_data))

    return results


def resolve_patch_floor(ep_dir: Path, floor: float | None = None) -> tuple[str, str | None]:
    """预消解补丁池 floor，返回 (floor_arg, calibration_guide)。"""
    animes = bgm.animes_of(ep_dir)
    if len(animes) > 1:
        if floor is None:
            raise SystemExit(
                "FAIL 企划期补丁池 floor 无主番可继承，请人拍板后以 "
                "`/scout --type patch --floor <值>` 或 `--floor <值>` 重跑"
            )
        return f" --floor {floor}", None

    if floor is not None:
        return f" --floor {floor}", None

    main_anime = animes[0] if animes else bgm.anime_of(ep_dir)
    from .ingest_patch import resolve_patch_floor as _ingest_resolve_floor

    try:
        _ingest_resolve_floor(main_anime, explicit_floor=None)
        return "", None
    except SystemExit:
        target = main_anime or "<番名>"
        guide = f"先 `{sys.executable} -m pipeline.vprobe scene {target} <集>` 标定"
        return "", guide


resolve_floor = resolve_patch_floor


def render_patch_ticket(ep_dir: Path, probe_data: dict, floor: float | None = None) -> str:
    """渲染 patch 工单。"""
    from .clips import MIN_CLIP

    animes = bgm.animes_of(ep_dir)
    anime_str = "/".join(animes) if animes else (bgm.anime_of(ep_dir) or "未知")
    iso_now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    floor_arg, calib_guide = resolve_patch_floor(ep_dir, floor)

    pool_file = ep_dir / "04-patch" / "pool.json"
    n_assets = 0
    if pool_file.exists():
        try:
            pool_data = json.loads(pool_file.read_text(encoding="utf-8"))
            n_assets = len(pool_data.get("assets", []))
        except Exception:
            n_assets = 0

    patchable = probe_data.get("patchable", [])
    unrescuable = probe_data.get("unrescuable", [])

    lines = [
        f"# pi 侦察派工单 · patch ｜ 期: {ep_dir.name} ｜ 番: {anime_str} ｜ 生成: {iso_now}",
        "",
        "> 执行环境：所有命令在仓库根执行，解释器用钉死值：",
        f">   cd {paths.ROOT}",
        f">   {sys.executable} -m pipeline.ingest_patch ...",
        "> （不要用系统 python——依赖在项目 .venv 内，E6）",
        "",
        "> 动手前先确认 `patch_assets/` 无遗留文件（status advisory 『补料挂起』会显示）；遗留会被本次 ingest 一并登记，使判据 3 的资产计数对不上。",
        "",
        "## 任务",
    ]

    if not patchable:
        lines.append("无缺口可推导——素材直落 patch_assets/ 后跑验收命令（runbook 04.5）")
    else:
        lines.extend([
            "采集补丁视频素材，修补当前排片缺口。",
            "",
            "### 补丁池可救（pi 的任务）",
            "| 段号 | 状态 | 需补足时长 | 配音原文 | 查询原文 | 通道 |",
            "|---|---|---|---|---|---|",
        ])
        for s in patchable:
            idx = s.get("index")
            st = s.get("status")
            res_d = s.get("residual", 0.0)
            txt = str(s.get("text", "")).replace("\n", " ").replace("|", "\\|")
            q = str(s.get("scene") or s.get("query") or s.get("text", "")).replace("\n", " ").replace("|", "\\|")
            ch = s.get("channel", "")
            lines.append(f"| 段{idx} | {st} | {res_d}s | {txt} | {q} | {ch} |")

        if unrescuable:
            lines.extend([
                "",
                "### 补丁池救不了（给人看的，不要采料）",
                "| 段号 | 状态 | 行动 |",
                "|---|---|---|",
            ])
            for s in unrescuable:
                idx = s.get("index")
                st = s.get("status")
                act = format_unrescuable_action(s)
                lines.append(f"| 段{idx} | {st} | {act} |")

    lines.extend([
        "",
        "## 遵守的标准（先读原文，逐条遵守）",
        f"- 素材采掘纪律（渠道选择、无台标/水印、原图分辨率）：`{paths.ROOT / 'skills' / 'acquire-assets' / 'SKILL.md'}`",
        f"- 入库规格与流程：`{paths.ROOT / 'docs' / 'runbook' / '04-clips.md'}` 04.5 节",
        "",
        "## 硬约束（违反即返工）",
        "- 仅视频文件；图片先转微动视频（`ffmpeg -loop 1 -t 8 -i in.jpg -pix_fmt yuv420p out.mp4`）；",
        f"- 素材采掘纪律逐条遵守 `{paths.ROOT / 'skills' / 'acquire-assets' / 'SKILL.md'}`（渠道选择、无台标/水印、原图分辨率）；",
        f"- 入库规格与流程逐条遵守 `{paths.ROOT / 'docs' / 'runbook' / '04-clips.md'}` 04.5 节。边界声明：补丁通道不走 acquire-assets 的 candidates.json 人审流程——那是 Phase 0 池扩充的闸门；补丁素材直落 patch_assets/，由 ingest 门禁与 05 人审把关；",
        "- 按 Mode 1（纯净画面）采集即可，渲染强制 `-an` 剥音轨（ADR-0013），素材有无音轨皆可；音画同源（Mode 2）缺口的派工不在本协议覆盖范围；",
        f"- 单段补料镜头本身 ≥ {MIN_CLIP}s（MIN_CLIP，短了 rescue-B 连检索都不发）；",
        f"- 落盘到 `{ep_dir / 'patch_assets'}`；",
    ])

    if calib_guide:
        lines.append(f"- 素材番未标定画面检索门槛，入库前须执行：{calib_guide}；")

    lines.extend([
        "",
        "## 产物与落盘路径",
        f"- 素材存放目录：`{ep_dir / 'patch_assets'}`",
        "",
        "## 验收（全部成立才算过，报告时贴输出）",
        "验收命令：",
        f"  cd {paths.ROOT} && {sys.executable} -m pipeline.ingest_patch {ep_dir}{floor_arg}",
        "",
        "复合判据：",
        "1. 退出码 0；",
        "2. stdout 含「补料入库完成」（出现「已提交云端打标」= 两段式第一段，等远端 tmux 跑完必须重跑本命令；退出码 2 = 远端打标运行中，等待后重跑）；",
    ])

    if not patchable:
        lines.extend([
            f"3. 已登记资产数从 {n_assets} 涨到 {n_assets}+<本单交付数>——计数命令：",
            f'   {sys.executable} -c "import json;print(len(json.load(open(\'{pool_file}\'))[\'assets\']))"',
            "4. 判据 4 不适用（无「本单目标段」）：验收 = 判据 1–3 + 人回 ava `/run clips` 确认补丁池可被检索。",
        ])
    else:
        target_segs = "、".join(f"段{s.get('index')}" for s in patchable)
        lines.extend([
            f"3. 已登记资产数从 {n_assets} 涨到 {n_assets}+{len(patchable)}——计数命令：",
            f'   {sys.executable} -c "import json;print(len(json.load(open(\'{pool_file}\'))[\'assets\']))"',
            f"4. cd {paths.ROOT} && {sys.executable} -m pipeline.clips {ep_dir}：本单目标段（{target_segs}）status 必须转 ok/ok_extended——以页脚段级输出为准（exit code 1 可能只是「救不了区」的锚点段仍在，不必然是本单失败）；目标段未转 ok 或命令本身失败，贴输出交人裁决，不许报完成。",
        ])

    lines.extend([
        "",
        "## 禁止事项",
        "- 严禁引入带台标、水印、字幕、插画的素材；",
        f"- 严禁单段视频镜头时长低于 {MIN_CLIP}s；",
        "- 云端是计费动作：实例不可达时停下来报告人，由人决定是否 `cloud up`——pi 无权自行开机；",
        "- 严禁直接修改 `04-clips.json` 或私自手写 patch 索引；",
        "- 严禁在未跑绿复合判据前声称完成。",
        "",
        "## 完成后",
        "人回 ava `/run clips`，补丁池自动 rescue 补位，再走 05 人审。",
    ])

    return "\n".join(lines) + "\n"


def render_notes_ticket(ep_dir: Path, probe_data: dict) -> str:
    """渲染 notes 工单。"""
    missing_animes = probe_data.get("missing_notes", [])
    if not missing_animes:
        raise SystemExit("FAIL 素材番笔记齐备，无需派工")

    anime_str = "/".join(missing_animes)
    iso_now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    missing_list_str = "\n".join(f"- 《{a}》" for a in missing_animes)
    paths_list = []
    for a in missing_animes:
        note_p = paths.DATA / "library" / "notes" / f"{a}.md"
        audit_p = paths.DATA / "library" / "notes" / f"{a}.audit.md"
        verdict_p = paths.DATA / "library" / "notes" / f"{a}.verdict.md"
        paths_list.extend([
            f"- 《{a}》笔记：`{note_p}`",
            f"- 《{a}》审查报告：`{audit_p}`",
            f"- 《{a}》裁决记录：`{verdict_p}`",
        ])
    notes_paths_str = "\n".join(paths_list)

    lines = [
        f"# pi 侦察派工单 · notes ｜ 期: {ep_dir.name} ｜ 番: {anime_str} ｜ 生成: {iso_now}",
        "",
        "> 执行环境：所有命令在仓库根执行，解释器用钉死值：",
        f">   cd {paths.ROOT}",
        f">   {sys.executable} -m pipeline.vindex status --anime <番>",
        "> （不要用系统 python——依赖在项目 .venv 内，E6）",
        "",
        "## 任务",
        "采掘以下素材番的番剧笔记，补齐 Phase 0 资产：",
        missing_list_str,
        "",
        "## 遵守的标准（先读原文，逐条遵守）",
        f"- 番剧笔记标准：`{paths.ROOT / 'docs' / 'dev' / 'STANDARD.md'}` S11（零上下文对抗审查 + 双重指标 + 终审裁决）、S12（行级时间码指到台词行）",
        "",
        "## 硬约束（违反即返工）",
        "- 笔记内容严禁空泛剧情梗概，必须带行级台词时间码（S12）；",
        "- 严格执行零上下文对抗审查，并产出双重指标与裁决记录（S11）；",
        f"- 每部番必须产出三份文件落盘到 `{paths.DATA / 'library' / 'notes'}`：",
        "  1. 笔记：`<番>.md`",
        "  2. 审查报告：`<番>.audit.md`",
        "  3. 裁决记录：`<番>.verdict.md`",
        "",
        "## 产物与落盘路径",
        notes_paths_str,
        "",
        "## 验收（全部成立才算过，报告时贴输出）",
        "对每部缺失番执行：",
        f"  cd {paths.ROOT} && {sys.executable} -m pipeline.vindex status --anime <番>",
        "",
        "复合判据：",
        "1. 命令输出的七条数字中「笔记 == 片源」（前置分支：若该命令报「片源登记表里没有《番》」，Phase 0 未开始，判据 1 改为人核：分集速查表集数 == 该番实际集数）；",
        "2. 三份产物齐备（`<番>.md`、`<番>.audit.md`、`<番>.verdict.md` 全部落盘）；",
        "3. 显式声明：正确性与厚度密度无机器门禁，人工对照 S11 清单核对是强制步骤，跳过不算通过（S9）。",
        "",
        "## 禁止事项",
        "- 严禁虚构时间码或抄写模糊区间；",
        "- 严禁跳过人工对照 S11 审查直接报完成；",
        "- 严禁直接修改既有库内其他番的笔记文件。",
        "",
        "## 完成后",
        "人回 ava 继续 `/script`。",
    ]
    return "\n".join(lines) + "\n"


def render_titles_ticket(ep_dir: Path) -> str:
    """渲染 titles 工单。"""
    animes = bgm.animes_of(ep_dir)
    anime_str = "/".join(animes) if animes else (bgm.anime_of(ep_dir) or "未知")
    iso_now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    topic_file = ep_dir / "01-topic.md"
    tension = ""
    ep_type = "未指定"
    if topic_file.exists():
        text = topic_file.read_text(encoding="utf-8")
        m_t = re.search(r"^\s*张力\s*[:：]\s*(.+?)\s*$", text, re.M)
        if m_t:
            tension = m_t.group(1).strip()
        m_type = re.search(r"^\s*类型\s*[:：]\s*(.+?)\s*$", text, re.M)
        if m_type:
            ep_type = m_type.group(1).strip()

    if not tension:
        tension_str = f"本期类型={ep_type}，不设张力（N2），标题扣人物/故事本身"
    else:
        tension_str = tension

    lines = [
        f"# pi 侦察派工单 · titles ｜ 期: {ep_dir.name} ｜ 番: {anime_str} ｜ 生成: {iso_now}",
        "",
        "> 执行环境：所有命令在仓库根执行，解释器用钉死值：",
        f">   cd {paths.ROOT}",
        f">   {sys.executable} ...",
        "> （不要用系统 python——依赖在项目 .venv 内，E6）",
        "",
        "## 任务",
        "根据稿件张力与核心论点，生成 5 条候选标题，对齐网感与受众点击意愿。",
        "",
        f"- 张力字段：{tension_str}",
        f"- 文稿路径（请直接读取）：`{ep_dir / '02-script.md'}`",
        "",
        "## 遵守的标准（先读原文，逐条遵守）",
        f"- 标题标准：`{paths.ROOT / 'docs' / 'dev' / 'STANDARD.md'}` N2、N6；",
        f"- 规范要求：`{paths.ROOT / 'docs' / 'runbook' / '08-cover-title.md'}`",
        "",
        "## 硬约束（违反即返工）",
        "- 金句式不论文式（判据：这句话能否脱离视频单独发出去）；",
        "- 不碰政治议题；",
        "- 候选之间没有排名，不许标推荐度（N6）；",
        f"- pi 候选必须追加写入 `{ep_dir / '07-titles.md'}` 候选表第 6–10 行，严禁覆盖、改写 1–5 行已有内容（ava agent 的候选占 1–5 槽，两组并存）；",
        "- 不许只打在对话里让人手抄（E2）；",
        "- pi 只出候选，定稿权在人。",
        "",
        "## 产物与落盘路径",
        f"- 目标文件：`{ep_dir / '07-titles.md'}`（追加至第 6–10 槽位）",
        "",
        "## 验收（全部成立才算过，报告时贴输出）",
        f"1. `{ep_dir / '07-titles.md'}` 候选表第 6–10 行已填入 5 条标题及路子；",
        "2. 第 1–5 行原始内容完整无损；",
        "3. 报告时贴出追加后的候选表全文。无机检，人在 09 步从候选表挑一条定稿。",
        "",
        "## 禁止事项",
        "- 严禁覆盖或篡改第 1–5 槽已有标题；",
        "- 严禁标注推荐指数、星级或进行优劣排序（N6）；",
        "- 严禁输出论文式/学术式刻板标题。",
        "",
        "## 完成后",
        "人在 09 步从 10 条候选表里挑 1 条定稿。",
    ]
    return "\n".join(lines) + "\n"


def render_ticket(t_type: str, ep_dir: Path, probe_data: dict, floor: float | None = None) -> str:
    """按类型分发渲染工单，并在出口自查引用的规范文件真实存在（🔴-1 防断链自查）。"""
    if t_type == "patch":
        content = render_patch_ticket(ep_dir, probe_data, floor)
    elif t_type == "notes":
        content = render_notes_ticket(ep_dir, probe_data)
    elif t_type == "titles":
        content = render_titles_ticket(ep_dir)
    else:
        raise ValueError(f"未知工单类型: {t_type}")

    root_str = str(paths.ROOT)
    for m in re.finditer(rf"`({re.escape(root_str)}/(?:docs|skills)/[^\s`]+)`", content):
        target = Path(m.group(1))
        if not target.exists():
            raise RuntimeError(f"工单引用断链：目标文件不存在 {target}")

    return content


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ava ↔ pi 侦察派工单生成器")
    ap.add_argument("episode", type=Path, help="期目录路径")
    ap.add_argument("--type", choices=["patch", "notes", "titles"], default=None, help="显式指定工单类型")
    ap.add_argument("--floor", type=float, default=None, help="补丁池检索门槛")
    a = ap.parse_args(argv)

    ep_dir = a.episode.resolve()
    if not ep_dir.exists() or not ep_dir.is_dir():
        print(f"FAIL 期目录不存在: {ep_dir}", file=sys.stderr)
        return 1

    targets: list[tuple[str, dict]] = []
    p_data = probe(ep_dir)

    if a.type:
        if a.type == "patch":
            has_failed = bool(p_data["patchable"] or p_data["unrescuable"])
            if has_failed and not p_data["patchable"]:
                print("FAIL 失败段全部不可救，无法签发 patch 工单：", file=sys.stderr)
                for s in p_data["unrescuable"]:
                    idx = s.get("index")
                    st = s.get("status")
                    act = format_unrescuable_action(s)
                    print(f"  - 段{idx} ({st}): {act}", file=sys.stderr)
                return 1
            targets.append(("patch", p_data))
        elif a.type == "notes":
            if not p_data["missing_notes"]:
                raise SystemExit("FAIL 素材番笔记齐备，无需派工")
            targets.append(("notes", p_data))
        elif a.type == "titles":
            if not (ep_dir / "07-titles.md").exists():
                raise SystemExit("FAIL 07-titles.md 不存在，先 /run cover")
            targets.append(("titles", p_data))
    else:
        targets = detect_type(ep_dir, probe_data=p_data)
        if not targets:
            raise SystemExit("FAIL 未检测到可生成的工单类型。可选显式指定: --type {patch,notes,titles}")

    for t_type, p_info in targets:
        ticket_content = render_ticket(t_type, ep_dir, p_info, floor=a.floor)
        out_path = ep_dir / f"scout-ticket-{t_type}.md"
        paths.atomic_write(out_path, ticket_content)
        print(f"已生成派工单落盘: {out_path}", file=sys.stderr)
        print(f"{TICKET_START_MARKER}\n{ticket_content}{TICKET_END_MARKER}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
