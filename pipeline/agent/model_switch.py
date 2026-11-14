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

            raise LLMError(f"认证凭据不可用或续期失败: {exc}", retryable=True) from exc
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


def _fetch_openai_models(url: str, headers: dict[str, str], *, root: Path | None = None) -> tuple[list[str] | None, ModelSwitchError | None]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=MODELS_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
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
    return [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")], None


def list_models(cfg: LLMConfig, *, root: Path | None = None) -> list[str]:
    """GET `{base_url}/models` 并合并已登录服务商模型。代理失败但已登录服务商有模型时照常返回。"""
    from pipeline.agent import auth

    auth_models = auth.list_authenticated_models(root)
    url = cfg.base_url.rstrip("/") + "/models"
    proxy_ids, proxy_err = _fetch_openai_models(
        url, {"Authorization": f"Bearer {cfg.api_key}"}, root=root,
    )
    if proxy_ids is not None:
        return sorted(dict.fromkeys([*proxy_ids, *auth_models]))
    if auth_models:
        return auth_models
    raise proxy_err or ModelSwitchError("取模型列表失败")


def list_models_unauthenticated(root: Path | None = None) -> list[str]:
    """D153/D170：无会话进程时合并代理 `/models` 与已登录服务商模型（仅二者全空/失败才抛）。"""
    from pipeline.agent import auth
    from pipeline.agent.llm import configured_base_url

    auth_models = auth.list_authenticated_models(root)
    base_url = configured_base_url(root)
    if base_url is None:
        if auth_models:
            return auth_models
        raise ModelSwitchError("未能从 config/agent*.json 读取 base_url，取不了模型列表")
    proxy_ids, proxy_err = _fetch_openai_models(base_url.rstrip("/") + "/models", {}, root=root)
    if proxy_ids is not None:
        return sorted(dict.fromkeys([*proxy_ids, *auth_models]))
    if auth_models:
        return auth_models
    raise proxy_err or ModelSwitchError("取模型列表失败")


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
    last_warn = f"模型 {name} 不在代理列表{hint}——已照常切换，名字打错的话用 /model 换"
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
    if bare.startswith(("gemini-2.5", "gemini-3")):
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
    """D168/D170：供 PROBE_MODEL_LIST 输出结构化 JSON（模型列表 + 每模型支持集 + 当前默认模型）。"""
    from pipeline.agent import auth
    from pipeline.agent.llm import configured_model_name

    items = list_models_unauthenticated(root)
    active_model = configured_model_name(root)
    if active_model is None:
        auth_cfg = auth.first_authenticated_config(root)
        active_model = str(auth_cfg["model"]) if auth_cfg else None
    return {
        "items": items,
        "efforts": list(efforts_for_model(active_model, root)),
        "model_efforts": efforts_map_for_models(items, root),
        "active_model": active_model,
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
        last_warn = f"模型 {model or '默认'} 的推理强度只支持 {supported}——已降为 {clamped}"
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
        if not arg:
            available = models_fetcher(cfg)
            tag = "本会话设置" if info["model_source"] == "session" else "默认配置"
            lines = [f"当前模型：{info['model']}（{tag}）", "代理提供的模型："]
            lines += [f"  {'●' if m == info['model'] else '○'} {m}" for m in available]
            lines.append("切换：/model <名>；恢复默认：/model default（只影响本会话）")
            return override, "\n".join(lines)
        if arg == RESET_WORD:
            new = replace(override, model=None)
            return new, f"已恢复默认模型：{describe(cfg, new, purpose)['model']}（本会话）"
        name = check_model(arg, models_fetcher(cfg))
        new = replace(override, model=name)
        text = f"本会话已切换到 {name}（下一轮起生效；新会话仍用默认模型）"
        if last_warn:
            text += f"\n{last_warn}"
        return new, text
    if cmd == "/thinking":
        if not arg:
            levels = " / ".join(f"{e}（{EFFORT_LABELS[e]}）" for e in supported_efforts(info["model"]))
            return override, (
                f"当前推理强度：{_effort_text(info['reasoning_effort'], info['effort_source'])}\n"
                f"可选：{levels}\n切换：/thinking <档>；恢复默认：/thinking default（只影响本会话）"
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
