"""试听型音乐时间轴：解析与推导是纯函数，任何带 `音乐段` 标记的期都走这里。

可复用性约束（CLAUDE.md 总纲）：
- 只依赖稿子的标准格式（`## 音乐段 Mx` / `音乐:` / `过渡:` 行）与
  config/bgm.json 的曲库结构，不绑任何一部番的曲目名。
- 测试用本期的真实稿子，但断言只谈结构（事件序列），不写死曲目时长。
"""

import json
from pathlib import Path

import pytest

from pipeline import music as m


SCRIPT = """# 测试稿

## 音乐段 M1 · 开头

音乐: `测试曲甲` 完整版 00:00-00:15
状态: 前景试听，旁白停止
过渡: 试听结束后自然降为 BGM，进入段落 1

## 段落 1

配音：第一段。

## 音乐段 M2 · 经典段落

音乐: `测试曲甲` 完整版 04:23-04:46
状态: 前景试听，旁白停止
过渡: 结尾自然淡出

## 段落 2

配音：第二段。

## 音乐段 M3 · 尾段

音乐: `测试曲乙` 完整版 00:00-01:00
状态: 前景试听，旁白停止
过渡: 结束后自然进入下一首，不加解释性旁白

## 段落 3

配音：第三段。

音乐: `测试曲乙` 继续播放至完整版结束
状态: 旁白结束后不再说话，不淡出，让歌曲自然结束
"""


MANIFEST = {"segments": [
    {"index": 1, "duration": 10.0},
    {"index": 2, "duration": 10.0},
    {"index": 3, "duration": 10.0},
]}


def _bgm() -> dict:
    return {"tracks": {
        "测试曲甲": {"path": "x.flac", "dur": 300.0, "lufs": -10.0},
        "测试曲乙": {"path": "y.flac", "dur": 200.0, "lufs": -8.0},
    }}


@pytest.fixture(autouse=True)
def _real_track_files(tmp_path: Path, monkeypatch):
    """`_track_for` 的配置前置校验（2026-08-18 复盘②）要求曲目文件真实存在。

    测试不碰 ffmpeg/真音频，空文件只是「存在」这个事实的载体；
    paths.ROOT 指到 tmp，路径解析与线上一致。
    """
    for name in ("x.flac", "y.flac", "s.flac"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(m.paths, "ROOT", tmp_path)


@pytest.fixture
def episode(tmp_path: Path) -> Path:
    (tmp_path / "02-script.md").write_text(SCRIPT, encoding="utf-8")
    return tmp_path


class TestParseScriptMusic:
    def test_块与曲目和时间(self, episode):
        blocks = m.parse_script_music(episode / "02-script.md")
        assert [b.label for b in blocks] == ["M1", "M2", "M3"]
        assert blocks[0].title == "测试曲甲"
        assert (blocks[0].t0, blocks[0].t1) == (0.0, 15.0)
        assert (blocks[1].t0, blocks[1].t1) == (263.0, 286.0)

    def test_固定起止时间(self, episode):
        # M3 现在是 00:00-01:00；t1=None 的场景由「结束」写法覆盖
        blocks = m.parse_script_music(episode / "02-script.md")
        assert blocks[2].t1 == 60.0

    def test_到结束写法_t1为None(self, tmp_path):
        (tmp_path / "02-script.md").write_text(
            "## 音乐段 M1\n\n音乐: `测试曲乙` 完整版 02:10-结束\n"
            "状态: 前景试听，旁白停止\n过渡: 结束后自然进入下一首\n",
            encoding="utf-8")
        blocks = m.parse_script_music(tmp_path / "02-script.md")
        assert blocks[0].t0 == 130.0
        assert blocks[0].t1 is None

    def test_过渡判定(self, episode):
        blocks = m.parse_script_music(episode / "02-script.md")
        assert [b.after for b in blocks] == ["bgm", "fade", "fade"]


class TestBuildTimeline:
    def test_时间轴累计(self, episode):
        plan = m.build_timeline(episode, MANIFEST, _bgm())
        # M1 15s + 段1 10s + M2 23s + 段2 10s + M3 60s + 段3 10s = 128s，
        # 段 3 后曲乙 natural 从曲目 60s 播到 200s（140s）→ 成片 268s
        assert plan["total_duration"] == pytest.approx(268.0)
        starts = {s["index"]: s["start"] for s in plan["segments"]}
        assert starts[1] == pytest.approx(15.0)
        assert starts[2] == pytest.approx(15 + 10 + 23)
        assert starts[3] == pytest.approx(15 + 10 + 23 + 10 + 60)

    def test_曲目内事件序列(self, episode):
        plan = m.build_timeline(episode, MANIFEST, _bgm())
        by_name = {t["name"]: t for t in plan["tracks"]}
        # 曲甲：前景 0-15（M1）→ BGM 15-（段1 长度）→ 跳 263 前景（M2）
        evs = by_name["测试曲甲"]["events"]
        assert [(e["vol"], e["t0"], e["t1"]) for e in evs] == [
            ("foreground", 0.0, 15.0),
            ("bgm", 15.0, 15.0 + 10.0),          # 段 1 覆盖 10s
            ("foreground", 263.0, 286.0),
        ]

    def test_bgm覆盖到下一个音乐段(self, episode):
        plan = m.build_timeline(episode, MANIFEST, _bgm())
        evs = plan["tracks"][0]["events"]
        bgm = [e for e in evs if e["vol"] == "bgm"][0]
        # BGM 从 15s 到 M2 开始（段 1 结束 = 25s）
        assert bgm["at"] == pytest.approx(15.0)
        assert bgm["t1"] == pytest.approx(25.0)

    def test_段落9自然收尾(self, episode):
        plan = m.build_timeline(episode, MANIFEST, _bgm())
        by_name = {t["name"]: t for t in plan["tracks"]}
        nat = [e for e in by_name["测试曲乙"]["events"]
               if e["vol"] == "natural"]
        assert nat, "段落块内的「继续播放至完整版结束」要产生 natural 事件"
        # M3 前景 0-60，段 3 结束后（128s 处）从曲目 60s 接续播到曲目结束
        assert nat[0]["t0"] == pytest.approx(60.0)
        assert nat[0]["t1"] == pytest.approx(200.0)
        assert nat[0]["at"] == pytest.approx(128.0)

    def test_无音乐段的稿子_返回空blocks(self, tmp_path):
        (tmp_path / "02-script.md").write_text(
            "## 段落 1\n\n配音：第一段。\n\n## 段落 2\n\n配音：第二段。\n",
            encoding="utf-8")
        plan = m.build_timeline(tmp_path, MANIFEST, _bgm())
        assert plan["blocks"] == []
        assert plan["tracks"] == []
        assert plan["total_duration"] == pytest.approx(20.0)


class TestBgmContinuationBounds:
    """BGM 延续事件的曲目内终点不许超曲目全长（2026-08-16 审计 2-13）。

    渲染端 `-ss t0 -t dur` 对超界静默截短，中段音乐空缺；render 的时长校验
    只查音乐床总长（amix longest 不变），测不出中段空洞。
    """

    def test_延续事件超全长当场失败(self, tmp_path: Path):
        # 前景 90-95s（5s），after=bgm 要再铺 30s（段落 1）→ 曲目内需播到
        # 125s，超过全长 100s
        (tmp_path / "02-script.md").write_text(
            "## 音乐段 M1\n\n音乐: `短曲` 完整版 01:30-01:35\n"
            "状态: 前景试听，旁白停止\n过渡: 试听结束后降为 BGM\n\n"
            "## 段落 1\n\n配音：第一段。\n", encoding="utf-8")
        bgm = {"tracks": {"短曲": {"path": "s.flac", "dur": 100.0, "lufs": -9.0}}}
        manifest = {"segments": [{"index": 1, "duration": 30.0}]}
        with pytest.raises(SystemExit, match="曲目全长"):
            m.build_timeline(tmp_path, manifest, bgm)

    def test_延续事件在全长之内照常构建(self, tmp_path: Path):
        (tmp_path / "02-script.md").write_text(
            "## 音乐段 M1\n\n音乐: `短曲` 完整版 01:30-01:35\n"
            "状态: 前景试听，旁白停止\n过渡: 试听结束后降为 BGM\n\n"
            "## 段落 1\n\n配音：第一段。\n", encoding="utf-8")
        bgm = {"tracks": {"短曲": {"path": "s.flac", "dur": 200.0, "lufs": -9.0}}}
        manifest = {"segments": [{"index": 1, "duration": 30.0}]}
        plan = m.build_timeline(tmp_path, manifest, bgm)
        evs = plan["tracks"][0]["events"]
        assert [(e["vol"], e["t0"], e["t1"]) for e in evs] == [
            ("foreground", 90.0, 95.0), ("bgm", 95.0, 125.0)]


class TestTrackForValidation:
    """曲目记录前置校验（2026-08-18 复盘②）：缺字段 / 文件不存在在解析
    时间轴时当场报——不拦的话错会流到渲染中段的 ffmpeg（文件不存在）
    或音量算术（lufs 为 None 时 TypeError），报错离病根隔好几层。
    """

    def test_缺lufs报错(self):
        bgm = {"tracks": {"曲": {"path": "x.flac", "dur": 100.0}}}
        with pytest.raises(SystemExit, match="lufs"):
            m._track_for("曲", bgm)

    def test_文件不存在报错(self):
        bgm = {"tracks": {"曲": {"path": "不存在.flac", "dur": 100.0, "lufs": -9.0}}}
        with pytest.raises(SystemExit, match="不存在"):
            m._track_for("曲", bgm)


class TestNaturalContinuationBounds:
    """自然收尾事件边界防御（审计加固）：自然收尾若已播完当场报错，拒绝负时长。"""

    def test_自然收尾前已播完当场失败(self, tmp_path: Path):
        # 前景已播到 100s（曲目全长 100s），后续段落再请求自然收尾至结束
        (tmp_path / "02-script.md").write_text(
            "## 音乐段 M1\n\n音乐: `满曲` 完整版 00:00-01:40\n"
            "状态: 前景试听，旁白停止\n过渡: 结尾自然淡出\n\n"
            "## 段落 1\n\n配音：第一段。\n\n"
            "音乐: `满曲` 继续播放至完整版结束\n", encoding="utf-8")
        (tmp_path / "m.flac").touch()
        bgm = {"tracks": {"满曲": {"path": "m.flac", "dur": 100.0, "lufs": -9.0}}}
        manifest = {"segments": [{"index": 1, "duration": 10.0}]}
        with pytest.raises(SystemExit, match="已经播完"):
            m.build_timeline(tmp_path, manifest, bgm)


    def test_记录齐全照常返回(self):
        rec = {"path": "x.flac", "dur": 100.0, "lufs": -9.0}
        assert m._track_for("曲", {"tracks": {"曲": rec}}) is rec


class TestAvsyncBlock:
    """形态 2 音画同源块（ADR-0013）：源片原声与画面切自同一源片同一时间码。

    每条断言对应一个声明不完整/自相矛盾的写法——同源块的崩法不是报错，
    是「被当成形态 1 CD 试听静默跑掉」（查曲库失败离病根很远，或更糟：
    画面切源片、声音放 CD 的两张皮没人拦）。
    """

    _HEAD = "## 段落 1\n\n配音：第一段。\n\n"
    _TAIL = "\n## 段落 2\n\n配音：第二段。\n"

    def _write(self, tmp_path: Path, body: str) -> Path:
        script = tmp_path / "02-script.md"
        script.write_text(self._HEAD + body + self._TAIL, encoding="utf-8")
        return script

    def test_解析同源块(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 M5 · 现场海啸\n\n"
            "源片原声: `测试企划 SP04` 00:21.60-00:41.60\n"
            "状态: 前景试听，旁白停止\n音画同源: 是\n过渡: 淡出\n画面: 同源\n")
        (blk,) = [b for b in m.parse_script_music(script) if b.label == "M5"]
        assert blk.avsync == "测试企划 SP04"
        assert blk.t0 == 21.6 and blk.t1 == 41.6
        assert blk.after == "fade" and blk.visual is None

    def test_缺音画同源声明当场失败(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 M5\n\n源片原声: `测试企划 SP04` 00:21.60-00:41.60\n"
            "状态: 前景试听，旁白停止\n过渡: 淡出\n画面: 同源\n")
        with pytest.raises(SystemExit, match="音画同源"):
            m.parse_script_music(script)

    def test_画面必须是同源(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 M5\n\n源片原声: `测试企划 SP04` 00:21.60-00:41.60\n"
            "状态: 前景试听，旁白停止\n音画同源: 是\n过渡: 淡出\n"
            "画面: 测试企划 SP05 00:01\n")
        with pytest.raises(SystemExit, match="同源"):
            m.parse_script_music(script)

    def test_过渡只认淡出(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 M5\n\n源片原声: `测试企划 SP04` 00:21.60-00:41.60\n"
            "状态: 前景试听，旁白停止\n音画同源: 是\n过渡: 降为 BGM\n画面: 同源\n")
        with pytest.raises(SystemExit, match="淡出"):
            m.parse_script_music(script)

    def test_时间区间为空当场失败(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 M5\n\n源片原声: `测试企划 SP04` 00:41.60-00:21.60\n"
            "状态: 前景试听，旁白停止\n音画同源: 是\n过渡: 淡出\n画面: 同源\n")
        with pytest.raises(SystemExit, match="区间为空"):
            m.parse_script_music(script)

    def test_同源块占时间槽但不产曲目事件(self, tmp_path: Path):
        """同源块占 20s 成片槽位、进 blocks（渲染切画面用），
        但 tracks 为空——进曲目事件序列 = 音乐床在该槽位不再静音（两张皮）。"""
        self._write(tmp_path,
            "## 音乐段 M5\n\n源片原声: `测试企划 SP04` 00:21.60-00:41.60\n"
            "状态: 前景试听，旁白停止\n音画同源: 是\n过渡: 淡出\n画面: 同源\n")
        manifest = {"segments": [{"index": 1, "duration": 10.0},
                                 {"index": 2, "duration": 10.0}]}
        plan = m.build_timeline(tmp_path, manifest, {"tracks": {}})
        assert plan["tracks"] == []
        (blk,) = plan["blocks"]
        assert blk["avsync"] == "测试企划 SP04"
        assert blk["dur"] == 20.0 and blk["t0"] == 21.6
        # 时间轴：段 1（10s）→ M5（20s）→ 段 2（10s），总长 40s
        assert plan["total_duration"] == 40.0
        assert [s["start"] for s in plan["segments"]] == [0.0, 30.0]

    def test_自然收尾画面声明(self, tmp_path: Path):
        """`收尾画面:` 显式指定长源：收尾默认继承同曲前景块画面源（短 MV 必越界），
        声明后事件直接带该画面——没写时 visual 为 None（继承行为不变）。"""
        (tmp_path / "02-script.md").write_text(
            "## 音乐段 M1\n\n音乐: `测试曲甲` 完整版 00:00-00:15\n"
            "状态: 前景试听，旁白停止\n过渡: 结尾自然淡出\n\n"
            "## 段落 1\n\n配音：第一段。\n\n"
            "音乐: `测试曲甲` 继续播放至完整版结束\n"
            "收尾画面: 测试番 SP05 13:26.00\n", encoding="utf-8")
        manifest = {"segments": [{"index": 1, "duration": 10.0}]}
        plan = m.build_timeline(tmp_path, manifest, _bgm())
        nat = [e for tr in plan["tracks"] for e in tr["events"] if e["vol"] == "natural"]
        assert len(nat) == 1 and nat[0]["visual"] == "测试番 SP05 13:26.00"


class TestBgmOnlyBlock:
    """背景铺底块（对齐 cue 乐章设计）：零时长、只做 BGM 边界与铺底事件。

    缺口实录：「段 1–4 片头前奏铺底」「段 17–20 英雄前奏铺底」这类**没有前景试听**
    的段落，「前景试听后降为 BGM」一条路表达不了——不是内容不要，是机制没有。
    """

    def _write(self, tmp_path: Path, body: str) -> Path:
        script = tmp_path / "02-script.md"
        script.write_text(body, encoding="utf-8")
        return script

    def test_解析铺底块(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 B1 · 前奏铺底\n\n"
            "音乐: `测试曲甲` 完整版 00:00\n"
            "状态: 背景铺底\n")
        (blk,) = m.parse_script_music(script)
        assert blk.bgm_only and blk.title == "测试曲甲"
        assert blk.t0 == 0.0 and blk.t1 is None

    def test_铺底块禁止画面与源片原声(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 B1\n\n音乐: `测试曲甲` 完整版 00:00\n"
            "状态: 背景铺底\n画面: 测试番 SP01 00:01\n")
        with pytest.raises(SystemExit, match="不许写"):
            m.parse_script_music(script)

    def test_铺底块缺时间行报错(self, tmp_path: Path):
        script = self._write(tmp_path,
            "## 音乐段 B1\n\n音乐: `测试曲甲` 完整版\n状态: 背景铺底\n")
        with pytest.raises(SystemExit, match="时间解析失败"):
            m.parse_script_music(script)

    def test_铺底块零时长占位且产BGM事件(self, tmp_path: Path):
        """铺底块不占成片时长、不进 blocks（无画面槽）、铺底事件覆盖到下一个音乐段；
        前序「降为 BGM」的覆盖在铺底块处收束（边界语义）。"""
        self._write(tmp_path,
            "## 音乐段 B1 · 片头铺底\n\n音乐: `测试曲甲` 完整版 00:00\n状态: 背景铺底\n\n"
            "## 段落 1\n\n配音：第一段。\n\n"
            "## 音乐段 B2 · 换曲铺底\n\n音乐: `测试曲乙` 完整版 00:30\n状态: 背景铺底\n\n"
            "## 段落 2\n\n配音：第二段。\n\n"
            "## 音乐段 M1 · 前景\n\n音乐: `测试曲甲` 完整版 01:00-01:15\n"
            "状态: 前景试听，旁白停止\n过渡: 结尾自然淡出\n")
        manifest = {"segments": [{"index": 1, "duration": 10.0},
                                 {"index": 2, "duration": 10.0}]}
        plan = m.build_timeline(tmp_path, manifest, _bgm())
        # 零时长：段 1 起点 0、段 2 起点 10、总长 10+15+10=35
        assert [s["start"] for s in plan["segments"]] == [0.0, 10.0]
        assert plan["total_duration"] == 35.0
        # blocks 只含前景 M1（铺底块无画面槽）
        assert [b["label"] for b in plan["blocks"]] == ["M1"]
        evs = {tr["name"]: tr["events"] for tr in plan["tracks"]}
        # B1：曲甲 00:00 起铺 10s（到 B2 边界）；B2：曲乙 00:30 起铺 10s（到 M1）
        assert evs["测试曲甲"] == [
            {"t0": 0.0, "t1": 10.0, "vol": "bgm", "at": 0.0, "underlay": True},
            {"t0": 60.0, "t1": 75.0, "vol": "foreground", "at": 20.0}]
        assert evs["测试曲乙"] == [{"t0": 30.0, "t1": 40.0, "vol": "bgm", "at": 10.0,
                                    "underlay": True}]

    def test_前序降为BGM在铺底块处收束(self, tmp_path: Path):
        self._write(tmp_path,
            "## 音乐段 M1 · 前景\n\n音乐: `测试曲甲` 完整版 00:00-00:10\n"
            "状态: 前景试听，旁白停止\n过渡: 降为 BGM\n\n"
            "## 段落 1\n\n配音：第一段。\n\n"
            "## 音乐段 B1 · 换曲\n\n音乐: `测试曲乙` 完整版 00:00\n状态: 背景铺底\n\n"
            "## 段落 2\n\n配音：第二段。\n")
        manifest = {"segments": [{"index": 1, "duration": 10.0},
                                 {"index": 2, "duration": 10.0}]}
        plan = m.build_timeline(tmp_path, manifest, _bgm())
        evs = {tr["name"]: tr["events"] for tr in plan["tracks"]}
        # M1 的 BGM 延续覆盖 段1（10s）到 B1 边界：曲甲 10.0→20.0
        assert evs["测试曲甲"][1] == {"t0": 10.0, "t1": 20.0, "vol": "bgm", "at": 10.0}
        assert evs["测试曲乙"] == [{"t0": 0.0, "t1": 10.0, "vol": "bgm", "at": 20.0,
                                    "underlay": True}]
