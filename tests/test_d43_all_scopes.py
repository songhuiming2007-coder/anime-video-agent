"""D43 / Spec 17（所有模式开放全部工具）的新增验收用例 TA-1 ~ TA-7、TA-3b、TA-6b。

对应 §9「新增」清单；改写类用例散落在原文件（test_agent_tools / test_agent_web 等），
本文件只收纯新增。变异 MUT-A1 ~ A11 的指定杀手见各用例 docstring。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from pipeline.agent import llm
from pipeline.agent.llm import LLMConfig
from pipeline.agent.session import review_tool_call
from pipeline.agent.tools import (
    NEEDS_EPISODE_TOOLS,
    NO_EPISODE_MESSAGE,
    TOOL_SCHEMAS,
    ToolContext,
    _extra_available,
    build_tool_schemas,
    execute_tool,
    tool_names,
    validate_pipeline_command,
)
from tests.test_agent_tools import SPEC_TOOLS, make_agent_root


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def llm_capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    """拦 urlopen：记录请求体，回一条无工具调用的 assistant 回复。"""
    state: dict = {"requests": []}

    def fake_urlopen(request: urllib.request.Request, timeout: int | None = None):
        state["requests"].append(json.loads(request.data.decode("utf-8")))
        reply = {"role": "assistant", "content": "好"}
        return _FakeResponse(json.dumps({"choices": [{"message": reply}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


def _mem_root(tmp_path: Path) -> Path:
    """带 data/library 与证据期的假仓库根（write_memory 的 plan/apply 需要证据期有 01-topic.md）。"""
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    (root / "data" / "library").mkdir(parents=True, exist_ok=True)
    ep = root / "data" / "episodes" / "番A" / "01-x"
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    return root


def _add_args(pattern: str) -> dict:
    return {
        "op": "add",
        "pattern": pattern,
        "evidence": ["番A/01-x"],
        "boundary": "仅适用于测试",
    }


# ---------------------------------------------------------------------------
# TA-1：四个 scope 的请求体工具表逐字节相等（MUT-A1 的杀手；D43-R2 🔵 R2-3 改在请求层断言）
# ---------------------------------------------------------------------------


def test_ta1_request_tools_byte_identical_across_scopes(tmp_path, llm_capture):
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    cfg = LLMConfig(base_url="http://127.0.0.1:9/v1", model="mock", api_key="k")

    bodies = []
    for scope in ("creative", "pipeline", "asset", "idea"):
        llm_capture["requests"].clear()
        outcome = llm.run_tool_loop(
            [{"role": "user", "content": "在吗"}],
            ctx=ToolContext(scope=scope, root=root),
            config=cfg,
        )
        assert outcome["stopped"] == "done"
        assert len(llm_capture["requests"]) == 1
        bodies.append(json.dumps(llm_capture["requests"][0]["tools"], ensure_ascii=False, sort_keys=True))

    assert len(set(bodies)) == 1, "四个 scope 的请求体工具表必须逐字节相等"

    # 名字集合 == 注册集 ∩ 可选依赖可用集（v0.2 🔵-5：缺 extras 的工具被跳过）
    sent_names = {t["function"]["name"] for t in llm_capture["requests"][0]["tools"]}
    expected = {
        n for n, s in TOOL_SCHEMAS.items()
        if not s.get("requires_extra") or _extra_available(str(s["requires_extra"]))
    }
    assert sent_names == expected


# ---------------------------------------------------------------------------
# TA-2：单表分叉检查（MUT-A2 的杀手）——少列一个已注册工具 / 缺失 / 损坏 / 旧四键格式
# ---------------------------------------------------------------------------


def test_ta2_single_table_divergence_fails_loudly(tmp_path):
    full = list(SPEC_TOOLS)

    # 少列一个已注册工具 → 反向检查报错
    root = make_agent_root(tmp_path / "missing-one", "http://127.0.0.1:9/v1", tools=full[:-1])
    with pytest.raises(KeyError, match="未列出已注册的工具"):
        build_tool_schemas(root=root)

    # 缺失 → 空表 → 反向检查报错（fail-closed）
    root2 = make_agent_root(tmp_path / "absent", "http://127.0.0.1:9/v1")
    (root2 / "config" / "agent" / "tools.json").unlink()
    with pytest.raises(KeyError, match="未列出已注册的工具"):
        build_tool_schemas(root=root2)

    # 损坏 → 空表 → 同样报错
    root3 = make_agent_root(tmp_path / "broken", "http://127.0.0.1:9/v1")
    (root3 / "config" / "agent" / "tools.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(KeyError, match="未列出已注册的工具"):
        build_tool_schemas(root=root3)

    # 旧四键格式残留 → 空表 → 同样报错
    root4 = make_agent_root(tmp_path / "legacy", "http://127.0.0.1:9/v1")
    (root4 / "config" / "agent" / "tools.json").write_text(
        json.dumps({"creative": ["read_artifact"]}), encoding="utf-8"
    )
    with pytest.raises(KeyError, match="未列出已注册的工具"):
        build_tool_schemas(root=root4)

    # 多写一个未注册名字 → 正向检查报错（B3-r6 保留）
    root5 = make_agent_root(tmp_path / "extra", "http://127.0.0.1:9/v1", tools=[*full, "rm_rf"])
    with pytest.raises(KeyError, match="未注册的工具"):
        build_tool_schemas(root=root5)


# ---------------------------------------------------------------------------
# TA-3：三个非 creative 模式下 execute_tool 调 web_search 都执行（不打白名单错误）
# TA-3b：review 层不按 scope reject（MUT-A9 的杀手）
# ---------------------------------------------------------------------------


def test_ta3_web_search_executes_in_non_creative_scopes(tmp_path, monkeypatch):
    from pipeline.agent import web

    seen: list[str] = []

    def fake_search(query, limit, *, root=None, **kwargs):
        seen.append(query)
        return {"query": query, "results": [], "provider": "fake"}

    monkeypatch.setattr(web, "search_web", fake_search)
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")

    for scope in ("pipeline", "asset", "idea"):
        out = execute_tool(
            "web_search", {"query": "春物"}, ToolContext(scope=scope, root=root)
        )
        assert out["ok"] is True, (scope, out)
    assert seen == ["春物"] * 3


def test_ta3b_review_layer_has_no_scope_reject(tmp_path):
    root = _mem_root(tmp_path)
    ep = root / "data" / "episodes" / "番A" / "01-x"

    for scope in ("pipeline", "asset", "idea"):
        v = review_tool_call("web_search", {"query": "x"}, ep_dir=ep, scope=scope, root=root)
        assert v.action != "reject", (scope, v)
        v = review_tool_call("write_memory", _add_args("审查层模式"), ep_dir=ep, scope=scope, root=root)
        assert v.action != "reject", (scope, v)

    for scope in ("pipeline", "asset"):
        v = review_tool_call(
            "run_pipeline", {"command": "cloud status"}, ep_dir=ep, scope=scope, root=root
        )
        assert v.action != "reject", (scope, v)


# ---------------------------------------------------------------------------
# TA-5：合表语义（MUT-A4 / MUT-A5 的杀手之一）——任意调用路径下
# ---------------------------------------------------------------------------


def test_ta5_merged_table_semantics():
    ok, msg, _ = validate_pipeline_command("cloud status")
    assert ok, msg

    ok, msg, _ = validate_pipeline_command("cloud exec 'rm -rf /'")
    assert not ok and "cloud exec" in msg

    ok, msg, _ = validate_pipeline_command("tts --force-a")
    assert not ok and "全量覆盖" in msg

    ok, msg, _ = validate_pipeline_command("faces unknown")
    assert not ok and "子命令" in msg

    # 既不在 PIPELINE_MODULES 也不在 ASSET_COMMANDS 的模块仍拒
    ok, msg, _ = validate_pipeline_command("rm -rf /")
    assert not ok and "不在白名单内" in msg


# ---------------------------------------------------------------------------
# TA-6：idea 会话的需期工具——review 层 reject「先建期」、不弹卡、零写入；
#       实现层双保险同一文案（MUT-A6 / MUT-A10 / MUT-A11 的杀手）
# TA-6 write_memory 腿（🟡-2 完整回环）：弹卡 → 人按 y → 真落盘、日志 episode 为 null
# ---------------------------------------------------------------------------


def test_ta6_idea_needs_episode_tools_rejected_before_card(tmp_path):
    root = _mem_root(tmp_path)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

    tool_args = {
        "write_episode_file": {"filename": "01-topic.md", "content": "x"},
        "cover_edit": {"title": "x"},
        "run_pipeline": {"command": "cloud status"},
        "acquire_propose": {"candidates": [{
            "title": "候选", "url": "https://example.com/v/1", "type": "live",
            "source": "s", "why": " TA-6 的理由写够二十个字以防触发提示……",
        }]},
    }
    assert set(tool_args) == set(NEEDS_EPISODE_TOOLS)

    for name, args in tool_args.items():
        # review 层：弹卡之前 reject，统一文案，无 request（不弹卡）
        v = review_tool_call(name, args, ep_dir=None, scope="idea", root=root)
        assert v.action == "reject", name
        assert v.reason == NO_EPISODE_MESSAGE, (name, v.reason)
        assert v.request is None

        # 实现层双保险：直调 execute_tool 拿到同一文案
        out = execute_tool(name, args, ToolContext(scope="idea", episode_dir=None, root=root))
        assert out["ok"] is False, name
        assert NO_EPISODE_MESSAGE in out["error"], (name, out)

    # R2-6：本身非法的命令（faces unknown）在 idea 下拿到的也是统一文案，不是白名单拒因
    v = review_tool_call(
        "run_pipeline", {"command": "faces unknown"}, ep_dir=None, scope="idea", root=root
    )
    assert v.action == "reject"
    assert v.reason == NO_EPISODE_MESSAGE

    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert after == before, "被拒的调用零写入（incoming 候选池不变）"


def test_ta6_idea_write_memory_full_loop(tmp_path):
    """弹卡 → 人按 y → memory.md 真落盘、memory.log.jsonl 新增一行且 episode 为 null。"""
    root = _mem_root(tmp_path)
    args = _add_args("idea 全回环写入的模式")

    v = review_tool_call("write_memory", args, ep_dir=None, scope="idea", root=root)
    assert v.action == "ask" and v.request is not None  # 弹卡

    # 人按 y（confirmed 只由宿主注入）
    out = execute_tool(
        "write_memory", args, ToolContext(scope="idea", episode_dir=None, root=root, confirmed=True)
    )
    assert out["ok"] is True, out
    assert "idea 全回环写入的模式" in (root / "data" / "library" / "memory.md").read_text(encoding="utf-8")
    rows = [
        json.loads(line)
        for line in (root / "data" / "library" / "memory.log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["scope"] == "idea"
    assert rows[-1]["episode"] is None


# ---------------------------------------------------------------------------
# TA-6b：非 creative 直调 apply_op 成功落盘（MUT-A8 的杀手）
# ---------------------------------------------------------------------------


def test_ta6b_apply_op_succeeds_outside_creative(tmp_path):
    from pipeline.agent import memory

    for scope in ("pipeline", "idea"):
        root = _mem_root(tmp_path / scope)
        plan = memory.apply_op(
            "add", _add_args(f"TA-6b {scope} 直调模式"),
            root=root, episode_dir=None, confirmed=True, scope=scope,
        )
        assert f"TA-6b {scope} 直调模式" in plan.result_text


# ---------------------------------------------------------------------------
# TA-7：pipeline 模式下 web_fetch 的 URL 含受限串 → 仍被出网断言拦截（MUT-A7 的杀手，R1 回归守卫）
# ---------------------------------------------------------------------------


def test_ta7_web_fetch_egress_assert_still_holds_in_pipeline(tmp_path):
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    # 合法 web.json（出网断言在配置加载之后，须先让它过配置关；Spec 15 新 schema）
    (root / "config" / "agent" / "web.json").write_text(
        json.dumps({
            "search": {"timeout_s": 20, "providers": [{"name": "exa_mcp"}]},
            "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
        }),
        encoding="utf-8",
    )
    out = execute_tool(
        "web_fetch",
        {"url": "https://example.org/agent.local.json"},
        ToolContext(scope="pipeline", root=root),
    )
    assert out["ok"] is False
    assert "拦截" in out["error"] or "出网" in out["error"]
