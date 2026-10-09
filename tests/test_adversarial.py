"""02.8 零上下文对抗审查（D48 ②，spec 2026-10-09-adversarial-review-spec.md）。临时夹具，不碰真实 data/。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import adversarial as adv
from pipeline.agent import llm as llm_mod

# 段 1 有锚点 S02E07 19:48；配音 36 字 → 窗口 [19:33, 19:48 + 5 s + 15 s = 20:08]
SCRIPT = (
    "## 段落 1\n\n配音：回到房间的董香闭上双眼，喃喃自语：刚见面就动手，他肯定会不好意思回来的吧。\n\n"
    "画面：\n  锚点: S02E07 19:48\n  查询: 刚见面就动手\n\n"
    "## 段落 2\n\n配音：随后她抹掉眼泪，咬牙挤出一句，学习吧。\n\n"
    "画面：\n  锚点: 无（氛围段）\n  查询: 书桌前的董香\n"
)
SUB_LINES = [
    (1170.0, "早一点的台词"),
    (1189.5, "好不容易见面就突然揍人还说那种话"),
    (1194.0, "他会不好意思回来的吧"),
    (1500.0, "很久以后的台词"),
]
NOTES = "# 东京喰种\n\n## 雾岛董香\n\n第二季第七集，董香在房间里对雏实说起金木。\n"
PRESENCE = ["  19:49.06-19:54.07  董香",
            "本集 315 个镜头，115 个有已识别角色；只列已贴名、脸被检出的角色——没列出不等于不在场。"]


def _units(lines: list[tuple[float, str]]) -> list[dict]:
    """subindex 的 WINDOW=2 滑窗：unit_i = line_i + " " + line_{i+1}，末单元只有一句。"""
    out = []
    for i, (start, text) in enumerate(lines):
        nxt = lines[i + 1][1] if i + 1 < len(lines) else None
        out.append({"start": start, "end": start + 2, "text": f"{text} {nxt}" if nxt else text})
    return out


@pytest.fixture
def presence_calls() -> list[tuple]:
    return []


@pytest.fixture
def ep(tmp_path: Path, monkeypatch, presence_calls: list[tuple]) -> Path:
    d = tmp_path / "data" / "episodes" / "T1"
    d.mkdir(parents=True)
    (d / "01-topic.md").write_text("# 选题\n番: 东京喰种\n", encoding="utf-8")
    (d / "02-script.draft.md").write_text(SCRIPT, encoding="utf-8")
    index = tmp_path / "index"
    index.mkdir()
    (index / "东京喰种_S02E07.json").write_text(json.dumps({"units": _units(SUB_LINES)}, ensure_ascii=False),
                                              encoding="utf-8")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "东京喰种.md").write_text(NOTES, encoding="utf-8")
    monkeypatch.setattr(adv, "INDEX_DIR", index)
    monkeypatch.setattr(adv, "NOTES_DIR", notes)
    def fake_presence(anime, key, start, end, cache):
        presence_calls.append((anime, key, round(start, 2), round(end, 2)))
        return list(PRESENCE)

    monkeypatch.setattr(adv, "presence_lines", fake_presence)
    return d


# ---- 证据预取 ----


def test_evidence_window_and_sources(ep: Path, presence_calls: list[tuple]):
    ev = adv.gather_evidence(ep, "02-script.draft.md")
    s1, s2 = ev.segments
    assert s1.anchors == ["东京喰种 S02E07 19:48"]
    # 窗口外的 19:30 与 25:00 不给；窗口内两句给
    assert [(k, t, ln) for k, t, ln in s1.subs] == [
        ("S02E07", 1189.5, "好不容易见面就突然揍人还说那种话"), ("S02E07", 1194.0, "他会不好意思回来的吧")]
    voice_len = len("回到房间的董香闭上双眼，喃喃自语：刚见面就动手，他肯定会不好意思回来的吧。")
    assert presence_calls == [("东京喰种", "S02E07", 1173.0, round(1188 + voice_len / 7.2 + 15, 2))]
    assert s1.presence == PRESENCE
    assert s2.anchors == [] and s2.subs == [] and s2.presence == []
    assert ev.notes == {"东京喰种": NOTES}
    assert ev.gaps == []


def test_evidence_gaps_are_reported(ep: Path, monkeypatch):
    (adv.INDEX_DIR / "东京喰种_S02E07.json").unlink()
    (adv.NOTES_DIR / "东京喰种.md").unlink()

    def no_presence(anime, key, start, end, cache):
        raise adv.AdversarialError("FAIL 没有《东京喰种》的角色在场索引")

    monkeypatch.setattr(adv, "presence_lines", no_presence)
    ev = adv.gather_evidence(ep, "02-script.draft.md")
    assert ev.gaps == ["没有《东京喰种》的番剧笔记"]
    assert ev.segments[0].gaps == ["没有《东京喰种》S02E07 的字幕索引", "在场索引不可用：FAIL 没有《东京喰种》的角色在场索引"]


def test_prompt_carries_script_notes_and_per_segment_evidence(ep: Path):
    user = adv.build_user_prompt(adv.gather_evidence(ep, "02-script.draft.md"))
    assert "## 稿件（02-script.draft.md）" in user and "刚见面就动手" in user
    assert "## 番剧笔记《东京喰种》" in user and "董香在房间里对雏实说起金木" in user
    assert "### 段 1（锚点：东京喰种 S02E07 19:48）" in user
    assert "S02E07 19:54 他会不好意思回来的吧" in user
    assert "  19:49.06-19:54.07  董香" in user and "没列出不等于不在场" in user
    assert "### 段 2（无锚点）\n（只有稿件与笔记）" in user


# ---- 引用自校：审查者也会编 ----


def _fact(**kw) -> dict:
    base = {"段": "1", "类": "事实", "原文": "刚见面就动手", "问题": "说话人存疑", "判定": "与证据矛盾",
            "出处": {"类型": "字幕", "引用": "S02E07 19:54"}}
    return {**base, **kw}


@pytest.mark.parametrize("item, problem", [
    (_fact(), ""),
    (_fact(出处={"类型": "字幕", "引用": "S02E07 19:49"}), ""),                       # 19:49.5 取整到秒
    (_fact(出处={"类型": "字幕", "引用": "S02E07 20:30"}), "证据里没有 S02E07 20:30 这句字幕"),
    (_fact(出处={"类型": "字幕", "引用": "S02E08 19:54"}), "证据里没有 S02E08 19:54 这句字幕"),
    (_fact(出处={"类型": "字幕", "引用": "19:54"}), "字幕引用「19:54」不是「集号 mm:ss」"),
    (_fact(出处={"类型": "在场索引", "引用": "19:49"}), ""),
    (_fact(出处={"类型": "在场索引", "引用": "19:30"}), "证据里没有 19:30 起的在场镜头"),
    (_fact(出处={"类型": "笔记", "引用": "董香在房间里对雏实说起金木"}), ""),
    (_fact(出处={"类型": "笔记", "引用": "董香在天桥上对西尾锦说"}), "笔记摘句不在笔记里"),
    (_fact(出处={"类型": "无", "引用": ""}), "「与证据矛盾」必须给出处"),
    (_fact(判定="无出处", 出处={"类型": "无", "引用": ""}), ""),
    (_fact(判定="大概错了"), "判定「大概错了」不在 与证据矛盾/无出处/表述模糊 之内"),
    (_fact(原文="西尾锦走上天桥"), "原文不在稿件里"),
    (_fact(段="9"), "稿件里没有段 9"),
])
def test_verify_fact_citations(ep: Path, item: dict, problem: str):
    ev = adv.gather_evidence(ep, "02-script.draft.md")
    assert adv.verify_item(item, ev)["核对"] == problem


def test_flow_and_language_items_are_diagnosis_only(ep: Path):
    ev = adv.gather_evidence(ep, "02-script.draft.md")
    out = adv.verify_item({"段": "2", "类": "语言", "原文": "咬牙挤出一句", "问题": "动作堆叠",
                           "判定": "与证据矛盾", "出处": {"类型": "字幕", "引用": "S02E07 99:99"}}, ev)
    assert out["判定"] == "请人看" and out["出处"] == {} and out["核对"] == ""


# ---- 回复解析 ----


def test_parse_reply_accepts_fenced_json_and_rejects_garbage():
    assert adv.parse_reply('```json\n{"items": [{"段": "1"}, 3]}\n```') == [{"段": "1"}]
    assert adv.parse_reply('好的，结果如下：{"items": []}') == []
    for bad in ("没有问题", '{"items": "x"}', "{坏"):
        with pytest.raises(adv.AdversarialError):
            adv.parse_reply(bad)


# ---- 端到端（替身模型）----


def _fake_llm(monkeypatch, content: str) -> list[list[dict]]:
    sent: list[list[dict]] = []

    def fake_chat(messages, tools=None, **kwargs):
        sent.append(messages)
        assert kwargs.get("purpose") == "adversarial" and kwargs.get("timeout") == adv.LLM_TIMEOUT_S
        return {"role": "assistant", "content": content}

    monkeypatch.setattr(llm_mod, "chat_complete", fake_chat)
    monkeypatch.setattr(llm_mod, "load_llm_config",
                        lambda root=None: llm_mod.LLMConfig(base_url="http://x", model="m-test", api_key="k"))
    return sent


REPLY = json.dumps({"items": [
    _fact(),
    _fact(原文="喃喃自语", 判定="无出处", 出处={"类型": "笔记", "引用": "董香在天桥上对西尾锦说"}),
    {"段": "1", "类": "衔接", "原文": "随后她抹掉眼泪", "问题": "上段没交代她为什么哭"},
    {"段": "2", "类": "语言", "原文": "咬牙挤出一句", "问题": "动作堆叠，像翻译腔"},
]}, ensure_ascii=False)


def test_main_writes_report_with_flags_and_verdict_table(ep: Path, monkeypatch, capsys):
    sent = _fake_llm(monkeypatch, REPLY)
    assert adv.main([str(ep)]) == 0
    assert len(sent) == 1 and sent[0][0]["content"] == adv.SYSTEM_PROMPT
    report = (ep / adv.REPORT).read_text(encoding="utf-8")
    assert "审查对象：`02-script.draft.md`" in report and "模型 m-test" in report
    assert "结果：事实 2 条、衔接 1 条、语言 1 条；**审查者引用核不上 1 条**（标 ⚠，先别信）。" in report
    assert "| F1 | 1 | 刚见面就动手 | 说话人存疑 | 与证据矛盾 | 字幕 S02E07 19:54 |" in report
    assert "无出处<br>⚠ 审查者引用不存在（笔记摘句不在笔记里）" in report
    assert "| C1 | 1 | 随后她抹掉眼泪 | 上段没交代她为什么哭 |" in report
    assert "| L1 | 2 | 咬牙挤出一句 | 动作堆叠，像翻译腔 |" in report
    assert report.endswith("| F1 |  |  |\n| F2 |  |  |\n| C1 | 人定 |  |\n| L1 | 人定 |  |\n")
    out = capsys.readouterr().out
    assert "[OK] 已写 02-adversarial.md（审 02-script.draft.md）：事实 2 条（与证据矛盾 1、无出处 1）、衔接 1 条、语言 1 条" in out
    assert "审查者引用核不上 1 条" in out


def test_rerun_keeps_previous_report(ep: Path, monkeypatch):
    _fake_llm(monkeypatch, REPLY)
    (ep / adv.REPORT).write_text("旧报告，终审已填\n", encoding="utf-8")
    assert adv.main([str(ep)]) == 0
    hist = list((ep / "_agent" / "adversarial-history").glob("*.md"))
    assert [p.read_text(encoding="utf-8") for p in hist] == ["旧报告，终审已填\n"]


def test_prefers_final_script_when_present(ep: Path, monkeypatch, capsys):
    _fake_llm(monkeypatch, '{"items": []}')
    (ep / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    assert adv.main([str(ep)]) == 0
    assert "（审 02-script.md）" in capsys.readouterr().out


def test_bad_reply_saves_raw_and_writes_no_report(ep: Path, monkeypatch, capsys):
    _fake_llm(monkeypatch, "我觉得稿子挺好")
    assert adv.main([str(ep)]) == 1
    assert not (ep / adv.REPORT).exists()
    assert (ep / "_agent" / "adversarial-raw.txt").read_text(encoding="utf-8") == "我觉得稿子挺好"
    assert "FAIL 审查者没有返回 JSON 对象" in capsys.readouterr().err


def test_oversized_input_never_calls_model(ep: Path, monkeypatch, capsys):
    sent = _fake_llm(monkeypatch, '{"items": []}')
    monkeypatch.setattr(adv, "MAX_INPUT_CHARS", 100)
    assert adv.main([str(ep)]) == 1
    assert sent == [] and "超过上限 100" in capsys.readouterr().err


def test_egress_block_is_reported(ep: Path, monkeypatch, capsys):
    _fake_llm(monkeypatch, '{"items": []}')

    def blocked(messages, tools=None, **kwargs):
        raise PermissionError("出网断言：命中受限模式 03-audio/manifest.json")

    monkeypatch.setattr(llm_mod, "chat_complete", blocked)
    assert adv.main([str(ep)]) == 1
    assert "出网断言拦下了这次审查" in capsys.readouterr().err


# ---- agent 接入与状态提示 ----


def test_run_pipeline_adversarial_needs_card_and_autofills_episode(ep: Path):
    from pipeline.agent.session import review_tool_call
    from pipeline.agent.tools import validate_pipeline_command

    ok, _msg, argv = validate_pipeline_command("adversarial --script 02-script.draft.md", ep_dir=ep)
    assert ok and argv[-3:] == [str(ep.resolve()), "--script", "02-script.draft.md"]
    verdict = review_tool_call("run_pipeline", {"command": "adversarial"}, ep_dir=ep, scope="creative", root=None)
    assert verdict.action == "ask"


def test_status_hints_until_report_exists(ep: Path):
    from pipeline import status

    hint = "02.8 对抗审查还没跑：`adversarial`（审事实、衔接、语言；只提示不拦）"
    assert hint in status._detect_advisories(ep)
    (ep / adv.REPORT).write_text("x", encoding="utf-8")
    assert hint not in status._detect_advisories(ep)
    (ep / adv.REPORT).unlink()
    (ep / "03-audio").mkdir()
    (ep / "03-audio" / "manifest.json").write_text("{}", encoding="utf-8")
    assert hint not in status._detect_advisories(ep), "已经配音的期不再提示"
