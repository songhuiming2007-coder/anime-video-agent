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
import errno
import fcntl
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline import paths

REFRESH_SKEW_MS = 60_000
PENDING_TTL_MS = 30 * 60 * 1000
DEFAULT_HTTP_TIMEOUT_S = 20.0
ALLOWED_AUTH_MODES = frozenset({"proxy", "api_key", "oauth_pkce", "oauth_device"})


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
            "device_code_url": "https://github.com/login/device/code",
            "token_url": "https://github.com/login/oauth/access_token",
            "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
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
                placeholder="仅本地加密落盘",
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
    if expected_state and state and state != expected_state:
        raise AuthError("OAuth state 校验不匹配，请重新发起授权")
    if not state_optional and expected_state and "?" in text and not state:
        raise AuthError("OAuth 回调缺少 state 参数，请重新发起授权")
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


_SECRET_EXTRA_KEYS = frozenset({"aws_secret_access_key", "aws_session_token"})


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
    secret_keys = {ef.key for ef in spec.extra_fields if ef.secret} | _SECRET_EXTRA_KEYS
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
    base = raw_base.strip() or spec.default_base_url
    if spec.id == "cloudflare-ai-gateway":
        acc = extra.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        gw = extra.get("gateway_id") or os.environ.get("CLOUDFLARE_GATEWAY_ID", "").strip()
        if not acc or not gw:
            raise AuthError("Cloudflare AI Gateway 需要填写 Account ID 和 Gateway ID")
        base = base.replace("{account_id}", acc).replace("{gateway_id}", gw)
        return _validate_base_url(base)
    if spec.id == "cloudflare-workers-ai":
        acc = extra.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        if not acc:
            raise AuthError("Cloudflare Workers AI 需要填写 Account ID")
        base = base.replace("{account_id}", acc)
        return _validate_base_url(base)
    if spec.id == "amazon-bedrock":
        region = extra.get("aws_region") or os.environ.get("AWS_REGION", "").strip() or "us-east-1"
        if not raw_base.strip() or base == spec.default_base_url:
            base = f"https://bedrock-runtime.{region}.amazonaws.com"
        return _validate_base_url(base)
    if spec.id == "google-vertex":
        loc = extra.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or "us-central1"
        if not raw_base.strip() or base == spec.default_base_url:
            if loc == "global":
                base = "https://aiplatform.googleapis.com/v1"
            else:
                base = f"https://{loc}-aiplatform.googleapis.com/v1"
        return _validate_base_url(base)
    if spec.id == "azure":
        res_name = extra.get("resource_name") or os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "").strip()
        env_base = os.environ.get("AZURE_OPENAI_BASE_URL", "").strip()
        if (not raw_base.strip() or "YOUR-RESOURCE" in base) and env_base:
            base = env_base
        elif (not raw_base.strip() or "YOUR-RESOURCE" in base) and res_name:
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
    return _validate_base_url(base)


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


def _exchange_oauth_code(
    spec: ProviderSpec,
    code: str,
    verifier: str,
    state: str | None,
    *,
    issued_client_id: str | None = None,
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
        )
    client_id = issued_client_id or str(oauth["client_id"])
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": str(oauth["redirect_uri"]),
        "code_verifier": verifier,
    }
    if oauth.get("resource"):
        payload["resource"] = str(oauth["resource"])
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
        extra_cfg = entry.get("extra_config") if isinstance(entry.get("extra_config"), dict) else {}
        ent_domain = str(extra_cfg.get("enterprise_domain") or "").strip()
        copilot_url = (
            f"https://api.{ent_domain}/copilot_internal/v2/token"
            if ent_domain
            else str(oauth["copilot_token_url"])
        )
        cop = _http_json(
            copilot_url,
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


def get_auth_status(root: Path | None = None) -> dict[str, Any]:
    """返回脱敏后的全部支持服务商状态（绝不返回真实密钥/令牌）。"""
    path = auth_path(root)
    with _auth_lock(path):
        store = _read_store_unlocked(path)
    providers_data = store.get("providers", {})
    rows = []
    for spec in PROVIDERS:
        found = _stored_entry_for_spec(providers_data, spec)
        rows.append(_public_provider_row(spec, found[1] if found else None, root))
    connected_count = sum(1 for r in rows if r["connected"])
    return {
        "ok": True,
        "auth_file": str(path),
        "connected_count": connected_count,
        "providers": rows,
    }


def start_auth_flow(provider: str, mode: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
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
        state = secrets.token_urlsafe(24)
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
        authorize_url = f"{oauth['authorize_url']}?{urllib.parse.urlencode(params)}"
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
    client_id = str(oauth["client_id"])
    scopes = str(oauth.get("scopes") or "").strip()
    dev_payload: dict[str, str] = {"client_id": client_id}
    if scopes:
        dev_payload["scope"] = scopes
    dev_url = str(oauth["device_code_url"])
    if oauth.get("device_content_type") == "form":
        dev = _http_json(dev_url, method="POST", form_dict=dev_payload)
    else:
        dev = _http_json(dev_url, method="POST", data_dict=dev_payload)

    device_code = str(dev.get("device_code") or dev.get("device_auth_id") or "").strip()
    user_code = str(dev.get("user_code") or "").strip()
    verification_uri = str(
        dev.get("verification_uri_complete")
        or dev.get("verification_uri")
        or oauth.get("device_verification_uri")
        or ""
    ).strip()
    raw_interval = dev.get("interval")
    interval_s = int(raw_interval) if isinstance(raw_interval, (int, float)) else 5
    raw_exp = dev.get("expires_in")
    expires_in_s = int(raw_exp) if isinstance(raw_exp, (int, float)) else 900
    if not device_code or not user_code or not verification_uri:
        raise AuthError(f"{spec.name} 未返回有效的设备码信息")
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        _prune_expired_pending(store, now)
        store.setdefault("_pending", {})[spec.id] = {
            "mode": "oauth_device",
            "device_code": device_code,
            "user_code": user_code,
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
        has_adc_cfg = bool(extra_cfg.get("project_id") and extra_cfg.get("location"))
        has_sa = bool(extra_cfg.get("credentials_path"))
        return has_adc_cfg or has_sa
    return False


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
    extra_cfg = _normalize_extra_config(spec, data.get("extra_config"))

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
        }
        if api_key:
            entry["api_key"] = api_key
        if extra_cfg:
            entry["extra_config"] = extra_cfg
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
        for ef in spec.extra_fields:
            if ef.required and not extra_cfg.get(ef.key):
                raise AuthError(f"请填写 {ef.label}")
        api_key = str(data.get("api_key") or "").strip()
        if not api_key and not _complete_cloud_entry_allowed_empty_key(spec, extra_cfg):
            raise AuthError("请填写 API Key")
        raw_base = str(data.get("base_url") or "").strip()
        base_url = _resolve_effective_base_url(spec, raw_base, extra_cfg)
        models = list(spec.default_models)
        entry = {
            "type": "api_key",
            "base_url": base_url,
            "api_protocol": spec.api_protocol,
            "models": models,
            "updated_at_ms": now,
        }
        if api_key:
            entry["api_key"] = api_key
        if extra_cfg:
            entry["extra_config"] = extra_cfg
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            store.setdefault("providers", {})[spec.id] = entry
            for alias in spec.aliases:
                store.get("providers", {}).pop(alias, None)
            store.get("_pending", {}).pop(spec.id, None)
            _write_store_unlocked(path, store)
        return {
            "ok": True,
            "provider": spec.id,
            "mode": "api_key",
            "models": models,
            "text": f"已保存 {spec.name} 凭据",
        }

    if mode == "oauth_pkce":
        raw_code = str(data.get("code") or data.get("callback_url") or "").strip()
        with _auth_lock(path):
            store = _read_store_unlocked(path)
            pending = store.get("_pending", {}).get(spec.id)
        if not isinstance(pending, dict) or pending.get("mode") != "oauth_pkce":
            raise AuthError("尚未发起 OAuth 授权或会话已失效，请重新点击开始登录")
        if now - int(pending.get("created_at_ms") or 0) > PENDING_TTL_MS:
            raise AuthError("OAuth 授权会话已过期（超过 30 分钟），请重新点击开始登录")
        oauth = spec.oauth
        code, state, cb_params = _parse_callback_input(
            raw_code,
            pending.get("state"),
            state_optional=bool(oauth.get("state_optional")),
        )
        issued_cid = cb_params.get("client_id") if oauth.get("dynamic_client_id") else None
        if oauth.get("dynamic_client_id") and not issued_cid:
            raise AuthError(f"{spec.name} OAuth 回调链接缺少动态签发的 client_id 参数，请粘贴完整回调 URL")
        tok = _exchange_oauth_code(
            spec,
            code,
            str(pending["code_verifier"]),
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

    # oauth_device
    with _auth_lock(path):
        store = _read_store_unlocked(path)
        pending = store.get("_pending", {}).get(spec.id)
    if not isinstance(pending, dict) or pending.get("mode") != "oauth_device":
        raise AuthError(f"尚未发起 {spec.name} 设备码授权，请先点击获取设备码")
    if int(pending.get("expires_at_ms") or 0) <= now:
        raise AuthError(f"{spec.name} 设备码已过期，请重新获取")
    oauth = spec.oauth
    token_url = str(oauth.get("device_token_url") or oauth["token_url"])
    req_body: dict[str, str]
    if oauth.get("device_content_type") == "json":
        req_body = {
            "client_id": str(oauth["client_id"]),
            "user_code": str(pending.get("user_code") or ""),
            "device_auth_id": str(pending["device_code"]),
        }
        tok = _http_json(token_url, method="POST", data_dict=req_body)
    elif oauth.get("token_content_type") == "form":
        req_body = {
            "client_id": str(oauth["client_id"]),
            "device_code": str(pending["device_code"]),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        tok = _http_json(token_url, method="POST", form_dict=req_body)
    else:
        req_body = {
            "client_id": str(oauth["client_id"]),
            "device_code": str(pending["device_code"]),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        tok = _http_json(token_url, method="POST", data_dict=req_body)

    err = tok.get("error")
    if err in ("authorization_pending", "slow_down"):
        return {
            "ok": False,
            "pending": True,
            "provider": spec.id,
            "error": str(err),
            "interval_s": int(pending.get("interval_s") or 5) + (5 if err == "slow_down" else 0),
            "text": f"等待用户在浏览器完成 {spec.name} 设备码授权…",
        }
    if err:
        raise AuthError(f"{spec.name} 授权失败：{tok.get('error_description') or err}")

    # OpenAI Codex deviceauth returns authorization_code + code_verifier -> exchange for OAuth tokens
    if tok.get("authorization_code") and tok.get("code_verifier"):
        tok = _exchange_oauth_code(
            spec,
            str(tok["authorization_code"]),
            str(tok["code_verifier"]),
            None,
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
        if extra_cfg:
            base_entry["extra_config"] = extra_cfg
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
        if extra_cfg:
            entry["extra_config"] = extra_cfg
        if spec.id == "openai-codex":
            acc = _jwt_chatgpt_account_id(access_raw)
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


def _build_env_credential(spec: ProviderSpec) -> dict[str, Any] | None:
    env_configured, env_source, _ = _detect_env_status(spec)
    if not env_configured:
        return None
    extra_cfg = _normalize_extra_config(spec, {})
    api_key = ""
    for env_name in spec.env_vars:
        if env_name in ("OLLAMA_HOST", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "AWS_PROFILE", "AWS_ACCESS_KEY_ID"):
            continue
        val = os.environ.get(env_name, "").strip()
        if val:
            api_key = val
            break
    if spec.id == "cloudflare-ai-gateway" and not (extra_cfg.get("account_id") and extra_cfg.get("gateway_id")):
        return None
    if spec.id == "cloudflare-workers-ai" and not extra_cfg.get("account_id"):
        return None
    base_url = _resolve_effective_base_url(spec, spec.default_base_url, extra_cfg)
    return {
        "provider": spec.id,
        "auth_type": "env",
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "api_protocol": spec.api_protocol,
        "extra_headers": dict(spec.extra_headers),
        "extra_config": extra_cfg,
        "models": list(spec.default_models),
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
            if entry.get("type") == "oauth":
                exp = entry.get("expires_at_ms")
                if isinstance(exp, (int, float)) and exp - _now_ms() <= REFRESH_SKEW_MS:
                    entry = _refresh_oauth_token(spec, entry)
                    store.setdefault("providers", {})[stored_key] = entry
                    _write_store_unlocked(path, store)
    if entry is None:
        return _build_env_credential(spec) if allow_env else None
    api_key = str(entry.get("api_key") or entry.get("access_token") or "").strip()
    extra_cfg = dict(entry.get("extra_config") or {}) if isinstance(entry.get("extra_config"), dict) else {}
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
        found = _stored_entry_for_spec(providers, spec)
        if found is None:
            continue
        entry = found[1]
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
    """若模型名带 `<provider>/`（含兼容别名）且该服务商已登录，返回 `(provider, wire_model, credential)`。

    例：`openrouter/anthropic/claude-sonnet-4.6` → `("openrouter", "anthropic/claude-sonnet-4.6", cred)`。
    """
    if "/" not in model_name:
        return None
    pid, wire_model = model_name.split("/", 1)
    if pid not in PROVIDER_MAP or not wire_model.strip():
        return None
    spec = PROVIDER_MAP[pid]
    cred = resolve_provider_credential(spec.id, root=root)
    if cred is None:
        return None
    return spec.id, wire_model.strip(), cred


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
        if arg in ("oauth", "oauth_pkce", "oauth_device"):
            req_mode = next((m for m in spec.auth_modes if m.startswith("oauth")), None) if arg == "oauth" else arg
            started = start_auth_flow(pid, mode=req_mode, root=root)
            mode = started["mode"]
            if mode == "oauth_pkce":
                return True, f"{spec.name} OAuth 授权地址：\n{started['authorize_url']}\n完成后执行：/login {pid} complete <回调链接或授权码>", None
            if mode == "oauth_device":
                return True, f"{spec.name} 设备码授权：\n1. 打开 {started['verification_uri']}\n2. 输入设备码 {started['user_code']}\n3. 授权后执行：/login {pid} complete", None
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
