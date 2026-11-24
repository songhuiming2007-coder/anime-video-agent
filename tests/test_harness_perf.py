"""Harness 性能与延迟优化专项测试（Keep-Alive / effort=none / usage / 并发只读工具 / search_notes / crawl 复用 / 流式增量 / KV Cache 稳定）。"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pipeline.agent import aging, llm as llm_mod, protocol as proto_mod, session as sess_mod, tools as tools_mod, web_crawl
from pipeline.agent.compact import STATUS_CARD_KEY
from pipeline.agent.llm import LLMConfig, LLMError, run_tool_loop
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


def test_google_effort_none_sends_zero_thinking_budget_and_normalizes_usage() -> None:
    cfg = LLMConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-4-hydrogen",
        api_key="AIza-test",
        api_protocol="google-generative-ai",
        reasoning_effort="none",
    )
    payload, _ = llm_mod._google_request(cfg, [{"role": "user", "content": "hi"}], None, None, None)
    assert payload["generationConfig"] == {"thinkingConfig": {"thinkingBudget": 0}}

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


def test_stream_tightens_idle_timeout_after_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """首包到达后将流式空闲超时收紧至 STREAM_CHUNK_IDLE_TIMEOUT_S，首包前不收紧。"""
    seen_timeouts: list[float | None] = []

    class _FakeStreamResp:
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self) -> None:
            self._lines = [
                b'data: {"choices":[{"delta":{"role":"assistant","content":"hi"}}]}\n',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            ]

        def set_stream_timeout(self, t: float | None) -> None:
            seen_timeouts.append(t)

        def __iter__(self):
            assert seen_timeouts == []  # 首包读出前尚未收紧
            yield self._lines[0]
            yield self._lines[1]

        def __enter__(self):
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(llm_mod, "_open_llm_request", lambda req, *, timeout: _FakeStreamResp())
    cfg = LLMConfig(base_url=ROOT_URL, model="m", api_key="k", request_timeout_s=300)
    msg = llm_mod.chat_complete([{"role": "user", "content": "hi"}], config=cfg)
    assert msg["content"] == "hi"
    assert seen_timeouts == [llm_mod.STREAM_CHUNK_IDLE_TIMEOUT_S]


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
            with pytest.raises(BaseException) if False else _suppress_barrier():
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
    assert aging.AGED_TOOL_MAX_CHARS == 4_000
