"""pipeline.agent.web_crawl: crawl 无头渲染抓取工具（Spec 5 / ADR-0021，升级链第二级）。

纪律：
1. 顶层零重依赖：crawl4ai 只在 _default_crawl_fn 内函数级延迟 import；
   顶层只 import stdlib + pipeline.agent.web（Spec 4 的守卫/清洗/配置复用）；
2. 出网双闸沿用 Spec 4：发送前 assert_egress_boundary（迭代 unquote 归一后）
   + _guard_url（scheme 白名单 + 私网拒连）；
3. 只读：不写任何文件；抓回内容经 _scrub 后作数据回喂；
4. 诚实失败：渲染失败如实报错并提示升级 browser，严禁静默降级水百科
   （STANDARD.md:196-199）。
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline import paths
from pipeline.agent.tools import assert_egress_boundary
from pipeline.agent.web import (
    _guard_url,
    _normalized_for_assert,
    _scrub,
)


@dataclass(frozen=True)
class CrawlSection:
    timeout_s: float
    max_chars: int
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


def load_crawl_section(root: Path | None = None) -> CrawlSection | None:
    """读 web.local.json（优先）/ web.json 的 crawl 段，逐字段按 §3.1 校验。

    缺失/损坏/违例 → None（显式降级，不拖垮基座段，§2.6）。
    """
    cfg_dir = Path(root or paths.ROOT) / "config" / "agent"
    local_cfg = cfg_dir / "web.local.json"
    cfg_file = local_cfg if local_cfg.exists() else (cfg_dir / "web.json")
    if not cfg_file.exists():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    crawl = data.get("crawl")
    if not isinstance(crawl, dict):
        return None

    timeout_s = crawl.get("timeout_s")
    max_chars = crawl.get("max_chars")
    if not _is_number(timeout_s) or not (0 < float(timeout_s) <= 120):
        return None
    if not _is_int(max_chars) or int(max_chars) < 1000:
        return None

    trusted_ranges = _parse_trusted_ranges(data.get("trusted_fake_ip_ranges", []))
    return CrawlSection(
        timeout_s=float(timeout_s),
        max_chars=int(max_chars),
        trusted_fake_ip_ranges=trusted_ranges,
    )


def _resolve_bound_crawl4ai_dir() -> str | None:
    """读取已导入 crawl4ai 在首次 import 时绑定的基目录的父目录（即当时生效的 env 值）。

    真包 crawl4ai/async_database.py:17-19 的写法是
    ``base_directory = os.path.join(os.getenv("CRAWL4_AI_BASE_DIRECTORY", Path.home()), ".crawl4ai")``，
    故 parent 恰为当时生效的 ``CRAWL4_AI_BASE_DIRECTORY``（未设时为 home）。
    取不到该子模块或它没有 base_directory（旧版/部分安装/假包）→ None（绑定目录不可知）。
    """
    async_db = sys.modules.get("crawl4ai.async_database")
    if async_db is None:
        return None
    bound = getattr(async_db, "base_directory", None)
    if not bound:
        return None
    return str(Path(str(bound)).parent)


def _default_crawl_fn(url: str, *, stealth: bool, timeout_s: float) -> dict[str, str]:
    """crawl4ai 封装（函数级延迟 import，基于实测版本 crawl4ai 0.9.4 钉死，S16 🔵-6）。

    1. 复用 data/ 可达检查（S16 🟡-3 / 🔵-5）：data/ 不可达立即报错，绝不自动创建 data/；
    2. 在首次 import crawl4ai 前直接赋值 os.environ["CRAWL4_AI_BASE_DIRECTORY"] = str(paths.DATA / "crawl4ai")
       （严禁用 setdefault，防外部/pi 预设值导致两边共用 ~/.crawl4ai）；若导入前 crawl4ai 已在
       sys.modules 且其 crawl4ai.async_database.base_directory 的父目录非目标目录（或不可知），
       当场抛 ValueError 诚实失败（S16 🔵-5）；
    3. 经 ThreadPoolExecutor(max_workers=1).submit(asyncio.run, _run()).result() 在独立线程执行协程，
       隔离 playwright sync API 遗留的线程级 _set_running_loop 状态（S16 🟡-2）；
    4. import 失败（部分/损坏安装，🔴-1）与渲染异常一律包装为 ValueError（含升级 browser 提示）。
    前置：人工 `uv run crawl4ai-setup` 装浏览器二进制（🟡-6，§10 RF-2）。
    """
    data_dir = Path(paths.DATA)
    if not data_dir.is_dir():
        raise ValueError(
            f"data/ 不可达（{data_dir}）：插盘或 preflight --init（严禁自动创建 data/）"
        )
    target_base_dir = str(data_dir / "crawl4ai")

    if sys.modules.get("crawl4ai") is not None:
        bound_dir = _resolve_bound_crawl4ai_dir()
        if bound_dir is None or os.path.normpath(bound_dir) != os.path.normpath(target_base_dir):
            raise ValueError(
                f"crawl4ai 已在进程内提前导入并绑定在 {bound_dir or '未知目录'}（非目标 {target_base_dir}），"
                "无法重绑定 CRAWL4_AI_BASE_DIRECTORY，拒绝静默污染 home 目录"
            )

    os.environ["CRAWL4_AI_BASE_DIRECTORY"] = target_base_dir

    try:
        import asyncio
        import concurrent.futures
        import crawl4ai  # type: ignore[import-not-found]
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError(
            f"crawl 依赖已定位但导入失败（部分或损坏安装: {exc}），"
            "请重装: uv sync --extra crawl"
        ) from exc

    async def _run() -> dict[str, str]:
        browser_cfg = BrowserConfig(
            headless=True,
            enable_stealth=bool(stealth),
        )
        run_cfg = CrawlerRunConfig(
            page_timeout=int(timeout_s * 1000),
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            res = await crawler.arun(url=url, config=run_cfg)
            if not getattr(res, "success", True):
                err_msg = getattr(res, "error_message", "") or "crawl4ai arun failed"
                raise RuntimeError(err_msg)
            md_obj = getattr(res, "markdown", None)
            if hasattr(md_obj, "fit_markdown") and md_obj.fit_markdown:
                md_text = str(md_obj.fit_markdown)
            elif hasattr(md_obj, "raw_markdown") and md_obj.raw_markdown:
                md_text = str(md_obj.raw_markdown)
            else:
                md_text = str(md_obj or "")
            final_u = str(getattr(res, "redirected_url", None) or getattr(res, "url", None) or url)
            return {"final_url": final_u, "markdown": md_text}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()
    except PermissionError:
        raise
    except Exception as exc:
        raw_msg = str(exc)
        binary_hint = ""
        if any(k in raw_msg.lower() for k in ("executable doesn't exist", "playwright install", "browsertype.launch")):
            binary_hint = "（浏览器二进制缺失，请人工执行一次 `uv run crawl4ai-setup`，严禁自动下载）"
        raise ValueError(
            f"无头渲染失败（{exc}）{binary_hint}：按 STANDARD.md 五节升级链应升级 browser"
            "（登录态，需人审卡）；严禁静默降级为水百科（STANDARD.md:196-199）。"
        ) from exc


def crawl_page(
    url: str,
    reason: str,
    *,
    stealth: bool = False,
    section: CrawlSection | None = None,
    crawl_fn: Callable[..., dict[str, str]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """流程：reason 非空校验 → load_crawl_section（None → ValueError 显式降级）→
    assert_egress_boundary(url, {"url": _normalized_for_assert(url)}) →
    _guard_url(url) → crawl_fn（None 时调用点解析 _default_crawl_fn，B1 纪律）
    → _scrub(markdown) → max_chars 截断（truncated=True）→ 返回 §3.2 契约。
    渲染异常包装为含升级 browser 提示的 ValueError（§3.4）。
    """
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("url 不能为空")
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("reason 不能为空（必须说明为什么 web_fetch 不够）")

    cfg = section if section is not None else load_crawl_section(root)
    if cfg is None:
        raise ValueError(
            "缺少或损坏 config/agent/web.json 的 crawl 段；"
            "按 STANDARD.md 五节升级链应升级 browser（登录态，需人审卡）；"
            "严禁静默降级为水百科（STANDARD.md:196-199）。"
        )

    assert_egress_boundary(
        clean_url,
        {
            "url": _normalized_for_assert(clean_url),
            "reason": _normalized_for_assert(clean_reason),
        },
    )
    _guard_url(clean_url, trusted_ranges=cfg.trusted_fake_ip_ranges)

    fn = _default_crawl_fn if crawl_fn is None else crawl_fn
    try:
        raw = fn(clean_url, stealth=bool(stealth), timeout_s=cfg.timeout_s)
    except PermissionError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "升级 browser" not in msg and "导入失败" not in msg:
            msg = (
                f"无头渲染失败（{exc}）：按 STANDARD.md 五节升级链应升级 browser"
                "（登录态，需人审卡）；严禁静默降级为水百科（STANDARD.md:196-199）。"
            )
        raise ValueError(msg) from exc

    final_url = str(raw.get("final_url") or clean_url).strip() or clean_url
    if final_url != clean_url:
        _guard_url(final_url, trusted_ranges=cfg.trusted_fake_ip_ranges)

    raw_md = str(raw.get("markdown") or "")
    scrubbed_md = _scrub(raw_md)
    truncated = len(scrubbed_md) > cfg.max_chars
    capped_md = scrubbed_md[: cfg.max_chars] if truncated else scrubbed_md

    return {
        "url": clean_url,
        "final_url": _scrub(final_url),
        "markdown": capped_md,
        "truncated": truncated,
        "stealth": bool(stealth),
    }
