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
