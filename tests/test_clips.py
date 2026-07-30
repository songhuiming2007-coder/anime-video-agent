"""排片：时长水填、重叠判定、稿件解析、跨番过滤。

`size()` 是这个仓库里改得最多的函数——四版：只裁末尾 → 等比例缩 → 水填 →
再加往前拉。每一版都是靠跑真实数据才发现问题的，所以这里把每一版踩过的坑
都固化成一条断言，避免第五版重新踩。

**期望值全部先在实现上跑过再写进来。** 写这个文件时有一条我一开始想错了：
以为「起点 1395、片长上限 1400、要 20 秒」必然 short，实际它会往前拉到 1380–1400，
返回 ok——代码是对的，我的期望是错的。先探后写。
"""

import pytest

from pipeline import clips as c


def mk(start, dur, limit, *, span=None, floor=None, season=2, episode=2):
    d = {"start": start, "dur": dur, "limit": limit, "span": span or dur,
         "season": season, "episode": episode}
    if floor is not None:
        d["floor"] = floor
    return d


class TestSize:
    def test_空片段列表(self):
        assert c.size([], 10.0) == ([], "no_source")

    def test_总时长精确等于需求(self):
        out, st = c.size([mk(10, 8.0, 1400), mk(50, 3.0, 1400), mk(90, 2.5, 1400)], 9.3)
        assert st == "ok"
        assert abs(sum(x["dur"] for x in out) - 9.3) < 0.01

    def test_没有片段被压到_MIN_CLIP_以下(self):
        # 这是水填替换等比例缩的唯一理由。等比例保的是相对长短，保不住下限：
        # [8.0, 3.0, 2.5] 只需要 9.3s，乘 0.689 会把末尾压到 1.72s。
        out, st = c.size([mk(10, 8.0, 1400), mk(50, 3.0, 1400), mk(90, 2.5, 1400)], 9.3)
        assert all(x["dur"] >= c.MIN_CLIP for x in out)

    def test_需求装不下就砍片段数而不是压扁(self):
        # need=6 除以 MIN_CLIP=2.5 只放得下 2 个，第三个直接不要。
        # hits 已按分数降序，截断即保留最强的。
        out, _ = c.size([mk(10, 3, 1400), mk(50, 3, 1400), mk(90, 3, 1400)], 6.0)
        assert len(out) == 2
        assert all(x["dur"] >= c.MIN_CLIP for x in out)

    def test_单片段可以拉长填满(self):
        # 凑不满时拉长已选片段，不要退而求其次拿弱命中填——
        # 同一场戏多放两秒仍然对题，换个不相干的镜头就不对题了。
        out, st = c.size([mk(10, 3.0, 1400)], 12.0)
        assert st == "ok" and out[0]["dur"] == 12.0

    def test_向后堵死时往前拉(self):
        # 段 11 踩过：向后被段 12 挡住差 0.96s，而它前面有 3.5 秒没人用的画面。
        # 只往后拉是不必要的限制，提前起切在剪辑上完全正常。
        out, st = c.size([mk(1395, 2.6, 1400)], 20.0)
        assert st == "ok"
        assert out[0]["start"] < 1395            # 确实往前挪了
        assert out[0]["start"] + out[0]["dur"] <= 1400 + 0.01   # 没越过片尾

    def test_前后都堵死才判_short(self):
        out, st = c.size([mk(1395, 2.6, 1400, floor=1393)], 20.0)
        assert st == "short"

    def test_临时字段不进产物(self):
        # limit/span/floor 是排版中间量，落进 03-clips.json 会误导下游
        out, _ = c.size([mk(10, 8.0, 1400, floor=5.0)], 9.3)
        for k in ("limit", "span", "floor"):
            assert k not in out[0]


class TestLadder:
    """查询阶梯：`查询` → `备选` → `配音`，**只救不比**。

    这是本文件里最容易被后人改坏的地方，因为「三种问法取最高分」看起来明显更好：
    2026-07-30 实测 21 段，配音原文在 11 段上分数最高，查询字段只在 8 段最高。

    但分数量的是「旁白与字幕的语义相似度」，不是「这个镜头是否呈现旁白讲的那一刻」。
    取最优会毁掉写成逐字引语的段落（段 17：查询 0.855 → 配音 0.559），
    也会引入说话人偏置（段 4 配音「雪之下雪乃。」得 0.750，
    可那个镜头拍的是**说出她名字的人**）。
    """

    def _shot(self, query="查询语", alt="备选语", text="配音正文。"):
        return {"index": 1, "query": query, "alt": alt, "text": text}

    def _fake(self, scores):
        """按查询文本返回预设分数。scores: {查询文本: top1 分数}"""
        calls = []

        def search(q, vecs, units, k):
            calls.append(q)
            sc = scores.get(q)
            return [(sc, object())] if sc is not None else []
        return search, calls

    def _run(self, shot, scores, monkeypatch):
        search, calls = self._fake(scores)
        monkeypatch.setattr(c, "search", search)
        hits, used, rung = c._ladder(shot, None, None)
        return used, rung, calls

    def test_第一级够格就不往下试(self, monkeypatch):
        # 关键：**根本不去查后面两级**。查了再比就已经错了。
        used, rung, calls = self._run(
            self._shot(), {"查询语": 0.50, "备选语": 0.99, "配音正文。": 0.99}, monkeypatch)
        assert (used, rung) == ("查询语", 1)
        assert calls == ["查询语"]

    def test_第一级不够才用备选(self, monkeypatch):
        used, rung, _ = self._run(
            self._shot(), {"查询语": 0.30, "备选语": 0.50}, monkeypatch)
        assert (used, rung) == ("备选语", 2)

    def test_前两级都不够才用配音原文(self, monkeypatch):
        used, rung, calls = self._run(
            self._shot(), {"查询语": 0.30, "备选语": 0.20, "配音正文。": 0.60}, monkeypatch)
        assert (used, rung) == ("配音正文。", 3)
        assert calls == ["查询语", "备选语", "配音正文。"]

    def test_备选留空就跳到配音(self, monkeypatch):
        used, rung, calls = self._run(
            self._shot(alt=None), {"查询语": 0.30, "配音正文。": 0.60}, monkeypatch)
        assert (used, rung) == ("配音正文。", 3)
        assert "备选语" not in calls

    def test_门槛不随级数放松(self, monkeypatch):
        # 加大的是找的力度，不是放低的标准。三级全在门槛下 → 一级都不算成功。
        used, rung, _ = self._run(
            self._shot(), {"查询语": 0.30, "备选语": 0.35, "配音正文。": 0.44}, monkeypatch)
        assert rung == 3 and used == "配音正文。"   # 报最高分那次

    def test_全不够格时报最高分那次而不是最后一次(self, monkeypatch):
        # 报最后一次只是碰巧的顺序；报最高分才说明「差多少」。
        used, _, _ = self._run(
            self._shot(), {"查询语": 0.44, "备选语": 0.10, "配音正文。": 0.20}, monkeypatch)
        assert used == "查询语"

    def test_一条都没命中不报错(self, monkeypatch):
        hits_used, rung, _ = self._run(self._shot(), {}, monkeypatch)
        assert rung == 1


class TestOverlaps:
    def test_同集时间相近算同一处画面(self):
        assert c._overlaps(mk(101, 3, 1400), [mk(100, 3, 1400)])

    def test_不同集不算重叠(self):
        assert not c._overlaps(mk(101, 3, 1400, episode=3), [mk(100, 3, 1400, episode=2)])

    def test_同集隔得远不算重叠(self):
        assert not c._overlaps(mk(200, 3, 1400), [mk(100, 3, 1400)])

    def test_按台词真实跨度判而不是排版后的时长(self):
        # 段 10 踩过：15:12 那句自然跨度 2.4s 被拉到 MIN_CLIP 再加间隔，
        # 把 6 秒后段 11 要的 15:18 也圈掉了。两句是同一场对话的连续两句，
        # 本来该各用各的镜头。所以判定用 span，不用 dur。
        a = mk(912.0, 9.0, 1400, span=2.4)     # dur 被拉长到 9s，但 span 只有 2.4s
        b = mk(918.0, 3.0, 1400, span=2.0)
        assert not c._overlaps(b, [a])


class TestParseShots:
    def test_解析段落(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text(
            "# 标题\n\n## 段落 1\n\n配音：第一段。\n\n画面：\n  查询: 角色说了什么\n"
            "  备选: 退而求其次\n\n## 段落 2\n\n配音：第二段。\n\n画面：\n  查询: 另一句\n",
            encoding="utf-8")
        out = c.parse_shots(f)
        assert [s["index"] for s in out] == [1, 2]
        assert out[0]["text"] == "第一段。"
        assert out[0]["query"] == "角色说了什么"
        assert out[0]["alt"] == "退而求其次"
        assert out[1]["alt"] is None      # 备选留空是允许的

    def test_解析不出段落就报错不静默返回空(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text("# 只有标题没有段落\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            c.parse_shots(f)


class TestCandidate:
    class U:
        def __init__(self, anime="春物", season=2, episode=2, start=100.0, end=104.0):
            self.anime, self.season, self.episode = anime, season, episode
            self.start, self.end, self.text = start, end, "某句台词"

    SRC = {"S02E02": {"path": "/x/e02.mkv", "duration": 1400.0}}

    def test_低于_NO_MATCH_不采用(self):
        assert c.candidate(0.30, self.U(), self.SRC) is None

    def test_每个片段都要过线不只是第一个(self):
        # 初版只卡 hits[0]，后面凑时长的片段照单全收——
        # 段 5 因此混进一个 0.417 的选举片段，而那段配音讲的是修学旅行。
        assert c.candidate(0.417, self.U(), self.SRC) is None

    def test_该集没登记就不许用(self):
        assert c.candidate(0.8, self.U(episode=99), self.SRC) is None

    def test_跨番命中必须挡住(self):
        # 索引目录是全局的。不挡的话，命中别的番的台词会被解析成
        # **本番的同季同集文件**，切出完全不相干的画面且不报错。
        assert c.candidate(0.9, self.U(anime="别的番"), self.SRC, anime="春物") is None
        assert c.candidate(0.9, self.U(anime="春物"), self.SRC, anime="春物") is not None

    def test_不给_anime_时不做番过滤(self):
        # 向后兼容：老调用点不传就不管，行为与从前一致
        assert c.candidate(0.9, self.U(anime="别的番"), self.SRC) is not None

    def test_截取守卫_超出片尾就放弃而不是截断(self):
        # ffmpeg 在片段超出源片末尾时静默截断且不报错，踩到就整条音画错位。
        assert c.candidate(0.9, self.U(start=1399.0, end=1400.0), self.SRC) is None
