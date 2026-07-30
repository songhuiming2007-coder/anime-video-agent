"""配音：稿件解析、合成单元切分、回读比对。

这三块各自踩过一个坑，都固化在下面：

- `split_sentences` 不是「按句号切」那么简单。段 4 那句 40 字的长句一口气念完 6.79 秒，
  明显比别处快还吞字——模型对整句时长有先验，单元越长压得越狠。
  所以超过 30 字要在逗号处再断。
- `cer` 必须同时返回编辑距离和比率。十个字的段落错一个同音字就是 10%，
  而那只是 ASR 自己听岔了，不是配音出了问题。
- `normalize` 决定了上面两件事的「字数」口径：标点不算字。
"""

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


class TestCer:
    def test_一个错字(self):
        assert t.cer("八幡自爆", "八番自爆") == (1, 0.25)

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


class TestExpectedDuration:
    def test_按去标点后的字数算(self):
        # 标点不发音，算进去会把估算时长撑长，DUR_BAND 的上下界就跟着偏
        assert t.expected_duration("八幡，自爆！") == t.expected_duration("八幡自爆")

    def test_与_CPM_一致(self):
        assert t.expected_duration("八幡自爆了") == pytest.approx(5 / t.CPM * 60)


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
