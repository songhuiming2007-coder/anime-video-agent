"""D170：AVA 独立多服务商认证、OAuth 续期、本地模型元数据缓存与路由。

完全独立运行，不依赖任何外部 agent CLI/路径：
- 凭据存储：`config/auth.local.json`（环境变量 `AVA_AUTH_PATH` 可覆盖），`0o600` 权限 + 文件锁原子写。
- 支持 10 家服务商：CLIProxyAPI 代理、Anthropic (OAuth PKCE / API Key)、OpenAI Codex (OAuth PKCE)、
  GitHub Copilot (Device Flow)、OpenAI、DeepSeek、Kimi Coding、GLM/Z.ai、Google Gemini、OpenRouter。
- OAuth 访问令牌在过期前 60 秒自动加锁刷新并回写。
- `N50`：凭据文件纳入 `tools.py` 出网阻止集与脱敏密钥集，永不传入工作流子进程或 Bash。
"""
from __future__ import annotations

import base64
import contextlib
from dataclasses import dataclass, field
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request

from pipeline import paths

REFRESH_SKEW_MS = 60_000
DEFAULT_HTTP_TIMEOUT_S = 20.0
ALLOWED_AUTH_MODES = frozenset({"proxy", "api_key", "oauth_pkce", "oauth_device"})


class AuthError(RuntimeError):
    """D170 认证/续期/回调解析错误（可直接展示给用户）。"""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    desc: str
    auth_modes: tuple[str, ...]
    default_base_url: str
    api_protocol: str = "openai-completions"
    default_models: tuple[str, ...] = ()
    extra_headers: dict[str, str] = field(default_factory=dict)
    oauth: dict[str, Any] = field(default_factory=dict)
    supports_custom_base_url: bool = True
    sync_models_on_connect: bool = False


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="cliproxyapi",
        name="CLIProxyAPI / 本机代理",
        desc="连接本机或自建 CLIProxyAPI / OpenAI 兼容反代，并同步其模型与推理能力表",
        auth_modes=("proxy",),
        default_base_url="http://127.0.0.1:8317/v1",
        api_protocol="openai-completions",
        default_models=(),
        sync_models_on_connect=True,
    ),
    ProviderSpec(
        id="anthropic",
        name="Anthropic (Claude)",
        desc="支持 Claude 订阅 OAuth PKCE 登录或 sk-ant-* API Key",
        auth_modes=("oauth_pkce", "api_key"),
        default_base_url="https://api.anthropic.com",
        api_protocol="anthropic-messages",
        default_models=(
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ),
        oauth={
            "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
            "authorize_url": "https://claude.ai/oauth/authorize",
            "token_url": "https://console.anthropic.com/v1/oauth/token",
            "redirect_uri": "https://console.anthropic.com/oauth/code/callback",
            "scopes": "org:create_api_key user:profile user:inference",
            "token_content_type": "json",
            "extra_authorize_params": {"code": "true"},
            "extra_headers": {
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14",
            },
        },
    ),
    ProviderSpec(
        id="openai-codex",
        name="OpenAI Codex (ChatGPT)",
        desc="使用 ChatGPT Plus / Pro 账号 OAuth PKCE 登录（Responses API）",
        auth_modes=("oauth_pkce",),
        default_base_url="https://chatgpt.com/backend-api",
        api_protocol="openai-codex-responses",
        default_models=(
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-5.1-codex-mini",
        ),
        extra_headers={
            "OpenAI-Beta": "responses=experimental",
            "originator": "ava",
        },
        oauth={
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "authorize_url": "https://auth.openai.com/oauth/authorize",
            "token_url": "https://auth.openai.com/oauth/token",
            "redirect_uri": "http://localhost:1455/auth/callback",
            "scopes": "openid profile email offline_access",
            "token_content_type": "form",
            "extra_authorize_params": {
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "originator": "ava",
            },
        },
    ),
    ProviderSpec(
        id="github-copilot",
        name="GitHub Copilot",
        desc="使用 GitHub 账号 Device Flow 设备码登录 Copilot",
        auth_modes=("oauth_device",),
        default_base_url="https://api.individual.githubcopilot.com",
        api_protocol="openai-completions",
        default_models=(
            "claude-sonnet-4.6",
            "gpt-5.2-codex",
            "gemini-2.5-pro",
            "gpt-4.1",
        ),
        extra_headers={
            "Editor-Version": "vscode/1.107.0",
            "Editor-Plugin-Version": "copilot-chat/0.35.0",
            "Copilot-Integration-Id": "vscode-chat",
        },
        oauth={
            "client_id": "Iv1.b507a08c87ecfe98",
            "device_code_url": "https://github.com/login/device/code",
            "token_url": "https://github.com/login/oauth/access_token",
            "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
            "scopes": "read:user",
        },
    ),
    ProviderSpec(
        id="openai",
        name="OpenAI",
        desc="OpenAI 官方 API Key（支持自定义兼容 base_url）",
        auth_modes=("api_key",),
        default_base_url="https://api.openai.com/v1",
        api_protocol="openai-completions",
        default_models=("gpt-5.4", "gpt-5.2", "gpt-4.1", "o3", "o4-mini"),
    ),
    ProviderSpec(
        id="deepseek",
        name="DeepSeek",
        desc="DeepSeek 官方 API Key（deepseek-chat / deepseek-reasoner）",
        auth_modes=("api_key",),
        default_base_url="https://api.deepseek.com/v1",
        api_protocol="openai-completions",
        default_models=("deepseek-chat", "deepseek-reasoner"),
    ),
    ProviderSpec(
        id="kimi-coding",
        name="Kimi Coding",
        desc="Moonshot Kimi For Coding API Key",
        auth_modes=("api_key",),
        default_base_url="https://api.kimi.com/coding/v1",
        api_protocol="openai-completions",
        default_models=("kimi-for-coding", "k2p5"),
    ),
    ProviderSpec(
        id="zai",
        name="GLM / Z.ai",
        desc="智谱 GLM / Z.ai OpenAI 兼容 API Key",
        auth_modes=("api_key",),
        default_base_url="https://api.z.ai/api/paas/v4",
        api_protocol="openai-completions",
        default_models=("glm-5", "glm-4.7", "glm-4.6"),
    ),
    ProviderSpec(
        id="google-gemini",
        name="Google Gemini",
        desc="Google AI Studio API Key（OpenAI 兼容接口）",
        auth_modes=("api_key",),
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_protocol="openai-completions",
        default_models=("gemini-2.5-pro", "gemini-2.5-flash"),
    ),
    ProviderSpec(
        id="openrouter",
        name="OpenRouter",
        desc="OpenRouter 多模型聚合网关 API Key",
        auth_modes=("api_key",),
        default_base_url="https://openrouter.ai/api/v1",
        api_protocol="openai-completions",
        default_models=(
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.4",
            "google/gemini-2.5-pro",
            "deepseek/deepseek-r1",
        ),
    ),
)

PROVIDER_MAP: dict[str, ProviderSpec] = {p.id: p for p in PROVIDERS}


def auth_path(root: Path | None = None) -> Path:
    """凭据文件落点：`AVA_AUTH_PATH` > `<root>/config/auth.local.json`。"""
    override = os.environ.get("AVA_AUTH_PATH", "").strip()
    if override:
        return Path(override)
    base = Path(root) if root is not None else paths.ROOT
    return base / "config" / "auth.local.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_provider(provider: str) -> ProviderSpec:
    spec = PROVIDER_MAP.get(provider)
    if spec is None:
        raise AuthError(f"未知服务商：{provider}")
    return spec


def _validate_base_url(url: str) -> str:
    clean = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AuthError(f"base_url 须是合法 http/https 地址：{url!r}")
    return clean


@contextlib.contextmanager
def _auth_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_store_unlocked(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"providers": {}, "_pending": {}}
    except OSError as exc:
        raise AuthError(f"读取认证文件失败: {exc}") from exc
    if not raw.strip():
        return {"providers": {}, "_pending": {}}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError(f"认证文件 JSON 已损坏: {exc}") from exc
    if not isinstance(data, dict):
        return {"providers": {}, "_pending": {}}
    providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    pending = data.get("_pending") if isinstance(data.get("_pending"), dict) else {}
    return {"providers": dict(providers), "_pending": dict(pending)}


def _write_store_unlocked(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _http_json(
    url: str,
    *,
    method: str = "POST",
    data_dict: dict[str, Any] | None = None,
    form_dict: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
) -> dict[str, Any]:
    req_headers: dict[str, str] = {"Accept": "application/json", "User-Agent": "ava-agent/1.0"}
    if headers:
        req_headers.update(headers)
    body_bytes: bytes | None = None
    if data_dict is not None:
        req_headers.setdefault("Content-Type", "application/json")
        body_bytes = json.dumps(data_dict, ensure_ascii=False).encode("utf-8")
    elif form_dict is not None:
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        body_bytes = urllib.parse.urlencode(form_dict).encode("utf-8")
    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", errors="replace")
        raise AuthError(f"HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(f"网络请求失败: {exc}") from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError(f"响应非 JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise AuthError("响应 JSON 不是对象")
    return parsed


def _mask_secret(val: str | None) -> str | None:
    if not val:
        return None
    s = str(val)
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}…{s[-4:]}"


def _jwt_chatgpt_account_id(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload_b64 = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        claims = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        acc = auth_claim.get("chatgpt_account_id")
        if isinstance(acc, str) and acc.strip():
            return acc.strip()
    return None


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_callback_input(raw: str, expected_state: str | None) -> tuple[str, str | None]:
    text = raw.strip()
    if not text:
        raise AuthError("请粘贴授权回调链接或授权码")
    code = text
    state: str | None = None
    if text.startswith("http://") or text.startswith("https://") or "?" in text:
        parsed = urllib.parse.urlparse(text)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("error"):
            desc = qs.get("error_description", [""])[0]
            raise AuthError(f"授权被拒绝：{qs['error'][0]} {desc}".strip())
        if qs.get("code"):
            code = qs["code"][0].strip()
        if qs.get("state"):
            state = qs["state"][0].strip()
    elif "#" in text:
        c_part, s_part = text.split("#", 1)
        code = c_part.strip()
        state = s_part.strip() or None
    if not code:
        raise AuthError("未从输入中解析到授权码（code）")
    if expected_state and state and state != expected_state:
        raise AuthError("OAuth state 校验不匹配，请重新发起授权")
    return code, state or expected_state


def _sync_proxy_models(base_url: str, api_key: str, root: Path | None) -> list[str]:
    from pipeline.agent import model_switch

    url = f"{base_url.rstrip('/')}/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = _http_json(url, method="GET", headers=headers, timeout=10.0)
    raw_items = data.get("data")
    if not isinstance(raw_items, list):
        raise AuthError("代理 /models 返回缺少 data 数组")
    ids = sorted(
        {
            str(item.get("id")).strip()
            for item in raw_items
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id").strip()
        }
    )
    model_switch.cache_models_metadata(raw_items, root=root)
    return ids


def _exchange_oauth_code(spec: ProviderSpec, code: str, verifier: str, state: str | None) -> dict[str, Any]:
    oauth = spec.oauth
    token_url = str(oauth["token_url"])
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": str(oauth["client_id"]),
        "code": code,
        "redirect_uri": str(oauth["redirect_uri"]),
        "code_verifier": verifier,
    }
    if oauth.get("token_content_type") == "json":
        if state:
            payload["state"] = state
        return _http_json(token_url, method="POST", data_dict=payload, headers=oauth.get("extra_headers"))
    return _http_json(token_url, method="POST", form_dict=payload)


def _refresh_oauth_token(spec: ProviderSpec, entry: dict[str, Any]) -> dict[str, Any]:
    oauth = spec.oauth
    now = _now_ms()
    if spec.id == "github-copilot":
        gh_token = str(entry.get("refresh_token") or "").strip()
        if not gh_token:
            raise AuthError("GitHub Copilot 缺少 GitHub 刷新令牌，请重新登录")
        cop = _http_json(
            str(oauth["copilot_token_url"]),
            method="GET",
            headers={
                "Authorization": f"token {gh_token}",
                **spec.extra_headers,
            },
        )
        token = str(cop.get("token") or "").strip()
        if not token:
            raise AuthError("GitHub Copilot 未返回有效会话令牌")
        expires_at = cop.get("expires_at")
        if isinstance(expires_at, (int, float)):
            exp_ms = int(expires_at * 1000) if expires_at < 10_000_000_000 else int(expires_at)
        else:
            exp_ms = now + 25 * 60 * 1000
        endpoints = cop.get("endpoints") if isinstance(cop.get("endpoints"), dict) else {}
        base_url = str(endpoints.get("api") or entry.get("base_url") or spec.default_base_url).rstrip("/")
        updated = dict(entry)
        updated["access_token"] = token
        updated["expires_at_ms"] = exp_ms
        updated["base_url"] = base_url
        updated["updated_at_ms"] = now
        return updated

    refresh_tok = str(entry.get("refresh_token") or "").strip()
    token_url = str(oauth.get("token_url") or "").strip()
    client_id = str(oauth.get("client_id") or "").strip()
    if not refresh_tok or not token_url:
        raise AuthError(f"{spec.name} 令牌已过期且无法续期，请重新登录")
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
        "client_id": client_id,
    }
    if oauth.get("token_content_type") == "json":
        res = _http_json(token_url, method="POST", data_dict=body, headers=oauth.get("extra_headers"))
    else:
        res = _http_json(token_url, method="POST", form_dict=body)
    access = str(res.get("access_token") or "").strip()
    if not access:
        raise AuthError(f"{spec.name} 刷新令牌未返回 access_token")
    new_refresh = str(res.get("refresh_token") or refresh_tok).strip()
    expires_in = res.get("expires_in")
    exp_ms = now + int(float(expires_in) * 1000) if isinstance(expires_in, (int, float)) else now + 3600 * 1000
    updated = dict(entry)
    updated["access_token"] = access
    updated["refresh_token"] = new_refresh
    updated["expires_at_ms"] = exp_ms
    updated["updated_at_ms"] = now
    if spec.id == "openai-codex":
        acc = _jwt_chatgpt_account_id(access)
        if acc:
            updated["account_id"] = acc
    return updated


def _public_provider_row(spec: ProviderSpec, entry: dict[str, Any] | None, root: Path | None) -> dict[str, Any]:
    connected = isinstance(entry, dict) and bool(entry.get("type"))
    models: list[str] = []
    if connected and isinstance(entry, dict):
        raw_models = entry.get("models")
        if isinstance(raw_models, list) and raw_models:
            models = [str(m) for m in raw_models if isinstance(m, str) and m.strip()]
        else:
            models = list(spec.default_models)
    masked = None
    if connected and isinstance(entry, dict):
        masked = _mask_secret(entry.get("api_key") or entry.get("access_token"))
    return {
        "id": spec.id,
        "name": spec.name,
        "desc": spec.desc,
        "auth_modes": list(spec.auth_modes),
        "default_base_url": spec.default_base_url,
        "api_protocol": spec.api_protocol,
        "supports_custom_base_url": spec.supports_custom_base_url,
        "connected": connected,
        "auth_type": entry.get("type") if connected and isinstance(entry, dict) else None,
        "base_url": entry.get("base_url", spec.default_base_url) if connected and isinstance(entry, dict) else spec.default_base_url,
        "masked_secret": masked,
        "expires_at_ms": entry.get("expires_at_ms") if connected and isinstance(entry, dict) else None,
        "models": models,
        "default_models": list(spec.default_models),
    }


def get_auth_status(root: Path | None = None) -> dict[str, Any]:
    """返回脱敏后的全部 10 家服务商状态（绝不返回真实密钥/令牌）。"""
    path = auth_path(root)
    with _auth_lock(path):
        store = _read_store_unlocked(path)
    providers_data = store.get("providers", {})
    rows = [_public_provider_row(spec, providers_data.get(spec.id), root) for spec in PROVIDERS]
    connected_count = sum(1 for r in rows if r["connected"])
    return {
        "ok": True,
        "auth_file": str(path),
        "connected_count": connected_count,
        "providers": rows,
    }


def start_auth_flow(provider: str, mode: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """发起某家服务商的登录流程：PKCE 授权链接 / GitHub Device Code / API Key / 代理参数说明。"""
    spec = _require_provider(provider)
    chosen = mode or spec.auth_modes[0]
    if chosen not in ALLOWED_AUTH_MODES or chosen not in spec.auth_modes:
        raise AuthError(f"{spec.name} 不支持认证方式：{chosen}")

    if chosen in ("api_key", "proxy"):
        return {
            "ok": True,
            "provider": spec.id,
            "mode": chosen,
            "default_base_url": spec.default_base_url,
            "api_protocol": spec.api_protocol,
        }

    path = auth_path(root)
    now = _now_ms()
    if chosen == "oauth_pkce":
        oauth = spec.oauth
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": str(oauth["client_id"]),
            "redirect_uri": str(oauth["redirect_uri"]),
            "scope": str(oauth["scopes"]),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        for k, v in (oauth.get("extra_authorize_params") or {}).items():
            params[str(k)] = str(v)
        authorize_url = f"{oauth['authorize_url']}?{urllib.parse.urlencode(params)}"
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            store.setdefault("_pending", {})[spec.id] = {
                "mode": "oauth_pkce",
                "code_verifier": verifier,
                "state": state,
                "created_at_ms": now,
            }
            _write_store_unlocked(path, store)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "oauth_pkce",
            "authorize_url": authorize_url,
            "redirect_uri": str(oauth["redirect_uri"]),
            "state": state,
        }

    # oauth_device (GitHub Copilot)
    oauth = spec.oauth
    dev = _http_json(
        str(oauth["device_code_url"]),
        method="POST",
        data_dict={"client_id": str(oauth["client_id"]), "scope": str(oauth["scopes"])},
    )
    device_code = str(dev.get("device_code") or "").strip()
    user_code = str(dev.get("user_code") or "").strip()
    verification_uri = str(dev.get("verification_uri") or "").strip()
    interval_s = int(dev.get("interval") or 5)
    expires_in_s = int(dev.get("expires_in") or 900)
    if not device_code or not user_code or not verification_uri:
        raise AuthError("GitHub 未返回有效的设备码信息")
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        store.setdefault("_pending", {})[spec.id] = {
            "mode": "oauth_device",
            "device_code": device_code,
            "interval_s": interval_s,
            "expires_at_ms": now + expires_in_s * 1000,
            "created_at_ms": now,
        }
        _write_store_unlocked(path, store)
    return {
        "ok": True,
        "provider": spec.id,
        "mode": "oauth_device",
        "verification_uri": verification_uri,
        "user_code": user_code,
        "interval_s": interval_s,
        "expires_in_s": expires_in_s,
    }


def complete_auth_flow(
    provider: str,
    payload: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """完成认证并落盘 `config/auth.local.json`（0o600）；CLIProxyAPI 会同步 `/models` 到 `models.local.json`。"""
    spec = _require_provider(provider)
    data = dict(payload or {})
    mode = str(data.get("mode") or spec.auth_modes[0]).strip()
    if mode not in ALLOWED_AUTH_MODES or mode not in spec.auth_modes:
        raise AuthError(f"{spec.name} 不支持认证方式：{mode}")

    path = auth_path(root)
    now = _now_ms()

    if mode == "proxy":
        raw_base = str(data.get("base_url") or spec.default_base_url).strip()
        base_url = _validate_base_url(raw_base)
        api_key = str(data.get("api_key") or "").strip()
        if data.get("skip_probe"):
            models = [str(m).strip() for m in (data.get("models") or []) if str(m).strip()]
        else:
            models = _sync_proxy_models(base_url, api_key, root)
        entry: dict[str, Any] = {
            "type": "proxy",
            "base_url": base_url,
            "api_protocol": spec.api_protocol,
            "models": models,
            "updated_at_ms": now,
        }
        if api_key:
            entry["api_key"] = api_key
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            store.setdefault("providers", {})[spec.id] = entry
            store.get("_pending", {}).pop(spec.id, None)
            _write_store_unlocked(path, store)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "proxy",
            "models": models,
            "text": f"已连接 {spec.name}（同步 {len(models)} 个模型）",
        }

    if mode == "api_key":
        api_key = str(data.get("api_key") or "").strip()
        if not api_key:
            raise AuthError("请填写 API Key")
        raw_base = str(data.get("base_url") or spec.default_base_url).strip()
        base_url = _validate_base_url(raw_base)
        models = list(spec.default_models)
        entry = {
            "type": "api_key",
            "api_key": api_key,
            "base_url": base_url,
            "api_protocol": spec.api_protocol,
            "models": models,
            "updated_at_ms": now,
        }
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            store.setdefault("providers", {})[spec.id] = entry
            store.get("_pending", {}).pop(spec.id, None)
            _write_store_unlocked(path, store)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "api_key",
            "models": models,
            "text": f"已保存 {spec.name} API Key",
        }

    if mode == "oauth_pkce":
        raw_code = str(data.get("code") or data.get("callback_url") or "").strip()
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            pending = store.get("_pending", {}).get(spec.id)
        if not isinstance(pending, dict) or pending.get("mode") != "oauth_pkce":
            raise AuthError("尚未发起 OAuth 授权或会话已失效，请重新点击开始登录")
        code, state = _parse_callback_input(raw_code, pending.get("state"))
        tok = _exchange_oauth_code(spec, code, str(pending["code_verifier"]), state)
        access = str(tok.get("access_token") or "").strip()
        if not access:
            raise AuthError("OAuth 令牌接口未返回 access_token")
        refresh = str(tok.get("refresh_token") or "").strip()
        expires_in = tok.get("expires_in")
        exp_ms = now + int(float(expires_in) * 1000) if isinstance(expires_in, (int, float)) else now + 3600 * 1000
        entry = {
            "type": "oauth",
            "access_token": access,
            "refresh_token": refresh,
            "expires_at_ms": exp_ms,
            "base_url": spec.default_base_url,
            "api_protocol": spec.api_protocol,
            "models": list(spec.default_models),
            "updated_at_ms": now,
        }
        if spec.id == "openai-codex":
            acc = _jwt_chatgpt_account_id(access)
            if acc:
                entry["account_id"] = acc
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            store.setdefault("providers", {})[spec.id] = entry
            store.get("_pending", {}).pop(spec.id, None)
            _write_store_unlocked(path, store)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "oauth_pkce",
            "models": list(spec.default_models),
            "text": f"已通过 OAuth 登录 {spec.name}",
        }

    # oauth_device (GitHub Copilot)
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        pending = store.get("_pending", {}).get(spec.id)
    if not isinstance(pending, dict) or pending.get("mode") != "oauth_device":
        raise AuthError("尚未发起 GitHub Copilot 设备码授权，请先点击获取设备码")
    if int(pending.get("expires_at_ms") or 0) <= now:
        raise AuthError("GitHub 设备码已过期，请重新获取")
    oauth = spec.oauth
    tok = _http_json(
        str(oauth["token_url"]),
        method="POST",
        data_dict={
            "client_id": str(oauth["client_id"]),
            "device_code": str(pending["device_code"]),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )
    err = tok.get("error")
    if err in ("authorization_pending", "slow_down"):
        return {
            "ok": False,
            "pending": True,
            "provider": spec.id,
            "error": str(err),
            "interval_s": int(pending.get("interval_s") or 5) + (5 if err == "slow_down" else 0),
            "text": "等待用户在浏览器完成 GitHub 设备码授权…",
        }
    if err:
        raise AuthError(f"GitHub 授权失败：{tok.get('error_description') or err}")
    gh_access = str(tok.get("access_token") or "").strip()
    if not gh_access:
        raise AuthError("GitHub 未返回 access_token")
    base_entry: dict[str, Any] = {
        "type": "oauth",
        "refresh_token": gh_access,
        "access_token": "",
        "expires_at_ms": 0,
        "base_url": spec.default_base_url,
        "api_protocol": spec.api_protocol,
        "models": list(spec.default_models),
        "updated_at_ms": now,
    }
    entry = _refresh_oauth_token(spec, base_entry)
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        store.setdefault("providers", {})[spec.id] = entry
        store.get("_pending", {}).pop(spec.id, None)
        _write_store_unlocked(path, store)
    return {
        "ok": True,
        "provider": spec.id,
        "mode": "oauth_device",
        "models": list(spec.default_models),
        "text": f"已登录 {spec.name}",
    }


def logout_provider(provider: str, *, root: Path | None = None) -> dict[str, Any]:
    """登出并删除指定服务商凭据。"""
    spec = _require_provider(provider)
    path = auth_path(root)
    existed = False
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        if spec.id in store.get("providers", {}):
            del store["providers"][spec.id]
            existed = True
        store.get("_pending", {}).pop(spec.id, None)
        _write_store_unlocked(path, store)
    return {
        "ok": True,
        "provider": spec.id,
        "removed": existed,
        "text": f"已退出 {spec.name} 登录" if existed else f"{spec.name} 当前未登录",
    }


def resolve_provider_credential(provider: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """读取服务商有效凭据；OAuth 距过期 <= 60s 时加锁自动续期并回写。"""
    spec = PROVIDER_MAP.get(provider)
    if spec is None:
        return None
    path = auth_path(root)
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        entry = store.get("providers", {}).get(provider)
        if not isinstance(entry, dict) or not entry.get("type"):
            return None
        if entry.get("type") == "oauth":
            exp = entry.get("expires_at_ms")
            if isinstance(exp, (int, float)) and exp - _now_ms() <= REFRESH_SKEW_MS:
                entry = _refresh_oauth_token(spec, entry)
                store.setdefault("providers", {})[provider] = entry
                _write_store_unlocked(path, store)
    api_key = str(entry.get("api_key") or entry.get("access_token") or "").strip()
    headers = dict(spec.extra_headers)
    if spec.id == "anthropic" and entry.get("type") == "oauth":
        headers.update(spec.oauth.get("extra_headers") or {})
    if spec.id == "openai-codex" and entry.get("account_id"):
        headers["chatgpt-account-id"] = str(entry["account_id"])
    return {
        "provider": spec.id,
        "auth_type": str(entry.get("type")),
        "api_key": api_key,
        "base_url": str(entry.get("base_url") or spec.default_base_url).rstrip("/"),
        "api_protocol": str(entry.get("api_protocol") or spec.api_protocol),
        "extra_headers": headers,
        "models": list(entry.get("models") or spec.default_models),
    }


def list_authenticated_models(root: Path | None = None) -> list[str]:
    """列出所有已登录服务商的 `<provider>/<model>` 模型列表（去重排序）。"""
    path = auth_path(root)
    try:
        with _auth_lock(path):
            store = _read_store_unlocked(path)
    except AuthError:
        return []
    except OSError as exc:
        if exc.errno == errno.EACCES:
            return []
        return []
    providers = store.get("providers", {})
    out: list[str] = []
    seen: set[str] = set()
    for spec in PROVIDERS:
        entry = providers.get(spec.id)
        if not isinstance(entry, dict) or not entry.get("type"):
            continue
        raw_models = entry.get("models")
        models = raw_models if isinstance(raw_models, list) and raw_models else spec.default_models
        for m in models:
            if not isinstance(m, str) or not m.strip():
                continue
            full = f"{spec.id}/{m.strip()}"
            if full not in seen:
                seen.add(full)
                out.append(full)
    return sorted(out)


def resolve_model_provider(model_name: str, *, root: Path | None = None) -> tuple[str, str, dict[str, Any]] | None:
    """若模型名带 `<provider>/` 且该服务商已登录，返回 `(provider, wire_model, credential)`。

    例：`openrouter/anthropic/claude-sonnet-4.6` → `("openrouter", "anthropic/claude-sonnet-4.6", cred)`。
    """
    if "/" not in model_name:
        return None
    pid, wire_model = model_name.split("/", 1)
    if pid not in PROVIDER_MAP or not wire_model.strip():
        return None
    cred = resolve_provider_credential(pid, root=root)
    if cred is None:
        return None
    return pid, wire_model.strip(), cred


def first_authenticated_config(root: Path | None = None) -> dict[str, Any] | None:
    """当 `config/agent*.json` 未就绪时，从第一个已登录服务商构造默认连接参数。"""
    path = auth_path(root)
    try:
        with _auth_lock(path):
            store = _read_store_unlocked(path)
    except (AuthError, OSError):
        return None
    providers = store.get("providers", {})
    for spec in PROVIDERS:
        entry = providers.get(spec.id)
        if not isinstance(entry, dict) or not entry.get("type"):
            continue
        cred = resolve_provider_credential(spec.id, root=root)
        if cred is None:
            continue
        models = cred["models"]
        if not models:
            continue
        return {**cred, "model": f"{spec.id}/{models[0]}"}
    return None


def is_auth_slash_command(line: str) -> bool:
    head = line.strip().split(maxsplit=1)[0] if line.strip() else ""
    return head in ("/login", "/logout")


def run_auth_command(line: str, *, root: Path | None = None) -> tuple[bool, str, str | None]:
    """D170 REPL/对话框 `/login` 与 `/logout` 命令。返回 `(ok, 回显文字, logged_out_provider)`。"""
    parts = line.strip().split()
    if not parts:
        return False, "空命令", None
    cmd = parts[0]
    if cmd == "/logout":
        if len(parts) < 2:
            return False, "用法：/logout <provider>", None
        try:
            res = logout_provider(parts[1].strip(), root=root)
        except AuthError as exc:
            return False, str(exc), None
        return True, str(res["text"]), str(res["provider"])

    if cmd != "/login":
        return False, f"未知命令：{cmd}", None

    if len(parts) == 1:
        status = get_auth_status(root)
        lines = [f"已连接 {status['connected_count']} / {len(status['providers'])} 家服务商："]
        for p in status["providers"]:
            mark = "●" if p["connected"] else "○"
            extra = f"（已登录 {p['masked_secret']}）" if p["connected"] and p.get("masked_secret") else ("（已连接）" if p["connected"] else f"（未登录 · 支持 {'/'.join(p['auth_modes'])}）")
            lines.append(f"  {mark} {p['id']} — {p['name']} {extra}")
        lines.append("用法：/login <provider> <api_key>；OAuth：/login <provider> 发起，然后 /login <provider> complete <回调或码>；退出：/logout <provider>")
        return True, "\n".join(lines), None

    pid = parts[1].strip()
    try:
        spec = _require_provider(pid)
        if len(parts) == 2:
            started = start_auth_flow(pid, root=root)
            mode = started["mode"]
            if mode == "oauth_pkce":
                return True, f"{spec.name} OAuth 授权地址：\n{started['authorize_url']}\n完成后执行：/login {pid} complete <回调链接或授权码>", None
            if mode == "oauth_device":
                return True, f"{spec.name} 设备码授权：\n1. 打开 {started['verification_uri']}\n2. 输入设备码 {started['user_code']}\n3. 授权后执行：/login {pid} complete", None
            return True, f"{spec.name} 默认地址：{started['default_base_url']}\n执行：/login {pid} <api_key> 完成配置（或使用桌面端登录弹窗）", None
        arg = parts[2].strip()
        if arg == "complete":
            code = " ".join(parts[3:]).strip()
            mode = "oauth_device" if "oauth_device" in spec.auth_modes and not code else spec.auth_modes[0]
            res = complete_auth_flow(pid, {"mode": mode, **({"code": code} if code else {})}, root=root)
            return bool(res.get("ok")), str(res.get("text") or ""), None
        if "api_key" in spec.auth_modes:
            res = complete_auth_flow(pid, {"mode": "api_key", "api_key": arg}, root=root)
            return True, str(res["text"]), None
        if "proxy" in spec.auth_modes:
            base = arg if arg.startswith(("http://", "https://")) else spec.default_base_url
            key = parts[3].strip() if len(parts) > 3 else ("" if arg.startswith(("http://", "https://")) else arg)
            res = complete_auth_flow(pid, {"mode": "proxy", "base_url": base, "api_key": key}, root=root)
            return True, str(res["text"]), None
        res = complete_auth_flow(pid, {"mode": spec.auth_modes[0], "code": arg}, root=root)
        return bool(res.get("ok")), str(res.get("text") or ""), None
    except AuthError as exc:
        return False, str(exc), None


def dispatch_auth_cli(
    args: list[str],
    *,
    stdin_reader: Any = None,
    root: Path | None = None,
) -> int | None:
    """D170 `--auth-*` JSON 子命令入口；不命中返回 None。"""
    import sys

    if not args or not args[0].startswith("--auth-"):
        return None
    flag = args[0]
    try:
        if flag == "--auth-status":
            if len(args) != 1:
                raise AuthError("用法: ava --auth-status")
            print(json.dumps(get_auth_status(root), ensure_ascii=False))
            return 0
        if flag == "--auth-start":
            if len(args) not in (2, 3):
                raise AuthError("用法: ava --auth-start <provider> [<mode>]")
            mode = args[2] if len(args) == 3 else None
            print(json.dumps(start_auth_flow(args[1], mode, root=root), ensure_ascii=False))
            return 0
        if flag == "--auth-complete":
            if len(args) not in (2, 3):
                raise AuthError("用法: ava --auth-complete <provider> [<json>|--stdin]")
            if len(args) == 3 and args[2] != "--stdin":
                raw = args[2]
            else:
                raw = stdin_reader() if stdin_reader is not None else sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
            if not isinstance(payload, dict):
                raise AuthError("auth-complete 载荷须是 JSON 对象")
            print(json.dumps(complete_auth_flow(args[1], payload, root=root), ensure_ascii=False))
            return 0
        if flag == "--auth-logout":
            if len(args) != 2:
                raise AuthError("用法: ava --auth-logout <provider>")
            print(json.dumps(logout_provider(args[1], root=root), ensure_ascii=False))
            return 0
    except (AuthError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return None
