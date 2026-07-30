"""稿件机检：正文统计口径、片尾摘除、锚点词的番剧无关性。

这个模块的价值全在「检查项不误报」。**一个长期误报的检查项等于没有检查**——
人看两次全是红的就再也不看了，真出问题时也不会发现。所以两个坑都跟误报有关：

- 片尾套话「好了，今天就到这里」是固定收尾，不承担内容。不摘出去的话，
  每期都会把段数门槛顶穿一格、字数也虚高。
- 锚点词原先写死成春物的专有名词（文化祭、修学旅行、侍奉部……）。
  换一部番这一半一个都命中不了，「剧情锚点 ≥3」会永远判 0 处。
"""

import importlib
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
        f = script(tmp_path, ["啊" * 45 + "。"] + [BODY] * 7)
        c = get(cs.run(f), "无超")
        assert not c.ok and "45字" in c.detail


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


class TestAnchorWords:
    """锚点词必须能换番——这是这个模块唯一的通用性缺口，补的就是它。"""

    @pytest.fixture
    def with_words(self, tmp_path, monkeypatch):
        """用别的番的场合词重建 ANCHOR，跑完还原，免得污染其他用例。"""
        def apply(words):
            (tmp_path / "project.json").write_text(
                json.dumps({"anime": {"anchor_words": words}}), encoding="utf-8")
            monkeypatch.setattr(paths, "CONFIG", tmp_path)
            monkeypatch.setattr(paths, "_CONF", None)
            return importlib.reload(cs)
        yield apply
        monkeypatch.undo()
        importlib.reload(cs)

    def test_季集写法与番无关永远命中(self):
        assert cs.ANCHOR.findall("第二季第三集 S02E02 第十话") == [
            "第二季", "第三集", "S02E02", "第十话"]

    def test_换番之后新场合词命中(self, with_words):
        mod = with_words(["海边", "天台", "转学"])
        assert mod.ANCHOR.findall("那天在天台上，后来他转学了") == ["天台", "转学"]

    def test_换番之后旧番的词不再命中(self, with_words):
        # 反向也要测。只测「新词能中」的话，把配置读错成「追加」也会通过。
        mod = with_words(["海边", "天台"])
        assert mod.ANCHOR.findall("文化祭") == []

    def test_配置为空时只剩季集写法且正则不崩(self, with_words):
        # 空列表会让 join 出来是空串，正则里留下一个 `|)` 就废了——所以代码里有分支
        mod = with_words([])
        assert mod.ANCHOR.findall("第二季 文化祭") == ["第二季"]
