"""Google Colab 浏览器自举器（`pipeline/cloud_colab_boot.py`）。

供 AVA Agent 在执行 `cloud up --provider colab` 时自主唤醒 Colab 运行时并抓取
`cloudflared` 隧道地址（补齐 Colab 没有官方开机 REST API 的最后一环）：
- 顶层零重依赖：`playwright.sync_api` 仅在浏览器动作内延迟导入；
- 浏览器档案严格隔离于 `data/cloud/colab-browser-profile`（`0700`），绝不触碰系统主力 Chrome Default；
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
    """返回 AVA 专用的 Colab 隔离浏览器目录（`data/cloud/colab-browser-profile`，`0700`）。"""
    base = data_dir if data_dir is not None else paths.DATA
    p = base / "cloud" / "colab-browser-profile"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def has_saved_browser_profile(profile_dir: Path) -> bool:
    """判断隔离浏览器档案是否已做过一次人工登录（纯文件系统探测）。"""
    if not profile_dir.is_dir():
        return False
    # Chrome/Chromium persistent context 登录后会生成 Default/Cookies 或 Local State
    for marker in (
        profile_dir / "Default" / "Cookies",
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Local State",
    ):
        if marker.is_file() and marker.stat().st_size > 0:
            return True
    return False


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
    if "Sign in" in txt and "Colab Pro" not in txt and "Runtime" not in txt:
        return True
    if "登录" in txt and "代码执行程序" not in txt and "Runtime" not in txt:
        return True
    return False


def _launch_persistent_context(pw: Any, profile_dir: Path, *, headless: bool) -> Any:
    """用 Chrome（首选）或 Chromium 拉起受控隔离 persistent context。"""
    common = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        ignore_default_args=["--enable-automation"],
    )
    try:
        return pw.chromium.launch_persistent_context(channel="chrome", **common)
    except Exception:
        return pw.chromium.launch_persistent_context(**common)


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


def _click_dialog_button(page: Any, pattern: str) -> bool:
    """安全点击弹窗确认按钮（若存在且已启用）。"""
    try:
        btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
        if btn.count() > 0 and btn.is_visible() and btn.is_enabled():
            btn.click(timeout=2000)
            return True
    except Exception:
        return False
    return False


def _handle_colab_modals(page: Any) -> bool:
    """处理 GitHub Notebook 信任确认与 Google Drive 挂载授权弹窗。"""
    clicked = False
    for pattern in (
        r"^Run anyway$|^仍然运行$",
        r"^Connect to Google Drive$|^连接到 Google 云端硬盘$",
        r"^Allow$|^允许$",
    ):
        if _click_dialog_button(page, pattern):
            clicked = True
    return clicked


def interactive_browser_login(
    notebook_url: str,
    *,
    profile_dir: Path,
    timeout_s: int = 300,
    log_fn: Callable[[str], None] = print,
) -> str:
    """打开隔离的有界面 Chrome 让用户完成一次 Google 登录并返回最终确认后的 Notebook URL。"""
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    log_fn(f"[*] 正在打开独立 Colab 授权浏览器窗口（档案目录: {profile_dir}）...")
    log_fn("[*] 请在弹出的窗口中登录你的 Google AI Pro 账号；进入 Colab Notebook 后会自动保存并继续。")

    final_url = notebook_url
    with sync_playwright() as pw:
        ctx = _launch_persistent_context(pw, profile_dir, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(notebook_url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.time() + max(60, timeout_s)
            saw_ready = False
            while time.time() < deadline:
                if ctx.pages:
                    cur = ctx.pages[-1]
                    cur_url = cur.url or ""
                    if "colab.research.google.com" in cur_url and "accounts.google.com" not in cur_url:
                        body = _collect_page_and_frames_text(cur)
                        has_account = (
                            cur.locator('[aria-label*="Google Account"], [aria-label*="Google 账号"]').count() > 0
                            or ("Runtime" in body or "代码执行程序" in body)
                        )
                        not_guest = not detect_colab_login_required(cur_url, body)
                        if has_account and not_guest:
                            final_url = cur_url.split("#", 1)[0]
                            saw_ready = True
                            break
                time.sleep(2)
            if not saw_ready:
                raise SystemExit(f"FAIL 在 {timeout_s} 秒内未检测到已登录的 Colab 页面，请重试 --browser-login。")
        finally:
            ctx.close()
    return final_url


def _trigger_colab_run_all(page: Any) -> bool:
    """触发 Colab 执行（优先点 Run all 按钮，回退第一个 Cell 的 Run cell 或 Ctrl/Meta+F9）。"""
    for pat in (r"^Run all", r"^全部运行", r"^Run cell"):
        try:
            btn = page.get_by_role("button", name=re.compile(pat, re.I)).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                return True
        except Exception:
            pass
    try:
        page.keyboard.press("Meta+F9")
        return True
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
    """自主打开 Colab Notebook、触发运行并抓取新的 `AVA_COLAB_SSH_READY` 隧道目标。"""
    if not has_saved_browser_profile(profile_dir):
        raise RuntimeError(
            "Colab 隔离浏览器档案尚未登录 Google。请先运行一次：\n"
            "  python -m pipeline.cloud connect --provider colab --browser-login"
        )

    from playwright.sync_api import sync_playwright

    log_fn(f"[*] 正在通过 AVA 隔离浏览器自动唤醒 Colab 运行时 ({'headless' if headless else 'headed'})...")
    with sync_playwright() as pw:
        ctx = _launch_persistent_context(pw, profile_dir, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(notebook_url, wait_until="domcontentloaded", timeout=60_000)
            # 等 Colab 工具栏或登录跳转稳定
            for _ in range(15):
                if detect_colab_login_required(page.url, _collect_page_and_frames_text(page)):
                    raise RuntimeError(
                        "Colab 浏览器登录态已失效，请重新运行一次：\n"
                        "  python -m pipeline.cloud connect --provider colab --browser-login"
                    )
                if page.get_by_role("button", name=re.compile(r"Run all|Run cell|全部运行", re.I)).count() > 0:
                    break
                time.sleep(1)

            initial_text = _collect_page_and_frames_text(page)
            initial_ready = _READY_RE.findall(initial_text)
            prev_last = initial_ready[-1] if initial_ready else None

            if not _trigger_colab_run_all(page):
                raise RuntimeError("未能触发 Colab Run all，请检查 Notebook URL 是否有效。")
            log_fn("[*] 已触发 Colab 执行，等待驱动挂载、venv 恢复与 cloudflared 隧道就绪...")

            deadline = time.time() + max(60, timeout_s)
            saw_running = False
            while time.time() < deadline:
                _handle_colab_modals(page)
                cur_url = page.url or ""
                all_text = _collect_page_and_frames_text(page)
                if detect_colab_login_required(cur_url, all_text):
                    raise RuntimeError(
                        "Colab 页面跳转到登录框（Cookie 已过期），请运行：\n"
                        "  python -m pipeline.cloud connect --provider colab --browser-login"
                    )
                if not saw_running and (
                    "Executing" in all_text
                    or "Connecting" in all_text
                    or "Mounted at /content/drive" in all_text
                    or page.locator('[aria-label*="Interrupt execution"]').count() > 0
                ):
                    saw_running = True

                ready_matches = _READY_RE.findall(all_text)
                if ready_matches:
                    latest = ready_matches[-1].strip()
                    if latest != prev_last or saw_running:
                        target = latest if "@" in latest else f"root@{latest}"
                        # 留 2 秒给 cloudflared trycloudflare 边缘路由生效
                        time.sleep(2)
                        return target
                time.sleep(2)

            raise RuntimeError(
                f"在 {timeout_s} 秒内未在 Colab 输出中找到 AVA_COLAB_SSH_READY 隧道信号。"
            )
        finally:
            ctx.close()
