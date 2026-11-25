"""D170：AVA 独立多服务商认证、OAuth 续期、本地模型元数据缓存与路由。

完全独立运行，不依赖任何外部 agent CLI/路径：
- 凭据存储：`config/auth.local.json`（环境变量 `AVA_AUTH_PATH` 可覆盖），`0o600` 权限 + 文件锁原子写。
- 完整对齐开源大模型与云路由生态（OAuth 订阅、主流直连、云平台与路由、本地/反代代理 共 44 家）。
- OAuth 访问令牌在过期前 60 秒自动加锁刷新并回写。
- `N50`：凭据文件纳入 `tools.py` 出网阻止集与脱敏密钥集，永不传入工作流子进程或 Bash。
"""
from __future__ import annotations

import base64
import contextlib
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pipeline import paths

REFRESH_SKEW_MS = 60_000
PENDING_TTL_MS = 30 * 60 * 1000
DEFAULT_HTTP_TIMEOUT_S = 20.0
ALLOWED_AUTH_MODES = frozenset({"proxy", "api_key", "oauth_pkce", "oauth_device"})

# D170 安全加固：macOS 钥匙串托管主密钥 + AES-256-GCM 敏感字段加密
_KEYCHAIN_EXEC = "/usr/bin/security"
_KEYCHAIN_SERVICE = "local.ava.auth"
_KEYCHAIN_ACCOUNT = "auth-store-master-key-v1"
_ENC_PREFIX = "enc:v1:"
_ENCRYPTED_PROVIDER_FIELDS = frozenset({"api_key", "access_token", "refresh_token"})
_ENCRYPTED_PENDING_FIELDS = frozenset({"code_verifier", "device_code"})
_ENCRYPTED_EXTRA_FIELDS = frozenset({"aws_secret_access_key", "aws_session_token"})
_KEYCHAIN_LOCK = threading.Lock()
_CACHED_MASTER_KEY: bytes | None = None
# 测试可注入的钥匙串后端：(op: "get"|"set", value: str|None) -> str|None
_KEYCHAIN_BACKEND: Callable[[str, str | None], str | None] | None = None
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_=-]+$")
# collect_stored_secret_values 只读缓存（避免并发只读工具争抢 LOCK_EX 或在钥匙串锁定时超时风暴）
_SECRETS_CACHE_LOCK = threading.Lock()
_SECRETS_POS_CACHE: dict[tuple[Any, ...], tuple[str, ...]] = {}
_SECRETS_NEG_CACHE: dict[tuple[Any, ...], float] = {}
_SECRETS_NEG_TTL_S = 15.0

_CLOUD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_CLOUD_REGION_RE = re.compile(r"^[a-z0-9-]{2,64}$")
_AZURE_RESOURCE_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_DYNAMIC_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_ERR_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})")
_ERR_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_ERR_SECRET_KV_RE = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password)[\"']?\s*[:=]\s*[\"']?)([^\"',\s}]+)"
)


class AuthError(RuntimeError):
    """D170 认证/续期/回调解析错误（可直接展示给用户）。"""


@dataclass(frozen=True)
class ExtraFieldSpec:
    key: str
    label: str
    placeholder: str = ""
    required: bool = False
    secret: bool = False
    default: str = ""
    env_var: str = ""
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "placeholder": self.placeholder,
            "required": self.required,
            "secret": self.secret,
            "default": self.default,
            "env_var": self.env_var,
            "help": self.help,
        }


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
    category: str = "flagship"
    env_vars: tuple[str, ...] = ()
    extra_fields: tuple[ExtraFieldSpec, ...] = ()
    optional_key_in_proxy: bool = False
    aliases: tuple[str, ...] = ()
    oauth_label: str = ""


_COPILOT_HEADERS: dict[str, str] = {
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}

PROVIDERS: tuple[ProviderSpec, ...] = (
    # ---- OAuth 订阅 / 联合认证 ----
    ProviderSpec(
        id="anthropic",
        name="Anthropic (Claude)",
        desc="Claude Pro/Max 订阅 OAuth PKCE 或 sk-ant-* API Key",
        auth_modes=("oauth_pkce", "api_key"),
        default_base_url="https://api.anthropic.com",
        api_protocol="anthropic-messages",
        default_models=(
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5-20251001",
        ),
        category="oauth",
        env_vars=("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
        oauth_label="Anthropic (Claude Pro/Max)",
        oauth={
            "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
            "authorize_url": "https://claude.ai/oauth/authorize",
            "token_url": "https://platform.claude.com/v1/oauth/token",
            "redirect_uri": "https://platform.claude.com/oauth/code/callback",
            "scopes": "org:create_api_key user:profile user:inference",
            "token_content_type": "json",
            "state_is_verifier": True,
            "extra_authorize_params": {"code": "true"},
            "extra_headers": {
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14",
            },
        },
    ),
    ProviderSpec(
        id="openai",
        name="OpenAI",
        desc="支持 ChatGPT 订阅一键 OAuth 登录（Responses）或 OpenAI API Key",
        auth_modes=("api_key", "oauth_pkce"),
        default_base_url="https://api.openai.com/v1",
        api_protocol="openai-responses",
        default_models=("gpt-5.4", "gpt-5.6", "gpt-6-luna", "gpt-4.1", "o4-mini"),
        category="oauth",
        env_vars=("OPENAI_API_KEY",),
        oauth_label="Sign in with ChatGPT",
        oauth={
            "client_id": "dynamic_agent_client",
            "dynamic_client_id": True,
            "authorize_url": "https://auth.openai.com/api/accounts/authorize",
            "token_url": "https://auth.openai.com/api/accounts/oauth/token",
            "redirect_uri": "http://127.0.0.1:1455/auth/callback",
            "scopes": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            "required_scope": "chatgpt.tokens.use.direct",
            "resource": "https://api.openai.com/v1",
            "token_content_type": "form",
            "include_host_id": True,
            "include_nonce": True,
            "extra_authorize_params": {"agent_name_hint": "AVA"},
        },
    ),
    ProviderSpec(
        id="openai-codex",
        name="OpenAI Codex (ChatGPT)",
        desc="ChatGPT Plus / Pro 订阅 OAuth PKCE 或设备码登录（Codex Responses）",
        auth_modes=("oauth_pkce", "oauth_device"),
        default_base_url="https://chatgpt.com/backend-api",
        api_protocol="openai-codex-responses",
        default_models=("gpt-6.1-sol", "gpt-6-sol", "gpt-5.4", "gpt-5.3-codex", "gpt-5.2-codex"),
        category="oauth",
        env_vars=("OPENAI_CODEX_TOKEN",),
        oauth_label="OpenAI (ChatGPT Plus/Pro)",
        extra_headers={"OpenAI-Beta": "responses=experimental", "originator": "ava"},
        oauth={
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "authorize_url": "https://auth.openai.com/oauth/authorize",
            "token_url": "https://auth.openai.com/oauth/token",
            "redirect_uri": "http://localhost:1455/auth/callback",
            "device_redirect_uri": "https://auth.openai.com/deviceauth/callback",
            "scopes": "openid profile email offline_access",
            "token_content_type": "form",
            "device_code_url": "https://auth.openai.com/api/accounts/deviceauth/usercode",
            "device_token_url": "https://auth.openai.com/api/accounts/deviceauth/token",
            "device_verification_uri": "https://auth.openai.com/codex/device",
            "device_content_type": "json",
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
        desc="GitHub 账号 / Enterprise 设备码或 COPILOT_GITHUB_TOKEN",
        auth_modes=("oauth_device", "api_key"),
        default_base_url="https://api.individual.githubcopilot.com",
        api_protocol="openai-completions",
        default_models=("claude-sonnet-4.6", "claude-opus-4.8", "gpt-5.4", "gpt-4.1"),
        category="oauth",
        env_vars=("COPILOT_GITHUB_TOKEN",),
        oauth_label="GitHub Copilot",
        extra_headers=dict(_COPILOT_HEADERS),
        extra_fields=(
            ExtraFieldSpec(
                key="enterprise_domain",
                label="GitHub Enterprise 域名（可选）",
                placeholder="例如 company.ghe.com（公有云留空）",
                help="仅 GitHub Enterprise Cloud 用户需要填写",
            ),
        ),
        oauth={
            "client_id": "Iv1.b507a08c87ecfe98",
            "device_code_url": "https://{domain}/login/device/code",
            "token_url": "https://{domain}/login/oauth/access_token",
            "copilot_token_url": "https://api.{domain}/copilot_internal/v2/token",
            "scopes": "read:user",
        },
    ),
    ProviderSpec(
        id="openrouter",
        name="OpenRouter",
        desc="OpenRouter 多模型聚合网关（支持 OAuth PKCE 一键换 Key 或 API Key）",
        auth_modes=("api_key", "oauth_pkce"),
        default_base_url="https://openrouter.ai/api/v1",
        api_protocol="openai-completions",
        default_models=(
            "anthropic/claude-opus-5",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.4",
            "google/gemini-2.5-pro",
            "deepseek/deepseek-v4-pro",
        ),
        category="oauth",
        env_vars=("OPENROUTER_API_KEY",),
        oauth_label="OpenRouter OAuth",
        oauth={
            "authorize_url": "https://openrouter.ai/auth",
            "token_url": "https://openrouter.ai/api/v1/auth/keys",
            "redirect_uri": "http://127.0.0.1:1455/auth/callback",
            "callback_param": "callback_url",
            "pkce_kind": "openrouter_key",
            "state_optional": True,
        },
    ),
    ProviderSpec(
        id="kimi-coding",
        name="Kimi For Coding",
        desc="Kimi Code 会员设备码 OAuth 或 KIMI_API_KEY",
        auth_modes=("oauth_device", "api_key"),
        default_base_url="https://api.kimi.com/coding/v1",
        api_protocol="openai-completions",
        default_models=("kimi-for-coding", "kimi-for-coding-highspeed", "k3.1-pro", "k2p5"),
        category="oauth",
        env_vars=("KIMI_API_KEY",),
        oauth_label="Sign in with Kimi Code",
        oauth={
            "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
            "device_code_url": "https://auth.kimi.com/api/oauth/device_authorization",
            "token_url": "https://auth.kimi.com/api/oauth/token",
            "scopes": "",
            "device_content_type": "form",
            "token_content_type": "form",
        },
    ),
    ProviderSpec(
        id="xai",
        name="xAI (Grok)",
        desc="Grok / X 订阅设备码 OAuth 或 XAI_API_KEY（Responses API）",
        auth_modes=("api_key", "oauth_device"),
        default_base_url="https://api.x.ai/v1",
        api_protocol="openai-responses",
        default_models=("grok-4.20-0309-non-reasoning", "grok-3", "grok-3-fast"),
        category="oauth",
        env_vars=("XAI_API_KEY",),
        oauth_label="xAI (Grok/X subscription)",
        oauth={
            "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
            "device_code_url": "https://auth.x.ai/oauth2/device/code",
            "token_url": "https://auth.x.ai/oauth2/token",
            "scopes": "offline_access",
            "device_content_type": "form",
            "token_content_type": "form",
        },
    ),
    ProviderSpec(
        id="meta",
        name="Meta (Llama)",
        desc="Meta AI 设备码 OAuth 授权或 META_API_KEY（Responses API）",
        auth_modes=("api_key", "oauth_device"),
        default_base_url="https://api.meta.ai/v1",
        api_protocol="openai-responses",
        default_models=("llama-4-maverick", "llama-4-scout"),
        category="oauth",
        env_vars=("META_API_KEY",),
        oauth_label="Meta AI Device Login",
        oauth={
            "client_id": "1031625952748946",
            "device_code_url": "https://auth.meta.com/oidc/device/authorization/",
            "token_url": "https://auth.meta.com/oidc/device/token/",
            "exchange_key_url": "https://api.meta.ai/muse-code/key",
            "scopes": "",
            "device_content_type": "form",
            "token_content_type": "form",
        },
    ),
    # ---- 常用大模型直连 ----
    ProviderSpec(
        id="google",
        name="Google Gemini",
        desc="Google AI Studio 原生 Gemini REST 协议（兼容 google-gemini 别名）",
        auth_modes=("api_key",),
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        api_protocol="google-generative-ai",
        default_models=("gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"),
        category="flagship",
        env_vars=("GEMINI_API_KEY",),
        aliases=("google-gemini", "gemini"),
    ),
    ProviderSpec(
        id="deepseek",
        name="DeepSeek",
        desc="DeepSeek 官方 API（deepseek-chat / deepseek-reasoner / deepseek-v4-pro）",
        auth_modes=("api_key",),
        default_base_url="https://api.deepseek.com/v1",
        api_protocol="openai-completions",
        default_models=("deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro"),
        category="flagship",
        env_vars=("DEEPSEEK_API_KEY",),
    ),
    ProviderSpec(
        id="mistral",
        name="Mistral",
        desc="Mistral 官方原生对话接口（Tool ID 自动归一化）",
        auth_modes=("api_key",),
        default_base_url="https://api.mistral.ai",
        api_protocol="mistral-conversations",
        default_models=("mistral-medium-3.5", "mistral-large-latest", "codestral-latest"),
        category="flagship",
        env_vars=("MISTRAL_API_KEY",),
    ),
    ProviderSpec(
        id="zai",
        name="Z.AI (GLM 国际)",
        desc="Z.AI GLM Coding Plan 国际版",
        auth_modes=("api_key",),
        default_base_url="https://api.z.ai/api/coding/paas/v4",
        api_protocol="openai-completions",
        default_models=("glm-5", "glm-4.7", "glm-4.6"),
        category="flagship",
        env_vars=("ZAI_API_KEY",),
        aliases=("zhipu",),
    ),
    ProviderSpec(
        id="zai-coding-cn",
        name="Z.AI Coding CN (智谱国内)",
        desc="智谱 BigModel Coding Plan 国内节点",
        auth_modes=("api_key",),
        default_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_protocol="openai-completions",
        default_models=("glm-5", "glm-4.7", "glm-4.6"),
        category="flagship",
        env_vars=("ZAI_CODING_CN_API_KEY",),
    ),
    ProviderSpec(
        id="moonshotai",
        name="Moonshot AI (国际)",
        desc="月之暗面 Moonshot 国际开放平台 API Key",
        auth_modes=("api_key",),
        default_base_url="https://api.moonshot.ai/v1",
        api_protocol="openai-completions",
        default_models=("kimi-k2.5", "moonshot-v1-128k"),
        category="flagship",
        env_vars=("MOONSHOT_API_KEY",),
        aliases=("moonshot",),
    ),
    ProviderSpec(
        id="moonshotai-cn",
        name="Moonshot AI CN (国内)",
        desc="月之暗面 Moonshot 国内开放平台（api.moonshot.cn）",
        auth_modes=("api_key",),
        default_base_url="https://api.moonshot.cn/v1",
        api_protocol="openai-completions",
        default_models=("kimi-k2.5", "moonshot-v1-128k"),
        category="flagship",
        env_vars=("MOONSHOT_API_KEY",),
    ),
    ProviderSpec(
        id="minimax",
        name="MiniMax (国际)",
        desc="MiniMax 国际版（Anthropic Messages 兼容协议）",
        auth_modes=("api_key",),
        default_base_url="https://api.minimax.io/anthropic",
        api_protocol="anthropic-messages",
        default_models=("MiniMax-M2.1", "MiniMax-M1"),
        category="flagship",
        env_vars=("MINIMAX_API_KEY",),
    ),
    ProviderSpec(
        id="minimax-cn",
        name="MiniMax CN (国内)",
        desc="MiniMax 国内版（api.minimaxi.com，Anthropic Messages 协议）",
        auth_modes=("api_key",),
        default_base_url="https://api.minimaxi.com/anthropic",
        api_protocol="anthropic-messages",
        default_models=("MiniMax-M2.1", "MiniMax-M1"),
        category="flagship",
        env_vars=("MINIMAX_CN_API_KEY",),
    ),
    ProviderSpec(
        id="qwen-token-plan",
        name="Qwen Token Plan Team (国际)",
        desc="通义千问 Token Plan 团队版（国际节点）",
        auth_modes=("api_key",),
        default_base_url="https://token-plan.maas.qwencloudapi.com/compatible-mode/v1",
        api_protocol="openai-completions",
        default_models=("qwen3-coder-plus", "qwen3.5-plus", "qwq-plus"),
        category="flagship",
        env_vars=("QWEN_TOKEN_PLAN_TEAM_API_KEY", "QWEN_TOKEN_PLAN_API_KEY"),
    ),
    ProviderSpec(
        id="qwen-token-plan-cn",
        name="Qwen Token Plan Team CN (国内)",
        desc="通义千问 Token Plan 团队版（国内节点）",
        auth_modes=("api_key",),
        default_base_url="https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
        api_protocol="openai-completions",
        default_models=("qwen3-coder-plus", "qwen3.5-plus", "qwq-plus"),
        category="flagship",
        env_vars=("QWEN_TOKEN_PLAN_TEAM_API_KEY", "QWEN_TOKEN_PLAN_API_KEY"),
    ),
    ProviderSpec(
        id="qwen-token-plan-individual",
        name="Qwen Token Plan Individual (国际)",
        desc="通义千问 Token Plan 个人版（国际节点）",
        auth_modes=("api_key",),
        default_base_url="https://token-plan.maas.qwencloudapi.com/compatible-mode/v1",
        api_protocol="openai-completions",
        default_models=("qwen3-coder-plus", "qwen3.5-plus"),
        category="flagship",
        env_vars=("QWEN_TOKEN_PLAN_INDIVIDUAL_API_KEY", "QWEN_TOKEN_PLAN_API_KEY"),
    ),
    ProviderSpec(
        id="qwen-token-plan-individual-cn",
        name="Qwen Token Plan Individual CN (国内)",
        desc="通义千问 Token Plan 个人版（国内节点）",
        auth_modes=("api_key",),
        default_base_url="https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
        api_protocol="openai-completions",
        default_models=("qwen3-coder-plus", "qwen3.5-plus"),
        category="flagship",
        env_vars=("QWEN_TOKEN_PLAN_INDIVIDUAL_API_KEY", "QWEN_TOKEN_PLAN_API_KEY"),
    ),
    ProviderSpec(
        id="xiaomi",
        name="Xiaomi MiMo",
        desc="小米 MiMo 官方 API（OpenAI 兼容接口）",
        auth_modes=("api_key",),
        default_base_url="https://api.xiaomimimo.com/v1",
        api_protocol="openai-completions",
        default_models=("mimo-v2-flash",),
        category="flagship",
        env_vars=("XIAOMI_API_KEY",),
    ),
    ProviderSpec(
        id="xiaomi-token-plan-cn",
        name="Xiaomi Token Plan CN",
        desc="小米 MiMo Token Plan 国内节点",
        auth_modes=("api_key",),
        default_base_url="https://token-plan-cn.xiaomimimo.com/v1",
        api_protocol="openai-completions",
        default_models=("mimo-v2-flash",),
        category="flagship",
        env_vars=("XIAOMI_TOKEN_PLAN_CN_API_KEY",),
    ),
    ProviderSpec(
        id="xiaomi-token-plan-sgp",
        name="Xiaomi Token Plan SGP",
        desc="小米 MiMo Token Plan 新加坡节点",
        auth_modes=("api_key",),
        default_base_url="https://token-plan-sgp.xiaomimimo.com/v1",
        api_protocol="openai-completions",
        default_models=("mimo-v2-flash",),
        category="flagship",
        env_vars=("XIAOMI_TOKEN_PLAN_SGP_API_KEY",),
    ),
    ProviderSpec(
        id="xiaomi-token-plan-ams",
        name="Xiaomi Token Plan AMS",
        desc="小米 MiMo Token Plan 欧洲节点",
        auth_modes=("api_key",),
        default_base_url="https://token-plan-ams.xiaomimimo.com/v1",
        api_protocol="openai-completions",
        default_models=("mimo-v2-flash",),
        category="flagship",
        env_vars=("XIAOMI_TOKEN_PLAN_AMS_API_KEY",),
    ),
    ProviderSpec(
        id="ant-ling",
        name="Ant Ling (百灵)",
        desc="蚂蚁百灵大模型官方 API",
        auth_modes=("api_key",),
        default_base_url="https://api.ant-ling.com/v1",
        api_protocol="openai-completions",
        default_models=("ling-plus",),
        category="flagship",
        env_vars=("ANT_LING_API_KEY",),
    ),
    # ---- 云平台与路由 ----
    ProviderSpec(
        id="amazon-bedrock",
        name="Amazon Bedrock",
        desc="AWS Bedrock Converse 原生协议（Bearer Token / IAM AK+SK SigV4 / AWS Profile）",
        auth_modes=("api_key",),
        default_base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_protocol="bedrock-converse-stream",
        default_models=("anthropic.claude-opus-5", "anthropic.claude-sonnet-4-6", "amazon.nova-pro-v1:0"),
        category="cloud",
        env_vars=("AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_PROFILE"),
        extra_fields=(
            ExtraFieldSpec(
                key="aws_region",
                label="AWS Region",
                placeholder="us-east-1",
                default="us-east-1",
                env_var="AWS_REGION",
            ),
            ExtraFieldSpec(
                key="aws_access_key_id",
                label="IAM Access Key ID（使用 SigV4 时填写）",
                placeholder="AKIA...",
                env_var="AWS_ACCESS_KEY_ID",
            ),
            ExtraFieldSpec(
                key="aws_secret_access_key",
                label="IAM Secret Access Key",
                placeholder="粘贴 AWS Secret Access Key",
                secret=True,
                env_var="AWS_SECRET_ACCESS_KEY",
            ),
            ExtraFieldSpec(
                key="aws_session_token",
                label="IAM Session Token（临时凭据可选）",
                placeholder="可选 STS Token",
                secret=True,
                env_var="AWS_SESSION_TOKEN",
            ),
            ExtraFieldSpec(
                key="aws_profile",
                label="AWS Profile（~/.aws/credentials 可选）",
                placeholder="default",
                env_var="AWS_PROFILE",
            ),
        ),
    ),
    ProviderSpec(
        id="google-vertex",
        name="Google Vertex AI",
        desc="GCP Vertex AI 原生 REST（API Key / ADC 凭据 / Service Account JSON）",
        auth_modes=("api_key",),
        default_base_url="https://us-central1-aiplatform.googleapis.com/v1",
        api_protocol="google-vertex",
        default_models=("gemini-2.5-pro", "gemini-2.5-flash"),
        category="cloud",
        env_vars=("GOOGLE_CLOUD_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"),
        extra_fields=(
            ExtraFieldSpec(
                key="project_id",
                label="GCP Project ID",
                placeholder="my-gcp-project",
                env_var="GOOGLE_CLOUD_PROJECT",
                help="使用 ADC 或 Service Account 时必填",
            ),
            ExtraFieldSpec(
                key="location",
                label="GCP Location",
                placeholder="us-central1 或 global",
                default="us-central1",
                env_var="GOOGLE_CLOUD_LOCATION",
            ),
            ExtraFieldSpec(
                key="credentials_path",
                label="Service Account JSON 路径（可选，默认读 ADC）",
                placeholder="~/.config/gcloud/application_default_credentials.json",
                env_var="GOOGLE_APPLICATION_CREDENTIALS",
            ),
        ),
    ),
    ProviderSpec(
        id="azure",
        name="Azure OpenAI",
        desc="Microsoft Azure OpenAI / Foundry（Responses API + 部署名映射）",
        auth_modes=("api_key",),
        default_base_url="https://YOUR-RESOURCE.openai.azure.com/openai/v1",
        api_protocol="azure-openai-responses",
        default_models=("gpt-5.4", "gpt-4.1", "o4-mini"),
        category="cloud",
        env_vars=("AZURE_OPENAI_API_KEY",),
        extra_fields=(
            ExtraFieldSpec(
                key="resource_name",
                label="Azure Resource Name",
                placeholder="my-openai-resource",
                env_var="AZURE_OPENAI_RESOURCE_NAME",
                help="未自定义 Base URL 时自动拼接 https://<resource>.openai.azure.com/openai/v1",
            ),
            ExtraFieldSpec(
                key="deployment_name",
                label="Deployment Name（可选）",
                placeholder="留空则直接传模型 ID",
                env_var="AZURE_OPENAI_DEPLOYMENT_NAME",
            ),
            ExtraFieldSpec(
                key="api_version",
                label="API Version",
                placeholder="v1",
                default="v1",
                env_var="AZURE_OPENAI_API_VERSION",
            ),
        ),
    ),
    ProviderSpec(
        id="cloudflare-ai-gateway",
        name="Cloudflare AI Gateway",
        desc="Cloudflare AI Gateway /compat 统一路由（需 Account ID 与 Gateway ID）",
        auth_modes=("api_key",),
        default_base_url="https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/compat",
        api_protocol="openai-completions",
        default_models=("anthropic/claude-sonnet-4-6", "openai/gpt-5.4"),
        category="cloud",
        env_vars=("CLOUDFLARE_API_KEY",),
        extra_fields=(
            ExtraFieldSpec(
                key="account_id",
                label="Cloudflare Account ID",
                placeholder="32 位 Account ID",
                required=True,
                env_var="CLOUDFLARE_ACCOUNT_ID",
            ),
            ExtraFieldSpec(
                key="gateway_id",
                label="AI Gateway ID",
                placeholder="my-gateway",
                required=True,
                env_var="CLOUDFLARE_GATEWAY_ID",
            ),
        ),
    ),
    ProviderSpec(
        id="cloudflare-workers-ai",
        name="Cloudflare Workers AI",
        desc="Cloudflare Workers AI OpenAI 兼容推理接口",
        auth_modes=("api_key",),
        default_base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        api_protocol="openai-completions",
        default_models=("@cf/meta/llama-4-scout-17b-16e-instruct", "@cf/qwen/qwq-32b"),
        category="cloud",
        env_vars=("CLOUDFLARE_API_KEY",),
        extra_fields=(
            ExtraFieldSpec(
                key="account_id",
                label="Cloudflare Account ID",
                placeholder="32 位 Account ID",
                required=True,
                env_var="CLOUDFLARE_ACCOUNT_ID",
            ),
        ),
    ),
    ProviderSpec(
        id="vercel-ai-gateway",
        name="Vercel AI Gateway",
        desc="Vercel AI Gateway（Anthropic Messages 路由接口）",
        auth_modes=("api_key",),
        default_base_url="https://ai-gateway.vercel.sh",
        api_protocol="anthropic-messages",
        default_models=("anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6"),
        category="cloud",
        env_vars=("AI_GATEWAY_API_KEY",),
    ),
    ProviderSpec(
        id="groq",
        name="Groq",
        desc="Groq LPU 超低延迟推理接口",
        auth_modes=("api_key",),
        default_base_url="https://api.groq.com/openai/v1",
        api_protocol="openai-completions",
        default_models=("llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
        category="cloud",
        env_vars=("GROQ_API_KEY",),
    ),
    ProviderSpec(
        id="cerebras",
        name="Cerebras",
        desc="Cerebras Wafer-Scale 极速推理 API",
        auth_modes=("api_key",),
        default_base_url="https://api.cerebras.ai/v1",
        api_protocol="openai-completions",
        default_models=("llama3.1-70b", "qwen-3-32b"),
        category="cloud",
        env_vars=("CEREBRAS_API_KEY",),
    ),
    ProviderSpec(
        id="together",
        name="Together",
        desc="Together AI 开源大模型推理云",
        auth_modes=("api_key",),
        default_base_url="https://api.together.ai/v1",
        api_protocol="openai-completions",
        default_models=("deepseek-ai/DeepSeek-V4-Pro-0813", "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"),
        category="cloud",
        env_vars=("TOGETHER_API_KEY",),
    ),
    ProviderSpec(
        id="fireworks",
        name="Fireworks",
        desc="Fireworks AI 高并发推理平台",
        auth_modes=("api_key",),
        default_base_url="https://api.fireworks.ai/inference",
        api_protocol="anthropic-messages",
        default_models=("accounts/fireworks/models/deepseek-v3p1", "accounts/fireworks/models/llama4-maverick-instruct-basic"),
        category="cloud",
        env_vars=("FIREWORKS_API_KEY",),
    ),
    ProviderSpec(
        id="huggingface",
        name="Hugging Face",
        desc="Hugging Face Inference Router 统一接口",
        auth_modes=("api_key",),
        default_base_url="https://router.huggingface.co/v1",
        api_protocol="openai-completions",
        default_models=("Qwen/Qwen3-235B-A22B", "deepseek-ai/DeepSeek-R1"),
        category="cloud",
        env_vars=("HF_TOKEN",),
    ),
    ProviderSpec(
        id="nvidia",
        name="NVIDIA",
        desc="NVIDIA NIM 云推理 API",
        auth_modes=("api_key",),
        default_base_url="https://integrate.api.nvidia.com/v1",
        api_protocol="openai-completions",
        default_models=("nvidia/llama-3.3-nemotron-super-49b-v1",),
        category="cloud",
        env_vars=("NVIDIA_API_KEY",),
    ),
    ProviderSpec(
        id="baseten",
        name="Baseten",
        desc="Baseten Model APIs 高性能推理网关",
        auth_modes=("api_key",),
        default_base_url="https://inference.baseten.co/v1",
        api_protocol="openai-completions",
        default_models=("deepseek-ai/DeepSeek-R1-0528",),
        category="cloud",
        env_vars=("BASETEN_API_KEY",),
    ),
    ProviderSpec(
        id="opencode",
        name="OpenCode Zen",
        desc="OpenCode Zen 多模型编程路由",
        auth_modes=("api_key",),
        default_base_url="https://opencode.ai/zen/v1",
        api_protocol="openai-completions",
        default_models=("claude-sonnet-4.6", "gpt-5.4"),
        category="cloud",
        env_vars=("OPENCODE_API_KEY",),
    ),
    ProviderSpec(
        id="opencode-go",
        name="OpenCode Go",
        desc="OpenCode Go 低延迟订阅路由",
        auth_modes=("api_key",),
        default_base_url="https://opencode.ai/zen/go/v1",
        api_protocol="openai-completions",
        default_models=("claude-sonnet-4.6", "gpt-5.4"),
        category="cloud",
        env_vars=("OPENCODE_API_KEY",),
    ),
    # ---- 本地与代理 ----
    ProviderSpec(
        id="cliproxyapi",
        name="CLIProxyAPI / 本机代理",
        desc="连接本机或自建 CLIProxyAPI / OpenAI 兼容反代，并同步其模型与推理能力表",
        auth_modes=("proxy",),
        default_base_url="http://127.0.0.1:8317/v1",
        api_protocol="openai-completions",
        default_models=(),
        sync_models_on_connect=True,
        optional_key_in_proxy=True,
        category="local",
    ),
    ProviderSpec(
        id="ollama",
        name="Ollama (本机模型)",
        desc="连接本机或局域网 Ollama 服务（默认 127.0.0.1:11434/v1，密钥可选）",
        auth_modes=("proxy",),
        default_base_url="http://127.0.0.1:11434/v1",
        api_protocol="openai-completions",
        default_models=("qwen3:32b", "deepseek-r1:32b"),
        sync_models_on_connect=True,
        optional_key_in_proxy=True,
        category="local",
        env_vars=("OLLAMA_API_KEY", "OLLAMA_HOST"),
    ),
    ProviderSpec(
        id="lmstudio",
        name="LM Studio (本机推理)",
        desc="连接本机 LM Studio Local Server（默认 127.0.0.1:1234/v1，密钥可选）",
        auth_modes=("proxy",),
        default_base_url="http://127.0.0.1:1234/v1",
        api_protocol="openai-completions",
        default_models=(),
        sync_models_on_connect=True,
        optional_key_in_proxy=True,
        category="local",
        env_vars=("LMSTUDIO_API_KEY",),
    ),
)

PROVIDER_MAP: dict[str, ProviderSpec] = {}
PROVIDER_ALIASES: dict[str, str] = {}
for _spec in PROVIDERS:
    PROVIDER_MAP[_spec.id] = _spec
    for _alias in _spec.aliases:
        PROVIDER_MAP[_alias] = _spec
        PROVIDER_ALIASES[_alias] = _spec.id


def auth_path(root: Path | None = None) -> Path:
    """凭据文件落点：`AVA_AUTH_PATH` > `<root>/config/auth.local.json`。"""
    override = os.environ.get("AVA_AUTH_PATH", "").strip()
    if override:
        return Path(override)
    base = Path(root) if root is not None else paths.ROOT
    return base / "config" / "auth.local.json"


def _validate_https_url(url: str) -> str | None:
    """仅允许严格的 https:// 授权链接（非空主机名、无 userinfo、无空白与控制字符）。"""
    trimmed = (url or "").strip()
    if not trimmed or len(trimmed) > 4096 or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in trimmed):
        return None
    try:
        parsed = urllib.parse.urlsplit(trimmed)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return trimmed


def _try_open_browser(url: str) -> bool:
    safe = _validate_https_url(url)
    if not safe or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["open", "--", safe], capture_output=True, check=False, timeout=3)
            return r.returncode == 0
        import webbrowser

        return bool(webbrowser.open(safe))
    except Exception:
        return False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_provider(provider: str) -> ProviderSpec:
    raw = str(provider or "").strip()
    spec = PROVIDER_MAP.get(raw) or PROVIDER_MAP.get(raw.lower())
    if spec is None:
        import difflib

        close = difflib.get_close_matches(raw.lower(), sorted(PROVIDER_MAP.keys()), n=3, cutoff=0.5)
        hint = f"（相近：{'、'.join(close)}）" if close else "（输入 /login all 查看全部服务商）"
        raise AuthError(f"未知服务商：{provider}{hint}")
    return spec


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    low = host.strip().lower().rstrip(".")
    if low == "localhost":
        return True
    try:
        return ipaddress.ip_address(low.split("%")[0]).is_loopback
    except ValueError:
        return False


def _validate_base_url(url: str, *, allow_http: bool = False) -> str:
    clean = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise AuthError(f"base_url 须是合法 http/https 地址：{url!r}")
    if parsed.username is not None or parsed.password is not None:
        raise AuthError(f"base_url 不允许包含内嵌凭据（userinfo）：{url!r}")
    if parsed.scheme == "http" and not allow_http and not _is_loopback_host(parsed.hostname):
        raise AuthError(
            f"远程服务商 base_url 必须使用 https://（拒绝明文 http://{parsed.hostname}，以防 API Key 与对话内容明文泄露）"
        )
    return clean


def _keychain_enabled() -> bool:
    """判断当前环境是否应启用钥匙串主密钥加密。

    - 测试注入 `_KEYCHAIN_BACKEND` 时强制启用；
    - 环境变量 `AVA_AUTH_NO_KEYCHAIN=1` 显式关闭；
    - `AVA_AUTH_KEYCHAIN=1` 显式开启；
    - 默认仅在真实 macOS 生产/开发运行态启用（单测指定 `AVA_AUTH_PATH` 或 pytest 下默认不碰本机真实钥匙串）。
    """
    if _KEYCHAIN_BACKEND is not None:
        return True
    if os.environ.get("AVA_AUTH_NO_KEYCHAIN", "").strip() == "1":
        return False
    if os.environ.get("AVA_AUTH_KEYCHAIN", "").strip() == "1":
        return sys.platform == "darwin"
    if sys.platform != "darwin":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("AVA_AUTH_PATH", "").strip():
        return False
    return os.path.isfile(_KEYCHAIN_EXEC)


def _keychain_get_raw() -> tuple[str, str | None]:
    """返回 `("ok", b64_key)` / `("not_found", None)` / `("error", detail)`。

    严格区分退出码 44（条目不存在）与其他错误（36 钥匙串已锁、51/128 拒权、TimeoutExpired 超时），
    绝不将暂时不可用误判为「不存在」而触发覆盖旧密钥。
    """
    if _KEYCHAIN_BACKEND is not None:
        try:
            val = _KEYCHAIN_BACKEND("get", None)
        except (OSError, subprocess.SubprocessError, AuthError) as exc:
            return "error", str(exc)
        return ("ok", val) if val else ("not_found", None)
    try:
        res = subprocess.run(
            [_KEYCHAIN_EXEC, "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-a", _KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", f"读取钥匙串超时或失败（{type(exc).__name__}）"
    if res.returncode == 0:
        val = res.stdout.strip()
        if val:
            return "ok", val
        return "error", "钥匙串条目为空"
    if res.returncode == 44:
        return "not_found", None
    err_detail = _scrub_error_detail((res.stderr or "").strip())
    return "error", f"security find-generic-password 退出码 {res.returncode}" + (f": {err_detail}" if err_detail else "")


def _keychain_set_raw(b64_key: str) -> tuple[str, str | None]:
    """通过 `security -i` 从 stdin 写入主密钥（避免出现在 argv，且不传 `-U` 防止覆盖既有密钥）。

    返回 `("ok", None)` / `("duplicate", None)` / `("error", detail)`。
    """
    if not _BASE64URL_RE.fullmatch(b64_key):
        return "error", "主密钥编码非法"
    if _KEYCHAIN_BACKEND is not None:
        try:
            _KEYCHAIN_BACKEND("set", b64_key)
        except (OSError, subprocess.SubprocessError, AuthError) as exc:
            return "error", str(exc)
        return "ok", None
    stdin_cmd = f"add-generic-password -s {_KEYCHAIN_SERVICE} -a {_KEYCHAIN_ACCOUNT} -w {b64_key}\n"
    try:
        res = subprocess.run(
            [_KEYCHAIN_EXEC, "-i"],
            input=stdin_cmd,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", f"写入钥匙串超时或失败（{type(exc).__name__}）"
    if res.returncode == 0:
        return "ok", None
    stderr_low = (res.stderr or "").lower()
    if res.returncode == 45 or "already exists" in stderr_low or "-25299" in stderr_low:
        return "duplicate", None
    err_detail = _scrub_error_detail((res.stderr or "").strip())
    return "error", f"security add-generic-password 退出码 {res.returncode}" + (f": {err_detail}" if err_detail else "")


def _decode_master_key_b64(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthError(f"钥匙串中的 AVA 主密钥（{_KEYCHAIN_SERVICE}）编码损坏: {exc}") from None
    if len(key) != 32:
        raise AuthError(f"钥匙串中的 AVA 主密钥（{_KEYCHAIN_SERVICE}）长度异常（期望 32 字节，实得 {len(key)} 字节）")
    return key


def _load_master_key(*, create: bool) -> bytes | None:
    """从钥匙串读取或初始化 32 字节 AES-256 主密钥。

    - 未启用钥匙串加密时返回 None；
    - 已启用但钥匙串锁定/拒权/超时/损坏时直接抛 AuthError（fail-closed，绝不覆盖旧键、绝不静默降级明文）。
    """
    global _CACHED_MASTER_KEY
    if not _keychain_enabled():
        return None
    with _KEYCHAIN_LOCK:
        if _CACHED_MASTER_KEY is not None and _KEYCHAIN_BACKEND is None:
            return _CACHED_MASTER_KEY
        status, raw = _keychain_get_raw()
        if status == "error":
            raise AuthError(
                f"无法访问 macOS 登录钥匙串（{raw}）；请先解锁登录钥匙串后重试，已中止操作以防覆盖旧密钥或明文降级"
            )
        if status == "ok" and raw:
            key = _decode_master_key_b64(raw)
            if _KEYCHAIN_BACKEND is None:
                _CACHED_MASTER_KEY = key
            return key
        if not create:
            return None
        key = secrets.token_bytes(32)
        b64_key = base64.urlsafe_b64encode(key).decode("ascii")
        set_status, set_err = _keychain_set_raw(b64_key)
        if set_status == "ok":
            if _KEYCHAIN_BACKEND is None:
                _CACHED_MASTER_KEY = key
            return key
        if set_status == "duplicate":
            r_status, r_raw = _keychain_get_raw()
            if r_status == "ok" and r_raw:
                existing = _decode_master_key_b64(r_raw)
                if _KEYCHAIN_BACKEND is None:
                    _CACHED_MASTER_KEY = existing
                return existing
            raise AuthError(f"并发初始化钥匙串主密钥时回读失败（{r_raw}），已中止保存")
        raise AuthError(f"无法将主密钥写入 macOS 登录钥匙串（{set_err}）；已中止保存，拒绝明文降级")


def _encrypt_secret_value(plaintext: str, key: bytes, aad: bytes) -> str:
    if not plaintext or plaintext.startswith(_ENC_PREFIX):
        return plaintext
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return _ENC_PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def _decrypt_secret_value(value: str, key: bytes | None, aad: bytes) -> str:
    if not value.startswith(_ENC_PREFIX):
        return value
    if key is None:
        raise AuthError("认证文件已由钥匙串加密，但当前无法读取钥匙串主密钥（请解锁 macOS 登录钥匙串后重试）")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        blob = base64.urlsafe_b64decode(value[len(_ENC_PREFIX):].encode("ascii"))
        if len(blob) <= 12:
            raise ValueError("ciphertext too short")
        nonce, ct = blob[:12], blob[12:]
        return AESGCM(key).decrypt(nonce, ct, aad).decode("utf-8")
    except Exception as exc:
        raise AuthError(f"认证凭据解密失败（钥匙串密钥不匹配或文件已损坏）: {exc}") from None


def _store_encryption_flags(store: dict[str, Any]) -> tuple[bool, bool]:
    """扫描原始落盘 store 的敏感字段，返回 `(是否存在密文, 是否存在未加密明文)`。"""
    has_enc = False
    has_plain = False
    for entry in (store.get("providers") or {}).values():
        if not isinstance(entry, dict):
            continue
        for field_name in _ENCRYPTED_PROVIDER_FIELDS:
            v = entry.get(field_name)
            if isinstance(v, str) and v:
                if v.startswith(_ENC_PREFIX):
                    has_enc = True
                else:
                    has_plain = True
        extra = entry.get("extra_config")
        if isinstance(extra, dict):
            for field_name in _ENCRYPTED_EXTRA_FIELDS:
                v = extra.get(field_name)
                if isinstance(v, str) and v:
                    if v.startswith(_ENC_PREFIX):
                        has_enc = True
                    else:
                        has_plain = True
    for entry in (store.get("_pending") or {}).values():
        if not isinstance(entry, dict):
            continue
        for field_name in _ENCRYPTED_PENDING_FIELDS:
            v = entry.get(field_name)
            if isinstance(v, str) and v:
                if v.startswith(_ENC_PREFIX):
                    has_enc = True
                else:
                    has_plain = True
    return has_enc, has_plain


def _store_has_encrypted_values(store: dict[str, Any]) -> bool:
    return _store_encryption_flags(store)[0]


def _invalidate_secrets_cache(path: Path | None = None) -> None:
    with _SECRETS_CACHE_LOCK:
        if path is None:
            _SECRETS_POS_CACHE.clear()
            _SECRETS_NEG_CACHE.clear()
            return
        target = str(path)
        for k in [k for k in _SECRETS_POS_CACHE if k[0] == target]:
            _SECRETS_POS_CACHE.pop(k, None)
        for k in [k for k in _SECRETS_NEG_CACHE if k[0] == target]:
            _SECRETS_NEG_CACHE.pop(k, None)


def _transform_store_secrets(store: dict[str, Any], *, encrypt: bool, key: bytes | None) -> dict[str, Any]:
    out = copy.deepcopy(store)
    providers = out.get("providers")
    if isinstance(providers, dict):
        for pid, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            for field_name in _ENCRYPTED_PROVIDER_FIELDS:
                val = entry.get(field_name)
                if isinstance(val, str) and val:
                    aad = f"providers:{pid}:{field_name}".encode("utf-8")
                    if encrypt:
                        assert key is not None
                        entry[field_name] = _encrypt_secret_value(val, key, aad)
                    else:
                        entry[field_name] = _decrypt_secret_value(val, key, aad)
            extra = entry.get("extra_config")
            if isinstance(extra, dict):
                for field_name in _ENCRYPTED_EXTRA_FIELDS:
                    val = extra.get(field_name)
                    if isinstance(val, str) and val:
                        aad = f"extra:{pid}:{field_name}".encode("utf-8")
                        if encrypt:
                            assert key is not None
                            extra[field_name] = _encrypt_secret_value(val, key, aad)
                        else:
                            extra[field_name] = _decrypt_secret_value(val, key, aad)
    pending = out.get("_pending")
    if isinstance(pending, dict):
        for pid, entry in pending.items():
            if not isinstance(entry, dict):
                continue
            for field_name in _ENCRYPTED_PENDING_FIELDS:
                val = entry.get(field_name)
                if isinstance(val, str) and val:
                    aad = f"pending:{pid}:{field_name}".encode("utf-8")
                    if encrypt:
                        assert key is not None
                        entry[field_name] = _encrypt_secret_value(val, key, aad)
                    else:
                        entry[field_name] = _decrypt_secret_value(val, key, aad)
    return out


def collect_stored_secret_values(root: Path | None = None) -> list[str]:
    """返回已落盘认证凭据中的真实明文密钥列表（供 `tools.scrub_secrets` 脱敏，即使磁盘已加密也能生效）。

    只读无锁快速路径（依赖 `_write_store_unlocked` 的原子 `os.replace`）：
    - 文件不存在时直接返回空，绝不在只读脱敏路径创建 `config/` 目录或 `.lock` 文件；
    - 按 `(path, mtime_ns, size)` 缓存解密结果，避免并行只读工具争抢 `LOCK_EX`；
    - 钥匙串暂不可用时短 TTL 负缓存，防止并发超时风暴。
    """
    path = auth_path(root)
    try:
        st = path.stat()
    except OSError:
        return []
    cache_key = (str(path), st.st_mtime_ns, st.st_size, _keychain_enabled(), id(_KEYCHAIN_BACKEND))
    now = time.monotonic()
    with _SECRETS_CACHE_LOCK:
        cached = _SECRETS_POS_CACHE.get(cache_key)
        if cached is not None:
            return list(cached)
        neg_until = _SECRETS_NEG_CACHE.get(cache_key)
        if neg_until is not None and now < neg_until:
            return []
    try:
        store = _read_store_unlocked(path)
    except AuthError:
        with _SECRETS_CACHE_LOCK:
            if len(_SECRETS_NEG_CACHE) >= 32:
                _SECRETS_NEG_CACHE.clear()
            _SECRETS_NEG_CACHE[cache_key] = time.monotonic() + _SECRETS_NEG_TTL_S
        return []
    except OSError:
        return []
    found: list[str] = []
    for entry in (store.get("providers") or {}).values():
        if not isinstance(entry, dict):
            continue
        for field_name in _ENCRYPTED_PROVIDER_FIELDS:
            v = entry.get(field_name)
            if isinstance(v, str) and v:
                found.append(v)
        extra = entry.get("extra_config")
        if isinstance(extra, dict):
            for field_name in _ENCRYPTED_EXTRA_FIELDS:
                v = extra.get(field_name)
                if isinstance(v, str) and v:
                    found.append(v)
    for entry in (store.get("_pending") or {}).values():
        if not isinstance(entry, dict):
            continue
        for field_name in ("code_verifier", "device_code", "user_code"):
            v = entry.get(field_name)
            if isinstance(v, str) and v:
                found.append(v)
    with _SECRETS_CACHE_LOCK:
        if len(_SECRETS_POS_CACHE) >= 32:
            _SECRETS_POS_CACHE.clear()
        _SECRETS_POS_CACHE[cache_key] = tuple(found)
        _SECRETS_NEG_CACHE.pop(cache_key, None)
    return found


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
    store = {"providers": dict(providers), "_pending": dict(pending)}
    if _store_has_encrypted_values(store):
        key = _load_master_key(create=False)
        store = _transform_store_secrets(store, encrypt=False, key=key)
    return store


def _prune_expired_pending(store: dict[str, Any], now_ms: int) -> None:
    pending = store.get("_pending")
    if not isinstance(pending, dict):
        return
    expired = [
        k
        for k, v in pending.items()
        if not isinstance(v, dict)
        or (isinstance(v.get("expires_at_ms"), (int, float)) and int(v["expires_at_ms"]) <= now_ms)
        or (now_ms - int(v.get("created_at_ms") or v.get("created_at") or 0) > PENDING_TTL_MS)
    ]
    for k in expired:
        pending.pop(k, None)


def _write_store_unlocked(path: Path, data: dict[str, Any]) -> None:
    _prune_expired_pending(data, _now_ms())
    if _keychain_enabled():
        key = _load_master_key(create=True)
        assert key is not None
        payload_to_write = _transform_store_secrets(data, encrypt=True, key=key)
    else:
        payload_to_write = data
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    text = json.dumps(payload_to_write, ensure_ascii=False, indent=2) + "\n"
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
    _invalidate_secrets_cache(path)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise AuthError(f"认证端点拒绝 HTTP {code} 重定向，以防凭据泄露")


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _scrub_error_detail(raw: str) -> str:
    text = _ERR_BEARER_RE.sub(r"\1[已脱敏]", raw)
    text = _ERR_JWT_RE.sub("[已脱敏]", text)
    text = _ERR_SECRET_KV_RE.sub(r"\1[已脱敏]", text)
    return text[:300]


def _http_request_json(
    url: str,
    *,
    method: str = "POST",
    data_dict: dict[str, Any] | None = None,
    form_dict: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    allow_redirects: bool = False,
) -> tuple[int, dict[str, Any] | None, str]:
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
    opener = urllib.request.urlopen if allow_redirects else _NO_REDIRECT_OPENER.open
    try:
        with opener(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed_err: dict[str, Any] | None = None
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                parsed_err = obj
        except json.JSONDecodeError:
            parsed_err = None
        return int(exc.code), parsed_err, _scrub_error_detail(raw)
    except AuthError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(f"网络请求失败: {exc}") from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError(f"响应非 JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise AuthError("响应 JSON 不是对象")
    return status, parsed, raw


def _http_json(
    url: str,
    *,
    method: str = "POST",
    data_dict: dict[str, Any] | None = None,
    form_dict: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    allow_redirects: bool = False,
) -> dict[str, Any]:
    status, parsed, detail = _http_request_json(
        url,
        method=method,
        data_dict=data_dict,
        form_dict=form_dict,
        headers=headers,
        timeout=timeout,
        allow_redirects=allow_redirects,
    )
    if status >= 400 or parsed is None:
        raise AuthError(f"HTTP {status}: {detail}")
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


def _stable_host_uuid(path: Path) -> str:
    """派生稳定的本机 UUID（非密钥），用于 OpenAI ChatGPT OAuth `ext_agent_host_id`。"""
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest[:16]))


def _parse_callback_input(
    raw: str,
    expected_state: str | None,
    *,
    state_optional: bool = False,
) -> tuple[str, str | None, dict[str, str]]:
    text = raw.strip()
    if not text:
        raise AuthError("请粘贴授权回调链接或授权码")
    code = text
    state: str | None = None
    extra_params: dict[str, str] = {}
    if text.startswith(("http://", "https://")) or "?" in text:
        parsed = urllib.parse.urlparse(text)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("error"):
            desc = qs.get("error_description", [""])[0]
            raise AuthError(f"授权被拒绝：{qs['error'][0]} {desc}".strip())
        if qs.get("code"):
            code = qs["code"][0].strip()
        if qs.get("state"):
            state = qs["state"][0].strip()
        for k, vals in qs.items():
            if vals and isinstance(vals[0], str):
                extra_params[k] = vals[0].strip()
    elif "#" in text:
        c_part, s_part = text.split("#", 1)
        code = c_part.strip()
        state = s_part.strip() or None
    if not code:
        raise AuthError("未从输入中解析到授权码（code）")
    if expected_state:
        if not state_optional and not state:
            raise AuthError("OAuth 回调缺少 state 参数，请粘贴完整回调链接或 code#state 后重试")
        if state and state != expected_state:
            raise AuthError("OAuth state 校验不匹配，请重新发起授权")
    return code, state or expected_state, extra_params


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


MODELS_SYNC_TTL_MS = 60_000
MODELS_SYNC_FAIL_COOLDOWN_MS = 20_000
LIVE_MODELS_SYNC_TIMEOUT_S = 4.0
_ENV_MODELS_LOCK = threading.Lock()
_ENV_MODELS_CACHE: dict[tuple[str, str, str], tuple[int, list[str]]] = {}
_MODELS_SYNC_FAILURES: dict[tuple[str, str, str], int] = {}


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_int_ms(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value.strip()))
        except ValueError:
            return 0
    return 0


def _get_cached_env_models(provider_id: str, base_url: str, api_key: str) -> list[str] | None:
    if not api_key:
        return None
    cache_key = (provider_id, base_url.rstrip("/"), _secret_digest(api_key))
    with _ENV_MODELS_LOCK:
        cached = _ENV_MODELS_CACHE.get(cache_key)
        return list(cached[1]) if cached and cached[1] else None


def _sync_google_generative_models(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 5.0,
) -> list[str]:
    """查询 Google Gemini REST `/models` 端点并提取支持 `generateContent` 的模型 ID。"""
    base = f"{base_url.rstrip('/')}/models"
    headers = {"Accept": "application/json", "x-goog-api-key": api_key}
    ids: list[str] = []
    seen: set[str] = set()
    page_token: str | None = None
    deadline = time.monotonic() + max(0.05, float(timeout))
    for _ in range(5):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AuthError("Google 模型列表同步超时")
        params = {"pageSize": "1000"}
        if page_token:
            params["pageToken"] = page_token
        url = f"{base}?{urllib.parse.urlencode(params)}"
        data = _http_json(url, method="GET", headers=headers, timeout=remaining)
        raw_items = data.get("models")
        if not isinstance(raw_items, list):
            break
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            methods = item.get("supportedGenerationMethods")
            if not isinstance(methods, list) or "generateContent" not in methods:
                continue
            raw_name = item.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            mid = raw_name.strip().removeprefix("models/")
            if mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        next_token = data.get("nextPageToken")
        if not isinstance(next_token, str) or not next_token.strip():
            break
        page_token = next_token.strip()
    return sorted(ids)


def _sync_api_key_models(spec: ProviderSpec, base_url: str, api_key: str) -> list[str]:
    """为 API Key 直连服务商同步可用模型列表；探测失败时回退到 `spec.default_models`。"""
    if spec.api_protocol == "google-generative-ai" and api_key:
        try:
            synced = _sync_google_generative_models(base_url, api_key)
            if synced:
                return synced
        except (AuthError, OSError, ValueError):
            pass
    return list(spec.default_models)


_SECRET_EXTRA_KEYS = frozenset({"aws_secret_access_key", "aws_session_token"})
_MASKED_EXTRA_KEYS = frozenset({"aws_access_key_id", "credentials_path", "enterprise_domain", "project_id"})
_NON_SECRET_ENV_VARS = frozenset({
    "OLLAMA_HOST",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_ACCESS_KEY_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_OPENAI_RESOURCE_NAME",
    "AZURE_OPENAI_BASE_URL",
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    "AZURE_OPENAI_API_VERSION",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_GATEWAY_ID",
})
_GHE_HOST_RE = re.compile(r"^[a-z0-9.-]+(?::\d{1,5})?$")


def auth_secret_env_names() -> tuple[str, ...]:
    """返回所有服务商声明的密钥类环境变量名（供 N50 从子进程剥离与脱敏）。"""
    names: set[str] = set()
    for spec in PROVIDERS:
        for env_name in spec.env_vars:
            if env_name not in _NON_SECRET_ENV_VARS:
                names.add(env_name)
        for ef in spec.extra_fields:
            if ef.secret and ef.env_var:
                names.add(ef.env_var)
    return tuple(sorted(names))


def _normalize_ghe_domain(raw: str | None) -> str:
    trimmed = str(raw or "").strip()
    if not trimmed:
        return "github.com"
    candidate = trimmed if "://" in trimmed else f"https://{trimmed}"
    parsed = urllib.parse.urlparse(candidate)
    host = (parsed.netloc or "").strip().lower()
    if not host or "/" in host or not _GHE_HOST_RE.match(host):
        raise AuthError(f"GitHub Enterprise 域名格式不合法：{raw!r}")
    return host


def _copilot_oauth_url(template: str, enterprise_domain: str | None) -> str:
    domain = _normalize_ghe_domain(enterprise_domain)
    return template.replace("{domain}", domain)


def _validate_copilot_api_url(url_str: str, enterprise_domain: str | None) -> str:
    clean = _validate_base_url(url_str, allow_http=False)
    parsed = urllib.parse.urlparse(clean)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_suffixes = ["githubcopilot.com"]
    if enterprise_domain:
        ent = _normalize_ghe_domain(enterprise_domain).split(":", 1)[0]
        if ent:
            allowed_suffixes.append(ent)
    if not any(host == suf or host.endswith("." + suf) for suf in allowed_suffixes):
        raise AuthError(f"GitHub Copilot 返回了不受信任的 API 端点：{host!r}")
    return clean


def _copilot_base_url_from_token(token: str, enterprise_domain: str | None) -> str | None:
    match = re.search(r"proxy-ep=([^;\s]+)", token)
    if match:
        proxy_host = match.group(1).strip()
        api_host = re.sub(r"^proxy\.", "api.", proxy_host)
        return _validate_copilot_api_url(f"https://{api_host}", enterprise_domain)
    if enterprise_domain:
        domain = _normalize_ghe_domain(enterprise_domain)
        return _validate_copilot_api_url(f"https://copilot-api.{domain}", enterprise_domain)
    return None


def _vertex_project_id_from_file(cred_path_raw: str | None) -> str | None:
    raw = str(cred_path_raw or "").strip()
    if raw:
        cred_path = Path(raw).expanduser()
    else:
        cred_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if not cred_path.is_file():
        return None
    try:
        info = json.loads(cred_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    for key in ("project_id", "quota_project_id"):
        val = str(info.get(key) or "").strip()
        if val:
            return val
    return None


def _normalize_extra_config(spec: ProviderSpec, raw_extra: Any) -> dict[str, str]:
    src = raw_extra if isinstance(raw_extra, dict) else {}
    out: dict[str, str] = {}
    for ef in spec.extra_fields:
        val = str(src.get(ef.key) or "").strip()
        if not val and ef.env_var:
            val = os.environ.get(ef.env_var, "").strip()
        if not val and ef.default:
            val = ef.default
        if val:
            out[ef.key] = val
    return out


def _sanitize_extra_config(spec: ProviderSpec, raw_extra: Any) -> dict[str, str]:
    if not isinstance(raw_extra, dict):
        return {}
    secret_keys = {ef.key for ef in spec.extra_fields if ef.secret} | _SECRET_EXTRA_KEYS | _MASKED_EXTRA_KEYS
    clean: dict[str, str] = {}
    for k, v in raw_extra.items():
        if not isinstance(k, str) or not isinstance(v, str) or not v.strip():
            continue
        clean[k] = (_mask_secret(v) or "****") if k in secret_keys else v.strip()
    return clean


def _normalize_azure_base_url(url_str: str) -> str:
    clean = _validate_base_url(url_str)
    parsed = urllib.parse.urlparse(clean)
    host = (parsed.hostname or "").lower()
    is_azure = host.endswith((".openai.azure.com", ".cognitiveservices.azure.com", ".ai.azure.com"))
    path_norm = parsed.path.rstrip("/")
    if is_azure and path_norm in ("", "/", "/openai", "/openai/v1/responses"):
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/openai/v1", "", "", "")).rstrip("/")
    return clean


def _resolve_effective_base_url(
    spec: ProviderSpec,
    raw_base: str,
    extra: dict[str, str],
) -> str:
    allow_http = spec.category == "local"
    base = raw_base.strip() or spec.default_base_url
    if spec.id == "cloudflare-ai-gateway":
        acc = extra.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        gw = extra.get("gateway_id") or os.environ.get("CLOUDFLARE_GATEWAY_ID", "").strip()
        if not acc or not gw:
            raise AuthError("Cloudflare AI Gateway 需要填写 Account ID 和 Gateway ID")
        if not _CLOUD_ID_RE.match(acc):
            raise AuthError(f"Cloudflare Account ID 格式不合法：{acc!r}")
        if not _CLOUD_ID_RE.match(gw):
            raise AuthError(f"Cloudflare Gateway ID 格式不合法：{gw!r}")
        base = base.replace("{account_id}", acc).replace("{gateway_id}", gw)
        return _validate_base_url(base, allow_http=allow_http)
    if spec.id == "cloudflare-workers-ai":
        acc = extra.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        if not acc:
            raise AuthError("Cloudflare Workers AI 需要填写 Account ID")
        if not _CLOUD_ID_RE.match(acc):
            raise AuthError(f"Cloudflare Account ID 格式不合法：{acc!r}")
        base = base.replace("{account_id}", acc)
        return _validate_base_url(base, allow_http=allow_http)
    if spec.id == "amazon-bedrock":
        region = extra.get("aws_region") or os.environ.get("AWS_REGION", "").strip() or "us-east-1"
        if not _CLOUD_REGION_RE.match(region):
            raise AuthError(f"AWS Region 格式不合法：{region!r}")
        if not raw_base.strip() or base == spec.default_base_url:
            base = f"https://bedrock-runtime.{region}.amazonaws.com"
        return _validate_base_url(base, allow_http=allow_http)
    if spec.id == "google-vertex":
        loc = extra.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or "us-central1"
        if not _CLOUD_REGION_RE.match(loc):
            raise AuthError(f"GCP Location 格式不合法：{loc!r}")
        if not raw_base.strip() or base == spec.default_base_url:
            if loc == "global":
                base = "https://aiplatform.googleapis.com/v1"
            else:
                base = f"https://{loc}-aiplatform.googleapis.com/v1"
        return _validate_base_url(base, allow_http=allow_http)
    if spec.id == "azure":
        res_name = extra.get("resource_name") or os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "").strip()
        env_base = os.environ.get("AZURE_OPENAI_BASE_URL", "").strip()
        if (not raw_base.strip() or "YOUR-RESOURCE" in base) and env_base:
            base = env_base
        elif (not raw_base.strip() or "YOUR-RESOURCE" in base) and res_name:
            if not _AZURE_RESOURCE_RE.match(res_name):
                raise AuthError(f"Azure Resource Name 格式不合法：{res_name!r}")
            base = f"https://{res_name}.openai.azure.com/openai/v1"
        elif "YOUR-RESOURCE" in base:
            raise AuthError("Azure OpenAI 请填写 Resource Name 或自定义 Base URL")
        return _normalize_azure_base_url(base)
    if spec.id == "ollama" and (not raw_base.strip() or base == spec.default_base_url):
        host = os.environ.get("OLLAMA_HOST", "").strip()
        if host:
            if not host.startswith(("http://", "https://")):
                host = f"http://{host}"
            host = host.rstrip("/")
            base = host if host.endswith("/v1") else f"{host}/v1"
    return _validate_base_url(base, allow_http=allow_http)


def _detect_env_status(spec: ProviderSpec) -> tuple[bool, str | None, str | None]:
    """检测当前进程环境变量是否已为该服务商提供可用凭据（不回传明文）。"""
    for env_name in spec.env_vars:
        val = os.environ.get(env_name, "").strip()
        if not val:
            continue
        if env_name == "OLLAMA_HOST":
            return True, env_name, None
        if env_name in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
            return True, env_name, None
        if env_name == "AWS_PROFILE":
            return True, env_name, None
        if env_name == "AWS_ACCESS_KEY_ID" and not os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip():
            continue
        return True, env_name, _mask_secret(val)
    if spec.id == "google-vertex":
        adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if adc.is_file() and os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip():
            return True, "ADC + GOOGLE_CLOUD_PROJECT", None
    return False, None, None


_COPILOT_ENV_CACHE: dict[tuple[str, str], tuple[str, str, int]] = {}
_COPILOT_ENV_LOCK = threading.Lock()


def _validate_token_scope(spec: ProviderSpec, tok: dict[str, Any]) -> None:
    req_scope = str(spec.oauth.get("required_scope") or "").strip()
    if not req_scope:
        return
    raw_scope = tok.get("scope")
    granted = str(raw_scope).split() if isinstance(raw_scope, str) else []
    if req_scope not in granted:
        raise AuthError(
            f"{spec.name} OAuth 授权缺少必要权限 {req_scope}，请重新执行 /login {spec.id} 并完整勾选权限"
        )


def _exchange_oauth_code(
    spec: ProviderSpec,
    code: str,
    verifier: str,
    state: str | None,
    *,
    issued_client_id: str | None = None,
    redirect_uri: str | None = None,
) -> dict[str, Any]:
    oauth = spec.oauth
    token_url = str(oauth["token_url"])
    if oauth.get("pkce_kind") == "openrouter_key":
        return _http_json(
            token_url,
            method="POST",
            data_dict={
                "code": code,
                "code_verifier": verifier,
                "code_challenge_method": "S256",
            },
            allow_redirects=False,
        )
    client_id = issued_client_id or str(oauth["client_id"])
    effective_redirect = redirect_uri if redirect_uri is not None else str(oauth["redirect_uri"])
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": effective_redirect,
        "code_verifier": verifier,
    }
    if oauth.get("resource"):
        payload["resource"] = str(oauth["resource"])
    if oauth.get("token_content_type") == "json":
        if state:
            payload["state"] = state
        return _http_json(
            token_url,
            method="POST",
            data_dict=payload,
            headers=oauth.get("extra_headers"),
            allow_redirects=False,
        )
    return _http_json(token_url, method="POST", form_dict=payload, allow_redirects=False)


def _refresh_oauth_token(spec: ProviderSpec, entry: dict[str, Any]) -> dict[str, Any]:
    oauth = spec.oauth
    now = _now_ms()
    if spec.id == "github-copilot":
        gh_token = str(entry.get("refresh_token") or "").strip()
        if not gh_token:
            raise AuthError("GitHub Copilot 缺少 GitHub 刷新令牌，请重新登录")
        extra_cfg = entry.get("extra_config") if isinstance(entry.get("extra_config"), dict) else {}
        ent_domain = str(extra_cfg.get("enterprise_domain") or "").strip() or None
        copilot_url = _copilot_oauth_url(str(oauth["copilot_token_url"]), ent_domain)
        cop = _http_json(
            copilot_url,
            method="GET",
            headers={
                "Authorization": f"Bearer {gh_token}",
                **spec.extra_headers,
            },
            allow_redirects=False,
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
        raw_api_ep = endpoints.get("api")
        validated_ep = (
            _validate_copilot_api_url(str(raw_api_ep), ent_domain)
            if isinstance(raw_api_ep, str) and raw_api_ep.strip()
            else None
        )
        base_url = str(
            validated_ep
            or _copilot_base_url_from_token(token, ent_domain)
            or entry.get("base_url")
            or spec.default_base_url
        ).rstrip("/")
        updated = dict(entry)
        updated["type"] = "oauth"
        updated["access_token"] = token
        updated["refresh_token"] = gh_token
        updated["expires_at_ms"] = exp_ms
        updated["base_url"] = base_url
        updated["updated_at_ms"] = now
        return updated

    if oauth.get("exchange_key_url"):
        ident_tok = str(entry.get("refresh_token") or "").strip()
        if not ident_tok:
            raise AuthError(f"{spec.name} 会话已失效，请重新完成设备码授权")
        minted = _http_json(
            str(oauth["exchange_key_url"]),
            method="POST",
            data_dict={},
            headers={
                "Authorization": f"Bearer {ident_tok}",
                "x-api-version": "1.0.0",
            },
            allow_redirects=False,
        )
        api_key = str(minted.get("api_key") or "").strip()
        if not api_key:
            raise AuthError(f"{spec.name} 未能签发 Model API Key，请重新登录")
        updated = dict(entry)
        updated["access_token"] = api_key
        updated["expires_at_ms"] = now + 20 * 3600 * 1000
        updated["updated_at_ms"] = now
        return updated

    refresh_tok = str(entry.get("refresh_token") or "").strip()
    token_url = str(oauth.get("token_url") or "").strip()
    client_id = str(entry.get("oauth_client_id") or oauth.get("client_id") or "").strip()
    if not refresh_tok or not token_url:
        raise AuthError(f"{spec.name} 令牌已过期且无法续期，请重新登录")
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
        "client_id": client_id,
    }
    if oauth.get("resource"):
        body["resource"] = str(oauth["resource"])
    if oauth.get("token_content_type") == "json":
        res = _http_json(token_url, method="POST", data_dict=body, headers=oauth.get("extra_headers"), allow_redirects=False)
    else:
        res = _http_json(token_url, method="POST", form_dict=body, allow_redirects=False)
    if "scope" in res:
        _validate_token_scope(spec, res)
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
        secret_candidate = entry.get("api_key") or entry.get("access_token")
        if not secret_candidate and isinstance(entry.get("extra_config"), dict):
            ec = entry["extra_config"]
            secret_candidate = ec.get("aws_access_key_id") or ec.get("project_id") or ec.get("aws_profile")
        masked = _mask_secret(secret_candidate)
    env_configured, env_source, masked_env = _detect_env_status(spec)
    return {
        "id": spec.id,
        "name": spec.name,
        "desc": spec.desc,
        "category": spec.category,
        "auth_modes": list(spec.auth_modes),
        "default_base_url": spec.default_base_url,
        "api_protocol": spec.api_protocol,
        "supports_custom_base_url": spec.supports_custom_base_url,
        "optional_key_in_proxy": spec.optional_key_in_proxy,
        "oauth_label": spec.oauth_label,
        "env_vars": list(spec.env_vars),
        "env_configured": env_configured,
        "env_source": env_source,
        "masked_env_secret": masked_env,
        "extra_fields": [ef.to_dict() for ef in spec.extra_fields],
        "aliases": list(spec.aliases),
        "connected": connected,
        "auth_type": entry.get("type") if connected and isinstance(entry, dict) else None,
        "base_url": entry.get("base_url", spec.default_base_url) if connected and isinstance(entry, dict) else spec.default_base_url,
        "masked_secret": masked,
        "extra_config": _sanitize_extra_config(spec, entry.get("extra_config") if connected and isinstance(entry, dict) else None),
        "expires_at_ms": entry.get("expires_at_ms") if connected and isinstance(entry, dict) else None,
        "models": models,
        "default_models": list(spec.default_models),
    }


def _stored_entry_for_spec(providers_data: dict[str, Any], spec: ProviderSpec) -> tuple[str, dict[str, Any]] | None:
    for key in (spec.id, *spec.aliases):
        entry = providers_data.get(key)
        if isinstance(entry, dict) and entry.get("type"):
            return key, entry
    return None


def _read_raw_store_dict(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_auth_status(root: Path | None = None) -> dict[str, Any]:
    """返回脱敏后的全部支持服务商状态（绝不返回真实密钥/令牌）。"""
    path = auth_path(root)
    with _auth_lock(path):
        raw_disk = _read_raw_store_dict(path)
        store = _read_store_unlocked(path)
    _, has_plaintext = _store_encryption_flags(raw_disk)
    storage_encrypted = _keychain_enabled() and not has_plaintext
    providers_data = store.get("providers", {})
    rows = []
    for spec in PROVIDERS:
        found = _stored_entry_for_spec(providers_data, spec)
        rows.append(_public_provider_row(spec, found[1] if found else None, root))
    connected_count = sum(1 for r in rows if r["connected"])
    return {
        "ok": True,
        "auth_file": str(path),
        "storage_encrypted": storage_encrypted,
        "connected_count": connected_count,
        "providers": rows,
    }


def start_auth_flow(
    provider: str,
    mode: str | None = None,
    *,
    extra_config: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """发起某家服务商的登录流程：PKCE 授权链接 / Device Code / API Key / 代理参数说明。"""
    spec = _require_provider(provider)
    chosen = mode or spec.auth_modes[0]
    if chosen not in ALLOWED_AUTH_MODES or chosen not in spec.auth_modes:
        raise AuthError(f"{spec.name} 不支持认证方式：{chosen}")

    if chosen in ("api_key", "proxy"):
        env_configured, env_source, masked_env = _detect_env_status(spec)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": chosen,
            "default_base_url": spec.default_base_url,
            "api_protocol": spec.api_protocol,
            "extra_fields": [ef.to_dict() for ef in spec.extra_fields],
            "env_vars": list(spec.env_vars),
            "env_configured": env_configured,
            "env_source": env_source,
            "masked_env_secret": masked_env,
        }

    path = auth_path(root)
    now = _now_ms()
    if chosen == "oauth_pkce":
        oauth = spec.oauth
        verifier, challenge = _pkce_pair()
        state = verifier if oauth.get("state_is_verifier") else secrets.token_urlsafe(24)
        if oauth.get("pkce_kind") == "openrouter_key":
            params = {
                "callback_url": str(oauth["redirect_uri"]),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        else:
            params = {
                "response_type": "code",
                "client_id": str(oauth["client_id"]),
                "redirect_uri": str(oauth["redirect_uri"]),
                "scope": str(oauth["scopes"]),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
            if oauth.get("resource"):
                params["resource"] = str(oauth["resource"])
            if oauth.get("include_host_id"):
                params["ext_agent_host_id"] = f"urn:uuid:{_stable_host_uuid(path)}"
            if oauth.get("include_nonce"):
                params["nonce"] = secrets.token_urlsafe(24)
            for k, v in (oauth.get("extra_authorize_params") or {}).items():
                params[str(k)] = str(v)
        authorize_url = _validate_https_url(f"{oauth['authorize_url']}?{urllib.parse.urlencode(params)}")
        if not authorize_url:
            raise AuthError(f"{spec.name} 生成了非法的授权地址")
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            _prune_expired_pending(store, now)
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

    # oauth_device (GitHub Copilot / OpenAI Codex / Kimi Coding / xAI / Meta)
    oauth = spec.oauth
    extra_cfg = _normalize_extra_config(spec, extra_config)
    ent_domain = extra_cfg.get("enterprise_domain") or None
    client_id = str(oauth["client_id"])
    scopes = str(oauth.get("scopes") or "").strip()
    dev_payload: dict[str, str] = {"client_id": client_id}
    if scopes:
        dev_payload["scope"] = scopes
    dev_url = str(oauth["device_code_url"])
    if spec.id == "github-copilot":
        dev_url = _copilot_oauth_url(dev_url, ent_domain)
    if oauth.get("device_content_type") == "form":
        dev = _http_json(dev_url, method="POST", form_dict=dev_payload, allow_redirects=False)
    else:
        dev = _http_json(dev_url, method="POST", data_dict=dev_payload, allow_redirects=False)

    device_code = str(dev.get("device_code") or dev.get("device_auth_id") or "").strip()
    user_code = str(dev.get("user_code") or "").strip()
    raw_verification_uri = str(
        dev.get("verification_uri_complete")
        or dev.get("verification_uri")
        or oauth.get("device_verification_uri")
        or ""
    ).strip()
    raw_interval = dev.get("interval")
    interval_s = int(raw_interval) if isinstance(raw_interval, (int, float)) else 5
    raw_exp = dev.get("expires_in")
    expires_in_s = int(raw_exp) if isinstance(raw_exp, (int, float)) else 900
    if not device_code or not user_code or not raw_verification_uri:
        raise AuthError(f"{spec.name} 未返回有效的设备码信息")
    verification_uri = _validate_https_url(raw_verification_uri)
    if not verification_uri:
        raise AuthError(f"{spec.name} 返回了非 https 授权地址，已拒绝")
    pending_entry: dict[str, Any] = {
        "mode": "oauth_device",
        "device_code": device_code,
        "user_code": user_code,
        "interval_s": interval_s,
        "expires_at_ms": now + expires_in_s * 1000,
        "created_at_ms": now,
    }
    if extra_cfg:
        pending_entry["extra_config"] = extra_cfg
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        _prune_expired_pending(store, now)
        store.setdefault("_pending", {})[spec.id] = pending_entry
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


def _backfill_vertex_extra(spec: ProviderSpec, extra_cfg: dict[str, str]) -> dict[str, str]:
    if spec.id != "google-vertex" or extra_cfg.get("project_id"):
        return extra_cfg
    inferred = _vertex_project_id_from_file(extra_cfg.get("credentials_path"))
    if inferred:
        return {**extra_cfg, "project_id": inferred}
    return extra_cfg


def _complete_cloud_entry_allowed_empty_key(spec: ProviderSpec, extra_cfg: dict[str, str]) -> bool:
    if spec.id == "amazon-bedrock":
        has_iam = bool(extra_cfg.get("aws_access_key_id") and extra_cfg.get("aws_secret_access_key"))
        has_profile = bool(extra_cfg.get("aws_profile"))
        has_env = bool(
            os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip()
            or (os.environ.get("AWS_ACCESS_KEY_ID", "").strip() and os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip())
            or os.environ.get("AWS_PROFILE", "").strip()
        )
        return has_iam or has_profile or has_env
    if spec.id == "google-vertex":
        return bool(extra_cfg.get("project_id") and (extra_cfg.get("location") or "us-central1"))
    return False


def _save_provider_entry(path: Path, spec: ProviderSpec, entry: dict[str, Any]) -> None:
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        providers_map = store.setdefault("providers", {})
        providers_map[spec.id] = entry
        for alias in spec.aliases:
            providers_map.pop(alias, None)
        store.get("_pending", {}).pop(spec.id, None)
        _write_store_unlocked(path, store)


def complete_auth_flow(
    provider: str,
    payload: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """完成认证并落盘 `config/auth.local.json`（0o600）；本地代理模式可同步 `/models` 到 `models.local.json`。"""
    spec = _require_provider(provider)
    data = dict(payload or {})
    mode = str(data.get("mode") or spec.auth_modes[0]).strip()
    if mode not in ALLOWED_AUTH_MODES or mode not in spec.auth_modes:
        raise AuthError(f"{spec.name} 不支持认证方式：{mode}")

    path = auth_path(root)
    now = _now_ms()
    extra_cfg = _backfill_vertex_extra(spec, _normalize_extra_config(spec, data.get("extra_config")))

    if mode == "proxy":
        raw_base = str(data.get("base_url") or "").strip()
        base_url = _resolve_effective_base_url(spec, raw_base, extra_cfg)
        api_key = str(data.get("api_key") or "").strip()
        if data.get("skip_probe"):
            models = [str(m).strip() for m in (data.get("models") or []) if str(m).strip()]
        else:
            models = _sync_proxy_models(base_url, api_key, root)
        if not models and spec.default_models:
            models = list(spec.default_models)
        entry: dict[str, Any] = {
            "type": "proxy",
            "base_url": base_url,
            "api_protocol": spec.api_protocol,
            "models": models,
            "updated_at_ms": now,
            "models_synced_at_ms": now,
        }
        if api_key:
            entry["api_key"] = api_key
        if extra_cfg:
            entry["extra_config"] = extra_cfg
        _save_provider_entry(path, spec, entry)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "proxy",
            "models": models,
            "text": f"已连接 {spec.name}（同步 {len(models)} 个模型）",
        }

    if mode == "api_key":
        for ef in spec.extra_fields:
            if ef.required and not extra_cfg.get(ef.key):
                raise AuthError(f"请填写 {ef.label}")
        api_key = str(data.get("api_key") or "").strip()
        if not api_key and not _complete_cloud_entry_allowed_empty_key(spec, extra_cfg):
            if spec.id == "google-vertex" and extra_cfg.get("credentials_path"):
                raise AuthError("请填写 GCP Project ID（或确保凭据 JSON 中包含 project_id / quota_project_id）")
            raise AuthError("请填写 API Key")
        raw_base = str(data.get("base_url") or "").strip()
        base_url = _resolve_effective_base_url(spec, raw_base, extra_cfg)
        models = list(spec.default_models) if data.get("skip_probe") else _sync_api_key_models(spec, base_url, api_key)
        if spec.id == "github-copilot":
            base_entry: dict[str, Any] = {
                "type": "oauth",
                "refresh_token": api_key,
                "access_token": "",
                "expires_at_ms": 0,
                "base_url": base_url,
                "api_protocol": spec.api_protocol,
                "models": models,
                "updated_at_ms": now,
                "models_synced_at_ms": now,
            }
            if extra_cfg:
                base_entry["extra_config"] = extra_cfg
            entry = _refresh_oauth_token(spec, base_entry)
        else:
            entry = {
                "type": "api_key",
                "base_url": base_url,
                "api_protocol": spec.api_protocol,
                "models": models,
                "updated_at_ms": now,
                "models_synced_at_ms": now,
            }
            if api_key:
                entry["api_key"] = api_key
            if extra_cfg:
                entry["extra_config"] = extra_cfg
        _save_provider_entry(path, spec, entry)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "api_key",
            "models": models,
            "text": f"已保存 {spec.name} 凭据",
        }

    if mode == "oauth_pkce":
        raw_code = str(data.get("code") or data.get("callback_url") or "").strip()
        oauth = spec.oauth
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            pending = store.get("_pending", {}).get(spec.id)
            if not isinstance(pending, dict) or pending.get("mode") != "oauth_pkce":
                raise AuthError("尚未发起 OAuth 授权或授权会话已失效，请重新发起登录")
            if now - int(pending.get("created_at_ms") or 0) > PENDING_TTL_MS:
                raise AuthError("OAuth 授权会话已过期（超过 30 分钟），请重新发起登录")
            if pending.get("exchanging") and (now - int(pending.get("exchanging_at_ms") or pending.get("created_at_ms") or 0)) <= 120_000:
                raise AuthError(f"{spec.name} 授权换票正在进行中，请勿重复提交")
            code, state, cb_params = _parse_callback_input(
                raw_code,
                pending.get("state"),
                state_optional=bool(oauth.get("state_optional")),
            )
            issued_cid = cb_params.get("client_id") if oauth.get("dynamic_client_id") else None
            if oauth.get("dynamic_client_id"):
                if not issued_cid:
                    raise AuthError(f"{spec.name} OAuth 回调链接缺少动态签发的 client_id 参数，请粘贴完整回调 URL")
                if not _DYNAMIC_CLIENT_ID_RE.match(issued_cid):
                    raise AuthError(f"{spec.name} OAuth 回调中的 client_id 格式不合法")
            verifier = str(pending["code_verifier"])
            pending["exchanging"] = True
            pending["exchanging_at_ms"] = now
            _write_store_unlocked(path, store)
        try:
            tok = _exchange_oauth_code(
                spec,
                code,
                verifier,
                state,
                issued_client_id=issued_cid,
            )
            if oauth.get("pkce_kind") == "openrouter_key":
                permanent_key = str(tok.get("key") or "").strip()
                if not permanent_key:
                    raise AuthError("OpenRouter OAuth 未返回永久 API Key")
                entry = {
                    "type": "api_key",
                    "api_key": permanent_key,
                    "base_url": spec.default_base_url,
                    "api_protocol": spec.api_protocol,
                    "models": list(spec.default_models),
                    "updated_at_ms": now,
                }
            else:
                _validate_token_scope(spec, tok)
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
                if issued_cid:
                    entry["oauth_client_id"] = issued_cid
                if spec.id == "openai-codex":
                    acc = _jwt_chatgpt_account_id(access)
                    if acc:
                        entry["account_id"] = acc
        except BaseException:
            with _auth_lock(path):
                store = _read_store_unlocked(path)
                cur_pend = store.get("_pending", {}).get(spec.id)
                if isinstance(cur_pend, dict):
                    cur_pend.pop("exchanging", None)
                    cur_pend.pop("exchanging_at_ms", None)
                    _write_store_unlocked(path, store)
            raise
        _save_provider_entry(path, spec, entry)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "oauth_pkce",
            "models": list(spec.default_models),
            "text": f"已通过 OAuth 登录 {spec.name}",
        }

    # oauth_device
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        pending = store.get("_pending", {}).get(spec.id)
    if not isinstance(pending, dict) or pending.get("mode") != "oauth_device":
        raise AuthError(f"尚未发起 {spec.name} 设备码授权，请先获取设备码")
    if int(pending.get("expires_at_ms") or 0) <= now:
        raise AuthError(f"{spec.name} 设备码已过期，请重新获取")
    pending_extra = pending.get("extra_config") if isinstance(pending.get("extra_config"), dict) else {}
    merged_extra = {**pending_extra, **extra_cfg} if (pending_extra or extra_cfg) else {}
    ent_domain = str(merged_extra.get("enterprise_domain") or "").strip() or None

    oauth = spec.oauth
    token_url = str(oauth.get("device_token_url") or oauth["token_url"])
    if spec.id == "github-copilot":
        token_url = _copilot_oauth_url(token_url, ent_domain)
    if oauth.get("device_content_type") == "json":
        req_body = {
            "client_id": str(oauth["client_id"]),
            "user_code": str(pending.get("user_code") or ""),
            "device_auth_id": str(pending["device_code"]),
        }
        status_code, tok_obj, detail = _http_request_json(
            token_url, method="POST", data_dict=req_body, allow_redirects=False,
        )
    elif oauth.get("token_content_type") == "form":
        req_body = {
            "client_id": str(oauth["client_id"]),
            "device_code": str(pending["device_code"]),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        status_code, tok_obj, detail = _http_request_json(
            token_url, method="POST", form_dict=req_body, allow_redirects=False,
        )
    else:
        req_body = {
            "client_id": str(oauth["client_id"]),
            "device_code": str(pending["device_code"]),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        status_code, tok_obj, detail = _http_request_json(
            token_url, method="POST", data_dict=req_body, allow_redirects=False,
        )

    tok: dict[str, Any] = tok_obj if isinstance(tok_obj, dict) else {}
    raw_err = tok.get("error")
    err_code = raw_err.get("code") if isinstance(raw_err, dict) else raw_err
    if spec.id == "openai-codex" and status_code in (403, 404):
        err_code = err_code if err_code == "deviceauth_authorization_pending" else "authorization_pending"

    if err_code in ("authorization_pending", "deviceauth_authorization_pending", "slow_down"):
        cur_interval = int(pending.get("interval_s") or 5)
        if err_code == "slow_down":
            srv_interval = tok.get("interval")
            new_interval = max(cur_interval + 5, int(srv_interval) if isinstance(srv_interval, (int, float)) else 0)
            with _auth_lock(path):
                store = _read_store_unlocked(path)
                cur_pend = store.get("_pending", {}).get(spec.id)
                if isinstance(cur_pend, dict):
                    cur_pend["interval_s"] = new_interval
                    _write_store_unlocked(path, store)
        else:
            new_interval = cur_interval
        return {
            "ok": False,
            "pending": True,
            "provider": spec.id,
            "error": str(err_code),
            "interval_s": new_interval,
            "text": f"等待用户在浏览器完成 {spec.name} 设备码授权…",
        }
    if err_code:
        desc = (
            raw_err.get("message")
            if isinstance(raw_err, dict)
            else tok.get("error_description")
        ) or err_code
        raise AuthError(f"{spec.name} 授权失败：{desc}")
    if status_code >= 400 or tok_obj is None:
        raise AuthError(f"HTTP {status_code}: {detail}")

    # OpenAI Codex deviceauth returns authorization_code + code_verifier -> exchange for OAuth tokens
    if tok.get("authorization_code") and tok.get("code_verifier"):
        device_redirect = str(oauth.get("device_redirect_uri") or oauth.get("redirect_uri") or "")
        tok = _exchange_oauth_code(
            spec,
            str(tok["authorization_code"]),
            str(tok["code_verifier"]),
            None,
            redirect_uri=device_redirect,
        )

    access_raw = str(tok.get("access_token") or "").strip()
    if not access_raw:
        raise AuthError(f"{spec.name} 未返回 access_token")

    if spec.id == "github-copilot" or oauth.get("exchange_key_url"):
        base_entry: dict[str, Any] = {
            "type": "oauth",
            "refresh_token": access_raw,
            "access_token": "",
            "expires_at_ms": 0,
            "base_url": spec.default_base_url,
            "api_protocol": spec.api_protocol,
            "models": list(spec.default_models),
            "updated_at_ms": now,
        }
        if merged_extra:
            base_entry["extra_config"] = merged_extra
        entry = _refresh_oauth_token(spec, base_entry)
    else:
        refresh_tok = str(tok.get("refresh_token") or "").strip()
        expires_in = tok.get("expires_in")
        exp_ms = now + int(float(expires_in) * 1000) if isinstance(expires_in, (int, float)) else now + 3600 * 1000
        entry = {
            "type": "oauth",
            "access_token": access_raw,
            "refresh_token": refresh_tok,
            "expires_at_ms": exp_ms,
            "base_url": spec.default_base_url,
            "api_protocol": spec.api_protocol,
            "models": list(spec.default_models),
            "updated_at_ms": now,
        }
        if merged_extra:
            entry["extra_config"] = merged_extra
        if spec.id == "openai-codex":
            acc = _jwt_chatgpt_account_id(access_raw)
            if acc:
                entry["account_id"] = acc

    _save_provider_entry(path, spec, entry)
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
        providers_map = store.get("providers", {})
        for key in (spec.id, *spec.aliases):
            if key in providers_map:
                del providers_map[key]
                existed = True
            store.get("_pending", {}).pop(key, None)
        _write_store_unlocked(path, store)
    return {
        "ok": True,
        "provider": spec.id,
        "removed": existed,
        "text": f"已退出 {spec.name} 登录" if existed else f"{spec.name} 当前未登录",
    }


def _copilot_env_session(
    spec: ProviderSpec,
    gh_token: str,
    extra_cfg: dict[str, str],
) -> tuple[str, str]:
    ent_domain = str(extra_cfg.get("enterprise_domain") or "").strip()
    tok_digest = hashlib.sha256(gh_token.encode("utf-8")).hexdigest()
    cache_key = (tok_digest, ent_domain)
    now = _now_ms()
    with _COPILOT_ENV_LOCK:
        cached = _COPILOT_ENV_CACHE.get(cache_key)
        if cached and cached[2] - now > REFRESH_SKEW_MS:
            return cached[0], cached[1]
    exchanged = _refresh_oauth_token(
        spec,
        {
            "type": "oauth",
            "refresh_token": gh_token,
            "extra_config": extra_cfg,
            "base_url": spec.default_base_url,
        },
    )
    sess_tok = str(exchanged.get("access_token") or "").strip()
    sess_base = str(exchanged.get("base_url") or spec.default_base_url).rstrip("/")
    exp_ms = int(exchanged.get("expires_at_ms") or (now + 20 * 60 * 1000))
    with _COPILOT_ENV_LOCK:
        if len(_COPILOT_ENV_CACHE) > 32:
            expired = [k for k, v in _COPILOT_ENV_CACHE.items() if v[2] <= now]
            for k in expired:
                _COPILOT_ENV_CACHE.pop(k, None)
        _COPILOT_ENV_CACHE[cache_key] = (sess_tok, sess_base, exp_ms)
    return sess_tok, sess_base


def _env_api_key(spec: ProviderSpec) -> str:
    for env_name in spec.env_vars:
        if env_name in (
            "OLLAMA_HOST",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "AWS_PROFILE",
            "AWS_ACCESS_KEY_ID",
        ):
            continue
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    return ""


def _build_env_credential(spec: ProviderSpec) -> dict[str, Any] | None:
    env_configured, env_source, _ = _detect_env_status(spec)
    if not env_configured:
        return None
    extra_cfg = _backfill_vertex_extra(spec, _normalize_extra_config(spec, {}))
    api_key = _env_api_key(spec)
    if spec.id == "cloudflare-ai-gateway" and not (extra_cfg.get("account_id") and extra_cfg.get("gateway_id")):
        return None
    if spec.id == "cloudflare-workers-ai" and not extra_cfg.get("account_id"):
        return None
    base_url = _resolve_effective_base_url(spec, spec.default_base_url, extra_cfg)
    if spec.id == "github-copilot" and api_key:
        api_key, base_url = _copilot_env_session(spec, api_key, extra_cfg)
    norm_base = base_url.rstrip("/")
    models = list(spec.default_models)
    if spec.api_protocol == "google-generative-ai" and api_key:
        cached_models = _get_cached_env_models(spec.id, norm_base, api_key)
        if cached_models:
            models = cached_models
    return {
        "provider": spec.id,
        "auth_type": "env",
        "api_key": api_key,
        "base_url": norm_base,
        "api_protocol": spec.api_protocol,
        "extra_headers": dict(spec.extra_headers),
        "extra_config": extra_cfg,
        "models": models,
        "env_source": env_source,
    }


def resolve_provider_credential(
    provider: str,
    *,
    root: Path | None = None,
    allow_env: bool = True,
) -> dict[str, Any] | None:
    """读取服务商有效凭据；OAuth 距过期 <= 60s 时加锁自动续期并回写；无落盘凭据且允许环境变量时回落环境配置。"""
    spec = PROVIDER_MAP.get(provider)
    if spec is None:
        return None
    path = auth_path(root)
    stored_key: str | None = None
    entry: dict[str, Any] | None = None
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        found = _stored_entry_for_spec(store.get("providers", {}), spec)
        if found is not None:
            stored_key, entry = found
            if spec.id == "github-copilot" and entry.get("type") == "api_key" and entry.get("api_key"):
                entry = _refresh_oauth_token(
                    spec,
                    {
                        **entry,
                        "type": "oauth",
                        "refresh_token": str(entry["api_key"]),
                        "access_token": "",
                        "expires_at_ms": 0,
                    },
                )
                entry.pop("api_key", None)
                store.setdefault("providers", {})[stored_key] = entry
                _write_store_unlocked(path, store)
            elif entry.get("type") == "oauth":
                exp = entry.get("expires_at_ms")
                if isinstance(exp, (int, float)) and exp - _now_ms() <= REFRESH_SKEW_MS:
                    entry = _refresh_oauth_token(spec, entry)
                    store.setdefault("providers", {})[stored_key] = entry
                    _write_store_unlocked(path, store)
    if entry is None:
        return _build_env_credential(spec) if allow_env else None
    api_key = str(entry.get("api_key") or entry.get("access_token") or "").strip()
    extra_cfg = dict(entry.get("extra_config") or {}) if isinstance(entry.get("extra_config"), dict) else {}
    extra_cfg = _backfill_vertex_extra(spec, extra_cfg)
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
        "extra_config": extra_cfg,
        "models": list(entry.get("models") or spec.default_models),
    }


def _format_channel_label(raw: str) -> str:
    """将代理通道或 owned_by 标识转为可读标签。"""
    key = raw.strip()
    known = {
        "devin": "Devin",
        "antigravity": "Antigravity",
        "opencode": "OpenCode",
        "deepseek": "DeepSeek",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google",
        "cognition": "Cognition",
        "moonshot": "Moonshot",
        "nvidia": "NVIDIA",
    }
    low = key.lower()
    if low in known:
        return known[low]
    if key.isascii() and key.islower() and key.isalpha() and len(key) <= 24:
        return key.capitalize()
    return key


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw and not raw.startswith("//"):
        raw = f"http://{raw}"
    try:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    formatted = f"[{host}]" if ":" in host else host
    return f"{formatted}:{port}" if port is not None else formatted


def _env_provider_entry(spec: ProviderSpec) -> tuple[str, str, tuple[str, ...]] | None:
    """无网络副作用地推导环境变量配置的 `(source_kind, base_url, models)`，与 `resolve_provider_credential` 路由口径一致。"""
    env_configured, _, _ = _detect_env_status(spec)
    if not env_configured:
        return None
    extra_cfg = _backfill_vertex_extra(spec, _normalize_extra_config(spec, {}))
    if spec.id == "cloudflare-ai-gateway" and not (extra_cfg.get("account_id") and extra_cfg.get("gateway_id")):
        return None
    if spec.id == "cloudflare-workers-ai" and not extra_cfg.get("account_id"):
        return None
    try:
        base_url = _resolve_effective_base_url(spec, spec.default_base_url, extra_cfg)
    except AuthError:
        return None
    models: tuple[str, ...] = spec.default_models
    if spec.api_protocol == "google-generative-ai":
        api_key = _env_api_key(spec)
        cached_models = _get_cached_env_models(spec.id, base_url.rstrip("/"), api_key) if api_key else None
        if cached_models:
            models = tuple(cached_models)
    return "env", base_url, models


def _refresh_authenticated_models_with_meta(
    root: Path | None = None,
    *,
    provider_ids: tuple[str, ...] | list[str] | None = None,
    ttl_ms: int = MODELS_SYNC_TTL_MS,
    timeout: float = LIVE_MODELS_SYNC_TIMEOUT_S,
    force: bool = False,
) -> tuple[dict[str, list[str]], dict[str, tuple[str, str]]]:
    path = auth_path(root)
    now = _now_ms()
    wanted = set(provider_ids) if provider_ids is not None else None
    stored_candidates: list[tuple[str, str, str, str, int, int]] = []
    env_candidates: list[tuple[str, str, str, tuple[str, str, str], list[str] | None]] = []
    refreshed: dict[str, list[str]] = {}
    refreshed_meta: dict[str, tuple[str, str]] = {}

    try:
        with _auth_lock(path):
            store = _read_store_unlocked(path)
    except (AuthError, OSError):
        store = {"version": 1, "providers": {}}

    providers = store.get("providers", {})
    if not isinstance(providers, dict):
        providers = {}

    for spec in PROVIDERS:
        if wanted is not None and spec.id not in wanted:
            continue
        if spec.api_protocol != "google-generative-ai":
            continue
        found = _stored_entry_for_spec(providers, spec)
        if found is not None:
            stored_key, entry = found
            api_key = str(entry.get("api_key") or entry.get("access_token") or "").strip()
            if not api_key:
                continue
            base_url = str(entry.get("base_url") or spec.default_base_url).rstrip("/")
            raw_sync_ts = _safe_int_ms(entry.get("models_synced_at_ms"))
            raw_updated_ts = _safe_int_ms(entry.get("updated_at_ms"))
            last_sync = raw_sync_ts or raw_updated_ts
            if force or ttl_ms <= 0 or last_sync <= 0 or (now - last_sync) >= ttl_ms:
                fail_key = (spec.id, base_url, _secret_digest(api_key))
                with _ENV_MODELS_LOCK:
                    fail_ts = _MODELS_SYNC_FAILURES.get(fail_key)
                if not force and fail_ts is not None and (now - fail_ts) < MODELS_SYNC_FAIL_COOLDOWN_MS:
                    continue
                stored_candidates.append(
                    (spec.id, stored_key, base_url, api_key, raw_sync_ts, raw_updated_ts)
                )
        else:
            env_configured, _, _ = _detect_env_status(spec)
            if not env_configured:
                continue
            extra_cfg = _backfill_vertex_extra(spec, _normalize_extra_config(spec, {}))
            try:
                base_url = _resolve_effective_base_url(spec, spec.default_base_url, extra_cfg).rstrip("/")
            except AuthError:
                continue
            api_key = _env_api_key(spec)
            if not api_key:
                continue
            cache_key = (spec.id, base_url, _secret_digest(api_key))
            with _ENV_MODELS_LOCK:
                cached = _ENV_MODELS_CACHE.get(cache_key)
                fail_ts = _MODELS_SYNC_FAILURES.get(cache_key)
            stale_models = list(cached[1]) if cached and cached[1] else None
            if not force and ttl_ms > 0 and cached is not None and (now - cached[0]) < ttl_ms:
                refreshed[spec.id] = list(cached[1])
                refreshed_meta[spec.id] = (api_key, base_url)
            elif not force and fail_ts is not None and (now - fail_ts) < MODELS_SYNC_FAIL_COOLDOWN_MS:
                if stale_models:
                    refreshed[spec.id] = stale_models
                    refreshed_meta[spec.id] = (api_key, base_url)
            else:
                env_candidates.append((spec.id, base_url, api_key, cache_key, stale_models))

    stored_updates: dict[str, tuple[str, str, str, int, int, list[str]]] = {}
    for pid, stored_key, base_url, api_key, expected_sync_ts, expected_updated_ts in stored_candidates:
        fail_key = (pid, base_url, _secret_digest(api_key))
        try:
            synced = _sync_google_generative_models(base_url, api_key, timeout=timeout)
        except (AuthError, OSError, ValueError):
            with _ENV_MODELS_LOCK:
                if len(_MODELS_SYNC_FAILURES) > 32:
                    _MODELS_SYNC_FAILURES.pop(next(iter(_MODELS_SYNC_FAILURES)), None)
                _MODELS_SYNC_FAILURES[fail_key] = _now_ms()
            continue
        if synced:
            with _ENV_MODELS_LOCK:
                _MODELS_SYNC_FAILURES.pop(fail_key, None)
            stored_updates[pid] = (
                stored_key,
                base_url,
                api_key,
                expected_sync_ts,
                expected_updated_ts,
                synced,
            )
            refreshed[pid] = list(synced)
            refreshed_meta[pid] = (api_key, base_url)

    for pid, base_url, api_key, cache_key, stale_models in env_candidates:
        try:
            synced = _sync_google_generative_models(base_url, api_key, timeout=timeout)
        except (AuthError, OSError, ValueError):
            with _ENV_MODELS_LOCK:
                if len(_MODELS_SYNC_FAILURES) > 32:
                    _MODELS_SYNC_FAILURES.pop(next(iter(_MODELS_SYNC_FAILURES)), None)
                _MODELS_SYNC_FAILURES[cache_key] = _now_ms()
            if stale_models:
                refreshed[pid] = list(stale_models)
                refreshed_meta[pid] = (api_key, base_url)
            continue
        if synced:
            with _ENV_MODELS_LOCK:
                _MODELS_SYNC_FAILURES.pop(cache_key, None)
                if len(_ENV_MODELS_CACHE) > 32:
                    _ENV_MODELS_CACHE.pop(next(iter(_ENV_MODELS_CACHE)), None)
                _ENV_MODELS_CACHE[cache_key] = (_now_ms(), list(synced))
            cur_spec = PROVIDER_MAP.get(pid)
            if cur_spec is not None and _env_api_key(cur_spec) == api_key:
                refreshed[pid] = list(synced)
                refreshed_meta[pid] = (api_key, base_url)
        elif stale_models:
            refreshed[pid] = list(stale_models)
            refreshed_meta[pid] = (api_key, base_url)

    if stored_updates:
        try:
            with _auth_lock(path):
                latest_store = _read_store_unlocked(path)
                latest_providers = latest_store.setdefault("providers", {})
                write_ts = _now_ms()
                dirty = False
                for pid, (
                    stored_key,
                    expected_base,
                    expected_key,
                    expected_sync_ts,
                    expected_updated_ts,
                    synced,
                ) in stored_updates.items():
                    cur = latest_providers.get(stored_key)
                    if not isinstance(cur, dict):
                        refreshed.pop(pid, None)
                        refreshed_meta.pop(pid, None)
                        continue
                    cur_spec = PROVIDER_MAP.get(pid)
                    cur_key = str(cur.get("api_key") or cur.get("access_token") or "").strip()
                    cur_base = str(
                        cur.get("base_url") or (cur_spec.default_base_url if cur_spec else "")
                    ).rstrip("/")
                    cur_sync_ts = _safe_int_ms(cur.get("models_synced_at_ms"))
                    cur_updated_ts = _safe_int_ms(cur.get("updated_at_ms"))
                    if (
                        cur_key != expected_key
                        or cur_base != expected_base
                        or cur_sync_ts > expected_sync_ts
                        or cur_updated_ts > expected_updated_ts
                    ):
                        refreshed.pop(pid, None)
                        refreshed_meta.pop(pid, None)
                        continue
                    cur["models"] = list(synced)
                    cur["models_synced_at_ms"] = max(write_ts, cur_sync_ts + 1)
                    dirty = True
                if dirty:
                    _write_store_unlocked(path, latest_store)
        except (AuthError, OSError):
            pass

    return refreshed, refreshed_meta


def refresh_authenticated_models(
    root: Path | None = None,
    *,
    provider_ids: tuple[str, ...] | list[str] | None = None,
    ttl_ms: int = MODELS_SYNC_TTL_MS,
    timeout: float = LIVE_MODELS_SYNC_TIMEOUT_S,
    force: bool = False,
) -> dict[str, list[str]]:
    """按 TTL 对已接入动态目录的服务商（如 Google AI Studio）刷新可用模型列表，并安全写回存储。

    三阶段无锁出网：持锁读候选 → 解锁发 HTTP 请求 → 持锁核对凭据/base_url/时间戳未变更后原子回写。
    任何网络或远端异常均进入短冷却并静默保留已有缓存，不阻断模型列表展示。
    """
    refreshed, _ = _refresh_authenticated_models_with_meta(
        root=root,
        provider_ids=provider_ids,
        ttl_ms=ttl_ms,
        timeout=timeout,
        force=force,
    )
    return refreshed


def _iter_authenticated_model_entries(
    root: Path | None = None,
    *,
    refresh: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    """读取所有已登录（含环境变量已生效）服务商的 `(full_model_id, source_meta)` 列表（按完整模型名去重排序）。"""
    refreshed, refreshed_meta = (
        _refresh_authenticated_models_with_meta(root=root) if refresh else ({}, {})
    )
    path = auth_path(root)
    try:
        with _auth_lock(path):
            store = _read_store_unlocked(path)
    except (AuthError, OSError):
        return []
    providers = store.get("providers", {})
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for spec in PROVIDERS:
        found = _stored_entry_for_spec(providers, spec)
        if found is not None:
            entry = found[1]
            raw_models = entry.get("models")
            models: list[str] | tuple[str, ...] = (
                raw_models if isinstance(raw_models, list) and raw_models else spec.default_models
            )
            auth_type = str(entry.get("type") or spec.category)
            base_url = str(entry.get("base_url") or spec.default_base_url)
            entry_key = str(entry.get("api_key") or entry.get("access_token") or "").strip()
            meta_pair = refreshed_meta.get(spec.id)
            if refreshed.get(spec.id) and (
                meta_pair is None or meta_pair == (entry_key, base_url.rstrip("/"))
            ):
                models = refreshed[spec.id]
        else:
            env_info = _env_provider_entry(spec)
            if env_info is None:
                continue
            auth_type, base_url, models = env_info
            if refreshed.get(spec.id):
                models = refreshed[spec.id]
        endpoint = _url_host(base_url)
        for m in models:
            if not isinstance(m, str) or not m.strip():
                continue
            wire = m.strip()
            full = f"{spec.id}/{wire}"
            if full in seen:
                continue
            seen.add(full)
            meta: dict[str, Any] = {
                "source_id": spec.id,
                "source_name": spec.name,
                "source_kind": auth_type,
                "category": spec.category,
                "wire_model": wire,
                "display_model": wire,
            }
            if endpoint:
                meta["endpoint"] = endpoint
            if spec.id in ("cliproxyapi", "ollama", "lmstudio") and "/" in wire:
                meta["channel"] = _format_channel_label(wire.split("/", 1)[0])
            out.append((full, meta))
    out.sort(key=lambda item: item[0])
    return out


def list_authenticated_models(root: Path | None = None, *, refresh: bool = False) -> list[str]:
    """公共只读 API：列出所有已登录（含环境变量已配置）服务商的 `<provider>/<model>` 模型列表（去重排序）。

    保留为兼容外部调用的公共辅助函数；仓内合并流程优先使用 `list_authenticated_model_sources` 一次取回元信息。
    """
    return [full for full, _ in _iter_authenticated_model_entries(root, refresh=refresh)]


def list_authenticated_model_sources(root: Path | None = None, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
    """返回已登录服务商模型的来源元数据映射：`{full_model_id: source_meta}`。"""
    return {full: meta for full, meta in _iter_authenticated_model_entries(root, refresh=refresh)}


def resolve_model_provider(model_name: str, *, root: Path | None = None) -> tuple[str, str, dict[str, Any]] | None:
    """若模型名带 `<provider>/`（含兼容别名）、该服务商已登录且模型在该服务商清单内，返回 `(provider, wire_model, credential)`。

    仅在 `<provider>/` 确属该服务商已申报模型清单时接管路由，防止代理原生 `anthropic/claude-x` 等同名命名被错误劫持。
    """
    if "/" not in model_name:
        return None
    pid, wire_model = model_name.split("/", 1)
    wire = wire_model.strip()
    if pid not in PROVIDER_MAP or not wire:
        return None
    spec = PROVIDER_MAP[pid]
    cred = resolve_provider_credential(spec.id, root=root)
    if cred is None:
        return None
    catalog = cred.get("models") or ()
    if wire not in catalog and spec.api_protocol == "google-generative-ai" and cred.get("api_key"):
        orig_key = str(cred.get("api_key") or "")
        orig_base = str(cred.get("base_url") or "")
        refreshed = refresh_authenticated_models(root=root, provider_ids=(spec.id,), ttl_ms=MODELS_SYNC_TTL_MS)
        if refreshed.get(spec.id):
            cred = resolve_provider_credential(spec.id, root=root) or cred
            if (
                wire not in (cred.get("models") or ())
                and str(cred.get("api_key") or "") == orig_key
                and str(cred.get("base_url") or "") == orig_base
            ):
                cred["models"] = list(refreshed[spec.id])
            catalog = cred.get("models") or ()
    if wire not in catalog:
        return None
    return spec.id, wire, cred


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
        found = _stored_entry_for_spec(providers, spec)
        if found is None:
            continue
        try:
            cred = resolve_provider_credential(spec.id, root=root, allow_env=False)
        except AuthError:
            continue
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


_CLI_MODE_LABEL: dict[str, str] = {
    "oauth_pkce": "OAuth",
    "oauth_device": "设备码",
    "proxy": "代理",
    "api_key": "密钥",
}

_CLI_CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    ("oauth", "OAuth 订阅"),
    ("flagship", "常用大模型"),
    ("cloud", "云平台与路由"),
    ("local", "本地 / 代理"),
)


def _format_provider_row_cli(p: dict[str, Any]) -> str:
    if p["connected"]:
        extra = f"（已登录 {p['masked_secret']}）" if p.get("masked_secret") else "（已连接）"
        return f"  ● {p['id']} — {p['name']} {extra}"
    if p.get("env_configured"):
        src = p.get("env_source") or "环境变量"
        return f"  ◐ {p['id']} — {p['name']} （环境变量已就绪：{src}）"
    modes = "/".join(_CLI_MODE_LABEL.get(m, m) for m in p.get("auth_modes") or [])
    return f"  ○ {p['id']} — {p['name']} （未登录 · 支持 {modes}）"


def run_auth_command(
    line: str,
    *,
    root: Path | None = None,
    interactive: bool = False,
) -> tuple[bool, str, str | None]:
    """D170 REPL/对话框 `/login` 与 `/logout` 命令。返回 `(ok, 回显文字, logged_out_provider)`。"""
    import getpass

    parts = line.strip().split()
    if not parts:
        return False, "空命令", None
    cmd = parts[0]
    if cmd == "/logout":
        if len(parts) < 2:
            status = get_auth_status(root)
            connected_ids = [str(p["id"]) for p in status["providers"] if p.get("connected")]
            hint = f"当前已连接：{'、'.join(connected_ids)}。" if connected_ids else "当前无已连接的服务商。"
            return False, f"{hint}用法：/logout <服务商ID>", None
        try:
            res = logout_provider(parts[1].strip(), root=root)
        except AuthError as exc:
            return False, str(exc), None
        return True, str(res["text"]), str(res["provider"])

    if cmd != "/login":
        return False, f"未知命令：{cmd}", None

    if len(parts) == 1 or (len(parts) == 2 and parts[1].strip().lower() == "all"):
        show_all = len(parts) == 2
        status = get_auth_status(root)
        provs = list(status["providers"])
        lines = [f"已连接 {status['connected_count']} / {len(provs)} 家服务商："]
        connected = [p for p in provs if p["connected"]]
        env_ready = [p for p in provs if not p["connected"] and p.get("env_configured")]
        rest = [p for p in provs if not p["connected"] and not p.get("env_configured")]
        if connected:
            lines.append("【已连接】")
            lines.extend(_format_provider_row_cli(p) for p in connected)
        if env_ready:
            lines.append("【环境变量已就绪】")
            lines.extend(_format_provider_row_cli(p) for p in env_ready)
        if show_all:
            for cat_key, cat_label in _CLI_CATEGORY_ORDER:
                items = [p for p in rest if (p.get("category") or "flagship") == cat_key]
                if items:
                    lines.append(f"【{cat_label}】")
                    lines.extend(_format_provider_row_cli(p) for p in items)
        else:
            for cat_key, cat_label in (("oauth", "OAuth 订阅"), ("flagship", "常用大模型"), ("local", "本地 / 代理")):
                items = [p for p in rest if (p.get("category") or "flagship") == cat_key]
                if items:
                    lines.append(f"【{cat_label}】")
                    lines.extend(_format_provider_row_cli(p) for p in items)
            cloud_n = sum(1 for p in rest if (p.get("category") or "flagship") == "cloud")
            if cloud_n > 0:
                lines.append(f"  … 另有 {cloud_n} 家云平台与路由服务商，输入 /login all 查看全部")
        lines.append("用法：/login <服务商ID>；OAuth：/login <服务商ID> complete <回调或码>；退出：/logout <服务商ID>；脚本请用：ava --auth-complete <provider> --stdin")
        return True, "\n".join(lines), None

    pid = parts[1].strip()
    try:
        spec = _require_provider(pid)
        pid = spec.id

        def _try_copy_clipboard(text: str) -> bool:
            if not text or sys.platform != "darwin" or os.environ.get("PYTEST_CURRENT_TEST"):
                return False
            try:
                r = subprocess.run(["pbcopy"], input=text.encode("utf-8"), capture_output=True, check=False, timeout=2)
                return r.returncode == 0
            except Exception:
                return False

        def _prompt_interactive_secret(mode: str, started: dict[str, Any]) -> tuple[bool, str, None]:
            if any(os.environ.get(v, "").strip() for v in spec.env_vars):
                print(f"[提示] 当前环境变量已配置 {spec.name}，也可继续保存独立凭据覆盖。")
            extra_cfg: dict[str, str] = {}
            if spec.extra_fields:
                for ef in spec.extra_fields:
                    default_hint = f" [默认 {ef.default}]" if ef.default else ("（必填）" if ef.required else "（可选，直接回车跳过）")
                    prompt_lbl = f"{ef.label}{default_hint}: "
                    try:
                        val = (getpass.getpass(prompt_lbl) if ef.secret else input(prompt_lbl)).strip()
                    except (EOFError, KeyboardInterrupt):
                        return False, "已取消登录", None
                    if val:
                        extra_cfg[ef.key] = val
                    elif ef.default:
                        extra_cfg[ef.key] = ef.default
            if mode == "proxy":
                try:
                    raw_url = input(f"{spec.name} 代理地址 [默认 {started['default_base_url']}]: ").strip()
                    raw_key = getpass.getpass(f"{spec.name} 代理密钥（无鉴权可直接回车）: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return False, "已取消登录", None
                payload_p: dict[str, Any] = {
                    "mode": "proxy",
                    "base_url": raw_url or started["default_base_url"],
                    "api_key": raw_key,
                }
                if extra_cfg:
                    payload_p["extra_config"] = extra_cfg
                res = complete_auth_flow(pid, payload_p, root=root)
                return True, str(res["text"]), None
            can_skip_key = _complete_cloud_entry_allowed_empty_key(spec, _backfill_vertex_extra(spec, extra_cfg))
            key_hint = "（使用 IAM/ADC 可直接回车跳过）" if can_skip_key else "（输入不回显，直接回车取消）"
            try:
                raw_key = getpass.getpass(f"请输入 {spec.name} API Key{key_hint}: ").strip()
            except (EOFError, KeyboardInterrupt):
                return False, "已取消登录", None
            if not raw_key and not can_skip_key:
                return False, "已取消登录", None
            payload_k: dict[str, Any] = {"mode": "api_key", "api_key": raw_key}
            if extra_cfg:
                payload_k["extra_config"] = extra_cfg
            res = complete_auth_flow(pid, payload_k, root=root)
            return True, str(res["text"]), None

        def _poll_interactive_device(started: dict[str, Any]) -> tuple[bool, str, None]:
            uri = str(started.get("verification_uri") or "")
            code = str(started.get("user_code") or "")
            copied = _try_copy_clipboard(code)
            opened = _try_open_browser(uri)
            copy_note = "（已复制到剪贴板）" if copied else ""
            open_note = "（已自动打开浏览器）" if opened else ""
            print(f"{spec.name} 设备码授权：")
            print(f"  1. 打开 {uri} {open_note}".rstrip())
            print(f"  2. 输入设备码 {code} {copy_note}".rstrip())
            print(f"  正在自动等待授权完成（按 Ctrl-C 取消，或稍后执行 /login {pid} complete）...", flush=True)
            interval = max(2, int(started.get("interval_s") or 5))
            try:
                while True:
                    # 分片休眠，确保 Ctrl-C 立即响应
                    deadline = time.monotonic() + interval
                    while time.monotonic() < deadline:
                        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                    res = complete_auth_flow(pid, {"mode": "oauth_device"}, root=root)
                    if res.get("ok"):
                        return True, str(res.get("text") or f"已登录 {spec.name}"), None
                    if res.get("pending"):
                        interval = max(2, int(res.get("interval_s") or interval))
                        continue
                    return False, str(res.get("text") or "授权未完成"), None
            except KeyboardInterrupt:
                return False, f"已暂停等待。在浏览器完成授权后可执行：/login {pid} complete", None

        if len(parts) == 2:
            started = start_auth_flow(pid, root=root)
            mode = started["mode"]
            if mode == "oauth_pkce":
                opened = interactive and sys.stdin.isatty() and _try_open_browser(str(started.get("authorize_url") or ""))
                open_note = "（已尝试在浏览器打开）" if opened else ""
                alt = "（也可输入 /login " + pid + " api_key 改用密钥登录）" if "api_key" in spec.auth_modes else ""
                return True, f"{spec.name} OAuth 授权地址{open_note}：\n{started['authorize_url']}\n完成后执行：/login {pid} complete <回调链接或授权码>{alt}", None
            if mode == "oauth_device":
                if interactive and sys.stdin.isatty() and not os.environ.get("PYTEST_CURRENT_TEST"):
                    return _poll_interactive_device(started)
                alt = "\n也可输入 /login " + pid + " api_key 改用密钥登录" if "api_key" in spec.auth_modes else ""
                return True, f"{spec.name} 设备码授权：\n1. 打开 {started['verification_uri']}\n2. 输入设备码 {started['user_code']}\n3. 授权后执行：/login {pid} complete{alt}", None
            if interactive and sys.stdin.isatty():
                return _prompt_interactive_secret(mode, started)
            oauth_hint = f"（也支持 OAuth：/login {pid} oauth）" if any(m.startswith("oauth") for m in spec.auth_modes) else ""
            return True, f"{spec.name} 默认地址：{started['default_base_url']}{oauth_hint}\n推荐使用桌面端 /login 登录弹窗，或在终端执行：ava --auth-complete {pid} --stdin（避免明文密钥进入对话记录）", None
        arg = parts[2].strip()
        if interactive and len(parts) == 3 and arg in ("api_key", "proxy"):
            started = start_auth_flow(pid, mode=arg, root=root)
            if sys.stdin.isatty():
                return _prompt_interactive_secret(started["mode"], started)
            return True, f"{spec.name} 默认地址：{started['default_base_url']}\n推荐使用桌面端 /login 登录弹窗，或在终端执行：ava --auth-complete {pid} --stdin（避免明文密钥进入对话记录）", None
        allowed_extra = {ef.key for ef in spec.extra_fields}
        is_extra_kv = bool(parts[2:]) and all(
            "=" in tok and tok.split("=", 1)[0].strip() in allowed_extra
            for tok in parts[2:]
        )
        if arg in ("oauth", "oauth_pkce", "oauth_device") or is_extra_kv:
            req_mode: str | None
            extra_opts: dict[str, str] = {}
            tail_tokens = parts[3:] if arg in ("oauth", "oauth_pkce", "oauth_device") else parts[2:]
            for tok in tail_tokens:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    if k.strip() and v.strip():
                        extra_opts[k.strip()] = v.strip()
            if arg in ("oauth", "oauth_pkce", "oauth_device"):
                req_mode = next((m for m in spec.auth_modes if m.startswith("oauth")), None) if arg == "oauth" else arg
            else:
                req_mode = spec.auth_modes[0]
            started = start_auth_flow(pid, mode=req_mode, extra_config=extra_opts or None, root=root)
            mode = started["mode"]
            if mode == "oauth_pkce":
                opened = interactive and sys.stdin.isatty() and _try_open_browser(str(started.get("authorize_url") or ""))
                open_note = "（已尝试在浏览器打开）" if opened else ""
                return True, f"{spec.name} OAuth 授权地址{open_note}：\n{started['authorize_url']}\n完成后执行：/login {pid} complete <回调链接或授权码>", None
            if mode == "oauth_device":
                if interactive and sys.stdin.isatty() and not os.environ.get("PYTEST_CURRENT_TEST"):
                    return _poll_interactive_device(started)
                return True, f"{spec.name} 设备码授权：\n1. 打开 {started['verification_uri']}\n2. 输入设备码 {started['user_code']}\n3. 授权后执行：/login {pid} complete", None
            return True, f"{spec.name} 默认地址：{started['default_base_url']}\n推荐使用桌面端 /login 登录弹窗，或在终端执行：ava --auth-complete {pid} --stdin（避免明文密钥进入对话记录）", None
        if arg == "complete":
            code = " ".join(parts[3:]).strip()
            mode: str | None = None
            with _auth_lock(auth_path(root)):
                store = _read_store_unlocked(auth_path(root))
                pend = store.get("_pending", {}).get(spec.id)
                if isinstance(pend, dict) and pend.get("mode"):
                    mode = str(pend["mode"])
            if mode is None:
                mode = "oauth_device" if "oauth_device" in spec.auth_modes and not code else spec.auth_modes[0]
            res = complete_auth_flow(pid, {"mode": mode, **({"code": code} if code else {})}, root=root)
            return bool(res.get("ok")), str(res.get("text") or ""), None
        if interactive:
            return False, f"为防止密钥落入命令历史，请直接输入 /login {pid} 按提示隐藏输入，或使用桌面端登录弹窗", None
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


_ARGV_SECRET_KEY_RE = re.compile(r"(?i)(api_key|token|secret|password|^code$|callback_url)")


def _payload_has_sensitive_key(node: Any) -> bool:
    if isinstance(node, dict):
        for k, v in node.items():
            if _ARGV_SECRET_KEY_RE.search(str(k)) and isinstance(v, str) and v.strip():
                return True
            if _payload_has_sensitive_key(v):
                return True
    elif isinstance(node, list):
        for item in node:
            if _payload_has_sensitive_key(item):
                return True
    return False


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
            if len(args) < 2 or len(args) > 4:
                raise AuthError("用法: ava --auth-start <provider> [<mode>] [--stdin]")
            has_stdin = args[-1] == "--stdin"
            pos_args = args[1:-1] if has_stdin else args[1:]
            if len(pos_args) not in (1, 2):
                raise AuthError("用法: ava --auth-start <provider> [<mode>] [--stdin]")
            mode = pos_args[1] if len(pos_args) == 2 else None
            extra_config: dict[str, Any] | None = None
            if has_stdin:
                raw_start = stdin_reader() if stdin_reader is not None else sys.stdin.read()
                if raw_start.strip():
                    start_obj = json.loads(raw_start)
                    if not isinstance(start_obj, dict):
                        raise AuthError("auth-start stdin 载荷须是 JSON 对象")
                    if mode is None and isinstance(start_obj.get("mode"), str) and start_obj["mode"].strip():
                        mode = start_obj["mode"].strip()
                    if isinstance(start_obj.get("extra_config"), dict):
                        extra_config = start_obj["extra_config"]
            print(json.dumps(start_auth_flow(pos_args[0], mode, extra_config=extra_config, root=root), ensure_ascii=False))
            return 0
        if flag == "--auth-complete":
            if len(args) not in (2, 3):
                raise AuthError("用法: ava --auth-complete <provider> [<json>|--stdin]")
            from_argv = len(args) == 3 and args[2] != "--stdin"
            if from_argv:
                raw = args[2]
            else:
                raw = stdin_reader() if stdin_reader is not None else sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
            if not isinstance(payload, dict):
                raise AuthError("auth-complete 载荷须是 JSON 对象")
            if from_argv and _payload_has_sensitive_key(payload):
                raise AuthError("为防止密钥进入进程参数或命令历史，--auth-complete 禁止通过命令行参数传凭据，请改用 --stdin")
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
