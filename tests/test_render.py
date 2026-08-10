"""render.py 的纯函数测试。ffmpeg 命令本身的正确性由运行期 ffprobe 复核，
这里只测不依赖音视频的解析逻辑。"""

from pathlib import Path

import pytest

from pipeline.render import (_bgm_layout, _bgm_list, _body_entry_delay, _seg_entry)
from pipeline.render import BGM_MID_ENTRY, BGM_OUTRO_ENTRY


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
