"""时间网格：行级还原、合并/切开、吸附、首尾空洞。

blocks() 是纯函数，全部用合成单元测。**单元必须按 subindex 的滑窗结构造**
（WINDOW=2, step=1：units[i] 覆盖第 i、i+1 两行）——拿单行时间当单元测
测的是另一种数据结构。真实数据对账（天气之子锚点）是手工验证项，不进测试。
"""

from pipeline.timeline import blocks


def windowed(*lines):
    """行级 (start, end, text) → WINDOW=2/step=1 滑窗单元（与 subindex 同构）。"""
    class U:
        pass
    out = []
    for i in range(len(lines) - 1):
        x = U()
        x.start, x.end, x.text = lines[i][0], lines[i + 1][1], lines[i][2]
        out.append(x)
    return out


def kinds(grid):
    return [b["kind"] for b in grid]


class TestBlocks:
    def test_间隔小于gap并块(self):
        # 三行台词间隔 2s、3s（<8）→ 一个对白块
        grid = blocks(windowed((1, 3, "a"), (5, 7, "b"), (10, 12, "c")),
                      [0.0, 50.0], 100.0, 8.0)
        assert kinds(grid).count("对白") == 1

    def test_间隔够大切开成无台词块(self):
        # A-B-C 挤在一起，D-E 挤在一起，C-D 之间 24s 沉默 → 两组对白夹一块无台词
        grid = blocks(windowed((1, 3, "a"), (30, 33, "b"), (34, 36, "c"),
                               (60, 63, "d"), (64, 66, "e")),
                      [0.0, 10.0, 25.0, 40.0, 60.0], 100.0, 8.0)
        assert kinds(grid) == ["对白", "无台词", "对白"]
        gap_block = grid[1]
        assert (gap_block["start"], gap_block["end"]) == (40.0, 60.0)

    def test_边界吸附切点(self):
        # 首行 12s 落在镜头 [10,25)，起点 floor 到 10；末行 20s 同镜头，ceil 到 25
        grid = blocks(windowed((12, 14, "a"), (18, 20, "b")),
                      [0.0, 10.0, 25.0, 40.0], 100.0, 8.0)
        talk = [b for b in grid if b["kind"] == "对白"]
        assert (talk[0]["start"], talk[0]["end"]) == (10.0, 25.0)

    def test_首尾空洞成无台词块(self):
        grid = blocks(windowed((12, 14, "a"), (16, 18, "b")), [0.0, 10.0, 25.0], 30.0, 8.0)
        assert grid[0]["kind"] == "无台词" and grid[0]["start"] == 0.0
        assert grid[-1]["kind"] == "无台词" and grid[-1]["end"] == 30.0

    def test_沉默在长镜头内部时并入前块不留零长块(self):
        # B-C 之间 11s 真沉默（可测），但 [10,60) 是一个整镜头、中间无切点：
        # 两个对白块的吸附跨度都被同一个镜头吞掉 → 并成一块，不许出零长度块
        grid = blocks(windowed((11, 13, "a"), (14, 16, "b"), (27, 29, "c"), (30, 32, "d")),
                      [0.0, 10.0, 60.0], 100.0, 8.0)
        assert kinds(grid) == ["无台词", "对白", "无台词"]
        talk = grid[1]
        assert (talk["start"], talk["end"]) == (10.0, 60.0)
        assert all(b["end"] > b["start"] for b in grid)

    def test_无切点时边界不吸附(self):
        grid = blocks(windowed((12, 14, "a"), (16, 18, "b")), [], 30.0, 8.0)
        talk = [b for b in grid if b["kind"] == "对白"]
        assert (talk[0]["start"], talk[0]["end"]) == (12.0, 18.0)

    def test_空单元返回空(self):
        assert blocks([], [0.0], 100.0, 8.0) == []
