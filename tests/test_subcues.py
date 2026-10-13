"""逐句字幕时间（D86）：从 WINDOW=2 滑窗索引还原每句的起止，供试看片段烧原片中文字幕。

期望值都是先在实现上跑过、对着 subindex 的拼法（unit_i = 第 i 句 + 第 i+1 句，
start = 第 i 句起点，end = 第 i+1 句终点）推一遍再写的。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import paths, subcues

# 三句台词：A 0–2、B 2.5–4、C 5–7（滑窗：AB、BC、C）
UNITS = [
    {"start": 0.0, "end": 4.0, "text": "甲 乙"},
    {"start": 2.5, "end": 7.0, "text": "乙 丙"},
    {"start": 5.0, "end": 7.0, "text": "丙"},
]


def test_cues_逐句起止由滑窗还原():
    assert subcues.cues(UNITS, Path("x.json")) == [
        (0.0, 2.5, "甲"),   # 整集第 0 句没有精确终点：取下一句起点
        (2.5, 4.0, "乙"),   # 上一单元（甲乙）的终点就是乙的终点
        (5.0, 7.0, "丙"),   # 末单元只有一句：自己的终点
    ]


def test_cues_结构不对照样报错():
    with pytest.raises(ValueError, match="滑窗结构"):
        subcues.cues([{"start": 0, "end": 1, "text": "甲 乙"}, {"start": 1, "end": 2, "text": "丙"}], Path("x.json"))


def test_window_裁剪_偏移_丢闪字():
    cs = [(0.0, 2.5, "甲"), (2.5, 4.0, "乙"), (5.0, 7.0, "丙"), (9.0, 10.0, "丁")]
    # 窗口 [2.3, 6.0]：甲只剩 0.2 s（< MIN_CUE_S）丢掉；乙整句；丙裁到 6.0；丁在窗口外
    assert subcues.window(cs, 2.3, 6.0) == [(0.2, 1.7, "乙"), (2.7, 3.7, "丙")]
    # 刚好 MIN_CUE_S 的保留
    assert subcues.window([(0.0, 1.0, "甲")], 0.7, 5.0) == [(0.0, 0.3, "甲")]


def test_load_cues_没有索引返回None(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    assert subcues.load_cues("某番", "S01E01") is None
    (tmp_path / "library" / "index").mkdir(parents=True)
    (tmp_path / "library" / "index" / "某番_S01E01.json").write_text(
        json.dumps({"meta": {}, "units": UNITS}, ensure_ascii=False), encoding="utf-8")
    assert subcues.load_cues("某番", "S01E01")[1] == (2.5, 4.0, "乙")


def test_与agent字幕检索同一份还原():
    from pipeline.agent.tools import subtitle_lines
    assert subtitle_lines(UNITS, Path("x.json")) == [(0.0, "甲"), (2.5, "乙"), (5.0, "丙")]
