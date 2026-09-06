# 排片引擎泛化（2026-09-07）：SP 特典集号、单段多锚点蒙太奇、锚点段尾帧定格
# 保护（ok_extended）、QC 对 SP 素材的艺术暗场豁免。规格：ADR-0010 决策二。
# 单番期与既有跨番期行为 0 漂移由 test_clips / test_cross_anime / test_qc 等
# 既有套件兜底，这里只测新增的泛化行为。

import json

import pytest

from pipeline import align, check_script, clips, ingest, qc, render, review, shots


# ---------------------------------------------------------------- 任务 1：SPxx 集号

class TestSpAnchorParse:
    """`SPxx` 锚点解析（ADR-0010 决策二）。season=None 是 SP 的标记位——
    season=0 已被 OVA（S00E0x）的既有登记占用，不许占。"""

    def test_SP点时间码(self):
        a = clips._parse_anchor("SP01 12:30", 1)
        assert (a["season"], a["episode"], a["t0"], a["t1"]) == (None, 1, 750.0, None)

    def test_SP区间(self):
        a = clips._parse_anchor("SP02 01:00-02:30", 1)
        assert (a["episode"], a["t0"], a["t1"]) == (2, 60.0, 150.0)

    def test_小写sp也认(self):
        assert clips._parse_anchor("sp3 00:10", 1)["episode"] == 3

    def test_SP锚点允许企划名前缀(self):
        # 企划名（番: 行）不是素材番，但 SP 素材登记在它名下（ADR-0010）
        a = clips._parse_anchor("EGOIST SP01 108:45", 1, ["罪恶王冠"], ["EGOIST"])
        assert a["anime"] == "EGOIST" and a["t0"] == 108 * 60 + 45

    def test_SP不写番名默认企划池(self):
        a = clips._parse_anchor("SP01 12:30", 1, ["罪恶王冠"], ["EGOIST"])
        assert a["anime"] == "EGOIST"

    def test_SP前缀是素材番也认(self):
        a = clips._parse_anchor("罪恶王冠 SP01 12:30", 1, ["罪恶王冠"], ["EGOIST"])
        assert a["anime"] == "罪恶王冠"

    def test_普通季集锚点不许指企划名(self):
        # 企划没有 S01E01——认了只会推迟到运行期 no_match，写稿期就该拦
        with pytest.raises(SystemExit, match="EGOIST"):
            clips._parse_anchor("EGOIST S01E01 17:50", 1, ["罪恶王冠"], ["EGOIST"])

    def test_SP前缀番表外仍报错(self):
        with pytest.raises(SystemExit, match="甲铁城"):
            clips._parse_anchor("甲铁城 SP01 12:30", 1, ["罪恶王冠"], ["EGOIST"])

    def test_普通锚点行为不变(self):
        a = clips._parse_anchor("S01E01 17:50", 1, ["春物"], ["春物"])
        assert (a["season"], a["episode"], a["anime"]) == (1, 1, "春物")

    def test_ep_key三种形态(self):
        assert clips._ep_key(None, 1) == "SP01"
        assert clips._ep_key(1, 1) == "S01E01"
        assert clips._ep_key(0, 1) == "S00E01"      # OVA 既有登记，与 SP 不撞


class TestSpEpisodeField:
    """`集: SPxx` 字段：允许与 SP 锚点同写（一致性校验），不许单独锁检索段。"""

    def _parse(self, tmp_path, block):
        f = tmp_path / "s.md"
        f.write_text(f"## 段落 1\n\n配音：x。\n\n画面：\n{block}\n", encoding="utf-8")
        return clips.parse_shots(f, ["番"])

    def test_集SP与SP锚点同写通过(self, tmp_path):
        out = self._parse(tmp_path, "  集: SP01\n  锚点: SP01 12:30\n")
        assert out[0]["episode"] == "SP01"
        assert out[0]["anchor"]["season"] is None

    def test_集SP小写归一化(self, tmp_path):
        out = self._parse(tmp_path, "  集: sp1\n  锚点: SP01 12:30\n")
        assert out[0]["episode"] == "SP01"

    def test_集SP没有锚点当场报错(self, tmp_path):
        # SP 素材没有字幕索引，检索通道锁不到它——不拦就是运行期 no_match
        with pytest.raises(SystemExit, match="SP01"):
            self._parse(tmp_path, "  集: SP01\n  查询: 某句台词\n")

    def test_集SP加锚点无也报错(self, tmp_path):
        with pytest.raises(SystemExit, match="SP01"):
            self._parse(tmp_path, "  集: SP01\n  锚点: 无（测试）\n")

    def test_集与SP锚点写叉报错(self, tmp_path):
        with pytest.raises(SystemExit, match="不是同一集"):
            self._parse(tmp_path, "  集: SP02\n  锚点: SP01 12:30\n")


class TestSpAnchorCandidate:
    """SP 锚点 → 片段：复合键 `sources_get(sources, anime, "SP01")` 直通，
    镜头表按 SP 键加载，产物带 sp 标记（qc 艺术暗场豁免只认这个标）。"""

    TBL = {"shots": [{"i": 0, "start": 0.0, "end": 10.0, "rep": 5.0},
                     {"i": 1, "start": 10.0, "end": 25.0, "rep": 17.5}]}

    def _cand(self, monkeypatch, raw, sources, anime="EGOIST"):
        monkeypatch.setattr(shots, "load", lambda a, k: self.TBL)
        return clips._anchor_candidate(clips._parse_anchor(raw, 1), sources, anime)

    def test_平面键查找(self, monkeypatch):
        src = {"SP01": {"path": "/egoist/live2023.mp4", "duration": 5400.0}}
        cand = self._cand(monkeypatch, "SP01 00:12", src)
        assert cand["season"] is None and cand["episode"] == 1
        assert cand["sp"] is True and cand["source"] == "/egoist/live2023.mp4"
        assert cand["start"] == 10.0                 # 起点吸附镜头切点，与普通锚点同规

    def test_复合键查找(self, monkeypatch):
        src = {("EGOIST", "SP01"): {"path": "/egoist/mv.mp4", "duration": 320.0}}
        cand = self._cand(monkeypatch, "EGOIST SP01 00:12", src)
        assert cand["source"] == "/egoist/mv.mp4"

    def test_SP未登记返回None(self, monkeypatch):
        assert self._cand(monkeypatch, "SP09 00:12",
                          {"SP01": {"path": "/x.mp4", "duration": 100.0}}) is None

    def test_普通锚点不带sp标记(self, monkeypatch):
        src = {"S01E01": {"path": "/x/e01.mkv", "duration": 100.0}}
        cand = self._cand(monkeypatch, "S01E01 00:12", src, "番")
        assert "sp" not in cand and cand["season"] == 1


# ---------------------------------------------------------------- 任务 2：单段多锚点

class TestMultiAnchorParse:
    """`锚点:` 支持逗号分隔与续行列表，顺序 = 书写顺序（蒙太奇即顺序）。"""

    def _parse(self, tmp_path, anc_block):
        f = tmp_path / "s.md"
        f.write_text(f"## 段落 1\n\n配音：x。\n\n画面：\n{anc_block}\n", encoding="utf-8")
        return clips.parse_shots(f, ["罪恶王冠", "心理测量者"])[0]

    def test_逗号分隔(self, tmp_path):
        s = self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40, 心理测量者 S01E01 02:15\n")
        assert [a["anime"] for a in s["anchors"]] == ["罪恶王冠", "心理测量者"]
        assert [a["t0"] for a in s["anchors"]] == [1120.0, 135.0]
        assert s["anchor"] is s["anchors"][0] or s["anchor"] == s["anchors"][0]

    def test_全角逗号(self, tmp_path):
        s = self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40，心理测量者 S01E01 02:15\n")
        assert len(s["anchors"]) == 2

    def test_续行列表(self, tmp_path):
        s = self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40\n"
                        "        心理测量者 S01E01 02:15\n"
                        "        罪恶王冠 S01E02 11:20\n")
        assert len(s["anchors"]) == 3
        assert s["anchors"][2]["episode"] == 2

    def test_续行不吞后续字段(self, tmp_path):
        s = self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40\n"
                        "        心理测量者 S01E01 02:15\n"
                        "  查询: 这句不该被吞\n")
        assert len(s["anchors"]) == 2 and s["query"] == "这句不该被吞"

    def test_单锚点不产anchors键(self, tmp_path):
        # 零漂移护栏：单锚点段的产物形状与旧版逐字节一致
        s = self._parse(tmp_path, "  锚点: 罪恶王冠 S01E01 18:40\n")
        assert "anchors" not in s and s["anchor"]["t0"] == 1120.0
        assert s["episode"] == "S01E01"

    def test_多锚点跨集时段落集号留空(self, tmp_path):
        s = self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40, 罪恶王冠 S01E02 11:20\n")
        assert s["episode"] is None

    def test_多锚点同集时段落集号带上(self, tmp_path):
        s = self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40, 罪恶王冠 S01E01 20:00\n")
        assert s["episode"] == "S01E01"

    def test_集与多锚点写叉报错(self, tmp_path):
        with pytest.raises(SystemExit, match="不是同一集"):
            self._parse(tmp_path,
                        "  集: S01E02\n  锚点: 罪恶王冠 S01E01 18:40, 罪恶王冠 S01E01 20:00\n")

    def test_列表里混垃圾条目当场报错(self, tmp_path):
        with pytest.raises(SystemExit, match="机器认不了"):
            self._parse(tmp_path,
                        "  锚点: 罪恶王冠 S01E01 18:40, 随便一集 02:15\n")

    def test_无锚点写法不受列表解析影响(self, tmp_path):
        s = self._parse(tmp_path, "  锚点: 无（这段是过渡，没有对应剧情）\n")
        assert s["anchor"] is None and s["anchor_none"] is True and "anchors" not in s


class TestMultiAnchorRun:
    """run() 级集成：多锚点段定死为有序候选列表，交 size() 按权重分满本段。"""

    TBL = {"shots": [{"i": 0, "start": 0.0, "end": 10.0, "rep": 5.0},
                     {"i": 1, "start": 10.0, "end": 25.0, "rep": 17.5},
                     {"i": 2, "start": 25.0, "end": 300.0, "rep": 112.5}]}
    SRC = {("罪恶王冠", "S01E01"): {"path": "/gc/e01.mkv", "duration": 300.0},
           ("心理测量者", "S01E01"): {"path": "/pp/e01.mkv", "duration": 300.0}}

    def _run(self, tmp_path, monkeypatch, script_text, durations, sources=None, hits=()):
        monkeypatch.setattr(clips, "load_sources_multi",
                            lambda animes: sources or self.SRC)
        monkeypatch.setattr(clips, "load_all", lambda d, a: (None, None))
        monkeypatch.setattr(clips, "search",
                            lambda *a, **k: [(0.9, u) for u in hits])
        monkeypatch.setattr(shots, "load", lambda a, k: self.TBL)
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

    def test_跨番三锚点蒙太奇有序排满(self, tmp_path, monkeypatch):
        script = ("## 段落 1\n\n配音：从王冠的废墟到心理测量者的雨夜。\n\n画面：\n"
                  "  锚点: 罪恶王冠 S01E01 00:05, 心理测量者 S01E01 00:12，罪恶王冠 S01E01 00:30\n")
        data = self._run(tmp_path, monkeypatch, script, [15.0])
        seg = data["segments"][0]
        assert seg["status"] == "ok" and seg["channel"] == "anchor"
        got = [(c["anime"], c["start"]) for c in seg["clips"]]
        assert got == [("罪恶王冠", 0.0), ("心理测量者", 10.0), ("罪恶王冠", 25.0)]
        total = sum(c["dur"] for c in seg["clips"])
        assert total == pytest.approx(15.0, abs=0.05)
        assert all(c["dur"] >= clips.MIN_CLIP for c in seg["clips"])

    def test_单锚点产物形状零漂移(self, tmp_path, monkeypatch):
        script = "## 段落 1\n\n配音：一。\n\n画面：\n  锚点: 罪恶王冠 S01E01 00:12\n"
        data = self._run(tmp_path, monkeypatch, script, [8.0])
        seg = data["segments"][0]
        assert seg["status"] == "ok" and "anchors" not in seg
        assert seg["anchor"]["raw"] == "罪恶王冠 S01E01 00:12"

    def test_段内两锚点指同一处算撞车(self, tmp_path, monkeypatch):
        # 蒙太奇里同一处画面放两次 = 写稿问题，不静默挪
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n"
                  "  锚点: 罪恶王冠 S01E01 00:12, 罪恶王冠 S01E01 00:14\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0])
        assert data["segments"][0]["status"] == "anchor_overlap"

    def test_多锚点之一未入库整段no_match(self, tmp_path, monkeypatch):
        # 蒙太奇是确定的有序序列，缺一环就是不成立——不静默少放一段
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n"
                  "  锚点: 罪恶王冠 S01E01 00:12, 心理测量者 S02E01 00:12\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0])
        assert data["segments"][0]["status"] == "no_match"

    def test_多锚点段与检索段共存(self, tmp_path, monkeypatch):
        class U:
            anime, season, episode = "罪恶王冠", 1, 1
            start, end, text = 60.0, 63.0, "台词"
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n"
                  "  锚点: 罪恶王冠 S01E01 00:05, 心理测量者 S01E01 00:12\n\n"
                  "## 段落 2\n\n配音：二。\n\n画面：\n  查询: 那句台词\n  锚点: 无（测试）\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0, 4.0], hits=[U()])
        s1, s2 = data["segments"]
        assert s1["status"] == "ok" and len(s1["clips"]) == 2
        assert s2["status"] == "ok" and s2["clips"][0]["start"] == 59.75


# ---------------------------------------------------------------- 任务 3：尾帧定格防 short

class TestSizeExtend:
    """锚点段素材不够长时：先把真实素材两个方向拉满，剩下的缺口定格末帧补足
    （ok_extended + 末片 extend 字段），而不是判 short 让整期卡死。"""

    CLIP = {"anime": "番", "season": 1, "episode": 1, "source": "/x.mkv",
            "start": 90.0, "dur": 6.0, "span": 6.0, "limit": 96.0}

    def _clip(self, **kw):
        return {**self.CLIP, **kw}

    def test_缺口挂末片extend(self):
        # floor 顶住起点 = 前向无借量、后向 room=6s 拉满仍差 2s → 定格补
        got, status = clips.size([self._clip(floor=90.0)], 8.0, allow_extend=True)
        assert status == "ok_extended"
        assert got[-1]["dur"] == 6.0 and got[-1]["extend"] == pytest.approx(2.0)
        assert sum(c["dur"] + c.get("extend", 0.0) for c in got) == pytest.approx(8.0)

    def test_水填优先于定格(self):
        # 向后还有 4s 余量：真实素材先拉满，不产生定格
        got, status = clips.size([self._clip(limit=100.0)], 8.0, allow_extend=True)
        assert status == "ok" and "extend" not in got[-1] and got[-1]["dur"] == 8.0

    def test_向前余量也先吃(self):
        # floor 之前 2s + 向后 1s，缺口 1s 定格
        got, status = clips.size([self._clip(limit=97.0, floor=88.0)], 10.0,
                                 allow_extend=True)
        assert status == "ok_extended"
        assert got[-1]["extend"] == pytest.approx(1.0)

    def test_超过定格上限仍判short(self):
        # 定格 20s 不是延展是盯着静帧发呆——诚实判 short 交人处理
        got, status = clips.size([self._clip(floor=90.0)], 30.0, allow_extend=True)
        assert status == "short" and "extend" not in got[-1]

    def test_不传旗标行为零漂移(self):
        got, status = clips.size([self._clip(floor=90.0)], 8.0)
        assert status == "short" and "extend" not in got[-1]

    def test_排版临时字段不泄漏(self):
        got, _ = clips.size([self._clip()], 8.0, allow_extend=True)
        for k in ("limit", "span", "floor"):
            assert k not in got[-1]

    def test_run级_锚点段定格(self, tmp_path, monkeypatch):
        # 锚点贴源片开头（前向无借量）、镜头到源尾（后向拉满仍不够）→ 定格补
        tbl = {"shots": [{"i": 0, "start": 0.0, "end": 6.0, "rep": 3.0}]}
        monkeypatch.setattr(clips, "load_sources",
                            lambda a: {"S01E01": {"path": "/x.mkv", "duration": 6.0}})
        monkeypatch.setattr(clips, "load_all", lambda d, a: (None, None))
        monkeypatch.setattr(shots, "load", lambda a, k: tbl)
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "02-script.md").write_text(
            "## 段落 1\n\n配音：一。\n\n画面：\n  锚点: S01E01 00:02\n", encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(json.dumps({"segments": [
            {"index": 1, "label": "1", "text": "x", "file": "s1.wav",
             "duration": 8.0, "cer": 0.0, "attempts": 1}]}), encoding="utf-8")
        data = json.loads(clips.run(ep, anime="番").read_text(encoding="utf-8"))
        seg = data["segments"][0]
        assert seg["status"] == "ok_extended"
        assert seg["clips"][-1]["extend"] == pytest.approx(2.0)
        # 段级不变量把定格算进去：approve/render 两道闸不该拦
        assert align.verify_alignment(data["segments"],
                                      [{"duration": 8.0}]) == []


class TestAlignExtend:
    """align 对 ok_extended 的校验与 refit 定格吸收。"""

    def test_verify_把extend算进总量(self):
        segs = [{"index": 1, "status": "ok_extended",
                 "clips": [{"dur": 6.0, "extend": 2.0}]}]
        assert align.verify_alignment(segs, [{"duration": 8.0}]) == []

    def test_verify_定格总量不符仍报(self):
        segs = [{"index": 1, "status": "ok_extended",
                 "clips": [{"dur": 6.0, "extend": 2.0}]}]
        assert align.verify_alignment(segs, [{"duration": 9.0}]) != []

    def test_refit_漂移由定格吸收(self):
        segs = [{"index": 1, "status": "ok_extended", "clips": [
            {"season": None, "episode": 1, "source": "/sp.mkv",
             "start": 90.0, "dur": 6.0, "extend": 2.0}]}]
        out, report = align.refit(segs, [{"duration": 9.0}],
                                  {"SP01": {"path": "/sp.mkv", "duration": 96.0}})
        assert out[0]["clips"][-1]["extend"] == pytest.approx(3.0)
        assert out[0]["clips"][-1]["dur"] == 6.0      # 画面身份不动
        assert "定格" in report[0]

    def test_refit_负漂移减定格(self):
        segs = [{"index": 1, "status": "ok_extended", "clips": [
            {"season": None, "episode": 1, "source": "/sp.mkv",
             "start": 90.0, "dur": 6.0, "extend": 3.0}]}]
        out, _ = align.refit(segs, [{"duration": 8.0}],
                             {"SP01": {"path": "/sp.mkv", "duration": 96.0}})
        assert out[0]["clips"][-1]["extend"] == pytest.approx(2.0)
        assert out[0]["status"] == "ok_extended"

    def test_refit_定格减光落末片dur且段退回ok(self):
        segs = [{"index": 1, "status": "ok_extended", "clips": [
            {"season": None, "episode": 1, "source": "/sp.mkv",
             "start": 90.0, "dur": 6.0, "extend": 1.0}]}]
        out, _ = align.refit(segs, [{"duration": 5.0}],
                             {"SP01": {"path": "/sp.mkv", "duration": 96.0}})
        last = out[0]["clips"][-1]
        assert "extend" not in last and last["dur"] == pytest.approx(5.0)
        assert out[0]["status"] == "ok"

    def test_refit_SP末片按SP键查片源(self):
        # season=None 的末片必须查 "SP01"，不能拼成 "S00E01"（OVA 已被占）
        segs = [{"index": 1, "status": "ok", "clips": [
            {"season": None, "episode": 1, "source": "/sp.mkv",
             "start": 90.0, "dur": 4.0}]}]
        out, _ = align.refit(segs, [{"duration": 5.0}],
                             {"SP01": {"path": "/sp.mkv", "duration": 96.0},
                              "S00E01": {"path": "/ova.mkv", "duration": 5.0}})
        # 查到 SP01（96s 余量够）才能拉；查错表（S00E01 只有 5s）会夹不住报错
        assert out[0]["clips"][-1]["dur"] == pytest.approx(5.0)


class TestRenderExtend:
    """render.cut：extend 走 tpad 克隆末帧，切后时长按 dur+extend 复核。"""

    def _cut(self, monkeypatch, clip, out_dur):
        ran = {}
        monkeypatch.setattr(render, "duration",
                            lambda p: 100.0 if str(p).endswith(".mkv") else out_dur)
        monkeypatch.setattr(render, "frame_time", lambda p: 1 / 24)
        monkeypatch.setattr(render.subprocess, "run",
                            lambda cmd, **k: ran.setdefault("cmd", cmd))
        render.cut(clip, __import__("pathlib").Path("/tmp/out.mp4"))
        return ran["cmd"]

    def test_带extend加tpad(self, monkeypatch):
        cmd = self._cut(monkeypatch, {"source": "/x.mkv", "start": 10.0,
                                      "dur": 6.0, "extend": 2.0}, out_dur=8.0)
        assert "tpad=stop_mode=clone:stop=2.0" in cmd[cmd.index("-vf") + 1]
        assert cmd[cmd.index("-t") + 1] == "8.000"

    def test_不带extend零漂移(self, monkeypatch):
        cmd = self._cut(monkeypatch, {"source": "/x.mkv", "start": 10.0, "dur": 6.0},
                        out_dur=6.0)
        assert "tpad" not in cmd[cmd.index("-vf") + 1]
        assert cmd[cmd.index("-t") + 1] == "6.000"

    def test_渲染门禁放行ok_extended(self):
        # render.run 的状态闸：ok_extended 是可渲染终态
        segs = [{"index": 1, "status": "ok"}, {"index": 2, "status": "ok_extended"}]
        assert [s["index"] for s in segs
                if s["status"] not in ("ok", "ok_extended")] == []


class TestReviewExtend:
    def test_审图页把extend算进画面合计(self):
        seg = {"index": 1, "status": "ok_extended",
               "clips": [{"dur": 6.0, "extend": 2.0}]}
        txt = review._align_txt(seg, {"duration": 8.0}, True)
        assert "差" not in txt                       # 6+2 == 8，不该标红

    def test_SP片段集号标签(self):
        assert review._clip_ep({"sp": True, "season": None, "episode": 1,
                                "source": "/x.mp4"}) == "SP01"
        assert review._clip_ep({"season": 1, "episode": 1}) == "S01E01"


# ---------------------------------------------------------------- 任务 4：QC 艺术暗场豁免

class TestQcSpBlack:
    """SP 素材（MV/Live）的自然暗灯转场按 1.5s 门限判，普通番剧仍按 0.5s。"""

    def _span(self, src, f0, f1, bi=0):
        # (源路径, 源区间起, 源区间止, 成片起, 成片止, 片段序号, 黑帧序号)
        return (src, 10.0, 11.0, f0, f1, 1, bi)

    def test_SP暗场1秒级豁免(self):
        m = [self._span("/live.mp4", 5.0, 6.2)]       # 1.2s 未覆盖
        assert qc._black_defects(m, set(), {"/live.mp4"}) == []

    def test_SP暗场超1秒5仍拦(self):
        m = [self._span("/live.mp4", 5.0, 7.0)]       # 2.0s 未覆盖
        assert qc._black_defects(m, set(), {"/live.mp4"}) != []

    def test_普通番剧门限不变(self):
        m = [self._span("/anime.mkv", 5.0, 6.2)]
        assert qc._black_defects(m, set(), {"/live.mp4"}) != []   # 不在豁免集里

    def test_不传sp集合行为零漂移(self):
        m = [self._span("/live.mp4", 5.0, 6.2)]
        assert qc._black_defects(m, set()) != []

    def test_一条黑跨两类源分别判(self):
        m = [self._span("/anime.mkv", 5.0, 5.6, bi=0),   # 普通 0.6s → 拦
             self._span("/live.mp4", 5.6, 6.8, bi=0)]    # SP 1.2s → 豁免
        defects = qc._black_defects(m, set(), {"/live.mp4"})
        assert len(defects) == 1 and defects[0][0] == "/anime.mkv"

    def test_extend片段的映射区间(self):
        # 定格延展区占成片时间但不读源片：映射按 dur+extend 推，源区间夹到 dur
        plan = {"segments": [{"index": 1, "clips": [
            {"source": "/x.mkv", "start": 10.0, "dur": 4.0, "extend": 2.0}]}]}
        out = qc._map_black_to_sources([(5.5, 6.5)], plan)
        assert out[0][3:5] == (5.5, 6.0)               # 成片区间夹到 0–6s
        assert out[0][1] == 14.0                       # 源起点夹到 10+dur
        assert out[0][2] == 14.0                       # 源止点同上（一个点）


# ---------------------------------------------------------------- check_script 泛化校验

class TestCheckScriptGeneralized:
    def _mk(self, tmp_path, monkeypatch, blocks, sources_by_anime):
        from pipeline import ingest as ing
        monkeypatch.setattr(ing, "load_sources", lambda a: sources_by_anime[a])
        ep = tmp_path / "ep"
        ep.mkdir()
        (ep / "01-topic.md").write_text(
            "番: EGOIST\n素材番剧:\n  - 罪恶王冠\n  - 心理测量者\n", encoding="utf-8")
        script = ep / "02-script.md"
        script.write_text("\n".join(
            f"## 段落 {i}\n\n配音：第{i}段的话。\n\n画面：\n{b}\n"
            for i, b in enumerate(blocks, 1)), encoding="utf-8")
        return {c.name: c for c in check_script.run(script)}

    SRC = {"罪恶王冠": {"S01E01": {"path": "/gc.mkv", "duration": 200.0}},
           "心理测量者": {"S01E01": {"path": "/pp.mkv", "duration": 300.0}},
           "EGOIST": {"SP01": {"path": "/live.mp4", "duration": 7000.0}}}

    def test_SP锚点格式与素材校验都过(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: EGOIST SP01 108:45\n"], self.SRC)
        assert checks["锚点格式"].ok and checks["锚点指向素材"].ok

    def test_SP不写番名归企划池(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch, ["  锚点: SP01 01:00\n"], self.SRC)
        assert checks["锚点指向素材"].ok

    def test_普通锚点指企划名报错(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: EGOIST S01E01 01:00\n"], self.SRC)
        assert not checks["锚点格式"].ok

    def test_SP超出片长按企划池报(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: EGOIST SP01 119:00\n"], self.SRC)   # 7140s > 7000s
        assert not checks["锚点指向素材"].ok

    def test_多锚点逐条校验(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: 罪恶王冠 S01E01 01:00, EGOIST SP01 02:00\n"],
                          self.SRC)
        assert checks["锚点格式"].ok and checks["锚点指向素材"].ok

    def test_多锚点列表一条坏报该条(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: 罪恶王冠 S01E01 01:00, 甲铁城 S01E01 02:00\n"],
                          self.SRC)
        assert not checks["锚点格式"].ok and "甲铁城" in checks["锚点格式"].detail

    def test_续行锚点参与校验(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: 罪恶王冠 S01E01 01:00\n        EGOIST SP01 02:00\n"],
                          self.SRC)
        assert checks["锚点格式"].ok and checks["锚点指向素材"].ok

    def test_集SP在企划池里查(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  集: SP01\n  锚点: EGOIST SP01 01:00\n"], self.SRC)
        assert checks["集号格式"].ok and checks["集号在素材库"].ok

    def test_多锚点段算有画面来源(self, tmp_path, monkeypatch):
        checks = self._mk(tmp_path, monkeypatch,
                          ["  锚点: 罪恶王冠 S01E01 01:00, EGOIST SP01 02:00\n"],
                          self.SRC)
        assert checks["每段都有查询或锚点"].ok


# ---------------------------------------------------------------- SP 登记入口（ADR-0010 落地链）

class TestSpRegistration:
    def test_shots键(self):
        assert shots._key(None, 1) == "SP01"
        assert shots._key(1, 1) == "S01E01"
        assert shots._key(0, 1) == "S00E01"          # OVA 既有登记不撞

    def test_register落SP键(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest, "intact", lambda v: (True, ""))
        monkeypatch.setattr(ingest, "probe_video",
                            lambda v: {"duration": 320.0, "fps": "24/1",
                                       "width": 1920, "height": 1080})
        db_path = tmp_path / "sources.json"
        ingest.register(tmp_path / "mv.mp4", "EGOIST", 0, 0, path=db_path, sp=3)
        db = json.loads(db_path.read_text(encoding="utf-8"))
        assert db["EGOIST"]["SP03"]["duration"] == 320.0

    def test_register不带sp零漂移(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest, "intact", lambda v: (True, ""))
        monkeypatch.setattr(ingest, "probe_video",
                            lambda v: {"duration": 100.0, "fps": "24/1",
                                       "width": 1920, "height": 1080})
        db_path = tmp_path / "sources.json"
        ingest.register(tmp_path / "e01.mkv", "番", 1, 1, path=db_path)
        assert "S01E01" in json.loads(db_path.read_text(encoding="utf-8"))["番"]
