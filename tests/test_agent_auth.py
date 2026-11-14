"""D170：AVA 独立多服务商 `/login` 认证、OAuth 续期、多协议转换与 N50 密钥隔离测试。"""
from __future__ import annotations

import base64
import json
import os
import stat
import threading
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


def test_ten_providers_and_no_pi_dependency() -> None:
    ids = [p.id for p in auth.PROVIDERS]
    assert len(ids) == 10
    assert set(ids) == {
        "cliproxyapi",
        "anthropic",
        "openai-codex",
        "github-copilot",
        "openai",
        "deepseek",
        "kimi-coding",
        "zai",
        "google-gemini",
        "openrouter",
    }
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

    # NB #3：OAuth 续期失败时 _load_auth_fallback_cfg 优雅回落 None，effective_config 转为可重试 LLMError
    monkeypatch.setattr(auth, "resolve_model_provider", lambda *a, **kw: (_ for _ in ()).throw(auth.AuthError("refresh boom")))
    base = llm.LLMConfig(base_url="http://127.0.0.1:1/v1", model="fake", api_key="k")
    with pytest.raises(llm.LLMError) as exc_info:
        model_switch.effective_config(base, model_switch.Override(model="anthropic/claude-opus-4-6"), root=tmp_path)
    assert exc_info.value.retryable is True

    monkeypatch.setattr(auth, "first_authenticated_config", lambda *a, **kw: (_ for _ in ()).throw(auth.AuthError("refresh boom")))
    assert llm._load_auth_fallback_cfg(tmp_path) is None
