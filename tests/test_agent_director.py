"""AI-Native Director REPL 与状态卡测试（Spec §1, §2, §5, §6 PR5）。

涵盖：
- classify_input 纯函数分类器（M1, M2, M18）
- status_card 纯函数组装与受限出网清洗（M5, M6）
- director.md 规格与文档不变量（M12）
- 显式降级模式（M7）
- scope 热推导与动态切换（M11）
- 全部既有快捷键回归（M13）
- 聚焦模式子循环独立性与消息历史不污染（M19）
- 异常捕获与消息回滚（LLMError, PermissionError）
"""

import copy
import json
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from pipeline import paths
from pipeline.agent import cli
from pipeline.agent.cli import (
    assemble_system_prompt,
    classify_input,
    run_agent_loop,
    run_creative_loop,
    run_repl,
)
from pipeline.agent.scopes import load_scope
from pipeline.agent.status_card import build_status_card
from pipeline.agent.tools import (
    RESTRICTED_EGRESS_PATTERNS,
    assert_egress_boundary,
)
from pipeline.status import EpisodeStatus, inspect_episode
from tests.test_agent_tools import make_agent_root, mock_llm_server


@contextmanager
def patch_inputs(values: list[str]):
    """把 builtins.input 依次喂成给定值（用尽即 EOFError）。"""
    with patch("builtins.input", side_effect=[*values, EOFError()]):
        yield


# ===========================================================================
# 1. classify_input 分类器测试（M1, M2, M18 前置）
# ===========================================================================


def test_classify_input_slash_shortcuts():
    """以 / 开头的已知快捷键一律分类为 shortcut。"""
    for cmd in ["/status", "/board", "/voice", "/patch", "/asset", "/pipeline",
                "/chat", "/script", "/run tts", "/help", "/quit", "/exit"]:
        assert classify_input(cmd) == "shortcut"


def test_classify_input_unknown_slash_is_shortcut():
    """未知 / 命令依然是 shortcut，由快捷键路由保留零 token 报错 (Spec §1.1)。"""
    for cmd in ["/statsu", "/unknown", "//", "/foo/bar", "/123"]:
        assert classify_input(cmd) == "shortcut"


def test_classify_input_natural_language_is_chat():
    """非 / 开头的输入一律为 chat，不加裸命令识别 (Spec §1.1)。"""
    for phrase in [
        "帮我看下当前排片缺口",
        "今天状态怎么样",
        "run tts",  # 裸命令不识别，走 chat
        "status",
        "check script",
        "你好，总监",
    ]:
        assert classify_input(phrase) == "chat"


def test_classify_input_edge_cases():
    """边界情况：空串、首字符空格、内含斜杠。"""
    assert classify_input(" hello /world") == "chat"
    assert classify_input("data/episodes") == "chat"
    assert classify_input("") == "chat"


# ===========================================================================
# 2. M1, M2, M18: REPL 路由分流与零 Token 保证
# ===========================================================================


def test_m1_slash_inputs_zero_llm_calls(tmp_path: Path, monkeypatch):
    """M1: /status /run … 等以 / 开头的输入零 LLM 调用 (Spec §5.1 M1)。"""
    ep = tmp_path / "data" / "episodes" / "01-m1"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)

    mock_tool_loop = MagicMock()
    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", mock_tool_loop)

    with patch_inputs(["/status", "/board", "/help", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    assert mock_tool_loop.call_count == 0


def test_m2_natural_language_enters_run_tool_loop_and_appends_messages(tmp_path: Path, monkeypatch):
    """M2: 自然语言输入进入 run_tool_loop 且 messages 追加 (Spec §5.1 M2)。"""
    ep = tmp_path / "data" / "episodes" / "01-m2"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)

    captured_turns = []

    def fake_dispatch(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        captured_turns.append({"line": line, "scope": scope, "msgs_len": len(messages)})
        messages.append({"role": "user", "content": line})
        messages.append({"role": "assistant", "content": "收到，我是总监"})
        return {"stopped": "done", "messages": messages, "final": {"content": "收到，我是总监"}}

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", fake_dispatch)

    with patch_inputs(["分析一下当前状态", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    assert len(captured_turns) == 1
    assert captured_turns[0]["line"] == "分析一下当前状态"
    assert captured_turns[0]["scope"] == "creative"


def test_m18_unknown_slash_command_zero_llm_calls_and_error(tmp_path: Path, monkeypatch, capsys):
    """M18: 未知 /xxx 输入零 LLM 调用、报「未知命令」(Spec §5.1 M18)。"""
    ep = tmp_path / "data" / "episodes" / "01-m18"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)

    mock_dispatch = MagicMock()
    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", mock_dispatch)

    with patch_inputs(["/statsu", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    assert mock_dispatch.call_count == 0
    out = capsys.readouterr().out
    assert "[ERROR] 未知命令: '/statsu'" in out


# ===========================================================================
# 3. M5, M6: 状态卡纯函数与脱敏清洗
# ===========================================================================


def test_status_card_basic_structure(tmp_path: Path):
    """状态卡包含期名、工序、阻塞状态、scope、人时、推荐命令、产物清单与提示。"""
    ep = tmp_path / "01-card-test"
    ep.mkdir()
    (ep / "01-topic.md").write_text("# Topic\n", encoding="utf-8")

    card = build_status_card(ep)
    assert "[状态卡]" in card
    assert "期名: 01-card-test" in card
    assert "工序:" in card
    assert "阻塞: 否" in card
    assert "scope: creative" in card
    assert "人时: 0.0m" in card
    assert "推荐命令:" in card
    assert "产物:" in card
    assert "提示: 无" in card


def test_status_card_artifact_checklist_symbols(tmp_path: Path):
    """产物存在性清单仅显示阶段名 + ✓/✗，不含文件路径或内容。"""
    ep = tmp_path / "01-card-arts"
    ep.mkdir()
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.draft.md").write_text("# Draft", encoding="utf-8")

    card = build_status_card(ep)
    assert "选题 ✓" in card
    assert "草稿 ✓" in card
    assert "定稿 ✗" in card
    assert "配音产物 ✗" in card
    assert "排片 ✗" in card
    assert "审片 ✗" in card
    assert "成片 ✗" in card
    assert "01-topic.md" not in card
    assert "02-script.draft.md" not in card


def test_status_card_blocked_state(tmp_path: Path):
    """阻塞状态及其原因格式化正确。"""
    ep = tmp_path / "01-blocked"
    ep.mkdir()  # 缺 01-topic.md -> 阻塞

    card = build_status_card(ep)
    assert "阻塞: 是（缺少 01-topic.md 选题配置）" in card


def test_status_card_human_time_sum(tmp_path: Path):
    """人时累计能正确累加 human_time.json 内的所有段。"""
    ep = tmp_path / "01-ht"
    ep.mkdir()
    (ep / "human_time.json").write_text(json.dumps([
        {"stop": "02.5", "minutes": 10.5},
        {"stop": "03.5", "minutes": 5.0},
    ]), encoding="utf-8")

    card = build_status_card(ep)
    assert "人时: 15.5m" in card


def test_status_card_human_time_corrupted_resilient(tmp_path: Path):
    """损坏的 human_time.json 安全降级为 0.0m，不崩溃。"""
    ep = tmp_path / "01-bad-ht"
    ep.mkdir()
    (ep / "human_time.json").write_text("{bad json", encoding="utf-8")

    card = build_status_card(ep)
    assert "人时: 0.0m" in card


def test_m6_status_card_length_under_400_chars(tmp_path: Path):
    """M6: 状态卡 ≤ 400 字符（Spec §5.1 M6）。"""
    ep = tmp_path / "data" / "episodes" / "2026-08-10-罪恶王冠-楪祈人物志-一"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.md").write_text("# Script", encoding="utf-8")
    (ep / "02-diff.patch").write_text("diff", encoding="utf-8")
    (ep / "04-clips.json").write_text(json.dumps({"total_duration": 300.0}), encoding="utf-8")

    card = build_status_card(ep)
    assert len(card) <= 400, f"状态卡过长: {len(card)} 字符\n{card}"


def test_m6_nested_episode_strips_absolute_path_prefix(tmp_path: Path):
    """M6: 嵌套期（*/子期）下 next_command 不重复出现期目录绝对路径 (Spec §5.1 M6)。"""
    nested_ep = tmp_path / "data" / "episodes" / "EGOIST-传奇企划志-V2" / "03-终局葬礼"
    nested_ep.mkdir(parents=True)
    (nested_ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    status = inspect_episode(nested_ep)
    assert str(nested_ep) in (status.next_command or "")

    card = build_status_card(nested_ep, status)
    assert len(card) <= 400
    assert "推荐命令: python -m pipeline.check_script 02-script.md" in card
    # 绝对路径前缀不得留在推荐命令中
    assert str(nested_ep) not in card


def test_m5_status_card_sanitizes_dirty_advisory_manifest(tmp_path: Path):
    """M5: 状态卡对合成脏 advisory（含 manifest.json）替换为 [已脱敏] (Spec §5.1 M5)。"""
    ep = tmp_path / "01-dirty-mf"
    ep.mkdir()

    status = inspect_episode(ep)
    status.advisories = ["待处理：请查看 03-audio/manifest.json 文件"]

    card = build_status_card(ep, status)
    assert "[已脱敏]" in card
    assert "03-audio/manifest.json" not in card


def test_m5_status_card_sanitizes_dirty_advisory_voice_json(tmp_path: Path):
    """M5: 状态卡对合成脏 advisory（含 voice.json）替换为 [已脱敏] (Spec §5.1 M5)。"""
    ep = tmp_path / "01-dirty-vj"
    ep.mkdir()

    status = inspect_episode(ep)
    status.advisories = ["参考 03-audio/voice.json 数据"]

    card = build_status_card(ep, status)
    assert "[已脱敏]" in card
    assert "03-audio/voice.json" not in card


def test_m5_status_card_sanitizes_casefold_variations(tmp_path: Path):
    """M5: 大小写变体（03-AUDIO/MANIFEST.JSON 等）一律被 casefold 脱敏 (Spec §5.1 M5)。"""
    ep = tmp_path / "01-dirty-case"
    ep.mkdir()

    status = inspect_episode(ep)
    status.advisories = [
        "Check 03-AUDIO/MANIFEST.JSON now",
        "Found CLOUD.LOCAL.JSON in root",
    ]

    card = build_status_card(ep, status)
    assert "03-AUDIO/MANIFEST.JSON" not in card
    assert "CLOUD.LOCAL.JSON" not in card
    assert card.count("[已脱敏]") == 2


def test_m5_status_card_sanitizes_episode_name_with_restricted_patterns(tmp_path: Path):
    """M5: 期名自身若含有受限模式串（整串清洗兜底），整卡依然脱敏 (Spec §1.3 纪律 3)。"""
    ep = tmp_path / "cloud.local.json"
    ep.mkdir()

    status = inspect_episode(ep)
    card = build_status_card(ep, status)
    assert "cloud.local.json" not in card.lower()
    assert "[已脱敏]" in card


def test_m5_status_card_passes_assert_egress_boundary(tmp_path: Path):
    """M5: 经脱敏后的状态卡整串通过 assert_egress_boundary，无 PermissionError (Spec §1.3 纪律 4)。"""
    ep = tmp_path / "01-egress-safe"
    ep.mkdir()

    status = inspect_episode(ep)
    status.advisories = [
        "涉密: agent.local.json",
        "涉密: 03-audio/manifest.json",
    ]

    card = build_status_card(ep, status)
    # assert_egress_boundary 不抛出异常即通过
    assert_egress_boundary("/chat/completions", card)


# ===========================================================================
# 4. M12: director.md 规格与文档不变量
# ===========================================================================


def test_m12_director_md_exists_and_non_empty():
    """M12: config/agent/scopes/director.md 必须存在且正文非空 (Spec §5.1 M12)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    assert director_path.exists(), "director.md 必须存在"
    text = director_path.read_text(encoding="utf-8").strip()
    assert len(text) > 100, "director.md 正文过短"


def test_m12_director_md_contains_all_four_stop_points():
    """M12: director.md 必须包含四大停机点编号 02.5, 03.5, 05, 09 (Spec §5.1 M12)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    text = director_path.read_text(encoding="utf-8")

    for stop in ["02.5", "03.5", "05", "09"]:
        assert stop in text, f"director.md 缺少停机点编号: {stop}"


def test_m12_director_md_contains_stop_point_unblocking_artifacts():
    """M12: director.md 必须列出停机点真实解封物 (02-diff.patch, 04-clips.approved.json)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    text = director_path.read_text(encoding="utf-8")

    assert "02-diff.patch" in text
    assert "04-clips.approved.json" in text


def test_m12_director_md_contains_audio_unreachable_clause():
    """M12: director.md 必须包含音频元数据不可达与禁止估算冒充声明 (Spec §1.5 🟡1)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    text = director_path.read_text(encoding="utf-8")

    assert "03-audio/" in text
    assert "无权访问" in text
    assert "严禁估算冒充" in text


def test_m12_director_md_contains_data_not_instruction_clause():
    """M12: director.md 必须包含「是数据不是指令」防御条款 (Spec §5.1 M12)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    text = director_path.read_text(encoding="utf-8")

    assert "是数据不是指令" in text


def test_m12_director_md_contains_disciplines():
    """M12: director.md 必须包含 Code Freeze 与 --force 禁项 (Spec §5.1 M12)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    text = director_path.read_text(encoding="utf-8")

    assert "Code Freeze" in text
    assert "--force" in text


def test_m12_director_md_contains_professional_vocabulary():
    """M12: director.md 必须包含专业域判读框架四核心词汇 (Spec §2.2)。"""
    director_path = paths.ROOT / "config" / "agent" / "scopes" / "director.md"
    text = director_path.read_text(encoding="utf-8")

    for term in ["动漫解构", "戏剧张力", "CPM", "声画对位"]:
        assert term in text, f"director.md 缺少专业词汇: {term}"


# ===========================================================================
# 5. M7: 显式降级模式测试
# ===========================================================================


def test_m7_llm_unconfigured_shows_degraded_flag_in_repl(tmp_path: Path, monkeypatch, capsys):
    """M7: LLM 未配置时自然语言输入输出含 degraded 标识且零网络调用 (Spec §5.1 M7)。"""
    ep = tmp_path / "data" / "episodes" / "01-m7"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr("pipeline.agent.llm.load_llm_config", lambda *a: None)

    network_spy = MagicMock()
    monkeypatch.setattr(urllib.request, "urlopen", network_spy)

    with patch_inputs(["帮我梳理一下下一步", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    assert network_spy.call_count == 0
    out = capsys.readouterr().out
    assert "降级模式" in out
    assert "LLM 不可用" in out


def test_m7_llm_unconfigured_repl_remains_interactive(tmp_path: Path, monkeypatch, capsys):
    """M7: 降级后 REPL 不退出，快捷键照常可用 (Spec §1.4)。"""
    ep = tmp_path / "data" / "episodes" / "01-m7-interactive"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr("pipeline.agent.llm.load_llm_config", lambda *a: None)

    with patch_inputs(["自然语言输入", "/status", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "降级模式" in out
    assert "02 脚本写作" in out


def test_m7_creative_loop_degrades_and_exits(tmp_path: Path, monkeypatch, capsys):
    """M7: /chat 聚焦模式在缺失配置时打印降级并退出，保持旧行为 (Spec §1.1)。"""
    ep = tmp_path / "data" / "episodes" / "01-m7-chat"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr("pipeline.agent.llm.load_llm_config", lambda *a: None)

    assert cli.run_agent_loop(ep, scope_mode="creative", root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "降级模式" in out


# ===========================================================================
# 6. M11: Scope 热推导与动态切换
# ===========================================================================


def test_m11_scope_hot_derivation_from_creative_to_pipeline(tmp_path: Path, monkeypatch):
    """M11: scope 由 creative 热切到 pipeline 后，write_episode_file 从工具表消失 (Spec §5.1 M11)。"""
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-hot"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.md").write_text("# Script", encoding="utf-8")

    # 此时在 02.5 人审改稿，scope 为 creative
    status1 = inspect_episode(ep)
    assert status1.current_step.startswith("02.5")
    from pipeline.agent.resolver import scope_of
    assert scope_of(status1) == "creative"

    sent_tools_per_turn = []

    # 模拟两轮对话：第一轮 creative 后产生 02-diff.patch 进入 03；第二轮自动变 pipeline
    def fake_dispatch_hot(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        from pipeline.agent.tools import tool_names_for_scope
        sent_tools_per_turn.append(tool_names_for_scope(scope, root=root))
        (ep / "02-diff.patch").write_text("diff", encoding="utf-8")
        return {"stopped": "done", "messages": messages, "final": {"content": "ok"}}

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", fake_dispatch_hot)

    with patch_inputs(["第一轮提问", "第二轮提问", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    assert len(sent_tools_per_turn) == 2
    # 第一轮 creative 包含 write_episode_file
    assert "write_episode_file" in sent_tools_per_turn[0]
    # 第二轮 pipeline 不含 write_episode_file，但包含 run_pipeline
    assert "write_episode_file" not in sent_tools_per_turn[1]
    assert "run_pipeline" in sent_tools_per_turn[1]


def test_scope_asset_manual_override_and_return(tmp_path: Path, monkeypatch, capsys):
    """/asset 切换至 asset scope，/pipeline 切回自动推导。"""
    ep = tmp_path / "data" / "episodes" / "01-asset-test"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    scopes_seen = []

    def fake_dispatch(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        scopes_seen.append(scope)
        return {"stopped": "done", "messages": messages, "final": {"content": "ok"}}

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", fake_dispatch)

    with patch_inputs([
        "问一句",        # creative
        "/asset",
        "再问一句",      # asset
        "/pipeline",
        "第三次提问",    # creative
        "/quit",
    ]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    assert scopes_seen == ["creative", "asset", "creative"]


# ===========================================================================
# 7. M13: 全部既有快捷键回归测试
# ===========================================================================


def test_m13_all_shortcuts_functional(tmp_path: Path, monkeypatch, capsys):
    """M13: 全部既有快捷键逐条回归 (Spec §5.1 M13)。"""
    ep = tmp_path / "data" / "episodes" / "01-shortcuts"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setattr(paths, "ROOT", tmp_path)

    called_routes = []
    monkeypatch.setattr("pipeline.agent.cli.run_voice_session", lambda ep: called_routes.append("voice") or 0)

    with patch_inputs([
        "/help",
        "/status",
        "/board",
        "/patch",
        "/voice",
        "/asset",
        "/pipeline",
        "/run nonexistent_module",  # 触发拒绝
        "/quit",
    ]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "支持的命令路由:" in out
    assert "02 脚本写作" in out
    assert "anime-video-agent-ava 统一制片工作台" in out
    assert "进入临时补料模式" in out
    assert "已进入 asset scope" in out
    assert "已切回自动推导工序模式" in out
    assert "[REJECT]" in out  # /run 拒绝
    assert "voice" in called_routes


def test_m13_run_empty_command_error(tmp_path: Path, monkeypatch, capsys):
    """/run 未传命令时给出明确指引。"""
    ep = tmp_path / "data" / "episodes" / "01-run-err"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    with patch_inputs(["/run", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "[ERROR] /run 需要指定命令" in out


def test_m13_run_confirm_cancel(tmp_path: Path, monkeypatch, capsys):
    """/run 在确认卡按 N 时取消执行。"""
    ep = tmp_path / "data" / "episodes" / "01-run-cancel"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    with patch_inputs(["/run check_script 02-script.md", "n", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "待执行:" in out
    assert "[CANCEL] 已取消执行" in out


# ===========================================================================
# 8. M19: 聚焦模式子循环独立性与消息不污染
# ===========================================================================


def test_m19_chat_subloop_does_not_pollute_main_messages(tmp_path: Path, monkeypatch):
    """M19: /chat 进入-退出后，主会话 messages 与进入前逐字相等 (Spec §5.1 M19)。"""
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-m19"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
    turn_counter = 0
    main_history_snapshots = []

    def fake_dispatch(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        nonlocal turn_counter
        turn_counter += 1
        messages.append({"role": "user", "content": line})
        messages.append({"role": "assistant", "content": f"回复-{turn_counter}"})
        return {"stopped": "done", "messages": messages, "final": {"content": f"回复-{turn_counter}"}}

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", fake_dispatch)

    # 流程：
    # 1. 主会话说一句 -> main_messages 有 1 轮
    # 2. 敲 /chat 进入子循环，在子循环说一句，然后 /quit 退出子循环
    # 3. 回到主会话，main_messages 应该和步骤 1 结束时逐字相同
    def interactive_inputs():
        yield "主会话第 1 句"
        yield "/chat"
        yield "子循环发散选题"
        yield "/quit"
        yield "主会话第 2 句"
        yield "/quit"

    gen = interactive_inputs()
    monkeypatch.setattr("builtins.input", lambda *a: next(gen))

    # 包装 _run_repl_body 观察 main_messages
    orig_dispatch = cli._dispatch_agent_turn

    def observing_dispatch(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        res = fake_dispatch(line, messages, ep_dir, scope, status, extra_prompt, root, tracker)
        if extra_prompt == "":  # 仅在非 script / 主会话层面记录快照
            main_history_snapshots.append(copy.deepcopy(messages))
        return res

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", observing_dispatch)

    assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    # 观察记录中：
    # snapshot 0: 主会话第 1 句
    # snapshot 1: 子循环发散选题（sub_messages）
    # snapshot 2: 主会话第 2 句（应包含第 1 句，绝不含「子循环发散选题」）
    user_contents_turn2 = [m["content"] for m in main_history_snapshots[2] if m["role"] == "user"]
    assert user_contents_turn2 == ["主会话第 1 句", "主会话第 2 句"]
    assert "子循环发散选题" not in user_contents_turn2


def test_m19_script_subloop_with_focus_prompt(tmp_path: Path, monkeypatch):
    """M19: /script 注入聚焦提示且子循环消息独立 (Spec §1.1)。"""
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-script-focus"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
    prompts_seen = []

    def fake_dispatch(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        prompts_seen.append(extra_prompt)
        return {"stopped": "done", "messages": messages, "final": {"content": "ok"}}

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", fake_dispatch)

    with patch_inputs(["/script", "开始写稿", "/quit", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    assert any("本轮聚焦写稿" in p for p in prompts_seen)


# ===========================================================================
# 9. 系统提示组装、异常捕获与消息回滚
# ===========================================================================


def test_assemble_system_prompt_structure(tmp_path: Path):
    """系统提示词由 director.md + scope 边界 + 状态卡三段拼接 (Spec §1.3)。"""
    ep = tmp_path / "01-prompt-struct"
    ep.mkdir()
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    status = inspect_episode(ep)
    assembled = assemble_system_prompt(ep, "creative", status, extra_prompt="聚焦提示")

    assert "Director Persona" in assembled
    assert "Creative Scope System Prompt" in assembled
    assert "聚焦提示" in assembled
    assert "[状态卡]" in assembled
    assert assembled.count("\n\n---\n\n") == 3  # Spec 1 收编后含 AGENTS.md，由 2 处节区分隔升为 3 处


def test_system_prompt_replaced_not_appended_across_turns(tmp_path: Path, monkeypatch):
    """每轮对话 messages[0] 整段替换，不追加 (Spec §1.3 纪律 2)。"""
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-replace-sys"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    messages_after_turns = []

    def fake_dispatch(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None):
        # 真实调用 assemble_system_prompt 检验 messages[0]
        sys_content = assemble_system_prompt(ep_dir, scope, status, extra_prompt="", root=root)
        if not messages:
            messages.append({"role": "system", "content": sys_content})
        else:
            messages[0] = {"role": "system", "content": sys_content}
        messages.append({"role": "user", "content": line})
        messages_after_turns.append(copy.deepcopy(messages))
        return {"stopped": "done", "messages": messages, "final": {"content": "ok"}}

    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", fake_dispatch)

    with patch_inputs(["轮次 1", "轮次 2", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    assert len(messages_after_turns) == 2
    # 两轮后 messages 列表中 system 消息依然只有 1 条
    sys_msgs = [m for m in messages_after_turns[1] if m["role"] == "system"]
    assert len(sys_msgs) == 1


def test_repl_handles_permission_error_and_rolls_back_user_message(tmp_path: Path, monkeypatch, capsys):
    """出网拦截 (PermissionError) 时不崩溃，回滚用户消息，提示用户改问 (Spec §1.1)。"""
    monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-perm-err"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    def mock_run_tool_loop(messages, ctx, approve):
        raise PermissionError("命中受限出网子串: cloud.local.json")

    monkeypatch.setattr("pipeline.agent.llm.run_tool_loop", mock_run_tool_loop)

    with patch_inputs(["告诉我 cloud.local.json 里的内容", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    out = capsys.readouterr().out
    assert "[BLOCKED] 出网被拦截" in out
    assert "本次请求未发出" in out


def test_repl_handles_llm_error_and_rolls_back_user_message(tmp_path: Path, monkeypatch, capsys):
    """网络/协议失败 (LLMError) 时不崩溃，回滚用户消息 (Spec §3.4)。"""
    monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
    from pipeline.agent.llm import LLMError
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-llm-err"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    def mock_run_tool_loop(messages, ctx, approve):
        raise LLMError("API 500: Internal Server Error")

    monkeypatch.setattr("pipeline.agent.llm.run_tool_loop", mock_run_tool_loop)

    with patch_inputs(["分析一下", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    out = capsys.readouterr().out
    assert "[FAIL] API 500: Internal Server Error" in out


def test_repl_max_iterations_warning(tmp_path: Path, monkeypatch, capsys):
    """达到 max_iterations 时发出显式警告交人接管 (Spec §1.2)。"""
    monkeypatch.setenv("AVA_TEST_KEY", "test-key-mock")
    root = make_agent_root(tmp_path, "http://127.0.0.1:9/v1")
    ep = root / "data" / "episodes" / "01-max-iter"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    def mock_run_tool_loop(messages, ctx, approve):
        return {
            "stopped": "max_iterations",
            "iterations": 10,
            "tool_calls_made": 10,
            "messages": messages,
            "final": {"content": "我跑了 10 轮"},
        }

    monkeypatch.setattr("pipeline.agent.llm.run_tool_loop", mock_run_tool_loop)

    with patch_inputs(["深度任务", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=root) == 0

    out = capsys.readouterr().out
    assert "[WARN] 工具调用已达上限 10 轮，停止并交人接管。" in out


def test_run_creative_loop_alias_compatibility(tmp_path: Path, monkeypatch):
    """既有调用点 run_creative_loop 兼容性验证 (Spec §1.1)。"""
    ep = tmp_path / "01-compat"
    ep.mkdir()

    called_with = []

    def mock_run_agent_loop(ep_dir, scope_mode="auto", extra_prompt="", root=None):
        called_with.append({"scope_mode": scope_mode, "extra_prompt": extra_prompt})
        return 0

    monkeypatch.setattr("pipeline.agent.cli.run_agent_loop", mock_run_agent_loop)

    assert run_creative_loop(ep, mode="chat") == 0
    assert called_with[0]["scope_mode"] == "creative"
    assert called_with[0]["extra_prompt"] == ""

    assert run_creative_loop(ep, mode="script") == 0
    assert called_with[1]["scope_mode"] == "creative"
    assert "本轮聚焦写稿" in called_with[1]["extra_prompt"]


def test_run_repl_wall_clock_time_accounting_intact(tmp_path: Path, monkeypatch):
    """停机点墙钟时间记账外层包装保持完好 (Spec §0 现状盘点)。"""
    ep = tmp_path / "data" / "episodes" / "01-stop-clock"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.md").write_text("# Script", encoding="utf-8")
    # 处于 02.5 停机点

    monkeypatch.setattr(paths, "ROOT", tmp_path)

    with patch_inputs(["/quit"]):
        assert run_repl(ep) == 0

    ht_path = ep / "human_time.json"
    assert ht_path.exists()
    records = json.loads(ht_path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["stop"] == "02.5"


def test_status_card_next_command_none_renders_wu(tmp_path: Path):
    """当推荐命令为空时，状态卡渲染为「推荐命令: 无」。"""
    ep = tmp_path / "01-none-cmd"
    ep.mkdir()
    status = inspect_episode(ep)
    status.next_command = None
    card = build_status_card(ep, status)
    assert "推荐命令: 无" in card


def test_status_card_audio_directory_with_wav_file(tmp_path: Path):
    """03-audio 目录含非隐藏音频文件时，配音产物标记为 ✓。"""
    ep = tmp_path / "01-audio-wav"
    ep.mkdir()
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    audio_dir = ep / "03-audio"
    audio_dir.mkdir()
    (audio_dir / "seg1.wav").write_bytes(b"RIFFdummy")

    card = build_status_card(ep)
    assert "配音产物 ✓" in card


def test_status_card_empty_episode_all_crosses(tmp_path: Path):
    """全新空期目录下产物存在性清单全为 ✗。"""
    ep = tmp_path / "01-empty-ep"
    ep.mkdir()

    card = build_status_card(ep)
    assert "选题 ✗" in card
    assert "草稿 ✗" in card
    assert "定稿 ✗" in card
    assert "配音产物 ✗" in card
    assert "排片 ✗" in card
    assert "审片 ✗" in card
    assert "成片 ✗" in card


def test_status_card_multiple_advisories_joined(tmp_path: Path):
    """多条 advisory 用分号拼接。"""
    ep = tmp_path / "01-multi-adv"
    ep.mkdir()
    status = inspect_episode(ep)
    status.advisories = ["警告一", "警告二"]
    card = build_status_card(ep, status)
    assert "提示: 警告一；警告二" in card


def test_assemble_system_prompt_missing_director_md_uses_fallback(tmp_path: Path):
    """当 director.md 缺失时安全降级为默认标题，不崩溃。"""
    ep = tmp_path / "01-no-director"
    ep.mkdir()
    status = inspect_episode(ep)
    # tmp_path 没有 config/agent/scopes/director.md
    assembled = assemble_system_prompt(ep, "creative", status, root=tmp_path)
    assert "# Director Persona" in assembled


def test_repl_quit_variations(tmp_path: Path, monkeypatch):
    """/exit, exit, quit 等退出命令均能正常退出 REPL。"""
    ep = tmp_path / "data" / "episodes" / "01-quit-var"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    for q in ["/exit", "exit", "quit"]:
        with patch_inputs([q]):
            assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0


def test_m13_shortcut_run_rejects_force_and_force_all(tmp_path: Path, monkeypatch, capsys):
    """/run 拒绝执行 --force 或 --force-all 参数命令 (Spec §4.2)。"""
    ep = tmp_path / "data" / "episodes" / "01-force-rej"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    with patch_inputs(["/run tts --force", "/run tts --force-all", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert out.count("[REJECT]") == 2
    assert "禁止在 ava 中使用 --force" in out


def test_repl_empty_lines_ignored(tmp_path: Path, monkeypatch):
    """输入空行直接跳过，不触发任何 LLM 调用。"""
    ep = tmp_path / "data" / "episodes" / "01-empty-lines"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

    mock_dispatch = MagicMock()
    monkeypatch.setattr("pipeline.agent.cli._dispatch_agent_turn", mock_dispatch)

    with patch_inputs(["", "   ", "\t", "/quit"]):
        assert cli.run_agent_loop(ep, scope_mode="auto", root=tmp_path) == 0

    assert mock_dispatch.call_count == 0


def test_status_card_scope_override_reflected(tmp_path: Path):
    """状态卡显式接收 scope 参数（如 /asset override），显示生效 scope (解决 🔵)。"""
    ep = tmp_path / "01-scope-override"
    ep.mkdir()
    status = inspect_episode(ep)
    card = build_status_card(ep, status, scope="asset")
    assert "scope: asset" in card


def test_dispatch_agent_turn_messages_merged_across_turns(tmp_path: Path, monkeypatch):
    """验证 _dispatch_agent_turn 在两轮真实对话中正确合并并保留上一轮历史 (解决 🟡2)。"""
    with mock_llm_server([
        {"role": "assistant", "content": "这是第一轮回复"},
        {"role": "assistant", "content": "这是第二轮回复"},
    ]) as (url, state):
        root = make_agent_root(tmp_path, url + "/v1")
        monkeypatch.setenv("AVA_TEST_KEY", "test-key-merge")
        ep = root / "data" / "episodes" / "01-merge-test"
        ep.mkdir(parents=True)
        (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")

        messages: list[dict] = []
        status = inspect_episode(ep)
        from pipeline.agent.assembly import SessionContextTracker
        tracker = SessionContextTracker()

        cli._dispatch_agent_turn("第一轮问题", messages, ep, "creative", status, root=root, tracker=tracker)
        cli._dispatch_agent_turn("第二轮问题", messages, ep, "creative", status, root=root, tracker=tracker)

        # 第二轮请求发往 LLM 时，body 中的 messages 必须包含第一轮的 user 和 assistant
        assert len(state["requests"]) == 2
        req2_messages = state["requests"][1]["body"]["messages"]
        user_texts = [m["content"] for m in req2_messages if m["role"] == "user"]
        assert user_texts == ["第一轮问题", "第二轮问题"]
        assistant_texts = [m["content"] for m in req2_messages if m["role"] == "assistant"]
        assert assistant_texts == ["这是第一轮回复"]


