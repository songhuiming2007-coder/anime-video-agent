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

import json
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

    def test_裸JP样式被滤掉_但SubJP不受牵连(self):
        # 2026-08-09 罪恶王冠（诸神字幕组）：日文轨样式就叫裸 "JP"，不落进任何前缀规则，
        # 全靠 KANA 兜底——但纯汉字的日文词/人名零假名，会被当中文台词漏进索引
        # （实测漏了 183 条：「桜満集」「了解」「作戦開始」…）。精确匹配，不是前缀匹配，
        # 就是为了不牵连 Sub-JP 这类已有样式。
        assert subindex.NON_DIALOGUE_STYLE.match("JP")
        assert subindex.NON_DIALOGUE_STYLE.match("jp")
        assert not subindex.NON_DIALOGUE_STYLE.match("Sub-JP")
        assert not subindex.NON_DIALOGUE_STYLE.match("JPSC")

    def test_纯汉字日文人名不会当中文台词进索引(self, tmp_path):
        # test_裸JP样式被滤掉 保证的是「style 判据挡住了它」；这条守的是端到端结果——
        # 光有 style 判据、`parse()` 没接上就是白测。角色本名「桜満集」零假名，
        # 光靠 KANA 判据会放行，必须靠 style="JP" 才能挡住。
        f = ass(tmp_path,
                line(1, 4, "青春是谎言 亦是罪恶"),
                line(5, 8, "桜満集", style="JP"),
                line(9, 12, "盲目地肯定周遭的所有事物"))
        assert all("桜満集" not in u.text for u in subindex.parse(f, "X", 1, 1))

    def test_反序命名的插曲歌词样式也被滤掉(self):
        # 2026-08-09 罪恶王冠：插曲歌词样式叫 CN_song/JP_song/Eng.song——跟已有的
        # song_CN_ed 是同一个陷阱（语义贴题、画面是别的），只是复合顺序反了，
        # 前缀规则抓不到，实测漏了 16+22 条，用后缀匹配补上。
        for s in ("CN_song", "JP_song", "Eng.song", "cn_song"):
            assert subindex.NON_DIALOGUE_STYLE.match(s), s
        # 不能因为加了后缀匹配就误伤真台词样式——它们都不以 song 结尾
        for s in self.KEEP:
            assert "song" not in s.lower()

    def test_NOTE样式被滤掉(self):
        # 2026-08-09 罪恶王冠：NOTE 混标译注（多数，如「Daath：希伯来语…」）与极少数
        # 唯一承载某个画面文字的真台词。两难之下选「宁可漏，不可错」，整个 style 滤掉——
        # 详细取舍见 subindex.py 里 NON_DIALOGUE_STYLE 上方的注释。
        assert subindex.NON_DIALOGUE_STYLE.match("NOTE")
        assert subindex.NON_DIALOGUE_STYLE.match("note")

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


class TestEncoding:
    """字幕编码探测（2026-08-09，罪恶王冠 ep09-22 外挂字幕触发）。

    ASS 没有强制编码，`pysubs2.load` 不传 `encoding` 时按 UTF-8 硬解。
    之前所有番的外挂字幕恰好都是 UTF-8，这条分支从没被走到过；罪恶王冠的
    `.GB.ass`/`.BIG5.ass` 是 Windows 工具存出来的 UTF-16 LE 带 BOM，
    不探测直接送进 pysubs2.load 会当场 UnicodeDecodeError，phase0 在 ep09 就地崩溃。
    """

    def test_utf16le_bom_识别为utf16(self, tmp_path):
        f = tmp_path / "t.ass"
        f.write_bytes(b"\xff\xfe")
        assert subindex.sniff_encoding(f) == "utf-16"

    def test_utf16be_bom_识别为utf16(self, tmp_path):
        f = tmp_path / "t.ass"
        f.write_bytes(b"\xfe\xff")
        assert subindex.sniff_encoding(f) == "utf-16"

    def test_无bom时退回utf8sig(self, tmp_path):
        # utf-8-sig 对没有 BOM 的普通 UTF-8 同样安全：找不到 BOM 就按普通 UTF-8 解，
        # 不会影响任何现有素材（其余全部番的外挂字幕都是这种情况）。
        f = tmp_path / "t.ass"
        f.write_text("普通 UTF-8 文件", encoding="utf-8")
        assert subindex.sniff_encoding(f) == "utf-8-sig"

    def test_utf16文件能被parse正确解析(self, tmp_path):
        # 这条就是 2026-08-09 那个 bug 的复现：不传 encoding 时这里会 UnicodeDecodeError
        f = tmp_path / "t.ass"
        f.write_text(HEAD + line(1, 4, "青春是谎言 亦是罪恶") + "\n", encoding="utf-16")
        out = subindex.parse(f, "X", 1, 1)
        assert out and out[0].text == "青春是谎言 亦是罪恶"


class TestPairCriterion:
    """索引成对判据（2026-08-16 审计 2-1）：.json 与 .npy 都在才算「已索引」。

    索引是两段写、没有事务。崩溃残骸（npy 在、json 缺）此前被 phase0 的
    跳过判据当成「已索引」、被 status 的 *.npy glob 数进六条数字——检索池
    实际少一集而对账全绿，正是「六条数字」要防的那种假绿。
    """

    def test_只有npy不算已索引(self, tmp_path):
        (tmp_path / "春物_S01E01.npy").write_bytes(b"")
        assert subindex.has_index(tmp_path, "春物", 1, 1) is False

    def test_只有json不算已索引(self, tmp_path):
        (tmp_path / "春物_S01E01.json").write_text("{}", encoding="utf-8")
        assert subindex.has_index(tmp_path, "春物", 1, 1) is False

    def test_成对才算已索引(self, tmp_path):
        (tmp_path / "春物_S01E01.npy").write_bytes(b"")
        (tmp_path / "春物_S01E01.json").write_text("{}", encoding="utf-8")
        assert subindex.has_index(tmp_path, "春物", 1, 1) is True

    def test_别的集不算(self, tmp_path):
        (tmp_path / "春物_S01E02.npy").write_bytes(b"")
        (tmp_path / "春物_S01E02.json").write_text("{}", encoding="utf-8")
        assert subindex.has_index(tmp_path, "春物", 1, 1) is False


class TestLoadAllFilterBeforeCheck:
    """load_all 先按番过滤、再做元信息校验（2026-08-16 审计 2-8）。

    _check 对旧格式/换模型的文件直接 SystemExit，而索引目录是全局的——
    别的番有一个坏文件，当前番的检索就被拦死，报错还指向别人的文件。
    """

    def _ep_file(self, tmp_path, name, ep, anime):
        import numpy as np
        d = {"meta": {"model_id": subindex.MODEL_NAME, "revision": None},
             "units": [{"anime": anime, "season": 1, "episode": ep,
                        "start": 0.0, "end": 2.0, "text": "台词"}]}
        (tmp_path / f"{name}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")
        np.save(tmp_path / f"{name}.npy", np.zeros((1, 4), dtype=np.float32))

    def test_他番的旧格式索引不拦当前番(self, tmp_path):
        import numpy as np
        # 他番：旧格式（裸列表）——_check 会当场 SystemExit 要求重建
        (tmp_path / "他番_S01E01.json").write_text(
            json.dumps([{"anime": "他番"}]), encoding="utf-8")
        np.save(tmp_path / "他番_S01E01.npy", np.zeros((1, 4), dtype=np.float32))
        # 当前番：正常索引
        self._ep_file(tmp_path, "春物_S01E02", 2, "春物")
        vecs, units = subindex.load_all(tmp_path, "春物")
        assert len(units) == 1 and units[0].anime == "春物"

    def test_当前番自己的坏索引仍然拦(self, tmp_path):
        # 反向护栏：先过滤≠不校验——当前番的旧格式文件照样当场失败
        (tmp_path / "春物_S01E01.json").write_text(
            json.dumps([{"anime": "春物"}]), encoding="utf-8")
        import numpy as np
        np.save(tmp_path / "春物_S01E01.npy", np.zeros((1, 4), dtype=np.float32))
        with pytest.raises(SystemExit, match="reindex"):
            subindex.load_all(tmp_path, "春物")
