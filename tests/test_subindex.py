"""字幕 → 检索单元：该滤掉什么，以及滑窗怎么合并。

这个模块的全部风险都在**过滤**上。滤漏了不会报错——垃圾照样建成向量、照样被检索到、
分数照样落在阈值以上，只是切出来的画面是 OP 花瓣特效。

已经踩过两次，两次都是同一类东西：

- 2026-07-29 S3E02 建出 1010 个单元，619 个是 ASS 绘图坐标串。加了「必须含汉字」兜住。
- 2026-07-30 S1 每一集有 234 个漏网的。OP 特效每片花瓣带一个标题汉字，
  剥完标签正好是「坐标串 + 一个汉字」，汉字判据于是放行。
  **根因是 `_clean` 先剥 `{...}` 标签，把用来识别绘图的 `\\p1` 连同标签一起扔了**——
  判据依赖的信息在判据执行之前就没了。
"""

from pathlib import Path

import numpy as np
import pytest

from pipeline import subindex


HEAD = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,20
Style: OP-JP,Arial,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass(tmp_path, *events):
    p = tmp_path / "t.ass"
    p.write_text(HEAD + "\n".join(events) + "\n", encoding="utf-8")
    return p


def line(start, end, text, style="Default"):
    return (f"Dialogue: 0,0:00:{start:02d}.00,0:00:{end:02d}.00,"
            f"{style},,0,0,0,,{text}")


# OP 卡拉OK 的一片花瓣：绘图标签 + 坐标串 + 一个标题汉字
PETAL = r"{\move(470,-47,419,121)\p1\fad(100,200)}m 0 0 b -1 0 -3 -3 -4 -1 陽"


class TestDrawingFilter:
    def test_绘图事件被滤掉(self, tmp_path):
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, PETAL, style="OP-JP"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        assert all("b -1 0" not in u.text for u in subindex.parse(f, "X", 1, 1))

    def test_带汉字的绘图串也要滤掉(self, tmp_path):
        # 这条就是 2026-07-30 那个 bug。花瓣尾巴上挂着「陽」，
        # 「必须含汉字」的判据放行了它，S1 每集因此漏进 234 个。
        f = ass(tmp_path, line(1, 4, PETAL, style="OP-JP"))
        assert subindex.parse(f, "X", 1, 1) == []

    def test_判据看原文不看清洗后的文本(self, tmp_path):
        # 守的是修复的**方式**：\p1 藏在 {...} 里，_clean 会把整段剥掉。
        # 如果哪天有人把过滤挪到 _clean 之后，这条会红。
        assert subindex.DRAW.search(PETAL)
        assert not subindex.DRAW.search(subindex._clean(PETAL))

    def test_p0_是关闭绘图不该误杀(self, tmp_path):
        # \p0 表示「绘图模式到此结束」，后面跟的是真台词
        f = ass(tmp_path, line(1, 4, r"{\p0}这是一句真台词"))
        assert len(subindex.parse(f, "X", 1, 1)) == 1

    def test_普通覆盖标签不受影响(self, tmp_path):
        f = ass(tmp_path,
                line(1, 4, r"{\an8}青春是谎言 亦是罪恶"),
                line(5, 8, "盲目地肯定周遭的所有事物"))
        out = subindex.parse(f, "X", 1, 1)
        assert out and out[0].text.startswith("青春是谎言")


class TestStyleFilter:
    """OP/ED/插曲歌词按 style 滤掉。

    2026-07-30 踩的：段落「过日子最怕的是有话不说、攒着」检索到 S01E10 与 S01E11
    的 **22:43.63——两集同一个时间码，因为那是片尾 ED**。

    最坏的一种陷阱：**语义最贴、画面完全不能用。** ED 歌词就是全剧主题的浓缩，
    对「说不出口」这类主题式查询命中率极高，分数正常，切出来是滚字幕的片尾。

    判据用 style 而不是时间窗：style 是字幕组自己声明的分类，
    最接近「这一行是不是台词」这个事实；时间窗每集不同（有的集没 OP）。
    """

    KEEP = ["Sub-CN", "Text-cn", "Sub-JP", "Sub-CN-BZ", "Text-cnup",
            "Sub-CNUP", "Default", "Inner"]
    DROP = ["OP-CN", "ED-CN", "EDCN", "EDCN-b", "EDCN-yui", "EDCN-yui-2",
            "In-CN", "bgm-cn", "Title", "title", "Title-Yokoku", "STAFF", "Gamen"]

    def test_对话样式全部放行(self):
        for s in self.KEEP:
            assert not subindex.NON_DIALOGUE_STYLE.match(s), s

    def test_歌词与标题样式全部滤掉(self):
        # 这 13 个是春物三季实际用到的命名，逐季不同（OP-CN / EDCN / EDCN-yui…）
        for s in self.DROP:
            assert subindex.NON_DIALOGUE_STYLE.match(s), s

    def test_Inner_不会被当成_In_误杀(self):
        # `^in` 要求后面接分隔符或语言码，否则「Inner」这类内心独白样式会被连带滤掉
        assert not subindex.NON_DIALOGUE_STYLE.match("Inner")
        assert subindex.NON_DIALOGUE_STYLE.match("In-CN")

    def test_ED_歌词不进索引(self, tmp_path):
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "直到最后 我还是无法坦率起来", style="ED-CN"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        assert all("坦率" not in u.text for u in subindex.parse(f, "X", 1, 1))

    def test_滤掉歌词不会把相邻台词并到一起(self, tmp_path):
        # 滤是在滑窗**之前**做的，所以歌词行不会在窗口里占位。
        # 反过来（先滑窗再滤）会留下一个只剩半句的单元。
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "歌词歌词歌词", style="OP-CN"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        out = subindex.parse(f, "X", 1, 1)
        assert out[0].text == "青春是谎言 亦是罪恶 盲目地肯定周遭的所有事物"


class TestOtherFilters:
    def test_日文行滤掉(self, tmp_path):
        # 双语字幕中日同文件。检索查询是中文，混进日文既翻倍单元数，
        # 又会让滑窗把中日两句并成一个单元。
        f = ass(tmp_path,
                line(1, 4, "我是雪之下雪乃"),
                line(5, 8, "私は雪ノ下雪乃"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        assert all("私" not in u.text for u in subindex.parse(f, "X", 1, 1))

    def test_字幕组信息滤掉(self, tmp_path):
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "本字幕由诸神字幕组制作 仅供交流学习"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        assert all("字幕组" not in u.text for u in subindex.parse(f, "X", 1, 1))

    def test_单字不进索引(self, tmp_path):
        # 纯语气词对检索无价值，而且卡拉OK 逐字特效会产生大量单字事件
        f = ass(tmp_path,
                line(1, 4, "啊"),
                line(5, 8, "青春是谎言 亦是罪恶"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        assert all(u.text != "啊" for u in subindex.parse(f, "X", 1, 1))


class TestWindow:
    def test_滑窗合并相邻两句(self, tmp_path):
        # WINDOW=2：窗口=1 语义信号不足，窗口=3 混入无关台词。
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "讴歌青春之辈往往欺人欺己"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        out = subindex.parse(f, "X", 1, 1)
        assert out[0].text == "青春是谎言 亦是罪恶 讴歌青春之辈往往欺人欺己"

    def test_时间区间覆盖窗内全部台词(self, tmp_path):
        # 单元的时间区间是要拿去切视频的。取不全就会切掉半句话。
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "讴歌青春之辈往往欺人欺己"))
        u = subindex.parse(f, "X", 1, 1)[0]
        assert u.start == pytest.approx(1.0) and u.end == pytest.approx(8.0)

    def test_末句自己成一个单元(self, tmp_path):
        # 滑窗到末尾时只剩一句，仍然要建——否则最后一句检索不到
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "讴歌青春之辈往往欺人欺己"))
        assert subindex.parse(f, "X", 1, 1)[-1].text == "讴歌青春之辈往往欺人欺己"

    def test_番名季集原样带上(self, tmp_path):
        # 索引目录是全局的，跨番过滤全靠这三个字段
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "讴歌青春之辈往往欺人欺己"))
        u = subindex.parse(f, "春物", 2, 4)[0]
        assert (u.anime, u.season, u.episode) == ("春物", 2, 4)


class TestMeta:
    """索引必须自描述模型身份（ADR-0003）。

    **理由是这类不一致不会自己暴露。** 维度不同会崩（`np.vstack` 或 `vecs @ q` 抛异常），
    那算运气好；**同维度换模型不会崩**——余弦照样算得出来，分数照样落在看起来正常的
    区间，照样过阈值、照样返回 Top-K、照样渲染出片。

    2026-08-03 之前这个文件里只有台词和时间码，靠「只有一个人、只有一台机器、
    没改过模型」侥幸没出事。
    """

    def test_旧格式当场失败并给出重建命令(self):
        # 旧格式是裸列表。它建的时候用的什么模型无从得知，
        # 而拿 bge-base 的查询去打一份 bge-small 建的索引不会报错，只会静默变差。
        with pytest.raises(SystemExit, match="reindex"):
            subindex._check([{"anime": "春物"}], Path("春物_S01E01.json"))

    def test_换了模型就失败(self):
        d = {"meta": {"model_id": "BAAI/bge-small-zh-v1.5"}, "units": []}
        with pytest.raises(SystemExit, match="不可比"):
            subindex._check(d, Path("x.json"))

    def test_模型一致就放行(self):
        d = {"meta": {"model_id": subindex.MODEL_NAME, "revision": None},
             "units": [{"anime": "春物"}]}
        assert subindex._check(d, Path("x.json")) == [{"anime": "春物"}]

    def test_元信息带上检索约定(self):
        # 窗口和查询前缀变了，向量空间的含义就变了，但维度不变、不会崩
        m = subindex.meta(768)
        assert m["window"] == subindex.WINDOW
        assert m["query_prefix"] == subindex.QUERY_PREFIX
        assert m["dim"] == 768


class TestSearchEpisode:
    """ADR-0004 集掩码：给了集号就**只在该集内打分**。

    集号是剧情知识，比语义分更强——跨季/跨集的高分顶掉正确画面是本期
    （2026-08-09）踩的真 bug（段 19 正确的 S01E08 0.653 输给跨季 S04E05 0.506）。
    所以过滤进打分这一步，不是检索之后过滤；打分本身和掩码前完全一样。
    """

    def _units(self):
        return [
            subindex.Unit("东京喰种", 1, 7, 100.0, 103.0, "甲"),
            subindex.Unit("东京喰种", 1, 7, 200.0, 203.0, "乙"),
            subindex.Unit("东京喰种", 1, 8, 300.0, 303.0, "丙"),
            subindex.Unit("东京喰种", 2, 7, 400.0, 403.0, "丁"),
        ]

    def _embed(self, units):
        """把文本映射到二维向量，让点积可算：查询 = e0，各单元分数可指定。"""
        v = {"甲": [0.9, 0.1], "乙": [0.8, 0.2],
             "丙": [0.7, 0.3], "丁": [0.6, 0.4]}

        def embed(texts, is_query=False):
            rows = [[1.0, 0.0]] if is_query else [v[t] for t in texts]
            return np.array(rows, dtype=np.float32)
        return embed

    def test_不给集号_全量打分_与掩码前行为一致(self, monkeypatch):
        units = self._units()
        monkeypatch.setattr(subindex, "embed", self._embed(units))
        hits = subindex.search("q", np.array([[.9, .1], [.8, .2], [.7, .3], [.6, .4]]),
                               units, k=4)
        assert [(round(sc, 3), (u.season, u.episode, u.text)) for sc, u in hits] == [
            (0.9, (1, 7, "甲")), (0.8, (1, 7, "乙")),
            (0.7, (1, 8, "丙")), (0.6, (2, 7, "丁"))]

    def test_给集号_只在该集内打分(self, monkeypatch):
        units = self._units()
        monkeypatch.setattr(subindex, "embed", self._embed(units))
        hits = subindex.search("q", np.array([[.9, .1], [.8, .2], [.7, .3], [.6, .4]]),
                               units, k=4, season=1, episode=7)
        assert [(u.season, u.episode) for _, u in hits] == [(1, 7), (1, 7)]

    def test_该集在索引里没有单元_返回空(self, monkeypatch):
        units = self._units()
        monkeypatch.setattr(subindex, "embed", self._embed(units))
        hits = subindex.search("q", np.array([[.9, .1], [.8, .2], [.7, .3], [.6, .4]]),
                               units, season=3, episode=1)
        assert hits == []                # 空结果走降级链，不是硬失败

    def test_集号只给一个_当场报错(self, monkeypatch):
        units = self._units()
        monkeypatch.setattr(subindex, "embed", self._embed(units))
        with pytest.raises(SystemExit):
            subindex.search("q", np.array([[.9, .1]]), units, season=1)
