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
