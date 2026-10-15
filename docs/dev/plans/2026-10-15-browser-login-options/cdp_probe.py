"""用法: python cdp_probe.py <cdp_url> <shot.png>：经 CDP 接上人启动的 Chrome，开 YouTube 与 Google 登录页。"""
import sys, json
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp(sys.argv[1])
    ctx = b.contexts[0]; page = ctx.new_page()
    page.goto("https://www.youtube.com/", timeout=60000); page.wait_for_timeout(3000)
    yt = [c["name"] for c in ctx.cookies("https://www.youtube.com")]
    page.goto("https://accounts.google.com/signin/v2/identifier?service=youtube", timeout=60000); page.wait_for_timeout(3000)
    page.screenshot(path=sys.argv[2])
    print(json.dumps({"yt_cookies": yt, "webdriver": page.evaluate("navigator.webdriver"),
                      "body": page.inner_text("body")[:200]}, ensure_ascii=False))
    page.close()
