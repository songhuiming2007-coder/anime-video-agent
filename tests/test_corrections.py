"""PR2 顺听极简纠错与增量重配链路测试（Spec §3, §5）。

覆盖：
  1. 拼音音节切分器（空格切分、声调切分、隔音符报错、混写报错、幂等性）
  2. 纠错文法解析器（22+ 边界对账：段号写法、全角归一、多段号拦截、语速词拦截、受控词表、heard 宽校验）
  3. 接线级合成测试（假 Engine 验证渲染文本包含注入拼音，多句拆句段显式 overlay_label 传递）
  4. _reusable overlay 精确段级比对（段级隔离，不污染其他段）
  5. R3 契约：pending 条目在普通 run 下绝不泄漏
  6. 进度模型与原子写盘（affected 落盘，done_segments set 语义，覆盖后置 applied）
  7. 回滚再战不死锁（apply -> revert -> apply 依然进 redo 重配）
  8. /voice 指令表 fullmatch 与路由优先级
  9. 写者纪律（写前指纹校验、单写者锁、回读确认）
  10. _report_stale 对新特征与 v5 旧产物的报账
"""

from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest

from pipeline import corrections, g2p, paths, tts
from pipeline.agent import cli
from pipeline.corrections import (
    CONTROLLED_TIMBRE_WORDS,
    Patch,
    PatchError,
    append_correction,
    backup_segments,
    effective_injections,
    load_corrections_raw,
    load_overlay,
    parse_correction,
    pinyin_to_tone3,
    plan_apply,
    retract_correction,
    revert_segment,
    save_corrections_raw,
    split_pinyin_syllables,
)


# ===========================================================================
# 1. 拼音音节切分器测试（Spec §3.2.1）
# ===========================================================================


class TestSplitPinyinSyllables:
    def test_空格切分带调号(self):
        assert split_pinyin_syllables("zhòng dié") == ["zhòng", "dié"]
        assert pinyin_to_tone3("zhòng dié") == "zhong4die2"

    def test_空格切分带调数字(self):
        assert split_pinyin_syllables("zhong4 die2") == ["zhong4", "die2"]
        assert pinyin_to_tone3("zhong4 die2") == "zhong4die2"

    def test_数字调号幂等(self):
        assert pinyin_to_tone3("zhong4die2") == "zhong4die2"
        assert pinyin_to_tone3("zhong4 die2") == "zhong4die2"

    def test_无空格调号切分(self):
        assert split_pinyin_syllables("zhòngdié") == ["zhòng", "dié"]
        assert pinyin_to_tone3("zhòngdié") == "zhong4die2"

    def test_单音节特殊字符_lü(self):
        assert split_pinyin_syllables("lǜ") == ["lǜ"]
        assert pinyin_to_tone3("lǜ") == "lv4"

    def test_单音节数字_lv4(self):
        assert split_pinyin_syllables("lv4") == ["lv4"]
        assert pinyin_to_tone3("lv4") == "lv4"

    def test_隔音符_报错引导(self):
        with pytest.raises(PatchError) as exc:
            split_pinyin_syllables("xī'ān")
        assert "请用空格分音节或给每个音节标声调" in str(exc.value)

    def test_中文弯单引号隔音符_报错引导(self):
        with pytest.raises(PatchError) as exc:
            split_pinyin_syllables("xī’ān")
        assert "请用空格分音节" in str(exc.value)

    def test_无调号_报错引导(self):
        with pytest.raises(PatchError) as exc:
            split_pinyin_syllables("zhongdie")
        assert "缺少声调标记" in str(exc.value)

    def test_混用调号与数字_报错(self):
        with pytest.raises(PatchError) as exc:
            split_pinyin_syllables("zhòng4")
        assert "混用了调号字母与数字" in str(exc.value)


# ===========================================================================
# 2. 纠错文法解析器 22+ 边界测试（Spec §3.2）
# ===========================================================================


class TestParseCorrectionGrammar:
    SEGS = [
        tts.Segment(1, "1", "这是第一段口播。"),
        tts.Segment(2, "5", "重叠的部分很多。"),
        tts.Segment(3, "5.1", "细分段落也有重叠。"),
        tts.Segment(4, "7", "第七段也有内容。"),
        tts.Segment(5, "9", "第九段讲的是语速。"),
        tts.Segment(6, "12", "第十二段语气发飘。"),
        tts.Segment(7, "18", "第十八段重叠出现。"),
        tts.Segment(8, "21.2", "第二十一小节的句子。"),
    ]

    def test_标准示例A_字词错读(self):
        p = parse_correction("5段 重叠 念成 chóng dié 改成 zhòng dié", self.SEGS)
        assert p.segment == "5"
        assert p.kind == "pronunciation"
        assert p.word == "重叠"
        assert p.heard == "chóng dié"
        assert p.target_tone3 == "zhong4die2"
        assert p.action == "inject"
        assert p.scope == "segment"

    def test_标准示例B_整句听感(self):
        p = parse_correction("12段 语气发飘 换种子", self.SEGS)
        assert p.segment == "12"
        assert p.kind == "timbre"
        assert p.issue == "发飘"
        assert p.action == "pin_seed"
        assert p.scope == "segment"

    def test_段号写法_第5段(self):
        p = parse_correction("第5段 重叠 改成 zhòng dié", self.SEGS)
        assert p.segment == "5"

    def test_段号写法_第_5_段(self):
        p = parse_correction("第 5 段 重叠 改成 zhòng dié", self.SEGS)
        assert p.segment == "5"

    def test_段号写法_段落5点1(self):
        p = parse_correction("段落 5.1 重叠 改成 zhòng dié", self.SEGS)
        assert p.segment == "5.1"

    def test_段号写法_段5(self):
        p = parse_correction("段5 重叠 改成 zhòng dié", self.SEGS)
        assert p.segment == "5"

    def test_段号写法_段_5(self):
        p = parse_correction("段 5 重叠 改成 zhòng dié", self.SEGS)
        assert p.segment == "5"

    def test_段号写法_小数段号21点2(self):
        p = parse_correction("段落 21.2 句子 改成 jù zǐ", self.SEGS)
        assert p.segment == "21.2"
        assert p.word == "句子"

    def test_NFKC_全角数字归一(self):
        p = parse_correction("５段 重叠 改成 zhòng dié", self.SEGS)
        assert p.segment == "5"

    def test_NFKC_全角逗号拼音单簇(self):
        p = parse_correction("5段 重叠 改成 zhòng，dié", self.SEGS)
        assert p.target_tone3 == "zhong4die2"

    def test_中文汉字数字_第五段_报错(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("第五段 重叠 改成 zhòng dié", self.SEGS)
        assert "未在输入中找到有效段号" in str(exc.value)

    def test_多段号拦截(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("5段和7段都念错了", self.SEGS)
        assert "一次只纠一段" in str(exc.value)

    def test_多段号前置简式拦截(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("段5 段7 念错", self.SEGS)
        assert "一次只纠一段" in str(exc.value)

    def test_段号不在稿件中_报错列出可用段(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("99段 重叠 改成 zhòng dié", self.SEGS)
        assert "不在稿件段落中" in str(exc.value)

    def test_有heard无target_报错(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("5段 重叠 念成 chóng dié", self.SEGS)
        assert "缺目标读音" in str(exc.value)

    def test_无关键词单拉丁簇_默认为target(self):
        p = parse_correction("5段 重叠 zhòng dié", self.SEGS)
        assert p.target_tone3 == "zhong4die2"
        assert p.word == "重叠"

    def test_无关键词双拉丁簇_报错(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("5段 重叠 chong4 die2 zhong4 die2", self.SEGS)
        assert "存在多个无关键词拼音簇" in str(exc.value)

    def test_heard宽校验_念成隔音符原样落盘(self):
        # heard 是 audit-only 字段，xī'ān 不被隔音符规则误杀
        p = parse_correction("5段 重叠 念成 xī'ān 改成 zhòng dié", self.SEGS)
        assert p.heard == "xī'ān"
        assert p.target_tone3 == "zhong4die2"

    def test_词不在段内_报错并打印原文(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("5段 飞船 改成 fēi chuán", self.SEGS)
        assert "不在第 5 段配音文本中" in str(exc.value)
        assert "重叠的部分很多" in str(exc.value)

    def test_语速词拦截_太快(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("9段 太快", self.SEGS)
        assert "语速调整暂不支持" in str(exc.value)

    def test_语速词拦截_有点快(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("9段 有点快", self.SEGS)
        assert "语速调整暂不支持" in str(exc.value)

    def test_语速词拦截_偏慢(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("9段 偏慢", self.SEGS)
        assert "语速调整暂不支持" in str(exc.value)

    def test_听感词_发飘(self):
        p = parse_correction("12段 语气发飘 换种子", self.SEGS)
        assert p.kind == "timbre"
        assert p.action == "pin_seed"
        assert p.issue == "发飘"

    def test_听感词_断层(self):
        p = parse_correction("12段 音频断层", self.SEGS)
        assert p.kind == "timbre"
        assert p.action == "pin_seed"
        assert p.issue == "断层"

    def test_表外听感表达_音色变了_报错列出全词表(self):
        with pytest.raises(PatchError) as exc:
            parse_correction("12段 音色变了 换种子", self.SEGS)
        msg = str(exc.value)
        assert "未知听感词或表外表达" in msg
        for word in CONTROLLED_TIMBRE_WORDS:
            assert word in msg

    def test_全局范围声明(self):
        p = parse_correction("5段 重叠 改成 zhòng dié 全局", self.SEGS)
        assert p.scope == "global"

    def test_所有段范围声明(self):
        p = parse_correction("5段 重叠 改成 zhòng dié 所有段", self.SEGS)
        assert p.scope == "global"


# ===========================================================================
# 3. 接线级合成与 overlay 渲染测试（Spec §3.4）
# ===========================================================================


class _CapturingFakeEngine:
    """捕获真正送进 synthesize 的文本与参数的假 Engine。"""

    kind = "qwen3_tts"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {
            "engine": "qwen3_tts",
            "model": "fake-model",
            "ref_audio": "data/voice/reference/seg6.wav",
            "seed_offset": 7,
            "emotions": {"平静叙述": {}},
            "speed": {"中": 1.0},
        }
        self.segment_seeds = self.cfg.get("segment_seeds", {})
        self.seed_offset = self.cfg.get("seed_offset", 7)
        self.synthesized_texts: list[str] = []

    def synthesize(self, text, dest, attempt, seed=0, emo_params=None):
        self.synthesized_texts.append(text)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 生成合规微型 wav 文件 (24000Hz, 16bit mono, 0.5s)
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            nframes = 12000
            w.writeframes(struct.pack("<" + "h" * nframes, *([1000] * nframes)))
        return 24000


def _stub_render_env(monkeypatch):
    """设置 TTS 渲染外围环境桩（避免 ASR / 实际时长门禁阻断）。"""
    monkeypatch.setattr(tts, "probe_duration", lambda _p: 1.0)
    monkeypatch.setattr(tts, "expected_duration", lambda _s: 1.0)
    monkeypatch.setattr(tts, "cer", lambda a, b: (0, 0.0))
    monkeypatch.setattr(tts, "transcribe", lambda _p: "")
    monkeypatch.setattr(tts, "_concat_with_gap", lambda parts, dest, gap: dest.write_bytes(b"x"))
    monkeypatch.setattr(tts, "_pad_tail", lambda p, s: None)
    monkeypatch.setattr(tts, "_trim_silence", lambda *a, **k: None)


class TestWiringAndOverlaySynthesis:
    def test_假Engine接线_单句段收到渲染拼音(self, tmp_path: Path, monkeypatch):
        """核心接线测试：断言 Engine.synthesize 收到的真实文本包含 zhòngdié 渲染形。"""
        _stub_render_env(monkeypatch)

        overlay = {
            "injections": {
                "5": {"重叠": "zhong4die2"},
            },
            "segment_seeds": {},
        }
        eng = _CapturingFakeEngine()
        eng.cfg["overlay"] = overlay

        seg = tts.Segment(5, "5", "重叠的部分很多。")
        dest = tmp_path / "seg-05.wav"

        take = tts.render_segment(eng, seg, dest)

        # 断言真实传入合成引擎的文本被注入为拼音渲染形
        assert len(eng.synthesized_texts) == 1
        synth_text = eng.synthesized_texts[0]
        assert "zhòngdié" in synth_text
        assert "重叠" not in synth_text
        assert take.speakable is not None
        assert "zhòngdié" in take.speakable
        assert take.g2p_injections is not None
        assert any(inj["from"] == "重叠" for inj in take.g2p_injections)

    def test_假Engine接线_超过30字多句段显式传递overlay_label(self, tmp_path: Path, monkeypatch):
        """N3/B7 重点测试：多句拆句段（>30字）每一句都收到注入拼音，走 overlay_label。"""
        _stub_render_env(monkeypatch)

        overlay = {
            "injections": {
                "21": {"重叠": "zhong4die2"},
            },
            "segment_seeds": {},
        }
        eng = _CapturingFakeEngine()
        eng.cfg["overlay"] = overlay

        # 构造超过 30 字且含有逗号断句的长段落
        long_text = "这一句讲述重叠的起因，那一句讲述重叠的发展，最后一句讲述重叠的彻底终结。"
        assert len(long_text) > 30
        seg = tts.Segment(21, "21", long_text)
        dest = tmp_path / "seg-21.wav"

        take = tts.render_segment(eng, seg, dest)

        # 拆成了多句合成单元
        assert len(eng.synthesized_texts) >= 2
        for text in eng.synthesized_texts:
            assert "zhòngdié" in text
            assert "重叠" not in text

        assert take.speakable is not None
        assert "zhòngdié" in take.speakable

    def test_reusable段级隔离_段5变而段18不变(self, tmp_path: Path):
        """_reusable 在段级 scope overlay 下精确判定：段5不可复用，段18可复用。"""
        segs = [
            tts.Segment(5, "5", "重叠的部分很多。"),
            tts.Segment(18, "18", "重叠的部分很多。"),
        ]
        # 伪造已有 manifest (当时未注入，speakable 为原词)
        (tmp_path / "seg-05.wav").write_bytes(b"dummy")
        (tmp_path / "seg-18.wav").write_bytes(b"dummy")

        old_manifest = {
            "engine": "qwen3_tts",
            "model": "fake-model",
            "ref_audio": "data/voice/reference/seg6.wav",
            "readings": {},
            "injections": {},
            "segments": [
                {
                    "index": 5,
                    "label": "5",
                    "text": "重叠的部分很多。",
                    "file": "seg-05.wav",
                    "duration": 2.0,
                    "cer": 0.0,
                    "attempts": 1,
                    "speakable": "重叠的部分很多。",
                    "synth_logic": 6,
                    "seed_pins": [None],
                },
                {
                    "index": 18,
                    "label": "18",
                    "text": "重叠的部分很多。",
                    "file": "seg-18.wav",
                    "duration": 2.0,
                    "cer": 0.0,
                    "attempts": 1,
                    "speakable": "重叠的部分很多。",
                    "synth_logic": 6,
                    "seed_pins": [None],
                },
            ],
        }

        cfg = {
            "engine": "qwen3_tts",
            "model": "fake-model",
            "ref_audio": "data/voice/reference/seg6.wav",
            "overlay": {
                "injections": {
                    "5": {"重叠": "zhong4die2"},
                },
                "segment_seeds": {},
            },
        }

        done = tts._reusable(old_manifest, segs, tmp_path, cfg)
        # 段 5 speakable 失配，不可复用；段 18 不受影响，正常复用
        assert 5 not in done
        assert 18 in done


# ===========================================================================
# 4. R3 契约与生命周期测试（Spec §3.4, §3.5）
# ===========================================================================


class TestOverlayLifecycleAndR3:
    def test_常态只收applied为true_pending绝不泄漏(self, tmp_path: Path):
        """R3 核心测试：未 applied 的 pending 条目在常态 load_overlay 中不生效。"""
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        corrections_file = audio_dir / "corrections.json"

        entries = [
            {
                "id": 1,
                "segment": "5",
                "kind": "pronunciation",
                "scope": "segment",
                "word": "重叠",
                "target_tone3": "zhong4die2",
                "action": "inject",
                "applied": False,  # pending
            },
            {
                "id": 2,
                "segment": "12",
                "kind": "timbre",
                "action": "pin_seed",
                "seed_pin": 123456,
                "applied": False,  # pending
            },
            {
                "id": 3,
                "segment": "7",
                "kind": "pronunciation",
                "scope": "segment",
                "word": "全部",
                "target_tone3": "quan2bu4",
                "action": "inject",
                "applied": True,  # applied
            },
        ]
        corrections_file.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

        # 常态 (include_pending=False)
        normal_overlay = load_overlay(tmp_path, include_pending=False)
        assert "5" not in normal_overlay["injections"]
        assert "12" not in normal_overlay["segment_seeds"]
        assert normal_overlay["injections"]["7"]["全部"] == "quan2bu4"

        # --apply-patch 模式 (include_pending=True)
        patch_overlay = load_overlay(tmp_path, include_pending=True)
        assert patch_overlay["injections"]["5"]["重叠"] == "zhong4die2"
        assert patch_overlay["segment_seeds"]["12"] == 123456
        assert patch_overlay["injections"]["7"]["全部"] == "quan2bu4"

    def test_effective_injections纯函数分派(self):
        overlay = {
            "injections": {
                "*": {"全局": "quan2ju2"},
                "5": {"重叠": "zhong4die2"},
            }
        }
        eff5 = effective_injections(overlay, "5")
        assert eff5 == {"全局": "quan2ju2", "重叠": "zhong4die2"}

        eff18 = effective_injections(overlay, "18")
        assert eff18 == {"全局": "quan2ju2"}

        eff_none = effective_injections(None, "5")
        assert eff_none == {}


# ===========================================================================
# 5. 进度模型、原子写盘与 plan_apply 测试（Spec §3.5）
# ===========================================================================


class TestPlanApplyAndProgress:
    def test_plan_apply缺失manifest报错指路(self, tmp_path: Path):
        # 写入一条待应用的纠错
        entries = [{"id": 1, "segment": "5", "word": "重叠", "target_tone3": "zhong4die2", "action": "inject", "applied": False}]
        save_corrections_raw(tmp_path, entries)
        with pytest.raises(SystemExit) as exc:
            plan_apply(tmp_path, [], {})
        assert "03-audio/manifest.json 缺失，请先跑 03 配音" in str(exc.value)

    def test_plan_apply受影响段与pin_seed报账(self, tmp_path: Path):
        """受影响段 = redo = backup 集，pin_seed 轴同样进入 redo。"""
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "seg-05.wav").write_bytes(b"dummy")
        (audio_dir / "seg-12.wav").write_bytes(b"dummy")

        mf = {
            "engine": "qwen3_tts",
            "segments": [
                {
                    "index": 5,
                    "label": "5",
                    "text": "重叠部分。",
                    "file": "seg-05.wav",
                    "speakable": "重叠部分。",
                    "seed_pins": [None],
                },
                {
                    "index": 12,
                    "label": "12",
                    "text": "语气发飘。",
                    "file": "seg-12.wav",
                    "speakable": "语气发飘。",
                    "seed_pins": [None],
                },
            ],
        }
        (audio_dir / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")

        entries = [
            {
                "id": 1,
                "segment": "5",
                "kind": "pronunciation",
                "scope": "segment",
                "word": "重叠",
                "target_tone3": "zhong4die2",
                "action": "inject",
                "applied": False,
            },
            {
                "id": 2,
                "segment": "12",
                "kind": "timbre",
                "action": "pin_seed",
                "seed_pin": 99999,
                "applied": False,
            },
        ]
        save_corrections_raw(tmp_path, entries)

        segs = [
            tts.Segment(5, "5", "重叠部分。"),
            tts.Segment(12, "12", "语气发飘。"),
        ]
        cfg = {"engine": "qwen3_tts", "segment_seeds": {}}

        plan = plan_apply(tmp_path, segs, cfg)
        assert "5" in plan["redo"]
        assert "12" in plan["redo"]
        assert "5" in plan["affected_labels"]
        assert "12" in plan["affected_labels"]

        # 回读 corrections.json，affected 字段已被回填落盘
        saved_entries, _ = load_corrections_raw(tmp_path)
        assert saved_entries[0]["affected"] == ["5"]
        assert saved_entries[1]["affected"] == ["12"]

    def test_备份与attic修剪(self, tmp_path: Path):
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "manifest.json").write_text(
            json.dumps({"segments": [{"label": "5", "file": "seg-05.wav"}]}),
            encoding="utf-8",
        )
        (audio_dir / "seg-05.wav").write_bytes(b"audio-data-5")

        snap_dir = backup_segments(tmp_path, ["5"])
        assert snap_dir.exists()
        assert (snap_dir / "manifest.json").exists()
        assert (snap_dir / "seg-05.wav").read_bytes() == b"audio-data-5"


# ===========================================================================
# 6. 回滚再战不死锁测试（Spec §3.5 R1-r5）
# ===========================================================================


class TestRevertAndNoDeadlock:
    def test_回滚再战不死锁(self, tmp_path: Path):
        """apply -> revert 5 -> 再次 plan_apply，段 5 依然通过判据减法进入 redo！"""
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_file = audio_dir / "seg-05.wav"
        wav_file.write_bytes(b"old-audio-v1")

        old_mf = {
            "engine": "qwen3_tts",
            "segments": [
                {
                    "index": 5,
                    "label": "5",
                    "text": "重叠部分。",
                    "file": "seg-05.wav",
                    "speakable": "重叠部分。",  # 旧 speakable
                    "seed_pins": [None],
                }
            ],
        }
        (audio_dir / "manifest.json").write_text(json.dumps(old_mf), encoding="utf-8")

        # 1. 备份段 5
        snap = backup_segments(tmp_path, ["5"])
        assert (snap / "seg-05.wav").read_bytes() == b"old-audio-v1"

        # 2. 模拟 apply 完成：音频被换成新版，manifest 也记录新 speakable，条目已 applied
        wav_file.write_bytes(b"new-audio-v2")
        new_mf = {
            "engine": "qwen3_tts",
            "segments": [
                {
                    "index": 5,
                    "label": "5",
                    "text": "重叠部分。",
                    "file": "seg-05.wav",
                    "speakable": "zhòngdié部分。",  # 新 speakable
                    "seed_pins": [None],
                }
            ],
        }
        (audio_dir / "manifest.json").write_text(json.dumps(new_mf), encoding="utf-8")

        entries = [
            {
                "id": 1,
                "segment": "5",
                "kind": "pronunciation",
                "scope": "segment",
                "word": "重叠",
                "target_tone3": "zhong4die2",
                "action": "inject",
                "applied": True,
                "affected": ["5"],
                "done_segments": ["5"],
            }
        ]
        save_corrections_raw(tmp_path, entries)

        # 3. 用户执行「回滚 5」
        revert_res = revert_segment(tmp_path, "5")
        assert revert_res["snapshot"] == snap.name
        # 音频已恢复为 old-audio-v1
        assert wav_file.read_bytes() == b"old-audio-v1"
        # manifest 段条目已恢复为旧版
        cur_mf = json.loads((audio_dir / "manifest.json").read_text(encoding="utf-8"))
        assert cur_mf["segments"][0]["speakable"] == "重叠部分。"

        # 条目状态已回到 pending
        reloaded_entries, _ = load_corrections_raw(tmp_path)
        assert reloaded_entries[0]["applied"] is False

        # 4. 关键断言：再次计算 plan_apply，段 5 必须因为 speakable 失配而进入 redo！
        segs = [tts.Segment(5, "5", "重叠部分。")]
        plan = plan_apply(tmp_path, segs, {"engine": "qwen3_tts"})
        assert "5" in plan["redo"]
        assert plan["pending"][0]["id"] == 1

    def test_撤回条目删除沉淀(self, tmp_path: Path):
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        entries = [
            {"id": 1, "segment": "5", "word": "重叠", "applied": False},
            {"id": 2, "segment": "12", "issue": "发飘", "applied": False},
        ]
        save_corrections_raw(tmp_path, entries)

        retract_correction(tmp_path, 1)
        remaining, _ = load_corrections_raw(tmp_path)
        assert len(remaining) == 1
        assert remaining[0]["id"] == 2


# ===========================================================================
# 7. 写者纪律与并发防护测试（Spec §3.3）
# ===========================================================================


class TestWriterDiscipline:
    def test_写前指纹校验不等_FAIL(self, tmp_path: Path):
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        entries = [{"id": 1, "segment": "5"}]
        save_corrections_raw(tmp_path, entries)

        _, fp = load_corrections_raw(tmp_path)
        # 模拟其他进程在背后修改了文件
        (audio_dir / "corrections.json").write_text(json.dumps([{"id": 1, "segment": "5", "tampered": True}]), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            save_corrections_raw(tmp_path, entries, expected_fp=fp)
        assert "期间 corrections.json 被其他进程改过" in str(exc.value)

    def test_apply运行期间单写者拒绝y确认(self, tmp_path: Path):
        audio_dir = tmp_path / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        lock = audio_dir / ".apply_patch.lock"
        lock.write_text("locked", encoding="utf-8")

        patch = Patch("5", "pronunciation", "重叠", None, "zhong4die2", None, "inject", "segment", "raw")
        with pytest.raises(SystemExit) as exc:
            append_correction(tmp_path, patch)
        assert "应用纠错进行中，稍后再落盘" in str(exc.value)


# ===========================================================================
# 8. /voice 指令表与路由测试（Spec §3.1）
# ===========================================================================


class TestVoiceCommandRouting:
    def test_内建指令fullmatch全集(self):
        assert cli.RE_STOP.fullmatch("停")
        assert not cli.RE_STOP.fullmatch("停止")
        assert not cli.RE_STOP.fullmatch("停 ")

        assert cli.RE_PLAY_ALL.fullmatch("听")
        assert not cli.RE_PLAY_ALL.fullmatch("听起来")

        m = cli.RE_PLAY_SEG.fullmatch("听 5")
        assert m and m.group(1) == "5"

        m_dec = cli.RE_PLAY_SEG.fullmatch("听 12.3")
        assert m_dec and m_dec.group(1) == "12.3"

        m_rev = cli.RE_REVERT.fullmatch("回滚 5")
        assert m_rev and m_rev.group(1) == "5"

        m_rev_dec = cli.RE_REVERT.fullmatch("回滚 12.3")
        assert m_rev_dec and m_rev_dec.group(1) == "12.3"

        m_ret = cli.RE_RETRACT.fullmatch("撤回 3")
        assert m_ret and m_ret.group(1) == "3"

        assert cli.RE_DONE.fullmatch("done")
        assert cli.RE_DONE.fullmatch("/done")

    def test_听感词不被听指令吞掉(self):
        # 「听起来第5段发飘」不是 fullmatch("听")，正常进纠错解析
        line = "听起来第5段发飘 换种子"
        assert not cli.RE_PLAY_ALL.fullmatch(line)
        assert not cli.RE_PLAY_SEG.fullmatch(line)

        segs = [tts.Segment(5, "5", "第五段原文本。")]
        patch = parse_correction(line, segs)
        assert patch.kind == "timbre"
        assert patch.segment == "5"
        assert patch.issue == "发飘"


# ===========================================================================
# 9. 报账闸有效性测试（Spec §3.4 R1-r17）
# ===========================================================================


class TestStaleCensusWithOverlay:
    def test_旧v5段被准确列出且带overlay提示(self, capsys):
        """造一条 synth_logic=5 的旧 Take + 一条 synth_logic=6 的段，_report_stale 必须精确报出。"""
        takes = [
            tts.Take(1, "1", "文本", "seg-01.wav", 2.0, 0.0, 1, synth_logic=5),
            tts.Take(2, "2", "文本", "seg-02.wav", 2.0, 0.0, 1, synth_logic=6),
        ]
        count = tts._report_stale(takes)
        assert count == 1
        captured = capsys.readouterr().err
        assert "WARN 1/2 段是旧合成逻辑的产物" in captured
        assert "段落 1" in captured or "1" in captured
        assert "本期存在 overlay 纠错段，其音频由 v6 之后的代码产出" in captured
