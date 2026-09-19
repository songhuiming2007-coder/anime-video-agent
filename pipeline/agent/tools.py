"""LLM 受控工具注册表与 Pipeline 白名单执行器（Spec §2.4, §2.5, §5 PR1）。
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

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
    "03-audio/manifest.json",
    "03-audio/voice.json",
)


def write_episode_file(
    episode_dir: Path | str,
    filename: str,
    content: str,
    scope: str = "creative",
    confirmed: bool = False,
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
    raw_ep = Path(episode_dir)
    resolved_ep = raw_ep.resolve()
    target_resolved = (raw_ep / clean_name).resolve()

    # 1. 拦截对 pipeline/ 源码目录的修改（优先触发 Code Freeze）
    repo_pipeline = (paths.ROOT / "pipeline").resolve()
    if (
        repo_pipeline in target_resolved.parents
        or str(target_resolved).startswith(str(repo_pipeline))
        or repo_pipeline in resolved_ep.parents
        or resolved_ep == repo_pipeline
    ):
        raise PermissionError("禁止修改 pipeline/ 源码目录文件，触发 Code Freeze 护栏")

    # 2. 纵深防御：期目录必须落在 data/episodes 之下，严禁写入仓库根或系统 /tmp
    episodes_root = (paths.ROOT / "data" / "episodes").resolve()
    if resolved_ep == (paths.ROOT).resolve() or resolved_ep == Path("/tmp").resolve():
        raise PermissionError(f"禁止将仓库根或 /tmp 作为期目录写入: {resolved_ep}")
    if episodes_root.exists() and (resolved_ep == episodes_root or episodes_root not in resolved_ep.parents):
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
    """提取真正的命令行位置参数，跳过旗标及其参数值。"""
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
    """
    text = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        if pattern in text:
            raise PermissionError(f"拦截出网请求：内容包含受限敏感标记 '{pattern}'")


def sys_python() -> str:
    import sys
    return sys.executable
