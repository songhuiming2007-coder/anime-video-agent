"""D122：browser 截图动作。替身浏览器，不出网、不碰真实 data/。"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pipeline import paths
from pipeline.agent import web_browser as wb
from pipeline.agent.tools import ToolContext, _tool_browser
from tests.test_agent_crawl_browser import _make_full_web_root, _pin_public_dns


def _png(w: int, h: int, text: str = "") -> bytes:
    """白底截图；给了 text 就逐行真画上去（D141 起截图会被文字识别，替身也得是真图）。"""
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), "white")
    if text:
        font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 48, index=0)
        for i, line in enumerate(text.splitlines()):
            ImageDraw.Draw(img).text((40, 40 + i * 80), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _ShotSession:
    def __init__(self, *, element_text: str = "君の知らない物語 週間5位 66週ランクイン", missing: str | None = None,
                 size: tuple[int, int] = (1920, 900), drawn: str | None = None) -> None:
        """drawn：图上实际画的字；缺省 = 元素文字（页面正常显示），"" = DOM 里有、图上没画出来。"""
        self.element_text, self.missing, self.size = element_text, missing, size
        self.drawn = element_text if drawn is None else drawn
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def screenshot(self, url: str, *, selector: str | None, text: str | None, timeout_s: float) -> dict[str, Any]:
        self.calls.append({"url": url, "selector": selector, "text": text})
        if self.missing:
            return {"final_url": url, "missing": self.missing, "css_size": [708, 4065]}
        return {"final_url": url, "title": "ORICON 年間ランキング", "png": _png(*self.size, self.drawn),
                "element_text": self.element_text, "css_size": [self.size[0] // 2, self.size[1] // 2],
                "text": "ORICON 年間ランキング " + self.element_text,
                "html": '<html><body><img src="https://example.com/a.png"></body></html>'}

    def close(self) -> None:
        self.closed = True


class _Main:
    """截图前已经开着的平时会话。"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pin_public_dns(monkeypatch)
    r = _make_full_web_root(tmp_path)
    (r / "data" / "library" / "incoming").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", r)
    monkeypatch.setattr(paths, "DATA", r / "data")
    monkeypatch.setattr(wb, "_emit_browser_started", lambda **k: None)
    wb._reset_session_for_testing()
    yield r
    wb._reset_session_for_testing()


def _launcher(sess: _ShotSession, seen: list[dict[str, Any]]):
    def launch(*, user_data_dir: Path, headed: bool, prefs: dict | None = None) -> _ShotSession:
        seen.append({"user_data_dir": user_data_dir, "headed": headed, "prefs": prefs})
        return sess
    return launch


def test_截图关掉平时会话_按两倍密度另起_截完即关(root):
    main = _Main()
    wb._SESSION = main
    sess, seen = _ShotSession(), []
    out = wb.browser_action("screenshot", "证榜单名次", url="https://example.com/rank", text="週間5位",
                            launch_fn=_launcher(sess, seen), root=root)
    assert main.closed and sess.closed and wb._SESSION is None, "下次 navigate 要按平时配置重启"
    assert seen[0]["prefs"] == wb.SHOT_PREFS
    assert seen[0]["user_data_dir"] == (root / "data" / "browser-profile").resolve(), "同一份 profile，登录态在里面"
    assert sess.calls == [{"url": "https://example.com/rank", "selector": None, "text": "週間5位"}]
    assert out["png"].startswith(b"\x89PNG") and "66週" in out["element_text"]


@pytest.mark.parametrize("kw", [{}, {"selector": "#rank", "text": "週間5位"}])
def test_selector与text恰好给一个(root, kw):
    with pytest.raises(ValueError, match="selector 或 text"):
        wb.browser_action("screenshot", "x", url="https://example.com/", launch_fn=_launcher(_ShotSession(), []),
                          root=root, **kw)


def test_expect_text不在截图里_不交图(root):
    sess = _ShotSession(element_text="週刊アスキー 2008年 特集ページ")
    with pytest.raises(ValueError, match="截到的元素里没有「好不容易见面就」"):
        wb.browser_action("screenshot", "证引语", url="https://example.com/", selector="article",
                          expect_text="好不容易见面就", launch_fn=_launcher(sess, []), root=root)
    assert sess.closed


def test_expect_text按事实库同口径归一(root):
    """空白、全半角、引号差异不算不一样（与 D99 fact_add 的逐字比对同一个 norm_exact）。"""
    sess = _ShotSession(element_text="『君の知らない物語』\n週間 5 位")
    out = wb.browser_action("screenshot", "x", url="https://example.com/", selector="table",
                            expect_text="「君の知らない物語」週間５位", launch_fn=_launcher(sess, []), root=root)
    assert out["action"] == "screenshot"


needs_vision = pytest.mark.skipif(not shutil.which("swift"), reason="需要 swift（macOS Vision 文字识别）")


@needs_vision
def test_DOM里有_图上没画出来_不交图(root):
    """D141：2026-10-24 卷三两张 ORICON `id_` 页截图，DOM 里有名次、图上没画出来，expect_text 照样通过。"""
    sess = _ShotSession(element_text="君の知らない物語 週間5位", drawn="ORICON")
    with pytest.raises(ValueError, match="截图上却认不出来.*图上认出的最接近一段"):
        wb.browser_action("screenshot", "证名次", url="https://example.com/", selector="table",
                          expect_text="週間5位", launch_fn=_launcher(sess, []), root=root)


@needs_vision
def test_图上逐字认出_回执记exact(root):
    sess = _ShotSession(element_text="君の知らない物語 週間5位")
    out = wb.browser_action("screenshot", "x", url="https://example.com/", selector="table",
                            expect_text="週間5位", launch_fn=_launcher(sess, []), root=root)
    assert out["ocr_match"] == "exact"


@needs_vision
def test_标点有出入_只比字母数字命中_回执记approx(root):
    # DOM 与图上都是「物語 週間5位」，要证的话写成「物語、週間5位」：逐字差一个顿号，字母数字一致
    sess = _ShotSession(element_text="君の知らない物語、週間5位", drawn="君の知らない物語 週間5位")
    out = wb.browser_action("screenshot", "x", url="https://example.com/", selector="table",
                            expect_text="物語、週間5位", launch_fn=_launcher(sess, []), root=root)
    assert out["ocr_match"] == "approx"


def test_没有swift_说清楚且不交图(root, monkeypatch):
    from pipeline.agent import ocr
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="本机没有 swift"):
        wb.browser_action("screenshot", "x", url="https://example.com/", selector="table",
                          expect_text="週間5位", launch_fn=_launcher(_ShotSession(), []), root=root)


def test_没给expect_text不做识别(root, monkeypatch):
    from pipeline.agent import ocr
    monkeypatch.setattr(ocr, "recognize", lambda png: (_ for _ in ()).throw(AssertionError("不该识别")))
    out = wb.browser_action("screenshot", "x", url="https://example.com/", selector="table",
                            launch_fn=_launcher(_ShotSession(), []), root=root)
    assert "ocr_match" not in out


def test_找不到文字说清楚(root):
    with pytest.raises(ValueError, match="找不到包含「不存在的字」的元素"):
        wb.browser_action("screenshot", "x", url="https://example.com/", text="不存在的字",
                          launch_fn=_launcher(_ShotSession(missing="missing"), []), root=root)


def test_文字跨元素_命中的是整篇就拒(root):
    with pytest.raises(ValueError, match="708×4065.*换一段在同一行里原样出现的短文字"):
        wb.browser_action("screenshot", "x", url="https://example.com/", text="週間5位",
                          launch_fn=_launcher(_ShotSession(missing="big"), []), root=root)


def test_工具层落盘_留档_台账_回执报放大倍数(root, monkeypatch):
    monkeypatch.setattr(wb, "_default_launch_fn", _launcher(_ShotSession(size=(1920, 900)), []))
    out = _tool_browser({"action": "screenshot", "url": "https://example.com/rank", "text": "週間5位",
                         "reason": "证段 20 的榜单名次", "name": "ORICON 君の知らない物語"},
                        ToolContext(root=root, session_id="s1"))
    f = Path(out["file"])
    assert f.parent == root / "data" / "library" / "incoming" and f.name.startswith("shot-ORICON")
    with Image.open(f) as im:
        assert im.size == (1920, 900) and out["size"] == [1920, 900]
    assert "png" not in out and "full_text" not in out, "图片字节与全文不进 LLM"
    assert out["magnify"] == 0.96 and out["gate"] == "PASS"     # 2.13:1 缩进安全框：1604/1920 × 1.15
    led = json.loads((root / "data" / "library" / "incoming" / "fetched.json").read_text(encoding="utf-8"))
    assert led[-1]["file"] == str(f) and led[-1]["type"] == "scan" and led[-1]["source"] == "screenshot"
    assert led[-1]["fetch_record"] == out["fetch_record"] and out["fetch_record"]
    idx = (root / "data" / "library" / "fetch-log" / "index.jsonl").read_text(encoding="utf-8")
    assert "https://example.com/rank" in idx


def test_窄截图回执直说门禁会拒(root, monkeypatch):
    monkeypatch.setattr(wb, "_default_launch_fn", _launcher(_ShotSession(size=(960, 320)), []))
    out = _tool_browser({"action": "screenshot", "url": "https://example.com/", "selector": "table",
                         "reason": "x"}, ToolContext(root=root))
    assert out["gate"].startswith("FAIL：入池要放大")


# ---------------------------------------------------------------- D138：命令行截图


def test_命令行截图_落盘留档台账_记命令行与调用方(root):
    from pipeline.agent import fetch_log
    from pipeline.agent import screenshot as S

    out = S.shoot("https://example.com/rank", reason="证段 42 的周榜名次", text="週間5位", name="ORICON",
                  caller="claude-code", root=root, launch_fn=_launcher(_ShotSession(), []))
    f = Path(out["file"])
    assert f.is_file() and f.parent == root / "data" / "library" / "incoming"
    led = json.loads((root / "data" / "library" / "incoming" / "fetched.json").read_text(encoding="utf-8"))
    assert led[-1]["file"] == str(f) and led[-1]["source"] == "screenshot" and led[-1]["caller"] == "claude-code"
    assert led[-1]["why"] == "证段 42 的周榜名次"
    e = fetch_log.lookup("https://example.com/rank", root=root)[0]
    assert e["id"] == out["fetch_record"] and e["origin"] == "cli" and e["caller"] == "claude-code"


def test_命令行截图_expect_text不在图里_不落盘不进台账(root):
    from pipeline.agent import screenshot as S

    with pytest.raises(ValueError, match="截到的元素里没有"):
        S.shoot("https://example.com/", reason="x", selector="article", expect_text="好不容易见面就",
                root=root, launch_fn=_launcher(_ShotSession(element_text="別の話"), []))
    inc = root / "data" / "library" / "incoming"
    assert not list(inc.glob("shot-*.png")) and not (inc / "fetched.json").exists()


@needs_vision
def test_命令行截图_图上识别结果进台账与回执(root):
    from pipeline.agent import screenshot as S

    out = S.shoot("https://example.com/rank", reason="证名次", selector="table", expect_text="週間5位",
                  root=root, launch_fn=_launcher(_ShotSession(), []))
    led = json.loads((root / "data" / "library" / "incoming" / "fetched.json").read_text(encoding="utf-8"))
    assert out["ocr_match"] == led[-1]["ocr_match"] == "exact"


@needs_vision
def test_命令行截图_图上没画出来_不落盘不进台账(root):
    from pipeline.agent import screenshot as S

    with pytest.raises(ValueError, match="截图上却认不出来"):
        S.shoot("https://example.com/", reason="x", selector="table", expect_text="週間5位",
                root=root, launch_fn=_launcher(_ShotSession(drawn=""), []))
    inc = root / "data" / "library" / "incoming"
    assert not list(inc.glob("shot-*.png")) and not (inc / "fetched.json").exists()


def test_AVA截图仍记会话里抓(root, monkeypatch):
    from pipeline.agent import fetch_log

    monkeypatch.setattr(wb, "_default_launch_fn", _launcher(_ShotSession(), []))
    _tool_browser({"action": "screenshot", "url": "https://example.com/rank", "text": "週間5位", "reason": "x"},
                  ToolContext(root=root, session_id="s1"))
    e = fetch_log.lookup("https://example.com/rank", root=root)[0]
    assert e["origin"] == "session" and e["sid"] == "s1" and not e.get("caller")
    led = json.loads((root / "data" / "library" / "incoming" / "fetched.json").read_text(encoding="utf-8"))
    assert "caller" not in led[-1]


@pytest.mark.parametrize("argv", [["https://example.com/", "--reason", "x"],
                                  ["https://example.com/", "--selector", "a", "--text", "b", "--reason", "x"],
                                  ["https://example.com/", "--text", "b"]])
def test_命令行参数_selector与text恰好一个_reason必填(root, monkeypatch, argv):
    """挂上 root 与替身浏览器：参数校验被改坏时也只碰临时目录，不出网、不写真实 data/。"""
    from pipeline.agent import screenshot as S

    monkeypatch.setattr(wb, "_default_launch_fn", _launcher(_ShotSession(element_text="b"), []))
    with pytest.raises(SystemExit):
        S.main(argv)
    assert not list((root / "data" / "library" / "incoming").glob("shot-*.png"))


# ---------------------------------------------------------------- D139：截图像素密度


@pytest.mark.parametrize("scale", [3, 4])
def test_scale按倍数起截图会话_只换像素密度(root, scale):
    sess, seen = _ShotSession(), []
    wb.browser_action("screenshot", "证引语", url="https://example.com/a", text="週間5位", scale=scale,
                      launch_fn=_launcher(sess, seen), root=root)
    assert seen[0]["prefs"] == {**wb.SHOT_PREFS, "layout.css.devPixelsPerPx": f"{scale}.0"}
    assert wb.SHOT_PREFS == {"layout.css.devPixelsPerPx": "2.0"}, "缺省口径不被改写"
    assert sess.closed


@pytest.mark.parametrize("bad", [1, 5, True, "3", 3.0, None])
def test_scale只收2到4的整数_不起浏览器(root, bad):
    seen: list[dict[str, Any]] = []
    with pytest.raises(ValueError, match="scale 只能是 2/3/4"):
        wb.browser_action("screenshot", "x", url="https://example.com/", text="週間5位", scale=bad,
                          launch_fn=_launcher(_ShotSession(), seen), root=root)
    assert not seen


def test_命令行scale传到截图会话(root, monkeypatch):
    from pipeline.agent import screenshot as S

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(wb, "_default_launch_fn", _launcher(_ShotSession(), seen))
    assert S.main(["https://example.com/rank", "--text", "週間5位", "--reason", "证段 53", "--scale", "4"]) == 0
    assert seen[0]["prefs"]["layout.css.devPixelsPerPx"] == "4.0"
    with pytest.raises(SystemExit):
        S.main(["https://example.com/rank", "--text", "週間5位", "--reason", "x", "--scale", "5"])
    assert len(seen) == 1


def test_AVA工具scale传到截图会话_不给就是两倍(root, monkeypatch):
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(wb, "_default_launch_fn", _launcher(_ShotSession(), seen))
    ctx = ToolContext(root=root, session_id="s1")
    _tool_browser({"action": "screenshot", "url": "https://example.com/a", "text": "週間5位", "reason": "x", "scale": 3}, ctx)
    _tool_browser({"action": "screenshot", "url": "https://example.com/b", "text": "週間5位", "reason": "x"}, ctx)
    assert [s["prefs"]["layout.css.devPixelsPerPx"] for s in seen] == ["3.0", "2.0"]


class _PickPage:
    """只走到按文字挑元素那一步：记下传给页面脚本的参数，回「找不到」。"""

    def __init__(self) -> None:
        self.url, self.args = "https://example.com/", None

    def goto(self, url: str, **kw: Any) -> None:
        self.url = url

    def evaluate(self, js: str, args: Any) -> dict[str, Any]:
        self.js, self.args = js, args
        return {"why": "missing"}


def test_按文字挑元素_一屏半按视口高兜底(monkeypatch):
    """密度调高后视口只剩一百多 CSS 高，「一屏半」的上限按 SHOT_MIN_VIEWPORT_H 兜底（D139）。"""
    page = _PickPage()
    adapter = wb._PlaywrightSessionAdapter.__new__(wb._PlaywrightSessionAdapter)
    monkeypatch.setattr(adapter, "_current_page", lambda: page, raising=False)
    out = adapter.screenshot("https://example.com/", selector=None, text="週間5位", timeout_s=5)
    assert out["missing"] == "missing"
    assert page.args == ["週間5位", wb.SHOT_MIN_W, wb.SHOT_MIN_H, wb.SHOT_MAX_H_VIEWPORTS, wb.SHOT_MIN_VIEWPORT_H]
    assert "Math.max(innerHeight, minVpH)" in page.js
