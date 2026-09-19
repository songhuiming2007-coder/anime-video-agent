"""Scope 配置与 Prompt 加载器。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import paths


@dataclass
class ScopeConfig:
    scope: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)


def get_scopes_dir(root: Path | None = None) -> Path:
    base = root or paths.ROOT
    return base / "config" / "agent" / "scopes"


def get_tools_json_path(root: Path | None = None) -> Path:
    base = root or paths.ROOT
    return base / "config" / "agent" / "tools.json"


def load_scope(scope: str, root: Path | None = None) -> ScopeConfig:
    """加载指定 scope 的系统提示词与可用工具列表。"""
    scopes_dir = get_scopes_dir(root)
    prompt_file = scopes_dir / f"{scope}.md"
    if prompt_file.exists():
        system_prompt = prompt_file.read_text(encoding="utf-8")
    else:
        system_prompt = f"# {scope.capitalize()} Scope"

    tools_file = get_tools_json_path(root)
    tools: list[str] = []
    if tools_file.exists():
        try:
            tools_data = json.loads(tools_file.read_text(encoding="utf-8"))
            if isinstance(tools_data, dict):
                tools = tools_data.get(scope, [])
        except Exception:
            tools = []

    return ScopeConfig(scope=scope, system_prompt=system_prompt, tools=tools)
