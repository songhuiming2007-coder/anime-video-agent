"""D61 后续：subgrep 逐字查询（匹配语义与引用核销一致：整集拼接 + 归一化 + 简繁折叠）。"""

from __future__ import annotations

from pipeline import subgrep as G

LINES = [(10.0, "第一句"), (20.0, "第二句 有目標"), (30.0, "第三句"),
         (40.0, "第四句"), (50.0, "第五句 又有目標"), (60.0, "第六句")]


def test_子串命中带上下文():
    groups, folded = G.hits_with_context(LINES, "第二句", ctx=1)
    assert not folded
    assert groups == [(0, 2, {1})]


def test_多命中上下文重叠时合并():
    groups, _ = G.hits_with_context(LINES, "目標", ctx=3)   # 命中第 1、4 行，上下文相接
    assert groups == [(0, 5, {1, 4})]
    groups, _ = G.hits_with_context(LINES, "目標", ctx=0)   # 不带上下文：两组分开
    assert groups == [(1, 1, {1}), (4, 4, {4})]


def test_归一化与简繁折叠():
    groups, folded = G.hits_with_context(LINES, "第 二 句！", ctx=0)
    assert [m for _, _, m in groups] == [{1}] and not folded
    groups, folded = G.hits_with_context([(10.0, "只是路過的冤魂")], "只是路过的冤魂", ctx=3)
    assert folded and groups[0][2] == {0}
    groups, folded = G.hits_with_context(LINES, "全集没有这句", ctx=3)
    assert groups == [] and not folded


def test_时间窗():
    rows = G.window(LINES, 35, 15)
    assert [s for _, s in rows] == ["第二句 有目標", "第三句", "第四句", "第五句 又有目標"]


# ---------- CLI（替身掉数据目录与索引） ----------


def _stub(monkeypatch, lines):
    monkeypatch.setattr(G.paths, "require_data", lambda: None)
    monkeypatch.setattr(G, "subtitle_lines", lambda anime, key, cache: lines)


def test_main_时间码模式(capsys, monkeypatch):
    _stub(monkeypatch, LINES)
    assert G.main(["番", "s01e01", "0:00:35", "15"]) == 0
    out = capsys.readouterr().out
    assert "第二句" in out and "第六句" not in out


def test_main_折叠命中提示原字形(capsys, monkeypatch):
    _stub(monkeypatch, [(10.0, "只是路過的冤魂")])
    assert G.main(["番", "S01E01", "只是路过的冤魂"]) == 0
    out = capsys.readouterr().out
    assert "简繁折叠" in out and "只是路過的冤魂" in out


def test_main_找不到与缺索引的返回码(capsys, monkeypatch):
    _stub(monkeypatch, LINES)
    assert G.main(["番", "S01E01", "全集没有这句"]) == 1
    monkeypatch.setattr(G, "subtitle_lines", lambda anime, key, cache: None)
    assert G.main(["番", "S01E01", "随便"]) == 1
