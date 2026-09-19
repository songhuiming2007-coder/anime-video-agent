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

    target_raw = raw_ep / filename
    target_resolved = (raw_ep / clean_name).resolve()

    # 1. 拦截对 pipeline/ 源码目录的修改
    repo_pipeline = (paths.ROOT / "pipeline").resolve()
    if repo_pipeline in target_resolved.parents or str(target_resolved).startswith(str(repo_pipeline)):
        raise PermissionError("禁止修改 pipeline/ 源码目录文件，触发 Code Freeze 护栏")

    # 2. 拦截父级或兄弟目录越界
    if target_resolved.parent != resolved_ep:
        raise PermissionError(f"目标路径越界：{target_resolved} 不在当期根目录 {resolved_ep} 下")

    # 3. 写 01-topic.md 强制人类确认 (B7)
    if clean_name == "01-topic.md" and not confirmed:
        raise PermissionError("写入 01-topic.md 是关键立项操作，必须获得人类显式确认")

    # 4. 原子落盘
    paths.atomic_write(target_resolved, content)
    return target_resolved


def validate_pipeline_command(
    cmd_tokens: list[str] | str,
    scope: str = "pipeline",
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

    # 1. 拒收 --force / --force-all 标志（§2.4 B1-r10, B4-r18）
    for a in args:
        if a in ("--force", "--force-all") or a.startswith("--force="):
            if module == "cloud" and args and args[0] == "down":
                return (
                    False,
                    "拒绝执行：cloud down --force 会强行销毁正在运行的后台任务！请先运行 cloud status 确认无活跃任务。",
                    [],
                )
            return (
                False,
                f"拒绝执行：禁止在 ava 中使用 {a} 全量覆盖！请使用增量参数：--redo <段号> 或 --apply-patch。",
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
        # 模块参数简单校验
        normalized = ["python", "-m", f"pipeline.{module}"] + args
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

        # 对 cloud run 校验 extra_args
        if module == "cloud" and args[0] == "run":
            # args: run <target> <task> [extra...]
            if len(args) >= 3:
                extra_tokens = args[3:]
                if extra_tokens:
                    try:
                        validate_extra_args(" ".join(extra_tokens))
                    except ValueError as exc:
                        return False, f"cloud run 参数非法: {exc}", []

        normalized = ["python", "-m", f"pipeline.{module}"] + args
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
