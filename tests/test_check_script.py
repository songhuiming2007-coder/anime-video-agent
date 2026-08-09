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


def build(vos, queries=None):
    """按稿件格式拼一篇。queries 不给就每段配一条。"""
    parts = []
    for i, v in enumerate(vos, 1):
        q = queries[i - 1] if queries else "某句台词"
        parts.append(f"## 段落 {i}\n\n配音：{v}\n\n画面：\n  查询: {q}\n")
    return "\n".join(parts)


def script(tmp_path, vos, queries=None):
    f = tmp_path / "02-script.md"
    f.write_text(build(vos, queries), encoding="utf-8")
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


class TestAnchorPattern:
    """具名场合词的正则拼装：纯函数，给什么词表就拼什么正则，不碰配置/文件。"""

    def test_季集写法与番无关永远命中(self):
        assert cs._anchor_pattern([]).findall("第二季第三集 S02E02 第十话") == [
            "第二季", "第三集", "S02E02", "第十话"]

    def test_具名场合词命中(self):
        assert cs._anchor_pattern(["天台", "转学"]).findall(
            "那天在天台上，后来他转学了") == ["天台", "转学"]

    def test_不在词表里的词不命中(self):
        assert cs._anchor_pattern(["天台"]).findall("文化祭") == []

    def test_空词表只剩季集写法且正则不崩(self):
        # 空列表会让 join 出来是空串，正则里留下一个 `|)` 就废了——所以代码里有分支
        assert cs._anchor_pattern([]).findall("第二季 文化祭") == ["第二季"]


class TestEpisodeAnchorWords:
    """锚点词必须能换番——2026-08-09 从「全局单例，换番要手改配置」改成
    「每期按自己 01-topic.md 的番名，从 config 里按番分桶取词表」，因为
    `data/episodes/` 下不同番的期数是同时存在的，不是切换着做。"""

    def _config(self, tmp_path, monkeypatch, table):
        (tmp_path / "project.json").write_text(
            json.dumps({"anime": {"anchor_words": table}}), encoding="utf-8")
        monkeypatch.setattr(paths, "CONFIG", tmp_path)
        monkeypatch.setattr(paths, "_CONF", None)

    def _episode(self, tmp_path, name: str, anime: str | None):
        ep = tmp_path / name
        ep.mkdir()
        if anime is not None:
            (ep / "01-topic.md").write_text(f"番: {anime}\n", encoding="utf-8")
        return ep / "02-script.md"  # episode_anchor_words 只看目录，不要求文件真存在

    def test_按番名挑对应的桶(self, tmp_path, monkeypatch):
        self._config(tmp_path, monkeypatch,
                     {"春物": ["文化祭"], "东京喰种": ["库因克", "赫子"]})
        script = self._episode(tmp_path, "ep", "东京喰种")
        assert cs.episode_anchor_words(script) == ["库因克", "赫子"]

    def test_不拿别番的词表垫背(self, tmp_path, monkeypatch):
        # 只测「选对」不够，选错了也可能刚好非空——反向也要测
        self._config(tmp_path, monkeypatch,
                     {"春物": ["文化祭"], "东京喰种": ["库因克"]})
        script = self._episode(tmp_path, "ep", "东京喰种")
        assert "文化祭" not in cs.episode_anchor_words(script)

    def test_该番还没建词表就退回空列表(self, tmp_path, monkeypatch):
        self._config(tmp_path, monkeypatch, {"春物": ["文化祭"]})
        script = self._episode(tmp_path, "ep", "夏日重现")
        assert cs.episode_anchor_words(script) == []

    def test_没有01_topic就退回空列表不崩(self, tmp_path, monkeypatch):
        self._config(tmp_path, monkeypatch, {"春物": ["文化祭"]})
        script = self._episode(tmp_path, "ep", None)
        assert cs.episode_anchor_words(script) == []


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
