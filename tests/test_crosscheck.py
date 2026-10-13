"""D81 PR-4（C-1 / C-2）：`pipeline.crosscheck` 镜头与配乐跨卷重复，警告不拦截。

临时仓库根（paths.ROOT 指过去），夹具企划三卷都在 tmp_path 下，不碰真实 data/。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from pipeline import crosscheck as cc
from pipeline import paths
from pipeline.clips import _overlaps, same_footage

TABLE = (
    "企划: 测试编年史\n合集: 00-合集\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n"
    "| 1 | 01-a | 2011 | 14-16分钟 | 余韵18秒 |\n| 2 | 02-b | 2012 | 14-16分钟 | 余韵18秒 |\n"
    "| 3 | 03-c | 2013 | 14-16分钟 | 完整保留 |\n"
)


def clip(anime, season, episode, start, dur, **kw) -> dict:
    return {"anime": anime, "season": season, "episode": episode, "start": start, "dur": dur, **kw}


def write_clips(vol: Path, segments: list[list[dict]], name: str = "04-clips.approved.json") -> None:
    data = {"segments": [{"index": i, "clips": cs} for i, cs in enumerate(segments, 1)]}
    (vol / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def fg(label: str, title: str, rng: str = "00:10-00:40", trans: str = "结尾自然淡出") -> str:
    return f"## 音乐段 {label}\n\n音乐: `{title}` 完整版 {rng}\n状态: 前景试听，旁白停止\n过渡: {trans}\n\n"


def bed(label: str, title: str) -> str:
    return f"## 音乐段 {label}\n\n音乐: `{title}` 完整版 00:00\n状态: 背景铺底\n\n"


def para(n: int) -> str:
    return f"## 段落 {n}\n\n配音：第 {n} 段。\n\n"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    (r / "data" / "episodes").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", r)
    monkeypatch.setattr(paths, "DATA", r / "data")
    return r


@pytest.fixture
def proj(root: Path) -> Path:
    """三卷：a / b 有 approved，c 只有 04-clips.json；合集期不参与。"""
    p = root / "data" / "episodes" / "P"
    for name in ("01-a", "02-b", "03-c", "00-合集"):
        (p / name).mkdir(parents=True)
        (p / name / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    (p / "00-project.md").write_text(TABLE, encoding="utf-8")
    write_clips(p / "01-a", [
        [clip("番", 1, 1, 100.0, 10.0)],                       # 段 1：与 b 段 1 交集 5 s → 命中
        [clip("番", 1, 2, 50.0, 5.0)],                         # 段 2：与 b 段 2 相邻（b0 == a1）→ 不命中
        [clip("番", 1, 3, 10.0, 5.0)],                         # 段 3：与 b 段 3 不同番同 S01E03 → 不命中
        [clip(None, 1, 4, 20.0, 6.0)],                         # 段 4：anime=None 通配，与 c 同区间 → 命中
        [clip("EGOIST", None, 6, 0.0, 6.006, sp=True, limit=6.006)],   # 段 5：微动 SP，与 b 重叠 → 微动物证
        [clip("番", 1, 5, 0.0, 4.0, extend=30.0)],              # 段 6：extend 不计，与 c 的 [10, 14) 不交
    ])
    write_clips(p / "02-b", [
        [clip("番", 1, 1, 105.0, 10.0)],
        [clip("番", 1, 2, 55.0, 5.0)],
        [clip("别的番", 1, 3, 10.0, 5.0)],
        [clip("番", 1, 9, 0.0, 1.0)],
        [clip("EGOIST", None, 6, 2.0, 6.006, sp=True, limit=6.006)],
    ])
    write_clips(p / "03-c", [
        [clip("番", 1, 4, 20.0, 6.0)],
        [clip("番", 1, 5, 10.0, 4.0)],
    ], name="04-clips.json")
    (p / "01-a" / "02-script.md").write_text(fg("M1", "同一首") + para(1) + bed("M2", "铺底曲"), encoding="utf-8")
    (p / "02-b" / "02-script.md").write_text(fg("M1", "同一首", trans="自然播放至结束") + para(1)
                                             + fg("M2", "铺底曲") + para(2), encoding="utf-8")
    (p / "03-c" / "02-script.md").write_text(bed("M1", "同一首") + para(1), encoding="utf-8")
    return p


def hits_key(res: cc.Result, micro: bool) -> list[tuple]:
    return [(h.a.volume, h.a.segment, h.b.volume, h.b.segment, h.overlap)
            for h in (res.micro_hits if micro else res.shot_hits)]


# ---------------------------------------------------------------- same_footage / _overlaps


def test_same_footage_None通配_跨番不同集() -> None:
    assert same_footage(clip(None, 1, 1, 0, 1), clip("X", 1, 1, 0, 1)) is True
    assert same_footage(clip("X", 1, 1, 0, 1), clip("Y", 1, 1, 0, 1)) is False
    assert same_footage(clip("X", None, 6, 0, 1), clip("X", 1, 6, 0, 1)) is False   # SP06 ≠ S01E06


def test_overlaps_anime为None与具名番同集重叠算撞车() -> None:
    """抽谓词时最容易丢的一条：None 是「未知番」通配，跟谁都同番。"""
    cand = {"anime": None, "season": 1, "episode": 1, "start": 10.0, "dur": 5.0, "score": None}
    chosen = [{"anime": "X", "season": 1, "episode": 1, "start": 12.0, "dur": 5.0, "score": None}]
    assert _overlaps(cand, chosen) is True
    assert _overlaps({**cand, "anime": "Y"}, chosen) is False


# ---------------------------------------------------------------- C-1 镜头


def test_镜头命中与不命中(proj: Path) -> None:
    res = cc.run(proj)
    # 同集同区间、None 通配命中；相邻、跨番、extend 都不命中；合集期不参与
    assert hits_key(res, micro=False) == [("01-a", 1, "02-b", 1, 5.0), ("01-a", 4, "03-c", 1, 6.0)]
    assert hits_key(res, micro=True) == [("01-a", 5, "02-b", 5, 4.006)]
    assert res.clip_sources == {"01-a": "04-clips.approved.json", "02-b": "04-clips.approved.json",
                                "03-c": "04-clips.json"}
    assert res.unchecked == []


def test_报告分节_注明来源(proj: Path) -> None:
    text = cc.render(cc.run(proj), "2026-10-13 10:00")
    assert "镜头清单来源：`01-a` 04-clips.approved.json；`02-b` 04-clips.approved.json；`03-c` 04-clips.json" in text
    assert "## 镜头（2）\n- 番 S01E01：`01-a` 段 1 [100.00, 110.00) ↔ `02-b` 段 1 [105.00, 115.00)，交集 5.00 s" in text
    assert "## 微动物证（单卷内豁免的那一类，1）\n- EGOIST SP06：`01-a` 段 5" in text
    assert "汇总：命中 4 条（镜头 2 · 微动物证 1 · 配乐 1）；未检查 0 卷" in text


def test_两份片段清单都没有_列入未检查(proj: Path) -> None:
    (proj / "03-c" / "04-clips.json").unlink()
    res = cc.run(proj)
    assert ("03-c", "镜头", "没有 04-clips.approved.json 也没有 04-clips.json") in res.unchecked
    assert res.unchecked_volumes == ["03-c"]
    assert hits_key(res, micro=False) == [("01-a", 1, "02-b", 1, 5.0)]   # 其余照查


# ---------------------------------------------------------------- C-2 配乐


def test_配乐_前景两卷命中_铺底不算(proj: Path) -> None:
    res = cc.run(proj)
    # 「同一首」a / b 都是前景 → 命中（c 是铺底不列）；「铺底曲」a 铺底 b 前景 → 只有一卷前景，不命中
    assert [(h.title, [(u.volume, u.block, u.after) for u in h.uses]) for h in res.music_hits] == [
        ("同一首", [("01-a", "M1", "fade"), ("02-b", "M1", "end")])]
    text = cc.render(res, "t")
    assert "- `同一首`：`01-a` 块 M1（fade，00:10.00-00:40.00）；`02-b` 块 M1（end，00:10.00-00:40.00）" in text


def test_配乐_全角半角经NFKC后相同(proj: Path) -> None:
    (proj / "03-c" / "02-script.md").write_text(fg("M1", "ＥＧＯＩＳＴ　Ｓｏｎｇ ") + para(1), encoding="utf-8")
    (proj / "01-a" / "02-script.md").write_text(fg("M1", "EGOIST Song") + para(1), encoding="utf-8")
    res = cc.run(proj)
    assert [(h.title, [u.volume for u in h.uses]) for h in res.music_hits] == [("EGOIST Song", ["01-a", "03-c"])]


def test_配乐_格式错的卷未检查_其余照查(proj: Path) -> None:
    (proj / "02-b" / "02-script.md").write_text("## 音乐段 M1\n\n音乐: `同一首` 完整版 00:10-00:40\n\n" + para(1),
                                               encoding="utf-8")   # 缺 `过渡:` 行
    (proj / "03-c" / "02-script.md").write_text(fg("M1", "同一首") + para(1), encoding="utf-8")
    res = cc.run(proj)
    assert [(v, k) for v, k, _r in res.unchecked] == [("02-b", "配乐")]
    assert "缺 `过渡: `行" in res.unchecked[0][2]
    assert [[u.volume for u in h.uses] for h in res.music_hits] == [["01-a", "03-c"]]
    assert len(res.shot_hits) == 2   # 镜头照查


# ---------------------------------------------------------------- CLI：只读、--write、退出码


def _snapshot(d: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(d)): (p.stat().st_size, p.stat().st_mtime_ns) for p in d.rglob("*")}


def test_默认只读_企划目录无写入(proj: Path, capsys: pytest.CaptureFixture) -> None:
    before = _snapshot(proj)
    marker = time.time()
    assert cc.main([str(proj)]) == 0
    assert "汇总：命中 4 条" in capsys.readouterr().out
    assert [p for p in proj.rglob("*") if p.stat().st_mtime > marker] == []
    assert _snapshot(proj) == before
    assert cc.main([str(proj), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"].startswith("汇总：命中 4 条") and len(out["shots"]) == 2
    assert _snapshot(proj) == before


def test_write只写报告与留底(proj: Path) -> None:
    before = _snapshot(proj)
    assert cc.main([str(proj), "--write"]) == 0
    after = _snapshot(proj)
    assert set(after) - set(before) == {"00-crosscheck.md"}
    assert {k: v for k, v in after.items() if k in before} == before
    first = (proj / "00-crosscheck.md").read_text(encoding="utf-8")
    assert cc.main([str(proj), "--write"]) == 0
    hist = list((proj / "_agent" / "file-history").iterdir())
    assert len(hist) == 1 and hist[0].name.endswith("-00-crosscheck.md")
    assert hist[0].read_text(encoding="utf-8") == first
    assert set(_snapshot(proj)) - set(before) == {"00-crosscheck.md", "_agent", "_agent/file-history",
                                                 f"_agent/file-history/{hist[0].name}"}


def test_缩写不被认作write(proj: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cc.main([str(proj), "--wr"])
    assert exc.value.code == 2
    assert not (proj / "00-crosscheck.md").exists()


@pytest.mark.parametrize("doc, why", [
    (None, "没有 00-project.md"),
    ("企划: x\n\n## 卷表\n\n| 卷序 | 目录 |\n", "解析失败"),
    ("企划: x\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n", "卷表为空"),
])
def test_企划不成立退2(proj: Path, doc: str | None, why: str, capsys: pytest.CaptureFixture) -> None:
    if doc is None:
        (proj / "00-project.md").unlink()
    else:
        (proj / "00-project.md").write_text(doc, encoding="utf-8")
    assert cc.main([str(proj)]) == 2
    assert why in capsys.readouterr().err


def test_卷不是企划退2(proj: Path, capsys: pytest.CaptureFixture) -> None:
    assert cc.main([str(proj / "01-a")]) == 2
    assert "不是企划目录" in capsys.readouterr().err


def test_软链卷照读(proj: Path, root: Path) -> None:
    """镜像企划的形状：卷是指向别处的软链，字面路径认作本企划的卷，只读。"""
    m = root / "data" / "episodes" / "_mirror"
    m.mkdir()
    (m / "00-project.md").write_text(TABLE, encoding="utf-8")
    for v in ("01-a", "02-b", "03-c"):
        os.symlink(proj / v, m / v)
    before = _snapshot(proj)
    res = cc.run(m)
    assert hits_key(res, micro=False) == [("01-a", 1, "02-b", 1, 5.0), ("01-a", 4, "03-c", 1, 6.0)]
    assert _snapshot(proj) == before


# ---------------------------------------------------------------- 状态卡


def test_状态卡显示上次检查(proj: Path) -> None:
    from pipeline.agent.status_card import build_project_card

    assert "上次跨卷检查" not in build_project_card(proj)
    assert cc.main([str(proj), "--write"]) == 0
    card = build_project_card(proj)
    line = next(ln for ln in card.splitlines() if ln.startswith("上次跨卷检查"))
    assert line.startswith("上次跨卷检查: 4 条命中（镜头 2 · 微动物证 1 · 配乐 1）/ 未检查 0 卷（")
    (proj / "00-crosscheck.md").write_text("被手改了\n", encoding="utf-8")
    assert "找不到汇总行" in build_project_card(proj)


def test_is_micro_认显式still标():
    """D84：12 s 微动（新默认）过不了 ≤8 s 代理，靠 `still` 标；口径与 clips._is_micro 一致。"""
    from pipeline.clips import _is_micro
    for c in (clip("P", None, 3, 0.0, 12.0, sp=True, still=True),
              clip("P", None, 3, 0.0, 6.0, sp=True, limit=6.006),
              clip("P", None, 3, 0.0, 12.0, sp=True, limit=12.012),
              clip("P", 1, 3, 0.0, 12.0, still=True)):
        assert cc._is_micro(c) == _is_micro(c)
    assert cc._is_micro(clip("P", None, 3, 0.0, 12.0, sp=True, still=True))
    assert not cc._is_micro(clip("P", None, 3, 0.0, 12.0, sp=True, limit=12.012))
