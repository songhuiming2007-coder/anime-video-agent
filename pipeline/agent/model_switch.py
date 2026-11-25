"""会话内切换模型与推理强度（D91）：`/model`、`/thinking`。

**只对当前会话**（人裁决 2026-10-13）：默认值仍是 `config/agent.local.json`，本模块不写它。
会话的设置按 sid 落在会话记录目录的 `_agent/session-models.json`（同 session_titles：不往
session.jsonl 塞元数据，读改写持 fcntl 锁、原子写），所以重开同一个会话、桌面端重启进程之后还在。

推理强度走请求体 `reasoning_effort`（OpenAI Chat Completions 字段）。本机 CLIProxyAPI 把它译成各家
的思考参数（`internal/thinking/apply.go`）；2026-10-13 实测 gemini-3.8-flash-high 与 kimi-k3 上
none / low / high 的推理 token 数都随之变化，模型名里的 `-high` 只是默认值。
"""

from __future__ import annotations

import contextlib
import difflib
import fcntl
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pipeline.agent.llm import LLMConfig

# CLIProxyAPI 认的档位（apply.go：none → 关，auto → 动态，其余为档位名）
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "auto")
# D154/D168：档位排序（none 当 off，含 max 供 Claude 4.6/5.5 等预算型模型；auto 置 xhigh 后供 Gemini/CPA 动态思考，clamp_effort 在支持集内按序就近回落）。
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "auto", "max")
EFFORT_LABELS = {
    "none": "关", "minimal": "极低", "low": "低", "medium": "中",
    "high": "高", "xhigh": "超高", "auto": "自动", "max": "最大",
}
RESET_WORD = "default"
MODELS_TIMEOUT_S = 10
PROBE_MODELS_TIMEOUT_S = 6.5

FILE_NAME = "session-models.json"
LOCK_NAME = "session-models.lock"


class ModelSwitchError(ValueError):
    """命令参数不合法 / 列表取不到：原文回给人，不改任何设置。"""


@dataclass(frozen=True)
class Override:
    model: str | None = None
    reasoning_effort: str | None = None

    @property
    def empty(self) -> bool:
        return self.model is None and self.reasoning_effort is None


def effective_config(cfg: LLMConfig | None, override: Override, *, root: Path | None = None) -> LLMConfig | None:
    """默认配置 + 本会话设置 → 实际使用的配置。

    换模型时把分档表清空：推理档与轻量档都用这个模型（会话里说「用 kimi-k3」就是全用它，
    不让起标题、压缩之类的轻量请求悄悄留在旧模型上）。
    D170：有效模型带已登录 `<provider>/` 前缀时，自动路由至该服务商的 base_url / api_key / api_protocol / extra_headers。
    """
    if cfg is None:
        return None
    out = cfg
    if not override.empty:
        if override.model is not None:
            out = replace(out, model=override.model, models={})
        if override.reasoning_effort is not None:
            out = replace(out, reasoning_effort=override.reasoning_effort)
    if "/" in out.model:
        from pipeline.agent import auth

        try:
            resolved = auth.resolve_model_provider(out.model, root=root)
        except auth.AuthError as exc:
            from pipeline.agent.llm import LLMError

            raw_pid = out.model.split("/", 1)[0]
            spec = auth.PROVIDER_MAP.get(raw_pid)
            pid_hint = spec.id if spec is not None else raw_pid
            raise LLMError(
                f"认证凭据不可用或续期失败: {exc}（请执行 /login {pid_hint} 重新登录）",
                retryable=False,
            ) from exc
        if resolved is not None:
            _pid, wire_model, cred = resolved
            out = replace(
                out,
                base_url=str(cred["base_url"]),
                model=wire_model,
                api_key=str(cred.get("api_key") or ""),
                models={},
                api_protocol=str(cred.get("api_protocol") or "openai-completions"),
                extra_headers=dict(cred.get("extra_headers") or {}),
                extra_config=dict(cred.get("extra_config") or {}),
                auth_type=cred.get("auth_type"),
            )
    return out


def describe(cfg: LLMConfig | None, override: Override, purpose: str | None) -> dict[str, Any]:
    """给界面与命令回显用：实际模型、推理档、各自来源（session / default）。"""
    if cfg is None:
        return {"model": None, "reasoning_effort": None, "model_source": "default", "effort_source": "default"}
    eff = replace(cfg, model=override.model, models={}) if override.model is not None else cfg
    if override.reasoning_effort is not None:
        eff = replace(eff, reasoning_effort=override.reasoning_effort)
    return {
        "model": eff.model_for(purpose),
        "reasoning_effort": eff.reasoning_effort,
        "model_source": "session" if override.model is not None else "default",
        "effort_source": "session" if override.reasoning_effort is not None else "default",
    }


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise ModelSwitchError(f"取模型列表拒绝 HTTP {code} 重定向，以防 Authorization 凭据泄露")


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_ORIG_URLOPEN = urllib.request.urlopen


def _fetch_openai_model_entries(
    url: str,
    headers: dict[str, str],
    *,
    root: Path | None = None,
    timeout: float | None = None,
) -> tuple[list[dict[str, Any]] | None, ModelSwitchError | None]:
    """GET `{url}` 并保留模型条目的 id 与 owned_by 来源元信息（禁止 3xx 重定向转发 Authorization）。"""
    request = urllib.request.Request(url, headers=headers)
    eff_timeout = MODELS_TIMEOUT_S if timeout is None else timeout
    try:
        opener = _NO_REDIRECT_OPENER.open if urllib.request.urlopen is _ORIG_URLOPEN else urllib.request.urlopen
        with opener(request, timeout=eff_timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except ModelSwitchError as exc:
        return None, exc
    except urllib.error.HTTPError as exc:
        return None, ModelSwitchError(f"取模型列表失败：HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, ModelSwitchError(f"取模型列表失败：{exc}")
    except json.JSONDecodeError as exc:
        return None, ModelSwitchError(f"模型列表不是 JSON：{exc}")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None, ModelSwitchError("模型列表格式不对（缺 data 数组）")
    _maybe_cache_models_metadata(data, root=root)
    entries: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mid = str(item["id"]).strip()
        if not mid:
            continue
        owned_raw = item.get("owned_by")
        owned_by = str(owned_raw).strip() if isinstance(owned_raw, str) and owned_raw.strip() else None
        entries.append({"id": mid, "owned_by": owned_by})
    return entries, None


_GENERIC_OWNED_BY = frozenset({"", "system", "organization-owner", "user"})


def _proxy_source_meta(
    model_id: str,
    *,
    base_url: str | None = None,
    owned_by: str | None = None,
) -> dict[str, Any]:
    """为来自默认配置 / 本机代理 `/models` 的模型构造可读来源元信息。"""
    from pipeline.agent import auth

    host = auth._url_host(base_url)
    is_local_proxy = bool(
        host
        and (
            host in ("127.0.0.1", "localhost", "[::1]")
            or host.startswith(("127.0.0.1:", "localhost:", "[::1]:"))
        )
    )
    base_label = "默认代理" if is_local_proxy else (f"默认配置 ({host})" if host else "默认配置")
    channel: str | None = None
    display_model = model_id
    if "/" in model_id:
        prefix, rest = model_id.split("/", 1)
        channel = auth._format_channel_label(prefix)
        source_id = f"default:{prefix.strip().lower()}"
        source_name = f"{channel} ({base_label})"
        if rest:
            display_model = rest
    elif owned_by and owned_by.strip().lower() not in _GENERIC_OWNED_BY and (
        is_local_proxy or owned_by.strip().lower() != "openai"
    ):
        channel = auth._format_channel_label(owned_by)
        source_id = f"default:{owned_by.strip().lower()}"
        source_name = f"{channel} ({base_label})"
    else:
        source_id = "default"
        source_name = base_label
    meta: dict[str, Any] = {
        "source_id": source_id,
        "source_name": source_name,
        "source_kind": "proxy" if is_local_proxy else "config",
        "wire_model": model_id,
        "display_model": display_model,
    }
    if channel:
        meta["channel"] = channel
    if owned_by:
        meta["owned_by"] = owned_by
    if host:
        meta["endpoint"] = host
    return meta


def _match_auth_source(mid: str, auth_sources: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """按完整名或服务商兼容别名匹配已认证服务商元信息（与 `resolve_model_provider` 路由口径一致）。"""
    if mid in auth_sources:
        return auth_sources[mid]
    if "/" not in mid:
        return None
    from pipeline.agent import auth

    pid, wire = mid.split("/", 1)
    spec = auth.PROVIDER_MAP.get(pid)
    if spec is None or not wire.strip():
        return None
    return auth_sources.get(f"{spec.id}/{wire.strip()}")


def _merge_proxy_and_auth_models(
    proxy_entries: list[dict[str, Any]] | None,
    proxy_err: ModelSwitchError | None,
    *,
    base_url: str | None,
    root: Path | None = None,
    refresh_auth: bool = False,
    auth_sources: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """合并代理 `/models` 与已登录服务商模型；已登录服务商路由覆盖同名来源元信息。"""
    from pipeline.agent import auth

    if auth_sources is None:
        auth_sources = auth.list_authenticated_model_sources(root, refresh=refresh_auth)
    auth_models = list(auth_sources.keys())
    if proxy_entries is not None:
        sources: dict[str, dict[str, Any]] = {}
        proxy_ids: list[str] = []
        for entry in proxy_entries:
            mid = str(entry["id"])
            proxy_ids.append(mid)
            if mid not in sources:
                matched = _match_auth_source(mid, auth_sources)
                sources[mid] = matched if matched is not None else _proxy_source_meta(
                    mid, base_url=base_url, owned_by=entry.get("owned_by"),
                )
        sources.update(auth_sources)
        items = sorted(dict.fromkeys([*proxy_ids, *auth_models]))
        return items, {m: sources[m] for m in items if m in sources}
    if auth_models:
        return auth_models, auth_sources
    raise proxy_err or ModelSwitchError("取模型列表失败")


def list_models_with_sources(cfg: LLMConfig, *, root: Path | None = None) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """GET `{base_url}/models` 并发合并已登录服务商模型，同时返回每个模型的来源映射。"""
    from pipeline.agent import auth

    url = cfg.base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        proxy_fut = pool.submit(_fetch_openai_model_entries, url, headers, root=root)
        auth_fut = pool.submit(auth.list_authenticated_model_sources, root, refresh=True)
        proxy_entries, proxy_err = proxy_fut.result()
        auth_sources = auth_fut.result()
    return _merge_proxy_and_auth_models(
        proxy_entries, proxy_err, base_url=cfg.base_url, root=root, auth_sources=auth_sources
    )


def list_models(cfg: LLMConfig, *, root: Path | None = None) -> list[str]:
    """GET `{base_url}/models` 并合并已登录服务商模型。代理失败但已登录服务商有模型时照常返回。"""
    items, _ = list_models_with_sources(cfg, root=root)
    return items


_ORIGINAL_LIST_MODELS = list_models


def list_models_unauthenticated_with_sources(
    root: Path | None = None,
    *,
    proxy_timeout: float | None = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """D153/D170：无会话进程时并发合并代理 `/models` 与已登录服务商模型及来源元信息。"""
    from pipeline.agent import auth
    from pipeline.agent.llm import configured_base_url

    base_url = configured_base_url(root)
    if base_url is None:
        auth_sources = auth.list_authenticated_model_sources(root, refresh=True)
        if auth_sources:
            return list(auth_sources.keys()), auth_sources
        raise ModelSwitchError("未能从 config/agent*.json 读取 base_url，取不了模型列表")
    proxy_url = base_url.rstrip("/") + "/models"
    with ThreadPoolExecutor(max_workers=2) as pool:
        proxy_fut = pool.submit(
            _fetch_openai_model_entries, proxy_url, {}, root=root, timeout=proxy_timeout
        )
        auth_fut = pool.submit(auth.list_authenticated_model_sources, root, refresh=True)
        proxy_entries, proxy_err = proxy_fut.result()
        auth_sources = auth_fut.result()
    return _merge_proxy_and_auth_models(
        proxy_entries, proxy_err, base_url=base_url, root=root, auth_sources=auth_sources
    )


def list_models_unauthenticated(root: Path | None = None) -> list[str]:
    """D153/D170：无会话进程时合并代理 `/models` 与已登录服务商模型（仅二者全空/失败才抛）。"""
    items, _ = list_models_unauthenticated_with_sources(root)
    return items


def describe_model_source(
    model: str | None,
    *,
    base_url: str | None = None,
    root: Path | None = None,
    auth_sources: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """为单个模型名推导来源元信息（优先查已登录/环境配置的服务商路由，其次按默认配置/代理通道归类）。"""
    if not model or not model.strip():
        return None
    mid = model.strip()
    if auth_sources is None:
        from pipeline.agent import auth

        auth_sources = auth.list_authenticated_model_sources(root)
    matched = _match_auth_source(mid, auth_sources)
    if matched is not None:
        return matched
    return _proxy_source_meta(mid, base_url=base_url)


def model_sources_for_items(
    items: list[str] | tuple[str, ...],
    *,
    base_url: str | None = None,
    known: dict[str, dict[str, Any]] | None = None,
    root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """为任意模型列表补全 `{model_id: source_meta}`，供 CLI `/model` 分组展示。"""
    from pipeline.agent import auth

    auth_sources = auth.list_authenticated_model_sources(root) if not known or len(known) < len(items) else None
    out: dict[str, dict[str, Any]] = {}
    for m in items:
        if known and m in known:
            out[m] = known[m]
            continue
        meta = describe_model_source(m, base_url=base_url, root=root, auth_sources=auth_sources)
        if meta is not None:
            out[m] = meta
    return out


#: ``check_model`` 放行未知模型时留下的提醒；每次调用先清回 None（同进程一次只跑一条命令，
#: 协议层执行 ``run_command`` 的线程与读这个值的线程是同一个）
last_warn: str | None = None


def check_model(name: str, available: list[str]) -> str:
    """D148：格式非法（空 / 含空白或控制字符）照旧拒；不在代理列表不再拒——返回名字照常切，
    提醒留在 ``last_warn`` 由调用方放进回执（代理列表落后于新模型，人打字出错靠 warn 就够，不拦死）。"""
    global last_warn
    last_warn = None
    name = name.strip()
    if not name or any(c.isspace() or ord(c) < 0x20 for c in name):
        raise ModelSwitchError(f"模型名 {name!r} 不合法（空或含空白/控制字符）")
    if name in available:
        return name
    close = difflib.get_close_matches(name, available, n=3, cutoff=0.4)
    hint = f"；相近：{'、'.join(close)}" if close else ""
    last_warn = f"模型 {name} 不在代理列表或已连服务商中{hint}——已照常切换，名字打错的话用 /model 换"
    return name


def _window_entry(model: str | None, root: Path | None = None):
    """compact.json 里该模型的 ModelWindow；表没记 / 配置坏了 → None。"""
    try:
        from pipeline.agent.compact import CompactConfigError, load_compact_config
        return load_compact_config(root).window_for(model or "")
    except (CompactConfigError, OSError, json.JSONDecodeError, KeyError, AttributeError):
        return None


def validate_effort(value: str | None) -> str | None:
    """D168：规范化推理强度：None / "" / "default" → None；"off" → "none"；非法名抛 ModelSwitchError。"""
    if value is None:
        return None
    v = value.strip().lower()
    if not v or v == RESET_WORD:
        return None
    if v == "off":
        v = "none"
    if v not in EFFORT_ORDER:
        raise ModelSwitchError(f"推理强度只能是 {' / '.join(EFFORT_ORDER)}（或 {RESET_WORD} 恢复默认），收到 {value!r}")
    return v


def models_cache_path(root: Path | None = None) -> Path:
    """D168：AVA 本地动态模型元数据缓存路径（可用 AVA_MODELS_CACHE_PATH 覆盖以便测试隔离）。"""
    env = os.environ.get("AVA_MODELS_CACHE_PATH", "").strip()
    if env:
        return Path(env)
    from pipeline import paths
    return Path(root or paths.ROOT) / "config" / "agent" / "models.local.json"


def cache_models_metadata(data: list[Any], root: Path | None = None) -> None:
    """将 /models 返回中带 thinkingLevelMap / efforts 的元数据写入 AVA 本地缓存。"""
    entries: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mid = str(item["id"])
        rec: dict[str, Any] = {}
        if isinstance(item.get("thinkingLevelMap"), dict) and item["thinkingLevelMap"]:
            rec["thinkingLevelMap"] = item["thinkingLevelMap"]
        if isinstance(item.get("efforts"), list):
            rec["efforts"] = item["efforts"]
        if rec:
            entries[mid] = rec
    if not entries:
        return
    p = models_cache_path(root)
    try:
        from pipeline import paths
        p.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if p.is_file():
            try:
                old = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(old, dict):
                    existing = old
            except (OSError, ValueError):
                existing = {}
        merged = {**existing, **entries}
        paths.atomic_write(p, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return


def _maybe_cache_models_metadata(data: list[Any], root: Path | None = None) -> None:
    # shortcut: pytest 常以打桩调 discover_models 且不传 tmp_path root，为免污染真实仓 config/agent/models.local.json，
    # 测试默认 root=None 下跳过落盘；显式传 root 的缓存单测仍走真实写盘。
    if root is None and not os.environ.get("AVA_MODELS_CACHE_PATH", "").strip() and "pytest" in sys.modules:
        return
    cache_models_metadata(data, root=root)


def _extract_efforts_from_entry(entry: dict[str, Any]) -> tuple[str, ...] | None:
    raw_efforts = entry.get("efforts")
    if isinstance(raw_efforts, list):
        mapped: list[str] = []
        for item in raw_efforts:
            if not isinstance(item, str):
                continue
            nk = "none" if item.strip().lower() == "off" else item.strip().lower()
            if nk in EFFORT_ORDER and nk not in mapped:
                mapped.append(nk)
        if raw_efforts == []:
            return ()
        if mapped:
            return tuple(lv for lv in EFFORT_ORDER if lv in mapped) or None
    tmap = entry.get("thinkingLevelMap")
    if not isinstance(tmap, dict) or not tmap:
        return None
    mapped_t: list[str] = []
    for k in tmap:
        if not isinstance(k, str):
            continue
        nk = "none" if k.strip().lower() == "off" else k.strip().lower()
        if nk in EFFORT_ORDER and nk not in mapped_t:
            mapped_t.append(nk)
    if not mapped_t:
        return None
    return tuple(lv for lv in EFFORT_ORDER if lv in mapped_t) or None


def _candidate_model_names(model: str) -> list[str]:
    """为 `<provider>/<vendor>/<model>` 依次生成匹配候选（含去服务商前缀及裸模型名）。"""
    out = [model]
    if "/" in model:
        first, rest = model.split("/", 1)
        if rest and rest not in out:
            out.append(rest)
        if "/" in rest:
            bare = rest.split("/", 1)[1]
            if bare and bare not in out:
                out.append(bare)
        elif first:
            pass
    return out


def _cached_model_efforts(model: str | None, root: Path | None = None) -> tuple[str, ...] | None:
    """第二级解析：从 AVA 本地 models.local.json 缓存读取 efforts / thinkingLevelMap。"""
    if not model:
        return None
    p = models_cache_path(root)
    try:
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = _candidate_model_names(model)
    entry: dict[str, Any] | None = None
    candidates: list[Any] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("models"), list):
            candidates = raw["models"]
        elif isinstance(raw.get("data"), list):
            candidates = raw["data"]
        else:
            for k in names:
                if isinstance(raw.get(k), dict):
                    entry = raw[k]
                    break
    elif isinstance(raw, list):
        candidates = raw
    if entry is None:
        for item in candidates:
            if isinstance(item, dict) and item.get("id") in names:
                entry = item
                break
    if not isinstance(entry, dict):
        return None
    return _extract_efforts_from_entry(entry)


def _family_heuristic_efforts(model: str | None) -> tuple[str, ...]:
    """第三级解析：按模型家族推断合理档位；未识别模型回落 EFFORTS 全档（绝不锁死）。"""
    if not model:
        return EFFORTS
    low = model.strip().lower()
    bare = low.split("/")[-1] if "/" in low else low
    if any(
        p in bare
        for p in (
            "claude-opus-4-6",
            "claude-opus-4.6",
            "claude-opus-5",
            "claude-sonnet-4-6",
            "claude-sonnet-4.6",
            "claude-sonnet-5",
        )
    ):
        return ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    if bare.startswith("claude-"):
        return ("none", "minimal", "low", "medium", "high", "xhigh")
    if bare.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")) or "codex" in bare:
        return ("none", "minimal", "low", "medium", "high", "xhigh")
    if bare.startswith(("gemini-2.5", "gemini-3", "gemini-4")):
        return ("none", "minimal", "low", "medium", "high")
    if bare.startswith("deepseek"):
        return ("none", "low", "medium", "high")
    return EFFORTS


def efforts_for_model(model: str | None, root: Path | None = None) -> tuple[str, ...]:
    """D168 三级推理支持集解析（完全独立于外部 CLI，零外部路径依赖）：
    1. config/agent/compact.json 显式 effort_map / efforts；
    2. AVA 本地缓存 config/agent/models.local.json 的 efforts / thinkingLevelMap；
    3. 模型家族启发式与 EFFORTS 默认全档（未入探针表不再锁死）。
    """
    if model:
        for candidate in _candidate_model_names(model):
            entry = _window_entry(candidate, root)
            supported = entry.supported_efforts() if entry is not None else None
            if supported is not None:
                return supported
    cached = _cached_model_efforts(model, root)
    if cached is not None:
        return cached
    return _family_heuristic_efforts(model)


def supported_efforts(model: str | None) -> tuple[str, ...]:
    """兼容旧入口：委托 `efforts_for_model(model)`。"""
    return efforts_for_model(model)


def efforts_map_for_models(items: list[str] | tuple[str, ...], root: Path | None = None) -> dict[str, list[str]]:
    """D168：为候选模型列表批量计算每个模型的推理支持集。"""
    return {m: list(efforts_for_model(m, root)) for m in items}


def probe_models_and_efforts_payload(root: Path | None = None) -> dict[str, Any]:
    """D168/D170：供 PROBE_MODEL_LIST 输出结构化 JSON（模型列表 + 来源元信息 + 每模型支持集 + 当前默认模型）。"""
    from pipeline.agent import auth
    from pipeline.agent.llm import configured_base_url, configured_model_name

    items, model_sources = list_models_unauthenticated_with_sources(
        root, proxy_timeout=PROBE_MODELS_TIMEOUT_S
    )
    active_model = configured_model_name(root)
    if active_model is None:
        auth_cfg = auth.first_authenticated_config(root)
        active_model = str(auth_cfg["model"]) if auth_cfg else None
    active_source = (
        model_sources.get(active_model)
        if active_model
        else None
    ) or describe_model_source(active_model, base_url=configured_base_url(root), root=root)
    return {
        "items": items,
        "efforts": list(efforts_for_model(active_model, root)),
        "model_efforts": efforts_map_for_models(items, root),
        "model_sources": model_sources,
        "active_model": active_model,
        **({"active_model_source": active_source} if active_source else {}),
    }


def effort_request(model: str | None, level: str, root: Path | None = None) -> tuple[str, Any] | None:
    """D155：该模型这档实际发什么参数 → (参数名, 值)；None = 不发任何 effort 参数
    （对应模型 thinkingLevelMap 里值为 null 的档）。表没记 → 默认 reasoning_effort=level。"""
    # none 语义 = 不发档：不管哪个模型、不管有没有 effort_map，一律不带参数。
    if level == "none":
        return None
    entry = None
    if model:
        for candidate in _candidate_model_names(model):
            entry = _window_entry(candidate, root)
            if entry is not None:
                break
    if entry is None:
        return ("reasoning_effort", level)
    spec = entry.effort_spec_for(level)
    if spec.param is None:
        return None
    return (spec.param, spec.value if spec.value is not None else level)


def apply_effort_to_extra_body(
    payload: dict[str, Any],
    model: str | None,
    level: str | None,
    *,
    protocol: str = "openai-completions",
    root: Path | None = None,
) -> dict[str, Any]:
    """D168：按模型与目标协议将 Pi 推理档位写入请求体（不改动 none / default）。"""
    lvl = validate_effort(level)
    if lvl is None or lvl == "none":
        return payload
    if protocol == "openai-codex-responses":
        codex_lvl = "low" if lvl == "minimal" else ("xhigh" if lvl == "max" else lvl)
        payload["reasoning"] = {"effort": codex_lvl, "summary": "auto"}
        return payload
    if protocol == "anthropic-messages":
        from pipeline.agent.compact import THINKING_BUDGETS

        # shortcut: Anthropic 合法 thinking.type 仅 enabled/disabled；auto 不发 thinking 键交服务端默认
        if lvl == "auto":
            return payload
        budget = THINKING_BUDGETS.get(lvl, 8192)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        return payload
    req = effort_request(model, lvl, root=root)
    if req is not None:
        param, value = req
        payload[param] = value
    return payload


def clamp_effort(model: str | None, level: str) -> str:
    """照 Pi clampThinkingLevel：level 不在该模型支持集 → 先向上、再向下找最近可用档。"""
    supported = supported_efforts(model)
    if level in supported:
        return level
    if level not in EFFORT_ORDER:
        return supported[0] if supported else level
    idx = EFFORT_ORDER.index(level)
    for i in range(idx + 1, len(EFFORT_ORDER)):
        if EFFORT_ORDER[i] in supported:
            return EFFORT_ORDER[i]
    for i in range(idx - 1, -1, -1):
        if EFFORT_ORDER[i] in supported:
            return EFFORT_ORDER[i]
    return supported[0] if supported else level


def check_effort(value: str, model: str | None = None) -> str:
    """D154/D168：三分支——非法名照旧拒；不在该模型支持集 → clamp 就近回落并留 warn；
    在支持集 → 原样。支持 Pi `off` 归一成 `none`。"""
    global last_warn
    last_warn = None  # 与 check_model 同一约定：本条命令的 warn 只看本条
    raw_norm = value.strip().lower()
    if raw_norm == "off":
        raw_norm = "none"
    if raw_norm not in EFFORT_ORDER:
        raise ModelSwitchError(f"推理强度只能是 {' / '.join(EFFORT_ORDER)}（或 {RESET_WORD} 恢复默认），收到 {value!r}")
    clamped = clamp_effort(model, raw_norm)
    if clamped != raw_norm:
        supported = " / ".join(supported_efforts(model))
        verb = "已降为" if EFFORT_ORDER.index(clamped) < EFFORT_ORDER.index(raw_norm) else "已调为"
        last_warn = f"模型 {model or '默认'} 的推理强度只支持 {supported}——{verb} {clamped}"
        return clamped
    return raw_norm


# ---------------------------------------------------------------------------
# 按 sid 持久化
# ---------------------------------------------------------------------------


def store_path(log_dir: Path) -> Path:
    return Path(log_dir) / "_agent" / FILE_NAME


def _parse(raw: str) -> dict[str, dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("顶层不是对象")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _from_entry(entry: dict[str, Any] | None) -> Override:
    if not entry:
        return Override()
    model = entry.get("model")
    effort = entry.get("reasoning_effort")
    return Override(
        model=model if isinstance(model, str) and model.strip() else None,
        reasoning_effort=effort if effort in EFFORT_ORDER else None,
    )


def load(log_dir: Path | None, sid: str | None) -> tuple[Override, str | None]:
    """读回某个会话的设置：返回 (设置, 警告)。文件坏了 → 默认 + 警告（不静默当成没设过）。"""
    if log_dir is None or not sid:
        return Override(), None
    path = store_path(log_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Override(), None
    except OSError as exc:
        return Override(), f"读不了 {path.name}（{exc}），本会话按默认模型"
    try:
        return _from_entry(_parse(raw).get(sid)), None
    except (ValueError, TypeError) as exc:
        return Override(), f"{path.name} 无法解析（{exc}），本会话按默认模型"


@contextlib.contextmanager
def _lock(log_dir: Path) -> Iterator[None]:
    agent_dir = Path(log_dir) / "_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(agent_dir / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save(log_dir: Path, sid: str, override: Override) -> None:
    """持锁读改写本 sid 的条目；设置清空则删掉条目。文件坏了 → 拒写（不拿空表覆盖别的会话）。"""
    from pipeline.paths import atomic_write

    path = store_path(log_dir)
    with _lock(log_dir):
        try:
            data = _parse(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (ValueError, TypeError) as exc:
            raise ModelSwitchError(f"{path.name} 无法解析，拒绝覆盖：{exc}") from None
        if override.empty:
            if sid not in data:
                return
            data.pop(sid)
        else:
            data[sid] = {
                "model": override.model,
                "reasoning_effort": override.reasoning_effort,
                "at": datetime.now(UTC).isoformat(),
            }
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


# ---------------------------------------------------------------------------
# 命令（终端 REPL 与对话框共用）
# ---------------------------------------------------------------------------


def _effort_text(effort: str | None, source: str) -> str:
    if effort is None:
        return "模型默认（不发推理参数）"
    tag = "本会话设置" if source == "session" else "默认配置"
    return f"{effort}（{EFFORT_LABELS.get(effort, effort)}，{tag}）"


def run_command(
    line: str,
    cfg: LLMConfig | None,
    override: Override,
    purpose: str | None,
    *,
    models_fetcher=None,
) -> tuple[Override, str]:
    """解析 `/model …` / `/thinking …`：返回 (新设置, 回显文字)。参数不合法抛 ModelSwitchError。

    `cfg` 是**默认**配置（未叠本会话设置）。没有可用配置时只能看、不能换。
    """
    global last_warn
    last_warn = None   # D148：每条命令先把上一条的提醒清掉（模块级瞬时态）
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cfg is None:
        raise ModelSwitchError("LLM 不可用（缺配置或密钥），无法切换模型")
    if models_fetcher is None:
        models_fetcher = list_models   # 调用时取（测试可打桩模块属性）
    info = describe(cfg, override, purpose)
    if cmd == "/model":
        if not arg or arg.startswith("?"):
            query = arg[1:].strip().lower() if arg.startswith("?") else ""
            if models_fetcher is _ORIGINAL_LIST_MODELS:
                available, raw_sources = list_models_with_sources(cfg)
            else:
                available = models_fetcher(cfg)
                raw_sources = {}
            sources = model_sources_for_items(available, base_url=cfg.base_url, known=raw_sources)
            cur_meta = (
                sources.get(info["model"]) if info["model"] else None
            ) or describe_model_source(info["model"], base_url=cfg.base_url)
            tag = "本会话设置" if info["model_source"] == "session" else "默认配置"
            cur_src = f" · 来源：{cur_meta['source_name']}" if cur_meta else ""
            filtered = [m for m in available if not query or query in m.lower()] if query else available
            header = f"匹配「{arg[1:].strip()}」的模型（{len(filtered)} / {len(available)}）：" if query else "可用模型："
            lines = [f"当前模型：{info['model']}（{tag}{cur_src}）", header]
            grouped: dict[str, tuple[str, list[str]]] = {}
            for m in filtered:
                meta = sources.get(m, {})
                sid = str(meta.get("source_id") or "default")
                slabel = str(meta.get("source_name") or "默认配置")
                if sid not in grouped:
                    grouped[sid] = (slabel, [])
                grouped[sid][1].append(m)
            if not grouped:
                lines.append("  （无匹配模型，输入 /model 查看全部）")
            for slabel, group_items in grouped.values():
                lines.append(f"  [{slabel}]")
                for m in group_items:
                    n_eff = len(supported_efforts(m))
                    eff_tag = f"（{n_eff}档推理）" if n_eff else ""
                    lines.append(f"    {'●' if m == info['model'] else '○'} {m}{eff_tag}")
            lines.append("切换：/model <名>；搜索：/model ?<关键词>；恢复默认：/model default（只影响本会话）")
            return override, "\n".join(lines)
        if arg == RESET_WORD:
            new = replace(override, model=None)
            return new, f"已恢复默认模型：{describe(cfg, new, purpose)['model']}（本会话）"
        name = check_model(arg, models_fetcher(cfg))
        new = replace(override, model=name)
        text = f"本会话已切换到 {name}（下一轮起生效；新会话仍用默认模型）"
        if last_warn:
            text += f"\n{last_warn}"
        if override.reasoning_effort and override.reasoning_effort not in supported_efforts(name):
            nearest = clamp_effort(name, override.reasoning_effort)
            text += f"\n注意：当前推理强度 {override.reasoning_effort} 不在 {name} 支持范围内，发送时将按最近支持档 {nearest} 处理（可用 /thinking 调整）"
        return new, text
    if cmd == "/thinking":
        if not arg:
            cur_eff = info["reasoning_effort"]
            levels = " / ".join(
                f"{'●' if e == cur_eff else ''}{e}（{EFFORT_LABELS[e]}）"
                for e in supported_efforts(info["model"])
            )
            return override, (
                f"当前推理强度：{_effort_text(cur_eff, info['effort_source'])}\n"
                f"可选：{levels}\n切换：/thinking <档|off>；恢复默认：/thinking default（只影响本会话）"
            )
        if arg == RESET_WORD:
            new = replace(override, reasoning_effort=None)
            after = describe(cfg, new, purpose)
            return new, f"已恢复默认推理强度：{_effort_text(after['reasoning_effort'], after['effort_source'])}"
        effort = check_effort(arg, model=info["model"])
        new = replace(override, reasoning_effort=effort)
        text = f"本会话推理强度设为 {effort}（{EFFORT_LABELS[effort]}，下一轮起生效）"
        if last_warn:
            text += f"\n{last_warn}"
        return new, text
    raise ModelSwitchError(f"未知命令 {cmd}")


def is_command(line: str) -> bool:
    head = line.strip().split(maxsplit=1)[0] if line.strip() else ""
    return head in ("/model", "/thinking")
