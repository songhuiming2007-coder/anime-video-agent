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
    assert any("人类耗时超预算" in a for a in advisories), advisories
