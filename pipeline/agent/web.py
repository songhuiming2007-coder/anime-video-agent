"""pipeline.agent.web: web_search / web_fetch 只读网络工具实现（Spec 4 / ADR-0021）。

纪律：
1. 零重依赖：纯 stdlib（urllib / html.parser / ipaddress / socket），
   与 llm.py:3-4 docstring 同款先例；
2. 仓库仅有的两条出网路径之一（另一条是 llm.py）：每个 HTTP 请求发送前
   必过 assert_egress_boundary，且 query/URL 先迭代 unquote 归一（§2.4①）；
3. 只读：本模块不写任何文件、无日志、密钥不出模块（§2.3 四条）；
4. 抓回内容是数据：HTML 剥离 + 体积封顶 + _scrub 清洗，不做注入识别
   （§2.5 已知上限）；
5. 零自动重试：静态获取失败诚实报错并提示升级链（STANDARD.md:196-198）。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from pipeline import paths
from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS, assert_egress_boundary

SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT = 10
_TEXT_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/xhtml+xml",
    "text/markdown",
)


@dataclass(frozen=True)
class WebConfig:
    search_endpoint: str
    search_query_param: str
    api_key: str            # 已从环境变量读出；空串 = 无凭据模式。绝不出模块。
    api_key_param: str
    search_timeout_s: float
    fetch_timeout_s: float
    max_fetch_bytes: int
    max_fetch_chars: int
    trusted_fake_ip_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def load_web_config(root: Path | None = None) -> WebConfig | None:
    """读 config/agent/web.local.json（优先）或 config/agent/web.json + 指名环境变量。

    缺失 / JSON 损坏 / 字段不全 / 约束违例 → None（显式降级，不静默回落默认值）。
    镜像 llm.py:51-76 load_llm_config 纪律：密钥只从 api_key_env 指名的
    环境变量读；指名了变量但环境变量为空 → None（配了凭据却拿不到 = 配置错误，
    不许静默退成无凭据模式发裸请求）。
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

    search = data.get("search")
    fetch = data.get("fetch")
    if not isinstance(search, dict) or not isinstance(fetch, dict):
        return None

    endpoint = search.get("endpoint")
    query_param = search.get("query_param")
    api_key_env = search.get("api_key_env")
    api_key_param = search.get("api_key_param")
    search_timeout_s = search.get("timeout_s")

    fetch_timeout_s = fetch.get("timeout_s")
    max_bytes = fetch.get("max_bytes")
    max_chars = fetch.get("max_chars")

    if not isinstance(endpoint, str) or not re.match(r"^https?://\S+", endpoint.strip()):
        return None
    if not isinstance(query_param, str) or not query_param.strip():
        return None
    if not isinstance(api_key_env, str) or not isinstance(api_key_param, str):
        return None

    env_name = api_key_env.strip()
    param_name = api_key_param.strip()
    if bool(env_name) != bool(param_name):
        return None
    if env_name:
        api_key = os.environ.get(env_name, "").strip()
        if not api_key:
            return None
    else:
        api_key = ""

    if not _is_number(search_timeout_s) or not (0 < float(search_timeout_s) <= 60):
        return None
    if not _is_number(fetch_timeout_s) or not (0 < float(fetch_timeout_s) <= 60):
        return None
    if not _is_int(max_bytes) or max_bytes < 65536:
        return None
    if not _is_int(max_chars) or max_chars < 1000:
        return None

    raw_ranges = data.get("trusted_fake_ip_ranges", [])
    if not isinstance(raw_ranges, list):
        return None
    parsed_ranges: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw_ranges:
        if not isinstance(item, str) or not item.strip():
            return None
        try:
            parsed_ranges.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            return None

    return WebConfig(
        search_endpoint=endpoint.strip(),
        search_query_param=query_param.strip(),
        api_key=api_key,
        api_key_param=param_name,
        search_timeout_s=float(search_timeout_s),
        fetch_timeout_s=float(fetch_timeout_s),
        max_fetch_bytes=int(max_bytes),
        max_fetch_chars=int(max_chars),
        trusted_fake_ip_ranges=tuple(parsed_ranges),
    )


def _normalized_for_assert(text: str) -> str:
    """egress 断言前的归一（v0.2，🟡-7）：迭代 urllib.parse.unquote 至稳定
    （封顶 5 轮，防 %25 双重编码链死循环）。
    已知上限：不做 Unicode NFKC / 同形字归一（§10 RF-11）。"""
    cur = text
    for _ in range(5):
        nxt = urllib.parse.unquote(cur)
        if nxt == cur:
            break
        cur = nxt
    return cur


def _scrub(text: str) -> str:
    """RESTRICTED_EGRESS_PATTERNS 替换为 [已脱敏]（re.IGNORECASE，
    镜像 status_card.py:113-116 先例，🔵-6 口径登记）。

    模式常量从 tools.py import 复用，严禁复制第二份清单（§2.4③）。
    """
    out = text
    for pattern in RESTRICTED_EGRESS_PATTERNS:
        out = re.sub(re.escape(pattern), "[已脱敏]", out, flags=re.IGNORECASE)
    return out


def _redact_secret(text: str, api_key: str) -> str:
    if not api_key or not text:
        return text
    out = text.replace(api_key, "***")
    quoted = urllib.parse.quote(api_key, safe="")
    if quoted and quoted != api_key:
        out = out.replace(quoted, "***")
    return out


def _parse_ip_literal(
    host_clean: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """识别 IP 字面量（含十进制/缩写/十六进制/八进制等非标准 IPv4 写法，v0.4 D）。"""
    try:
        return ipaddress.ip_address(host_clean)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host_clean))
    except OSError:
        return None


def _guard_url(
    url: str,
    *,
    trusted_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (),
) -> None:
    """出网目标收敛（§2.6）：scheme 白名单 + 私网/保留地址拒连 + 域名 fake-ip 段放行。

    非 http/https → PermissionError；主机为 IP 字面量（含十进制/缩写/十六进制/八进制
    非标准写法）时永远按原六谓词严格判定（trusted_ranges 不生效）；主机为域名时，
    解析地址落在任一 trusted_ranges 内则放行，其余地址仍过 is_private/is_loopback/
    is_link_local/is_multicast/is_reserved/is_unspecified 任一命中 → PermissionError。
    DNS 解析失败（gaierror，OSError 子类）不捕获，自然传播为断网错误数据。
    """
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise PermissionError(f"仅允许 http/https 协议，拒绝 scheme '{parsed.scheme}'")
    host = parsed.hostname
    if not host:
        raise PermissionError("URL 缺少主机名")

    host_clean = host.split("%")[0]
    literal_ip = _parse_ip_literal(host_clean)
    if literal_ip is not None:
        is_ip_literal = True
        resolved_ips = [literal_ip]
    else:
        is_ip_literal = False
        infos = socket.getaddrinfo(host, None)
        if not infos:
            raise PermissionError(f"无法解析主机名 '{host}'")
        resolved_ips = []
        for info in infos:
            raw_ip = str(info[4][0]).split("%")[0]
            try:
                resolved_ips.append(ipaddress.ip_address(raw_ip))
            except ValueError as exc:
                raise PermissionError(f"非法 IP 地址 '{raw_ip}'") from exc

    for ip in resolved_ips:
        if not is_ip_literal and any(
            ip.version == net.version and ip in net for net in trusted_ranges
        ):
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise PermissionError(f"拒绝访问内网或保留地址: {host} ({ip})")


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """逐跳重跑 _guard_url 的重定向处理器（§2.6③⑤；链长沿用默认上限 10，🔵-2）。"""

    def __init__(
        self,
        trusted_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (),
    ) -> None:
        super().__init__()
        self._trusted_ranges = tuple(trusted_ranges)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _guard_url(newurl, trusted_ranges=self._trusted_ranges)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER_CACHE: dict[
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    urllib.request.OpenerDirector,
] = {}


def _default_opener(
    trusted_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (),
) -> Callable[..., Any]:
    """按 trusted_ranges 元组缓存带 _GuardedRedirectHandler 的 opener。

    选型依据（v0.4 A3）：按清单元组缓存（而非无参单例或每次新建）——既保证不同
    trusted_fake_ip_ranges 配置下的重定向 handler 严格持有对应网段、互不串味，
    又避免同一配置下每次请求重复构造 OpenerDirector。
    注意（Spec 2 红队三轮 B1 教训）：默认 opener 绝不做成 search_web/fetch_web
    的函数默认参数——默认参数在 def 时绑定会穿透运行期 mock，一律在调用点以
    opener is None 解析。
    """
    key = tuple(trusted_ranges)
    director = _OPENER_CACHE.get(key)
    if director is None:
        director = urllib.request.build_opener(
            _GuardedRedirectHandler(trusted_ranges=key)
        )
        _OPENER_CACHE[key] = director
    return director.open


class _TextExtractor(HTMLParser):
    """HTML → 净文：收集文本节点，跳过 script/style/noscript 整块，丢弃全部属性。"""

    _SKIP_TAGS = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            cleaned = data.replace("<", " ").replace(">", " ").strip()
            if cleaned:
                self._chunks.append(cleaned)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._chunks)).strip()


def _decode_ddg_href(href: str) -> str:
    """若为 DuckDuckGo 重定向链接（含 uddg 参数），解码出真实目标 URL。"""
    raw = href.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return urllib.parse.unquote(qs["uddg"][0])
    except Exception:
        pass
    return raw


class _SearchResultParser(HTMLParser):
    """搜索结果页解析：提取 标题/URL/摘要 三元组，uddg 重定向参数解码。

    DOM 结构以 2026-09 观测为准（约）；fixture 钉死（§7.1 T1），线上漂移时
    由 §3.2 空结果纪律诚实报错（§7.1 T16）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._title_depth = 0
        self._in_snippet = False
        self._snippet_depth = 0
        self._cur_url = ""
        self._cur_title_parts: list[str] = []
        self._cur_snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        classes = set(attr_map.get("class", "").split())

        if tag.lower() == "a" and "result__a" in classes:
            href = attr_map.get("href", "").strip()
            if href:
                self._in_title = True
                self._title_depth = 1
                self._cur_url = _decode_ddg_href(href)
                self._cur_title_parts = []
                return

        if self._in_title:
            self._title_depth += 1

        if "result__snippet" in classes and not self._in_snippet:
            self._in_snippet = True
            self._snippet_depth = 1
            self._cur_snippet_parts = []
            return

        if self._in_snippet:
            self._snippet_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._in_title:
            self._title_depth -= 1
            if self._title_depth <= 0:
                self._in_title = False
                title = re.sub(r"\s+", " ", "".join(self._cur_title_parts)).strip()
                if title and self._cur_url:
                    self.results.append(
                        {"title": title, "url": self._cur_url, "snippet": ""}
                    )
                self._cur_url = ""
                self._cur_title_parts = []

        if self._in_snippet:
            self._snippet_depth -= 1
            if self._snippet_depth <= 0:
                self._in_snippet = False
                snippet = re.sub(r"\s+", " ", "".join(self._cur_snippet_parts)).strip()
                if self.results and not self.results[-1]["snippet"]:
                    self.results[-1]["snippet"] = snippet
                self._cur_snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._cur_title_parts.append(data)
        elif self._in_snippet:
            self._cur_snippet_parts.append(data)


def _extract_charset(content_type: str) -> str:
    m = re.search(r"charset=([^\s;\"']+)", content_type, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "utf-8"


def _decode_body(raw_bytes: bytes, content_type: str) -> str:
    charset = _extract_charset(content_type)
    try:
        return raw_bytes.decode(charset, errors="replace")
    except LookupError:
        return raw_bytes.decode("utf-8", errors="replace")


def _raise_http_upgrade_error(exc: urllib.error.HTTPError) -> None:
    raise ValueError(
        f"HTTP {exc.code} {exc.reason}：静态获取被拒。按 STANDARD.md 五节升级链"
        "应升级 crawl（无头绕盾，Spec 5）；严禁静默降级为水百科（STANDARD.md:196-198）。"
    ) from None


def search_web(
    query: str,
    limit: int = SEARCH_DEFAULT_LIMIT,
    *,
    config: WebConfig | None = None,
    opener: Callable[..., Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """关键词探测：GET endpoint?<query_param>=<query>[&<api_key_param>=<key>]。

    流程：limit clamp → load_web_config（None → ValueError 显式降级）→
    assert_egress_boundary(endpoint, {"query": _normalized_for_assert(query)})
    （发送前最后一道，v0.2 归一后断言）→ _guard_url(完整请求串) →
    opener(request, timeout=search_timeout_s) → 解析三元组（0 条 →
    ValueError 空结果纪律，§3.2）→ _scrub 文本字段 → 返回 §3.2 契约。
    opener 为 None 时调用点解析 _default_opener()（B1 纪律）。
    """
    clamped_limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))
    cfg = config if config is not None else load_web_config(root)
    if cfg is None:
        raise ValueError("缺少或无效的 config/agent/web.json（或指名环境变量未设置）")

    assert_egress_boundary(
        cfg.search_endpoint, {"query": _normalized_for_assert(query)}
    )

    parsed_ep = urllib.parse.urlparse(cfg.search_endpoint)
    qs_pairs = urllib.parse.parse_qsl(parsed_ep.query, keep_blank_values=True)
    qs_pairs.append((cfg.search_query_param, query))
    if cfg.api_key and cfg.api_key_param:
        qs_pairs.append((cfg.api_key_param, cfg.api_key))
    full_query = urllib.parse.urlencode(qs_pairs)
    req_url = urllib.parse.urlunparse(parsed_ep._replace(query=full_query))

    _guard_url(req_url, trusted_ranges=cfg.trusted_fake_ip_ranges)

    open_fn = (
        opener
        if opener is not None
        else _default_opener(cfg.trusted_fake_ip_ranges)
    )
    req = urllib.request.Request(req_url, headers={"User-Agent": "ava-agent/1.0"})
    try:
        resp = open_fn(req, timeout=cfg.search_timeout_s)
    except urllib.error.HTTPError as exc:
        _raise_http_upgrade_error(exc)

    raw_bytes = resp.read(cfg.max_fetch_bytes)
    headers = getattr(resp, "headers", None) or {}
    content_type = str(headers.get("Content-Type", "text/html; charset=utf-8"))
    html_text = _decode_body(raw_bytes, content_type)

    parser = _SearchResultParser()
    parser.feed(html_text)
    if not parser.results:
        raise ValueError(
            "搜索解析为空（无结果或结构变更）：可能是无结果，也可能是端点页面结构已变更（解析器 fixture 失效）"
        )

    truncated = len(parser.results) > clamped_limit
    sliced = parser.results[:clamped_limit]
    cleaned_results = [
        {
            "title": _redact_secret(_scrub(item["title"]), cfg.api_key),
            "url": _redact_secret(_scrub(item["url"]), cfg.api_key),
            "snippet": _redact_secret(_scrub(item["snippet"]), cfg.api_key),
        }
        for item in sliced
    ]
    return {
        "query": query,
        "provider": _redact_secret(cfg.search_endpoint, cfg.api_key),
        "results": cleaned_results,
        "truncated": truncated,
    }


def fetch_web(
    url: str,
    *,
    config: WebConfig | None = None,
    opener: Callable[..., Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """单 URL 静态抓取净文（升级链第一级）。

    流程：load_web_config → assert_egress_boundary(url,
    {"url": _normalized_for_assert(url)}) → _guard_url(url) →
    opener GET（timeout=fetch_timeout_s，流式读至 max_fetch_bytes 即断）→
    Content-Type 白名单校验（§2.5）→ HTTPError 转含升级提示的
    ValueError（§3.4）→ charset 解码（响应头 charset 优先，回落
    utf-8 errors="replace"；不探 <meta>、不声明 gzip，§2.5⑤）→
    _TextExtractor 净文 → _scrub → max_fetch_chars 截断 →
    final_url 凭据值替换 "***" 并对 geturl() 结果再验 scheme →
    返回 §3.3 契约。
    """
    cfg = config if config is not None else load_web_config(root)
    if cfg is None:
        raise ValueError("缺少或无效的 config/agent/web.json（或指名环境变量未设置）")

    assert_egress_boundary(url, {"url": _normalized_for_assert(url)})
    _guard_url(url, trusted_ranges=cfg.trusted_fake_ip_ranges)

    open_fn = (
        opener
        if opener is not None
        else _default_opener(cfg.trusted_fake_ip_ranges)
    )
    req = urllib.request.Request(url, headers={"User-Agent": "ava-agent/1.0"})
    try:
        resp = open_fn(req, timeout=cfg.fetch_timeout_s)
    except urllib.error.HTTPError as exc:
        _raise_http_upgrade_error(exc)

    headers = getattr(resp, "headers", None) or {}
    content_type = str(headers.get("Content-Type", "text/html; charset=utf-8"))
    ct_lower = content_type.lower().strip()
    if not any(ct_lower.startswith(prefix) for prefix in _TEXT_CONTENT_TYPES):
        raise ValueError(
            f"不支持的 Content-Type '{content_type}'（仅允许文本类 {_TEXT_CONTENT_TYPES}）"
        )

    raw_bytes = resp.read(cfg.max_fetch_bytes)
    fetched_bytes = len(raw_bytes)
    decoded = _decode_body(raw_bytes, content_type)

    extractor = _TextExtractor()
    extractor.feed(decoded)
    text = _redact_secret(_scrub(extractor.get_text()), cfg.api_key)

    truncated = False
    if len(text) > cfg.max_fetch_chars:
        text = text[: cfg.max_fetch_chars]
        truncated = True

    raw_final_url = (
        resp.geturl() if hasattr(resp, "geturl") and resp.geturl() else url
    )
    final_scheme = (urllib.parse.urlparse(raw_final_url).scheme or "").lower()
    if final_scheme not in ("http", "https"):
        raise PermissionError(f"重定向最终 URL 协议非法: '{final_scheme}'")

    final_url = _redact_secret(_scrub(raw_final_url), cfg.api_key)
    status_code = int(getattr(resp, "status", None) or getattr(resp, "code", 200))

    return {
        "url": _redact_secret(url, cfg.api_key),
        "final_url": final_url,
        "status": status_code,
        "content_type": content_type,
        "text": text,
        "truncated": truncated,
        "fetched_bytes": fetched_bytes,
    }
