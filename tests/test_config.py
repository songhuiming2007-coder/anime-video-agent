"""`paths.conf`：把写死的常量搬进配置文件的那套机制。

这个函数的设计有一条硬规矩，写在它的 docstring 里：
**每个调用点都必须给出 default，而且 default 要等于原来写死的那个值。**

理由是配置文件是用来「调」的，不是用来「让程序能跑」的。把默认值搬进 json
会让「文件缺失」变成一种新的失败模式——而这个仓库已经有太多静默失败了。
所以这里测的重点全在**降级路径**：文件没有、键没有、层级中途撞上非字典，
三种情况都必须原样返回 default，不许抛异常、不许返回 None。

唯一允许炸的是 json 语法错误：那说明人改坏了配置，静默用默认值反而会让他
以为改生效了。
"""

import json

import pytest

from pipeline import paths


@pytest.fixture
def conf_dir(tmp_path, monkeypatch):
    """把配置目录指到 tmp，并清掉进程级缓存。

    `_CONF` 是模块全局的一次性缓存，不清的话第二个用例读到的还是第一个的内容。
    """
    monkeypatch.setattr(paths, "CONFIG", tmp_path)
    monkeypatch.setattr(paths, "_CONF", None)

    def write(obj):
        (tmp_path / "project.json").write_text(
            obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        paths._CONF = None
    return write


class TestLookup:
    def test_点分取嵌套值(self, conf_dir):
        conf_dir({"video": {"crf": "20"}})
        assert paths.conf("video.crf", "18") == "20"

    def test_取顶层值(self, conf_dir):
        conf_dir({"anime": {"default": "春物"}})
        assert paths.conf("anime.default") == "春物"

    def test_零和空串是合法值不会被当成缺失(self, conf_dir):
        # 曾经想过用 `or default` 写这个函数，那样 0 / "" / [] 会被默认值悄悄顶掉。
        conf_dir({"audio": {"lufs": 0}, "x": {"y": ""}})
        assert paths.conf("audio.lufs", -16) == 0
        assert paths.conf("x.y", "fallback") == ""


class TestFallback:
    def test_配置文件不存在就用_default(self, conf_dir, tmp_path):
        # 别人 clone 下来还没建配置，行为必须与从前完全一致
        paths._CONF = None
        assert not (tmp_path / "project.json").exists()
        assert paths.conf("video.crf", "18") == "18"

    def test_键不存在就用_default(self, conf_dir):
        conf_dir({"video": {"crf": "20"}})
        assert paths.conf("video.preset", "medium") == "medium"

    def test_中途撞上非字典也用_default(self, conf_dir):
        # `video` 被写成字符串，再往下取 `.crf` 不能炸
        conf_dir({"video": "1080p"})
        assert paths.conf("video.crf", "18") == "18"

    def test_不给_default_时返回_None(self, conf_dir):
        conf_dir({})
        assert paths.conf("anime.default") is None


class TestBrokenJson:
    def test_语法错误要当场报错而不是退到默认值(self, conf_dir):
        # 这是唯一该炸的情况。人明明改了配置却静默用默认值跑，
        # 他会以为改生效了——比直接失败糟得多。
        conf_dir("{ 这不是 json")
        with pytest.raises(SystemExit):
            paths.conf("video.crf", "18")


class TestCache:
    def test_只读一次盘(self, conf_dir, tmp_path):
        conf_dir({"video": {"crf": "20"}})
        assert paths.conf("video.crf", "18") == "20"
        (tmp_path / "project.json").write_text('{"video":{"crf":"30"}}', encoding="utf-8")
        # 没清 _CONF，仍然读到旧值——这是有意的：一次运行里配置不该中途变
        assert paths.conf("video.crf", "18") == "20"


class TestDeadKeysRemoved:
    """声而不用的键已删（2026-08-16 审计 2-37/2-38）：font_fallback 全库零读取；
    frames_per_shot 的 _frames_note 承诺了 shots.frames 不存在的调参能力。"""

    def test_死键没有回来(self):
        cfg = json.loads((paths.CONFIG / "project.json").read_text(encoding="utf-8"))
        assert "font_fallback" not in cfg["subtitle"]
        assert "frames_per_shot" not in cfg["visual"]
        assert all(not k.startswith("_") or k != "_frames_note"
                   for k in cfg["visual"])
