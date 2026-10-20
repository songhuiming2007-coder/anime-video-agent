"""D122：browser 截图动作。替身浏览器，不出网、不碰真实 data/。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pipeline import paths
from pipeline.agent import web_browser as wb
from pipeline.agent.tools import ToolContext, _tool_browser
from tests.test_agent_crawl_browser import _make_full_web_root, _pin_public_dns


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


class _ShotSession:
    def __init__(self, *, element_text: str = "君の知らない物語 週間5位 66週ランクイン", missing: str | None = None,
                 size: tuple[int, int] = (1920, 900)) -> None:
        self.element_text, self.missing, self.size = element_text, missing, size
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def screenshot(self, url: str, *, selector: str | None, text: str | None, timeout_s: float) -> dict[str, Any]:
        self.calls.append({"url": url, "selector": selector, "text": text})
        if self.missing:
            return {"final_url": url, "missing": self.missing, "css_size": [708, 4065]}
        return {"final_url": url, "title": "ORICON 年間ランキング", "png": _png(*self.size),
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
