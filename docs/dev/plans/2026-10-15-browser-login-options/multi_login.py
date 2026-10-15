"""D96 第二轮：Camoufox 同一份持久配置里，一次开多个站点的登录页，人逐个登录，关窗即保存。

用法（全局装的 camoufox 0.5.6 在 Python 3.14 下）：
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -I multi_login.py <profile_dir> <log.jsonl> [站点键 ...]
不给站点键 = 全部。登录完关掉整个窗口（或在终端按 Ctrl-C）。日志按站点记：是否出现「不安全 / 机器人验证」字样、关窗时有没有登录 cookie。
登录 cookie 名单只用于事后自动判断「登上了没有」，不读取 cookie 值。
注意：按 cookie 名判对 pixiv / note / 小红书不可靠（访客也发同名 cookie，实测记录 §四之三）。
D96 施工后人补登录用 `uv run python -m pipeline.agent.web_browser login [URL ...]`，本脚本只作当时实测的留档。
"""
import json
import sys
import time

from camoufox.sync_api import Camoufox

# 站点键 → (登录页, 判断已登录的 cookie 所在站点, 任一即算已登录的 cookie 名)
SITES = {
    "niconico": ("https://account.nicovideo.jp/login", "https://www.nicovideo.jp", {"user_session"}),
    "bilibili": ("https://passport.bilibili.com/login", "https://www.bilibili.com", {"SESSDATA"}),
    "x": ("https://x.com/i/flow/login", "https://x.com", {"auth_token"}),
    "pixiv": ("https://accounts.pixiv.net/login", "https://www.pixiv.net", {"PHPSESSID"}),
    "note": ("https://note.com/login", "https://note.com", {"_note_session_v5"}),
    "zhihu": ("https://www.zhihu.com/signin", "https://www.zhihu.com", {"z_c0"}),
    "xiaohongshu": ("https://www.xiaohongshu.com/explore", "https://www.xiaohongshu.com", {"web_session"}),
    "bangumi": ("https://bgm.tv/login", "https://bgm.tv", {"chii_auth"}),
}
BLOCK_WORDS = ("may not be secure", "不安全", "unusual traffic", "verify you are human", "機器人", "机器人", "captcha")

prof, log = sys.argv[1], sys.argv[2]
keys = sys.argv[3:] or list(SITES)


def note(**k):
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **k}, ensure_ascii=False) + "\n")


with Camoufox(persistent_context=True, user_data_dir=prof, headless=False, os="macos") as ctx:
    pages = {}
    for k in keys:
        page = ctx.new_page()
        try:
            page.goto(SITES[k][0], timeout=60000)
        except Exception as exc:  # 打不开也照记，不中断其它站
            note(site=k, ev="open_failed", error=str(exc)[:200])
        pages[k] = page
    note(ev="opened", sites=keys)
    flagged = set()
    logged = {k: False for k in keys}  # 关窗后上下文就没了，读不到 cookie，所以循环里随时记
    try:
        while any(not p.is_closed() for p in pages.values()):
            time.sleep(5)
            for k, p in pages.items():
                if p.is_closed() or k in flagged:
                    continue
                try:
                    body = p.inner_text("body")[:3000].lower()
                except Exception:
                    continue
                hit = next((w for w in BLOCK_WORDS if w.lower() in body), None)
                if hit:
                    flagged.add(k)
                    note(site=k, ev="block_text", word=hit)
            try:
                for k in keys:
                    if not logged[k] and {c["name"] for c in ctx.cookies(SITES[k][1])} & SITES[k][2]:
                        logged[k] = True
                        note(site=k, ev="logged_in")
            except Exception:
                break  # 窗口已关
    except KeyboardInterrupt:
        pass
    note(ev="final", logged_in=logged)
note(ev="closed")
