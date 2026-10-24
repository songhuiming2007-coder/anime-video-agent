"""抓一页并留档：AVA 的抓取工具、出处核对的当场抓取、命令行三处共用（D136）。

出处核对的底线（D124）是「这条在那一页上」只由系统代码抓取并比对得出，不认任何手写留档。
D136 之前这条底线被实现成「AVA 的工具抓过」：别的 agent 查到的候选与事实，要再让 AVA 把同一页抓一遍。
现在抓取与落档都走这里：`acquire check` / `fact_add` 发现出处页没有留档时当场抓（`origin=system`），
别的 agent 或人用命令行抓（`origin=cli`，记调用方标签）；留档仍只由 `fetch_log.record` 写，内容是本模块
抓回来的原文，调用方给不了正文。

用法：
    python -m pipeline.agent.fetch <url> [--crawl | --browser] [--caller claude-code] [--reason …]

默认静态抓取，失败自动升级无头渲染（crawl）；要登录态的页（X、B 站收藏夹）用 --browser，它用 AVA 的
独立浏览器 profile，AVA 开着浏览器会话时会占用同一 profile，先在 AVA 里关掉或等它空闲。
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from pipeline.agent import fetch_log

#: 系统当场抓、命令行没给 --reason 时写进 crawl / browser 的理由（它们要求非空，审计时看得出是谁抓的）
SYSTEM_REASON = "出处核对：出处页没有抓取留档，系统当场抓取（D136）"


def keep_record(res: dict[str, Any], kind: str, url: str, *, sid: str | None, root: Path | None,
                origin: str = "session", caller: str | None = None) -> dict[str, Any]:
    """D99：抓到的全文留档，返回值换成记录号——全文不进 LLM（它看的仍是截断版 text / markdown）。"""
    full = res.pop("full_text", None)
    # D100：截帽前的完整图片 / 链接列表只进留档，不进 LLM
    full_images = res.pop("full_images", None)
    full_links = res.pop("full_links", None)
    if isinstance(full, str):
        # 留档失败不连累抓取本身，但要让调用方看见：之后 fact_add 会如实报「这页没有留档」
        try:
            res["fetch_record"] = fetch_log.record(
                url=url, final_url=res.get("final_url") or res.get("url"), kind=kind, text=full, sid=sid,
                root=root, images=full_images, links=full_links, origin=origin, caller=caller)
        except (ValueError, OSError) as exc:
            res["fetch_record"] = None
            res["fetch_record_error"] = f"抓取留档失败：{exc}"
    return res


def fetch_and_record(url: str, *, mode: str = "auto", origin: str, caller: str | None = None,
                     sid: str | None = None, root: Path | None = None, reason: str | None = None,
                     ) -> dict[str, Any]:
    """抓 `url` 并留档 → {record, kind, final_url, chars, title, tried}。抓不到或没落上档抛 ValueError。

    mode：auto（静态，失败升级 crawl）/ fetch / crawl / browser。browser 只在明说时用：它要登录态 profile，
    系统当场抓不去碰它。
    """
    from pipeline.agent.web import fetch_web
    from pipeline.agent.web_crawl import crawl_page

    why = (reason or "").strip() or SYSTEM_REASON
    tried: list[str] = []
    res: dict[str, Any] | None = None
    kind = ""
    if mode in ("auto", "fetch"):
        try:
            res, kind = fetch_web(url, root=root, keep_full=True), "fetch"
        except ValueError as exc:
            tried.append(f"静态抓取：{exc}")
            if mode == "fetch":
                raise ValueError("；".join(tried)) from None
    if res is None and mode in ("auto", "crawl"):
        try:
            res, kind = crawl_page(url, why, root=root, keep_full=True), "crawl"
        except ValueError as exc:
            tried.append(f"无头渲染：{exc}")
            raise ValueError("；".join(tried)) from None
    if res is None and mode == "browser":
        res, kind = _browser_text(url, why, root), "browser"
    if res is None:
        raise ValueError(f"未知抓取方式 {mode!r}（auto / fetch / crawl / browser）")
    res = keep_record(res, kind, url, sid=sid, root=root, origin=origin, caller=caller)
    if not res.get("fetch_record"):
        raise ValueError(res.get("fetch_record_error") or "抓到了，但这次没有全文可留档")
    return {"record": res["fetch_record"], "kind": kind, "final_url": res.get("final_url") or res.get("url") or url,
            "chars": len(str(res.get("text") or res.get("markdown") or "")), "title": res.get("title"),
            "tried": tried}


def _browser_text(url: str, reason: str, root: Path | None) -> dict[str, Any]:
    from pipeline.agent.web_browser import browser_action

    browser_action("navigate", reason, url=url, root=root)
    return browser_action("extract_text", reason, root=root, keep_full=True)


def system_fetcher(root: Path | None) -> Callable[[str], str | None]:
    """出处核对用的当场抓取：同一 URL 一批里只抓一次（并发核对时多条候选常指同一页）。

    返回的函数 → None = 已留档；字符串 = 抓不到的原因（核对报告照录）。
    """
    done: dict[str, str | None] = {}
    lock = threading.Lock()
    per_url: dict[str, threading.Lock] = {}

    def fetch(url: str) -> str | None:
        key = fetch_log.norm_url(url)
        with lock:
            url_lock = per_url.setdefault(key, threading.Lock())
        with url_lock:
            if key not in done:
                try:
                    fetch_and_record(url, origin="system", root=root)
                    done[key] = None
                except ValueError as exc:
                    done[key] = str(exc)
            return done[key]

    return fetch


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.agent.fetch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--crawl", action="store_true", help="直接无头渲染（JS 渲染 / 反爬页）")
    g.add_argument("--browser", action="store_true", help="用 AVA 的独立浏览器 profile（要登录态的页）")
    ap.add_argument("--caller", default="cli", help="调用方标签，记进留档（如 claude-code、pi）")
    ap.add_argument("--reason", default=None, help="为什么抓这页（crawl / browser 记进审计）")
    a = ap.parse_args(argv)
    mode = "crawl" if a.crawl else "browser" if a.browser else "auto"
    try:
        out = fetch_and_record(a.url, mode=mode, origin="cli", caller=a.caller.strip() or "cli", reason=a.reason)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    for line in out["tried"]:
        print(f"WARN {line}（已升级下一级）", file=sys.stderr)
    print(f"OK  留档 {out['record']}（{out['kind']}，{out['chars']} 字）{out['final_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
