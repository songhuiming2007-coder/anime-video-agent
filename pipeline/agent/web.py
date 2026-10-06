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

import gzip
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from pipeline import paths
from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS, assert_egress_boundary

# N59（人 2026-10-06 裁决）：默认 = 上限 = 20。原 5 / 10（Spec 4 §3.2）对人物剖析、伏笔考据这类调研
# 远远不够（剖析类查询 limit=10 时两家全部截断、约一半仍是百科）；查什么、查几次由模型决定，
# harness 不按题材定策略，只给足够的条数。20 = Tavily 文档单次上限；Exa 免 key 入口实测 30 条
# 照给。代价：每条 snippet 封顶 500 字，一次至多约 1 万字进上下文、此后每轮重发（人已知悉）。
SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 20
# Spec 13 §3.2 常量依据：ANCHOR_MAX_CHARS=60（实测四页锚文本绝大多数 <40 字符，
# 60 容下长句锚并截断异常值）；LINKS_MAX_COUNT/CHARS=200/12000（JSON 口径实测四类
# 真实页面，内容链接区止于 idx 185 / 累计 11373 字符，覆盖全部样本的最小整百/整千值，
# 12000 ≈ max_fetch_chars(30000) 的 40%）。调大常量须附新实测表（§10 RF-1）。
ANCHOR_MAX_CHARS = 60
LINKS_MAX_COUNT = 200
LINKS_MAX_CHARS = 12000  # JSON 计长口径（json.dumps(entry, ensure_ascii=False)）
_TEXT_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/xhtml+xml",
    "text/markdown",
)


# Spec 15 §2.3 / §11 Q4：每条 snippet 封顶 500 字（人 2026-09-29 确认）。摘录只需够
# 辨认页面，全文由 web_fetch 取；Exa Highlights 可达上千字，不封顶时 5 条就占数千字，
# 且每轮后续 LLM 调用都会重发。
SEARCH_SNIPPET_MAX_CHARS = 500
# Spec 15 §2.4：全链最坏耗时 = 家数 × timeout_s，故链上至多 4 家。
SEARCH_MAX_PROVIDERS = 4
# Spec 15 §2.4 名字校验②：与 Spec 10 §2.9 KEY_ENV_NAME_RE（desktop/src/shared/constants.ts）同形。
_KEY_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*_(API_KEY|KEY|TOKEN)$")
# search 段旧五字段（DDG/360 时代的单端点 schema）：出现即判无效，不许被静默当新配置读。
_LEGACY_SEARCH_FIELDS = ("endpoint", "query_param", "api_key_env", "api_key_param")


@dataclass(frozen=True)
class ProviderCfg:
    name: str
    api_key_env: str = ""   # 指名的环境变量；空串 = 免 key 的家（exa_mcp）
    api_key: str = ""       # 已从环境变量读出；空串 = 无 key / 未就绪。绝不出模块。


@dataclass(frozen=True)
class WebConfig:
    search_providers: tuple[ProviderCfg, ...]
    search_timeout_s: float
    fetch_timeout_s: float
    max_fetch_bytes: int
    max_fetch_chars: int
    trusted_fake_ip_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    # 链上全部非空 key（Spec 15 §4.1，v0.2 🟡-4）：search 与 fetch 的出向脱敏都按它逐一替换。
    secret_values: tuple[str, ...] = ()


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _web_cfg_file(root: Path | None) -> Path:
    """local 优先（整份取代 web.json）：load_web_config 与 web_key_env_names 共用这一段选择。"""
    cfg_dir = Path(root or paths.ROOT) / "config" / "agent"
    local_cfg = cfg_dir / "web.local.json"
    return local_cfg if local_cfg.exists() else (cfg_dir / "web.json")


def _parse_web_config(
    root: Path | None, *, read_env: bool
) -> tuple[WebConfig | None, str]:
    """校验 web 配置 → (WebConfig | None, 无效原因)。read_env=False 时不读任何环境变量
    （web_key_env_names 只要名字，Spec 15 §2.7 第 1 条）。"""
    cfg_file = _web_cfg_file(root)
    if not cfg_file.exists():
        return None, ""
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, f"{cfg_file.name} 不是合法 JSON"
    if not isinstance(data, dict):
        return None, ""

    search = data.get("search")
    fetch = data.get("fetch")
    if not isinstance(search, dict) or not isinstance(fetch, dict):
        return None, ""

    legacy = [k for k in _LEGACY_SEARCH_FIELDS if k in search]
    if legacy:
        return None, (
            f"{cfg_file.name} 的 search 段含旧字段 {legacy}，"
            "请改用 search.providers（Spec 15 §2.4）"
        )

    providers_raw = search.get("providers")
    if (
        not isinstance(providers_raw, list)
        or not providers_raw
        or len(providers_raw) > SEARCH_MAX_PROVIDERS
    ):
        return None, f"search.providers 须为 1～{SEARCH_MAX_PROVIDERS} 项的数组"

    llm_env_name: str | None = None
    if any(isinstance(p, dict) and "api_key_env" in p for p in providers_raw):
        # Spec 4 §5.1 依赖白名单：web.py 顶层只导 paths 与 tools，这里函数内延迟导入（R3-1）。
        from pipeline.agent.llm import api_key_env_name

        llm_env_name = api_key_env_name(root)

    providers: list[ProviderCfg] = []
    seen: set[str] = set()
    for item in providers_raw:
        if not isinstance(item, dict):
            return None, "search.providers 每项须为对象"
        name = item.get("name")
        if not isinstance(name, str) or name not in _PROVIDERS:
            return None, f"search.providers 含未知检索服务 {name!r}"
        if name in seen:
            return None, f"search.providers 中 {name} 重复"
        seen.add(name)
        prefix = _PROVIDERS[name].key_env_prefix
        if prefix is None:
            if "api_key_env" in item:
                return None, f"search.providers.{name} 不接受 api_key_env"
            providers.append(ProviderCfg(name=name))
            continue
        env_name = item.get("api_key_env")
        if not isinstance(env_name, str) or not env_name.strip():
            return None, f"search.providers.{name} 缺少 api_key_env"
        env_name = env_name.strip()
        if (
            not env_name.startswith(prefix)
            or not _KEY_ENV_NAME_RE.match(env_name)
            or env_name == llm_env_name
        ):
            return None, (
                f"search.providers.{name}.api_key_env = {env_name!r} 不合规"
                f"（须以 {prefix} 开头、形如 *_API_KEY，且不得与 LLM 密钥同名）"
            )
        api_key = os.environ.get(env_name, "").strip() if read_env else ""
        providers.append(ProviderCfg(name=name, api_key_env=env_name, api_key=api_key))

    search_timeout_s = search.get("timeout_s")
    fetch_timeout_s = fetch.get("timeout_s")
    max_bytes = fetch.get("max_bytes")
    max_chars = fetch.get("max_chars")

    if not _is_number(search_timeout_s) or not (0 < float(search_timeout_s) <= 60):
        return None, ""
    if not _is_number(fetch_timeout_s) or not (0 < float(fetch_timeout_s) <= 60):
        return None, ""
    if not _is_int(max_bytes) or max_bytes < 65536:
        return None, ""
    if not _is_int(max_chars) or max_chars < 1000:
        return None, ""

    raw_ranges = data.get("trusted_fake_ip_ranges", [])
    if not isinstance(raw_ranges, list):
        return None, ""
    parsed_ranges: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw_ranges:
        if not isinstance(item, str) or not item.strip():
            return None, ""
        try:
            parsed_ranges.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            return None, ""

    return (
        WebConfig(
            search_providers=tuple(providers),
            search_timeout_s=float(search_timeout_s),
            fetch_timeout_s=float(fetch_timeout_s),
            max_fetch_bytes=int(max_bytes),
            max_fetch_chars=int(max_chars),
            trusted_fake_ip_ranges=tuple(parsed_ranges),
            secret_values=tuple(p.api_key for p in providers if p.api_key),
        ),
        "",
    )


def load_web_config(root: Path | None = None) -> WebConfig | None:
    """读 config/agent/web.local.json（优先）或 config/agent/web.json + 指名环境变量。

    缺失 / JSON 损坏 / 字段不全 / 约束违例 / search 段含旧字段 → None（显式降级，不静默
    回落默认值）。密钥只从 api_key_env 指名的环境变量读；指名了变量但为空 → **该家未就绪**，
    不使整份配置无效（Spec 15 §2.4、§11 Q2：否则缺 Tavily key 会连免 key 的 exa_mcp
    与 web_fetch 一起拖垮）。
    """
    return _parse_web_config(root, read_env=True)[0]


def web_key_env_names(root: Path | None = None) -> list[str]:
    """当前生效 web 配置链上各家指名的密钥环境变量名（Spec 15 §2.7 第 1 条）。

    供桌面端 PROBE_WEB_KEY_ENVS 问 core「该从钥匙串注入哪些名字」：纯读配置、不读环境
    变量、不读值；保持链序、去重；配置无效时返回空清单。只会回答通过 §2.4 名字校验的
    名字（族前缀、KEY_ENV_NAME_RE、≠ LLM 名）。
    """
    cfg, _ = _parse_web_config(root, read_env=False)
    if cfg is None:
        return []
    names: list[str] = []
    for p in cfg.search_providers:
        if p.api_key_env and p.api_key_env not in names:
            names.append(p.api_key_env)
    return names


def _invalid_config_error(root: Path | None) -> ValueError:
    reason = _parse_web_config(root, read_env=False)[1]
    msg = "缺少或无效的 config/agent/web.json（或 web.local.json）"
    return ValueError(f"{msg}：{reason}" if reason else msg)


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


def _redact_all(text: str, secrets: tuple[str, ...]) -> str:
    """链上全部非空 key 逐一 _redact_secret（Spec 15 §4.1，v0.2 🟡-4）：任一 key 都可能被
    页面或 provider 回显，只脱一个等于没脱。"""
    for secret in secrets:
        text = _redact_secret(text, secret)
    return text


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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """provider 请求不跟随重定向（Spec 15 §2.5 ⑤）：返回 None 让 3xx 以 HTTPError 浮出。

    provider 端点都是 POST API，被重定向本身就是端点漂移；urllib 跟随 301/302/303 会把
    POST 改成 GET、丢 body，却把 headers= 设的密钥头原样转发给新主机（2026-10-06 本机
    127.0.0.1 探针实测）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_PROVIDER_DIRECTOR: urllib.request.OpenerDirector | None = None


def _provider_opener(request: urllib.request.Request, timeout: float) -> Any:
    """检索 provider 专用 opener（不跟随重定向）。search_web 在调用点以 opener is None
    解析到它（B1 纪律，不做函数默认参数）。"""
    global _PROVIDER_DIRECTOR
    if _PROVIDER_DIRECTOR is None:
        _PROVIDER_DIRECTOR = urllib.request.build_opener(_NoRedirectHandler())
    return _PROVIDER_DIRECTOR.open(request, timeout=timeout)


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


class _LinkExtractor(HTMLParser):
    """<a> 链接提取：与 _TextExtractor 同款的 stdlib HTMLParser 子类。

    收集 (href, 锚文本) 对；script/style/noscript 内的链接同样跳过
    （复用 _TextExtractor._SKIP_TAGS，不复制第二份清单）。
    """

    _SKIP_TAGS = _TextExtractor._SKIP_TAGS

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_anchor = False
        self._href = ""
        self._parts: list[str] = []
        self.pairs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or self._in_anchor or tag != "a":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        href = attr_map.get("href", "").strip()
        if href:
            self._in_anchor = True
            self._href = href
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag == "a" and self._in_anchor:
            anchor = re.sub(r"\s+", " ", "".join(self._parts)).strip()
            self.pairs.append((self._href, anchor))
            self._in_anchor = False
            self._href = ""
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._in_anchor and data:
            self._parts.append(data)


def _extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Spec 13 §3.2 七步管线 → [{"url", "anchor"}]，未 scrub、未做清单双帽截断。

    1 数据源是已抓取的 HTML（零新请求）；2 urljoin(base_url=final_url) 绝对化；
    3 scheme ∈ {http, https}；4 按去 fragment 的 URL 去重，锚文本回填（首个非空胜出）；
    5 丢空锚；6 同站（hostname 相等）优先、组内文档序、跨站殿后；7 锚文本封顶 60。
    纯函数，可单测。
    """
    parser = _LinkExtractor()
    parser.feed(html)

    base_host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    entries: dict[str, dict[str, str]] = {}
    for href, anchor in parser.pairs:
        absolute = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlsplit(absolute)
        if (parts.scheme or "").lower() not in ("http", "https"):
            continue
        url = urllib.parse.urlunsplit(parts._replace(fragment=""))
        entry = entries.get(url)
        if entry is None:
            entries[url] = {"url": url, "anchor": anchor[:ANCHOR_MAX_CHARS]}
        elif not entry["anchor"] and anchor:
            entry["anchor"] = anchor[:ANCHOR_MAX_CHARS]

    same_site: list[dict[str, str]] = []
    cross_site: list[dict[str, str]] = []
    for entry in entries.values():
        if not entry["anchor"]:
            continue
        host = (urllib.parse.urlparse(entry["url"]).hostname or "").lower()
        (same_site if host == base_host else cross_site).append(entry)
    return same_site + cross_site


def _cap_links(
    entries: list[dict[str, str]], secrets: tuple[str, ...]
) -> tuple[list[dict[str, str]], bool]:
    """逐条 _scrub + _redact_all，再双帽截断（条数帽或 JSON 字符帽任一触发即截断）。

    §2.3：url 与 anchor 与正文同纪律过清洗，受限字样进上下文的是 [已脱敏]，会话不炸。
    §4.1：字符帽按逐条 json.dumps 计长累计，数组括号与条目间分隔符 ~400 字符从简不计
    （2×(n-1)+2），小于帽余量 12000-11373=627。
    """
    links: list[dict[str, str]] = []
    used_chars = 0
    truncated = False
    for entry in entries:
        cleaned = {
            "url": _redact_all(_scrub(entry["url"]), secrets),
            "anchor": _redact_all(_scrub(entry["anchor"]), secrets),
        }
        size = len(json.dumps(cleaned, ensure_ascii=False))
        if len(links) >= LINKS_MAX_COUNT or used_chars + size > LINKS_MAX_CHARS:
            truncated = True
            break
        used_chars += size
        links.append(cleaned)
    return links, truncated


def _extract_charset(content_type: str) -> str:
    m = re.search(r"charset=([^\s;\"']+)", content_type, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "utf-8"


def _read_body_capped(resp: Any, content_encoding: str, limit: int) -> bytes:
    """读响应体至 limit 封顶；gzip/deflate 流式解压且解压后字节同受封顶（防解压炸弹）。

    N45：请求不声明 gzip，但服务器仍可能强制压缩（python.org 实测无视
    Accept-Encoding: identity）；不处理会把压缩字节当正文，静默产出乱码净文。
    identity 之外的未知编码诚实报错并附升级 crawl 提示（严禁静默错解）。
    """
    encoding = content_encoding.lower().strip()
    if encoding in ("", "identity"):
        return resp.read(limit)
    if encoding == "gzip":
        try:
            with gzip.GzipFile(fileobj=resp) as gz:
                return gz.read(limit)
        except (EOFError, OSError) as exc:
            raise ValueError(
                f"gzip 响应体解压失败（{exc}）：静态获取被拒。按 STANDARD.md 五节"
                "升级链应升级 crawl（无头绕盾，Spec 5）。"
            ) from None
    if encoding == "deflate":
        decomp = zlib.decompressobj()
        out = bytearray()
        try:
            while len(out) <= limit:
                chunk = resp.read(65536)
                if not chunk:
                    out += decomp.flush()
                    break
                out += decomp.decompress(chunk, limit + 1 - len(out))
        except zlib.error as exc:
            raise ValueError(
                f"deflate 响应体解压失败（{exc}）：静态获取被拒。按 STANDARD.md 五节"
                "升级链应升级 crawl（无头绕盾，Spec 5）。"
            ) from None
        return bytes(out[:limit])
    raise ValueError(
        f"不支持的 Content-Encoding '{content_encoding}'（仅 identity/gzip/deflate）："
        "静态获取被拒。按 STANDARD.md 五节升级链应升级 crawl（无头绕盾，Spec 5）。"
    )


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


# ——— Spec 15：检索 provider 适配器（Exa 主 + Tavily 备）———
#
# 适配器只做「构造请求」与「解析响应」两件纯函数；egress 断言 / _guard_url / 清洗 /
# 脱敏由 search_web 主流程在每次发送前后统一做（§2.2，避免每家各写一遍而漏一个）。

# exa_mcp 的 objective 参数（Spec 15 §12 施工偏差 1）：2026-10-06 PR0 实测 tools/list 已把
# objective 标为必填（服务端当下仍宽容缺省）。人 2026-10-06 裁决固定传一段不含用户内容的
# 通用文案：没有新的外发面，服务端收紧校验时也不会断。
EXA_MCP_OBJECTIVE = (
    "Return the pages that best match the query, most relevant first."
)
# Exa MCP 文本块分隔符（2026-09-29 / 2026-10-06 两次实测，fixture exa_mcp_ok.sse.txt）。
_EXA_BLOCK_SEP = "\n\n---\n\n"


class _InBandError(Exception):
    """JSON-RPC 带内错误（HTTP 200 下 error 或 result.isError: true，v0.2 🔵-1）。"""


@dataclass(frozen=True)
class _Provider:
    endpoint: str
    # None = 免 key 的家；否则 api_key_env 必须以此前缀开头（§2.4 名字校验①）
    key_env_prefix: str | None
    # (endpoint, query, 请求条数, ProviderCfg) → 请求；endpoint 由主流程传入，保证断言 /
    # _guard_url 校验的地址与实际发送的地址是同一个
    build: Callable[[str, str, int, ProviderCfg], urllib.request.Request]
    parse: Callable[[str], list[dict[str, str]]]
    # 单次请求条数上限（N59 B-1）：None = 不设。请求条数 = min(limit + 1, 该值)；被它截住时按
    # Spec 15 §2.3「返回数 == 请求数即判 truncated」
    max_count: int | None = None


def _json_post(url: str, body: dict[str, Any], accept: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": "ava-agent/1.0",
        },
        method="POST",
    )


def _build_exa_mcp(
    endpoint: str, query: str, count: int, pcfg: ProviderCfg
) -> urllib.request.Request:
    return _json_post(
        endpoint,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": query,
                    "numResults": count,
                    "objective": EXA_MCP_OBJECTIVE,
                },
            },
        },
        "application/json, text/event-stream",
    )


def _parse_sse_json(body: str) -> dict[str, Any]:
    """SSE 响应 → 第一条带 result 或 error 的 JSON-RPC 消息；纯 JSON 响应原样解析。"""
    stripped = body.lstrip()
    if stripped.startswith("{"):
        msg = json.loads(stripped)
        if isinstance(msg, dict):
            return msg
        raise ValueError("JSON-RPC 响应不是对象")
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        msg = json.loads(payload)
        if isinstance(msg, dict) and ("result" in msg or "error" in msg):
            return msg
    raise ValueError("SSE 响应中没有 JSON-RPC 结果")


def _parse_exa_mcp(body: str) -> list[dict[str, str]]:
    msg = _parse_sse_json(body)
    if "error" in msg:
        err = msg["error"]
        text = err.get("message") if isinstance(err, dict) else err
        raise _InBandError(str(text))
    result = msg["result"]
    content = result["content"]
    texts = [
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    if result.get("isError"):
        raise _InBandError(" ".join(texts))
    results: list[dict[str, str]] = []
    for block in "\n".join(texts).split(_EXA_BLOCK_SEP):
        title = url = ""
        snippet_lines: list[str] = []
        in_highlights = False
        for line in block.split("\n"):
            if in_highlights:
                snippet_lines.append(line)
            elif line.startswith("Title:"):
                title = line[len("Title:"):].strip()
            elif line.startswith("URL:"):
                url = line[len("URL:"):].strip()
            elif line.startswith("Highlights:"):
                in_highlights = True
                snippet_lines.append(line[len("Highlights:"):])
        if url:
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": re.sub(r"\s+", " ", "\n".join(snippet_lines)).strip(),
                }
            )
    return results


def _build_tavily(
    endpoint: str, query: str, count: int, pcfg: ProviderCfg
) -> urllib.request.Request:
    req = _json_post(
        endpoint,
        {"query": query, "max_results": count, "search_depth": "basic"},
        "application/json",
    )
    # 密钥头一律 unredirected（§2.5 ⑤ 纵深）：即使将来换回跟随重定向的 opener 也不随跳转转发。
    req.add_unredirected_header("Authorization", f"Bearer {pcfg.api_key}")
    return req


def _parse_tavily(body: str) -> list[dict[str, str]]:
    data = json.loads(body)
    items = data["results"]
    if not isinstance(items, list):
        raise ValueError("results 不是数组")
    results: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url:
            results.append(
                {
                    "title": re.sub(r"\s+", " ", str(item.get("title") or "")).strip(),
                    "url": url,
                    "snippet": re.sub(r"\s+", " ", str(item.get("content") or "")).strip(),
                }
            )
    return results


# 注册表（§2.2）。exa_api 暂不注册（Spec 15 §12 施工偏差 2：人 2026-10-06 裁决先不申请
# EXA_API_KEY，没有真实响应不写解析器；写进链里会被 load_web_config 判为未知服务）。
_PROVIDERS: dict[str, _Provider] = {
    "exa_mcp": _Provider(
        "https://mcp.exa.ai/mcp?tools=web_search_exa", None, _build_exa_mcp, _parse_exa_mcp
    ),
    # Tavily 官方 API 参考：max_results 取值 0 <= x <= 20（2026-10-06 D29-B 核对）；实测 21 目前不报错，
    # 但不能指望——Exa 挂了轮到它时一个 400 就让备家形同不存在
    "tavily": _Provider(
        "https://api.tavily.com/search", "TAVILY_", _build_tavily, _parse_tavily, max_count=20
    ),
}


def _missing_key_reason(pcfg: ProviderCfg) -> str:
    return (
        f"需要环境变量 {pcfg.api_key_env}（未设置；终端：在 shell profile 里 export；"
        f"桌面端：security add-generic-password -s ava -a {pcfg.api_key_env} -w）"
    )


def _in_band_reason(text: str, query: str, secrets: tuple[str, ...]) -> str:
    """带内错误文案（v0.3 R2-6）：先把原始与归一后的 query 换成 <query>，清洗、脱敏之后再截 200 字
    （N59 B-2：先截后脱敏时，跨在截断处的 key 前半段会漏出来；与 snippet 路径同序）。"""
    out = text
    for q in sorted({query, _normalized_for_assert(query)}, key=len, reverse=True):
        if q:
            out = out.replace(q, "<query>")
    out = _redact_all(_scrub(re.sub(r"\s+", " ", out).strip()), secrets)
    return "带内错误：" + out[:200]


def search_web(
    query: str,
    limit: int = SEARCH_DEFAULT_LIMIT,
    *,
    config: WebConfig | None = None,
    opener: Callable[..., Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """关键词探测：按配置顺序尝试 provider 链（Spec 15），第一家成功的答。

    对链上每一家、每一次实际发送（§2.5）：构造请求 → assert_egress_boundary(该家端点,
    {"query": 归一后 query}) → _guard_url(端点) → opener(request, timeout)。
    可落下一家（§2.2）：429 / 5xx / 3xx（不跟随）/ 超时 / 连接失败 / 结构不符 / 带内错误 /
    解析出 0 条 / 免 key 的家 401/403。直接失败：egress 命中、_guard_url 拒连、配了 key 的家
    401/403。全链失败 → ValueError 逐家列原因（空结果不静默，Spec 4 §3.2）。
    零自动重试：每家至多 1 次请求。opener 为 None 时调用点解析 _provider_opener（B1 纪律）。
    """
    clamped_limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))
    cfg = config if config is not None else load_web_config(root)
    if cfg is None:
        raise _invalid_config_error(root)
    open_fn = opener if opener is not None else _provider_opener
    secrets = cfg.secret_values
    normalized_query = _normalized_for_assert(query)

    failures: list[str] = []
    attempted = False
    for pcfg in cfg.search_providers:
        provider = _PROVIDERS[pcfg.name]
        if provider.key_env_prefix is not None and not pcfg.api_key:
            failures.append(f"{pcfg.name}: {_missing_key_reason(pcfg)}")
            continue
        attempted = True
        count = clamped_limit + 1
        capped = provider.max_count is not None and count > provider.max_count
        if capped:
            count = provider.max_count  # type: ignore[assignment]
        req = provider.build(provider.endpoint, query, count, pcfg)
        assert_egress_boundary(provider.endpoint, {"query": normalized_query})
        _guard_url(provider.endpoint, trusted_ranges=cfg.trusted_fake_ip_ranges)

        try:
            resp = open_fn(req, timeout=cfg.search_timeout_s)
            headers = getattr(resp, "headers", None) or {}
            raw_bytes = _read_body_capped(
                resp, str(headers.get("Content-Encoding", "identity")), cfg.max_fetch_bytes
            )
            body = _decode_body(
                raw_bytes, str(headers.get("Content-Type", "application/json; charset=utf-8"))
            )
            items = provider.parse(body)
        except PermissionError:
            raise
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code in (401, 403):
                if provider.key_env_prefix is not None:
                    tried = f"；此前已尝试：{'；'.join(failures)}" if failures else ""
                    raise ValueError(
                        f"web_search {pcfg.name} 的密钥被拒（HTTP {code}）：检查环境变量 "
                        f"{pcfg.api_key_env} 的值（终端：shell profile；桌面端：钥匙串 "
                        f"security add-generic-password -s ava -a {pcfg.api_key_env} -w）{tried}"
                    ) from None
                reason = (
                    f"Exa 免 key 入口被拒（HTTP {code}），需申请 EXA_API_KEY 并补 exa_api "
                    "适配器（Spec 15 §12）"
                )
            elif 300 <= code < 400:
                reason = f"HTTP {code} 重定向（未跟随）"
            elif code == 429:
                reason = "HTTP 429（限流）"
            else:
                reason = f"HTTP {code}"
            failures.append(f"{pcfg.name}: {reason}")
            continue
        except _InBandError as exc:
            failures.append(f"{pcfg.name}: {_in_band_reason(str(exc), query, secrets)}")
            continue
        except TimeoutError as exc:
            failures.append(f"{pcfg.name}: 超时（{type(exc).__name__}）")
            continue
        except OSError as exc:  # URLError / gaierror / ConnectionError 等
            failures.append(f"{pcfg.name}: 连接失败（{type(exc).__name__}）")
            continue
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            failures.append(f"{pcfg.name}: 响应结构不符（{type(exc).__name__}）")
            continue

        seen_urls: set[str] = set()
        normalized: list[dict[str, str]] = []
        for item in items:
            url = item["url"]
            if (urllib.parse.urlparse(url).scheme or "").lower() not in ("http", "https"):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            normalized.append(
                {
                    "title": _redact_all(_scrub(item["title"]), secrets),
                    "url": _redact_all(_scrub(url), secrets),
                    "snippet": _redact_all(_scrub(item["snippet"]), secrets)[
                        :SEARCH_SNIPPET_MAX_CHARS
                    ],
                }
            )
        if not normalized:
            failures.append(f"{pcfg.name}: 解析为空（无结果或结构变更）")
            continue

        return {
            "query": query,
            "provider": _redact_all(pcfg.name, secrets),
            "results": normalized[:clamped_limit],
            # 请求被该家上限截住时多要的那一条拿不到，按 §2.3 以「返回数 == 请求数」判还有更多
            "truncated": len(normalized) > clamped_limit or (capped and len(items) >= count),
        }

    head = "web_search 全部检索服务失败" if attempted else "web_search 无可用检索服务"
    raise ValueError(_redact_all(f"{head}：{'；'.join(failures)}", secrets))


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
    opener GET（timeout=fetch_timeout_s，读至 max_fetch_bytes 即断）→
    Content-Type 白名单校验（§2.5）→ HTTPError 转含升级提示的
    ValueError（§3.4）→ Content-Encoding 处理（N45 修订：不声明 gzip，
    但服务器强制 gzip/deflate 时流式解压且解压后同受 max_fetch_bytes 封顶，
    未知编码诚实报错附升级 crawl 提示）→ charset 解码（响应头 charset 优先，回落
    utf-8 errors="replace"；不探 <meta>，§2.5⑤）→
    _TextExtractor 净文 → _scrub → max_fetch_chars 截断 →
    final_url 凭据值替换 "***" 并对 geturl() 结果再验 scheme →
    _extract_links(decoded, raw_final_url) 链接清单（同站优先、去 fragment 去重、
    丢空锚、锚文本封顶）逐条 _scrub/_redact_all 后双帽截断 →
    返回 §3.3 契约（Spec 13 增 links / links_truncated 两键；
    fetched_bytes 为进入解码的字节数——压缩响应为解压后字节，N45 修订）。
    """
    cfg = config if config is not None else load_web_config(root)
    if cfg is None:
        raise _invalid_config_error(root)

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

    raw_bytes = _read_body_capped(
        resp, str(headers.get("Content-Encoding", "identity")), cfg.max_fetch_bytes
    )
    fetched_bytes = len(raw_bytes)
    decoded = _decode_body(raw_bytes, content_type)

    extractor = _TextExtractor()
    extractor.feed(decoded)
    text = _redact_all(_scrub(extractor.get_text()), cfg.secret_values)

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

    final_url = _redact_all(_scrub(raw_final_url), cfg.secret_values)
    status_code = int(getattr(resp, "status", None) or getattr(resp, "code", 200))

    links, links_truncated = _cap_links(
        _extract_links(decoded, raw_final_url), cfg.secret_values
    )

    return {
        "url": _redact_all(url, cfg.secret_values),
        "final_url": final_url,
        "status": status_code,
        "content_type": content_type,
        "text": text,
        "truncated": truncated,
        "fetched_bytes": fetched_bytes,
        "links": links,
        "links_truncated": links_truncated,
    }
