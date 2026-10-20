"""D99 PR-1：抓取全文留档（pipeline/agent/fetch_log.py）与三个抓取工具的接线。

要点：留的是**截断前**的全文（LLM 看的仍是截断版）；全文不进工具返回值、只回记录号；
按 URL 找记录时容忍 fragment / 末尾斜杠 / 大小写；全文被改过（sha256 对不上）不能作比对依据。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.agent import fetch_log, tools
from pipeline.agent.tools import ToolContext
from pipeline.agent.web import fetch_web
from tests.test_agent_web import _FakeResponse, _make_config, _pin_public_dns


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


class TestRecordLookup:
    def test_往返与URL归一(self, root):
        rid = fetch_log.record(url="https://Note.com/a/b/#x", final_url=None, kind="fetch", text="全文", sid="s1", root=root)
        hits = fetch_log.lookup("https://note.com/a/b", root=root)
        assert [h["id"] for h in hits] == [rid]
        assert fetch_log.read_text(hits[0], root=root) == "全文"
        assert hits[0]["chars"] == 2 and hits[0]["sid"] == "s1"

    def test_按最终URL也找得到(self, root):
        fetch_log.record(url="https://short.example/x", final_url="https://long.example/article/1", kind="crawl",
                         text="t", sid=None, root=root)
        assert len(fetch_log.lookup("https://long.example/article/1", root=root)) == 1
        assert len(fetch_log.lookup("https://short.example/x", root=root)) == 1

    def test_查询串不同就是不同页(self, root):
        fetch_log.record(url="https://a.example/p?id=1", final_url=None, kind="fetch", text="t", sid=None, root=root)
        assert fetch_log.lookup("https://a.example/p?id=2", root=root) == []

    def test_sid过滤与新的在前(self, root):
        r1 = fetch_log.record(url="https://a.example/", final_url=None, kind="fetch", text="1", sid="s1", root=root)
        r2 = fetch_log.record(url="https://a.example/", final_url=None, kind="crawl", text="2", sid="s2", root=root)
        assert [h["id"] for h in fetch_log.lookup("https://a.example/", root=root)] == [r2, r1]
        assert [h["id"] for h in fetch_log.lookup("https://a.example/", sid="s1", root=root)] == [r1]

    def test_坏行跳过(self, root):
        fetch_log.record(url="https://a.example/", final_url=None, kind="fetch", text="1", sid=None, root=root)
        with (fetch_log.log_dir(root) / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write("{坏行\n")
        assert len(fetch_log.lookup("https://a.example/", root=root)) == 1

    def test_全文被改过不能作依据(self, root):
        fetch_log.record(url="https://a.example/", final_url=None, kind="fetch", text="原文", sid=None, root=root)
        hit = fetch_log.lookup("https://a.example/", root=root)[0]
        (fetch_log.log_dir(root) / hit["file"]).write_text("改过的原文", encoding="utf-8")
        with pytest.raises(ValueError, match="sha256"):
            fetch_log.read_text(hit, root=root)

    def test_记录里的路径越界拒读(self, root):
        fetch_log.record(url="https://a.example/", final_url=None, kind="fetch", text="原文", sid=None, root=root)
        hit = dict(fetch_log.lookup("https://a.example/", root=root)[0], file="../../../etc/passwd")
        with pytest.raises(ValueError, match="越界"):
            fetch_log.read_text(hit, root=root)

    def test_data不可达报错不自建(self, tmp_path):
        with pytest.raises(ValueError, match="data/ 不可达"):
            fetch_log.record(url="https://a.example/", final_url=None, kind="fetch", text="t", sid=None, root=tmp_path)
        assert not (tmp_path / "data").exists()


def test_fetch_web_keep_full给截断前全文(monkeypatch):
    _pin_public_dns(monkeypatch)
    body = ("<html><body>" + "阿" * 1500 + "</body></html>").encode("utf-8")
    out = fetch_web("https://example.org/b", config=_make_config(max_fetch_chars=1000), keep_full=True,
                    opener=lambda req, timeout=30.0: _FakeResponse(body, final_url="https://example.org/b"))
    assert len(out["text"]) == 1000 and out["truncated"] is True
    assert out["full_text"] == "阿" * 1500
    plain = fetch_web("https://example.org/b", config=_make_config(max_fetch_chars=1000),
                      opener=lambda req, timeout=30.0: _FakeResponse(body, final_url="https://example.org/b"))
    assert "full_text" not in plain


class TestToolWrappers:
    """三个抓取工具：全文进留档、不进返回值，返回值带记录号；会话号记进留档。"""

    def _ctx(self, root: Path) -> ToolContext:
        return ToolContext(scope="creative", root=root, session_id="sid-9")

    def test_web_fetch(self, root, monkeypatch):
        monkeypatch.setattr("pipeline.agent.web.fetch_web", lambda url, root=None, keep_full=False: {
            "url": url, "final_url": url, "text": "截断版", "truncated": True, **({"full_text": "完整全文"} if keep_full else {})})
        res = tools._tool_web_fetch({"url": "https://a.example/p"}, self._ctx(root))
        assert "full_text" not in res and res["text"] == "截断版"
        hit = fetch_log.lookup("https://a.example/p", root=root)[0]
        assert hit["id"] == res["fetch_record"] and hit["kind"] == "fetch" and hit["sid"] == "sid-9"
        assert fetch_log.read_text(hit, root=root) == "完整全文"

    def test_crawl(self, root, monkeypatch):
        monkeypatch.setattr("pipeline.agent.web_crawl.crawl_page",
                            lambda url, reason, stealth=False, root=None, keep_full=False: {
                                "url": url, "final_url": url + "/final", "markdown": "截断", **({"full_text": "**全文**"} if keep_full else {})})
        res = tools._tool_crawl({"url": "https://a.example/p", "reason": "403"}, self._ctx(root))
        assert "full_text" not in res
        hit = fetch_log.lookup("https://a.example/p/final", root=root)[0]
        assert hit["kind"] == "crawl" and fetch_log.read_text(hit, root=root) == "**全文**"

    def test_browser_只有extract_text留档(self, root, monkeypatch):
        def fake(action, reason, *, url=None, episode_dir=None, scope="creative", root=None, keep_full=False, **_kw):
            if action == "navigate":
                return {"action": "navigate", "url": url, "final_url": url, "title": "t"}
            return {"action": "extract_text", "url": "https://a.example/now", "text": "截断", "truncated": True,
                    **({"full_text": "登录后全文"} if keep_full else {})}
        monkeypatch.setattr("pipeline.agent.web_browser.browser_action", fake)
        nav = tools._tool_browser({"action": "navigate", "url": "https://a.example/now", "reason": "要登录"}, self._ctx(root))
        assert "fetch_record" not in nav
        ext = tools._tool_browser({"action": "extract_text", "reason": "读正文"}, self._ctx(root))
        assert "full_text" not in ext and ext["fetch_record"]
        assert fetch_log.read_text(fetch_log.lookup("https://a.example/now", root=root)[0], root=root) == "登录后全文"

    def test_留档失败不连累抓取但如实标出(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pipeline.agent.web.fetch_web", lambda url, root=None, keep_full=False: {
            "url": url, "final_url": url, "text": "t", "full_text": "全文"})
        res = tools._tool_web_fetch({"url": "https://a.example/p"}, self._ctx(tmp_path))  # 没有 data/
        assert res["text"] == "t" and res["fetch_record"] is None
        assert "留档失败" in res["fetch_record_error"]
        assert "full_text" not in res


def test_session把会话号交给工具上下文():
    """ToolContext.session_id 由宿主填（session._tool_context 取 SessionHost.sid），不取模型参数。"""
    import inspect
    from pipeline.agent import session

    src = inspect.getsource(session.AgentSession._tool_context)
    assert "session_id=self.host.sid" in src
    assert json.dumps(ToolContext().session_id) == "null"
