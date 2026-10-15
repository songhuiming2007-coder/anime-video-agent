"""D99 PR-2：事实库（pipeline/facts.py）——摘录比对、系统定级、人工升级、重做校验。

夹具是 2026-10-15 用 AVA 自己的 web_fetch 重抓卷一事实文件出处时的真实原文片段（施工图 §二），
摘录取自第三版事实文件与第二版勘误里的编造句。期望值先在实现上跑过、逐条对过原因才写进断言。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import facts
from pipeline.agent import fetch_log

# ---- 真实原文片段（web_fetch 文本：行内标签处多一个空格是它的原样） ----
NOTE17 = ("席になり、誰かが音楽理論の話をしだした。 自分は、すごいなと思って聞いていた。 当時、自分は全部、感覚だけで曲を作っていた。"
          " こうするとこうなる、という理屈を一つも持ってなかった。")
ATWIKI = "DLは こちら 。 2015年8月7日23時54分頃、ボカロオリジナル曲で2曲目となる 1000万再生 を達成。 2017年12月24日、"
WIKIWIKI = "ミリオン達成状況 『メルト』から『初めての恋が終わる時』まで、 5曲連続でミリオン再生達成 という金字塔を打ち立てた。 曲名 100万再生"
NICO2 = ("ラスト担当が違ったので、毎回変えていった方が華やかでいいなって話になったんです。知り合いの中でこういう話に乗ってくれそうな人を考えて、"
         "hukeさんとredjuiceさん 【※】 をryoさんに紹介してメンバーが増えました。 ——どんな名義で活動してたんですか？ "
         "別名義は「スガ」で、三輪士郎のアシスタントという設定。もし「三輪士郎に絵柄が似てないか？」と疑われても、アシスタントなら仕方ないって言い訳できるように（笑）。")
NICO1 = "「メルト」のCDをコミケで売るって話を知って、119さんも居るんじゃないかと思って、挨拶に行ったんですよ。"
ASCII = ("元々あの絵には設定があったのですか。 huke いや、設定はないんです。頭の中にあったのは、女の子が武器を持っている絵を描こうというだけだったんですね。")
NOTE19 = "職員室に呼び出された。 音楽の山田先生だった。 女の先生で、怒ると休み時間が全部なくなるくらい詰められる。"

URL = {
    "note17": "https://note.com/ryodayospcl/n/n1de8849fcd76",
    "atwiki": "https://w.atwiki.jp/hmiku/pages/82.html",
    "wikiwiki": "https://wikiwiki.jp/negi/ryo",
    "nico1": "https://originalnews.nico/20667",
    "nico2": "https://originalnews.nico/20667/2",
    "ascii": "https://ascii.jp/elem/000/000/449/449394/4/",
    "note19": "https://note.com/ryodayospcl/n/ne0daf286b0bf",
}


class TestCheckExcerpt:
    @pytest.mark.parametrize("excerpt,text", [
        ("当時、自分は全部、感覚だけで曲を作っていた。", NOTE17),                                     # 原样
        ("2015年8月7日23時54分頃、ボカロオリジナル曲で2曲目となる1000万再生を達成。", ATWIKI),          # fetch 在链接处多了空格
        ("『メルト』から『初めての恋が終わる時』まで、**5曲連続でミリオン再生達成** という金字塔を打ち立てた。", WIKIWIKI),  # 摘录自带粗体
        ("別名義は『スガ』で、三輪士郎のアシスタントという設定。", NICO2),                              # 原文「」抄成『』
    ])
    def test_逐字(self, excerpt, text):
        assert facts.check_excerpt(excerpt, text) == ("exact", "")

    @pytest.mark.parametrize("excerpt,text", [
        ("知り合いの中でこういう話に乗ってくれそうな人を考えて、hukeさんとredjuiceさんをryoさんに紹介してメンバーが増えました。", NICO2),  # 删了脚注号【※】
        ("huke：いや、設定はないんです。", ASCII),                                                 # 加了说话人冒号
    ])
    def test_近似不算逐字(self, excerpt, text):
        match, near = facts.check_excerpt(excerpt, text)
        assert match == "approx"
        assert near  # 给模型对照的原文片段

    @pytest.mark.parametrize("excerpt,text", [
        ("山田先生が血相を変えて", NOTE19),                       # 第二版编造（note #19 勘误）
        ("放送部だった自分はドラクエの曲を流した", NOTE19),
        ("元々あの絵には設定はないんです", ASCII),               # 第二版把提问和回答拼成一句
        ("集英社の編集部に止められたので、別名義で描き始めたんですよ。", NICO2),  # 仿第一版的动机改写
    ])
    def test_编造与拼接不命中(self, excerpt, text):
        assert facts.check_excerpt(excerpt, text)[0] == "none"

    def test_问号改句号只算近似(self):
        # 判据 5：标点里有信号。L5 丢掉标点后才对上，不能判逐字
        assert facts.check_excerpt("もし「三輪士郎に絵柄が似てないか。」と疑われても", NICO2)[0] == "approx"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    for key, text in (("note17", NOTE17), ("nico1", NICO1), ("nico2", NICO2), ("ascii", ASCII)):
        fetch_log.record(url=URL[key], final_url=None, kind="fetch", text=text, sid="old-session", root=tmp_path)
    return tmp_path


@pytest.fixture
def vol(root: Path) -> Path:
    d = root / "data" / "episodes" / "企划" / "01-卷"
    d.mkdir(parents=True)
    return d


def E(claim: str, url: str, *excerpts: str, level: str = "一手", topic: str = "成员") -> dict:
    return {"claim": claim, "url": url, "excerpts": list(excerpts), "proposed_level": level, "topic": topic}


class TestAdd:
    def test_逐字命中落待核_模型提一手也不落一手(self, root, vol):
        out = facts.add([E("当时全凭感觉作曲", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。")],
                        target=vol, sid="new-session", root=root)
        assert out["results"] == [{"ok": True, "id": "F001", "level": "待核", "match": "exact",
                                   "hint": "摘录逐字在原文里；级别「待核」，一手 / 二手由人定"}]
        saved = facts.load(vol)[0]
        assert saved["level_by"] == "system" and saved["proposed_level"] == "一手" and saved["sid"] == "new-session"

    def test_跨会话的留档也算(self, root, vol):
        # 留档是 old-session 抓的，写入的是 new-session（人裁决 2026-10-15）
        out = facts.add([E("x", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。")], target=vol, sid="new-session", root=root)
        assert out["results"][0]["match"] == "exact"

    def test_没抓过的URL只能未核实(self, root, vol):
        r = facts.add([E("x", "https://example.com/never", "随便一句")], target=vol, sid=None, root=root)["results"][0]
        assert (r["level"], r["match"]) == ("未核实", "none")
        assert "没有抓取留档" in r["hint"]

    def test_挂错页抓得出来(self, root, vol):
        # 第三版的真错：这句只在采访第 2 页，事实文件挂在第 1 页
        ex = "知り合いの中でこういう話に乗ってくれそうな人を考えて"
        wrong = facts.add([E("x", URL["nico1"], ex)], target=vol, sid=None, root=root)["results"][0]
        right = facts.add([E("x", URL["nico2"], ex)], target=vol, sid=None, root=root)["results"][0]
        assert (wrong["level"], wrong["match"]) == ("未核实", "none")
        assert (right["level"], right["match"]) == ("待核", "exact")

    def test_近似命中落待核但回执要求重抄(self, root, vol):
        r = facts.add([E("x", URL["ascii"], "huke：いや、設定はないんです。")], target=vol, sid=None, root=root)["results"][0]
        assert (r["level"], r["match"]) == ("待核", "approx")
        assert "逐字重抄" in r["hint"]

    def test_多条摘录取最差的那条(self, root, vol):
        r = facts.add([E("x", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。", "編造された一句")],
                      target=vol, sid=None, root=root)["results"][0]
        assert (r["level"], r["match"]) == ("未核实", "none")

    def test_模型自己说未核实就是未核实(self, root, vol):
        r = facts.add([E("x", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。", level="未核实")],
                      target=vol, sid=None, root=root)["results"][0]
        assert r["level"] == "未核实"

    @pytest.mark.parametrize("bad,msg", [
        ({"claim": "", "url": "https://a.b/", "excerpts": ["x"]}, "claim"),
        ({"claim": "x", "url": "ftp://a.b/", "excerpts": ["x"]}, "http"),
        ({"claim": "x", "url": "https://a.b/", "excerpts": "一句"}, "excerpts"),
        ({"claim": "x", "url": "https://a.b/", "excerpts": ["x"], "proposed_level": "待核"}, "proposed_level"),
    ])
    def test_坏条目逐条报错不连累别的(self, root, vol, bad, msg):
        out = facts.add([bad, E("好的", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。")], target=vol, sid=None, root=root)
        assert out["results"][0]["ok"] is False and msg in out["results"][0]["error"]
        assert out["results"][1] == {**out["results"][1], "ok": True, "id": "F001"}

    def test_被改过的留档不作依据(self, root, vol):
        hit = fetch_log.lookup(URL["note17"], root=root)[0]
        (fetch_log.log_dir(root) / hit["file"]).write_text("当時、自分は全部、感覚だけで曲を作っていた。（改过）", encoding="utf-8")
        r = facts.add([E("x", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。")], target=vol, sid=None, root=root)["results"][0]
        assert (r["level"], r["match"]) == ("未核实", "none") and "被改过" in r["hint"]

    def test_编号接着排_并生成阅读版(self, root, vol):
        facts.add([E("a", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。")], target=vol, sid=None, root=root)
        out = facts.add([E("b", URL["nico2"], "別名義は『スガ』で")], target=vol, sid=None, root=root)
        assert out["results"][0]["id"] == "F002"
        md = (vol / "facts.md").read_text(encoding="utf-8")
        assert "手改无效" in md and "**F002**［待核］b" in md and "> 別名義は『スガ』で" in md


class TestCheckAndPromote:
    def _two(self, root, vol):
        facts.add([E("a", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。"),
                   E("b", URL["ascii"], "huke：いや、設定はないんです。"),
                   E("c", URL["nico2"], "別名義は『スガ』で", "アシスタントという設定"),
                   E("d", "https://example.com/never", "一句")], target=vol, sid=None, root=root)

    def test_刚写进去的全部对得上(self, root, vol):
        self._two(root, vol)
        assert facts.check(vol, root=root) == []

    def test_bash手改级别现形(self, root, vol):
        self._two(root, vol)
        lines = (vol / "facts.jsonl").read_text(encoding="utf-8").splitlines()
        e = json.loads(lines[0]); e["level"] = "一手"
        lines[0] = json.dumps(e, ensure_ascii=False)
        (vol / "facts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert any("不是人定的" in p for p in facts.check(vol, root=root))

    def test_手改摘录现形(self, root, vol):
        self._two(root, vol)
        lines = (vol / "facts.jsonl").read_text(encoding="utf-8").splitlines()
        e = json.loads(lines[0]); e["excerpts"] = ["当時、自分は全部、理論だけで曲を作っていた。"]
        lines[0] = json.dumps(e, ensure_ascii=False)
        (vol / "facts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert any(p.startswith("F001") and "对不上" in p for p in facts.check(vol, root=root))

    def test_按条目号升级_只升逐字命中的(self, root, vol):
        self._two(root, vol)
        done = facts.promote(vol, level="一手", ids=["F001", "F002", "F004"], root=root)
        assert done == ["F001"]  # F002 近似、F004 未核实，都不替它背书
        by = {e["id"]: e for e in facts.load(vol)}
        assert (by["F001"]["level"], by["F001"]["level_by"]) == ("一手", "human")
        assert by["F002"]["level"] == "待核" and by["F004"]["level"] == "未核实"
        assert facts.check(vol, root=root) == []

    def test_按URL批量升级(self, root, vol):
        self._two(root, vol)
        assert facts.promote(vol, level="二手", url=URL["nico2"] + "#frag", root=root) == ["F003"]

    def test_模型自标未核实的不被按URL批量升级(self, root, vol):
        facts.add([E("x", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。", level="未核实")], target=vol, sid=None, root=root)
        assert facts.promote(vol, level="一手", url=URL["note17"], root=root) == []

    def test_只能升一手二手(self, root, vol):
        with pytest.raises(ValueError):
            facts.promote(vol, level="待核", ids=["F001"], root=root)

    def test_CLI_check有问题退1(self, root, vol, monkeypatch, capsys):
        self._two(root, vol)
        monkeypatch.setattr(fetch_log.paths, "ROOT", root)
        assert facts.main(["check", str(vol)]) == 0
        lines = (vol / "facts.jsonl").read_text(encoding="utf-8").splitlines()
        e = json.loads(lines[0]); e["level"] = "二手"
        (vol / "facts.jsonl").write_text("\n".join([json.dumps(e, ensure_ascii=False), *lines[1:]]) + "\n", encoding="utf-8")
        assert facts.main(["check", str(vol)]) == 1
        assert "FAIL F001" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 工具层：fact_add 的审查、写入目录、bash 绕行
# ---------------------------------------------------------------------------


class TestTool:
    def test_免卡(self, vol):
        from pipeline.agent.session import review_tool_call

        v = review_tool_call("fact_add", {"entries": [E("x", URL["note17"], "y")]}, ep_dir=vol, scope="creative")
        assert v.action == "allow"

    def test_卷会话写进卷目录_企划会话写进企划目录(self, root, vol):
        from pipeline.agent.tools import ToolContext, _tool_fact_add

        args = {"entries": [E("x", URL["note17"], "当時、自分は全部、感覚だけで曲を作っていた。")]}
        _tool_fact_add(args, ToolContext(episode_dir=vol, root=root, session_id="s"))
        assert facts.load(vol)[0]["sid"] == "s"
        proj = vol.parent
        # 企划会话：SessionHost 里 ep_dir 与 project_dir 都是企划目录
        _tool_fact_add(args, ToolContext(episode_dir=proj, project_dir=proj, session_kind="project", root=root))
        assert len(facts.load(proj)) == 1 and len(facts.load(vol)) == 1

    def test_没有期目录的会话拒绝(self, root):
        from pipeline.agent.tools import NO_EPISODE_MESSAGE, ToolContext, _tool_fact_add

        with pytest.raises(PermissionError, match=NO_EPISODE_MESSAGE[:6]):
            _tool_fact_add({"entries": [E("x", "", "y")]}, ToolContext(episode_dir=None, root=root))

    @pytest.mark.parametrize("cmd,hit", [
        ("cat >> facts.jsonl <<'EOF'\n{}\nEOF", True),
        ("echo x | tee data/library/fetch-log/index.jsonl", True),
        ("sed -i '' 's/待核/一手/' facts.jsonl", True),
        ("cat facts.jsonl", False),
        ("python -m pipeline.facts check .", False),
    ])
    def test_bash写事实库弹卡(self, cmd, hit):
        from pipeline.agent.status_card import bash_danger_tags

        assert any("事实库" in t for t in bash_danger_tags(cmd)) is hit

    def test_bash跑promote弹卡(self):
        from pipeline.agent.status_card import bash_danger_tags

        assert "[须人审的流水线命令]" in bash_danger_tags("python -m pipeline.facts promote . --level 一手 --ids F001")
        assert "[须人审的流水线命令]" not in bash_danger_tags("python -m pipeline.facts check .")
