"""review.py 渲染逻辑（ADR-0004 新字段展示）。

不碰 build()（会真的抽帧）——只测被提取出来的纯函数：
集号 + 降级链标记、在场分标签。这两个是人审界面上唯一能看到的
「集号有没有锁对」「在场分是检出还是漏检」的地方，展示错了等于
人审没看到关键信息（又是静默失败）。
"""

from pipeline.review import _ep_label, _presence_txt


class TestEpLabel:
    def test_no_episode_blank(self):
        assert _ep_label({}) == ""
        assert _ep_label({"episode": None}) == ""

    def test_episode_only(self):
        out = _ep_label({"episode": "S01E08"})
        assert "S01E08" in out and "集" in out
        assert "→" not in out

    def test_fell_back_season(self):
        out = _ep_label({"episode": "S01E08", "ep_fell_back": True, "ep_scope": 1})
        assert "→季内检索" in out and "→全空间检索" not in out

    def test_fell_back_global(self):
        out = _ep_label({"episode": "S01E08", "ep_fell_back": True, "ep_scope": 2})
        assert "→全空间检索" in out and "→季内检索" not in out

    def test_escapes_episode(self):
        # 集号来自稿件解析，含 < > 只会来自写错的稿件；不转义会注入 HTML
        out = _ep_label({"episode": "S01E<1>"})
        assert "<b>S01E<1></b>" not in out


class TestPresenceTxt:
    def test_none_blank(self):
        # 场景段没有在场分，显示空白而不是「在场 0」（0 = 有语义的未检出）
        assert _presence_txt(None) == ""

    def test_zero_flag(self):
        # 未检出：垫底是排片时故意放的，但必须让人看到「这段未必有那个人」。
        # 精确匹配不是 in——漏检显示成「在场 0.00」也含「在场 0」子串，
        # in 会把变异后的错误展示放过去（变异检验抓到的）
        assert _presence_txt(0.0) == '<span class="flag">在场 0</span>'

    def test_positive_two_decimals(self):
        # 0.976 → 0.98 是向上舍入；0.995 不测——它在二进制浮点里是
        # 0.99499…，格式化只会给 0.99，是浮点表示问题不是展示问题
        assert _presence_txt(0.973) == " · 在场 0.97"
        assert _presence_txt(0.976) == " · 在场 0.98"
