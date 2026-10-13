"""状态卡组装器（Spec §1.3, §2.2, §6 PR5；Spec 3 §2.8 引导行）。

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
import difflib
import json
import re
from pathlib import Path
from typing import Any

from pipeline.agent.resolver import scope_of
from pipeline.agent.tools import (
    DRAFT_FILENAME,
    SCRIPT_FILENAME,
    resolve_write_content,
    scrub_restricted,
)
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
    project_dir: Path | str | None = None,
) -> str:
    """构建注入 system prompt 的紧凑状态卡（目标 ≤ 400 字符）。

    D81 §6.3 ⑨：卷会话（`project_dir` 由宿主按树规则给出）首行加「企划 <企划> · 第 N 卷」，N 取卷表。
    """
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

    volume_line = ""
    if project_dir is not None:
        from pipeline import project as project_mod

        pdir = Path(project_dir)
        doc = project_mod.load(pdir)
        pname = doc.name if isinstance(doc, project_mod.ProjectDoc) else pdir.name
        seq = project_mod.volume_seq(pdir, Path(ep_dir).name)
        volume_line = f"企划 {pname} · {f'第 {seq} 卷' if seq is not None else '不在卷表'}\n"

    card = (
        f"[状态卡]\n"
        f"{volume_line}"
        f"期名: {name} | 工序: {step} | 阻塞: {blocked_str} | scope: {effective_scope} | 人时: {ht_min:.1f}m\n"
        f"推荐命令: {next_cmd}\n"
        f"产物: {checklist}\n"
        f"提示: {advisories_str}"
    )

    # Spec 3 §2.8 / §4.3：确定性引导行（经 approvals.get_status_card_guidance 一次进锁获取）
    try:
        from pipeline import approvals

        pending_labels, has_uncovered_rejected = approvals.get_status_card_guidance(d)
        guidance_lines: list[str] = []
        if pending_labels:
            full_line = f"待审批: {'、'.join(pending_labels)}（/approvals 查看详情）"
            fallback_line = f"待审批: {len(pending_labels)} 项（/approvals 查看详情）"
            if len(card) + 1 + len(full_line) <= 400:
                guidance_lines.append(full_line)
            elif len(card) + 1 + len(fallback_line) <= 400:
                guidance_lines.append(fallback_line)
            else:
                guidance_lines.append(f"待审批: {len(pending_labels)} 项")

        if has_uncovered_rejected:
            guidance_lines.append("驳回反馈: _agent/approval_feedback.md（read_artifact 可读）")

        if guidance_lines:
            card = card + "\n" + "\n".join(guidance_lines)
    except Exception:
        pass

    # 返回前对整串做一次 RESTRICTED_EGRESS_PATTERNS 清洗（casefold 判定，命中替换为 [已脱敏]）
    card = scrub_restricted(card)

    return card


def build_project_card(project_dir: Path | str) -> str:
    """企划会话的状态卡（D81 §6.3 ⑨）：企划名、卷表（每卷当前工序）、合集期是否有成片、解析错误与对表提示。"""
    from pipeline import project as project_mod

    pdir = Path(project_dir)
    doc = project_mod.load(pdir)
    members = {project_mod.name_key(m.name): m for m in project_mod.project_members(pdir)}
    lines = ["[状态卡]"]
    if doc is None:
        lines.append(f"模式: 企划会话 | 企划目录: {pdir.name} | scope: project")
        lines.append("立项文件: 00-project.md 未立项（写好机读头与卷表后人在卡上批）")
    elif isinstance(doc, project_mod.ParseError):
        lines.append(f"模式: 企划会话 | 企划目录: {pdir.name} | scope: project")
        lines.append(f"立项文件: 00-project.md 解析失败（卷表不可信）：{doc}")
    else:
        lines.append(f"模式: 企划会话 | 企划: {doc.name}（目录 {pdir.name}） | scope: project")
        vols = []
        for v in doc.volumes:
            m = members.get(project_mod.name_key(v.dir))
            if m is None:
                vols.append(f"{v.seq} {v.dir}：目录不存在")
                continue
            st = inspect_episode(m)
            vols.append(f"{v.seq} {v.dir}：{st.current_step}{'（停机点）' if st.is_blocked else ''}")
        lines.append("卷表: " + ("；".join(vols) if vols else "空（先定卷表）"))
        if doc.collection is not None:
            m = members.get(project_mod.name_key(doc.collection))
            has_final = m is not None and (m / "05-final.mp4").exists()
            lines.append(f"合集: {doc.collection}：{'成片 ✓' if has_final else ('成片 ✗' if m is not None else '目录未建')}")
        hints = project_mod.members_vs_table(pdir, doc)
        if hints:
            lines.append("提示: " + "；".join(hints))
    # D81 §9.3 ⑦：上次 `crosscheck --write` 的汇总（没写过报告则不显示）
    from pipeline import crosscheck

    last = crosscheck.read_summary(pdir)
    if last is not None:
        lines.append(f"上次跨卷检查: {last}")
    lines.append("企划会话只做规划、跨卷审查与总装；卷级工序到卷的会话里跑")
    return scrub_restricted("\n".join(lines))


def build_idea_card() -> str:
    """构建无期选题会话（idea scope）的静态状态卡（纯函数，目标 ≤ 400 字符）。"""
    return (
        "[状态卡]\n"
        "模式: 选题会话（无期） | scope: idea | 期目录: 无（写期文件前须先建期）\n"
        "读域: data/library/ 与跨期 read_status\n"
        "产出落盘: 定稿后点『＋ 新建一期』，选题讨论自动带入新期"
    )



# D59：会写 data/library/ 的 asset 侧模块（只读子命令在弹卡前已免卡，到这里的都是写）。
LIBRARY_WRITE_MODULES = frozenset({"acquire", "ingest", "shots", "vindex", "subindex", "faces", "vprobe", "timeline"})


def _matches_flag_prefix(tokens: list[str], full_flag: str) -> bool:
    """匹配完整旗标或其 argparse 缩写前缀（如 --app 匹配 --approve）。"""
    for t in tokens:
        if t.startswith("--") and len(t) > 2 and full_flag.startswith(t):
            return True
    return False


def _sanitize_card_field(val: Any) -> str:
    """单行化并剥离 ANSI / 控制字符（防模型可控字段伪造审批卡面行）。"""
    raw = str(val or "").strip()
    first = raw.splitlines()[0] if raw else ""
    no_ansi = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", first)
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", no_ansi).strip()


# D47：写稿卡上的 diff 上限。80 行约一屏，够看清一两段的改动；整篇重写时看头部 + 剩余行数，
# 人一眼知道「它改了全篇」，这本身就是该拒的信号。
CARD_DIFF_MAX_LINES = 80


def _script_diff(target: Path, new: str) -> list[str]:
    """写稿卡的改动预览：与磁盘现版的 unified diff（上下文 2 行：稿件里段落标题与配音行隔一个空行，2 行才看得到改的是哪一段），剥控制字符，超出上限截断并注明。"""
    try:
        old = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ["改动: 新建文件，无现版可比"]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"改动: 读不了磁盘现版（{type(exc).__name__}），无法给出 diff"]
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "磁盘现版", "写入后", lineterm="", n=2))
    if not diff:
        return ["改动: 与磁盘现版逐字相同"]
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    body = [re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", d) for d in diff[2:]]
    out = [f"改动: +{added} / -{removed} 行"]
    out.extend(body[:CARD_DIFF_MAX_LINES])
    if len(body) > CARD_DIFF_MAX_LINES:
        out.append(f"……另有 {len(body) - CARD_DIFF_MAX_LINES} 行 diff 未显示")
    return out


# ADR-0030（D78）：bash 的「硬危险」——命中即弹卡，未命中免卡。只收不可逆或越过人工关卡的操作；
# 宿主静态规则，判不到不等于安全（`python -c "shutil.rmtree(…)"` 这类绕法由 ADR-0030 写明、人已接受）。
# D77 原表里的 mv / chmod / curl / git commit / 项目内 uv、pip 已按人裁决「再减少一些弹框」移出。
_BASH_DANGER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[\s;&|(])(rm|rmdir|unlink)(\s|$)|\s-delete\b"), "[删除]"),
    (re.compile(r"\bgit\s+(push|reset|rebase|clean)\b"), "[git 改历史/推远端]"),
    (re.compile(r"(^|[\s;&|(])(sudo|su)\s"), "[提权]"),
    (
        re.compile(
            r"\bbrew\s+(install|reinstall|upgrade|uninstall)\b|\bnpm\s+(\S+\s+)*(-g|--global)\b"
            r"|\buv\s+tool\s+install\b|\bpipx\s+install\b"
        ),
        "[全局装依赖]",
    ),
    (re.compile(r"02-diff\.patch|04-clips\.approved\.json|07-cover/selected"), "[停机点解封物] 批准只能由人做"),
)
# Code Freeze 只认「写入」：读 pipeline/ config/（cat、grep、ls）不拦。写法 = 重定向、tee、原地编辑、
# 拷贝 / 移动 / 链接 / 新建；先剥掉 `2>&1`、`>/dev/null` 这类不写文件的重定向
_FREEZE_PATH = re.compile(r"(^|[\s'\"/=])(pipeline|config)/")
_HARMLESS_REDIRECT = re.compile(r"\d?>&\d|&?\d?>{1,2}\s*/dev/null")
_WRITE_FORM = re.compile(
    r">|\btee\b|\bsed\s+(\S+\s+)*-i|\bperl\s+(\S+\s+)*-i|(^|[\s;&|(])(cp|mv|ln|touch|truncate|install|rsync|dd)\s"
)
_FREEZE_TAG = "[写源码/配置] Code Freeze 范围"

# D82 §4.4（ADR-0030 危险清单扩充，人裁决 Q3 / Q4，2026-10-12）
_GATED_TAG = "[须人审的流水线命令]"
_TICKETS_PATH = re.compile(r"(^|[\s'\"/=])tickets/")
_TICKETS_TAG = "[写工单目录]"
# 命令词（行首或 ; & | ( 之后，允许前置 env X=1 / X=1 / nohup / exec）。名单 = 本机已装（claude、pi，2026-10-12
# `command -v` 实测）+ 常见 coding agent CLI；静态名单绕得过（python -c 一类），ADR-0030 已接受这一类
CODING_AGENT_COMMANDS: tuple[str, ...] = ("claude", "codex", "opencode", "pi", "aider", "gemini", "cursor-agent")
_CODING_AGENT = re.compile(
    r"(?:^|[;&|(])\s*(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:(?:nohup|exec)\s+)?"
    r"(" + "|".join(re.escape(c) for c in CODING_AGENT_COMMANDS) + r")(?=$|[\s;&|)])"
)
_AGENT_TAG = "[拉起 coding agent]"


def _gated_pipeline_hit(command: str) -> bool:
    """命令里出现 `pipeline.<模块>` / `pipeline/<模块>.py`，且其后**任意位置**出现该模块须人审的子命令词。

    不要求紧挨着（红队 R-6）：`python -m pipeline.corrections -- global …`、`-m 'pipeline.corrections'` 都能跑。
    规则从 tools.HUMAN_GATED_COMMANDS 生成，不另写清单。
    """
    from pipeline.agent.tools import HUMAN_GATED_COMMANDS

    for module, sub in HUMAN_GATED_COMMANDS:
        for m in re.finditer(rf"pipeline(?:\.{re.escape(module)}\b|/{re.escape(module)}\.py\b)", command):
            if sub is None or re.search(rf"(^|[\s'\"=]){re.escape(sub)}($|[\s'\"])", command[m.end():]):
                return True
    return False


def bash_danger_tags(command: str) -> list[str]:
    """bash 命令命中的硬危险标记（按规则表顺序，去重）。非空即弹卡（ADR-0030 决策 1）。"""
    tags = [tag for pat, tag in _BASH_DANGER_RULES if pat.search(command)]
    stripped = _HARMLESS_REDIRECT.sub(" ", command)
    if _FREEZE_PATH.search(stripped) and _WRITE_FORM.search(stripped):
        tags.append(_FREEZE_TAG)
    if _gated_pipeline_hit(command):
        tags.append(_GATED_TAG)
    if _TICKETS_PATH.search(stripped) and _WRITE_FORM.search(stripped):
        tags.append(_TICKETS_TAG)
    if _CODING_AGENT.search(command):
        tags.append(_AGENT_TAG)
    return tags


def render_approval_card(
    name: str,
    args: dict[str, Any],
    argv: list[str] | None = None,
    stop_label: str | None = None,
    *,
    target_exists: bool | None = None,
    episode_dir: Path | str | None = None,
    memory_preview: list[str] | None = None,
    command_preview: list[str] | None = None,
) -> str:
    """标准化人机审批卡片渲染纯函数（Spec §3.2, §6 PR6）。

    版式：
    1. 执行审批（run_pipeline 普通命令 / render 长任务）
    2. 计费审批（run_pipeline cloud up/run/push/pull）
    3. 写入审批（write_episode_file）
    4. 记忆写入审批（write_memory；全文由 memory.render_plan_preview 给出）
    危险标记由宿主静态规则打，不依赖模型自报。
    """
    args = dict(args or {})
    argv_list = list(argv) if argv is not None else []

    if name == "write_memory":
        lines = [
            "┌─ 记忆写入审批 ──────────────────────────────────",
            f"│ 工具: {name}",
            "│ 危险标记: [跨期记忆] 按 y 即确认下方全文进入之后所有 creative/asset/idea 会话",
        ]
        for raw in memory_preview or []:
            for piece in str(raw).split("\n"):
                lines.append(f"│ {piece}")
        lines.append("└─ 执行? [y/N]: ")
        return "\n".join(lines)

    if name == "write_episode_file":
        raw_filename = str(args.get("filename", "")).strip()
        first_line = raw_filename.splitlines()[0] if raw_filename else ""
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", first_line).strip()
        ep = episode_dir or args.get("episode_dir")
        # D48：edits 写法在磁盘现版上算出全文，卡上的 diff 就是将要落盘的内容；算不出时 review 已在弹卡前拒
        try:
            content = resolve_write_content(ep, args)
        except (ValueError, PermissionError):
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

        if filename == SCRIPT_FILENAME and ep:
            # D47：定稿被改之后，已有的封板与配音都可能过期——只提示，不拒
            if (Path(ep) / "02-diff.patch").exists():
                danger_str += "；改后封板失效，需重新封板"
            if (Path(ep) / "03-audio" / "manifest.json").exists():
                danger_str += "；已配音，改动段落需 tts --redo"

        target_str = f"{filename}（{size_str}，{status_str}）" if filename else size_str
        lines = [
            "┌─ 写入审批 ──────────────────────────────────────────",
            f"│ 工具: {name}",
            f"│ 目标: {target_str}",
            f"│ 危险标记: {danger_str}",
        ]
        if filename in (SCRIPT_FILENAME, DRAFT_FILENAME) and ep and isinstance(content, str):
            lines.extend(f"│ {ln}" for ln in _script_diff(Path(ep) / filename, content))
        if command_preview:
            # D81 §4.4：企划立项文件——机读字段逐行 diff + 全文 diff（审查层算好，人看到的就是将要落盘的）
            lines.extend(f"│ {_sanitize_card_field(ln)}" for ln in command_preview)
        lines.append("└─ 执行? [y/N]: ")
        return "\n".join(lines)

    if name == "write_note":
        # D61 / ADR-0028：笔记是所有期共用的事实源——卡上给目标、理由与 diff（新建给开头若干行）
        from pipeline.agent.tools import note_target, resolve_note_content

        try:
            dest = note_target(args)
            content = resolve_note_content(args)
        except (ValueError, PermissionError, OSError):
            dest, content = None, args.get("content") if isinstance(args.get("content"), str) else ""
        content_bytes = len(content.encode("utf-8"))
        size_str = f"{content_bytes / 1024:.1f} KB" if content_bytes >= 100 else f"{content_bytes} B"
        exists = bool(dest and dest.exists())
        kind = "终审表" if str(args.get("target")) == "review" else "笔记"
        lines = [
            "┌─ 笔记写入审批 ──────────────────────────────────────",
            f"│ 工具: {name}",
            f"│ 目标: data/library/notes/{dest.name if dest else '?'}（{kind}，{size_str}，"
            f"{'覆盖现有文件，旧版留底 notes/_history/' if exists else '新建文件'}）",
            "│ 危险标记: [素材库·所有期共用] 写稿 agent 照着笔记写，笔记错会传给之后每一期",
            f"│ 理由: {_sanitize_card_field(args.get('reason', '')) or '（未给）'}",
        ]
        if exists:
            lines.extend(f"│ {ln}" for ln in _script_diff(dest, content))
        else:
            head = content.splitlines()
            lines.append(f"│ 新建 {len(head)} 行，开头：")
            lines.extend(f"│ + {_sanitize_card_field(ln)}" for ln in head[:CARD_DIFF_MAX_LINES // 2])
        lines.append("└─ 执行? [y/N]: ")
        return "\n".join(lines)

    if name == "bash":
        command = str(args.get("command", ""))
        purpose = _sanitize_card_field(args.get("description")) or "（未说明）"
        tags = bash_danger_tags(command)
        lines = [
            "┌─ 命令审批 ──────────────────────────────────────",
            f"│ 工具: {name}",
            # 危险标记行必须排在一切模型可控字段（用途、命令原文）之前：session 取卡面第一处
            # 「危险标记:」，用途或命令里夹带的同样字样骗不过
            f"│ 危险标记: {' '.join(tags) if tags else '无'}",
            f"│ 用途: {purpose}",
            f"│ 目录: {episode_dir or '仓库根'}",
            "│ 命令:",
        ]
        cmd_lines = command.splitlines() or [""]
        for raw in cmd_lines[:CARD_DIFF_MAX_LINES]:
            lines.append("│   " + re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", raw))
        if len(cmd_lines) > CARD_DIFF_MAX_LINES:
            lines.append(f"│   ……另有 {len(cmd_lines) - CARD_DIFF_MAX_LINES} 行未显示")
        lines.append("└─ 执行? [y/N]: ")
        return "\n".join(lines)

    if name == "acquire_propose":
        raw_cands = args.get("candidates")
        items = [c for c in raw_cands if isinstance(c, dict)] if isinstance(raw_cands, list) else []
        lines = [
            "┌─ 提案审批 ──────────────────────────────────────────",
            f"│ 工具: {name}",
            "│ 目标: data/library/incoming/candidates.json（追加候选，不触发抓取）",
            f"│ 候选 ({len(items)} 条):",
        ]
        for idx, c in enumerate(items[:10], 1):
            t = _sanitize_card_field(c.get("title", "")) or "无标题"
            k = _sanitize_card_field(c.get("type", "")) or "?"
            u = _sanitize_card_field(c.get("url", ""))
            lines.append(f"│   {idx}. {t}（{k}）{u}")
        if len(items) > 10:
            lines.append(f"│   …共 {len(items)} 条")
        lines.extend([
            "│ 危险标记: 无",
            "└─ 执行? [y/N]: ",
        ])
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

    # D59：素材登记的门禁豁免（`acquire register --waive 理由`，含 argparse 前缀缩写与 `=` 写法）
    module = next((tok.removeprefix("pipeline.") for tok in argv_list if tok.startswith("pipeline.")), None)
    if module == "acquire" and any(
        tok.startswith("--w") and "--waive".startswith(tok.split("=", 1)[0]) for tok in all_tokens
    ):
        danger_tags.append("[门禁豁免] 素材门禁没过，按命令里的理由照样登记")

    if module == "calibration":
        danger_tags.append("[全局配置] 改 config 里的标定值，所有期生效")

    danger_str = " ".join(danger_tags) if danger_tags else "无"

    # 解封物判定（🔵 终审：review --approve 产出 04-clips.approved.json，非「只读产物」）
    is_review_approve = _matches_flag_prefix(all_tokens, "--approve") and (
        "review" in cmd_str or any("review" in t for t in argv_list)
    )

    is_corrections = "pipeline.corrections" in argv_list
    is_global_voice = is_corrections and "global" in argv_list
    if is_global_voice:
        danger_tags.append("[全局] 改 config/voice.json 的读音表，影响所有番、所有期")
        danger_str = " ".join(danger_tags)

    if is_cloud:
        nature = "☁ 云端计费动作（计费审批）"
    elif is_corrections:
        nature = "写读音表（" + ("全局 config/voice.json" if is_global_voice else "本期 03-audio/corrections.json") + "）"
    elif is_render:
        nature = "本地成片渲染 | 预计耗时较长（分钟级）"
    elif is_review_approve:
        nature = "产生解封物（推进工序，不可回退）"
    elif module == "acquire" and _matches_flag_prefix(all_tokens, "--to-patch"):
        nature = "把 incoming/ 里的文件挪进本期 patch_assets/（期内补料）"
    elif module == "calibration":
        nature = "写全局配置（标定值，AGENTS.md Code Freeze 例外 D60）"
    elif module == "notes_review":
        nature = "番剧笔记对抗审查（每集一次模型调用，出网；写新报告，不覆盖旧报告）"
    elif module in LIBRARY_WRITE_MODULES:
        nature = "写素材库 data/library/（所有期共用）"
    else:
        nature = "本地只读产物生成 | 预计分钟级"

    lines = [
        "┌─ 执行审批 ──────────────────────────────────────────",
        f"│ 工具: {name}",
        f"│ 命令: {cmd_str}",
        f"│ 性质: {nature}",
        f"│ 危险标记: {danger_str}",
    ]
    lines += [f"│ {piece}" for raw in command_preview or [] for piece in str(raw).split("\n")]
    lines.append("└─ 执行? [y/N]: ")
    return "\n".join(lines)


def log_approval_decision(
    ep_dir: Path | str | None,
    tool_name: str,
    target: str,
    decision: str,
    latency_s: float | None = None,
    *,
    emit_event: bool = True,
    channel: str | None = None,
) -> None:
    """追加一行审批记录到期目录 _agent/approvals.jsonl（Spec §3.2-6, §5 M17）。

    `latency_s` = 卡片弹出→人类按键的决策耗时（秒）。它是 §7-1「秒按 y」
    审批疲劳判据的唯一可读量：只有卡片时间戳无法区分「秒敲」与「读完后敲」。

    `channel`（Spec 9 C-R4）= 答复从哪条线进来（`tty` / `protocol`）。**默认为 None 时
    记录逐字节不变**——历史记录与只关心旧字段的读者都不受影响。

    .jsonl 后缀天然在读域白名单外，不进读域。
    """
    norm_y = decision.strip().lower() in ("y", "yes")
    resolved_decision = "approved" if norm_y else "rejected"
    if emit_event:
        try:
            from pipeline.jobs import EventType, get_publisher

            get_publisher().emit(
                EventType.APPROVAL_RESOLVED,
                {
                    "command": target,
                    "decision": resolved_decision,
                    "source": tool_name,
                },
                episode_dir=ep_dir,
            )
        except Exception:
            pass

    if not ep_dir:
        return
    d = Path(ep_dir)
    agent_dir = d / "_agent"
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        log_file = agent_dir / "approvals.jsonl"
        norm_decision = "y" if norm_y else "n"
        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tool": tool_name,
            "target": target,
            "command": target,
            "decision": norm_decision,
            "decision_latency_s": round(latency_s, 3) if latency_s is not None else None,
        }
        if channel is not None:
            record["channel"] = channel
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[WARN] 审批记账失败: {exc}")


