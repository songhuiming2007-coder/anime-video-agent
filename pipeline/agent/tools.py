"""LLM 受控工具注册表与 Pipeline 白名单执行器（Spec §2.4, §2.5, §5 PR1）。
"""

from __future__ import annotations

import codecs
import collections
import importlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from pipeline import paths
from pipeline.cloud import validate_extra_args

# 期文件写入白名单（§2.4）。D43 / Spec 17（ADR-0027）：所有模式开放全部工具，
# 白名单不再按 scope 收窄——任何模式可写这三份。审卡：01-topic.md、07-titles.md 每次弹卡；
# 02-script.draft.md 每轮前 DRAFT_FREE_WRITES_PER_TURN 次免卡（2026-10-08 spec §A）。
# Spec 12 C12-R1：扩入 `07-titles.md`——标题候选的落盘点（07-titles.md 从来就是
# 「agent 写候选、人定稿」的文件，不是 02-script.md 那种定稿物；写仍弹人审卡）。
# D47（2026-10-08 人裁决，修订 ADR-0024 决策 1）：扩入 02-script.md——只能改不能新建、每次弹卡带 diff、
# 写前全量留底；02-script.md 存在后草稿冻结（它是 02-diff.patch 的基线）。
EPISODE_WRITABLE_FILES: set[str] = {
    "01-topic.md",
    "02-script.draft.md",
    "02-script.md",
    "07-titles.md",
    # D48 ②（2026-10-09）：02.8 报告的终审表由写稿会话填（采纳 / 驳回 + 理由），每次弹卡带 diff
    "02-adversarial.md",
    # D84（2026-10-13 人裁决）：写稿期缺画面的登记处（段号 / 要什么画面 / 原片为何给不了 / 补料进度）。
    # 没有代码读它，纯给人和后续回合看；写入免卡、覆盖前留底，与其余期文件同一纪律
    "02-asset-requests.json",
}

# D81：企划会话的写入白名单（Q4：机读字段变了才弹卡，散文免卡）。卷 / 普通期会话不含它
PROJECT_FILE = "00-project.md"
PROJECT_WRITABLE_FILES: frozenset[str] = frozenset({PROJECT_FILE})

# 草稿是模型自己的工作稿，每次覆盖前留底（DRAFT_HISTORY_KEEP 份），所以写入可回退，每轮前几次免卡。
# 3 = 写一次 + 修两次，与 creative.md「同一项连修两次仍没过就停下来问人」同一个数；
# 超过即恢复弹卡，作为原地打转的刹车（2026-10-08 董香二期段落 3 连写十余轮）。
DRAFT_FILENAME = "02-script.draft.md"
DRAFT_FREE_WRITES_PER_TURN = 3
DRAFT_HISTORY_DIR = ("_agent", "draft-history")
DRAFT_HISTORY_KEEP = 20

# D47：定稿的留底不修剪——02-diff.patch 是「机器初稿 vs 人定稿」的标注数据，agent 代笔混进来的措辞
# 要靠这些快照才能在分析时剔出去；每份 KB 级，一期至多几十份。
SCRIPT_FILENAME = "02-script.md"
SCRIPT_HISTORY_DIR = ("_agent", "script-history")
# ADR-0030（D78）：写期文件全部免卡后，其余白名单文件（01-topic.md、07-titles.md、02-adversarial.md）
# 覆盖前同样留底，人要回退直接从这里拿；不修剪（KB 级纯文本，一期至多几十份）
FILE_HISTORY_DIR = ("_agent", "file-history")

# D61 / ADR-0028：write_note 的目标与留底目录
NOTE_TARGETS = ("notes", "review")
NOTES_HISTORY_DIR = "_history"
REVIEW_REPORT_MARK = "-对抗审查报告"
# 番名：不许空、不许带路径分隔符 / 控制字符、不许以 . 或 _ 开头（_history 等是保留目录）
_ANIME_NAME = re.compile(r"^[^/\\\x00-\x1f._][^/\\\x00-\x1f]*$")


def script_write_refusal(ep_dir: Path | str | None, filename: str) -> str | None:
    """D47 两条写入前提，弹卡前（review_tool_call）与落盘前（write_episode_file）各查一次；None = 放行。"""
    if ep_dir is None:
        return None
    script = Path(ep_dir) / SCRIPT_FILENAME
    if filename == SCRIPT_FILENAME and not script.exists():
        return (
            f"{SCRIPT_FILENAME} 还不存在：从草稿新建定稿是人的动作（桌面端 02.5「从草稿新建」或终端 cp），"
            "建好之后才能改它"
        )
    if filename == DRAFT_FILENAME and script.exists():
        return (
            f"已进入 02.5（{SCRIPT_FILENAME} 已存在）：草稿是 02-diff.patch 的基线，不再改；"
            f"要改稿请写 {SCRIPT_FILENAME}"
        )
    return None

# 制片期（creative / pipeline）允许执行的 pipeline 模块白名单（§2.4）
PIPELINE_MODULES: set[str] = {
    "check_script",
    "tts",
    "clips",
    "review",
    "render",
    "qc",
    "cover",
    "bgm",
    "status",
    # D51：读音纠错录入（期级 corrections.json / 全局 config/voice.json）。会写盘，所以不进只读集合、一律弹卡
    "corrections",
    # D48 ②：02.8 零上下文对抗审查（一次 LLM 调用，写 02-adversarial.md）。出网 + 写盘，弹卡
    "adversarial",
    # D99 PR-3：事实库独立核对（一次 LLM 调用，写 facts-review.md，不支持的条目回落待核）
    "fact_review",
    # D59：番剧笔记的时间网格（写 data/library/timeline/），无子命令，弹卡
    "timeline",
    # D59：期内补料——scout 出缺口清单（写 scout-ticket-*.md），ingest_patch 建本期补丁池（含 --reset）。
    # 两者都只接期目录一个位置参数，自动补位
    "scout",
    "ingest_patch",
    # D61：番剧笔记零上下文对抗审查（每集一次 LLM 调用 + 机械核销，写新报告、不覆盖旧的）。出网 + 写盘，弹卡；
    # 按番不按期，不补期目录
    "notes_review",
    # D61 终审辅助：字幕逐字查询（子串/时间窗），只读索引后打印——进只读集合
    "subgrep",
    # D82：撞 bug 开工单。open 一律逐条人审（HUMAN_GATED_COMMANDS）；list / show 只读免卡（只打系统解析字段）
    "tickets",
    # D81 PR-4：跨卷镜头 / 配乐重复检查（警告不拦）。默认只读、--write 才写 <企划>/00-crosscheck.md，
    # 所以不整模块进 READONLY_PIPELINE_MODULES（只读判定按参数级做：crosscheck_writes）。
    # 位置参数是企划目录：企划会话补企划目录，卷会话补所属企划目录（validate_pipeline_command 的 project_dir）
    "crosscheck",
    # D81 PR-5：企划总装（各卷成片 → 合集成片）。只在企划会话放行（PROJECT_ONLY_MODULES）；
    # 每次逐条人审、不开放「本会话都批准」（HUMAN_GATED_COMMANDS），卡面是 assemble.preview
    "assemble",
}

# D81（人裁决 Q2：企划会话只做规划、跨卷审查、总装）：企划会话的 run_pipeline 只放行这几个企划级模块，
# 外加不绑期目录的只读模块 subgrep 与 D82 的 tickets（PROJECT_SESSION_EXTRA；tickets 落地前白名单本就拒）
PROJECT_PIPELINE_MODULES: frozenset[str] = frozenset({"assemble", "crosscheck"})
PROJECT_SESSION_EXTRA: frozenset[str] = frozenset({"subgrep", "tickets", "fact_review"})
# D95（2026-10-14，经人同意，修订 D81 §4.2）：素材抓取在企划会话里放行——候选（library/incoming/candidates.json）、
# 下载（library/incoming/）、登记（--pool 进按番的素材池）都是全库共用，不写进母目录；真实流程「立项先预收集」时
# 卷可能还没建。唯一写进某一期的 `register --to-patch` 仍拒（acquire_targets_volume）
PROJECT_LIBRARY_MODULES: frozenset[str] = frozenset({"acquire"})
# 只在企划会话里放行的模块（卷 / 普通期会话拒并指路）
PROJECT_ONLY_MODULES: frozenset[str] = frozenset({"assemble"})

# 其中纯只读、免审卡的模块（2026-10-08 spec §A）：check_script 只读稿件与索引后打印报告，
# status 只读期目录，subgrep 只读字幕索引后打印；都不落盘（入选前已 grep 过无写调用）。新增成员前必须同样核实。
READONLY_PIPELINE_MODULES: frozenset[str] = frozenset({"check_script", "status", "subgrep"})

# asset 侧纯只读、免审卡的子命令（2026-10-09，D54 ③）：`vindex who` 只读在场索引与镜头表后打印，
# 全链（load_presence / shots.load / display_names）无写调用。新增成员前必须同样核实。
# D59（2026-10-09）读码核实：`acquire gate` 只 ffprobe / Pillow 读文件后打印判据表；`ingest probe/intact`
# 只跑 ffprobe / ffmpeg -f null；`ingest verify` 抽音频进 tempfile.mkdtemp、finally 里 rmtree；
# `vindex status/search`、`subindex search` 只读索引与本地嵌入模型后打印。全链无写调用。
# `shots calibrate` 不在此列：带 --sheet / --long 会写联系表。
READONLY_ASSET_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "vindex": frozenset({"who", "status", "search"}),
    "acquire": frozenset({"gate"}),
    "ingest": frozenset({"probe", "intact", "verify"}),
    "subindex": frozenset({"search"}),
    "calibration": frozenset({"show"}),
    # D82：tickets list / show 只读 tickets/*.md 后打印系统解析出的字段（读码核实：全链无写调用、不出正文）
    "tickets": frozenset({"list", "show"}),
}

# 逐条人审的模块命令（D82 §4.4）：run_pipeline 命中 → 每次弹卡、不开放「本会话都批准」；bash 直接跑也弹卡
# （status_card 的 `[须人审的流水线命令]` 规则由本表生成）。子命令为 None = 该模块任何调用。
# 前三条是 AGENTS.md Code Freeze 三条例外（写 config/，ADR-0030 决策 2）；`session.CONFIG_WRITE_COMMANDS` 是它的子集。
HUMAN_GATED_COMMANDS: frozenset[tuple[str, str | None]] = frozenset({
    ("corrections", "global"), ("calibration", "set"), ("bgm", "register"),
    ("tickets", "open"),
    # D81 §4.8（人裁决 Q5）：每次总装都弹卡，--replace 覆盖已有成片同样逐条人审
    ("assemble", None),
    # D99：一手 / 二手只能由人定（人裁决 2026-10-15）；ava 经 bash 跑 promote 即弹卡
    ("facts", "promote"),
})

# D82 §4.7：交接文本由 core 模板生成，不取模块输出里的任意文字
TICKET_HANDOFF_TEMPLATE = (
    "请处理工单 `{path}`：先读工单，按仓库 AGENTS.md 出修复方案等我批准，不要直接改；"
    "修完在工单「处理结果」写状态、提交号与续跑命令，并把它整理成 docs/dev/issues/README.md 的正式条目"
    "（工单原文不进 commit）。"
)


def ticket_handoff(content: Any, root: Path | None = None) -> str | None:
    """成功的 `tickets open` 工具结果 → 交接文本；其余（失败、不是 tickets open、路径不在 tickets/ 内）→ None。

    严格解析 stdout 末行 `TICKET_JSON {…}`，只取 `path`，且 resolve 后必须在 `<仓库根>/tickets/` 内；
    `TICKET_JSON` 里多塞的键（如伪造的 handoff）一律忽略。
    """
    try:
        outer = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return None
    result = outer.get("result") if isinstance(outer, dict) else None
    if not isinstance(result, dict) or result.get("returncode") != 0:
        return None
    argv = [str(a) for a in result.get("argv") or []]
    at = argv.index("-m") + 1 if "-m" in argv else len(argv)
    if argv[at:at + 2] != ["pipeline.tickets", "open"]:
        return None
    lines = [ln for ln in str(result.get("stdout_tail") or "").splitlines() if ln.strip()]
    if not lines or not lines[-1].startswith("TICKET_JSON "):
        return None
    try:
        meta = json.loads(lines[-1][len("TICKET_JSON "):])
    except ValueError:
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("path"), str):
        return None
    target = Path(meta["path"]).resolve()
    tickets_root = (Path(root or paths.ROOT) / "tickets").resolve()
    if tickets_root not in target.parents or target.suffix != ".md":
        return None
    return TICKET_HANDOFF_TEMPLATE.format(path=str(target))

# Asset Scope 允许执行的 Phase 0 子命令白名单（§2.4 Y1-r8, Y2-r10）
# Spec 9 S6-R1：增 `acquire: {fetch}`——抓取卡批准后由内核经注入的执行器跑（工具实现与 schema 零改动）。
# D59（2026-10-09）：Phase 0 与换条件重测里原本要人在终端跑的子命令全部收进来（只读的进
# READONLY_ASSET_SUBCOMMANDS 免卡，其余弹卡）。`vindex scene` 是已删除的旧通道，不放。
ASSET_COMMANDS: dict[str, set[str]] = {
    "ingest": {"phase0", "probe", "intact", "verify", "subs", "run", "sources"},
    "shots": {"build", "frames", "caption-frames", "calibrate", "rebuild", "gallery"},
    "vindex": {"captions", "embed", "who", "presence", "search", "status"},
    "subindex": {"build", "search"},
    "vprobe": {"tagger", "presence", "scene", "captions"},
    "faces": {"detect", "cluster", "sheet", "name", "presence"},
    # D60：标定值写入 config（AGENTS.md Code Freeze 第二个例外）；set 弹卡前预检，show 只读
    "calibration": {"set", "show"},
    "cloud": {"status", "logs", "doctor", "up", "down", "run", "push", "pull", "relocate-data", "clean-frames", "fix-env"},
    # D59：gate（只读免卡）/ register（登记或 --to-patch，弹卡）/ forget（台账移走一条，弹卡）。
    # 人只批卡，不再在终端手敲（ADR-0021 §3 原意，S6-R1 只接了 fetch）。
    # D61 后续（2026-10-10）：probe 可得性探针——出网只解析不下载，URL 清单经人卡，弹卡
    "acquire": {"fetch", "gate", "register", "forget", "probe"},
}

# 出网敏感目录与关键词（§2.5 Y2-r19）：只剩凭据文件。
#
# D89（2026-10-13）去掉 `03-audio/manifest.json` 与 `03-audio/voice.json`。当初列它们的理由是
# 「没给 LLM 读它们的工具，碰不到就不会发」；bash 落地后这个前提没了——`cat 03-audio/*.json`
# 不含这串字面量照样读得到（子串比对拦不住内容），而老实写出完整路径的正常提问整轮回滚
# （《束胸力学》「配音总长多少？」）。两份文件里没有凭据：引擎名、读音表、参考音频路径、
# 各段文本 / 时长 / CER / 种子，文本与稿件本就出网的部分重合。凭据两条照旧拦（含大小写变体）。
RESTRICTED_EGRESS_PATTERNS: tuple[str, ...] = (
    "cloud.local.json",
    "agent.local.json",
)


# 回合脚注的查证计数（2026-10-09，D48 ①）：四类，桌面端照实显示，不判断模型编没编
LOOKUP_KINDS: tuple[str, ...] = ("subs", "presence", "notes", "web")
_WEB_TOOLS = frozenset({"web_search", "web_fetch", "crawl", "browser"})


def lookup_kind(name: str, args: dict[str, Any]) -> str | None:
    """一次已执行的工具调用算哪类查证；不是查证返回 None。

    字幕 / 笔记按 `search_notes` 的 `source` 分（缺省是笔记）；在场 = `run_pipeline` 的 `vindex who`。
    """
    if name == "search_notes":
        return "subs" if str(args.get("source") or "notes").strip() == "subs" else "notes"
    if name in _WEB_TOOLS:
        return "web"
    if name == "run_pipeline":
        try:
            tokens = shlex.split(str(args.get("command") or ""))
        except ValueError:
            return None
        if tokens[:1] and tokens[0] in ("python", "python3", sys_python()):
            tokens = tokens[1:]
            if tokens[:1] == ["-m"]:
                tokens = tokens[1:]
        if len(tokens) >= 2 and tokens[0].removeprefix("pipeline.") == "vindex" and tokens[1] == "who":
            return "presence"
    return None


def scrub_restricted(text: str) -> str:
    """受限字样替换为 [已脱敏]（re.IGNORECASE）。状态卡、抓回网页、工具结果三处共用（D52）。

    只换字样不判内容：出网断言比的也只是文件名（ADR-0026）。`re.IGNORECASE` 与断言的
    casefold 口径不同，漏掉的变体由断言照拦——脱敏是减少误拦，不是第二道闸。
    """
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        text = re.sub(re.escape(pattern), "[已脱敏]", text, flags=re.IGNORECASE)
    return text


def resolve_episode_dir(episode_dir: Path | str, root: Path | None = None) -> Path:
    """期目录双端 resolve 校验（fail-closed）：返回解析后的绝对期目录路径。

    `write_episode_file`（LLM 写工具）与 `agent.cli` 的停机点子命令群（Spec 11 §3.1）
    共用**同一份**解析纪律——两处各写一份迟早分叉。三种越界一律 PermissionError：
    落进 `pipeline/`、不是 `data/episodes` 的子目录、仓库根或 `/tmp`。
    """
    base = Path(root or paths.ROOT)
    resolved_ep = Path(episode_dir).resolve()

    # 1. 拦截对 pipeline/ 源码目录的操作（优先触发 Code Freeze）
    repo_pipeline = (base / "pipeline").resolve()
    if repo_pipeline in resolved_ep.parents or resolved_ep == repo_pipeline:
        raise PermissionError("禁止修改 pipeline/ 源码目录文件，触发 Code Freeze 护栏")

    # 2. 纵深防御（fail-closed，B4-r6）：期目录必须落在 data/episodes 之下
    episodes_root = (base / "data" / "episodes").resolve()
    if not episodes_root.exists():
        raise PermissionError(f"data/episodes 不可达（外置盘未挂载？），拒绝写入: {episodes_root}")
    if resolved_ep == base.resolve() or resolved_ep == Path("/tmp").resolve():
        raise PermissionError(f"禁止将仓库根或 /tmp 作为期目录写入: {resolved_ep}")
    if resolved_ep == episodes_root or episodes_root not in resolved_ep.parents:
        raise PermissionError(f"期目录必须位于 {episodes_root} 之下: {resolved_ep}")

    # 4. D81 §4.1：企划目录本身不是期（母目录 README 写着严禁在母目录写 01-topic.md，§三 #2）
    from pipeline.project import classify_dir

    if classify_dir(episode_dir, root=base) == "project":
        raise PermissionError(f"这是企划目录，不是期：{episode_dir}（企划级改动用企划会话）")

    return resolved_ep


def resolve_project_dir(project_dir: Path | str, root: Path | None = None) -> Path:
    """企划目录校验（D81 §4.4 第 4 条）：与 resolve_episode_dir 同样的三种越界检查，再加 classify == project。

    不经 resolve_episode_dir——后者对企划目录一律拒（红队 R-3）。返回 resolve 后的企划目录。
    """
    from pipeline.project import classify_dir

    base = Path(root or paths.ROOT)
    resolved = Path(project_dir).resolve()
    repo_pipeline = (base / "pipeline").resolve()
    if repo_pipeline in resolved.parents or resolved == repo_pipeline:
        raise PermissionError("禁止修改 pipeline/ 源码目录文件，触发 Code Freeze 护栏")
    episodes_root = (base / "data" / "episodes").resolve()
    if not episodes_root.exists():
        raise PermissionError(f"data/episodes 不可达（外置盘未挂载？），拒绝写入: {episodes_root}")
    if resolved == episodes_root or episodes_root not in resolved.parents:
        raise PermissionError(f"企划目录必须位于 {episodes_root} 之下: {resolved}")
    if classify_dir(project_dir, root=base) != "project":
        raise PermissionError(f"不是企划目录：{project_dir}")
    return resolved


def write_episode_file(
    episode_dir: Path | str,
    filename: str,
    content: str,
    confirmed: bool = False,
    root: Path | None = None,
) -> Path:
    """受控期文件写入工具（Spec §2.4 Code Freeze 护栏）。

    纪律：
    1. 文件名在白名单：{01-topic.md, 02-script.draft.md, 07-titles.md}（D43 起不再按 scope 收窄）；
    2. 双端 resolve 防 symlink 穿透；
    3. 三种越界拦截：写 pipeline/、写父级/祖先目录、写白名单外文件；
    4. 写 01-topic.md 强制人类确认；
    5. D47：02-script.md 只能改不能新建，它存在后草稿冻结（script_write_refusal）；
    6. 覆盖任何白名单文件前留底（草稿限份数，其余不修剪；ADR-0030）；
    7. 落盘必须走 paths.atomic_write。
    """
    # 文件名白名单检查
    clean_name = Path(filename).name
    if clean_name not in EPISODE_WRITABLE_FILES or filename != clean_name:
        raise PermissionError(
            f"文件 '{filename}' 不在期文件写入白名单内（仅放行: {sorted(EPISODE_WRITABLE_FILES)}）"
        )

    # 路径解析与双端 resolve 校验（期目录级检查见 resolve_episode_dir）
    base = Path(root or paths.ROOT)
    resolved_ep = resolve_episode_dir(episode_dir, root=root)
    target_resolved = (Path(episode_dir) / clean_name).resolve()

    # 1. 拦截对 pipeline/ 源码目录的修改（优先触发 Code Freeze）
    repo_pipeline = (base / "pipeline").resolve()
    if (
        repo_pipeline in target_resolved.parents
        or str(target_resolved).startswith(str(repo_pipeline))
    ):
        raise PermissionError("禁止修改 pipeline/ 源码目录文件，触发 Code Freeze 护栏")

    # 2. 拦截父级或兄弟目录越界
    if target_resolved.parent != resolved_ep:
        raise PermissionError(f"目标路径越界：{target_resolved} 不在当期根目录 {resolved_ep} 下")

    # 3. 写 01-topic.md 强制人类确认 (B7)
    if clean_name == "01-topic.md" and not confirmed:
        raise PermissionError("写入 01-topic.md 是关键立项操作，必须获得人类显式确认")

    # 4. D47 写入前提（弹卡前已查过一次；这里防绕过 review 的直接调用）
    refusal = script_write_refusal(resolved_ep, clean_name)
    if refusal:
        raise PermissionError(refusal)

    # 5. 覆盖前留底：草稿保留最近 DRAFT_HISTORY_KEEP 份，定稿全量保留
    if clean_name == DRAFT_FILENAME and target_resolved.exists():
        _keep_history(resolved_ep, target_resolved, DRAFT_HISTORY_DIR, DRAFT_HISTORY_KEEP)
    elif clean_name == SCRIPT_FILENAME and target_resolved.exists():
        _keep_history(resolved_ep, target_resolved, SCRIPT_HISTORY_DIR, None)
    elif target_resolved.exists():
        _keep_history(resolved_ep, target_resolved, FILE_HISTORY_DIR, None)

    # 6. 原子落盘
    paths.atomic_write(target_resolved, content)
    return target_resolved


def _keep_history(ep_dir: Path, target: Path, sub: tuple[str, ...], keep: int | None) -> None:
    """把即将被覆盖的文件存进 `<期>/<sub>/<UTC 纳秒>-<文件名>`；keep=None 不修剪，否则只留最近 keep 份。"""
    hist = ep_dir.joinpath(*sub)
    hist.mkdir(parents=True, exist_ok=True)
    ns = time.time_ns()  # 一次取时：秒与纳秒同源，文件名字典序 = 时间序
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(ns // 1_000_000_000)) + f"{ns % 1_000_000_000:09d}"
    paths.atomic_write(hist / f"{stamp}-{target.name}", target.read_text(encoding="utf-8"))
    if keep is None:
        return
    olds = sorted(p for p in hist.iterdir() if p.name.endswith(f"-{target.name}"))
    for old in olds[:-keep]:
        old.unlink()


# 自动补位模块的带值旗标（非布尔 flag）。漏登记 → 旗标的值被当成位置参数 → 期目录不补位、
# 位置参数计数误拒。`tests/test_agent_tools.py` 用 ast 扫各模块的 add_argument 守这张表（D53）。
PIPELINE_VALUED_FLAGS: frozenset[str] = frozenset({
    "--redo", "--config", "--review", "--anime", "--out", "--ref", "--seed",
    "--pattern", "--episode", "--note", "--target", "--session", "--floor",
    "--index-dir", "--expect-size", "--expect-mtime-ns", "--pick", "--character",
    "--text", "--word", "--pinyin", "--homophone", "--expect", "--script",
    "--type", "--batch",  # D59：scout --type、ingest_patch --batch
    "--call", "--blocked", "--symptom", "--expected", "--repro", "--why",  # D82：tickets open
})

# 自动补位的模块：argparse 都只有一个位置参数（tts 另有子命令词 run / probe）
_AUTOFILL_MODULES: tuple[str, ...] = ("tts", "clips", "review", "render", "qc", "cover", "status", "corrections",
                                      "adversarial", "scout", "ingest_patch", "tickets", "crosscheck", "assemble",
                                      "fact_review")
# 带子命令的模块：子命令词不计入位置参数，期目录补在它之后
_SUBCOMMAND_WORDS: dict[str, tuple[str, ...]] = {
    "tts": ("run", "probe"),
    "corrections": ("add", "global", "check"),
    "tickets": ("open", "list", "show"),
}
_SEGMENT_LABEL_RE = re.compile(r"^\d+(\.\d+)?$")


def _extract_positional_args(args: list[str]) -> list[str]:
    """提取真正的命令行位置参数，跳过旗标及其参数值（带值旗标见 PIPELINE_VALUED_FLAGS）。"""
    pos: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            flag = a.split("=")[0]
            if "=" in a:
                i += 1
            elif flag in PIPELINE_VALUED_FLAGS:
                i += 2
            else:
                i += 1
        else:
            pos.append(a)
            i += 1
    return pos


def _positional_refusal(module: str, args: list[str]) -> str | None:
    """自动补位模块的位置参数结构检查（D53 ②）。段号是否存在由 tts 运行时对照稿件判，这里只拦结构错。"""
    # 规则 2：`--redo` 的值后紧跟裸段号（`--redo 2 4 5`）——tts 只吃第一个，其余被当成期目录 / 多余参数
    for i, a in enumerate(args):
        if a == "--redo" and i + 1 < len(args):
            labels, rest = [args[i + 1]], args[i + 2:]
        elif a.startswith("--redo="):
            labels, rest = [a.split("=", 1)[1]], args[i + 1:]
        else:
            continue
        for tok in rest:
            if not _SEGMENT_LABEL_RE.match(tok):
                break
            labels.append(tok)
        if len(labels) > 1:
            fixed = ",".join(labels)
            return f"拒绝执行：--redo 只接一个值，段号要用逗号连写。正确写法：{module} --redo {fixed}"
    # 规则 1：位置参数最多 1 个（tts 打头的子命令词不计）
    pos = _extract_positional_args(args)
    if pos and pos[0] in _SUBCOMMAND_WORDS.get(module, ()):
        pos = pos[1:]
    if len(pos) > 1:
        return (
            f"拒绝执行：pipeline.{module} 只接一个位置参数（期目录，可省略、由 ava 自动补上），"
            f"多出了 {' '.join(pos[1:])}。带值的选项要紧跟它的值，多个值用逗号连写。"
        )
    return None


def validate_pipeline_command(
    cmd_tokens: list[str] | str,
    ep_dir: Path | str | None = None,
    project_dir: Path | str | None = None,
) -> tuple[bool, str, list[str]]:
    """白名单子命令校验执行器（Spec §2.4 Y1-r8, Y1-r11, R1-r10, B1-r10；D43 / Spec 17 合表）。

    D43 / Spec 17 §3.3：不再按 scope 分派，合成一份放行表——
    `PIPELINE_MODULES` 的模块（不限子命令）∪ `ASSET_COMMANDS` 的模块与各自子命令清单。
    两条写死的合表语义：
    (a) 模块属于 `ASSET_COMMANDS` 时子命令校验**永远生效**（将来两表若出现重名模块，以子命令限制为准）；
    (b) 当期目录自动补位只对 `PIPELINE_MODULES` 侧的模块生效，asset 侧模块不补位（维持现状）。

    D81 §9.3 ⑥：`project_dir` 是卷 / 企划会话绑定的企划目录（取自 ToolContext，宿主绑定，不取模型参数），
    只用于给 `crosscheck` 补位——卷会话补的是所属企划目录，不是卷目录。

    返回: (is_valid, message, normalized_argv)
    """
    if isinstance(cmd_tokens, str):
        tokens = shlex.split(cmd_tokens.strip())
    else:
        tokens = list(cmd_tokens)

    if not tokens:
        return False, "命令为空", []

    # 剥离前导的 python -m / python3 -m / pipeline. 前缀
    idx = 0
    if tokens[idx] in ("python", "python3", sys_python()):
        idx += 1
        if idx < len(tokens) and tokens[idx] == "-m":
            idx += 1

    if idx >= len(tokens):
        return False, "缺少执行模块", []

    mod_token = tokens[idx]
    if mod_token.startswith("pipeline."):
        module = mod_token.split(".", 1)[1]
    else:
        module = mod_token
    args = tokens[idx + 1:]

    # 1. 拒收 --force / --force-all 标志（含 --force=x, --force-all=true 变体）
    #    Spec 9 C-R5（自查 S-1）：argparse 的前缀缩写让 `--force-a` 能命中 `--force-all`，
    #    所以按**前缀**判定（`--` 开头、`split("=")[0]`、长度 ≥ 3），人与模型两条路同时生效。
    for a in args:
        bare = a.split("=")[0]
        if not bare.startswith("--") or len(bare) < 3:
            continue
        forced = bare[2:]
        if not any(flag.startswith(forced) for flag in ("force", "force-all")):
            continue
        flag_name = "--force-all" if "force-all".startswith(forced) and forced != "force" else "--force"
        if module == "cloud" and args and args[0] == "down":
            return (
                False,
                "拒绝执行：cloud down --force 会强行销毁正在运行的后台任务！请先运行 cloud status 确认无活跃任务。",
                [],
            )
        return (
            False,
            f"拒绝执行：禁止在 ava 中使用 {flag_name} 全量覆盖！请使用增量参数：--redo <段号> 或 --apply-patch。",
            [],
        )

    # 2. 绝对拒收 cloud exec（R1-r10）
    if module == "cloud" and args and args[0] == "exec":
        return (
            False,
            "拒绝执行：cloud exec 绕过安全白名单直接执行任意远端命令，已被护栏永久禁用。",
            [],
        )

    # 3. 合表白名单检查（D43 / Spec 17 §3.3：不再按 scope 分派）
    in_pipeline = module in PIPELINE_MODULES
    in_asset = module in ASSET_COMMANDS
    if not in_pipeline and not in_asset:
        return (
            False,
            f"模块 'pipeline.{module}' 不在白名单内（放行: {sorted(PIPELINE_MODULES)} ∪ {sorted(ASSET_COMMANDS)}）",
            [],
        )

    # (a) 模块属于 ASSET_COMMANDS 时，子命令校验永远生效（防未来重名模块被「不限子命令」侧吞掉）
    if in_asset:
        allowed_subs = ASSET_COMMANDS[module]
        if not args or args[0] not in allowed_subs:
            return (
                False,
                f"子命令 '{args[0] if args else ''}' 不在 'pipeline.{module}' 的放行清单内（当前放行: {sorted(allowed_subs)}）",
                [],
            )

        # 对 cloud run 校验 extra_args（直接传 token 列表，🔵 2 优化）
        if module == "cloud" and args[0] == "run":
            if len(args) >= 3:
                extra_tokens = args[3:]
                if extra_tokens:
                    try:
                        validate_extra_args(extra_tokens)
                    except ValueError as exc:
                        return False, f"cloud run 参数非法: {exc}", []

    # (b0) D53：格式错的命令在弹卡前拒（人批准后才 0.2 s 退出码 2 = 白审一次）
    if in_pipeline and module in _AUTOFILL_MODULES:
        refusal = _positional_refusal(module, args)
        if refusal:
            return False, refusal, []

    # (b) 自动补位只对原 PIPELINE_MODULES 侧模块生效，asset 侧不补位
    if in_pipeline:
        if module in ("crosscheck", "assemble"):
            # 补企划目录（字面路径：企划目录的身份按字面判，§4.1）；普通期会话没有企划，不补
            if project_dir and not _extract_positional_args(args):
                args = [os.path.abspath(str(project_dir))] + args
        # 自动补位当前期目录参数（🔴 1 修复）
        elif ep_dir:
            ep_path = Path(ep_dir).resolve()
            pos_args = _extract_positional_args(args)
            if module == "corrections":
                if len(pos_args) == 1 and pos_args[0] in _SUBCOMMAND_WORDS["corrections"]:
                    sub_idx = args.index(pos_args[0])
                    args = args[:sub_idx + 1] + [str(ep_path)] + args[sub_idx + 1:]
            elif module in ("tts", "clips", "review", "render", "qc", "cover", "status", "adversarial",
                            "scout", "ingest_patch", "fact_review"):
                if not pos_args:
                    args = [str(ep_path)] + args
                elif module == "tts" and pos_args == ["run"]:
                    run_idx = args.index("run")
                    args = args[:run_idx + 1] + [str(ep_path)] + args[run_idx + 1:]
            elif module == "tickets":
                # D82 §4.1：只给 open 与不带位置参数的 list 补期目录；show <工单号> 不补（补了多一个位置参数被 D53 预检拒）
                if pos_args in (["open"], ["list"]):
                    sub_idx = args.index(pos_args[0])
                    args = args[:sub_idx + 1] + [str(ep_path)] + args[sub_idx + 1:]
            elif module == "check_script":
                if not pos_args:
                    script_file = ep_path / "02-script.md"
                    if not script_file.exists():
                        script_file = ep_path / "02-script.draft.md"
                    args = [str(script_file)] + args
                else:
                    first_pos = pos_args[0]
                    first_path = Path(first_pos)
                    if not first_path.is_absolute() and (ep_path / first_path).exists():
                        pos_idx = args.index(first_pos)
                        args[pos_idx] = str(ep_path / first_path)

    normalized = [sys_python(), "-m", f"pipeline.{module}"] + args
    return True, "校验通过", normalized


# 可信文本的长度门槛（Spec 16 §5.1 第 3 步，🔵-1）：短于此的可信文本一律忽略——
# 防「全文只有一句含路径」的短文档被模型在工具参数里复述一遍就拿到豁免；顺带滤掉空串。
TRUSTED_TEXT_MIN_CHARS = 200


def _spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """needle 在 haystack 中的全部出现区间（允许重叠：起点前移 1）。"""
    out: list[tuple[int, int]] = []
    i = haystack.find(needle)
    while i != -1:
        out.append((i, i + len(needle)))
        i = haystack.find(needle, i + 1)
    return out


def assert_egress_boundary(
    endpoint: str, content: Any, *, trusted_texts: Sequence[str] = ()
) -> None:
    """出网安全边界断言（Spec §2.5 Y2-r19）。

    确保发送给外部 LLM 端点的内容不含敏感目录路径及凭据数据。
    **大小写不敏感**：APFS 默认大小写不敏感，`Cloud.Local.JSON` 与 `cloud.local.json`
    是同一个文件，字面量比较等于半扇门（终审二轮 P0）。

    `trusted_texts`（Spec 16 / ADR-0026，命中位置级豁免）：仓库规程的逐字正文。一次模式命中
    只有**整体落在**某段可信文本的逐字副本区间内才放过，其余命中照旧拦；不做「先删后匹配」。
    可信文本按与 content 相同的序列化口径找（dict/list → JSON 转义形态；str → 原文），
    两边都在 casefold 后的同一坐标系里算区间。为空时与改动前逐字节同判。
    全仓只有 `llm.py::chat_complete` 传它；web 四个出方向不传。
    """
    is_str = isinstance(content, str)
    text = content if is_str else json.dumps(content, ensure_ascii=False)
    folded = text.casefold()
    trusted_spans: list[tuple[int, int]] | None = None  # 惰性：没有命中就不算
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        needle = pattern.casefold()
        if needle not in folded:
            continue
        if trusted_spans is None:
            trusted_spans = []
            for trusted in trusted_texts:
                if len(trusted) < TRUSTED_TEXT_MIN_CHARS:
                    continue
                form = trusted if is_str else json.dumps(trusted, ensure_ascii=False)[1:-1]
                trusted_spans.extend(_spans(folded, form.casefold()))
        for start, end in _spans(folded, needle):
            if not any(a <= start and end <= b for a, b in trusted_spans):
                raise PermissionError(f"拦截出网请求：内容包含受限敏感标记 '{pattern}'")


def sys_python() -> str:
    import sys
    return sys.executable


# ---------------------------------------------------------------------------
# LLM 面向的受控工具表与业务实现（Spec §2.4/§2.5, §5 PR4）
#
# 工具清单不现场发明：config/agent/tools.json 是白名单，本表是它们的唯一实现
# （B3-r6）。改这张表 = 改护栏，不是普通配置。
# ---------------------------------------------------------------------------

# read_artifact 的读域硬排除（Spec §2.5 Y2-r19）：03-audio/（音频与 manifest）与
# 补丁池素材物理上就在期目录里，但属「一律不出网」清单——读域该拒的必须在这里拒，
# 不能靠发送前的字符串断言补漏（漏一个文件名就是一次静默出网）。
# 同理只放行文本类后缀，二进制产物连读出都不给。
READ_DENY_PARTS = frozenset({"03-audio", "04-patch"})
READ_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json", ".patch", ".log"})

MAX_READ_BYTES = 200_000      # read_artifact 单次读出上限
MAX_NOTE_BYTES = 1_000_000    # search_notes 单文件扫描上限
MAX_NOTE_LIMIT = 20           # search_notes 命中条数上限
NOTE_SUFFIXES = {".md", ".txt"}


@dataclass
class ToolContext:
    """一次工具执行的会话上下文。

    scope 只作记录（D43 / Spec 17：不参与任何放行判断）；episode_dir 决定读/写边界。
    **读域与写域都从上下文绑定，不取 LLM 传来的参数**——参数由模型填，边界由人定。
    """
    scope: str = "creative"
    episode_dir: Path | None = None
    root: Path | None = None
    confirmed: bool = False
    # D81（§4.2 / §4.4）：四项都由宿主填，**不取模型参数**。
    # session_kind ∈ episode | volume | project | idea；project_dir = 卷所属企划 / 企划会话自身（字面路径）。
    # human_approved / bound：只在人在卡上批准时为真 / 带卡面所依据的状态（卡上绑定，§4.4）。
    project_dir: Path | None = None
    session_kind: str = "episode"
    human_approved: bool = False
    bound: dict | None = None
    # D99：会话号，抓取留档记它作追溯（宿主填，不取模型参数）；无会话（单测、CLI 直调）为 None
    session_id: str | None = None

    @property
    def base(self) -> Path:
        return Path(self.root or paths.ROOT)


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_artifact": {
        "name": "read_artifact",
        "side_effect": False,
        "adr": "ADR-0018",
        "description": (
            "读取当期目录内文件或 data/library/ 下的笔记（只读）。"
            "读域写死：期目录内 + data/library/，且硬排除 03-audio/ 与 04-patch/（一律不出网）；"
            "越界或非文本类必拒。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对路径，如 01-topic.md、02-script.draft.md、notes/春物.md",
                },
                "area": {
                    "type": "string",
                    "enum": ["episode", "project", "library"],
                    "description": (
                        "可选，读哪个区：episode=当期 / 当卷目录；project=所属企划（卷会话只能读企划顶层的单个文件，"
                        "如 00-project.md；企划会话可读各卷）；library=data/library。省略则按 期 → library → 企划顶层 的顺序找"
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "write_episode_file": {
        "name": "write_episode_file",
        "adr": "ADR-0018",
        "description": (
            "写入当期稿件文件（白名单见 filename）。写 01-topic.md 必须 confirmed=true 且需人显式确认。"
            "02-script.md 还不存在时写 02-script.draft.md；02-script.md 存在后只改 02-script.md（草稿冻结），"
            "不弹卡，写前旧版自动留底；写之前先问人（人已说清怎么改的直接写）。改已有文件的一两段时用 edits 只传改动的片段，不要整篇重写。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "enum": sorted(EPISODE_WRITABLE_FILES | PROJECT_WRITABLE_FILES),
                    "description": "白名单内的文件名；00-project.md 只在企划会话里可写（改卷表 / 企划名 / 合集会弹卡）",
                },
                "content": {"type": "string", "description": "完整文件内容（新建或整篇重写；与 edits 二选一）"},
                "edits": {
                    "type": "array",
                    "description": (
                        "局部替换（与 content 二选一）：按顺序把 old 换成 new；每个 old 必须在文件中恰好出现一次，"
                        "带上足够的上下文保证唯一"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "old": {"type": "string", "description": "要替换的原文片段（逐字）"},
                            "new": {"type": "string", "description": "替换后的文字"},
                        },
                        "required": ["old", "new"],
                        "additionalProperties": False,
                    },
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "写 01-topic.md 时必须为 true（人类已确认）",
                },
            },
            "required": ["filename"],
            "additionalProperties": False,
        },
    },
    "list_episodes": {
        "name": "list_episodes",
        "side_effect": False,
        "adr": "ADR-0018",
        "description": "列出可见期目录（排除 . 与 _ 前缀），返回期名列表与每期的阶段及阻塞标记（episodes_detail）。无参数。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "read_status": {
        "name": "read_status",
        "side_effect": False,
        "adr": "ADR-0018",
        "description": "读取某期的阶段状态卡（当前工序、停机点、advisories、推荐命令）。",
        "parameters": {
            "type": "object",
            "properties": {
                "episode": {"type": "string", "description": "期号或期目录路径；省略则用当期"},
            },
            "additionalProperties": False,
        },
    },
    "run_pipeline": {
        "name": "run_pipeline",
        "adr": "ADR-0018",
        "description": (
            "校验白名单内的 pipeline 子命令（如 tts --redo 2,4,5，段号逗号分隔）并返回规范化 argv。"
            "**校验通过即在本对话回路内真执行，大多不弹卡**（ADR-0030）：有副作用的命令先在对话里问人、人同意再调，"
            "人已明确说了要跑的直接调；只有云端计费（cloud up/run/down 等）、写 config/ 的三条例外"
            "（corrections global、calibration set、bgm register）、越过人工关卡的选项、tickets open、assemble 会弹审批卡。"
            "实时输出与尾部日志（stdout_tail/stderr_tail）回喂给你做汇报；"
            "--force/--force-all/cloud exec 一律拒收。"
            "只读命令：check_script、status、corrections check、"
            "vindex who <番> <集> --start mm:ss --end mm:ss（列该时段画面里已识别的角色；没列出不等于不在场）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "如 'tts --redo 2,4,5'（段号逗号分隔）或 'clips'"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "search_notes": {
        "name": "search_notes",
        "side_effect": False,
        "adr": "ADR-0018",
        "description": (
            "只读检索。source=notes（默认）：在 data/library/ 笔记里做大小写不敏感的子串检索，返回命中片段。"
            "source=subs：检索字幕台词，命中返回集号、时间码、本句与前后句；只给 episode 不给 query 则返回整集台词。"
            "剧情断言（某集某时间码发生了什么）先用 subs 查。字幕只有台词，没有说话人和画面；"
            "无台词的戏查不到，查不到不等于没有这场戏。"
            "画面里有谁用 run_pipeline 的 vindex who 查。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词（source=subs 且给了 episode 时可省略）"},
                "limit": {"type": "integer", "description": "最多返回几条（默认 5，上限 20）"},
                "source": {"type": "string", "enum": ["notes", "subs"], "description": "检索笔记还是字幕，默认 notes"},
                "episode": {"type": "string", "description": "仅 subs：限定一集，如 S01E09"},
                "anime": {"type": "string", "description": "仅 subs：番名短名；省略则取本期 01-topic.md 的素材番"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "web_search": {
        "name": "web_search",
        "side_effect": False,
        "adr": "ADR-0021",
        "description": (
            "关键词联网检索（只读）。由配置的检索服务按序尝试（provider 字段写明这次是哪一家答的），"
            "返回标题/URL/摘要三元组清单；摘要只够辨认页面，要全文用 web_fetch。"
            "全部检索服务失败会逐家如实报错，严禁静默降级为水百科。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词"},
                "limit": {"type": "integer", "description": "最多返回几条（默认 20，上限 20）"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "web_fetch": {
        "name": "web_fetch",
        "side_effect": False,
        "adr": "ADR-0021",
        "description": (
            "抓取单个 URL 的网页净文（只读，升级链第一级静态获取）。"
            "限 http/https，拒连内网与二进制内容；失败如实报错并提示升级 crawl。"
            "返回含 `links`（本页可跟进链接清单，同站优先，去重，≤200 条）与"
            " `links_truncated`；清单被截断或静态被挡时升级 crawl。"
            "另含 `images`（图片直链：og:image 在前，<img> 取 srcset 最大档，≤80 条）与 `images_truncated`；"
            "补料要用的图交 acquire_propose（type scan）由人审，不许自己下载。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http/https URL"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    "write_note": {
        "name": "write_note",
        "adr": "ADR-0028",
        # 不标 side_effect：fail-closed 默认 True，每次写都弹人审卡（卡上带 diff）
        "description": (
            "写番剧笔记 data/library/notes/<番>.md（target=notes），或填已有笔记对抗审查报告的终审表"
            "（target=review，report 给报告文件名）。笔记是所有期共用的剧情事实源：按 workflow-history"
            "「番剧笔记」节写厚（每集独立小节、4–8 个场景、带行级时间码的逐字台词与说话人、多源交叉、"
            "分歧存档），在单独的会话里写，不在写稿时顺带改。改已有笔记用 edits 只传改动片段；"
            "采纳对抗审查的更正时同时在笔记「已知更正记录」节记一笔。不需要期目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "anime": {"type": "string", "description": "番名（与 sources.json / 笔记文件名一致）"},
                "target": {"type": "string", "enum": list(NOTE_TARGETS), "description": "notes=笔记本体；review=审查报告终审表"},
                "report": {"type": "string", "description": "target=review 时：报告文件名，如 罪恶王冠-对抗审查报告-2026-10-09.md"},
                "content": {"type": "string", "description": "完整内容（新建或整篇重写；与 edits 二选一）"},
                "edits": {
                    "type": "array",
                    "description": "局部替换（与 content 二选一）：按顺序把 old 换成 new；每个 old 必须恰好出现一次",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old": {"type": "string", "description": "要替换的原文片段（逐字）"},
                            "new": {"type": "string", "description": "替换后的文字"},
                        },
                        "required": ["old", "new"],
                        "additionalProperties": False,
                    },
                },
                "reason": {"type": "string", "description": "这次改什么、依据是什么（卡上给人看）"},
            },
            "required": ["anime", "target", "reason"],
            "additionalProperties": False,
        },
    },
    "acquire_propose": {
        "name": "acquire_propose",
        "adr": "ADR-0021",
        # 不标 side_effect：fail-closed 默认 True（cli.py:_default_approve）；ADR-0030 起只读与多数工序免卡，见 session.review_tool_call
        "description": (
            "把素材候选追加到 data/library/incoming/candidates.json（人审用提案清单，"
            "追加式、同 URL 自动跳过），**并立即抓取本次提交的候选**（ADR-0030 起不再逐条出卡，走 pipeline.acquire）。"
            "所以调用前先在对话里把候选（链接、补哪段、为什么）列给人，人同意再调。每条必须含 title/url/type/source/why；"
            "why 要写清补哪个缺口、凭什么认为是它（skill: acquire-assets §一）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "一批候选（一次调用整批登记并抓取）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string", "description": "http/https 直链或视频页"},
                            "type": {"type": "string", "enum": ["live", "mv", "scan", "interview"]},
                            "source": {"type": "string", "description": "站点名 + 大致检索路径"},
                            "why": {"type": "string", "description": "补哪个缺口 + 凭什么认为是它"},
                            "expected_dur": {"type": ["number", "null"], "description": "秒数，可空"},
                        },
                        "required": ["title", "url", "type", "source", "why"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["candidates"],
            "additionalProperties": False,
        },
    },
    "crawl": {
        "name": "crawl",
        "side_effect": False,
        "adr": "ADR-0021",
        "requires_extra": "crawl4ai",
        "description": (
            "无头渲染抓取单个 URL（只读，升级链第二级）。仅在 web_fetch 失败后使用，"
            "reason 必填说明下级为何不够；stealth 仅在普通无头被盾时显式开启。"
            "失败如实报错并提示升级 browser，严禁静默降级为水百科。"
            "返回含 `images`（渲染后页面的图片直链，口径同 web_fetch）；补料用图交 acquire_propose 由人审。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http/https URL"},
                "reason": {"type": "string", "description": "为什么 web_fetch 不够（其报错原文/遇到的盾）"},
                "stealth": {"type": "boolean", "description": "绕盾模式，默认 false"},
            },
            "required": ["url", "reason"],
            "additionalProperties": False,
        },
    },
    "browser": {
        "name": "browser",
        "adr": "ADR-0021",
        "requires_extra": "playwright",
        "description": (
            "登录态浏览器（升级链第三级；ADR-0030 起不弹卡，每次启动落审批事件）。"
            "action 仅支持 navigate / extract_text；profile 独立持久化、路径由配置钉死。"
            "仅在 crawl 也不够或必须登录态时使用，reason 必填。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["navigate", "extract_text"]},
                "url": {"type": "string", "description": "navigate 的目标 URL"},
                "reason": {"type": "string", "description": "为什么 crawl 不够/为何需要登录态"},
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        },
    },
    "write_memory": {
        "name": "write_memory",
        "side_effect": True,
        "adr": "ADR-0023",
        "description": (
            "跨期记忆 data/library/memory.md 的唯一受控写入口。条目 = 模式 + 证据期 + 适用边界，"
            "写经验不写规则、不写流水账。op=add/revise/merge/retire 不弹卡（ADR-0030），写前先把全文给人看、人同意再写；"
            "op=cite 把当期记为某条的证据（免卡）。禁用「可能」类措辞、规则强度词与审批行为词。"
            "全文 4000 字符预算，超限被拒时按返回的腾位候选序先 merge 或 retire 首位。"
            "卷会话 / 企划会话里 target=project 写本企划的企划记忆（<企划>/00-memory.md，3000 字符，"
            "证据只认本企划的卷）：只属于这个系列的经验进企划记忆，跨系列通用的进全局记忆。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "revise", "merge", "cite", "retire"]},
                "target": {"type": "string", "enum": ["global", "project"],
                           "description": "写哪份记忆：global（默认，跨期记忆）/ project（本企划记忆，只在卷 / 企划会话有效）"},
                "ids": {"type": "array", "items": {"type": "string"}},
                "pattern": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "boundary": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["op"],
            "additionalProperties": False,   # 仅作模型提示；宿主层拒收由 plan_op 执行
        },
    },
    "cover_edit": {
        "name": "cover_edit",
        "adr": "ADR-0025",
        # 不标 side_effect：fail-closed 默认 True（cli.py:_default_approve）——
        # 每次渲染弹一张人审卡（2026-09-26 用户裁决：多轮迭代就多张卡，每张一次点击）
        "description": (
            "把标题文字确定性叠到 07-cover/ 下的一张源图上，产出候选 07-cover/edit-*.png。"
            "**只产候选、定稿权在人**：本工具不排名、不推荐，也不存在任何「定稿/选最终版」动作，"
            "唯一写入点是人自己的 09 ack。不同缩放/裁剪/滤镜/多图合成；同参数同字节，"
            "output 目标已存在则拒收（换名迭代 edit-1、edit-2…）。不弹卡：写什么字先和人商量定，字定了直接出图——不先贴参数表问「如果认可我来渲染」，人说了放哪、多大就直接渲染。"
            "渲染后先用 view_image 看一眼（挡没挡脸、字在手机上看不看得清），再把结果交给人。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "07-cover/（可含 import/）下的 .png/.jpg 相对路径，必须存在（只读源）",
                },
                "output": {
                    "type": "string",
                    "description": "如 07-cover/edit-1.png（^07-cover/edit-[a-z0-9][a-z0-9-]{0,38}\\.png$，目标必须不存在）",
                },
                "lines": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "description": "1–6 行文字，按数组顺序依次绘制（后画的盖先画的）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "1–40 字符"},
                            "size": {"type": "integer", "description": "字号 8–400"},
                            "anchor": {
                                "type": "string",
                                "enum": ["tl", "tc", "tr", "cl", "cc", "cr", "bl", "bc", "br"],
                                "description": "九宫锚点（tl=左上，cc=正中心，br=右下）",
                            },
                            "dx": {"type": "integer", "description": "锚点基础上的水平像素偏移，-4000–4000，默认 0"},
                            "dy": {"type": "integer", "description": "锚点基础上的垂直像素偏移（向下为正），-4000–4000，默认 0"},
                            "color": {"type": "string", "description": "文字色 ^#[0-9a-fA-F]{6}$，默认 #FFFFFF"},
                            "stroke_width": {"type": "integer", "description": "描边宽 0–40，默认 0"},
                            "stroke_color": {"type": "string", "description": "描边色 ^#[0-9a-fA-F]{6}$，默认 #000000"},
                        },
                        "required": ["text", "size", "anchor"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["source", "output", "lines"],
            "additionalProperties": False,
        },
    },
    "bash": {
        "name": "bash",
        "adr": "ADR-0029",
        # 不标 side_effect：fail-closed 默认 True——每次执行弹命令审批卡（ADR-0029 决策 4）
        "description": (
            "在本机执行一段 bash 命令（/bin/bash -c）。工作目录是当期目录（无期时为仓库根）；"
            "仓库根在环境变量 `$AVA_ROOT`（读 skills/、docs/ 写 `\"$AVA_ROOT/skills/…\"`，不要写相对路径）；"
            "`python` 就是项目解释器（已带 pipeline 依赖）。查写法先读 skills/ 与 docs/runbook/，别靠猜函数名翻源码。"
            "stdin 为空，stdout / stderr 各回传尾部 16 KB，输出里的已知密钥值会被替换为 [已脱敏]。"
            "用于白名单之外的文件与媒体操作：把期目录外的文件拷进来、改名、ffmpeg / sips 裁图转码、查看文件。"
            "**多数命令直接执行、不弹卡**，只在删除、写 pipeline/ 或 config/、git 改历史、碰停机点解封物这类硬危险时弹卡；"
            "有副作用的先在对话里问人。不要叫人去终端敲命令。"
            "流水线工序（tts / clips / render / check_script…）仍走 run_pipeline。"
            "遵守做片期代码只读（production.md）：不改 pipeline/ 源码与 config/，"
            "不写停机点解封物（02-diff.patch、04-clips.approved.json）；顶层定稿封面只在人点名用哪一版之后才转——批准只能由人做。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 bash 命令，可多行；相对路径相对当期目录",
                },
                "description": {
                    "type": "string",
                    "description": "一句话说明这条命令要做什么（回显给人看；弹卡时印在卡上）",
                },
            },
            "required": ["command", "description"],
            "additionalProperties": False,
        },
    },
    "view_image": {
        "name": "view_image",
        "side_effect": False,
        "adr": "ADR-0031",
        "description": (
            "看期目录里的一张图（.png/.jpg/.jpeg/.webp，只读、免卡）：封面候选、cover_edit 的渲染结果、"
            "裁出的竖版底图、联系表、用 bash 抽出的视频帧都行。结果只回尺寸，图本身附在这批工具结果之后的"
            "宿主消息里，**只在本回合附**；下一回合要看现状请重新调用。硬排除 03-audio/ 与 04-patch/。"
            "视觉类工作（叠字、裁图、挑帧）交给人之前先自己看；没看过的图不要描述效果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "期目录相对路径，如 07-cover/edit-6.png、07-cover/import/cover-1-vertical.png",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "fact_add": {
        "name": "fact_add",
        "side_effect": True,
        # ADR-0032 软约束：出处是 D99 施工图，「为什么不复用 run_pipeline / write_episode_file / bash」写在其 PR-2 第 1 条
        "adr": "plans/2026-10-15-fact-gate-spec.md",
        "description": (
            "往本卷 / 本期（企划会话里是企划）的事实库 facts.jsonl 写事实条目，一次可多条，逐条回执。"
            "**写事实只用这个工具**，不要用 bash 或 write_episode_file 写事实文件；facts.md 是生成的阅读版。"
            "每条给：claim（中文说法）、url（出处页）、excerpts（从这一页原文**逐字复制**的句子，不改写、不拼接、不加说话人冒号）、"
            "proposed_level（一手 / 二手 / 未核实，只是提议）、topic（节名）。"
            "系统按 url 找本机抓取留档（web_fetch / crawl / browser 打开过的页，跨会话也算）逐条比对摘录："
            "逐字命中 → 落「待核」，一手 / 二手由人定；只在去掉标点后才对上 → 落「待核」并要你按原文重抄；"
            "对不上或这页没抓过 → 只能落「未核实」。回执里会给原文最接近的片段，照它改。"
            "向人汇报时如实说各条的级别，不要把「待核」说成已核实。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string", "description": "这条事实的中文说法"},
                            "url": {"type": "string", "description": "出处页 URL（必须是你打开过的那一页）；没有出处给空串"},
                            "excerpts": {"type": "array", "items": {"type": "string"}, "description": "原文逐字摘录，一句一项"},
                            "proposed_level": {"type": "string", "enum": ["一手", "二手", "未核实"]},
                            "topic": {"type": "string", "description": "节名，如「成员编制」"},
                        },
                        "required": ["claim", "url", "excerpts", "proposed_level"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
}


def _extra_available(dist: str) -> bool:
    """可选 extras 探测：find_spec 只定位不执行（防顶层 import 重依赖污染热路径）。"""
    importlib.invalidate_caches()
    return importlib.util.find_spec(dist) is not None


def tool_names(root: Path | None = None) -> list[str]:
    """读 config/agent/tools.json 的单表（D43 / Spec 17 §3.1：不再按 scope 分表）。

    缺失 / 损坏 / 残留旧四键格式 = 空表；读取失败也不静默扩张，一样是空表——
    空表随即被 `build_tool_schemas` 的反向分叉检查挡下（fail-closed）。
    """
    from pipeline.agent.scopes import get_tools_json_path

    tools_file = get_tools_json_path(root)
    if not tools_file.exists():
        return []
    try:
        data = json.loads(tools_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    names = data.get("tools", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return []
    return list(names)


def build_tool_schemas(root: Path | None = None) -> list[dict[str, Any]]:
    """把 tools.json 单表翻译成 OpenAI tools 参数（D43 / Spec 17 §3.1：不再收 scope）。

    tools.json 里出现未注册的名字 = 配置与实现分叉，当场报错而不是静默跳过
    （静默跳过会让护栏看起来还在，实际已经漏了）。反向同样当场报错：已注册但
    tools.json 没列 = 分叉（全开之后两边应恰好相等）。正向与反向同点同形态。
    """
    names = tool_names(root)
    for name in names:
        if name not in TOOL_SCHEMAS:
            raise KeyError(
                f"tools.json 声明了未注册的工具 '{name}'；工具清单不现场发明（Spec §2.5 B3-r6），"
                f"已注册: {sorted(TOOL_SCHEMAS)}"
            )
    missing = sorted(set(TOOL_SCHEMAS) - set(names))
    if missing:
        raise KeyError(
            f"tools.json 未列出已注册的工具 {missing}；单表须恰好等于注册集（D43 反向分叉检查）"
        )
    schemas: list[dict[str, Any]] = []
    for name in names:
        req_extra = TOOL_SCHEMAS[name].get("requires_extra")
        if req_extra and not _extra_available(str(req_extra)):
            continue
        _PROTOCOL_KEYS = ("name", "description", "parameters")
        fn_schema = {k: v for k, v in TOOL_SCHEMAS[name].items() if k in _PROTOCOL_KEYS}
        schemas.append({"type": "function", "function": fn_schema})
    return schemas


def _allowed_read_roots(ctx: ToolContext) -> list[Path]:
    """读域的根（全部 resolve）。D81 §4.3：企划会话 = 企划目录 + 各成员的 resolve()（成员可以是软链卷）+ library；
    卷会话 = 卷目录 + library（企划顶层文件另由 `_project_top_ok` 单独放行）；普通期不变。"""
    roots: list[Path] = []
    if ctx.session_kind == "project" and ctx.project_dir is not None:
        from pipeline.project import project_members

        roots.append(Path(ctx.project_dir).resolve())
        roots.extend(m.resolve() for m in project_members(Path(ctx.project_dir)))
    elif ctx.episode_dir:
        roots.append(Path(ctx.episode_dir).resolve())
    library = (ctx.base / "data" / "library").resolve()
    if library.exists():
        roots.append(library)
    return roots


def _project_top_ok(rel: Path, target: Path, ctx: ToolContext) -> bool:
    """卷会话读企划顶层：`rel` 只有一段，且 resolve 后的 target 的父目录就是企划目录（软链指出去不算）。"""
    if ctx.session_kind != "volume" or ctx.project_dir is None or len(rel.parts) != 1:
        return False
    return target.parent == Path(ctx.project_dir).resolve()


def deny_dir_hit(path: Path) -> str | None:
    """路径是否落在读域硬排除目录里（大小写不敏感），命中返回目录名。

    APFS 默认大小写不敏感：`03-AUDIO/manifest.json` 的 is_file() 照样命中真文件，
    精确比较的部件匹配却匹配不上——不 casefold 就是一条真能读到内容的绕过路径
    （终审二轮 P0）。判定放在 resolve 后的绝对路径上，软链接穿透同样拦下。
    """
    folded = {part.casefold() for part in path.parts}
    for name in READ_DENY_PARTS:
        if name.casefold() in folded:
            return name
    return None


# D81 §4.3 第 4 条：企划记忆按 basename 判（经软链读到的 target basename 仍是它）；日志带 result_text（全文），同样不可读
_MEMORY_BASENAMES = frozenset({"00-memory.md", "00-memory.log.jsonl"})


def _is_memory_file(target: Path, ctx: ToolContext) -> bool:
    """是否命中记忆正文或审计日志（resolve + casefold，Spec 7 §4.3②；D81 扩到企划记忆与日志）。

    记忆只经装配器校验后注入；读工具留口子，校验层就有了旁路。日志的状态行带 result_text（记忆全文），
    读它等于绕过来源确认读记忆。
    """
    from pipeline.agent.memory import LOG_REL_PATH, MEMORY_REL_PATH   # 函数内延迟 import（叶子性）

    resolved = Path(target).resolve()
    folded = str(resolved).casefold()
    if resolved.name.casefold() in _MEMORY_BASENAMES or Path(target).name.casefold() in _MEMORY_BASENAMES:
        return True
    return folded in {
        str((ctx.base / MEMORY_REL_PATH).resolve()).casefold(),
        str((ctx.base / LOG_REL_PATH).resolve()).casefold(),
    }


def _tool_read_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """读域写死：期目录内 + data/library/ 只读，硬排除 03-audio/ 与 04-patch/（§2.5）。

    D81 §4.3：`area` 选区；卷会话另可读企划顶层的单个文件（如 00-project.md）、不能借读兄弟卷；
    企划会话可读整个企划（含软链卷）。后缀、硬排除、记忆文件三条都对 `rel` 与 resolve 后的 target 各判一次。
    """
    raw = str(args.get("path", "")).strip()
    if not raw:
        raise ValueError("path 不能为空")
    area = args.get("area")
    if area is not None and area not in ("episode", "project", "library"):
        raise ValueError(f"area 只能是 episode / project / library，收到 {area!r}")
    if area == "project" and ctx.session_kind not in ("volume", "project"):
        raise PermissionError("area=project 只在卷会话与企划会话里可用（当前会话不属于任何企划）")
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise PermissionError(f"越界读被拒（只读期目录内与 data/library/）: {raw}")
    if rel.suffix.casefold() not in READ_ALLOWED_SUFFIXES:
        raise PermissionError(
            f"只读文本类文件 {sorted(READ_ALLOWED_SUFFIXES)}，拒读: {raw}"
        )
    hit = deny_dir_hit(rel)
    if hit:
        raise PermissionError(
            f"读域硬排除 {hit}/：属「一律不出网」清单（Spec §2.5 Y2-r19），拒读: {raw}"
        )

    library = ctx.base / "data" / "library"
    candidates: list[Path] = []
    if area in (None, "episode") and ctx.episode_dir and ctx.session_kind != "project":
        candidates.append(Path(ctx.episode_dir) / rel)
    if area in (None, "project") and ctx.session_kind == "project" and ctx.project_dir is not None:
        candidates.append(Path(ctx.project_dir) / rel)
    if area in (None, "library"):
        candidates.append(library / rel)
        candidates.append(library / "notes" / rel)
    if area in (None, "project") and ctx.session_kind == "volume" and ctx.project_dir is not None and len(rel.parts) == 1:
        candidates.append(Path(ctx.project_dir) / rel)   # 企划顶层候选排在最后（同名时当卷文件优先）

    roots = _allowed_read_roots(ctx)
    for cand in candidates:
        target = cand.resolve()
        if target.suffix.casefold() not in READ_ALLOWED_SUFFIXES:
            # 红队 R-15：软链 `x.json -> 00-memory.log.jsonl` 以 .json 的名义读 .jsonl——后缀对 resolve 后再判一次
            raise PermissionError(f"只读文本类文件 {sorted(READ_ALLOWED_SUFFIXES)}，拒读: {raw}")
        # 判 resolve 后的绝对路径：软链接绕进 03-audio 同样拦下
        hit = deny_dir_hit(target)
        if hit:
            raise PermissionError(
                f"读域硬排除 {hit}/：属「一律不出网」清单（Spec §2.5 Y2-r19），拒读: {raw}"
            )
        if _is_memory_file(target, ctx) or _is_memory_file(cand, ctx):
            raise PermissionError(
                "memory.md 只经装配器校验后注入，模型不可直读；人请用 /memory"
            )
        if not (any(target == r or r in target.parents for r in roots) or _project_top_ok(rel, target, ctx)):
            continue
        if target.is_file():
            size = target.stat().st_size
            with target.open("rb") as fh:
                blob = fh.read(MAX_READ_BYTES)
            return {
                "path": str(target),
                "text": blob.decode("utf-8", errors="replace"),
                "truncated": size > MAX_READ_BYTES,
            }
    raise FileNotFoundError(f"文件不存在或不在读域内: {raw}（只读期目录内与 data/library/）")


def resolve_write_content(ep_dir: Path | str | None, args: dict[str, Any]) -> str:
    """D48：write_episode_file 写入后的全文。`content` 原样返回；`edits` 在磁盘现版上逐条唯一替换。

    三处共用（落盘、写稿卡 diff、弹卡前校验），保证人在卡上看到的就是将要落盘的。
    任何一条 old 不唯一或找不到就 ValueError，一字不写——宁可让模型重给，也不猜它想换哪一处。
    """
    content, edits = args.get("content"), args.get("edits")
    if (content is None) == (edits is None):
        raise ValueError("content 与 edits 必须且只能给一个")
    if content is not None:
        if not isinstance(content, str):
            raise ValueError("content 必须是字符串")
        return content
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits 必须是非空数组")
    if ep_dir is None:
        raise PermissionError(NO_EPISODE_MESSAGE)
    target = Path(ep_dir) / Path(str(args.get("filename", ""))).name
    return _apply_edits(target, edits)


def _apply_edits(target: Path, edits: list) -> str:
    """D48 的局部替换语义（写稿与写笔记共用）：每条 old 在当时的文本里恰好出现一次，按顺序替换。"""
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"{target.name} 不存在，edits 只能改已有文件；新建请用 content") from None
    for i, e in enumerate(edits, 1):
        if not isinstance(e, dict) or not isinstance(e.get("old"), str) or not isinstance(e.get("new"), str):
            raise ValueError(f"edits 第 {i} 条必须是 {{old, new}} 两个字符串")
        n = text.count(e["old"]) if e["old"] else 0
        if n != 1:
            why = "old 为空" if not e["old"] else ("找不到" if n == 0 else f"出现了 {n} 次，带更多上下文使它唯一")
            raise ValueError(f"edits 第 {i} 条 {why}（按顺序替换，前面各条已生效后再找）")
        text = text.replace(e["old"], e["new"], 1)
    return text


# ---------- D61 / ADR-0028：write_note（番剧笔记与笔记对抗审查报告的终审表） ----------

def notes_dir(root: Path | None = None) -> Path:
    return Path(root or paths.ROOT) / "data" / "library" / "notes"


def _registered_animes(root: Path | None = None) -> set[str]:
    """已登记的番：sources.json 与 config/characters.json 的顶层键（下划线开头的是注释）。"""
    base = Path(root or paths.ROOT)
    names: set[str] = set()
    for f in (base / "data" / "library" / "sources.json", base / "config" / "characters.json"):
        try:
            names |= {k for k in json.loads(f.read_text(encoding="utf-8")) if not k.startswith("_")}
        except (OSError, ValueError, AttributeError):
            continue
    return names


def note_target(args: dict[str, Any], root: Path | None = None) -> Path:
    """write_note 的落点（校验番名、目标类型与报告文件名；不判断写不写得）。"""
    anime = str(args.get("anime", "")).strip()
    if not _ANIME_NAME.match(anime) or ".." in anime:
        raise PermissionError(f"番名「{anime}」不合用：不能空、不能含 / \\ 或控制字符、不能以 . 或 _ 开头")
    target = str(args.get("target", "notes")).strip()
    if target not in NOTE_TARGETS:
        raise ValueError(f"target 只能是 {NOTE_TARGETS}，收到 {target!r}")
    base = notes_dir(root)
    if not base.is_dir():
        raise PermissionError(f"笔记目录不可达（外置盘未挂载？）：{base}")
    if target == "notes":
        name = f"{anime}.md"
    else:
        name = str(args.get("report", "")).strip()
        if not (name.startswith(anime + REVIEW_REPORT_MARK) and name.endswith(".md")) or "/" in name or "\\" in name:
            raise PermissionError(f"report 必须是本番的对抗审查报告文件名（{anime}{REVIEW_REPORT_MARK}….md），收到 {name!r}")
    dest = (base / name).resolve()
    if dest.parent != base.resolve():
        raise PermissionError(f"目标越界：{dest} 不在 {base} 下")
    return dest


def note_write_refusal(args: dict[str, Any], root: Path | None = None) -> str | None:
    """弹卡前与落盘前各查一次；None = 放行。"""
    try:
        dest = note_target(args, root)
    except (PermissionError, ValueError) as exc:
        return str(exc)
    if dest.exists():
        return None
    if str(args.get("target", "notes")) == "review":
        return f"{dest.name} 不存在：审查报告由 notes_review 生成，write_note 只填它的终审表"
    anime = str(args.get("anime", "")).strip()
    if anime not in _registered_animes(root):
        return (f"《{anime}》还没有笔记，也不在 sources.json / config/characters.json 里："
                "新建笔记前先确认番名写法（防手误造出孤立文件）")
    return None


def resolve_note_content(args: dict[str, Any], root: Path | None = None) -> str:
    """write_note 写入后的全文（落盘、卡面 diff、弹卡前校验三处共用）。"""
    content, edits = args.get("content"), args.get("edits")
    if (content is None) == (edits is None):
        raise ValueError("content 与 edits 必须且只能给一个")
    if content is not None:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content 必须是非空字符串")
        return content
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits 必须是非空数组")
    return _apply_edits(note_target(args, root), edits)


def _tool_write_note(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    root = ctx.base
    refusal = note_write_refusal(args, root)
    if refusal:
        raise PermissionError(refusal)
    dest = note_target(args, root)
    content = resolve_note_content(args, root)
    if dest.exists():
        # 覆盖前全量留底（不修剪）：笔记是所有期共用的事实源，改错了要能逐版追回
        hist = dest.parent / NOTES_HISTORY_DIR / str(args.get("anime")).strip()
        hist.mkdir(parents=True, exist_ok=True)
        ns = time.time_ns()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(ns // 1_000_000_000)) + f"{ns % 1_000_000_000:09d}"
        paths.atomic_write(hist / f"{stamp}-{dest.name}", dest.read_text(encoding="utf-8"))
    paths.atomic_write(dest, content)
    return {"written": str(dest), "bytes": len(content.encode("utf-8"))}


@dataclass(frozen=True)
class ProjectWritePlan:
    """写 `00-project.md` 的一次规划（审查层与执行层各算一次，D81 §4.4）。"""

    project_dir: Path          # resolve 后的企划目录
    target: Path               # resolve 后的 00-project.md
    before: str | None
    after: str
    base_sha: str              # 盘上 before 的 sha256（不存在为 "absent"）
    machine_diff: list[str]    # 机读字段逐行 diff；空 = 只改散文


def project_file_sha(target: Path) -> str:
    import hashlib

    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"


def plan_project_write(project_dir: Path | str, args: dict[str, Any], root: Path | None = None) -> ProjectWritePlan:
    """解析 → 机读 diff。`after` 解析不了在弹卡前拒（ValueError）；`before` 不存在或不可解析都算机读字段有变（R-16）。"""
    from pipeline import project as project_mod

    filename = str(args.get("filename", "")).strip()
    if filename != PROJECT_FILE:
        raise PermissionError(f"企划会话里 write_episode_file 只认 {PROJECT_FILE}，收到 {filename!r}（卷级文件到卷会话里写）")
    pdir = resolve_project_dir(project_dir, root=root)
    target = (Path(project_dir) / PROJECT_FILE).resolve()
    if target.parent != pdir:
        raise PermissionError(f"{PROJECT_FILE} 指向企划目录之外（软链？），拒绝写入: {target}")
    after = resolve_write_content(project_dir, args)
    try:
        after_doc = project_mod.parse(after)
    except project_mod.ParseError as exc:
        raise ValueError(f"{PROJECT_FILE} 写后不可解析：{exc}") from None
    try:
        before = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        before = None
    before_doc = None
    if before is not None:
        try:
            before_doc = project_mod.parse(before)
        except project_mod.ParseError:
            before_doc = None
    return ProjectWritePlan(
        project_dir=pdir, target=target, before=before, after=after,
        base_sha=project_file_sha(target),
        machine_diff=project_mod.diff_machine(before_doc, after_doc),
    )


def project_write_preview(plan: ProjectWritePlan) -> list[str]:
    """卡面：机读字段逐行 diff + 全文 diff（D81 §4.4 第 2 条）。"""
    import difflib

    lines = ["[企划立项文件] 机读字段（企划名 / 合集 / 卷表）有变化：", *plan.machine_diff, "", "全文 diff："]
    lines.extend(difflib.unified_diff(
        (plan.before or "").split("\n"), plan.after.split("\n"),
        fromfile=f"{PROJECT_FILE}（现版）" if plan.before is not None else "（新建）",
        tofile=f"{PROJECT_FILE}（写后）", lineterm="",
    ))
    return lines


def _write_project_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """企划会话写 `00-project.md`：执行层重算机读 diff；有变则要人在卡上批 + 卡面所依据的 base_sha 仍是盘上的。

    确认只从宿主来：只看 `ctx.human_approved` 与 `ctx.bound`，不读 args 里任何同名键（§4.4 第 5 条）。
    """
    plan = plan_project_write(ctx.project_dir or ctx.episode_dir, args, root=ctx.root)  # type: ignore[arg-type]
    if plan.machine_diff:
        if not ctx.human_approved:
            raise PermissionError(f"{PROJECT_FILE} 的机读字段（企划名 / 合集 / 卷表）有变，必须由人在卡上批准")
        if not isinstance(ctx.bound, dict) or ctx.bound.get("base_sha") != plan.base_sha:
            raise PermissionError(f"{PROJECT_FILE} 在你看卡之后变了，重新提交")
    if plan.before is not None:
        _keep_history(plan.project_dir, plan.target, FILE_HISTORY_DIR, None)
    paths.atomic_write(plan.target, plan.after)
    return {"written": str(plan.target), "bytes": len(plan.after.encode("utf-8")),
            "machine_fields_changed": bool(plan.machine_diff)}


def _tool_write_episode_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        raise PermissionError(NO_EPISODE_MESSAGE)
    if ctx.session_kind == "project":
        return _write_project_file(args, ctx)
    content = resolve_write_content(ctx.episode_dir, args)
    filename = str(args.get("filename", "")).strip()
    confirmed = bool(args.get("confirmed", False)) or ctx.confirmed
    target = write_episode_file(
        ctx.episode_dir, filename, content, confirmed=confirmed, root=ctx.root
    )
    return {"written": str(target), "bytes": len(content.encode("utf-8"))}


def _tool_list_episodes(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.cli import get_episodes_list
    from pipeline.status import inspect_episode

    from pipeline.project import episodes_root, get_episode_tree

    ep_root = episodes_root(ctx.root)
    episodes, hidden = get_episodes_list(root=ctx.root)
    tree = get_episode_tree(ctx.root)

    def key(p: Path) -> str:
        # D81 §6.3 ⑮：name 是相对 data/episodes/ 的键（V1 / V2 同名卷给出两个不同的 name）
        return p.relative_to(ep_root).as_posix()

    episodes_detail = []
    for ep in episodes:
        status = inspect_episode(ep)
        parts = key(ep).split("/")
        episodes_detail.append({
            "name": key(ep),
            "project": parts[0] if len(parts) == 2 else None,
            "current_step": status.current_step,
            "is_blocked": status.is_blocked,
        })
    return {
        "episodes": [key(ep) for ep in episodes],
        "episodes_detail": episodes_detail,
        "projects": [
            {"name": key(proj.dir), "has_project_file": proj.has_project_file,
             "members": [key(m) for m in proj.members]}
            for proj in tree.projects
        ],
        "hidden_underscore": hidden,
    }


def _tool_read_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.status import format_status, inspect_episode

    target = str(args.get("episode", "")).strip()
    if target:
        from pipeline.agent.cli import explain_episode_target

        # D81：卷 / 企划会话里裸卷名先在本企划内解析；歧义时返回候选，不猜
        ep_dir, why = explain_episode_target(target, project=ctx.project_dir)
        if ep_dir is None:
            raise FileNotFoundError(why)
    elif ctx.session_kind == "project":
        raise ValueError("企划会话没有「当期」：给 episode=<卷名> 看某一卷的进度")
    elif ctx.episode_dir:
        ep_dir = Path(ctx.episode_dir).resolve()
    else:
        raise ValueError("未指定期，且当前会话未绑定期目录")

    status = inspect_episode(ep_dir)
    return {
        "episode": status.episode_name,
        "current_step": status.current_step,
        "is_blocked": status.is_blocked,
        "next_command": status.next_command,
        "advisories": status.advisories,
        "card": format_status(status),
    }


def crosscheck_writes(argv: list[str]) -> bool:
    """规范化 argv 的 `crosscheck` 是否带 `--write`（含前缀缩写与 `=` 写法，按写判；argparse 本身不认缩写）。"""
    at = argv.index("-m") + 2 if "-m" in argv else len(argv)
    for tok in argv[at:]:
        flag = tok.split("=", 1)[0]
        if len(flag) > 2 and flag.startswith("--") and "--write".startswith(flag):
            return True
    return False


def acquire_targets_volume(argv: list[str]) -> bool:
    """规范化 argv 的 `acquire` 是否带 `--to-patch`（挪进某期补丁池）。acquire 的 argparse 认前缀缩写，`--t` 已唯一
    指向它，所以前缀与 `=` 写法都算（register 的其余选项 --pool / --as / --waive 都不以 --t 开头）。"""
    at = argv.index("-m") + 2 if "-m" in argv else len(argv)
    for tok in argv[at:]:
        flag = tok.split("=", 1)[0]
        if len(flag) > 2 and flag.startswith("--") and "--to-patch".startswith(flag):
            return True
    return False


def project_module_refusal(argv: list[str], session_kind: str, project_dir: Path | None) -> str | None:
    """D81 §4.2（人裁决 Q2）：企划会话的 run_pipeline 只放行企划级模块 + subgrep / tickets；
    企划级专属模块（assemble）只在企划会话里放行；卷会话只放行只读的 crosscheck（带 --write 拒）。
    审查层与实现层共用；None = 放行。"""
    if "-m" not in argv:
        return None
    at = argv.index("-m") + 1
    module = argv[at].removeprefix("pipeline.") if at < len(argv) else ""
    if session_kind == "volume" and module == "crosscheck" and crosscheck_writes(argv):
        return "卷会话只跑只读的 crosscheck（不带 --write）；要把报告写进企划目录，到企划会话里跑"
    if session_kind == "project":
        if module in PROJECT_PIPELINE_MODULES or module in PROJECT_SESSION_EXTRA:
            return None
        if module in PROJECT_LIBRARY_MODULES and not acquire_targets_volume(argv):
            return None
        if module in PROJECT_LIBRARY_MODULES:
            from pipeline.project import project_members

            vols = "、".join(m.name for m in project_members(project_dir)) if project_dir is not None else ""
            return ("挪进某卷补丁池（register --to-patch）是卷级操作，到对应卷的会话里做"
                    f"{f'（本企划的卷：{vols}）' if vols else ''}；抓取、gate、登记进素材池（--pool）在企划会话里照做")
        if module in ("status", "check_script"):
            return (f"企划会话不跑 {module}：它要一个期目录。看某一卷的进度用 read_status(episode=<卷名>)，"
                    "跑卷级检查到卷的会话里做")
        from pipeline.project import project_members

        vols = "、".join(m.name for m in project_members(project_dir)) if project_dir is not None else ""
        return (f"这是卷级工序（pipeline.{module}），企划会话只做规划、跨卷审查与总装；"
                f"到对应卷的会话里跑{f'（本企划的卷：{vols}）' if vols else ''}")
    if module in PROJECT_ONLY_MODULES:
        return f"pipeline.{module} 是企划级工序，只在企划会话里跑（侧栏点企划本身开企划会话）"
    return None


def _tool_run_pipeline(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        # 实现层双保险（D43 / Spec 17 §3.4）：裸循环等绕过 review 层的路径也拿到同一文案。
        raise PermissionError(NO_EPISODE_MESSAGE)
    dry = run_pipeline(str(args.get("command", "")), episode_dir=ctx.episode_dir, scope=ctx.scope, confirmed=False,
                       project_dir=ctx.project_dir)
    refusal = project_module_refusal(list(dry.get("argv") or []), ctx.session_kind, ctx.project_dir)
    if refusal:
        raise PermissionError(refusal)
    outcome = run_pipeline(
        str(args.get("command", "")),
        episode_dir=ctx.episode_dir,
        scope=ctx.scope,
        confirmed=ctx.confirmed,
        project_dir=ctx.project_dir,
    )
    if outcome["ok"] and outcome["returncode"] is None and not ctx.confirmed:
        # 只校验未执行：显式报「需人类确认」，不假装跑过了（静默 fallback 是家规禁项）。
        return {
            "ok": False,
            "error": "需人类确认：本工具只校验并回显 argv，执行请由人在宿主 REPL 用 /run 确认",
            "argv": outcome["argv"],
        }
    # D83：与 bash 对齐，模块输出出网前过密钥脱敏（_tool_message 只过 scrub_restricted，
    # 模块若把密钥值打进 stdout / stderr，原样进会话日志并发给模型服务商）。
    for key in ("stdout_tail", "stderr_tail", "message", "error"):
        if isinstance(outcome.get(key), str):
            outcome[key] = scrub_secrets(outcome[key], ctx.root)
    return outcome


def _tool_search_notes(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    source = str(args.get("source", "notes") or "notes")
    if source == "subs":
        return _search_subs(args, ctx)
    if source != "notes":
        raise ValueError(f"source 只能是 notes 或 subs，收到 {source!r}")
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query 不能为空")
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        raise ValueError("limit 必须是整数") from None
    limit = max(1, min(limit, MAX_NOTE_LIMIT))

    library = (ctx.base / "data" / "library")
    if not library.exists():
        return {"query": query, "hits": [], "note": "data/library/ 不可达（外置盘未挂载？）"}

    needle = query.lower()
    hits: list[dict[str, str]] = []
    excluded: list[str] = []
    for path in sorted(library.rglob("*")):
        if len(hits) >= limit:
            break
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in NOTE_SUFFIXES:
            continue
        if _is_memory_file(path, ctx):
            excluded.append(path.name)      # 跳过要显式说明，不静默
            continue
        if path.stat().st_size > MAX_NOTE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        idx = text.lower().find(needle)
        if idx < 0:
            continue
        snippet = text[max(0, idx - 80): idx + len(query) + 160].replace("\n", " ").strip()
        hits.append({"path": str(path.relative_to(library)), "snippet": snippet})

    result: dict[str, Any] = {"query": query, "hits": hits, "truncated": len(hits) >= limit}
    if excluded:
        result["excluded"] = sorted(set(excluded))
    return result


SUBS_TRANSCRIPT_MAX_CHARS = 20_000  # 整集台词上限；一集约 6–8k 字，余量两倍多
_EP_CODE = re.compile(r"^S\d+E\d+$")


def subtitle_lines(units: list[dict[str, Any]], source: Path) -> list[tuple[float, str]]:
    """把 WINDOW=2 滑窗单元还原成单句（`subindex.py` 的拼接：unit_i = line_i + " " + line_{i+1}，
    末单元只有一句）。还原算法在 `pipeline/subcues.py`（D86：试看字幕同用一份）；结构对不上就报错。"""
    from pipeline.subcues import lines

    return [(float(u["start"]), line) for u, line in zip(units, lines(units, source))]


def _mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _search_subs(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """字幕检索（2026-10-08 spec §C）。只读 data/library/index/<番>_SxxEyy.json，纯 stdlib。"""
    query = str(args.get("query", "") or "").strip()
    episode = str(args.get("episode", "") or "").strip().upper()
    if episode and not _EP_CODE.match(episode):
        raise ValueError(f"episode 格式应为 S01E09 这种，收到 {episode!r}")
    if not query and not episode:
        raise ValueError("source=subs 时 query 与 episode 至少给一个")
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        raise ValueError("limit 必须是整数") from None
    limit = max(1, min(limit, MAX_NOTE_LIMIT))

    anime_arg = str(args.get("anime", "") or "").strip()
    if anime_arg:
        animes = [anime_arg]
    elif ctx.episode_dir is not None:
        from pipeline.bgm import animes_of

        animes = animes_of(Path(ctx.episode_dir))
        if not animes:
            raise ValueError("本期 01-topic.md 没写「番:」，读不到番名；请显式传 anime")
    else:
        raise ValueError("无期会话检索字幕必须显式传 anime")

    index_dir = ctx.base / "data" / "library" / "index"
    if not index_dir.exists():
        return {"source": "subs", "query": query, "hits": [], "note": "data/library/index/ 不可达（外置盘未挂载？）"}

    files: list[tuple[str, Path]] = []
    for anime in animes:
        # 文件名整串匹配：番名前缀相同的另一部番（跨番命中是静默失败）不得混入
        pat = re.compile(rf"^{re.escape(anime)}_(S\d+E\d+)\.json$")
        for path in sorted(index_dir.iterdir()):
            m = pat.match(path.name)
            if m and (not episode or m.group(1) == episode):
                files.append((m.group(1), path))
    result: dict[str, Any] = {"source": "subs", "animes": animes, "query": query}
    if not files:
        result.update(hits=[], note=f"index/ 下没有《{'、'.join(animes)}》{episode}的字幕索引")
        return result

    if not query:  # 整集台词
        ep_code, path = files[0]
        lines = subtitle_lines(json.loads(path.read_text(encoding="utf-8"))["units"], path)
        out, used, truncated = [], 0, False
        for start, line in lines:
            row = f"{_mmss(start)} {line}"
            if used + len(row) > SUBS_TRANSCRIPT_MAX_CHARS:
                truncated = True
                break
            out.append(row)
            used += len(row)
        result.update(episode=ep_code, transcript="\n".join(out), truncated=truncated)
        return result

    needle = query.lower()
    hits: list[dict[str, Any]] = []
    for ep_code, path in files:
        lines = subtitle_lines(json.loads(path.read_text(encoding="utf-8"))["units"], path)
        for i, (start, line) in enumerate(lines):
            if needle in line.lower():
                ctx_lines = [lines[j][1] for j in (i - 1, i + 1) if 0 <= j < len(lines)]
                hits.append({"ep": ep_code, "time": _mmss(start), "text": line, "context": ctx_lines})
                if len(hits) >= limit:
                    result.update(hits=hits, truncated=True)
                    return result
    result.update(hits=hits, truncated=False)
    return result


def _tool_web_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.web import SEARCH_DEFAULT_LIMIT, SEARCH_MAX_LIMIT, search_web

    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query 不能为空")
    try:
        limit = int(args.get("limit", SEARCH_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        raise ValueError("limit 必须是整数") from None
    return search_web(query, max(1, min(limit, SEARCH_MAX_LIMIT)), root=ctx.root)


def _keep_record(res: dict[str, Any], kind: str, url: str, ctx: ToolContext) -> dict[str, Any]:
    """D99：抓到的全文留档，返回值换成记录号——全文不进 LLM（它看的仍是截断版 text / markdown）。"""
    from pipeline.agent import fetch_log

    full = res.pop("full_text", None)
    if isinstance(full, str):
        # 留档失败不连累抓取本身，但要让模型看见：之后 fact_add 会如实报「这页没有留档」
        try:
            res["fetch_record"] = fetch_log.record(url=url, final_url=res.get("final_url") or res.get("url"),
                                                   kind=kind, text=full, sid=ctx.session_id, root=ctx.root)
        except (ValueError, OSError) as exc:
            res["fetch_record"] = None
            res["fetch_record_error"] = f"抓取留档失败：{exc}"
    return res


def _tool_fact_add(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """D99：事实库写入。目录取自宿主绑定（卷 / 期目录；企划会话取企划目录），不取模型参数。"""
    from pipeline import facts

    target = ctx.project_dir if ctx.session_kind == "project" else ctx.episode_dir
    if target is None:
        raise PermissionError(NO_EPISODE_MESSAGE)
    entries = args.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries 必须是非空数组")
    return facts.add(entries, target=Path(target), sid=ctx.session_id, root=ctx.root)


def _tool_web_fetch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.web import fetch_web

    url = str(args.get("url", "")).strip()
    if not url:
        raise ValueError("url 不能为空")
    return _keep_record(fetch_web(url, root=ctx.root, keep_full=True), "fetch", url, ctx)


def _tool_acquire_propose(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        # D43 / Spec 17 §3.4（人裁决 (A)）：idea 下提候选也报「先建期」——否则提案成功后
        # 内核会无条件弹抓取卡，而抓取卡执行器以 episode_dir=None 调 run_pipeline，自相矛盾。
        raise PermissionError(NO_EPISODE_MESSAGE)
    # D95：企划会话放行——候选写进全库共用的 library/incoming/candidates.json，不写企划母目录
    from pipeline.candidates import propose_candidates

    raw = args.get("candidates")
    if not isinstance(raw, list) or not raw or not all(isinstance(c, dict) for c in raw):
        raise ValueError("candidates 必须是非空的对象数组")
    return propose_candidates(raw, data_root=ctx.base / "data")


def _tool_crawl(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.web_crawl import crawl_page

    url = str(args.get("url", "")).strip()
    if not url:
        raise ValueError("url 不能为空")
    reason = str(args.get("reason", "")).strip()
    if not reason:
        raise ValueError("reason 不能为空")
    stealth = bool(args.get("stealth", False))
    return _keep_record(crawl_page(url, reason, stealth=stealth, root=ctx.root, keep_full=True), "crawl", url, ctx)


def _tool_browser(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.web_browser import browser_action

    action = str(args.get("action", "")).strip()
    reason = str(args.get("reason", "")).strip()
    url_raw = args.get("url")
    url = str(url_raw).strip() if url_raw is not None else None
    res = browser_action(
        action,
        reason,
        url=url,
        episode_dir=ctx.episode_dir,
        scope=ctx.scope,
        root=ctx.root,
        keep_full=True,
    )
    return _keep_record(res, "browser", str(res.get("url") or url or ""), ctx) if "full_text" in res else res


def memory_store_for(
    args: dict[str, Any], session_kind: str, project_dir: Path | None, root: Path | None
) -> tuple[Any, dict[str, Any]]:
    """D81 §4.5：`target` 选记忆（global 默认 / project）。企划目录取自宿主绑定，不取模型参数。

    返回 (store 或 None=全局, 去掉 target 键的参数)。普通期 / 选题会话给 project → PermissionError。
    """
    from pipeline.agent import memory

    rest = {k: v for k, v in args.items() if k != "target"}
    target = args.get("target", "global")
    if target in (None, "", "global"):
        return None, rest
    if target != "project":
        raise ValueError(f"target 只能是 global 或 project，收到 {target!r}")
    if session_kind not in ("volume", "project") or project_dir is None:
        raise PermissionError("target=project 只在卷会话与企划会话里有效（当前会话不属于任何企划）")
    return memory.project_store(project_dir, root), rest


def _tool_write_memory(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """唯一的记忆写入口。confirmed 只取 ctx.confirmed，**不读 args["confirmed"]**（Spec 7 §2.2）。"""
    from pipeline.agent import memory

    store, rest = memory_store_for(args, ctx.session_kind, ctx.project_dir, ctx.root)
    plan = memory.apply_op(
        str(rest.get("op", "")),
        rest,
        root=ctx.root,
        episode_dir=ctx.episode_dir,
        confirmed=ctx.confirmed,
        scope=ctx.scope,
        store=store,
    )
    return {
        "op": plan.op,
        "summary": plan.summary,
        "after_len": plan.after_len,
        "text": plan.result_text,      # 模型从工具返回值里拿到写后全文（Spec 7 §2.6）
    }


def _tool_cover_edit(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """第 13 个工具（Spec 12 / ADR-0025）：委托 `pipeline.cover_edit` 确定性渲染。

    函数级 import cover_edit：顶层不背 Pillow（红线 7）。受控领域异常转 ValueError，
    由 execute_tool 的统一闸转成结构化错误回喂 LLM（不裸抛 RuntimeException）。
    """
    if not ctx.episode_dir:
        raise PermissionError(NO_EPISODE_MESSAGE)
    if ctx.session_kind == "project":
        raise PermissionError(PROJECT_VOLUME_ONLY_MESSAGE.format(tool="cover_edit"))
    from pipeline import cover_edit

    try:
        return cover_edit.render_cover(ctx.episode_dir, dict(args))
    except cover_edit.CoverEditError as exc:
        raise ValueError(str(exc)) from exc


def _tool_view_image(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """第 16 个工具（D90 / ADR-0031）：实现在 `agent/vision.py`（附图投影与工具同一处）。"""
    from pipeline.agent import vision

    return vision.view_image(args, ctx)


# ---------------------------------------------------------------------------
# bash（第 15 个工具，D77 / ADR-0029：人选方案 B，不加沙箱）
# ---------------------------------------------------------------------------

# 只读判定：首词在此集合、且整条命令不含任何 shell 元字符才算只读。判不准一律当写（弹卡）。
_BASH_READONLY_HEADS = frozenset({
    "ls", "pwd", "cat", "head", "tail", "wc", "file", "stat", "du", "find",
    "ffprobe", "mdls", "which", "echo", "sips",
})
# 重定向、管道、串联、命令替换、变量展开、换行、转义、子 shell / 花括号：出现任意一个就不再是「单条只读命令」
_BASH_META = re.compile(r"[><|;&`$\n\\(){}]")
_FIND_WRITE_FLAGS = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls",
})


def bash_readonly(command: str) -> bool:
    """保守判定一条 bash 命令是否只读（ADR-0029 决策 4）。宁可误判为写，不可误判为读。"""
    text = command.strip()
    if not text or _BASH_META.search(text):
        return False
    try:
        words = shlex.split(text)
    except ValueError:
        return False
    if not words or words[0] not in _BASH_READONLY_HEADS:
        return False
    if words[0] == "find" and any(w in _FIND_WRITE_FLAGS for w in words[1:]):
        return False
    if words[0] == "sips":
        # sips 只有 -g / --getProperty 是查询；-s / -z / -c / --out 等都会写文件
        flags = [w for w in words[1:] if w.startswith("-")]
        if not flags or any(f not in ("-g", "--getProperty") for f in flags):
            return False
    return True


# 密钥变量名（N50 同一组）：名字像密钥的环境变量，外加 LLM / web 配置指名的密钥变量
_SECRET_ENV_NAME = re.compile(r"(?i)(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$|^AVA_.*KEY")
# config/*.local.json 里键名含这些词的字符串值视为密钥
_SECRET_FIELD_NAME = re.compile(r"(?i)(key|token|secret|password)")
SECRET_MIN_LEN = 8   # 短于 8 的值不当密钥替换：「true」「cn」这类短值替换会把正常输出改得面目全非


def secret_env_names(root: Path | None = None) -> tuple[str, ...]:
    """不传给 bash 子进程的环境变量名。"""
    names = {k for k in os.environ if _SECRET_ENV_NAME.search(k)}
    try:
        from pipeline.agent.llm import api_key_env_name

        llm_name = api_key_env_name(root)
        if llm_name:
            names.add(llm_name)
    except Exception:  # noqa: BLE001 —— 配置坏了不影响剥名字：正则那一组照剥
        pass
    try:
        from pipeline.agent.web import web_key_env_names

        names.update(web_key_env_names(root))
    except Exception:  # noqa: BLE001
        pass
    return tuple(sorted(names))


def _collect_secret_fields(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and _SECRET_FIELD_NAME.search(str(k)):
                out.add(v)
            else:
                _collect_secret_fields(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_secret_fields(v, out)


def _secret_values(root: Path | None = None) -> list[str]:
    """已知密钥值：密钥环境变量的值 + config/*.local.json 里键名像密钥的值。长的排前（先换长值）。"""
    values: set[str] = set()
    for name in secret_env_names(root):
        v = os.environ.get(name)
        if v:
            values.add(v)
    config_dir = Path(root or paths.ROOT) / "config"
    for path in sorted(config_dir.glob("*.local.json")):
        try:
            _collect_secret_fields(json.loads(path.read_text(encoding="utf-8")), values)
        except (OSError, ValueError):
            continue
    return sorted((v for v in values if len(v) >= SECRET_MIN_LEN), key=len, reverse=True)


def scrub_secrets(text: str, root: Path | None = None) -> str:
    """把已知密钥值换成 [已脱敏]（ADR-0029 决策 5：出网断言只比文件名不看内容，bash 输出要自己脱）。"""
    for v in _secret_values(root):
        text = text.replace(v, "[已脱敏]")
    return text


#: 回合报错原文落盘 / 下发桌面端的长度上限：够放服务商的一句错误说明（模型名不存在、额度用尽、HTTP 状态），
#: 不够塞进整段响应体——那是排查要的「哪一类错」，不是转储。
TURN_ERROR_MAX_CHARS = 300


def turn_error_text(error: Any, root: Path | None = None) -> str | None:
    """回合报错原文 → 可落盘、可给人看的一行：脱敏密钥值、压空白、截断。没有报错返回 None。"""
    if not error:
        return None
    text = " ".join(scrub_secrets(str(error), root).split())
    return text if len(text) <= TURN_ERROR_MAX_CHARS else text[: TURN_ERROR_MAX_CHARS - 1] + "…"


def _tool_bash(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    command = str(args.get("command", ""))
    if not command.strip():
        raise ValueError("command 不能为空")
    if not ctx.confirmed and not bash_readonly(command):
        # 实现层双保险：绕过 review 层的裸调用只放只读命令
        raise PermissionError("需人类确认：这条 bash 命令会写盘或无法判定为只读，须经审批卡批准后执行")
    from pipeline.jobs import create_shell_job, execute_job

    job = create_shell_job(
        command, ctx.episode_dir, scope=ctx.scope, env_drop=secret_env_names(ctx.root),
    )
    done = execute_job(job)
    return {
        "ok": done.returncode == 0,
        "cwd": str(done.cwd),
        "returncode": done.returncode,
        "duration_s": done.duration_s,
        "stdout_tail": scrub_secrets(done.stdout_tail, ctx.root),
        "stderr_tail": scrub_secrets(done.stderr_tail, ctx.root),
        "truncated": done.truncated,
    }


_TOOL_IMPLS: dict[str, Callable[[dict[str, Any], ToolContext], Any]] = {
    "read_artifact": _tool_read_artifact,
    "write_episode_file": _tool_write_episode_file,
    "list_episodes": _tool_list_episodes,
    "read_status": _tool_read_status,
    "run_pipeline": _tool_run_pipeline,
    "search_notes": _tool_search_notes,
    "web_search": _tool_web_search,
    "web_fetch": _tool_web_fetch,
    "acquire_propose": _tool_acquire_propose,
    "crawl": _tool_crawl,
    "browser": _tool_browser,
    "write_memory": _tool_write_memory,
    "cover_edit": _tool_cover_edit,
    "write_note": _tool_write_note,
    "bash": _tool_bash,
    "view_image": _tool_view_image,
    "fact_add": _tool_fact_add,
}


# 需要期目录的工具集合（D43 / Spec 17 §3.4）：无期会话（idea）里调用这几个工具，
# review 层在弹卡之前 reject、实现层拋同一文案（双保险），两层共用这一份集合与文案。
NEEDS_EPISODE_TOOLS: frozenset[str] = frozenset(
    {"write_episode_file", "cover_edit", "run_pipeline", "acquire_propose", "view_image"}
)
NO_EPISODE_MESSAGE = "当前没有期目录：这一步要先建期（桌面端「＋ 新建一期」/ 终端 `ava new <名>`）"
# D81 §4.2（红队 R-3）：企划会话的期目录绑定是企划目录，卷级操作不许落到默认分支写进母目录。
# D95：acquire_propose 移出（候选与素材全库共用，见 PROJECT_LIBRARY_MODULES）
PROJECT_VOLUME_ONLY_TOOLS: frozenset[str] = frozenset({"cover_edit", "view_image"})
PROJECT_VOLUME_ONLY_MESSAGE = "{tool} 是卷级操作，企划会话里不做：到对应卷的会话里做（封面与看图只认某一卷的期目录）"


def execute_tool(name: str, args: dict[str, Any] | None, ctx: ToolContext) -> dict[str, Any]:
    """执行一个工具，返回可 JSON 化的结果（错误也当数据回喂 LLM）。

    三层闸（D43 / Spec 17 §3.2：原第 ② 层「越 scope 白名单」已删）：
    ① 名字已注册；② 可选依赖已安装；③ 实现层自身边界。
    """
    if name not in TOOL_SCHEMAS:
        return {"ok": False, "error": f"未注册的工具 '{name}'（工具清单不现场发明）"}
    req_extra = TOOL_SCHEMAS[name].get("requires_extra")
    if req_extra and not _extra_available(str(req_extra)):
        return {
            "ok": False,
            "error": (
                f"工具 '{name}' 需要可选依赖 '{req_extra}'（uv sync --extra {name}；"
                "注意 uv sync 会卸掉未列出的 extras，在用的 apple/dev 等须一并列出），"
                "当前环境未安装"
            ),
        }
    try:
        result = _TOOL_IMPLS[name](dict(args or {}), ctx)
    except (PermissionError, FileNotFoundError, ValueError, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "result": result}


class _TailBuffer:
    def __init__(self, max_bytes: int = 4096) -> None:
        self.max_bytes = max_bytes
        self.chunks: collections.deque[bytes] = collections.deque()
        self.current_bytes = 0
        self.total_bytes = 0

    def append(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if len(chunk) > self.max_bytes * 2:
            chunk = chunk[-(self.max_bytes * 2):]
        self.chunks.append(chunk)
        self.current_bytes += len(chunk)
        while self.current_bytes > self.max_bytes * 2 and len(self.chunks) > 1:
            dropped = self.chunks.popleft()
            self.current_bytes -= len(dropped)

    def get_tail(self) -> tuple[str, bool]:
        full = b"".join(self.chunks)
        truncated = self.total_bytes > self.max_bytes
        if len(full) > self.max_bytes:
            full = full[-self.max_bytes:]
        return full.decode("utf-8", errors="replace"), truncated


def run_pipeline(
    command: str | list[str],
    episode_dir: Path | str | None = None,
    *,
    scope: str = "pipeline",
    confirmed: bool = False,
    project_dir: Path | str | None = None,
) -> dict[str, Any]:
    """白名单执行器入口（向后兼容适配器，底层由 pipeline.jobs 全权驱动）。

    `project_dir`：卷 / 企划会话绑定的企划目录（D81 §9.3 ⑥，crosscheck 补位用），校验与立项两处同传。

    纪律：
    1. confirmed=False 时为纯 dry-run 校验，不持久化 Job，不发 job_created，不留幽灵；
    2. confirmed=True 时真正调用 create_job 立项并 execute_job 执行。
    """
    from pipeline.agent.tools import validate_pipeline_command
    from pipeline.jobs import JobStatus, create_job, execute_job

    ep_path = Path(episode_dir).resolve() if episode_dir else None

    # 阶段一：未确认状态，做纯 dry-run 校验，零事件发射，零幽灵对象
    if not confirmed:
        valid, msg, argv = validate_pipeline_command(command, ep_dir=ep_path, project_dir=project_dir)
        if not valid:
            return {
                "ok": False,
                "message": msg,
                "argv": [],
                "returncode": None,
                "duration_s": 0.0,
                "stdout_tail": "",
                "stderr_tail": "",
                "truncated": False,
                "job_id": None,
            }
        return {
            "ok": True,
            "message": "待人类确认",
            "argv": argv,
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "truncated": False,
            "job_id": None,
        }

    # 阶段二：已确认状态，正式立项并流转状态机
    job, valid, msg = create_job(command, episode_dir=ep_path, scope=scope, project_dir=project_dir)
    if not valid:
        return {
            "ok": False,
            "message": msg,
            "argv": [],
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "truncated": False,
            "job_id": job.job_id,
        }

    executed_job = execute_job(job)
    return {
        "ok": executed_job.status == JobStatus.SUCCEEDED,
        "message": executed_job.message,
        "argv": executed_job.argv,
        "returncode": executed_job.returncode,
        "duration_s": executed_job.duration_s,
        "stdout_tail": executed_job.stdout_tail,
        "stderr_tail": executed_job.stderr_tail,
        "truncated": executed_job.truncated,
        "job_id": executed_job.job_id,
    }
