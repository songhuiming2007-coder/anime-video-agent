"""模型按用途分层与跨档清洗（Spec 7 §7.1 T13–T15、T20）。

出网请求体一律被 `urlopen` 桩截获，不发真请求。golden 在 PR1 改动之前生成：
`tests/golden/llm_request_no_models.json`，含生成时刻的冻结工具表快照。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from pipeline.agent.llm import (
    SCOPE_PURPOSE,
    LLMConfig,
    _reset_warn_latch_for_testing,
    _wire_messages,
    chat_complete,
    load_llm_config,
    run_tool_loop,
)
from pipeline.agent.tools import ToolContext

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "llm_request_no_models.json").read_text(encoding="utf-8")
)
API_KEY = "sk-test-secret-do-not-print"
SCOPES = ("creative", "pipeline", "idea", "asset")


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
    """拦在 `urllib.request.urlopen`：记录每个请求体，按序回放 replies（末条重复）。"""
    state: dict = {"requests": [], "replies": [{"role": "assistant", "content": "好"}]}

    def fake_urlopen(request: urllib.request.Request, timeout: int | None = None):
        state["requests"].append(json.loads(request.data.decode("utf-8")))
        reply = state["replies"][min(len(state["requests"]) - 1, len(state["replies"]) - 1)]
        return _FakeResponse(json.dumps({"choices": [{"message": reply}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


def _make_root(dirpath: Path, *, model: str = "mock-model", models=None) -> Path:
    cfg_dir = dirpath / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {"base_url": "http://127.0.0.1:9/v1", "model": model, "api_key_env": "AVA_TEST_KEY"}
    if models is not None:
        data["models"] = models
    (dirpath / "config" / "agent.json").write_text(json.dumps(data), encoding="utf-8")
    (cfg_dir / "tools.json").write_text(
        json.dumps({"creative": ["read_artifact"], "pipeline": ["read_artifact"], "idea": [], "asset": []}),
        encoding="utf-8",
    )
    return dirpath


def _norm(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _history(scope: str) -> list[dict]:
    return [
        {"role": "system", "content": "常驻层"},
        {"role": "assistant", "content": "上一轮回复", "reasoning_content": "私有思考字段"},
        {"role": "user", "content": f"{scope} 的问话"},
    ]


@pytest.fixture(autouse=True)
def _reset_latch(monkeypatch: pytest.MonkeyPatch):
    _reset_warn_latch_for_testing()
    monkeypatch.setenv("AVA_TEST_KEY", API_KEY)
    yield
    _reset_warn_latch_for_testing()


# ---------------------------------------------------------------------------
# T13：未配置 models 零回归（门禁 1）
# ---------------------------------------------------------------------------


def test_no_models_section_is_byte_identical_to_golden(tmp_path, monkeypatch, llm_capture):
    root = _make_root(tmp_path)
    # 工具表冻结成 golden 里生成时刻的快照：Spec 4/5/6/7 加工具不波及这条回归
    monkeypatch.setattr(
        "pipeline.agent.llm.build_tool_schemas",
        lambda scope, root=None: GOLDEN["frozen_tools"][scope],
    )
    cfg = load_llm_config(root)
    assert cfg is not None
    assert cfg.models == {}
    assert cfg.tiering_active is False
    # LLMConfig 三参数构造仍然可用（既有测试替身依赖它）
    assert LLMConfig(base_url="u", model="m", api_key="k").models == {}

    for scope in SCOPES:
        run_tool_loop(_history(scope), ctx=ToolContext(scope=scope, root=root), config=cfg)
        body = llm_capture["requests"][-1]
        assert _norm(body) == _norm(GOLDEN["requests"][scope]), f"{scope} 请求体与 golden 不等"


# ---------------------------------------------------------------------------
# T14：models 段映射与回落（门禁 2）
# ---------------------------------------------------------------------------


def test_models_tier_mapping_and_fallback(tmp_path, llm_capture):
    # ① 两档都配：idea/creative/asset → reasoning，pipeline → light
    root = _make_root(tmp_path / "both", models={"reasoning": "strong", "light": "cheap"})
    cfg = load_llm_config(root)
    assert cfg is not None and cfg.tiering_active is True
    for scope in SCOPES:
        run_tool_loop(_history(scope), ctx=ToolContext(scope=scope, root=root), config=cfg)
        expected = "cheap" if scope == "pipeline" else "strong"
        assert llm_capture["requests"][-1]["model"] == expected, scope

    # ② 只配 reasoning / light 为空白串：pipeline 回落到 model
    for models in ({"reasoning": "strong"}, {"reasoning": "strong", "light": "  "}):
        root2 = _make_root(tmp_path / f"fallback-{len(models)}", models=models)
        cfg2 = load_llm_config(root2)
        assert cfg2 is not None
        for scope in SCOPES:
            run_tool_loop(_history(scope), ctx=ToolContext(scope=scope, root=root2), config=cfg2)
            expected = "mock-model" if scope == "pipeline" else "strong"
            assert llm_capture["requests"][-1]["model"] == expected, scope
        assert cfg2.model_for("pipeline") == "mock-model"


@pytest.mark.parametrize(
    "bad_models",
    [
        {"reasoning": "a", "reasonning": "b"},   # 未知键
        ["reasoning"],                            # 不是对象
        {"reasoning": 1, "light": 2},             # 值不是字符串
    ],
)
def test_models_invalid_discards_whole_section_with_one_warn(
    tmp_path, capsys, llm_capture, bad_models
):
    root = _make_root(tmp_path, models=bad_models)
    for _ in range(3):
        cfg = load_llm_config(root)
        assert cfg is not None
        assert cfg.models == {}
        assert cfg.tiering_active is False
    assert capsys.readouterr().err.count("[WARN]") == 1

    # 整段作废：4 个 scope 全部用 model
    cfg = load_llm_config(root)
    for scope in SCOPES:
        run_tool_loop(_history(scope), ctx=ToolContext(scope=scope, root=root), config=cfg)
        assert llm_capture["requests"][-1]["model"] == "mock-model", scope


def test_models_section_from_local_overrides_and_shadow_warn(tmp_path, capsys):
    # ④ local 带 models 时取 local
    root = _make_root(tmp_path / "local-wins", models={"reasoning": "strong", "light": "cheap"})
    (root / "config" / "agent.json").write_text(
        json.dumps({"base_url": "http://127.0.0.1:9/v1", "model": "base-model",
                    "api_key_env": "AVA_TEST_KEY", "models": {"reasoning": "x", "light": "y"}}),
        encoding="utf-8",
    )
    local = {"base_url": "http://127.0.0.1:9/v1", "model": "local-model",
             "api_key_env": "AVA_TEST_KEY", "models": {"reasoning": "strong", "light": "cheap"}}
    (root / "config" / "agent.local.json").write_text(json.dumps(local), encoding="utf-8")
    cfg = load_llm_config(root)
    assert cfg is not None
    assert cfg.model == "local-model"
    assert cfg.model_for("reasoning") == "strong"
    assert capsys.readouterr().err.count("[WARN]") == 0

    # ⑤ agent.json 有 models 而 local 没有 → 打一次 WARN
    root2 = _make_root(tmp_path / "shadowed", models={"reasoning": "strong", "light": "cheap"})
    (root2 / "config" / "agent.local.json").write_text(
        json.dumps({"base_url": "http://127.0.0.1:9/v1", "model": "local-model",
                    "api_key_env": "AVA_TEST_KEY"}),
        encoding="utf-8",
    )
    for _ in range(2):
        cfg2 = load_llm_config(root2)
        assert cfg2 is not None and cfg2.models == {}
    err = capsys.readouterr().err
    assert err.count("[WARN]") == 1
    assert "agent.local.json 整文件取代它" in err


# ---------------------------------------------------------------------------
# T15：档位写死，不按内容路由
# ---------------------------------------------------------------------------


def test_tier_is_static_per_scope_not_content_routed(tmp_path, monkeypatch, llm_capture):
    # 前置条件：两档必须配成不同的值，否则内容路由的变异无从发现
    root = _make_root(tmp_path, models={"reasoning": "strong", "light": "cheap"})
    cfg = load_llm_config(root)
    assert cfg is not None and cfg.tiering_active is True

    # ① 冻结映射全等
    assert SCOPE_PURPOSE == {
        "idea": "reasoning",
        "creative": "reasoning",
        "asset": "reasoning",
        "pipeline": "light",
    }

    # ② 50 条随机内容 × 三种历史长度：请求体的 model 恒等于该 scope 的档位
    import random

    rng = random.Random(20260923)
    contents = ["写稿", "我卡在哪", "how do I write a script", "", "选题落盘", "TTS 跑到哪了"]
    contents += [f"随机内容 {rng.random():.6f}" for _ in range(50 - len(contents))]
    prefixes = [
        [],                                                       # 1 条
        [{"role": "system", "content": "常驻层"}],                 # 2 条
        [{"role": "system", "content": "常驻层"},
         {"role": "assistant", "content": "上一轮回复"},
         {"role": "assistant", "content": "再上一轮回复"}],          # 4 条
    ]
    for scope in SCOPES:
        expected = "cheap" if scope == "pipeline" else "strong"
        for content in contents:
            for prefix in prefixes:
                run_tool_loop(
                    [*prefix, {"role": "user", "content": content}],
                    ctx=ToolContext(scope=scope, root=root),
                    config=cfg,
                )
                assert llm_capture["requests"][-1]["model"] == expected, (scope, content, len(prefix))

    # ④ 一次 run_tool_loop 的 3 轮迭代用的是同一个 model
    llm_capture["replies"] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "read_artifact", "arguments": "{\"path\": \"x.md\"}"}}],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c2", "type": "function",
                            "function": {"name": "read_artifact", "arguments": "{\"path\": \"y.md\"}"}}],
        },
        {"role": "assistant", "content": "完"},
    ]
    runs_before = 0
    llm_capture["requests"].clear()
    outcome = run_tool_loop([{"role": "user", "content": "写稿"}],
                            ctx=ToolContext(scope="creative", root=root), config=cfg)
    assert outcome["iterations"] == 3
    rounds = llm_capture["requests"][runs_before:]
    assert len(rounds) == 3
    assert {r["model"] for r in rounds} == {"strong"}

    # ③ spy chat_complete：每次收到的 purpose 等于 SCOPE_PURPOSE[scope]
    seen: list[str | None] = []
    real = chat_complete

    def spy(messages, tools=None, **kwargs):
        seen.append(kwargs.get("purpose"))
        return real(messages, tools, **kwargs)

    monkeypatch.setattr("pipeline.agent.llm.chat_complete", spy)
    for scope in SCOPES:
        run_tool_loop([{"role": "user", "content": "x"}], ctx=ToolContext(scope=scope, root=root), config=cfg)
        assert seen[-1] == SCOPE_PURPOSE[scope]


# ---------------------------------------------------------------------------
# T20：跨档清洗只在分档生效时发生
# ---------------------------------------------------------------------------


def test_cross_tier_history_sanitized_only_when_tiering_active(tmp_path, monkeypatch, llm_capture):
    root = _make_root(tmp_path / "tiered", models={"reasoning": "strong", "light": "cheap"})
    cfg = load_llm_config(root)
    assert cfg is not None
    history = [
        {"role": "assistant", "content": "上一轮", "reasoning_content": "私有思考字段",
         "_ava_tier": "reasoning"},
    ]

    # ① 跨档：只剩白名单字段；同档：原样保留
    chat_complete(history, config=cfg, purpose="light")
    msg = llm_capture["requests"][-1]["messages"][0]
    assert msg == {"role": "assistant", "content": "上一轮"}

    chat_complete(history, config=cfg, purpose="reasoning")
    msg = llm_capture["requests"][-1]["messages"][0]
    assert msg["reasoning_content"] == "私有思考字段"

    # ② 请求体中永远不出现 _ava_tier
    for body in llm_capture["requests"]:
        assert "_ava_tier" not in json.dumps(body, ensure_ascii=False)

    # ③ 两档相同或未配置：返回同一个列表对象，且不打标记
    plain = _make_root(tmp_path / "plain")
    cfg_plain = load_llm_config(plain)
    assert cfg_plain is not None and cfg_plain.tiering_active is False
    unmarked = [{"role": "assistant", "content": "上一轮", "reasoning_content": "私有思考字段"}]
    assert _wire_messages(unmarked, "light") is unmarked
    outcome = run_tool_loop(_history("creative"), ctx=ToolContext(scope="creative", root=plain),
                            config=cfg_plain)
    assert all("_ava_tier" not in m for m in outcome["messages"])
