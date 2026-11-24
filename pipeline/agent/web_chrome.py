"""AVA 主力 Chrome 后台接入层：通过标准 chrome-devtools-mcp --autoConnect 连接日常 Chrome。

设计边界（docs/dev/plans/2026-11-14-chrome-devtools-mcp-spec.md）：
1. 与 Camoufox `browser` 完全正交：绝不读写 `data/browser-profile-firefox`、`cookies_for`
   或修改高 DPI 窗口缩放；仅作为登录态/交互/已打开标签页的独立补充通道。
2. 后台焦点硬隔离：参数表不暴露 `background` / `bringToFront`；`new_page` 硬编码
   `background=True`，`select_page` 硬编码 `bringToFront=False`。
3. Tab 归属硬隔离：仅记录本次由 `open` 创建的 `_OWNED_PAGES`；`close` 严禁关闭非自有页；
   读写/交互非自有页必须显式传 `allow_existing=True` 并经人审卡放行；`atexit` 只清理自有页。
4. 安全出网与凭证脱敏：导航前后过 `_guard_url` + `assert_egress_boundary`，子进程走
   `browser_env()`（N50），`get_network` 剥离 `Cookie`/`Set-Cookie`/`Authorization` 等头，
   所有返回文本过 `_scrub` + `scrub_secrets` 并受 `max_chars` 封顶。
"""

from __future__ import annotations

import atexit
import base64
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import datetime
import errno
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any
from urllib.parse import urlparse

from pipeline.agent import vision
from pipeline.agent.tools import assert_egress_boundary, scrub_secrets
from pipeline.agent.web import (
    _clean_refs,
    _extract_images,
    _extract_links,
    _guard_url,
    _normalized_for_assert,
    _scrub,
    _TextExtractor,
)
from pipeline.agent.web_browser import _parse_trusted_ranges, browser_env

CHROME_ACTIONS: tuple[str, ...] = (
    "list_pages",
    "open",
    "navigate",
    "close",
    "snapshot",
    "extract_text",
    "screenshot",
    "click",
    "fill",
    "wait_for",
    "list_network",
    "get_network",
    "evaluate",
)

# 常量取值理由见 docs/dev/plans/2026-11-14-chrome-devtools-mcp-spec.md §4.2
# 安全加固：精确锁定 npm 版本，禁止 @latest 自动拉取未审计上游版本（防 TCC 进程树内供应链 RCE）
_DEFAULT_COMMAND = "npx"
_PINNED_MCP_PACKAGE = "chrome-devtools-mcp@1.13.0"
_DEFAULT_ARGS: tuple[str, ...] = ("-y", _PINNED_MCP_PACKAGE, "--autoConnect", "--redactNetworkHeaders")
_PINNED_SEMVER_RE = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+@\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_DEFAULT_TIMEOUT_S = 45.0
_DEFAULT_MAX_CHARS = 20000
_CLOSE_TIMEOUT_S = 3.0
_DEFAULT_SENSITIVE_HOSTS: tuple[str, ...] = (
    "accounts.google.com",
    "myaccount.google.com",
    "mail.google.com",
    "drive.google.com",
    "passport.bilibili.com",
    "pay.bilibili.com",
    "studio.youtube.com",
    "member.bilibili.com",
    "pay.weixin.qq.com",
    "alipay.com",
    "github.com",
    "gitlab.com",
    "console.aws.amazon.com",
    "signin.aws.amazon.com",
    "console.cloud.google.com",
    "portal.azure.com",
    "dash.cloudflare.com",
    "platform.openai.com",
    "console.anthropic.com",
    "aistudio.google.com",
    "openrouter.ai",
    "login.microsoftonline.com",
    "outlook.live.com",
    "outlook.office.com",
    "icloud.com",
    "appleid.apple.com",
    "id.apple.com",
    "vault.bitwarden.com",
    "my.1password.com",
    "paypal.com",
    "stripe.com",
    "dashboard.stripe.com",
)
_CACHE_SHOT_REL = Path("data") / "_cache" / "chrome-shots"

_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "cookie",
        "set-cookie",
        "set_cookie",
        "authorization",
        "proxy-authorization",
        "authentication",
        "www-authenticate",
        "x-auth-token",
        "x-access-token",
        "x-session-id",
        "x-amz-security-token",
        "x-id-token",
        "x-refresh-token",
        "x-csrf-token",
        "x-xsrf-token",
        "x-api-key",
        "api-key",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "csrf_token",
        "xsrf_token",
        "session_id",
        "session_token",
        "client_secret",
        "secret",
        "password",
        "passwd",
    }
)
_SENSITIVE_HEADER_ALT = "|".join(re.escape(name) for name in sorted(_SENSITIVE_HEADER_NAMES))
_SENSITIVE_HEADER_LINE_RE = re.compile(
    rf"(?im)^(\s*[\"']?(?:{_SENSITIVE_HEADER_ALT})[\"']?\s*[:=]\s*)(.+)$"
)
_SENSITIVE_HEADER_JSON_KV_RE = re.compile(
    rf"(?i)([\"'](?:{_SENSITIVE_HEADER_ALT})[\"']\s*:\s*)"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
_JWT_TOKEN_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_BEARER_TOKEN_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]{12,})")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&#](?:token|access_token|refresh_token|id_token|auth|code|state|code_verifier|code_challenge|"
    r"csrf|csrf_token|xsrf|xsrf_token|key|api_key|apikey|sig|signature|secret|session|session_id|"
    r"password|passwd|pwd|jwt|bearer|x-amz-security-token|x-amz-signature)=)"
    r"([^&#\s\"'<>]+)"
)
_SAVED_SHOT_PATH_RE = re.compile(r"(?im)saved screenshot to\s+([^\r\n]+)")

_CONNECT_HINT_KEYWORDS = (
    "autoconnect",
    "remote-debugging",
    "remote debugging",
    "could not connect",
    "econnrefused",
    "browser",
    "chrome",
    "target closed",
    "protocol error",
)
_EVAL_CONNECT_HINT_KEYWORDS = (
    "autoconnect",
    "remote-debugging",
    "remote debugging",
    "could not connect",
    "econnrefused",
    "target closed",
    "protocol error",
)

_PERMISSION_DENIED_KEYWORDS = (
    "eperm",
    "eacces",
    "operation not permitted",
    "permission denied",
)
_DEVTOOLS_PORT_HINT_KEYWORDS = (
    "devtoolsactiveport",
    "google/chrome",
    "google\\chrome",
    "application support/google/chrome",
    "chrome/user data",
    "chrome\\user data",
    "chrome-profile",
)
_DEVTOOLS_IO_CONTEXT_KEYWORDS = (
    "could not read",
    "could not connect",
    "open '",
    'open "',
    "eperm",
    "eacces",
    "operation not permitted",
)
_DEVTOOLS_PORT_FILE = "DevToolsActivePort"
_MACOS_CHROME_CHANNEL_DIRS: dict[str, str] = {
    "stable": "Chrome",
    "chrome": "Chrome",
    "beta": "Chrome Beta",
    "chrome-beta": "Chrome Beta",
    "dev": "Chrome Dev",
    "chrome-dev": "Chrome Dev",
    "canary": "Chrome Canary",
    "chrome-canary": "Chrome Canary",
}

_PAGE_LINE_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\s*:)\s*(.+?)\s*$")
_URL_TOKEN_RE = re.compile(r"(?:https?://\S+|about:\S+|chrome(?:-[a-z]+)?://\S+)")

_EXTRACT_PAGE_JS = """() => {
  const url = window.location ? String(window.location.href || "") : "";
  const title = document ? String(document.title || "") : "";
  const body = document && document.body ? document.body : document.documentElement;
  const text = body ? String(body.innerText || body.textContent || "") : "";
  const html = document && document.documentElement ? String(document.documentElement.outerHTML || "") : "";
  return { url, title, text, html };
}"""

_READ_PAGE_META_JS = """() => ({
  url: window.location ? String(window.location.href || "") : "",
  title: document ? String(document.title || "") : ""
})"""


@dataclass(frozen=True)
class ChromeSection:
    command: str
    args: tuple[str, ...]
    timeout_s: float
    max_chars: int
    sensitive_hosts: tuple[str, ...]
    trusted_fake_ip_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


_SESSION_LOCK = threading.RLock()
_SESSION: Any = None
_OWNED_PAGES: set[int] = set()
_PAGE_URLS: dict[int, str] = {}
_ATEXIT_REGISTERED = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"配置文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件格式非法 ({path}): {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件顶层必须为 JSON 对象: {path}")
    return raw


_NPX_VALUE_FLAGS = frozenset({
    "--prefix",
    "--cache",
    "--registry",
    "--userconfig",
    "--node-options",
    "--shell",
    "--workspace",
    "-w",
    "-C",
    "--cwd",
    "--tag",
})
_ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir"})


def _extract_pkg_runner_args(command: str, args: Sequence[str]) -> tuple[str, list[str]] | None:
    """剥离外层 env（含 -S / --split-string 展开），识别 npx / bunx / npm exec / pnpm dlx / yarn dlx 等动态拉包启动器。"""
    tokens = [command.strip(), *(str(a).strip() for a in args if str(a).strip())]
    if not tokens or not tokens[0]:
        return None
    idx = 0
    while idx < len(tokens) and Path(tokens[idx]).name.lower() in {"env", "env.exe"}:
        idx += 1
        while idx < len(tokens):
            tok = tokens[idx]
            if tok == "--":
                idx += 1
                break
            if tok in {"-S", "--split-string"}:
                if idx + 1 >= len(tokens):
                    return None
                try:
                    expanded = shlex.split(tokens[idx + 1])
                except ValueError:
                    expanded = tokens[idx + 1].split()
                tokens = [*tokens[:idx], *expanded, *tokens[idx + 2:]]
                continue
            if tok.startswith("--split-string=") or (tok.startswith("-S") and len(tok) > 2):
                raw_s = tok.split("=", 1)[1] if tok.startswith("--split-string=") else tok[2:]
                try:
                    expanded = shlex.split(raw_s)
                except ValueError:
                    expanded = raw_s.split()
                tokens = [*tokens[:idx], *expanded, *tokens[idx + 1:]]
                continue
            if tok in _ENV_VALUE_FLAGS:
                idx += 2
                continue
            if tok.startswith("-"):
                idx += 1
                continue
            if "=" in tok and not tok.startswith("="):
                idx += 1
                continue
            break
    if idx >= len(tokens):
        return None
    runner = Path(tokens[idx]).name.lower()
    rest = tokens[idx + 1:]
    if runner in {"npx", "npx.cmd", "bunx", "bunx.exe"}:
        return runner, rest
    if runner in {"npm", "npm.cmd"}:
        j = 0
        while j < len(rest):
            t = rest[j]
            if t in _NPX_VALUE_FLAGS:
                j += 2
                continue
            if t.startswith("-"):
                j += 1
                continue
            if t in {"exec", "x"}:
                return f"{runner} {t}", rest[j + 1:]
            return None
        return None
    if runner in {"pnpm", "pnpm.cmd", "yarn", "yarn.cmd"}:
        j = 0
        while j < len(rest):
            t = rest[j]
            if t in _NPX_VALUE_FLAGS:
                j += 2
                continue
            if t.startswith("-"):
                j += 1
                continue
            if t == "dlx":
                return f"{runner} dlx", rest[j + 1:]
            return None
        return None
    return None


def _validate_chrome_command_args(
    command: str,
    args: Sequence[str],
    *,
    source_path: Path | None = None,
) -> None:
    """使用 npx / npm exec / pnpm dlx / yarn dlx / bunx 启动 MCP 时，要求包名精确锁定 semver，禁止浮动标签与 -c。"""
    extracted = _extract_pkg_runner_args(command, args)
    if extracted is None:
        return
    runner_label, runner_args = extracted
    src_hint = f"（配置文件：{source_path}）" if source_path is not None else ""

    explicit_pkgs: list[str] = []
    positional_pkg: str | None = None
    i = 0
    after_double_dash = False
    while i < len(runner_args):
        s = runner_args[i]
        if not after_double_dash:
            if s == "--":
                after_double_dash = True
                i += 1
                continue
            if s in {"-p", "--package"}:
                if i + 1 >= len(runner_args) or not runner_args[i + 1].strip():
                    raise ValueError(f"web 配置 `chrome.args` 的 `{s}` 缺少包名参数{src_hint}")
                explicit_pkgs.append(runner_args[i + 1].strip())
                i += 2
                continue
            if s.startswith("--package=") or s.startswith("-p="):
                pkg_val = s.split("=", 1)[1].strip()
                if not pkg_val:
                    raise ValueError(f"web 配置 `chrome.args` 的 `{s}` 缺少包名参数{src_hint}")
                explicit_pkgs.append(pkg_val)
                i += 1
                continue
            if s.startswith("-p") and not s.startswith("--") and len(s) > 2:
                explicit_pkgs.append(s[2:].strip())
                i += 1
                continue
            if (
                s in {"-c", "--call"}
                or s.startswith("--call=")
                or (s.startswith("-") and not s.startswith("--") and "c" in s[1:])
            ):
                raise ValueError(
                    f"web 配置 `chrome.args` 使用 {runner_label} 时禁止 `-c`/`--call` 任意命令执行{src_hint}"
                )
            if s in _NPX_VALUE_FLAGS:
                i += 2
                continue
            if s.startswith("-"):
                i += 1
                continue
        if positional_pkg is None:
            positional_pkg = s
        break

    pkgs_to_check = explicit_pkgs if explicit_pkgs else ([positional_pkg] if positional_pkg else [])
    if not pkgs_to_check:
        raise ValueError(
            f"web 配置 `chrome.args` 使用 {runner_label} 时未识别到可校验的锁版本包名{src_hint}："
            f"请精确固定版本号（例如 {_PINNED_MCP_PACKAGE!r}）"
        )
    for pkg_spec in pkgs_to_check:
        if not _PINNED_SEMVER_RE.match(pkg_spec):
            raise ValueError(
                f"web 配置 `chrome.args` 使用 {runner_label} 时禁止未锁定版本或浮动标签 ({pkg_spec!r}){src_hint}："
                f"请精确固定版本号（例如 {_PINNED_MCP_PACKAGE!r}），防止高权限进程树内供应链自动拉取未审计代码"
            )


def _load_trusted_fake_ip_ranges(
    root: Path | None = None,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """仅读取 `trusted_fake_ip_ranges`，避免 `chrome.args` 等其他字段校验失败时静默丢弃代理 FakeIP 白名单。"""
    base = Path(root) if root is not None else _repo_root()
    agent_dir = base / "config" / "agent"
    local_path = agent_dir / "web.local.json"
    base_path = agent_dir / "web.json"
    primary_data = _read_json_dict(local_path) if local_path.exists() else {}
    fallback_data = _read_json_dict(base_path) if base_path.exists() else {}
    merged_ranges_raw = (
        primary_data["trusted_fake_ip_ranges"]
        if "trusted_fake_ip_ranges" in primary_data
        else fallback_data.get("trusted_fake_ip_ranges", [])
    )
    return _parse_trusted_ranges(merged_ranges_raw)


def load_chrome_section(root: Path | None = None) -> ChromeSection:
    """读取 `config/agent/web.local.json`（优先）与 `web.json` 的 `chrome` 段。

    既有环境已有 `web.local.json` 但尚未写 `chrome` 键时，回退 `web.json` 的 `chrome` 段或默认值，
    不强制要求改动用户本地 `web.local.json`。
    """
    base = Path(root) if root is not None else _repo_root()
    agent_dir = base / "config" / "agent"
    local_path = agent_dir / "web.local.json"
    base_path = agent_dir / "web.json"

    primary_data: dict[str, Any] = {}
    fallback_data: dict[str, Any] = {}
    if local_path.exists():
        primary_data = _read_json_dict(local_path)
    if base_path.exists():
        fallback_data = _read_json_dict(base_path)
    if not local_path.exists() and not base_path.exists():
        raise ValueError(f"缺少 web 配置文件: {base_path}")

    merged_ranges_raw = (
        primary_data["trusted_fake_ip_ranges"]
        if "trusted_fake_ip_ranges" in primary_data
        else fallback_data.get("trusted_fake_ip_ranges", [])
    )
    trusted_ranges = _parse_trusted_ranges(merged_ranges_raw)

    has_local_chrome = "chrome" in primary_data
    chrome_src_path = local_path if has_local_chrome else base_path
    raw_chrome = primary_data.get("chrome") if has_local_chrome else fallback_data.get("chrome")
    if raw_chrome is None:
        raw_chrome = {}
    if not isinstance(raw_chrome, dict):
        raise ValueError(f"web 配置 `chrome` 段必须为 JSON 对象（{chrome_src_path}）")

    command = str(raw_chrome.get("command") or _DEFAULT_COMMAND).strip()
    if not command:
        raise ValueError(f"web 配置 `chrome.command` 不能为空（{chrome_src_path}）")

    raw_args = raw_chrome.get("args", list(_DEFAULT_ARGS))
    if not isinstance(raw_args, list) or not raw_args or not all(isinstance(x, str) and x.strip() for x in raw_args):
        raise ValueError(f"web 配置 `chrome.args` 必须为非空字符串数组（{chrome_src_path}）")
    args = tuple(raw_args)
    _validate_chrome_command_args(command, args, source_path=chrome_src_path)

    raw_timeout = raw_chrome.get("timeout_s", _DEFAULT_TIMEOUT_S)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)) or raw_timeout <= 0:
        raise ValueError(f"web 配置 `chrome.timeout_s` 必须为正数（{chrome_src_path}）")

    raw_max_chars = raw_chrome.get("max_chars", _DEFAULT_MAX_CHARS)
    if isinstance(raw_max_chars, bool) or not isinstance(raw_max_chars, int) or raw_max_chars <= 0:
        raise ValueError(f"web 配置 `chrome.max_chars` 必须为正整数（{chrome_src_path}）")

    raw_hosts = raw_chrome.get("sensitive_hosts")
    replace_defaults = bool(raw_chrome.get("replace_default_sensitive_hosts", False))
    if raw_hosts is None:
        sensitive_hosts = _DEFAULT_SENSITIVE_HOSTS
    else:
        if not isinstance(raw_hosts, list) or not all(isinstance(x, str) for x in raw_hosts):
            raise ValueError(f"web 配置 `chrome.sensitive_hosts` 必须为字符串数组（{chrome_src_path}）")
        cleaned_custom = [h.strip().lower().lstrip(".") for h in raw_hosts if h.strip()]
        if replace_defaults:
            sensitive_hosts = tuple(dict.fromkeys(cleaned_custom))
        else:
            # 默认与内置高敏名单取并集，防止旧版 web.local.json 仅保留 8 个旧域名而静默丢弃新增高敏站
            sensitive_hosts = tuple(dict.fromkeys((*_DEFAULT_SENSITIVE_HOSTS, *cleaned_custom)))

    return ChromeSection(
        command=command,
        args=args,
        timeout_s=float(raw_timeout),
        max_chars=raw_max_chars,
        sensitive_hosts=sensitive_hosts,
        trusted_fake_ip_ranges=trusted_ranges,
    )


def coerce_page_id(raw: Any) -> int | None:
    """将输入转为合法 `page_id` 整数；拒绝 bool 与非整数。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def is_owned_page(page_id: int | None) -> bool:
    """该 `page_id` 是否由当前会话 AVA `chrome open` 主动创建。"""
    if page_id is None:
        return False
    with _SESSION_LOCK:
        return page_id in _OWNED_PAGES


def known_page_url(page_id: int | None) -> str:
    """返回最近观测到的该 `page_id` URL（未记录时为空串）。"""
    if page_id is None:
        return ""
    with _SESSION_LOCK:
        return _PAGE_URLS.get(page_id, "")


def page_origin(url_or_host: str) -> str:
    """提取 URL 的规范 origin（`scheme://host[:port]`）；含反斜杠/userinfo 或无法解析时返回空串。"""
    raw = str(url_or_host or "").strip()
    if not raw or "://" not in raw or "\\" in raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if scheme not in {"http", "https"} or not host:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    formatted = f"[{host}]" if ":" in host else host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        return f"{scheme}://{formatted}:{port}"
    return f"{scheme}://{formatted}"


def _scrub_url(url: str) -> str:
    """对返回给模型的 URL 做敏感 query/fragment 参数剥离 + 受限路径词脱敏。"""
    raw = str(url or "")
    if not raw:
        return ""
    cleaned = _SENSITIVE_QUERY_RE.sub(r"\1[已剥离敏感参数]", raw)
    cleaned = _JWT_TOKEN_RE.sub("[已剥离敏感令牌]", cleaned)
    return _scrub(cleaned)


def is_sensitive_host(url_or_host: str, sensitive_hosts: Sequence[str] | None = None) -> bool:
    """判断 URL 或主机名是否命中高敏站点名单（精确或子域后缀匹配；先将反斜杠归一防 WHATWG 解析差异）。"""
    raw = str(url_or_host or "").strip().replace("\\", "/")
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    hosts = sensitive_hosts if sensitive_hosts is not None else _DEFAULT_SENSITIVE_HOSTS
    for item in hosts:
        target = str(item).strip().lower().lstrip(".")
        if not target:
            continue
        if host == target or host.endswith("." + target):
            return True
    return False


def coerce_bool_flag(raw: Any, default: bool = False) -> bool:
    """将输入严格归一为布尔：拒绝把字符串 ``"false"`` / ``"0"`` 误判为 True。"""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        norm = raw.strip().lower()
        if norm in {"true", "1", "yes"}:
            return True
        if norm in {"", "false", "0", "no", "null", "none"}:
            return False
    return bool(raw)


def _coerce_int_param(
    raw: Any,
    field: str,
    *,
    min_value: int | None = None,
) -> tuple[int | None, str | None]:
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, f"chrome {field} 必须为整数"
    if isinstance(raw, int):
        val = raw
    elif isinstance(raw, float) and raw.is_integer():
        val = int(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            val = int(raw.strip())
        except ValueError:
            return None, f"chrome {field} 必须为整数"
    else:
        return None, f"chrome {field} 必须为整数"
    if min_value is not None and val < min_value:
        return None, f"chrome {field} 必须 >= {min_value}"
    return val, None


def _check_target_url_allowed(url: str, *, root: Path | None) -> tuple[str | None, bool]:
    trusted_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    try:
        trusted_ranges = _load_trusted_fake_ip_ranges(root)
    except Exception:
        pass
    try:
        _guard_url(url, trusted_ranges=trusted_ranges)
        assert_egress_boundary(url, {"url": _normalized_for_assert(url)})
    except PermissionError as exc:
        return str(exc), True
    except Exception as exc:
        return str(exc), False
    return None, False


def _precheck_with_exc(
    action: str,
    args: dict[str, Any],
    *,
    root: Path | None = None,
) -> tuple[str | None, bool]:
    """前置静态护栏与参数检查；返回 `(拒绝文案, 是否 PermissionError)`。"""
    act = str(action or "").strip()
    if act not in CHROME_ACTIONS:
        return f"chrome 不支持的 action={act!r}，可选：{', '.join(CHROME_ACTIONS)}", False

    for field_name, min_val in (("timeout_ms", 1), ("page_size", 1), ("page_idx", 0), ("reqid", None)):
        if args.get(field_name) is not None:
            _, int_err = _coerce_int_param(args.get(field_name), field_name, min_value=min_val)
            if int_err:
                return int_err, False

    if args.get("resource_types") is not None:
        rtypes = args.get("resource_types")
        if not isinstance(rtypes, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in rtypes):
            return "chrome resource_types 必须为非空字符串数组", False

    eargs = args.get("eval_args") if args.get("eval_args") is not None else args.get("args")
    if eargs is not None:
        if not isinstance(eargs, (list, tuple)) or not all(isinstance(x, str) for x in eargs):
            return "chrome args/eval_args 必须为字符串数组", False

    if act == "list_pages":
        return None, False

    if act == "open":
        url = str(args.get("url") or "").strip()
        if not url:
            return "chrome open 必须提供 url", False
        return _check_target_url_allowed(url, root=root)

    pid = coerce_page_id(args.get("page_id"))
    if pid is None:
        return f"chrome {act} 必须提供有效的整数 page_id", False

    if act == "close":
        if not is_owned_page(pid):
            return (
                f"拒绝关闭非 AVA 创建的标签页 {pid}："
                "chrome close 仅允许关闭当前会话由 open 打开的后台页",
                True,
            )
        return None, False

    if not is_owned_page(pid) and not coerce_bool_flag(args.get("allow_existing"), False):
        return (
            f"标签页 {pid} 不是本会话由 chrome open 打开的页面。"
            "若确需协助处理用户已打开页面，请先经用户确认后显式传 allow_existing=true",
            True,
        )

    if act == "navigate":
        nav_type = str(args.get("nav_type") or "url").strip()
        if nav_type not in {"url", "back", "forward", "reload"}:
            return f"chrome navigate 的 nav_type={nav_type!r} 非法，可选 url / back / forward / reload", False
        if nav_type == "url":
            nav_url = str(args.get("url") or "").strip()
            if not nav_url:
                return "chrome navigate (nav_type=url) 必须提供 url", False
            return _check_target_url_allowed(nav_url, root=root)

    if act in {"click", "fill"} and not str(args.get("uid") or "").strip():
        return f"chrome {act} 必须提供 snapshot 返回的元素 uid", False

    if act == "fill" and args.get("value") is None:
        return "chrome fill 必须提供 value 字符串", False

    if act == "wait_for":
        raw_text = args.get("text")
        texts = [raw_text] if isinstance(raw_text, str) else raw_text
        if not isinstance(texts, list) or not any(isinstance(t, str) and t.strip() for t in texts):
            return "chrome wait_for 必须提供非空 text（字符串或字符串数组）", False

    if act == "evaluate" and not str(args.get("function") or "").strip():
        return "chrome evaluate 必须提供 JavaScript 函数源码 function", False

    if act == "screenshot" and coerce_bool_flag(args.get("save_evidence"), False):
        if not str(args.get("reason") or "").strip():
            return "chrome screenshot 传 save_evidence=true 时必须填写 reason（说明物证用途）", False
        if coerce_bool_flag(args.get("full_page"), False):
            return (
                "chrome screenshot 传 save_evidence=true 入库时禁止 full_page=true（整页截图进 1080p 成片字会缩糊）："
                "请先调 snapshot 找到目标段落/表格元素的 uid 做局部截图",
                False,
            )
        if not str(args.get("uid") or "").strip():
            return (
                "chrome screenshot 传 save_evidence=true 入库时必须提供目标段落/表格元素的 uid"
                "（先调 snapshot 取对应节点的 uid，禁止不传 uid 截整个视口）",
                False,
            )
        if not str(args.get("expect_text") or "").strip():
            return (
                "chrome screenshot 传 save_evidence=true 入库时必须提供 expect_text（填入本段要印证的关键原文或数据）",
                False,
            )

    return None, False


def precheck_chrome_access(action: str, args: dict[str, Any], *, root: Path | None = None) -> str | None:
    """前置静态护栏与参数检查；通过返回 None，不合法返回拒绝文案。

    由 `session.py::review_tool_call`（弹卡前拦截必然被拒的请求）与 `chrome_action` 共用。
    """
    err, _ = _precheck_with_exc(action, args, root=root)
    return err


def _has_autoconnect_flag(args: Sequence[str]) -> bool:
    """判断配置参数是否启用 `--autoConnect`（兼容 kebab-case、空格布尔值与显式关闭标记，后者覆盖前者）。"""
    arg_list = [str(a).strip() for a in args]
    enabled = False
    i = 0
    while i < len(arg_list):
        token = arg_list[i]
        i += 1
        if not token:
            continue
        lowered = token.lower()
        if lowered in {"--autoconnect", "--auto-connect"}:
            enabled = True
            if i < len(arg_list):
                nxt = arg_list[i].lower()
                if nxt in {"1", "true", "yes"}:
                    enabled = True
                    i += 1
                elif nxt in {"0", "false", "no"}:
                    enabled = False
                    i += 1
        elif lowered in {"--no-autoconnect", "--no-auto-connect"}:
            enabled = False
        elif lowered.startswith(("--autoconnect=", "--auto-connect=")):
            val = lowered.split("=", 1)[1].strip()
            if val in {"1", "true", "yes", ""}:
                enabled = True
            elif val in {"0", "false", "no"}:
                enabled = False
    return enabled


def _extract_cli_option(args: Sequence[str], names: Sequence[str]) -> str | None:
    """提取 yargs 风格参数值（last-wins，支持 `--flag=value` 与 `--flag value`，不吞后续 flag）。"""
    lowered_names = tuple(n.lower() for n in names)
    arg_list = [str(a).strip() for a in args]
    found: str | None = None
    i = 0
    while i < len(arg_list):
        token = arg_list[i]
        i += 1
        if not token:
            continue
        low = token.lower()
        for name in lowered_names:
            if low == name:
                if i < len(arg_list):
                    nxt = arg_list[i]
                    if nxt and not nxt.startswith("-"):
                        found = nxt
                        i += 1
                    else:
                        found = None
                else:
                    found = None
                break
            prefix = f"{name}="
            if low.startswith(prefix):
                val = token[len(prefix) :].strip()
                found = val or None
                break
    return found


def _resolve_autoconnect_port_path(
    cfg: ChromeSection,
    *,
    platform: str | None = None,
    home: Path | None = None,
) -> Path | None:
    """解析 `--autoConnect` 模式将读取的 `DevToolsActivePort` 路径；非直连端口文件模式返回 None。"""
    if not _has_autoconnect_flag(cfg.args):
        return None
    if _extract_cli_option(cfg.args, ("--wsEndpoint", "--ws-endpoint", "-w")):
        return None
    if _extract_cli_option(cfg.args, ("--browserUrl", "--browser-url", "-u")):
        return None

    user_data_dir = _extract_cli_option(cfg.args, ("--userDataDir", "--user-data-dir"))
    if user_data_dir:
        return Path(user_data_dir).expanduser() / _DEVTOOLS_PORT_FILE

    plat = platform if platform is not None else sys.platform
    if plat != "darwin":
        return None

    channel = (_extract_cli_option(cfg.args, ("--channel",)) or "stable").strip().lower()
    dir_name = _MACOS_CHROME_CHANNEL_DIRS.get(channel)
    if not dir_name:
        return None

    base_home = home if home is not None else Path.home()
    return base_home / "Library" / "Application Support" / "Google" / dir_name / _DEVTOOLS_PORT_FILE


def _is_devtools_permission_error(detail: str) -> bool:
    lowered = detail.lower()
    return (
        any(kw in lowered for kw in _PERMISSION_DENIED_KEYWORDS)
        and any(kw in lowered for kw in _DEVTOOLS_PORT_HINT_KEYWORDS)
        and (
            "devtoolsactiveport" in lowered
            or any(kw in lowered for kw in _DEVTOOLS_IO_CONTEXT_KEYWORDS)
        )
    )


def _permission_error_message(
    detail: str,
    *,
    platform: str | None = None,
    port_path: Path | None = None,
) -> str:
    plat = platform if platform is not None else sys.platform
    path_hint = f"（{port_path}）" if port_path is not None else ""
    if plat == "darwin":
        return (
            f"chrome 无法读取主力 Chrome 的 DevToolsActivePort{path_hint}（权限被 macOS TCC 拦截）：{detail}。"
            "这不是未勾选 chrome://inspect/#remote-debugging 的问题，而是当前进程树所属 App 无权读取 Google Chrome 数据目录"
            "（TCC 服务 kTCCServiceSystemPolicyAppDataDetailed）。"
            "请按最小权限原则放行并重启 AVA："
            "打开「系统设置 → 隐私与安全性 → 文件与文件夹」，展开「AVA」并仅勾选「Google Chrome」"
            "（不建议授予「完全磁盘访问权限」，以免 AVA 子进程树获得整机隐私目录读取权限）。"
            "注意：若启动的是 dist/mac-arm64/ 下未重签的 ad-hoc AVA.app，每次重新打包后 CDHash 会变化，系统设置中旧条目可能表面仍开启但已被 TCC 静默拒绝，"
            "需先选中旧条目点「-」移除再点「+」重新添加后完全重启；如希望一次授权长期有效，请运行 `cd desktop && npm run release-install` 安装稳定签名版到 /Applications/AVA.app。"
            "（若仅抓公开网页，请用 web_fetch / crawl / browser）"
        )
    return (
        f"chrome 无法读取主力 Chrome 的 DevToolsActivePort{path_hint}（操作系统或沙盒权限被拒）：{detail}。"
        "这不是未开启远程调试的问题，请检查当前宿主进程及子进程对 Chrome 用户数据目录的读取权限"
        "（若仅抓公开网页，请用 web_fetch / crawl / browser）"
    )


def _connect_error_message(detail: str, *, tool_name: str | None = None) -> str:
    if _is_devtools_permission_error(detail):
        return _permission_error_message(detail)
    lowered = detail.lower()
    hint_keywords = (
        _EVAL_CONNECT_HINT_KEYWORDS if tool_name == "evaluate_script" else _CONNECT_HINT_KEYWORDS
    )
    if any(kw in lowered for kw in hint_keywords):
        return (
            f"chrome 连接主力 Chrome 失败：{detail}。"
            "请确认 Google Chrome 144+ 已运行且在 chrome://inspect/#remote-debugging 勾选启用远程调试"
            "（若仅抓公开网页，请用 web_fetch / crawl / browser）"
        )
    return f"chrome 操作失败：{detail}"


class _McpStdioClient:
    """零三方依赖的 MCP stdio (newline-delimited JSON-RPC 2.0) 同步客户端。"""

    def __init__(
        self,
        command: str,
        args: Sequence[str],
        *,
        env: dict[str, str],
        init_timeout_s: float = 45.0,
    ) -> None:
        try:
            self._proc = subprocess.Popen(
                [command, *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            raise ValueError(f"无法启动 Chrome MCP 进程 ({command}): {exc}") from exc

        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._next_id = 0
        self._closed = False

        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

        try:
            self._initialize(timeout_s=init_timeout_s)
        except Exception:
            self.close()
            raise

    @property
    def pid(self) -> int | None:
        return getattr(self._proc, "pid", None)

    def is_alive(self) -> bool:
        return not self._closed and self._proc.poll() is None

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                # 服务端发起的 JSON-RPC 请求/通知（带 method 且无 result/error）绝不路由到 _pending
                if "method" in msg and "result" not in msg and "error" not in msg:
                    if "id" in msg and msg.get("id") is not None:
                        try:
                            self._send_json(
                                {
                                    "jsonrpc": "2.0",
                                    "id": msg["id"],
                                    "error": {"code": -32601, "message": "Method not found"},
                                }
                            )
                        except Exception:
                            pass
                    continue
                if "id" not in msg:
                    continue
                req_id = msg.get("id")
                if not isinstance(req_id, int) or isinstance(req_id, bool):
                    continue
                with self._pending_lock:
                    q = self._pending.get(req_id)
                if q is not None:
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        pass
        except Exception:
            pass
        finally:
            self._wake_all_with_eof()

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                clean = line.rstrip("\r\n")
                if clean:
                    self._stderr_tail.append(clean)
        except Exception:
            pass

    def _wake_all_with_eof(self) -> None:
        err_msg = self._format_stderr_eof()
        with self._pending_lock:
            queues = list(self._pending.values())
        for q in queues:
            try:
                q.put_nowait({"error": {"code": -32000, "message": err_msg}})
            except queue.Full:
                pass

    def _format_stderr_eof(self) -> str:
        tail = " | ".join(self._stderr_tail)
        code = self._proc.poll()
        return f"MCP 进程已退出 (code={code}): {tail}" if tail else f"MCP 进程已退出 (code={code})"

    def _send_json(self, payload: dict[str, Any]) -> None:
        if not self.is_alive() or self._proc.stdin is None:
            raise ValueError(_connect_error_message(self._format_stderr_eof()))
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._write_lock:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()

    def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send_json(payload)

    def _request(self, method: str, params: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        if not self.is_alive() or self._proc.stdin is None:
            raise ValueError(_connect_error_message(self._format_stderr_eof()))
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._next_id += 1
            req_id = self._next_id
            self._pending[req_id] = q
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            with self._write_lock:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise ValueError(_connect_error_message(str(exc))) from exc

        try:
            resp = q.get(timeout=timeout_s)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            tail = " | ".join(self._stderr_tail)
            msg = f"等待 Chrome MCP 响应超时 ({timeout_s:g}s)" + (f"：{tail}" if tail else "")
            raise ValueError(_connect_error_message(msg)) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            err_text = err.get("message") if isinstance(err, dict) else str(err)
            raise ValueError(_connect_error_message(str(err_text)))
        result = resp.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def _initialize(self, *, timeout_s: float) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ava-chrome", "version": "1.0"},
            },
            timeout_s=timeout_s,
        )
        self._send_notification("notifications/initialized")

    def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout_s=timeout_s)
        if result.get("isError"):
            err_text = _extract_mcp_text(result) or f"MCP 工具 {name} 返回错误"
            raise ValueError(_connect_error_message(err_text, tool_name=name))
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:
            pass
        if self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=1.0)
                except Exception:
                    pass
        self._wake_all_with_eof()


def _check_autoconnect_port_permission(
    cfg: ChromeSection,
    *,
    platform: str | None = None,
    home: Path | None = None,
) -> None:
    """`--autoConnect` 启动前做 1 字节只读探针；被 TCC/OS 拦下时立即快速失败，不拉起空转 npx 子进程。"""
    port_path = _resolve_autoconnect_port_path(cfg, platform=platform, home=home)
    if port_path is None:
        return
    try:
        with port_path.open("rb") as fh:
            # 仅探测内核读权限，绝不保留、记录或暴露文件内容（含敏感 CDP websocket token）
            fh.read(1)
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in (errno.EPERM, errno.EACCES):
            detail = exc.strerror or str(exc)
            raise ValueError(
                _permission_error_message(
                    f"{detail} (errno={exc.errno})",
                    platform=platform,
                    port_path=port_path,
                )
            ) from exc


def _default_launch_fn(cfg: ChromeSection) -> _McpStdioClient:
    _check_autoconnect_port_permission(cfg)
    return _McpStdioClient(
        cfg.command,
        cfg.args,
        env=browser_env(),
        init_timeout_s=cfg.timeout_s,
    )


def _session_alive(sess: Any) -> bool:
    if sess is None:
        return False
    if hasattr(sess, "is_alive"):
        try:
            return bool(sess.is_alive())
        except Exception:
            return False
    return True


def _reset_session_for_testing() -> None:
    """关闭所有 AVA 自建后台 Tab 并终止 MCP 子进程（单测复位与 `atexit` 共用）。"""
    global _SESSION
    with _SESSION_LOCK:
        sess = _SESSION
        owned = sorted(_OWNED_PAGES)
        _SESSION = None
        _OWNED_PAGES.clear()
        _PAGE_URLS.clear()

    if sess is not None:
        if _session_alive(sess):
            for pid in owned:
                try:
                    sess.call_tool("close_page", {"pageId": pid}, timeout_s=_CLOSE_TIMEOUT_S)
                except Exception:
                    pass
        try:
            sess.close()
        except Exception:
            pass


def _get_or_start_session(
    cfg: ChromeSection,
    *,
    launch_fn: Callable[[ChromeSection], Any] | None = None,
) -> Any:
    global _SESSION, _ATEXIT_REGISTERED
    with _SESSION_LOCK:
        if _session_alive(_SESSION):
            return _SESSION
        if _SESSION is not None:
            try:
                _SESSION.close()
            except Exception:
                pass
            _SESSION = None
            _OWNED_PAGES.clear()
            _PAGE_URLS.clear()

        fn = _default_launch_fn if launch_fn is None else launch_fn
        sess = fn(cfg)
        _SESSION = sess
        if not _ATEXIT_REGISTERED:
            atexit.register(_reset_session_for_testing)
            _ATEXIT_REGISTERED = True
        return sess


def _extract_mcp_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if parts:
            return "\n".join(p for p in parts if p)
    if isinstance(result.get("text"), str):
        return result["text"]
    return ""


def _extract_mcp_image_bytes(result: dict[str, Any]) -> bytes | None:
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                data = item.get("data")
                if isinstance(data, str) and data:
                    try:
                        return base64.b64decode(data)
                    except Exception:
                        continue
    return None


def _parse_json_candidate(raw_text: str) -> Any:
    stripped = raw_text.strip()
    if not stripped:
        return None
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else stripped
    try:
        return json.loads(candidate)
    except Exception:
        pass
    dec = json.JSONDecoder()
    for idx, ch in enumerate(candidate):
        if ch in "{[":
            try:
                val, _ = dec.raw_decode(candidate[idx:])
                return val
            except Exception:
                continue
    return None


def _parse_evaluate_payload(result: dict[str, Any]) -> Any:
    if "value" in result and isinstance(result.get("value"), (dict, list, str, int, float, bool)):
        return result["value"]
    text = _extract_mcp_text(result)
    parsed = _parse_json_candidate(text)
    if parsed is not None:
        return parsed
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)):
        if isinstance(structured, dict) and "result" in structured and "url" not in structured:
            return structured["result"]
        return structured
    return text


def _extract_url_and_title(rest_clean: str) -> tuple[str, str]:
    url_m = _URL_TOKEN_RE.search(rest_clean)
    if url_m:
        url = url_m.group(0).rstrip(".,")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        wrapped = re.search(r"\(\s*" + re.escape(url) + r"\s*\)", rest_clean)
        if wrapped:
            title = (rest_clean[: wrapped.start()] + rest_clean[wrapped.end() :]).strip(" -|[]")
        else:
            title = rest_clean.replace(url, "", 1).strip(" -|[]")
        return url, title
    url = rest_clean.split()[0] if rest_clean else ""
    title = rest_clean.replace(url, "", 1).strip(" -|[]") if url else ""
    return url, title


def _parse_pages(result: dict[str, Any], *, update_cache: bool = True) -> list[dict[str, Any]]:
    """解析 `list_pages` 返回的页面列表，并按需刷新 `_PAGE_URLS`。"""
    pages: list[dict[str, Any]] = []
    structured = result.get("pages") or result.get("structuredContent")
    if isinstance(structured, dict):
        structured = structured.get("pages") or structured.get("openedPages")
    if isinstance(structured, list):
        for item in structured:
            if not isinstance(item, dict):
                continue
            pid = coerce_page_id(item.get("pageId") if "pageId" in item else item.get("id"))
            if pid is None:
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            pages.append(
                {
                    "page_id": pid,
                    "url": url,
                    "title": title,
                    "selected": bool(item.get("selected", False)),
                }
            )
    if not pages:
        text = _extract_mcp_text(result)
        for line in text.splitlines():
            m = _PAGE_LINE_RE.match(line)
            if not m:
                continue
            pid = int(m.group(1) or m.group(2))
            rest = m.group(3)
            selected = "[selected]" in rest.lower()
            rest_clean = re.sub(r"\[selected\]", "", rest, flags=re.IGNORECASE).strip()
            url, title = _extract_url_and_title(rest_clean)
            pages.append({"page_id": pid, "url": url, "title": title, "selected": selected})

    if update_cache:
        with _SESSION_LOCK:
            for p in pages:
                if p["url"]:
                    _PAGE_URLS[p["page_id"]] = p["url"]
    return pages


def _cap_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + f"\n…（已截断，总长 {len(text)} 字符）", True


def _strip_sensitive_headers_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).strip().lower() in _SENSITIVE_HEADER_NAMES:
                cleaned[k] = "[已剥离敏感头]"
            else:
                cleaned[k] = _strip_sensitive_headers_obj(v)
        return cleaned
    if isinstance(obj, list):
        return [_strip_sensitive_headers_obj(x) for x in obj]
    return obj


def _strip_sensitive_headers_text(raw: str) -> str:
    """对 Network 详情做结构化 + 请求头/JSON 凭据键 + JWT/Bearer + URL 敏感 query 四层剥离。"""
    stripped = raw.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, (dict, list)):
                redacted = _strip_sensitive_headers_obj(parsed)
                raw = json.dumps(redacted, ensure_ascii=False, indent=2)
        except Exception:
            pass
    raw = _SENSITIVE_HEADER_JSON_KV_RE.sub(r'\1"[已剥离敏感头]"', raw)
    raw = _SENSITIVE_HEADER_LINE_RE.sub(r"\1[已剥离敏感头]", raw)
    raw = _BEARER_TOKEN_RE.sub(r"\1[已剥离敏感令牌]", raw)
    raw = _JWT_TOKEN_RE.sub("[已剥离敏感令牌]", raw)
    raw = _SENSITIVE_QUERY_RE.sub(r"\1[已剥离敏感参数]", raw)
    return raw


def _sanitize_text(text: str, *, root: Path | None, max_chars: int) -> tuple[str, bool]:
    scrubbed = scrub_secrets(_scrub(text), root)
    return _cap_text(scrubbed, max_chars)


def _select_background(sess: Any, page_id: int, *, timeout_s: float) -> None:
    """切换工具操作上下文到 `page_id`；硬编码 `bringToFront=False` 不抢焦点。"""
    sess.call_tool("select_page", {"pageId": page_id, "bringToFront": False}, timeout_s=timeout_s)


def _read_current_page_meta(sess: Any, page_id: int, *, timeout_s: float) -> tuple[str, str]:
    try:
        res = sess.call_tool(
            "evaluate_script",
            {"pageId": page_id, "function": _READ_PAGE_META_JS, "waitForStableDom": False},
            timeout_s=timeout_s,
        )
        payload = _parse_evaluate_payload(res)
        if isinstance(payload, dict):
            url = str(payload.get("url") or "").strip()
            title = str(payload.get("title") or "").strip()
            if url:
                with _SESSION_LOCK:
                    _PAGE_URLS[page_id] = url
                return url, title
    except Exception:
        pass
    pages = _parse_pages(sess.call_tool("list_pages", {}, timeout_s=timeout_s))
    for p in pages:
        if p["page_id"] == page_id:
            return p["url"], p["title"]
    return known_page_url(page_id), ""


def _norm_url_key(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}" if netloc else raw


def _url_host(url: str) -> str:
    try:
        raw = str(url or "").strip().replace("\\", "/")
        return (urlparse(raw).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _resolve_opened_page_id(
    before_ids: set[int],
    open_res: dict[str, Any],
    after_pages: list[dict[str, Any]],
    target_url: str,
) -> int:
    new_pages = [p for p in after_pages if p["page_id"] not in before_ids]
    new_id_set = {p["page_id"] for p in new_pages}
    target_key = _norm_url_key(target_url)
    target_host = _url_host(target_url)

    # 1) 在新建差集中优先按精确/归一 URL 匹配，防并发手开用户页误登记为自有页
    for p in reversed(new_pages):
        if p["url"] == target_url or (target_key and _norm_url_key(p["url"]) == target_key):
            return p["page_id"]

    # 2) 解析 new_page 自身响应里的页面信息（重定向时 URL 可能已变，但 new_page 可定位本次新建页）
    open_pages = _parse_pages(open_res, update_cache=False)
    open_new_pages = [p for p in open_pages if p["page_id"] in new_id_set]
    for p in reversed(open_new_pages):
        if p["url"] == target_url or (target_key and _norm_url_key(p["url"]) == target_key):
            return p["page_id"]
    selected_new = [p for p in open_new_pages if p.get("selected")]
    if len(selected_new) == 1:
        return selected_new[0]["page_id"]
    if len({p["page_id"] for p in open_new_pages}) == 1:
        return open_new_pages[0]["page_id"]

    text = _extract_mcp_text(open_res)
    parsed = _parse_json_candidate(text)
    structured_src = open_res.get("structuredContent") if isinstance(open_res.get("structuredContent"), dict) else None
    for cand in (parsed, structured_src):
        if isinstance(cand, dict):
            pid = coerce_page_id(cand.get("pageId") if "pageId" in cand else cand.get("id"))
            if pid is not None and pid in new_id_set:
                return pid

    # 3) 次优先：同主机名或父/子域关系匹配（兼容 example.org → www.example.org 重定向）
    if target_host:
        for p in reversed(new_pages):
            if _url_host(p["url"]) == target_host:
                return p["page_id"]
        for p in reversed(new_pages):
            cand_host = _url_host(p["url"])
            if cand_host and (cand_host.endswith("." + target_host) or target_host.endswith("." + cand_host)):
                return p["page_id"]

    # 4) 仅有一个新增页（例如发生跨域重定向）：只要 open_res 未指向既有旧页即认领
    if len(new_pages) == 1:
        open_existing_ids = {p["page_id"] for p in open_pages if p["page_id"] in before_ids}
        if not (open_existing_ids and not open_new_pages and target_url not in text):
            return new_pages[0]["page_id"]

    # 5) 多个新增页且 URL / 响应 / 主机均未命中时，绝不盲目认领最后一个新增页（防把用户并发手开页误登记为自有页）
    if len(new_pages) > 1:
        raise ValueError(
            "chrome open 期间检测到多个新增标签页且无法唯一识别归属（可能与用户手动新开页并发），"
            "为防误操作用户标签页已中止认领"
        )
    for p in open_pages:
        if p["page_id"] not in before_ids:
            return p["page_id"]
    if isinstance(parsed, dict):
        pid = coerce_page_id(parsed.get("pageId") if "pageId" in parsed else parsed.get("id"))
        if pid is not None and pid not in before_ids:
            return pid
    for p in reversed(after_pages):
        if p["page_id"] not in before_ids and p["url"] == target_url:
            return p["page_id"]
    raise ValueError("chrome open 成功但未能识别新建标签页 ID")


def _extract_dom_snapshot(
    sess: Any,
    page_id: int,
    *,
    timeout_s: float,
    wait_for_stable_dom: bool = False,
) -> tuple[str, str, str, str]:
    res = sess.call_tool(
        "evaluate_script",
        {
            "pageId": page_id,
            "function": _EXTRACT_PAGE_JS,
            "waitForStableDom": bool(wait_for_stable_dom),
        },
        timeout_s=timeout_s,
    )
    payload = _parse_evaluate_payload(res)
    if isinstance(payload, dict):
        return (
            str(payload.get("url") or "").strip(),
            str(payload.get("title") or "").strip(),
            str(payload.get("text") or ""),
            str(payload.get("html") or ""),
        )
    return known_page_url(page_id), "", str(payload or ""), ""


def _normalize_extracted_text(raw_text: str, raw_html: str) -> str:
    text = raw_text.strip()
    if not text and raw_html:
        ext = _TextExtractor()
        ext.feed(raw_html)
        text = ext.text()
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _guard_url_and_cleanup_on_fail(
    url: str,
    *,
    trusted_ranges: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    sess: Any | None = None,
    close_pid_on_fail: int | None = None,
) -> None:
    try:
        _guard_url(url, trusted_ranges=trusted_ranges)
    except Exception:
        if sess is not None and close_pid_on_fail is not None:
            try:
                sess.call_tool("close_page", {"pageId": close_pid_on_fail}, timeout_s=_CLOSE_TIMEOUT_S)
            except Exception:
                pass
            with _SESSION_LOCK:
                _OWNED_PAGES.discard(close_pid_on_fail)
                _PAGE_URLS.pop(close_pid_on_fail, None)
        raise


def _is_sensitive_url_approved(
    url: str,
    cfg: ChromeSection,
    *,
    human_approved: bool,
    approved_sensitive_hosts: Sequence[str] | None,
) -> bool:
    """判断高敏 URL 是否已获本次审批放行。

    - 若调用方传入了显式主机白名单（agent 工具执行层从 `ctx.bound` 注入），必须实时主机名精确命中该名单；
      防止「批准了普通既有页 / JS 执行，但执行期页面实为或跳向高敏站」的跨标记 / TOCTOU 绕过。
    - 若未传主机名单（外部 Python 直调兼容模式），退回 `human_approved` 布尔判断。
    """
    if not url or not is_sensitive_host(url, cfg.sensitive_hosts):
        return True
    if approved_sensitive_hosts is not None:
        live_host = _url_host(url)
        allowed = {str(h).strip().lower().rstrip(".") for h in approved_sensitive_hosts if str(h).strip()}
        return bool(live_host and live_host in allowed)
    return bool(human_approved)


def _guard_sensitive_host_live(
    url: str,
    cfg: ChromeSection,
    *,
    action: str,
    human_approved: bool,
    approved_sensitive_hosts: Sequence[str] | None = None,
    sess: Any | None = None,
    close_pid_on_fail: int | None = None,
) -> None:
    """实时 URL 高敏站点复核（防两次调用间 JS/重定向跳转与跨标记审批绕过）。"""
    if _is_sensitive_url_approved(
        url,
        cfg,
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
    ):
        return
    if sess is not None and close_pid_on_fail is not None:
        try:
            sess.call_tool("close_page", {"pageId": close_pid_on_fail}, timeout_s=_CLOSE_TIMEOUT_S)
        except Exception:
            pass
        with _SESSION_LOCK:
            _OWNED_PAGES.discard(close_pid_on_fail)
            _PAGE_URLS.pop(close_pid_on_fail, None)
    raise PermissionError(
        f"实时页面位于未获审批的高敏站点（{_scrub_url(url)}），chrome {action} 须经针对该站点的人工审批后才能执行"
    )


def _guard_expected_origin_live(
    url: str,
    page_id: int,
    *,
    expected_origin: str | None,
) -> None:
    """校验既有标签页实时 origin 与卡面 / session_allow 绑定的 origin 一致（防页面切站后静默复用放行）。"""
    if not expected_origin:
        return
    live_origin = page_origin(url)
    if live_origin != expected_origin:
        raise PermissionError(
            f"标签页 page_id={page_id} 当前站点已变更为 {_scrub_url(url) or '(未知)'}"
            f"（与已批准的 origin={expected_origin} 不一致），请重新审批"
        )


def _guard_current_page_url(
    sess: Any,
    cfg: ChromeSection,
    page_id: int,
    *,
    close_owned_on_fail: bool = True,
    action: str | None = None,
    human_approved: bool = True,
    approved_sensitive_hosts: Sequence[str] | None = None,
    expected_origin: str | None = None,
) -> tuple[str, str]:
    """实时刷新页内 URL/title（更新 `_PAGE_URLS`）并对当前真实 URL 过 `_guard_url`、origin 绑定与高敏站点复核。"""
    url, title = _read_current_page_meta(sess, page_id, timeout_s=cfg.timeout_s)
    close_pid = page_id if (close_owned_on_fail and is_owned_page(page_id)) else None
    if url:
        _guard_url_and_cleanup_on_fail(
            url,
            trusted_ranges=cfg.trusted_fake_ip_ranges,
            sess=sess if close_owned_on_fail else None,
            close_pid_on_fail=close_pid,
        )
        _guard_expected_origin_live(url, page_id, expected_origin=expected_origin)
        if action is not None:
            _guard_sensitive_host_live(
                url,
                cfg,
                action=action,
                human_approved=human_approved,
                approved_sensitive_hosts=approved_sensitive_hosts,
                sess=sess if close_owned_on_fail else None,
                close_pid_on_fail=close_pid,
            )
    elif expected_origin:
        _guard_expected_origin_live("", page_id, expected_origin=expected_origin)
    return url, title


def _redact_sensitive_network_list(
    raw_text: str,
    cfg: ChromeSection,
    *,
    human_approved: bool,
    approved_sensitive_hosts: Sequence[str] | None,
) -> str:
    """对 list_network 中指向未审批高敏站点的请求行做遮蔽（含 include_preserved 历史请求）。"""
    out_lines: list[str] = []
    for ln in raw_text.splitlines():
        urls = _URL_TOKEN_RE.findall(ln)
        if any(
            not _is_sensitive_url_approved(
                u,
                cfg,
                human_approved=human_approved,
                approved_sensitive_hosts=approved_sensitive_hosts,
            )
            for u in urls
        ):
            out_lines.append("[已隐藏未审批高敏站点网络请求]")
        else:
            out_lines.append(ln)
    return "\n".join(out_lines)


_NET_REQ_LINE_URL_RE = re.compile(
    r"(?im)^(?:(?:Request\s+URL|URL|Location)\s*:\s*|(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)(https?://\S+)"
)


def _extract_network_request_urls(raw_text: str) -> list[str]:
    """优先提取 get_network 详情中的结构化请求 URL，避免响应正文外链（如文档页里的 github 链接）误拦。"""
    urls: list[str] = []
    stripped = raw_text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("url", "requestUrl", "redirectUrl", "location"):
                v = parsed.get(key)
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    urls.append(v)
            req_obj = parsed.get("request")
            if isinstance(req_obj, dict):
                v = req_obj.get("url")
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    urls.append(v)
    if not urls:
        for m in _NET_REQ_LINE_URL_RE.finditer(raw_text):
            urls.append(m.group(1).strip().rstrip(",;\"')"))
    if urls:
        return urls
    # 无法解析结构化请求头/请求行时 fail-closed 退回全文 URL 扫描
    return _URL_TOKEN_RE.findall(raw_text)


def _guard_sensitive_network_detail(
    raw_text: str,
    cfg: ChromeSection,
    *,
    human_approved: bool,
    approved_sensitive_hosts: Sequence[str] | None,
) -> None:
    """校验 get_network 返回详情中的目标请求 URL 不属于未审批高敏站点。"""
    for u in _extract_network_request_urls(raw_text):
        if not _is_sensitive_url_approved(
            u,
            cfg,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
        ):
            raise PermissionError(
                f"抓取到的网络请求指向未获审批的高敏站点（{_scrub_url(u)}），chrome get_network 须经针对该站点的人工审批后才能读取"
            )


def _handle_list_pages(sess: Any, cfg: ChromeSection) -> dict[str, Any]:
    res = sess.call_tool("list_pages", {}, timeout_s=cfg.timeout_s)
    pages = _parse_pages(res)
    live_ids = {p["page_id"] for p in pages}
    with _SESSION_LOCK:
        for gone in list(_OWNED_PAGES - live_ids):
            _OWNED_PAGES.discard(gone)
            _PAGE_URLS.pop(gone, None)
        owned_snapshot = set(_OWNED_PAGES)
    out_pages = [
        {
            "page_id": p["page_id"],
            "owned": p["page_id"] in owned_snapshot,
            "selected": p["selected"],
            "url": _scrub_url(p["url"]),
            "title": _scrub(p["title"]),
        }
        for p in pages
    ]
    return {
        "action": "list_pages",
        "pages": out_pages,
        "owned_page_ids": sorted(owned_snapshot),
    }


def _handle_open(
    sess: Any,
    cfg: ChromeSection,
    *,
    url: str,
    human_approved: bool = False,
    approved_sensitive_hosts: Sequence[str] | None = None,
) -> dict[str, Any]:
    _guard_url(url, trusted_ranges=cfg.trusted_fake_ip_ranges)
    assert_egress_boundary(url, {"url": _normalized_for_assert(url)})
    _guard_sensitive_host_live(
        url,
        cfg,
        action="open",
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
    )

    before_pages = _parse_pages(sess.call_tool("list_pages", {}, timeout_s=cfg.timeout_s))
    before_ids = {p["page_id"] for p in before_pages}

    open_res = sess.call_tool(
        "new_page",
        {
            "url": url,
            "background": True,  # 后台焦点硬隔离：永远后台建页，不抢前台焦点
            "timeout": int(cfg.timeout_s * 1000),
        },
        timeout_s=cfg.timeout_s,
    )
    after_pages = _parse_pages(sess.call_tool("list_pages", {}, timeout_s=cfg.timeout_s))
    pid = _resolve_opened_page_id(before_ids, open_res, after_pages, url)

    with _SESSION_LOCK:
        _OWNED_PAGES.add(pid)

    final_url, title = _read_current_page_meta(sess, pid, timeout_s=cfg.timeout_s)
    final_url = final_url or url
    _guard_url_and_cleanup_on_fail(
        final_url,
        trusted_ranges=cfg.trusted_fake_ip_ranges,
        sess=sess,
        close_pid_on_fail=pid,
    )
    _guard_sensitive_host_live(
        final_url,
        cfg,
        action="open",
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
        sess=sess,
        close_pid_on_fail=pid,
    )
    with _SESSION_LOCK:
        _PAGE_URLS[pid] = final_url

    return {
        "action": "open",
        "page_id": pid,
        "owned": True,
        "background": True,
        "url": _scrub_url(url),
        "final_url": _scrub_url(final_url),
        "title": _scrub(title),
    }


def _handle_navigate(
    sess: Any,
    cfg: ChromeSection,
    *,
    page_id: int,
    nav_type: str,
    url: str | None,
    human_approved: bool = False,
    approved_sensitive_hosts: Sequence[str] | None = None,
) -> dict[str, Any]:
    if nav_type == "url":
        assert url is not None
        _guard_url(url, trusted_ranges=cfg.trusted_fake_ip_ranges)
        assert_egress_boundary(url, {"url": _normalized_for_assert(url)})
        _guard_sensitive_host_live(
            url,
            cfg,
            action="navigate",
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
        )

    _select_background(sess, page_id, timeout_s=cfg.timeout_s)
    _guard_current_page_url(
        sess,
        cfg,
        page_id,
        action="navigate",
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
    )
    nav_args: dict[str, Any] = {
        "pageId": page_id,
        "type": nav_type,
        "timeout": int(cfg.timeout_s * 1000),
    }
    if nav_type == "url" and url:
        nav_args["url"] = url
    nav_res = sess.call_tool("navigate_page", nav_args, timeout_s=cfg.timeout_s)

    final_url, title = _guard_current_page_url(
        sess,
        cfg,
        page_id,
        action="navigate",
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
    )
    return {
        "action": "navigate",
        "page_id": page_id,
        "owned": is_owned_page(page_id),
        "final_url": _scrub_url(final_url),
        "title": _scrub(title),
        "text": _scrub(_extract_mcp_text(nav_res)),
    }


def _handle_close(sess: Any, cfg: ChromeSection, *, page_id: int) -> dict[str, Any]:
    already_closed = False
    try:
        sess.call_tool("close_page", {"pageId": page_id}, timeout_s=min(cfg.timeout_s, 10.0))
    except ValueError as exc:
        lowered = str(exc).lower()
        if any(tok in lowered for tok in ("no page found", "page not found", "already closed")):
            already_closed = True
        else:
            raise
    with _SESSION_LOCK:
        _OWNED_PAGES.discard(page_id)
        _PAGE_URLS.pop(page_id, None)
    out: dict[str, Any] = {"action": "close", "page_id": page_id, "closed": True}
    if already_closed:
        out["already_closed"] = True
    return out


def _handle_extract_text(
    sess: Any,
    cfg: ChromeSection,
    *,
    page_id: int,
    root: Path | None,
    keep_full: bool,
    wait_for_stable_dom: bool = False,
    human_approved: bool = False,
    approved_sensitive_hosts: Sequence[str] | None = None,
    expected_origin: str | None = None,
) -> dict[str, Any]:
    _select_background(sess, page_id, timeout_s=cfg.timeout_s)
    _guard_current_page_url(
        sess,
        cfg,
        page_id,
        action="extract_text",
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
        expected_origin=expected_origin,
    )
    raw_url, title, raw_text, raw_html = _extract_dom_snapshot(
        sess, page_id, timeout_s=cfg.timeout_s, wait_for_stable_dom=wait_for_stable_dom
    )
    final_url = raw_url or known_page_url(page_id)
    if final_url:
        close_pid = page_id if is_owned_page(page_id) else None
        _guard_url_and_cleanup_on_fail(
            final_url,
            trusted_ranges=cfg.trusted_fake_ip_ranges,
            sess=sess,
            close_pid_on_fail=close_pid,
        )
        _guard_expected_origin_live(final_url, page_id, expected_origin=expected_origin)
        _guard_sensitive_host_live(
            final_url,
            cfg,
            action="extract_text",
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            sess=sess,
            close_pid_on_fail=close_pid,
        )
        with _SESSION_LOCK:
            _PAGE_URLS[page_id] = final_url

    normalized = _normalize_extracted_text(raw_text, raw_html)
    scrubbed = scrub_secrets(_scrub(normalized), root)
    capped, truncated = _cap_text(scrubbed, cfg.max_chars)
    images = _clean_refs(_extract_images(raw_html, final_url), ()) if raw_html and final_url else []
    links = _clean_refs(_extract_links(raw_html, final_url, keep_unanchored=True), ()) if raw_html and final_url else []

    out: dict[str, Any] = {
        "action": "extract_text",
        "page_id": page_id,
        "url": _scrub_url(final_url),
        "final_url": _scrub_url(final_url),
        "title": _scrub(title),
        "text": capped,
        "truncated": truncated,
        "image_count": len(images),
        "link_count": len(links),
        "images": images[:20],
        "links": links[:20],
    }
    if keep_full:
        out["full_text"] = scrubbed
        out["full_images"] = images
        out["full_links"] = links
    return out


def _shot_cache_dir(base_root: Path) -> Path:
    if (base_root / "data").is_dir():
        d = base_root / _CACHE_SHOT_REL
    else:
        d = Path(tempfile.gettempdir()) / "ava-chrome-shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _capture_png_bytes(
    sess: Any,
    cfg: ChromeSection,
    *,
    page_id: int,
    uid: str | None,
    full_page: bool,
    target_path: Path,
) -> tuple[bytes, Path]:
    # chrome-devtools-mcp 默认仅允许在 os.tmpdir() 下写文件：先在临时目录落盘，再读回转存到目标缓存路径
    stage_dir = Path(tempfile.mkdtemp(prefix="ava-chrome-shot-"))
    stage_path = stage_dir / target_path.name
    shot_args: dict[str, Any] = {
        "pageId": page_id,
        "format": "png",
        "fullPage": bool(full_page) if not uid else False,
        "filePath": str(stage_path),
    }
    if uid:
        shot_args["uid"] = uid
    try:
        res = sess.call_tool("take_screenshot", shot_args, timeout_s=cfg.timeout_s)
        png_bytes: bytes | None = None
        if stage_path.is_file():
            png_bytes = stage_path.read_bytes()
        else:
            png_bytes = _extract_mcp_image_bytes(res)
            if png_bytes is None:
                m = _SAVED_SHOT_PATH_RE.search(_extract_mcp_text(res))
                if m:
                    raw_reported = m.group(1).strip().strip("\"'")
                    if raw_reported.endswith(".") and not Path(raw_reported).exists():
                        raw_reported = raw_reported[:-1].rstrip("\"'")
                    reported = Path(raw_reported).resolve()
                    tmp_root = Path(tempfile.gettempdir()).resolve()
                    if reported.is_file() and (reported == tmp_root or tmp_root in reported.parents):
                        png_bytes = reported.read_bytes()
        if not png_bytes:
            raise ValueError(f"chrome screenshot 未生成截图文件，且未返回图像内容（{target_path}）")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(png_bytes)
        return png_bytes, target_path
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def _handle_screenshot(
    sess: Any,
    cfg: ChromeSection,
    *,
    page_id: int,
    base_root: Path,
    uid: str | None,
    full_page: bool,
    save_evidence: bool,
    expect_text: str | None,
    keep_full: bool,
    human_approved: bool = False,
    approved_sensitive_hosts: Sequence[str] | None = None,
    expected_origin: str | None = None,
) -> dict[str, Any]:
    from PIL import Image

    _select_background(sess, page_id, timeout_s=cfg.timeout_s)
    _guard_current_page_url(
        sess,
        cfg,
        page_id,
        action="screenshot",
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
        expected_origin=expected_origin,
    )
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:18]
    cache_dir = _shot_cache_dir(base_root)
    cache_path = cache_dir / f"chrome-p{page_id}-{stamp}.png"

    png_bytes, saved_path = _capture_png_bytes(
        sess,
        cfg,
        page_id=page_id,
        uid=uid,
        full_page=full_page,
        target_path=cache_path,
    )
    with Image.open(io.BytesIO(png_bytes)) as im:
        size = [im.width, im.height]

    if not save_evidence:
        try:
            cur_url, title = _guard_current_page_url(
                sess,
                cfg,
                page_id,
                action="screenshot",
                human_approved=human_approved,
                approved_sensitive_hosts=approved_sensitive_hosts,
                expected_origin=expected_origin,
            )
        except Exception:
            saved_path.unlink(missing_ok=True)
            raise
        digest = hashlib.sha256(png_bytes).hexdigest()
        return {
            "action": "screenshot",
            "page_id": page_id,
            "final_url": _scrub_url(cur_url),
            "title": _scrub(title),
            "file": str(saved_path),
            "size": size,
            vision.OUTCOME_IMAGE_KEY: {"abs_path": str(saved_path), "sha256": digest},
        }

    # D122/D141 证据入库模式：先校验局部构图尺寸，再取页内 DOM 与 1080p 缩放图 OCR 复核，供 save_screenshot 入 incoming
    from pipeline.still_geometry import screenshot_frame_issue

    issue = screenshot_frame_issue((size[0], size[1]))
    if issue:
        saved_path.unlink(missing_ok=True)
        raise ValueError(f"chrome 物证截图尺寸不合规（{size[0]}×{size[1]}）：{issue}，没有保存")

    try:
        raw_url, title, raw_text, raw_html = _extract_dom_snapshot(sess, page_id, timeout_s=cfg.timeout_s)
        final_url = raw_url or known_page_url(page_id)
        if final_url:
            close_pid = page_id if is_owned_page(page_id) else None
            _guard_url_and_cleanup_on_fail(
                final_url,
                trusted_ranges=cfg.trusted_fake_ip_ranges,
                sess=sess,
                close_pid_on_fail=close_pid,
            )
            _guard_expected_origin_live(final_url, page_id, expected_origin=expected_origin)
            _guard_sensitive_host_live(
                final_url,
                cfg,
                action="screenshot",
                human_approved=human_approved,
                approved_sensitive_hosts=approved_sensitive_hosts,
                sess=sess,
                close_pid_on_fail=close_pid,
            )
            with _SESSION_LOCK:
                _PAGE_URLS[page_id] = final_url
    except Exception:
        saved_path.unlink(missing_ok=True)
        raise
    normalized = scrub_secrets(_scrub(_normalize_extracted_text(raw_text, raw_html)), base_root)

    ocr_match: str | None = None
    if expect_text:
        from pipeline.agent import ocr
        from pipeline.facts import check_excerpt, norm_exact

        if norm_exact(expect_text) not in norm_exact(normalized):
            raise ValueError(
                f"页面 DOM 文字里没有「{expect_text}」，截图证明不了它，没有保存。"
                f"页面文字开头：{normalized[:120]!r}"
            )
        ocr_match, near = check_excerpt(expect_text, ocr.recognize_for_1080p(png_bytes))
        if ocr_match == "none":
            raise ValueError(
                f"页面 DOM 里有「{expect_text}」，但缩进 1080p 成片安全框后截图上 OCR 认不出来，没有保存；"
                f"最接近一段：{near!r}"
            )

    refs: dict[str, Any] = {}
    if keep_full:
        images = _clean_refs(_extract_images(raw_html, final_url), ()) if raw_html and final_url else []
        links = _clean_refs(_extract_links(raw_html, final_url, keep_unanchored=True), ()) if raw_html and final_url else []
        refs = {"full_text": normalized, "full_images": images, "full_links": links}

    return {
        "action": "screenshot",
        "page_id": page_id,
        "url": _scrub_url(final_url),
        "final_url": _scrub_url(final_url),
        "title": _scrub(title),
        "png": png_bytes,
        "element_text": normalized[:500],
        **({"ocr_match": ocr_match} if ocr_match else {}),
        **refs,
    }


def chrome_action(
    action: str,
    *,
    url: str | None = None,
    page_id: int | None = None,
    allow_existing: bool = False,
    nav_type: str = "url",
    uid: str | None = None,
    value: str | None = None,
    dbl_click: bool = False,
    include_snapshot: bool = False,
    verbose: bool = False,
    text: str | Sequence[str] | None = None,
    visible: bool = False,
    timeout_ms: int | None = None,
    resource_types: Sequence[str] | None = None,
    page_size: int | None = None,
    page_idx: int | None = None,
    include_preserved: bool = False,
    reqid: int | None = None,
    function: str | None = None,
    eval_args: Sequence[str] | None = None,
    wait_for_stable_dom: bool = False,
    full_page: bool = False,
    save_evidence: bool = False,
    reason: str | None = None,
    expect_text: str | None = None,
    root: Path | None = None,
    keep_full: bool = False,
    human_approved: bool = False,
    approved_sensitive_hosts: Sequence[str] | None = None,
    expected_origin: str | None = None,
    launch_fn: Callable[[ChromeSection], Any] | None = None,
) -> dict[str, Any]:
    """执行一次受控的主力 Chrome 动作（通过标准 chrome-devtools-mcp --autoConnect）。"""
    base_root = Path(root) if root is not None else _repo_root()
    err, is_perm = _precheck_with_exc(
        action,
        {
            "url": url,
            "page_id": page_id,
            "allow_existing": allow_existing,
            "nav_type": nav_type,
            "uid": uid,
            "value": value,
            "text": text,
            "timeout_ms": timeout_ms,
            "resource_types": resource_types,
            "page_size": page_size,
            "page_idx": page_idx,
            "reqid": reqid,
            "function": function,
            "eval_args": eval_args,
            "full_page": full_page,
            "save_evidence": save_evidence,
            "reason": reason,
            "expect_text": expect_text,
        },
        root=base_root,
    )
    if err:
        if is_perm:
            raise PermissionError(err)
        raise ValueError(err)

    timeout_ms_val, _ = _coerce_int_param(timeout_ms, "timeout_ms", min_value=1)
    page_size_val, _ = _coerce_int_param(page_size, "page_size", min_value=1)
    page_idx_val, _ = _coerce_int_param(page_idx, "page_idx", min_value=0)
    reqid_val, _ = _coerce_int_param(reqid, "reqid")

    cfg = load_chrome_section(base_root)
    sess = _get_or_start_session(cfg, launch_fn=launch_fn)

    if action == "list_pages":
        return _handle_list_pages(sess, cfg)

    if action == "open":
        assert url is not None
        return _handle_open(
            sess,
            cfg,
            url=url.strip(),
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
        )

    pid = coerce_page_id(page_id)
    assert pid is not None

    if action == "navigate":
        return _handle_navigate(
            sess,
            cfg,
            page_id=pid,
            nav_type=nav_type,
            url=url.strip() if url else None,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
        )

    if action == "close":
        return _handle_close(sess, cfg, page_id=pid)

    if action == "extract_text":
        return _handle_extract_text(
            sess,
            cfg,
            page_id=pid,
            root=base_root,
            keep_full=keep_full,
            wait_for_stable_dom=wait_for_stable_dom,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )

    if action == "screenshot":
        return _handle_screenshot(
            sess,
            cfg,
            page_id=pid,
            base_root=base_root,
            uid=uid,
            full_page=full_page,
            save_evidence=coerce_bool_flag(save_evidence, False),
            expect_text=expect_text,
            keep_full=keep_full,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )

    _select_background(sess, pid, timeout_s=cfg.timeout_s)
    cur_url, _ = _guard_current_page_url(
        sess,
        cfg,
        pid,
        action=action,
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
        expected_origin=expected_origin,
    )

    if action == "snapshot":
        res = sess.call_tool("take_snapshot", {"pageId": pid, "verbose": bool(verbose)}, timeout_s=cfg.timeout_s)
        clean, truncated = _sanitize_text(_extract_mcp_text(res), root=base_root, max_chars=cfg.max_chars)
        final_url, _ = _guard_current_page_url(
            sess,
            cfg,
            pid,
            action=action,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )
        return {"action": "snapshot", "page_id": pid, "final_url": _scrub_url(final_url), "text": clean, "truncated": truncated}

    if action == "click":
        assert uid is not None
        res = sess.call_tool(
            "click",
            {
                "pageId": pid,
                "uid": uid,
                "dblClick": bool(dbl_click),
                "includeSnapshot": bool(include_snapshot),
            },
            timeout_s=cfg.timeout_s,
        )
        clean, truncated = _sanitize_text(_extract_mcp_text(res), root=base_root, max_chars=cfg.max_chars)
        final_url, _ = _guard_current_page_url(
            sess,
            cfg,
            pid,
            action=action,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )
        return {
            "action": "click",
            "page_id": pid,
            "uid": uid,
            "final_url": _scrub_url(final_url),
            "text": clean,
            "truncated": truncated,
        }

    if action == "fill":
        assert uid is not None and value is not None
        assert_egress_boundary(cur_url or known_page_url(pid) or "chrome:fill", {"value": value})
        res = sess.call_tool(
            "fill",
            {
                "pageId": pid,
                "uid": uid,
                "value": str(value),
                "includeSnapshot": bool(include_snapshot),
            },
            timeout_s=cfg.timeout_s,
        )
        clean, truncated = _sanitize_text(_extract_mcp_text(res), root=base_root, max_chars=cfg.max_chars)
        final_url, _ = _guard_current_page_url(
            sess,
            cfg,
            pid,
            action=action,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )
        return {
            "action": "fill",
            "page_id": pid,
            "uid": uid,
            "final_url": _scrub_url(final_url),
            "text": clean,
            "truncated": truncated,
        }

    if action == "wait_for":
        texts = [text] if isinstance(text, str) else [str(t) for t in (text or [])]
        wait_args: dict[str, Any] = {"pageId": pid, "text": texts, "visible": bool(visible)}
        if timeout_ms_val is not None:
            wait_args["timeout"] = timeout_ms_val
        res = sess.call_tool("wait_for", wait_args, timeout_s=cfg.timeout_s)
        clean, truncated = _sanitize_text(_extract_mcp_text(res), root=base_root, max_chars=cfg.max_chars)
        final_url, _ = _guard_current_page_url(
            sess,
            cfg,
            pid,
            action=action,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )
        return {
            "action": "wait_for",
            "page_id": pid,
            "final_url": _scrub_url(final_url),
            "text": clean,
            "truncated": truncated,
        }

    if action == "list_network":
        net_args: dict[str, Any] = {"pageId": pid, "includePreservedRequests": bool(include_preserved)}
        if resource_types:
            net_args["resourceTypes"] = list(resource_types)
        if page_size_val is not None:
            net_args["pageSize"] = page_size_val
        if page_idx_val is not None:
            net_args["pageIdx"] = page_idx_val
        res = sess.call_tool("list_network_requests", net_args, timeout_s=cfg.timeout_s)
        final_url, _ = _guard_current_page_url(
            sess,
            cfg,
            pid,
            action=action,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )
        filtered_list = _redact_sensitive_network_list(
            _extract_mcp_text(res),
            cfg,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
        )
        stripped_list = _SENSITIVE_QUERY_RE.sub(r"\1[已剥离敏感参数]", filtered_list)
        clean, truncated = _sanitize_text(stripped_list, root=base_root, max_chars=cfg.max_chars)
        return {"action": "list_network", "page_id": pid, "final_url": _scrub_url(final_url or cur_url), "text": clean, "truncated": truncated}

    if action == "get_network":
        req_args: dict[str, Any] = {"pageId": pid}
        if reqid_val is not None:
            req_args["reqid"] = reqid_val
        res = sess.call_tool("get_network_request", req_args, timeout_s=cfg.timeout_s)
        final_url, _ = _guard_current_page_url(
            sess,
            cfg,
            pid,
            action=action,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
            expected_origin=expected_origin,
        )
        raw_net_text = _extract_mcp_text(res)
        _guard_sensitive_network_detail(
            raw_net_text,
            cfg,
            human_approved=human_approved,
            approved_sensitive_hosts=approved_sensitive_hosts,
        )
        stripped = _strip_sensitive_headers_text(raw_net_text)
        clean, truncated = _sanitize_text(stripped, root=base_root, max_chars=cfg.max_chars)
        return {"action": "get_network", "page_id": pid, "final_url": _scrub_url(final_url or cur_url), "text": clean, "truncated": truncated}

    # evaluate
    assert function is not None
    eval_payload: dict[str, Any] = {
        "pageId": pid,
        "function": function,
        "waitForStableDom": bool(wait_for_stable_dom),
    }
    if eval_args:
        eval_payload["args"] = list(eval_args)
    res = sess.call_tool("evaluate_script", eval_payload, timeout_s=cfg.timeout_s)
    clean, truncated = _sanitize_text(_extract_mcp_text(res), root=base_root, max_chars=cfg.max_chars)
    final_url, _ = _guard_current_page_url(
        sess,
        cfg,
        pid,
        action=action,
        human_approved=human_approved,
        approved_sensitive_hosts=approved_sensitive_hosts,
        expected_origin=expected_origin,
    )
    return {
        "action": "evaluate",
        "page_id": pid,
        "final_url": _scrub_url(final_url),
        "text": clean,
        "truncated": truncated,
    }
