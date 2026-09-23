"""集成测试：上下文装配器与 CLI/REPL 接入集成（Spec 1 PR2）。

验证工序切换、user 增量注入、去重纪律以及 messages[0] 常驻层前缀稳定性。
"""

from __future__ import annotations

import copy
import json
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.agent import cli
from pipeline.agent.assembly import (
    SessionContextTracker,
    assemble_resident_prompt,
    step_key_of,
)
from pipeline.agent.llm import LLMConfig
from pipeline.status import inspect_episode


@pytest.fixture(autouse=True)
def mock_llm_cfg(monkeypatch):
    monkeypatch.setattr(
        "pipeline.agent.llm.load_llm_config",
        lambda *a, **kw: LLMConfig(base_url="http://mock/v1", model="mock-model", api_key="sk-mock"),
    )


class MockHTTPResponse:
    def __init__(self, content: str):
        self._raw = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _mock_llm_response(text: str = "好的，我知道了。") -> MockHTTPResponse:
    return MockHTTPResponse(text)


def test_first_round_step_docs_injected_as_user_msg_not_in_messages_zero(tmp_path: Path):
    """首轮工序层文档以独立 user 消息注入，严禁拼入 messages[0]（Spec 1 §2.1, §2.2, M1）。"""
    ep = tmp_path / "01-first-round"
    ep.mkdir()
    # 空期目录 -> 01 选题
    status = inspect_episode(ep)
    assert status.current_step.startswith("01")

    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt("pipeline", root=tmp_path).content
    messages = []

    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("选题回复")):
        cli._dispatch_agent_turn(
            "我想做一期罪恶王冠",
            messages,
            ep,
            "pipeline",
            status,
            tracker=tracker,
        )

    # messages 结构：
    # [0] system prompt (仅 resident + status_card，无工序文档)
    # [1] user injection ([系统提示更新] 01 选题 SOP)
    # [2] user turn input ("我想做一期罪恶王冠")
    # [3] assistant reply
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert "Runbook: 01" not in messages[0]["content"]
    assert "docs/runbook/01-topic.md" not in messages[0]["content"]

    assert messages[1]["role"] == "user"
    assert "[系统提示更新]" in messages[1]["content"]
    assert "01 选题" in messages[1]["content"] or "01-topic.md" in messages[1]["content"]

    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "我想做一期罪恶王冠"

    assert messages[3]["role"] == "assistant"
    assert "docs/runbook/01-topic.md" in tracker.injected_paths
    assert tracker.active_step_key == "01"


def test_step_switch_incremental_injection_and_deduplication(tmp_path: Path):
    """跨轮对话工序切换精准增量注入，同工序不重复注入，已注入文档去重（Spec 1 §2.2, §5.2）。"""
    ep = tmp_path / "02-step-switch"
    ep.mkdir()

    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt("creative").content
    messages = []

    # --- 轮次 1：01 选题工序 ---
    status_1 = inspect_episode(ep)
    assert status_1.current_step.startswith("01")

    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("选题建议已提供")):
        cli._dispatch_agent_turn(
            "帮忙看看选题",
            messages,
            ep,
            "creative",
            status_1,
            tracker=tracker,
        )

    assert len(messages) == 4
    assert messages[1]["role"] == "user"
    assert "[系统提示更新]" in messages[1]["content"]
    user_injections_count = sum(1 for m in messages if "[系统提示更新]" in str(m.get("content", "")))
    assert user_injections_count == 1
    assert "docs/runbook/01-topic.md" in tracker.injected_paths

    # --- 轮次 2：依然是 01 选题工序（无切换）---
    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("选题继续")):
        cli._dispatch_agent_turn(
            "那我们就定这个选题",
            messages,
            ep,
            "creative",
            status_1,
            tracker=tracker,
        )

    # 不应追加新的 [系统提示更新] 注入
    user_injections_count_r2 = sum(1 for m in messages if "[系统提示更新]" in str(m.get("content", "")))
    assert user_injections_count_r2 == 1
    # messages 增加了一轮 user("那我们就定这个选题") + assistant("选题继续")
    assert len(messages) == 6

    # --- 轮次 3：工序切换至 02 脚本写作 ---
    # 写入 01-topic.md，使工序推导进入 02
    (ep / "01-topic.md").write_text(
        "---\n番: 罪恶王冠\n类型: 剧情回顾\n---\n",
        encoding="utf-8",
    )
    status_2 = inspect_episode(ep)
    assert status_2.current_step.startswith("02")

    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("脚本草稿已生成")):
        cli._dispatch_agent_turn(
            "开始写脚本",
            messages,
            ep,
            "creative",
            status_2,
            tracker=tracker,
        )

    # 检测到工序切换，追加了针对 02 的 [系统提示更新]
    user_injections_count_r3 = sum(1 for m in messages if "[系统提示更新]" in str(m.get("content", "")))
    assert user_injections_count_r3 == 2

    # creative 模式 02 工序包含 02-script.md 与 skills/write-script/SKILL.md
    assert "docs/runbook/02-script.md" in tracker.injected_paths
    assert "skills/write-script/SKILL.md" in tracker.injected_paths
    assert tracker.active_step_key == "02"

    # --- 轮次 4：模拟工序回退至 01（已注入文档必须去重）---
    (ep / "01-topic.md").unlink()
    status_3 = inspect_episode(ep)
    assert status_3.current_step.startswith("01")

    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("回退处理")):
        cli._dispatch_agent_turn(
            "重新考虑选题",
            messages,
            ep,
            "creative",
            status_3,
            tracker=tracker,
        )

    # 因为 01-topic.md 已在 tracker.injected_paths 中，不应再次追加注入
    user_injections_count_r4 = sum(1 for m in messages if "[系统提示更新]" in str(m.get("content", "")))
    assert user_injections_count_r4 == 2


def test_resident_prompt_prefix_byte_level_identical_across_turns(tmp_path: Path):
    """跨轮对话 messages[0] 的常驻层前缀字节级恒定，保证 Prompt Cache（Spec 1 §2.1, §3.5）。"""
    ep = tmp_path / "03-prefix-stable"
    ep.mkdir()

    tracker = SessionContextTracker()
    resident = assemble_resident_prompt("pipeline")
    tracker.resident_prompt = resident.content
    messages = []

    # 轮次 1
    status_1 = inspect_episode(ep)
    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("r1")):
        cli._dispatch_agent_turn("hello 1", messages, ep, "pipeline", status_1, tracker=tracker)

    sys_prompt_1 = messages[0]["content"]
    assert sys_prompt_1.startswith(tracker.resident_prompt)

    # 改变期目录产生新的 status_card
    (ep / "01-topic.md").write_text("---\n番: 测试\n---\n", encoding="utf-8")
    status_2 = inspect_episode(ep)

    # 轮次 2
    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("r2")):
        cli._dispatch_agent_turn("hello 2", messages, ep, "pipeline", status_2, tracker=tracker)

    sys_prompt_2 = messages[0]["content"]
    assert sys_prompt_2.startswith(tracker.resident_prompt)

    # 两轮的常驻层前缀严格字节级一致
    assert sys_prompt_1[: len(tracker.resident_prompt)] == sys_prompt_2[: len(tracker.resident_prompt)]


def test_idea_scope_zero_step_injection(tmp_path: Path):
    """idea scope（无期目录）严禁注入工序文档（Spec 1 §3.4, §5.2）。"""
    tracker = SessionContextTracker()
    tracker.resident_prompt = assemble_resident_prompt("idea").content
    messages = []

    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("idea 回复")):
        cli._dispatch_agent_turn(
            "发散一些动漫选题",
            messages,
            None,
            "idea",
            None,
            tracker=tracker,
        )

    # 无工序注入消息，仅 system [0] + user [1] + assistant [2]
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "发散一些动漫选题"
    assert messages[2]["role"] == "assistant"
    assert len(tracker.injected_paths) == 0


def test_scope_hot_switch_updates_resident_prompt(tmp_path: Path):
    """Scope 热切换时正确重组装常驻层，边界段不冻结（Review 🔴-1 修复）。"""
    ep = tmp_path / "04-scope-switch"
    ep.mkdir()

    tracker = SessionContextTracker()
    messages = []
    status = inspect_episode(ep)

    # 轮 1: creative scope
    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("r1")):
        cli._dispatch_agent_turn("写稿问题", messages, ep, "creative", status, tracker=tracker)

    assert "Creative Scope" in messages[0]["content"] or "创意" in messages[0]["content"]
    assert tracker.active_scope == "creative"

    # 轮 2: 热推导或手动切换至 pipeline scope
    with patch.object(urllib.request, "urlopen", return_value=_mock_llm_response("r2")):
        cli._dispatch_agent_turn("流水线问题", messages, ep, "pipeline", status, tracker=tracker)

    assert "Pipeline Scope" in messages[0]["content"] or "流水线" in messages[0]["content"]
    assert "Creative Scope" not in messages[0]["content"]
    assert tracker.active_scope == "pipeline"


def test_dispatch_turn_raises_value_error_without_tracker(tmp_path: Path):
    """验证 _dispatch_agent_turn 严禁轮内懒创建，缺失 tracker 必抛 ValueError（Spec 1 M4）。"""
    ep = tmp_path / "05-no-tracker"
    ep.mkdir()
    status = inspect_episode(ep)
    with pytest.raises(ValueError, match="严禁在 _dispatch_agent_turn 轮内懒创建"):
        cli._dispatch_agent_turn("hello", [], ep, "creative", status, tracker=None)
