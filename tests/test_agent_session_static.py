"""Spec 9 的静态与纯洁性守卫（§7.1「静态与纯洁性」）。

PR1：TG-3（固定轮数上限不再存在于 `pipeline/`）。
PR2：TG-1/TG-2/TG-4/TG-5/TG-6/TG-7（新模块的重依赖、无 server、日志边界、临界区字面量、
调用点必传 control、import 方向）。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "pipeline"


def test_tg3_no_iteration_cap_in_pipeline() -> None:
    """TG-3：`pipeline/` 下不再有 `DEFAULT_MAX_ITERATIONS` / `max_iterations`。

    上限由「人中断 + 本轮判重 + 检查点」取代（Spec 9 §2.3；IS-R1 / D28 用户裁决）。
    留在这里的守卫是：谁把硬上限加回来，谁就得先过这一关。
    """
    hits: list[str] = []
    for path in sorted(PIPELINE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in ("DEFAULT_MAX_ITERATIONS", "max_iterations"):
            if needle in text:
                hits.append(f"{path.relative_to(REPO)}: {needle}")
    assert not hits, "固定轮数上限已删（Spec 9 §2.3）：\n" + "\n".join(hits)
