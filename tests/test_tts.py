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
import re
import struct
import wave

import pytest

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
        monkeypatch.setattr(t, "_readings",
                            lambda: {"喰种": "餐种", "绚都": "绚督"})
        assert t.speakable("东京喰种里陪着绚都") == "东京餐种里陪着绚督"

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
            def synthesize(self, text, dest, attempt, seed=0):
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
