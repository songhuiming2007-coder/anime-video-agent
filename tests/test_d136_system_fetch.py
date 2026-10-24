"""D136：出处核对由系统确认，不再要求「AVA 自己抓过」。

底线不动（D124）：留档只由系统代码抓取后写，不认手写留档。变的是谁能触发抓取：
`acquire check` / `fact_add` 发现出处页没留档时当场抓，别的 agent 用 `python -m pipeline.agent.fetch`。
不出网：当场抓一律注入替身 fetcher；测 `fetch_and_record` 本身的用例替掉 fetch_web / crawl_page。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from pipeline import candidate_check as CC
from pipeline import facts
from pipeline.agent import fetch as F
from pipeline.agent import fetch_log

PAGE = "https://supercell.jp/news/1"
IMG = "https://supercell.jp/img/full/melt.jpg"
TEXT = "当時、自分は全部、感覚だけで曲を作っていた。"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "library" / "incoming").mkdir(parents=True)
    return tmp_path


def _system_page(root: Path, url: str, *, text: str = "正文", images=()):
    """替身 fetcher：照真实当场抓的口径落一份 origin=system 的留档，返回 None（成功）。"""
    def fetch(u: str):
        assert fetch_log.norm_url(u) == fetch_log.norm_url(url)
        fetch_log.record(url=u, final_url=None, kind="fetch", text=text, sid=None, root=root,
                         images=[{"url": i} for i in images], links=[], origin="system")
        return None
    return fetch


# ---------------------------------------------------------------- 留档来源（③）


def test_留档记来源与调用方_老记录按会话里抓读(root):
    rid = fetch_log.record(url=PAGE, final_url=None, kind="fetch", text="x", sid=None, root=root,
                           origin="cli", caller="claude-code")
    e = fetch_log.lookup(PAGE, root=root)[0]
    assert e["id"] == rid and e["origin"] == "cli" and e["caller"] == "claude-code"
    assert fetch_log.origin_label(e) == "命令行抓（claude-code）"
    assert fetch_log.origin_label({"id": "old"}) == "AVA 会话里抓"
    with pytest.raises(ValueError, match="未知抓取来源"):
        fetch_log.record(url=PAGE, final_url=None, kind="fetch", text="x", sid=None, root=root, origin="hand")


def test_AVA工具抓的仍记会话里抓(root):
    from pipeline.agent import tools

    res = tools._keep_record({"text": "x", "full_text": "全文", "final_url": PAGE}, "fetch", PAGE,
                             tools.ToolContext(root=root, episode_dir=None, scope="creative", session_id="sid1"))
    e = fetch_log.lookup(PAGE, root=root)[0]
    assert res["fetch_record"] == e["id"] and e["origin"] == "session" and e["sid"] == "sid1"


# ---------------------------------------------------------------- acquire check（①）


def test_候选出处页没留档_系统当场抓后核上_报告标来源(root):
    c = {"url": IMG, "found_on": PAGE, "title": "t", "type": "scan"}
    r = CC.check_provenance(c, root=root, fetcher=_system_page(root, PAGE, images=[IMG]))
    assert r["ok"] is True and r["origin"] == "系统当场抓"


def test_当场抓失败_照录原因判出处核不上(root):
    c = {"url": IMG, "found_on": PAGE}
    r = CC.check_provenance(c, root=root, fetcher=lambda u: "静态抓取：HTTP 403；无头渲染：超时")
    assert r["ok"] is False and "系统当场抓取也失败：静态抓取：HTTP 403" in r["detail"]


def test_已有留档不再抓(root):
    fetch_log.record(url=PAGE, final_url=None, kind="fetch", text="x", sid="s", root=root, images=[{"url": IMG}])
    r = CC.check_provenance({"url": IMG, "found_on": PAGE}, root=root,
                            fetcher=lambda u: pytest.fail("有留档就不该再抓"))
    assert r["ok"] is True and r["origin"] == "AVA 会话里抓"


def test_当场抓到了页但链接不在页上_仍核不上(root):
    r = CC.check_provenance({"url": IMG, "found_on": PAGE}, root=root, fetcher=_system_page(root, PAGE))
    assert r["ok"] is False and "不在 found_on 页" in r["detail"] and r["origin"] == "系统当场抓"


def test_同一页一批只抓一次(root, monkeypatch):
    calls: list[str] = []

    def fake(url, **kw):
        calls.append(url)
        time.sleep(0.05)
        return {"record": "r"}
    monkeypatch.setattr(F, "fetch_and_record", fake)
    fetch = F.system_fetcher(root)
    threads = [threading.Thread(target=fetch, args=(PAGE + ("/" if i % 2 else ""),)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [PAGE] or calls == [PAGE + "/"], calls
    assert fetch("https://other.example/") is None and len(calls) == 2


# ---------------------------------------------------------------- fact_add（①）与 facts CLI


def _vol(root: Path) -> Path:
    d = root / "data" / "episodes" / "企划" / "03-卷三"
    d.mkdir(parents=True)
    return d


def test_事实出处没留档_系统当场抓后逐字命中(root):
    vol = _vol(root)
    out = facts.add([{"claim": "全凭感觉", "url": PAGE, "excerpts": [TEXT], "proposed_level": "一手"}], target=vol, sid=None, root=root,
                    fetcher=_system_page(root, PAGE, text="前文。" + TEXT + "后文。"))
    r = out["results"][0]
    assert (r["level"], r["match"]) == ("待核", "exact")
    saved = facts.load(vol)[0]
    assert saved["check"]["origin"] == "系统当场抓"
    assert "（留档：系统当场抓）" in (vol / "facts.md").read_text(encoding="utf-8")


def test_事实当场抓失败_未核实并给出抓取原因与登录态指路(root):
    vol = _vol(root)
    r = facts.add([{"claim": "x", "url": PAGE, "excerpts": [TEXT]}], target=vol, sid=None, root=root,
                  fetcher=lambda u: "静态抓取：HTTP 403")["results"][0]
    assert r["level"] == "未核实" and "系统当场抓取也失败：静态抓取：HTTP 403" in r["hint"]
    assert "pipeline.agent.fetch <url> --browser" in r["hint"]


def test_重做校验不出网(root):
    vol = _vol(root)
    facts.add([{"claim": "x", "url": PAGE, "excerpts": [TEXT], "proposed_level": "一手"}], target=vol, sid=None,
              root=root, fetcher=_system_page(root, PAGE, text=TEXT))
    for e in fetch_log.lookup(PAGE, root=root):        # 留档没了：check 只能报问题，不能自己再去抓一份补上
        (fetch_log.log_dir(root) / e["file"]).unlink()
    (fetch_log.log_dir(root) / "index.jsonl").unlink()
    assert facts.check(vol, root=root) and "对不上任何留档" in facts.check(vol, root=root)[0]


def test_facts_add命令行_别的agent写事实(root, monkeypatch, capsys):
    from pipeline import paths

    monkeypatch.setattr(paths, "ROOT", root)
    fetch_log.record(url=PAGE, final_url=None, kind="fetch", text=TEXT, sid=None, root=root, origin="cli",
                     caller="claude-code")
    vol = _vol(root)
    f = root / "entries.json"
    f.write_text(json.dumps([{"claim": "x", "url": PAGE, "excerpts": [TEXT], "topic": "起点", "proposed_level": "二手"}]), encoding="utf-8")
    assert facts.main(["add", str(vol), str(f), "--caller", "claude-code"]) == 0
    saved = facts.load(vol)[0]
    assert saved["sid"] == "cli:claude-code" and saved["level"] == "待核"
    assert saved["check"]["origin"] == "命令行抓（claude-code）"
    assert "F001 ［待核］exact" in capsys.readouterr().out


# ---------------------------------------------------------------- fetch_and_record 与命令行（②）


@pytest.mark.real_fetch_and_record
def test_静态失败自动升级无头渲染_留档记命令行与调用方(root, monkeypatch):
    from pipeline.agent import web, web_crawl

    def fetch_fail(url, **kw):
        raise ValueError("HTTP 403")
    monkeypatch.setattr(web, "fetch_web", fetch_fail)
    seen = {}

    def crawl(url, reason, **kw):
        seen["reason"] = reason
        return {"markdown": "截断版", "full_text": "完整全文", "final_url": url, "title": "T"}
    monkeypatch.setattr(web_crawl, "crawl_page", crawl)
    out = F.fetch_and_record(PAGE, origin="cli", caller="claude-code", root=root)
    e = fetch_log.lookup(PAGE, root=root)[0]
    assert out["kind"] == "crawl" and out["record"] == e["id"] and e["kind"] == "crawl"
    assert e["origin"] == "cli" and e["caller"] == "claude-code"
    assert fetch_log.read_text(e, root=root) == "完整全文"
    assert out["tried"] == ["静态抓取：HTTP 403"] and seen["reason"] == F.SYSTEM_REASON


@pytest.mark.real_fetch_and_record
def test_两级都失败_报两级原因不留档(root, monkeypatch):
    from pipeline.agent import web, web_crawl

    monkeypatch.setattr(web, "fetch_web", lambda url, **kw: (_ for _ in ()).throw(ValueError("HTTP 403")))
    monkeypatch.setattr(web_crawl, "crawl_page", lambda *a, **kw: (_ for _ in ()).throw(ValueError("渲染超时")))
    with pytest.raises(ValueError, match="静态抓取：HTTP 403；无头渲染：渲染超时"):
        F.fetch_and_record(PAGE, origin="system", root=root)
    assert fetch_log.lookup(PAGE, root=root) == []


@pytest.mark.real_fetch_and_record
def test_明说fetch时不升级(root, monkeypatch):
    from pipeline.agent import web, web_crawl

    monkeypatch.setattr(web, "fetch_web", lambda url, **kw: (_ for _ in ()).throw(ValueError("HTTP 403")))
    monkeypatch.setattr(web_crawl, "crawl_page", lambda *a, **kw: pytest.fail("mode=fetch 不许升级"))
    with pytest.raises(ValueError, match="HTTP 403"):
        F.fetch_and_record(PAGE, mode="fetch", origin="cli", root=root)


@pytest.mark.real_fetch_and_record
def test_命令行抓取(root, monkeypatch, capsys):
    from pipeline import paths
    from pipeline.agent import web

    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.setattr(web, "fetch_web", lambda url, **kw: {"text": "截断", "full_text": "全文", "final_url": url})
    assert F.main([PAGE, "--caller", "claude-code"]) == 0
    assert "OK  留档" in capsys.readouterr().out
    e = fetch_log.lookup(PAGE, root=root)[0]
    assert (e["origin"], e["caller"], e["kind"]) == ("cli", "claude-code", "fetch")


def test_候选表标出留档来源(root):
    c = {"url": IMG, "found_on": PAGE, "title": "t", "type": "scan", "why": "w"}
    rec = {"no": 1, "verdict": "可下载", "reasons": [], "measured": {},
           "provenance": {"found_on": PAGE, "ts": "20261024T000000Z", "origin": "系统当场抓"}}
    assert "（系统当场抓，抓取 20261024T000000Z）" in CC.render_table([(c, rec)])


def test_默认就走系统当场抓_acquire_check与fact_add(root):
    """不注入 fetcher 时用 system_fetcher；conftest 把它的出网换成「测试里不出网」，从失败原因看出确实去抓了。"""
    rec = CC.check_batch([(1, {"url": IMG, "found_on": PAGE, "type": "scan"})], root=root,
                         run=lambda argv, binary: __import__("subprocess").CompletedProcess(argv, 1, b"", ""),
                         workers=1)[0]
    assert "系统当场抓取也失败：测试里不出网" in rec["provenance"]["detail"]
    r = facts.add([{"claim": "x", "url": PAGE, "excerpts": [TEXT]}], target=_vol(root), sid=None, root=root)
    assert "系统当场抓取也失败：测试里不出网" in r["results"][0]["hint"]
