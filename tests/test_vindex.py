"""视觉索引：角色名映射、在场判定、元信息硬校验。

这个模块的风险集中在**元信息校验**上，理由是这类不一致不会自己暴露：
维度不同会崩，那算运气好；同维度换模型不会崩——余弦照样算得出来，
分数照样落在看起来正常的区间，照样过阈值、照样返回 Top-K、照样渲染出片。

所以这里每一条断言都在问同一件事：**该失败的时候它失败了吗。**
"""

import json
from pathlib import Path

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
    """两个镜头：0–10 秒有雪乃（0.95），10–20 秒有结衣（0.90）。"""
    return vindex.Presence(
        anime="春物",
        by_ep={"S01E01": [
            {"i": 0, "start": 0.0, "end": 10.0, "tags": {"yukinoshita_yukino"},
             "scores": {"yukinoshita_yukino": 0.95}},
            {"i": 1, "start": 10.0, "end": 20.0, "tags": {"yuigahama_yui"},
             "scores": {"yuigahama_yui": 0.90}},
        ]},
        alias={"雪乃": "yukinoshita_yukino", "结衣": "yuigahama_yui",
               "yukinoshita_yukino": "yukinoshita_yukino",
               "yuigahama_yui": "yuigahama_yui"},
        threshold=kw.get("threshold", 0.5),
    )


class TestPresenceScore:
    """在场分：present 的布尔实现全在这里，`present` 只是 `>= threshold` 包装。

    分数只用于**同一段落内、同一 producer 内**的候选 tie-break（ADR-0004），
    绝不跨段落、绝不跨 producer 比绝对值。没检出必须返回 0.0 而不是报错——
    排片侧的软过滤要拿这个数给漏检镜头垫底，不能让它炸。
    """

    def test_相交镜头取最大分(self):
        # 跨镜头时任一命中即算命中——分数同理，取相交镜头里的最大分
        assert presence().presence_score(1, 1, 8.0, 12.0, "结衣") == 0.90

    def test_没检出返回_0(self):
        assert presence().presence_score(1, 1, 2.0, 5.0, "结衣") == 0.0

    def test_没索引的集返回_0_而不是报错(self):
        assert presence().presence_score(2, 5, 0.0, 10.0, "雪乃") == 0.0

    def test_present_与分数同一条线(self):
        # 包装关系必须逐点成立：present 真 ⇔ 分数 >= threshold
        p = presence()
        for name in ("雪乃", "结衣"):
            got = p.presence_score(1, 1, 2.0, 5.0, name)
            assert p.present(1, 1, 2.0, 5.0, name) == (got >= p.threshold)

    def test_名表里没有的角色当场报错(self):
        with pytest.raises(SystemExit):
            presence().presence_score(1, 1, 0.0, 5.0, "三浦")


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

    def test_素材池的_SP_键季是_None(self):
        # 池索引（EGOIST SP01~08）加载要过这一关：看不懂 SP，整池加载不了
        assert vindex._parse_key("SP05") == (None, 5)

    def test_命中单元能回到集键(self):
        assert vindex.key_of(vindex.Shot("EGOIST", None, 5, 0.0, 1.0, "x")) == "SP05"
        assert vindex.key_of(vindex.Shot("春物", 1, 3, 0.0, 1.0, "x")) == "S01E03"

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


class TestSubtitleIndexCount:
    """成对计数（2026-08-16 审计 2-1）：口径必须与 load_all 一致。

    load_all 按 .json glob、要求 .npy 在盘；旧计数按 *.npy glob，孤儿 npy
    （写一半崩溃的残骸）被数进六条数字——对账全绿而检索池实际少一集。
    """

    def test_孤儿npy不计数(self, tmp_path):
        (tmp_path / "春物_S01E01.npy").write_bytes(b"")
        (tmp_path / "春物_S01E02.npy").write_bytes(b"")
        (tmp_path / "春物_S01E02.json").write_text("{}", encoding="utf-8")
        assert vindex.subtitle_index_count("春物", tmp_path) == 1

    def test_只有json也不计数(self, tmp_path):
        (tmp_path / "春物_S01E03.json").write_text("{}", encoding="utf-8")
        assert vindex.subtitle_index_count("春物", tmp_path) == 0

    def test_成对全计数(self, tmp_path):
        for ep in (1, 2, 3):
            (tmp_path / f"春物_S01E{ep:02d}.npy").write_bytes(b"")
            (tmp_path / f"春物_S01E{ep:02d}.json").write_text("{}", encoding="utf-8")
        assert vindex.subtitle_index_count("春物", tmp_path) == 3


class TestPresenceLoadHardCheck:
    """presence 加载的 model_id 与判定阈值一致性（2026-08-16 审计 2-2）。

    producer 检查拦不住 camie→wd 这种换 tagger（producer 字段都还是
    "tagger"），两种量纲的概率分会静默混用——同维度换模型不会崩，
    只会静默变差（ADR-0003 索引自描述标准的加载侧缺口）。
    """

    FP = {"detector": "x", "scene_threshold": 1.0, "min_shot": 1.0, "duration": 100.0}

    def _env(self, monkeypatch):
        monkeypatch.setattr(vindex, "presence_producer", lambda: "ccip")
        monkeypatch.setattr(vindex.shots, "load",
                            lambda anime, key: {"meta": self.FP,
                                                "shots": [{"i": 0, "start": 0.0, "end": 5.0}]})
        monkeypatch.setattr(vindex, "alias_map",
                            lambda anime: {"雪乃": "x", "x": "x"})

    def _write(self, tmp_path, name, ep, model_id, thr):
        d = {"meta": {"kind": "presence", "producer": "ccip", "anime": "春物",
                      "episode": ep, "shots": self.FP, "model_id": model_id,
                      "revision": None, "decision_threshold": thr},
             "shots": [{"i": 0, "char": {"x": 0.97}, "gen": {}}]}
        p = tmp_path / name
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return p

    def _ccip_id(self):
        from pipeline.faces import CCIP_MODEL, CCIP_REPO
        return CCIP_REPO + "/" + CCIP_MODEL

    def test_换了模型就失败(self, tmp_path, monkeypatch):
        self._env(monkeypatch)
        self._write(tmp_path, "春物_S01E01.presence.json", "S01E01",
                    "别的模型/别的权", 0.95)
        with pytest.raises(SystemExit, match="不可比"):
            vindex.load_presence("春物", tmp_path)

    def test_各集判定阈值不一致就失败(self, tmp_path, monkeypatch):
        self._env(monkeypatch)
        self._write(tmp_path, "春物_S01E01.presence.json", "S01E01",
                    self._ccip_id(), 0.95)
        self._write(tmp_path, "春物_S01E02.presence.json", "S01E02",
                    self._ccip_id(), 0.15)
        with pytest.raises(SystemExit, match="不一致"):
            vindex.load_presence("春物", tmp_path)

    def test_一致就加载(self, tmp_path, monkeypatch):
        self._env(monkeypatch)
        self._write(tmp_path, "春物_S01E01.presence.json", "S01E01",
                    self._ccip_id(), 0.95)
        self._write(tmp_path, "春物_S01E02.presence.json", "S01E02",
                    self._ccip_id(), 0.95)
        pres = vindex.load_presence("春物", tmp_path)
        assert pres.threshold == 0.95 and set(pres.by_ep) == {"S01E01", "S01E02"}


class TestAnimeFallback:
    """config 删掉 anime.default → status 显式报错，不静默落到「春物」（审计 2-16）。"""

    def test_无番名配置显式报错(self, monkeypatch):
        monkeypatch.setattr(vindex.paths, "conf", lambda d, default=None: default)
        monkeypatch.setattr("sys.argv", ["vindex", "status"])
        with pytest.raises(SystemExit, match="番名"):
            vindex.main()


# ---------------------------------------------------------------- 通道 2（v2：ADR-0015）


class TestCaptionCheck:
    """caption 输出校验：超长 / 禁词 / 空输出（ADR-0015 决定 2）。

    这里放宽或收紧的每一条，都会直接变成对账第七条上的一个洞或一次误伤：
    误伤把合格描述记成 failed（而 failed 是洞），放宽把评价词和剧情推测放进索引
    （那可是排片要拿来当画面语义用的文本）。
    """

    def test_标点与空格不计入字数(self):
        # ADR 写的是「30 个汉字」——量字符会把标点算进去，把合格描述判成超长
        assert vindex.caption_chars("夜晚教室，少女伏案，冷白灯光。") == 12

    def test_三十字合格三十一字超长(self):
        assert vindex.check_caption("字" * 30) is None
        assert "超长 31" in vindex.check_caption("字" * 31)

    def test_空输出与纯标点都算空(self):
        assert vindex.check_caption("   ") == "空输出"
        assert vindex.check_caption("，。！") == "空输出"

    def test_评价词与剧情推测进禁词(self):
        assert "禁词" in vindex.check_caption("夜晚教室，画面精美，冷白灯光")
        assert "禁词" in vindex.check_caption("少年站起，似乎要离开")

    def test_情绪词不是禁词(self):
        # 「情绪氛围」是要求里必须覆盖的一维。把「孤独」也禁掉等于逼模型交白卷
        assert vindex.check_caption("夜晚教室，少女独坐，冷白灯光，孤独压抑") is None

    def test_去掉前缀与包裹引号(self):
        assert vindex.clean_caption("描述：夜晚教室") == "夜晚教室"
        assert vindex.clean_caption("“夜晚教室，少女伏案”") == "夜晚教室，少女伏案"
        # 只剥离外层，不碰内容
        assert vindex.clean_caption("画面： 雨中天台") == "雨中天台"


class TestOom:
    """显存不足的判定必须能认出来：认不出来就是「batch 减半重试」整条路白写，
    认得太宽会把普通报错当成 OOM 无限重试（减到 1 之后再失败才算诚实失败）。
    """

    def test_认得出显存不足(self):
        assert vindex.is_oom(RuntimeError("CUDA out of memory. Tried to allocate 2.0 GiB"))

    def test_换后端后的类名也认(self):
        class OutOfMemoryError(RuntimeError):
            pass

        assert vindex.is_oom(OutOfMemoryError("nothing in the message"))

    def test_普通报错不算(self):
        assert not vindex.is_oom(ValueError("bad image size"))


class TestEmbedModelRef:
    """bge-m3 的加载目标：平铺本地目录优先。

    不是风格问题：HF 缓存的 blob 名带 etag，xet 后端每次请求换 etag，断一次就整份重下
    （2026-09-11 实测 2.27G 权重两次都没下完）。平铺目录 + curl 断点续传才稳得住，
    而且与云端数据盘的布局是同一个约定。
    """

    def test_有平铺目录就用它(self, tmp_path, monkeypatch):
        root = tmp_path / "models"
        (root / vindex.EMBED_REPO).mkdir(parents=True)
        monkeypatch.setattr(vindex.paths, "MODELS", root)
        assert vindex.embed_model_ref() == str(root / vindex.EMBED_REPO)

    def test_没有就退回_HF_repo_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vindex.paths, "MODELS", tmp_path / "空")
        assert vindex.embed_model_ref() == vindex.EMBED_REPO


class TestCaptionMeta:
    """自描述元信息硬校验：**改 prompt 等于换模型**（ADR-0015 决定 2）。

    这四条不一致都不会崩：旧 captions 照样能读、照样能建库、照样返回 Top-K，
    只是描述口径已经漂了一半。全都必须在加载时当场失败。
    """

    def _meta(self):
        return vindex.caption_meta("春物", "S01E01", {
            "detector": "ffmpeg-scdet", "scene_threshold": 10.0,
            "min_shot": 0.5, "duration": 100.0})

    def test_正常元信息带全四个自描述维度(self):
        m = self._meta()
        assert m["model_id"] == vindex.CAPTIONS_REPO
        assert m["prompt_version"] == vindex.CAPTION_PROMPT_VERSION
        assert m["frames"] == vindex.caption_input_spec()
        assert m["max_chars"] == vindex.CAPTION_MAX_CHARS

    def test_kind_不对就失败(self, tmp_path):
        m = self._meta()
        m["kind"] = "presence"
        with pytest.raises(SystemExit, match="不是 captions"):
            vindex.check_caption_meta(m, tmp_path / "x.json")

    def test_换模型就失败(self, tmp_path):
        m = self._meta()
        m["model_id"] = "Qwen/Qwen2.5-VL-7B-Instruct"
        with pytest.raises(SystemExit, match="重建 captions"):
            vindex.check_caption_meta(m, tmp_path / "x.json")

    def test_prompt_版本不一致就失败(self, tmp_path):
        m = self._meta()
        m["prompt_version"] = "v0"
        with pytest.raises(SystemExit, match="prompt"):
            vindex.check_caption_meta(m, tmp_path / "x.json")

    def test_模型目录指纹不一致就失败(self, tmp_path, monkeypatch):
        # 云端模型是平铺目录，没有 hub 的 refs/main（revision 恒为 None），
        # 所以「换了模型没有」只能靠目录指纹回答
        model = tmp_path / "model"
        model.mkdir()
        (model / "config.json").write_text('{"a":1}', encoding="utf-8")
        (model / "model.safetensors.index.json").write_text('{"b":2}', encoding="utf-8")
        monkeypatch.setattr(vindex, "caption_model_ref", lambda: str(model))
        m = self._meta()
        m["model_fingerprint"] = "deadbeefdeadbeef"
        with pytest.raises(SystemExit, match="指纹"):
            vindex.check_caption_meta(m, tmp_path / "x.json")

    def test_版本标识同类不一致才报(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vindex.paths, "model_revision", lambda repo: "aaaa1111")
        m = self._meta()
        m["revision"] = "bbbb2222"
        with pytest.raises(SystemExit, match="模型版本"):
            vindex.check_caption_meta(m, tmp_path / "x.json")

    def test_两类标识不许交叉比较(self, tmp_path, monkeypatch):
        # 云端平铺目录只有指纹、本地 hub 缓存只有 commit sha：拿一类去比另一类
        # 会把「同一个模型」判成不一致，所以每一类各自两边都有才比
        monkeypatch.setattr(vindex.paths, "model_revision", lambda repo: None)
        monkeypatch.setattr(vindex, "caption_model_ref", lambda: str(tmp_path / "缺"))
        monkeypatch.setattr(vindex, "_check_meta_shots", lambda m, p: m)
        m = self._meta()
        m["revision"] = "aaaa1111"          # 旧文件记着 sha，本机这次认不出来
        vindex.check_caption_meta(m, tmp_path / "x.json")   # 不抛 = 没拿指纹去比 sha

    def test_取样规格变了就失败(self, tmp_path):
        m = self._meta()
        m["frames"] = {"long_shot": 6.0, "points": [0.5], "width": 448}
        with pytest.raises(SystemExit, match="取样规格"):
            vindex.check_caption_meta(m, tmp_path / "x.json")


class TestCaptionMessages:
    """对话结构：**system 的 content 必须是部件列表**。

    transformers 4.57 的 processor 版 `apply_chat_template` 会把 `message["content"]`
    当部件列表逐个取 `content["type"]`，裸字符串当场 TypeError（云端实测，
    见 pipeline/vindex.py `caption_messages` 的注释）。而这个坑**只有 processor 版有**——
    本地拿 tokenizer 试会误以为没问题，所以把结构本身钉在测试里。
    """

    def test_system_与_user_都是部件列表(self):
        msgs = vindex.caption_messages([Path("a.jpg")])
        assert [m["role"] for m in msgs] == ["system", "user"]
        for m in msgs:
            assert isinstance(m["content"], list)
            assert all(isinstance(c, dict) and "type" in c for c in m["content"])

    def test_多帧按时间顺序进同一个_user_轮(self):
        msgs = vindex.caption_messages([Path("a.jpg"), Path("b.jpg"), Path("c.jpg")])
        parts = msgs[1]["content"]
        assert [p["image"] for p in parts[:-1]] == ["a.jpg", "b.jpg", "c.jpg"]
        assert "3 个代表帧" in parts[-1]["text"]


class TestPoolScope:
    """池级加载：**`anime` 的真实语义是素材池**（2026-09-11 裁决）。

    一部番与一池 Live/MV 素材走同一个键系（`S01E03` / `SP05`）。池的特点是没有
    笔记也没有字幕——视觉索引是它唯一的排片依据，所以这条路径必须能走通：
    看不懂 SP 键的后果是整池索引加载不了，而 `embed` 要跑完才会发现。
    """

    def _env(self, tmp_path, monkeypatch, indexed=("SP01", "SP02")):
        shots_dir = tmp_path / "shots"
        shots_dir.mkdir()
        for k in ("SP01", "SP02", "SP03"):        # 池声明了 3 集
            (shots_dir / f"EGOIST_{k}.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(vindex.shots, "SHOTS_DIR", shots_dir)
        monkeypatch.setattr(vindex.shots, "load",
                            lambda a, k, **kw: {"shots": [{"i": 0, "start": 0.0, "end": 2.0}]})
        monkeypatch.setattr(vindex, "_check_meta", lambda m, p, **kw: m)
        for k in indexed:
            import numpy as np
            np.save(tmp_path / f"EGOIST_{k}.scene.npy",
                    np.ones((1, vindex.EMBED_DIM), dtype="float32"))
            (tmp_path / f"EGOIST_{k}.scene.json").write_text(json.dumps({
                "meta": {"kind": "scene", "producer": "captions", "anime": "EGOIST",
                         "episode": k, "dim": vindex.EMBED_DIM},
                "shots": [{"i": 0, "start": 0.0, "end": 2.0, "label": "舞台"}],
            }, ensure_ascii=False), encoding="utf-8")
            # 集键清单读的是 captions（抽样与状态都以打标产物为准，不是向量产物）
            (tmp_path / f"EGOIST_{k}.captions.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def test_池索引只打了一部分时检索默认拒绝加载(self, tmp_path, monkeypatch):
        # 拿半份索引去检索排片＝静默的池缩水：Top-K 照常返回、分数照常过阈值
        d = self._env(tmp_path, monkeypatch)
        with pytest.raises(SystemExit, match="2/3"):
            vindex.load_scene("EGOIST", d)

    def test_探针可以显式放宽(self, tmp_path, monkeypatch):
        # 量噪声地板时「索引盖了多少就量多少」可接受；这个口子只给探针
        d = self._env(tmp_path, monkeypatch)
        vecs, units = vindex.load_scene("EGOIST", d, require_full=False)
        assert vecs.shape == (2, vindex.EMBED_DIM)
        assert [vindex.key_of(u) for u in units] == ["SP01", "SP02"]
        assert all(u.season is None for u in units)

    def test_池里的集键可以逐个列出来(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch, indexed=("SP02", "SP01"))
        assert vindex.caption_keys("EGOIST", d) == ["SP01", "SP02"]   # 抽样要稳定顺序


class TestModelFingerprint:
    """模型指纹：**覆盖权重清单，不哈希 17G 权重**（每次加载都要跑这一步）。"""

    def test_覆盖权重清单(self, tmp_path):
        d = tmp_path / "m"
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
        fp0 = vindex.model_fingerprint(d)
        (d / "model.safetensors.index.json").write_text('{"total_size":1}', encoding="utf-8")
        assert fp0 != vindex.model_fingerprint(d)
        assert vindex.model_fingerprint(d) == vindex.model_fingerprint(d)   # 确定性

    def test_不是目录或没有清单就返回_None(self, tmp_path):
        assert vindex.model_fingerprint(tmp_path / "缺") is None
        d = tmp_path / "empty"
        d.mkdir()
        assert vindex.model_fingerprint(d) is None


class TestCaptionAlignment:
    """captions 与镜头表**逐行对齐**。这是本模块最毒的一类静默失败：
    句子通顺、检索正常、分数正常，只是写的不是那个镜头。"""

    def test_没有文件就报先跑哪条命令(self, tmp_path):
        with pytest.raises(SystemExit, match="vindex captions"):
            vindex.load_captions("春物", "S01E01", tmp_path)

    def test_行与镜头错位就失败(self, tmp_path, monkeypatch):
        p = tmp_path / "春物_S01E01.captions.json"
        p.write_text(json.dumps({"meta": {"kind": "captions"},
                                 "captions": [{"shot": 1, "status": "ok"}]},
                                ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(vindex, "check_caption_meta", lambda m, path: m)
        monkeypatch.setattr(vindex.shots, "load",
                            lambda a, k, **kw: {"shots": [{"i": 0}, {"i": 1}]})
        with pytest.raises(SystemExit, match="对不上"):
            vindex.load_captions("春物", "S01E01", tmp_path)


class TestCaptionCounts:
    """第七条数字的口径：**数完整覆盖的集，不数文件。**

    跑到一半崩溃的 captions 文件同样会被 glob 数到，而它代表的是一集没打完。
    文件数口径下这种半份状态看起来和做完了完全一样。
    """

    def _env(self, monkeypatch, tmp_path):
        shots_dir = tmp_path / "shots"
        shots_dir.mkdir()
        for key, n in (("S01E01", 3), ("S01E02", 2)):
            (shots_dir / f"春物_{key}.json").write_text(
                json.dumps({"shots": [{"i": i} for i in range(n)]}), encoding="utf-8")
        monkeypatch.setattr(vindex.shots, "SHOTS_DIR", shots_dir)
        return tmp_path / "vindex"

    def _write(self, d, key, statuses):
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"shot": i, "start": float(i), "end": float(i + 1),
                 "caption": "场景" if s == "ok" else "", "status": s}
                for i, s in enumerate(statuses)]
        (d / f"春物_{key}.captions.json").write_text(
            json.dumps({"meta": {}, "captions": rows}, ensure_ascii=False),
            encoding="utf-8")

    def test_跑到一半的集不计入(self, monkeypatch, tmp_path):
        d = self._env(monkeypatch, tmp_path)
        self._write(d, "S01E01", ["ok", "ok", "ok"])
        self._write(d, "S01E02", ["ok", "pending"])
        c = vindex.caption_counts("春物", d)
        assert c["files"] == 2 and c["episodes"] == 1
        assert c["shots"] == 3 and c["pending"] == 1

    def test_行数与镜头表对不上也不计入(self, monkeypatch, tmp_path):
        d = self._env(monkeypatch, tmp_path)
        self._write(d, "S01E01", ["ok", "ok"])          # 少一行
        assert vindex.caption_counts("春物", d)["episodes"] == 0

    def test_failed_计入覆盖但单独计数(self, monkeypatch, tmp_path):
        # S4：跳过不定罪，不是不算账（ADR-0015 决定 2）
        d = self._env(monkeypatch, tmp_path)
        self._write(d, "S01E01", ["ok", "failed", "ok"])
        c = vindex.caption_counts("春物", d)
        assert c["episodes"] == 1 and c["failed"] == 1 and c["shots"] == 3


class TestFullIndex:
    """检索池缩水的当场失败：不给 episode 就要求每一集都在。

    判据不是「找得到东西吗」（那个永远为真），而是「集数对得上吗」。
    """

    def _env(self, monkeypatch, tmp_path):
        shots_dir = tmp_path / "shots"
        shots_dir.mkdir()
        for key in ("S01E01", "S01E02"):
            (shots_dir / f"春物_{key}.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(vindex.shots, "SHOTS_DIR", shots_dir)

    def test_缺集当场报(self, monkeypatch, tmp_path):
        self._env(monkeypatch, tmp_path)
        with pytest.raises(SystemExit, match="1/2"):
            vindex._require_full_index("春物", {"S01E01"}, "画面语义索引")

    def test_多出的集也报(self, monkeypatch, tmp_path):
        # 镜头表里没有的集 = 残留的旧产物，同样说明索引与素材已经不同步
        self._env(monkeypatch, tmp_path)
        with pytest.raises(SystemExit, match="多"):
            vindex._require_full_index("春物", {"S01E01", "S01E02", "S01E09"},
                                       "画面语义索引")

    def test_对齐就通过(self, monkeypatch, tmp_path):
        self._env(monkeypatch, tmp_path)
        vindex._require_full_index("春物", {"S01E01", "S01E02"}, "画面语义索引")


class TestCaptionCost:
    def test_成本估值随镜头数线性(self):
        # 估值只用来卡预算闸；这里锁的是「它没被写成常数」
        assert vindex.estimate_caption_cost(3600, 2.4) == pytest.approx(1.5 * 2.4)

    def test_预算闸读得到真实配置(self):
        rate, budget = vindex.cloud_budget()
        assert rate > 0 and budget > 0


class TestBuildCaptions:
    """打标主链路：取帧 → 校验 → 降温重试 → 落盘 → 续跑。

    **只把 8B 模型替身掉**（`_generate`），其余全真跑：计划取帧、输出校验、
    重试、failed 记账、逐行对齐、断点续跑。云端唯一测不到的是模型本身，
    这一组管的是模型前后那两段代码——而它们才是「不报错但错了」的地方。
    """

    KEY = "S01E01"

    def _env(self, tmp_path, monkeypatch):
        frames = tmp_path / "frames"
        (frames / "春物_S01E01_cap").mkdir(parents=True)
        table = {"meta": {"detector": "ffmpeg-scdet", "scene_threshold": 10.0,
                          "min_shot": 0.5, "duration": 30.0},
                 "shots": [{"i": 0, "start": 0.0, "end": 3.0, "rep": 1.5},
                           {"i": 1, "start": 3.0, "end": 23.0, "rep": 13.0}]}
        monkeypatch.setattr(vindex.shots, "SHOTS_DIR", tmp_path / "shots")
        monkeypatch.setattr(vindex.shots, "FRAMES_DIR", frames)
        monkeypatch.setattr(vindex.shots, "load", lambda a, k, **kw: table)
        for i, k in ((0, 0), (1, 0), (1, 1), (1, 2)):
            vindex.shots.caption_frame_path("春物", self.KEY, i, k, frames).write_bytes(b"x")
        return tmp_path / "vindex"

    def _read(self, d):
        return json.loads((d / f"春物_{self.KEY}.captions.json").read_text(encoding="utf-8"))

    def test_短镜头一帧长镜头三帧且逐行对齐(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        seen = []

        def fake(req, temp, seed):
            seen.append((len(req), temp, [len(r) for r in req]))
            return ["夜晚教室，少女伏案独坐"] * len(req)

        monkeypatch.setattr(vindex, "_generate", fake)
        r = vindex.build_captions("春物", self.KEY, out_dir=d)
        assert r == {"shots": 2, "ok": 2, "failed": 0, "skipped": 0}
        assert seen == [(2, 0.2, [1, 3])]          # 一次前向：短镜头 1 帧、长镜头 3 帧
        rows = self._read(d)["captions"]
        assert [x["shot"] for x in rows] == [0, 1]
        assert [x["frames"] for x in rows] == [1, 3]
        assert all(x["status"] == "ok" and x["caption"] for x in rows)

    def test_不合格降温重试一次只重跑那一条(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        calls = []

        def fake(req, temp, seed):
            calls.append((temp, len(req)))
            # 首轮两条都不合格（超长 + 禁词），重试时才给合格输出
            return (["字" * 40, "画面精美"] if temp > 0
                    else ["夜晚教室，少女伏案独坐"] * len(req))

        monkeypatch.setattr(vindex, "_generate", fake)
        r = vindex.build_captions("春物", self.KEY, out_dir=d)
        assert r["ok"] == 2 and r["failed"] == 0
        assert calls == [(0.2, 2), (0.0, 1), (0.0, 1)]   # 重试逐条跑，不整批重来
        rows = self._read(d)["captions"]
        assert [x["tries"] for x in rows] == [2, 2]
        assert all("reason" not in x for x in rows)      # 合格之后不许留失败痕迹

    def test_两次都不合格记_failed_并写明原因(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        monkeypatch.setattr(vindex, "_generate",
                            lambda req, temp, seed: ["似乎要离开"] * len(req))
        r = vindex.build_captions("春物", self.KEY, out_dir=d)
        assert r["failed"] == 2 and r["ok"] == 0
        rows = self._read(d)["captions"]
        # S4：跳过不定罪，但描述留空 + 原因写明，对账第七条上看得见
        assert all(x["status"] == "failed" and x["caption"] == "" for x in rows)
        assert all("禁词" in x["reason"] for x in rows)

    def test_续跑只补没打完的(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        monkeypatch.setattr(vindex, "_generate",
                            lambda req, temp, seed: ["夜晚教室，少女伏案独坐"] * len(req))
        vindex.build_captions("春物", self.KEY, out_dir=d)
        # 第二次跑：一个镜头都不该再喂模型（钱已经花过了）
        monkeypatch.setattr(vindex, "_generate",
                            lambda *a, **k: pytest.fail("续跑又把已合格的镜头重打了一遍"))
        r = vindex.build_captions("春物", self.KEY, out_dir=d)
        assert r == {"shots": 2, "ok": 2, "failed": 0, "skipped": 2}

    def test_冒烟_limit_只打前几个其余留_pending(self, tmp_path, monkeypatch):
        # 云端第一次跑必须先用 --limit 验链路（批处理/多图 padding 写错了不会崩，
        # 只会让描述写的不是那个镜头），验收完再全量
        d = self._env(tmp_path, monkeypatch)
        monkeypatch.setattr(vindex, "_generate",
                            lambda req, temp, seed: ["夜晚教室，少女伏案独坐"] * len(req))
        r = vindex.build_captions("春物", self.KEY, out_dir=d, limit=1)
        assert r["ok"] == 1 and r["failed"] == 0
        rows = self._read(d)["captions"]
        assert [x["status"] for x in rows] == ["ok", "pending"]

    def test_缺帧当场失败且说清先跑哪条命令(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        vindex.shots.caption_frame_path("春物", self.KEY, 1, 2,
                                        vindex.shots.FRAMES_DIR).unlink()
        with pytest.raises(SystemExit, match="caption-frames"):
            vindex.build_captions("春物", self.KEY, out_dir=d)
        # 失败发生在花钱之前：模型一次都不该被调用
        assert not (d / f"春物_{self.KEY}.captions.json").exists()

    def test_改_prompt_后旧文件拒绝续跑(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        monkeypatch.setattr(vindex, "_generate",
                            lambda req, temp, seed: ["夜晚教室，少女伏案独坐"] * len(req))
        vindex.build_captions("春物", self.KEY, out_dir=d)
        p = d / f"春物_{self.KEY}.captions.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["meta"]["prompt_version"] = "v0"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(SystemExit, match="prompt"):
            vindex.build_captions("春物", self.KEY, out_dir=d)


class TestBuildEmbed:
    """captions → 向量库：**行数必须与镜头数相等**，打标失败的镜头留零向量占位。

    跳过 failed 会让向量行与镜头行错位，而错位之后每条检索结果都还是
    「看起来正常」的——那正是本模块反复要堵的那类失败。
    """

    def _env(self, tmp_path, monkeypatch):
        frames = tmp_path / "frames"
        (frames / "春物_S01E01_cap").mkdir(parents=True)
        table = {"meta": {"detector": "ffmpeg-scdet", "scene_threshold": 10.0,
                          "min_shot": 0.5, "duration": 30.0},
                 "shots": [{"i": 0, "start": 0.0, "end": 3.0, "rep": 1.5},
                           {"i": 1, "start": 3.0, "end": 23.0, "rep": 13.0}]}
        monkeypatch.setattr(vindex.shots, "FRAMES_DIR", frames)
        monkeypatch.setattr(vindex.shots, "load", lambda a, k, **kw: table)
        for i, k in ((0, 0), (1, 0), (1, 1), (1, 2)):
            vindex.shots.caption_frame_path("春物", "S01E01", i, k, frames).write_bytes(b"x")
        monkeypatch.setattr(vindex, "_generate",
                            lambda req, temp, seed: ["夜晚教室，少女伏案独坐"] * len(req))
        d = tmp_path / "vindex"
        vindex.build_captions("春物", "S01E01", out_dir=d)
        return d

    def test_行数与镜头数相等且失败镜头留零向量(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        p = d / "春物_S01E01.captions.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["captions"][1].update({"status": "failed", "caption": "", "reason": "禁词"})
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

        class FakeEmb:
            def encode(self, texts, batch_size=64, normalize_embeddings=True,
                       show_progress_bar=False):
                import numpy as np
                return np.ones((len(texts), vindex.EMBED_DIM), dtype="float32")

        monkeypatch.setattr(vindex, "embedder", lambda: FakeEmb())
        r = vindex.build_embed("春物", out_dir=d)
        import numpy as np
        vecs = np.load(d / "春物_S01E01.scene.npy")
        assert r == {"episodes": 1, "shots": 2, "failed": 1}
        assert vecs.shape == (2, vindex.EMBED_DIM)      # 失败的那行不删，留零向量
        assert not vecs[1].any() and vecs[0].any()
        meta = json.loads((d / "春物_S01E01.scene.json").read_text(encoding="utf-8"))
        assert meta["meta"]["producer"] == "captions"
        assert meta["meta"]["model_id"] == vindex.EMBED_REPO
        assert [s["caption_status"] for s in meta["shots"]] == ["ok", "failed"]

    def test_还有_pending_就拒绝建库(self, tmp_path, monkeypatch):
        d = self._env(tmp_path, monkeypatch)
        p = d / "春物_S01E01.captions.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["captions"][1]["status"] = "pending"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(vindex, "embedder",
                            lambda: pytest.fail("半份 captions 不该走到建库"))
        with pytest.raises(SystemExit, match="pending"):
            vindex.build_embed("春物", out_dir=d)


class TestHitLine:
    """`vindex search` 的一行输出：池素材的集键必须能打出来。

    这一条是实跑撞出来的：池的 `season` 是 None，按 `S%02dE%02d` 格式化直接
    TypeError——而它是人手动检索时唯一的输出路径（排片前最常用的一条）。
    """

    def test_池命中不崩且带_SP_键(self):
        u = vindex.Shot("EGOIST", None, 5, 83.4, 86.1, "镜头 01:23.40 舞台紫光闪烁")
        line = vindex.hit_line(0.7966, u)
        assert line.startswith("0.797  SP05 01:23.40-01:26.10")
        assert "舞台紫光闪烁" in line

    def test_番剧命中仍是_SxxEyy(self):
        u = vindex.Shot("春物", 1, 3, 12.0, 14.0, "镜头 00:12.00")
        assert vindex.hit_line(0.61, u).startswith("0.610  S01E03 00:12.00-00:14.00")
