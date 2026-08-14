"""稿件机检：正文统计口径、片尾摘除、锚点词的番剧无关性。

这个模块的价值全在「检查项不误报」。**一个长期误报的检查项等于没有检查**——
人看两次全是红的就再也不看了，真出问题时也不会发现。所以两个坑都跟误报有关：

- 片尾套话「好了，今天就到这里」是固定收尾，不承担内容。不摘出去的话，
  每期都会把段数门槛顶穿一格、字数也虚高。
- 锚点词原先写死成春物的专有名词（文化祭、修学旅行、侍奉部……）。
  换一部番这一半一个都命中不了，「剧情锚点 ≥3」会永远判 0 处。
"""

import json

import pytest

from pipeline import check_script as cs
from pipeline import paths


def build(vos, queries=None, eps=None):
    """按稿件格式拼一篇。queries 不给就每段配一条；eps 按段给集号（None 段不写）。"""
    parts = []
    for i, v in enumerate(vos, 1):
        q = queries[i - 1] if queries else "某句台词"
        ep = f"\n  集: {eps[i - 1]}" if eps and eps[i - 1] else ""
        parts.append(f"## 段落 {i}\n\n配音：{v}\n\n画面：\n  查询: {q}{ep}\n")
    return "\n".join(parts)


def script(tmp_path, vos, queries=None, eps=None):
    f = tmp_path / "02-script.md"
    f.write_text(build(vos, queries, eps), encoding="utf-8")
    return f


def get(checks, prefix):
    """按名字前缀取一条检查结果。名字里带阈值，所以只能前缀匹配。"""
    for c in checks:
        if c.name.startswith(prefix):
            return c
    raise AssertionError(f"没有名为 {prefix}… 的检查项：{[c.name for c in checks]}")


BODY = "第二季第二集里他站起来说了那句话，全场安静了三秒。"


class TestParse:
    def test_三类字段各自归位(self):
        vo, q, alt = cs.parse(
            "## 段落 1\n\n配音：正文。\n\n画面：\n  查询: 甲\n  备选: 乙\n")
        assert vo == ["正文。"] and q == ["甲"] and alt == ["乙"]

    def test_配音必须顶格查询允许缩进(self):
        # 稿件里查询/备选是缩进在「画面：」下面的，配音不是。口径要跟 skill 的模板一致。
        vo, q, _ = cs.parse("配音：正文。\n  查询: 甲\n")
        assert vo == ["正文。"] and q == ["甲"]

    def test_备选留空是允许的(self):
        _, _, alt = cs.parse("配音：正文。\n  查询: 甲\n")
        assert alt == []


class TestOutro:
    def test_片尾不计入段数与字数(self, tmp_path):
        # 不摘的话 8 段正文 + 1 段片尾 = 9 段，字数也多出一截。
        # 门槛是按正文定的，把套话算进去等于每期都虚高一格。
        f = script(tmp_path, [BODY] * 8 + ["好了，今天就到这里，下期见。"])
        checks = cs.run(f)
        assert get(checks, "段落数").detail.startswith("8 段")
        assert "另有片尾" in get(checks, "字数").detail

    def test_查询按含片尾的总段数比(self, tmp_path):
        # 片尾不计内容，但它照样要出画面，所以查询数按 9 比不是按 8 比
        f = script(tmp_path, [BODY] * 8 + ["好了，今天就到这里，下期见。"])
        c = get(cs.run(f), "每段都有查询")
        assert c.ok and "9" in c.detail

    def test_太长的末段不算片尾(self, tmp_path):
        # 25 字的上限挡的是「末段恰好提到下期，但它是正文」的情况。
        # 没这个上限，一段有实质内容的收尾会被整段从统计里摘掉。
        long_tail = "他最后那句话我到现在都还记得，说完就走了，感谢观看这四个字他一次都没说过。"
        f = script(tmp_path, [BODY] * 8 + [long_tail])
        assert get(cs.run(f), "段落数").detail.startswith("9 段")

    def test_没有片尾时不显示提示(self, tmp_path):
        f = script(tmp_path, [BODY] * 8)
        assert "另有片尾" not in get(cs.run(f), "段落数").detail


class TestShrinkNoPadding:
    """`01-topic.md` 的 `缩段不注水: 是`：字数下限 × SHRINK_FACTOR，上界不动（E1）。

    默认 MIN_CHARS=870、MAX_CHARS=1300、SHRINK_FACTOR=0.8 → shrunk 下限
    round(870*0.8)=696。这三个数直接来自 pipeline/check_script.py 的默认配置，
    先在实现上确认过再写进下面的断言。800 字卡在 696 与 870 之间，用来验证
    这条字段不只是换个数字显示，是真的能把 FAIL 翻成 PASS。
    """

    def _episode(self, tmp_path, per_seg_chars, n=8, shrink=False):
        if shrink:
            (tmp_path / "01-topic.md").write_text("缩段不注水: 是\n", encoding="utf-8")
        return script(tmp_path, ["文" * per_seg_chars] * n)

    def test_无字段行为不变(self, tmp_path):
        f = self._episode(tmp_path, 50, shrink=False)
        assert get(cs.run(f), "字数").name == "字数 870–1300"

    def test_行尾备注不静默失效(self, tmp_path):
        # 2026-08-14 审计实测踩到：原正则锚 \s*$，而 01-topic.md 允许行尾挂
        # `# 备注`（BGM 字段既有惯例）——带备注时字段不匹配、静默退回默认
        # 下限、零报错。同文件 DURATION_FIELD 注释记录过同款坑。
        # 修法：\b 词边界替代 $（同 DURATION_FIELD 的处理）。
        (tmp_path / "01-topic.md").write_text(
            "缩段不注水: 是 # 人物志，料不够\n", encoding="utf-8")
        f = script(tmp_path, ["文" * 50] * 8)
        assert get(cs.run(f), "字数").name == "字数 696–1300"

    def test_有字段下限乘shrink_factor且上界不动(self, tmp_path):
        f = self._episode(tmp_path, 50, shrink=True)
        assert get(cs.run(f), "字数").name == "字数 696–1300"

    def test_不带字段_800字判不合格(self, tmp_path):
        f = self._episode(tmp_path, 100, shrink=False)   # 8×100=800 < 870
        c = get(cs.run(f), "字数")
        assert c.ok is False and "800" in c.detail

    def test_带字段_同样800字判合格(self, tmp_path):
        f = self._episode(tmp_path, 100, shrink=True)     # 800 ≥ 696
        c = get(cs.run(f), "字数")
        assert c.ok is True


class TestLongSentence:
    def test_按单句判而不是按整段判(self, tmp_path):
        # 一段里三句短句加起来 60 字完全正常，念着不断气。
        # 按整段判会把所有正常段落判成超长。
        seg = "他站起来了。全场安静了三秒。没有人接话，包括老师。" * 2
        f = script(tmp_path, [seg] + [BODY] * 7)
        assert get(cs.run(f), "无超").ok

    def test_单句超限要报出来(self, tmp_path):
        f = script(tmp_path, ["啊" * 95 + "。"] + [BODY] * 7)
        c = get(cs.run(f), "无超")
        assert not c.ok and "95字" in c.detail

    def test_上限只防失控不防长句(self, tmp_path):
        # 60 字的整句在人类样本里是常态（占比中位约 27%），不该拦。
        # 旧上限 40 会把它判死，而那正是「三期零长句」的直接成因。
        seg = "他不是不会说话，是算准了说什么最快能让人讨厌他，而被讨厌这件事只要是他自己选的，就不再算作一次失败了。"
        assert len(seg) > 45
        assert get(cs.run(script(tmp_path, [seg] * 8)), "无超").ok


class TestQuotes:
    def test_引号要报出来(self, tmp_path):
        # 管道侧已经在合成前剥掉（`tts.speakable`），但稿件里出现引号仍是个信号：
        # 那句话在靠视觉标记表达强调或引用，而口播只有语气可用。
        f = script(tmp_path, [BODY] * 7 + ['他说了句“好啊”就走了。第二季第二集的事。'])
        c = get(cs.run(f), "无引号")
        assert not c.ok and "2 处" in c.detail

    def test_没有引号就放行(self, tmp_path):
        assert get(cs.run(script(tmp_path, [BODY] * 8)), "无引号").ok


class TestPaperWords:
    """论文连接词：**宁可漏判也不要误判。**

    检查项误报一次，人就会开始怀疑其余十条，最后整份报告没人看。
    2026-07-30 踩过：「最后那一集，她说了一句」被判违规，而它完全正常。
    """

    def test_句首的论文腔要抓住(self):
        for s in ["最后，我们可以看到这一点。", "因此我认为不对。",
                  "总之，事情就是这样。", "首先要看清一件事。"]:
            assert cs.PAPER_WORDS.findall(s), s

    def test_句读之后的也算(self):
        assert cs.PAPER_WORDS.findall("他走了。然而没人在意。") == ["然而"]

    def test_内嵌用法不算(self):
        # 「不因此而放弃」里的「因此」不是连接词
        assert not cs.PAPER_WORDS.findall("我不会不因此而放弃")

    def test_最后作时间词不算(self):
        # 这九个词里只有「最后」身兼时间词。论文腔的那个后面必带逗号，
        # 叙事用法不带——判据就卡在这个逗号上。
        for s in ["完结那一集她说了一句话。", "最后他还是走了。",
                  "他排最后一个。", "这是全剧最后的镜头。", "最后那一集，她说了一句。"]:
            assert not cs.PAPER_WORDS.findall(s), s

    def test_最后带逗号才算(self):
        assert cs.PAPER_WORDS.findall("最后，我要说一句。") == ["最后"]

    def test_只有一个捕获组(self):
        # findall 的结果直接进 detail 字符串。多个捕获组会返回元组，join 当场炸。
        hits = cs.PAPER_WORDS.findall("因此。然而。最后，")
        assert all(isinstance(h, str) for h in hits)


class TestDuplicateQuery:
    def test_两段用同一条查询要报出来(self, tmp_path):
        # 排片是全局贪心、同一处画面整期只用一次，所以第二段拿不到那句台词的画面，
        # 会退到明显更差的命中，**而且不报 no_match**。
        # 2026-07-30 实测：合并段落时把上一段的查询整条带过来了，检查全绿。
        qs = ["某句台词"] * 2 + [f"查询{i}" for i in range(6)]
        f = script(tmp_path, [BODY] * 8, queries=qs)
        c = get(cs.run(f), "查询不重复")
        assert not c.ok and "×2" in c.detail

    def test_全不重复就放行(self, tmp_path):
        f = script(tmp_path, [BODY] * 8, queries=[f"查询{i}" for i in range(8)])
        assert get(cs.run(f), "查询不重复").ok


class TestQueryHygiene:
    def test_查询里的构图词要拦下(self, tmp_path):
        # 索引建在字幕上，「中景逆光」这类词字幕里根本没有，必然检索落空
        f = script(tmp_path, [BODY] * 8,
                   queries=["中景，逆光，樱花飘落"] + ["某句台词"] * 7)
        c = get(cs.run(f), "查询无构图词")
        assert not c.ok and "中景" in c.detail

    def test_台词语义的查询放行(self, tmp_path):
        f = script(tmp_path, [BODY] * 8, queries=["角色承认自己一直在逃避"] * 8)
        assert get(cs.run(f), "查询无构图词").ok


class TestAnchors:
    """剧情锚点 = 画面块 `集:` 字段，不是正文里的「第X集」字样。

    2026-08-12 改：正文要不要点明集数、剧情对不对，是 02.5 人审的活；
    机检逼正文写集数 = 让机器决定内容。锚点改认每段画面块的集号字段
    （ADR-0004 解析结果），所以有集号的段落越多锚点越足。
    """

    def _script(self, tmp_path, bodies, eps):
        text = "\n\n".join(
            f"## 段落 {i + 1}\n\n配音：{b}\n\n画面：\n  集: {e}" if e else f"## 段落 {i + 1}\n\n配音：{b}\n\n画面："
            for i, (b, e) in enumerate(zip(bodies, eps)))
        f = tmp_path / "02-script.md"
        f.write_text(text, encoding="utf-8")
        return f

    def test_正文集数不算锚点(self, tmp_path):
        # 正文满是「第一集」「第二十三集」，画面块一个集号都没有 → 不算锚点
        bodies = [f"第{i}集讲了那么件事" for i in range(1, 5)]
        f = self._script(tmp_path, bodies, [None] * 4)
        c = get(cs.run(f), "剧情锚点 ≥3")
        assert not c.ok and "0 段" in c.detail

    def test_画面块集号算锚点(self, tmp_path):
        f = self._script(tmp_path, ["正文不写集数"] * 4, ["S01E01"] * 4)
        c = get(cs.run(f), "剧情锚点 ≥3")
        assert c.ok and "4/4 段" in c.detail

    def test_集号太少锚点不足(self, tmp_path):
        f = self._script(tmp_path, ["正文"] * 4, ["S01E01", None, None, None])
        c = get(cs.run(f), "剧情锚点 ≥3")
        assert not c.ok and "1/4 段" in c.detail

    def test_格式错误的集号不计锚点(self, tmp_path):
        # 格式坏掉会被「集号格式」项拦下，这里确认它同时不算锚点
        f = self._script(tmp_path, ["正文"] * 4, ["第一季第七集", "S01E01", "S01E02", None])
        c = get(cs.run(f), "剧情锚点 ≥3")
        assert not c.ok and "2/4 段" in c.detail


class TestRhythm:
    """节奏：**换气卡上限，机器味卡起伏。**

    2026-08-04 推翻了 08-03 的判据。08-03 卡的是「气口段均长 ≥11 字」，
    重测 11 篇人类样本时被打脸：赞数最高的两篇（30400、18399）气口均长只有
    7.1 和 7.5，都会被那道门禁判死。**它惩罚的是短句多的文风，不是机器味。**

    分得开的是起伏：三期 AI 稿句长 CV 0.34/0.46/0.48，人类样本 0.45–0.81。
    完整对照见 skills/write-script/BASELINE.md。
    """

    def test_单个气口段超长要拦(self, tmp_path):
        # 换气发生在逗号之间。这一段 34 字中间没有任何停顿点，念到一半就得吸气。
        winded = "他站起来把那句所有人都想说但谁也不肯先开口的话原原本本讲完了然后坐下。"
        c = get(cs.run(script(tmp_path, [winded] * 8)), "气口段 ≤")
        assert not c.ok and "34字" in c.detail

    def test_整句长但逗号密照样放行(self, tmp_path):
        # 整句 50 字，气口段最长 17 字——念得完。这正是旧规则搞反的地方。
        flowing = ("他不是不会说话，是算准了说什么最快能让人讨厌他，"
                   "而被讨厌这件事只要是他自己选的就不再算失败。")
        assert get(cs.run(script(tmp_path, [flowing] * 8)), "气口段 ≤").ok

    def test_长短齐一的稿子拦下(self, tmp_path):
        # 每段同一个句式、同一个长度：机检全绿但人一听就是同一个模子。
        even = "他站起来说了那句话，全场安静了三秒钟。她低头看着桌子，没有接他的话。"
        c = get(cs.run(script(tmp_path, [even] * 8)), "句长起伏")
        assert not c.ok and "抛光" in c.detail

    def test_有长有短的稿子放行(self, tmp_path):
        # 一句长的铺开，一句短的砸下来——这是参照样本的形状
        varied = ("他不是不会说话，是算准了说什么最快能让人讨厌他，"
                  "而被讨厌这件事只要是他自己选的，就不再算作一次失败。他清楚。")
        assert get(cs.run(script(tmp_path, [varied, "第二季第二集，全场安静了三秒。"] * 4)),
                   "句长起伏").ok

    def test_一句长句都没有要拦(self, tmp_path):
        # 三期实测这一项全是 0%，而人类样本是 0–43%。
        # 短句配额管得住「太碎」，管不住「全篇一律中等长度」。
        c = get(cs.run(script(tmp_path, [BODY] * 8)), "超 45 字长句")
        assert not c.ok and "0 句" in c.detail

    def test_两句长句就够(self, tmp_path):
        long_one = ("他不是不会说话，是算准了说什么最快能让人讨厌他，"
                    "而被讨厌这件事只要是他自己选的，就不再算作一次失败。")
        assert len(long_one) > 45
        assert get(cs.run(script(tmp_path, [long_one] * 2 + [BODY] * 6)), "超 45 字长句").ok

    def test_短句超配额要报出来(self, tmp_path):
        # 一段 3 句 × 8 段 = 24 处，远超配额 5
        f = script(tmp_path, ["那不是牺牲。是止损。他自己清楚。"] * 8)
        c = get(cs.run(f), "≤8字短句")
        assert not c.ok and "「那不是牺牲」" in c.detail

    def test_少量短句放行(self, tmp_path):
        # 全篇 1 处短句正是参照样本的密度，不该拦
        f = script(tmp_path, ["这就很奇怪。" + BODY] + [BODY] * 7)
        c = get(cs.run(f), "≤8字短句")
        assert c.ok and "1 处" in c.detail


class TestStockPhrase:
    """跨期套话：**跟话题无关的句子，就是每期长得一样的原因。**

    这几条实测在三期里复用过（「说句公道话」3/3 期）。它们一条内容都不携带，
    作用只是宣告「下面这句重要」或「我要让步了」。
    """

    def test_实测复用过的套话要抓住(self):
        for s in ["当然要说句公道话，他确实护住了几个人。",
                  "当然要说句实话，她不好相处。",
                  "我知道你要说什么，太冷了太完美了。",
                  "这就是我的答案。", "所以别误会，我不是这个意思。",
                  "你先别急，我先给你三条理由。"]:
            assert cs.STOCK.findall(s), s

    def test_注意你看要带逗号才算(self):
        # 「你看他那个表情」是正常叙事，判死它就是误报。
        # 检查项误报一次，人就会开始怀疑其余十条。
        for s in ["你看他那个表情就知道了。", "注意力全在她身上。",
                  "他让我注意安全。", "你看过第三季吗。"]:
            assert not cs.STOCK.findall(s), s
        assert cs.STOCK.findall("他走了。注意，她没有追上去。")
        assert cs.STOCK.findall("你看，这件事他一次都没提过。")

    def test_正常内容放行(self, tmp_path):
        assert get(cs.run(script(tmp_path, [BODY] * 8)), "无跨期套话").ok

    def test_套话要走到检查结果里(self, tmp_path):
        # 变异检验补的：原先只测了正则，把 add() 改成恒真也不红。
        # 正则对不代表门禁接上了。
        f = script(tmp_path, [BODY] * 7 + ["当然要说句公道话，" + BODY])
        c = get(cs.run(f), "无跨期套话")
        assert not c.ok and "说句公道话" in c.detail

    def test_报菜名要走到检查结果里(self, tmp_path):
        f = script(tmp_path, ["第一条，她说规矩。第二条，她不灌鸡汤。" + BODY] + [BODY] * 7)
        c = get(cs.run(f), "论据不报菜名")
        assert not c.ok and "挑一个挖到底" in c.detail

    def test_单个序号不拦(self, tmp_path):
        # 一条论据用「第一条」开头是正常写法，只有并列平铺才是病
        f = script(tmp_path, ["第一条，她永远把规矩说在前头。" + BODY] + [BODY] * 7)
        assert get(cs.run(f), "论据不报菜名").ok

    def test_报菜名要凑够两个才算(self):
        # 单独一个「第一条」是正常写法；三个并排就是在念清单
        assert len(set(cs.ENUM.findall("第一条，她把规矩说在前头。"))) < 2
        assert len(set(cs.ENUM.findall(
            "第一条，她说规矩。第二条，她不灌鸡汤。第三条，她不让人闲着。"))) >= 2

    def test_季集写法不误伤(self):
        # 「第三季第十一集」既不在句首带条点，也不是并列——不该命中
        assert not cs.ENUM.findall("这套办法的账，到第三季第十一集才结清。")


class TestNoArabicDigits:
    """阿拉伯数字。**2026-08-03 真的进了成片。**

    「平时只做2件事」被 IndexTTS 念成「平时只做匪件事」。那个「匪」字和引号那次
    （「谁更该“赢”」→「谁更该非赢匪」）是同一个——模型遇到念不出来的字符就拿
    垃圾音节去凑。回读质检抓不住：59 字的段落错 1 个字 CER 1.7%、绝对错数 1，
    而 `MIN_EDITS` 那道闸本来就是为了不让单字替换冤枉短段落。
    **这类缺陷要在合成前挡掉，不该指望回读。**
    """

    def _one(self, tmp_path, body: str):
        return get(cs.run(script(tmp_path, [body])), "无阿拉伯数字")

    def test_半角数字判失败(self, tmp_path):
        assert not self._one(tmp_path, "平时只做2件事，看番和写代码。").ok

    def test_全角数字判失败(self, tmp_path):
        assert not self._one(tmp_path, "平时只做２件事，看番和写代码。").ok

    def test_中文数字放行(self, tmp_path):
        assert self._one(tmp_path, "平时只做两件事，看番和写代码。").ok

    def test_拉丁字母不管(self, tmp_path):
        # 同一期的 yy 与 coding 都念得正常。没有证据就不立规矩。
        assert self._one(tmp_path, "今天开始yy，顺便写点coding。").ok


class TestSubjectiveHint:
    """机检末尾的主观项提示要按题材来，不能焊死杂谈的判据。

    人物志/剧情回顾/共鸣没有张力、公道话这两个字段——提示如果每期都逼人去查
    「张力立不立得住、公道话像不像反方」，等于要求人核验本期不存在的东西。
    """

    def _topic(self, tmp_path, genre_line):
        f = tmp_path / "01-topic.md"
        f.write_text(f"番:   东京喰种\n{genre_line}\n锚点: S01E07\n", encoding="utf-8")
        return tmp_path / "02-script.md"

    def test_读不到题材退回通用提示(self, tmp_path):
        s = self._topic(tmp_path, "类型: 不知道是啥")
        hint = cs.subjective_hint(*cs.episode_genre(s))
        assert "公道话" not in hint and "张力立不立得住" not in hint

    def test_人物志不逼查张力公道话判据(self, tmp_path):
        s = self._topic(tmp_path, "类型: 人物志（经历+点评，编年体）")
        genre, mode = cs.episode_genre(s)
        hint = cs.subjective_hint(genre, mode)
        assert "安论点" in hint
        assert "公道话" not in hint and "立不立得住" not in hint

    def test_杂谈带括号备注也能识别(self, tmp_path):
        s = self._topic(tmp_path, "类型: 杂谈思辨向\n模式: 驳论")
        genre, mode = cs.episode_genre(s)
        assert genre == "杂谈" and mode == "驳论"
        assert "公道话像不像反方" in cs.subjective_hint(genre, mode)

    def test_杂谈吐槽不吃张力公道话判据(self, tmp_path):
        hint = cs.subjective_hint("杂谈", "吐槽")
        assert "不吃张力/公道话判据" in hint

    def test_剧情回顾提醒因果链(self, tmp_path):
        genre, mode = "剧情回顾", ""
        assert "因果链" in cs.subjective_hint(genre, mode)

    def test_无类型文件不报错(self, tmp_path):
        assert cs.episode_genre(tmp_path / "02-script.md") == ("", "")


class TestParseEpisodes:
    """`集:` 字段（ADR-0004）：**必须按块切**，引用核对区不误抓。"""

    def test_按块抓集号(self):
        text = ("## 段落 1\n\n配音：a\n\n画面：\n  集: S01E07\n"
                "## 段落 2\n\n配音：b\n\n画面：\n  集: S1E8\n")
        assert cs.parse_episodes(text) == [("1", "S01E07"), ("2", "S1E8")]

    def test_引用核对区不误抓(self):
        # 「S01E01 21:21」是逐字引语的自查记录，不是检索约束；它在引用核对区，
        # 不在任何段落块内。全文正则一抓就把「21:21」整行当集号了。
        text = ("## 引用核对\n\n- S01E01 21:21 董香的原话\n\n"
                "## 段落 1\n\n配音：a\n\n画面：\n  集: S01E07\n")
        assert cs.parse_episodes(text) == [("1", "S01E07")]

    def test_没写集号返回空(self):
        assert cs.parse_episodes("## 段落 1\n\n配音：a\n\n画面：\n  查询: q\n") == []


class TestEpisodeChecks:
    """机检两道新检查：集号格式（永远可跑）+ 集号在素材库（要番名+登记表）。

    无集字段不判失败（人物志有已知落空段落，强制每段写 = 逼人编集号），
    覆盖率只作信息报在 detail 里。读不到番名/登记表时显式 FAIL「跳过」——
    S9「跳过不是通过」。
    """

    def _sources(self, monkeypatch, sources):
        from pipeline import ingest
        monkeypatch.setattr(ingest, "load_sources", lambda anime: sources)

    def _fail_sources(self, monkeypatch):
        from pipeline import ingest
        def boom(anime):
            raise SystemExit("FAIL 没有片源登记表，先跑 `ingest sources`")
        monkeypatch.setattr(ingest, "load_sources", boom)

    def test_规范形过_覆盖率报在detail(self, tmp_path):
        f = script(tmp_path, [BODY] * 2, eps=["S01E07", None])
        c = get(cs.run(f), "集号格式")
        assert c.ok and "1/2 段" in c.detail

    def test_S1E7归一化_放行(self, tmp_path):
        f = script(tmp_path, [BODY], eps=["S1E7"])
        assert get(cs.run(f), "集号格式").ok

    def test_中文写法FAIL_带段落号(self, tmp_path):
        f = script(tmp_path, [BODY], eps=["第一季第七集"])
        c = get(cs.run(f), "集号格式")
        assert not c.ok and "段1" in c.detail and "S01E07" in c.detail

    def test_集号都在素材库_PASS(self, tmp_path, monkeypatch):
        f = script(tmp_path, [BODY], eps=["S01E07"])
        (tmp_path / "01-topic.md").write_text("番: 东京喰种\n", encoding="utf-8")
        self._sources(monkeypatch, {"S01E07": {"path": "/x/e07.mkv"}})
        assert get(cs.run(f), "集号在素材库").ok

    def test_不在素材库_同季有别的集_提示多半写错(self, tmp_path, monkeypatch):
        f = script(tmp_path, [BODY], eps=["S01E08"])
        (tmp_path / "01-topic.md").write_text("番: 东京喰种\n", encoding="utf-8")
        self._sources(monkeypatch, {"S01E07": {"path": "/x/e07.mkv"}})
        c = get(cs.run(f), "集号在素材库")
        assert not c.ok and "多半是集号写错" in c.detail

    def test_不在素材库_整季都没有_提示先入库(self, tmp_path, monkeypatch):
        f = script(tmp_path, [BODY], eps=["S01E08"])
        (tmp_path / "01-topic.md").write_text("番: 东京喰种\n", encoding="utf-8")
        self._sources(monkeypatch, {"S02E01": {"path": "/x/e01.mkv"}})
        c = get(cs.run(f), "集号在素材库")
        assert not c.ok and "整季没入库" in c.detail and "ingest" in c.detail

    def test_读不到番名_跳过不是通过(self, tmp_path):
        # 没有 01-topic.md → 读不到番名 → 显式 FAIL，不许静默跳过
        f = script(tmp_path, [BODY], eps=["S01E07"])
        c = get(cs.run(f), "集号在素材库")
        assert not c.ok and "跳过不可判定" in c.detail

    def test_缺素材库_跳过不是通过(self, tmp_path, monkeypatch):
        f = script(tmp_path, [BODY], eps=["S01E07"])
        (tmp_path / "01-topic.md").write_text("番: 东京喰种\n", encoding="utf-8")
        self._fail_sources(monkeypatch)
        c = get(cs.run(f), "集号在素材库")
        assert not c.ok and "跳过不可判定" in c.detail

    def test_没写集号_素材库检查不跑(self, tmp_path):
        # 零集号 = 零约束，不存在可校验的对象；不建 01-topic.md 也不该报
        f = script(tmp_path, [BODY])
        assert "集号在素材库" not in [c.name for c in cs.run(f)]

    def test_格式不合规的集号_素材库检查不再重复判(self, tmp_path, monkeypatch):
        # 格式错已由「集号格式」FAIL 报过；存在性检查只测格式合规的集号，
        # 否则同一个「第一季第七集」要被两道检查各报一遍。
        f = script(tmp_path, [BODY], eps=["第一季第七集"])
        (tmp_path / "01-topic.md").write_text("番: 东京喰种\n", encoding="utf-8")
        self._sources(monkeypatch, {})
        c = get(cs.run(f), "集号在素材库")
        assert c.ok
