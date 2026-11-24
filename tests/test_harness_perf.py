"""Harness 性能与延迟优化专项测试（Keep-Alive / effort=none / usage / 并发只读工具 / search_notes / crawl 复用 / 流式增量 / KV Cache 稳定）。"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pipeline.agent import llm as llm_mod, protocol as proto_mod, session as sess_mod, tools as tools_mod, web_crawl
from pipeline.agent.compact import STATUS_CARD_KEY
from pipeline.agent.llm import Decision, LLMConfig, LLMError, LoopControl, run_tool_loop
from pipeline.agent.tools import ToolContext
from tests.test_agent_loop import ROOT_URL, Interrupt, Script, build_control, patch_execute, tool_messages
from tests.test_agent_tools import make_agent_root, tool_call


# ---------------------------------------------------------------------------
# 1. HTTP Keep-Alive 连接复用、过期连接自愈与 3xx 禁重定向
# ---------------------------------------------------------------------------


def test_keepalive_pool_reuses_single_tcp_connection_and_refuses_redirects() -> None:
    """3 次连续 chat_complete 复用同一 TCP 连接；3xx 重定向仍拒绝以防凭据泄露。"""
    accepted_sockets: set[tuple[str, int]] = set()
    req_count = 0

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:
            nonlocal req_count
            accepted_sockets.add(self.client_address)
            length = int(self.headers.get("Content-Length") or 0)
            _ = self.rfile.read(length)
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "https://evil.example/steal")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            req_count += 1
            body = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": f"ok-{req_count}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

    class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    llm_mod._LLM_CONN_POOL.close_all()
    srv = _Server(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        cfg = LLMConfig(base_url=f"http://127.0.0.1:{port}/v1", model="mock", api_key="sk-test")
        replies = [
            llm_mod.chat_complete([{"role": "user", "content": f"hi-{i}"}], config=cfg)
            for i in range(3)
        ]
        assert [r["content"] for r in replies] == ["ok-1", "ok-2", "ok-3"]
        # 3 次请求只建了 1 条 TCP 连接
        assert len(accepted_sockets) == 1

        cfg_redir = LLMConfig(base_url=f"http://127.0.0.1:{port}/redirect/chat/completions", model="mock", api_key="sk-test")
        with pytest.raises(LLMError, match="拒绝 HTTP 302 重定向"):
            llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=cfg_redir)
    finally:
        llm_mod._LLM_CONN_POOL.close_all()
        srv.shutdown()
        srv.server_close()


def test_keepalive_pool_transparently_retries_stale_socket() -> None:
    """服务端主动关闭闲置 Keep-Alive 连接后，池自动无感新建连接重发一次。"""
    calls = 0

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:
            nonlocal calls
            calls += 1
            length = int(self.headers.get("Content-Length") or 0)
            _ = self.rfile.read(length)
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": f"r{calls}"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)
            if calls == 1:
                # 响应头声称 keep-alive 但服务端随即关掉底层 socket，制造陈旧池连接
                self.close_connection = True

    class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    llm_mod._LLM_CONN_POOL.close_all()
    srv = _Server(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        cfg = LLMConfig(base_url=f"http://127.0.0.1:{port}/v1", model="mock", api_key="sk-test")
        r1 = llm_mod.chat_complete([{"role": "user", "content": "1"}], config=cfg)
        time.sleep(0.05)  # 让服务端关闭完成
        r2 = llm_mod.chat_complete([{"role": "user", "content": "2"}], config=cfg)
        assert r1["content"] == "r1" and r2["content"] == "r2"
    finally:
        llm_mod._LLM_CONN_POOL.close_all()
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# 2. Google effort=none 显式 thinkingBudget=0 + Usage 观测 + 流式块间超时收紧
# ---------------------------------------------------------------------------


def test_google_effort_none_respects_model_capability_config() -> None:
    """仅配置了 google_none_thinking_budget 的模型发 thinkingBudget=0；gemini-2.5-pro / 非思考模型保持省略。"""
    cfg_h = LLMConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-4-hydrogen",
        api_key="AIza-test",
        api_protocol="google-generative-ai",
        reasoning_effort="none",
    )
    payload_h, _ = llm_mod._google_request(cfg_h, [{"role": "user", "content": "hi"}], None, None, None)
    assert payload_h["generationConfig"] == {"thinkingConfig": {"thinkingBudget": 0}}

    cfg_h_vertex = LLMConfig(
        base_url="https://aiplatform.googleapis.com/v1",
        model="google-vertex/gemini-4-hydrogen",
        api_key="AIza-test",
        api_protocol="google-vertex",
        reasoning_effort="none",
    )
    payload_hv, _ = llm_mod._google_request(cfg_h_vertex, [{"role": "user", "content": "hi"}], None, None, None)
    assert payload_hv["generationConfig"] == {"thinkingConfig": {"thinkingBudget": 0}}

    # gemini-2.5-pro 禁止 thinkingBudget=0；非思考旧模型不认 thinkingConfig：两者均须省略 generationConfig
    for model_id, proto in (
        ("gemini-2.5-pro", "google-generative-ai"),
        ("google-vertex/gemini-2.5-pro", "google-vertex"),
        ("gemini-2.0-flash", "google-generative-ai"),
    ):
        cfg_other = LLMConfig(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model=model_id,
            api_key="AIza-test",
            api_protocol=proto,
            reasoning_effort="none",
        )
        p_other, _ = llm_mod._google_request(cfg_other, [{"role": "user", "content": "hi"}], None, None, None)
        assert "generationConfig" not in p_other, f"{model_id} 不应在 effort=none 时硬发 thinkingBudget=0"

    usage = llm_mod._normalize_google_usage({
        "promptTokenCount": 64886,
        "cachedContentTokenCount": 62837,
        "thoughtsTokenCount": 420,
        "candidatesTokenCount": 180,
        "totalTokenCount": 65486,
    })
    assert usage == {
        "prompt_tokens": 64886,
        "completion_tokens": 600,
        "total_tokens": 65486,
        "cached_tokens": 62837,
        "thoughts_tokens": 420,
    }


def test_stream_tightens_idle_timeout_only_on_substantive_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """控制帧/心跳/role 帧不收紧超时，收到首个实质内容/思考/工具增量时才收紧至 STREAM_CHUNK_IDLE_TIMEOUT_S。"""
    cases = [
        (
            "openai-completions",
            [
                b": keep-alive\n",
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\n',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            ],
            2,  # 第 3 行（下标 2）才收到 content
        ),
        (
            "anthropic-messages",
            [
                b'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n',
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n',
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"step"}}\n',
                b'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"hi"}}\n',
                b'data: {"type":"message_stop"}\n',
            ],
            2,  # message_start 与空 content_block_start 均不收紧；thinking_delta 才收紧
        ),
        (
            "openai-responses",
            [
                b'data: {"type":"response.created","response":{"id":"r1"}}\n',
                b'data: {"type":"response.in_progress","response":{"id":"r1"}}\n',
                b'data: {"type":"response.output_text.delta","delta":"hi"}\n',
                b'data: {"type":"response.completed","response":{"output":[],"usage":{"input_tokens":5,"output_tokens":1}}}\n',
            ],
            2,  # response.created / in_progress 不收紧；output_text.delta 才收紧
        ),
    ]
    for proto, raw_lines, tighten_at_idx in cases:
        seen_timeouts: list[tuple[int, float | None]] = []
        yielded = {"i": -1}

        class _FakeStreamResp:
            headers = {"Content-Type": "text/event-stream"}

            def set_stream_timeout(self, t: float | None) -> None:
                seen_timeouts.append((yielded["i"], t))

            def __iter__(self):
                for idx, ln in enumerate(raw_lines):
                    yielded["i"] = idx
                    yield ln

            def __enter__(self):
                return self

            def __exit__(self, *_a: Any) -> bool:
                return False

        monkeypatch.setattr(llm_mod, "_open_llm_request", lambda req, *, timeout: _FakeStreamResp())
        cfg = LLMConfig(
            base_url=ROOT_URL, model="m", api_key="k", api_protocol=proto, request_timeout_s=300
        )
        msg = llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=cfg)
        assert msg["content"] == "hi"
        assert seen_timeouts == [(tighten_at_idx, llm_mod.STREAM_CHUNK_IDLE_TIMEOUT_S)], proto


# ---------------------------------------------------------------------------
# 3. search_notes 跳过非笔记大目录（shots / index 等）
# ---------------------------------------------------------------------------


def test_search_notes_skips_media_dirs_and_finds_real_and_custom_notes(tmp_path: Path) -> None:
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    lib = root / "data" / "library"
    (lib / "notes").mkdir(parents=True, exist_ok=True)
    (lib / "fetch-log" / "abc").mkdir(parents=True, exist_ok=True)
    (lib / "自定义番剧考据").mkdir(parents=True, exist_ok=True)
    (lib / "shots" / "ep01").mkdir(parents=True, exist_ok=True)
    (lib / "index").mkdir(parents=True, exist_ok=True)

    (lib / "notes" / "aimer.md").write_text("Aimer 破晓淬火关键笔记", encoding="utf-8")
    (lib / "fetch-log" / "abc" / "page.txt").write_text("破晓淬火一手访谈记录", encoding="utf-8")
    (lib / "自定义番剧考据" / "ref.md").write_text("破晓淬火自定义目录笔记", encoding="utf-8")
    # 放进被剪枝目录的同名文本不得被扫到
    (lib / "shots" / "ep01" / "noise.txt").write_text("破晓淬火噪音不应命中", encoding="utf-8")
    (lib / "index" / "noise.txt").write_text("破晓淬火索引噪音不应命中", encoding="utf-8")

    ctx = ToolContext(scope="creative", root=root)
    res = tools_mod._tool_search_notes({"query": "破晓淬火", "limit": 10}, ctx)
    hit_paths = [h["path"] for h in res["hits"]]
    assert any("notes/aimer.md" in p for p in hit_paths)
    assert any("fetch-log/abc/page.txt" in p for p in hit_paths)
    assert any("自定义番剧考据/ref.md" in p for p in hit_paths)
    assert not any(p.startswith("shots/") or p.startswith("index/") for p in hit_paths)


# ---------------------------------------------------------------------------
# 4. 只读免卡工具批内并发执行、顺序写史与中断兜底
# ---------------------------------------------------------------------------


def _tc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def test_parallel_readonly_tools_run_concurrently_and_commit_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同批 4 个 web_fetch 真正并发执行（并发峰值 >= 3），但提交顺序严格保持原 index 0..3。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tc("web_fetch", {"url": f"https://example.com/{i}"}, call_id=f"c{i}")
            for i in range(4)
        ],
    }
    script = Script([reply, {"role": "assistant", "content": "全部抓完"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    barrier = threading.Barrier(3, timeout=3.0)

    def slow_fetch(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            # 只要 3 个线程同时到达就立刻放行，串行执行必然超时抛 BrokenBarrierError
            with _suppress_barrier():
                barrier.wait()
            # 故意让后号先完成（c3 最快，c0 最慢），验证 commit 仍按 c0..c3 顺序
            idx = int(args["url"].rsplit("/", 1)[-1])
            time.sleep((3 - idx) * 0.03)
            return {"ok": True, "result": {"url": args["url"], "idx": idx}}
        finally:
            with lock:
                in_flight -= 1

    patch_execute(monkeypatch, slow_fetch)
    messages = [{"role": "user", "content": "并发抓 4 页"}]
    cfg = LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k")
    out = run_tool_loop(
        messages,
        ctx=ToolContext(scope="creative", root=root),
        config=cfg,
        control=build_control(messages),
    )
    assert out["stopped"] == "done"
    assert max_in_flight >= 3
    tmsgs = tool_messages(messages)
    assert [m["tool_call_id"] for m in tmsgs] == ["c0", "c1", "c2", "c3"]
    assert [json.loads(m["content"])["result"]["idx"] for m in tmsgs] == [0, 1, 2, 3]


class _suppress_barrier:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type is threading.BrokenBarrierError


def test_parallel_batch_interrupt_pairs_all_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发批次中途 KeyboardInterrupt 时，已完成与未完成调用均按原序补齐 tool 消息，不留孤儿。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tc("web_fetch", {"url": f"https://example.com/{i}"}, call_id=f"p{i}")
            for i in range(3)
        ] + [
            _tc("bash", {"command": "echo after", "description": "后置"}, call_id="b3")
        ],
    }
    script = Script([reply, {"role": "assistant", "content": "已收尾"}])
    monkeypatch.setattr(llm_mod, "chat_complete", script)

    def flaky_exec(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        if args.get("url", "").endswith("/1"):
            raise KeyboardInterrupt
        return {"ok": True, "result": {"url": args.get("url")}}

    patch_execute(monkeypatch, flaky_exec)
    messages = [{"role": "user", "content": "中断测试"}]
    cfg = LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k")
    out = run_tool_loop(
        messages,
        ctx=ToolContext(scope="creative", root=root),
        config=cfg,
        control=build_control(messages, interrupt=Interrupt()),
    )
    assert out["stopped"] == "interrupted"
    tmsgs = tool_messages(messages)
    assert [m["tool_call_id"] for m in tmsgs] == ["p0", "p1", "p2", "b3"]
    # 排在并发批之后的 bash 必须是未执行
    assert json.loads(tmsgs[3]["content"])["error"] == "未执行：人类中断"


# ---------------------------------------------------------------------------
# 5. crawl 常驻浏览器实例复用与崩溃自动恢复
# ---------------------------------------------------------------------------


def test_persistent_crawler_reuses_instance_and_recovers_on_crash() -> None:
    runtime = web_crawl._PersistentCrawlerRuntime()
    enters = 0
    exits = 0
    aruns = 0
    fail_once = {"flag": False}

    class _FakeRes:
        success = True
        markdown = "正文内容"
        html = "<p>正文内容</p>"
        redirected_url = None

        def __init__(self, url: str) -> None:
            self.url = url

    class _FakeCrawler:
        def __init__(self, config: Any) -> None:
            self.config = config

        async def __aenter__(self) -> "_FakeCrawler":
            nonlocal enters
            enters += 1
            return self

        async def __aexit__(self, *_a: Any) -> None:
            nonlocal exits
            exits += 1

        async def arun(self, *, url: str, config: Any) -> _FakeRes:
            nonlocal aruns
            aruns += 1
            if fail_once["flag"]:
                fail_once["flag"] = False
                raise RuntimeError("Target closed")
            return _FakeRes(url)

    class _Cfg:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    try:
        r1 = runtime.run_crawl(
            "https://example.com/1", target_base_dir="/tmp/c4", stealth=False, timeout_s=10,
            crawler_cls=_FakeCrawler, browser_cfg_cls=_Cfg, run_cfg_cls=_Cfg,
        )
        r2 = runtime.run_crawl(
            "https://example.com/2", target_base_dir="/tmp/c4", stealth=False, timeout_s=10,
            crawler_cls=_FakeCrawler, browser_cfg_cls=_Cfg, run_cfg_cls=_Cfg,
        )
        assert r1["markdown"] == "正文内容" and r2["markdown"] == "正文内容"
        # 两次同配置抓取仅冷启动 1 次浏览器
        assert enters == 1 and exits == 0

        # 模拟复用中的浏览器崩掉：应自动淘汰旧实例并重建 1 次成功返回
        fail_once["flag"] = True
        r3 = runtime.run_crawl(
            "https://example.com/3", target_base_dir="/tmp/c4", stealth=False, timeout_s=10,
            crawler_cls=_FakeCrawler, browser_cfg_cls=_Cfg, run_cfg_cls=_Cfg,
        )
        assert r3["markdown"] == "正文内容"
        assert enters == 2 and exits == 1
    finally:
        runtime.close_all()
    assert exits == 2


# ---------------------------------------------------------------------------
# 6. 流式文本增量回调 + KV Cache 状态卡去重 + 单调水位老化不反复击穿缓存
# ---------------------------------------------------------------------------


def test_google_stream_emits_only_visible_text_deltas() -> None:
    """流式增量只推送可见正文（忽略 thought 块），且协议通道产出 assistant_delta 帧。"""
    deltas: list[str] = []
    tok = llm_mod._ON_TEXT_DELTA.set(deltas.append)
    try:
        msg, usage = llm_mod._assemble_google_stream(iter([
            'data: {"candidates":[{"content":{"parts":[{"text":"内部思考","thought":true}]}}]}\n',
            'data: {"candidates":[{"content":{"parts":[{"text":"你好，"}]}}]}\n',
            'data: {"candidates":[{"content":{"parts":[{"text":"世界"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":100,"cachedContentTokenCount":80,"thoughtsTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":115}}\n',
        ]))
    finally:
        llm_mod._ON_TEXT_DELTA.reset(tok)
    assert deltas == ["你好，", "世界"]
    assert msg["content"] == "你好，世界"
    assert usage is not None and usage["cached_tokens"] == 80 and usage["thoughts_tokens"] == 10

    sent: list[dict[str, Any]] = []

    class _W:
        def send(self, frame: dict[str, Any]) -> None:
            sent.append(frame)

    ch = proto_mod.ProtocolChannel(_W(), {"turn_id": "t99", "pending": {}})
    ch.show("assistant_delta", {"turn_id": "t99", "text": "你好，"})
    assert sent == [{"t": "assistant_delta", "turn_id": "t99", "text": "你好，"}]


def test_session_skips_duplicate_unchanged_status_card_across_turns(tmp_path: Path) -> None:
    """连续多轮状态卡逐字未变时不再重复追加，保持前缀稳定以命中 KV Cache。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    ep = root / "data" / "episodes" / "01-test"
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text("类型: 杂谈\n", encoding="utf-8")

    class _DummyChan:
        name = "test"
        def is_tty(self) -> bool: return True
        def show(self, kind: str, payload: dict[str, Any]) -> None: return None
        def ask(self, req: Any) -> Any: raise AssertionError("unexpected ask")

    host = sess_mod.SessionHost(ep, channel=_DummyChan(), root=root)
    session = host.session(persist=False, scope_mode="creative")
    from pipeline.agent.assembly import SessionContextTracker

    tracker = SessionContextTracker()
    messages: list[dict[str, Any]] = []
    session.messages = messages
    session._ep_dir = ep
    session._status_card = lambda scope, status: "[状态卡] 工序: 02.5 人审改稿"  # type: ignore[method-assign]

    # Turn 1 装配写入 1 张状态卡
    session._assemble(messages, tracker, None, "creative", "第一轮问题", root=root)
    messages.append({"role": "assistant", "content": "第一轮答复"})
    # Turn 2 状态卡未变 -> 不重复追加
    session._assemble(messages, tracker, None, "creative", "第二轮问题", root=root)
    cards = [m for m in messages if m.get(STATUS_CARD_KEY)]
    assert len(cards) == 1

    # Turn 3 状态卡发生变化 -> 追加新卡
    session._status_card = lambda scope, status: "[状态卡] 工序: 03 语音合成"  # type: ignore[method-assign]
    session._assemble(messages, tracker, None, "creative", "第三轮问题", root=root)
    cards = [m for m in messages if m.get(STATUS_CARD_KEY)]
    assert len(cards) == 2


def test_intra_turn_watermark_does_not_mutate_stable_batches_under_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """触发一次回合内老化后，只要后续新结果未再次越过 safe_tokens，不改写上一批以保住 KV Cache。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    (root / "config" / "agent" / "compact.json").write_text(
        json.dumps({
            "auto_trigger": False,
            "trigger_ratio": 0.6,
            "tail_ratio": 0.15,
            "tail_max_messages": 20,
            "models": {"mock-model": {"window": 100_000, "reliable_ratio": 1.0}},
        }),
        encoding="utf-8",
    )
    r1 = "R1-" + "甲" * 45_000 + "-R1END"
    r2 = "R2-" + "乙" * 40_000 + "-R2END"
    r3 = "R3-小结果"
    results = iter([
        {"ok": True, "result": {"data": r1}},
        {"ok": True, "result": {"data": r2}},
        {"ok": True, "result": {"data": r3}},
    ])
    patch_execute(monkeypatch, lambda name, args, ctx: next(results))

    seen_reqs: list[list[dict[str, Any]]] = []
    step = 0

    def fake_chat(messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> dict[str, Any]:
        nonlocal step
        step += 1
        seen_reqs.append([dict(m) for m in messages])
        if step == 1:
            llm_mod._LAST_USAGE.set({"prompt_tokens": 5_000})
            return tool_call("read_artifact", {"path": "a.md"}, call_id="c1")
        if step == 2:
            llm_mod._LAST_USAGE.set({"prompt_tokens": 50_000})
            return tool_call("read_artifact", {"path": "b.md"}, call_id="c2")
        if step == 3:
            # r1 已老化，r2 保留，总 token 降回 45k（远低于 75k safe_tokens）
            llm_mod._LAST_USAGE.set({"prompt_tokens": 45_000})
            return tool_call("read_artifact", {"path": "c.md"}, call_id="c3")
        llm_mod._LAST_USAGE.set({"prompt_tokens": 45_500})
        return {"role": "assistant", "content": "完成"}

    monkeypatch.setattr(llm_mod, "chat_complete", fake_chat)
    convo: list[dict[str, Any]] = [{"role": "system", "content": "S"}, {"role": "user", "content": "开工"}]
    cfg = LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k")
    out = run_tool_loop(convo, ctx=ToolContext(scope="creative", root=root), config=cfg, control=build_control(convo))
    assert out["stopped"] == "done"
    # 第 3 次请求（r1+r2 > 75k）：r1 被老化，r2 保留
    req3_tools = [m["content"] for m in seen_reqs[2] if m.get("role") == "tool"]
    assert "-R1END" not in req3_tools[0] and r2 in req3_tools[1]
    # 第 4 次请求（只新增极小 r3，总 45k < 75k）：r1 保持老化（单调不复活），且 r2 不得被无故改写（保住 KV Cache）
    req4_tools = [m["content"] for m in seen_reqs[3] if m.get("role") == "tool"]
    assert "-R1END" not in req4_tools[0]
    assert r2 in req4_tools[1]
    assert req4_tools[:2] == req3_tools[:2]


# ---------------------------------------------------------------------------
# 7. Code Review 回归：Keep-Alive 超时/代理凭据、并发中断不双写、异常配对、常驻浏览器并发、流式重试丢弃
# ---------------------------------------------------------------------------


def test_keepalive_pool_does_not_retry_timeout_and_preserves_proxy_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复用连接遇 TimeoutError 必须直抛不得静默重发 POST；带凭据 HTTP/HTTPS 代理保留 Proxy-Authorization。"""
    slow_hits = 0

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:
            nonlocal slow_hits
            length = int(self.headers.get("Content-Length") or 0)
            _ = self.rfile.read(length)
            if self.path.startswith("/slow"):
                slow_hits += 1
                time.sleep(0.35)
                body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "late"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass
                return
            if self.path.startswith("/err400"):
                body = b'{"error":{"message":"bad"}}'
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "fast"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

    class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    llm_mod._LLM_CONN_POOL.close_all()
    srv = _Server(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # 1. 先预热 Keep-Alive 连接到池中，再对同 host:port 发超时请求 -> 服务端只能收到 1 次（不重发）
        cfg_fast = LLMConfig(base_url=f"http://127.0.0.1:{port}/v1", model="m", api_key="sk-test")
        assert llm_mod.chat_complete([{"role": "user", "content": "warm"}], config=cfg_fast)["content"] == "fast"
        cfg_slow = LLMConfig(
            base_url=f"http://127.0.0.1:{port}/slow/chat/completions", model="m", api_key="sk-test"
        )
        with pytest.raises(LLMError, match="timed out|超时"):
            llm_mod.chat_complete([{"role": "user", "content": "wait"}], config=cfg_slow, timeout=0.08)
        time.sleep(0.05)
        assert slow_hits == 1

        # 2. 4xx 响应关闭连接，不归还到池中
        cfg_err = LLMConfig(
            base_url=f"http://127.0.0.1:{port}/err400/chat/completions", model="m", api_key="sk-test"
        )
        with pytest.raises(LLMError):
            llm_mod.chat_complete([{"role": "user", "content": "bad"}], config=cfg_err)
        assert not llm_mod._LLM_CONN_POOL._buckets
    finally:
        llm_mod._LLM_CONN_POOL.close_all()
        srv.shutdown()
        srv.server_close()

    # 3. 验证 HTTPS 代理隧道保留 Proxy-Authorization
    pool = llm_mod._KeepAlivePool()
    conn = pool._new_conn("https", "api.example.com", 443, "http://alice:s3cr%40t@proxy.local:8080", 10.0)
    expected_auth = "Basic " + base64.b64encode(b"alice:s3cr@t").decode("ascii")
    assert conn._tunnel_headers.get("Proxy-Authorization") == expected_auth  # type: ignore[attr-defined]

    # 4. 验证 read(0) 不误置 _eof，且未关闭响应不归还到池中
    class _FakeResp:
        headers: dict[str, str] = {}
        status = 200
        will_close = False

        def __init__(self, closed: bool) -> None:
            self._closed = closed

        def read(self, amt: int | None = None) -> bytes:
            return b"" if amt == 0 else b"abc"

        def isclosed(self) -> bool:
            return self._closed

    class _FakeConn:
        sock = object()
        closed_called = False

        def close(self) -> None:
            self.closed_called = True

    fc = _FakeConn()
    pr = llm_mod._PooledHTTPResponse(pool, ("http", "h", 80, ""), fc, _FakeResp(closed=False))  # type: ignore[arg-type]
    assert pr.read(0) == b""
    assert pr._eof is False
    pr.close()
    assert fc.closed_called is True
    assert not pool._buckets


def test_parallel_batch_interrupt_in_review_and_commit_never_duplicates_tool_call_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并行批在预检 review 阶段与落史 commit 循环中途被中断时，绝不重复提交同一 tool_call_id。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tc("web_fetch", {"url": f"https://example.com/{i}"}, call_id=f"r{i}")
            for i in range(3)
        ],
    }

    # 场景 A：预检阶段第 0 条已通过、第 1 条在 review 时被中断
    monkeypatch.setattr(llm_mod, "chat_complete", Script([reply, {"role": "assistant", "content": "收尾A"}]))
    review_calls = 0

    def review_interrupt(name: str, args: dict[str, Any]) -> Decision:
        nonlocal review_calls
        review_calls += 1
        if review_calls == 2:
            raise KeyboardInterrupt
        return Decision(ok=True, provenance="card_free")

    msgs_a = [{"role": "user", "content": "场景A"}]
    ctrl_a = LoopControl(
        interrupt=Interrupt(),
        commit=lambda m, o: msgs_a.append(m),
        review=review_interrupt,
        critical_tools=frozenset(),
    )
    out_a = run_tool_loop(
        msgs_a,
        ctx=ToolContext(scope="creative", root=root),
        config=LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k"),
        control=ctrl_a,
    )
    assert out_a["stopped"] == "interrupted"
    tids_a = [m["tool_call_id"] for m in tool_messages(msgs_a)]
    assert tids_a == ["r0", "r1", "r2"]

    # 场景 B：并行执行全部完成后，在落史 commit 第 0 条后抛 KeyboardInterrupt（验证 committed 集合防双写）
    monkeypatch.setattr(llm_mod, "chat_complete", Script([reply, {"role": "assistant", "content": "收尾B"}]))
    patch_execute(monkeypatch, lambda n, a, c: {"ok": True, "result": {"url": a["url"]}})
    msgs_b: list[dict[str, Any]] = [{"role": "user", "content": "场景B"}]
    tool_commits = 0

    def flaky_commit(m: dict[str, Any], origin: str) -> None:
        nonlocal tool_commits
        msgs_b.append(m)
        if origin == "tool":
            tool_commits += 1
            if tool_commits == 1:
                raise KeyboardInterrupt

    ctrl_b = LoopControl(
        interrupt=Interrupt(),
        commit=flaky_commit,
        review=lambda n, a: Decision(ok=True, provenance="card_free"),
        critical_tools=frozenset(),
    )
    out_b = run_tool_loop(
        msgs_b,
        ctx=ToolContext(scope="creative", root=root),
        config=LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k"),
        control=ctrl_b,
    )
    assert out_b["stopped"] == "interrupted"
    tids_b = [m["tool_call_id"] for m in tool_messages(msgs_b)]
    assert tids_b == ["r0", "r1", "r2"]
    assert out_b["tool_executions"] == 3
    assert out_b["lookups"]["web"] == 3


def test_parallel_batch_worker_exception_pairs_cleanly_as_error_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发批内单个工作线程抛非 KeyboardInterrupt 异常时转为结构化错误回喂，不撕裂同批 tool_call_id 配对。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tc("web_fetch", {"url": f"https://example.com/{i}"}, call_id=f"e{i}")
            for i in range(3)
        ],
    }
    monkeypatch.setattr(llm_mod, "chat_complete", Script([reply, {"role": "assistant", "content": "已处理异常"}]))

    def boom_on_1(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        if args["url"].endswith("/1"):
            raise RuntimeError("boom-worker")
        return {"ok": True, "result": {"url": args["url"]}}

    patch_execute(monkeypatch, boom_on_1)
    msgs = [{"role": "user", "content": "异常测试"}]
    out = run_tool_loop(
        msgs,
        ctx=ToolContext(scope="creative", root=root),
        config=LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k"),
        control=build_control(msgs),
    )
    assert out["stopped"] == "done"
    tmsgs = tool_messages(msgs)
    assert [m["tool_call_id"] for m in tmsgs] == ["e0", "e1", "e2"]
    err_payload = json.loads(tmsgs[1]["content"])
    assert err_payload["ok"] is False and "RuntimeError: boom-worker" in err_payload["error"]
    assert out["lookups"]["web"] == 3


def test_persistent_crawler_concurrent_cold_start_and_non_crash_isolation() -> None:
    """并发冷启仅建 1 个浏览器实例（无泄漏）；单页普通异常不淘汰共享健康浏览器；close_all 回收线程与 loop。"""
    runtime = web_crawl._PersistentCrawlerRuntime()
    enters = 0
    exits = 0

    class _FakeRes:
        success = True
        markdown = "ok"
        html = "<p>ok</p>"
        redirected_url = None

        def __init__(self, url: str) -> None:
            self.url = url

    class _FakeCrawler:
        def __init__(self, config: Any) -> None:
            self.closed = False

        async def __aenter__(self) -> "_FakeCrawler":
            nonlocal enters
            await asyncio.sleep(0.05)  # 拉长冷启窗口以暴露并发竞态
            enters += 1
            return self

        async def __aexit__(self, *_a: Any) -> None:
            nonlocal exits
            self.closed = True
            exits += 1

        async def arun(self, *, url: str, config: Any) -> _FakeRes:
            if url.endswith("/timeout-page"):
                await asyncio.sleep(0.01)
                raise RuntimeError("Page navigation timeout")
            await asyncio.sleep(0.06)
            assert not self.closed, "普通单页失败不得关闭同浏览器上其他在途页面"
            return _FakeRes(url)

    class _Cfg:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    try:
        # 1. 4 线程并发冷启 -> 只启动 1 次浏览器
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = [
                pool.submit(
                    runtime.run_crawl,
                    f"https://example.com/{i}",
                    target_base_dir="/tmp/c4-conc",
                    stealth=False,
                    timeout_s=10,
                    crawler_cls=_FakeCrawler,
                    browser_cfg_cls=_Cfg,
                    run_cfg_cls=_Cfg,
                )
                for i in range(4)
            ]
            res_list = [f.result() for f in futs]
        assert len(res_list) == 4
        assert enters == 1 and exits == 0

        # 2. 并发请求中 1 条遇到普通单页超时，不得杀掉同浏览器上另一条健康页面
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_bad = pool.submit(
                runtime.run_crawl,
                "https://example.com/timeout-page",
                target_base_dir="/tmp/c4-conc",
                stealth=False,
                timeout_s=10,
                crawler_cls=_FakeCrawler,
                browser_cfg_cls=_Cfg,
                run_cfg_cls=_Cfg,
            )
            f_ok = pool.submit(
                runtime.run_crawl,
                "https://example.com/healthy-page",
                target_base_dir="/tmp/c4-conc",
                stealth=False,
                timeout_s=10,
                crawler_cls=_FakeCrawler,
                browser_cfg_cls=_Cfg,
                run_cfg_cls=_Cfg,
            )
            with pytest.raises(RuntimeError, match="Page navigation timeout"):
                f_bad.result()
            assert f_ok.result()["markdown"] == "ok"
        assert enters == 1 and exits == 0
        loop_ref = runtime._loop
        thread_ref = runtime._thread
    finally:
        runtime.close_all()
    assert exits == 1
    assert loop_ref is not None and loop_ref.is_closed()
    assert thread_ref is not None and not thread_ref.is_alive()


def test_session_discards_partial_stream_buffer_on_retry_and_rollback(tmp_path: Path) -> None:
    """流式重试或回滚时必须丢弃未刷出的半截文本缓冲，防止串流污染。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    ep = root / "data" / "episodes" / "01-test"
    ep.mkdir(parents=True, exist_ok=True)
    events: list[tuple[str, dict[str, Any]]] = []

    class _CapChan:
        name = "test"
        def is_tty(self) -> bool: return True
        def show(self, kind: str, payload: dict[str, Any]) -> None: events.append((kind, payload))
        def ask(self, req: Any) -> Any: raise AssertionError("unexpected ask")

    host = sess_mod.SessionHost(ep, channel=_CapChan(), root=root)
    s = host.session(persist=False, scope_mode="creative")
    s._turn_id = "t1"
    s._on_text_delta("首包")       # 首包立刻刷出
    s._on_text_delta("残留半句")   # 未满 48 字、无换行、未满 50 ms，留在 _stream_buf
    assert s._stream_buf == ["残留半句"]

    s._on_llm_retry(1, 3, 2.0, "timeout")
    assert s._stream_buf == [] and s._stream_buf_chars == 0
    # 回滚收尾时同样不得把缓冲刷上屏
    from pipeline.agent.assembly import SessionContextTracker

    tracker = SessionContextTracker()
    s._stream_buf.append("回滚垃圾")
    s._stream_buf_chars = 4
    s._finish_turn((0, SessionContextTracker()), tracker, {"stopped": "error", "rollback": True}, [])
    deltas = [p["text"] for k, p in events if k == "assistant_delta"]
    assert deltas == ["首包"]


def test_search_notes_skips_subs_and_non_regular_files(tmp_path: Path) -> None:
    """search_notes(source=notes) 跳过 subs/ 且忽略 FIFO 等非普通文件不挂起。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    lib = root / "data" / "library"
    (lib / "notes").mkdir(parents=True, exist_ok=True)
    (lib / "subs").mkdir(parents=True, exist_ok=True)
    (lib / "notes" / "ok.md").write_text("核心考据", encoding="utf-8")
    (lib / "subs" / "sub.txt").write_text("核心考据字幕噪音", encoding="utf-8")
    fifo_path = lib / "notes" / "block.txt"
    os.mkfifo(fifo_path)

    ctx = ToolContext(scope="creative", root=root)
    res = tools_mod._tool_search_notes({"query": "核心考据", "limit": 10}, ctx)
    assert [h["path"] for h in res["hits"]] == ["notes/ok.md"]


def test_default_crawl_fn_concurrent_import_is_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发首次导入 crawl4ai 时受锁保护，不会因 sys.modules 半初始化误报未知目录。"""
    fake_data = tmp_path / "data"
    fake_data.mkdir()
    target_dir = str(fake_data / "crawl4ai")
    monkeypatch.setattr(web_crawl.paths, "DATA", fake_data)
    monkeypatch.delenv("CRAWL4_AI_BASE_DIRECTORY", raising=False)
    orig_c4 = sys.modules.pop("crawl4ai", None)
    orig_db = sys.modules.pop("crawl4ai.async_database", None)

    orig_import = __import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "crawl4ai":
            mod = types.ModuleType("crawl4ai")
            sys.modules["crawl4ai"] = mod
            time.sleep(0.05)  # 模拟主包已入 sys.modules 但 async_database 尚未完成导入的窗口
            db_mod = types.ModuleType("crawl4ai.async_database")
            db_mod.base_directory = str(Path(os.environ["CRAWL4_AI_BASE_DIRECTORY"]) / ".crawl4ai")  # type: ignore[attr-defined]
            sys.modules["crawl4ai.async_database"] = db_mod
            mod.AsyncWebCrawler = object  # type: ignore[attr-defined]
            mod.BrowserConfig = object  # type: ignore[attr-defined]
            mod.CrawlerRunConfig = object  # type: ignore[attr-defined]
            return mod
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.setattr(
        web_crawl._CRAWLER_RUNTIME,
        "run_crawl",
        lambda url, **_kw: {"final_url": url, "markdown": "ok", "html": ""},
    )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = [
                pool.submit(web_crawl._default_crawl_fn, f"https://example.com/{i}", stealth=False, timeout_s=5.0)
                for i in range(4)
            ]
            results = [f.result() for f in futs]
        assert len(results) == 4
        assert os.environ.get("CRAWL4_AI_BASE_DIRECTORY") == target_dir
    finally:
        sys.modules.pop("crawl4ai", None)
        sys.modules.pop("crawl4ai.async_database", None)
        if orig_c4 is not None:
            sys.modules["crawl4ai"] = orig_c4
        if orig_db is not None:
            sys.modules["crawl4ai.async_database"] = orig_db


def test_parallel_commit_uses_real_interrupt_defer_not_absorb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常落史期间收到真实 Interrupt.defer 中断不得被吞掉，且已落盘条目绝不双写、后续工具不执行。"""
    root = make_agent_root(tmp_path / "repo", ROOT_URL)
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tc("web_fetch", {"url": "https://example.com/0"}, call_id="p0"),
            _tc("web_fetch", {"url": "https://example.com/1"}, call_id="p1"),
            _tc("bash", {"command": "echo should-not-run", "description": "后置"}, call_id="b2"),
        ],
    }
    monkeypatch.setattr(llm_mod, "chat_complete", Script([reply, {"role": "assistant", "content": "中断收尾"}]))
    executed_names: list[str] = []

    def fake_exec(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        executed_names.append(name)
        return {"ok": True, "result": {"name": name}}

    patch_execute(monkeypatch, fake_exec)
    real_interrupt = sess_mod.TurnInterrupt()
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "真实 defer 中断"}]
    tool_commits = 0

    def real_like_commit(m: dict[str, Any], origin: str) -> None:
        nonlocal tool_commits
        # 复刻 AgentSession._commit 的真实 defer 语义：写盘期收到 SIGINT → 补 append 后延后重抛
        with real_interrupt.defer():
            if origin == "tool":
                tool_commits += 1
                if tool_commits == 1:
                    msgs.append(m)
                    raise KeyboardInterrupt
            msgs.append(m)

    ctrl = LoopControl(
        interrupt=real_interrupt,
        commit=real_like_commit,
        review=lambda n, a: Decision(ok=True, provenance="card_free"),
        critical_tools=frozenset(),
    )
    out = run_tool_loop(
        msgs,
        ctx=ToolContext(scope="creative", root=root),
        config=LLMConfig(base_url=ROOT_URL, model="mock-model", api_key="k"),
        control=ctrl,
    )
    # 绝不能被 absorb 吞掉后继续跑 bash
    assert out["stopped"] == "interrupted"
    assert real_interrupt.dropped == 0
    assert "bash" not in executed_names
    tmsgs = tool_messages(msgs)
    assert [m["tool_call_id"] for m in tmsgs] == ["p0", "p1", "b2"]
    assert json.loads(tmsgs[2]["content"])["error"] == "未执行：人类中断"
    assert out["tool_executions"] == 2
    assert out["lookups"]["web"] == 2


def test_chat_complete_forwards_custom_root_to_google_thinking_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chat_complete(..., root=custom_root) 应读取 custom_root 下的 compact.json 决定 thinkingBudget。"""
    custom_root = tmp_path / "custom_repo"
    (custom_root / "config" / "agent").mkdir(parents=True, exist_ok=True)
    (custom_root / "config" / "agent" / "compact.json").write_text(
        json.dumps({
            "models": {
                "gemini-custom-test": {"window": 1048576, "reliable_ratio": 1.0, "google_none_thinking_budget": 0}
            }
        }),
        encoding="utf-8",
    )
    captured: list[dict[str, Any]] = []

    class _FakeResp:
        headers = {"Content-Type": "application/json"}

        def read(self) -> bytes:
            return json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

    def fake_open(req: urllib.request.Request, *, timeout: float | None) -> _FakeResp:
        captured.append(json.loads((req.data or b"{}").decode("utf-8")))
        return _FakeResp()

    monkeypatch.setattr(llm_mod, "_open_llm_request", fake_open)
    cfg = LLMConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-custom-test",
        api_key="AIza-test",
        api_protocol="google-generative-ai",
        reasoning_effort="none",
    )
    msg = llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=cfg, root=custom_root)
    assert msg["content"] == "ok"
    assert captured[0]["generationConfig"] == {"thinkingConfig": {"thinkingBudget": 0}}
