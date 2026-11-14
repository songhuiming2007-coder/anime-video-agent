"""极简 OpenAI 兼容 LLM 客户端与工具调用循环（Spec §2.5, §5 PR4）。

零新依赖：只用 stdlib `urllib` POST `{base_url}/chat/completions`。代价是自己追
协议变化——接受，因为工具表 11 个、字段用量是协议的最小公约数（Y11）。

两条硬边界：
1. 密钥只从 `config/agent.json` 的 `api_key_env` 指名环境变量读，绝不落盘/打印/回传；
2. 本模块与 `pipeline.agent.web` 是仓库仅有的两条出网路径：发请求前都必须过 `assert_egress_boundary`。

配置或密钥缺失时不抛异常，降级为本地纯指示模式，且降级显式可辨（返回消息带
`degraded` 与原因）——静默换一条假回答是家规禁项。
"""

from __future__ import annotations

import base64
import configparser
import contextlib
import contextvars
import datetime
import hashlib
import hmac
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pipeline import paths
from pipeline.agent import model_switch, vision
from pipeline.agent.aging import age_tool_results
from pipeline.agent.compact import (
    COMPACT_KEY,
    STATUS_CARD_KEY,
    drop_stale_status_cards,
    estimate_tokens,
)
from pipeline.agent.tools import (
    LOOKUP_KINDS,
    ToolContext,
    assert_egress_boundary,
    build_tool_schemas,
    execute_tool,
    lookup_kind,
    scrub_restricted,
)

# D93：单次请求的超时（秒）——只用来判「连接已死」，不是给模型快慢设门槛。300 = Pi（`httpIdleTimeoutMs`）
# 与 Codex CLI（`stream_idle_timeout_ms`）的默认值；实测本机代理 + gemini-3.8-flash-high 在 58–64k 上下文的
# 健康调用最长 56 s 且随上下文增长，原先写死的 60 s 两回合连杀（2026-10-14 supercell 企划会话）。
# 代理在模型思考期间一个字节都不发，所以这一段静默整个算在超时里。配置 `request_timeout_s` 可覆盖，0 = 不设。
# 人随时可点停止，不靠这个数兜底。
DEFAULT_REQUEST_TIMEOUT_S = 300

# D93：可重试错误（超时、连不上、408/409/429/5xx）的自动重试：最多 3 次，等 2 / 4 / 8 s。
# 取 Pi 的默认（`retry.maxRetries` 3、`baseDelayMs` 2000）；Codex 是 5 次。只在工具循环里重试
# （主回合与收尾调用），标题 / 压缩 / 对抗审查保持单次。等待中点停止照常中断。
LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_S = 2.0
_sleep = time.sleep  # 测试替换点

# HTTP 状态里属于「过一会儿再试可能就好」的：与 OpenAI / Anthropic SDK、Pi 的 provider-retry 同一口径。
# 其余 4xx（400 模型名失效、401 密钥错，见 D85）重试只会白等。
_RETRYABLE_HTTP = frozenset({408, 409, 429})

# D56：最近一次 chat_complete 响应里的 `usage`（服务商按自己的分词器算的 token 数）。
# 走 ContextVar 而不是改返回值：chat_complete 有约 60 处测试替身按固定签名拦截，替身不写它，
# 读出来就是 None，调用方回落到字数口径。
_LAST_USAGE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("ava_llm_last_usage", default=None)

# 裸循环硬上限（D166）：bare=True 没有交互通道也没有判重保护，模型回复满 BARE_LOOP_CAP 条
# **或**工具执行满 BARE_LOOP_CAP 次（先到先停）直接收尾停止——防死循环最后一道。
# 交互会话的检查点弹窗已于 2026-11-11 被人裁决取消（推翻 Spec 9 §2.3 第 6 条）：打断无人值守
# 长任务，防失控已有「人中断 + 本轮判重 + D62 自动压缩」三层兜底。交互路径防失控仍**没有**
# 固定轮数硬上限（2026-09-24 用户裁决，D28）。
BARE_LOOP_CAP = 50

# 收尾指令（Spec 9 §3.5，逐字冻结；reason 用 replace 注入，不用 format——正文里有花括号）。
WRAPUP_INSTRUCTION = (
    "[系统收尾] 本轮因{reason}停止，不再执行任何工具。请只基于上文已获得的信息作答："
    "① 目前能确定的结论；② 还缺什么信息、为什么没拿到；③ 需要人做什么决定或操作。"
    "不要假装已完成未完成的步骤。"
)

# 停止原因 → 收尾指令里的中文短语。
_STOP_REASON_TEXT = {
    "interrupted": "人类中断",
    "error": "出错",
    "checkpoint_stop": "检查点停止",
    "compact_failed": "上下文压缩失败",
}

# 合成工具结果（Spec 9 §2.2.4 表，逐字）。
_SYNTH_HUMAN_VOIDED = "人审请求因人类中断作废，未执行"
_SYNTH_HUMAN_UNEXECUTED = "未执行：人类中断"
_SYNTH_UNKNOWN = "已中断：执行被打断，结果未知"
_SYNTH_CHECKPOINT = "未执行：检查点停止"
_SYNTH_WRAPUP = "未执行：收尾阶段禁止调用工具"

# 模型按用途分层（Spec 7 §2.7）：档位由调用所在的 scope 查表决定，**不看内容**。
PURPOSES = ("reasoning", "light")
SCOPE_PURPOSE: dict[str, str] = {
    "idea": "reasoning",
    "creative": "reasoning",
    "asset": "reasoning",
    "pipeline": "light",
}

# 宿主内部档位标记：只进会话史，进请求体前由 _wire_messages 一律删除。
_TIER_KEY = "_ava_tier"
# D65（§3.4，红队 F1）：收尾指令消息的内部标记——老化的边界扫描跳过它，否则收尾总结会在
# 「刚拿到的大结果」被老化、且 tool_choice=none 禁止重读的双重 degradation 下生成。
_WRAPUP_KEY = "_ava_wrapup"
# D79：回合中途送达的排队消息（照 Pi 的 steering）的内部标记——老化边界扫描跳过它，否则回合中途
# 插一条用户消息会把本回合前半段、模型正在用的工具结果当成旧回合换成占位串
STEER_KEY = "_ava_steer"
# D90：view_image 结果消息上的图片引用（路径 + sha），附图由 vision.attach_images 在请求时投影
_IMAGE_KEY = vision.IMAGE_KEY
# 进请求体前一律删除的宿主内部字段：档位 + D62 压缩标记（摘要 / 一次性重注入卡）+ D65 收尾标记 + D79 排队标记
# + D90 图片引用 + 状态卡身份键
_INTERNAL_KEYS = frozenset({_TIER_KEY, COMPACT_KEY, _WRAPUP_KEY, STEER_KEY, _IMAGE_KEY, STATUS_CARD_KEY})

# 进程级 WARN 锁存（Spec 7 §1.1 🔵-1）：键 = 配置文件路径 + 问题描述。
_WARN_LATCH: set[tuple[str, str]] = set()


def _reset_warn_latch_for_testing() -> None:
    _WARN_LATCH.clear()


def purpose_for_scope(scope: str) -> str:
    """scope → 档位。未知 scope 回落到 reasoning（风险不对称：错配弱档是事故）。"""
    return SCOPE_PURPOSE.get(scope, "reasoning")


class LLMError(RuntimeError):
    """网络/HTTP/协议层失败（领域异常，不是裸 urllib 报错）。

    `retryable`（D93）：超时、连不上、408/409/429/5xx 为真，工具循环据此自动重试；默认假。
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _warn_once(cfg_file: Path, kind: str, message: str) -> None:
    key = (str(cfg_file), kind)
    if key in _WARN_LATCH:
        return
    _WARN_LATCH.add(key)
    print(f"[WARN] {message}", file=sys.stderr)


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    models: dict[str, str] = field(default_factory=dict)
    # D91：请求体 `reasoning_effort`；None = 不发（请求体与加这个字段之前逐字节相同）
    reasoning_effort: str | None = None
    # D93：单次请求超时（秒）；None = 不设（配置写 0）
    request_timeout_s: float | None = DEFAULT_REQUEST_TIMEOUT_S
    # D170：多协议路由与额外请求头/云平台参数
    api_protocol: str = "openai-completions"
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_config: dict[str, str] = field(default_factory=dict)
    auth_type: str | None = None

    def _effective_model(self, purpose: str | None) -> str:
        raw = self.model_for(purpose)
        if self.api_protocol == "azure-openai-responses":
            dep = str(self.extra_config.get("deployment_name") or "").strip()
            if dep:
                return dep
        return raw

    def endpoint_for(self, purpose: str | None = None) -> str:
        base = self.base_url.rstrip("/")
        proto = self.api_protocol
        if proto == "anthropic-messages":
            if base.endswith("/messages"):
                return base
            return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        if proto in ("openai-codex-responses", "openai-responses"):
            if base.endswith("/responses"):
                return base
            if proto == "openai-codex-responses":
                parsed_codex = urllib.parse.urlparse(base)
                host = (parsed_codex.hostname or "").lower()
                path_part = parsed_codex.path.rstrip("/")
                if (
                    (host == "chatgpt.com" or host.endswith(".chatgpt.com"))
                    and (path_part == "/backend-api" or path_part.startswith("/backend-api/"))
                    and not path_part.endswith("/codex")
                ):
                    return f"{base}/codex/responses"
            return f"{base}/responses"
        if proto == "azure-openai-responses":
            parsed = urllib.parse.urlparse(base)
            path_part = parsed.path.rstrip("/")
            if not path_part.endswith("/responses"):
                path_part = f"{path_part}/responses"
            qs = urllib.parse.parse_qs(parsed.query)
            if "api-version" not in qs:
                api_ver = str(self.extra_config.get("api_version") or os.environ.get("AZURE_OPENAI_API_VERSION") or "v1").strip() or "v1"
                qs["api-version"] = [api_ver]
            query_str = urllib.parse.urlencode([(k, v) for k, vals in qs.items() for v in vals])
            return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path_part, "", query_str, ""))
        if proto == "google-generative-ai":
            if ":streamGenerateContent" in base or ":generateContent" in base:
                return base
            model_id = urllib.parse.quote(self.model_for(purpose), safe="")
            return f"{base}/models/{model_id}:streamGenerateContent?alt=sse"
        if proto == "google-vertex":
            if ":streamGenerateContent" in base or ":generateContent" in base:
                return base
            model_id = urllib.parse.quote(self.model_for(purpose), safe="")
            project_id = str(self.extra_config.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
            location = str(self.extra_config.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1").strip() or "us-central1"
            if project_id and "/projects/" not in base:
                return f"{base}/projects/{urllib.parse.quote(project_id, safe='')}/locations/{urllib.parse.quote(location, safe='')}/publishers/google/models/{model_id}:streamGenerateContent?alt=sse"
            return f"{base}/models/{model_id}:streamGenerateContent?alt=sse"
        if proto == "bedrock-converse-stream":
            if base.endswith("/converse") or base.endswith("/converse-stream"):
                return base
            model_id = urllib.parse.quote(self.model_for(purpose), safe="")
            return f"{base}/model/{model_id}/converse"
        if proto == "mistral-conversations":
            if base.endswith("/chat/completions"):
                return base
            return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    @property
    def endpoint(self) -> str:
        return self.endpoint_for(None)

    def model_for(self, purpose: str | None) -> str:
        """purpose=None 或该档未配置 → 回落到单一 model。"""
        if purpose is None:
            return self.model
        return self.models.get(purpose) or self.model

    @property
    def tiering_active(self) -> bool:
        """只有两档真的配成不同模型时才算分档生效。"""
        return self.model_for("reasoning") != self.model_for("light")


def _parse_models(data: dict[str, Any], cfg_file: Path) -> dict[str, str]:
    """解析 models 段（Spec 7 §2.7 回落表）：非法即**整段作废**，不半信半疑。"""
    raw = data.get("models")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn_once(cfg_file, "models-shape", "models 段不是对象，整段作废，两档都回落到 model")
        return {}
    if set(raw) - set(PURPOSES) or any(not isinstance(v, str) for v in raw.values()):
        _warn_once(
            cfg_file, "models-keys",
            f"models 段含未知键或非字符串值，整段作废，两档都回落到 model（合法键: {list(PURPOSES)}）",
        )
        return {}
    parsed: dict[str, str] = {}
    for purpose in PURPOSES:
        value = str(raw.get(purpose, "")).strip()
        if value:
            parsed[purpose] = value
    return parsed


def _load_agent_cfg(root: Path | None) -> tuple[dict[str, Any], Path, bool, Path] | None:
    """读生效的 agent 配置：`(data, cfg_file, using_local, local_cfg)`；读不到/损坏返回 None。

    local 整文件优先，其次 agent.json。四个值一起返回，是为了让调用方**只读一次**：
    取名字与取密钥必须来自同一份数据（两次读取之间配置被改会拿到互相矛盾的组合）。
    """
    cfg_dir = Path(root or paths.ROOT) / "config"
    local_cfg = cfg_dir / "agent.local.json"
    using_local = local_cfg.exists()
    cfg_file = local_cfg if using_local else (cfg_dir / "agent.json")
    if not cfg_file.exists():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data, cfg_file, using_local, local_cfg


def _env_name_of(data: dict[str, Any]) -> str | None:
    """三个字段缺一即 None——`api_key_env_name` 与 `load_llm_config` 共用这一段。"""
    base_url = str(data.get("base_url") or "").strip()
    model = str(data.get("model") or "").strip()
    env_name = str(data.get("api_key_env") or "").strip()
    if not (base_url and model and env_name):
        return None
    return env_name


def configured_base_url(root: Path | None = None) -> str | None:
    """当前生效配置的 base_url（只读配置，**不**读环境变量）。

    D153：桌面端在会话进程不存在时经 PROBE_BASE_URL 取它去直连代理
    `GET /models`——与 `api_key_env_name` 同一目的（TS 侧不出现第二份
    「local 优先」规则）。字段缺失 / 配置损坏 → None。
    """
    loaded = _load_agent_cfg(root)
    if loaded is None:
        return None
    base_url = str(loaded[0].get("base_url") or "").strip()
    return base_url or None


def configured_model_name(root: Path | None = None) -> str | None:
    """D168：当前生效配置的默认模型名（只读配置，不读环境变量与钥匙串）。"""
    loaded = _load_agent_cfg(root)
    if loaded is None:
        return None
    model = str(loaded[0].get("model") or "").strip()
    return model or None


def api_key_env_name(root: Path | None = None) -> str | None:
    """当前生效配置指名的密钥环境变量名（只读配置，**不**读环境变量）。

    Spec 10 C10-R2：桌面端从钥匙串取值前先问 core「该注入哪个名字」，TS 侧
    因此不需要第二份「local 优先」规则。`base_url` / `model` / `api_key_env`
    缺一即 None，与 `load_llm_config` 同一段选择逻辑。
    """
    loaded = _load_agent_cfg(root)
    if loaded is None:
        return None
    return _env_name_of(loaded[0])


def _load_auth_fallback_cfg(
    root: Path | None,
    *,
    reasoning_effort: str | None = None,
    request_timeout_s: float | None = DEFAULT_REQUEST_TIMEOUT_S,
) -> LLMConfig | None:
    """D170：`agent*.json` 未就绪时，回落到 `config/auth.local.json` 的首个已登录服务商。"""
    from pipeline.agent import auth

    try:
        cred = auth.first_authenticated_config(root)
    except auth.AuthError as exc:
        print(f"[WARN] 读取已登录凭据失败（已回落降级模式）：{exc}", file=sys.stderr)
        return None
    if cred is None:
        return None
    return LLMConfig(
        base_url=str(cred["base_url"]),
        model=str(cred["model"]),
        api_key=str(cred.get("api_key") or ""),
        models={},
        reasoning_effort=reasoning_effort,
        request_timeout_s=request_timeout_s,
        api_protocol=str(cred.get("api_protocol") or "openai-completions"),
        extra_headers=dict(cred.get("extra_headers") or {}),
        extra_config=dict(cred.get("extra_config") or {}),
        auth_type=cred.get("auth_type"),
    )


def load_llm_config(root: Path | None = None) -> LLMConfig | None:
    """读 config/agent.local.json（优先）或 config/agent.json + 指名环境变量；缺省时回落已登录服务商。

    无配置 / JSON 损坏 / 字段不全 / 密钥空 → None（若 auth.local.json 也未登录）。没有 models 段时行为与单模型完全一致。
    """
    loaded = _load_agent_cfg(root)
    if loaded is None:
        return _load_auth_fallback_cfg(root)
    data, cfg_file, using_local, local_cfg = loaded
    cfg_dir = cfg_file.parent
    effort = _parse_effort(data, cfg_file)
    timeout = _parse_timeout(data, cfg_file)
    base_url = str(data.get("base_url") or "").strip()
    model = str(data.get("model") or "").strip()
    env_name = _env_name_of(data)  # 与 api_key_env_name 同一段规则，但不再重读配置文件
    if env_name is None:
        return _load_auth_fallback_cfg(root, reasoning_effort=effort, request_timeout_s=timeout)
    api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        return _load_auth_fallback_cfg(root, reasoning_effort=effort, request_timeout_s=timeout)

    if using_local and "models" not in data:
        # local 是整文件取代：写在 agent.json 的 models 会被静默埋掉，必须响亮
        try:
            peer = json.loads((cfg_dir / "agent.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            peer = None
        if isinstance(peer, dict) and "models" in peer:
            _warn_once(
                local_cfg, "models-shadowed",
                "models 段写在 agent.json，但本机 agent.local.json 整文件取代它，分档未生效",
            )

    protocol = str(data.get("api_protocol") or "openai-completions").strip() or "openai-completions"
    return LLMConfig(
        base_url=base_url, model=model, api_key=api_key, models=_parse_models(data, cfg_file),
        reasoning_effort=effort,
        request_timeout_s=timeout,
        api_protocol=protocol,
    )


def _parse_timeout(data: dict[str, Any], cfg_file: Path) -> float | None:
    """D93：可选的单次请求超时（秒）。缺省 → 默认值；0 → None（不设）；不合法 → 警告一次、回落默认。"""
    raw = data.get("request_timeout_s")
    if raw is None:
        return DEFAULT_REQUEST_TIMEOUT_S
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not raw >= 0 or raw == float("inf"):
        _warn_once(
            cfg_file, "request-timeout",
            f"request_timeout_s={raw!r} 不是非负秒数（0 = 不设超时），已回落默认 {DEFAULT_REQUEST_TIMEOUT_S} s",
        )
        return DEFAULT_REQUEST_TIMEOUT_S
    return None if raw == 0 else raw


def _parse_effort(data: dict[str, Any], cfg_file: Path) -> str | None:
    """D91/D168：配置里可选的默认推理强度。`off` 归一成 `none`；不合法 → 警告一次、当没配。"""
    from pipeline.agent.model_switch import EFFORT_ORDER

    raw = data.get("reasoning_effort")
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value == "off":
        value = "none"
    if value not in EFFORT_ORDER:
        _warn_once(cfg_file, "effort", f"reasoning_effort={raw!r} 不在 {list(EFFORT_ORDER)} 里，已忽略")
        return None
    return value


def local_directive_message(scope: str, reason: str) -> dict[str, Any]:
    """降级输出：打开文件 + 打印 checklist，不假装有 LLM 在场。清单随 scope 变。"""
    if scope == "creative":
        steps = (
            "  1. 01-topic.md：番 / 类型 / 锚点 / 张力 是否填齐（张力是唯一编辑判断，不许代填）；\n"
            "  2. 02-script.draft.md：写完跑 `python -m pipeline.check_script` 至全绿；\n"
        )
    elif scope == "idea":
        steps = (
            "  1. 人工阅读 `data/library/notes/` 对应的番剧笔记，梳理候选张力与锚点；\n"
            "  2. 想好选题后运行 `ava new <名>` 创建新期并进入对话；\n"
        )
    else:
        steps = (
            f"  1. 本 scope（{scope}）的清单见 docs/WORKFLOW.md 与对应 runbook，逐条人工核对；\n"
            "  2. 机器步骤用 `ava <期> /run <命令>` 推进（先回显、按 y 执行）；\n"
        )
    return {
        "role": "assistant",
        "degraded": True,
        "degrade_reason": reason,
        "content": (
            f"[降级模式·本地纯指示] LLM 不可用（{reason}）。\n"
            f"本回合不生成内容，请人工按 {scope} scope 的清单逐条走：\n"
            f"{steps}"
            "  3. 到达人工停机点（02.5 / 03.5 / 05 / 09）必须停下等人拍板。"
        ),
    }


def _standard_message(message: dict[str, Any]) -> dict[str, Any]:
    """跨档重放前的白名单字段复制（Spec 7 §2.7 决策 7b）。"""
    clean: dict[str, Any] = {"role": message.get("role"), "content": message.get("content")}
    calls = message.get("tool_calls")
    if calls:
        clean["tool_calls"] = [
            {
                "id": call.get("id"),
                "type": call.get("type"),
                "function": {
                    "name": (call.get("function") or {}).get("name"),
                    "arguments": (call.get("function") or {}).get("arguments"),
                },
            }
            for call in calls
        ]
    return clean


def _wire_messages(messages: list[dict[str, Any]], purpose: str | None) -> list[dict[str, Any]]:
    """请求体用的消息序列（Spec 7 §2.7 决策 7b）。

    没有任何消息带宿主内部标记（档位 / D62 压缩标记 / 状态卡键）且至多一张状态卡 → **原样返回同一个列表对象**。
    压缩标记只删不改：摘要与重注入卡对模型就是普通 user 消息，模型看不见标记、也仿写不出（D62 §4.4）。
    过期状态卡只留最后一张（认 `_ava_status_card` 键，不认正文前缀）。
    P8 恒定的是 messages[0] 常驻层；状态卡是易变层，请求体在上一张卡位置起不与上一轮前缀重合。
    """
    messages = drop_stale_status_cards(messages)
    if not any(isinstance(m, dict) and not _INTERNAL_KEYS.isdisjoint(m) for m in messages):
        return messages
    wired: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            wired.append(message)
            continue
        tier = message.get(_TIER_KEY)
        clean = {k: v for k, v in message.items() if k not in _INTERNAL_KEYS}
        if tier is not None and tier != purpose:
            clean = _standard_message(clean)
        wired.append(clean)
    return wired


_PROTECTED_AUTH_HEADERS = frozenset({"authorization", "x-api-key", "x-goog-api-key", "api-key", "content-type"})


def _merge_extra_headers(headers: dict[str, str], extra_headers: dict[str, str] | None) -> dict[str, str]:
    """合并附加请求头，但禁止覆写或注入鉴权 / Content-Type 头（D170 安全底线）。"""
    if not extra_headers:
        return headers
    for k, v in extra_headers.items():
        if k.lower() in _PROTECTED_AUTH_HEADERS:
            continue
        headers[k] = v
    return headers


def _stream_error(err: Any) -> LLMError:
    """流里的 `data: {"error": …}`：按其中的状态码分类，与 HTTPError 同一套可重试口径（D93）。"""
    info = err if isinstance(err, dict) else {"message": err}
    code = info.get("code", info.get("status"))
    try:
        num = int(code)
    except (TypeError, ValueError):
        num = None
    retryable = num is not None and (num in _RETRYABLE_HTTP or num >= 500)
    return LLMError(f"LLM 流式响应报错 {code}: {str(info.get('message', info))[:200]}", retryable=retryable)


def _assemble_stream(lines: Iterator[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """D94：SSE 增量块拼回与非流式同形的 `choices[0].message`，并取末块的 `usage`。

    - `content` / `reasoning_content` 字符串累加；累加结果为空（全程 null 或只有空串）→ `None`；
      `reasoning_content` 从没出现过就不带这个键。
    - `tool_calls` 按 `index` 归并：`id` / `type` / `function.name` 取首次非空，`function.arguments` 累加；
      `index` 只用来归并、不留在结果里。键出现过但全程为 null → `None`。
    - 以上空值与键形状照 2026-10-15 探针同题非流式回复与旧会话日志（都是 `null`、调用里没有 `index`），
      夹具 tests/fixtures/llm_stream_probe/。
    - 其余 delta 字段取最后一次非 null。
    - 既没有 `[DONE]` 也没有 `finish_reason` → 断流，可重试（不拿半截回复当结果）。
    """
    text: dict[str, list[str]] = {}
    calls: dict[int, dict[str, Any]] = {}
    extra: dict[str, Any] = {}
    role: str | None = None
    usage: dict[str, Any] | None = None
    ended = False
    saw_calls = False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            ended = True
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM 流式响应行非 JSON: {exc}") from None
        if not isinstance(chunk, dict):
            raise LLMError("LLM 流式响应行不是对象")
        if chunk.get("error") is not None:
            raise _stream_error(chunk["error"])
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            continue
        if choices[0].get("finish_reason") is not None:
            ended = True
        delta = choices[0].get("delta") or {}
        for key, val in delta.items():
            if key == "role":
                role = role or val
            elif key in ("content", "reasoning_content"):
                parts = text.setdefault(key, [])
                if isinstance(val, str):
                    parts.append(val)
            elif key == "tool_calls":
                saw_calls = True
                for part in val or []:
                    idx = part.get("index", len(calls))
                    slot = calls.setdefault(idx, {"index": idx, "function": {"name": None, "arguments": ""}})
                    for k in ("id", "type"):
                        if part.get(k) and not slot.get(k):
                            slot[k] = part[k]
                    fn = part.get("function") or {}
                    if fn.get("name") and not slot["function"]["name"]:
                        slot["function"]["name"] = fn["name"]
                    if isinstance(fn.get("arguments"), str):
                        slot["function"]["arguments"] += fn["arguments"]
            elif val is not None:
                extra[key] = val
    if not ended:
        raise LLMError("LLM 流式响应中途断开（没有 [DONE] 也没有 finish_reason）", retryable=True)
    message: dict[str, Any] = {"role": role or "assistant", "content": "".join(text.get("content", [])) or None}
    if "reasoning_content" in text:
        message["reasoning_content"] = "".join(text["reasoning_content"]) or None
    if calls:
        message["tool_calls"] = [{k: v for k, v in calls[i].items() if k != "index"} for i in sorted(calls)]
    elif saw_calls:
        message["tool_calls"] = None
    for key, val in extra.items():
        message.setdefault(key, val)
    return message, usage


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return "" if content is None else str(content)


def _anthropic_content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": _content_text(content)}]
    blocks: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "image_url":
            url = str(((item.get("image_url") or {}).get("url")) or "")
            if url.startswith("data:") and ";base64," in url:
                header, b64 = url.split(";base64,", 1)
                media_type = header[5:] or "image/png"
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
                continue
        if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"):
            blocks.append({"type": "text", "text": str(item.get("text") or "")})
        elif isinstance(item, str):
            blocks.append({"type": "text", "text": item})
    return blocks or [{"type": "text", "text": ""}]


def _append_anthropic_msg(out: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if out and out[-1]["role"] == role:
        prev = out[-1]["content"]
        if isinstance(prev, str):
            out[-1]["content"] = [{"type": "text", "text": prev}, *blocks]
        else:
            prev.extend(blocks)
        return
    out.append({"role": role, "content": blocks})


_ANTHROPIC_MAX_OUTPUT_TOKENS = 64000


def _anthropic_request(
    cfg: LLMConfig,
    wired: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    purpose: str | None,
    tool_choice: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    from pipeline.agent import model_switch

    model_name = cfg.model_for(purpose)
    system_parts: list[str] = []
    anth_msgs: list[dict[str, Any]] = []
    for msg in wired:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "system":
            t = _content_text(msg.get("content"))
            if t:
                system_parts.append(t)
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            # Anthropic Messages 开启 thinking 且带工具循环时，历史 assistant 须把带签名的 thinking 块放前面原样回传
            raw_tblocks = msg.get("thinking_blocks")
            if isinstance(raw_tblocks, list) and raw_tblocks:
                for tb in raw_tblocks:
                    if not isinstance(tb, dict):
                        continue
                    tb_type = tb.get("type")
                    if tb_type == "thinking" and tb.get("signature"):
                        blocks.append({
                            "type": "thinking",
                            "thinking": str(tb.get("thinking") or ""),
                            "signature": str(tb["signature"]),
                        })
                    elif tb_type == "redacted_thinking" and tb.get("data"):
                        blocks.append({"type": "redacted_thinking", "data": str(tb["data"])})
            elif msg.get("reasoning_content") and msg.get("reasoning_signature"):
                blocks.append({
                    "type": "thinking",
                    "thinking": str(msg["reasoning_content"]),
                    "signature": str(msg["reasoning_signature"]),
                })
            txt = _content_text(msg.get("content"))
            if txt:
                blocks.append({"type": "text", "text": txt})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(call.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "input": _parse_tool_args(fn.get("arguments")),
                })
            _append_anthropic_msg(anth_msgs, "assistant", blocks or [{"type": "text", "text": ""}])
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id") or ""),
                "content": _content_text(msg.get("content")),
            }
            _append_anthropic_msg(anth_msgs, "user", [block])
        else:
            _append_anthropic_msg(anth_msgs, "user", _anthropic_content_blocks(msg.get("content")))

    payload: dict[str, Any] = {
        "model": model_name,
        "max_tokens": 16384,
        "messages": anth_msgs,
        "stream": True,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if tools:
        anth_tools = []
        for t in tools:
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                anth_tools.append({
                    "name": fn.get("name"),
                    "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                })
        if anth_tools:
            payload["tools"] = anth_tools
    if tool_choice is not None:
        if tool_choice == "auto":
            payload["tool_choice"] = {"type": "auto"}
        elif tool_choice == "required":
            payload["tool_choice"] = {"type": "any"}
        elif tool_choice == "none":
            payload["tool_choice"] = {"type": "none"}
        elif isinstance(tool_choice, str):
            payload["tool_choice"] = {"type": "tool", "name": tool_choice}
    if cfg.reasoning_effort is not None:
        model_switch.apply_effort_to_extra_body(
            payload, model_name, cfg.reasoning_effort, protocol="anthropic-messages",
        )
        thinking = payload.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            thinking = dict(thinking)
            budget = int(thinking.get("budget_tokens") or 0)
            if budget > 0:
                budget = min(budget, _ANTHROPIC_MAX_OUTPUT_TOKENS - 4096)
                thinking["budget_tokens"] = budget
                payload["max_tokens"] = min(
                    _ANTHROPIC_MAX_OUTPUT_TOKENS,
                    max(int(payload["max_tokens"]), budget + 4096),
                )
            payload["thinking"] = thinking

    is_oauth = cfg.auth_type == "oauth" or cfg.api_key.startswith("sk-ant-oat")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if is_oauth:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
        headers["anthropic-beta"] = "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14"
    else:
        headers["x-api-key"] = cfg.api_key
        headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"
    _merge_extra_headers(headers, cfg.extra_headers)
    return payload, headers


def _normalize_anthropic_usage(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    inp = int(raw.get("input_tokens") or 0) + int(raw.get("cache_read_input_tokens") or 0) + int(raw.get("cache_creation_input_tokens") or 0)
    out = int(raw.get("output_tokens") or 0)
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}


def _anthropic_json_to_message(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for block in body.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(str(block.get("text") or ""))
        elif btype == "thinking":
            t_str = str(block.get("thinking") or "")
            thinkings.append(t_str)
            tb: dict[str, Any] = {"type": "thinking", "thinking": t_str}
            if block.get("signature"):
                tb["signature"] = str(block["signature"])
            thinking_blocks.append(tb)
        elif btype == "redacted_thinking" and block.get("data"):
            thinking_blocks.append({"type": "redacted_thinking", "data": str(block["data"])})
        elif btype == "tool_use":
            calls.append({
                "id": str(block.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if calls:
        msg["tool_calls"] = calls
    return msg, _normalize_anthropic_usage(body.get("usage"))


def _assemble_anthropic_stream(lines: Iterator[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    blocks: dict[int, dict[str, Any]] = {}
    usage_raw: dict[str, Any] = {}
    ended = False
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            ev = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Anthropic 流式响应行非 JSON: {exc}") from None
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "error":
            raise _stream_error(ev.get("error") or ev)
        if etype == "message_start":
            u = (ev.get("message") or {}).get("usage")
            if isinstance(u, dict):
                usage_raw.update(u)
        elif etype == "content_block_start":
            idx = int(ev.get("index") or 0)
            blk = ev.get("content_block") or {}
            btype = blk.get("type")
            slot: dict[str, Any] = {
                "type": btype,
                "text": [str(blk.get("text") or "")] if btype == "text" and blk.get("text") else [],
                "thinking": [str(blk.get("thinking") or "")] if btype == "thinking" and blk.get("thinking") else [],
                "signature": [str(blk.get("signature") or "")] if btype == "thinking" and blk.get("signature") else [],
                "data": str(blk.get("data") or "") if btype == "redacted_thinking" else "",
                "id": blk.get("id"),
                "name": blk.get("name"),
                "input_parts": [],
                "init_input": blk.get("input") if isinstance(blk.get("input"), dict) and blk.get("input") else None,
            }
            blocks[idx] = slot
        elif etype == "content_block_delta":
            idx = int(ev.get("index") or 0)
            slot = blocks.setdefault(idx, {"type": "text", "text": [], "thinking": [], "signature": [], "input_parts": []})
            delta = ev.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                slot["text"].append(str(delta.get("text") or ""))
            elif dtype == "thinking_delta":
                slot["thinking"].append(str(delta.get("thinking") or ""))
            elif dtype == "signature_delta":
                slot.setdefault("signature", []).append(str(delta.get("signature") or ""))
            elif dtype == "input_json_delta":
                slot["input_parts"].append(str(delta.get("partial_json") or ""))
        elif etype == "message_delta":
            u = ev.get("usage")
            if isinstance(u, dict):
                usage_raw.update(u)
        elif etype == "message_stop":
            ended = True
    if not ended:
        raise LLMError("Anthropic 流式响应中途断开（缺少 message_stop）", retryable=True)
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for idx in sorted(blocks):
        blk = blocks[idx]
        btype = blk.get("type")
        if btype == "text":
            texts.extend(blk["text"])
        elif btype == "thinking":
            t_str = "".join(blk["thinking"])
            sig_str = "".join(blk.get("signature") or [])
            thinkings.append(t_str)
            tb: dict[str, Any] = {"type": "thinking", "thinking": t_str}
            if sig_str:
                tb["signature"] = sig_str
            thinking_blocks.append(tb)
        elif btype == "redacted_thinking" and blk.get("data"):
            thinking_blocks.append({"type": "redacted_thinking", "data": str(blk["data"])})
        elif btype == "tool_use":
            raw_json = "".join(blk["input_parts"])
            if not raw_json and blk.get("init_input") is not None:
                raw_json = json.dumps(blk["init_input"], ensure_ascii=False)
            calls.append({
                "id": str(blk.get("id") or ""),
                "type": "function",
                "function": {"name": str(blk.get("name") or ""), "arguments": raw_json or "{}"},
            })
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if calls:
        msg["tool_calls"] = calls
    return msg, _normalize_anthropic_usage(usage_raw) if usage_raw else None


def _codex_request(
    cfg: LLMConfig,
    wired: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    purpose: str | None,
    tool_choice: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    from pipeline.agent import model_switch

    model_name = cfg._effective_model(purpose)
    instr_parts: list[str] = []
    items: list[dict[str, Any]] = []
    for msg in wired:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "system":
            t = _content_text(msg.get("content"))
            if t:
                instr_parts.append(t)
        elif role == "assistant":
            for tb in msg.get("thinking_blocks") or []:
                if isinstance(tb, dict) and tb.get("type") == "codex_reasoning" and isinstance(tb.get("item"), dict):
                    raw_item = tb["item"]
                    replayed = {
                        k: raw_item[k]
                        for k in ("type", "id", "summary", "encrypted_content")
                        if k in raw_item and raw_item[k] is not None
                    }
                    replayed["type"] = "reasoning"
                    items.append(replayed)
            txt = _content_text(msg.get("content"))
            if txt:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": txt}],
                })
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                args = fn.get("arguments")
                items.append({
                    "type": "function_call",
                    "call_id": str(call.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False),
                })
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(msg.get("tool_call_id") or ""),
                "output": _content_text(msg.get("content")),
            })
        else:
            raw_c = msg.get("content")
            content_parts: list[dict[str, Any]] = []
            if isinstance(raw_c, list):
                for part in raw_c:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = str(((part.get("image_url") or {}).get("url")) or "")
                        if url:
                            content_parts.append({"type": "input_image", "image_url": url})
                    elif isinstance(part, dict):
                        content_parts.append({"type": "input_text", "text": str(part.get("text") or "")})
            else:
                content_parts.append({"type": "input_text", "text": _content_text(raw_c)})
            items.append({"type": "message", "role": "user", "content": content_parts})

    payload: dict[str, Any] = {
        "model": model_name,
        "instructions": "\n\n".join(instr_parts) if instr_parts else "You are AVA.",
        "input": items,
        "store": False,
        "stream": True,
    }
    if cfg.api_protocol != "azure-openai-responses":
        payload["include"] = ["reasoning.encrypted_content"]
    if tools:
        codex_tools = []
        for t in tools:
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                codex_tools.append({
                    "type": "function",
                    "name": fn.get("name"),
                    "description": fn.get("description") or "",
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                })
        if codex_tools:
            payload["tools"] = codex_tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if cfg.reasoning_effort is not None:
        model_switch.apply_effort_to_extra_body(
            payload, model_name, cfg.reasoning_effort, protocol="openai-codex-responses",
        )
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key:
        if cfg.api_protocol == "azure-openai-responses":
            headers["api-key"] = cfg.api_key
        else:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
    _merge_extra_headers(headers, cfg.extra_headers)
    return payload, headers


def _codex_reasoning_tb(item: dict[str, Any]) -> dict[str, Any] | None:
    snap = {
        k: item[k]
        for k in ("type", "id", "summary", "encrypted_content")
        if k in item and item[k] is not None
    }
    if not snap.get("encrypted_content") and not snap.get("summary") and not snap.get("id"):
        return None
    snap["type"] = "reasoning"
    return {"type": "codex_reasoning", "item": snap}


def _normalize_codex_usage(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    inp = int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0)
    out = int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)
    tot = int(raw.get("total_tokens") or (inp + out))
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": tot}


def _codex_json_to_message(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    texts.append(str(c.get("text") or ""))
        elif itype == "reasoning":
            for s in item.get("summary") or []:
                if isinstance(s, dict) and s.get("text"):
                    thinkings.append(str(s["text"]))
            tb = _codex_reasoning_tb(item)
            if tb is not None:
                thinking_blocks.append(tb)
        elif itype == "function_call":
            calls.append({
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {"name": str(item.get("name") or ""), "arguments": str(item.get("arguments") or "{}")},
            })
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if calls:
        msg["tool_calls"] = calls
    return msg, _normalize_codex_usage(body.get("usage"))


def _assemble_codex_stream(lines: Iterator[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    seen_reasoning_ids: set[str] = set()
    calls: dict[str, dict[str, Any]] = {}
    call_order: list[str] = []
    usage: dict[str, Any] | None = None
    ended = False
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            ev = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Codex 流式响应行非 JSON: {exc}") from None
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype in ("error", "response.failed"):
            err = ev.get("error") or (ev.get("response") or {}).get("error") or ev
            raise _stream_error(err)
        if etype == "response.output_text.delta":
            texts.append(str(ev.get("delta") or ""))
        elif etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            thinkings.append(str(ev.get("delta") or ""))
        elif etype == "response.output_item.added":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                key = str(item.get("id") or item.get("call_id") or len(call_order))
                if key not in calls:
                    call_order.append(key)
                    calls[key] = {
                        "id": str(item.get("call_id") or item.get("id") or key),
                        "name": str(item.get("name") or ""),
                        "parts": [str(item.get("arguments"))] if item.get("arguments") else [],
                        "done": None,
                    }
        elif etype == "response.output_item.done":
            item = ev.get("item") or {}
            itype = item.get("type")
            if itype == "function_call":
                key = str(item.get("id") or item.get("call_id") or len(call_order))
                if key not in calls:
                    call_order.append(key)
                    calls[key] = {
                        "id": str(item.get("call_id") or item.get("id") or key),
                        "name": str(item.get("name") or ""),
                        "parts": [],
                        "done": str(item["arguments"]) if item.get("arguments") is not None else None,
                    }
                else:
                    if item.get("call_id"):
                        calls[key]["id"] = str(item["call_id"])
                    if item.get("name") and not calls[key]["name"]:
                        calls[key]["name"] = str(item["name"])
                    if calls[key]["done"] is None and item.get("arguments") is not None:
                        done_args = str(item["arguments"])
                        if not calls[key]["parts"] or len(done_args) >= sum(len(p) for p in calls[key]["parts"]):
                            calls[key]["done"] = done_args
            elif itype == "reasoning":
                tb = _codex_reasoning_tb(item)
                if tb is not None:
                    rid = str(item.get("id") or "")
                    if rid:
                        seen_reasoning_ids.add(rid)
                    thinking_blocks.append(tb)
                if not thinkings:
                    for s in item.get("summary") or []:
                        if isinstance(s, dict) and s.get("text"):
                            thinkings.append(str(s["text"]))
        elif etype == "response.function_call_arguments.delta":
            key = str(ev.get("item_id") or (call_order[-1] if call_order else ""))
            if key and key in calls:
                calls[key]["parts"].append(str(ev.get("delta") or ""))
        elif etype == "response.function_call_arguments.done":
            key = str(ev.get("item_id") or (call_order[-1] if call_order else ""))
            if key and key in calls:
                calls[key]["done"] = str(ev.get("arguments") or "")
        elif etype in ("response.completed", "response.incomplete"):
            ended = True
            resp_obj = ev.get("response") or {}
            usage = _normalize_codex_usage(resp_obj.get("usage"))
            out_items = resp_obj.get("output")
            if not texts and not calls and not thinking_blocks and isinstance(out_items, list):
                return _codex_json_to_message(resp_obj)[0], usage
            if isinstance(out_items, list):
                for item in out_items:
                    if not isinstance(item, dict) or item.get("type") != "reasoning":
                        continue
                    rid = str(item.get("id") or "")
                    if rid and rid in seen_reasoning_ids:
                        continue
                    tb = _codex_reasoning_tb(item)
                    if tb is not None:
                        if rid:
                            seen_reasoning_ids.add(rid)
                        thinking_blocks.append(tb)
    if not ended:
        raise LLMError("Codex 流式响应中途断开（缺少 response.completed）", retryable=True)
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if call_order:
        msg["tool_calls"] = [
            {
                "id": calls[k]["id"],
                "type": "function",
                "function": {
                    "name": calls[k]["name"],
                    "arguments": calls[k]["done"] if calls[k]["done"] is not None else ("".join(calls[k]["parts"]) or "{}"),
                },
            }
            for k in call_order
        ]
    return msg, usage


_GOOGLE_TOKEN_CACHE: dict[tuple[str, int], tuple[str, float]] = {}
_GOOGLE_TOKEN_LOCK = threading.Lock()


def _mistral_tool_call_id(raw_id: str, cache: dict[str, str]) -> str:
    if raw_id in cache:
        return cache[raw_id]
    used = set(cache.values())
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    attempt = 0
    while True:
        seed = raw_id if attempt == 0 else f"{raw_id}#{attempt}"
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        code = "".join(alphabet[b % len(alphabet)] for b in digest[:9])
        if code not in used:
            cache[raw_id] = code
            return code
        attempt += 1


def _completions_request(
    cfg: LLMConfig,
    wired: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    purpose: str | None,
    tool_choice: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    is_mistral = cfg.api_protocol == "mistral-conversations"
    id_cache: dict[str, str] = {}
    oai_msgs: list[dict[str, Any]] = []
    for m in wired:
        if not isinstance(m, dict):
            oai_msgs.append(m)
            continue
        clean = {
            k: v
            for k, v in m.items()
            if k not in ("thinking_blocks", "reasoning_signature")
        }
        if isinstance(clean.get("tool_calls"), list):
            clean["tool_calls"] = [
                {k: v for k, v in tc.items() if k not in ("thoughtSignature", "_ava_sig_model")}
                if isinstance(tc, dict)
                else tc
                for tc in clean["tool_calls"]
            ]
        if is_mistral:
            role = str(clean.get("role") or "")
            if role == "assistant" and isinstance(clean.get("tool_calls"), list):
                clean["tool_calls"] = [
                    {**tc, "id": _mistral_tool_call_id(str(tc.get("id") or ""), id_cache)}
                    if isinstance(tc, dict)
                    else tc
                    for tc in clean["tool_calls"]
                ]
            elif role == "tool" and clean.get("tool_call_id"):
                clean["tool_call_id"] = _mistral_tool_call_id(str(clean["tool_call_id"]), id_cache)
        oai_msgs.append(clean)

    payload: dict[str, Any] = {
        "model": cfg.model_for(purpose),
        "messages": oai_msgs,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = "any" if (is_mistral and tool_choice == "required") else tool_choice
    if cfg.reasoning_effort is not None:
        # D155/D168：按协议与模型映射推理参数；reasoning_effort=none 语义 = 不发档
        model_switch.apply_effort_to_extra_body(
            payload,
            cfg.model_for(purpose),
            cfg.reasoning_effort,
            protocol="openai-completions",
        )
    # D94：一律请求流式；不支持流式的服务商回 application/json，按 Content-Type 分流解析
    payload["stream"] = True
    if not is_mistral:
        payload["stream_options"] = {"include_usage": True}
    headers: dict[str, str] = {"Content-Type": "application/json", "Authorization": f"Bearer {cfg.api_key}"}
    _merge_extra_headers(headers, cfg.extra_headers)
    return payload, headers


def _completions_json_to_message(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    usage = body.get("usage") if isinstance(body, dict) else None
    try:
        return body["choices"][0]["message"], usage if isinstance(usage, dict) else None
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 响应缺少 choices[0].message: {exc}") from None


def _clean_gemini_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_clean_gemini_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    drop_keys = {"$schema", "additionalProperties", "exclusiveMinimum", "exclusiveMaximum"}
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in drop_keys:
            continue
        out[k] = _clean_gemini_schema(v)
    return out


def _append_google_turn(contents: list[dict[str, Any]], role: str, parts: list[dict[str, Any]]) -> None:
    if not parts:
        return
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"].extend(parts)
    else:
        contents.append({"role": role, "parts": list(parts)})


def _google_vertex_bearer_token(extra_config: dict[str, str]) -> str:
    cred_path_raw = (
        str(extra_config.get("credentials_path") or "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )
    if cred_path_raw:
        cred_path = Path(cred_path_raw).expanduser()
    else:
        cred_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if not cred_path.is_file():
        raise LLMError(f"未找到 Google Vertex 凭据文件：{cred_path}")
    try:
        st = cred_path.stat()
    except OSError as exc:
        raise LLMError(f"读取 Google 凭据文件状态失败: {exc}") from None
    cache_key = (str(cred_path.resolve()), int(st.st_mtime_ns))
    now = time.time()
    with _GOOGLE_TOKEN_LOCK:
        for stale_key in [k for k, v in _GOOGLE_TOKEN_CACHE.items() if v[1] <= now]:
            _GOOGLE_TOKEN_CACHE.pop(stale_key, None)
        cached = _GOOGLE_TOKEN_CACHE.get(cache_key)
        if cached and cached[1] - now > 60:
            return cached[0]
    try:
        info = json.loads(cred_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LLMError(f"读取 Google 凭据 JSON 失败: {exc}") from None
    if not isinstance(info, dict):
        raise LLMError("Google 凭据文件格式错误")
    ctype = str(info.get("type") or "")
    token_uri = str(info.get("token_uri") or "https://oauth2.googleapis.com/token")
    if ctype == "authorized_user":
        form_data = urllib.parse.urlencode({
            "client_id": str(info.get("client_id") or ""),
            "client_secret": str(info.get("client_secret") or ""),
            "refresh_token": str(info.get("refresh_token") or ""),
            "grant_type": "refresh_token",
        }).encode("utf-8")
    elif ctype == "service_account":
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise LLMError(f"Google Vertex 服务账号签名需要 cryptography 依赖: {exc}") from None

        iat = int(now)
        header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8")).rstrip(b"=")
        claim_b64 = base64.urlsafe_b64encode(json.dumps({
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": token_uri,
            "iat": iat,
            "exp": iat + 3600,
        }).encode("utf-8")).rstrip(b"=")
        signing_input = header_b64 + b"." + claim_b64
        key = serialization.load_pem_private_key(str(info["private_key"]).encode("utf-8"), password=None)
        sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]
        assertion = (signing_input + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode("ascii")
        form_data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }).encode("utf-8")
    else:
        raise LLMError(f"不支持的 Google 凭据类型：{ctype or 'unknown'}")

    class _TokenNoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
            raise urllib.error.HTTPError(req.full_url, code, "Vertex OAuth 拒绝重定向", headers, fp)

    req = urllib.request.Request(
        token_uri,
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_TokenNoRedirect())
    try:
        with opener.open(req, timeout=15.0) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise LLMError(f"获取 Google OAuth 访问令牌失败: {exc}") from None
    access_token = str(tok.get("access_token") or "").strip()
    if not access_token:
        raise LLMError("Google OAuth 未返回 access_token")
    expires_in = float(tok.get("expires_in") or 3600)
    with _GOOGLE_TOKEN_LOCK:
        _GOOGLE_TOKEN_CACHE[cache_key] = (access_token, time.time() + expires_in)
    return access_token


def _google_request(
    cfg: LLMConfig,
    wired: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    purpose: str | None,
    tool_choice: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    for msg in wired:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "system":
            t = _content_text(msg.get("content"))
            if t:
                system_parts.append({"text": t})
        elif role == "assistant":
            model_id = cfg.model_for(purpose)
            parts: list[dict[str, Any]] = []
            for tb in msg.get("thinking_blocks") or []:
                if isinstance(tb, dict) and tb.get("type") == "thinking":
                    p: dict[str, Any] = {"thought": True, "text": str(tb.get("thinking") or "")}
                    sig_model = tb.get("_ava_sig_model")
                    if tb.get("signature") and (not sig_model or sig_model == model_id):
                        p["thoughtSignature"] = str(tb["signature"])
                    parts.append(p)
            txt = _content_text(msg.get("content"))
            if txt:
                parts.append({"text": txt})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                cid = str(call.get("id") or "")
                cname = str(fn.get("name") or "")
                if cid and cname:
                    call_names[cid] = cname
                fc_part: dict[str, Any] = {
                    "functionCall": {
                        "name": cname,
                        "args": _parse_tool_args(fn.get("arguments")),
                    }
                }
                sig_model = call.get("_ava_sig_model")
                if call.get("thoughtSignature") and (not sig_model or sig_model == model_id):
                    fc_part["thoughtSignature"] = str(call["thoughtSignature"])
                parts.append(fc_part)
            _append_google_turn(contents, "model", parts)
        elif role == "tool":
            cid = str(msg.get("tool_call_id") or "")
            cname = str(msg.get("name") or call_names.get(cid) or "tool")
            _append_google_turn(
                contents,
                "user",
                [{
                    "functionResponse": {
                        "name": cname,
                        "response": {"output": _content_text(msg.get("content"))},
                    }
                }],
            )
        else:
            raw_c = msg.get("content")
            u_parts: list[dict[str, Any]] = []
            if isinstance(raw_c, list):
                for part in raw_c:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = str(((part.get("image_url") or {}).get("url")) or "")
                        if url.startswith("data:") and ";base64," in url:
                            head, b64 = url.split(";base64,", 1)
                            u_parts.append({"inlineData": {"mimeType": head[5:] or "image/png", "data": b64}})
                    elif isinstance(part, dict) and part.get("text"):
                        u_parts.append({"text": str(part["text"])})
            else:
                u_parts.append({"text": _content_text(raw_c)})
            _append_google_turn(contents, "user", u_parts)

    payload: dict[str, Any] = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    if tools:
        decls = []
        for t in tools:
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                decls.append({
                    "name": fn.get("name"),
                    "description": fn.get("description") or "",
                    "parameters": _clean_gemini_schema(fn.get("parameters") or {"type": "object", "properties": {}}),
                })
        if decls:
            payload["tools"] = [{"functionDeclarations": decls}]
    if tool_choice is not None:
        mode_map = {"auto": "AUTO", "none": "NONE", "required": "ANY"}
        if tool_choice in mode_map:
            payload["toolConfig"] = {"functionCallingConfig": {"mode": mode_map[tool_choice]}}
        else:
            payload["toolConfig"] = {
                "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [str(tool_choice)]}
            }
    if cfg.reasoning_effort is not None:
        lvl = model_switch.validate_effort(cfg.reasoning_effort)
        if lvl and lvl != "none":
            from pipeline.agent.compact import THINKING_BUDGETS

            budget = THINKING_BUDGETS.get(lvl, 8192)
            payload["generationConfig"] = {"thinkingConfig": {"thinkingBudget": budget, "includeThoughts": True}}

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_protocol == "google-vertex" and not cfg.api_key:
        bearer = _google_vertex_bearer_token(cfg.extra_config)
        headers["Authorization"] = f"Bearer {bearer}"
    elif cfg.api_key:
        headers["x-goog-api-key"] = cfg.api_key
    _merge_extra_headers(headers, cfg.extra_headers)
    return payload, headers


def _normalize_google_usage(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    inp = int(raw.get("promptTokenCount") or 0)
    out = int(raw.get("candidatesTokenCount") or 0) + int(raw.get("thoughtsTokenCount") or 0)
    tot = int(raw.get("totalTokenCount") or (inp + out))
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": tot}


def _collect_google_candidate(
    cand: dict[str, Any],
    texts: list[str],
    thinkings: list[str],
    thinking_blocks: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> None:
    content = cand.get("content") or {}
    for part in content.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("text") is not None:
            t_str = str(part["text"])
            if part.get("thought"):
                thinkings.append(t_str)
                tb: dict[str, Any] = {"type": "thinking", "thinking": t_str}
                if part.get("thoughtSignature"):
                    tb["signature"] = str(part["thoughtSignature"])
                thinking_blocks.append(tb)
            else:
                texts.append(t_str)
        elif isinstance(part.get("functionCall"), dict):
            fc = part["functionCall"]
            idx = len(calls) + 1
            cid = str(fc.get("id") or f"call_gemini_{idx}")
            tc_item: dict[str, Any] = {
                "id": cid,
                "type": "function",
                "function": {
                    "name": str(fc.get("name") or ""),
                    "arguments": json.dumps(fc.get("args") or {}, ensure_ascii=False),
                },
            }
            if part.get("thoughtSignature"):
                tc_item["thoughtSignature"] = str(part["thoughtSignature"])
            calls.append(tc_item)


def _google_json_to_message(body: dict[str, Any] | list[Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    chunks = body if isinstance(body, list) else [body]
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    usage_raw: dict[str, Any] | None = None
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if isinstance(chunk.get("error"), dict):
            raise _stream_error(chunk["error"])
        if isinstance(chunk.get("usageMetadata"), dict):
            usage_raw = chunk["usageMetadata"]
        for cand in chunk.get("candidates") or []:
            if isinstance(cand, dict):
                _collect_google_candidate(cand, texts, thinkings, thinking_blocks, calls)
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if calls:
        msg["tool_calls"] = calls
    return msg, _normalize_google_usage(usage_raw)


def _assemble_google_stream(lines: Iterator[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    usage_raw: dict[str, Any] | None = None
    saw_event = False
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            ev = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Gemini 流式响应行非 JSON: {exc}") from None
        if not isinstance(ev, dict):
            continue
        saw_event = True
        if isinstance(ev.get("error"), dict):
            raise _stream_error(ev["error"])
        if isinstance(ev.get("usageMetadata"), dict):
            usage_raw = ev["usageMetadata"]
        for cand in ev.get("candidates") or []:
            if isinstance(cand, dict):
                _collect_google_candidate(cand, texts, thinkings, thinking_blocks, calls)
    if not saw_event:
        raise LLMError("Gemini 流式响应未收到有效数据", retryable=True)
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if calls:
        msg["tool_calls"] = calls
    return msg, _normalize_google_usage(usage_raw)


def _resolve_aws_iam_credentials(extra_config: dict[str, str]) -> tuple[str, str, str | None, str]:
    region = str(
        extra_config.get("aws_region")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    ).strip()
    ak = str(extra_config.get("aws_access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    sk = str(extra_config.get("aws_secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    tok = str(extra_config.get("aws_session_token") or os.environ.get("AWS_SESSION_TOKEN") or "").strip() or None
    if ak and sk:
        return ak, sk, tok, region
    profile = str(extra_config.get("aws_profile") or os.environ.get("AWS_PROFILE") or "default").strip()
    creds_file = Path.home() / ".aws" / "credentials"
    if creds_file.is_file():
        cp = configparser.ConfigParser()
        cp.read(creds_file, encoding="utf-8")
        if cp.has_section(profile):
            ak = cp.get(profile, "aws_access_key_id", fallback="").strip()
            sk = cp.get(profile, "aws_secret_access_key", fallback="").strip()
            tok = cp.get(profile, "aws_session_token", fallback="").strip() or None
            if ak and sk:
                return ak, sk, tok, region
    raise LLMError("Amazon Bedrock 缺少 Bearer Token 或 AWS IAM Access Key / Profile")


def _sign_aws_v4(
    endpoint: str,
    body_bytes: bytes,
    headers: dict[str, str],
    extra_config: dict[str, str],
) -> dict[str, str]:
    ak, sk, session_tok, region = _resolve_aws_iam_credentials(extra_config)
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    canonical_uri = urllib.parse.quote(parsed.path or "/", safe="/-_.~")
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now_dt.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now_dt.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body_bytes).hexdigest()

    signed_headers_map = {
        "content-type": headers.get("Content-Type", "application/json"),
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if session_tok:
        signed_headers_map["x-amz-security-token"] = session_tok

    sorted_keys = sorted(signed_headers_map)
    canonical_headers = "".join(f"{k}:{signed_headers_map[k]}\n" for k in sorted_keys)
    signed_headers_str = ";".join(sorted_keys)
    canonical_request = "\n".join([
        "POST",
        canonical_uri,
        parsed.query,
        canonical_headers,
        signed_headers_str,
        payload_hash,
    ])
    scope = f"{date_stamp}/{region}/bedrock/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    def _hmac(key_b: bytes, msg_s: str) -> bytes:
        return hmac.new(key_b, msg_s.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(f"AWS4{sk}".encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, "bedrock")
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out = dict(headers)
    out["Host"] = host
    out["X-Amz-Date"] = amz_date
    out["X-Amz-Content-Sha256"] = payload_hash
    if session_tok:
        out["X-Amz-Security-Token"] = session_tok
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers_str}, Signature={signature}"
    )
    return out


def _append_bedrock_msg(out: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
    else:
        out.append({"role": role, "content": list(blocks)})


_BEDROCK_ADAPTIVE_RE = re.compile(
    r"(?:opus-4[.-][678]|sonnet-4[.-]6|(?:opus|sonnet|haiku|fable)-5)(?:$|[-@:.])",
)
_BEDROCK_BLOCK_BINDING_RE = re.compile(
    r"(?:opus-4[.-][78]|(?:opus|sonnet|haiku|fable)-5)(?:$|[-@:.])",
)


def _bedrock_apply_reasoning(
    payload: dict[str, Any],
    model_name: str,
    reasoning_effort: str | None,
    extra_config: dict[str, str] | None = None,
) -> None:
    """将 reasoning_effort 按 Bedrock 目标模型家族映射为 additionalModelRequestFields / inferenceConfig。"""
    if not reasoning_effort:
        return
    lvl = str(reasoning_effort).strip().lower()
    if not lvl or lvl in ("none", "off"):
        return
    low = model_name.lower()
    if "claude" in low:
        is_adaptive = bool(_BEDROCK_ADAPTIVE_RE.search(low))
        if is_adaptive:
            use_binding = bool(_BEDROCK_BLOCK_BINDING_RE.search(low))
            region = str((extra_config or {}).get("aws_region") or os.environ.get("AWS_REGION") or "").lower()
            is_gov = region.startswith("us-gov-") or low.startswith(("us-gov.", "arn:aws-us-gov:"))
            effort_map = {
                "minimal": "low",
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "xhigh" if use_binding else "max",
                "max": "max",
                "auto": "medium",
            }
            thinking_cfg: dict[str, Any] = {"type": "adaptive"}
            add_fields: dict[str, Any] = {
                "thinking": thinking_cfg,
                "output_config": {"effort": effort_map.get(lvl, "medium")},
            }
            if use_binding and not is_gov:
                thinking_cfg["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
                add_fields["anthropic_beta"] = ["thinking-binding-controls-2026-08-01"]
            payload["additionalModelRequestFields"] = add_fields
            return
        from pipeline.agent.compact import THINKING_BUDGETS

        clamped = "high" if lvl in ("xhigh", "max") else ("medium" if lvl == "auto" else lvl)
        budget = int(THINKING_BUDGETS.get(clamped, 8192))
        payload["additionalModelRequestFields"] = {
            "thinking": {"type": "enabled", "budget_tokens": budget},
            "anthropic_beta": ["interleaved-thinking-2025-05-14"],
        }
        payload["inferenceConfig"] = {"maxTokens": max(16384, budget + 4096)}
        return
    if "gpt-oss" in low:
        oss_map = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
        payload["additionalModelRequestFields"] = {"reasoning_effort": oss_map.get(lvl, "medium")}
        return
    if "gpt-" in low:
        gpt_map = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
        payload["additionalModelRequestFields"] = {"reasoning": {"effort": gpt_map.get(lvl, "medium")}}


def _bedrock_request(
    cfg: LLMConfig,
    wired: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    purpose: str | None,
    tool_choice: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    model_name = cfg.model_for(purpose)
    is_claude = "claude" in model_name.lower() or "anthropic." in model_name.lower()
    system_blocks: list[dict[str, str]] = []
    bedrock_msgs: list[dict[str, Any]] = []
    for msg in wired:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "system":
            t = _content_text(msg.get("content"))
            if t:
                system_blocks.append({"text": t})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            raw_tblocks = msg.get("thinking_blocks")
            if isinstance(raw_tblocks, list) and raw_tblocks:
                for tb in raw_tblocks:
                    if not isinstance(tb, dict):
                        continue
                    tb_type = tb.get("type")
                    if tb_type == "redacted_thinking" and tb.get("data"):
                        blocks.append({"reasoningContent": {"redactedContent": str(tb["data"])}})
                    elif tb_type == "thinking":
                        t_str = str(tb.get("thinking") or "")
                        if not t_str.strip():
                            continue
                        sig = str(tb.get("signature") or "").strip()
                        if is_claude:
                            if sig:
                                blocks.append({"reasoningContent": {"reasoningText": {"text": t_str, "signature": sig}}})
                            else:
                                blocks.append({"text": t_str})
                        else:
                            blocks.append({"reasoningContent": {"reasoningText": {"text": t_str}}})
            elif msg.get("reasoning_content"):
                rc_text = str(msg["reasoning_content"])
                rc_sig = str(msg.get("reasoning_signature") or "").strip()
                if is_claude:
                    if rc_sig:
                        blocks.append({"reasoningContent": {"reasoningText": {"text": rc_text, "signature": rc_sig}}})
                    else:
                        blocks.append({"text": rc_text})
                else:
                    blocks.append({"reasoningContent": {"reasoningText": {"text": rc_text}}})
            txt = _content_text(msg.get("content"))
            if txt:
                blocks.append({"text": txt})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                blocks.append({
                    "toolUse": {
                        "toolUseId": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "input": _parse_tool_args(fn.get("arguments")),
                    }
                })
            _append_bedrock_msg(bedrock_msgs, "assistant", blocks or [{"text": ""}])
        elif role == "tool":
            _append_bedrock_msg(
                bedrock_msgs,
                "user",
                [{
                    "toolResult": {
                        "toolUseId": str(msg.get("tool_call_id") or ""),
                        "content": [{"text": _content_text(msg.get("content"))}],
                    }
                }],
            )
        else:
            _append_bedrock_msg(bedrock_msgs, "user", [{"text": _content_text(msg.get("content")) or " "}])

    payload: dict[str, Any] = {"messages": bedrock_msgs}
    if system_blocks:
        payload["system"] = system_blocks
    if tools and tool_choice != "none":
        specs = []
        for t in tools:
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                specs.append({
                    "toolSpec": {
                        "name": fn.get("name"),
                        "description": fn.get("description") or "tool",
                        "inputSchema": {"json": fn.get("parameters") or {"type": "object", "properties": {}}},
                    }
                })
        if specs:
            tc_obj: dict[str, Any] = {"tools": specs}
            if tool_choice == "auto":
                tc_obj["toolChoice"] = {"auto": {}}
            elif tool_choice == "required":
                tc_obj["toolChoice"] = {"any": {}}
            elif isinstance(tool_choice, str):
                tc_obj["toolChoice"] = {"tool": {"name": tool_choice}}
            payload["toolConfig"] = tc_obj

    _bedrock_apply_reasoning(payload, model_name, cfg.reasoning_effort, cfg.extra_config)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    _merge_extra_headers(headers, cfg.extra_headers)
    return payload, headers


def _bedrock_json_to_message(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    content_blocks = ((body.get("output") or {}).get("message") or {}).get("content") or []
    texts: list[str] = []
    thinkings: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if "text" in block and block["text"] is not None:
            texts.append(str(block["text"]))
        elif isinstance(block.get("reasoningContent"), dict):
            rc = block["reasoningContent"]
            rt_obj = rc.get("reasoningText")
            if isinstance(rt_obj, dict):
                rt = str(rt_obj.get("text") or "")
                sig = rt_obj.get("signature")
                if rt:
                    thinkings.append(rt)
                tb: dict[str, Any] = {"type": "thinking", "thinking": rt}
                if sig:
                    tb["signature"] = str(sig)
                thinking_blocks.append(tb)
            if rc.get("redactedContent"):
                thinking_blocks.append({"type": "redacted_thinking", "data": str(rc["redactedContent"])})
        elif isinstance(block.get("toolUse"), dict):
            tu = block["toolUse"]
            calls.append({
                "id": str(tu.get("toolUseId") or ""),
                "type": "function",
                "function": {
                    "name": str(tu.get("name") or ""),
                    "arguments": json.dumps(tu.get("input") or {}, ensure_ascii=False),
                },
            })
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thinkings:
        msg["reasoning_content"] = "".join(thinkings) or None
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if calls:
        msg["tool_calls"] = calls
    raw_u = body.get("usage")
    usage = None
    if isinstance(raw_u, dict):
        inp = int(raw_u.get("inputTokens") or 0)
        out = int(raw_u.get("outputTokens") or 0)
        tot = int(raw_u.get("totalTokens") or (inp + out))
        usage = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": tot}
    return msg, usage


def _bedrock_stream_unsupported(lines: Iterator[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raise LLMError("Amazon Bedrock Converse 协议当前仅支持 JSON 响应，不支持 text/event-stream 流式帧")


_PROTOCOL_ADAPTERS: dict[str, tuple[Any, Any, Any]] = {
    "openai-completions": (_completions_request, _assemble_stream, _completions_json_to_message),
    "mistral-conversations": (_completions_request, _assemble_stream, _completions_json_to_message),
    "anthropic-messages": (_anthropic_request, _assemble_anthropic_stream, _anthropic_json_to_message),
    "openai-codex-responses": (_codex_request, _assemble_codex_stream, _codex_json_to_message),
    "openai-responses": (_codex_request, _assemble_codex_stream, _codex_json_to_message),
    "azure-openai-responses": (_codex_request, _assemble_codex_stream, _codex_json_to_message),
    "google-generative-ai": (_google_request, _assemble_google_stream, _google_json_to_message),
    "google-vertex": (_google_request, _assemble_google_stream, _google_json_to_message),
    "bedrock-converse-stream": (_bedrock_request, _bedrock_stream_unsupported, _bedrock_json_to_message),
}


def _stamp_google_sig_model(msg: dict[str, Any], cfg: LLMConfig, purpose: str | None) -> dict[str, Any]:
    """为 Gemini / Vertex 响应的 thoughtSignature 标注来源模型，切模型时跳过过期签名回放。"""
    if cfg.api_protocol not in ("google-generative-ai", "google-vertex"):
        return msg
    model_id = cfg.model_for(purpose)
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict) and tc.get("thoughtSignature"):
            tc["_ava_sig_model"] = model_id
    for tb in msg.get("thinking_blocks") or []:
        if isinstance(tb, dict) and tb.get("signature"):
            tb["_ava_sig_model"] = model_id
    return msg


def chat_complete(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    config: LLMConfig | None = None,
    root: Path | None = None,
    timeout: float | None = None,
    scope: str = "creative",
    purpose: str | None = None,
    tool_choice: str | None = None,
    egress_trusted: Sequence[str] = (),
) -> dict[str, Any]:
    """发一次统一多协议 LLM 请求，返回 OpenAI 同形 `choices[0].message`。

    配置缺失 → 降级消息（不抛）；网络/HTTP/协议失败 → `LLMError`。
    `tool_choice=None`（默认）时请求体**不含该键**——与加这个参数之前逐字节相同（TL-15）。
    `egress_trusted`：出网断言的可信文本集（仓库规程正文，Spec 16 §5.2），原样交给断言。
    `timeout=None`（默认）用配置的 `request_timeout_s`（D93）；显式传值的调用方（标题、压缩、对抗审查）照旧优先。
    """
    cfg = config if config is not None else load_llm_config(root)
    if cfg is None:
        return local_directive_message(scope, "缺少 config/agent.json 或环境变量密钥")

    wired = _wire_messages(messages, purpose)
    endpoint = cfg.endpoint_for(purpose)
    build_req, parse_stream, parse_json = _PROTOCOL_ADAPTERS.get(
        cfg.api_protocol,
        (_completions_request, _assemble_stream, _completions_json_to_message),
    )
    payload, headers = build_req(cfg, wired, tools, purpose, tool_choice)

    assert_egress_boundary(endpoint, payload, trusted_texts=egress_trusted)  # 出网前最后一道断言

    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if cfg.api_protocol == "bedrock-converse-stream" and not cfg.api_key:
        headers = _sign_aws_v4(endpoint, body_bytes, headers, cfg.extra_config)

    request = urllib.request.Request(
        endpoint,
        data=body_bytes,
        headers=headers,
        method="POST",
    )
    try:
        effective = timeout if timeout is not None else cfg.request_timeout_s
        with urllib.request.urlopen(request, timeout=effective) as resp:
            resp_headers = getattr(resp, "headers", None)
            ctype = str(resp_headers.get("Content-Type", "") if resp_headers is not None else "").lower()
            if "text/event-stream" in ctype:
                line_iter = (raw.decode("utf-8", errors="replace") for raw in resp)
                message, usage = parse_stream(line_iter)
                _LAST_USAGE.set(usage)
                return _stamp_google_sig_model(message, cfg, purpose)
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", errors="replace")
        retryable = exc.code in _RETRYABLE_HTTP or exc.code >= 500
        raise LLMError(f"LLM HTTP {exc.code}: {detail}", retryable=retryable) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        # HTTPException：流读到一半连接被掐（IncompleteRead 等），与超时同属可重试
        raise LLMError(f"LLM 请求失败: {exc}", retryable=True) from None
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 响应非 JSON: {exc}") from None

    if not isinstance(body, (dict, list)) or (isinstance(body, dict) is False and cfg.api_protocol not in ("google-generative-ai", "google-vertex")):
        raise LLMError(f"{cfg.api_protocol} 响应不是 JSON 对象")
    msg, usage = parse_json(body)
    _LAST_USAGE.set(usage if isinstance(usage, dict) else None)
    return _stamp_google_sig_model(msg, cfg, purpose)


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    """工具参数是 JSON 字符串；解析失败当空参数（由实现层自己报缺参）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class Decision:
    """结构化的人审/放行决定（Spec 9 §4.1）。

    `provenance` 是判重清空规则的唯一依据：只有 `provenance == "human"` 且真的执行了，
    才清空本轮判重表（§2.3 第 5 条）。`feedback` 是协议下自由文本拒绝的回喂内容。
    """

    ok: bool
    reason: str = ""
    provenance: Literal["human", "auto", "card_free", "precheck", "voided"] = "auto"
    feedback: str | None = None
    # D81 §4.4 卡上绑定：只在人在卡上批准时带卡面所依据的状态（如 00-project.md 的 base_sha）；其余路径一律 None
    bind: dict | None = None


@contextlib.contextmanager
def _stopping(control: LoopControl) -> Iterator[None]:
    """「停止中」（§2.2 状态表）：补合成结果 / 决定收尾期间到达的中断只置标志、不抛。

    这里**只吸收、不放延迟区**：`defer()` 会把异常挡在区内，代价是中止它包住的那段语句，
    合成结果就不齐了（实测：24 个调用只写出 9 条 tool 消息）。真正的临界区是 `commit()`
    自己的延迟区（§2.2 第 3 条的延迟区闭集）——信号落在那里被接住、按吸收标志丢弃，
    循环继续跑下一个。**收尾请求不在区内**：「收尾中」到达的中断照旧要能放弃收尾（TL-9）。
    """
    interrupt = control.interrupt
    if interrupt is None:
        yield
        return
    with interrupt.absorb():  # type: ignore[union-attr]
        yield


@dataclass
class LoopControl:
    """工具循环的外部控制面（Spec 9 §4.1）。

    `llm.py` 不认识 `session.py`，只认这里的鸭子类型（import 方向固定 session → llm，TG-7）。
    """

    interrupt: Any
    commit: Callable[[dict[str, Any], str], None]
    """唯一提交入口：写盘 → 追加进内存 `messages`（Spec 9 §2.5）。

    实现必须把消息追加进 `run_tool_loop` 收到的**同一个**列表对象，循环直接拿它当上下文。
    """
    review: Callable[[str, dict[str, Any]], Decision]
    post_execute: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    fetch_executor: Callable[[int], dict[str, Any]] | None = None
    on_trace: Callable[[dict[str, Any]], None] | None = None
    on_exec_started: Callable[[str, str], None] | None = None
    critical_tools: frozenset[str] = frozenset()
    compact: Callable[[list[dict[str, Any]], int | None], dict[str, Any] | None] | None = None
    """D62 自动压缩触发点：`(messages, token 读数提示)` → None（没压）/ `{"ok": True, ...}`（已原地压缩）/
    `{"ok": False, "abort": True, "text": …}`（失败，中止回合交人）。读数提示 = 上一次请求的服务商实测
    + 其后新增消息的字数估算；拿不到实测为 None（回调自己回落估算）。"""
    drain_steer: Callable[[], list[dict[str, str]]] | None = None
    """D79 排队消息（照 Pi 的 steering）：取走回合在途期间人发来的消息 `[{id, text}]`。
    只在一批工具结果全部写史之后、下一次请求之前调用（assistant.tool_calls 之后必须先接齐 tool 消息）；
    模型给出最终答复时再调一次，非空则不收工、继续一轮。收尾调用期间不调。None = 没有排队（终端）。"""
    on_steer: Callable[[list[str], str | None], None] | None = None
    """D79：排队消息已写史（送达模型）后的通知：(送达的 id 列表, 被续上的那条答复正文)。
    第二个参数只在「模型已给最终答复、因排队而继续」时非 None——这条答复不再是回合终稿，
    协议层要先把它推上屏，否则人看不到。"""
    on_llm_retry: Callable[[int, int, float, str], None] | None = None
    """D93：模型请求遇可重试错误、即将等待重发时的通知：(第几次重试, 上限, 等几秒, 报错原文)。"""


def dedup_key(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """本轮判重键（Spec 9 §2.3 第 5 条）：工具名 + 规范化参数串。

    顶层字符串 `strip()`；**不做 URL 或语义归一**（与 Spec 6 §2.3 同口径）。参数微扰
    （URL 加 `?`、`#`）挡不住，这是裁决时已知并接受的代价（RF-15），由检查点兜底。
    """
    normalized = {
        str(key): (value.strip() if isinstance(value, str) else value)
        for key, value in (args or {}).items()
    }
    return (
        name,
        json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
    )


def _bare_review(
    approve: Callable[[str, dict[str, Any]], bool | tuple[bool, str]] | None,
) -> Callable[[str, dict[str, Any]], Decision]:
    """裸循环的 review：旧 `approve` 适配器（approve 为 None → 直接执行，与改造前相同）。"""

    def review(name: str, args: dict[str, Any]) -> Decision:
        if approve is None:
            return Decision(ok=True, provenance="auto")
        raw = approve(name, args)
        if isinstance(raw, bool):
            if raw:
                return Decision(ok=True, provenance="auto")
            return Decision(ok=False, provenance="human", reason="人类拒绝执行该工具调用")
        ok, reason = raw
        if ok:
            return Decision(ok=True, provenance="auto")
        return Decision(
            ok=False, provenance="human", reason=reason or "人类拒绝执行该工具调用"
        )

    return review


def _tool_message(call: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """工具结果进会话的唯一序列化点。受限字样先脱敏（D52）：作业输出常带
    `…/03-audio/manifest.json` 这类路径，不脱敏的话下一次请求整轮被出网断言拦下回滚。
    人看的工具帧走 `_trace`，是原始结果；assistant 的 tool_calls 参数不脱敏（双保险，ADR-0026）。
    D90：view_image 结果里的私有图片键不进正文，挪到消息内部键上（附图在请求时投影）。
    """
    outcome, image = vision.split_outcome(outcome)
    message: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": str(call.get("id", "")),
        "content": scrub_restricted(json.dumps(outcome, ensure_ascii=False)),
    }
    if image is not None:
        message[_IMAGE_KEY] = {**image, "path": str((outcome.get("result") or {}).get("path", ""))}
    return message


def _reject_outcome(decision: Decision) -> dict[str, Any]:
    """被拒时的合成工具结果（M15b：拒因**具体**回喂，不是一句固定文案）。"""
    if decision.feedback:
        return {"ok": False, "error": f"人类拒绝执行该工具调用：{decision.feedback}"}
    return {"ok": False, "error": decision.reason or "人类拒绝执行该工具调用"}


def _duplicate_outcome(name: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"重复调用：本轮已用完全相同的参数调用过 {name}（上次结果见上文）。"
            "换一条路，或基于已有信息作答。"
        ),
    }


def _trace(
    control: LoopControl,
    index: int,
    name: str,
    args: dict[str, Any],
    *,
    content: Any = None,
    duplicate: bool = False,
) -> None:
    """工具帧轨迹（§3.1 的 `tool`）：start 在每次调用之前，end 在结果提交之前。

    `summary`/`ok`/`observation` 的判定在 session 侧做一次（core 只做一次），这里只给原始材料。
    """
    if control.on_trace is None:
        return
    frame: dict[str, Any] = {"index": index, "name": name, "duplicate": duplicate}
    if content is None:
        frame["phase"] = "start"
        frame["args"] = args
    else:
        frame["phase"] = "end"
        frame["content"] = content
    control.on_trace(frame)


def _loop_result(
    convo: list[dict[str, Any]],
    *,
    final: dict[str, Any] | None = None,
    local_note: str | None = None,
    stopped: str,
    rollback: bool = False,
    llm_calls: int = 0,
    tool_calls_made: int = 0,
    tool_executions: int = 0,
    duplicates_rejected: int = 0,
    checkpoints: int = 0,
    wrapup: str = "none",
    error: str | None = None,
    prompt_chars: int = 0,
    elapsed_s: float = 0.0,
    lookups: dict[str, int] | None = None,
    prompt_tokens: int | None = None,
    compaction: dict[str, Any] | None = None,
    llm_retries: int = 0,
) -> dict[str, Any]:
    """返回契约（Spec 9 §3.4）。`iterations` 保留旧键名 = `llm_calls`。

    D62：本回合压缩过时 `rollback` 照报——回滚到哪由会话层决定（最近一次压缩点，spec §九 偏离 10）。
    """
    comp = compaction or {}
    return {
        "messages": convo,
        "final": final,
        "local_note": local_note,
        "stopped": stopped,
        "rollback": rollback,
        "iterations": llm_calls,
        "llm_calls": llm_calls,
        "tool_calls_made": tool_calls_made,
        "tool_executions": tool_executions,
        "duplicates_rejected": duplicates_rejected,
        "checkpoints": checkpoints,
        "wrapup": wrapup,
        "error": error,
        "prompt_chars": prompt_chars,
        "elapsed_s": elapsed_s,
        "lookups": dict(lookups) if lookups else _zero_lookups(),
        "prompt_tokens": prompt_tokens,
        "compacted": bool(comp.get("compacted")),
        "tokens_before": comp.get("tokens_before"),   # 本回合第一次压缩前（峰值）
        "tokens_after": comp.get("tokens_after"),     # 本回合最后一次压缩后
        "llm_retries": llm_retries,                   # D93：本回合模型请求自动重试的次数
    }


def _prompt_tokens_of(usage: dict[str, Any] | None) -> int | None:
    """`usage.prompt_tokens` 是非负整数才收（bool 不算整数）；其余一律 None。"""
    v = usage.get("prompt_tokens") if usage else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _zero_lookups() -> dict[str, int]:
    return {k: 0 for k in LOOKUP_KINDS}


def run_tool_loop(
    messages: list[dict[str, Any]],
    *,
    ctx: ToolContext | None = None,
    scope: str = "creative",
    config: LLMConfig | None = None,
    root: Path | None = None,
    approve: Callable[[str, dict[str, Any]], bool | tuple[bool, str]] | None = None,
    control: LoopControl | None = None,
    egress_trusted: Sequence[str] = (),
) -> dict[str, Any]:
    """多轮 tool_calls 状态机（Spec 9 §2.3、§4.7）。**没有固定轮数上限。**

    交互会话防失控两层：人中断、本轮判重（检查点弹窗已按 D166 人裁决取消）；
    `control is None` 的裸循环另有 `BARE_LOOP_CAP` 硬上限——没通道可问、没判重，到点直接停。
    任何非正常停止都先做一次 `tool_choice:"none"` 的无工具收尾（除非本轮一条回复都没拿到，
    或本轮被出网断言拒绝——那时收尾没有意义，直接回滚）。

    `control is None` 时是**裸循环**（仅测试与兼容用途，生产调用点必须传 `control`，TG-6）：
    不装中断处理器、不判重、有 `BARE_LOOP_CAP` 硬上限、`review` 是旧 `approve` 的适配器。
    「没有人审通道就拒绝运行」由内核（`AgentSession`）执行，不在这里（TK-5）。
    """
    context = ctx or ToolContext(scope=scope, root=root)
    # root 未显式给定时用 ctx 的（会话绑定的仓库根），否则 ctx.root 会被静默忽略
    effective_root = root if root is not None else context.root
    cfg = config if config is not None else load_llm_config(effective_root)

    if cfg is None:
        final = local_directive_message(context.scope, "缺少 config/agent.json 或环境变量密钥")
        return _loop_result(list(messages), final=final, stopped="degraded")

    tools = build_tool_schemas(effective_root)
    # 同一次工具循环的所有迭代用同一档（Spec 7 §2.7 决策 7a）
    purpose = purpose_for_scope(context.scope)

    bare = control is None
    # 裸循环照旧复制（调用方随后 clear+extend，与改造前一致）；有 control 时用**调用方的那个
    # 列表对象**：commit 的契约就是「写盘 → 追加进内存 messages」（Spec 9 §2.5）。
    convo = list(messages) if bare else messages
    if bare:
        control = LoopControl(
            interrupt=None,
            commit=lambda message, origin: convo.append(message),
            review=_bare_review(approve),
            critical_tools=frozenset(),
        )

    dedup: dict[tuple[str, str], None] = {}
    turn_tally: dict[str, int] = {}   # 本轮调用名 → 次数（本地说明用，§2.3 第 4 条）
    llm_calls = tool_calls_made = tool_executions = duplicates_rejected = checkpoints = 0
    llm_retries = 0  # D93：本回合自动重试次数（跨请求累计）
    replies_since_cp = execs_since_cp = 0
    prompt_chars = 0
    prompt_tokens: int | None = None  # D56：本回合最后一次请求的输入 token（服务商给的）；拿不到为 None
    # D62：触发读数提示的基准——最近一次请求时的实测 token 与当时的历史长度；压缩后作废
    usage_anchor: tuple[int, int] | None = None
    comp: dict[str, Any] = {"compacted": False, "tokens_before": None, "tokens_after": None}
    lookups = _zero_lookups()  # D48 ①：本回合实际执行的查证调用，按类计数
    started = time.monotonic()
    # 中断落点（Spec 9 §2.2 第 4 条的表）：handler 据此决定补哪一条合成结果。
    live: dict[str, Any] = {
        "calls": [], "index": 0, "call": None, "stage": None, "outcome": None, "name": "",
    }

    def _chat(tool_choice: str | None = None) -> dict[str, Any]:
        """一次模型请求；可重试错误（D93）按 2 / 4 / 8 s 自动重发，用尽才抛。

        等待用 `_sleep`：人点停止 → SIGINT 打断等待 → KeyboardInterrupt 走调用方现有的中断落点。
        """
        nonlocal llm_retries
        attempt = 0
        while True:
            try:
                return _chat_once(tool_choice)
            except LLMError as exc:
                if not exc.retryable:
                    raise
                if attempt >= LLM_MAX_RETRIES:
                    raise LLMError(f"{exc}（已自动重试 {LLM_MAX_RETRIES} 次）") from None
                attempt += 1
                llm_retries += 1
                delay = LLM_RETRY_BASE_S * 2 ** (attempt - 1)
                if control.on_llm_retry is not None:  # type: ignore[union-attr]
                    control.on_llm_retry(attempt, LLM_MAX_RETRIES, delay, str(exc))  # type: ignore[union-attr]
                _sleep(delay)

    def _chat_once(tool_choice: str | None) -> dict[str, Any]:
        nonlocal prompt_chars, prompt_tokens, usage_anchor
        # D65：发送与字符统计都走老化投影（上一回合及更早的大工具结果换占位串）；
        # convo 与 session.jsonl 一字不动（纯函数投影，日志即事实）
        aged = age_tool_results(convo)
        prompt_chars = sum(
            len(str(m.get("content") or "")) for m in aged if isinstance(m, dict)
        )
        requested_len = len(convo)
        _LAST_USAGE.set(None)
        reply = chat_complete(
            # D90：本回合 view_image 的图附在各批工具结果之后（同为请求时投影；字符统计在附图之前算）
            vision.attach_images(aged),
            tools=tools or None,
            config=cfg,
            scope=context.scope,
            purpose=purpose,
            tool_choice=tool_choice,
            egress_trusted=egress_trusted,
        )
        prompt_tokens = _prompt_tokens_of(_LAST_USAGE.get())
        usage_anchor = None if prompt_tokens is None else (prompt_tokens, requested_len)
        return reply

    def _synthesize_rest(start: int, text: str) -> None:
        for rest in live["calls"][start:]:
            control.commit(  # type: ignore[union-attr]
                _tool_message(rest, {"ok": False, "error": text}), "synthetic_tool"
            )

    def _deliver_steer(answered: str | None = None) -> bool:
        """D79：把排队消息逐条写史（user 消息带 STEER_KEY）。返回是否送达了任何消息。"""
        if control.drain_steer is None:  # type: ignore[union-attr]
            return False
        items = control.drain_steer()  # type: ignore[union-attr]
        if not items:
            return False
        for item in items:
            control.commit(  # type: ignore[union-attr]
                {"role": "user", "content": str(item.get("text", "")), STEER_KEY: True}, "user_steer"
            )
        if control.on_steer is not None:  # type: ignore[union-attr]
            control.on_steer([str(item.get("id", "")) for item in items], answered)  # type: ignore[union-attr]
        return True

    def _local_note(reason: str, wrapup_error: str | None) -> str:
        # 本轮**全部**调用（跨回复累计）：只看最后一条回复会在多轮后写成「（无）」
        detail = "、".join(f"{name}×{count}" for name, count in turn_tally.items()) or "无"
        why = f"失败（{wrapup_error}）" if wrapup_error else "未产生可用答复"
        return (
            f"[收尾·本地] 本轮在第 {llm_calls} 次模型调用后因{_STOP_REASON_TEXT.get(reason, reason)}"
            f"停止；工具调用 {tool_calls_made} 次（{detail}）；收尾调用{why}。"
            "已执行的工具结果保留在会话中。"
        )

    def _wrapup(reason: str) -> tuple[str, dict[str, Any] | None, str | None, str | None]:
        """收尾调用（§2.3 第 2 条）。返回 (wrapup 状态, final, local_note, error)。"""
        nonlocal llm_calls
        if control.interrupt is not None:  # type: ignore[union-attr]
            with contextlib.suppress(AttributeError):
                control.interrupt.wrapup_started = True  # type: ignore[union-attr]
        control.commit(  # type: ignore[union-attr]
            {
                "role": "user",
                "content": WRAPUP_INSTRUCTION.replace(
                    "{reason}", _STOP_REASON_TEXT.get(reason, reason)
                ),
                _WRAPUP_KEY: True,  # D65 §3.4：老化的边界扫描跳过收尾指令（红队 F1）
            },
            "wrapup_instruction",
        )
        try:
            reply = _chat(tool_choice="none")
        except KeyboardInterrupt:
            return "aborted", None, None, None
        except Exception as exc:  # noqa: BLE001 - 收尾失败必须如实降级，不许假装答完
            return "failed", None, _local_note(reason, f"{type(exc).__name__}: {exc}"), str(exc)
        llm_calls += 1
        control.commit(reply, "assistant")  # type: ignore[union-attr]
        calls = reply.get("tool_calls") or []
        if not calls:
            return "ok", reply, None, None
        # A1 不成立：服务商忽略了 tool_choice:"none"。不执行，逐个补合成结果。
        for call in calls:
            control.commit(  # type: ignore[union-attr]
                _tool_message(call, {"ok": False, "error": _SYNTH_WRAPUP}), "synthetic_tool"
            )
        if str(reply.get("content") or ""):
            return "ok", reply, None, None
        return "failed", None, _local_note(reason, None), "收尾回复不含可用答复"

    def _stop(reason: str, *, error: str | None = None):
        """非正常停止的收尾闸：本轮一条回复都没拿到 → 回滚；否则收尾。"""
        if getattr(control.interrupt, "skip_wrapup", False):
            # SIGTERM（§2.2 第 5 条）：host 的 SIGKILL 倒计时只有 5 s，收尾最长 60 s → 跳过收尾
            return _loop_result(
                convo, stopped=reason, llm_calls=llm_calls, tool_calls_made=tool_calls_made,
                tool_executions=tool_executions, duplicates_rejected=duplicates_rejected,
                checkpoints=checkpoints, wrapup="skipped", error=error,
                prompt_chars=prompt_chars, lookups=lookups, prompt_tokens=prompt_tokens, compaction=comp, llm_retries=llm_retries, elapsed_s=time.monotonic() - started,
            )
        if llm_calls == 0:
            return _loop_result(
                convo, stopped=reason, rollback=True, llm_calls=llm_calls,
                tool_calls_made=tool_calls_made, tool_executions=tool_executions,
                duplicates_rejected=duplicates_rejected, checkpoints=checkpoints,
                error=error, prompt_chars=prompt_chars, lookups=lookups, prompt_tokens=prompt_tokens, compaction=comp, llm_retries=llm_retries, elapsed_s=time.monotonic() - started,
            )
        state, final, note, wrapup_error = _wrapup(reason)
        return _loop_result(
            convo, final=final, local_note=note, stopped=reason, llm_calls=llm_calls,
            tool_calls_made=tool_calls_made, tool_executions=tool_executions,
            duplicates_rejected=duplicates_rejected, checkpoints=checkpoints,
            wrapup=state, error=error or wrapup_error,
            prompt_chars=prompt_chars, lookups=lookups, prompt_tokens=prompt_tokens, compaction=comp, llm_retries=llm_retries, elapsed_s=time.monotonic() - started,
        )

    try:
        while True:
            if bare and (replies_since_cp >= BARE_LOOP_CAP or execs_since_cp >= BARE_LOOP_CAP):
                # D166：裸循环到硬上限直接停（没有交互通道可问）；交互会话不再设检查点
                checkpoints += 1
                live["call"] = None
                return _stop("checkpoint_stop")

            live["call"] = None
            live["calls"] = []
            live["index"] = 0
            # D62 双触发（spec §4.1）：回合首次请求前 + 每批工具结果全部写史之后、下一次请求前。
            # 这里 live["call"] 已清空：压缩中途被中断，落点表不会给已配对的调用再补一条合成结果
            if control.compact is not None:  # type: ignore[union-attr]
                live["stage"] = "compact"
                hint = None
                if usage_anchor is not None and usage_anchor[1] <= len(convo):
                    hint = usage_anchor[0] + estimate_tokens(convo[usage_anchor[1]:])
                compacted = control.compact(convo, hint)  # type: ignore[union-attr]
                if compacted is not None and compacted.get("ok"):
                    usage_anchor = None  # 历史已缩：旧实测不再对得上
                    if not comp["compacted"]:
                        comp["tokens_before"] = compacted.get("tokens_before")
                    comp["compacted"] = True
                    comp["tokens_after"] = compacted.get("tokens_after")
                elif compacted is not None and compacted.get("abort"):
                    # 失败中止交人（spec §4.5）：不做收尾——收尾请求带的正是那份压不下去的上下文
                    note = (
                        f"[压缩失败] {compacted.get('text', '')}。本回合停止，本会话不再自动压缩；"
                        "可手动 /compact 重试，或开新会话。"
                    )
                    return _loop_result(
                        convo, local_note=note, stopped="compact_failed", rollback=llm_calls == 0,
                        llm_calls=llm_calls, tool_calls_made=tool_calls_made,
                        tool_executions=tool_executions, duplicates_rejected=duplicates_rejected,
                        checkpoints=checkpoints, wrapup="skipped", error=str(compacted.get("text", "")),
                        prompt_chars=prompt_chars, lookups=lookups, prompt_tokens=prompt_tokens, compaction=comp, llm_retries=llm_retries,
                        elapsed_s=time.monotonic() - started,
                    )
            live["stage"] = "model"
            reply = _chat()
            control.commit(reply, "assistant")  # type: ignore[union-attr]
            llm_calls += 1
            replies_since_cp += 1

            calls = reply.get("tool_calls") or []
            if not calls:
                # D79（Pi 的「有排队就不收工」）：人在模型作答期间发来的消息，送达后继续一轮
                if _deliver_steer(str(reply.get("content") or "")):
                    continue
                return _loop_result(
                    convo, final=reply, stopped="done", llm_calls=llm_calls,
                    tool_calls_made=tool_calls_made, tool_executions=tool_executions,
                    duplicates_rejected=duplicates_rejected, checkpoints=checkpoints,
                    prompt_chars=prompt_chars, lookups=lookups, prompt_tokens=prompt_tokens, compaction=comp, llm_retries=llm_retries, elapsed_s=time.monotonic() - started,
                )

            live["calls"] = calls
            for index, call in enumerate(calls):
                live["index"] = index
                live["call"] = call
                function = call.get("function") or {}
                name = str(function.get("name", ""))
                args = _parse_tool_args(function.get("arguments"))
                live["name"] = name
                tool_calls_made += 1
                turn_tally[name] = turn_tally.get(name, 0) + 1

                # 裸循环执行计数**每次执行之前**查（🟡-A）：同一条回复里的并行调用也逐个受约束
                if bare and execs_since_cp >= BARE_LOOP_CAP:
                    checkpoints += 1
                    _synthesize_rest(index, _SYNTH_CHECKPOINT)
                    live["call"] = None
                    return _stop("checkpoint_stop")

                if bare:
                    key: tuple[str, str] | None = None
                else:
                    key = dedup_key(name, args)
                    if key in dedup:
                        duplicates_rejected += 1
                        _trace(control, index, name, args, duplicate=True)
                        control.commit(  # type: ignore[union-attr]
                            _tool_message(call, _duplicate_outcome(name)), "synthetic_tool"
                        )
                        _trace(control, index, name, args, content=_duplicate_outcome(name),
                               duplicate=True)
                        continue
                    dedup[key] = None  # 预登记：中断落在这里也算「已调过」

                live["stage"] = "review"
                decision = control.review(name, args)  # type: ignore[union-attr]
                live["stage"] = "tool"
                if not decision.ok:
                    rejected = _reject_outcome(decision)
                    _trace(control, index, name, args)
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, rejected), "synthetic_tool"
                    )
                    _trace(control, index, name, args, content=rejected)
                    continue

                _trace(control, index, name, args)
                if control.on_exec_started is not None:  # type: ignore[union-attr]
                    control.on_exec_started(str(call.get("id", "")), name)  # type: ignore[union-attr]
                live["outcome"] = None
                exec_ctx = (
                    replace(
                        context, confirmed=True,
                        # D81 §4.4：执行层据此分辨「人在卡上批了」与免卡 / 本会话已批（后两者 confirmed 也为真）
                        human_approved=bool(decision.ok and decision.provenance == "human"),
                        bound=decision.bind if decision.ok and decision.provenance == "human" else None,
                    ) if (approve is not None or not bare) else context
                )
                if control.interrupt is not None and name in control.critical_tools:  # type: ignore[union-attr]
                    with control.interrupt.defer():  # type: ignore[union-attr]
                        live["outcome"] = execute_tool(name, args, exec_ctx)
                else:
                    live["outcome"] = execute_tool(name, args, exec_ctx)
                outcome = live["outcome"]
                live["outcome"] = None
                tool_executions += 1
                execs_since_cp += 1
                kind = lookup_kind(name, args)
                if kind is not None:
                    lookups[kind] += 1

                if name == "acquire_propose" and outcome.get("ok") and control.post_execute is not None:  # type: ignore[union-attr]
                    live["stage"] = "hook"
                    try:
                        outcome = control.post_execute(name, args, outcome)  # type: ignore[union-attr]
                    except KeyboardInterrupt as exc:
                        # 抓取钩子被中断（D35）：提案已执行、已计数，结果照常（带抓取记录）
                        live["outcome"] = getattr(exc, "outcome", None) or outcome
                        raise
                    live["stage"] = "tool"
                # 人看的工具帧同样不带 view_image 的私有图片键（本机绝对路径 + sha，对人无用）
                _trace(control, index, name, args, content=vision.split_outcome(outcome)[0])
                if decision.provenance == "human" or (name == "write_episode_file" and outcome.get("ok")):
                    # 经人批准的执行完成后清空（§2.3 第 5 条）。D48：成功写期文件后也清——D44 让草稿写入免卡后，
                    # 「改完再跑同一条 check_script」被判重复，模型换着写路径绕过去。只清写入后：只读检查免卡执行不清，
                    # 原地反复跑同一条检查仍判重
                    dedup.clear()
                control.commit(_tool_message(call, outcome), "tool")  # type: ignore[union-attr]

            # D79：这批工具结果已全部写史，下一次请求之前送达排队消息（Pi 的 steering 落点）
            live["call"] = None
            _deliver_steer()

    except KeyboardInterrupt:
        # 落点表（§2.2 第 4 条）：补合成结果 → 收尾或回滚。
        # 「停止中」：这期间到达的中断只置标志、不抛（合成结果必须齐全，不许丢）。
        # 随后的 `_stop` 在吸收区**之外**，所以「收尾中」的中断照旧能放弃收尾（TL-9）。
        with _stopping(control):
            call = live["call"]
            if call is not None:
                if live["stage"] == "hook":
                    # 抓取钩子里的中断：真实结果照常配对（执行计数在钩子之前已加过）
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, live["outcome"]), "tool"
                    )
                    live["outcome"] = None
                elif live["stage"] == "tool" and live["outcome"] is not None:
                    # 临界区工具已经跑完，中断在延迟区退出时才浮出：用**真实结果**，配对不受影响
                    tool_executions += 1
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, live["outcome"]), "tool"
                    )
                    live["outcome"] = None
                else:
                    text = _SYNTH_HUMAN_VOIDED if live["stage"] == "review" else _SYNTH_UNKNOWN
                    control.commit(  # type: ignore[union-attr]
                        _tool_message(call, {"ok": False, "error": text}), "synthetic_tool"
                    )
                _synthesize_rest(live["index"] + 1, _SYNTH_HUMAN_UNEXECUTED)
                live["call"] = None
        return _stop("interrupted")
    except PermissionError as exc:
        # 出网断言拒绝（llm.py::assert_egress_boundary）：不做收尾（同一份历史必然再被拒），回滚
        return _loop_result(
            convo, stopped="blocked", rollback=True, llm_calls=llm_calls,
            tool_calls_made=tool_calls_made, tool_executions=tool_executions,
            duplicates_rejected=duplicates_rejected, checkpoints=checkpoints, error=str(exc),
            prompt_chars=prompt_chars, lookups=lookups, prompt_tokens=prompt_tokens, compaction=comp, llm_retries=llm_retries, elapsed_s=time.monotonic() - started,
        )
    except LLMError as exc:
        return _stop("error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - 意外异常也走「先收尾后停」，不假装无事发生
        return _stop("error", error=f"{type(exc).__name__}: {exc}")
