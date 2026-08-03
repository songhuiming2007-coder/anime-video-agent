"""视觉索引：角色名映射、在场判定、元信息硬校验。

这个模块的风险集中在**元信息校验**上，理由是这类不一致不会自己暴露：
维度不同会崩，那算运气好；同维度换模型不会崩——余弦照样算得出来，
分数照样落在看起来正常的区间，照样过阈值、照样返回 Top-K、照样渲染出片。

所以这里每一条断言都在问同一件事：**该失败的时候它失败了吗。**
"""

import json

import pytest

from pipeline import vindex


# ---------------------------------------------------------------- 角色名表


TABLE = {
    "春物": {
        "_note": "下划线开头的键是注释，不该被当成角色",
        "yukinoshita_yukino": ["雪乃", "雪之下雪乃"],
        "yuigahama_yui": ["结衣", "团子"],
    }
}


@pytest.fixture
def table(tmp_path):
    p = tmp_path / "characters.json"
    p.write_text(json.dumps(TABLE, ensure_ascii=False), encoding="utf-8")
    return p


class TestAliasMap:
    def test_每个别名都映射到同一个标签(self, table):
        m = vindex.alias_map("春物", table)
        assert m["雪乃"] == m["雪之下雪乃"] == "yukinoshita_yukino"
        assert m["团子"] == "yuigahama_yui"

    def test_标签本身也算别名(self, table):
        # 方便直接写 booru 名，也让 `tag_of` 对已经是标签的输入幂等
        assert vindex.alias_map("春物", table)["yuigahama_yui"] == "yuigahama_yui"

    def test_下划线开头的键不是角色(self, table):
        assert "_note" not in vindex.alias_map("春物", table)

    def test_没有这部番就失败(self, table):
        with pytest.raises(SystemExit):
            vindex.alias_map("紫罗兰", table)

    def test_没有名表文件就失败(self, tmp_path):
        with pytest.raises(SystemExit):
            vindex.alias_map("春物", tmp_path / "缺.json")


# ---------------------------------------------------------------- 在场判定


def presence(**kw):
    """两个镜头：0–10 秒有雪乃，10–20 秒有结衣。"""
    return vindex.Presence(
        anime="春物",
        by_ep={"S01E01": [
            {"i": 0, "start": 0.0, "end": 10.0, "tags": {"yukinoshita_yukino"}},
            {"i": 1, "start": 10.0, "end": 20.0, "tags": {"yuigahama_yui"}},
        ]},
        alias={"雪乃": "yukinoshita_yukino", "结衣": "yuigahama_yui",
               "yukinoshita_yukino": "yukinoshita_yukino",
               "yuigahama_yui": "yuigahama_yui"},
        threshold=kw.get("threshold", 0.5),
    )


class TestPresent:
    def test_区间落在镜头内(self):
        assert presence().present(1, 1, 2.0, 5.0, "雪乃") is True

    def test_不在场返回_False(self):
        assert presence().present(1, 1, 2.0, 5.0, "结衣") is False

    def test_跨镜头时任一命中即算命中(self):
        # 一句台词横跨两三个镜头是常态；镜头 1 有结衣，所以这个区间算有结衣
        assert presence().present(1, 1, 8.0, 12.0, "结衣") is True

    def test_端点相接不算相交(self):
        # [10,20) 是结衣那个镜头；查询 [0,10) 只碰到镜头 0
        assert presence().present(1, 1, 0.0, 10.0, "结衣") is False

    def test_没索引的集返回_False_而不是报错(self):
        # 缺一集不该让整期崩掉，排片那侧本来就设计成过滤为空则退回
        assert presence().present(2, 5, 0.0, 10.0, "雪乃") is False

    def test_名表里没有的角色当场报错(self):
        # **不是静默返回 False**：写错名字和「他没出现」是两回事，
        # 静默返回 False 会让整段过滤悄悄失效，而看起来只是「这段没找到画面」
        with pytest.raises(SystemExit):
            presence().present(1, 1, 0.0, 5.0, "三浦")


# ---------------------------------------------------------------- 元信息校验


def meta(**kw):
    m = {"kind": "presence", "producer": "ccip", "anime": "春物",
         "model_id": "deepghs/ccip_onnx", "revision": None}
    m.update(kw)
    return m


class TestCheckMeta:
    def test_种类不符就失败(self, tmp_path):
        with pytest.raises(SystemExit, match="不是 scene 索引"):
            vindex._check_meta(meta(), tmp_path / "x.json", kind="scene")

    def test_换了模型就失败(self, tmp_path):
        # **这是本模块最要紧的一条。** 同维度换 backbone 不会崩，
        # 分数照样落在正常区间，照样过阈值，照样出片。
        with pytest.raises(SystemExit, match="重建索引"):
            vindex._check_meta(meta(), tmp_path / "x.json",
                               kind="presence", model_id="别的模型")

    def test_模型一致就放行(self, tmp_path):
        out = vindex._check_meta(meta(), tmp_path / "x.json",
                                 kind="presence", model_id="deepghs/ccip_onnx")
        assert out["producer"] == "ccip"

    def test_不给_model_id_就只校验种类(self, tmp_path):
        # 两个 producer 的模型不同，加载 presence 时按 producer 分别校验
        assert vindex._check_meta(meta(), tmp_path / "x.json", kind="presence")


class TestShotsFingerprint:
    def test_切分参数进指纹(self):
        m = {"detector": "ffmpeg-scdet", "scene_threshold": 10.0,
             "min_shot": 0.5, "duration": 1450.0, "anime": "春物"}
        f = vindex._shots_fingerprint(m)
        assert f == {"detector": "ffmpeg-scdet", "scene_threshold": 10.0,
                     "min_shot": 0.5, "duration": 1450.0}

    def test_阈值变了指纹就变(self):
        # 切分变了，索引里的镜头号就不再指向同一段时间——而这不会崩
        a = vindex._shots_fingerprint(
            {"detector": "d", "scene_threshold": 10.0, "min_shot": 0.5, "duration": 1.0})
        b = vindex._shots_fingerprint(
            {"detector": "d", "scene_threshold": 12.0, "min_shot": 0.5, "duration": 1.0})
        assert a != b

    def test_片长变了指纹也变(self):
        # 换片源（不同压制、不同版本）时长会变，镜头表整体失效
        a = vindex._shots_fingerprint(
            {"detector": "d", "scene_threshold": 10.0, "min_shot": 0.5, "duration": 1450.0})
        b = vindex._shots_fingerprint(
            {"detector": "d", "scene_threshold": 10.0, "min_shot": 0.5, "duration": 1400.0})
        assert a != b


class TestParseKey:
    def test_正常集号(self):
        assert vindex._parse_key("S02E07") == (2, 7)

    def test_OVA_记作_E00(self):
        assert vindex._parse_key("S01E00") == (1, 0)

    @pytest.mark.parametrize("bad", ["S1E7", "S02E7", "02E07", "S02E07x", ""])
    def test_格式不对就失败(self, bad):
        with pytest.raises(SystemExit):
            vindex._parse_key(bad)


class TestNoteEpisodes:
    def test_只数速查表的行(self, tmp_path):
        # **不能按全文出现的集号数**：正文里到处都在引用集号（「见 S2E02」），
        # 那样数出来永远偏大，而这条判据的全部意义就是发现偏小
        p = tmp_path / "n.md"
        p.write_text(
            "# 春物\n\n正文里提到 S2E02 和 S3E04，这些不算。\n\n"
            "| 集 | 摘要 |\n|---|---|\n"
            "| S1E01 | 侍奉部成立 |\n| S1E02 | 材木座 |\n| S1OVA | 后日谈 |\n"
            "\n### S2E11 这是小节标题，也不算\n",
            encoding="utf-8")
        assert vindex.note_episodes(p) == {"S01E01", "S01E02", "S01E00"}

    def test_同一集出现在两张表里只数一次(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("| S1E01 | a |\n| S1E01 | b |\n", encoding="utf-8")
        assert vindex.note_episodes(p) == {"S01E01"}

    def test_没有笔记返回空(self, tmp_path):
        assert vindex.note_episodes(tmp_path / "缺.md") == set()


class TestIndexed:
    """「名表里有」与「索引里有」是两回事，必须分开报。

    一个在角色名表里、却没有任何簇贴给他的角色，过滤永远返回空、永远退回——
    **那是 Phase 0 的贴名没做完，不是这一段漏检**。两者修法完全不同：
    前者去补贴名，后者是检测的固有局限，只能接受。
    不分开的话，前者会伪装成后者，看起来像「检测不给力」。
    """

    def test_只算真的出现过的角色(self):
        assert presence().indexed() == {"yukinoshita_yukino", "yuigahama_yui"}

    def test_名表里有但索引里没有的能被发现(self):
        # 名表登记了三个人，索引里只有两个
        p = presence()
        p.alias["一色"] = "isshiki_iroha"
        assert p.tag_of("一色") not in p.indexed()
        assert p.tag_of("雪乃") in p.indexed()

    def test_空索引返回空集(self):
        p = presence()
        p.by_ep = {}
        assert p.indexed() == set()
