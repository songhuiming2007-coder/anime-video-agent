"""配音：稿件解析、合成单元切分、回读比对。

这三块各自踩过一个坑，都固化在下面：

- `split_sentences` 不是「按句号切」那么简单。段 4 那句 40 字的长句一口气念完 6.79 秒，
  明显比别处快还吞字——模型对整句时长有先验，单元越长压得越狠。
  所以超过 30 字要在逗号处再断。
- `cer` 必须同时返回编辑距离和比率。十个字的段落错一个同音字就是 10%，
  而那只是 ASR 自己听岔了，不是配音出了问题。
- `normalize` 决定了上面两件事的「字数」口径：标点不算字。
"""

import json
import numpy as np
import re
import struct
import wave

import pytest

from pipeline import paths
from pipeline import tts as t


class TestNormalize:
    def test_去标点转小写(self):
        assert t.normalize("八幡，自爆！ABC") == "八幡自爆abc"

    def test_引号破折号省略号全去掉(self):
        assert t.normalize('说"是"——那 (真的) 吗…') == "说是那真的吗"

    def test_空白也去掉(self):
        # ASR 输出常带空格，比对时不能因为空格算成错字
        assert t.normalize("八幡 自爆 了") == "八幡自爆了"

    def test_他她它折叠成一个(self):
        assert t.normalize("她它牠") == "他他他"
        assert t.normalize("妳") == "你"

    def test_读音不同的不折叠(self):
        # 的/得/地 看着像同一档，但「得」有 dé、「地」有 dì，
        # 折了会掩盖真的念错。只折读音完全一致的。
        assert t.normalize("的得地") == "的得地"


class TestHomophoneFalseFailure:
    """他/她/它 同音导致的假失败。**2026-07-30 真的卡停过一期配音。**

    「她帮你，但她不让你欠她。」十个字里三个「她」被听成「他」，
    CER 30% + 绝对错字数 3，两条阈值一起越线，重试三次全一样，整期退出——
    **而合成出来的音频完全正确。**

    回读质检要抓的是漏读、重复、跑飞，也就是「有没有念对声音」。
    ASR 不产出能区分他/她的信息，拿这个维度比对得到的不是证据，是噪声。
    """

    def test_性别代词差异不算错(self):
        assert t.cer("她帮你，但她不让你欠她。", "他帮你但他不让你欠他") == (0, 0.0)

    def test_漏读仍然抓得住(self):
        edits, rate = t.cer("她帮你，但她不让你欠她。", "她帮你")
        assert edits >= t.MIN_EDITS and rate > t.MAX_CER

    def test_跑飞仍然抓得住(self):
        edits, rate = t.cer("她帮你，但她不让你欠她。", "完全不相干的一句话在这里")
        assert edits >= t.MIN_EDITS and rate > t.MAX_CER

    def test_重复仍然抓得住(self):
        edits, rate = t.cer("她帮你。", "她帮你帮你帮你帮你")
        assert edits >= t.MIN_EDITS


class TestProperNoun:
    """专有名词同音字导致的假失败。**2026-08-03 真的卡停过一期配音。**

    第 11 段那句「户冢彩加这一位你早就在等了吧。」重试三次全一样：
    回读听成「户种采家…」，14 个字里 3 个错，CER 21% + 绝对错数 3，
    两条阈值一起越线，整期退出——**而合成出来的音频完全正确。**

    这不是个例。同一次实测里每一个日文专名都被听错，长段落只是靠字数
    把错误率稀释到门槛下侥幸过关。按字形比 CER，量到的是
    「ASR 认不认识这个人名」，不是「TTS 念没念对」。
    """

    def test_户冢彩加听成户种采家不算错(self):
        assert t.cer("户冢彩加这一位你早就在等了吧。", "户种采家这一位你早就在等了吧") == (0, 0.0)

    def test_川崎沙希听成穿其沙西不算错(self):
        assert t.cer("川崎沙希", "穿其沙西") == (0, 0.0)

    def test_雪之下雪乃听成雪之下雪奶不算错(self):
        assert t.cer("雪之下雪乃", "雪之下雪奶") == (0, 0.0)

    def test_声调不同仍然算错(self):
        # 户中（zhōng）与 户冢（zhǒng）声调不同，是真的不同音，不该被折掉。
        # 它单独一处不会判失败——MIN_EDITS 那道闸放行——但必须被数出来。
        edits, _ = t.cer("户冢彩加", "户中采家")
        assert edits == 1

    def test_短句里的专名不再顶穿阈值(self):
        # 这一条就是卡停那一段。修之前 edits=3、CER=21%，两条同时越线。
        edits, rate = t.cer("户冢彩加这一位你早就在等了吧。", "户种采家这一位你早就在等了吧")
        assert not (edits >= t.MIN_EDITS and rate > t.MAX_CER)


class TestSyllables:
    def test_汉字转带声调拼音(self):
        assert t.syllables("八幡") == ["ba1", "fan1"]

    def test_声调保留所以得和地不会被误折(self):
        # `_FOLD` 当初不敢碰「的/得/地」，怕掩盖真的念错。带声调拼音自动把它们分开。
        assert t.syllables("得") != t.syllables("地")

    def test_非汉字逐字保留(self):
        # 稿子里出现过 yy、coding、2件事。它们必须原样留下且不打乱对齐，
        # 否则参考文本与回读文本的音节数会错位，CER 凭空变大。
        assert t.syllables("y2") == ["y", "2"]


class TestCer:
    def test_一个错字(self):
        # 「幡」与「番」都是 fan1，按声音比这两个没有差别——**这正是本次修改的目的**。
        # 要构造一个真的错字，得挑一个读音也不同的。
        assert t.cer("八幡自爆", "八幡自保") == (1, 0.25)

    def test_同音字不再算错(self):
        assert t.cer("八幡自爆", "八番自爆") == (0, 0.0)

    def test_只差标点算完全一致(self):
        # 这正是要先 normalize 的原因：ASR 不还原标点，不去掉的话每段都判不合格
        assert t.cer("八幡自爆", "八幡，自爆！") == (0, 0.0)

    def test_两个都空(self):
        assert t.cer("", "") == (0, 0.0)

    def test_原文为空时不除零(self):
        assert t.cer("", "啊") == (1, 1.0)

    def test_返回两个数字而不是只返回比率(self):
        # 短段落上比率极不稳。判定时比率与绝对错字数要同时越线，
        # 少返回一个数就没法这么判。
        edits, rate = t.cer("啊哦", "啊呃")
        assert edits == 1 and rate == 0.5


class TestSpeakable:
    """送进合成器前剥掉念不出来的符号。

    2026-07-30 实测 IndexTTS 把弯引号当字符各吐一个音节：
    「谁更该“赢”。」→「谁更该**非赢匪**」，「她那句“好啊”后面」→「她那句**非好啊非**」。

    **而门禁放行了它。** `normalize` 早就把引号算进 `_DROP`，所以参考文本本来就没引号，
    回读只多出两个字、算 2 处插入，CER 7% 远低于 20% 的门槛——
    要人听出来才发现。这是「门禁测的不是它自称在测的东西」的又一例。
    """

    def test_弯引号剥掉(self):
        assert t.speakable("谁更该“赢”。") == "谁更该赢。"

    def test_各类引号括号都剥(self):
        for s in ('"a"', "'a'", "「a」", "『a』", "《a》", "（a）", "(a)", "【a】", "[a]"):
            assert t.speakable(s) == "a", s

    def test_控制停顿的标点必须保留(self):
        # 逗号、句号、破折号决定 IndexTTS 在哪断气口。剥了语速会乱。
        # 破折号实测不会被念出音（回读里它安静地消失了），所以留着只有好处。
        s = "注意，她没说啥客套话。就在这儿——你永远不用猜。"
        assert t.speakable(s) == s

    def test_字幕那一侧不受影响(self):
        # 剥的只是喂给合成器的那份；字幕用原文，引号照常显示
        raw = "你永远不用猜她那句“好啊”后面藏着什么。"
        assert t.speakable(raw) != raw and "“" in raw

    def test_读音表替换只发生在合成侧(self, monkeypatch):
        # IndexTTS 念错多音字/生僻字只能换同音字（喰种→餐种、绚都→绚督），
        # 替换进合成文本；字幕用原文，「喰种」照常显示。
        # v2 起 readings 是链路末端的一层，单测它必须把拼音注入层清空——
        # 否则测的是「拼音表覆盖了什么」，不是「readings 还生不生效」。
        monkeypatch.setattr(t, "_injections", lambda: {})
        monkeypatch.setattr(t, "_readings",
                            lambda: {"喰种": "餐种", "绚都": "绚督"})
        assert t.speakable("东京喰种里陪着绚都") == "东京餐种里陪着绚督"

    def test_拼音直注优先于读音表(self, monkeypatch):
        """v2 链路顺序（架构设计三.2）：剥符号 → g2p.inject → readings override。

        已在拼音表里的词不再走同音字替换。**这个顺序有实质含义**：拼音对模型是
        物理阻断（无日语语义映射可借调），同音字是概率规避（换个字赌它不漂）。
        顺序反了，注音后的拼音串会让 readings 完全失效。
        """
        monkeypatch.setattr(t, "_injections", lambda: {"世界": "shi4jie4"})
        monkeypatch.setattr(t, "_readings", lambda: {"世界": "逝戒"})
        assert t.speakable("世界", "indextts2") == "SHI4JIE4"
        assert t.speakable("世界", "qwen3_tts") == "shìjiè"

    def test_读音表兜住拼音表未覆盖的残留个例(self, monkeypatch):
        """readings 降级为「逐案 override」后仍有职责：接住非纯汉字的替换词。

        `成人→chéng人` 这类条目替换词含拉丁字母，机械转录会产垃圾，
        所以它**不在**拼音表里——正是靠 readings 这层兜住。
        """
        monkeypatch.setattr(t, "_injections", lambda: {})
        monkeypatch.setattr(t, "_readings", lambda: {"成人": "chéng人"})
        assert t.speakable("成人") == "chéng人"

    def test_读音表是词级替换_单字不全局替换(self, monkeypatch):
        # 键必须是词。若全局替换「都」，会把念 dōu 对的句子改错
        monkeypatch.setattr(t, "_readings", lambda: {"绚都": "绚督"})
        assert t.speakable("我们都能去") == "我们都能去"


class TestExpectedDuration:
    def test_按去标点后的字数算(self):
        # 标点不发音，算进去会把估算时长撑长，DUR_BAND 的上下界就跟着偏
        assert t.expected_duration("八幡，自爆！") == t.expected_duration("八幡自爆")

    def test_与_CPM_一致(self):
        assert t.expected_duration("八幡自爆了") == pytest.approx(5 / t.CPM * 60)

    def test_CPM_与_config_同源(self):
        # D14：tts.py 曾经把 CPM 写死成字面量 280，和 check_script.py 读同一个
        # config 键各写各的数——两处「同源」的注释成了谎言。这条断言测的正是
        # 分叉本身：CPM 必须来自 config，不能是任何硬编码字面量。
        from pipeline import paths as pl
        assert t.CPM == pl.conf("script.cpm", 380)


class TestSplitSentences:
    def test_按句末标点切且保留标点(self):
        assert t.split_sentences("八幡自爆了。他不是不会说话？对！") == [
            "八幡自爆了。", "他不是不会说话？", "对！"]

    def test_短句不再拆(self):
        assert t.split_sentences("那不是牺牲。") == ["那不是牺牲。"]

    def test_超长句在逗号处再断(self):
        # 就是踩坑的那一句，40 字。断成两段之后语速才稳。
        s = "在户部告白之前，当着所有人的面走过去，说我从很早以前就开始喜欢你了，请和我交往吧。"
        out = t.split_sentences(s)
        assert len(out) == 2
        assert all(len(t.normalize(x)) <= t.MAX_SYNTH_CHARS for x in out)
        # 断卡不能丢字——合成单元拼起来必须还是原句
        assert "".join(out) == s

    def test_没有逗号的长句只能整句合成(self):
        # 已知局限，写下来免得下次当 bug 查：没有可断点就断不了，
        # 这种句子该在写稿阶段被 check_script 的「无超 40 字长句」拦下。
        long = "啊" * 50
        assert t.split_sentences(long) == [long]

    def test_空串(self):
        assert t.split_sentences("") == []


class TestSplitAtCommas:
    def test_太短的尾巴并回上一段(self):
        # 否则会留下三五个字的碎片单独合成，那段音频的语调完全不对
        assert t._split_at_commas("啊" * 16 + "，" + "哦" * 3) == ["啊" * 16 + "，" + "哦" * 3]

    def test_尾巴并不回去就单独成段(self):
        # 16 + 25 超过 30 字上限，并回去等于没断
        out = t._split_at_commas("啊" * 16 + "，" + "哦" * 25)
        assert len(out) == 2

    def test_逗号太靠前不断(self):
        # 断点要求累计至少半个上限（15 字），否则会切出一串短碎片
        assert len(t._split_at_commas("啊，" + "哦" * 25)) == 1


class TestParseScript:
    def _write(self, tmp_path, text):
        f = tmp_path / "02-script.md"
        f.write_text(text, encoding="utf-8")
        return f

    def test_解析段落与编号(self, tmp_path):
        f = self._write(tmp_path,
                        "# 标题\n\n## 段落 1\n\n配音：第一段。\n\n画面：\n  查询: 甲\n\n"
                        "## 段落 2\n\n配音：第二段。\n\n画面：\n  查询: 乙\n")
        segs = t.parse_script(f)
        assert [s.index for s in segs] == [1, 2]
        assert [s.text for s in segs] == ["第一段。", "第二段。"]

    def test_只取配音行不取查询行(self, tmp_path):
        # 查询是给检索用的，混进配音会被念出来
        f = self._write(tmp_path, "## 段落 1\n\n配音：正文。\n\n画面：\n  查询: 不该被念\n")
        assert [s.text for s in t.parse_script(f)] == ["正文。"]

    def test_解析不出就报错不静默返回空(self, tmp_path):
        # 静默返回空的话，tts 会「成功」生成一个零段落的音频目录，
        # 一路跑到渲染才发现没声音
        f = self._write(tmp_path, "# 只有标题\n")
        with pytest.raises(SystemExit):
            t.parse_script(f)


class TestQcSkipExemption:
    """ASR 盲区豁免：3 次不同种子全不达标 + 时长达标 → 保留音频标 qc_skip。

    2026-08-16 段落 5.1 实测卡停：Qwen3-TTS 日语参考音色念中文，Whisper
    稳定误听，三次回读 CER 60/60/30% 且内容互不相似，而音频实际念对
    （人耳确认）。旧判据要求回读文本两两相似（_reads_agree），失真模式
    在「假名乱码」与「近音中文」间摇摆时判 False → 整期退出。
    新判据：3 次全不达标 + 时长达标即豁免（S4：跳过样本不定罪），
    qc_skip 标注后由成片前的人耳确认兜底。
    """

    # 段落 5.1 的三次真实回读（2026-08-16 实测，CER 60/60/30%）。
    _REAL_HEARD = ["EUTERPE一陣来 世界クランテイエンロ",
                   "EUTERPE一陣来世界クランテイエンラ",
                   "A.U.T.R.P.一進來世界不難太遠了"]

    def _fake_engine(self):
        """synthesize 写一个 0.17s 的有声 wav（_trim_silence 要读它）。"""
        class E:
            kind = "qwen3_tts"      # Engine 契约成员，_render_one 按它决定尾巴检测
            # Engine 契约还有 cfg：_render_one 从它取情绪/语速受控词表（架构设计 3.3）
            cfg = {"emotions": {"平静叙述": {"emo_vector": None, "emo_alpha": 1.0}},
                   "speed": {"慢": 0.92, "中": 1.0, "快": 1.08}}
            def synthesize(self, text, dest, attempt, seed=0, emo_params=None):
                with wave.open(str(dest), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
                    w.writeframes(struct.pack("<4000h", *([3000] * 4000)))
        return E()

    def _render(self, monkeypatch, tmp_path, heard_seq, dur_ratio=1.0, text=None):
        """mock 掉转录与时长探测，只让回读内容与时长比可变。"""
        text = text or "エウテルペ 一进来，世界忽然退远了。"
        heard = iter(heard_seq)
        monkeypatch.setattr(t, "transcribe", lambda _p: next(heard))
        want = t.expected_duration(text)
        monkeypatch.setattr(t, "probe_duration", lambda _p: want * dur_ratio)
        dest = tmp_path / "seg.wav"
        take = t._render_one(self._fake_engine(), t.Segment(1, "1", text), dest)
        return take, dest

    def test_三次全不达标时长达标_豁免保留音频(self, monkeypatch, tmp_path):
        take, dest = self._render(monkeypatch, tmp_path, self._REAL_HEARD)
        assert take.qc_skip == "asr-blind"
        assert take.attempts == t.ATTEMPTS
        assert dest.exists(), "豁免必须保留音频，人耳确认要用"

    def test_三次全不达标时长超带_硬失败不留坏音频(self, monkeypatch, tmp_path):
        # 时长是跑飞与盲区的分界：漏读/重复会改变时长，超带不豁免
        with pytest.raises(SystemExit):
            self._render(monkeypatch, tmp_path, self._REAL_HEARD, dur_ratio=3.0)
        assert not (tmp_path / "seg.wav").exists(), "硬失败必须删掉坏音频"

    def test_某次回读达标_正常返回不豁免(self, monkeypatch, tmp_path):
        good = "エウテルペ 一进来，世界忽然退远了。"
        take, dest = self._render(monkeypatch, tmp_path,
                                  [self._REAL_HEARD[0], good, self._REAL_HEARD[1]])
        assert take.qc_skip is None
        assert take.attempts == 2

    def test_译文全对_第一次就通过(self, monkeypatch, tmp_path):
        text = "エウテルペ 一进来，世界忽然退远了。"
        take, _ = self._render(monkeypatch, tmp_path, [text])
        assert take.qc_skip is None
        assert take.attempts == 1

    def test_豁免选时长最接近估算的尝试(self, monkeypatch, tmp_path):
        # 三次尝试时长不同：attempt1 超带排除、attempt2 最接近 want（选中）、
        # attempt3 在带内但偏离。豁免应保留 attempt2，而不是最后一次。
        # 2026-08-16 段落 5.1 实测：三次尝试种子不同，最后一次把歌名念得特别长，
        # 旧实现保留最后一次，时长是盲区段唯一客观质量指标。
        text = "エウテルペ 一进来，世界忽然退远了。"
        want = t.expected_duration(text)
        ratios = {1: 2.5, 2: 0.95, 3: 1.2}   # 按 attempt 区分时长
        heard = iter(["A" * 10, "B" * 10, "C" * 10])
        monkeypatch.setattr(t, "transcribe", lambda _p: next(heard))
        monkeypatch.setattr(
            t, "probe_duration",
            lambda p: want * ratios[int(re.search(r"\.(\d+)\.wav$", str(p)).group(1))])
        dest = tmp_path / "seg.wav"
        take = t._render_one(self._fake_engine(), t.Segment(1, "1", text), dest)
        assert take.qc_skip == "asr-blind"
        assert take.duration == pytest.approx(want * 0.95, abs=0.001)
        assert dest.exists()
        # 临时尝试文件必须清干净
        assert list(tmp_path.glob(".seg.*.wav")) == []


class TestVoiceFingerprint:
    """增量重跑的音色指纹（2026-08-16 审计 2-6）。

    manifest 顶层从第一天就存着 engine/model/ref_audio，却从没参与比对——
    换音色后忘带 --force 会静默复用旧 wav，出一期混两种音色的成片且零警告
    （当天恰好发生 seg7→seg6 换音色）。readings 影响合成文本，一并入指纹。
    """

    CFG = {"engine": "qwen3_tts", "model": "mlx-community/M",
           "ref_audio": "data/voice/reference/seg6.wav", "readings": {"祈": "其"}}

    ROW = {"index": 1, "label": "1", "text": "正文", "file": "seg-01.wav",
           "duration": 5.0, "cer": 0.0, "attempts": 1}

    def _segs(self):
        return [t.Segment(1, "1", "正文")]

    def _old(self, cfg):
        return {**t._voice_fingerprint(cfg), "segments": [self.ROW]}

    def test_指纹一致文本一致wav在盘_可复用(self, tmp_path):
        (tmp_path / "seg-01.wav").write_bytes(b"x")
        done = t._reusable(self._old(self.CFG), self._segs(), tmp_path, self.CFG)
        assert 1 in done and done[1].file == "seg-01.wav"

    def test_换ref_audio后不可复用(self, tmp_path):
        (tmp_path / "seg-01.wav").write_bytes(b"x")
        cfg = {**self.CFG, "ref_audio": "data/voice/reference/seg7.wav"}
        assert t._reusable(self._old(self.CFG), self._segs(), tmp_path, cfg) == {}

    def test_换读音表后不可复用(self, tmp_path):
        # 2026-08-16 之前的 voice.json 注释明说「换 readings 增量重跑不会自动
        # 生效」——指纹补上之后这句不再成立，改字必须重念受影响的段
        (tmp_path / "seg-01.wav").write_bytes(b"x")
        cfg = {**self.CFG, "readings": {"祈": "其", "世界": "世介"}}
        assert t._reusable(self._old(self.CFG), self._segs(), tmp_path, cfg) == {}

    def test_换引擎或模型后不可复用(self, tmp_path):
        (tmp_path / "seg-01.wav").write_bytes(b"x")
        for k, v in (("engine", "indextts"), ("model", "mlx-community/M2")):
            cfg = {**self.CFG, k: v}
            assert t._reusable(self._old(self.CFG), self._segs(), tmp_path, cfg) == {}

    def test_文本变了不可复用(self, tmp_path):
        (tmp_path / "seg-01.wav").write_bytes(b"x")
        segs = [t.Segment(1, "1", "改过的正文")]
        assert t._reusable(self._old(self.CFG), segs, tmp_path, self.CFG) == {}

    def test_wav不在盘不可复用(self, tmp_path):
        assert t._reusable(self._old(self.CFG), self._segs(), tmp_path, self.CFG) == {}


class TestTitleDurationsFromConfig:
    """歌名实测时长进配置（2026-08-16 审计 2-7）。

    tts.py 曾把五首罪恶王冠歌名 + seg7 实测值硬编码成 _TITLE_DURS——
    换番要改代码、换音色数字过期，都违反「机制进代码、内容进配置」。
    现在值在 voice.json 的 titles 里：数字 = 实测秒数，null = 未实测。
    """

    def _cfg(self, tmp_path, monkeypatch, titles):
        p = tmp_path / "voice.json"
        p.write_text(json.dumps({"titles": titles}, ensure_ascii=False),
                     encoding="utf-8")
        monkeypatch.setattr(t, "CONFIG", p)

    def test_只取数字值_null和注释不算(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch,
                  {"_note": "说明", "歌名甲": 1.5, "歌名乙": None})
        assert t._title_durs() == {"歌名甲": 1.5}

    def test_实测时长替代字数估算(self, tmp_path, monkeypatch):
        # 8 字句含 3 字歌名：总时长 = (8-3) 字按 cpm + 歌名实测 1.5s
        self._cfg(tmp_path, monkeypatch, {"歌名乙": 1.5})
        want = (8 - 3 + 1.5 * t.CPM / 60) / t.CPM * 60
        assert t.expected_duration("先听歌名乙再说话") == pytest.approx(want)

    def test_未实测按cpm估(self, tmp_path, monkeypatch):
        # null = 未实测 → 该歌名按普通字数走 cpm（会高估英日歌名，run() 会 WARN）
        self._cfg(tmp_path, monkeypatch, {"歌名乙": None})
        assert t.expected_duration("先听歌名乙再说话") == pytest.approx(8 / t.CPM * 60)

    def test_未实测歌名能被挑出来提醒(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch,
                  {"_note": "说明", "歌名甲": 1.5, "歌名乙": None})
        assert t._unmeasured_titles(["先听歌名乙再说话", "没有歌名"]) == ["歌名乙"]

    def test_实测过的不再提醒(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch, {"歌名甲": 1.5})
        assert t._unmeasured_titles(["先听歌名甲再说话"]) == []


class TestParseScriptBlocks:
    """parse_script 按块切、`配音：` 可在块内任意位置（2026-08-16 审计 2-19）。

    旧正则要求「配音：」紧跟段落标题——中间夹一个 `画面：` 块的段被静默跳过、
    后续段序号整体前移，只能靠 clips 的段数对账兜住，且报错文案
    （「稿件改过就要重跑 pipeline.tts」）指错方向。现在与 clips/check_script 同口径。
    """

    def test_画面块夹在标题与配音之间也能解析(self, tmp_path):
        # 旧实现在这份稿上只解析出 1 段（第二段），第一段被静默吞掉
        f = tmp_path / "02-script.md"
        f.write_text(
            "## 段落 1\n\n画面：\n  查询: 某台词\n  集: S01E01\n\n配音：第一段。\n\n"
            "## 段落 2\n\n配音：第二段。\n", encoding="utf-8")
        segs = t.parse_script(f)
        assert [s.index for s in segs] == [1, 2]
        assert [s.text for s in segs] == ["第一段。", "第二段。"]

    def test_块内没有配音行仍然跳过(self, tmp_path):
        # 纯画面说明块（无配音）不是口播段——与 clips.parse_shots 同语义
        f = tmp_path / "02-script.md"
        f.write_text("## 段落 1\n\n配音：第一段。\n\n## 附录\n\n画面：\n  查询: x\n",
                     encoding="utf-8")
        assert [s.text for s in t.parse_script(f)] == ["第一段。"]


class TestExciseOnlyRefTitles:
    """歌名豁免只挖 ref 侧出现的歌名（2026-08-16 审计 2-18）。

    hyp 侧按长度窗口挖变体，窗口若对准全部歌名，正文里无关的英文词
    （"coding" 长 6，落在 エウテルペ(5)±1 窗内）会被误挖——ref 保留、
    hyp 被挖，CER 凭空虚高，误杀重试。方向是误杀不是漏放。
    """

    def test_没有歌名的句子_英文词不再被误挖(self, monkeypatch):
        monkeypatch.setattr(t, "_titles", lambda: ["エウテルペ", "My Dearest"])
        ref = "他用 coding 写了脚本"
        _, err = t.cer(ref, ref)
        assert err == 0.0            # 旧实现：hyp 侧 coding 被挖、ref 保留 → 虚高

    def test_同句出现的歌名变体仍被豁免(self, monkeypatch):
        monkeypatch.setattr(t, "_titles", lambda: ["エウテルペ"])
        ref = "第一首是エウテルペ，旋律还在"
        _, err = t.cer(ref, "第一首是エウテルペ，旋律还在")
        assert err == 0.0

    def test_挖除窗口收窄后中文照常比对(self, monkeypatch):
        monkeypatch.setattr(t, "_titles", lambda: ["My Dearest"])
        edits, err = t.cer("八幡自爆", "八幡自保")   # 无歌名句，行为与从前一致
        assert (edits, err) == (1, 0.25)


class TestLoadConfig:
    """voice.json 前置校验（2026-08-18 复盘②）：坏 JSON / 缺必要键在加载处
    当场报，不许流到 Engine 构造或 _voice_fingerprint 才裸 KeyError。"""

    GOOD = {"engine": "qwen3_tts", "model": "mlx-community/M",
            "ref_audio": "data/voice/reference/seg6.wav"}

    def test_合法配置原样返回(self, tmp_path):
        p = tmp_path / "voice.json"
        p.write_text(json.dumps(self.GOOD), encoding="utf-8")
        assert t.load_config(p)["engine"] == "qwen3_tts"

    def test_缺文件(self, tmp_path):
        with pytest.raises(SystemExit, match="缺少"):
            t.load_config(tmp_path / "voice.json")

    def test_坏JSON不裸抛(self, tmp_path):
        p = tmp_path / "voice.json"
        p.write_text("{ 不是 json", encoding="utf-8")
        with pytest.raises(SystemExit, match="合法 JSON"):
            t.load_config(p)

    def test_缺必要键逐个报出(self, tmp_path):
        p = tmp_path / "voice.json"
        p.write_text(json.dumps({"engine": "qwen3_tts"}), encoding="utf-8")
        with pytest.raises(SystemExit, match="model"):
            t.load_config(p)

    def test_空串值也算缺(self, tmp_path):
        # 写成 "" 不是「填了」——Engine 会拿空串去拼路径，报错离病根更远
        p = tmp_path / "voice.json"
        p.write_text(json.dumps({**self.GOOD, "ref_audio": ""}), encoding="utf-8")
        with pytest.raises(SystemExit, match="ref_audio"):
            t.load_config(p)


class TestStaleDownstream:
    """级联校验（2026-08-18 复盘①）：局部重跑改了段时长，下游 04-clips*.json
    要在产生的这一刻被指出来，不许攒到渲染时才炸。"""

    AUDIO = [{"index": 1, "duration": 6.0}]

    def _clips(self, episode, name, clip_durs):
        (episode / name).write_text(json.dumps({"segments": [
            {"index": 1, "status": "ok",
             "clips": [{"dur": d} for d in clip_durs]}]}), encoding="utf-8")

    def test_下游不存在时安静(self, tmp_path):
        assert t._stale_downstream(tmp_path, self.AUDIO) == []

    def test_下游仍对齐时安静(self, tmp_path):
        self._clips(tmp_path, "04-clips.json", [3.0, 3.0])
        assert t._stale_downstream(tmp_path, self.AUDIO) == []

    def test_下游漂移被点名(self, tmp_path):
        self._clips(tmp_path, "04-clips.json", [3.0, 2.0])   # Σ5.0 vs 配音 6.0
        out = t._stale_downstream(tmp_path, self.AUDIO)
        assert len(out) == 1 and "04-clips.json" in out[0] and "段1" in out[0]

    def test_approved同样查(self, tmp_path):
        # approved 是渲染真正吃的那份，它脏比 04-clips.json 脏更危险
        self._clips(tmp_path, "04-clips.approved.json", [8.0])
        out = t._stale_downstream(tmp_path, self.AUDIO)
        assert len(out) == 1 and "approved" in out[0]


class TestReusable:
    """段级复用（2026-09-02 须贺期）：readings 改动只重做「合成文本变了」的段。

    此前 _reusable 按 readings 全表指纹一刀切，改一个词九段全废——WORKFLOW
    承诺的「自动重做受影响的段」从未实现。改法：合成时把每段的 speakable
    存进 manifest，复用时段级比对。段级比对还覆盖全表指纹漏检的键序变化
    （指纹 sort_keys 对顺序不敏感，而 str.replace 按插入序生效）。
    """

    def _cfg(self, readings):
        return {"engine": "eng", "model": "m", "ref_audio": "ref.wav",
                "readings": readings}

    def _mk(self, tmp_path, segs):
        """造旧 manifest + 盘上 wav。segs: [(text, speakable)]，speakable=None 表示旧 manifest 没存。"""
        old = {"engine": "eng", "model": "m", "ref_audio": "ref.wav",
               "readings": "旧指纹", "segments": []}
        for i, (text, sp) in enumerate(segs, 1):
            (tmp_path / f"seg-{i:02d}.wav").write_bytes(b"x")
            d = {"index": i, "label": str(i), "text": text,
                 "file": f"seg-{i:02d}.wav", "duration": 1.0,
                 "cer": 0.0, "attempts": 1}
            if sp is not None:
                d["speakable"] = sp
            old["segments"].append(d)
        return old

    def test_改读音只重做受影响段(self, tmp_path, monkeypatch):
        # 旧表只有 私奔→丝奔；新表加了 世界→试介。段 2 合成文本变了，段 1 没变。
        monkeypatch.setattr(t, "_injections", lambda: {})
        monkeypatch.setattr(t, "_readings",
                            lambda: {"私奔": "丝奔", "世界": "试介"})
        old = self._mk(tmp_path, [("他们私奔了", "他们丝奔了"),
                                  ("一个世界本来", "一个世界本来")])
        segs = [t.Segment(1, "1", "他们私奔了"), t.Segment(2, "2", "一个世界本来")]
        done = t._reusable(old, segs, tmp_path,
                           self._cfg({"私奔": "丝奔", "世界": "试介"}))
        assert list(done) == [1]

    def test_加无关读音全部复用(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "_injections", lambda: {})
        monkeypatch.setattr(t, "_readings", lambda: {"喰种": "餐种"})
        old = self._mk(tmp_path, [("他们私奔了", "他们私奔了")])
        segs = [t.Segment(1, "1", "他们私奔了")]
        done = t._reusable(old, segs, tmp_path, self._cfg({"喰种": "餐种"}))
        assert list(done) == [1]

    def test_键序变化能检出_指纹检不出的变异(self, tmp_path, monkeypatch):
        # 同一组词条只调插入序：全表指纹 sort_keys 不变（旧行为静默复用），
        # 但 str.replace 按序生效，合成文本实际变了——段级比对必须抓住。
        monkeypatch.setattr(t, "_readings",
                            lambda: {"私奔到": "远奔", "私奔": "丝奔"})
        old = self._mk(tmp_path, [("他们私奔到东京", "他们丝奔到东京")])  # 旧序：私奔先替
        segs = [t.Segment(1, "1", "他们私奔到东京")]
        cfg = self._cfg({"私奔到": "远奔", "私奔": "丝奔"})
        assert t._voice_fingerprint(cfg)["readings"] == t._voice_fingerprint(
            self._cfg({"私奔": "丝奔", "私奔到": "远奔"}))["readings"]  # 前提：指纹确实对序不敏感
        assert t._reusable(old, segs, tmp_path, cfg) == {}

    def test_旧manifest无speakable_指纹没变才复用(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "_injections", lambda: {})
        monkeypatch.setattr(t, "_readings", lambda: {"私奔": "丝奔"})
        cfg = self._cfg({"私奔": "丝奔"})
        segs = [t.Segment(1, "1", "他们私奔了")]
        old = self._mk(tmp_path, [("他们私奔了", None)])                 # 无 speakable
        old["readings"] = t._voice_fingerprint(cfg)["readings"]         # 指纹一致
        assert list(t._reusable(old, segs, tmp_path, cfg)) == [1]
        old["readings"] = "别的指纹"
        assert t._reusable(old, segs, tmp_path, cfg) == {}              # 退回旧行为

    def test_音色变了仍全量重做(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "_readings", lambda: {})
        old = self._mk(tmp_path, [("他们私奔了", "他们私奔了")])
        old["engine"] = "旧引擎"
        segs = [t.Segment(1, "1", "他们私奔了")]
        assert t._reusable(old, segs, tmp_path, self._cfg({})) == {}

    def test_合成的take带speakable(self, tmp_path, monkeypatch):
        """speakable 必须落进 Take——漏了它，段级比对静默退化成旧的全表指纹。"""
        text = "他们私奔了"
        monkeypatch.setattr(t, "_injections", lambda: {})
        monkeypatch.setattr(t, "_readings", lambda: {"私奔": "丝奔"})
        monkeypatch.setattr(t, "transcribe", lambda _p: text)
        want = t.expected_duration(text)
        monkeypatch.setattr(t, "probe_duration", lambda _p: want)

        class E:
            kind = "qwen3_tts"
            cfg = {"emotions": {"平静叙述": {"emo_vector": None, "emo_alpha": 1.0}},
                   "speed": {"慢": 0.92, "中": 1.0, "快": 1.08}}
            def synthesize(self, _text, dest, attempt, seed=0, emo_params=None):
                with wave.open(str(dest), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
                    w.writeframes(struct.pack("<4000h", *([3000] * 4000)))

        take = t._render_one(E(), t.Segment(1, "1", text), tmp_path / "seg.wav")
        assert take.speakable == "他们丝奔了"


class TestTrimSilence:
    """结尾机械声检测只对 IndexTTS 开（2026-09-05 审计 P2-5）：

    _tail_artifact 认的是 BigVGAN 对停止符的渲染产物；Qwen3-TTS 无此成因
    （Qwen3 时代 80 句产物零触发），check_tail=False 时尾部必须原样保留。
    """

    def _wav(self, tmp_path):
        """0.5s 有声 + 0.1s 谷 + 0.1s 复起——正是 _tail_artifact 要认的形状。"""
        sr = 16000
        a = ([10000] * int(sr * 0.5) + [0] * int(sr * 0.1)
             + [5000] * int(sr * 0.1))
        p = tmp_path / "x.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(struct.pack(f"<{len(a)}h", *a))
        return p, sr, len(a)

    def test_indextts尾巴会被裁掉(self, tmp_path):
        p, sr, n = self._wav(tmp_path)
        t._trim_silence(p, check_tail=True)
        with wave.open(str(p)) as w:
            assert w.getnframes() < int(sr * 0.6)      # 谷后的复起被裁

    def test_qwen3路径不跑尾巴检测(self, tmp_path):
        p, sr, n = self._wav(tmp_path)
        t._trim_silence(p, check_tail=False)
        with wave.open(str(p)) as w:
            assert w.getnframes() == n                 # 尾部原样保留


# ---------------------------------------------------------------------------
# v2 情绪/语速协议、拼音注入留痕、评审打点（架构设计三.2/三.3，2026-09-11）
# ---------------------------------------------------------------------------


class _FakeEngine:
    """满足 Engine 契约的最小替身：kind + cfg + synthesize。"""

    kind = "qwen3_tts"
    cfg = {
        "emotions": {
            "平静叙述": {"emo_vector": None, "emo_alpha": 1.0},
            "低沉克制": {"emo_text": "低沉克制", "emo_alpha": 0.8},
        },
        "speed": {"慢": 0.92, "中": 1.0, "快": 1.08},
    }

    def __init__(self):
        self.seen: list[tuple] = []

    def synthesize(self, text, dest, attempt, seed=0, emo_params=None):
        self.seen.append((text, emo_params))
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(struct.pack("<4000h", *([3000] * 4000)))


V2_SCRIPT = """## 段落 1

配音：第一段口播。

情绪: 低沉克制
语速: 慢

画面：
  查询: x

## 段落 2

配音：第二段口播。

画面：
  查询: y
"""


class TestV2EmotionSpeedProtocol:
    def test_解析可选字段(self, tmp_path):
        """情绪/语速是可选的：写了就读出来，没写就是 None。"""
        f = tmp_path / "02-script.md"
        f.write_text(V2_SCRIPT, encoding="utf-8")
        assert [(s.label, s.emotion, s.speed) for s in t.parse_script(f)] == [
            ("1", "低沉克制", "慢"),
            ("2", None, None),
        ]

    def test_v1稿件零迁移(self, tmp_path):
        """不带新字段的 v1 稿件必须解析成功且两字段为 None（落默认值，行为不变）。"""
        body = "\n".join(
            f"## 段落 {i}\n\n配音：第{i}段。\n\n画面：\n  查询: q{i}\n" for i in range(1, 4)
        )
        f = tmp_path / "02-script.md"
        f.write_text(body, encoding="utf-8")
        segs = t.parse_script(f)
        assert len(segs) == 3
        assert all(s.emotion is None and s.speed is None for s in segs)

    def test_情绪默认落平静叙述(self):
        cfg = {"emotions": {"平静叙述": {"emo_alpha": 1.0}}}
        assert t.resolve_emotion(cfg, None) == ("平静叙述", {"emo_alpha": 1.0})

    def test_表外情绪报错不静默回退(self):
        """静默回退会造出「写了但没生效」的安慰剂——而防这个正是它进 manifest 的目的。"""
        cfg = {"emotions": {"平静叙述": {}}}
        with pytest.raises(SystemExit, match="受控词表"):
            t.resolve_emotion(cfg, "悲愤交加")

    def test_语速系数映射(self):
        cfg = {"speed": {"慢": 0.92, "中": 1.0, "快": 1.08}}
        assert t.resolve_speed(cfg, None) == ("中", 1.0)
        assert t.resolve_speed(cfg, "快") == ("快", 1.08)
        with pytest.raises(SystemExit, match="受控词表"):
            t.resolve_speed(cfg, "极快")

    def test_情绪参数下传引擎并落manifest字段(self, tmp_path, monkeypatch):
        """情绪声明必须抵达引擎、并落进 Take——否则 eval 无法回答「声明是否真的改变了声学输出」。"""
        text = "第一段口播。"
        monkeypatch.setattr(t, "transcribe", lambda _p: text)
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        eng = _FakeEngine()
        take = t._render_one(eng, t.Segment(1, "1", text, "低沉克制", "慢"), tmp_path / "s.wav")
        assert eng.seen[0][1] == {"emo_text": "低沉克制", "emo_alpha": 0.8}, "参数必须下传引擎"
        assert take.emotion == "低沉克制" and take.speed == "慢"

    def test_拼音注入记录落Take(self, tmp_path, monkeypatch):
        """机器做的替换与 readings 表一样需要可审计（架构设计三.2 要点 3）。"""
        text = "世界忽然退远了"
        monkeypatch.setattr(t, "transcribe", lambda _p: text)
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        monkeypatch.setattr(t, "_injections", lambda: {"世界": "shi4jie4"})
        eng = _FakeEngine()
        take = t._render_one(eng, t.Segment(1, "1", text), tmp_path / "s.wav")
        assert take.g2p_injections == [
            {"from": "世界", "to": "shìjiè", "tone3": "shi4jie4", "hits": 1}
        ]
        assert eng.seen[0][0].startswith("shìjiè"), "喂给引擎的必须是注入后文本"


class TestReviewArg:
    def test_全名与别名(self):
        assert t.parse_review_arg("voice_stability=4,prosody=3") == {
            "voice_stability": 4, "prosody": 3
        }
        assert t.parse_review_arg("voice=4,misread=5") == {
            "voice_stability": 4, "misread": 5
        }

    @pytest.mark.parametrize("raw", [
        "voice=0",      # 低于下界
        "voice=6",      # 高于上界
        "voice=abc",    # 非数字
        "voice",        # 缺 =
        "nope=3",       # 未知维度
        "",             # 空
    ])
    def test_非法输入当场报错(self, raw):
        """打点是人工评审的唯一入口：写错还继续跑，会得到一份「看起来打过点」的空 manifest。"""
        with pytest.raises(SystemExit):
            t.parse_review_arg(raw)

    def test_写入是合并而非覆盖(self, tmp_path):
        """一次只打 03.5 的三项、下次补 05 的两项是正常流程。"""
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        mf = ep / "03-audio" / "manifest.json"
        mf.write_text(json.dumps({"human_review": {"voice_stability": 3}, "segments": []}),
                      encoding="utf-8")
        t.write_review(ep, {"prosody": 4})
        assert json.loads(mf.read_text(encoding="utf-8"))["human_review"] == {
            "voice_stability": 3, "prosody": 4
        }

    def test_没有manifest时报错(self, tmp_path):
        with pytest.raises(SystemExit, match="先跑配音"):
            t.write_review(tmp_path, {"prosody": 4})

    def test_review短路不触发合成(self, tmp_path, monkeypatch):
        """打点发生在听完音频之后，不该重跑合成。"""
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "02-script.md").write_text("## 段落 1\n\n配音：甲。\n", encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(
            json.dumps({"segments": []}), encoding="utf-8")

        def _boom(*a, **k):
            raise AssertionError("--review 不该触发合成")

        monkeypatch.setattr(t, "Engine", _boom)
        t.run(ep, review={"prosody": 4})
        assert json.loads((ep / "03-audio" / "manifest.json").read_text(encoding="utf-8"))[
            "human_review"
        ] == {"prosody": 4}


class TestConfigOverlay:
    """`_extends` 覆盖层：本地 mlx 与云端 CUDA 各用一份差异配置，共同继承 voice.json。"""

    def test_覆盖层只写差异_其余继承(self, tmp_path):
        base = tmp_path / "base.json"
        base.write_text(json.dumps({
            "engine": "qwen3_tts", "model": "mlx-x", "ref_audio": "a.wav",
            "readings": {"世界": "逝戒"}, "emotions": {"平静叙述": {}},
        }), encoding="utf-8")
        over = tmp_path / "over.json"
        over.write_text(json.dumps({
            "_extends": str(base), "engine": "indextts2", "model": "IndexTeam/IndexTTS-2",
        }), encoding="utf-8")

        cfg = t.load_config(over)
        assert cfg["engine"] == "indextts2" and cfg["model"] == "IndexTeam/IndexTTS-2"
        assert cfg["readings"] == {"世界": "逝戒"}, "未覆盖的字段必须继承，否则两份配置会分叉"
        assert cfg["ref_audio"] == "a.wav"

    def test_extends指向不存在时当场报错(self, tmp_path):
        over = tmp_path / "over.json"
        over.write_text(json.dumps({"_extends": "nope.json", "engine": "x",
                                    "model": "y", "ref_audio": "z"}), encoding="utf-8")
        with pytest.raises(SystemExit, match="_extends"):
            t.load_config(over)

    def test_云端覆盖层切到cuda引擎并继承全部共享表(self):
        """真实文件检查：云端配置必须切到 qwen3_tts_cuda，且共享表一条不少。

        防的错误：覆盖层写错字段名（比如漏了 readings 就该继承却手写了个空表），
        会让云端用一份残缺的词表跑完整期。

        引擎选型（2026-09-11 定稿）：Qwen3-TTS 的 PyTorch/CUDA 版。
        与本地 mlx 版同版权重 → 音色一致（用户实听确认「相当好，很纯净」）。
        Index 系列已否决：IndexTTS2 非多语种，与本项目英日歌名需求直接冲突。
        """
        base = t.load_config(paths.CONFIG / "voice.json")
        cloud = t.load_config(paths.CONFIG / "voice.cloud.json")
        assert cloud["engine"] == "qwen3_tts_cuda"
        # 云端模型在数据盘、不在仓库内，所以用绝对路径；本地配置仍用 HF 仓库名
        assert cloud["model"] == "/root/autodl-tmp/models/Qwen/Qwen3-TTS-12Hz-1.7B-Base"
        assert cloud["readings"] == base["readings"]
        assert cloud["pinyin_injections"] == base["pinyin_injections"]
        assert cloud["emotions"] == base["emotions"]
        assert cloud["speed"] == base["speed"]
        assert cloud["titles"] == base["titles"]


class TestCascadeArbitration:
    """级联仲裁（架构设计三.5）：主读不过才请仲裁，不是分数融合。

    为什么必须是级联：两个 ASR 的 CER 不可比（S10 同构——不同模型的错误率
    不是同一个量），混合分数等于把两把不同的尺子接起来量。
    """

    def test_同一把尺_判据纯函数(self):
        assert t.readback_passes(0, 0.0) is True
        assert t.readback_passes(2, 0.9) is True, "错字数少于 MIN_EDITS 时放行（短段落保护）"
        assert t.readback_passes(5, 0.9) is False
        assert t.readback_passes(99, 0.1) is True, "CER 达标即放行"

    def test_主读通过时不触发仲裁(self, monkeypatch, tmp_path):
        """主读过了就没有理由再花一次 ASR——仲裁是补漏，不是例行双跑。"""
        text = "他站在那里"
        calls: list = []
        monkeypatch.setattr(t, "transcribe", lambda _p: text)
        monkeypatch.setattr(t, "_asr_backends", lambda: ("primary", "arbiter"))
        monkeypatch.setattr(t.asr, "transcribe_audio",
                            lambda _p, backend=None: (calls.append(backend) or (text, backend)))
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        take = t._render_one(_FakeEngine(), t.Segment(1, "1", text), tmp_path / "s.wav")
        assert take.asr_arbitrated is False
        assert calls == [], "主读已过就不该请仲裁"

    def test_主读不过_仲裁通过_落痕(self, monkeypatch, tmp_path):
        """这正是升级 ASR 通道的全部意义：主读念飞、仲裁读对。"""
        text = "他站在那里很久没有说话"
        monkeypatch.setattr(t, "transcribe", lambda _p: "完全不同的内容乱码乱码乱码")
        monkeypatch.setattr(t, "_asr_backends", lambda: ("primary", "arbiter"))
        monkeypatch.setattr(t.asr, "transcribe_audio", lambda _p, backend=None: (text, backend))
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        take = t._render_one(_FakeEngine(), t.Segment(1, "1", text), tmp_path / "s.wav")
        assert take.asr_arbitrated is True
        assert take.cer <= t.MAX_CER

    def test_没有仲裁后端时不假装仲裁(self, monkeypatch, tmp_path):
        """单档可用时，主读不过就直落既有重试/豁免链——不拿同一份裁决再判一次充数。"""
        text = "他站在那里很久没有说话"
        monkeypatch.setattr(t, "transcribe", lambda _p: "乱码乱码乱码乱码乱码")
        monkeypatch.setattr(t, "_asr_backends", lambda: ("primary", None))
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        take = t._render_one(_FakeEngine(), t.Segment(1, "1", text), tmp_path / "s.wav")
        assert take.qc_skip == "asr-blind", "走既有豁免链，不是新造一条"
        assert take.asr_arbitrated is None

    def test_仲裁后端故障不静默(self, monkeypatch, tmp_path, capsys):
        """仲裁后端挂掉会让「主读不过」直接掉进重试链，不说清楚现象就是「质检莫名变严」。"""
        text = "他站在那里很久没有说话"
        monkeypatch.setattr(t, "transcribe", lambda _p: "乱码乱码乱码乱码乱码")
        monkeypatch.setattr(t, "_asr_backends", lambda: ("primary", "arbiter"))

        def _boom(_p, backend=None):
            raise RuntimeError("funasr 未安装")

        monkeypatch.setattr(t.asr, "transcribe_audio", _boom)
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        t._render_one(_FakeEngine(), t.Segment(1, "1", text), tmp_path / "s.wav")
        assert "仲裁后端 arbiter 执行失败" in capsys.readouterr().err


    def test_仲裁也不过_继续走重试链(self, monkeypatch, tmp_path):
        """仲裁是补漏，不是免罪符：两档都不过就照旧重试，最终落既有豁免链。

        防的错误：把「请过仲裁」当成通过条件——那会让质检在升级 ASR 通道后
        静默放宽，而放宽的证据（仲裁读对了）根本不存在。
        """
        text = "他站在那里很久没有说话"
        monkeypatch.setattr(t, "transcribe", lambda _p: "乱码乱码乱码乱码乱码")
        monkeypatch.setattr(t, "_asr_backends", lambda: ("primary", "arbiter"))
        monkeypatch.setattr(t.asr, "transcribe_audio",
                            lambda _p, backend=None: ("还是乱码乱码乱码乱码", backend))
        monkeypatch.setattr(t, "probe_duration", lambda _p: t.expected_duration(text))
        take = t._render_one(_FakeEngine(), t.Segment(1, "1", text), tmp_path / "s.wav")
        assert take.qc_skip == "asr-blind", "仲裁也不过 → 走既有豁免链"
        assert take.asr_arbitrated is not True, "没过就不能记成仲裁通过"


class TestQwen3CudaLoader:
    """云端引擎接缝：Qwen3-TTS 的 PyTorch/CUDA 版（2026-09-11 实测通过、定为 v2 引擎）。

    与本地 `qwen3_tts`（mlx）是**同一个模型的两条实现路径**，权重相同所以音色一致。
    """

    def test_必须走x_vector路径(self, monkeypatch, tmp_path):
        """**ADR-0006 的教训**：ICL 路径（传 ref_text）会把参考转录当正文念出来。

        官方 PyTorch 版把 x-vector 做成了显式开关 `x_vector_only_mode`，
        本 loader 必须固定用它、且不传 ref_text（我们的参考干声是日文，也没有可靠转录）。
        """
        captured: dict = {}

        class FakeModel:
            def generate_voice_clone(self, **kw):
                captured.update(kw)
                # 非零数据：synthesize 有「全静音即报错」的守卫，返回 zeros 会撞它
                return ([np.full(2400, 0.1, dtype=np.float32)], 24000)

        class FakeEngine:
            kind = "qwen3_tts_cuda"
            cfg = {"emotions": {"平静叙述": {}}, "speed": {"中": 1.0}}
            model = FakeModel()
            ref_audio = "/nonexistent-ref.wav"
            lang_code = "zh"
            # 借用真实现：本用例验证的是 Engine.synthesize 的分派与传参，
            # 不是重写一遍 cuda 分支（那测的就是测试自己了）
            _synthesize_cuda = t.Engine._synthesize_cuda

        eng = FakeEngine()
        t.Engine.synthesize(
            eng, "世界忽然退远了", tmp_path / "o.wav", attempt=1, seed=0,
        )
        assert captured["x_vector_only_mode"] is True, "必须是 x-vector 路径"
        assert "ref_text" not in captured, "x-vector 路径不该传 ref_text"
        # 采样率必须由模型返回并被采用，不能写死（不同模型输出率不同）
        import wave as _wave

        with _wave.open(str(tmp_path / "o.wav")) as w:
            assert w.getframerate() == 24000, "写盘的采样率应来自模型返回值"

    def test_loader_读取cuda配置(self, monkeypatch):
        """配置从 voice.cloud.json 的 qwen3_cuda 段读，不在代码里写死。"""
        seen: dict = {}

        class FakeModel:
            @staticmethod
            def from_pretrained(path, **kw):
                seen["path"] = path
                seen.update(kw)
                return object()

        import sys as _sys
        import types as _types

        fake_mod = _types.ModuleType("qwen_tts")
        fake_mod.Qwen3TTSModel = FakeModel
        monkeypatch.setitem(_sys.modules, "qwen_tts", fake_mod)

        t._load_qwen3_cuda(
            {"qwen3_cuda": {"device_map": "cuda:0", "dtype": "bfloat16"}}, "/models/x"
        )
        assert seen["path"] == "/models/x"
        assert seen["device_map"] == "cuda:0"

    def test_云端配置的模型路径是数据盘绝对路径(self):
        """云端模型在数据盘、不在仓库内，必须是绝对路径（否则 _resolve_model 会拼到仓库根）。"""
        cloud = t.load_config(paths.CONFIG / "voice.cloud.json")
        assert cloud["model"].startswith("/root/autodl-tmp/models/")
