"""镜头切分：切点 → 镜头表，以及时间点落在哪个镜头。

这个模块的风险在**过切与漏切的不对称**上，而这个不对称不是审美问题，是判据问题：

- 过切（一个镜头切成两个）：两个镜头内容几乎相同，各自都会被正确打标，
  对「这个镜头里有没有 X」这个布尔判断毫无影响。
- 漏切（两场戏并成一个镜头）：角色集合变成两场戏的并集，
  **「检测到 X」不再意味着「X 在这段时间里出现」**——整条角色过滤链的地基就是这句话。

所以 `cut` 里的合并逻辑要小心：它是唯一一处会**故意制造漏切**的代码。

期望值全部先在实现上跑过再写进断言（CLAUDE.md「写测试的两条纪律」）。
"""

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
