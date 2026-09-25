"""ava 统一 CLI 看板、输入语义与 REPL 交互测试（Spec §2.3, §2.4, §2.6, §5 PR1）。
"""

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from pipeline.agent.cli import (
    check_code_freeze,
    create_new_episode,
    get_episodes_list,
    main,
    print_board,
    record_human_time,
    select_episode_interactive,
)


def test_episodes_list_excludes_hidden_and_sorts_mtime(tmp_path: Path, monkeypatch):
    """验证期目录排除 . 与 _ 前缀，统计下划线目录，且按 mtime 降序排列 (Spec §2.3, B4-r14)。"""
    ep_root = tmp_path / "data" / "episodes"
    ep_root.mkdir(parents=True)

    # 伪造 paths.ROOT
    from pipeline import paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)

    # 创建目录
    d1 = ep_root / "01-older"
    d1.mkdir()
    d2 = ep_root / "02-newer"
    d2.mkdir()
    d_hidden = ep_root / ".hidden"
    d_hidden.mkdir()
    d_verify = ep_root / "_ava-verify-test"
    d_verify.mkdir()

    # 调整 mtime：d2 > d1
    import os
    os.utime(d1, (1000, 1000))
    os.utime(d2, (2000, 2000))

    visible, hidden_count = get_episodes_list()
    assert [p.name for p in visible] == ["02-newer", "01-older"]
    assert hidden_count == 1


def test_select_episode_interactive_semantics(tmp_path: Path):
    """验证输入语义：回车选 1，数字选序号，字符串选期名，非法输入重试 (Spec §2.3, B2-r8)。"""
    ep1 = tmp_path / "01-alpha"
    ep2 = tmp_path / "02-beta"
    ep1.mkdir()
    ep2.mkdir()
    episodes = [ep1, ep2]

    # 1. 回车（空字符串） -> 选第 1 行
    with patch("builtins.input", return_value=""):
        res = select_episode_interactive(episodes)
        assert res == ep1

    # 2. 数字 "2" -> 选第 2 行
    with patch("builtins.input", return_value="2"):
        res = select_episode_interactive(episodes)
        assert res == ep2

    # 3. 字符串全名 -> 匹配期名
    with patch("builtins.input", return_value="02-beta"):
        res = select_episode_interactive(episodes)
        assert res == ep2

    # 4. 非法输入重试 -> 输入 "99" (超限) 然后输入 "1"
    with patch("builtins.input", side_effect=["99", "1"]):
        res = select_episode_interactive(episodes)
        assert res == ep1


def test_non_tty_degradation_exits_cleanly(tmp_path: Path, monkeypatch, capsys):
    """非 tty 环境下（sys.stdin.isatty() == False）只打看板退出，杜绝 EOFError (Spec §2.3, B2-r5)。"""
    ep_root = tmp_path / "data" / "episodes"
    ep_root.mkdir(parents=True)
    (ep_root / "01-test").mkdir()

    from pipeline import paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    ret = main([])
    assert ret == 0
    out, _ = capsys.readouterr()
    assert "anime-video-agent-ava 统一制片工作台" in out
    assert "01-test" in out


def test_create_new_episode_and_reject_duplicates(tmp_path: Path, monkeypatch):
    """ava new <期号> 建立目录 + 01-topic.md 模板，重名拒绝覆盖 (Spec §5 PR1, B3-r9)。"""
    ep_root = tmp_path / "data" / "episodes"
    ep_root.mkdir(parents=True)

    from pipeline import paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)

    # 首次创建成功
    code1 = create_new_episode("01-新立项")
    assert code1 == 0
    target_dir = ep_root / "01-新立项"
    assert target_dir.exists()
    topic_file = target_dir / "01-topic.md"
    assert topic_file.exists()
    assert "# 01-新立项 选题配置" in topic_file.read_text(encoding="utf-8")

    # 重复创建拒绝覆盖
    code2 = create_new_episode("01-新立项")
    assert code2 == 1


def test_record_human_time_appends_records(tmp_path: Path):
    """验证 human_time.json 追加式记录停机点墙钟时间 (Spec §2.6, Y1-r19)。"""
    record_human_time(tmp_path, "02.5", entered_at=1000.0, left_at=1600.0)  # 600s = 10m
    record_human_time(tmp_path, "03.5", entered_at=2000.0, left_at=2900.0)  # 900s = 15m

    ht_file = tmp_path / "human_time.json"
    assert ht_file.exists()
    data = json.loads(ht_file.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["stop"] == "02.5"
    assert data[0]["minutes"] == 10.0
    assert data[1]["stop"] == "03.5"
    assert data[1]["minutes"] == 15.0


def test_check_code_freeze_runs_without_crash():
    """验证 Code Freeze 检查函数运行正常。"""
    res = check_code_freeze()
    assert isinstance(res, bool)


def test_repl_asset_scope_switch_and_quit(tmp_path: Path, capsys):
    """验证 REPL 交互中 /asset 与 /pipeline 切换 scope (Spec §2.1, §2.4, 🟡 3)。"""
    from pipeline.agent.cli import run_repl
    ep_dir = tmp_path / "01-test"
    ep_dir.mkdir()
    (ep_dir / "01-topic.md").write_text("# Topic", encoding="utf-8")

    # 模拟用户输入 /asset，然后 /pipeline，最后 /quit
    with patch("builtins.input", side_effect=["/asset", "/pipeline", "/quit"]):
        code = run_repl(ep_dir)
        assert code == 0

    out, _ = capsys.readouterr()
    assert "已进入 asset scope" in out
    assert "已切回自动推导工序模式" in out



# ---------------------------------------------------------------------------
# 停机点墙钟记账的接线级测试（Spec §2.6；终审 P0-2）
#
# 单测 record_human_time 本身在 PR1 就有了，但全仓没有调用点——止损链整条是死的。
# 下面这些用例驱动真 REPL / 真 /voice 包装，断言 human_time.json 真被写出来。
# ---------------------------------------------------------------------------


def test_human_stop_mapping_covers_the_four_stops():
    """current_step → 停机点标签的映射；03.5 在 REPL 停留记账里排除（由 /voice 记）。"""
    from pipeline.agent.cli import human_stop_of, park_stop_of

    assert human_stop_of("02.5 人审改稿") == "02.5"
    assert human_stop_of("03.5 配音顺听 / 04 排片") == "03.5"
    assert human_stop_of("05 审时间码") == "05"
    assert human_stop_of("09 人工发布") == "09"

    assert human_stop_of("03 语音合成") is None
    assert human_stop_of("06 本地渲染") is None
    assert human_stop_of("08 封面与标题候选") is None

    assert park_stop_of("03.5 配音顺听 / 04 排片") is None
    assert park_stop_of("02.5 人审改稿") == "02.5"


def _parked_at_02_5(tmp_path: Path) -> Path:
    """造一个停在 02.5 的期：有 02-script.md、无 02-diff.patch、无音频。"""
    from pipeline import paths
    ep = tmp_path / "data" / "episodes" / "01-parked"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.md").write_text("# Script", encoding="utf-8")
    paths.ROOT  # noqa: B018  (调用方已 monkeypatch)
    return ep


def test_repl_records_human_time_for_park_stop(tmp_path: Path, monkeypatch, capsys):
    """接线级：停在 02.5 的 REPL 退出时必须落一条 human_time.json（终审 P0-2）。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl
    from pipeline.status import inspect_episode

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    ep = _parked_at_02_5(tmp_path)
    assert inspect_episode(ep).current_step.startswith("02.5")

    with patch("builtins.input", side_effect=["/status", "/quit"]):
        assert run_repl(ep) == 0

    data = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["stop"] == "02.5"
    assert data[0]["minutes"] >= 0.0
    assert "entered_at" in data[0] and "left_at" in data[0]
    assert "[人时]" in capsys.readouterr().out


def test_repl_records_human_time_on_eof_exit(tmp_path: Path, monkeypatch):
    """EOF 退出路径同样记账（不是只有 /quit 才记）。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    ep = _parked_at_02_5(tmp_path)

    with patch("builtins.input", side_effect=EOFError()):
        assert run_repl(ep) == 0

    data = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    assert data[-1]["stop"] == "02.5"


def test_repl_does_not_record_outside_stop_points(tmp_path: Path, monkeypatch):
    """非停机点（如 03 语音合成）不记账——机器时间不是人类时间（§2.6 计数口径）。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl
    from pipeline.status import inspect_episode

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    ep = tmp_path / "data" / "episodes" / "01-machine"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.md").write_text("# Script", encoding="utf-8")
    (ep / "02-diff.patch").write_text("diff", encoding="utf-8")  # 已封板 → 进 03
    assert inspect_episode(ep).current_step.startswith("03 ")

    with patch("builtins.input", side_effect=["/quit"]):
        assert run_repl(ep) == 0

    assert not (ep / "human_time.json").exists()


def test_voice_session_records_03_5(tmp_path: Path):
    """接线级：/voice 进出记 03.5（终审 P0-2 的另一半）。"""
    from pipeline.agent.cli import run_voice_session

    ep = tmp_path / "01-voice"
    ep.mkdir()
    (ep / "02-script.md").write_text("## 段落 1\n配音：这是一句测试台词。\n", encoding="utf-8")
    (ep / "03-audio").mkdir()

    with patch("builtins.input", side_effect=EOFError()):
        assert run_voice_session(ep) == 0

    data = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    assert data[-1]["stop"] == "03.5"
    assert data[-1]["minutes"] >= 0.0


def test_voice_session_without_script_records_nothing(tmp_path: Path):
    """没有稿件的 /voice 起不来 → 不写空读数（宁可没读数，不要假读数）。"""
    from pipeline.agent.cli import run_voice_session

    ep = tmp_path / "01-empty"
    ep.mkdir()
    assert run_voice_session(ep) == 1
    assert not (ep / "human_time.json").exists()


def test_status_advisory_and_board_read_the_recorded_hours(tmp_path: Path, monkeypatch):
    """止损链闭环：记下的人时能被 status advisory 与看板读出来（终审 P0-2 的后果链）。"""
    from pipeline import paths
    from pipeline.agent.cli import record_human_time
    from pipeline.status import inspect_episode

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    ep = tmp_path / "data" / "episodes" / "01-budget"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# Topic", encoding="utf-8")
    (ep / "02-script.md").write_text("# Script", encoding="utf-8")
    (ep / "02-diff.patch").write_text("diff", encoding="utf-8")
    (ep / "04-clips.json").write_text(json.dumps({"total_duration": 600.0}), encoding="utf-8")

    assert record_human_time(ep, "03.5", 0.0, 1200.0) == 20.0  # 20 分钟
    advisories = inspect_episode(ep).advisories
    assert any("人类耗时：本期已记" in a for a in advisories), advisories


# ---------------------------------------------------------------------------
# T1–T5: 无期选题会话入口与启动形态改造测试 (Spec 2026-09-21-ava-entry-idea-scope §5.1)
# ---------------------------------------------------------------------------


def test_select_episode_interactive_anti_drift_and_sentinel(tmp_path: Path):
    """T1: 通用防漂移断言——解析 select prompt 的'词=说明'对并验证解析分支；输入 'idea' 返回 IDEA_KEYWORD sentinel。"""
    import re
    from unittest.mock import patch
    from pipeline.agent.cli import IDEA_KEYWORD, select_episode_interactive

    ep1 = tmp_path / "01-alpha"
    ep2 = tmp_path / "02-beta"
    ep1.mkdir()
    ep2.mkdir()
    episodes = [ep1, ep2]

    # 1. 拦截 input 以获取实际的 prompt 字符串
    captured_prompt = []

    def fake_input(prompt=""):
        captured_prompt.append(prompt)
        if len(captured_prompt) > 1:
            raise RuntimeError("select_episode_interactive 未识别关键词而陷入重试")
        return IDEA_KEYWORD

    with patch("builtins.input", side_effect=fake_input):
        res = select_episode_interactive(episodes)
        assert res == IDEA_KEYWORD

    assert captured_prompt
    prompt_str = captured_prompt[0]
    assert IDEA_KEYWORD in prompt_str

    # 2. 解析 prompt 中除期名段之外的 "词=说明" 关键词列表 (v1.2 🔵-R2)
    # prompt 形式如: "... [回车默认选 1: 01-alpha, idea=选题会话]: "
    match = re.search(r"\[(.*?)\]", prompt_str)
    inner = match.group(1) if match else prompt_str
    # 逗号分隔各段，过滤掉包含 "回车默认选" 的期名段，提取剩余包含 "=" 的 "词=说明"
    parts = [p.strip() for p in inner.split(",") if "回车默认选" not in p and "=" in p]
    keyword_pairs = {}
    for part in parts:
        k, v = part.split("=", 1)
        keyword_pairs[k.strip()] = v.strip()

    assert IDEA_KEYWORD in keyword_pairs, f"prompt 中未声明关键词 {IDEA_KEYWORD}"

    # 对 prompt 中声明的每个关键词，验证 select_episode_interactive 能够识别并返回对应 sentinel
    # 使用单元素 side_effect：若未被识别为关键词而进入重试循环，将立刻抛出 StopIteration 失败而不是死循环
    for kw in keyword_pairs:
        with patch("builtins.input", side_effect=[kw]):
            result = select_episode_interactive(episodes)
            assert result == kw, f"关键词 '{kw}' 无法被 select_episode_interactive 正常解析"


def test_main_idea_subcommand_dispatch_and_extra_args(monkeypatch):
    """T2: main(['idea']) 在 tty 下以 (None, scope_mode='idea') 调 run_agent_loop；带多余参数报错退出非零。"""
    from pipeline.agent.cli import main
    import sys

    calls = []

    def fake_run_agent_loop(ep_dir, scope_mode="auto", **kwargs):
        calls.append((ep_dir, scope_mode))
        return 0

    monkeypatch.setattr("pipeline.agent.cli.run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    # 正常调用
    rc = main(["idea"])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0] == (None, "idea")

    # 多余参数：退出非零
    rc_bad = main(["idea", "extra", "param"])
    assert rc_bad != 0
    assert len(calls) == 1  # 未增加新调用


def test_board_flow_routes_sentinel_to_run_agent_loop(tmp_path: Path, monkeypatch):
    """T3: 看板流交互选择返回 sentinel 时，路由到 run_agent_loop(None, scope_mode='idea') 而非 run_repl。"""
    from pipeline.agent.cli import IDEA_KEYWORD, main
    from pipeline import paths
    import sys

    ep_root = tmp_path / "data" / "episodes"
    ep_dir = ep_root / "01-test"
    ep_dir.mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    loop_calls = []
    repl_calls = []
    monkeypatch.setattr("pipeline.agent.cli.select_episode_interactive", lambda eps: IDEA_KEYWORD)
    monkeypatch.setattr("pipeline.agent.cli.run_agent_loop", lambda ep, scope_mode="auto", **kw: loop_calls.append((ep, scope_mode)) or 0)
    monkeypatch.setattr("pipeline.agent.cli.run_repl", lambda ep: repl_calls.append(ep) or 0)

    rc = main([])
    assert rc == 0
    assert len(loop_calls) == 1
    assert loop_calls[0] == (None, "idea")
    assert len(repl_calls) == 0


def test_main_new_enters_repl_in_tty(tmp_path: Path, monkeypatch):
    """T4: main(['new', name]) 在 tty 下建目录后直接以新期目录调用 run_repl；重名 exit 1 且不进对话。"""
    from pipeline.agent.cli import main
    from pipeline import paths
    import sys

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    repl_calls = []
    monkeypatch.setattr("pipeline.agent.cli.run_repl", lambda ep: repl_calls.append(ep) or 0)

    # 1. 成功创建并进入 run_repl
    rc = main(["new", "01-new-ep"])
    assert rc == 0
    assert len(repl_calls) == 1
    assert repl_calls[0].name == "01-new-ep"
    assert repl_calls[0].exists()

    # 2. 重名必拒，且不调用 run_repl
    rc_dup = main(["new", "01-new-ep"])
    assert rc_dup == 1
    assert len(repl_calls) == 1  # 依然是 1


def test_non_tty_dual_gates_for_new_and_idea(tmp_path: Path, monkeypatch, capsys):
    """T5: 非 tty 环境下 main(['new', name]) 与 main(['idea']) 均 exit 0 且不进入交互循环。"""
    from pipeline.agent.cli import main
    from pipeline import paths
    import sys

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    repl_calls = []
    loop_calls = []
    monkeypatch.setattr("pipeline.agent.cli.run_repl", lambda ep: repl_calls.append(ep) or 0)
    monkeypatch.setattr("pipeline.agent.cli.run_agent_loop", lambda ep, scope_mode="auto", **kw: loop_calls.append((ep, scope_mode)) or 0)

    # 1. ava new 在非 tty 下建目录但退出 0，不调 run_repl
    rc_new = main(["new", "02-non-tty-ep"])
    assert rc_new == 0
    assert len(repl_calls) == 0
    assert (tmp_path / "data" / "episodes" / "02-non-tty-ep").exists()

    # 2. ava idea 在非 tty 下打印说明退出 0，不调 run_agent_loop
    rc_idea = main(["idea"])
    assert rc_idea == 0
    assert len(loop_calls) == 0
    captured = capsys.readouterr()
    assert "ava idea" in captured.out
    assert "选题" in captured.out


# ---------------------------------------------------------------------------
# S1–S4: pi 派工交互与 scout/patch 人时记账测试 (Spec 2026-09-21-pi-scout §4, §7.4)
# ---------------------------------------------------------------------------


def _parked_at_05(tmp_path: Path) -> Path:
    """构造停在 05 的期：音频、排片均完备，04-clips.json 含失败段，未获 --approve。"""
    from pipeline import paths

    # 准备 scout 工单防断链所需的规范文件桩
    skills_dir = tmp_path / "skills" / "acquire-assets"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    docs_dir = tmp_path / "docs" / "runbook"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "04-clips.md").write_text("# Runbook\n", encoding="utf-8")
    dev_dir = tmp_path / "docs" / "dev"
    dev_dir.mkdir(parents=True, exist_ok=True)
    (dev_dir / "STANDARD.md").write_text("# Standard\n", encoding="utf-8")

    ep = tmp_path / "data" / "episodes" / "05-parked"
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text("# Topic\n番: 测试番\n", encoding="utf-8")
    (ep / "02-script.md").write_text("## 段落 1\n配音: 测试\n", encoding="utf-8")
    (ep / "02-diff.patch").write_text("diff", encoding="utf-8")
    audio_dir = ep / "03-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "manifest.json").write_text(json.dumps({"segments": []}), encoding="utf-8")

    clips_data = {
        "anime": "测试番",
        "segments": [
            {
                "index": 1,
                "channel": "scene",
                "status": "no_match",
                "duration": 5.0,
                "clips": [],
                "text": "测试",
                "scene": "测试场景",
            }
        ],
    }
    (ep / "04-clips.json").write_text(json.dumps(clips_data, ensure_ascii=False), encoding="utf-8")

    notes_dir = tmp_path / "data" / "library" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "测试番.md").write_text("# 笔记\n", encoding="utf-8")
    return ep


def test_repl_scout_ticket_output_and_noise_filtering(tmp_path: Path, monkeypatch, capsys):
    """S1: REPL 敲 /scout 打印工单标记行及 Markdown，落盘工单；<0.1min 噪音过滤不写 scout 记录。"""
    from pipeline import paths, scout
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    curr_time = 1000.0
    inputs = ["/scout", "/quit"]

    def fake_input(prompt=""):
        nonlocal curr_time
        cmd = inputs.pop(0)
        if cmd == "/quit":
            curr_time = 1002.0  # 2 秒后退出（< 0.1 分钟）
        return cmd

    monkeypatch.setattr("time.time", lambda: curr_time)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = run_repl(ep)
    assert rc == 0

    out = capsys.readouterr().out
    assert scout.TICKET_START_MARKER in out
    assert scout.TICKET_END_MARKER in out
    assert "pi 侦察派工单 · patch" in out
    assert (ep / "scout-ticket-patch.md").exists()

    ht_file = ep / "human_time.json"
    assert ht_file.exists()
    records = json.loads(ht_file.read_text(encoding="utf-8"))
    # 2 秒的 scout 噪音被过滤，仅留 05 的停机点记录
    assert not any(r.get("stop") == "scout" for r in records)


def test_repl_scout_human_time_mutual_exclusion_and_park_stop_restore(tmp_path: Path, monkeypatch):
    """S2: scout 计时与停机点互斥：/scout 挂起 05，下一条命令结算 scout 耗时并恢复 05，人时不双记。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    curr_time = 1000.0
    inputs = ["/scout", "/status", "/quit"]

    def fake_input(prompt=""):
        nonlocal curr_time
        cmd = inputs.pop(0)
        if cmd == "/scout":
            curr_time = 1600.0  # 05 耗时 600s = 10.0m
        elif cmd == "/status":
            curr_time = 2800.0  # scout 耗时 1200s = 20.0m
        elif cmd == "/quit":
            curr_time = 3400.0  # 恢复后的 05 耗时 600s = 10.0m
        return cmd

    monkeypatch.setattr("time.time", lambda: curr_time)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = run_repl(ep)
    assert rc == 0

    records = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    assert len(records) == 3

    # 第 1 段：进入 REPL 到敲 /scout 之前的 05
    assert records[0]["stop"] == "05"
    assert records[0]["minutes"] == 10.0

    # 第 2 段：/scout 到敲 /status 之间的 scout 采矿时段
    assert records[1]["stop"] == "scout"
    assert records[1]["minutes"] == 20.0

    # 第 3 段：敲 /status 恢复 05 停机点到 /quit 退出
    assert records[2]["stop"] == "05"
    assert records[2]["minutes"] == 10.0

    # 严格互斥无重叠检查
    assert records[0]["left_at"] == records[1]["entered_at"]
    assert records[1]["left_at"] == records[2]["entered_at"]


def test_repl_scout_consecutive_noise_filtering(tmp_path: Path, monkeypatch):
    """S3: 连敲 /scout 噪音过滤：两次 /scout 之间小于 0.1 分钟不落盘，最后由非 /scout 命令结算真实耗时。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    curr_time = 1000.0
    inputs = ["/scout", "/scout", "/status", "/quit"]

    def fake_input(prompt=""):
        nonlocal curr_time
        cmd = inputs.pop(0)
        if cmd == "/scout" and curr_time == 1000.0:
            curr_time = 1600.0  # 首次 /scout
        elif cmd == "/scout":
            curr_time = 1602.0  # 连敲 /scout（2秒后）
        elif cmd == "/status":
            curr_time = 2202.0  # 10 分钟后敲 /status
        elif cmd == "/quit":
            curr_time = 2205.0
        return cmd

    monkeypatch.setattr("time.time", lambda: curr_time)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = run_repl(ep)
    assert rc == 0

    records = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    scout_records = [r for r in records if r.get("stop") == "scout"]
    # 仅落盘一条 10 分钟的 scout 记录，连敲的 2 秒噪音被忽略
    assert len(scout_records) == 1
    assert scout_records[0]["minutes"] == 10.0


def test_repl_patch_alias_and_cli_subcommand(tmp_path: Path, monkeypatch):
    """S4: /patch 别名接入：REPL /patch 与 top-level ava <期> /patch 均成功调用 scout --type patch。"""
    from pipeline import paths
    from pipeline.agent.cli import main, run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    # 1. REPL /patch 交互别名测试
    with patch("builtins.input", side_effect=["/patch", "/quit"]):
        rc = run_repl(ep)
        assert rc == 0
        assert (ep / "scout-ticket-patch.md").exists()

    (ep / "scout-ticket-patch.md").unlink()

    # 2. CLI 顶层 ava <期> /patch
    rc_cli = main([str(ep), "/patch"])
    assert rc_cli == 0
    assert (ep / "scout-ticket-patch.md").exists()

    (ep / "scout-ticket-patch.md").unlink()

    # 3. CLI 顶层 ava <期> /scout
    rc_scout = main([str(ep), "/scout"])
    assert rc_scout == 0
    assert (ep / "scout-ticket-patch.md").exists()

    # 4. /patch 传 --type 被拒绝防越狱
    rc_reject = main([str(ep), "/patch", "--type", "notes"])
    assert rc_reject != 0


def test_repl_scout_bare_enter_settles_and_resumes_park_stop(tmp_path: Path, monkeypatch):
    """S5: 🟡-1 钉死：裸回车（肌肉记忆「我回来了」）立刻结算 scout span 并恢复停机点，不把后续等待静默错挂进 scout。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    curr_time = 1000.0
    inputs = ["/scout", "", "/quit"]

    def fake_input(prompt=""):
        nonlocal curr_time
        cmd = inputs.pop(0)
        if cmd == "/scout":
            curr_time = 1600.0  # 05 耗时 600s = 10.0m
        elif cmd == "":
            curr_time = 2800.0  # 敲裸回车：scout 耗时 1200s = 20.0m，停机点 05 复位
        elif cmd == "/quit":
            curr_time = 3400.0  # 回车后又等了 10 分钟退出：正确挂在 05（600s = 10.0m）而非 scout
        return cmd

    monkeypatch.setattr("time.time", lambda: curr_time)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = run_repl(ep)
    assert rc == 0

    records = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    assert len(records) == 3

    # 第 1 段：进入 REPL 到敲 /scout 之前的 05
    assert records[0]["stop"] == "05"
    assert records[0]["minutes"] == 10.0

    # 第 2 段：/scout 到敲回车之间的 scout 采矿时段
    assert records[1]["stop"] == "scout"
    assert records[1]["minutes"] == 20.0

    # 第 3 段：敲回车复位 05 停机点到 /quit 退出（后续等待正确归属 05）
    assert records[2]["stop"] == "05"
    assert records[2]["minutes"] == 10.0

    # 时间戳首尾相接无重叠
    assert records[0]["left_at"] == records[1]["entered_at"]
    assert records[1]["left_at"] == records[2]["entered_at"]


def test_repl_scout_invalid_patch_args_does_not_hang_span(tmp_path: Path, monkeypatch, capsys):
    """S6: 🔵-1 钉死：scout span 开启时输入非法 /patch --type，不能导致 scout span 空挂；正常结算旧 span 并恢复停机点。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    curr_time = 1000.0
    inputs = ["/scout", "/patch --type title", "/quit"]

    def fake_input(prompt=""):
        nonlocal curr_time
        cmd = inputs.pop(0)
        if cmd == "/scout":
            curr_time = 1600.0  # 05 耗时 600s = 10.0m
        elif cmd == "/patch --type title":
            curr_time = 2800.0  # scout 采矿 1200s = 20.0m，敲非法命令应结算 scout
        elif cmd == "/quit":
            curr_time = 3400.0  # 非法命令后回到 05 停留 600s = 10.0m
        return cmd

    monkeypatch.setattr("time.time", lambda: curr_time)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = run_repl(ep)
    assert rc == 0

    err = capsys.readouterr().err
    assert "/patch 别名已固定为 --type patch" in err

    records = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    assert len(records) == 3

    # 第 1 段：进入 REPL 到 /scout 之前的 05
    assert records[0]["stop"] == "05"
    assert records[0]["minutes"] == 10.0

    # 第 2 段：/scout 到敲 /patch 之间的 scout 采矿时段
    assert records[1]["stop"] == "scout"
    assert records[1]["minutes"] == 20.0

    # 第 3 段：敲非法 /patch 恢复 05 停机点到 /quit 退出（后续等待正确归属 05）
    assert records[2]["stop"] == "05"
    assert records[2]["minutes"] == 10.0

    assert records[0]["left_at"] == records[1]["entered_at"]
    assert records[1]["left_at"] == records[2]["entered_at"]


def test_repl_scout_step_advance_leaves_no_zero_park_garbage(tmp_path: Path, monkeypatch):
    """S7: 🔵-2 钉死：scout span 后工序被命令推进离开停机点，绝不产生 0.0 分钟 park 垃圾记录。"""
    from pipeline import paths
    from pipeline.agent.cli import run_repl

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    ep = _parked_at_05(tmp_path)

    curr_time = 1000.0
    inputs = ["/scout", "/advance", "/quit"]

    def fake_input(prompt=""):
        nonlocal curr_time
        cmd = inputs.pop(0)
        if cmd == "/scout":
            curr_time = 1600.0  # 05 耗时 600s = 10.0m
        elif cmd == "/advance":
            curr_time = 2800.0  # scout 采矿 1200s = 20.0m
            # 模拟工序推进：写入 04-clips.approved.json 使 05 推进至 06
            (ep / "04-clips.approved.json").write_text("{}", encoding="utf-8")
        elif cmd == "/quit":
            curr_time = 3400.0
        return cmd

    monkeypatch.setattr("time.time", lambda: curr_time)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = run_repl(ep)
    assert rc == 0

    records = json.loads((ep / "human_time.json").read_text(encoding="utf-8"))
    # 严格只有 2 条记录：05 (10m) 和 scout (20m)，绝无推进后遗留的 0.0m 停机点垃圾
    assert len(records) == 2
    assert records[0]["stop"] == "05"
    assert records[0]["minutes"] == 10.0
    assert records[1]["stop"] == "scout"
    assert records[1]["minutes"] == 20.0
    assert not any(r["minutes"] == 0.0 for r in records)



