"""D81 PR-1：企划成为一等实体（树规则、期名解析、`00-project.md` 解析）。

夹具一律在临时根下造（不碰真实 data/）。期望值的来历写在各用例里。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline import paths, project
from pipeline.agent import cli

SAMPLE = """# 00 企划

企划: EGOIST 传奇企划志
番: EGOIST
合集: 00-全一期-典藏版

## 卷表

| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |
|---|---|---|---|---|
| 1 | 01-借躯降生 | 2011–2014 | 14-16分钟 | 余韵18秒 |
| 2 | 02-皮套囚徒 | 2015–2019 | 14-16分钟 | 余韵18秒 |
| 3 | 03-终局葬礼 | 2020–2023 | 18-20分钟 | 完整保留 |

## 总纲
（人写的散文，不解析）

## 标题模板
EGOIST编年史：第X期 <四字主题>——<反差金句>（年份跨度）
"""


def _ep(path: Path, mtime: float | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _legacy_get_episodes_list(root: Path) -> tuple[list[Path], int]:
    """改动前的 `cli.get_episodes_list`（逐字搬来作基准，红队 R-9）。"""
    ep_root = root / "data" / "episodes"
    if not ep_root.exists():
        return [], 0
    visible: list[Path] = []
    hidden_underscore = 0
    for d in ep_root.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        if d.name.startswith("_"):
            hidden_underscore += 1
            continue
        sub_eps = [
            sd for sd in d.iterdir()
            if sd.is_dir() and not sd.name.startswith((".", "_")) and (sd / "01-topic.md").exists()
        ]
        if sub_eps:
            visible.extend(sub_eps)
            hidden_underscore += len([sd for sd in d.iterdir() if sd.is_dir() and sd.name.startswith("_")])
        else:
            visible.append(d)
    visible.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return visible, hidden_underscore


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """§6.5 树规则夹具（不含「有 00-project.md 无成员的新企划」——那是有意的行为差异，另测）。"""
    root = tmp_path / "repo"
    eps = root / "data" / "episodes"
    _ep(eps / "plain-a", 100)
    _ep(eps / "plain-b", 300)
    for proj, base in (("EGOIST-传奇企划志", 400), ("EGOIST-传奇企划志-V2", 500)):
        _ep(eps / proj / "01-借躯降生", base + 1)
        _ep(eps / proj / "02-皮套囚徒", base + 2)
        (eps / proj / "_agent").mkdir()
        _ep(eps / proj / "_hidden-vol", base + 3)
        (eps / proj / "README.md").write_text("母目录说明\n", encoding="utf-8")
    # 企划目录自己也有 01-topic.md（母目录曾被当成期，§三 #2）
    _ep(eps / "SELF", 600)
    _ep(eps / "SELF" / "01-x", 601)
    # 成员含软链卷（指向 data/ 之外）
    outside = _ep(tmp_path / "elsewhere" / "real-vol", 700)
    _ep(eps / "LINKED" / "01-a", 701)
    (eps / "LINKED" / "02-linked").symlink_to(outside)
    # 第三层子期：DEEP/a/b/01-topic.md（a 自己没有 01-topic.md → DEEP 是普通期）
    (eps / "DEEP").mkdir()
    _ep(eps / "DEEP" / "a" / "b", 800)
    os.utime(eps / "DEEP", (50, 50))
    (eps / "_验证").mkdir()
    (eps / ".hidden").mkdir()
    # 建子目录会刷新父目录 mtime：最后统一钉一遍，排序断言才确定
    for rel, t in (("plain-a", 100), ("plain-b", 300), ("EGOIST-传奇企划志", 400),
                   ("EGOIST-传奇企划志/01-借躯降生", 401), ("EGOIST-传奇企划志/02-皮套囚徒", 402),
                   ("EGOIST-传奇企划志-V2", 500), ("EGOIST-传奇企划志-V2/01-借躯降生", 501),
                   ("EGOIST-传奇企划志-V2/02-皮套囚徒", 502), ("SELF", 600), ("SELF/01-x", 601),
                   ("LINKED", 690), ("LINKED/01-a", 701), ("DEEP", 50)):
        os.utime(eps / rel, (t, t))
    monkeypatch.setattr(paths, "ROOT", root)
    return root


def test_get_episodes_list_与改动前逐项相同(tree: Path) -> None:
    assert cli.get_episodes_list(root=tree) == _legacy_get_episodes_list(tree)


def test_树规则_分组隐藏数与排序(tree: Path) -> None:
    t = cli.get_episode_tree(tree)
    eps = tree / "data" / "episodes"
    assert sorted(p.name for p in t.plain) == ["DEEP", "plain-a", "plain-b"]
    by_name = {p.dir.name: p for p in t.projects}
    assert sorted(by_name) == ["EGOIST-传奇企划志", "EGOIST-传奇企划志-V2", "LINKED", "SELF"]
    assert [m.name for m in by_name["EGOIST-传奇企划志-V2"].members] == ["01-借躯降生", "02-皮套囚徒"]
    assert [m.name for m in by_name["LINKED"].members] == ["01-a", "02-linked"]
    assert [m.name for m in by_name["SELF"].members] == ["01-x"]
    assert not any(p.has_project_file for p in t.projects)
    # 隐藏：顶层 `_验证` 1 + 两个 EGOIST 各自的 `_agent` 与 `_hidden-vol` 2×2 = 5（与改动前口径相同）
    assert t.hidden_underscore == 5
    # 企划 mtime = 企划目录与成员 mtime 的最大值；混排按 mtime 降序。
    # LINKED 的 02-linked 跟随软链取 stat → 700（软链目标），所以 LINKED 是 max(701, 700, 目录本身)
    order = [e.dir.name if isinstance(e, project.ProjectEntry) else e.name for e in t.order]
    assert order.index("LINKED") < order.index("SELF") < order.index("EGOIST-传奇企划志-V2")
    assert order.index("EGOIST-传奇企划志-V2") < order.index("EGOIST-传奇企划志") < order.index("plain-b")
    assert order.index("plain-b") < order.index("plain-a") < order.index("DEEP")
    assert eps / "DEEP" in t.plain


def test_有企划文件无成员_是企划_不进平铺列表(tree: Path) -> None:
    """有意的行为差异（R-9）：改动前它被当成普通期列出，改动后是企划、不进平铺列表。"""
    newp = tree / "data" / "episodes" / "NEWP"
    newp.mkdir()
    (newp / "00-project.md").write_text("企划: 新\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n", encoding="utf-8")
    legacy, _ = _legacy_get_episodes_list(tree)
    assert newp in legacy
    flat, _ = cli.get_episodes_list(root=tree)
    assert newp not in flat
    entry = next(p for p in cli.get_episode_tree(tree).projects if p.dir == newp)
    assert entry.has_project_file and entry.members == ()


class TestClassify:
    @pytest.mark.parametrize("rel,kind", [
        ("plain-a", "episode"),
        ("DEEP", "episode"),
        ("EGOIST-传奇企划志-V2", "project"),
        ("SELF", "project"),
        ("LINKED", "project"),
        ("EGOIST-传奇企划志-V2/01-借躯降生", "volume"),
        ("LINKED/02-linked", "volume"),
        ("SELF/01-x", "volume"),
        ("EGOIST-传奇企划志-V2/_hidden-vol", None),
        ("EGOIST-传奇企划志-V2/_agent", None),
        ("DEEP/a", None),
        ("DEEP/a/b", None),
        ("plain-a/sub", None),
        ("_验证", "episode"),
        (".hidden", None),
        ("nope", None),
    ])
    def test_判定(self, tree: Path, rel: str, kind: str | None) -> None:
        assert cli.classify_dir(tree / "data" / "episodes" / rel, tree) == kind

    def test_期根以上经软链_末段仍按字面(self, tree: Path, tmp_path: Path) -> None:
        """宿主可能给出期根以上经软链的路径（macOS `/var -> /private/var`）：只 resolve 期根那一段。"""
        alias = tmp_path / "alias-repo"
        alias.symlink_to(tree)
        eps = tree / "data" / "episodes"
        assert cli.classify_dir(alias / "data/episodes/plain-a", tree) == "episode"
        assert cli.classify_dir(alias / "data/episodes/LINKED/02-linked", tree) == "volume"
        assert cli.resolve_episode_target(str(alias / "data/episodes/EGOIST-传奇企划志-V2/01-借躯降生")) == \
            eps / "EGOIST-传奇企划志-V2" / "01-借躯降生"

    def test_期根之外与期根本身(self, tree: Path, tmp_path: Path) -> None:
        assert cli.classify_dir(tree / "data" / "episodes", tree) is None
        assert cli.classify_dir(tmp_path / "elsewhere" / "real-vol", tree) is None

    def test_软链卷的企划归属按字面路径(self, tree: Path, tmp_path: Path) -> None:
        """红队 R-5：M/01-a -> ../P/01-a（P 是另一个企划）归 M；M/02-x -> data 之外 同样归 M。"""
        eps = tree / "data" / "episodes"
        _ep(eps / "P" / "01-a")
        (eps / "M").mkdir()
        (eps / "M" / "01-a").symlink_to(Path("..") / "P" / "01-a")
        (eps / "M" / "02-x").symlink_to(_ep(tmp_path / "far" / "02-x"))
        assert cli.classify_dir(eps / "M" / "01-a", tree) == "volume"
        assert project.project_of(eps / "M" / "01-a", tree) == eps / "M"
        assert project.project_of(eps / "M" / "02-x", tree) == eps / "M"
        assert cli.resolve_episode_target("M/01-a") == eps / "M" / "01-a"


class TestResolve:
    def test_同名卷歧义_返回None并列出两个候选(self, tree: Path) -> None:
        assert cli.resolve_episode_target("01-借躯降生") is None
        eps = tree / "data" / "episodes"
        assert cli.resolve_episode_candidates("01-借躯降生") == [
            eps / "EGOIST-传奇企划志" / "01-借躯降生",
            eps / "EGOIST-传奇企划志-V2" / "01-借躯降生",
        ]

    def test_带企划前缀的卷键唯一(self, tree: Path) -> None:
        eps = tree / "data" / "episodes"
        assert cli.resolve_episode_target("EGOIST-传奇企划志-V2/01-借躯降生") == eps / "EGOIST-传奇企划志-V2" / "01-借躯降生"
        assert cli.resolve_episode_target("data/episodes/EGOIST-传奇企划志/01-借躯降生") == eps / "EGOIST-传奇企划志" / "01-借躯降生"

    def test_本企划内先解析(self, tree: Path) -> None:
        eps = tree / "data" / "episodes"
        v2 = eps / "EGOIST-传奇企划志-V2"
        assert cli.resolve_episode_target("01-借躯降生", project=v2) == v2 / "01-借躯降生"

    @pytest.mark.parametrize("target", ["03-audio", "pipeline", "EGOIST-传奇企划志-V2", "_hidden-vol", "DEEP/a/b", "a/../plain-a"])
    def test_拒(self, tree: Path, target: str, monkeypatch: pytest.MonkeyPatch) -> None:
        (tree / "data" / "episodes" / "plain-a" / "03-audio").mkdir()
        (tree / "pipeline").mkdir()
        monkeypatch.chdir(tree)  # 工作目录在仓库根：`pipeline` 不再被当成期
        assert cli.resolve_episode_target(target) is None

    def test_企划目录拒因写明用企划会话(self, tree: Path) -> None:
        path, reason = cli.explain_episode_target("EGOIST-传奇企划志-V2")
        assert path is None and "企划目录" in reason and "企划会话" in reason

    def test_绝对路径(self, tree: Path, tmp_path: Path) -> None:
        eps = tree / "data" / "episodes"
        assert cli.resolve_episode_target(str(eps / "plain-a")) == eps / "plain-a"
        assert cli.resolve_episode_target(str(eps.resolve() / "plain-a")) == eps / "plain-a"
        assert cli.resolve_episode_target(str(tmp_path / "elsewhere" / "real-vol")) is None
        assert cli.resolve_episode_target(str(eps / "EGOIST-传奇企划志-V2")) is None

    def test_下划线顶层可显式指定(self, tree: Path) -> None:
        eps = tree / "data" / "episodes"
        assert cli.resolve_episode_target("_验证") == eps / "_验证"


class TestParse:
    def test_样例(self) -> None:
        doc = project.parse(SAMPLE)
        assert doc.name == "EGOIST 传奇企划志"
        assert doc.collection == "00-全一期-典藏版"
        assert [v.dir for v in doc.volumes] == ["01-借躯降生", "02-皮套囚徒", "03-终局葬礼"]
        assert [v.tail_s for v in doc.volumes] == [18, 18, None]
        assert doc.volumes[2].budget_min == (18.0, 20.0)
        assert doc.sections == ("卷表", "总纲", "标题模板")

    def test_空卷表合法(self) -> None:
        doc = project.parse("企划: 新\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n")
        assert doc.volumes == () and doc.collection is None

    @pytest.mark.parametrize("mutate,needle", [
        (lambda s: s.replace("企划: EGOIST 传奇企划志\n", ""), "缺 `企划:`"),
        (lambda s: s.replace("合集: 00", "企划: 重复\n合集: 00"), "出现两次"),
        (lambda s: s.replace("| 2 | 02-皮套囚徒", "| 3 | 02-皮套囚徒"), "连续整数"),
        (lambda s: s.replace("| 完整保留 |", "| 余韵18秒 |"), "末行"),
        (lambda s: s.replace("02-皮套囚徒", "02/皮套"), "不合规"),
        (lambda s: s.replace("02-皮套囚徒", "01-借躯降生"), "重复"),
        (lambda s: s.replace("| 时长预算 |", "| 时长 |"), "表头"),
        (lambda s: s.replace("14-16分钟 | 余韵18秒 |\n| 2", "16-14分钟 | 余韵18秒 |\n| 2"), "0 < A ≤ B"),
        (lambda s: s.replace("余韵18秒 |\n| 2", "余韵61秒 |\n| 2"), "余韵N秒"),
        (lambda s: s.replace("合集: 00-全一期-典藏版", "合集: 01-借躯降生"), "合集"),
        (lambda s: s.replace("## 卷表", "## 卷目"), "缺 `## 卷表`"),
    ])
    def test_不成立(self, mutate, needle: str) -> None:
        with pytest.raises(project.ParseError, match=needle):
            project.parse(mutate(SAMPLE))

    def test_多位小数的时长预算是解析错不是崩溃(self, tmp_path: Path) -> None:
        """D81 审查发现（2026-10-13）：`1.2.3-4分钟` 这类单元格曾让 float() 的裸 ValueError 漏出
        parse/load——render_project_doc 只捕 ParseError，于是每轮注入都炸掉整个回合
        （坏文件只能经 bash / 手改落盘，而那正是「解析失败要给人看」分支存在的理由）。"""
        bad = SAMPLE.replace("14-16分钟", "1.2.3-4分钟", 1)
        with pytest.raises(project.ParseError, match="数字不成立"):
            project.parse(bad)
        proj = tmp_path / "P"
        proj.mkdir()
        (proj / "00-project.md").write_text(bad, encoding="utf-8")
        assert isinstance(project.load(proj), project.ParseError)   # load 返回错误实例，不抛
        from pipeline.agent.session import render_project_doc
        text = render_project_doc(proj, bad)
        assert "解析失败" in text and "卷表不可信" in text   # 注入降级为原文 + 错误行，不炸回合

    def test_目录大小写不同算重复(self) -> None:
        """红队 R-19：APFS 大小写不敏感，`01-A` 与 `01-a` 是同一个目录。"""
        text = SAMPLE.replace("01-借躯降生", "01-A").replace("02-皮套囚徒", "01-a")
        with pytest.raises(project.ParseError, match="重复"):
            project.parse(text)

    def test_机读字段不认对齐空白(self) -> None:
        aligned = SAMPLE.replace("| 1 | 01-借躯降生 |", "|  1  |   01-借躯降生   |")
        assert project.machine_fields(project.parse(aligned)) == project.machine_fields(project.parse(SAMPLE))
        assert project.diff_machine(project.parse(SAMPLE), project.parse(aligned)) == []

    def test_diff_改一格(self) -> None:
        after = project.parse(SAMPLE.replace("| 18-20分钟 |", "| 19-21分钟 |"))
        lines = project.diff_machine(project.parse(SAMPLE), after)
        assert lines == [
            "- | 3 | 03-终局葬礼 | 2020–2023 | 18-20分钟 | 完整保留 |",
            "+ | 3 | 03-终局葬礼 | 2020–2023 | 19-21分钟 | 完整保留 |",
        ]

    def test_成员与卷表对不上的提示(self, tmp_path: Path) -> None:
        proj = tmp_path / "P"
        _ep(proj / "01-借躯降生")
        _ep(proj / "05-多出来")
        _ep(proj / "00-全一期-典藏版")
        hints = project.members_vs_table(proj, project.parse(SAMPLE))
        assert hints == [
            "卷表列了 `02-皮套囚徒` 但目录不存在",
            "卷表列了 `03-终局葬礼` 但目录不存在",
            "目录 `05-多出来` 不在卷表",
        ]
