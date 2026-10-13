"""逐句字幕时间：从字幕索引还原「每句台词何时出现、何时消失」（D86）。

字幕索引（`subindex.py`）存的是 WINDOW=2 滑窗单元：unit_i = 第 i 句 + 第 i+1 句，
`start` = 第 i 句起点，`end` = 第 i+1 句终点，末单元只有一句。逐句文本从末尾往前剥后缀
即可精确还原；逐句终点 = unit_{i-1}.end（i ≥ 1 时精确），只有整集第 0 句没有精确终点，
取下一句起点（整集开头那一句，几乎不会落进试看窗口）。

索引建库时已滤掉日文行、制作名单与 OP 特效绘图（`subindex.KANA / NOISE / DRAW`），
时间与片源对齐（`ingest.verify` 校过），所以这里拿到的就是能直接烧进成片的中文台词。

纯标准库：渲染要用它，不能为读一份 JSON 把 sentence_transformers 一整套拉进来。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths

#: 裁到窗口里只剩不足 0.3 s 的句子丢掉：窗口边缘露出一帧半帧的字只会闪一下，
#: 0.3 s ≈ 7 帧（23.976 fps），低于此人眼读不出一个字。
MIN_CUE_S = 0.3

Cue = tuple[float, float, str]


def index_path(anime: str, key: str) -> Path:
    return paths.DATA / "library" / "index" / f"{anime}_{key}.json"


def lines(units: list[dict[str, Any]], source: Path) -> list[str]:
    """滑窗单元 → 逐句文本。结构对不上就报错，不静默给半截台词。"""
    if not units:
        return []
    out: list[str] = [""] * len(units)
    out[-1] = str(units[-1]["text"])
    for i in range(len(units) - 2, -1, -1):
        text, suffix = str(units[i]["text"]), " " + out[i + 1]
        if not (text.endswith(suffix) and len(text) > len(suffix)):
            raise ValueError(f"{source.name} 第 {i} 个单元不是 WINDOW=2 滑窗结构，无法还原单句")
        out[i] = text[: -len(suffix)]
    return out


def cues(units: list[dict[str, Any]], source: Path) -> list[Cue]:
    """滑窗单元 → [(起点, 终点, 台词)]（源片时间，秒）。"""
    texts = lines(units, source)
    out: list[Cue] = []
    for i, (u, text) in enumerate(zip(units, texts)):
        start = float(u["start"])
        if i == len(units) - 1:
            end = float(u["end"])              # 末单元只有一句：自己的终点
        elif i >= 1:
            end = float(units[i - 1]["end"])   # 上一单元 = 第 i-1 句 + 第 i 句，终点就是第 i 句的
        else:
            end = float(units[1]["start"])     # 整集第 0 句：没有精确终点，取下一句起点
        out.append((start, max(start, end), text))
    return out


def load_cues(anime: str, key: str) -> list[Cue] | None:
    """读 `<番>_<集键>.json` 还原逐句时间；没有这一集的索引返回 None（是否算错由调用方定）。"""
    path = index_path(anime, key)
    if not path.exists():
        return None
    return cues(json.loads(path.read_text(encoding="utf-8"))["units"], path)


def window(all_cues: list[Cue], t0: float, t1: float) -> list[Cue]:
    """与源片窗口 [t0, t1] 有重叠的句子，裁到窗口内，时间换成相对 t0 的偏移。

    裁完显示不足 MIN_CUE_S 的丢掉（边缘闪字）。按起点排序，原样保留台词文本。
    """
    out: list[Cue] = []
    for start, end, text in all_cues:
        a, b = max(start, t0), min(end, t1)
        if b - a < MIN_CUE_S:
            continue
        out.append((round(a - t0, 3), round(b - t0, 3), text))
    return sorted(out)
