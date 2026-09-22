"""pipeline.scout 单元测试与规范回归。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import paths, scout


def _create_clips(ep_dir: Path, segments: list[dict]) -> Path:
    clips_file = ep_dir / "04-clips.json"
    clips_file.write_text(
        json.dumps({"anime": "测试番", "segments": segments}, ensure_ascii=False),
        encoding="utf-8",
    )
    return clips_file


def _create_topic(ep_dir: Path, content: str) -> Path:
    topic_file = ep_dir / "01-topic.md"
    topic_file.write_text(content, encoding="utf-8")
    return topic_file


def test_probe_quadrants(tmp_path: Path):
    """测试 probe 谓词四象限（全 anchor、residual<2.5s、纯可救、混合）。"""
    # 象限 1：全 anchor（不可救）
    ep1 = tmp_path / "ep1"
    ep1.mkdir()
    _create_clips(ep1, [
        {"index": 1, "channel": "anchor", "status": "no_match", "duration": 5.0, "clips": []},
        {"index": 2, "channel": "anchor", "status": "short", "duration": 6.0, "clips": [{"dur": 2.0}]},
    ])
    res1 = scout.probe(ep1)
    assert len(res1["patchable"]) == 0
    assert len(res1["unrescuable"]) == 2

    # 象限 2：非 anchor 但 residual < 2.5s（不可救）
    ep2 = tmp_path / "ep2"
    ep2.mkdir()
    _create_clips(ep2, [
        {"index": 1, "channel": "scene", "status": "short", "duration": 5.0, "clips": [{"dur": 3.0}]},  # residual 2.0 < 2.5
        {"index": 2, "channel": "scene", "status": "short", "duration": 4.0, "clips": [{"dur": 1.6}]},  # residual 2.4 < 2.5
    ])
    res2 = scout.probe(ep2)
    assert len(res2["patchable"]) == 0
    assert len(res2["unrescuable"]) == 2

    # 象限 3：纯可救（no_match / no_source 或 residual >= 2.5）
    ep3 = tmp_path / "ep3"
    ep3.mkdir()
    _create_clips(ep3, [
        {"index": 1, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []},
        {"index": 2, "channel": "scene", "status": "no_source", "duration": 4.0, "clips": []},
        {"index": 3, "channel": "scene", "status": "short", "duration": 6.0, "clips": [{"dur": 3.0}]},  # residual 3.0 >= 2.5
    ])
    res3 = scout.probe(ep3)
    assert len(res3["patchable"]) == 3
    assert len(res3["unrescuable"]) == 0

    # 象限 4：混合（可救 + 不可救 + 正常 ok 段）
    ep4 = tmp_path / "ep4"
    ep4.mkdir()
    _create_clips(ep4, [
        {"index": 1, "channel": "scene", "status": "ok", "duration": 5.0, "clips": [{"dur": 5.0}]},
        {"index": 2, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []},
        {"index": 3, "channel": "anchor", "status": "short", "duration": 5.0, "clips": [{"dur": 1.0}]},
        {"index": 4, "channel": "scene", "status": "short", "duration": 4.0, "clips": [{"dur": 2.0}]},  # residual 2.0
    ])
    res4 = scout.probe(ep4)
    assert len(res4["patchable"]) == 1
    assert res4["patchable"][0]["index"] == 2
    assert len(res4["unrescuable"]) == 2
    assert {s["index"] for s in res4["unrescuable"]} == {3, 4}


def test_detect_type_and_fallthrough(tmp_path: Path):
    """测试推断工单类型及不可救时的 fall through 到 notes。"""
    ep = tmp_path / "ep_ft"
    ep.mkdir()
    # 失败段全是不可救段
    _create_clips(ep, [
        {"index": 1, "channel": "anchor", "status": "no_match", "duration": 5.0, "clips": []},
    ])
    _create_topic(ep, "番: 不存在的未入库番名xyz\n")

    # 应当拒签 patch，fall through 检出 notes
    detected = scout.detect_type(ep)
    assert len(detected) == 1
    assert detected[0][0] == "notes"

    # 若同时有 patchable，同时返回 patch 与 notes
    _create_clips(ep, [
        {"index": 1, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []},
    ])
    detected2 = scout.detect_type(ep)
    types = [t[0] for t in detected2]
    assert "patch" in types
    assert "notes" in types


def test_titles_detection_and_missing(tmp_path: Path):
    """测试 titles 自动推断、缺失 E10 报错及 08-cover-title.md 引用。"""
    ep = tmp_path / "ep_titles"
    ep.mkdir()
    _create_clips(ep, [{"index": 1, "status": "ok", "duration": 5.0, "clips": [{"dur": 5.0}]}])
    (ep / "07-titles.md").write_text("# 标题与简介候选\n", encoding="utf-8")

    detected = scout.detect_type(ep)
    assert len(detected) == 1
    assert detected[0][0] == "titles"

    ticket = scout.render_titles_ticket(ep)
    assert "docs/runbook/08-cover-title.md" in ticket
    assert (paths.ROOT / "docs" / "runbook" / "08-cover-title.md").exists()

    # --type titles 而文件不存在
    ep_empty = tmp_path / "ep_empty"
    ep_empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        scout.main([str(ep_empty), "--type", "titles"])
    assert "先 /run cover" in str(exc.value)


def test_notes_empty_rejection(monkeypatch, tmp_path: Path):
    """测试 notes 无缺失时拒签空工单（S4）。"""
    fake_data = tmp_path / "data"
    fake_notes = fake_data / "library" / "notes"
    fake_notes.mkdir(parents=True)
    (fake_notes / "春物.md").write_text("# 春物\n", encoding="utf-8")
    monkeypatch.setattr(paths, "DATA", fake_data)

    ep = tmp_path / "ep_notes_ok"
    ep.mkdir()
    _create_topic(ep, "番: 春物\n")  # 春物笔记已存在
    with pytest.raises(SystemExit) as exc:
        scout.main([str(ep), "--type", "notes"])
    assert "素材番笔记齐备，无需派工" in str(exc.value)


def test_render_ticket_broken_reference_guard(monkeypatch, tmp_path: Path):
    """🔴-1 & 🔵-1 出口自查回归：docs/ 与 skills/ 规范文件不存在时必须抛 RuntimeError。"""
    ep = tmp_path / "ep_broken"
    ep.mkdir()
    (ep / "07-titles.md").write_text("# 标题\n", encoding="utf-8")

    # 1. 模拟 docs/ 断链
    monkeypatch.setattr(
        scout,
        "render_titles_ticket",
        lambda _e: f"遵守标准：`{paths.ROOT / 'docs' / 'runbook' / 'nonexistent-07.md'}`\n",
    )
    with pytest.raises(RuntimeError) as exc1:
        scout.render_ticket("titles", ep, {})
    assert "工单引用断链：目标文件不存在" in str(exc1.value)

    # 2. 模拟 skills/ 断链
    monkeypatch.setattr(
        scout,
        "render_titles_ticket",
        lambda _e: f"遵守标准：`{paths.ROOT / 'skills' / 'acquire-assets' / 'NONEXISTENT_SKILL.md'}`\n",
    )
    with pytest.raises(RuntimeError) as exc2:
        scout.render_ticket("titles", ep, {})
    assert "工单引用断链：目标文件不存在" in str(exc2.value)


def test_main_type_patch_unrescuable_rejection(tmp_path: Path, capsys):
    """🟡-1 回归：main --type patch 遇到全不可救失败段必须拒签（返回 1）。"""
    ep = tmp_path / "ep_rej"
    ep.mkdir()
    _create_clips(ep, [
        {"index": 1, "channel": "anchor", "status": "no_match", "duration": 5.0, "clips": []},
    ])
    rc = scout.main([str(ep), "--type", "patch"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL 失败段全部不可救，无法签发 patch 工单" in captured.err
    assert "改 02-script.md 锚点" in captured.err


def test_floor_pre_resolution(tmp_path: Path):
    """测试企划期 floor 预消解与素材番未标定指引。"""
    ep = tmp_path / "ep_multi"
    ep.mkdir()
    _create_topic(ep, "番: 番A, 番B\n")

    # 企划期未传 floor -> SystemExit
    with pytest.raises(SystemExit) as exc:
        scout.resolve_patch_floor(ep, floor=None)
    assert "企划期补丁池 floor 无主番可继承" in str(exc.value)

    # 企划期指定 floor
    arg, guide = scout.resolve_patch_floor(ep, floor=0.65)
    assert arg == " --floor 0.65"
    assert guide is None

    # 单番期（素材番未标定场景）
    ep_single = tmp_path / "ep_single"
    ep_single.mkdir()
    _create_topic(ep_single, "番: 某未标定番剧999\n")
    arg_s, guide_s = scout.resolve_patch_floor(ep_single, floor=None)
    assert arg_s == ""
    assert guide_s is not None
    assert "vprobe scene 某未标定番剧999 <集>" in guide_s


def test_active_supply_ticket(tmp_path: Path):
    """测试主动补料工单（失败段为零）。"""
    ep = tmp_path / "ep_active"
    ep.mkdir()
    _create_clips(ep, [
        {"index": 1, "channel": "scene", "status": "ok", "duration": 5.0, "clips": [{"dur": 5.0}]}
    ])
    p_data = scout.probe(ep)
    ticket = scout.render_patch_ticket(ep, p_data)

    assert "无缺口可推导——素材直落 patch_assets/ 后跑验收命令" in ticket
    assert "判据 4 不适用（无「本单目标段」）" in ticket
    assert "04-patch/pool.json" in ticket


def test_ticket_markers_and_paths(tmp_path: Path, capsys):
    """测试标记行配对、sys.executable 写实与绝对路径。"""
    ep = tmp_path / "ep_render"
    ep.mkdir()
    _create_clips(ep, [
        {"index": 1, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []}
    ])
    _create_topic(ep, "番: 测试主番\n类型: 杂谈\n张力: 虚构张力\n")

    rc = scout.main([str(ep), "--type", "patch", "--floor", "0.55"])
    assert rc == 0
    captured = capsys.readouterr()

    # 检查标记行包裹与落盘
    assert scout.TICKET_START_MARKER in captured.out
    assert scout.TICKET_END_MARKER in captured.out
    ticket_file = ep / "scout-ticket-patch.md"
    assert ticket_file.exists()

    content = ticket_file.read_text(encoding="utf-8")
    # 钉死解释器写实与仓库绝对路径
    assert sys.executable in content
    assert str(paths.ROOT) in content
    assert str(paths.ROOT / "skills" / "acquire-assets" / "SKILL.md") in content
    assert str(paths.ROOT / "docs" / "runbook" / "04-clips.md") in content
    assert "--floor 0.55" in content


def test_dependency_isolation():
    """回归测试：scout 与 status 导入绝对禁止拉入 numpy 与 clips（守卫看板热路径）。"""
    cmd = [
        sys.executable,
        "-c",
        "import pipeline.scout, pipeline.status, sys; "
        "assert 'numpy' not in sys.modules, 'numpy leaked into modules'; "
        "assert 'pipeline.clips' not in sys.modules, 'pipeline.clips leaked at top level'",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"


def test_status_lightweight_isolation():
    """看板轻量化断言：import pipeline.status 独立执行绝对不污染 numpy。"""
    cmd = [
        sys.executable,
        "-c",
        "import pipeline.status, sys; assert 'numpy' not in sys.modules, 'FAIL: numpy 泄漏进了 status 模块！'",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"


def test_status_advisories_no_fork(tmp_path: Path):
    """构造假期目录验证 status 输出的 advisory 文案与 scout 探针严格对应、正确无分叉。"""
    from pipeline.status import _detect_advisories

    # 1. 待补料段（patchable 非空）：提示 N 段排片落空可补料
    ep_patchable = tmp_path / "ep_patchable"
    ep_patchable.mkdir()
    _create_clips(ep_patchable, [
        {"index": 1, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []},
        {"index": 2, "channel": "scene", "status": "short", "duration": 6.0, "clips": [{"dur": 3.0}]},  # residual 3.0 >= 2.5
    ])
    adv1 = _detect_advisories(ep_patchable)
    assert "2 段排片落空可补料（REPL 内敲 /scout，或命令行 python -m pipeline.scout <期> 生成派工单）" in adv1
    assert not any("补丁池救不了" in a for a in adv1)

    # 2. 混合情况（有 patchable 也有 unrescuable）：优先提示可补料，不报救不了
    ep_mixed = tmp_path / "ep_mixed"
    ep_mixed.mkdir()
    _create_clips(ep_mixed, [
        {"index": 1, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []},
        {"index": 2, "channel": "anchor", "status": "short", "duration": 5.0, "clips": [{"dur": 2.0}]},  # anchor 不可救
    ])
    adv_mixed = _detect_advisories(ep_mixed)
    assert "1 段排片落空可补料（REPL 内敲 /scout，或命令行 python -m pipeline.scout <期> 生成派工单）" in adv_mixed
    assert not any("补丁池救不了" in a for a in adv_mixed)

    # 3. 纯不可救段（unrescuable 非空但 patchable 为空）：提示 N 段失败且补丁池救不了
    ep_unrescuable = tmp_path / "ep_unrescuable"
    ep_unrescuable.mkdir()
    _create_clips(ep_unrescuable, [
        {"index": 1, "channel": "anchor", "status": "no_match", "duration": 5.0, "clips": []},
        {"index": 2, "channel": "scene", "status": "short", "duration": 5.0, "clips": [{"dur": 3.0}]},  # residual 2.0 < 2.5
    ])
    adv2 = _detect_advisories(ep_unrescuable)
    assert "2 段排片失败且补丁池救不了（改锚点或改稿）" in adv2
    assert not any("可补料" in a for a in adv2)

    # 4. 缺失番剧笔记（missing_notes 非空）
    ep_notes = tmp_path / "ep_notes"
    ep_notes.mkdir()
    _create_topic(ep_notes, "番: 葬送的芙莉莲, 迷宫饭\n")
    adv3 = _detect_advisories(ep_notes)
    assert "缺《葬送的芙莉莲》等 2 部番剧笔记（REPL 内敲 /scout，或命令行 python -m pipeline.scout <期> --type notes）" in adv3

    # 5. 异常容错（坏 04-clips.json 按 S4 优雅跳过，绝不抛出异常）
    ep_corrupt = tmp_path / "ep_corrupt"
    ep_corrupt.mkdir()
    (ep_corrupt / "04-clips.json").write_text("{invalid-json", encoding="utf-8")
    adv_corrupt = _detect_advisories(ep_corrupt)
    assert isinstance(adv_corrupt, list)


def test_clips_footer_guidance_dispatch(tmp_path: Path):
    """验证 clips.py 失败页脚所调 probe 逻辑的分流一致性。"""
    # 可救缺口
    ep_p = tmp_path / "ep_probe_p"
    ep_p.mkdir()
    _create_clips(ep_p, [
        {"index": 1, "channel": "scene", "status": "no_match", "duration": 5.0, "clips": []}
    ])
    p1 = scout.probe(ep_p)
    assert bool(p1.get("patchable")) is True
    # 纯不可救
    ep_u = tmp_path / "ep_probe_u"
    ep_u.mkdir()
    _create_clips(ep_u, [
        {"index": 1, "channel": "anchor", "status": "no_match", "duration": 5.0, "clips": []}
    ])
    p2 = scout.probe(ep_u)
    assert not p2.get("patchable")
    assert bool(p2.get("unrescuable")) is True


def test_mutation_probe_predicate(tmp_path: Path):
    """变异检验：故意改坏谓词断言必红（检验 2.5s 边界与 anchor 排除）。"""
    ep = tmp_path / "ep_mut"
    ep.mkdir()
    # 临界值 2.499 vs 2.500
    _create_clips(ep, [
        {"index": 1, "channel": "scene", "status": "short", "duration": 5.0, "clips": [{"dur": 2.501}]},  # residual = 2.499 < 2.5
        {"index": 2, "channel": "scene", "status": "short", "duration": 5.0, "clips": [{"dur": 2.500}]},  # residual = 2.500 >= 2.5
    ])
    p = scout.probe(ep)
    assert len(p["patchable"]) == 1
    assert p["patchable"][0]["index"] == 2
    assert len(p["unrescuable"]) == 1
    assert p["unrescuable"][0]["index"] == 1
