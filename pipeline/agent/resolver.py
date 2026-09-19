"""StateResolver：status.inspect_episode 的 scope 化封装与纯函数映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from pipeline.status import EpisodeStatus


@dataclass
class Scene:
    """场景结构体。"""
    name: str = ""
    description: str = ""


def scope_of(status: EpisodeStatus) -> str:
    """产物阶段 → Scope。

    status.py 实际产出 12 种 current_step 字符串（含「02 脚本写作（草稿待定稿）」
    「07 自动质检（未通过）」），测试按 12 种全枚举钉死 (B6)。
    """
    return "creative" if status.current_step.startswith(("01", "02")) else "pipeline"
