"""D99 PR-3：事实库独立核对（pipeline/fact_review.py）。

核对员是一次模型调用；这里与 02.8（tests/test_adversarial.py）同一做法：纯函数逐个测，端到端用替身模型。
夹具是三轮 2017 采访第 2 页的真实原文片段——第一版事实文件把「别名义」的动机改写成「躲编辑」，
正是机械比对抓不到、要读懂才能判的第 ④ 类。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from pipeline import fact_review as fr
from pipeline import facts
from pipeline.agent import fetch_log
from pipeline.agent import llm as llm_mod

URL = "https://originalnews.nico/20667/2"
PAGE = ("supercellのためではなくて、あくまで自分のためだったんですけど。当時、三輪士郎って名前がニコニコで知れてきちゃったので、"
        "迂闊に遊んでもいられなくなって。何かやるたびに『お前、仕事しろよ』って言われてしまう。 ただ、息抜きで遊ぶ余地は残したかったんですよ。 "
        "——どんな名義で活動してたんですか？ 別名義は「スガ」で、三輪士郎のアシスタントという設定。")
NOW = dt.datetime(2026, 10, 15, 13, 0)


@pytest.fixture
def vol(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    fetch_log.record(url=URL, final_url=None, kind="fetch", text=PAGE, sid="s", root=tmp_path)
    d = tmp_path / "data" / "episodes" / "企划" / "01-卷"
    d.mkdir(parents=True)
    ex = "当時、三輪士郎って名前がニコニコで知れてきちゃったので、迂闊に遊んでもいられなくなって。"
    facts.add([
        {"claim": "三轮用别名是因为名字在ニコニコ出名后想留个玩的地方", "url": URL, "excerpts": [ex], "proposed_level": "一手", "topic": "成员"},
        {"claim": "三轮用别名是为了躲集英社编辑", "url": URL, "excerpts": [ex], "proposed_level": "一手", "topic": "成员"},
        {"claim": "没出处的一条", "url": "https://example.com/none", "excerpts": ["x"], "proposed_level": "一手", "topic": "成员"},
    ], target=d, sid="s", root=tmp_path)
    facts.promote(d, level="一手", ids=["F001", "F002"], root=tmp_path)
    return d


def test_gather_跳过未核实_带上下文(vol):
    items, gaps = fr.gather(vol, root=vol.parents[3])
    assert [it["id"] for it in items] == ["F001", "F002"]
    assert "息抜きで遊ぶ余地" in items[0]["contexts"][0]       # 摘录后面那句原因在上下文里
    assert gaps == []


def test_window前后各带CONTEXT_PAD字():
    text = "甲" * 1000 + "目标句子" + "乙" * 1000
    w = fr._window(text, "目标句子")
    assert w == "甲" * fr.CONTEXT_PAD + "目标句子" + "乙" * fr.CONTEXT_PAD


class TestVerify:
    def _items(self, vol):
        return fr.gather(vol, root=vol.parents[3])[0]

    def test_引用核得上_漏判单列(self, vol):
        rows = [{"id": "F001", "verdict": "支持", "reason": "原文写了", "quote": "息抜きで遊ぶ余地は残したかった"}]
        judged, missing = fr.verify(rows, self._items(vol))
        assert judged[0]["flag"] == "" and missing == ["F002"]

    @pytest.mark.parametrize("row,flag", [
        ({"id": "F009", "verdict": "支持", "reason": "r", "quote": "息抜き"}, "没有这一条"),
        ({"id": "F001", "verdict": "大概对", "reason": "r", "quote": "息抜き"}, "不合法"),
        ({"id": "F001", "verdict": "支持", "reason": "r", "quote": "集英社の編集部"}, "依据不在"),
        ({"id": "F001", "verdict": "支持", "reason": "r", "quote": ""}, "没给依据"),
    ])
    def test_核不上的标出(self, vol, row, flag):
        judged, _ = fr.verify([row], self._items(vol))
        assert flag in judged[0]["flag"]

    def test_重复判同一条(self, vol):
        row = {"id": "F001", "verdict": "支持", "reason": "r", "quote": "息抜き"}
        judged, _ = fr.verify([row, dict(row)], self._items(vol))
        assert judged[1]["flag"] == "重复判了同一条"


def _fake_llm(monkeypatch, content: str) -> list[list[dict]]:
    sent: list[list[dict]] = []

    def fake_chat(messages, tools=None, **kwargs):
        sent.append(messages)
        assert kwargs.get("purpose") == "adversarial" and kwargs.get("timeout") == fr.LLM_TIMEOUT_S
        return {"role": "assistant", "content": content}

    monkeypatch.setattr(llm_mod, "chat_complete", fake_chat)
    monkeypatch.setattr(llm_mod, "load_llm_config", lambda root=None: llm_mod.LLMConfig(base_url="http://x", model="m-test", api_key="k"))
    return sent


REPLY = json.dumps({"items": [
    {"id": "F001", "verdict": "支持", "reason": "原文说名字出名后不能随便玩，想留透气的余地", "quote": "息抜きで遊ぶ余地は残したかった"},
    {"id": "F002", "verdict": "不支持", "reason": "原文没有编辑，原因是自己想玩", "quote": "あくまで自分のためだった"},
]}, ensure_ascii=False)


def test_端到端_不支持的一手回落待核_报告有通过清单(vol, monkeypatch):
    root = vol.parents[3]
    sent = _fake_llm(monkeypatch, REPLY)
    text, res = fr.review(vol, now=NOW, root=root)
    assert len(sent) == 1 and sent[0][0]["content"] == fr.SYSTEM_PROMPT
    assert "F003" not in sent[0][1]["content"]                       # 未核实的不送审
    assert res["demoted"] == ["F002"] and res["missing"] == []
    by = {e["id"]: e for e in facts.load(vol)}
    assert (by["F002"]["level"], by["F002"]["level_by"]) == ("待核", "review")
    assert (by["F001"]["level"], by["F001"]["review"]["verdict"]) == ("一手", "支持")
    assert "## 支持（通过清单，全量）（1 条）" in text and "| F001 |" in text
    assert "## 不支持（1 条）" in text and "## 终审裁决" in text
    assert "（独立核对：不支持）" in (vol / "facts.md").read_text(encoding="utf-8")
    assert facts.check(vol, root=root) == []                         # 回落后的条目仍对得上留档


def test_引用核不上的判定不生效(vol, monkeypatch):
    root = vol.parents[3]
    _fake_llm(monkeypatch, json.dumps({"items": [
        {"id": "F002", "verdict": "不支持", "reason": "r", "quote": "編集部に止められた"},
    ]}, ensure_ascii=False))
    text, res = fr.review(vol, now=NOW, root=root)
    assert res["demoted"] == []
    assert {e["id"]: e for e in facts.load(vol)}["F002"]["level"] == "一手"
    assert "核对员引用核不上" in text and "漏判" in text


def test_回复不是JSON留原文(vol, monkeypatch):
    _fake_llm(monkeypatch, "抱歉我不确定")
    with pytest.raises(ValueError, match="JSON"):
        fr.review(vol, now=NOW, root=vol.parents[3])
    assert (vol / "_agent" / "fact-review-raw.txt").read_text(encoding="utf-8") == "抱歉我不确定"


def test_输入超上限不调模型(vol, monkeypatch):
    sent = _fake_llm(monkeypatch, REPLY)
    monkeypatch.setattr(fr, "MAX_INPUT_CHARS", 100)
    with pytest.raises(ValueError, match="超过上限"):
        fr.review(vol, now=NOW, root=vol.parents[3])
    assert sent == []


def test_重跑留底旧报告(vol):
    fr.write_report(vol, "第一版", NOW)
    fr.write_report(vol, "第二版", NOW + dt.timedelta(minutes=1))
    assert (vol / fr.REPORT).read_text(encoding="utf-8") == "第二版"
    assert (vol / "_agent" / "fact-review-history" / "20261015-130100.md").read_text(encoding="utf-8") == "第一版"


def test_run_pipeline放行免卡并自动补目录(vol):
    from pipeline.agent.session import review_tool_call
    from pipeline.agent.tools import project_module_refusal, validate_pipeline_command

    ok, _msg, argv = validate_pipeline_command("fact_review", ep_dir=vol)
    assert ok and argv[-1] == str(vol.resolve())
    assert review_tool_call("run_pipeline", {"command": "fact_review"}, ep_dir=vol, scope="creative", root=None).action == "allow"
    # 企划会话（企划母目录有自己的事实库）也能跑
    assert project_module_refusal(argv, "project", vol.parent) is None
