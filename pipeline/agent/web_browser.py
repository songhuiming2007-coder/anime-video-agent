"""pipeline.agent.web_browser: browser 登录态浏览器工具（Spec 5 / ADR-0021，第三级）。

纪律：
1. 顶层零重依赖：playwright / camoufox 只在 _default_launch_fn / _pw_error_types 内函数级延迟 import；
   顶层 stdlib + pipeline.agent.web（守卫/清洗复用）；
2. profile 路径只来自 config（web.json browser.profile_dir），运行时参数
   无覆盖通道；_assert_profile_isolation 强制 data/ 严格后代（§2.2）——
   碰主力 Chrome Default 在构造上不可能；
3. approval 门 = 既有逐调用人审卡（本模块不感知审批，llm.py:212-213 在
   execute 前问人）；每次进程启动落 browser_session_started 事件（§3.5）；
4. action 面：navigate / extract_text（§2.5）+ screenshot（D122）；模块无内容写盘出口——截图的 PNG
   字节随返回值交给 tools 包装层落盘（profile 目录由浏览器进程自建，ava 仅对其 chmod 0700，§2.2）。
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from pipeline import paths
from pipeline.agent.tools import assert_egress_boundary
from pipeline.agent.web import (
    _TextExtractor,
    _clean_refs,
    _extract_images,
    _extract_links,
    _guard_url,
    _normalized_for_assert,
    _scrub,
)

_ACTIONS = ("navigate", "extract_text", "screenshot")

# D122 截图会话：Firefox 偏好把像素密度设成 2（2026-10-20 实测：无界面 Camoufox 默认 1，截出来就是 CSS 尺寸；
# 透传 device_scale_factor 会被拒——Camoufox 伪装窗口尺寸时关掉了固定视口）。只在截图时临时起一个这样的
# 会话、截完即关，平时浏览的配置与指纹不变；代价是这个会话的视口约 960 CSS 宽（窗口物理尺寸不变）。
SHOT_PREFS = {"layout.css.devPixelsPerPx": "2.0"}
# 正文栏窄的页（ナタリー、sleepfreaks、ORICON 新版等，正文 550～900 CSS 宽）两倍密度截出来不到 1840 像素宽，
# 门禁要放大、会拒；`scale` 3 或 4 让浏览器按更高密度重新排版文字，是真像素，不是放大图片（D139）。
# 代价：视口按密度缩（2026-10-24 实测 3、4 倍都只剩约 500×130～200 CSS），响应式页面会换成手机版排版。
SHOT_SCALES = (2, 3, 4)
# 按文字找元素时，命中的元素太小（一行字）就往上取，宽或高够其中一项就停，带上能看出出处的上下文（CSS 像素）。
# 往上取不越过视口高度 SHOT_MAX_H_VIEWPORTS 倍的父元素；命中的元素本身就这么高（文字跨了元素边界，只有大容器
# 才整段包含它）就拒——2026-10-20 实测：维基信息框只有 310 宽，「宽且高」的旧条件一路取到 4065 高的整篇正文
SHOT_MIN_W, SHOT_MIN_H = 480, 160
SHOT_MAX_H_VIEWPORTS = 1.5
# 「一屏半」按的视口高至少取这么多 CSS 像素：两倍密度时实测视口高约 443；密度调高后视口只剩一百多，
# 不兜底的话 scale 4 下稍长一点的段落都会被当成「跨了几个元素」拒掉（D139）
SHOT_MIN_VIEWPORT_H = 440
# 在页面里标出要截的元素：包含这段文字的最深元素，太小就往上取父元素
_SHOT_PICK_JS = r"""([needle, minW, minH, maxVp, minVpH]) => {
  const norm = s => (s || "").replace(/\s+/g, "");
  const n = norm(needle);
  let best = null;
  for (const el of document.body.querySelectorAll("*")) {
    if (!norm(el.innerText).includes(n)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (!best || best.contains(el)) best = el;
  }
  if (!best) return {why: "missing"};
  const maxH = Math.max(innerHeight, minVpH) * maxVp;
  const b = best.getBoundingClientRect();
  if (b.height > maxH) return {why: "big", w: Math.round(b.width), h: Math.round(b.height)};
  let el = best;
  while (el.parentElement && el !== document.body) {
    const r = el.getBoundingClientRect();
    if (r.width >= minW || r.height >= minH) break;
    if (el.parentElement.getBoundingClientRect().height > maxH) break;
    el = el.parentElement;
  }
  el.setAttribute("data-ava-shot", "1");
  return {why: null};
}"""
_SESSION: Any | None = None
_ATEXIT_REGISTERED: bool = False


@dataclass(frozen=True)
class BrowserSection:
    profile_dir: str   # 原始配置值（相对 root 解析在守卫内完成）
    headed: bool
    timeout_s: float
    max_chars: int = 30000
    trusted_fake_ip_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    # 需要登录态下载的域名清单（如 youtube.com / nicovideo.jp）：acquire fetch/probe 命中时
    # 给 yt-dlp 挂本 profile 的 --cookies-from-browser。默认空 = 任何站都不挂（未实测不写判断）
    cookies_for: tuple[str, ...] = ()


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _parse_trusted_ranges(
    raw_ranges: Any,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    if not isinstance(raw_ranges, list):
        return ()
    parsed: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw_ranges:
        if not isinstance(item, str) or not item.strip():
            return ()
        try:
            parsed.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            return ()
    return tuple(parsed)


def _assert_profile_isolation(profile_dir: str, root: Path) -> Path:
    """profile 隔离守卫：resolve 后必须是 <root>/data 的严格后代，否则
    PermissionError（配置指向危险位置必须响，§3.1）。返回 resolve 后路径。"""
    raw = str(profile_dir or "").strip()
    if not raw:
        raise PermissionError("browser profile_dir 不能为空")
    base_root = Path(root).resolve()
    data_root = (base_root / "data").resolve()
    expanded = Path(os.path.expanduser(raw))
    target = (expanded if expanded.is_absolute() else (base_root / expanded)).resolve()
    if target == data_root or data_root not in target.parents:
        raise PermissionError(
            f"严禁使用 data/ 子树外的浏览器 profile 路径: {profile_dir!r} "
            f"(resolved={target}, 必须位于 {data_root}/<subdir>)"
        )
    return target


def _active_config_path(root: Path | None = None) -> Path:
    """实际生效的配置文件：web.local.json 存在时整份取代 web.json（Spec 4 §3.1 覆盖语义）。"""
    cfg_dir = Path(root or paths.ROOT) / "config" / "agent"
    local_cfg = cfg_dir / "web.local.json"
    return local_cfg if local_cfg.exists() else (cfg_dir / "web.json")


def load_browser_section(root: Path | None = None) -> BrowserSection | None:
    """读 web.local.json（优先）/ web.json 的 browser 段（同 §2.6 独立校验）。"""
    cfg_file = _active_config_path(root)
    if not cfg_file.exists():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    browser = data.get("browser")
    if not isinstance(browser, dict):
        return None

    profile_dir = browser.get("profile_dir")
    headed = browser.get("headed")
    timeout_s = browser.get("timeout_s")

    if not isinstance(profile_dir, str) or not profile_dir.strip():
        return None
    if not isinstance(headed, bool):
        return None
    if not _is_number(timeout_s) or not (0 < float(timeout_s) <= 120):
        return None

    # 配置指向危险位置是对抗性事件，必须抛 PermissionError 而非返回 None（§3.1 / T14b）
    _assert_profile_isolation(profile_dir.strip(), Path(root or paths.ROOT))

    max_chars_raw = browser.get("max_chars", 30000)
    if not _is_int(max_chars_raw) or int(max_chars_raw) < 1000:
        return None

    trusted_ranges = _parse_trusted_ranges(data.get("trusted_fake_ip_ranges", []))
    cookies_for_raw = browser.get("cookies_for", [])
    cookies_for = (
        tuple(d.strip().lower() for d in cookies_for_raw if isinstance(d, str) and d.strip())
        if isinstance(cookies_for_raw, list) else ()
    )
    return BrowserSection(
        profile_dir=profile_dir.strip(),
        headed=headed,
        timeout_s=float(timeout_s),
        max_chars=int(max_chars_raw),
        trusted_fake_ip_ranges=trusted_ranges,
        cookies_for=cookies_for,
    )


def cookies_spec_for(url: str, *, root: Path | None = None) -> str | None:
    """该 URL 的 yt-dlp `--cookies-from-browser` 规格；域不在 cookies_for 里返回 None。

    纪律：ava 代码不触碰 cookie 本体（ADR-0021：登录态 cookie 属出网敏感物；本模块连
    探活都禁用 context.cookies()）——profile 目录路径整体交给 yt-dlp 自己去读。命中
    cookies_for 但 profile 还没有 Cookies 库 = 流程错了（还没登录就跑下载），响出来。
    """
    cfg = load_browser_section(root)
    if cfg is None or not cfg.cookies_for:
        return None
    host = urlparse(str(url)).netloc.lower().split("@")[-1].split(":")[0]
    if not any(host == d or host.endswith("." + d) for d in cfg.cookies_for):
        return None
    profile = _assert_profile_isolation(cfg.profile_dir, Path(root or paths.ROOT))
    # D96：浏览器是 Camoufox（Firefox 内核），cookie 库是配置目录下的 cookies.sqlite，不经钥匙串
    if not (profile / "cookies.sqlite").is_file():
        raise PermissionError(
            f"{host} 在 cookies_for 里，但 browser profile 还没有 cookie 库（{profile}）——\n"
            f"先登录该站一次再重跑：{LOGIN_COMMAND} https://{host}/"
        )
    return f"firefox:{profile}"


def _pw_error_types() -> tuple[type[BaseException], ...]:
    """playwright 异常族（函数级延迟 import；未安装 → 空元组）。

    playwright.sync_api.Error/TimeoutError 是 Exception 直接子类、与 builtin
    TimeoutError 同名不同类，不在 tools.py:668 捕获面内（🔴-2）——browser_action
    对启动/导航/提取全路径用本族做 isinstance 包装为 ValueError（§3.4）。
    本函数是可 monkeypatch 的测试缝（T18：注入 FakePWError）。
    """
    try:
        from playwright.sync_api import (  # type: ignore[import-not-found]
            Error as PWError,
            TimeoutError as PWTimeoutError,
        )
        return (PWError, PWTimeoutError)
    except ImportError:
        return ()


class _PlaywrightSessionAdapter:
    """将 playwright persistent context 封装为统一的 goto / extract_text / probe / close 接口。

    死会话探活（S16 🟡-1）：playwright 中 context.pages 仅返回 self._pages.copy()（本地列表拷贝、
    不发 RPC，context 关闭后访问仍不抛异常）。因此初始化时订阅 context.on("close", ...)，
    关闭回调触发时置 _dead = True；probe() 返回 not self._dead。
    严禁使用 context.cookies() 探活（违反 spec 不触碰 cookie 本体纪律）。
    """

    def __init__(self, pw: Any, context: Any) -> None:
        self._pw = pw
        self._context = context
        self.pid: int | None = None
        self._dead: bool = False
        if hasattr(context, "on"):
            context.on("close", self._mark_dead)

    def _mark_dead(self, *_args: Any) -> None:
        self._dead = True

    def _current_page(self) -> Any:
        pages = self._context.pages
        if pages:
            return pages[-1]
        return self._context.new_page()

    def probe(self) -> bool:
        return not self._dead

    def goto(self, url: str, *, timeout_s: float = 60.0) -> dict[str, str]:
        page = self._current_page()
        page.goto(url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")
        return {
            "final_url": str(page.url or url),
            "title": str(page.title() or ""),
        }

    def extract_text(self, *, timeout_s: float = 60.0) -> dict[str, str]:
        page = self._current_page()
        html = str(page.content() or "")
        parser = _TextExtractor()
        parser.feed(html)
        return {
            "url": str(page.url or ""),
            "text": parser.get_text(),
            "html": html,  # D100：给留档抽图片 / 链接；只在本模块内用，不进工具返回
        }

    def screenshot(self, url: str, *, selector: str | None, text: str | None,
                   timeout_s: float = 60.0) -> dict[str, Any]:
        """打开 url，按 selector 或文字定位元素，按设备像素截元素（D122）。"""
        ms = int(timeout_s * 1000)
        page = self._current_page()
        page.goto(url, timeout=ms, wait_until="load")
        if text:
            pick = page.evaluate(_SHOT_PICK_JS, [text, SHOT_MIN_W, SHOT_MIN_H, SHOT_MAX_H_VIEWPORTS,
                                                 SHOT_MIN_VIEWPORT_H])
            if pick.get("why"):
                return {"final_url": str(page.url or url), "missing": pick["why"],
                        "css_size": [pick.get("w"), pick.get("h")]}
            loc = page.locator('[data-ava-shot="1"]').first
        else:
            loc = page.locator(str(selector)).first
        loc.scroll_into_view_if_needed(timeout=ms)
        box = loc.bounding_box(timeout=ms) or {}
        png = loc.screenshot(scale="device", timeout=ms)
        html = str(page.content() or "")
        parser = _TextExtractor()
        parser.feed(html)
        return {
            "final_url": str(page.url or url),
            "title": str(page.title() or ""),
            "png": png,
            "element_text": str(loc.inner_text(timeout=ms) or ""),
            "css_size": [round(box.get("width", 0)), round(box.get("height", 0))],
            "text": parser.get_text(),
            "html": html,
        }

    def open_tab(self, url: str, *, timeout_s: float = 60.0) -> None:
        """login 入口用：每个网址开一个新标签页。"""
        self._context.new_page().goto(url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")

    def wait_closed(self) -> None:
        """login 入口用：阻塞到人关掉整个窗口（context 的 close 事件），不设超时。"""
        if not self._context.pages:
            self._context.new_page()
        self._context.wait_for_event("close", timeout=0)

    def close(self) -> None:
        self._dead = True
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


# D96：camoufox 0.5.6 启动时找不到浏览器本体会自己去下载（camoufox_path 默认 download_if_missing=True），
# 违背「浏览器二进制绝不自动下载」——启动前先以 download_if_missing=False 预检，缺了就报错给人
CAMOUFOX_FETCH_HINT = "（Camoufox 浏览器本体缺失，请人工执行一次 `uv run camoufox fetch`，严禁自动下载）"
# 宿主是 macOS：指纹报的系统与真实字体 / 渲染一致；Google 登录不被拦（D96 实测 E6）就是在这个参数下测的
CAMOUFOX_OS = "macos"
# 同一配置目录已有 Camoufox 在用时，第二个进程的启动报错里的原话（D96 施工实测）
_PROFILE_IN_USE = "already open"


def browser_env() -> dict[str, str]:
    """给 Camoufox 的环境：会话进程的环境去掉密钥变量（N50）。camoufox 在 env=None 时取整份 os.environ。"""
    from pipeline.agent.tools import secret_env_names

    drop = set(secret_env_names())
    return {k: v for k, v in os.environ.items() if k not in drop}


def _default_launch_fn(*, user_data_dir: Path, headed: bool, prefs: dict[str, Any] | None = None) -> Any:
    """Camoufox 持久配置（D96；函数级延迟 import，基于实测版本 camoufox 0.5.6 + playwright 1.62.0 钉死）：
    sync_playwright().start() + camoufox NewBrowser(pw, persistent_context=True, user_data_dir=...,
    headless=not headed, os="macos")，返回的仍是 playwright BrowserContext，探活回调照旧。
    import 失败（部分/损坏安装，🔴-1）→ ValueError 包装，绝不放任穿透。
    浏览器本体缺失 → 显式 ValueError 提示人工 `uv run camoufox fetch`，绝不自动下载。
    """
    try:
        from camoufox.pkgman import camoufox_path  # type: ignore[import-not-found]
        from camoufox.sync_api import NewBrowser  # type: ignore[import-not-found]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError(
            f"browser 依赖已定位但导入失败（部分或损坏安装: {exc}），"
            "请重装: uv sync --extra browser（uv sync 会卸掉未列出的 extras，apple/dev 等须一并列出）"
        ) from exc

    try:
        camoufox_path(download_if_missing=False)
    except Exception as exc:  # CamoufoxNotInstalled / UnsupportedVersion：都只能人去 fetch
        raise ValueError(f"browser 操作失败（启动失败: {exc}）{CAMOUFOX_FETCH_HINT}") from exc

    pw = None
    try:
        pw = sync_playwright().start()
        ctx = NewBrowser(
            pw,
            persistent_context=True,
            user_data_dir=str(user_data_dir),
            headless=not bool(headed),
            os=CAMOUFOX_OS,
            env=browser_env(),
            **({"firefox_user_prefs": prefs} if prefs else {}),
        )
        return _PlaywrightSessionAdapter(pw, ctx)
    except Exception as exc:
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        hint = "（这份浏览器配置正被另一个窗口占用：先关掉它，或结束 AVA 里的浏览器会话）" if _PROFILE_IN_USE in str(exc) else ""
        raise ValueError(f"browser 操作失败（启动失败: {exc}）{hint}") from exc


def _reset_session_for_testing() -> None:
    """测试夹具专用：关闭并清空模块级会话单例（消灭跨用例污染）。"""
    global _SESSION
    sess = _SESSION
    _SESSION = None
    if sess is not None and hasattr(sess, "close"):
        try:
            sess.close()
        except Exception:
            pass


def _emit_browser_started(
    *,
    profile_dir: Path,
    headed: bool,
    pid: int | None,
    scope: str,
    trigger: dict[str, Any],
    episode_dir: Path | None,
) -> None:
    """发射 browser_session_started 事件（Spec 2 sidecar 契约，失败静默）。"""
    payload: dict[str, Any] = {
        "profile_dir": str(profile_dir),
        "headed": bool(headed),
        "scope": str(scope),
        "trigger": trigger,
    }
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        payload["pid"] = pid

    try:
        from pipeline.jobs import EventType, get_publisher
        get_publisher().emit(
            EventType.BROWSER_SESSION_STARTED,
            payload,
            episode_dir=episode_dir,
        )
    except Exception:
        pass


def _start_new_session(
    *,
    base_root: Path,
    resolved_profile_dir: Path,
    cfg: BrowserSection,
    launch_fn: Callable[..., Any] | None,
    episode_dir: Path | None,
    scope: str,
    trigger: dict[str, Any],
) -> Any:
    global _SESSION, _ATEXIT_REGISTERED
    data_dir = base_root / "data"
    if not data_dir.is_dir():
        raise ValueError(
            f"data/ 不可达（{data_dir}）：插盘或 preflight --init（严禁自动创建 data/）"
        )
    resolved_profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved_profile_dir, 0o700)

    fn = _default_launch_fn if launch_fn is None else launch_fn
    pw_errs = _pw_error_types()
    try:
        sess = fn(user_data_dir=resolved_profile_dir, headed=cfg.headed)
    except Exception as exc:
        if pw_errs and isinstance(exc, pw_errs):
            raise ValueError(f"browser 操作失败（启动失败: {exc}）") from exc
        raise

    _SESSION = sess
    if not _ATEXIT_REGISTERED:
        atexit.register(_reset_session_for_testing)
        _ATEXIT_REGISTERED = True

    raw_pid = getattr(sess, "pid", None)
    _emit_browser_started(
        profile_dir=resolved_profile_dir,
        headed=cfg.headed,
        pid=raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None,
        scope=scope,
        trigger=trigger,
        episode_dir=episode_dir,
    )
    return sess


def _probe_session_alive(session: Any, pw_errs: tuple[type[BaseException], ...]) -> bool:
    """探活当前会话（🟡-R5）：存活返回 True，抛 playwright 异常或返回 False 则判死。"""
    try:
        if hasattr(session, "probe"):
            res = session.probe()
            return bool(res) if res is not None else True
        if hasattr(session, "is_alive"):
            return bool(session.is_alive())
        if hasattr(session, "pages"):
            pages_attr = getattr(session, "pages")
            _ = pages_attr() if callable(pages_attr) else pages_attr
            return True
        return True
    except Exception as exc:
        if pw_errs and isinstance(exc, pw_errs):
            return False
        return False


def _exec_on_session(
    session: Any,
    action: str,
    url: str,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    if action == "navigate":
        return session.goto(url, timeout_s=timeout_s)
    return session.extract_text(timeout_s=timeout_s)


def _screenshot_action(
    *,
    url: str,
    selector: str | None,
    text: str | None,
    expect_text: str | None,
    cfg: BrowserSection,
    base_root: Path,
    resolved_profile_dir: Path,
    launch_fn: Callable[..., Any] | None,
    episode_dir: Path | None,
    scope: str,
    trigger: dict[str, Any],
    keep_full: bool,
    scale: int = 2,
) -> dict[str, Any]:
    """D122：关掉当前会话，用同一份 profile（登录态在里面）按 SHOT_PREFS 临时起一个会话截图，截完即关。
    `scale` 不是 2 时只换像素密度（D139）。

    下一次 navigate 自动按平时的配置重启。`expect_text` 给了就核：元素文字里没有这句话 = 截图证明不了要证的事，
    不交 PNG（2026-10-19 卷二段 37：截图里根本没有配音引语，人复核才发现）。DOM 里有之后再对 PNG 做文字识别
    （D141，`ocr.recognize`），图上认不出也不交。
    """
    _reset_session_for_testing()
    fn = _default_launch_fn if launch_fn is None else launch_fn
    pw_errs = _pw_error_types()
    try:
        prefs = SHOT_PREFS if scale == 2 else {**SHOT_PREFS, "layout.css.devPixelsPerPx": f"{scale}.0"}
        sess = fn(user_data_dir=resolved_profile_dir, headed=cfg.headed, prefs=prefs)
    except Exception as exc:
        if pw_errs and isinstance(exc, pw_errs):
            raise ValueError(f"browser 截图失败（启动失败: {exc}）") from exc
        raise
    raw_pid = getattr(sess, "pid", None)
    _emit_browser_started(
        profile_dir=resolved_profile_dir, headed=cfg.headed,
        pid=raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None,
        scope=scope, trigger=trigger, episode_dir=episode_dir,
    )
    try:
        raw = sess.screenshot(url, selector=selector, text=text, timeout_s=cfg.timeout_s)
    except Exception as exc:
        if pw_errs and isinstance(exc, pw_errs):
            raise ValueError(f"browser 截图失败: {exc}") from exc
        raise
    finally:
        try:
            sess.close()
        except Exception:
            pass

    final_url = str(raw.get("final_url") or url).strip() or url
    _guard_url(final_url, trusted_ranges=cfg.trusted_fake_ip_ranges)
    if raw.get("missing") == "big":
        w, h = raw.get("css_size") or [None, None]
        raise ValueError(f"包含「{text}」的最小元素有 {w}×{h}（CSS 像素），比一屏半还高：这段文字多半跨了几个元素，"
                         f"换一段在同一行里原样出现的短文字，或改用 selector（{_scrub(final_url)}）")
    if raw.get("missing"):
        raise ValueError(f"页面上找不到包含「{text}」的元素（{_scrub(final_url)}）：换一段页面上原样出现的文字，或改用 selector")
    element_text = _scrub(str(raw.get("element_text") or ""))
    if expect_text:
        from pipeline.facts import norm_exact

        if norm_exact(expect_text) not in norm_exact(element_text):
            raise ValueError(f"截到的元素里没有「{expect_text}」，截图证明不了它，没有保存。"
                             f"元素文字开头：{element_text[:120]!r}——换 selector / text 截到真正写着这句话的区域")
        # D141：DOM 里有不等于图上画出来了（样式丢失、被遮挡、在截图区域外），对 PNG 本身再认一次
        from pipeline.agent import ocr
        from pipeline.facts import check_excerpt

        ocr_match, near = check_excerpt(expect_text, ocr.recognize(raw["png"]))
        if ocr_match == "none":
            raise ValueError(f"元素的 DOM 文字里有「{expect_text}」，截图上却认不出来，没有保存：多半是页面样式没加载、"
                             f"文字被遮挡或画在截图区域外（Wayback `id_` 原始页常见）。图上认出的最接近一段：{near!r}"
                             "——换一个能正常显示的版本（去掉 `id_`、换镜像）或换 selector 再截")
    scrubbed = _scrub(str(raw.get("text") or ""))
    refs: dict[str, Any] = {}
    if keep_full:
        raw_html = str(raw.get("html") or "")
        refs = {"full_text": scrubbed,
                "full_images": _clean_refs(_extract_images(raw_html, final_url), ()),
                "full_links": _clean_refs(_extract_links(raw_html, final_url, keep_unanchored=True), ())}
    return {
        "action": "screenshot",
        "url": url,
        "final_url": _scrub(final_url),
        "title": _scrub(str(raw.get("title") or "")),
        "png": raw["png"],          # tools 包装层落盘后 pop，不进 LLM
        "element_text": element_text,
        # 图上认出 expect_text 的程度：exact 逐字（同 norm_exact）/ approx 只比字母数字（标点、空格有出入）
        **({"ocr_match": ocr_match} if expect_text else {}),
        "css_size": raw.get("css_size"),
        **refs,
    }


def browser_action(
    action: str,
    reason: str,
    *,
    url: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    expect_text: str | None = None,
    section: BrowserSection | None = None,
    launch_fn: Callable[..., Any] | None = None,
    episode_dir: Path | None = None,
    scope: str = "creative",
    root: Path | None = None,
    keep_full: bool = False,
    scale: int = 2,
) -> dict[str, Any]:
    """流程：action ∈ _ACTIONS 校验 → reason 非空 → load_browser_section →
    navigate：egress 断言 + _guard_url(url)；extract_text：要求会话存活
    （不隐式启动，§3.3）→ 会话未存活则 _assert_profile_isolation（通过后
    首建目录 chmod 0700，§2.2）+ launch_fn 启动（B1 纪律解析默认实现）→
    启动成功即发 browser_session_started 事件 → navigate/extract_text 落地后
    对 URL 复跑 _guard_url（🟡-4）→ 死会话自愈分流（🟡-R4/🟡-R5）→
    extract_text 结果 _scrub + 封顶 → 返回 §3.3 契约。
    """
    global _SESSION

    clean_action = str(action or "").strip()
    if clean_action not in _ACTIONS:
        raise ValueError(f"未知 browser action: {action!r}（仅支持 {list(_ACTIONS)}）")

    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("reason 不能为空（必须说明为什么 crawl 不够或为何需要登录态）")

    base_root = Path(root or paths.ROOT)
    cfg = section if section is not None else load_browser_section(base_root)
    if cfg is None:
        raise ValueError(
            f"缺少或损坏 {_active_config_path(base_root)} 的 browser 段"
            "（web.local.json 存在时整份取代 web.json）"
        )

    resolved_profile_dir = _assert_profile_isolation(cfg.profile_dir, base_root)
    if not (base_root / "data").is_dir():
        raise ValueError(
            f"data/ 不可达（{base_root / 'data'}）：插盘或 preflight --init（严禁自动创建 data/）"
        )

    clean_url = str(url or "").strip()
    if clean_action == "screenshot":
        clean_sel, clean_text = str(selector or "").strip(), str(text or "").strip()
        if bool(clean_sel) == bool(clean_text):
            raise ValueError("action='screenshot' 要给 selector 或 text 其中一个（截哪个区域）")
        if type(scale) is not int or scale not in SHOT_SCALES:  # 3.0 == 3，会拼出 "3.0.0"
            raise ValueError(f"screenshot 的 scale 只能是 {'/'.join(map(str, SHOT_SCALES))}（像素密度倍数），收到 {scale!r}")
    if clean_action in ("navigate", "screenshot"):
        if not clean_url:
            raise ValueError(f"action={clean_action!r} 时 url 不能为空")
        assert_egress_boundary(
            clean_url,
            {
                "url": _normalized_for_assert(clean_url),
                "reason": _normalized_for_assert(clean_reason),
            },
        )
        _guard_url(clean_url, trusted_ranges=cfg.trusted_fake_ip_ranges)
    else:
        if _SESSION is None:
            raise ValueError("browser 会话未启动，请先使用 action='navigate' 导航至目标页面")

    trigger_info: dict[str, Any] = {"action": clean_action, "reason": clean_reason}
    if clean_url:
        trigger_info["url"] = clean_url

    if clean_action == "screenshot":
        return _screenshot_action(
            url=clean_url, selector=clean_sel or None, text=clean_text or None,
            expect_text=str(expect_text or "").strip() or None, cfg=cfg, base_root=base_root,
            resolved_profile_dir=resolved_profile_dir, launch_fn=launch_fn, episode_dir=episode_dir,
            scope=scope, trigger=trigger_info, keep_full=keep_full, scale=scale,
        )

    if _SESSION is None:
        _start_new_session(
            base_root=base_root,
            resolved_profile_dir=resolved_profile_dir,
            cfg=cfg,
            launch_fn=launch_fn,
            episode_dir=episode_dir,
            scope=scope,
            trigger=trigger_info,
        )

    pw_errs = _pw_error_types()
    try:
        raw_res = _exec_on_session(_SESSION, clean_action, clean_url, timeout_s=cfg.timeout_s)
    except Exception as exc:
        if pw_errs and isinstance(exc, pw_errs):
            if _probe_session_alive(_SESSION, pw_errs):
                # 探活存活 = 操作本身失败（如真实超时），直接包装 ValueError，会话不动（🟡-R5）
                raise ValueError(f"browser 操作失败: {exc}") from exc
            # 探活也败 = 死会话：丢弃旧会话，复用当前调用的人审卡重启一次 + 落新事件（🟡-R4）
            _reset_session_for_testing()
            _start_new_session(
                base_root=base_root,
                resolved_profile_dir=resolved_profile_dir,
                cfg=cfg,
                launch_fn=launch_fn,
                episode_dir=episode_dir,
                scope=scope,
                trigger=trigger_info,
            )
            try:
                raw_res = _exec_on_session(_SESSION, clean_action, clean_url, timeout_s=cfg.timeout_s)
            except Exception as exc2:
                if pw_errs and isinstance(exc2, pw_errs):
                    raise ValueError(f"browser 操作失败: {exc2}") from exc2
                raise
        else:
            raise

    if clean_action == "navigate":
        final_url = str(raw_res.get("final_url") or clean_url).strip() or clean_url
        _guard_url(final_url, trusted_ranges=cfg.trusted_fake_ip_ranges)
        title = _scrub(str(raw_res.get("title") or ""))
        return {
            "action": "navigate",
            "url": clean_url,
            "final_url": _scrub(final_url),
            "title": title,
        }

    cur_url = str(raw_res.get("url") or "").strip()
    if cur_url:
        _guard_url(cur_url, trusted_ranges=cfg.trusted_fake_ip_ranges)
    raw_text = str(raw_res.get("text") or "")
    scrubbed = _scrub(raw_text)
    refs: dict[str, Any] = {}
    if keep_full:
        raw_html = str(raw_res.get("html") or "")
        base = cur_url or clean_url
        refs = {"full_images": _clean_refs(_extract_images(raw_html, base), ()),
                "full_links": _clean_refs(_extract_links(raw_html, base, keep_unanchored=True), ())}
    truncated = len(scrubbed) > cfg.max_chars
    capped = scrubbed[: cfg.max_chars] if truncated else scrubbed
    return {
        "action": "extract_text",
        "url": _scrub(cur_url),
        "text": capped,
        "truncated": truncated,
        # D99：脱敏后、截断前的全文，给抓取留档；tools 包装层记完即 pop（D100 起连同图片 / 链接列表）
        **({"full_text": scrubbed, **refs} if keep_full else {}),
    }


# ---------- 人补登录入口（D96）：登录过期、遇到 Cloudflare 验证时，人开窗口登录 / 点验证 ----------

LOGIN_COMMAND = "uv run python -m pipeline.agent.web_browser login"


def login_main(argv: list[str], *, root: Path | None = None, launch_fn: Callable[..., Any] | None = None) -> int:
    """`login [URL ...]`：（提示一律 flush：人常在 Claude Code 的 `!` 里跑，stdout 接的是文件、默认整块缓冲）用 browser 工具同一份配置、同一个启动器开有界面窗口，打开给定网址，
    等人关掉窗口后返回。不读、不打印任何 cookie。"""
    if not argv or argv[0] != "login":
        print(f"用法：{LOGIN_COMMAND} [URL ...]", flush=True)
        return 2
    base_root = Path(root or paths.ROOT)
    cfg = load_browser_section(base_root)
    if cfg is None:
        print(f"缺少或损坏 {_active_config_path(base_root)} 的 browser 段", flush=True)
        return 1
    profile = _assert_profile_isolation(cfg.profile_dir, base_root)
    if not (base_root / "data").is_dir():
        print(f"data/ 不可达（{base_root / 'data'}）：插盘或 preflight --init（严禁自动创建 data/）", flush=True)
        return 1
    profile.mkdir(parents=True, exist_ok=True)
    os.chmod(profile, 0o700)
    fn = _default_launch_fn if launch_fn is None else launch_fn
    try:
        sess = fn(user_data_dir=profile, headed=True)
    except ValueError as exc:
        print(str(exc), flush=True)
        return 1
    try:
        for url in argv[1:]:
            try:
                sess.open_tab(url, timeout_s=cfg.timeout_s)
            except Exception as exc:  # 一个站打不开不影响其余站
                print(f"打不开 {url}：{exc}", flush=True)
        print(f"窗口已打开（配置 {profile}）。登录 / 点完验证后关掉整个窗口，命令随之结束。", flush=True)
        sess.wait_closed()
    except KeyboardInterrupt:  # 终端 Ctrl-C：照常关浏览器（finally），不打堆栈
        print("已中断，浏览器已关闭；已完成的登录仍留在配置里。", flush=True)
        return 130
    finally:
        sess.close()
    print("已关闭，登录状态留在配置里。", flush=True)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(login_main(sys.argv[1:]))
