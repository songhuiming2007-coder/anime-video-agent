"""render.py 的纯函数测试。ffmpeg 命令本身的正确性由运行期 ffprobe 复核，
这里只测不依赖音视频的解析逻辑。"""

from pathlib import Path

import pytest

from pipeline.render import (_bgm_layout, _bgm_list, _body_entry_delay, _seg_entry, _music_bed, _runs, _vol_expr, _extract_loudnorm_json)
from pipeline.render import BGM_MID_ENTRY, BGM_OUTRO_ENTRY, FOREGROUND_LUFS


def _topic(tmp_path: Path, content: str) -> Path:
    ep = tmp_path / "ep"
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text(content, encoding="utf-8")
    return ep


def test_entry_absent_returns_zero(tmp_path: Path):
    ep = _topic(tmp_path, "番: 罪恶王冠\nBGM正文: x\n")
    assert _body_entry_delay(ep) == 0.0


def test_entry_present_parses_seconds(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文切入点: 36秒\n")
    assert _body_entry_delay(ep) == 36.0


def test_entry_decimal(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文切入点: 35.2秒\n")
    assert _body_entry_delay(ep) == 35.2


def test_entry_malformed_raises(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文切入点: 三十六秒\n")
    with pytest.raises(SystemExit):
        _body_entry_delay(ep)


def test_no_topic_file_returns_zero(tmp_path: Path):
    assert _body_entry_delay(tmp_path) == 0.0


def test_bgm_list_parses_entries_and_auto(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A @36秒\n  - B\n  - C @470秒\n")
    assert _bgm_list(ep) == [("A", 36.0), ("B", None), ("C", 470.0)]


def test_bgm_list_absent_returns_none(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文: x\n")
    assert _bgm_list(ep) is None


def test_bgm_list_malformed_at_raises(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A @ 啊秒\n")
    with pytest.raises(SystemExit):
        _bgm_list(ep)


def test_bgm_list_stops_at_non_item_line(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A @36秒\n\n## 下一节\n")
    assert _bgm_list(ep) == [("A", 36.0)]


def test_seg_entry_reads_and_rejects(tmp_path: Path):
    ep = _topic(tmp_path, "BGM中段切入点: 291秒\n")
    assert _seg_entry(ep, "中段", BGM_MID_ENTRY) == 291.0
    ep2 = _topic(tmp_path / "ep2", "BGM中段切入点: 三秒\n")
    with pytest.raises(SystemExit):
        _seg_entry(ep2, "中段", BGM_MID_ENTRY)
    assert _seg_entry(tmp_path, "结尾", BGM_OUTRO_ENTRY) is None


def _rec(dur: float, start: float) -> tuple[dict, float]:
    return {"dur": dur}, start


def test_layout_crossfade_gap_last():
    # 本期真实结构：Departures(255s)36 起 → 100(175s)291 起 → Planetes 470 起
    plan = [_rec(255, 36), _rec(175, 291), _rec(367, 470)]
    lens, runs = _bgm_layout(plan, 490)
    assert lens == [258, 175, 20]
    assert runs == [0, 2]


def test_layout_three_contiguous_crossfades():
    plan = [_rec(100, 0), _rec(100, 90), _rec(100, 180)]
    lens, runs = _bgm_layout(plan, 300)
    assert lens == [93, 93, 120]
    assert runs == [0]


def test_layout_gap_mid_run():
    plan = [_rec(100, 0), _rec(100, 90), _rec(200, 200)]
    lens, runs = _bgm_layout(plan, 300)
    assert lens == [93, 100, 100]
    assert runs == [0, 2]


class TestMusicBedRuns:
    """F2：连续事件合并成 run（曲目内位置连续 = 同一音轨，只变音量）。

    合并后音量用表达式做斜坡，不在交界处剪出淡变断口（03-music-cues.md
    过渡原则「不重新切一份音频」）。
    """

    def test_连续事件合并成一个run(self):
        evs = [
            {"t0": 0.0, "t1": 15.0, "vol": "foreground"},
            {"t0": 15.0, "t1": 33.6, "vol": "bgm"},
            {"t0": 263.0, "t1": 286.0, "vol": "foreground"},
        ]
        runs = _runs(evs)
        assert len(runs) == 2
        assert [(e["t0"], e["t1"]) for e in runs[0]] == [(0.0, 15.0), (15.0, 33.6)]
        assert runs[1] == [evs[2]]

    def test_跳点不合并(self):
        evs = [
            {"t0": 0.0, "t1": 15.0, "vol": "foreground"},
            {"t0": 263.0, "t1": 286.0, "vol": "foreground"},
        ]
        assert len(_runs(evs)) == 2

    def test_单事件run(self):
        assert len(_runs([{"t0": 0.0, "t1": 22.0, "vol": "foreground"}])) == 1

    def test_音量表达式_单段恒定_线性域(self):
        evs = [{"t0": 0.0, "t1": 15.0, "vol": "foreground"}]
        expr = _vol_expr(evs, -10.0)
        # 前景增益 = FOREGROUND_LUFS - lufs（当前 -19-(-10) = -9dB）→ 线性值。
        # 引用常量算期望，不写死数值（常量标定变了测试自动跟随）。
        # 断言计算值而不是字符串（二轮审查 B2'：字符串断言在音量语义
        # 被改坏时照样全绿——volume 滤镜里裸数字是线性倍率不是 dB）。
        fg = FOREGROUND_LUFS - (-10.0)
        assert float(expr) == pytest.approx(10 ** (fg / 20), abs=1e-3)

    def test_音量表达式_单段bgm_线性域(self):
        # B2''：单段路径的 bgm 分支（-26 - lufs）之前没被任何测试覆盖
        evs = [{"t0": 0.0, "t1": 15.0, "vol": "bgm"}]
        expr = _vol_expr(evs, -10.0)
        assert float(expr) == pytest.approx(10 ** (-16 / 20), abs=1e-3)

    def test_音量表达式_斜坡分段_线性域(self):
        evs = [
            {"t0": 0.0, "t1": 15.0, "vol": "foreground"},
            {"t0": 15.0, "t1": 33.6, "vol": "bgm"},
        ]
        expr = _vol_expr(evs, -10.0)
        # 平台值必须出现在表达式里且是线性域：
        # 前景（FOREGROUND_LUFS-(-10) dB）与 BGM -26-(-10)=-16dB
        fg = FOREGROUND_LUFS - (-10.0)
        assert f"{10 ** (fg / 20):.4f}" in expr
        assert f"{10 ** (-16 / 20):.4f}" in expr
        assert "lt(t,15.000)," in expr
        assert "(t-15.000)/0.50" in expr
        # 斜坡两端的线性值夹在中间（斜坡在线性域插值）
        assert f"{10 ** (fg / 20):.4f}+({10 ** (-16 / 20):.4f}-{10 ** (fg / 20):.4f})" in expr


class TestLoudnormTwoPass:
    """两遍归一的 JSON 提取（纯函数部分）。

    2026-08-16：单次 loudnorm 动态模式会把音乐段后的旁白压掉（段落 8 人声
    -13.3 vs 段落 1 -8.2，TTS 原始电平相同）；改两遍测量 + 线性增益。
    """

    STDERR = """[Parsed_loudnorm_0 @ 0x123] {
        "input_i" : "-22.16",
        "input_tp" : "-8.91",
        "input_lra" : "11.70",
        "input_thresh" : "-32.60",
        "target_offset" : "0.16",
        "target_i" : "-16.00"
    }
    [out#0 @ 0x456] frame=  N/A ..."""

    def test_提取测量JSON(self):
        m = _extract_loudnorm_json(self.STDERR)
        assert m["input_i"] == "-22.16"
        assert m["target_offset"] == "0.16"
        assert m["input_lra"] == "11.70"

    def test_无JSON报错(self):
        with pytest.raises(SystemExit):
            _extract_loudnorm_json("ffmpeg 日志，没有 JSON")
