"""D61：番剧笔记对抗审查 notes_review（spec 三节）。全部临时夹具，LLM 用替身。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import notes_review as N
from pipeline import paths

SUBS = [(160.0, "拜託了 Fyu-Neru"), (161.5, "把這個交給涯"), (271.0, "恐怖分子的新聞"),
        (534.0, "連嘗試都不敢的話"), (536.0, "是絕對做不到"), (538.5, "櫻滿集是膽小鬼嗎"),
        (583.0, "袒護她的話 你也將被視為同罪"), (900.0, "把這個交給涯")]


def _units(lines):
    """按 subindex 的 WINDOW=2 滑窗拼单元：unit_i = line_i + " " + line_{i+1}。"""
    out = []
    for i, (t, s) in enumerate(lines):
        text = s + (" " + lines[i + 1][1] if i + 1 < len(lines) else "")
        out.append({"start": t, "text": text})
    return out


SCENES_OK = """1. 冷开场：祈盗出试管（「拜託了 Fyu-Neru 把這個交給涯」0:02:40）；追兵开火。
2. 次日上学（0:04:31）。
3. 祈激将「連嘗試都不敢的話 是絕對做不到 櫻滿集是
   膽小鬼嗎」（0:08:54-0:09:01）。
4. GHQ 闯入（「袒護她的話 你也將被視為同罪」0:09:30）。
5. 结尾。
"""


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "data"
    (d / "library" / "notes").mkdir(parents=True)
    (d / "library" / "index").mkdir(parents=True)
    (d / "library" / "index" / "番_S01E01.json").write_text(json.dumps({"units": _units(SUBS)}), encoding="utf-8")
    (d / "library" / "sources.json").write_text(json.dumps({"番": {"S01E01": {}, "S01E02": {}}}), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA", d)
    from pipeline import ingest

    monkeypatch.setattr(ingest, "SOURCES", d / "library" / "sources.json")
    return d


def _note(data: Path, body: str) -> None:
    (data / "library" / "notes" / "番.md").write_text(body, encoding="utf-8")


# ---------- 切集与厚度 ----------


def test_切集_同集只取第一次_到下一个标题为止():
    note = "# 番\n## 分集速查\n### S01E01 発生\n甲\n### S01E02 適者\n乙\n## 台词锚点\n### S01E01\n丙\n"
    secs = N.split_episodes(note)
    assert [(s.key, s.text.strip()) for s in secs] == [("S01E01", "甲"), ("S01E02", "乙")]


def test_厚度_写薄与达标():
    good = N.Section("S01E01", "S01E01", 1, SCENES_OK)
    thin = N.Section("S01E02", "S01E02", 1, "1. 只有一句话概括这集。\n")
    rows, _ = N.thickness("x\n" * 100, [good, thin], 2)
    assert [r.ok for r in rows] == [True, False]
    _rows, probs = N.thickness("x\n" * 10, [good], 2)
    assert any("总行数" in p for p in probs) and any("缺 1 集" in p for p in probs)
    _rows, probs = N.thickness("x\n", [], 2)
    assert any("没有按" in p for p in probs)


# ---------- 引用核销 ----------


def test_引文与时间码解析_折行接回_区间():
    qs = N.quotes_of(N.Section("S01E01", "", 1, SCENES_OK))
    assert [(q.text, q.tc) for q in qs] == [
        ("拜託了 Fyu-Neru 把這個交給涯", "0:02:40"),
        ("連嘗試都不敢的話 是絕對做不到 櫻滿集是膽小鬼嗎", "0:08:54-0:09:01"),
        ("袒護她的話 你也將被視為同罪", "0:09:30"),
    ]


def test_时间码不吞前导数字():
    qs = N.quotes_of(N.Section("S01E01", "", 1, "（「這是幫人保管的」0:14:19）\n"))
    assert qs[0].tc == "0:14:19" and qs[0].start == 859.0


def test_核销三种结局():
    q = lambda text, tc: N.quotes_of(N.Section("S01E01", "", 1, f"「{text}」{tc}\n"))[0]
    assert N.check_quote(q("拜託了 Fyu-Neru 把這個交給涯", "0:02:40"), SUBS).verdict == "通过"
    wrong = N.check_quote(q("袒護她的話 你也將被視為同罪", "0:09:30"), SUBS)   # 实际 0:09:43
    assert wrong.verdict == "时间码错" and "0:09:43" in wrong.detail
    assert N.check_quote(q("字幕里根本没有这一句", "0:01:00"), SUBS).verdict == "字幕里找不到"
    assert N.check_quote(q("拜託了", "0:01:00"), None).verdict == "该集无字幕索引"


def test_同一句全片多处_任一处对上就通过():
    q = N.quotes_of(N.Section("S01E01", "", 1, "「把這個交給涯」0:15:00\n"))[0]
    assert N.check_quote(q, SUBS).verdict == "通过"


def test_窗口缓冲_前3秒后5秒():
    mk = lambda tc: N.quotes_of(N.Section("S01E01", "", 1, f"「恐怖分子的新聞」{tc}\n"))[0]
    assert N.check_quote(mk("0:04:28"), SUBS).verdict == "通过"      # 字幕 271 s，笔记早 3 s 内
    assert N.check_quote(mk("0:04:34"), SUBS).verdict == "通过"      # 笔记晚 3 s：起头那句在 t−3 内
    assert N.check_quote(mk("0:04:40"), SUBS).verdict == "时间码错"


# ---------- LLM 层（替身） ----------


def _fake_llm(monkeypatch, items):
    from pipeline.agent import llm

    class Cfg:
        def model_for(self, _p):
            return "fake-model"

    monkeypatch.setattr(llm, "load_llm_config", lambda: Cfg())
    sent = []

    def fake(messages, **kw):
        sent.append(messages)
        return {"content": json.dumps({"items": items}, ensure_ascii=False)}

    monkeypatch.setattr(llm, "chat_complete", fake)
    return sent


def test_LLM层_引用自校(data: Path, monkeypatch):
    _note(data, "# 番\n## 分集速查\n### S01E01 発生\n" + SCENES_OK)
    sent = _fake_llm(monkeypatch, [
        {"类别": "说话人存疑", "原文": "祈激将「連嘗試都不敢的話", "字幕": "0:08:54", "说明": "字幕不带说话人"},
        {"类别": "与字幕矛盾", "原文": "笔记里根本没有的一句话", "字幕": "0:02:40", "说明": "伪造原文"},
        {"类别": "与字幕矛盾", "原文": "GHQ 闯入", "字幕": "0:30:00", "说明": "伪造时间码"},
        {"类别": "字幕无据", "原文": "次日上学（0:04:31）", "字幕": "", "说明": "需网源"},
    ])
    res = N.review("番")
    items = res.llm_items["S01E01"]
    assert items[0]["核对"] == [] and items[3]["核对"] == []
    assert any("原文在笔记小节里找不到" in m for m in items[1]["核对"])
    assert any("0:30:00 附近没有台词" in m for m in items[2]["核对"])
    # 零上下文：只发了笔记小节与整集字幕，没有别的
    user = sent[0][1]["content"]
    assert "## 笔记小节" in user and "0:09:43 袒護她的話" in user and len(sent[0]) == 2


def test_mechanical_only不调模型_报告不覆盖旧报告(data: Path, monkeypatch, capsys):
    _note(data, "# 番\n## 分集速查\n### S01E01 発生\n" + SCENES_OK)
    from pipeline.agent import llm

    monkeypatch.setattr(llm, "chat_complete", lambda *a, **k: pytest.fail("不许调模型"))
    old = data / "library" / "notes" / f"番-对抗审查报告-{datetime.now():%Y-%m-%d}.md"
    old.write_text("pi 时代的旧报告", encoding="utf-8")
    assert N.main(["番", "--mechanical-only"]) == 0
    assert old.read_text(encoding="utf-8") == "pi 时代的旧报告"
    new = old.with_name(old.stem + "-2.md")
    text = new.read_text(encoding="utf-8")
    assert "## 覆盖率声明" in text and "## 终审裁决" in text and "未调用（--mechanical-only）" in text
    assert "| Q1 | S01E01 | 0:09:30 |" in text and "时间码错" in text
    assert "缺 1 集" in text  # sources 登记 2 集、笔记只有 1 节


def test_剧场版整份笔记当一集(data: Path):
    (data / "library" / "sources.json").write_text(json.dumps({"番": {"S01E01": {}}}), encoding="utf-8")
    from pipeline import ingest

    _note(data, "# 番（剧场版）\n## 场景\n" + SCENES_OK + "\n".join("补" for _ in range(40)))
    res = N.review("番", mechanical_only=True)
    assert [s.key for s in res.sections] == ["S01E01"] and not res.thick_problems
    assert sum(c.verdict == "通过" for c in res.checks) == 2


def test_没有笔记报错(data: Path):
    with pytest.raises(N.NotesReviewError, match="没有《番》的笔记"):
        N.review("番", mechanical_only=True)


# ---------- 接入 ----------


def test_白名单放行且弹卡(data: Path, tmp_path: Path):
    from pipeline.agent.session import review_tool_call
    from pipeline.agent.tools import validate_pipeline_command

    ok, msg, argv = validate_pipeline_command("notes_review 罪恶王冠 --episodes S01E01,S01E02")
    assert ok, msg
    assert argv[3:] == ["罪恶王冠", "--episodes", "S01E01,S01E02"]  # 不补期目录
    ep = tmp_path / "ep"
    ep.mkdir()
    v = review_tool_call("run_pipeline", {"command": "notes_review 罪恶王冠"}, ep_dir=ep, scope="idea", root=None)
    assert v.action == "ask"


def test_状态卡提示_没审过与未终审(data: Path):
    from pipeline.status import notes_review_advisories

    ep = data / "episodes" / "01"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("---\n番: 番\n---\n", encoding="utf-8")
    assert notes_review_advisories(ep) == []                       # 没笔记不提示
    _note(data, "# 番\n")
    assert any("还没有对抗审查报告" in a for a in notes_review_advisories(ep))
    notes = data / "library" / "notes"
    (notes / "番-对抗审查报告.md").write_text("pi 时代旧格式，无终审表", encoding="utf-8")
    assert notes_review_advisories(ep) == []                       # 旧格式不判
    (notes / "番-对抗审查报告-2026-10-09.md").write_text(
        "## 终审裁决\n| # | 集 | 问题 | 摘要 | 裁决 |\n|---|---|---|---|---|\n"
        "| Q1 | S01E01 | 时间码错 | x |  |\n| F1 | S01E01 | 字幕无据 | y | 采纳 |\n", encoding="utf-8")
    assert notes_review_advisories(ep) == ["《番》笔记审查报告 番-对抗审查报告-2026-10-09.md 还有 1 条未终审（逐条裁决，采纳的用 write_note 改笔记）"]
