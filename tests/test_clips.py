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


class TestVerifyAlignment:
    """段级不变量校验面（B4）：Σclip.dur == manifest 段时长（±SEG_TOL）。"""

    def test_对齐通过(self):
        segs = [{"index": 1, "status": "ok", "clips": [{"dur": 3.0}, {"dur": 3.0}]}]
        audio = [{"duration": 6.0}]
        assert c.verify_alignment(segs, audio) == []

    def test_漂移超容差报段号和差值(self):
        # 6.0 画面 vs 6.3 配音，差 0.3s > SEG_TOL=0.05
        segs = [{"index": 5, "status": "ok", "clips": [{"dur": 3.0}, {"dur": 3.0}]}]
        audio = [{"duration": 6.3}]
        assert c.verify_alignment(segs, audio) == [
            "段5: 画面 6.00s / 配音 6.30s 差 -0.30s"]

    def test_no_match段跳过不比对(self):
        # status != ok 的段没有 clips（render 本就拒收），不该被判违例
        segs = [{"index": 2, "status": "no_match", "clips": []}]
        audio = [{"duration": 5.0}]
        assert c.verify_alignment(segs, audio) == []

    def test_ok但clips为空算违例(self):
        segs = [{"index": 3, "status": "ok", "clips": []}]
        audio = [{"duration": 5.0}]
        assert c.verify_alignment(segs, audio) == ["段3: status=ok 但 clips 为空"]

    def test_段数不齐报而不是静默用zip截断(self):
        # zip 会静默吞掉多出来的段——判据 9：跳过不是通过，错位更不是
        segs = [{"index": 1, "status": "ok", "clips": [{"dur": 3.0}]}]
        audio = []
        assert c.verify_alignment(segs, audio) == [
            "段数不齐：04-clips.json 1 段 / manifest 0 段"]


class TestRefit:
    """人审改过 start/source 之后，把末片 dur 重排到满足段级不变量。"""

    SRC = {"S02E02": {"path": "/x/e02.mkv", "duration": 1400.0}}

    def test_已对齐输入是幂等no_op(self):
        segs = [{"index": 1, "status": "ok", "clips": [
            {"season": 2, "episode": 2, "start": 10.0, "dur": 6.0}]}]
        audio = [{"duration": 6.0}]
        out, report = c.refit(segs, audio, self.SRC)
        assert report == []
        assert out[0]["clips"][0]["dur"] == 6.0

    def test_差0_4s由末片吸收且总和等于need(self):
        segs = [{"index": 7, "status": "ok", "clips": [
            {"season": 2, "episode": 2, "start": 10.0, "dur": 3.0},
            {"season": 2, "episode": 2, "start": 20.0, "dur": 3.0}]}]  # Σ=6.0
        audio = [{"duration": 6.4}]                                    # 差 +0.4
        out, report = c.refit(segs, audio, self.SRC)
        last = out[0]["clips"][-1]
        assert last["dur"] == 3.4                       # 3.0 + 0.4，headroom 充足
        assert sum(cl["dur"] for cl in out[0]["clips"]) == pytest.approx(6.4)
        assert report == ["段7: 末片 3.00s → 3.40s（漂移 +0.40s 由末片吸收）"]

    def test_末片headroom不够就SystemExit(self):
        # 末片起点 1397，源时长 1400 → 向后只有 3.0s 余量；
        # 需要吸收的漂移 2.5s 会把 dur 顶到 5.0，clamp 到 3.0 仍不够 need
        segs = [{"index": 9, "status": "ok", "clips": [
            {"season": 2, "episode": 2, "start": 10.0, "dur": 3.0},
            {"season": 2, "episode": 2, "start": 1397.0, "dur": 2.5}]}]  # Σ=5.5
        audio = [{"duration": 8.0}]                                      # 差 +2.5
        with pytest.raises(SystemExit, match="headroom 夹不住"):
            c.refit(segs, audio, self.SRC)

    def test_absorb后低于MIN_CLIP就SystemExit(self):
        # need 比 Σdur 少 4.0s，末片 5.0 − 4.0 = 1.0 < MIN_CLIP=2.5，
        # clamp 夹到 2.5 仍比 need 多，夹不住
        segs = [{"index": 11, "status": "ok", "clips": [
            {"season": 2, "episode": 2, "start": 10.0, "dur": 3.0},
            {"season": 2, "episode": 2, "start": 20.0, "dur": 5.0}]}]  # Σ=8.0
        audio = [{"duration": 4.0}]                                    # 差 -4.0
        with pytest.raises(SystemExit, match="headroom 夹不住"):
            c.refit(segs, audio, self.SRC)


class TestLadder:
    """查询阶梯：`查询` → `备选` → `配音`，**只救不比**。

    这是本文件里最容易被后人改坏的地方，因为「三种问法取最高分」看起来明显更好：
    2026-07-30 实测 21 段，配音原文在 11 段上分数最高，查询字段只在 8 段最高。

    但分数量的是「旁白与字幕的语义相似度」，不是「这个镜头是否呈现旁白讲的那一刻」。
    取最优会毁掉写成逐字引语的段落（段 17：查询 0.855 → 配音 0.559），
    也会引入说话人偏置（段 4 配音「雪之下雪乃。」得 0.750，
    可那个镜头拍的是**说出她名字的人**）。
    """

    def _shot(self, query="查询语", alt="备选语", text="配音正文。", episode=None):
        return {"index": 1, "query": query, "alt": alt, "text": text,
                "episode": episode}

    def _fake(self, scores):
        """按查询文本返回预设分数。scores: {查询文本: top1 分数}。

        键可以是纯文本（不区分作用域）或 (文本, season, episode)。
        """
        calls = []

        def search(q, vecs, units, k, season=None, episode=None):
            calls.append((q, season, episode))
            sc = scores.get((q, season, episode), scores.get(q))
            return [(sc, object())] if sc is not None else []
        return search, calls

    def _run(self, shot, scores, monkeypatch):
        search, calls = self._fake(scores)
        monkeypatch.setattr(c, "search", search)
        hits, used, rung, scope, _step = c._ladder(shot, None, None)
        return used, rung, scope, calls

    def test_第一级够格就不往下试(self, monkeypatch):
        # 关键：**根本不去查后面两级**。查了再比就已经错了。
        used, rung, _, calls = self._run(
            self._shot(), {"查询语": 0.50, "备选语": 0.99, "配音正文。": 0.99}, monkeypatch)
        assert (used, rung) == ("查询语", 1)
        assert calls == [("查询语", None, None)]

    def test_第一级不够才用备选(self, monkeypatch):
        used, rung, _, _ = self._run(
            self._shot(), {"查询语": 0.30, "备选语": 0.50}, monkeypatch)
        assert (used, rung) == ("备选语", 2)

    def test_前两级都不够才用配音原文(self, monkeypatch):
        used, rung, _, calls = self._run(
            self._shot(), {"查询语": 0.30, "备选语": 0.20, "配音正文。": 0.60}, monkeypatch)
        assert (used, rung) == ("配音正文。", 3)
        assert calls == [("查询语", None, None), ("备选语", None, None),
                         ("配音正文。", None, None)]

    def test_备选留空就跳到配音(self, monkeypatch):
        used, rung, _, calls = self._run(
            self._shot(alt=None), {"查询语": 0.30, "配音正文。": 0.60}, monkeypatch)
        assert (used, rung) == ("配音正文。", 3)
        assert ("备选语", None, None) not in calls

    def test_门槛不随级数放松(self, monkeypatch):
        # 加大的是找的力度，不是放低的标准。三级全在门槛下 → 一级都不算成功。
        used, rung, _, _ = self._run(
            self._shot(), {"查询语": 0.30, "备选语": 0.35, "配音正文。": 0.44}, monkeypatch)
        assert rung == 3 and used == "配音正文。"   # 报最高分那次

    def test_全不够格时报最高分那次而不是最后一次(self, monkeypatch):
        # 报最后一次只是碰巧的顺序；报最高分才说明「差多少」。
        used, _, _, _ = self._run(
            self._shot(), {"查询语": 0.44, "备选语": 0.10, "配音正文。": 0.20}, monkeypatch)
        assert used == "查询语"

    def test_一条都没命中不报错(self, monkeypatch):
        used, rung, scope, _ = self._run(self._shot(), {}, monkeypatch)
        assert rung == 1 and scope == 2

    # ---- ADR-0004：作用域链（集→季→全空间），「只救不比」同构延伸 ----

    def test_写了集_集级及格就不查季级和全空间(self, monkeypatch):
        # 关键：季级/全空间即使分数更高也不许查——集号是剧情知识，比语义分更强。
        used, rung, scope, calls = self._run(
            self._shot(episode="S01E07"),
            {("查询语", 1, 7): 0.50, ("查询语", 1, None): 0.99}, monkeypatch)
        assert (used, rung, scope) == ("查询语", 1, 0)
        assert calls == [("查询语", 1, 7)]

    def test_集级不及格_季级及格_降级到季(self, monkeypatch):
        # 集级三级阶梯全跑完仍不及格，才放宽到季；季级及格就停。
        used, rung, scope, calls = self._run(
            self._shot(episode="S01E07"),
            {("查询语", 1, 7): 0.30, ("备选语", 1, 7): 0.20, ("配音正文。", 1, 7): 0.10,
             ("查询语", 1, None): 0.55}, monkeypatch)
        assert (used, rung, scope) == ("查询语", 1, 1)
        assert calls[-1] == ("查询语", 1, None)

    def test_集和季都不及格_才到全空间(self, monkeypatch):
        used, rung, scope, _ = self._run(
            self._shot(episode="S01E07"),
            {("查询语", 1, 7): 0.30, ("备选语", 1, None): 0.20,
             ("配音正文。", None, None): 0.60}, monkeypatch)
        assert scope == 2 and used == "配音正文。"

    def test_没写集_只查一次全空间(self, monkeypatch):
        # 逐字节兼容今天：没有集字段时行为完全不变。
        used, rung, scope, calls = self._run(
            self._shot(episode=None), {"查询语": 0.50}, monkeypatch)
        assert (used, rung, scope) == ("查询语", 1, 2)
        assert calls == [("查询语", None, None)]

    def test_集级该集无单元_空结果走降级链(self, monkeypatch):
        # 集号写对了但该集一个字幕单元都没有（如整集没人说话）→ search 返回 []，
        # 空结果不是「命中失败」，继续降级到季级、全空间，不硬失败。
        used, rung, scope, _ = self._run(
            self._shot(episode="S99E99"),
            {("查询语", 99, None): 0.30, ("配音正文。", None, None): 0.60}, monkeypatch)
        assert (used, rung, scope) == ("配音正文。", 3, 2)


class TestLadderResume:
    """阶梯续爬（2026-09-02 须贺期段 4）：检索及格 ≠ 可分派——及格候选
    全被锚点占完的段，有权从停下的下一步继续往梯下走，已试过的步不重查。"""

    def _shot(self, query="查询语", alt="备选语", text="配音正文。", episode=None):
        return {"index": 1, "query": query, "alt": alt, "text": text,
                "episode": episode}

    def _fake(self, scores):
        calls = []

        def search(q, vecs, units, k, season=None, episode=None):
            calls.append((q, season, episode))
            sc = scores.get((q, season, episode), scores.get(q))
            return [(sc, object())] if sc is not None else []
        return search, calls

    def test_及格停梯后下一步是紧挨着的未试级(self, monkeypatch):
        search, calls = self._fake({"查询语": 0.50, "备选语": 0.60})
        monkeypatch.setattr(c, "search", search)
        *_, step = c._ladder(self._shot(), None, None)        # 首爬及格停在 rung 1
        assert step == 1
        _, used, rung, _, step = c._ladder(self._shot(), None, None, step0=step)
        assert (used, rung) == ("备选语", 2)
        assert calls == [("查询语", None, None), ("备选语", None, None)]  # 不重查

    def test_续爬穷尽返回步数_便于标死(self, monkeypatch):
        search, _ = self._fake({"查询语": 0.50})              # 备选/配音都没命中
        monkeypatch.setattr(c, "search", search)
        *_, step = c._ladder(self._shot(), None, None)
        hits, _, _, _, step = c._ladder(self._shot(), None, None, step0=step)
        assert not hits and step == len(c._ladder_steps(self._shot()))

    def test_步序列顺序_集优先且空查询跳过(self):
        steps = c._ladder_steps(self._shot(alt=None, episode="S01E07"))
        assert [(scope, rung) for scope, _, _, rung, _ in steps] == [
            (0, 1), (0, 3), (1, 1), (1, 3), (2, 1), (2, 3)]


class TestRescueStarved:
    """饿死段续爬：首选候选全被占（零片段）的台词段爬下一级；新命中追加
    在 hits 尾部，段内「只救不比」的序不变。"""

    def _p(self, clips=(), hits=((0.50, object()),), step=1, **kw):
        p = {"index": 1, "query": "查询语", "alt": "备选语", "text": "配音正文。",
             "episode": None, "person": None, "channel": "line",
             "threshold": c.NO_MATCH, "hits": list(hits), "clips": list(clips),
             "filter_fell_back": False, "ladder_step": step}
        p.update(kw)
        return p

    def _fake(self, scores):
        def search(q, vecs, units, k, season=None, episode=None):
            sc = scores.get(q)
            return [(sc, object())] if sc is not None else []
        return search

    def test_饿死段续爬及格_命中追加在尾(self, monkeypatch):
        monkeypatch.setattr(c, "search", self._fake({"备选语": 0.99}))
        p = self._p()
        assert c._rescue_starved([p], None, None, None) is True
        # 备选分数更高也排尾——原查询的候选仍先试（只救不比）
        assert [sc for sc, _ in p["hits"]] == [0.50, 0.99]
        assert p["rescue"] == {"rung": 2, "query": "备选语", "scope": 2}
        assert p["ladder_step"] == 2

    def test_有片段的段不触发(self, monkeypatch):
        monkeypatch.setattr(c, "search", self._fake({"备选语": 0.99}))
        p = self._p(clips=[{"start": 1.0}])
        assert c._rescue_starved([p], None, None, None) is False
        assert "rescue" not in p and len(p["hits"]) == 1

    def test_续爬也不及格_标死不再重试(self, monkeypatch):
        monkeypatch.setattr(c, "search",
                            self._fake({"备选语": 0.30, "配音正文。": 0.20}))
        p = self._p()
        assert c._rescue_starved([p], None, None, None) is False
        assert len(p["hits"]) == 1 and "rescue" not in p
        assert p["ladder_step"] == len(c._ladder_steps(p))    # 阶梯已尽
        assert c._rescue_starved([p], None, None, None) is False   # 不再发搜索

    def test_人物过滤作用于续爬命中(self, monkeypatch):
        monkeypatch.setattr(c, "search", self._fake({"备选语": 0.60}))
        seen = {}

        def fake_bc(hits, pres, person):
            seen["person"] = person
            return hits, True

        monkeypatch.setattr(c, "_by_character", fake_bc)
        p = self._p(person="须贺圭介")
        assert c._rescue_starved([p], None, None, "pres") is True
        assert seen["person"] == "须贺圭介" and p["filter_fell_back"] is True


class TestRescueAllocation:
    """生产现场回放（2026-09-02 须贺期段 4）：首选命中被锚点占死，
    分配一轮颗粒无收，续爬备选后第二轮拿到新画面。"""

    def _unit(self, start, end):
        from types import SimpleNamespace
        return SimpleNamespace(anime="番", season=1, episode=1,
                               start=start, end=end, text="台词")

    def test_首选被锚点占死_备选救回(self, monkeypatch):
        a, b = self._unit(100.0, 104.0), self._unit(200.0, 204.0)

        def search(q, vecs, units, k, season=None, episode=None):
            return {"查询语": [(0.50, a)], "备选语": [(0.60, b)]}.get(q, [])

        monkeypatch.setattr(c, "search", search)
        p = {"index": 2, "query": "查询语", "alt": "备选语", "text": "配音正文。",
             "episode": None, "person": None, "channel": "line",
             "threshold": c.NO_MATCH, "clips": [], "filter_fell_back": False,
             "duration": 5.0}
        hits, *_, step = c._ladder(p, None, None)             # rung 1 及格即停
        p["hits"], p["ladder_step"] = hits, step
        anchor = {"season": 1, "episode": 1, "start": 99.75, "span": 4.25}
        sources = {"S01E01": {"path": "x.mp4", "duration": 1000.0}}
        for _ in range(12):
            p["clips"] = []
            c._allocate([p], {2: p}, sources, "番", {2: 5.0}, pre=[anchor])
            if not c._rescue_starved([p], None, None, None):
                break
        assert p["clips"][0]["start"] == pytest.approx(199.75)   # 备选的 200s 画面
        assert p["rescue"]["rung"] == 2


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


class TestParseShots:
    """分镜标注的两个新字段：`人物` 走台词通道加角色过滤，`场景` 走画面通道。

    **ADR-0003 原文写的是 `画面`，实现改成了 `场景`**——稿件里 `画面：` 已经是
    包着 `查询/备选` 的块名，同名会让人和正则都分不清哪个是哪个。
    """

    HEAD = "# 02 全稿\n\n"

    def write(self, tmp_path, body):
        p = tmp_path / "02-script.md"
        p.write_text(self.HEAD + body, encoding="utf-8")
        return p

    def test_都不写就是现状(self, tmp_path):
        f = self.write(tmp_path, "## 段落 1\n\n配音：随便一句\n\n画面：\n  查询: 找这个\n")
        s = c.parse_shots(f)[0]
        assert s["person"] is None and s["scene"] is None
        assert s["query"] == "找这个"

    def test_人物字段(self, tmp_path):
        f = self.write(tmp_path,
                       "## 段落 1\n\n配音：随便一句\n\n画面：\n  人物: 雪乃\n  查询: 找这个\n")
        s = c.parse_shots(f)[0]
        assert s["person"] == "雪乃" and s["scene"] is None

    def test_场景字段(self, tmp_path):
        f = self.write(tmp_path, "## 段落 1\n\n配音：随便一句\n\n画面：\n  场景: 夜晚的天台\n")
        s = c.parse_shots(f)[0]
        assert s["scene"] == "夜晚的天台" and s["person"] is None

    def test_两个都写当场报错(self, tmp_path):
        # **一段只走一个通道。** 台词分数和画面分数不是同一个量，
        # 并跑之后没有任何办法把它们放在一起排序——所以在入口就拦住。
        f = self.write(tmp_path,
                       "## 段落 1\n\n配音：随便一句\n\n画面：\n  人物: 雪乃\n  场景: 天台\n")
        with pytest.raises(SystemExit):
            c.parse_shots(f)

    def test_全角冒号与缩进都认(self, tmp_path):
        f = self.write(tmp_path, "## 段落 1\n\n配音：随便一句\n\n画面：\n    人物：雪乃\n")
        assert c.parse_shots(f)[0]["person"] == "雪乃"

    def test_集字段_规范形与归一化(self, tmp_path):
        f = self.write(
            tmp_path,
            "## 段落 1\n\n配音：随便一句\n\n画面：\n  查询: 找这个\n  集: S01E07\n")
        s = c.parse_shots(f)[0]
        assert s["episode"] == "S01E07"
        f = self.write(
            tmp_path,
            "## 段落 2\n\n配音：随便一句\n\n画面：\n  查询: 找这个\n  集: S1E7\n")
        s = c.parse_shots(f)[0]
        assert s["episode"] == "S01E07"          # 归一化为全库唯一规范形

    def test_集字段_不写就是现状(self, tmp_path):
        f = self.write(tmp_path, "## 段落 1\n\n配音：随便一句\n\n画面：\n  查询: 找这个\n")
        assert c.parse_shots(f)[0]["episode"] is None

    def test_集字段_中文写法当场报错(self, tmp_path):
        f = self.write(
            tmp_path,
            "## 段落 1\n\n配音：随便一句\n\n画面：\n  查询: 找这个\n  集: 第一季第七集\n")
        with pytest.raises(SystemExit, match="段落 1"):
            c.parse_shots(f)

    def test_集字段_引用核对区不误抓(self, tmp_path):
        # 引用核对区里写「S01E01 21:21」是逐字引语的自查记录，不是检索约束。
        # 按块切分后它不在任何段落块内，不能被当成该段的集号。
        f = self.write(
            tmp_path,
            "## 段落 1\n\n配音：随便一句\n\n画面：\n  查询: 找这个\n\n"
            "---\n\n引用核对：S01E01 21:21 台词逐字\n")
        assert c.parse_shots(f)[0]["episode"] is None


class TestByCharacter:
    """角色在场参与排序：**带内次级排序**（ADR-0004）。

    旧实现是布尔硬过滤，「没检出」就把镜头整段枪毙——2026-08-09 段 19 的
    S01E08 正确画面就是这么被滤掉、退回跨季的。检测的不对称（ADR-0003）
    决定了「没检出」不能当「不在场」用，所以现在：不删镜头、检出者排前、
    漏检者（在场 0）垫底；presence 只在台词分同桶内（差 ≤ PRESENCE_BAND）
    做 tie-break，差超过带子台词分绝对优先；全漏检或全不及格仍退回原顺序。
    """

    class U:
        def __init__(self, ep, start):
            self.anime, self.season, self.episode = "春物", 1, ep
            self.start, self.end, self.text = start, start + 3.0, "台词"

    class P:
        """S01E01 的 0–10 秒有雪乃（0.98），10–30 秒有雪乃（0.90）。"""

        def presence_score(self, season, episode, start, end, name):
            if episode != 1 or name != "雪乃":
                return 0.0
            if start < 10.0:
                return 0.98
            if start < 30.0:
                return 0.90
            return 0.0

    def test_检出者排前_漏检者垫底但不删(self):
        # 漏检镜头（在场 0.0）不再被枪毙——它可能是侧脸/背身，画面对题照样能用，
        # 只是排到检出者后面。这正是段 19 那种「正确画面被滤掉」的修法。
        hits = [(0.8, self.U(1, 2.0)), (0.7, self.U(1, 50.0))]
        kept, fell_back = c._by_character(hits, self.P(), "雪乃")
        assert [h[0] for h in kept] == [0.8, 0.7]   # 两个都在，顺序台词分降序
        assert [h[2] for h in kept] == [0.98, 0.0]  # 各带在场分
        assert fell_back is False

    def test_带内_在场分高者排前(self):
        # 台词分差 0.02 ≤ PRESENCE_BAND → 同一档，0.90 在场那段排到 0.98 前面
        hits = [(0.52, self.U(1, 12.0)), (0.50, self.U(1, 2.0))]
        kept, _ = c._by_character(hits, self.P(), "雪乃")
        assert [h[0] for h in kept] == [0.50, 0.52]

    def test_带外_台词分绝对优先_presence无效(self):
        # 台词分差 0.10 > PRESENCE_BAND → 分高的在前，在场分再低也拦不住。
        # presence 结构性不可能顶掉清晰的台词命中（S2：一个量能卡门槛不代表能排序）。
        hits = [(0.60, self.U(1, 12.0)), (0.50, self.U(1, 2.0))]
        kept, _ = c._by_character(hits, self.P(), "雪乃")
        assert [h[0] for h in kept] == [0.60, 0.50]

    def test_全漏检就退回全部_顺序不变(self):
        hits = [(0.8, self.U(2, 2.0)), (0.7, self.U(3, 5.0))]
        kept, fell_back = c._by_character(hits, self.P(), "雪乃")
        assert fell_back is True
        assert [h[0] for h in kept] == [0.8, 0.7]   # 原顺序，一字不改
        assert [h[2] for h in kept] == [0.0, 0.0]

    def test_留下的都不及格也退回(self):
        # 过滤后只剩不及格的命中，等于没找到——退回去看不过滤的结果里有没有能用的。
        # 不退回的话，一次漏检会让这一段直接判死。
        hits = [(0.8, self.U(2, 2.0)), (0.30, self.U(1, 2.0))]
        kept, fell_back = c._by_character(hits, self.P(), "雪乃")
        assert [h[0] for h in kept] == [0.8, 0.30] and fell_back is True

    def test_空命中不报错(self):
        kept, fell_back = c._by_character([], self.P(), "雪乃")
        assert kept == [] and fell_back is True


class TestSceneNoMatch:
    """画面通道的门槛：**按番存，缺了就当场失败。**

    这个数不是模型常数——它拿该番的反例查询在该番的镜头上量出来。
    原先放在 `config/project.json` 的全局 `visual` 块里，换一部番会静默沿用，
    而沿用不会报错，只会让整条画面通道用一个错门槛跑起来（standard.md R2）。
    """

    import json as _json

    def conf(self, tmp_path, monkeypatch, obj):
        p = tmp_path / "scenes.json"
        p.write_text(self._json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr("pipeline.vindex.SCENES", p)

    def test_读该番自己的门槛(self, tmp_path, monkeypatch):
        self.conf(tmp_path, monkeypatch,
                  {"春物": {"queries": ["a"], "negative": ["b"], "no_match": 0.31}})
        assert c.scene_no_match("春物") == 0.31

    def test_没量过就失败而不是拿默认值(self, tmp_path, monkeypatch):
        # **故意不给默认值。** 抄一个数过来不会报错，只会静默用错门槛
        self.conf(tmp_path, monkeypatch, {"春物": {"queries": ["a"], "negative": ["b"]}})
        with pytest.raises(SystemExit, match="no_match"):
            c.scene_no_match("春物")

    def test_换番时不会沿用上一部番的数(self, tmp_path, monkeypatch):
        # 这是把它从全局块挪成按番存要堵的那个洞
        self.conf(tmp_path, monkeypatch,
                  {"春物": {"queries": ["a"], "negative": ["b"], "no_match": 0.31}})
        with pytest.raises(SystemExit):
            c.scene_no_match("紫罗兰")


class TestAllocate:
    """分配主循环：段内按带内序尝试，跨段仍按台词分贪心。

    **为什么单独测这一层**：带内排序的测试在 `TestByCharacter`（函数级），
    但 2026-08-09 实测发现 pool 摊平后按台词分重新全局排序，把段内带内序
    抹掉了——端到端只有「漏检不剔除」生效，「带内决胜负」和「漏检垫底」
    都落空（稳定排序只保留严格同分序）。`_allocate` 是指针轮转，段内尝试
    顺序 = `hits` 序（已带内修正），跨段每轮只比当前候选的台词分。
    """

    class U:
        def __init__(self, start=100.0, end=104.0):
            self.anime = "春物"
            self.season, self.episode = 1, 1
            self.start, self.end, self.text = start, end, "某句台词"

    SRC = {"S01E01": {"path": "/x/e01.mkv", "duration": 1400.0}}

    def prep(self, index, hits, duration=12.0, *, channel="line"):
        return {"index": index, "hits": hits, "duration": duration,
                "channel": channel, "threshold": 0.45, "clips": []}

    def alloc(self, *preps):
        live = list(preps)
        return c._allocate(live, {p["index"]: p for p in live}, self.SRC, "春物",
                           {p["index"]: p["duration"] for p in live})

    def test_带内_presence_高者优先(self):
        # 差 0.02 ≤ PRESENCE_BAND：presence 0.98 的候选在 hits 里排前（带内修正后）。
        # 两个候选时间重叠，先试的占位、后试的被拒——所以「谁被选中」=「谁先被尝试」。
        u_hi = self.U(101.0, 103.5)          # presence 0.98
        u_lo = self.U(100.0, 102.5)          # 台词分高 0.02 但 presence 只有 0.10
        # hits 序 = 带内修正后的序：u_hi 在前（presence 决胜负），即使它台词分低 0.02
        p1 = self.prep(1, [(0.50, u_hi, 0.98), (0.52, u_lo, 0.10)], duration=6.0)
        self.alloc(p1)
        # u_hi 先被尝试 → 它占位 → u_lo 因重叠被拒
        assert len(p1["clips"]) == 1
        assert p1["clips"][0]["start"] == 100.75   # u_hi 的候选起点 (101.0 - PAD)

    def test_带外_台词分绝对优先(self):
        # 差 0.10 > PRESENCE_BAND：presence 不发言，hits 序 = 台词分序。
        u_hi = self.U(101.0, 103.5)          # presence 0.98 但分低
        u_lo = self.U(100.0, 102.5)          # 台词分高，presence 0.10
        p1 = self.prep(1, [(0.60, u_lo, 0.10), (0.50, u_hi, 0.98)], duration=6.0)
        self.alloc(p1)
        assert p1["clips"][0]["start"] == 99.75     # u_lo 的候选起点 (100.0 - PAD)

    def test_漏检候选不剔除_只是排在后面试(self):
        # 漏检（presence 0.0）不枪毙：quota 够时它也进成片，只是尝试顺序靠后。
        u1 = self.U(100.0, 102.5)
        u2 = self.U(300.0, 302.5)            # 与 u1 不重叠
        p1 = self.prep(1, [(0.52, u1, 0.98), (0.50, u2, 0.0)], duration=12.0)
        self.alloc(p1)
        assert len(p1["clips"]) == 2
        assert p1["clips"][1]["start"] == 299.75   # u2 也被分配，只是排在后面

    def test_跨段仍按台词分_presence_不进跨段比较(self):
        # 段 2 台词分 0.52 > 段 1 的 0.50，即使段 1 的 presence 高得多。
        # 两个候选同集时间重叠，互相竞争。
        uA = self.U(100.0, 104.0)
        uB = self.U(100.0, 104.0)
        p1 = self.prep(1, [(0.50, uA, 0.98)], duration=6.0)
        p2 = self.prep(2, [(0.52, uB, 0.10)], duration=6.0)
        self.alloc(p1, p2)
        assert p2["clips"] and not p1["clips"]   # 段 2 先得画面，段 1 空

    def test_尝试失败推进指针_试下一个带内候选(self):
        # u1 被别的段占了 → 指针推进，试 u2（不重叠，成功）。
        uB = self.U(100.0, 104.0)
        u1 = self.U(101.0, 103.0)
        u2 = self.U(300.0, 302.5)
        p1 = self.prep(1, [(0.50, u1, 0.98), (0.48, u2, 0.0)], duration=12.0)
        p2 = self.prep(2, [(0.55, uB, 0.10)], duration=6.0)
        self.alloc(p1, p2)
        assert p2["clips"]                       # 段 2 先得（分高）
        assert len(p1["clips"]) == 1
        assert p1["clips"][0]["start"] == 299.75 # 段 1 拿到的是 u2 不是 u1


class TestSegmentGate:
    """段级门槛用**重排前**的检出者最高分（2026-08-16 审计 2-3）。

    带内交换可以把台词分低于 NO_MATCH 的候选换到首位（0.46 与 0.42 差
    ≤ PRESENCE_BAND 且后者在场分高时交换）。旧实现 top_score 取重排后的
    hits[0]，段被误判 no_match——而及格的 0.46 明明还在候选里，分配时
    本可以用它。带内重排只许改变尝试顺序，不许改「这段找到没有」的答案。
    """

    class U:
        def __init__(self, start):
            self.anime, self.season, self.episode = "春物", 1, 1
            self.start, self.end, self.text = start, start + 3.0, "台词"

    class P:
        def indexed(self):
            return {"yukinoshita_yukino"}

        def tag_of(self, name):
            return {"雪乃": "yukinoshita_yukino"}[name]

        def presence_score(self, season, episode, start, end, name):
            # 两个候选都检出（都 >0），低分的那个在场分更高——这才是带内交换
            # 的触发条件；若高分者漏检（0.0），走的会是「检出者最高分不过线
            # → 整体退回」分支，考不到段级门槛
            return 0.9 if start > 10.0 else 0.6

    def _episode(self, tmp_path):
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "02-script.md").write_text(
            "## 段落 1\n\n配音：正文。\n\n画面：\n  查询: 查询语\n  人物: 雪乃\n",
            encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(
            '{"segments": [{"duration": 6.0}]}', encoding="utf-8")
        return ep

    def test_带内交换不改变段级及格判定(self, tmp_path, monkeypatch):
        import json as _json
        ep = self._episode(tmp_path)
        monkeypatch.setattr(c, "load_sources",
                            lambda a: {"S01E01": {"path": "/x.mkv", "duration": 600.0}})
        monkeypatch.setattr(c, "load_all", lambda idx, a: (None, None))
        monkeypatch.setattr(c.vindex, "load_presence", lambda a: self.P())
        monkeypatch.setattr(c, "search",
                            lambda q, vecs, units, k, season=None, episode=None: [
                                (0.46, self.U(2.0)),      # 台词分过线，在场 0.6
                                (0.42, self.U(12.0))])    # 分差 0.04 ≤ BAND、在场 0.9 → 换到首位
        dest = c.run(ep, tmp_path, "春物")
        seg = _json.loads(dest.read_text(encoding="utf-8"))["segments"][0]
        assert seg["top_score"] == 0.46       # 重排前最高分，不是交换后的 0.42
        assert seg["status"] == "ok"          # 及格候选在，不许判 no_match

    def test_检出者最高分不过线仍是no_match(self, tmp_path, monkeypatch):
        # 反向护栏：门槛用检出者最高分≠放松门槛——没人过线照样 no_match
        import json as _json
        ep = self._episode(tmp_path)
        monkeypatch.setattr(c, "load_sources",
                            lambda a: {"S01E01": {"path": "/x.mkv", "duration": 600.0}})
        monkeypatch.setattr(c, "load_all", lambda idx, a: (None, None))
        monkeypatch.setattr(c.vindex, "load_presence", lambda a: self.P())
        monkeypatch.setattr(c, "search",
                            lambda q, vecs, units, k, season=None, episode=None: [
                                (0.44, self.U(2.0)), (0.40, self.U(12.0))])
        dest = c.run(ep, tmp_path, "春物")
        seg = _json.loads(dest.read_text(encoding="utf-8"))["segments"][0]
        assert seg["status"] == "no_match"


class TestAnimeFallback:
    """config 删掉 anime.default → 显式报错，不静默跳回另一部番（审计 2-16）。"""

    def test_主入口无番名配置显式报错(self, tmp_path, monkeypatch):
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "02-script.md").write_text("## 段落 1\n\n配音：x。\n", encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(
            '{"segments": [{"duration": 5.0}]}', encoding="utf-8")
        monkeypatch.setattr(c.paths, "conf", lambda d, default=None: default)
        monkeypatch.setattr("sys.argv", ["clips", str(ep)])
        with pytest.raises(SystemExit, match="番名"):
            c.main()


class TestRefitAtomic:
    """`--refit` 先备份人审产物再原子重写（2026-08-16 审计 2-27）。

    refit 改的是 --approve 之后的文件，写一半崩溃连「人改过什么样」
    都恢复不了；备份落在 .prerefit.bak，中间态 .tmp 不许残留。
    """

    def test_refit备份且不留tmp(self, tmp_path, monkeypatch):
        import json as _json
        ep = tmp_path / "ep"
        ep.mkdir()
        (ep / "04-clips.json").write_text(_json.dumps({
            "anime": "春物", "total_duration": 6.0,
            "segments": [{"index": 1, "text": "x", "duration": 6.0, "status": "ok",
                          "clips": [{"season": 1, "episode": 1, "source": "/x.mkv",
                                     "start": 10.0, "dur": 6.0, "span": 3.0,
                                     "limit": 600.0}]}]}, ensure_ascii=False),
            encoding="utf-8")
        (ep / "03-audio").mkdir()
        (ep / "03-audio" / "manifest.json").write_text(
            '{"segments": [{"duration": 6.0}]}', encoding="utf-8")
        monkeypatch.setattr(c, "load_sources",
                            lambda a: {"S01E01": {"path": "/x.mkv", "duration": 600.0}})
        monkeypatch.setattr("sys.argv", ["clips", str(ep), "--refit"])
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            c.main()
        assert (ep / "04-clips.json.prerefit.bak").exists()
        assert not (ep / "04-clips.json.tmp").exists()
        data = _json.loads((ep / "04-clips.json").read_text(encoding="utf-8"))
        assert data["segments"][0]["clips"]   # 重写后的文件仍是合法 plan


class TestAnchorParse:
    """`锚点:` 字段解析（ADR-0008）。锚点是排片的确定性输入，
    写错 = 画面直接指到错误的时间码，所以格式错误一律当场报错。"""

    def test_点时间码(self):
        a = c._parse_anchor("S01E01 17:50", 1)
        assert (a["season"], a["episode"], a["t0"], a["t1"]) == (1, 1, 1070.0, None)

    def test_区间时间码(self):
        a = c._parse_anchor("S01E01 17:50-18:20", 1)
        assert (a["t0"], a["t1"]) == (1070.0, 1100.0)

    def test_剧场版三位分钟(self):
        assert c._parse_anchor("S01E01 96:08", 1)["t0"] == 5768.0

    def test_无返回None(self):
        assert c._parse_anchor("无（纯氛围过场）", 1) is None
        assert c._parse_anchor("无", 1) is None     # 理由缺失由 check_script 拦

    def test_格式坏掉当场报错(self):
        with pytest.raises(SystemExit):
            c._parse_anchor("第一集 17:50", 3)

    def test_区间终点不在起点之后报错(self):
        with pytest.raises(SystemExit):
            c._parse_anchor("S01E01 18:20-17:50", 1)

    def test_parse_shots_锚点自带集号(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text("## 段落 1\n\n配音：x。\n\n画面：\n  锚点: S01E01 17:50\n",
                     encoding="utf-8")
        out = c.parse_shots(f)
        assert out[0]["episode"] == "S01E01" and out[0]["anchor"]["t0"] == 1070.0

    def test_parse_shots_集与锚点写叉报错(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text("## 段落 1\n\n配音：x。\n\n画面：\n  集: S01E02\n  锚点: S01E01 17:50\n",
                     encoding="utf-8")
        with pytest.raises(SystemExit):
            c.parse_shots(f)

    def test_parse_shots_锚点与人物冲突报错(self, tmp_path):
        # 锚点段不检索，人物过滤器用不上——写了只会被静默忽略，宁可当场报错
        f = tmp_path / "s.md"
        f.write_text("## 段落 1\n\n配音：x。\n\n画面：\n  人物: 阳菜\n  锚点: S01E01 17:50\n",
                     encoding="utf-8")
        with pytest.raises(SystemExit):
            c.parse_shots(f)


class TestAnchorCandidate:
    """锚点 → 片段：起点吸附镜头切点，时长交给 size() 水填。"""

    TBL = {"shots": [{"i": 0, "start": 0.0, "end": 10.0, "rep": 5.0},
                     {"i": 1, "start": 10.0, "end": 25.0, "rep": 17.5},
                     {"i": 2, "start": 25.0, "end": 40.0, "rep": 32.5}]}
    SRC = {"S01E01": {"path": "/x/e01.mkv", "duration": 100.0}}

    def _run(self, raw, monkeypatch, sources=None):
        from pipeline import shots
        monkeypatch.setattr(shots, "load", lambda a, k: self.TBL)
        return c._anchor_candidate(c._parse_anchor(raw, 1),
                                   sources or self.SRC, "番")

    def test_起点吸附到含锚点的镜头切点(self, monkeypatch):
        # 00:12 落在镜头 [10,25)，起点取 10.0——不切半镜
        cand = self._run("S01E01 00:12", monkeypatch)
        assert cand["start"] == 10.0 and cand["span"] == 15.0

    def test_区间跨镜头取到终点镜头的尾切点(self, monkeypatch):
        cand = self._run("S01E01 00:05-00:30", monkeypatch)
        assert cand["start"] == 0.0 and cand["span"] == 40.0

    def test_该集没登记返回None(self, monkeypatch):
        assert self._run("S01E09 00:12", monkeypatch) is None

    def test_锚点超出片长返回None(self, monkeypatch):
        assert self._run("S01E01 99:00", monkeypatch) is None

    def test_字段形状与_candidate_一致(self, monkeypatch):
        # 下游 _overlaps/size/render 依赖同一组字段，缺一个就是静默错
        cand = self._run("S01E01 00:12", monkeypatch)
        for k in ("season", "episode", "source", "start", "dur", "span",
                  "limit", "score", "line", "presence"):
            assert k in cand


class TestAnchorIntegration:
    """run() 级集成：预占位 → 3 轮 quota 循环 → final size → 序列化。

    mock 掉检索与镜头表，只留真实 run() 链路——审查列出的交互疑点
    （clips 重置循环、锚点跨轮存活、检索撞锚点、双锚撞车）都在这条链上。
    """

    TBL = {"shots": [{"i": 0, "start": 0.0, "end": 10.0, "rep": 5.0},
                     {"i": 1, "start": 10.0, "end": 25.0, "rep": 17.5},
                     {"i": 2, "start": 25.0, "end": 200.0, "rep": 112.5}]}
    SRC = {"S01E01": {"path": "/x/e01.mkv", "duration": 200.0}}

    class U:
        def __init__(self, start, end, text="台词"):
            self.anime, self.season, self.episode = "番", 1, 1
            self.start, self.end, self.text = start, end, text

    def _run(self, tmp_path, monkeypatch, script_text, durations, hits=()):
        import json as _json
        monkeypatch.setattr(c, "load_sources", lambda a: self.SRC)
        monkeypatch.setattr(c, "load_all", lambda d, a: (None, None))
        monkeypatch.setattr(c, "search",
                            lambda *a, **k: [(0.9, u) for u in hits])
        from pipeline import shots as sh
        monkeypatch.setattr(sh, "load", lambda a, k: self.TBL)
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "02-script.md").write_text(script_text, encoding="utf-8")
        (ep / "03-audio" / "manifest.json").write_text(_json.dumps({"segments": [
            {"index": i, "label": str(i), "text": "x", "file": f"s{i}.wav",
             "duration": d, "cer": 0.0, "attempts": 1}
            for i, d in enumerate(durations, 1)]}), encoding="utf-8")
        return _json.loads(c.run(ep, anime="番").read_text(encoding="utf-8"))

    def test_锚点段水填到配音时长且不泄漏临时字段(self, tmp_path, monkeypatch):
        data = self._run(tmp_path, monkeypatch,
                         "## 段落 1\n\n配音：一。\n\n画面：\n  锚点: S01E01 00:12\n",
                         [8.0])
        seg = data["segments"][0]
        assert seg["channel"] == "anchor" and seg["status"] == "ok"
        clip = seg["clips"][0]
        assert (clip["start"], clip["dur"]) == (10.0, 8.0)
        for k in ("limit", "span", "floor"):
            assert k not in clip          # 排版临时字段不进产物

    def test_检索候选撞锚点被拒退到次优(self, tmp_path, monkeypatch):
        # 锚点占 [10,25)；检索首选 (11,13) 落在里面必须被拒，退到 (60,63)
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n  锚点: S01E01 00:12\n\n"
                  "## 段落 2\n\n配音：二。\n\n画面：\n  查询: q\n  锚点: 无（测试）\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0, 4.0],
                         hits=[self.U(11.0, 13.0), self.U(60.0, 63.0)])
        seg2 = data["segments"][1]
        assert seg2["status"] == "ok" and seg2["clips"][0]["start"] == 59.75

    def test_双锚撞车标_anchor_overlap_不静默挪(self, tmp_path, monkeypatch):
        script = ("## 段落 1\n\n配音：一。\n\n画面：\n  锚点: S01E01 00:12\n\n"
                  "## 段落 2\n\n配音：二。\n\n画面：\n  锚点: S01E01 00:14\n")
        data = self._run(tmp_path, monkeypatch, script, [8.0, 4.0])
        seg2 = data["segments"][1]
        assert seg2["status"] == "anchor_overlap" and seg2["clips"] == []

    def test_全角波浪号当区间分隔符(self):
        a = c._parse_anchor("S01E01 17:50～18:20", 1)
        assert (a["t0"], a["t1"]) == (1070.0, 1100.0)
