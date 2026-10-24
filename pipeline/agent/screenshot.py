"""网页截图进 incoming 与台账：AVA 的 browser 截图与命令行两处共用（D138）。

D122 给网页截图类物证（官方公告、榜单、认证检索页）开了正式入库路径，但只做在 AVA 的 `browser` 工具里；
别的 agent（Claude Code 等）查到要截的页，只能列清单转交 AVA。D136 已把「抓一页并留档」做成命令行，
这里照同一思路把「截一块并落盘」也做成命令行：截图、页面全文留档、台账一条 scan 都由本模块写，
调用方给不了图片和正文。

用法：
    python -m pipeline.agent.screenshot <url> (--selector <CSS> | --text <页面上原样出现的一段文字>) \\
        --reason <证明第几段的哪句话> [--expect-text <截图里必须出现的话>] [--name <文件名提示>] \\
        [--caller claude-code]

用 AVA 的独立浏览器 profile（登录态在里面）；AVA 开着浏览器会话时会占用同一 profile，先在 AVA 里关掉或等它空闲。
截完照常 `acquire gate` → `acquire register`（企划卷进企划池）。
"""

from __future__ import annotations

import argparse
import datetime
import io
import sys
from pathlib import Path
from typing import Any, Callable


def save_screenshot(res: dict[str, Any], reason: str, name: str, *, sid: str | None, root: Path | None,
                    origin: str = "session", caller: str | None = None) -> dict[str, Any]:
    """D122：PNG 落 incoming/、写抓取留档（页面全文，kind=browser）、台账记一条 scan，回执不带图片字节。"""
    from PIL import Image

    from pipeline import candidates
    from pipeline.agent.fetch import keep_record
    from pipeline.still_geometry import STILL_MAX_MAGNIFY, still_peak_magnify

    png = res.pop("png")
    with Image.open(io.BytesIO(png)) as im:
        size = im.size
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = candidates.incoming() / f"shot-{candidates.slugify(name or res.get('title') or 'page')}-{stamp}.png"
    dest.write_bytes(png)
    page_url = str(res.get("final_url") or res.get("url") or "")
    res = keep_record(res, "browser", page_url, sid=sid, root=root, origin=origin, caller=caller)
    entries = candidates.ledger_load()
    entry = {"url": page_url, "title": name or res.get("title") or dest.stem, "type": "scan",
             "source": "screenshot", "candidate": None, "file": str(dest), "as": None, "why": reason,
             "fetch_record": res.get("fetch_record"),
             "element_text": str(res.get("element_text") or "")[:500]}
    if caller:
        entry["caller"] = caller
    entries.append(entry)
    candidates.ledger_save(entries)
    mag = still_peak_magnify(size)
    return {
        "action": "screenshot",
        "file": str(dest),
        "size": list(size),
        "css_size": res.get("css_size"),
        "magnify": round(mag, 2),
        "gate": "PASS" if mag <= STILL_MAX_MAGNIFY else f"FAIL：入池要放大 {mag:.2f} 倍（上限 {STILL_MAX_MAGNIFY:g}）",
        "final_url": page_url,
        "title": res.get("title"),
        "element_text": str(res.get("element_text") or "")[:200],
        "fetch_record": res.get("fetch_record"),
        **({"fetch_record_error": res["fetch_record_error"]} if "fetch_record_error" in res else {}),
    }


def shoot(url: str, *, reason: str, selector: str | None = None, text: str | None = None,
          expect_text: str | None = None, name: str = "", caller: str = "cli", root: Path | None = None,
          launch_fn: Callable[..., Any] | None = None) -> dict[str, Any]:
    """命令行截图：校验与截图本身同 AVA 的 `browser` 截图（`web_browser.browser_action`），落盘记 origin=cli。"""
    from pipeline.agent.web_browser import browser_action

    res = browser_action("screenshot", reason, url=url, selector=selector, text=text, expect_text=expect_text,
                         launch_fn=launch_fn, root=root, keep_full=True)
    return save_screenshot(res, reason, name, sid=None, root=root, origin="cli", caller=caller)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.agent.screenshot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--selector", help="要截的元素的 CSS 选择器")
    g.add_argument("--text", help="页面上原样出现的一段文字，截包含它的区域")
    ap.add_argument("--reason", required=True, help="这张截图证明第几段的哪句话（写进台账的 why）")
    ap.add_argument("--expect-text", default=None, help="截图里必须出现的话；元素文字里没有就不保存")
    ap.add_argument("--name", default="", help="文件名提示（缺省用页面标题）")
    ap.add_argument("--caller", default="cli", help="调用方标签，记进留档与台账（如 claude-code、pi）")
    a = ap.parse_args(argv)
    try:
        out = shoot(a.url, reason=a.reason, selector=a.selector, text=a.text, expect_text=a.expect_text,
                    name=a.name, caller=a.caller.strip() or "cli")
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    w, h = out["size"]
    print(f"OK  {out['file']}（{w}×{h}，入池放大 {out['magnify']} 倍，门禁预判 {out['gate']}）")
    print(f"    留档 {out['fetch_record'] or '无：' + out.get('fetch_record_error', '?')}  {out['final_url']}")
    print(f"    截到的文字：{out['element_text']}")
    return 0 if out["fetch_record"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
