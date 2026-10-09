"""D51：agent 经人审卡录读音纠错（plans/2026-10-08-agent-voice-corrections-spec.md）。

全部临时夹具：期目录与 voice.json 都在 tmp_path，`corrections.VOICE_CONFIG` 打桩指过去，
真实 config/voice.json 与董香二期一律不碰。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import corrections

SCRIPT = (
    "# 测试期\n\n"
    "## 段落 1\n\n配音：雾岛绚都踹开了门。\n\n画面：\n  查询: 门\n\n"
    "## 段落 2\n\n配音：她强忍着肉体的排斥。\n\n画面：\n  查询: 便当\n\n"
    "## 段落 3\n\n配音：绚都转身离开，少年的背影很远。\n\n画面：\n  查询: 背影\n"
)

VOICE = {
    "_note": "夹具",
    "engine": "qwen3_tts",
    "readings": {"乐迷": "月迷", "绚都": "绚督"},
    "pinyin_injections": {"_note": "TONE3", "喰种": "can1zhong3", "绚都": "xuan4du1"},
}


@pytest.fixture
def episode(tmp_path: Path) -> Path:
    ep = tmp_path / "data" / "episodes" / "T1"
    (ep / "03-audio").mkdir(parents=True)
    (ep / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    return ep


@pytest.fixture
def voice(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "config" / "voice.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(VOICE, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(corrections, "VOICE_CONFIG", path)
    return path


# ---- TV-1～4：读音校验 ----


def test_tv1_wrong_homophone_rejected():
    """TV-1：董香二期 agent 给的「肉惕」读 rou4 ti4，不是 rou4 ti3（D53 ④）。"""
    check = corrections.check_reading("肉体", "rou4ti3", "肉惕")
    assert not check.ok
    assert check.got == ["rou4", "ti4"] and check.expect == ["rou4", "ti3"]


def test_tv1b_synonym_is_not_homophone():
    """D51 ③：同义词替换（年轻人）在逐音节比对这一步必然被拒。"""
    assert not corrections.check_reading("少年", "shao4 nian2", "年轻人").ok


@pytest.mark.parametrize("word, homophone, expect", [
    ("绚都", "炫嘟", "xuan4du1"),
    ("肉体", "ròu体", "ròu tǐ"),   # 全局 readings 里真实存在的拼音汉字混写
])
def test_tv2_valid_homophones_pass(word, homophone, expect):
    check = corrections.check_reading(word, expect, homophone)
    assert check.ok, check.lines()
    assert check.warnings == []


def test_tv3_syllable_count_must_match_word_length():
    check = corrections.check_reading("绚都", "xuan4")
    assert not check.ok
    assert "2 个字" in check.errors[0]


def test_tv3b_invalid_syllable_rejected():
    assert not corrections.check_reading("绚都", "xuan4 abc1").ok


def test_tv4_reading_outside_dictionary_only_warns():
    """TV-4：专名读法可能不在词典里——拿不到证伪信息不定罪（判据 4），只警告。"""
    check = corrections.check_reading("绚都", "xuan4 da1")
    assert check.ok
    assert len(check.warnings) == 1 and "dou1/du1" in check.warnings[0]


# ---- TV-5：期级 add ----


def test_tv5_add_writes_same_entry_shape_as_voice_add(episode: Path, tmp_path: Path):
    """与人的入口 `/voice-add` 同路：条目内容一致（时间戳除外）。

    对照组直接调 `/voice-add` 内部的同一对函数（`cli._cmd_voice_add` 本身按真实仓库根解析期目录，
    夹具进不去）。
    """
    from pipeline import tts

    text = "3段 绚都 改成 xuan4du1"
    assert corrections.main(["add", str(episode), "--text", text]) == 0

    twin = tmp_path / "data" / "episodes" / "T2"
    (twin / "03-audio").mkdir(parents=True)
    (twin / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    corrections.append_correction(
        twin, corrections.parse_correction(text, tts.parse_script(twin / "02-script.md"))
    )

    mine = corrections.load_corrections_raw(episode)[0]
    human = corrections.load_corrections_raw(twin)[0]
    assert len(mine) == len(human) == 1
    drop = {"created_at"}
    assert {k: v for k, v in mine[0].items() if k not in drop} == {
        k: v for k, v in human[0].items() if k not in drop
    }
    assert mine[0]["word"] == "绚都" and mine[0]["target_tone3"] == "xuan4du1"
    assert mine[0]["applied"] is False


def test_tv5b_add_rejects_bad_reading_without_writing(episode: Path):
    assert corrections.main(["add", str(episode), "--text", "3段 绚都 改成 xuan4"]) == 1
    assert corrections.load_corrections_raw(episode)[0] == []


# ---- TV-6～8：全局写入 ----


def test_tv6_global_pinyin_changes_one_key_only(voice: Path, monkeypatch):
    monkeypatch.setattr(corrections, "_today", lambda: "2026-10-09")
    before = voice.read_text(encoding="utf-8")
    assert corrections.main(["global", "--word", "肉体", "--pinyin", "rou4 ti3"]) == 0
    after = json.loads(voice.read_text(encoding="utf-8"))
    expected = json.loads(before)
    expected["pinyin_injections"]["肉体"] = "rou4ti3"
    # N12：同一次写入在文件末尾的来历表里记一条（没给期目录：只记表与日期）
    expected["readings_provenance"] = {"肉体": {"table": "pinyin_injections", "date": "2026-10-09"}}
    assert after == expected
    # 其余字节不变：新文本 = 旧文本只多出这个键与它的来历（格式与原文件同为 indent=2、无尾换行）
    assert voice.read_text(encoding="utf-8") == json.dumps(expected, ensure_ascii=False, indent=2)


def test_n12_provenance_from_episode_manifest_and_topic(voice: Path, episode: Path, monkeypatch):
    """N12：给了期目录 → 来历带期号、番名（01-topic.md）、引擎与音色（本期配音清单）；supersede 后来历换成新条目的。"""
    monkeypatch.setattr(corrections, "_today", lambda: "2026-10-09")
    (episode / "01-topic.md").write_text("# 选题\n番: 东京喰种\n", encoding="utf-8")
    (episode / "03-audio" / "manifest.json").write_text(
        json.dumps({"engine": "qwen3_tts_cuda", "ref_audio": "assets/voice/seg6.wav", "segments": []}), encoding="utf-8")
    assert corrections.main(["global", str(episode), "--word", "绚都", "--homophone", "炫嘟",
                             "--expect", "xuan4du1", "--supersede"]) == 0
    cfg = json.loads(voice.read_text(encoding="utf-8"))
    assert cfg["readings_provenance"]["绚都"] == {
        "table": "readings", "date": "2026-10-09", "episode": "T1", "anime": "东京喰种",
        "engine": "qwen3_tts_cuda", "voice": "seg6.wav"}
    ok, lines = corrections.preview(["global", str(episode), "--word", "肉体", "--pinyin", "rou4ti3"])
    assert ok and lines[-1] == "来历记录：期 T1、番 东京喰种、引擎 qwen3_tts_cuda、音色 seg6.wav、2026-10-09"


def test_n12_provenance_skips_what_it_cannot_read(voice: Path, episode: Path, monkeypatch):
    """没有 01-topic.md、配音清单坏掉：只记拿得到的（期号），不猜。"""
    monkeypatch.setattr(corrections, "_today", lambda: "2026-10-09")
    (episode / "03-audio" / "manifest.json").write_text("{坏", encoding="utf-8")
    assert corrections.main(["global", str(episode), "--word", "肉体", "--pinyin", "rou4ti3"]) == 0
    cfg = json.loads(voice.read_text(encoding="utf-8"))
    assert cfg["readings_provenance"]["肉体"] == {"table": "pinyin_injections", "date": "2026-10-09", "episode": "T1"}


def test_tv7_same_key_conflict_needs_supersede(voice: Path):
    """TV-7：「绚都」拼音表已有 → 加同音字不带 --supersede 拒（否则是死条目）；带了则删拼音同键。"""
    before = voice.read_bytes()
    args = ["global", "--word", "绚都", "--homophone", "炫嘟", "--expect", "xuan4du1"]
    assert corrections.main(args) == 1
    assert voice.read_bytes() == before

    assert corrections.main([*args, "--supersede"]) == 0
    cfg = json.loads(voice.read_text(encoding="utf-8"))
    assert cfg["readings"]["绚都"] == "炫嘟"
    assert "绚都" not in cfg["pinyin_injections"]


def test_tv8_concurrent_edit_is_not_overwritten(voice: Path, monkeypatch):
    """TV-8：读取后文件被人改过 → 拒，人改的内容保留。"""
    real_plan = corrections.plan_global_write

    def plan_then_human_edits(*a, **k):
        plan = real_plan(*a, **k)
        cfg = json.loads(voice.read_text(encoding="utf-8"))
        cfg["readings"]["人改的"] = "人改"
        voice.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    monkeypatch.setattr(corrections, "plan_global_write", plan_then_human_edits)
    assert corrections.main(["global", "--word", "肉体", "--pinyin", "rou4ti3"]) == 1
    cfg = json.loads(voice.read_text(encoding="utf-8"))
    assert cfg["readings"]["人改的"] == "人改"
    assert "肉体" not in cfg["pinyin_injections"]


def test_tv8b_nonstandard_layout_refused(voice: Path):
    """排版不是标准 JSON 输出（人手改过缩进）→ 拒，免得写回时改动其他行。"""
    voice.write_text(json.dumps(VOICE, ensure_ascii=False, indent=4), encoding="utf-8")
    before = voice.read_bytes()
    assert corrections.main(["global", "--word", "肉体", "--pinyin", "rou4ti3"]) == 1
    assert voice.read_bytes() == before


def test_global_reports_affected_segments(voice: Path, episode: Path, capsys):
    assert corrections.main(["global", str(episode), "--word", "少年", "--pinyin", "shao4nian2"]) == 0
    assert "本期含该词的段：3" in capsys.readouterr().out


# ---- 预检（弹卡前，不落盘） ----


def test_preview_reports_plan_without_writing(voice: Path, episode: Path):
    before = voice.read_bytes()
    ok, lines = corrections.preview(
        ["global", str(episode), "--word", "绚都", "--homophone", "炫嘟", "--expect", "xuan4du1", "--supersede"]
    )
    assert ok, lines
    text = "\n".join(lines)
    assert "config/voice.json 的 readings" in text
    assert "同时删除 pinyin_injections 里的「绚都」→ xuan4du1" in text
    assert "本期含该词的段：1、3" in text
    assert voice.read_bytes() == before


def test_preview_rejects_bad_homophone(voice: Path, episode: Path):
    ok, lines = corrections.preview(
        ["global", str(episode), "--word", "肉体", "--homophone", "肉惕", "--expect", "rou4ti3"]
    )
    assert not ok
    assert any("rou4 ti4" in ln for ln in lines)
