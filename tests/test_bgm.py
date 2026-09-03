"""cue 分轨：时间码换算、instrumental 识别、轨边界、编码。

这个模块存在的理由本身就是个坑：原声碟是拆好轨的，OP/ED 单曲碟却是**整轨镜像 + cue**，
一张碟只有一个 flac。按 flac 文件数扫曲库，六张单曲碟只显示为六个文件，
整档 OP/ED 被漏掉——而每张碟都带 instrumental 轨。
"""

import pytest

from pipeline import bgm


CUE = """REM DATE 2015
PERFORMER "やなぎなぎ"
TITLE "春擬き"
FILE "disc.flac" WAVE
  TRACK 01 AUDIO
    TITLE "春擬き"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "鱗翅目標本"
    INDEX 01 04:30:39
  TRACK 03 AUDIO
    TITLE "春擬き -instrumental-"
    INDEX 00 08:16:03
    INDEX 01 08:20:03
  TRACK 04 AUDIO
    TITLE "鱗翅目標本 -instrumental-"
    INDEX 01 12:49:54
"""


@pytest.fixture
def cue(tmp_path):
    (tmp_path / "disc.flac").write_bytes(b"not really flac")
    p = tmp_path / "disc.cue"
    p.write_text(CUE, encoding="utf-8")
    return p


class TestParseCue:
    def test_轨数与曲名(self, cue):
        _, tracks = bgm.parse_cue(cue)
        assert [t.no for t in tracks] == [1, 2, 3, 4]
        assert tracks[2].title == "春擬き -instrumental-"

    def test_时间码按_CD_帧换算(self, cue):
        # MM:SS:FF，FF 是 CD 帧，一秒 75 帧（红皮书标准，不是视频帧率）。
        # 04:30:39 = 4*60 + 30 + 39/75 = 270.52
        _, tracks = bgm.parse_cue(cue)
        assert tracks[1].start == pytest.approx(270.52)

    def test_轨的终点取下一轨的前导起点(self, cue):
        # 用 INDEX 00 而不是 INDEX 01，是为了把下一首的引子切干净——
        # 否则结尾会带进半秒别的曲子。
        # 轨 2 终点 = 轨 3 的 INDEX 00 = 08:16:03 = 496.04
        _, tracks = bgm.parse_cue(cue)
        assert tracks[1].end == pytest.approx(496.04)

    def test_没有前导时退到下一轨起点(self, cue):
        # 轨 3 的下一轨没有 INDEX 00，终点取 INDEX 01 = 12:49:54 = 769.72
        _, tracks = bgm.parse_cue(cue)
        assert tracks[2].end == pytest.approx(769.72)

    def test_末轨到碟尾(self, cue):
        _, tracks = bgm.parse_cue(cue)
        assert tracks[-1].end is None
        assert tracks[-1].duration is None

    def test_碟标题不会被当成轨名(self, cue):
        # TITLE "春擬き" 在 TRACK 之前出现过一次，那条是碟标题不是轨名
        _, tracks = bgm.parse_cue(cue)
        assert tracks[0].title == "春擬き"      # 轨 1 恰好同名，但来自 TRACK 之后那行
        assert len(tracks) == 4                 # 没有因为碟标题多出一轨

    def test_音频文件不存在就报错(self, tmp_path):
        p = tmp_path / "x.cue"
        p.write_text(CUE, encoding="utf-8")     # 没建 disc.flac
        with pytest.raises(SystemExit):
            bgm.parse_cue(p)

    def test_shift_jis_编码也能读(self, tmp_path):
        (tmp_path / "disc.flac").write_bytes(b"x")
        p = tmp_path / "sj.cue"
        p.write_bytes(CUE.encode("cp932"))
        _, tracks = bgm.parse_cue(p)
        assert tracks[2].title == "春擬き -instrumental-"


class TestInstrumental:
    def test_六种写法都认(self):
        # 六张碟六种写法，没有统一标准。所以按关键词认，不按标点认。
        for title in ("春擬き -instrumental-", "エブリデイワールド （Instrumental）",
                      "ユキトキ <Instrumental>", "芽ぐみの雨 [Instrumental]",
                      "Hello Alone (Instrumental)", "某曲 カラオケ"):
            assert bgm.Track(1, title, 0.0).instrumental, title

    def test_人声版不算(self):
        for title in ("春擬き", "エブリデイワールド", "ユキトキ"):
            assert not bgm.Track(1, title, 0.0).instrumental


class TestEpisodeChoice:
    def test_读到字段(self, tmp_path):
        (tmp_path / "01-topic.md").write_text(
            "番: 东京喰种\nBGM正文: Grau\nBGM结尾: Schöpfer\n", encoding="utf-8")
        assert bgm.episode_choice(tmp_path, "正文") == "Grau"
        assert bgm.episode_choice(tmp_path, "结尾") == "Schöpfer"

    def test_字段没填返回None(self, tmp_path):
        (tmp_path / "01-topic.md").write_text("番: 东京喰种\n", encoding="utf-8")
        assert bgm.episode_choice(tmp_path, "正文") is None

    def test_文件不存在返回None(self, tmp_path):
        assert bgm.episode_choice(tmp_path, "正文") is None

    def test_允许后补不要求首次写入就有(self, tmp_path):
        # 01-topic.md 选题阶段就建了，BGM 要等听完配音才填——
        # 这里模拟"先建文件不带BGM字段，后来才追加"这个真实时序。
        p = tmp_path / "01-topic.md"
        p.write_text("番: 东京喰种\n", encoding="utf-8")
        assert bgm.episode_choice(tmp_path, "正文") is None
        p.write_text(p.read_text(encoding="utf-8") + "BGM正文: Grau\n", encoding="utf-8")
        assert bgm.episode_choice(tmp_path, "正文") == "Grau"


class TestResolveOverride:
    """override 优先于 use；没给 override 才退回 use（2026-08-08 每期现选改动）。"""

    FAKE_TABLE = {
        "use": {"正文": "曲A"},
        "tracks": {
            "曲A": {"path": "data/library/bgm/x/曲A.flac", "lufs": -15.0},
            "曲B": {"path": "data/library/bgm/x/曲B.flac", "lufs": -12.0},
        },
    }

    def test_没给override退回use(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bgm, "load", lambda anime: self.FAKE_TABLE)
        monkeypatch.setattr(bgm.paths, "ROOT", tmp_path)
        (tmp_path / "data/library/bgm/x").mkdir(parents=True)
        (tmp_path / "data/library/bgm/x/曲A.flac").write_bytes(b"x")
        got = bgm.resolve("x", "正文")
        assert got["name"] == "曲A"

    def test_给了override就不看use(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bgm, "load", lambda anime: self.FAKE_TABLE)
        monkeypatch.setattr(bgm.paths, "ROOT", tmp_path)
        (tmp_path / "data/library/bgm/x").mkdir(parents=True)
        (tmp_path / "data/library/bgm/x/曲B.flac").write_bytes(b"x")
        got = bgm.resolve("x", "正文", override="曲B")
        assert got["name"] == "曲B"

    def test_override指到曲库里没有的名字要报错(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bgm, "load", lambda anime: self.FAKE_TABLE)
        with pytest.raises(SystemExit, match="01-topic.md 的 BGM正文 字段"):
            bgm.resolve("x", "正文", override="不存在的曲")

    def test_use指到曲库里没有的名字报错信息不同(self, monkeypatch, tmp_path):
        bad_table = {"use": {"正文": "不存在的曲"}, "tracks": {}}
        monkeypatch.setattr(bgm, "load", lambda anime: bad_table)
        with pytest.raises(SystemExit, match="config/bgm.json 的 x.use.正文"):
            bgm.resolve("x", "正文")

    def test_两者都没给返回None(self, monkeypatch):
        monkeypatch.setattr(bgm, "load", lambda anime: {"use": {}, "tracks": {}})
        assert bgm.resolve("x", "正文") is None


class TestSafeName:
    def test_保留日文假名汉字(self):
        # 不做罗马字转写——文件名要能跟曲目表和笔记里的名字对上，
        # 转写之后人对不上号，改配置就得来回猜。
        assert bgm._safe("春擬き -instrumental-") == "春擬き -instrumental-"

    def test_清掉路径分隔符(self):
        assert "/" not in bgm._safe("a/b")
        assert ":" not in bgm._safe("a:b")

    def test_压缩连续空白(self):
        assert bgm._safe("  a   b  ") == "a b"


class TestCleanTrackTitle:
    def test_常见前缀清洗(self):
        assert bgm.clean_track_title("01. 回想") == "回想"
        assert bgm.clean_track_title("04.恋人") == "恋人"
        assert bgm.clean_track_title("05 - 战斗") == "战斗"
        assert bgm.clean_track_title("12、日常") == "日常"
        assert bgm.clean_track_title("03_日常風景") == "日常風景"

    def test_无序号前缀保留原样(self):
        assert bgm.clean_track_title("K&A 初訪問") == "K&A 初訪問"
        assert bgm.clean_track_title("春擬き -instrumental-") == "春擬き -instrumental-"


class TestInferSlot:
    def test_含关键词识别为结尾(self):
        assert bgm.infer_slot("Heart Pattern -instrumental-") == "结尾"
        assert bgm.infer_slot("芽ぐみの雨 [Instrumental]") == "结尾"
        assert bgm.infer_slot("某曲 (Off Vocal)") == "结尾"
        assert bgm.infer_slot("某曲 カラオケ") == "结尾"
        assert bgm.infer_slot("某曲 inst.") == "结尾"

    def test_普通曲名识别为正文(self):
        assert bgm.infer_slot("04. 回想") == "正文"
        assert bgm.infer_slot("繋ぎとめた世界") == "正文"


class TestRegister:
    def test_批量登记与增量合并(self, monkeypatch, tmp_path):
        import json
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        bgm_json = config_dir / "bgm.json"
        bgm_json.write_text(json.dumps({
            "测试番": {
                "_note": "原有说明",
                "tracks": {
                    "旧曲": {"path": "data/library/bgm/测试番/01. 旧曲.flac", "dur": 100.0, "lufs": -14.0}
                }
            }
        }, ensure_ascii=False), encoding="utf-8")

        monkeypatch.setattr(bgm.paths, "CONFIG", config_dir)
        monkeypatch.setattr(bgm.paths, "ROOT", tmp_path)

        # 创建两首假音频文件
        audio_dir = tmp_path / "data/library/bgm/测试番"
        audio_dir.mkdir(parents=True)
        f1 = audio_dir / "02. 新曲A.flac"
        f2 = audio_dir / "03. 主题曲 -instrumental-.flac"
        f1.write_bytes(b"dummy")
        f2.write_bytes(b"dummy")

        # mock measure
        monkeypatch.setattr(bgm, "measure", lambda p: {
            "duration": 120.5,
            "lufs": -15.345,
            "onset": 1.234,
            "vocal": None if "instrumental" in p.stem.lower() else "?"
        })

        # 1. 增量登记
        count = bgm.register([f1, f2], anime="测试番")
        assert count == 2

        db = json.loads(bgm_json.read_text(encoding="utf-8"))
        tracks = db["测试番"]["tracks"]
        assert "旧曲" in tracks  # 原有曲目得到保留
        assert "新曲A" in tracks
        assert "主题曲 -instrumental-" in tracks
        assert tracks["新曲A"]["slot"] == "正文"
        assert tracks["新曲A"]["dur"] == 120.5
        assert tracks["新曲A"]["lufs"] == -15.35
        assert tracks["主题曲 -instrumental-"]["slot"] == "结尾"

        # 2. 覆盖登记
        count = bgm.register([f1], anime="测试番", note="全新备注", overwrite=True)
        assert count == 1
        db = json.loads(bgm_json.read_text(encoding="utf-8"))
        assert db["测试番"]["_note"] == "全新备注"
        assert "旧曲" not in db["测试番"]["tracks"]
        assert len(db["测试番"]["tracks"]) == 1

