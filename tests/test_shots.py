"""镜头切分：切点 → 镜头表，以及时间点落在哪个镜头。

这个模块的风险在**过切与漏切的不对称**上，而这个不对称不是审美问题，是判据问题：

- 过切（一个镜头切成两个）：两个镜头内容几乎相同，各自都会被正确打标，
  对「这个镜头里有没有 X」这个布尔判断毫无影响。
- 漏切（两场戏并成一个镜头）：角色集合变成两场戏的并集，
  **「检测到 X」不再意味着「X 在这段时间里出现」**——整条角色过滤链的地基就是这句话。

所以 `cut` 里的合并逻辑要小心：它是唯一一处会**故意制造漏切**的代码。

期望值全部先在实现上跑过再写进断言（CLAUDE.md「写测试的两条纪律」）。
"""

import json
import sys

import pytest

from pipeline import shots


class TestCut:
    def test_没有切点就是一整个镜头(self):
        out = shots.cut([], 10.0, 100.0, 0.5)
        assert out == [{"i": 0, "start": 0.0, "end": 100.0, "rep": 50.0}]

    def test_切点把片长分成首尾相接的区间(self):
        out = shots.cut([(30.0, 20.0), (60.0, 20.0)], 10.0, 90.0, 0.5)
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0)]
        # 首尾相接：没有缝隙也没有重叠，否则 `at()` 会查不到或查到两个
        assert all(a["end"] == b["start"] for a, b in zip(out, out[1:]))

    def test_低于阈值的切点不算数(self):
        out = shots.cut([(30.0, 8.0), (60.0, 20.0)], 10.0, 90.0, 0.5)
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 60.0), (60.0, 90.0)]

    def test_代表帧取中点(self):
        out = shots.cut([(40.0, 20.0)], 10.0, 100.0, 0.5)
        assert [s["rep"] for s in out] == [20.0, 70.0]

    def test_编号连续且从零开始(self):
        out = shots.cut([(10.0, 20.0), (20.0, 20.0), (30.0, 20.0)], 10.0, 40.0, 0.1)
        assert [s["i"] for s in out] == [0, 1, 2, 3]

    def test_过短的镜头并进前一个(self):
        # 20.0–20.3 只有 0.3 秒，短于 0.5，并进 0–20 那个
        out = shots.cut([(20.0, 20.0), (20.3, 20.0)], 10.0, 60.0, 0.5)
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 20.3), (20.3, 60.0)]

    def test_首个镜头过短时并进后一个(self):
        # 0–0.2 前面没有镜头可并，只能并进后面那个
        out = shots.cut([(0.2, 20.0), (30.0, 20.0)], 10.0, 60.0, 0.5)
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 30.0), (30.0, 60.0)]

    def test_连续多个过短镜头一起并掉(self):
        cuts = [(10.0, 20.0), (10.2, 20.0), (10.4, 20.0), (10.6, 20.0)]
        out = shots.cut(cuts, 10.0, 40.0, 0.5)
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 10.6), (10.6, 40.0)]

    def test_越界切点被忽略(self):
        # 负数与超过片长的切点不该造出倒序或零长区间
        out = shots.cut([(-5.0, 20.0), (200.0, 20.0), (50.0, 20.0)], 10.0, 100.0, 0.5)
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 50.0), (50.0, 100.0)]

    def test_切点乱序也要排好(self):
        out = shots.cut([(60.0, 20.0), (30.0, 20.0)], 10.0, 90.0, 0.5)
        assert [s["start"] for s in out] == [0.0, 30.0, 60.0]


class TestAt:
    @pytest.fixture
    def sh(self):
        return shots.cut([(30.0, 20.0), (60.0, 20.0)], 10.0, 90.0, 0.5)

    def test_落在区间内(self, sh):
        assert shots.at(sh, 45.0)["i"] == 1

    def test_左闭右开(self, sh):
        # 边界必须只属于一个镜头，否则同一时刻会被算成两个镜头都在场。
        # 每个边界都要查：**只查第一个边界时，把 `t >= end` 写成 `t > end` 照样绿**
        # （二分正好在那一步转向），而它在别的边界上是错的。
        assert shots.at(sh, 30.0)["i"] == 1
        assert shots.at(sh, 29.999)["i"] == 0
        assert shots.at(sh, 60.0)["i"] == 2
        assert shots.at(sh, 59.999)["i"] == 1

    def test_零点属于第一个(self, sh):
        assert shots.at(sh, 0.0)["i"] == 0

    def test_越界返回_None(self, sh):
        assert shots.at(sh, -1.0) is None
        assert shots.at(sh, 90.0) is None      # 片长本身是开区间端点


class TestBetween:
    @pytest.fixture
    def sh(self):
        return shots.cut([(30.0, 20.0), (60.0, 20.0)], 10.0, 90.0, 0.5)

    def test_跨镜头的区间返回全部相交镜头(self, sh):
        # 一句台词横跨两三个镜头是常态，所以是列表不是单个
        assert [s["i"] for s in shots.between(sh, 25.0, 65.0)] == [0, 1, 2]

    def test_完全落在一个镜头内(self, sh):
        assert [s["i"] for s in shots.between(sh, 35.0, 40.0)] == [1]

    def test_端点相接不算相交(self, sh):
        # [30, 30) 空区间；[0,30) 只碰第一个
        assert [s["i"] for s in shots.between(sh, 0.0, 30.0)] == [0]

    def test_区间在片外返回空(self, sh):
        assert shots.between(sh, 200.0, 300.0) == []


class TestScdet:
    def test_解析日志行(self):
        s = "[scdet @ 0x1] lavfi.scd.score: 23.670, lavfi.scd.time: 5.889\n"
        assert shots.SCD.findall(s) == [("23.670", "5.889")]

    def test_进度行挤在同一行也要解析出来(self):
        # 实测 ffmpeg 会把进度写在同一行：按行切会漏掉挤在一起的那些
        s = ("frame=  346 fps=0.0 q=-0.0 size=N/A time=00:00:14.43 "
             "[scdet @ 0x1] lavfi.scd.score: 26.582, lavfi.scd.time: 17.15")
        assert shots.SCD.findall(s) == [("26.582", "17.15")]

    def test_一段输出里的多条(self):
        s = ("[scdet @ 0x1] lavfi.scd.score: 1.5, lavfi.scd.time: 1.0\n"
             "[scdet @ 0x1] lavfi.scd.score: 2.5, lavfi.scd.time: 2.0\n")
        assert len(shots.SCD.findall(s)) == 2


class TestPct:
    def test_空列表返回零(self):
        assert shots._pct([], 0.5) == 0.0

    def test_中位数(self):
        assert shots._pct([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0

    def test_不越界(self):
        assert shots._pct([1.0, 2.0], 1.0) == 2.0


class TestCalibrateCli:
    """calibrate 的 --sheet 与 --long 不互斥（2026-09-05 审计 P0 修复）。

    变异检验：任一分支改回 `return 0`，第一个用例立刻红。
    """

    def _fake(self, monkeypatch):
        calls = []
        monkeypatch.setattr(shots, "cut_sheet",
                            lambda v, t: calls.append(("sheet", t)) or "cuts.jpg")
        monkeypatch.setattr(shots, "long_sheet",
                            lambda v, t: calls.append(("long", t)) or "long.jpg")
        monkeypatch.setattr(shots.paths, "require_data", lambda: None)
        return calls

    def test_sheet与long同传两张表都出(self, monkeypatch):
        calls = self._fake(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["shots", "calibrate", "v.mkv", "--sheet", "10", "--long", "8"])
        assert shots.main() == 0
        assert calls == [("sheet", 10.0), ("long", 8.0)]

    def test_只传sheet不碰long(self, monkeypatch):
        calls = self._fake(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["shots", "calibrate", "v.mkv", "--sheet", "10"])
        assert shots.main() == 0
        assert calls == [("sheet", 10.0)]


class TestThreshold:
    """threshold 按番分键（2026-09-05 审计 P1-1：全局单一标量换番即崩）。"""

    @pytest.fixture
    def conf(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots.paths, "CONFIG", tmp_path)
        monkeypatch.setattr(shots.paths, "_CONF", None)
        (tmp_path / "project.json").write_text(json.dumps(
            {"visual": {"scene_threshold": {"春物": 10.0, "天气之子": 8.0}}}),
            encoding="utf-8")
        return tmp_path

    def test_按番取值互不干扰(self, conf):
        # 新番标定改自己的键，旧番镜头表照常 load——这是整个修复的存在理由
        assert shots.threshold("春物") == 10.0
        assert shots.threshold("天气之子") == 8.0

    def test_这部番没标定就当场失败(self, conf):
        with pytest.raises(SystemExit):
            shots.threshold("没标定的番")

    def test_旧的单标量配置拒绝沿用(self, conf):
        # 静默把单标量当全局值用 = 回到「抄默认值」，必须显式报错逼人迁移
        (conf / "project.json").write_text(
            json.dumps({"visual": {"scene_threshold": 10.0}}), encoding="utf-8")
        shots.paths._CONF = None
        with pytest.raises(SystemExit):
            shots.threshold("春物")


class TestThresholdPerKey:
    """扁平集键覆盖（2026-09-07，EGOIST 企划池）：`"<番>/<集键>"` 优先，回退 `"<番>"`。
    SP 池里 MV/Live/微动共用番名键，load() 对账不能因换键把已建表判死。"""

    @pytest.fixture
    def conf(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots.paths, "CONFIG", tmp_path)
        monkeypatch.setattr(shots.paths, "_CONF", None)
        (tmp_path / "project.json").write_text(json.dumps(
            {"visual": {"scene_threshold": {"EGOIST": 10.0, "EGOIST/SP04": 25.0,
                                            "春物": 10.0}}}),
            encoding="utf-8")
        return tmp_path

    def test_集键覆盖命中(self, conf):
        assert shots.threshold("EGOIST", "SP04") == 25.0

    def test_无覆盖回退番键(self, conf):
        assert shots.threshold("EGOIST", "SP01") == 10.0
        assert shots.threshold("EGOIST") == 10.0

    def test_番键缺失照常失败(self, conf):
        with pytest.raises(SystemExit):
            shots.threshold("甲铁城", "SP01")

    def test_老番标量配置零漂移(self, conf):
        assert shots.threshold("春物") == 10.0
        assert shots.threshold("春物", "S01E01") == 10.0   # 传了集键也回退到番键

    def test_load对账按覆盖值(self, conf, tmp_path):
        # SP04 表内记 25.0：按覆盖值放行；番键换成别的数也不许判死这张表
        d = {"meta": {"scene_threshold": 25.0, "min_shot": shots.min_shot()},
             "shots": []}
        (tmp_path / "EGOIST_SP04.json").write_text(json.dumps(d), encoding="utf-8")
        assert shots.load("EGOIST", "SP04", out_dir=tmp_path)["meta"]["scene_threshold"] == 25.0

    def test_load对账按覆盖值拦截(self, conf, tmp_path):
        # 表内值与覆盖值不一致照旧判死——覆盖只改变「拿什么对」，不放松「对不对」
        d = {"meta": {"scene_threshold": 10.0, "min_shot": shots.min_shot()},
             "shots": []}
        (tmp_path / "EGOIST_SP04.json").write_text(json.dumps(d), encoding="utf-8")
        with pytest.raises(SystemExit):
            shots.load("EGOIST", "SP04", out_dir=tmp_path)


class TestGallery:
    """镜头画廊（2026-09-07，ADR-0012）：零依赖单文件 HTML，看图选锚点。"""

    @pytest.fixture
    def lib(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots.paths, "CONFIG", tmp_path / "cfg")
        monkeypatch.setattr(shots.paths, "_CONF", None)
        (tmp_path / "cfg").mkdir()
        (tmp_path / "cfg" / "project.json").write_text(json.dumps(
            {"visual": {"scene_threshold": {"EGOIST": 10.0, "EGOIST/SP05": 8.0}}}),
            encoding="utf-8")
        shots_dir, frames_dir = tmp_path / "shots", tmp_path / "shots" / "frames"
        table = {"meta": {"scene_threshold": 8.0, "min_shot": shots.min_shot(),
                          "source": "/x/SP05.mp4"},
                 "shots": [{"i": 0, "start": 0.0, "end": 1.4, "rep": 0.7},
                           {"i": 1, "start": 1.4, "end": 63.44, "rep": 30.0}]}
        shots_dir.mkdir()
        (shots_dir / "EGOIST_SP05.json").write_text(json.dumps(table), encoding="utf-8")
        return shots_dir, frames_dir

    def test_时间格式化(self):
        assert shots._fmt_t(0.7) == "00:00.70"
        assert shots._fmt_t(1062.04) == "17:42.04"
        assert shots._fmt_t(6510.0) == "108:30.00"   # 三位分钟（剧场版/长 Live）

    def test_生成画廊(self, lib):
        shots_dir, frames_dir = lib
        fr = frames_dir / "EGOIST_SP05"
        fr.mkdir(parents=True)
        (fr / "00001.jpg").write_bytes(b"x")
        (fr / "00002.jpg").write_bytes(b"x")
        dest = shots.gallery("EGOIST", "SP05", out_dir=shots_dir, dest_dir=frames_dir)
        html = dest.read_text(encoding="utf-8")
        assert dest.name == "EGOIST_SP05_gallery.html"
        assert 'src="frames/EGOIST_SP05/00002.jpg"' in html       # 相对路径，非 base64
        assert "锚点: EGOIST SP05 00:01.40" in html                # 复制文本取 start 小数秒
        assert "00:00.00 – 00:01.40（1.4s）" in html
        assert html.count('class="card"') == 2
        assert "execCommand" in html                               # file:// 兜底在

    def test_帧缺失报错不产出裂图(self, lib):
        shots_dir, frames_dir = lib
        with pytest.raises(SystemExit):
            shots.gallery("EGOIST", "SP05", out_dir=shots_dir, dest_dir=frames_dir)
        fr = frames_dir / "EGOIST_SP05"
        fr.mkdir(parents=True)
        (fr / "00001.jpg").write_bytes(b"x")                     # 只抽了一张，对不上
        with pytest.raises(SystemExit):
            shots.gallery("EGOIST", "SP05", out_dir=shots_dir, dest_dir=frames_dir)

    def test_对账用集键覆盖值(self, lib):
        # SP05 表内记 8.0（覆盖值）——画廊经 load() 对账，覆盖机制必须兜住
        fr = lib[1] / "EGOIST_SP05"
        fr.mkdir(parents=True)
        for n in (1, 2):
            (fr / f"{n:05d}.jpg").write_bytes(b"x")
        assert shots.gallery("EGOIST", "SP05", out_dir=lib[0], dest_dir=lib[1]).exists()


class TestRebuildAlsoCut:
    """手动补刀（ADR-0012）：scdet 对黑底缓出失明，人眼看准的切点注入为强制边界。"""

    @pytest.fixture
    def lib(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots.paths, "CONFIG", tmp_path / "cfg")
        monkeypatch.setattr(shots.paths, "_CONF", None)
        (tmp_path / "cfg").mkdir()
        (tmp_path / "cfg" / "project.json").write_text(json.dumps(
            {"visual": {"scene_threshold": {"EGOIST": 10.0}}}), encoding="utf-8")
        shots_dir = tmp_path / "shots"
        shots_dir.mkdir()
        # 600s 片子：只有两个够分的自动切点，消散区（500s 附近）对检测失明
        table = {"meta": {"scene_threshold": 10.0, "min_shot": shots.min_shot(),
                          "duration": 600.0, "source": "/x/v.mp4"},
                 "cuts": [[100.0, 12.0], [300.0, 15.0]],
                 "shots": []}
        (shots_dir / "EGOIST_SP05.json").write_text(json.dumps(table), encoding="utf-8")
        return shots_dir

    def _bounds(self, lib):
        d = json.loads((lib / "EGOIST_SP05.json").read_text(encoding="utf-8"))
        return d, [(s["start"], s["end"]) for s in d["shots"]]

    def test_注入强制切点(self, lib):
        shots.rebuild("EGOIST", "SP05", out_dir=lib, also_cut=[500.0, 520.5])
        d, bounds = self._bounds(lib)
        assert (500.0, 520.5) in bounds
        assert d["meta"]["manual_cuts"] == [500.0, 520.5]     # 留痕可审计

    def test_重放与幂等(self, lib):
        shots.rebuild("EGOIST", "SP05", out_dir=lib, also_cut=[500.0])
        shots.rebuild("EGOIST", "SP05", out_dir=lib)          # 不带参数重建
        d, bounds = self._bounds(lib)
        assert any(a == 500.0 for a, _ in bounds)             # 手动切点仍在
        assert d["meta"]["manual_cuts"] == [500.0]

    def test_越界时刻当场报(self, lib):
        with pytest.raises(SystemExit):
            shots.rebuild("EGOIST", "SP05", out_dir=lib, also_cut=[700.0])

    def test_时刻解析(self):
        assert shots._mmss("12:55,13:10.5") == [775.0, 790.5]
        for bad in ("abc", "12", "12:", "12:abc", "12:-5", "12:55,"):
            with pytest.raises(SystemExit):
                shots._mmss(bad)
