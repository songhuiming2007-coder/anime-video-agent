"""D93：模型请求超时放宽可配 + 可重试错误自动重试。

背景：主回合超时原先写死 60 s，健康调用随上下文增长已到 40–56 s，2026-10-14 supercell 企划会话
两回合连续被误杀。改为默认 300 s（配置 `request_timeout_s` 可覆盖，0 = 不设），超时 / 连不上 /
408/409/429/5xx 在工具循环里自动重试 3 次（2 / 4 / 8 s）。

`chat_complete` 的分类与超时取值在 `urlopen` 桩上测；重试在 `run_tool_loop` 上用脚本化
`chat_complete` 测；最后一条用真 socket（本地假端点挂住超过 1 s）走一遍真实超时路径。
"""

from __future__ import annotations

import http.server
import io
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pipeline.agent import llm as llm_mod
from pipeline.agent.llm import LLMError, run_tool_loop
from pipeline.agent.tools import ToolContext
from tests.test_agent_loop import ROOT_URL, Interrupt, Script, build_control, patch_execute
from tests.test_agent_tools import make_agent_root, tool_call

CFG = llm_mod.LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="sk-test")
FINAL = {"role": "assistant", "content": "好"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_agent_root(tmp_path / "repo", ROOT_URL)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """换掉退避等待：只记录、不真睡。"""
    recorded: list[float] = []
    monkeypatch.setattr(llm_mod, "_sleep", recorded.append)
    return recorded


def _retryable(text: str = "LLM 请求失败: timed out") -> LLMError:
    return LLMError(text, retryable=True)


# ---------------------------------------------------------------------------
# chat_complete：错误分类
# ---------------------------------------------------------------------------


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ROOT_URL, code, "x", {}, io.BytesIO(b'{"error": "x"}'))


@pytest.mark.parametrize(
    ("raised", "retryable"),
    [
        (TimeoutError("timed out"), True),
        (urllib.error.URLError("connection refused"), True),
        (ConnectionResetError("reset by peer"), True),
        (_http_error(503), True),
        (_http_error(500), True),
        (_http_error(429), True),
        (_http_error(408), True),
        (_http_error(400), False),  # D85：模型名失效，重试只会白等
        (_http_error(401), False),
    ],
)
def test_chat_complete_marks_retryable(monkeypatch, raised, retryable) -> None:
    def fake_urlopen(request, timeout=None):
        raise raised

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(LLMError) as info:
        llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=CFG)
    assert info.value.retryable is retryable


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def test_non_json_reply_is_not_retryable(monkeypatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _Resp(b"<html>"))
    with pytest.raises(LLMError) as info:
        llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=CFG)
    assert info.value.retryable is False


def test_plain_llm_error_defaults_to_not_retryable() -> None:
    assert LLMError("x").retryable is False


# ---------------------------------------------------------------------------
# 超时取值：配置 → urlopen
# ---------------------------------------------------------------------------


def _write_local(root: Path, **extra) -> None:
    payload = {"base_url": ROOT_URL, "model": "m", "api_key_env": "AVA_TEST_KEY", **extra}
    (root / "config" / "agent.local.json").write_text(json.dumps(payload), encoding="utf-8")


def _timeout_sent(monkeypatch, cfg, **kwargs):
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append(timeout)
        return _Resp(json.dumps({"choices": [{"message": FINAL}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=cfg, **kwargs)
    return seen[0]


@pytest.mark.parametrize(
    ("extra", "expected", "warns"),
    [
        ({}, 300, False),
        ({"request_timeout_s": 120}, 120, False),
        ({"request_timeout_s": 7.5}, 7.5, False),
        ({"request_timeout_s": 0}, None, False),  # 0 = 不设超时
        ({"request_timeout_s": "abc"}, 300, True),
        ({"request_timeout_s": -1}, 300, True),
        ({"request_timeout_s": True}, 300, True),
    ],
)
def test_request_timeout_from_config(tmp_path, monkeypatch, capsys, extra, expected, warns) -> None:
    llm_mod._reset_warn_latch_for_testing()
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    _write_local(root, **extra)
    cfg = llm_mod.load_llm_config(root)
    assert cfg is not None
    assert _timeout_sent(monkeypatch, cfg) == expected
    assert ("request_timeout_s" in capsys.readouterr().err) is warns


def test_explicit_timeout_beats_config(monkeypatch) -> None:
    """标题 / 压缩 / 对抗审查显式传自己的超时，不随配置变。"""
    cfg = llm_mod.LLMConfig(base_url=ROOT_URL, model="m", api_key="k", request_timeout_s=999)
    assert _timeout_sent(monkeypatch, cfg, timeout=30) == 30
    assert _timeout_sent(monkeypatch, cfg) == 999


# ---------------------------------------------------------------------------
# run_tool_loop：自动重试
# ---------------------------------------------------------------------------


def _run(messages, root, control):
    return run_tool_loop(messages, ctx=ToolContext(scope="creative", root=root), config=CFG, control=control)


def _with_retry_log(control):
    calls: list[tuple] = []
    control.on_llm_retry = lambda *args: calls.append(args)
    return calls


def test_retryable_errors_are_retried_then_succeed(root, monkeypatch, sleeps) -> None:
    script = Script([_retryable(), _retryable("LLM HTTP 503: busy"), FINAL])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    messages = [{"role": "user", "content": "继续"}]
    control = build_control(messages)
    notified = _with_retry_log(control)

    outcome = _run(messages, root, control)

    assert outcome["stopped"] == "done"
    assert outcome["final"]["content"] == "好"
    assert outcome["llm_retries"] == 2
    assert outcome["llm_calls"] == 1  # 失败的尝试不算模型调用
    assert len(script.requests) == 3
    assert sleeps == [2.0, 4.0]
    assert [c[:3] for c in notified] == [(1, 3, 2.0), (2, 3, 4.0)]
    assert notified[1][3] == "LLM HTTP 503: busy"


def test_retries_exhausted_stop_with_error(root, monkeypatch, sleeps) -> None:
    script = Script([_retryable()] * 5)
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    messages = [{"role": "user", "content": "继续"}]
    control = build_control(messages)
    notified = _with_retry_log(control)

    outcome = _run(messages, root, control)

    assert outcome["stopped"] == "error"
    assert outcome["rollback"] is True  # 一条回复都没拿到：照旧回滚
    assert "已自动重试 3 次" in outcome["error"]
    assert len(script.requests) == 4  # 1 次原请求 + 3 次重试
    assert sleeps == [2.0, 4.0, 8.0]
    assert len(notified) == 3
    assert outcome["llm_retries"] == 3


def test_non_retryable_error_stops_at_once(root, monkeypatch, sleeps) -> None:
    script = Script([LLMError("LLM HTTP 400: model not found")])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    messages = [{"role": "user", "content": "继续"}]

    outcome = _run(messages, root, build_control(messages))

    assert outcome["stopped"] == "error"
    assert len(script.requests) == 1
    assert sleeps == []
    assert outcome["llm_retries"] == 0


def test_interrupt_during_backoff_stops_turn(root, monkeypatch) -> None:
    """等待中点停止：SIGINT 打断 sleep → 走主循环现有的中断落点。"""
    def interrupted_sleep(delay):
        raise KeyboardInterrupt

    monkeypatch.setattr(llm_mod, "_sleep", interrupted_sleep)
    script = Script([tool_call("read_artifact", {"path": "a.md"}, call_id="a1"), _retryable(), FINAL])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    messages = [{"role": "user", "content": "继续"}]

    outcome = _run(messages, root, build_control(messages, interrupt=Interrupt()))

    assert outcome["stopped"] == "interrupted"
    assert outcome["wrapup"] == "ok"  # 收尾照常（第 3 条脚本即收尾回复）
    assert len(script.requests) == 3


def test_wrapup_call_is_retried_too(root, monkeypatch, sleeps) -> None:
    script = Script([
        tool_call("read_artifact", {"path": "a.md"}, call_id="a1"),
        LLMError("LLM HTTP 400: bad"),  # 不可重试 → 回合出错、进收尾
        _retryable(),  # 收尾请求第一次超时
        {"role": "assistant", "content": "收尾总结"},
    ])
    monkeypatch.setattr(llm_mod, "chat_complete", script)
    patch_execute(monkeypatch)
    messages = [{"role": "user", "content": "继续"}]

    outcome = _run(messages, root, build_control(messages))

    assert outcome["stopped"] == "error"
    assert outcome["wrapup"] == "ok"
    assert outcome["final"]["content"] == "收尾总结"
    assert sleeps == [2.0]
    assert script.requests[-1]["tool_choice"] == "none"


# ---------------------------------------------------------------------------
# 会话层：notice + turn_end
# ---------------------------------------------------------------------------


def test_session_shows_retry_notice_and_logs_count(tmp_path, monkeypatch, sleeps) -> None:
    from pipeline import paths
    from pipeline.agent import session_log as slog
    from pipeline.agent.assembly import SessionContextTracker
    from pipeline.agent.session import AgentSession, SessionHost
    from pipeline.agent.session_log import EpisodeLease
    from tests.test_agent_session import FakeChannel, make_episode

    EpisodeLease._reset_for_testing()
    repo = make_agent_root(tmp_path / "repo", ROOT_URL)
    monkeypatch.setattr(paths, "ROOT", repo)
    monkeypatch.setattr(paths, "DATA", repo / "data")
    (repo / "data" / "library").mkdir(parents=True)
    episode = make_episode(repo)
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    script = Script([_retryable(), FINAL])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    channel = FakeChannel()
    host = SessionHost(episode, root=repo, channel=channel)
    lease = host.ensure_lease()
    assert lease is not None
    lease.begin("sid-d93")
    session = AgentSession(host, scope_mode="creative", persist=True)
    try:
        session.run_turn("继续", messages=[{"role": "system", "content": "s"}], scope="creative",
                         status=None, tracker=SessionContextTracker(), root=repo)
    finally:
        EpisodeLease._reset_for_testing()

    notices = [p for kind, p in channel.shown if kind == "notice" and p.get("code") == "llm_retry"]
    assert len(notices) == 1
    assert notices[0]["level"] == "warn"
    assert "timed out" in notices[0]["text"] and "（1/3）" in notices[0]["text"]
    records = [json.loads(line) for line in slog.read_log(episode).splitlines() if line.strip()]
    ends = [r for r in records if r.get("k") == "turn_end"]
    assert ends[-1]["llm_retries"] == 1
    assert ends[-1]["stopped"] == "done"


# ---------------------------------------------------------------------------
# 真 socket：端点挂住超过配置的超时 → 可重试 → 第二次成功
# ---------------------------------------------------------------------------


def test_real_socket_timeout_is_retried(root, monkeypatch, sleeps) -> None:
    state = {"n": 0}
    release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state["n"] += 1
            if state["n"] == 1:
                release.wait(5)  # 挂住：客户端 1 s 超时先断
                return
            data = json.dumps({"choices": [{"message": FINAL}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.handle_error = lambda *args: None  # 被挂住的那条连接写回时对端已关，不刷屏
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = llm_mod.LLMConfig(
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1", model="m", api_key="k",
        request_timeout_s=1,
    )
    monkeypatch.setattr(llm_mod, "assert_egress_boundary", lambda *a, **k: None)
    messages = [{"role": "user", "content": "继续"}]
    started = time.monotonic()
    try:
        outcome = run_tool_loop(messages, ctx=ToolContext(scope="creative", root=root), config=cfg,
                                control=build_control(messages))
    finally:
        release.set()
        server.shutdown()
        server.server_close()

    assert outcome["stopped"] == "done"
    assert outcome["llm_retries"] == 1
    assert state["n"] == 2
    assert sleeps == [2.0]
    assert time.monotonic() - started < 4  # 真等了约 1 s 超时，而不是默认 300 s
