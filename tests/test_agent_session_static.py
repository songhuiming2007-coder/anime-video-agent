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


# ---------------------------------------------------------------------------
# TG-1 / TG-2：依赖纯洁与「无 server」
# ---------------------------------------------------------------------------

NEW_MODULES = (
    "pipeline.agent.session_log",
    "pipeline.agent.session",
    "pipeline.agent.protocol",
)

HEAVY_MODULES = (
    "numpy", "torch", "sentence_transformers", "pysubs2", "playwright", "crawl4ai",
)

FORBIDDEN_SOURCES = (
    "socket", "http.server", "socketserver", "start_server", ".bind(", ".listen(", "sqlite3",
)
NEW_FILES = ("session_log.py", "session.py", "protocol.py")


def test_tg1_no_heavy_imports_in_new_modules() -> None:
    """TG-1：干净子进程 import 三个新模块（缺的跳过）后没有重依赖。"""
    import subprocess
    import sys

    probe = (
        "import importlib, json, sys\n"
        f"names = {NEW_MODULES!r}\n"
        "loaded = []\n"
        "for name in names:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        if exc.name not in names:\n"
        "            raise\n"
        "        continue\n"
        "    loaded.append(name)\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in "
        f"{HEAVY_MODULES!r}"
        ")))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert proc.stdout.strip() == "[]", proc.stdout


def test_tg2_no_server_primitives_in_new_modules() -> None:
    """TG-2：新模块源码不含 socket / http.server / socketserver / start_server / bind / listen / sqlite3。"""
    hits: list[str] = []
    for name in NEW_FILES:
        path = PIPELINE / "agent" / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SOURCES:
            if needle in text:
                hits.append(f"{path.name}: {needle}")
    assert not hits, "新模块不许出现 server/DB 原语（Spec 9 §5）：\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# TG-4：观测层不参与状态
# ---------------------------------------------------------------------------


def test_tg4_status_layer_never_reads_session_log() -> None:
    """TG-4：`status.py`、`approvals.py`、`jobs.py` 不出现 `session.jsonl`。"""
    hits = [
        str(path.relative_to(REPO))
        for path in (PIPELINE / "status.py", PIPELINE / "approvals.py", PIPELINE / "jobs.py")
        if "session.jsonl" in path.read_text(encoding="utf-8")
    ]
    assert not hits, "状态推导不许读会话日志（Spec 9 §2.5）：\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# TG-5 / TG-6 / TG-7
# ---------------------------------------------------------------------------


def test_tg5_critical_tools_is_exactly_the_side_effect_set_minus_run_pipeline() -> None:
    """TG-5：字面量 `CRITICAL_TOOLS` == {side_effect 为真的工具} − {run_pipeline}。

    这条断言才是「新增 side_effect 工具忘了登记临界区」（RF-1）的当场红。写成字面量是
    刻意的：如果它由 side_effect 集合算出来，这个比对就恒真（一轮 🔵-8）。
    """
    from pipeline.agent.session import CRITICAL_TOOLS
    from pipeline.agent.tools import TOOL_SCHEMAS

    derived = {
        name for name, schema in TOOL_SCHEMAS.items()
        if schema.get("side_effect", True)
    } - {"run_pipeline"}
    assert set(CRITICAL_TOOLS) == derived
    assert isinstance(CRITICAL_TOOLS, frozenset)


def test_tg6_every_run_tool_loop_call_passes_control() -> None:
    """TG-6：`pipeline/` 下每个 `run_tool_loop(` 调用点都带 `control=` 关键字参数。

    裸循环（`control=None`）只留给测试与兼容用途：「没有人审通道就拒绝运行」的纪律要能
    靠静态扫描守住，不能靠「记得传」。
    """
    import ast

    missing: list[str] = []
    for path in sorted(PIPELINE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "run_tool_loop":
                continue
            if not any(kw.arg == "control" for kw in node.keywords):
                missing.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not missing, "生产调用点必须传 control=（Spec 9 §4.1）：\n" + "\n".join(missing)


def test_tg7_llm_does_not_import_session_or_protocol() -> None:
    """TG-7：`llm.py` 不 import `session` / `protocol`（import 方向固定 session → llm）。"""
    text = (PIPELINE / "agent" / "llm.py").read_text(encoding="utf-8")
    assert "pipeline.agent.session" not in text
    assert "pipeline.agent.protocol" not in text
