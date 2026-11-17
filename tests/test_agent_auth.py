"""D170：AVA 独立多服务商 `/login` 认证、OAuth 续期、多协议转换与 N50 密钥隔离测试。"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import stat
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from pipeline.agent import auth, compact, llm, model_switch, tools


def _jwt_with_account(account_id: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode("ascii")
    payload_bytes = json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}).encode("utf-8")
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    return f"{header}.{payload}.sig"


def _make_mock_server(handler_fn):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            handler_fn(self, "GET", b"")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            handler_fn(self, "POST", body)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _send_json(req: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    raw = json.dumps(data).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json")
    req.send_header("Content-Length", str(len(raw)))
    req.end_headers()
    req.wfile.write(raw)


def _send_sse(req: BaseHTTPRequestHandler, chunks: list[str]) -> None:
    payload = "".join(chunks).encode("utf-8")
    req.send_response(200)
    req.send_header("Content-Type", "text/event-stream")
    req.send_header("Content-Length", str(len(payload)))
    req.end_headers()
    req.wfile.write(payload)


def test_provider_matrix_and_no_pi_dependency() -> None:
    import re

    ids = [p.id for p in auth.PROVIDERS]
    assert len(ids) == 44
    assert len(set(ids)) == 44
    for pid in ids:
        assert re.fullmatch(r"[a-z0-9-]+", pid), f"invalid provider id: {pid}"
    required = {
        "cliproxyapi",
        "ollama",
        "lmstudio",
        "anthropic",
        "openai",
        "openai-codex",
        "github-copilot",
        "openrouter",
        "kimi-coding",
        "xai",
        "meta",
        "google",
        "google-vertex",
        "amazon-bedrock",
        "azure",
        "mistral",
        "groq",
        "cerebras",
        "together",
        "fireworks",
        "huggingface",
        "nvidia",
        "baseten",
        "deepseek",
        "moonshotai",
        "moonshotai-cn",
        "zai",
        "zai-coding-cn",
        "minimax",
        "minimax-cn",
        "qwen-token-plan",
        "qwen-token-plan-cn",
        "xiaomi",
        "ant-ling",
        "cloudflare-ai-gateway",
        "cloudflare-workers-ai",
        "vercel-ai-gateway",
        "opencode",
        "opencode-go",
    }
    assert required.issubset(set(ids))
    assert auth.PROVIDER_MAP["google-gemini"].id == "google"
    root = Path(__file__).resolve().parent.parent
    for rel in ("pipeline/agent/auth.py", "pipeline/agent/model_switch.py", "pipeline/agent/llm.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert ".pi/agent" not in text
        assert "~/.pi" not in text
        assert "client_version=pi" not in text


def test_auth_storage_perms_and_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom" / "ava-auth.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom))
    assert auth.auth_path(tmp_path) == custom

    res = auth.complete_auth_flow("deepseek", {"mode": "api_key", "api_key": "sk-deepseek-secret-123456"}, root=tmp_path)
    assert res["ok"] is True
    assert custom.is_file()
    assert stat.S_IMODE(custom.stat().st_mode) == 0o600

    status = auth.get_auth_status(tmp_path)
    assert status["connected_count"] == 1
    ds = next(p for p in status["providers"] if p["id"] == "deepseek")
    assert ds["connected"] is True
    assert ds["masked_secret"].startswith("sk-d")
    assert "123456" not in json.dumps(status)

    out = auth.logout_provider("deepseek", root=tmp_path)
    assert out["removed"] is True
    assert auth.get_auth_status(tmp_path)["connected_count"] == 0


def test_pkce_flow_code_state_url_and_auto_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        text = body.decode("utf-8")
        seen.append({"path": req.path, "body": text, "headers": dict(req.headers)})
        if req.path == "/token":
            parsed = urllib.parse.parse_qs(text)
            grant = parsed.get("grant_type", [""])[0]
            if grant == "authorization_code":
                _send_json(req, {
                    "access_token": _jwt_with_account("acct_codex_99"),
                    "refresh_token": "rt-initial-codex",
                    "expires_in": 10,  # <= REFRESH_SKEW_MS (60s) -> next resolve triggers refresh!
                })
                return
            if grant == "refresh_token":
                _send_json(req, {
                    "access_token": _jwt_with_account("acct_codex_refreshed"),
                    "refresh_token": "rt-rotated-codex",
                    "expires_in": 3600,
                })
                return
        _send_json(req, {"error": "not found"}, 404)

    srv, base = _make_mock_server(handler)
    try:
        orig_codex = auth.PROVIDER_MAP["openai-codex"]
        patched_oauth = {**orig_codex.oauth, "token_url": f"{base}/token"}
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "openai-codex",
            type(orig_codex)(**{**orig_codex.__dict__, "oauth": patched_oauth}),
        )

        started = auth.start_auth_flow("openai-codex", "oauth_pkce", root=tmp_path)
        assert started["mode"] == "oauth_pkce"
        assert "code_challenge_method=S256" in started["authorize_url"]
        state = started["state"]

        with pytest.raises(auth.AuthError, match="state"):
            auth.complete_auth_flow(
                "openai-codex",
                {"mode": "oauth_pkce", "code": "abc#wrong-state"},
                root=tmp_path,
            )

        callback_url = f"http://localhost:1455/auth/callback?code=authcode-xyz&state={state}"
        done = auth.complete_auth_flow("openai-codex", {"mode": "oauth_pkce", "code": callback_url}, root=tmp_path)
        assert done["ok"] is True

        cred = auth.resolve_provider_credential("openai-codex", root=tmp_path)
        assert cred is not None
        assert cred["extra_headers"]["chatgpt-account-id"] == "acct_codex_refreshed"
        assert cred["extra_headers"]["originator"] == "ava"
        assert any("grant_type=refresh_token" in item["body"] for item in seen)
    finally:
        srv.shutdown()


def test_github_copilot_device_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poll_count = 0

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        nonlocal poll_count
        if req.path == "/device/code":
            _send_json(req, {
                "device_code": "dc-123",
                "user_code": "AVA-9988",
                "verification_uri": "https://github.com/login/device",
                "interval": 1,
                "expires_in": 300,
            })
            return
        if req.path == "/login/oauth/access_token":
            poll_count += 1
            if poll_count == 1:
                _send_json(req, {"error": "authorization_pending"})
            else:
                _send_json(req, {"access_token": "ghu_copilot_refresh_secret"})
            return
        if req.path == "/copilot_internal/v2/token":
            _send_json(req, {
                "token": "tid=copilot-session-token",
                "expires_at": int(os.path.getmtime(tmp_path)) + 1800,
                "endpoints": {"api": "https://api.enterprise.githubcopilot.com"},
            })
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        orig = auth.PROVIDER_MAP["github-copilot"]
        patched_oauth = {
            **orig.oauth,
            "device_code_url": f"{base}/device/code",
            "token_url": f"{base}/login/oauth/access_token",
            "copilot_token_url": f"{base}/copilot_internal/v2/token",
        }
        monkeypatch.setitem(auth.PROVIDER_MAP, "github-copilot", type(orig)(**{**orig.__dict__, "oauth": patched_oauth}))

        s = auth.start_auth_flow("github-copilot", root=tmp_path)
        assert s["user_code"] == "AVA-9988"

        pending = auth.complete_auth_flow("github-copilot", {"mode": "oauth_device"}, root=tmp_path)
        assert pending["ok"] is False and pending["pending"] is True

        ready = auth.complete_auth_flow("github-copilot", {"mode": "oauth_device"}, root=tmp_path)
        assert ready["ok"] is True

        cred = auth.resolve_provider_credential("github-copilot", root=tmp_path)
        assert cred is not None
        assert cred["api_key"] == "tid=copilot-session-token"
        assert cred["base_url"] == "https://api.enterprise.githubcopilot.com"
    finally:
        srv.shutdown()


def test_cliproxyapi_sync_routing_and_openrouter_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_file = tmp_path / "models.local.json"
    monkeypatch.setenv("AVA_MODELS_CACHE_PATH", str(cache_file))

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        if req.path == "/v1/models":
            _send_json(req, {
                "data": [
                    {"id": "cpa-custom-thinker", "thinkingLevelMap": {"none": None, "low": "low", "max": "max"}},
                ],
            })
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        res = auth.complete_auth_flow(
            "cliproxyapi",
            {"mode": "proxy", "base_url": f"{base}/v1", "api_key": "proxy-secret-token-1"},
            root=tmp_path,
        )
        assert res["ok"] is True
        assert res["models"] == ["cpa-custom-thinker"]
        assert cache_file.is_file()

        auth.complete_auth_flow("openrouter", {"mode": "api_key", "api_key": "sk-or-v1-secret-999"}, root=tmp_path)
        models = model_switch.list_models_unauthenticated(tmp_path)
        assert "cliproxyapi/cpa-custom-thinker" in models
        assert "openrouter/anthropic/claude-sonnet-4.6" in models

        assert model_switch.efforts_for_model("cliproxyapi/cpa-custom-thinker", tmp_path) == ("none", "low", "max")
        assert "max" in model_switch.efforts_for_model("openrouter/anthropic/claude-sonnet-4.6", tmp_path)

        base_cfg = llm.load_llm_config(tmp_path)
        assert base_cfg is not None  # load_llm_config falls back to auth.local.json when agent.json missing!

        or_cfg = model_switch.effective_config(base_cfg, model_switch.Override(model="openrouter/anthropic/claude-sonnet-4.6"), root=tmp_path)
        assert or_cfg is not None
        assert or_cfg.model == "anthropic/claude-sonnet-4.6"  # keeps openrouter vendor prefix intact!
        assert or_cfg.api_key == "sk-or-v1-secret-999"
        assert "openrouter.ai" in or_cfg.base_url
    finally:
        srv.shutdown()


def test_anthropic_and_codex_protocol_roundtrip(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        req_json = json.loads(body.decode("utf-8"))
        captured.append({"path": req.path, "headers": {k.lower(): v for k, v in req.headers.items()}, "body": req_json})
        if req.path == "/v1/messages":
            _send_sse(req, [
                'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":120}}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"先分析步骤"}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"text"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"完成回复"}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tu_1","name":"read_file"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"a.md\\"}"}}\n\n',
                'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":45}}\n\n',
                'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            ])
            return
        if req.path == "/codex/responses":
            _send_sse(req, [
                'data: {"type":"response.reasoning_summary_text.delta","delta":"codex思考"}\n\n',
                'data: {"type":"response.output_text.delta","delta":"codex正文"}\n\n',
                'data: {"type":"response.output_item.added","item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"list_dir"}}\n\n',
                'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"{\\"dir\\":\\".\\"}"}\n\n',
                'data: {"type":"response.completed","response":{"usage":{"input_tokens":80,"output_tokens":20}}}\n\n',
            ])
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        msgs = [
            {"role": "system", "content": "system rule"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "c0", "type": "function", "function": {"name": "ping", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c0", "content": "pong"},
        ]
        tools_spec = [{"type": "function", "function": {"name": "read_file", "description": "d", "parameters": {"type": "object"}}}]

        anth_cfg = llm.LLMConfig(
            base_url=base,
            model="claude-opus-4-6",
            api_key="sk-ant-key-123",
            reasoning_effort="xhigh",
            api_protocol="anthropic-messages",
        )
        anth_msg = llm.chat_complete(msgs, tools_spec, config=anth_cfg, root=tmp_path)
        assert anth_msg["content"] == "完成回复"
        assert anth_msg["reasoning_content"] == "先分析步骤"
        assert anth_msg["tool_calls"][0]["function"]["name"] == "read_file"
        assert json.loads(anth_msg["tool_calls"][0]["function"]["arguments"]) == {"path": "a.md"}
        assert llm._LAST_USAGE.get() == {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}

        anth_sent = captured[0]
        assert anth_sent["headers"]["x-api-key"] == "sk-ant-key-123"
        assert anth_sent["body"]["thinking"]["type"] == "enabled"
        assert anth_sent["body"]["max_tokens"] > anth_sent["body"]["thinking"]["budget_tokens"]

        codex_cfg = llm.LLMConfig(
            base_url=f"{base}/codex/responses",
            model="gpt-5.4",
            api_key="codex-jwt-123",
            reasoning_effort="max",
            api_protocol="openai-codex-responses",
            extra_headers={"chatgpt-account-id": "acct_42"},
        )
        codex_msg = llm.chat_complete(msgs, tools_spec, config=codex_cfg, root=tmp_path)
        assert codex_msg["content"] == "codex正文"
        assert codex_msg["reasoning_content"] == "codex思考"
        assert codex_msg["tool_calls"][0]["id"] == "call_1"
        assert json.loads(codex_msg["tool_calls"][0]["function"]["arguments"]) == {"dir": "."}
        assert llm._LAST_USAGE.get() == {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}

        codex_sent = captured[1]
        assert codex_sent["headers"]["chatgpt-account-id"] == "acct_42"
        assert codex_sent["body"]["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        assert codex_sent["body"]["instructions"] == "system rule"
    finally:
        srv.shutdown()


def test_cli_auth_subcommands_and_n50_secret_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    custom = tmp_path / "isolated-auth.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom))

    assert auth.dispatch_auth_cli(["--auth-status"], root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["connected_count"] == 0

    stdin_payload = json.dumps({"mode": "api_key", "api_key": "sk-super-secret-n50-value-xyz"})
    assert auth.dispatch_auth_cli(["--auth-complete", "openai", "--stdin"], stdin_reader=lambda: stdin_payload, root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    # N50: auth.local.json blocked from egress AND secrets scrubbed from logs/outputs
    with pytest.raises(PermissionError):
        tools.assert_egress_boundary("https://example.com", {"text": "leak config/auth.local.json"}, trusted_texts=())
    assert "sk-super-secret-n50-value-xyz" not in tools.scrub_secrets("err sk-super-secret-n50-value-xyz", root=tmp_path)

    assert auth.dispatch_auth_cli(["--auth-logout", "openai"], root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is True


def test_anthropic_tool_choice_none_and_thinking_tool_loop_continuation(tmp_path: Path) -> None:
    """B1 + B2 + NB#2 回归：tool_choice='none'、signed thinking 跨工具轮续传与 max_tokens 上限夹紧。"""
    sent_bodies: list[dict[str, Any]] = []
    turn = {"n": 0}

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        req_json = json.loads(body.decode("utf-8"))
        sent_bodies.append(req_json)
        turn["n"] += 1
        n = turn["n"]
        if n == 1:
            _send_sse(req, [
                'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":50}}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"先查期列表"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig_part_1"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"_part_2"}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu_ep","name":"list_episodes"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n',
                'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":30}}\n\n',
                'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            ])
            return
        if n == 2:
            # 第二轮再发一次 tool_use，让后续 approve 抛 KeyboardInterrupt 触发 tool_choice="none" 收尾（B1）
            _send_sse(req, [
                'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":80}}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tu_ep2","name":"list_episodes"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n',
                'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":10}}\n\n',
                'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            ])
            return
        _send_sse(req, [
            'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":90}}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"已收尾停止"}}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":12}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ])

    srv, base = _make_mock_server(handler)
    try:
        repo_root = Path(__file__).resolve().parents[1]
        (tmp_path / "config" / "agent").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "agent" / "tools.json").write_text(
            (repo_root / "config" / "agent" / "tools.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        cfg_high = llm.LLMConfig(
            base_url=base,
            model="claude-opus-4-6",
            api_key="sk-ant-key-123",
            reasoning_effort="high",
            api_protocol="anthropic-messages",
        )
        # B1 直接载荷断言
        none_payload, _ = llm._anthropic_request(cfg_high, [{"role": "user", "content": "stop"}], None, None, "none")
        assert none_payload["tool_choice"] == {"type": "none"}

        # NB #2：max 档位 clamp max_tokens <= 64000 且 budget_tokens < max_tokens
        cfg_max = llm.LLMConfig(
            base_url=base,
            model="claude-opus-4-6",
            api_key="sk-ant-key-123",
            reasoning_effort="max",
            api_protocol="anthropic-messages",
        )
        max_payload, _ = llm._anthropic_request(cfg_max, [{"role": "user", "content": "hi"}], None, None, None)
        assert max_payload["max_tokens"] == 64000
        assert max_payload["thinking"]["budget_tokens"] == 59904
        assert max_payload["thinking"]["budget_tokens"] < max_payload["max_tokens"]

        approve_calls = {"c": 0}

        def _approve(name: str, args: dict[str, Any]) -> bool:
            approve_calls["c"] += 1
            if approve_calls["c"] >= 2:
                raise KeyboardInterrupt
            return True

        res = llm.run_tool_loop(
            [{"role": "user", "content": "列出所有期"}],
            config=cfg_high,
            root=tmp_path,
            scope="idea",
            approve=_approve,
        )
        assert res["stopped"] == "interrupted"
        assert len(sent_bodies) == 3

        # B2：Turn 2 请求里的 assistant 历史消息必须以带完整 signature 的 thinking 块开头，紧跟 tool_use
        turn2_msgs = sent_bodies[1]["messages"]
        assert turn2_msgs[1]["role"] == "assistant"
        assert turn2_msgs[1]["content"][0] == {
            "type": "thinking",
            "thinking": "先查期列表",
            "signature": "sig_part_1_part_2",
        }
        assert turn2_msgs[1]["content"][1]["type"] == "tool_use"
        assert turn2_msgs[2]["role"] == "user"
        assert turn2_msgs[2]["content"][0]["type"] == "tool_result"

        # 复审残留项 1：无签名 reasoning_content / 无签名 thinking_blocks 必须丢弃，只有带签名才回传
        unsigned_payload, _ = llm._anthropic_request(
            cfg_high,
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "reasoning_content": "来自 openai 的无签名思考", "content": "a1"},
                {"role": "user", "content": "q2"},
            ],
            None,
            None,
            None,
        )
        assert unsigned_payload["messages"][1]["content"] == [{"type": "text", "text": "a1"}]

        signed_payload, _ = llm._anthropic_request(
            cfg_high,
            [
                {"role": "user", "content": "q1"},
                {
                    "role": "assistant",
                    "reasoning_content": "带签名的历史思考",
                    "reasoning_signature": "sig_ok",
                    "content": "a1",
                },
                {"role": "user", "content": "q2"},
            ],
            None,
            None,
            None,
        )
        assert signed_payload["messages"][1]["content"] == [
            {"type": "thinking", "thinking": "带签名的历史思考", "signature": "sig_ok"},
            {"type": "text", "text": "a1"},
        ]

        # B1：Turn 3 收尾请求必须发 {"type": "none"}，而非 {"type": "tool", "name": "none"}
        assert sent_bodies[2]["tool_choice"] == {"type": "none"}
    finally:
        srv.shutdown()


def test_compact_window_and_frontend_family_heuristics_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NB #3/#4/#6/#12：前缀剥离复用 compact 窗口 + 前后端启发式一致 + AuthError 降级 + 过期 pending 清理。"""
    cfg = compact.load_compact_config(Path(__file__).resolve().parents[1])
    w_anth = cfg.window_for("anthropic/claude-sonnet-4-6")
    w_or = cfg.window_for("openrouter/anthropic/claude-sonnet-4-6")
    assert w_anth is not None and w_anth.window == 200000
    assert w_or is not None and w_or.window == 200000
    assert cfg.window_for("unknown/claude-sonnet-4-6") is None

    # 解析 SessionHeader.tsx 存在性与关键家族分支（全对齐走 node 侧与 Python 对比）
    tsx = (Path(__file__).resolve().parents[1] / "desktop" / "src" / "renderer" / "SessionHeader.tsx").read_text(encoding="utf-8")
    assert 'lastIndexOf("/")' in tsx
    assert "claude-opus-4.6" in tsx and "claude-sonnet-4.6" in tsx
    assert model_switch._family_heuristic_efforts("openrouter/anthropic/claude-opus-4.6") == (
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    )
    assert model_switch._family_heuristic_efforts("devin/gpt-6-luna") == (
        "none", "minimal", "low", "medium", "high", "xhigh"
    )

    # NB #4：过期 _pending 条目在写盘时自动清理
    custom_auth = tmp_path / "auth.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom_auth))
    store = {
        "providers": {},
        "_pending": {
            "anthropic": {"mode": "oauth_pkce", "created_at_ms": 1_000},
            "openai-codex": {"mode": "oauth_pkce", "created_at_ms": auth._now_ms()},
        },
    }
    auth._write_store_unlocked(custom_auth, store)
    reloaded = auth._read_store_unlocked(custom_auth)
    assert "anthropic" not in reloaded["_pending"]
    assert "openai-codex" in reloaded["_pending"]

    # M10 + NB #3：OAuth 续期失败时 _load_auth_fallback_cfg 优雅回落 None，effective_config 转为不可重试 LLMError 并提示 /login
    monkeypatch.setattr(auth, "resolve_model_provider", lambda *a, **kw: (_ for _ in ()).throw(auth.AuthError("refresh boom")))
    base = llm.LLMConfig(base_url="http://127.0.0.1:1/v1", model="fake", api_key="k")
    with pytest.raises(llm.LLMError) as exc_info:
        model_switch.effective_config(base, model_switch.Override(model="anthropic/claude-opus-4-6"), root=tmp_path)
    assert exc_info.value.retryable is False
    assert "/login" in str(exc_info.value)

    monkeypatch.setattr(auth, "first_authenticated_config", lambda *a, **kw: (_ for _ in ()).throw(auth.AuthError("refresh boom")))
    assert llm._load_auth_fallback_cfg(tmp_path) is None


def test_env_detection_alias_routing_and_corrupt_tolerance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "auth.local.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom))
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-secret-abcdef123456")

    status = auth.get_auth_status(tmp_path)
    # 环境变量不计入落盘 connected_count，但暴露 env_configured 与脱敏提示且绝不回传明文
    assert status["connected_count"] == 0
    assert "abcdef123456" not in json.dumps(status)
    google_row = next(p for p in status["providers"] if p["id"] == "google")
    assert google_row["env_configured"] is True
    assert google_row["env_source"] == "GEMINI_API_KEY"
    assert google_row["masked_env_secret"].startswith("AIza")

    # 兼容旧 google-gemini/ 前缀路由
    resolved = auth.resolve_model_provider("google-gemini/gemini-2.5-pro", root=tmp_path)
    assert resolved is not None
    pid, wire, cred = resolved
    assert pid == "google"
    assert wire == "gemini-2.5-pro"
    assert cred["api_protocol"] == "google-generative-ai"
    assert cred["api_key"] == "AIza-test-secret-abcdef123456"

    # auth.local.json 损坏时明确报 AuthError
    custom.write_text("{corrupt-json", encoding="utf-8")
    with pytest.raises(auth.AuthError, match="损坏"):
        auth.get_auth_status(tmp_path)


def test_cloud_extra_fields_masking_and_dynamic_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))

    # Cloudflare AI Gateway 缺必填 extra_fields 时拒绝
    with pytest.raises(auth.AuthError, match="Cloudflare Account ID"):
        auth.complete_auth_flow("cloudflare-ai-gateway", {"mode": "api_key", "api_key": "cf-key-123"}, root=tmp_path)

    res_cf = auth.complete_auth_flow(
        "cloudflare-ai-gateway",
        {
            "mode": "api_key",
            "api_key": "cf-secret-token-xyz",
            "extra_config": {"account_id": "acc123", "gateway_id": "gw456"},
        },
        root=tmp_path,
    )
    assert res_cf["ok"] is True
    cred_cf = auth.resolve_provider_credential("cloudflare-ai-gateway", root=tmp_path)
    assert cred_cf is not None
    assert cred_cf["base_url"] == "https://gateway.ai.cloudflare.com/v1/acc123/gw456/compat"

    # Azure 自动拼接资源地址 + 保留 api_version / deployment_name
    auth.complete_auth_flow(
        "azure",
        {
            "mode": "api_key",
            "api_key": "az-secret-999",
            "extra_config": {"resource_name": "ava-aoai", "deployment_name": "dep-gpt5", "api_version": "2025-04-01-preview"},
        },
        root=tmp_path,
    )
    cred_az = auth.resolve_provider_credential("azure", root=tmp_path)
    assert cred_az is not None
    assert cred_az["base_url"] == "https://ava-aoai.openai.azure.com/openai/v1"
    cfg_az = llm.LLMConfig(
        base_url=cred_az["base_url"],
        model="gpt-5.4",
        api_key=cred_az["api_key"],
        api_protocol="azure-openai-responses",
        extra_config=cred_az["extra_config"],
    )
    assert cfg_az.endpoint == "https://ava-aoai.openai.azure.com/openai/v1/responses?api-version=2025-04-01-preview"
    assert cfg_az._effective_model(None) == "dep-gpt5"

    # Bedrock IAM 凭据支持无 Bearer Token 保存，且 secret_access_key 在状态接口脱敏
    auth.complete_auth_flow(
        "amazon-bedrock",
        {
            "mode": "api_key",
            "extra_config": {
                "aws_region": "us-west-2",
                "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
                "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            },
        },
        root=tmp_path,
    )
    status = auth.get_auth_status(tmp_path)
    assert "bPxRfiCYEXAMPLEKEY" not in json.dumps(status)
    br_row = next(p for p in status["providers"] if p["id"] == "amazon-bedrock")
    assert br_row["connected"] is True
    assert br_row["base_url"] == "https://bedrock-runtime.us-west-2.amazonaws.com"
    assert br_row["extra_config"]["aws_secret_access_key"].startswith("wJal")

    # Ollama 本地无密钥连接（skip_probe）
    res_ol = auth.complete_auth_flow("ollama", {"mode": "proxy", "skip_probe": True}, root=tmp_path)
    assert res_ol["ok"] is True


def test_new_oauth_flows_openai_openrouter_kimi_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))
    requests_seen: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        raw = body.decode("utf-8")
        requests_seen.append({"path": req.path, "body": raw, "headers": dict(req.headers)})
        if req.path == "/openrouter/keys":
            data = json.loads(raw)
            assert data["code"] == "or-auth-code-1"
            assert data["code_challenge_method"] == "S256"
            _send_json(req, {"key": "sk-or-v1-issued-permanent-key"})
            return
        if req.path == "/openai/token":
            parsed = urllib.parse.parse_qs(raw)
            grant = parsed["grant_type"][0]
            assert parsed["client_id"][0] == "issued-dyn-cid-42"
            assert parsed["resource"][0] == "https://api.openai.com/v1"
            _send_json(
                req,
                {
                    "access_token": f"oa-access-{grant}",
                    "refresh_token": "oa-refresh-1",
                    "expires_in": 10 if grant == "authorization_code" else 3600,
                    "scope": "openid profile email offline_access chatgpt.tokens.use.direct",
                },
            )
            return
        if req.path == "/kimi/device":
            _send_json(
                req,
                {
                    "device_code": "k-dc-1",
                    "user_code": "KIMI-123",
                    "verification_uri_complete": "https://auth.kimi.com/verify?code=KIMI-123",
                    "interval": 1,
                    "expires_in": 600,
                },
            )
            return
        if req.path == "/kimi/token":
            _send_json(req, {"access_token": "km-access-1", "refresh_token": "km-refresh-1", "expires_in": 3600})
            return
        if req.path == "/meta/device":
            _send_json(
                req,
                {
                    "device_code": "m-dc-1",
                    "user_code": "META-999",
                    "verification_uri": "https://meta.com/device",
                    "interval": 1,
                    "expires_in": 600,
                },
            )
            return
        if req.path == "/meta/token":
            _send_json(req, {"access_token": "meta-ident-tok"})
            return
        if req.path == "/meta/key":
            assert req.headers.get("Authorization") == "Bearer meta-ident-tok"
            _send_json(req, {"api_key": "meta-minted-api-key"})
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        # 1. OpenRouter PKCE permanent key
        orig_or = auth.PROVIDER_MAP["openrouter"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "openrouter",
            type(orig_or)(**{**orig_or.__dict__, "oauth": {**orig_or.oauth, "token_url": f"{base}/openrouter/keys"}}),
        )
        s_or = auth.start_auth_flow("openrouter", "oauth_pkce", root=tmp_path)
        assert "code_challenge=" in s_or["authorize_url"]
        c_or = auth.complete_auth_flow(
            "openrouter",
            {"mode": "oauth_pkce", "code": "http://127.0.0.1:1455/auth/callback?code=or-auth-code-1"},
            root=tmp_path,
        )
        assert c_or["ok"] is True
        assert auth.resolve_provider_credential("openrouter", root=tmp_path)["api_key"] == "sk-or-v1-issued-permanent-key"

        # 2. OpenAI dynamic client_id PKCE + refresh reuse
        orig_oa = auth.PROVIDER_MAP["openai"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "openai",
            type(orig_oa)(**{**orig_oa.__dict__, "oauth": {**orig_oa.oauth, "token_url": f"{base}/openai/token"}}),
        )
        s_oa = auth.start_auth_flow("openai", "oauth_pkce", root=tmp_path)
        cb_oa = f"http://127.0.0.1:1455/auth/callback?code=oa-code&state={s_oa['state']}&client_id=issued-dyn-cid-42"
        auth.complete_auth_flow("openai", {"mode": "oauth_pkce", "code": cb_oa}, root=tmp_path)
        cred_oa = auth.resolve_provider_credential("openai", root=tmp_path)
        assert cred_oa is not None and cred_oa["api_key"] == "oa-access-refresh_token"

        # 3. Kimi device flow
        orig_km = auth.PROVIDER_MAP["kimi-coding"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "kimi-coding",
            type(orig_km)(
                **{
                    **orig_km.__dict__,
                    "oauth": {**orig_km.oauth, "device_code_url": f"{base}/kimi/device", "token_url": f"{base}/kimi/token"},
                }
            ),
        )
        s_km = auth.start_auth_flow("kimi-coding", "oauth_device", root=tmp_path)
        assert s_km["user_code"] == "KIMI-123"
        auth.complete_auth_flow("kimi-coding", {"mode": "oauth_device"}, root=tmp_path)
        assert auth.resolve_provider_credential("kimi-coding", root=tmp_path)["api_key"] == "km-access-1"

        # 4. Meta device flow + key minting
        orig_meta = auth.PROVIDER_MAP["meta"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "meta",
            type(orig_meta)(
                **{
                    **orig_meta.__dict__,
                    "oauth": {
                        **orig_meta.oauth,
                        "device_code_url": f"{base}/meta/device",
                        "token_url": f"{base}/meta/token",
                        "exchange_key_url": f"{base}/meta/key",
                    },
                }
            ),
        )
        auth.start_auth_flow("meta", "oauth_device", root=tmp_path)
        auth.complete_auth_flow("meta", {"mode": "oauth_device"}, root=tmp_path)
        assert auth.resolve_provider_credential("meta", root=tmp_path)["api_key"] == "meta-minted-api-key"
    finally:
        srv.shutdown()


def test_unified_protocol_adapters_gemini_bedrock_azure_mistral(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        raw_json = json.loads(body.decode("utf-8"))
        seen.append({"path": req.path, "headers": {k.lower(): v for k, v in req.headers.items()}, "body": raw_json})
        if "/models/gemini-2.5-pro:streamGenerateContent" in req.path:
            _send_sse(
                req,
                [
                    'data: {"candidates":[{"content":{"parts":[{"thought":true,"text":"plan ","thoughtSignature":"sig-g1"},{"functionCall":{"name":"read_file","args":{"path":"a.txt"}}}]}}]}\n\n',
                    'data: {"candidates":[{"content":{"parts":[{"text":"done"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":5,"totalTokenCount":17}}\n\n',
                ],
            )
            return
        if "/model/anthropic.claude-opus-5/converse" in req.path:
            _send_json(
                req,
                {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"reasoningContent": {"reasoningText": {"text": "thinking"}}},
                                {"text": "bedrock-ok"},
                                {"toolUse": {"toolUseId": "tu_1", "name": "ls", "input": {"dir": "."}}},
                            ],
                        }
                    },
                    "usage": {"inputTokens": 20, "outputTokens": 10, "totalTokens": 30},
                },
            )
            return
        if req.path.startswith("/openai/v1/responses?api-version="):
            _send_json(
                req,
                {
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "azure-ok"}]}],
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                },
            )
            return
        if req.path == "/v1/chat/completions":
            _send_json(
                req,
                {
                    "choices": [{"message": {"role": "assistant", "content": "mistral-ok"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                },
            )
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    sample_tool = [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    try:
        # Gemini REST + thoughtSignature + functionCall
        g_cfg = llm.LLMConfig(
            base_url=f"{base}/v1beta",
            model="gemini-2.5-pro",
            api_key="gem-secret",
            api_protocol="google-generative-ai",
        )
        g_msg = llm.chat_complete(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            tools=sample_tool,
            config=g_cfg,
            root=tmp_path,
        )
        assert g_msg["content"] == "done"
        assert g_msg["reasoning_content"] == "plan "
        assert g_msg["thinking_blocks"][0]["signature"] == "sig-g1"
        assert g_msg["tool_calls"][0]["function"]["name"] == "read_file"
        assert seen[-1]["headers"].get("x-goog-api-key") == "gem-secret"
        assert (llm._LAST_USAGE.get() or {}).get("total_tokens") == 17

        # Bedrock SigV4 Converse
        b_cfg = llm.LLMConfig(
            base_url=base,
            model="anthropic.claude-opus-5",
            api_key="",
            api_protocol="bedrock-converse-stream",
            extra_config={"aws_region": "us-east-1", "aws_access_key_id": "AKIA123", "aws_secret_access_key": "SECRET456"},
        )
        b_msg = llm.chat_complete([{"role": "user", "content": "hi"}], tools=sample_tool, config=b_cfg, root=tmp_path)
        assert b_msg["content"] == "bedrock-ok"
        assert b_msg["reasoning_content"] == "thinking"
        assert b_msg["tool_calls"][0]["id"] == "tu_1"
        assert "AWS4-HMAC-SHA256" in seen[-1]["headers"].get("authorization", "")

        # Azure Responses (api-key header + deployment_name + api-version)
        a_cfg = llm.LLMConfig(
            base_url=f"{base}/openai/v1",
            model="gpt-5.4",
            api_key="az-key-1",
            api_protocol="azure-openai-responses",
            extra_config={"deployment_name": "my-dep", "api_version": "2025-04-01-preview"},
        )
        a_msg = llm.chat_complete([{"role": "user", "content": "hi"}], config=a_cfg, root=tmp_path)
        assert a_msg["content"] == "azure-ok"
        assert seen[-1]["headers"].get("api-key") == "az-key-1"
        assert seen[-1]["body"]["model"] == "my-dep"

        # Mistral 9-char alphanumeric tool_call_id & no stream_options
        m_cfg = llm.LLMConfig(base_url=base, model="mistral-medium-3.5", api_key="ms-key", api_protocol="mistral-conversations")
        m_msgs = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_very_long_open_id_12345", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_very_long_open_id_12345", "content": "ok"},
        ]
        m_msg = llm.chat_complete(m_msgs, config=m_cfg, root=tmp_path)
        assert m_msg["content"] == "mistral-ok"
        sent_m = seen[-1]["body"]
        assert "stream_options" not in sent_m
        norm_id = sent_m["messages"][0]["tool_calls"][0]["id"]
        assert len(norm_id) == 9 and norm_id.isalnum()
        assert sent_m["messages"][1]["tool_call_id"] == norm_id
    finally:
        srv.shutdown()


def test_device_flow_pending_errors_slowdown_and_codex_redirect_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1 + B2 + M14：RFC 8628 HTTP 400 pending、slow_down 回写间隔、Codex 403 嵌套 code 与固定 deviceauth/callback。"""
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))
    kimi_calls = 0
    codex_calls = 0
    codex_token_bodies: list[str] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        nonlocal kimi_calls, codex_calls
        raw = body.decode("utf-8")
        if req.path == "/kimi/device":
            _send_json(
                req,
                {
                    "device_code": "k-dc-pending",
                    "user_code": "KIMI-P1",
                    "verification_uri_complete": "https://auth.kimi.com/verify",
                    "interval": 5,
                    "expires_in": 600,
                },
            )
            return
        if req.path == "/kimi/token":
            kimi_calls += 1
            if kimi_calls == 1:
                _send_json(req, {"error": "authorization_pending"}, status=400)
                return
            if kimi_calls == 2:
                _send_json(req, {"error": "slow_down", "interval": 11}, status=400)
                return
            _send_json(req, {"access_token": "km-final-tok", "refresh_token": "km-ref", "expires_in": 3600})
            return
        if req.path == "/codex/usercode":
            _send_json(
                req,
                {"device_auth_id": "dauth_123", "user_code": "CDX-99", "interval": "5"},
            )
            return
        if req.path == "/codex/devtoken":
            codex_calls += 1
            if codex_calls == 1:
                _send_json(req, {"error": {"code": "deviceauth_authorization_pending"}}, status=403)
                return
            if codex_calls == 2:
                _send_json(req, {"error": "not_found"}, status=404)
                return
            _send_json(req, {"authorization_code": "cdx-ac-1", "code_verifier": "cdx-cv-1"})
            return
        if req.path == "/codex/oauth/token":
            codex_token_bodies.append(raw)
            _send_json(
                req,
                {
                    "access_token": _jwt_with_account("acct_dev_1"),
                    "refresh_token": "cdx-refresh-tok",
                    "expires_in": 3600,
                },
            )
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        orig_km = auth.PROVIDER_MAP["kimi-coding"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "kimi-coding",
            type(orig_km)(
                **{**orig_km.__dict__, "oauth": {**orig_km.oauth, "device_code_url": f"{base}/kimi/device", "token_url": f"{base}/kimi/token"}}
            ),
        )
        auth.start_auth_flow("kimi-coding", "oauth_device", root=tmp_path)
        r1 = auth.complete_auth_flow("kimi-coding", {"mode": "oauth_device"}, root=tmp_path)
        assert r1["ok"] is False and r1["pending"] is True

        r2 = auth.complete_auth_flow("kimi-coding", {"mode": "oauth_device"}, root=tmp_path)
        assert r2["ok"] is False and r2["pending"] is True
        assert r2["interval_s"] == 11
        persisted = auth._read_store_unlocked(tmp_path / "auth.json")
        assert persisted["_pending"]["kimi-coding"]["interval_s"] == 11

        r3 = auth.complete_auth_flow("kimi-coding", {"mode": "oauth_device"}, root=tmp_path)
        assert r3["ok"] is True

        # Codex device flow: 403 nested error -> pending, 404 -> pending, success -> deviceauth/callback redirect_uri
        orig_cdx = auth.PROVIDER_MAP["openai-codex"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "openai-codex",
            type(orig_cdx)(
                **{
                    **orig_cdx.__dict__,
                    "oauth": {
                        **orig_cdx.oauth,
                        "device_code_url": f"{base}/codex/usercode",
                        "device_token_url": f"{base}/codex/devtoken",
                        "token_url": f"{base}/codex/oauth/token",
                    },
                }
            ),
        )
        auth.start_auth_flow("openai-codex", "oauth_device", root=tmp_path)
        r_cdx1 = auth.complete_auth_flow("openai-codex", {"mode": "oauth_device"}, root=tmp_path)
        assert r_cdx1["ok"] is False and r_cdx1["pending"] is True and r_cdx1["interval_s"] == 5
        assert r_cdx1["error"] == "deviceauth_authorization_pending"
        assert auth.complete_auth_flow("openai-codex", {"mode": "oauth_device"}, root=tmp_path)["pending"] is True
        done_cdx = auth.complete_auth_flow("openai-codex", {"mode": "oauth_device"}, root=tmp_path)
        assert done_cdx["ok"] is True
        parsed_qs = urllib.parse.parse_qs(codex_token_bodies[0])
        assert parsed_qs["redirect_uri"][0] == "https://auth.openai.com/deviceauth/callback"
        assert parsed_qs["code"][0] == "cdx-ac-1"
        assert parsed_qs["code_verifier"][0] == "cdx-cv-1"
    finally:
        srv.shutdown()


def test_anthropic_pkce_state_is_verifier_and_no_bare_code_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3 + M4 + Minor #10/#11：Anthropic state == verifier；非 state_optional 服务商裸 code 不可跳过 state；exchanging 标记释放；禁用重定向。"""
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))
    seen_token_bodies: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        if req.path == "/anth/redirect":
            req.send_response(302)
            req.send_header("Location", "http://127.0.0.1:9/evil")
            req.end_headers()
            return
        if req.path == "/anth/token":
            seen_token_bodies.append(json.loads(body.decode("utf-8")))
            _send_json(req, {"access_token": "ant-oa-tok", "refresh_token": "ant-ref-tok", "expires_in": 3600})
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        orig_ant = auth.PROVIDER_MAP["anthropic"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "anthropic",
            type(orig_ant)(**{**orig_ant.__dict__, "oauth": {**orig_ant.oauth, "token_url": f"{base}/anth/token"}}),
        )
        started = auth.start_auth_flow("anthropic", "oauth_pkce", root=tmp_path)
        pending = auth._read_store_unlocked(tmp_path / "auth.json")["_pending"]["anthropic"]
        assert started["state"] == pending["code_verifier"]
        parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(started["authorize_url"]).query)
        assert parsed_qs["state"][0] == pending["code_verifier"]

        # M4：anthropic state_optional=False，传裸 code 必须被拒且不可绕过 state 校验
        with pytest.raises(auth.AuthError, match="state"):
            auth.complete_auth_flow("anthropic", {"mode": "oauth_pkce", "code": "bare-code-without-state"}, root=tmp_path)

        # Minor #10：换取失败后 exchanging 标记清理，仍可重试成功
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "anthropic",
            type(orig_ant)(**{**orig_ant.__dict__, "oauth": {**orig_ant.oauth, "token_url": f"{base}/anth/redirect"}}),
        )
        with pytest.raises(auth.AuthError, match="重定向"):
            auth.complete_auth_flow(
                "anthropic",
                {"mode": "oauth_pkce", "code": f"code1#{started['state']}"},
                root=tmp_path,
            )
        pending_after_err = auth._read_store_unlocked(tmp_path / "auth.json")["_pending"]["anthropic"]
        assert not pending_after_err.get("exchanging")

        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "anthropic",
            type(orig_ant)(**{**orig_ant.__dict__, "oauth": {**orig_ant.oauth, "token_url": f"{base}/anth/token"}}),
        )
        res = auth.complete_auth_flow(
            "anthropic",
            {"mode": "oauth_pkce", "code": f"code2#{started['state']}"},
            root=tmp_path,
        )
        assert res["ok"] is True
        assert seen_token_bodies[0]["state"] == pending["code_verifier"]
    finally:
        srv.shutdown()


def test_gemini_function_call_thought_signature_roundtrip_and_cross_proto_strip(tmp_path: Path) -> None:
    """B4 + Minor #12：Gemini functionCall.thoughtSignature 多轮回放保留，跨协议回放 OpenAI Completions 时彻底剥离。"""
    captured: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        captured.append(json.loads(body.decode("utf-8")))
        if len(captured) == 1:
            _send_sse(
                req,
                [
                    'data: {"candidates":[{"content":{"parts":[{"functionCall":{"id":"fc_g1","name":"read_file","args":{"path":"x.md"}},"thoughtSignature":"sig-fc-987"}]}}]}\n\n',
                    'data: {"candidates":[{"content":{"parts":[{"text":""}]},"finishReason":"STOP"}]}\n\n',
                ],
            )
            return
        _send_json(req, {"candidates": [{"content": {"parts": [{"text": "round2-ok"}]}, "finishReason": "STOP"}]})

    srv, base = _make_mock_server(handler)
    try:
        g_cfg = llm.LLMConfig(
            base_url=f"{base}/v1beta",
            model="gemini-3-pro",
            api_key="gem-k",
            api_protocol="google-generative-ai",
        )
        turn1 = llm.chat_complete([{"role": "user", "content": "read x.md"}], config=g_cfg, root=tmp_path)
        assert turn1["tool_calls"][0]["thoughtSignature"] == "sig-fc-987"

        turn2 = llm.chat_complete(
            [
                {"role": "user", "content": "read x.md"},
                turn1,
                {"role": "tool", "tool_call_id": turn1["tool_calls"][0]["id"], "name": "read_file", "content": "ok"},
            ],
            config=g_cfg,
            root=tmp_path,
        )
        assert turn2["content"] == "round2-ok"
        model_parts = captured[1]["contents"][1]["parts"]
        assert model_parts[0]["functionCall"]["name"] == "read_file"
        assert model_parts[0]["thoughtSignature"] == "sig-fc-987"

        # 切到 OpenAI Completions 时，thinking_blocks / reasoning_signature / tool_calls[*].thoughtSignature 一并剥离
        oai_cfg = llm.LLMConfig(base_url=base, model="gpt-4o", api_key="oai-k", api_protocol="openai-completions")
        oai_payload, _ = llm._completions_request(
            oai_cfg,
            [
                {"role": "user", "content": "hi"},
                {**turn1, "reasoning_signature": "sig-old", "thinking_blocks": [{"type": "thinking", "thinking": "t"}]},
            ],
            None,
            None,
            None,
        )
        sent_asst = oai_payload["messages"][1]
        assert "thinking_blocks" not in sent_asst
        assert "reasoning_signature" not in sent_asst
        assert "thoughtSignature" not in sent_asst["tool_calls"][0]
    finally:
        srv.shutdown()


def test_copilot_api_key_and_env_exchange_plus_enterprise_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B5 + M13：Copilot api_key / legacy api_key / COPILOT_GITHUB_TOKEN 经 /copilot_internal/v2/token 换算；GHE enterprise_domain 设备流端到端。"""
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    auth._COPILOT_ENV_CACHE.clear()

    seen_auth_headers: list[tuple[str, str]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        seen_auth_headers.append((req.path, req.headers.get("Authorization", "")))
        if req.path == "/copilot_internal/v2/token":
            _send_json(
                req,
                {
                    "token": "tid=abc;exp=1999999999;proxy-ep=proxy.business.githubcopilot.com;",
                    "expires_at": int(time.time()) + 3600,
                },
            )
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        orig_cp = auth.PROVIDER_MAP["github-copilot"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "github-copilot",
            type(orig_cp)(**{**orig_cp.__dict__, "oauth": {**orig_cp.oauth, "copilot_token_url": f"{base}/copilot_internal/v2/token"}}),
        )
        # 1. 新登录 api_key 模式：立即兑换为 Copilot OAuth 会话令牌与动态 base_url
        res = auth.complete_auth_flow("github-copilot", {"mode": "api_key", "api_key": "ghp_pat_secret_1"}, root=tmp_path)
        assert res["ok"] is True
        cred = auth.resolve_provider_credential("github-copilot", root=tmp_path)
        assert cred is not None
        assert cred["api_key"].startswith("tid=abc;exp=")
        assert cred["base_url"] == "https://api.business.githubcopilot.com"
        assert ("/copilot_internal/v2/token", "Bearer ghp_pat_secret_1") in seen_auth_headers

        # 2. 旧格式已落盘 api_key 条目：resolve_provider_credential 自动升级换算
        store = auth._read_store_unlocked(tmp_path / "auth.json")
        store["providers"]["github-copilot"] = {"type": "api_key", "api_key": "ghp_legacy_2", "extra_config": {}}
        auth._write_store_unlocked(tmp_path / "auth.json", store)
        upgraded = auth.resolve_provider_credential("github-copilot", root=tmp_path)
        assert upgraded is not None and upgraded["api_key"].startswith("tid=abc;")

        # 3. COPILOT_GITHUB_TOKEN 环境变量通道：同样兑换成 Copilot 会话令牌
        auth.logout_provider("github-copilot", root=tmp_path)
        monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "gho_env_secret_3")
        env_cred = auth.resolve_provider_credential("github-copilot", root=tmp_path)
        assert env_cred is not None
        assert env_cred["api_key"].startswith("tid=abc;")
        assert env_cred["base_url"] == "https://api.business.githubcopilot.com"
        monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)

        # 4. M13：GHE enterprise_domain 解析 URL 模板并在 start_auth_flow 中落盘供 complete 复用
        monkeypatch.setitem(auth.PROVIDER_MAP, "github-copilot", orig_cp)
        assert auth._copilot_oauth_url("https://{domain}/login/device/code", "ghe.example.com") == "https://ghe.example.com/login/device/code"
        assert auth._copilot_oauth_url("https://api.{domain}/copilot_internal/v2/token", "https://ghe.example.com/") == "https://api.ghe.example.com/copilot_internal/v2/token"
        captured_urls: list[str] = []

        def fake_http_json(url: str, **kw: Any) -> dict[str, Any]:
            captured_urls.append(url)
            if "copilot_internal/v2/token" in url:
                return {"token": "tid=ghe_tok;exp=1999999999", "expires_at": int(time.time()) + 1800}
            return {"device_code": "dc-ghe", "user_code": "GHE-1", "verification_uri": "https://ghe.example.com/login/device"}

        def fake_http_req_json(url: str, **kw: Any) -> tuple[int, dict[str, Any], str]:
            captured_urls.append(url)
            return (200, {"access_token": "gho-ghe-1"}, "")

        monkeypatch.setattr(auth, "_http_json", fake_http_json)
        monkeypatch.setattr(auth, "_http_request_json", fake_http_req_json)
        s_ghe = auth.start_auth_flow("github-copilot", "oauth_device", extra_config={"enterprise_domain": "ghe.example.com"}, root=tmp_path)
        assert s_ghe["user_code"] == "GHE-1"
        auth.complete_auth_flow("github-copilot", {"mode": "oauth_device"}, root=tmp_path)
        assert any("ghe.example.com/login/device/code" in u for u in captured_urls)
        assert any("ghe.example.com/login/oauth/access_token" in u for u in captured_urls)
        assert any("api.ghe.example.com/copilot_internal/v2/token" in u for u in captured_urls)
        ghe_cred = auth.resolve_provider_credential("github-copilot", root=tmp_path)
        assert ghe_cred is not None and ghe_cred["base_url"] == "https://copilot-api.ghe.example.com"
    finally:
        srv.shutdown()


def test_openai_pkce_scope_required_and_refresh_revocation_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M10 + M11：OpenAI OAuth 缺 chatgpt.tokens.use.direct 报错；真实 HTTP 刷新 401 时 model_switch 抛出不可重试 LLMError。"""
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        raw = body.decode("utf-8")
        if req.path == "/oa/token":
            parsed = urllib.parse.parse_qs(raw)
            if parsed.get("grant_type", [""])[0] == "authorization_code":
                _send_json(
                    req,
                    {"access_token": "at1", "refresh_token": "rt1", "expires_in": 10, "scope": "openid profile"},
                )
                return
            _send_json(req, {"error": "invalid_grant"}, status=401)
            return
        _send_json(req, {}, 404)

    srv, base = _make_mock_server(handler)
    try:
        orig_oa = auth.PROVIDER_MAP["openai"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "openai",
            type(orig_oa)(**{**orig_oa.__dict__, "oauth": {**orig_oa.oauth, "token_url": f"{base}/oa/token"}}),
        )
        s = auth.start_auth_flow("openai", "oauth_pkce", root=tmp_path)
        cb = f"http://127.0.0.1:1455/auth/callback?code=c1&state={s['state']}&client_id=cid_1"
        with pytest.raises(auth.AuthError, match="chatgpt.tokens.use.direct"):
            auth.complete_auth_flow("openai", {"mode": "oauth_pkce", "code": cb}, root=tmp_path)

        # 存入已过期的 OAuth 凭据并通过真实 HTTP 刷新触发 401 -> 经 effective_config 变成 retryable=False 的 LLMError
        auth._save_provider_entry(
            tmp_path / "auth.json",
            auth.PROVIDER_MAP["openai-codex"],
            {
                "type": "oauth",
                "access_token": "expired-tok",
                "refresh_token": "revoked-rt",
                "expires_at_ms": 1_000,
            },
        )
        orig_cdx = auth.PROVIDER_MAP["openai-codex"]
        monkeypatch.setitem(
            auth.PROVIDER_MAP,
            "openai-codex",
            type(orig_cdx)(**{**orig_cdx.__dict__, "oauth": {**orig_cdx.oauth, "token_url": f"{base}/oa/token"}}),
        )
        base_cfg = llm.LLMConfig(base_url="http://127.0.0.1:1/v1", model="fake", api_key="k")
        with pytest.raises(llm.LLMError) as exc_info:
            model_switch.effective_config(base_cfg, model_switch.Override(model="openai-codex/gpt-5.4"), root=tmp_path)
        assert exc_info.value.retryable is False
        assert "/login openai-codex" in str(exc_info.value)
    finally:
        srv.shutdown()


def test_vertex_project_id_backfill_sa_jwt_mtime_cache_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2 + M3 + Minor #4：Vertex 凭据 JSON 自动回填 project_id、真实 RSA 私钥 JWT 兑换令牌、mtime 缓存失效与 ImportError 包装。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    token_calls = 0

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        nonlocal token_calls
        token_calls += 1
        _send_json(req, {"access_token": f"vx-tok-{token_calls}", "expires_in": 3600})

    srv, base = _make_mock_server(handler)
    try:
        sa_file = tmp_path / "sa.json"
        sa_file.write_text(
            json.dumps({
                "type": "service_account",
                "project_id": "proj-from-sa",
                "client_email": "ava@proj-from-sa.iam.gserviceaccount.com",
                "private_key": pem,
                "token_uri": f"{base}/token",
            }),
            encoding="utf-8",
        )
        res = auth.complete_auth_flow(
            "google-vertex",
            {"mode": "api_key", "extra_config": {"credentials_path": str(sa_file), "location": "us-central1"}},
            root=tmp_path,
        )
        assert res["ok"] is True
        cred = auth.resolve_provider_credential("google-vertex", root=tmp_path)
        assert cred is not None and cred["extra_config"]["project_id"] == "proj-from-sa"

        # 无 project_id 也无有效凭据时，空 api_key 被明确拒绝
        with pytest.raises(auth.AuthError, match="Project ID"):
            auth.complete_auth_flow(
                "google-vertex",
                {"mode": "api_key", "extra_config": {"credentials_path": str(tmp_path / "missing.json")}},
                root=tmp_path,
            )

        with llm._GOOGLE_TOKEN_LOCK:
            llm._GOOGLE_TOKEN_CACHE.clear()
        tok1 = llm._google_vertex_bearer_token({"credentials_path": str(sa_file)})
        tok2 = llm._google_vertex_bearer_token({"credentials_path": str(sa_file)})
        assert tok1 == "vx-tok-1" and tok2 == "vx-tok-1" and token_calls == 1

        # 文件内容或 mtime 变化 -> 缓存失效并重新签发
        sa_file.write_text(
            json.dumps({
                "type": "authorized_user",
                "quota_project_id": "proj-adc-2",
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "rt",
                "token_uri": f"{base}/token",
            }),
            encoding="utf-8",
        )
        os.utime(sa_file, (time.time() + 5, time.time() + 5))
        tok3 = llm._google_vertex_bearer_token({"credentials_path": str(sa_file)})
        assert tok3 == "vx-tok-2" and token_calls == 2

        # M2：缺少 cryptography 时抛出 LLMError 而非裸 ImportError
        sa_only = tmp_path / "sa_uncached.json"
        sa_only.write_text(
            json.dumps({
                "type": "service_account",
                "project_id": "p",
                "client_email": "e@p.iam.gserviceaccount.com",
                "private_key": pem,
                "token_uri": f"{base}/token",
            }),
            encoding="utf-8",
        )
        orig_import = builtins.__import__ if hasattr(builtins := __import__("builtins"), "__import__") else __import__
        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("cryptography"):
                raise ImportError("no cryptography")
            return orig_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(llm.LLMError, match="cryptography"):
            llm._google_vertex_bearer_token({"credentials_path": str(sa_only)})
    finally:
        srv.shutdown()


def test_codex_responses_encrypted_reasoning_done_and_incomplete(tmp_path: Path) -> None:
    """M5 + M8 + Minor #7：include encrypted_content、output_item.done 回填、response.incomplete 终结与 URL 主机名解析。"""
    # Minor #7
    assert llm.LLMConfig(base_url="https://chatgpt.com/backend-api", model="gpt-5.4", api_key="k", api_protocol="openai-codex-responses").endpoint == "https://chatgpt.com/backend-api/codex/responses"
    assert llm.LLMConfig(base_url="https://evil.example/?x=chatgpt.com/backend-api", model="gpt-5.4", api_key="k", api_protocol="openai-codex-responses").endpoint == "https://evil.example/?x=chatgpt.com/backend-api/responses"

    captured: list[dict[str, Any]] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        captured.append(json.loads(body.decode("utf-8")))
        if len(captured) == 1:
            _send_sse(
                req,
                [
                    'data: {"type":"response.output_item.done","item":{"type":"reasoning","id":"rs_1","summary":[{"type":"summary_text","text":"think-1"}],"encrypted_content":"enc_blob_xyz"}}\n\n',
                    'data: {"type":"response.output_item.done","item":{"type":"function_call","id":"fc_9","call_id":"call_9","name":"grep","arguments":"{\\"q\\":\\"abc\\"}"}}\n\n',
                    'data: {"type":"response.incomplete","response":{"usage":{"input_tokens":10,"output_tokens":5}}}\n\n',
                ],
            )
            return
        _send_json(req, {"output": [{"type": "message", "content": [{"type": "output_text", "text": "done-ok"}]}]})

    srv, base = _make_mock_server(handler)
    try:
        cfg = llm.LLMConfig(base_url=base, model="gpt-5.4", api_key="k", api_protocol="openai-codex-responses")
        t1 = llm.chat_complete([{"role": "user", "content": "q"}], config=cfg, root=tmp_path)
        assert t1["reasoning_content"] == "think-1"
        assert t1["tool_calls"][0]["id"] == "call_9"
        assert json.loads(t1["tool_calls"][0]["function"]["arguments"]) == {"q": "abc"}
        assert t1["thinking_blocks"][0]["item"]["encrypted_content"] == "enc_blob_xyz"
        assert captured[0]["include"] == ["reasoning.encrypted_content"]

        t2 = llm.chat_complete(
            [{"role": "user", "content": "q"}, t1, {"role": "tool", "tool_call_id": "call_9", "content": "hit"}],
            config=cfg,
            root=tmp_path,
        )
        assert t2["content"] == "done-ok"
        assert captured[1]["input"][1] == {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "think-1"}],
            "encrypted_content": "enc_blob_xyz",
        }
    finally:
        srv.shutdown()


def test_bedrock_tool_choice_none_reasoning_blocks_and_sigv4_kat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M6 + M7 + Minor #5 + KAT：Bedrock tool_choice='none' 不发 toolConfig、redactedContent/签名规则、reasoning_effort 映射、禁用 SSE 与 SigV4 确定性签名。"""
    import datetime as dt_mod
    import hashlib
    import hmac

    sample_tool = [{"type": "function", "function": {"name": "read_file", "description": "r", "parameters": {"type": "object"}}}]
    b_claude = llm.LLMConfig(base_url="https://bedrock.example", model="anthropic.claude-opus-4-6-v1:0", api_key="bk", api_protocol="bedrock-converse-stream", reasoning_effort="high")
    p_none, _ = llm._bedrock_request(b_claude, [{"role": "user", "content": "hi"}], sample_tool, None, "none")
    assert "toolConfig" not in p_none
    assert p_none["additionalModelRequestFields"]["thinking"]["type"] == "adaptive"
    assert p_none["additionalModelRequestFields"]["output_config"]["effort"] == "high"

    b_gpt = llm.LLMConfig(base_url="https://bedrock.example", model="openai.gpt-oss-120b-1:0", api_key="bk", api_protocol="bedrock-converse-stream", reasoning_effort="max")
    p_gpt, _ = llm._bedrock_request(b_gpt, [{"role": "user", "content": "hi"}], None, None, None)
    assert p_gpt["additionalModelRequestFields"] == {"reasoning_effort": "high"}

    # M7: Claude 保留 signature 与 redactedContent，非 Claude 省略 signature
    history = [
        {
            "role": "assistant",
            "content": "ok",
            "thinking_blocks": [
                {"type": "thinking", "thinking": "deep", "signature": "sig-c1"},
                {"type": "thinking", "thinking": "unsigned"},
                {"type": "redacted_thinking", "data": "cmVkYWN0ZWQ="},
            ],
        }
    ]
    p_replay_c, _ = llm._bedrock_request(b_claude, history, None, None, None)
    c_blocks = p_replay_c["messages"][0]["content"]
    assert {"reasoningContent": {"reasoningText": {"text": "deep", "signature": "sig-c1"}}} in c_blocks
    assert {"reasoningContent": {"redactedContent": "cmVkYWN0ZWQ="}} in c_blocks
    assert {"text": "unsigned"} in c_blocks
    p_replay_g, _ = llm._bedrock_request(b_gpt, history, None, None, None)
    g_blocks = p_replay_g["messages"][0]["content"]
    assert {"reasoningContent": {"reasoningText": {"text": "deep"}}} in g_blocks

    # Minor #5：Bedrock text/event-stream 直接报明确协议错而非误用 OpenAIChoice 解析
    def sse_handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        _send_sse(req, ['data: {"output":{"message":{"content":[{"text":"x"}]}}}\n\n'])

    srv, base = _make_mock_server(sse_handler)
    try:
        with pytest.raises(llm.LLMError, match="不支持 text/event-stream"):
            llm.chat_complete(
                [{"role": "user", "content": "hi"}],
                config=llm.LLMConfig(base_url=base, model="anthropic.claude-opus-5", api_key="bk", api_protocol="bedrock-converse-stream"),
                root=tmp_path,
            )
    finally:
        srv.shutdown()

    # AWS SigV4 确定性 Known-Answer Test（冻结 UTC 时间 2015-08-30T12:36:00Z）
    fixed_dt = dt_mod.datetime(2015, 8, 30, 12, 36, 0, tzinfo=dt_mod.timezone.utc)
    class FixedDateTime(dt_mod.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> dt_mod.datetime:
            return fixed_dt if tz is not None else fixed_dt.replace(tzinfo=None)

    monkeypatch.setattr(llm.datetime, "datetime", FixedDateTime)
    signed = llm._sign_aws_v4(
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-opus-4-6-v1%3A0/converse",
        b'{"messages":[]}',
        {"Content-Type": "application/json"},
        {"aws_region": "us-east-1", "aws_access_key_id": "AKIDEXAMPLE", "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "aws_session_token": "tok-1"},
    )
    assert signed["X-Amz-Date"] == "20150830T123600Z"
    assert signed["X-Amz-Security-Token"] == "tok-1"
    body_hash = hashlib.sha256(b'{"messages":[]}').hexdigest()
    canonical = (
        f"POST\n/model/anthropic.claude-opus-4-6-v1%253A0/converse\n\n"
        f"content-type:application/json\nhost:bedrock-runtime.us-east-1.amazonaws.com\n"
        f"x-amz-content-sha256:{body_hash}\nx-amz-date:20150830T123600Z\nx-amz-security-token:tok-1\n\n"
        f"content-type;host;x-amz-content-sha256;x-amz-date;x-amz-security-token\n{body_hash}"
    )
    sts = f"AWS4-HMAC-SHA256\n20150830T123600Z\n20150830/us-east-1/bedrock/aws4_request\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    def _h(k: bytes, m: str) -> bytes:
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    k_sign = _h(_h(_h(_h(b"AWS4wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20150830"), "us-east-1"), "bedrock"), "aws4_request")
    expected_sig = hmac.new(k_sign, sts.encode(), hashlib.sha256).hexdigest()
    assert f"Signature={expected_sig}" in signed["Authorization"]


def test_provider_prefix_proxy_not_hijacked_and_auth_headers_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M9 + Minor #6：已登录 anthropic 不劫持代理原生 vendor/model 名；extra_headers 不可覆写鉴权头。"""
    monkeypatch.setenv("AVA_AUTH_PATH", str(tmp_path / "auth.json"))
    auth.complete_auth_flow("anthropic", {"mode": "api_key", "api_key": "sk-ant-real"}, root=tmp_path)
    # 代理透传的未知模型名 anthropic/claude-custom-proxy-9 不在 anthropic 注册目录 -> 返回 None 交由激活代理处理
    assert auth.resolve_model_provider("anthropic/claude-custom-proxy-9", root=tmp_path) is None
    # 官方已知模型名正常路由到 anthropic
    resolved = auth.resolve_model_provider("anthropic/claude-opus-4-6", root=tmp_path)
    assert resolved is not None and resolved[0] == "anthropic" and resolved[1] == "claude-opus-4-6"

    _, ant_hdrs = llm._anthropic_request(
        llm.LLMConfig(
            base_url="https://api.anthropic.com",
            model="claude-opus-4-6",
            api_key="real-ant-key",
            api_protocol="anthropic-messages",
            extra_headers={"x-api-key": "evil", "Authorization": "Bearer evil", "X-Custom": "ok"},
        ),
        [{"role": "user", "content": "hi"}],
        None,
        None,
        None,
    )
    assert ant_hdrs["x-api-key"] == "real-ant-key"
    assert "Authorization" not in ant_hdrs
    assert ant_hdrs["X-Custom"] == "ok"


def test_cli_stdin_secret_rejection_n50_aws_and_masked_extra_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M1 + M12 + Minor #1/#2/#13：AWS N50 脱敏、CLI inline argv 含密拒收、extra_config 非纯展示字段脱敏。"""
    custom = tmp_path / "isolated-auth.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-super-secret-n50-abc")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "br-token-n50-xyz")
    assert "AWS_SECRET_ACCESS_KEY" in tools.secret_env_names()
    assert "AWS_BEARER_TOKEN_BEDROCK" in tools.secret_env_names()
    scrubbed = tools.scrub_secrets("leak aws-super-secret-n50-abc and br-token-n50-xyz", root=tmp_path)
    assert "aws-super-secret-n50-abc" not in scrubbed and "br-token-n50-xyz" not in scrubbed
    assert tools._SECRET_FIELD_NAME.search("code_verifier")
    assert tools._SECRET_FIELD_NAME.search("device_code")
    assert tools._SECRET_FIELD_NAME.search("user_code")

    # M12：--auth-complete inline argv JSON 带敏感字段必须拒绝
    rc_bad = auth.dispatch_auth_cli(
        ["--auth-complete", "openai", '{"mode":"api_key","api_key":"sk-leak"}'],
        root=tmp_path,
    )
    assert rc_bad == 1
    assert "stdin" in json.loads(capsys.readouterr().out)["error"].lower()

    # Minor #2：project_id / enterprise_domain / aws_access_key_id / credentials_path 在 status 中脱敏
    auth.complete_auth_flow(
        "google-vertex",
        {"mode": "api_key", "api_key": "vx-k", "extra_config": {"project_id": "secret-gcp-project-99", "location": "us-east5"}},
        root=tmp_path,
    )
    status = auth.get_auth_status(tmp_path)
    assert "secret-gcp-project-99" not in json.dumps(status)
    vx_row = next(p for p in status["providers"] if p["id"] == "google-vertex")
    assert vx_row["extra_config"]["location"] == "us-east5"
    assert "project_id" not in vx_row["extra_config"] or "…" in vx_row["extra_config"]["project_id"]

    # Minor #13：/login 帮助提示包含 --auth-complete <provider> --stdin
    _, help_txt, _ = auth.run_auth_command("/login", root=tmp_path)
    assert "--auth-complete <provider> --stdin" in help_txt


def test_mistral_bijection_collision_concurrent_flock_and_alias_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minor #3 + 并发文件锁 + alias 清理测试缺口：Mistral 双射避碰、多线程 complete_auth_flow 并发无损坏、写盘清 legacy alias。"""
    import concurrent.futures

    # 1. Mistral：即使两个不同 raw_id 的首轮哈希碰撞，依然保持不同输出；相同 raw_id 复用同一 ID
    cache: dict[str, str] = {"raw_a": "abcdef123"}
    cache_id_dup = llm._mistral_tool_call_id("raw_a", cache)
    assert cache_id_dup == "abcdef123"
    # 找一个非 raw_a 但假装占位冲突的 raw_b
    raw_b = "raw_b"
    first_b = llm._mistral_tool_call_id(raw_b, {"other": llm._mistral_tool_call_id(raw_b, {})})
    assert first_b != llm._mistral_tool_call_id(raw_b, {})

    # 2. legacy alias 写盘清理
    custom = tmp_path / "auth.local.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom))
    auth._write_store_unlocked(custom, {"providers": {"google-gemini": {"type": "api_key", "api_key": "old-gem"}}})
    auth.complete_auth_flow("google", {"mode": "api_key", "api_key": "new-gem"}, root=tmp_path)
    stored = auth._read_store_unlocked(custom)["providers"]
    assert "google" in stored and "google-gemini" not in stored

    # 3. 并发线程同时写入不同服务商，flock 保证全量落盘且文件可合法解析
    targets = ["openai", "deepseek", "groq", "mistral", "xai", "openrouter"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futs = [
            pool.submit(auth.complete_auth_flow, pid, {"mode": "api_key", "api_key": f"key-{pid}"}, root=tmp_path)
            for pid in targets
        ]
        for fut in futs:
            assert fut.result()["ok"] is True
    final_store = auth._read_store_unlocked(custom)["providers"]
    for pid in targets:
        assert final_store[pid]["api_key"] == f"key-{pid}"


def test_residual_minor_items_hardening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """二次复核 8 项 Minor 残留加固测试：Bedrock block_binding/正则边界、Azure 跳过 include、exchanging 宽限、Vertex 禁重定向、REPL extra_config、Codex done 覆写截断 delta、Gemini 同模型签名门控。"""
    # 1 + 3. Bedrock opus-4-7 adaptive + block_binding + beta（GovCloud 跳过）；opus-4-60 不误判为 adaptive
    p47, _ = llm._bedrock_request(
        llm.LLMConfig(base_url="https://bedrock.example", model="anthropic.claude-opus-4-7-v1:0", api_key="bk", api_protocol="bedrock-converse-stream", reasoning_effort="xhigh"),
        [{"role": "user", "content": "hi"}],
        None,
        None,
        None,
    )
    assert p47["additionalModelRequestFields"]["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert p47["additionalModelRequestFields"]["output_config"] == {"effort": "xhigh"}
    assert p47["additionalModelRequestFields"]["anthropic_beta"] == ["thinking-binding-controls-2026-08-01"]

    p47_gov, _ = llm._bedrock_request(
        llm.LLMConfig(base_url="https://bedrock.example", model="us-gov.anthropic.claude-opus-4-7-v1:0", api_key="bk", api_protocol="bedrock-converse-stream", reasoning_effort="high", extra_config={"aws_region": "us-gov-west-1"}),
        [{"role": "user", "content": "hi"}],
        None,
        None,
        None,
    )
    assert "block_binding" not in p47_gov["additionalModelRequestFields"]["thinking"]
    assert "anthropic_beta" not in p47_gov["additionalModelRequestFields"]

    p460, _ = llm._bedrock_request(
        llm.LLMConfig(base_url="https://bedrock.example", model="anthropic.claude-opus-4-60-v1:0", api_key="bk", api_protocol="bedrock-converse-stream", reasoning_effort="medium"),
        [{"role": "user", "content": "hi"}],
        None,
        None,
        None,
    )
    assert p460["additionalModelRequestFields"]["thinking"]["type"] == "enabled"

    # 2. Azure Responses 不发 include
    p_az, _ = llm._codex_request(
        llm.LLMConfig(base_url="https://my.openai.azure.com/openai/v1", model="gpt-5", api_key="ak", api_protocol="azure-openai-responses"),
        [{"role": "user", "content": "hi"}],
        None,
        None,
        None,
    )
    assert "include" not in p_az

    # 7. Codex output_item.done 完整 arguments 优先于被截断的 delta parts
    msg_done, _ = llm._assemble_codex_stream(
        iter([
            'data: {"type":"response.output_item.added","item":{"type":"function_call","id":"fc_t","call_id":"call_t","name":"search"}}\n',
            'data: {"type":"response.function_call_arguments.delta","item_id":"fc_t","delta":"{\\"q\\":"}\n',
            'data: {"type":"response.output_item.done","item":{"type":"function_call","id":"fc_t","call_id":"call_t","name":"search","arguments":"{\\"q\\":\\"full\\"}"}}\n',
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":5,"output_tokens":2}}}\n',
        ])
    )
    assert json.loads(msg_done["tool_calls"][0]["function"]["arguments"]) == {"q": "full"}

    # 4 + 6. exchanging 超过 120s 宽限允许重试，以及 /login REPL enterprise_domain= 透传
    custom = tmp_path / "auth.local.json"
    monkeypatch.setenv("AVA_AUTH_PATH", str(custom))
    auth.start_auth_flow("anthropic", "oauth_pkce", root=tmp_path)
    store = auth._read_store_unlocked(custom)
    store["_pending"]["anthropic"]["exchanging"] = True
    store["_pending"]["anthropic"]["exchanging_at_ms"] = auth._now_ms() - 10_000
    auth._write_store_unlocked(custom, store)
    with pytest.raises(auth.AuthError, match="进行中"):
        auth.complete_auth_flow("anthropic", {"mode": "oauth_pkce", "code": "c#s"}, root=tmp_path)
    # 设为过期宽限 > 120s 后，不再被 exchanging 死锁卡住（转为正常 state 校验错误）
    store["_pending"]["anthropic"]["exchanging_at_ms"] = auth._now_ms() - 180_000
    auth._write_store_unlocked(custom, store)
    with pytest.raises(auth.AuthError, match="state"):
        auth.complete_auth_flow("anthropic", {"mode": "oauth_pkce", "code": f"c1#wrong"}, root=tmp_path)

    seen_cli_urls: list[str] = []
    monkeypatch.setattr(
        auth,
        "_http_json",
        lambda url, **kw: (seen_cli_urls.append(url) or {"device_code": "dc-cli", "user_code": "CLI-GHE", "verification_uri": "https://ghe.internal/device"}),
    )
    ok_cmd, txt_cmd, _ = auth.run_auth_command("/login github-copilot enterprise_domain=ghe.internal", root=tmp_path)
    assert ok_cmd is True and "CLI-GHE" in txt_cmd
    assert any("ghe.internal/login/device/code" in u for u in seen_cli_urls)

    # 5 + 8. Vertex SA token_uri 重定向被拒 & Gemini thoughtSignature 同模型保留、跨模型跳过
    def srv_handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        if req.path == "/redir":
            req.send_response(302)
            req.send_header("Location", "http://127.0.0.1:9/x")
            req.end_headers()
            return
        _send_json(req, {"candidates": [{"content": {"parts": [{"functionCall": {"id": "fc1", "name": "f", "args": {}}, "thoughtSignature": "sig-same"}]}, "finishReason": "STOP"}]})

    srv, base = _make_mock_server(srv_handler)
    try:
        bad_adc = tmp_path / "adc_redir.json"
        bad_adc.write_text(json.dumps({"type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "r", "token_uri": f"{base}/redir"}), encoding="utf-8")
        with llm._GOOGLE_TOKEN_LOCK:
            llm._GOOGLE_TOKEN_CACHE.clear()
        with pytest.raises(llm.LLMError, match="重定向"):
            llm._google_vertex_bearer_token({"credentials_path": str(bad_adc)})

        g3 = llm.LLMConfig(base_url=f"{base}/v1beta", model="gemini-3-pro", api_key="k", api_protocol="google-generative-ai")
        g25 = llm.LLMConfig(base_url=f"{base}/v1beta", model="gemini-2.5-flash", api_key="k", api_protocol="google-generative-ai")
        turn_g3 = llm.chat_complete([{"role": "user", "content": "go"}], config=g3, root=tmp_path)
        assert turn_g3["tool_calls"][0]["_ava_sig_model"] == "gemini-3-pro"
        same_req, _ = llm._google_request(g3, [{"role": "user", "content": "go"}, turn_g3], None, None, None)
        cross_req, _ = llm._google_request(g25, [{"role": "user", "content": "go"}, turn_g3], None, None, None)
        assert same_req["contents"][1]["parts"][0]["thoughtSignature"] == "sig-same"
        assert "thoughtSignature" not in cross_req["contents"][1]["parts"][0]
    finally:
        srv.shutdown()


def test_google_api_key_syncs_available_models(tmp_path: Path) -> None:
    """绑定 Google Gemini API Key 时应从 `/models` 同步支持 generateContent 的全量模型，失败时回退到 default_models。"""
    seen_headers: list[str | None] = []

    def handler(req: BaseHTTPRequestHandler, method: str, body: bytes) -> None:
        seen_headers.append(req.headers.get("x-goog-api-key"))
        if req.path.startswith("/v1beta/models"):
            if "pageToken=p2" in req.path:
                _send_json(req, {
                    "models": [
                        {"name": "models/gemini-4-hydrogen", "supportedGenerationMethods": ["generateContent", "countTokens"]},
                        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                    ]
                })
                return
            _send_json(req, {
                "models": [
                    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
                ],
                "nextPageToken": "p2",
            })
            return
        _send_json(req, {"error": {"message": "not found"}}, status=404)

    srv, base = _make_mock_server(handler)
    try:
        res = auth.complete_auth_flow(
            "google",
            {"mode": "api_key", "api_key": "AIza-test-sync", "base_url": f"{base}/v1beta"},
            root=tmp_path,
        )
        assert res["ok"] is True
        assert seen_headers == ["AIza-test-sync", "AIza-test-sync"]
        assert res["models"] == ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-4-hydrogen"]
        listed = auth.list_authenticated_models(root=tmp_path)
        assert listed == [
            "google/gemini-3.5-flash",
            "google/gemini-3.8-flash",
            "google/gemini-4-hydrogen",
        ]

        # 探测失败时回退到 default_models
        res_fallback = auth.complete_auth_flow(
            "google",
            {"mode": "api_key", "api_key": "AIza-fail", "base_url": f"{base}/bad"},
            root=tmp_path,
        )
        assert res_fallback["models"] == list(auth.PROVIDER_MAP["google"].default_models)
    finally:
        srv.shutdown()


def test_google_request_normalizes_all_tool_schemas() -> None:
    """Gemini `functionDeclarations` 必须把联合 type、oneOf 与非字符串 enum 规整为 Protobuf Schema 子集。"""
    from pipeline.agent import tools

    allowed_schema_keys = {
        "type", "format", "title", "description", "nullable", "enum",
        "items", "maxItems", "minItems", "properties", "required",
        "minProperties", "maxProperties", "minimum", "maximum",
        "minLength", "maxLength", "pattern", "example", "anyOf", "propertyOrdering",
    }

    def _assert_gemini_schema(node: object, path: str) -> None:
        assert isinstance(node, dict), f"{path} 必须是 dict"
        for k, v in node.items():
            assert k in allowed_schema_keys, f"{path} 包含 Gemini 不支持的 schema 字段 {k!r}"
            if k == "type":
                assert isinstance(v, str) and v != "null", f"{path}.type 非法: {v!r}"
            elif k == "enum":
                assert node.get("type") == "string", f"{path}.enum 仅允许用于 string，实际 type={node.get('type')!r}"
                assert isinstance(v, list) and all(isinstance(x, str) for x in v), f"{path}.enum 必须全为字符串: {v!r}"
            elif k == "properties":
                assert isinstance(v, dict)
                for pk, pv in v.items():
                    _assert_gemini_schema(pv, f"{path}.properties.{pk}")
            elif k == "items":
                _assert_gemini_schema(v, f"{path}.items")
            elif k == "anyOf":
                assert isinstance(v, list)
                for idx, branch in enumerate(v):
                    _assert_gemini_schema(branch, f"{path}.anyOf[{idx}]")

    cfg = llm.LLMConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-flash",
        api_key="AIza-test",
        api_protocol="google-generative-ai",
    )
    payload, _ = llm._google_request(cfg, [{"role": "user", "content": "hi"}], tools.build_tool_schemas(), None, None)
    decls = {d["name"]: d["parameters"] for d in payload["tools"][0]["functionDeclarations"]}
    for name, params in decls.items():
        _assert_gemini_schema(params, name)

    expected_dur = decls["acquire_propose"]["properties"]["candidates"]["items"]["properties"]["expected_dur"]
    assert expected_dur["type"] == "number" and expected_dur["nullable"] is True

    scale = decls["browser"]["properties"]["scale"]
    assert scale["type"] == "integer" and "enum" not in scale and "2, 3, 4" in scale["description"]

    chrome_text = decls["chrome"]["properties"]["text"]
    assert "oneOf" not in chrome_text and chrome_text["anyOf"] == [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ]

    # properties 下的同名参数（如 default / type）不能被当成 schema 关键字删掉，anyOf+[null] 应折叠
    cleaned = llm._clean_gemini_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {"type": ["string", "null"], "enum": ["a", "b"]},
            "default": {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": "默认值"},
            "mixed": {"type": ["string", "integer"]},
            "multi_nullable": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
            "only_null": {"anyOf": [{"type": "null"}]},
            "bare_str_enum": {"enum": ["x", "y"]},
            "bare_int_enum": {"enum": [1, 2]},
        },
    })
    assert set(cleaned["properties"]) == {
        "type", "default", "mixed", "multi_nullable", "only_null", "bare_str_enum", "bare_int_enum",
    }
    assert cleaned["properties"]["type"] == {"type": "string", "nullable": True, "enum": ["a", "b"]}
    assert cleaned["properties"]["default"] == {"type": "integer", "nullable": True, "description": "默认值"}
    assert cleaned["properties"]["mixed"] == {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert cleaned["properties"]["multi_nullable"] == {
        "anyOf": [{"type": "string"}, {"type": "integer"}],
        "nullable": True,
    }
    assert cleaned["properties"]["only_null"] == {"type": "string", "nullable": True}
    assert cleaned["properties"]["bare_str_enum"] == {"type": "string", "enum": ["x", "y"]}
    _assert_gemini_schema(cleaned["properties"]["bare_str_enum"], "bare_str_enum")
    assert cleaned["properties"]["bare_int_enum"] == {"description": "可选值：1, 2"}


def test_google_stream_rejects_malformed_blocked_and_empty_responses() -> None:
    """Gemini 返回 MALFORMED_RESPONSE / 安全拦截 / 空回复 / 中途断流时不能静默当成正常完成，应按可重试或硬拒抛 LLMError。"""
    cfg = llm.LLMConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-4-hydrogen",
        api_key="AIza-test",
        api_protocol="google-generative-ai",
        reasoning_effort="medium",
    )
    req_payload, _ = llm._google_request(cfg, [{"role": "user", "content": "hi"}], None, None, None)
    assert req_payload["generationConfig"] == {"thinkingConfig": {"thinkingBudget": 8192}}

    with pytest.raises(llm.LLMError, match="MALFORMED_RESPONSE") as exc_mal:
        llm._assemble_google_stream(iter([
            'data: {"candidates":[{"content":{"parts":[{"text":"","thoughtSignature":"sig"}]},"finishReason":"MALFORMED_RESPONSE"}]}\n',
        ]))
    assert exc_mal.value.retryable is True

    # 文本 + functionCall 同时存在时，MALFORMED_FUNCTION_CALL 仍必须报错，不能吞掉残缺工具批
    with pytest.raises(llm.LLMError, match="MALFORMED_FUNCTION_CALL") as exc_mixed_mal:
        llm._google_json_to_message({
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "先查一下"},
                        {"functionCall": {"name": "read_file", "args": {"path": "a.md"}}},
                    ],
                },
                "finishReason": "MALFORMED_FUNCTION_CALL",
            }],
        })
    assert exc_mixed_mal.value.retryable is True

    # SSE 半截断流（无 finishReason）必须可重试报错
    with pytest.raises(llm.LLMError, match="缺少 finishReason") as exc_trunc:
        llm._assemble_google_stream(iter([
            'data: {"candidates":[{"content":{"parts":[{"text":"说到一半"}]}}]}\n',
        ]))
    assert exc_trunc.value.retryable is True

    # finishReason="OTHER" 且已有完整正文时正常放行，不白白重跑
    msg_other, _ = llm._google_json_to_message({
        "candidates": [{"content": {"parts": [{"text": "完整答复"}]}, "finishReason": "OTHER"}],
    })
    assert msg_other["content"] == "完整答复"

    with pytest.raises(llm.LLMError, match="MALFORMED_FUNCTION_CALL") as exc_mfc:
        llm._google_json_to_message({
            "candidates": [{"content": {"parts": [{"text": "先查一下"}]}, "finishReason": "MALFORMED_FUNCTION_CALL"}],
        })
    assert exc_mfc.value.retryable is True

    with pytest.raises(llm.LLMError, match="SAFETY") as exc_safe:
        llm._assemble_google_stream(iter([
            'data: {"candidates":[{"finishReason":"SAFETY"}]}\n',
        ]))
    assert exc_safe.value.retryable is False

    with pytest.raises(llm.LLMError, match="PROHIBITED_CONTENT") as exc_blk:
        llm._google_json_to_message({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}})
    assert exc_blk.value.retryable is False

    with pytest.raises(llm.LLMError, match="空响应") as exc_empty:
        llm._assemble_google_stream(iter([
            'data: {"candidates":[{"content":{"parts":[{"text":""}]},"finishReason":"STOP"}]}\n',
        ]))
    assert exc_empty.value.retryable is True
