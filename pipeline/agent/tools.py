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
}

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
    # D59：番剧笔记的时间网格（写 data/library/timeline/），无子命令，弹卡
    "timeline",
    # D59：期内补料——scout 出缺口清单（写 scout-ticket-*.md），ingest_patch 建本期补丁池（含 --reset）。
    # 两者都只接期目录一个位置参数，自动补位
    "scout",
    "ingest_patch",
}

# 其中纯只读、免审卡的模块（2026-10-08 spec §A）：check_script 只读稿件与索引后打印报告，
# status 只读期目录；两者都不落盘（入选前已 grep 过无写调用）。新增成员前必须同样核实。
READONLY_PIPELINE_MODULES: frozenset[str] = frozenset({"check_script", "status"})

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
}

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
    "acquire": {"fetch", "gate", "register", "forget"},
}

# 出网敏感目录与关键词（§2.5 Y2-r19）
RESTRICTED_EGRESS_PATTERNS: tuple[str, ...] = (
    "cloud.local.json",
    "agent.local.json",
    "03-audio/manifest.json",
    "03-audio/voice.json",
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

    return resolved_ep


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
    6. 覆盖草稿 / 定稿前留底；
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
})

# 自动补位的模块：argparse 都只有一个位置参数（tts 另有子命令词 run / probe）
_AUTOFILL_MODULES: tuple[str, ...] = ("tts", "clips", "review", "render", "qc", "cover", "status", "corrections",
                                      "adversarial", "scout", "ingest_patch")
# 带子命令的模块：子命令词不计入位置参数，期目录补在它之后
_SUBCOMMAND_WORDS: dict[str, tuple[str, ...]] = {
    "tts": ("run", "probe"),
    "corrections": ("add", "global", "check"),
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
) -> tuple[bool, str, list[str]]:
    """白名单子命令校验执行器（Spec §2.4 Y1-r8, Y1-r11, R1-r10, B1-r10；D43 / Spec 17 合表）。

    D43 / Spec 17 §3.3：不再按 scope 分派，合成一份放行表——
    `PIPELINE_MODULES` 的模块（不限子命令）∪ `ASSET_COMMANDS` 的模块与各自子命令清单。
    两条写死的合表语义：
    (a) 模块属于 `ASSET_COMMANDS` 时子命令校验**永远生效**（将来两表若出现重名模块，以子命令限制为准）；
    (b) 当期目录自动补位只对 `PIPELINE_MODULES` 侧的模块生效，asset 侧模块不补位（维持现状）。

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
        # 自动补位当前期目录参数（🔴 1 修复）
        if ep_dir:
            ep_path = Path(ep_dir).resolve()
            pos_args = _extract_positional_args(args)
            if module == "corrections":
                if len(pos_args) == 1 and pos_args[0] in _SUBCOMMAND_WORDS["corrections"]:
                    sub_idx = args.index(pos_args[0])
                    args = args[:sub_idx + 1] + [str(ep_path)] + args[sub_idx + 1:]
            elif module in ("tts", "clips", "review", "render", "qc", "cover", "status", "adversarial",
                            "scout", "ingest_patch"):
                if not pos_args:
                    args = [str(ep_path)] + args
                elif module == "tts" and pos_args == ["run"]:
                    run_idx = args.index("run")
                    args = args[:run_idx + 1] + [str(ep_path)] + args[run_idx + 1:]
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
            "每次写都会弹卡给人看 diff。改已有文件的一两段时用 edits 只传改动的片段，不要整篇重写。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "enum": sorted(EPISODE_WRITABLE_FILES),
                    "description": "白名单内的文件名",
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
            "**校验通过后弹审批卡片，人类按 y 即在本对话回路内真执行**，"
            "实时输出与尾部日志（stdout_tail/stderr_tail）回喂给你做汇报；"
            "--force/--force-all/cloud exec 一律拒收。"
            "只读命令免卡：check_script、status、corrections check、"
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
    "acquire_propose": {
        "name": "acquire_propose",
        "adr": "ADR-0021",
        # 不标 side_effect：fail-closed 默认 True（cli.py:_default_approve），每次调用必过人审卡
        "description": (
            "把素材候选追加到 data/library/incoming/candidates.json（人审用提案清单，"
            "追加式、同 URL 自动跳过）。只写提案，绝不抓取——fetch 永远由人逐条批准后 "
            "走 pipeline.acquire。每条必须含 title/url/type/source/why；"
            "why 要写清补哪个缺口、凭什么认为是它（skill: acquire-assets §一）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "一批候选（一次调用一张审批卡审整批）",
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
            "登录态浏览器（升级链第三级，每次调用过人审卡，每次启动落审批事件）。"
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
            "写经验不写规则、不写流水账。op=add/revise/merge/retire 弹人审卡，人看全文按 y 才落盘；"
            "op=cite 把当期记为某条的证据（免卡）。禁用「可能」类措辞、规则强度词与审批行为词。"
            "全文 4000 字符预算，超限被拒时按返回的腾位候选序先 merge 或 retire 首位。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "revise", "merge", "cite", "retire"]},
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
            "output 目标已存在则拒收（换名迭代 edit-1、edit-2…）。每次调用弹人审卡。"
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
    roots: list[Path] = []
    if ctx.episode_dir:
        roots.append(Path(ctx.episode_dir).resolve())
    library = (ctx.base / "data" / "library").resolve()
    if library.exists():
        roots.append(library)
    return roots


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


def _is_memory_file(target: Path, ctx: ToolContext) -> bool:
    """是否命中 data/library/memory.md（resolve + casefold，Spec 7 §4.3②）。

    记忆只经装配器校验后注入；读工具留口子，校验层就有了旁路。
    """
    from pipeline.agent.memory import MEMORY_REL_PATH   # 函数内延迟 import（叶子性）

    return str(Path(target).resolve()).casefold() == str((ctx.base / MEMORY_REL_PATH).resolve()).casefold()


def _tool_read_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """读域写死：期目录内 + data/library/ 只读，硬排除 03-audio/ 与 04-patch/（§2.5）。"""
    raw = str(args.get("path", "")).strip()
    if not raw:
        raise ValueError("path 不能为空")
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

    candidates: list[Path] = []
    if ctx.episode_dir:
        candidates.append(Path(ctx.episode_dir) / rel)
    candidates.append(ctx.base / "data" / "library" / rel)
    candidates.append(ctx.base / "data" / "library" / "notes" / rel)

    roots = _allowed_read_roots(ctx)
    for cand in candidates:
        target = cand.resolve()
        # 判 resolve 后的绝对路径：软链接绕进 03-audio 同样拦下
        hit = deny_dir_hit(target)
        if hit:
            raise PermissionError(
                f"读域硬排除 {hit}/：属「一律不出网」清单（Spec §2.5 Y2-r19），拒读: {raw}"
            )
        if _is_memory_file(target, ctx):
            raise PermissionError(
                "memory.md 只经装配器校验后注入，模型不可直读；人请用 /memory"
            )
        if not any(target == r or r in target.parents for r in roots):
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


def _tool_write_episode_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        raise PermissionError(NO_EPISODE_MESSAGE)
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

    episodes, hidden = get_episodes_list(root=ctx.root)
    episodes_detail = [
        {
            "name": ep.name,
            "current_step": inspect_episode(ep).current_step,
            "is_blocked": inspect_episode(ep).is_blocked,
        }
        for ep in episodes
    ]
    return {
        "episodes": [ep.name for ep in episodes],
        "episodes_detail": episodes_detail,
        "hidden_underscore": hidden,
    }


def _tool_read_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.status import format_status, inspect_episode

    target = str(args.get("episode", "")).strip()
    if target:
        from pipeline.agent.cli import resolve_episode_target
        ep_dir = resolve_episode_target(target)
        if ep_dir is None:
            raise FileNotFoundError(f"期目录不存在: {target}")
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


def _tool_run_pipeline(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        # 实现层双保险（D43 / Spec 17 §3.4）：裸循环等绕过 review 层的路径也拿到同一文案。
        raise PermissionError(NO_EPISODE_MESSAGE)
    outcome = run_pipeline(
        str(args.get("command", "")),
        episode_dir=ctx.episode_dir,
        scope=ctx.scope,
        confirmed=ctx.confirmed,
    )
    if outcome["ok"] and outcome["returncode"] is None and not ctx.confirmed:
        # 只校验未执行：显式报「需人类确认」，不假装跑过了（静默 fallback 是家规禁项）。
        return {
            "ok": False,
            "error": "需人类确认：本工具只校验并回显 argv，执行请由人在宿主 REPL 用 /run 确认",
            "argv": outcome["argv"],
        }
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
    末单元只有一句）。从末尾往前剥后缀即可精确还原；结构对不上就报错，不静默给半截台词。"""
    if not units:
        return []
    lines: list[str] = [""] * len(units)
    lines[-1] = str(units[-1]["text"])
    for i in range(len(units) - 2, -1, -1):
        text, suffix = str(units[i]["text"]), " " + lines[i + 1]
        if not (text.endswith(suffix) and len(text) > len(suffix)):
            raise ValueError(f"{source.name} 第 {i} 个单元不是 WINDOW=2 滑窗结构，无法还原单句")
        lines[i] = text[: -len(suffix)]
    return [(float(u["start"]), line) for u, line in zip(units, lines)]


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


def _tool_web_fetch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.web import fetch_web

    url = str(args.get("url", "")).strip()
    if not url:
        raise ValueError("url 不能为空")
    return fetch_web(url, root=ctx.root)


def _tool_acquire_propose(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        # D43 / Spec 17 §3.4（人裁决 (A)）：idea 下提候选也报「先建期」——否则提案成功后
        # 内核会无条件弹抓取卡，而抓取卡执行器以 episode_dir=None 调 run_pipeline，自相矛盾。
        raise PermissionError(NO_EPISODE_MESSAGE)
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
    return crawl_page(url, reason, stealth=stealth, root=ctx.root)


def _tool_browser(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from pipeline.agent.web_browser import browser_action

    action = str(args.get("action", "")).strip()
    reason = str(args.get("reason", "")).strip()
    url_raw = args.get("url")
    url = str(url_raw).strip() if url_raw is not None else None
    return browser_action(
        action,
        reason,
        url=url,
        episode_dir=ctx.episode_dir,
        scope=ctx.scope,
        root=ctx.root,
    )


def _tool_write_memory(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """唯一的记忆写入口。confirmed 只取 ctx.confirmed，**不读 args["confirmed"]**（Spec 7 §2.2）。"""
    from pipeline.agent import memory

    plan = memory.apply_op(
        str(args.get("op", "")),
        args,
        root=ctx.root,
        episode_dir=ctx.episode_dir,
        confirmed=ctx.confirmed,
        scope=ctx.scope,
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
    from pipeline import cover_edit

    try:
        return cover_edit.render_cover(ctx.episode_dir, dict(args))
    except cover_edit.CoverEditError as exc:
        raise ValueError(str(exc)) from exc


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
}


# 需要期目录的工具集合（D43 / Spec 17 §3.4）：无期会话（idea）里调用这四个工具，
# review 层在弹卡之前 reject、实现层拋同一文案（双保险），两层共用这一份集合与文案。
NEEDS_EPISODE_TOOLS: frozenset[str] = frozenset(
    {"write_episode_file", "cover_edit", "run_pipeline", "acquire_propose"}
)
NO_EPISODE_MESSAGE = "当前没有期目录：这一步要先建期（桌面端「＋ 新建一期」/ 终端 `ava new <名>`）"


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
) -> dict[str, Any]:
    """白名单执行器入口（向后兼容适配器，底层由 pipeline.jobs 全权驱动）。

    纪律：
    1. confirmed=False 时为纯 dry-run 校验，不持久化 Job，不发 job_created，不留幽灵；
    2. confirmed=True 时真正调用 create_job 立项并 execute_job 执行。
    """
    from pipeline.agent.tools import validate_pipeline_command
    from pipeline.jobs import JobStatus, create_job, execute_job

    ep_path = Path(episode_dir).resolve() if episode_dir else None

    # 阶段一：未确认状态，做纯 dry-run 校验，零事件发射，零幽灵对象
    if not confirmed:
        valid, msg, argv = validate_pipeline_command(command, ep_dir=ep_path)
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
    job, valid, msg = create_job(command, episode_dir=ep_path, scope=scope)
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
