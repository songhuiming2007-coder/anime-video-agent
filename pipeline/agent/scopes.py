"""Scope 配置与 Prompt 加载器。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline import paths


@dataclass
class ScopeConfig:
    scope: str
    system_prompt: str


def get_scopes_dir(root: Path | None = None) -> Path:
    base = root or paths.ROOT
    return base / "config" / "agent" / "scopes"


def get_tools_json_path(root: Path | None = None) -> Path:
    base = root or paths.ROOT
    return base / "config" / "agent" / "tools.json"


def load_scope(scope: str, root: Path | None = None) -> ScopeConfig:
    """加载指定 scope 的系统提示词（D43 / Spec 17 §3.1：不再读工具表，工具表单表化后由
    `tools.tool_names` 直读 tools.json）。"""
    scopes_dir = get_scopes_dir(root)
    prompt_file = scopes_dir / f"{scope}.md"
    if prompt_file.exists():
        system_prompt = prompt_file.read_text(encoding="utf-8")
    else:
        system_prompt = f"# {scope.capitalize()} Scope"

    return ScopeConfig(scope=scope, system_prompt=system_prompt)
