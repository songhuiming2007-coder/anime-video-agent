"""pipeline.agent.web_browser: browser 登录态浏览器工具（Spec 5 / ADR-0021，第三级）。

纪律：
1. 顶层零重依赖：playwright 只在 _default_launch_fn / _pw_error_types 内函数级延迟 import；
   顶层 stdlib + pipeline.agent.web（守卫/清洗复用）；
2. profile 路径只来自 config（web.json browser.profile_dir），运行时参数
   无覆盖通道；_assert_profile_isolation 强制 data/ 严格后代（§2.2）——
   碰主力 Chrome Default 在构造上不可能；
3. approval 门 = 既有逐调用人审卡（本模块不感知审批，llm.py:212-213 在
   execute 前问人）；每次进程启动落 browser_session_started 事件（§3.5）；
4. action 面只有 navigate / extract_text（§2.5）；模块无内容写盘出口
   （profile 目录由浏览器进程自建，ava 仅对其 chmod 0700，§2.2）。
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline import paths
from pipeline.agent.tools import assert_egress_boundary
from pipeline.agent.web import (
    _TextExtractor,
    _guard_url,
    _normalized_for_assert,
    _scrub,
)

_ACTIONS = ("navigate", "extract_text")
_SESSION: Any | None = None
_ATEXIT_REGISTERED: bool = False


@dataclass(frozen=True)
class BrowserSection:
    profile_dir: str   # 原始配置值（相对 root 解析在守卫内完成）
    headed: bool
    timeout_s: float
    max_chars: int = 30000
    trusted_fake_ip_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


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
    return BrowserSection(
        profile_dir=profile_dir.strip(),
        headed=headed,
        timeout_s=float(timeout_s),
        max_chars=int(max_chars_raw),
        trusted_fake_ip_ranges=trusted_ranges,
    )


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
        }

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


def _default_launch_fn(*, user_data_dir: Path, headed: bool) -> Any:
    """playwright 封装（函数级延迟 import，基于实测版本 playwright 1.63.0 钉死，S16 🔵-6）：
    sync_playwright().start() + pw.chromium.launch_persistent_context(user_data_dir=..., headless=not headed)
    + context.on("close") 探活回调。
    import 失败（部分/损坏安装，🔴-1）→ ValueError 包装，绝不放任穿透。
    浏览器二进制缺失 → 显式 ValueError 提示人工 `uv run playwright install chromium`
    （§10 RF-2），绝不自动下载。
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError(
            f"browser 依赖已定位但导入失败（部分或损坏安装: {exc}），"
            "请重装: uv sync --extra browser（uv sync 会卸掉未列出的 extras，apple/dev 等须一并列出）"
        ) from exc

    pw = None
    try:
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=not bool(headed),
        )
        return _PlaywrightSessionAdapter(pw, ctx)
    except Exception as exc:
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        raw_msg = str(exc)
        binary_hint = ""
        if any(k in raw_msg.lower() for k in ("executable doesn't exist", "playwright install")):
            binary_hint = "（浏览器二进制缺失，请人工执行一次 `uv run playwright install chromium`，严禁自动下载）"
        raise ValueError(f"browser 操作失败（启动失败: {exc}）{binary_hint}") from exc


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


def browser_action(
    action: str,
    reason: str,
    *,
    url: str | None = None,
    section: BrowserSection | None = None,
    launch_fn: Callable[..., Any] | None = None,
    episode_dir: Path | None = None,
    scope: str = "creative",
    root: Path | None = None,
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
    if clean_action == "navigate":
        if not clean_url:
            raise ValueError("action='navigate' 时 url 不能为空")
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
    truncated = len(scrubbed) > cfg.max_chars
    capped = scrubbed[: cfg.max_chars] if truncated else scrubbed
    return {
        "action": "extract_text",
        "url": _scrub(cur_url),
        "text": capped,
        "truncated": truncated,
    }
