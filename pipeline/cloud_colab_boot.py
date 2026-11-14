"""Google Colab 浏览器自举器（`pipeline/cloud_colab_boot.py`）。

供 AVA Agent 在执行 `cloud up --provider colab` 时通过 **Camoufox（Firefox 内核）**
自主唤醒 Colab 运行时并抓取 `cloudflared` 隧道地址（补齐 Colab 无官方开机 REST API 的最后一环）：
- 与 AVA 既有浏览器栈（`pipeline/agent/web_browser.py`）一致：顶层零重依赖，函数级延迟导入
  `camoufox` / `playwright.sync_api`，复用 `browser_env()`（剥离密钥变量）与 `CAMOUFOX_OS`；
- 强制使用 `camoufox_path(download_if_missing=False)` 预检，绝不自动下载浏览器本体；
- 浏览器档案严格隔离于 `data/cloud/colab-browser-profile-firefox`（`0700`），与
  `data/browser-profile-firefox` 互不抢锁，也绝不触碰系统主力浏览器；
- 纯函数解析规则与状态判定均可单测覆盖，不依赖真实网络。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from pipeline import paths

DEFAULT_COLAB_NOTEBOOK_URL = (
    "https://colab.research.google.com/github/"
    "songhuiming2007-coder/anime-video-agent/blob/main/tools/ava_colab_bootstrap.ipynb"
)

_READY_RE = re.compile(
    r"AVA_COLAB_SSH_READY:\s*((?:root@)?[A-Za-z0-9-]+\.trycloudflare\.com)\b"
)
_ANY_TUNNEL_RE = re.compile(r"\b([A-Za-z0-9-]+\.trycloudflare\.com)\b")
_PLACEHOLDER_HOSTS = frozenset({"xxxx.trycloudflare.com", "example.trycloudflare.com"})


def colab_browser_profile_dir(data_dir: Path | None = None) -> Path:
    """返回 AVA 专用的 Colab Camoufox 隔离目录（`data/cloud/colab-browser-profile-firefox`，`0700`）。"""
    base = data_dir if data_dir is not None else paths.DATA
    p = base / "cloud" / "colab-browser-profile-firefox"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def has_saved_browser_profile(profile_dir: Path) -> bool:
    """判断 Camoufox（Firefox）隔离档案是否已做过人工登录（纯文件系统探测）。"""
    if not profile_dir.is_dir():
        return False
    # D96：Camoufox（Firefox 内核）登录后会在配置根目录写入非空 cookies.sqlite
    cookies_db = profile_dir / "cookies.sqlite"
    return cookies_db.is_file() and cookies_db.stat().st_size > 0


def resolve_colab_notebook_url(
    cfg_global: dict[str, Any],
    cfg_local: dict[str, Any],
    explicit: str | None = None,
) -> str:
    """按 CLI 显式传入 → `cloud.local.json` profiles.colab → `cloud.json` providers.colab → 默认解析 Notebook URL。"""
    if explicit and explicit.strip():
        return explicit.strip()
    local_prof = (cfg_local.get("profiles") or {}).get("colab") or {}
    if local_prof.get("notebook_url"):
        return str(local_prof["notebook_url"]).strip()
    global_prof = (cfg_global.get("providers") or {}).get("colab") or {}
    if global_prof.get("notebook_url"):
        return str(global_prof["notebook_url"]).strip()
    return DEFAULT_COLAB_NOTEBOOK_URL


def extract_colab_tunnel_host(text: str) -> str | None:
    """从 Colab 页面/输出文本提取最新的 `trycloudflare.com` 隧道目标。

    优先匹配显式信标 `AVA_COLAB_SSH_READY: root@<host>`，兜底取最后一条非占位域名。
    """
    if not text:
        return None
    ready_matches = _READY_RE.findall(text)
    if ready_matches:
        target = ready_matches[-1].strip()
        return target if "@" in target else f"root@{target}"

    candidates = [h for h in _ANY_TUNNEL_RE.findall(text) if h.lower() not in _PLACEHOLDER_HOSTS]
    if candidates:
        return f"root@{candidates[-1]}"
    return None


def detect_colab_login_required(url: str, page_text: str) -> bool:
    """判断当前 Colab 页面是否因未登录被跳到 Google 登录页或显示 Sign in 提示。"""
    u = (url or "").lower()
    if "accounts.google.com" in u or "servicelogin" in u:
        return True
    txt = page_text or ""
    lines = {ln.strip() for ln in txt.splitlines() if ln.strip()}
    if "Sign in" in lines or "Please Sign in to continue" in txt or "Sign in to" in txt:
        return True
    if "登录" in lines or "请登录" in txt or "需要登录" in txt:
        return True
    return False


def _is_colab_authenticated(page: Any) -> bool:
    """检查 Colab 页面 DOM 是否已呈现 Google 登录账号头像/SignOutOptions，且无 Sign in 按钮。"""
    try:
        signed_out = page.locator(
            'a[href*="ServiceLogin"], a:has-text("Sign in"), button:has-text("Sign in"), a:has-text("登录")'
        ).count()
        signed_in = page.locator(
            '[aria-label*="Google Account:"], [aria-label*="Google 账号："], a[href*="SignOutOptions"]'
        ).count()
        return signed_in > 0 and signed_out == 0
    except Exception:
        return False


def _launch_persistent_context(pw: Any, profile_dir: Path, *, headless: bool) -> Any:
    """用 AVA 同款 Camoufox（Firefox 内核 + `browser_env()`）拉起隔离持久上下文。"""
    from camoufox.pkgman import camoufox_path  # type: ignore[import-not-found]
    from camoufox.sync_api import NewBrowser  # type: ignore[import-not-found]

    from pipeline.agent.web_browser import CAMOUFOX_FETCH_HINT, CAMOUFOX_OS, browser_env

    try:
        camoufox_path(download_if_missing=False)
    except Exception as exc:
        raise RuntimeError(f"Camoufox 浏览器本体未就绪: {exc} {CAMOUFOX_FETCH_HINT}") from exc

    try:
        return NewBrowser(
            pw,
            persistent_context=True,
            user_data_dir=str(profile_dir),
            headless=headless,
            os=CAMOUFOX_OS,
            env=browser_env(),
        )
    except Exception as exc:
        hint = (
            "（该 Camoufox Colab 档案正被另一窗口占用，请先关闭后重试）"
            if "already open" in str(exc).lower()
            else ""
        )
        raise RuntimeError(f"启动 Colab Camoufox 浏览器失败: {exc}{hint}") from exc


def _collect_page_and_frames_text(page: Any) -> str:
    """读取主页面与所有 outputframe iframe 的可见文本（Colab 把 Cell 输出放在沙盒 iframe 内）。"""
    parts: list[str] = []
    try:
        parts.append(page.inner_text("body", timeout=1500))
    except Exception:
        pass
    for fr in list(getattr(page, "frames", [])):
        if fr is page.main_frame:
            continue
        try:
            txt = fr.inner_text("body", timeout=1000)
            if txt:
                parts.append(txt)
        except Exception:
            continue
    return "\n".join(parts)


def _click_dialog_button(target: Any, pattern: str) -> str | None:
    """安全点击目标页面或 frame 内的弹窗确认按钮（若存在且已启用）。"""
    try:
        btn = target.get_by_role("button", name=re.compile(pattern, re.I)).first
        if btn.count() > 0 and btn.is_visible() and btn.is_enabled():
            label = (btn.inner_text(timeout=1000) or pattern).strip()
            btn.click(timeout=2000)
            return label
    except Exception:
        return None
    return None


def _handle_colab_modals(ctx: Any, main_page: Any, log_fn: Callable[[str], None] = print) -> bool:
    """处理 GitHub Notebook 信任确认、Drive 挂载确认以及 OAuth 授权弹窗。"""
    clicked = False
    targets = [main_page] + [fr for fr in list(getattr(main_page, "frames", [])) if fr is not main_page.main_frame]
    patterns = (
        r"^Run anyway$|^仍然运行$",
        r"^Connect to Google Drive$|^连接到 Google 云端硬盘$",
        r"^Allow$|^允许$",
        r"^Continue$|^继续$",
    )
    for tgt in targets:
        for pat in patterns:
            label = _click_dialog_button(tgt, pat)
            if label:
                log_fn(f"[*] 已自动点击 Colab 确认弹窗: {label}")
                clicked = True

    # 处理点击 "Connect to Google Drive" 后可能弹出的 accounts.google.com OAuth 新标签页
    for p in list(getattr(ctx, "pages", [])):
        if p is main_page:
            continue
        pu = (getattr(p, "url", "") or "").lower()
        if "accounts.google.com" not in pu:
            continue
        try:
            if p.locator('input[type="password"]').count() > 0:
                raise RuntimeError(
                    "Google 账号在 Drive OAuth 弹窗要求重新输入密码/二次验证，"
                    "请运行一次 `cloud connect --provider colab --browser-login` 完成授权。"
                )
            acct = p.locator("[data-email], [data-identifier]").first
            if acct.count() > 0 and acct.is_visible():
                acct.click(timeout=2000)
                log_fn("[*] 已在 Google OAuth 弹窗选择已登录账号")
                time.sleep(2)
                clicked = True
                continue
            for pat in (r"^Continue$|^继续$", r"^Allow$|^允许$"):
                btn = p.get_by_role("button", name=re.compile(pat, re.I)).first
                if btn.count() > 0 and btn.is_visible() and btn.is_enabled():
                    try:
                        btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    label = (btn.inner_text(timeout=1000) or pat).strip()
                    btn.click(timeout=2000)
                    log_fn(f"[*] 已在 Google OAuth 弹窗点击: {label}")
                    time.sleep(2)
                    clicked = True
                    break
        except RuntimeError:
            raise
        except Exception:
            continue
    return clicked


def interactive_browser_login(
    notebook_url: str,
    *,
    profile_dir: Path,
    timeout_s: int = 300,
    log_fn: Callable[[str], None] = print,
) -> str:
    """打开隔离的有界面 Camoufox（Firefox），等用户完成 Google 登录并手动关闭窗口后返回 URL。"""
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    log_fn(f"[*] 正在打开 AVA Camoufox Colab 授权窗口（档案目录: {profile_dir}）...")
    log_fn("[*] 请在弹出的 Camoufox 窗口中完成以下两步：")
    log_fn("    1. 点击右上角「Sign in / 登录」，登录你的 Google AI Pro 账号；")
    log_fn("    2. 确认左上/右上已显示你的 Google 头像后，**直接关闭这个 Camoufox 窗口**即可保存（不会再提前闪退）。")

    final_url = notebook_url
    saw_auth = False
    with sync_playwright() as pw:
        ctx = _launch_persistent_context(pw, profile_dir, headless=False)
        closed = False

        def _on_close(*_args: Any) -> None:
            nonlocal closed
            closed = True

        if hasattr(ctx, "on"):
            ctx.on("close", _on_close)

        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(notebook_url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.time() + max(180, timeout_s)
            while not closed and time.time() < deadline:
                try:
                    pages = list(ctx.pages)
                except Exception:
                    break
                if not pages:
                    break
                cur = pages[-1]
                cur_url = getattr(cur, "url", "") or ""
                if "colab.research.google.com" in cur_url and "accounts.google.com" not in cur_url:
                    final_url = cur_url.split("#", 1)[0]
                    if not saw_auth and _is_colab_authenticated(cur):
                        saw_auth = True
                        log_fn("[*] 已检测到 Google 账号登录成功！如需改选 GPU 或复制到 Drive 可顺手完成，然后直接关闭此窗口即可。")
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            if not closed and time.time() >= deadline:
                raise SystemExit(f"FAIL 等待关闭登录窗口超时（{timeout_s}s），请重试 --browser-login。")
        finally:
            if not closed:
                try:
                    ctx.close()
                except Exception:
                    pass
    if not saw_auth:
        log_fn("[!] 提醒：窗口关闭前未检测到 Colab 页面出现 Google 已登录头像，若后续无头开机提示未登录，请重新执行一次 --browser-login。")
    return final_url


def _trigger_colab_run_all(page: Any) -> str | None:
    """触发 Colab 执行（优先点击唯一代码格 `colab-run-button`，再试菜单/按钮与快捷键）。"""
    try:
        run_btn = page.locator("colab-run-button").first
        if run_btn.count() > 0 and run_btn.is_visible():
            run_btn.click(timeout=3000)
            return "colab-run-button"
    except Exception:
        pass
    for pat in (r"^Run all", r"^全部运行", r"^Run cell"):
        try:
            btn = page.get_by_role("button", name=re.compile(pat, re.I)).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                return f"button:{pat}"
        except Exception:
            pass
    try:
        page.keyboard.press("Meta+F9")
        return "shortcut:Meta+F9"
    except Exception:
        return None


def _page_needs_login(page: Any, url: str, body_text: str) -> bool:
    if detect_colab_login_required(url, body_text):
        return True
    try:
        return page.locator('a[href*="ServiceLogin"]').count() > 0
    except Exception:
        return False


def auto_boot_colab_ssh(
    notebook_url: str,
    *,
    profile_dir: Path,
    timeout_s: int = 300,
    headless: bool = True,
    log_fn: Callable[[str], None] = print,
) -> str:
    """自主通过 Camoufox 打开 Colab Notebook、触发运行并抓取新的 `AVA_COLAB_SSH_READY` 隧道目标。"""
    if not has_saved_browser_profile(profile_dir):
        raise RuntimeError(
            "Colab Camoufox 隔离档案尚未登录 Google。请先运行一次：\n"
            "  python -m pipeline.cloud connect --provider colab --browser-login"
        )

    from playwright.sync_api import sync_playwright

    log_fn(f"[*] 正在通过 AVA Camoufox 自动唤醒 Colab 运行时 ({'headless' if headless else 'headed'})...")
    with sync_playwright() as pw:
        ctx = _launch_persistent_context(pw, profile_dir, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(notebook_url, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(15):
                if _page_needs_login(page, page.url, _collect_page_and_frames_text(page)):
                    raise RuntimeError(
                        "Colab Camoufox 登录态已失效，请重新运行一次：\n"
                        "  python -m pipeline.cloud connect --provider colab --browser-login"
                    )
                has_run_ui = (
                    page.locator("colab-run-button").count() > 0
                    or page.get_by_role("button", name=re.compile(r"Run all|Run cell|全部运行", re.I)).count() > 0
                )
                if has_run_ui:
                    break
                time.sleep(1)

            initial_text = _collect_page_and_frames_text(page)
            initial_ready = _READY_RE.findall(initial_text)
            prev_last = initial_ready[-1] if initial_ready else None

            how = _trigger_colab_run_all(page)
            if not how:
                raise RuntimeError("未能触发 Colab 运行按钮，请检查 Notebook URL 是否有效。")
            log_fn(f"[*] 已触发 Colab 执行 ({how})，等待 Drive 挂载、venv 恢复与 cloudflared 隧道就绪...")

            deadline = time.time() + max(60, timeout_s)
            saw_running = False
            saw_drive = False
            last_text = ""
            exec_retries = 0
            while time.time() < deadline:
                _handle_colab_modals(ctx, page, log_fn=log_fn)
                cur_url = page.url or ""
                all_text = _collect_page_and_frames_text(page)
                last_text = all_text
                if "Failed to execute cell" in all_text and exec_retries < 2:
                    ok_lbl = _click_dialog_button(page, r"^OK$|^确定$")
                    if ok_lbl:
                        exec_retries += 1
                        log_fn(f"[*] 捕获 Colab 运行时握手瞬断弹窗，确认并重试执行 ({exec_retries}/2)...")
                        time.sleep(2)
                        _trigger_colab_run_all(page)
                        time.sleep(2)
                        continue
                if detect_colab_login_required(cur_url, all_text):
                    raise RuntimeError(
                        "Colab 页面跳转到登录框（Cookie 已过期），请运行：\n"
                        "  python -m pipeline.cloud connect --provider colab --browser-login"
                    )
                if not saw_running and (
                    "Executing" in all_text
                    or "Connecting" in all_text
                    or "Busy" in all_text
                    or page.locator('[aria-label*="Interrupt execution"], [aria-label*="中断执行"]').count() > 0
                ):
                    saw_running = True
                    log_fn("[*] Colab 运行时已开始执行 Bootstrap...")
                if not saw_drive and "Mounted at /content/drive" in all_text:
                    saw_drive = True
                    saw_running = True
                    log_fn("[*] Google Drive 已挂载，正在恢复 venv 并启动 cloudflared...")

                ready_matches = _READY_RE.findall(all_text)
                if ready_matches:
                    latest = ready_matches[-1].strip()
                    if latest != prev_last or saw_running:
                        target = latest if "@" in latest else f"root@{latest}"
                        # 留 3 秒给 cloudflared trycloudflare 边缘路由生效
                        time.sleep(3)
                        return target
                time.sleep(2)

            diag_shot = profile_dir.parent / "colab-boot-timeout.png"
            try:
                page.screenshot(path=str(diag_shot), full_page=False)
                os.chmod(diag_shot, 0o600)
            except Exception:
                pass
            tail = " | ".join(ln.strip() for ln in last_text.splitlines()[-6:] if ln.strip())[:240]
            raise RuntimeError(
                f"在 {timeout_s} 秒内未在 Colab 输出中找到 AVA_COLAB_SSH_READY 隧道信号"
                f"（截图: {diag_shot}；末尾页面文本: {tail or '空'}）。"
            )
        finally:
            ctx.close()
