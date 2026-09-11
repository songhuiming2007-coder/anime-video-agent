"""拼音直注层测试（D25 音读泄漏治理）。

对齐 STANDARD 第八节：纯函数测试、内存 fixture、不触碰片源与网络。
"""

from __future__ import annotations

import json

from pipeline import g2p, paths


def test_to_tone3_basic():
    """汉字词 → TONE3 紧凑串，这是 pinyin_injections 表的唯一存储格式。"""
    assert g2p.to_tone3("世界") == "shi4jie4"
    assert g2p.to_tone3("心理测量者") == "xin1li3ce4liang2zhe3"


def test_format_for_engine_tones_every_syllable():
    """qwen3 格式必须**逐音节**转声调符号。

    防的错误（2026-09-11 实测踩到）：把整个紧凑串喂给 `to_tone`，
    `to_tone('xin1li3ce4liang2zhe3')` 返回 `'xinliceliāngzhe'` ——
    声调全丢且有一处标错位置。它对单音节才是对的，所以必须先切分。
    """
    assert g2p.format_for_engine("shi4jie4", "qwen3_tts") == "shìjiè"
    assert (
        g2p.format_for_engine("xin1li3ce4liang2zhe3", "qwen3_tts") == "xīnlǐcèliángzhě"
    )


def test_format_for_engine_indextts2_uses_official_syntax():
    """IndexTTS2 官方语法是「大写字母 + 数字声调」（docs/README_zh.md 的 DE5/XING2 样例）。"""
    assert g2p.format_for_engine("shi4jie4", "indextts2") == "SHI4JIE4"
    assert g2p.format_for_engine("de5", "indextts2") == "DE5"


def test_format_for_engine_neutral_tone_and_umlaut():
    """轻声（第 5 调）与 ü 不能把转换搞崩。"""
    assert g2p.format_for_engine("de5", "qwen3_tts") == "de"
    assert g2p.format_for_engine("nv3hai2", "qwen3_tts") == "nǚhái"


def test_derive_splits_mechanical_from_manual():
    """转不出来的条目必须**显式交出**，不许静默丢弃或用垃圾值填充。

    readings 的替换词里混着拼音（chéng人）、假名（えめ）、英文（egoist），
    对它们跑 pypinyin 只会产出垃圾——所以判据是「替换词是否全汉字」。
    """
    mech, manual = g2p.derive_injections_from_readings(
        {"世界": "逝戒", "成人": "chéng人", "_note": "元数据应被忽略"}
    )
    assert mech == {"世界": "shi4jie4"}
    assert manual == {"成人": "chéng人"}


def test_inject_longest_word_first():
    """长词必须优先命中。

    防的错误：readings 时代靠**人肉控制键的插入顺序**才能让「一个世界」早于
    「世界」命中（voice.json 的 _note 里专门写了一段警告）。按词长降序排序
    把这类坑结构性消除——顺序不再由人保证。
    """
    inj = {"世界": "shi4jie4", "一个世界": "yi2ge4shi4jie4"}

    # 长词优先：整词作为一个注入单元，而不是只把里面的「世界」注掉
    whole, _ = g2p.inject("一个世界", inj, "indextts2")
    assert whole == "YI2GE4SHI4JIE4"

    # 全局子串替换是既有机制（readings 也是 str.replace），所以「另一个世界」中的
    # 「一个世界」子串同样命中——读音仍正确（lìng yí gè shì jiè），属无害的过度覆盖。
    # 记为已知行为而非 bug：改成分词感知替换会引入新依赖与新风险，收益不抵。
    text, applied = g2p.inject("一个世界与另一个世界", inj, "indextts2")
    assert text == "YI2GE4SHI4JIE4与另YI2GE4SHI4JIE4"
    assert applied[0]["from"] == "一个世界", "长词必须排在短词之前"
    assert len(applied) == 1, "一个词条一条记录"
    assert applied[0]["hits"] == 2, "『一个世界』在这句里命中 2 处（含『另一个世界』内的子串）"


def test_inject_is_local_not_wholesale():
    """注入是局部的：未命中的部分原样保留。

    防的错误：全文转拼音会摧毁模型对分词与韵律的既有先验（架构设计三.2 要点 1）。
    """
    text, _ = g2p.inject("世界很大", {"世界": "shi4jie4"}, "indextts2")
    assert text == "SHI4JIE4很大"


def test_inject_records_every_change_for_audit():
    """每次替换都要留痕（进 manifest 的 g2p_injections），机器替换与 readings 一样要可审计。"""
    _, applied = g2p.inject("世界", {"世界": "shi4jie4"}, "qwen3_tts")
    assert applied == [{"from": "世界", "to": "shìjiè", "tone3": "shi4jie4", "hits": 1}]


def test_inject_no_match_returns_original():
    """没命中就一个字都不许动。"""
    src = "完全无关的句子"
    text, applied = g2p.inject(src, {"世界": "shi4jie4"}, "indextts2")
    assert text == src and applied == []


def test_scan_heteronyms_reports_instead_of_blocking():
    """多音字扫描只报告不拦截：返回清单，绝不返回布尔值让调用方去卡门。

    多音字本身不是错误（绝大多数在上下文里读对了），它是给 03.5 顺听的
    重点关注清单。
    """
    hits = g2p.scan_heteronyms("的")
    assert len(hits) == 1
    assert hits[0]["char"] == "的"
    assert len(hits[0]["readings"]) > 1, "『的』应当有多个读音"

    assert g2p.scan_heteronyms("的", covered={"的"}) == [], "已治理的字不再重复报警"


def test_scan_heteronyms_ignores_non_han():
    """非汉字（英文/数字/标点）不该出现在多音字清单里。"""
    assert g2p.scan_heteronyms("Eutelope 123，。") == []


def test_adr0006_leak_cases_are_a_fixed_sample_set():
    """ADR-0006 记载的六例音读泄漏＝项目自己踩出来的清单（E3），固定为回归样本。

    这六条不是从别处抄的，是本项目的历史事故：世界→秀界、全部→权部、早期→藻期、
    监督→坚督、舞台→舞抬、五百→五kaia。拼音直注的**唯一职责**就是让它们读对，
    所以它们进入固定样本集——任何改动只要让其中一条的拼音变了，测试就该红。
    """
    assert set(g2p.ADR0006_LEAK_CASES) == {"世界", "全部", "早期", "监督", "舞台", "五百"}
    for word, expected_upper in g2p.ADR0006_LEAK_CASES.items():
        assert g2p.to_tone3(word).upper() == expected_upper, f"{word} 的拼音不应漂移"


def test_derive_from_real_readings_table_is_total():
    """拿真实 readings 表跑一遍：**一条都不许漏**。

    防的错误：转录逻辑对某些形态的条目静默跳过。总数守恒（机械＋人工 == 表长）
    是「没有静默丢弃」的可证伪判据。
    """
    cfg = json.loads((paths.CONFIG / "voice.json").read_text(encoding="utf-8"))
    readings = {k: v for k, v in cfg["readings"].items() if not k.startswith("_")}
    mech, manual = g2p.derive_injections_from_readings(cfg["readings"])

    assert len(mech) + len(manual) == len(readings), "转录总数必须等于表长"
    assert mech and manual, "实际情况应两者都有"

    for word, tone3 in mech.items():
        for engine in ("indextts2", "qwen3_tts"):
            rendered = g2p.format_for_engine(tone3, engine)
            assert rendered, f"{word} 在 {engine} 下渲染为空"
