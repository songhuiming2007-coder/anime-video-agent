"""字幕排版：宽度计算、折行、分卡。

这几个函数直接决定「字幕会不会超出画面」，而它们踩过一次典型的坑：
质检算的是「原文长度 ÷ 每行容量 = 应该折成几行」，没验「实际折没折」，
于是 91 字的段落被 libass 渲成一整行、两端各切掉十几个字，检查还判 PASS。

**所以这里测的全是函数的真实返回值，不是它「应该」返回什么。**
每条断言的期望值都是 2026-07-29 在实际实现上跑出来的。
"""

from pipeline import render as r


class TestCharWidth:
    def test_汉字一格(self):
        assert r.char_width("汉") == 1.0

    def test_中文标点半格(self):
        # 全角码位，但渲染出来只占半格左右。按字数一刀切会把
        # 「31 汉字 + 1 逗号」误判成超宽——它实际只有 31.5 格。
        for ch in "，。、；：？！":
            assert r.char_width(ch) == 0.5

    def test_ascii_半格(self):
        for ch in "aZ0 -":
            assert r.char_width(ch) == 0.5

    def test_开引号也是半格(self):
        for ch in "（「『【":
            assert r.char_width(ch) == 0.5

    def test_line_width_混排(self):
        assert r.line_width("汉" * 31 + "，") == 31.5


class TestWidthBudget:
    def test_硬上限由画幅与边距算出(self):
        assert r.usable_width() == (r.W - r.MARGIN_LR * 2) / r.FONT_SIZE

    def test_折行目标比硬上限少半格(self):
        # 这半格留给避头尾：收尾标点即使超出目标也要留在本行。
        # 两者分开，超宽检查才不会被这个设计内的溢出误报。
        assert r.per_line() == int(r.usable_width()) - 0.5
        assert r.per_line() < r.usable_width()


class TestWrap:
    def test_短句不折(self):
        assert r.wrap("八幡自爆了", 30.5) == ["八幡自爆了"]

    def test_超宽就折(self):
        assert r.wrap("啊" * 8, 5) == ["啊啊啊啊啊", "啊啊啊"]

    def test_避头尾_收尾标点不挂到下一行开头(self):
        # 宽度 5，五个字已经占满；逗号本该溢出换行，但它不许出现在行首，
        # 所以留在本行、让这一行超出半格。这正是 per_line 少半格的原因。
        assert r.wrap("啊啊啊啊啊，哦", 5) == ["啊啊啊啊啊，", "哦"]

    def test_空串(self):
        assert r.wrap("", 30.5) == []

    def test_折出来的每一行都不超过宽度加半格(self):
        text = "他不是不会说话，是算准了说什么最快能让人讨厌他，而这一点比善良诚实得多。" * 3
        for line in r.wrap(text, r.per_line()):
            # 唯一允许的溢出是避头尾带来的半格
            assert r.line_width(line) <= r.per_line() + 0.5


class TestSplitLong:
    def _sent(self, text, start=0.0, dur=10.0):
        return {"text": text, "start": start, "duration": dur}

    def test_放得下就原样返回(self):
        s = self._sent("八幡自爆了")
        assert r._split_long(s) == [s]

    def test_太长就在标点处断卡(self):
        long = "在户部告白之前，当着所有人的面走过去，说我从很早以前就开始喜欢你了，请和我交往吧。"
        out = r._split_long(self._sent(long, dur=12.0))
        assert len(out) > 1
        # 断卡不能丢字——音频是连续的，字幕只是换一张
        assert "".join(p["text"] for p in out) == long

    def test_时长按字数比例分且总和守恒(self):
        long = "在户部告白之前，当着所有人的面走过去，说我从很早以前就开始喜欢你了，请和我交往吧。"
        out = r._split_long(self._sent(long, start=5.0, dur=12.0))
        assert abs(sum(p["duration"] for p in out) - 12.0) < 0.01
        assert out[0]["start"] == 5.0
        # 起点必须递增，否则字幕会倒着出
        assert all(a["start"] < b["start"] for a, b in zip(out, out[1:]))

    def test_无可断点的长句只能交给折行(self):
        # 整句没有标点，切不动，原样返回让 wrap 去折
        s = self._sent("啊" * 60)
        assert r._split_long(s) == [s]


class TestSplitLongDash:
    """破折号断卡。

    这个稿子的声音里**破折号本来就是一个停顿**（VOICE.md：「破折号做转折与补充，
    念的时候正好是一个停顿」），所以在那儿断卡与配音天然合拍。

    2026-07-30 踩的两件事：

    1. `_SUB_BREAK` 里没有破折号，于是「跟这种人相处最省心的就在这儿——你永远不用猜…」
       33.5 格、句中一个逗号都没有，找不到断点只能折两行。
    2. 加进去之后仍然折行——破折号处正好 15.0 格，而旧代码要求「攒够半行」= 15.25 格，
       **差 0.25 格**。那个 0.5 是个魔法数，而且它的失败方式是错的：
       不达标就整句放弃去折行，**而折行比稍短的卡更糟**，正是要防的东西。
       换成贪心断行之后没有可调参数。
    """

    def _sent(self, text, dur=6.0):
        return {"text": text, "start": 0.0, "duration": dur}

    LONG = "跟这种人相处最省心的就在这儿——你永远不用猜她那句“好啊”后面藏着什么。"

    def test_破折号处断开(self):
        out = r._split_long(self._sent(self.LONG))
        assert len(out) == 2
        assert out[0]["text"].endswith("——")

    def test_断完每片都放得下一行(self):
        for p in r._split_long(self._sent(self.LONG)):
            assert len(r.wrap(p["text"], r.per_line())) == 1, p["text"]

    def test_不在破折号中间断(self):
        # 中文破折号是两个字符。断在第一个后面会在行尾留下孤零零一个「—」。
        for p in r._split_long(self._sent(self.LONG)):
            assert not p["text"].endswith("—") or p["text"].endswith("——")
            assert not p["text"].startswith("—")

    def test_断卡不丢字(self):
        out = r._split_long(self._sent(self.LONG))
        assert "".join(p["text"] for p in out) == self.LONG

    def test_时长守恒且起点递增(self):
        out = r._split_long(self._sent(self.LONG, dur=6.0))
        assert abs(sum(p["duration"] for p in out) - 6.0) < 0.01
        assert all(a["start"] < b["start"] for a, b in zip(out, out[1:]))

    def test_逗号仍然是断点(self):
        # 加破折号不能把原来的断点弄坏
        long = "在户部告白之前，当着所有人的面走过去，说我从很早以前就开始喜欢你了，请和我交往吧。"
        out = r._split_long(self._sent(long, dur=12.0))
        assert len(out) > 1
        assert "".join(p["text"] for p in out) == long

    def test_取放得下的最远断点而不是最早的(self):
        # 贪心的意义：断出来的卡要尽量满，否则一句话会碎成一串短卡一闪而过
        long = "一，二，三，四，五，六，七，八，九，十，十一，十二，十三，十四，十五，十六，十七。"
        out = r._split_long(self._sent(long))
        assert all(r.line_width(p["text"]) <= r.per_line() for p in out)
        # 第一片必须已经接近满行，不能在第一个逗号就收
        assert r.line_width(out[0]["text"]) > r.per_line() * 0.5


class TestOutroStart:
    def test_取离片尾足够远的最后一个段落起点(self):
        starts = [0.0, 50.0, 100.0, 169.96, 186.94]
        # 186.94 离片尾只有 2.36s，撑不起收尾；退到 169.96 给出 19.3s
        assert r._outro_start(starts, 189.3) == 169.96

    def test_片子太短就不换曲(self):
        assert r._outro_start([0.0, 5.0], 8.0) is None
