"""D61 / ADR-0028：write_note（spec `docs/dev/plans/2026-10-09-calibration-and-notes-spec.md` 二节）。

假仓库根：`paths.ROOT` / `paths.DATA` 指向 tmp；笔记、sources.json、characters.json 都是夹具。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import paths
from pipeline.agent.session import review_tool_call
from pipeline.agent.status_card import render_approval_card
from pipeline.agent.tools import ToolContext, execute_tool


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    notes = r / "data" / "library" / "notes"
    notes.mkdir(parents=True)
    (r / "config").mkdir()
    (r / "data" / "library" / "sources.json").write_text(json.dumps({"新番": {}, "_note": "x"}), encoding="utf-8")
    (r / "config" / "characters.json").write_text(json.dumps({"另一部": {}}), encoding="utf-8")
    (notes / "罪恶王冠.md").write_text("# 罪恶王冠\n\n## 分集速查\n\n### S01E01\n1. 旧句子\n", encoding="utf-8")
    (notes / "罪恶王冠-对抗审查报告-2026-10-09.md").write_text("| # | 裁决 |\n|---|---|\n| F1 |  |\n", encoding="utf-8")
    monkeypatch.setattr(paths, "ROOT", r)
    monkeypatch.setattr(paths, "DATA", r / "data")
    return r


def _ctx(root: Path, episode: Path | None = None) -> ToolContext:
    return ToolContext(root=root, episode_dir=episode, scope="idea", confirmed=True)


def _review(args: dict, root: Path):
    return review_tool_call("write_note", args, ep_dir=None, scope="idea", root=root)


NOTES = Path("data/library/notes")


# ---------- 番名与目标校验 ----------


@pytest.mark.parametrize("anime", ["../x", "a/b", "_history", ".hidden", "", "a\\b"])
def test_番名穿越或保留名拒(root: Path, anime: str):
    v = _review({"anime": anime, "target": "notes", "content": "x", "reason": "r"}, root)
    assert v.action == "reject" and "番名" in v.reason


def test_未登记的番不许新建(root: Path):
    v = _review({"anime": "手误番", "target": "notes", "content": "# x\n", "reason": "r"}, root)
    assert v.action == "reject" and "还没有笔记" in v.reason


@pytest.mark.parametrize("anime", ["新番", "另一部"])
def test_已登记的番可以新建_无期会话也行(root: Path, anime: str):
    args = {"anime": anime, "target": "notes", "content": f"# {anime}\n", "reason": "新番起笔记"}
    assert _review(args, root).action == "ask"
    out = execute_tool("write_note", args, _ctx(root))
    assert out.get("ok", True) is not False, out
    assert (root / NOTES / f"{anime}.md").read_text(encoding="utf-8") == f"# {anime}\n"


def test_review不能新建报告_报告名必须属于本番(root: Path):
    v = _review({"anime": "罪恶王冠", "target": "review", "report": "罪恶王冠-对抗审查报告-2099.md",
                 "content": "x", "reason": "r"}, root)
    assert v.action == "reject" and "notes_review 生成" in v.reason
    v = _review({"anime": "罪恶王冠", "target": "review", "report": "伪恋-对抗审查报告.md",
                 "content": "x", "reason": "r"}, root)
    assert v.action == "reject" and "本番" in v.reason


# ---------- 写入与留底 ----------


def test_edits改已有笔记_旧版留底(root: Path):
    args = {"anime": "罪恶王冠", "target": "notes", "reason": "更正说话人",
            "edits": [{"old": "1. 旧句子", "new": "1. 新句子（0:02:41）"}]}
    assert _review(args, root).action == "ask"
    execute_tool("write_note", args, _ctx(root))
    note = root / NOTES / "罪恶王冠.md"
    assert "1. 新句子（0:02:41）" in note.read_text(encoding="utf-8")
    hist = list((root / NOTES / "_history" / "罪恶王冠").iterdir())
    assert len(hist) == 1 and "1. 旧句子" in hist[0].read_text(encoding="utf-8")


def test_edits找不到在弹卡前拒(root: Path):
    v = _review({"anime": "罪恶王冠", "target": "notes", "reason": "r",
                 "edits": [{"old": "不存在的句子", "new": "x"}]}, root)
    assert v.action == "reject" and "找不到" in v.reason


def test_review填终审表(root: Path):
    args = {"anime": "罪恶王冠", "target": "review", "report": "罪恶王冠-对抗审查报告-2026-10-09.md",
            "reason": "终审", "edits": [{"old": "| F1 |  |", "new": "| F1 | 采纳：字幕 0:02:41 实证 |"}]}
    assert _review(args, root).action == "ask"
    execute_tool("write_note", args, _ctx(root))
    assert "采纳：字幕" in (root / NOTES / "罪恶王冠-对抗审查报告-2026-10-09.md").read_text(encoding="utf-8")


def test_落盘层也拒_绕过审卡的直接调用(root: Path):
    out = execute_tool("write_note", {"anime": "../x", "target": "notes", "content": "x", "reason": "r"}, _ctx(root))
    assert out["ok"] is False
    assert not (root / "data" / "library" / "x.md").exists()


# ---------- 卡面 ----------


def test_卡面带diff与素材库标记(root: Path):
    args = {"anime": "罪恶王冠", "target": "notes", "reason": "更正说话人",
            "edits": [{"old": "1. 旧句子", "new": "1. 新句子"}]}
    card = render_approval_card("write_note", args)
    assert "笔记写入审批" in card and "[素材库·所有期共用]" in card
    assert "-1. 旧句子" in card and "+1. 新句子" in card and "理由: 更正说话人" in card
    assert "旧版留底" in card


def test_新建卡面给开头(root: Path):
    card = render_approval_card("write_note", {"anime": "新番", "target": "notes",
                                               "content": "# 新番\n## 分集速查\n", "reason": "起笔记"})
    assert "新建文件" in card and "+ # 新番" in card
