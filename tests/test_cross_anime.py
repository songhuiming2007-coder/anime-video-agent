# 跨番混剪（2026-09-06）：多番声明解析、锚点番名前缀、多库联合排片、
# 跨池 BGM 回查。规格：同一期视频混合多部番的素材，单番期行为 0 漂移
# （单番回归由既有测试覆盖，这里只测多番新增行为）。

import json

import numpy as np
import pytest

from pipeline import align, bgm, check_script, clips, ingest, subindex


# ---------------------------------------------------------------- 选题元数据

class TestAnimesOf:
    def _topic(self, tmp_path, text):
        (tmp_path / "01-topic.md").write_text(text, encoding="utf-8")
        return tmp_path

    def test_番行逗号分隔多番(self, tmp_path):
        ep = self._topic(tmp_path, "番: 罪恶王冠, PSYCHO-PASS，甲铁城的卡巴内利、夏隧\n")
        assert bgm.animes_of(ep) == ["罪恶王冠", "PSYCHO-PASS", "甲铁城的卡巴内利", "夏隧"]

    def test_企划名加素材番剧块(self, tmp_path):
        ep = self._topic(tmp_path,
                         "番: EGOIST\n素材番剧:\n  - 罪恶王冠\n  - PSYCHO-PASS（心理测量者）\n\n正文\n")
        # 企划名不是素材番（没入库没索引），素材集以列表块为准；全称括号剥掉
        assert bgm.animes_of(ep) == ["罪恶王冠", "PSYCHO-PASS"]
        assert bgm.anime_of(ep) == "EGOIST"      # 主番=企划名，BGM 池/笔记按它查

    def test_素材番剧空块落回番行(self, tmp_path):
        ep = self._topic(tmp_path, "番: EGOIST\n素材番剧:\n正文开始\n")
        assert bgm.animes_of(ep) == ["EGOIST"]

    def test_单番返回长度1且主番不变(self, tmp_path):
        ep = self._topic(tmp_path, "番: 春物（我的青春恋爱物语果然有问题）\n")
        assert bgm.animes_of(ep) == ["春物"]
        assert bgm.anime_of(ep) == "春物"

    def test_多番时主番是第一个(self, tmp_path):
        ep = self._topic(tmp_path, "番: 罪恶王冠, PSYCHO-PASS\n")
        assert bgm.anime_of(ep) == "罪恶王冠"

    def test_没有01topic返回空(self, tmp_path):
        assert bgm.animes_of(tmp_path) == []


# ---------------------------------------------------------------- 锚点番名前缀

class TestAnchorPrefix:
    def test_带番名前缀(self):
        a = clips._parse_anchor("罪恶王冠 S01E01 17:50", 1, ["罪恶王冠", "PSYCHO-PASS"])
        assert a["anime"] == "罪恶王冠" and a["season"] == 1 and a["t0"] == 17 * 60 + 50

    def test_不带番名默认主番(self):
        a = clips._parse_anchor("S01E01 17:50-18:20", 1, ["罪恶王冠", "PSYCHO-PASS"])
        assert a["anime"] == "罪恶王冠" and a["t1"] == 18 * 60 + 20

    def test_番表里没有的名字当场报错(self):
        with pytest.raises(SystemExit, match="甲铁城"):
            clips._parse_anchor("甲铁城 S01E01 17:50", 1, ["罪恶王冠", "PSYCHO-PASS"])

    def test_番表为空时不校验前缀(self):
        a = clips._parse_anchor("罪恶王冠 S01E01 17:50", 1, [])
        assert a["anime"] == "罪恶王冠"

    def test_区间与剧场版分钟仍认(self):
        a = clips._parse_anchor("你的名字 S01E01 96:08", 1, ["你的名字"])
        assert a["t0"] == 96 * 60 + 8


# ---------------------------------------------------------------- 多库联合排片

class TestCrossAnimeRun:
    """run() 级集成：双番联合检索/锚点直通，产物归属按 clip['anime'] 固化。"""

    TBL = {"shots": [{"i": 0, "start": 0.0, "end": 10.0, "rep": 5.0},
                     {"i": 1, "start": 10.0, "end": 25.0, "rep": 17.5},
                     {"i": 2, "start": 25.0, "end": 300.0, "rep": 112.5}]}
    SRC = {("罪恶王冠", "S01E01"): {"path": "/gc/e01.mkv", "duration": 200.0},
           ("心理测量者", "S01E01"): {"path": "/pp/e01.mkv", "duration": 300.0}}

    class U:
        def __init__(self, anime, start, end, text="台词"):
            self.anime, self.season, self.episode = anime, 1, 1
            self.start, self.end, self.text = start, end, text

    def _run(self, tmp_path, monkeypatch, script_text, durations, hits=()):
        monkeypatch.setattr(clips, "load_sources_multi", lambda animes: self.SRC)
        monkeypatch.setattr(clips, "load_all", lambda d, a: (None, None))
        monkeypatch.setattr(clips, "search", lambda *a, **k: [(0.9, u) for u in hits])
        from pipeline import shots as sh
        monkeypatch.setattr(sh, "load", lambda a, k: self.TBL)
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "01-topic.md").write_text("番: EGOIST\n素材番剧:\n  - 罪恶王冠\n  - 心理测量者\n",
                                        encoding="utf-8")
        (ep / "02-script.md").write_text(script_text, encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(json.dumps({"segments": [
            {"index": i, "label": str(i), "text": "x", "file": f"s{i}.wav",
             "duration": d, "cer": 0.0, "attempts": 1}
            for i, d in enumerate(durations, 1)]}), encoding="utf-8")
        return json.loads(clips.run(ep).read_text(encoding="utf-8"))

    def test_双番锚点各归各的片源(self, tmp_path, monkeypatch):
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n  锚点: 罪恶王冠 S01E01 00:12\n\n"
                  "## 段落 2\n\n配音：二。\n\n画面：\n  锚点: 心理测量者 S01E01 00:12\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0, 8.0])
        assert data["animes"] == ["罪恶王冠", "心理测量者"]
        assert data["anime"] == "罪恶王冠"          # 主番 = 素材番剧第一个
        s1, s2 = data["segments"]
        assert s1["status"] == s2["status"] == "ok"
        assert s1["clips"][0]["anime"] == "罪恶王冠"
        assert s1["clips"][0]["source"] == "/gc/e01.mkv"
        assert s2["clips"][0]["anime"] == "心理测量者"
        assert s2["clips"][0]["source"] == "/pp/e01.mkv"

    def test_跨番同集同时间码不算撞车(self, tmp_path, monkeypatch):
        """两部番各自有 S01E01：同一时间码各锚各的，不许误报 anchor_overlap。"""
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n  锚点: 罪恶王冠 S01E01 00:12\n\n"
                  "## 段落 2\n\n配音：二。\n\n画面：\n  锚点: 心理测量者 S01E01 00:12\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0, 8.0])
        assert all(s["status"] == "ok" for s in data["segments"])

    def test_同番同时间码仍然撞车(self, tmp_path, monkeypatch):
        """反向护栏：同番同处双锚照旧 anchor_overlap（多番键不能放宽同番判定）。"""
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n  锚点: 罪恶王冠 S01E01 00:12\n\n"
                  "## 段落 2\n\n配音：二。\n\n画面：\n  锚点: 罪恶王冠 S01E01 00:13\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0, 8.0])
        assert data["segments"][1]["status"] == "anchor_overlap"

    def test_检索命中按单元番名归属片源(self, tmp_path, monkeypatch):
        """台词通道：同一查询命中两部番，candidate 按 u.anime 查各自的片源表。"""
        script = "## 段落 1\n\n配音：一。\n\n画面：\n  查询: 那句台词\n"
        hits = [self.U("罪恶王冠", 30.0, 33.0), self.U("心理测量者", 40.0, 43.0)]
        data = self._run(tmp_path, monkeypatch, script, [6.0], hits=hits)
        seg = data["segments"][0]
        assert seg["status"] == "ok"
        got = {(c["anime"], c["source"]) for c in seg["clips"]}
        assert got == {("罪恶王冠", "/gc/e01.mkv"), ("心理测量者", "/pp/e01.mkv")}

    def test_锚点指定的番没入库该段_no_match(self, tmp_path, monkeypatch):
        script = "## 段落 1\n\n配音：一。\n\n画面：\n  锚点: 心理测量者 S02E01 00:12\n"
        data = self._run(tmp_path, monkeypatch, script, [8.0])
        assert data["segments"][0]["status"] == "no_match"


class TestCandidateMulti:
    SRC = {("番A", "S01E01"): {"path": "/a.mkv", "duration": 100.0},
           ("番B", "S01E01"): {"path": "/b.mkv", "duration": 100.0}}

    class U:
        def __init__(self, anime):
            self.anime, self.season, self.episode = anime, 1, 1
            self.start, self.end, self.text = 10.0, 13.0, "台词"

    def test_复合键按番取片源(self):
        got = clips.candidate(0.9, self.U("番B"), self.SRC, {"番A", "番B"})
        assert got["anime"] == "番B" and got["source"] == "/b.mkv"

    def test_允许集之外的番被拒(self):
        assert clips.candidate(0.9, self.U("番C"), self.SRC, {"番A", "番B"}) is None

    def test_单番字符串允许集行为不变(self):
        assert clips.candidate(0.9, self.U("番A"), self.SRC, "番A")["source"] == "/a.mkv"
        assert clips.candidate(0.9, self.U("番B"), self.SRC, "番A") is None


class TestOverlapsMulti:
    def _c(self, anime):
        return {"anime": anime, "season": 1, "episode": 1,
                "start": 100.0, "span": 3.0}

    def test_不同番同集同时间不算同一处(self):
        assert not clips._overlaps(self._c("番A"), [self._c("番B")])

    def test_同番同时间算同一处(self):
        assert clips._overlaps(self._c("番A"), [self._c("番A")])

    def test_缺anime字段的旧片段按同番判(self):
        old = {"season": 1, "episode": 1, "start": 100.0, "span": 3.0}
        assert clips._overlaps(self._c("番A"), [old])
        assert clips._overlaps(old, [self._c("番A")])


class TestPresenceDispatch:
    """_by_character 的 pres 传 {番: Presence} 时按命中单元的 anime 分派。"""

    class P:
        def __init__(self, score):
            self.score = score

        def presence_score(self, season, episode, start, end, name):
            return self.score

    class U:
        def __init__(self, anime):
            self.anime, self.season, self.episode = anime, 1, 1
            self.start, self.end = 0.0, 2.0

    def test_按番分派在场分(self):
        pres = {"番A": self.P(0.9), "番B": self.P(0.0)}
        hits = [(0.6, self.U("番A")), (0.59, self.U("番B"))]
        kept, fell_back = clips._by_character(hits, pres, "某人")
        # 番A 检出 0.9，番B 未检出垫底——带内修正后番A 仍应在检出者里
        assert not fell_back
        assert kept[0][1].anime == "番A" and kept[0][2] == 0.9
        assert kept[-1][2] == 0.0                       # 漏检垫底不枪毙

    def test_该番没建在场索引按漏检垫底(self):
        pres = {"番A": self.P(0.9)}                     # 番B 没有索引
        hits = [(0.6, self.U("番B")), (0.59, self.U("番A"))]
        kept, _ = clips._by_character(hits, pres, "某人")
        assert kept[-1][1].anime == "番B" and kept[-1][2] == 0.0


# ---------------------------------------------------------------- 多库/索引加载

class TestLoaders:
    def test_load_all_列表联合加载(self, tmp_path):
        for name, anime in (("番A_S01E01", "番A"), ("番B_S01E01", "番B"), ("番C_S01E01", "番C")):
            d = {"meta": {"model_id": subindex.MODEL_NAME, "revision": None},
                 "units": [{"anime": anime, "season": 1, "episode": 1,
                            "start": 0.0, "end": 2.0, "text": "台词"}]}
            (tmp_path / f"{name}.json").write_text(
                json.dumps(d, ensure_ascii=False), encoding="utf-8")
            np.save(tmp_path / f"{name}.npy", np.zeros((1, 4), dtype=np.float32))
        vecs, units = subindex.load_all(tmp_path, ["番A", "番B"])
        assert sorted(u.anime for u in units) == ["番A", "番B"]
        vecs, units = subindex.load_all(tmp_path, "番A")      # 单番 str 行为不变
        assert [u.anime for u in units] == ["番A"]

    def test_load_sources_multi_单番平面多番复合(self, tmp_path):
        db = {"番A": {"S01E01": {"path": "a.mkv", "duration": 1.0}},
              "番B": {"S01E01": {"path": "b.mkv", "duration": 2.0}}}
        f = tmp_path / "sources.json"
        f.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        flat = ingest.load_sources_multi(["番A"], f)
        assert flat == db["番A"]                               # 与 load_sources 同形
        multi = ingest.load_sources_multi(["番A", "番B"], f)
        assert multi[("番B", "S01E01")]["path"] == "b.mkv"
        assert "S01E01" not in multi                           # 复合键不混平面键

    def test_sources_get_复合优先平面回退(self):
        assert ingest.sources_get({("番A", "S01E01"): 1, "S01E01": 2}, "番A", "S01E01") == 1
        assert ingest.sources_get({"S01E01": 2}, None, "S01E01") == 2
        assert ingest.sources_get({("番A", "S01E01"): 1}, "番B", "S01E01") is None


# ---------------------------------------------------------------- 机检双番校验

class TestCheckScriptMulti:
    def _mk(self, tmp_path, monkeypatch, anchors, sources_by_anime):
        from pipeline import ingest as ing
        monkeypatch.setattr(ing, "load_sources", lambda a: sources_by_anime[a])
        blocks = []
        for i, anc in enumerate(anchors, 1):
            blocks.append(f"## 段落 {i}\n\n配音：第{i}段的话。\n\n画面：\n  锚点: {anc}\n")
        ep = tmp_path / "ep"
        ep.mkdir()
        (ep / "01-topic.md").write_text(
            "番: EGOIST\n素材番剧:\n  - 罪恶王冠\n  - 心理测量者\n", encoding="utf-8")
        script = ep / "02-script.md"
        script.write_text("\n".join(blocks), encoding="utf-8")
        return {c.name: c for c in check_script.run(script)}

    SRC = {"罪恶王冠": {"S01E01": {"path": "/gc.mkv", "duration": 200.0}},
           "心理测量者": {"S01E01": {"path": "/pp.mkv", "duration": 300.0}}}

    def test_双番锚点都落在各自片源内(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["罪恶王冠 S01E01 01:00", "心理测量者 S01E01 02:00"], self.SRC)
        assert checks["锚点格式"].ok
        assert checks["锚点指向素材"].ok

    def test_锚点超出所指番的片长按该番报(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["心理测量者 S01E01 09:00"], self.SRC)   # 540s > 300s
        assert not checks["锚点指向素材"].ok
        assert "心理测量者" in checks["锚点指向素材"].detail

    def test_锚点写了番表外的番名报格式错(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch, ["甲铁城 S01E01 01:00"], self.SRC)
        assert not checks["锚点格式"].ok
        assert "甲铁城" in checks["锚点格式"].detail

    def test_不带番名归主番校验(self, tmp_path, monkeypatch):
        # 主番 = 素材番剧第一个 = 罪恶王冠（片长 200s）；5 分钟在主番片长内
        checks = self._mk(tmp_path, monkeypatch, ["S01E01 03:00"], self.SRC)
        assert checks["锚点指向素材"].ok

    def test_集号按锚点番名分派校验(self, tmp_path, monkeypatch):
        # 集字段 S02E01 只存在于心理测量者；段锚点指了它就不该按主番报缺集
        src = {**self.SRC, "心理测量者": {**self.SRC["心理测量者"],
                                          "S02E01": {"path": "/pp2.mkv", "duration": 300.0}}}
        ep = tmp_path / "ep"
        ep.mkdir()
        (ep / "01-topic.md").write_text(
            "番: EGOIST\n素材番剧:\n  - 罪恶王冠\n  - 心理测量者\n", encoding="utf-8")
        script = ep / "02-script.md"
        script.write_text("## 段落 1\n\n配音：话。\n\n画面：\n  集: S02E01\n  锚点: 心理测量者 S02E01 01:00\n",
                          encoding="utf-8")
        from pipeline import ingest as ing
        monkeypatch.setattr(ing, "load_sources", lambda a: src[a])
        checks = {c.name: c for c in check_script.run(script)}
        assert checks["集号在素材库"].ok, checks["集号在素材库"].detail
        assert checks["锚点指向素材"].ok


# ---------------------------------------------------------------- BGM 跨池回查

class TestResolveMulti:
    TABLES = {"EGOIST": {"tracks": {"曲A": {"path": "data/library/bgm/e/曲A.flac",
                                          "lufs": -15.0}}},
              "罪恶王冠": {"tracks": {"曲B": {"path": "data/library/bgm/g/曲B.flac",
                                            "lufs": -12.0}}}}

    def _mk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bgm, "load", lambda a: self.TABLES.get(a, {}))
        monkeypatch.setattr(bgm.paths, "ROOT", tmp_path)
        for pool in ("e", "g"):
            (tmp_path / f"data/library/bgm/{pool}").mkdir(parents=True)
        (tmp_path / "data/library/bgm/e/曲A.flac").write_bytes(b"x")
        (tmp_path / "data/library/bgm/g/曲B.flac").write_bytes(b"x")

    def test_曲目落在后面的池也能取到(self, monkeypatch, tmp_path):
        self._mk(monkeypatch, tmp_path)
        got = bgm.resolve(["EGOIST", "罪恶王冠"], "列表", "曲B")
        assert got["name"] == "曲B" and got["lufs"] == -12.0

    def test_都没有时报错列出已查池(self, monkeypatch, tmp_path):
        self._mk(monkeypatch, tmp_path)
        with pytest.raises(SystemExit, match="EGOIST、罪恶王冠"):
            bgm.resolve(["EGOIST", "罪恶王冠"], "列表", "曲C")

    def test_单池字符串行为不变(self, monkeypatch, tmp_path):
        self._mk(monkeypatch, tmp_path)
        assert bgm.resolve("EGOIST", "列表", "曲A")["name"] == "曲A"


# ---------------------------------------------------------------- refit 跨番

class TestRefitMulti:
    def test_末片按所属番取伸缩边界(self):
        segments = [{"index": 1, "status": "ok", "clips": [
            {"anime": "番B", "season": 1, "episode": 1,
             "source": "b.mkv", "start": 10.0, "dur": 4.0}]}]
        audio = [{"index": 1, "duration": 6.0}]
        # 番A 的 S01E01 片长 5s（拉不到 6s），番B 的 100s（拉得到）——
        # 查错表会误判 refit 不了，复合键才对
        sources = {("番A", "S01E01"): {"path": "a.mkv", "duration": 5.0},
                   ("番B", "S01E01"): {"path": "b.mkv", "duration": 100.0}}
        out, report = align.refit(segments, audio, sources)
        assert out[0]["clips"][-1]["dur"] == pytest.approx(6.0)
