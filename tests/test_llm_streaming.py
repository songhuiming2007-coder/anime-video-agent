"""D94：模型请求改流式——SSE 增量拼装、按 Content-Type 分流、断流可重试。

手造序列照 2026-10-14 本机代理（CLIProxyAPI 7.2.132 + gemini-3.8-flash-high）探针记下的结构
（施工图 docs/dev/plans/2026-10-14-llm-streaming-spec.md §二）：每块 `choices[0].delta` 带 role / content /
reasoning_content / tool_calls，并行工具调用按 `index` 各自到达，末块带 `finish_reason` 与 `usage`，以 `data: [DONE]` 结尾。
拼出的 message 必须与非流式回复同形：2026-10-15 探针同题对照（tests/fixtures/llm_stream_probe/，只把响应 id 换成
probe-redacted）与旧会话日志都是 content / reasoning_content 空时为 null、tool_calls 里没有 index（TestRealProbe）。
"""

from __future__ import annotations

import http.client
import http.server
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from pipeline.agent import llm as llm_mod
from pipeline.agent.llm import LLMConfig, LLMError, _assemble_stream, chat_complete

CFG = LLMConfig(base_url="http://127.0.0.1:9/v1", model="mock-model", api_key="sk-test")


def sse(*chunks: object, done: bool = True) -> list[str]:
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks]
    return lines + (["data: [DONE]\n\n"] if done else [])


def delta(finish: str | None = None, **d: object) -> dict:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


USAGE = {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290}

# 两个并行工具调用，arguments 跨块分片；content / reasoning_content 全程为 null（探针：默认推理强度下就是这样）
TWO_CALLS = sse(
    delta(role="assistant", content=None, reasoning_content=None,
          tool_calls=[{"index": 0, "id": "call_a", "type": "function", "function": {"name": "get_weather", "arguments": ""}}]),
    delta(content=None, reasoning_content=None, tool_calls=[{"index": 0, "function": {"arguments": '{"city": "杭'}}]),
    delta(content=None, reasoning_content=None, tool_calls=[{"index": 0, "function": {"arguments": '州"}'}}]),
    delta(content=None, reasoning_content=None,
          tool_calls=[{"index": 1, "id": "call_b", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "太原"}'}}]),
    {**delta("tool_calls", content=None, reasoning_content=None), "usage": USAGE},
)


class TestAssemble:
    def test_两个并行工具调用拼回非流式形状(self):
        msg, usage = _assemble_stream(iter(TWO_CALLS))
        assert msg == {
            "role": "assistant",
            "content": None,
            "reasoning_content": None,
            "tool_calls": [
                {"id": "call_a", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "杭州"}'}},
                {"id": "call_b", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "太原"}'}},
            ],
        }
        assert usage == USAGE

    def test_id与name取首次非空(self):
        # 有的服务商每块都重发 id / name：后到的值不该盖掉先到的（施工图 §三「取首次非空」）
        msg, _ = _assemble_stream(iter(sse(
            delta(tool_calls=[{"index": 0, "id": "call_a", "type": "function", "function": {"name": "f", "arguments": "{"}}]),
            delta("tool_calls", tool_calls=[{"index": 0, "id": "call_x", "type": "x", "function": {"name": "g", "arguments": "}"}}]),
        )))
        assert msg["tool_calls"] == [{"id": "call_a", "type": "function", "function": {"name": "f", "arguments": "{}"}}]

    def test_正文跨块累加(self):
        msg, _ = _assemble_stream(iter(sse(
            delta(role="assistant", content="西湖", reasoning_content=None),
            delta(content="在杭州", reasoning_content=None),
            delta("stop", content="。", reasoning_content=None),
        )))
        assert msg == {"role": "assistant", "content": "西湖在杭州。", "reasoning_content": None}

    def test_只有思考内容(self):
        msg, _ = _assemble_stream(iter(sse(
            delta(role="assistant", content=None, reasoning_content="先想"),
            delta("stop", content=None, reasoning_content="一想"),
        )))
        assert msg == {"role": "assistant", "content": None, "reasoning_content": "先想一想"}

    def test_工具键全程null与只有空串(self):
        # 真实流的末块是 content: "" + tool_calls: null；非流式同题是 content: null、tool_calls: null
        msg, _ = _assemble_stream(iter(sse(
            delta(role="assistant", content=None, tool_calls=None),
            delta("stop", content="", tool_calls=None),
        )))
        assert msg == {"role": "assistant", "content": None, "tool_calls": None}

    def test_没出现过思考键就不带(self):
        msg, _ = _assemble_stream(iter(sse(delta("stop", role="assistant", content="好"))))
        assert msg == {"role": "assistant", "content": "好"}

    def test_usage只在末块也拿得到(self):
        _, usage = _assemble_stream(iter(sse(delta("stop", content="好"), {"choices": [], "usage": USAGE})))
        assert usage == USAGE

    def test_注释行与空行跳过(self):
        lines = [": keep-alive\n", "\n", *sse(delta("stop", content="好"))]
        assert _assemble_stream(iter(lines))[0]["content"] == "好"

    def test_只有finish_reason没有DONE也算正常结束(self):
        assert _assemble_stream(iter(sse(delta("stop", content="好"), done=False)))[0]["content"] == "好"

    def test_只有DONE没有finish_reason也算正常结束(self):
        assert _assemble_stream(iter(sse(delta(content="好"))))[0]["content"] == "好"

    def test_断流可重试且不交半截回复(self):
        with pytest.raises(LLMError) as info:
            _assemble_stream(iter(sse(delta(content="半截"), done=False)))
        assert info.value.retryable is True
        assert "中途断开" in str(info.value)

    @pytest.mark.parametrize("code,retryable", [(429, True), (503, True), (400, False), ("bad", False)])
    def test_流里的error按状态码分类(self, code, retryable):
        with pytest.raises(LLMError) as info:
            _assemble_stream(iter(sse(delta(content="x"), {"error": {"code": code, "message": "boom"}})))
        assert info.value.retryable is retryable

    def test_坏行不可重试(self):
        with pytest.raises(LLMError) as info:
            _assemble_stream(iter(["data: {不是json\n"]))
        assert info.value.retryable is False


# ---------------------------------------------------------------------------
# 真实序列对照（2026-10-15 探针：同一请求非流式 / 流式各发一次，原样存下）
# ---------------------------------------------------------------------------

PROBE = Path(__file__).parent / "fixtures" / "llm_stream_probe"
PROBE_CASES = ["tools2-default", "tools2-high", "text-default", "text-high"]


def _probe(case: str) -> tuple[dict, dict | None, dict]:
    msg, usage = _assemble_stream(iter((PROBE / f"{case}-stream.txt").read_text(encoding="utf-8").splitlines(keepends=True)))
    return msg, usage, json.loads((PROBE / f"{case}-json.txt").read_text(encoding="utf-8"))


def _shape(v: object) -> str:
    return "null" if v is None else type(v).__name__


class TestRealProbe:
    def test_两个并行工具调用逐字段(self):
        msg, usage, _ = _probe("tools2-default")
        assert msg == {
            "role": "assistant", "content": None, "reasoning_content": None,
            "tool_calls": [
                {"id": "get_weather-1792050056337294000-878", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "杭州"}'}},
                {"id": "get_weather-1792050056337493000-879", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "太原"}'}},
            ],
        }
        assert usage == {"completion_tokens": 33, "total_tokens": 163, "prompt_tokens": 58,
                         "completion_tokens_details": {"reasoning_tokens": 72}}

    @pytest.mark.parametrize("case", PROBE_CASES)
    def test_与同题非流式同形(self, case):
        """模型每次生成的文字不同，逐字比不了；比的是下游会读到的一切：键集合、每个键的空 / 非空形状、
        工具调用的键集合 / 名字 / 解析后的参数、usage 的键集合与 prompt_tokens。"""
        msg, usage, body = _probe(case)
        ref = body["choices"][0]["message"]
        assert sorted(msg) == sorted(ref)
        assert msg["role"] == ref["role"]
        for key in ("content", "reasoning_content"):
            if (case, key) == ("tools2-high", "reasoning_content"):
                # 代理行为不是拼装问题：这一题非流式带了思考内容，流式一块思考都没发
                assert (_shape(msg[key]), _shape(ref[key])) == ("null", "str")
            else:
                assert _shape(msg[key]) == _shape(ref[key])
        if ref["tool_calls"] is None:
            assert msg["tool_calls"] is None
        else:
            assert [sorted(c) for c in msg["tool_calls"]] == [sorted(c) for c in ref["tool_calls"]]
            assert [(c["type"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in msg["tool_calls"]] \
                == [(c["type"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in ref["tool_calls"]]
        assert sorted(usage) == sorted(body["usage"])
        assert usage["prompt_tokens"] == body["usage"]["prompt_tokens"]

    def test_思考与正文分块到达各自拼接(self):
        msg, _, _ = _probe("text-high")
        assert msg["reasoning_content"].startswith("Hangzhou's West Lake, a renowned scenic area")
        assert msg["reasoning_content"].endswith("selecting the best aspects of each draft.\n\n\n")
        assert msg["content"] == ("1. 杭州西湖位于浙江省杭州市，是中国著名的风景名胜与世界文化遗产，自古便享有“人间天堂”的美誉。\n"
                                  "2. 它以秀丽的湖光山色与“西湖十景”著称，堤岛相连、山水相依，完美体现了中国古典美学中人与自然融为一体的造园艺术。\n"
                                  "3. 这里不仅留下了苏轼、白居易等历代文人墨客的千古诗篇，更流传着《白蛇传》等动人传说，是江南诗意与东方浪漫的极致象征。")


# ---------------------------------------------------------------------------
# chat_complete：请求体与分流
# ---------------------------------------------------------------------------


class _StreamResp:
    def __init__(self, lines: list[str], ctype: str = "text/event-stream", boom: BaseException | None = None) -> None:
        self.headers = {"Content-Type": ctype}
        self._lines, self._boom = lines, boom

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for line in self._lines:
            yield line.encode("utf-8")
        if self._boom is not None:
            raise self._boom

    def read(self) -> bytes:
        return "".join(self._lines).encode("utf-8")


@pytest.fixture
def no_egress(monkeypatch):
    monkeypatch.setattr(llm_mod, "assert_egress_boundary", lambda *a, **k: None)


def test_请求体一律带stream与include_usage(monkeypatch, no_egress):
    sent: list[dict] = []

    def fake(request, timeout=None):
        sent.append(json.loads(request.data.decode("utf-8")))
        return _StreamResp(TWO_CALLS)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    msg = chat_complete([{"role": "user", "content": "查天气"}], config=CFG)
    assert sent[0]["stream"] is True and sent[0]["stream_options"] == {"include_usage": True}
    assert [c["id"] for c in msg["tool_calls"]] == ["call_a", "call_b"]
    assert llm_mod._LAST_USAGE.get() == USAGE      # D56 读数不回退


def test_application_json走旧路径(monkeypatch, no_egress):
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "好"}}], "usage": USAGE})
    monkeypatch.setattr(urllib.request, "urlopen", lambda r, timeout=None: _StreamResp([body], ctype="application/json"))
    assert chat_complete([{"role": "user", "content": "x"}], config=CFG) == {"role": "assistant", "content": "好"}
    assert llm_mod._LAST_USAGE.get() == USAGE


@pytest.mark.parametrize("boom", [TimeoutError("timed out"), http.client.IncompleteRead(b"")])
def test_读到一半断开可重试(monkeypatch, no_egress, boom):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda r, timeout=None: _StreamResp(sse(delta(content="半"), done=False), boom=boom))
    with pytest.raises(LLMError) as info:
        chat_complete([{"role": "user", "content": "x"}], config=CFG)
    assert info.value.retryable is True


def test_真socket_两段数据之间超时可重试(no_egress):
    """流式下 request_timeout_s 是「两段数据之间最长多久」：先发一块、再挂住超过超时 → 可重试超时。"""
    release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(sse(delta(role="assistant", content="先到的一块"), done=False)[0].encode())
            self.wfile.flush()
            release.wait(5)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.handle_error = lambda *args: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = LLMConfig(base_url=f"http://127.0.0.1:{server.server_address[1]}/v1", model="m", api_key="k",
                    request_timeout_s=1)
    try:
        with pytest.raises(LLMError) as info:
            chat_complete([{"role": "user", "content": "x"}], config=cfg)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
    assert info.value.retryable is True
