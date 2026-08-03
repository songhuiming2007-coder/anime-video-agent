"""画面语义的每番配置：探针查询（正例 / 反例）与门槛。

探针本身要模型和片源，按 CLAUDE.md「只测纯函数」不进测试。
测的是**读配置这一层**，因为它有一条机器判得了的不变量：
**正例与反例不许有交集。**

交集会静默毁掉门槛的定法。噪声地板取的是反例 Top-1 的最大值——
一条本该命中的查询混进反例，地板就被它自己的高分顶上去，
门槛跟着定高，于是真实查询大面积判 `no_match`。
**而这不会报错**，只会表现成「画面通道好像不太好用」。

「反例在题材上真的不可能出现」这一条机器判不了，只能靠人挑，
理由写在 `config/scenes.json` 的 `_negative_note` 里。
"""

import json

import pytest

from pipeline import vindex, vprobe


class TestShotIndex:
    """时间点 → 镜头号。探针拿它给命中配代表帧，**配错了不会报错**——
    图照样贴出来，只是贴的是隔壁镜头，而人正是靠这张图判命中对不对。

    镜头表由调用方读一次传进来（原先每个命中都重读一遍 shots.json）。
    """

    SH = [{"i": 0, "start": 0.0, "end": 10.0},
          {"i": 1, "start": 10.0, "end": 20.0}]

    def test_落在镜头内(self):
        assert vprobe._shot_index(self.SH, 5.0) == 0
        assert vprobe._shot_index(self.SH, 15.0) == 1

    def test_镜头起点算它自己(self):
        # 检索返回的 `start` 就是镜头起点，这是最常走的一条路。
        # `at()` 是左闭右开，直接查起点会命中本镜头——探针加的那 1ms 偏移
        # 是为了避开浮点表示误差，不该把结果推到下一个镜头
        assert vprobe._shot_index(self.SH, 10.0) == 1

    def test_超出末尾退回_0_而不是崩(self):
        # 宁可贴错一张图也不该让整次探针挂掉；真错了人看图就发现了
        assert vprobe._shot_index(self.SH, 999.0) == 0


CONF = {
    "春物": {
        "queries": ["空无一人的教室", "樱花树"],
        "negative": ["太空中的宇宙飞船", "火山喷发"],
    }
}


@pytest.fixture
def conf(tmp_path):
    def write(obj=CONF):
        p = tmp_path / "scenes.json"
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return p
    return write


class TestSceneQueries:
    def test_读出正反两张表(self, conf):
        pos, neg = vindex.scene_queries("春物", conf())
        assert pos == ["空无一人的教室", "樱花树"]
        assert neg == ["太空中的宇宙飞船", "火山喷发"]

    def test_正反例有交集就失败(self, conf):
        # **这条是本文件的重点。** 交集不会让任何东西崩，只会把门槛悄悄定高。
        bad = {"春物": {"queries": ["樱花树"], "negative": ["樱花树", "火山喷发"]}}
        with pytest.raises(SystemExit, match="交集"):
            vindex.scene_queries("春物", conf(bad))

    def test_没有反例就失败(self, conf):
        # 没有反例就没有噪声地板，门槛无从定起——而拍一个数上去不会报错
        with pytest.raises(SystemExit, match="negative"):
            vindex.scene_queries("春物", conf({"春物": {"queries": ["樱花树"], "negative": []}}))

    def test_没有正例就失败(self, conf):
        with pytest.raises(SystemExit, match="queries"):
            vindex.scene_queries("春物", conf({"春物": {"queries": [], "negative": ["火山"]}}))

    def test_没有这部番就失败(self, conf):
        # **不许退回别的番的表。** 反例照搬会把噪声地板测成一个真命中的高分
        with pytest.raises(SystemExit, match="没有《紫罗兰》"):
            vindex.scene_queries("紫罗兰", conf())

    def test_没有配置文件就失败(self, tmp_path):
        with pytest.raises(SystemExit):
            vindex.scene_queries("春物", tmp_path / "缺.json")

    def test_下划线开头的键不是番(self, conf):
        # 与 characters.json 同构，`_note` 这类注释键不该被当成一部番
        with pytest.raises(SystemExit):
            vindex.scene_queries("_note", conf({**CONF, "_note": "说明"}))


class TestSwitchAnime:
    """换番动作验证（standard.md 第二节 R6 + 第十五节）。

    **「当前番能跑」不构成证据。** 这一组测的是「加一部番要不要改代码」——
    答案必须是不要：只在 `config/scenes.json` 里多一个键。
    """

    TWO = {
        "春物": {"queries": ["空无一人的教室"], "negative": ["太空中的宇宙飞船"],
                 "no_match": 0.30},
        "紫罗兰": {"queries": ["战场上的废墟"], "negative": ["现代都市的霓虹灯"],
                   "no_match": 0.27},
    }

    def test_加一部番不用改代码(self, conf):
        pos, neg = vindex.scene_queries("紫罗兰", conf(self.TWO))
        assert pos == ["战场上的废墟"]
        assert neg == ["现代都市的霓虹灯"]

    def test_两部番互不串味(self, conf):
        # **反例串味是最隐蔽的**：「太空中的宇宙飞船」在春物里量噪声地板，
        # 在科幻番里是正片，串过去会把地板测成一个真命中的高分
        p = conf(self.TWO)
        assert vindex.scene_queries("春物", p)[1] != vindex.scene_queries("紫罗兰", p)[1]

    def test_门槛按番各存各的(self, conf):
        # 门槛是拿该番的反例在该番的镜头上量的，不是模型常数。
        # 放全局块里换番会静默沿用——这正是本项目反复要堵的那类失败
        p = conf(self.TWO)
        assert vindex.scene_conf("春物", p)["no_match"] == 0.30
        assert vindex.scene_conf("紫罗兰", p)["no_match"] == 0.27


class TestSceneEnabled:
    """通道启用与否，判据是**门槛标定了没有**。

    这条存在的意义是不让六条数字永远红着。第 2 层是有意不建的
    （春物实测门槛立不住，见 ADR-0003），它就该被报成「未启用」，
    而不是报成「缺 40 集」——**一道永远失败的门禁，人会学会忽略它**，
    这个项目在门禁上踩过的坑全是这个形状。
    """

    def test_标定过门槛就算启用(self, conf):
        p = conf({"春物": {"queries": ["a"], "negative": ["b"], "no_match": 0.42}})
        assert vindex.scene_enabled("春物", p) is True

    def test_没标定门槛就是未启用(self, conf):
        # 判据不是「索引建了没有」：探针跑一集会留下一份索引，
        # 但门槛没定，`clips` 那边写 `场景` 照样当场失败，这一层等于不存在
        p = conf({"春物": {"queries": ["a"], "negative": ["b"]}})
        assert vindex.scene_enabled("春物", p) is False

    def test_没有这部番也是未启用而不是抛错(self, conf):
        # status 会对任意一部番调它，缺配置是常态，不该让整条状态命令崩掉
        assert vindex.scene_enabled("紫罗兰", conf()) is False

    def test_没有配置文件也是未启用(self, tmp_path):
        assert vindex.scene_enabled("春物", tmp_path / "缺.json") is False


class TestShippedConf:
    """仓库里真实的 config/scenes.json 也要过同一套判据。

    测试用的是 tmp 里的假配置，真配置写错了照样没人发现——而它才是真正会被用的那份。
    """

    def test_春物的表是合法的(self):
        pos, neg = vindex.scene_queries("春物")
        assert len(pos) >= 8, "ADR-0003 要求手写 10 条这条流水线真会用到的查询"
        assert neg
