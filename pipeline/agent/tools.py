"""LLM 受控工具注册表与 Pipeline 白名单执行器（Spec §2.4, §2.5, §5 PR1）。
"""

from __future__ import annotations

import codecs
import collections
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
from typing import Any, Callable

from pipeline import paths
from pipeline.cloud import validate_extra_args

# Creative Scope 允许写入的文件白名单（§2.4）
CREATIVE_WRITABLE_FILES: set[str] = {
    "01-topic.md",
    "02-script.draft.md",
}

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
}

# Asset Scope 允许执行的 Phase 0 子命令白名单（§2.4 Y1-r8, Y2-r10）
ASSET_COMMANDS: dict[str, set[str]] = {
    "ingest": {"phase0"},
    "shots": {"build", "frames", "caption-frames"},
    "vindex": {"captions", "embed"},
    "faces": {"detect", "cluster", "sheet", "name", "presence"},
    "cloud": {"status", "logs", "doctor", "up", "down", "run", "push", "pull"},
}

# 出网敏感目录与关键词（§2.5 Y2-r19）
RESTRICTED_EGRESS_PATTERNS: tuple[str, ...] = (
    "cloud.local.json",
    "agent.local.json",
    "03-audio/manifest.json",
    "03-audio/voice.json",
)


def write_episode_file(
    episode_dir: Path | str,
    filename: str,
    content: str,
    scope: str = "creative",
    confirmed: bool = False,
    root: Path | None = None,
) -> Path:
    """受控期文件写入工具（Spec §2.4 Code Freeze 护栏）。

    纪律：
    1. 仅限 creative scope 且文件名在白名单：{01-topic.md, 02-script.draft.md}；
    2. pipeline / asset scope 零写权限；
    3. 双端 resolve 防 symlink 穿透；
    4. 三种越界拦截：写 pipeline/、写父级/祖先目录、写白名单外文件；
    5. 写 01-topic.md 强制人类确认；
    6. 落盘必须走 paths.atomic_write。
    """
    if scope != "creative":
        raise PermissionError(f"Scope '{scope}' 拥有零写权限，严禁写入任何期产物文件")

    # 文件名白名单检查
    clean_name = Path(filename).name
    if clean_name not in CREATIVE_WRITABLE_FILES or filename != clean_name:
        raise PermissionError(
            f"文件 '{filename}' 不在 creative 写入白名单内（仅放行: {sorted(CREATIVE_WRITABLE_FILES)}）"
        )

    # 路径解析与双端 resolve 校验
    base = Path(root or paths.ROOT)
    raw_ep = Path(episode_dir)
    resolved_ep = raw_ep.resolve()
    target_resolved = (raw_ep / clean_name).resolve()

    # 1. 拦截对 pipeline/ 源码目录的修改（优先触发 Code Freeze）
    repo_pipeline = (base / "pipeline").resolve()
    if (
        repo_pipeline in target_resolved.parents
        or str(target_resolved).startswith(str(repo_pipeline))
        or repo_pipeline in resolved_ep.parents
        or resolved_ep == repo_pipeline
    ):
        raise PermissionError("禁止修改 pipeline/ 源码目录文件，触发 Code Freeze 护栏")

    # 2. 纵深防御（fail-closed，B4-r6）：期目录必须落在 data/episodes 之下
    episodes_root = (base / "data" / "episodes").resolve()
    if not episodes_root.exists():
        raise PermissionError(f"data/episodes 不可达（外置盘未挂载？），拒绝写入: {episodes_root}")
    if resolved_ep == base.resolve() or resolved_ep == Path("/tmp").resolve():
        raise PermissionError(f"禁止将仓库根或 /tmp 作为期目录写入: {resolved_ep}")
    if resolved_ep == episodes_root or episodes_root not in resolved_ep.parents:
        raise PermissionError(f"期目录必须位于 {episodes_root} 之下: {resolved_ep}")

    # 2. 拦截父级或兄弟目录越界
    if target_resolved.parent != resolved_ep:
        raise PermissionError(f"目标路径越界：{target_resolved} 不在当期根目录 {resolved_ep} 下")

    # 3. 写 01-topic.md 强制人类确认 (B7)
    if clean_name == "01-topic.md" and not confirmed:
        raise PermissionError("写入 01-topic.md 是关键立项操作，必须获得人类显式确认")

    # 4. 原子落盘
    paths.atomic_write(target_resolved, content)
    return target_resolved


def _extract_positional_args(args: list[str]) -> list[str]:
    """提取真正的命令行位置参数，跳过旗标及其参数值。

    注意（债务记账）：模块若新增带值旗标（非布尔 flag），须同步登记到 valued_flags；
    否则该旗标的值会被误判为位置参数，导致自动注入当前期目录失效。
    """
    pos: list[str] = []
    i = 0
    valued_flags = {
        "--redo", "--config", "--review", "--anime", "--out", "--ref", "--seed",
        "--pattern", "--episode", "--note", "--target", "--session", "--floor",
    }
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            flag = a.split("=")[0]
            if "=" in a:
                i += 1
            elif flag in valued_flags:
                i += 2
            else:
                i += 1
        else:
            pos.append(a)
            i += 1
    return pos


def validate_pipeline_command(
    cmd_tokens: list[str] | str,
    scope: str = "pipeline",
    ep_dir: Path | str | None = None,
) -> tuple[bool, str, list[str]]:
    """白名单子命令校验执行器（Spec §2.4 Y1-r8, Y1-r11, R1-r10, B1-r10）。

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
    for a in args:
        flag_name = a.split("=")[0]
        if flag_name in ("--force", "--force-all"):
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

    # 3. 按 scope 分派白名单检查
    if scope in ("creative", "pipeline"):
        if module not in PIPELINE_MODULES:
            return (
                False,
                f"模块 'pipeline.{module}' 不在 {scope} 允许的白名单内（当前放行: {sorted(PIPELINE_MODULES)}）",
                [],
            )

        # 自动补位当前期目录参数（🔴 1 修复）
        if ep_dir:
            ep_path = Path(ep_dir).resolve()
            pos_args = _extract_positional_args(args)
            if module in ("tts", "clips", "review", "render", "qc", "cover", "status"):
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

    elif scope == "asset":
        if module not in ASSET_COMMANDS:
            return (
                False,
                f"模块 'pipeline.{module}' 不在 asset scope 白名单内（当前放行: {sorted(ASSET_COMMANDS)}）",
                [],
            )
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

        normalized = [sys_python(), "-m", f"pipeline.{module}"] + args
        return True, "校验通过", normalized

    return False, f"未知的 Scope: {scope}", []


def assert_egress_boundary(endpoint: str, content: Any) -> None:
    """出网安全边界断言（Spec §2.5 Y2-r19）。

    确保发送给外部 LLM 端点的内容不含敏感目录路径及凭据数据。
    **大小写不敏感**：APFS 默认大小写不敏感，`Cloud.Local.JSON` 与 `cloud.local.json`
    是同一个文件，字面量比较等于半扇门（终审二轮 P0）。
    """
    text = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    folded = text.casefold()
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        if pattern.casefold() in folded:
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

    scope 决定白名单；episode_dir 决定读/写边界。**读域与写域都从上下文绑定，
    不取 LLM 传来的参数**——参数由模型填，边界由人定。
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
        "description": (
            "写入当期稿件文件。仅限 01-topic.md 与 02-script.draft.md；"
            "写 01-topic.md 必须 confirmed=true 且需人在 REPL 显式确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "enum": sorted(CREATIVE_WRITABLE_FILES),
                    "description": "白名单内的文件名",
                },
                "content": {"type": "string", "description": "完整文件内容"},
                "confirmed": {
                    "type": "boolean",
                    "description": "写 01-topic.md 时必须为 true（人类已确认）",
                },
            },
            "required": ["filename", "content"],
            "additionalProperties": False,
        },
    },
    "list_episodes": {
        "name": "list_episodes",
        "side_effect": False,
        "description": "列出可见期目录（排除 . 与 _ 前缀），返回期名列表与每期的阶段及阻塞标记（episodes_detail）。无参数。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "read_status": {
        "name": "read_status",
        "side_effect": False,
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
        "description": (
            "校验白名单内的 pipeline 子命令（如 tts --redo 3）并返回规范化 argv。"
            "**校验通过后弹审批卡片，人类按 y 即在本对话回路内真执行**，"
            "实时输出与尾部日志（stdout_tail/stderr_tail）回喂给你做汇报；"
            "--force/--force-all/cloud exec 一律拒收。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "如 'tts --redo 3' 或 'clips'"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "search_notes": {
        "name": "search_notes",
        "side_effect": False,
        "description": "在 data/library/ 只读笔记里做大小写不敏感的子串检索，返回命中片段。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词"},
                "limit": {"type": "integer", "description": "最多返回几条（默认 5，上限 20）"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


def tool_names_for_scope(scope: str, root: Path | None = None) -> list[str]:
    """读 config/agent/tools.json 里该 scope 的能力表。

    缺键 = 空表（asset scope 的显式空表语义）；读取失败也不静默扩张，一样是空表。
    """
    from pipeline.agent.scopes import load_scope
    return list(load_scope(scope, root).tools)


def build_tool_schemas(scope: str, root: Path | None = None) -> list[dict[str, Any]]:
    """把 scope 白名单翻译成 OpenAI tools 参数。

    tools.json 里出现未注册的名字 = 配置与实现分叉，当场报错而不是静默跳过
    （静默跳过会让护栏看起来还在，实际已经漏了）。
    """
    schemas: list[dict[str, Any]] = []
    for name in tool_names_for_scope(scope, root):
        if name not in TOOL_SCHEMAS:
            raise KeyError(
                f"tools.json 声明了未注册的工具 '{name}'；工具清单不现场发明（Spec §2.5 B3-r6），"
                f"已注册: {sorted(TOOL_SCHEMAS)}"
            )
        fn_schema = {k: v for k, v in TOOL_SCHEMAS[name].items() if k != "side_effect"}
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


def _tool_write_episode_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.episode_dir:
        raise PermissionError("未绑定当期目录，拒绝写入")
    content = args.get("content", "")
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串")
    filename = str(args.get("filename", "")).strip()
    confirmed = bool(args.get("confirmed", False)) or ctx.confirmed
    target = write_episode_file(
        ctx.episode_dir, filename, content, scope=ctx.scope, confirmed=confirmed, root=ctx.root
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
    for path in sorted(library.rglob("*")):
        if len(hits) >= limit:
            break
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in NOTE_SUFFIXES:
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

    return {"query": query, "hits": hits, "truncated": len(hits) >= limit}


_TOOL_IMPLS: dict[str, Callable[[dict[str, Any], ToolContext], Any]] = {
    "read_artifact": _tool_read_artifact,
    "write_episode_file": _tool_write_episode_file,
    "list_episodes": _tool_list_episodes,
    "read_status": _tool_read_status,
    "run_pipeline": _tool_run_pipeline,
    "search_notes": _tool_search_notes,
}


def execute_tool(name: str, args: dict[str, Any] | None, ctx: ToolContext) -> dict[str, Any]:
    """按 scope 白名单执行一个工具，返回可 JSON 化的结果（错误也当数据回喂 LLM）。

    三层闸：① 名字已注册；② 在当前 scope 白名单内；③ 实现层自身边界（路径/确认）。
    """
    if name not in TOOL_SCHEMAS:
        return {"ok": False, "error": f"未注册的工具 '{name}'（工具清单不现场发明）"}
    allowed = tool_names_for_scope(ctx.scope, ctx.root)
    if name not in allowed:
        return {
            "ok": False,
            "error": f"工具 '{name}' 不在 {ctx.scope} scope 白名单内（当前放行: {allowed}）",
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
    """白名单执行器的唯一入口（Spec §2.4, §3.3）：校验 → 回显 → 人类确认后才真跑。

    `confirmed=False` 只返回待执行 argv（REPL 回显用）。`/run` 与 LLM 工具表都
    走这里，避免两处实现分叉。
    执行改用 Popen 双管排水 + 实时透传终端 + 尾环缓冲回喂。
    """
    valid, msg, argv = validate_pipeline_command(command, scope=scope, ep_dir=episode_dir)
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
        }
    if not confirmed:
        return {
            "ok": True,
            "message": "待人类确认",
            "argv": argv,
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "truncated": False,
        }

    t_start = time.time()
    try:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            argv,
            cwd=paths.ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "message": f"执行器未找到: {exc}",
            "argv": argv,
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "truncated": False,
        }

    stdout_buf = _TailBuffer(4096)
    stderr_buf = _TailBuffer(4096)

    def _drain(pipe: Any, buf: _TailBuffer, is_stderr: bool) -> None:
        target_stream = sys.stderr if is_stderr else sys.stdout
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            for chunk in iter(lambda: pipe.read1(4096), b""):
                text = decoder.decode(chunk)
                try:
                    target_stream.write(text)
                    target_stream.flush()
                except Exception:
                    pass
                buf.append(chunk)
            final_text = decoder.decode(b"", final=True)
            if final_text:
                try:
                    target_stream.write(final_text)
                    target_stream.flush()
                except Exception:
                    pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_buf, False), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_buf, True), daemon=True)
    t_out.start()
    t_err.start()

    try:
        retcode = proc.wait()
    except BaseException:
        # Ctrl-C（含其它中断）时子进程必须一起死：否则渲染会继续写 05-final.mp4，
        # 人重跑一次就是两个 ffmpeg 写同一个输出文件。`subprocess.run` 在 PR6
        # 被换成 Popen 时，丢掉了 CPython 在中断时替调用方做的那次 kill。
        try:
            proc.kill()
        except Exception:
            pass
        t_out.join(2)
        t_err.join(2)
        raise
    t_out.join()
    t_err.join()
    duration_s = time.time() - t_start

    stdout_tail, out_trunc = stdout_buf.get_tail()
    stderr_tail, err_trunc = stderr_buf.get_tail()
    truncated = out_trunc or err_trunc

    return {
        "ok": retcode == 0,
        "message": f"退出码 {retcode}",
        "argv": argv,
        "returncode": retcode,
        "duration_s": round(duration_s, 2),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "truncated": truncated,
    }
