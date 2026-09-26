"""协议端到端（Spec 9 §7.1 TP-*，PR3）。

子进程 + 本地假端点 + **测试侧 import 钩子**注入测试工具（生产代码零钩子）。
假端点按 §7 的规则校验「每条带 tool_calls 的 assistant，紧随其后的连续 tool 消息恰好覆盖
其全部 id」，否则返回 400 —— 配对修复的真实检验就在这里。
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pipeline.agent import protocol as proto

REPO = Path(__file__).resolve().parent.parent
KEY = "sk-proto-test"

# 测试侧注入的工具（sitecustomize 里注册，生产代码零钩子）
_TOOL_HOOK = '''
import os, subprocess, sys

from pipeline import paths

paths.ROOT = __import__("pathlib").Path(os.environ["AVA_PROTO_ROOT"])
paths.DATA = paths.ROOT / "data"


def _install():
    from pipeline.agent.tools import TOOL_SCHEMAS, _TOOL_IMPLS

    def test_writer(args, ctx):
        """TP-1：三种写者都往 fd 1 与 stderr 里塞东西。"""
        print("PRINT-TO-STDOUT")
        os.write(1, b"OSWRITE-TO-FD1\\n")
        subprocess.run(["echo", "ECHO-VIA-SUBPROCESS"], check=False)
        print("PRINT-TO-STDERR", file=sys.stderr)
        return {"ok": True, "wrote": True}

    def test_ping(args, ctx):
        return {"ok": True, "pong": True}

    def test_slow(args, ctx):
        time.sleep(float(args.get("seconds") or 3))
        return {"ok": True, "slept": True}

    import time  # noqa: E402

    def test_stdin_child(args, ctx):
        """TP-13：真子进程读 stdin。fd 0 已换成 /dev/null → 立刻得 EOF（读到 0 字节）。"""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"],
                capture_output=True, text=True, timeout=float(args.get("seconds") or 5),
            )
        except subprocess.TimeoutExpired:
            return {"ok": True, "stdin_bytes": -1}  # 卡住了 = fd 0 没换成 /dev/null
        return {"ok": True, "stdin_bytes": int((proc.stdout or "-1").strip())}

    for name, impl, side in (("test_writer", test_writer, False), ("test_ping", test_ping, False),
                             ("test_slow", test_slow, True), ("test_stdin_child", test_stdin_child, True)):
        TOOL_SCHEMAS[name] = {
            "name": name, "side_effect": side, "adr": "test",
            "description": f"测试用 {name}",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }
        _TOOL_IMPLS[name] = impl

    if os.environ.get("AVA_PROTO_STOP_POINT"):
        # TP-12：造一个挂起的停机点，否则 `stop_points.items` 恒为空集，
        # 键集合断言一个元素都遍历不到（MUT-32 就是这样漏网的）。
        from pipeline import approvals

        class _Artifact:
            path = "data/episodes/01-smoke/02-script.draft.md"

        class _StopPoint:
            approval_id = "ap-test-1"
            type = "job"
            created_at = "2026-01-01T00:00:00Z"
            note = "测试用挂起停机点"
            artifacts = (_Artifact(),)
            options = ("approve", "reject")

        approvals.list_pending = lambda ep_dir: [_StopPoint()]


_install()
'''


# ---------------------------------------------------------------------------
# 假端点（校验配对）
# ---------------------------------------------------------------------------


class FakeEndpoint:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.replies: list[dict] = []
        self.bad_pairings = 0
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # 静音
                return

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw.decode("utf-8"))
                endpoint.requests.append(body)
                if not _pairing_ok(body.get("messages") or []):
                    endpoint.bad_pairings += 1
                    payload = json.dumps({"error": {"message": "tool messages not paired"}}).encode()
                    self.send_response(400)
                else:
                    index = min(len(endpoint.requests) - 1, max(0, len(endpoint.replies) - 1))
                    message = endpoint.replies[index] if endpoint.replies else {"role": "assistant", "content": "好"}
                    payload = json.dumps({"choices": [{"message": message}]}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _pairing_ok(messages: list[dict]) -> bool:
    index = 0
    while index < len(messages):
        calls = messages[index].get("tool_calls") or []
        if calls:
            ids = [str(c.get("id")) for c in calls]
            following: list[str] = []
            cursor = index + 1
            while cursor < len(messages) and messages[cursor].get("role") == "tool":
                following.append(str(messages[cursor].get("tool_call_id")))
                cursor += 1
            if following != ids:
                return False
            index = cursor
            continue
        index += 1
    return True


@pytest.fixture
def endpoint():
    server = FakeEndpoint()
    try:
        yield server
    finally:
        server.close()


# ---------------------------------------------------------------------------
# 子进程驱动
# ---------------------------------------------------------------------------


class Protocol:
    def __init__(self, root: Path, endpoint: FakeEndpoint, episode: str = "01-smoke",
                 extra: list[str] | None = None, tmp: Path | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.endpoint = endpoint
        hook_dir = (tmp or root / "hook")
        hook_dir.mkdir(parents=True, exist_ok=True)
        (hook_dir / "sitecustomize.py").write_text(_TOOL_HOOK, encoding="utf-8")
        child_env = dict(os.environ)
        child_env["PYTHONPATH"] = os.pathsep.join([str(REPO), str(hook_dir)])
        child_env["AVA_PROTO_ROOT"] = str(root)
        child_env["AVA_TEST_KEY"] = KEY
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        child_env.pop("CPA_API_KEY", None)
        child_env.update(env or {})
        env = child_env
        argv = [sys.executable, "-m", "pipeline.agent.protocol", episode]
        argv.extend(extra or [])
        self.proc = subprocess.Popen(
            argv, cwd=REPO, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.frames: list[dict] = []
        # 按**类型**各记一个已返回计数：帧流里不同帧会交错（assistant 与 turn_finished 同时到），
        # 全局游标要么吞掉前一类、要么回头取到启动期那帧；按类型计数才是稳的。
        self._seen: dict[str, int] = {}
        self.stderr_lines: list[str] = []
        self._frames: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self._frames.put(line)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_lines.append(line)

    # ---- 帧收发 ----

    def send(self, frame: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, text: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(text)
        self.proc.stdin.flush()

    def next_frame(self, timeout: float = 30.0) -> dict:
        raw = self._frames.get(timeout=timeout)
        frame = json.loads(raw)
        self.frames.append(frame)
        return frame

    def wait_for(self, kind: str, timeout: float = 60.0) -> dict:
        """按类型等一帧。**先看已读过的帧**——否则「等 turn_finished 时顺手读掉的
        assistant 帧」会在后续 wait_for("assistant") 里凭空消失（帧流是有序的，不能只看新的）。"""
        want = self._seen.get(kind, 0)
        end = time.time() + timeout
        while True:
            matches = [f for f in self.frames if f.get("t") == kind]
            if len(matches) > want:
                self._seen[kind] = want + 1
                return matches[want]
            if time.time() >= end:
                break
            try:
                self.next_frame(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                break
        dump = Path(f"/tmp/proto_fail_{self.proc.pid}.log")
        dump.write_text("".join(self.stderr_lines), encoding="utf-8")
        raise AssertionError(
            f"等不到 {kind}（子进程 stderr 全文 → {dump}）；已收到 {[f.get('t') for f in self.frames]}"
            f"；want={want} seen={self._seen}；子进程 rc={self.proc.poll()}"
            f"；stderr 尾部={'|'.join(self.stderr_lines[-40:])[-3000:]}"
        )

    def wait_for_ready(self, timeout: float = 60.0) -> dict:
        """等到 ready，并**消费掉回合外那帧 `stop_points`**（它的 turn_id 是 null）。"""
        ready = self.wait_for("ready", timeout)
        self.wait_for("stop_points", timeout=10)
        return ready

    def kinds(self) -> list[str]:
        return [f.get("t") for f in self.frames]

    def close_input(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()

    def finish(self, timeout: float = 30.0) -> int:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
            return -9

    def drain(self, timeout: float = 5.0) -> None:
        """把还没读的帧读完（`kinds()` 只反映读过的帧，不排空会漏掉 bye）。"""
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.next_frame(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                return

    def shutdown(self) -> int:
        self.send({"t": "shutdown"})
        rc = self.finish()
        self.drain(0.5)
        return rc


@pytest.fixture
def world(tmp_path: Path, endpoint: FakeEndpoint):
    """假仓库根：config/agent.json 指向假端点，data/ 齐备，工具表带测试工具。"""
    root = tmp_path / "repo"
    (root / "config" / "agent").mkdir(parents=True)
    (root / "data" / "episodes" / "01-smoke").mkdir(parents=True)
    (root / "data" / "library").mkdir(parents=True)
    (root / "config" / "agent.json").write_text(
        json.dumps({"base_url": endpoint.url, "model": "mock", "api_key_env": "AVA_TEST_KEY"}),
        encoding="utf-8",
    )
    (root / "config" / "agent" / "tools.json").write_text(
        json.dumps({"creative": ["read_artifact", "test_ping", "test_writer", "test_slow",
                                 "test_stdin_child"],
                    "pipeline": ["read_artifact"], "asset": [], "idea": ["read_artifact"]}),
        encoding="utf-8",
    )
    episode = root / "data" / "episodes" / "01-smoke"
    (episode / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    return root, episode


def tool_call(name: str, args: dict | None = None, call_id: str = "c1") -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


# ---------------------------------------------------------------------------
# TP-1：fd 隔离
# ---------------------------------------------------------------------------


def test_tp1_fd_isolation_and_forged_frames(world, endpoint, tmp_path) -> None:
    """TP-1：直写 fd 1 的内容出现在 stderr；stdout 每行都是合法出站帧；伪造 answer 不改挂起请求。"""
    root, episode = world
    endpoint.replies = [tool_call("test_writer"), {"role": "assistant", "content": "写完了"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        ready = proto_proc.wait_for_ready()
        assert ready["llm"] == "ok"
        proto_proc.send({"t": "user_message", "text": "调 test_writer", "rid": "r1"})
        started = proto_proc.wait_for("turn_started")
        assert started["rid"] == "r1"
        proto_proc.wait_for("turn_finished")
        # 伪造的 answer 帧：进程仍活着，但不会当成参数
        proto_proc.send({"t": "answer", "request_id": "不存在", "decision": "approve", "feedback": None})
        err = proto_proc.wait_for("error")
        assert err["code"] == "E_UNKNOWN_REQUEST"
        proto_proc.shutdown()
    finally:
        rc = proto_proc.finish()
    assert rc == 0

    assert endpoint.bad_pairings == 0
    # os.write(1) 与子进程 echo 的输出到了 stderr（dup2(2,1)），没污染协议流
    stderr = "".join(proto_proc.stderr_lines)
    assert "OSWRITE-TO-FD1" in stderr
    assert "ECHO-VIA-SUBPROCESS" in stderr
    assert "PRINT-TO-STDERR" in stderr
    # print() 走 sys.stdout 替身 → log 帧
    logs = [f for f in proto_proc.frames if f.get("t") == "log"]
    assert any("PRINT-TO-STDOUT" in f["text"] for f in logs)
    assert all(f["stream"] in ("stdout", "stderr") for f in logs)
    # 每一行都能解析且 t 属于出站闭集
    outbound = {"ready", "history", "turn_started", "assistant", "tool", "request", "request_closed",
                "command_result", "stop_points", "turn_finished", "log", "notice", "error", "bye"}
    assert set(proto_proc.kinds()) <= outbound


# ---------------------------------------------------------------------------
# TP-2 / TP-3：正常一轮与工具卡
# ---------------------------------------------------------------------------


def test_tp2_normal_turn_frame_sequence(world, endpoint, tmp_path) -> None:
    """TP-2：正常一轮的帧序列与计数。"""
    root, episode = world
    endpoint.replies = [{"role": "assistant", "content": "收到"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "在吗"})
        proto_proc.wait_for("turn_finished")
        answer = proto_proc.wait_for("assistant")
        assert answer["kind"] == "answer" and answer["text"] == "收到"
        stop = proto_proc.wait_for("stop_points")
        assert stop["turn_id"] == answer["turn_id"]
        assert stop["items"] == [], "没有挂起停机点时 items 必须为空集（可空那一半）"
        assert proto_proc.shutdown() == 0
    finally:
        proto_proc.proc.kill()

    kinds = proto_proc.kinds()
    assert kinds[0] == "ready" and kinds[-1] == "bye", kinds
    # 启动期那帧 stop_points 的 turn_id 为 null（§4.7 回合外帧），不算本轮
    turn_frames = [f["t"] for f in proto_proc.frames
                   if f.get("t") in ("turn_started", "assistant", "turn_finished", "stop_points")
                   and f.get("turn_id")]
    assert turn_frames == ["turn_started", "assistant", "turn_finished", "stop_points"], kinds
    finished = [f for f in proto_proc.frames if f.get("t") == "turn_finished"][0]
    assert finished["stopped"] == "done" and finished["llm_calls"] == 1
    assert finished["tool_calls"] == 0 and finished["wrapup"] == "none"


def test_tp3_tool_card_approve_and_reject_with_feedback(world, endpoint, tmp_path) -> None:
    """TP-3：工具卡批准 → 执行；拒绝附 feedback → tool 消息含反馈原文。"""
    root, episode = world
    endpoint.replies = [
        tool_call("test_slow", {"seconds": 0}, call_id="w1"),
        {"role": "assistant", "content": "好"},
    ]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "跑慢工具"})
        proto_proc.wait_for("turn_started")
        request = proto_proc.wait_for("request")
        assert request["kind"] == "tool_call" and request["feedback_allowed"] is True
        proto_proc.send({"t": "answer", "request_id": request["request_id"],
                         "decision": "reject", "feedback": "这段先别写"})
        closed = proto_proc.wait_for("request_closed")
        assert closed["reason"] == "answered" and closed["decision"] == "reject"
        proto_proc.wait_for("turn_finished")
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0

    fed = endpoint.requests[1]["messages"][-1]["content"]
    assert "这段先别写" in fed, "拒因反馈要原文回喂"
    tools = [f for f in proto_proc.frames if f.get("t") == "tool"]
    assert [f["phase"] for f in tools] == ["start", "end"]
    assert tools[0]["summary"] and tools[0]["duplicate"] is False
    assert tools[1]["ok"] is False and "这段先别写" in tools[1]["observation"]


def test_tp3b_tool_card_approve_runs_the_tool(world, endpoint, tmp_path) -> None:
    """TP-3（批准那一半）：执行成功 → tool 帧 ok 为真、observation 为 null。"""
    root, episode = world
    endpoint.replies = [tool_call("test_slow", {"seconds": 0}), {"role": "assistant", "content": "好"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "跑慢工具"})
        request = proto_proc.wait_for("request")
        proto_proc.send({"t": "answer", "request_id": request["request_id"],
                         "decision": "approve", "feedback": None})
        proto_proc.wait_for("turn_finished")
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0
    end = [f for f in proto_proc.frames if f.get("t") == "tool" and f["phase"] == "end"][0]
    assert end["ok"] is True and end["observation"] is None


# ---------------------------------------------------------------------------
# TP-4 / TP-4b：interrupt
# ---------------------------------------------------------------------------


def test_tp4_interrupt_matches_turn_id(world, endpoint, tmp_path) -> None:
    """TP-4：正确 turn_id 的 interrupt → 2 s 内 interrupted 且收尾带 tool_choice:"none"；错的 → E_STALE。"""
    root, episode = world
    endpoint.replies = [tool_call("test_slow", {"seconds": 0}), {"role": "assistant", "content": "收尾"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "先慢慢来"})
        started = proto_proc.wait_for("turn_started")
        proto_proc.send({"t": "interrupt", "turn_id": "deadbeef"})
        stale = proto_proc.wait_for("error")
        assert stale["code"] == "E_STALE"

        # 真的中断：让模型阻塞在第二次请求上不好构造，改用「等卡时中断」
        request = proto_proc.wait_for("request")
        began = time.time()
        proto_proc.send({"t": "interrupt", "turn_id": started["turn_id"]})
        finished = proto_proc.wait_for("turn_finished", timeout=10)
        assert time.time() - began < 5, "中断必须 2 s 量级内浮出"
        assert finished["stopped"] == "interrupted"
        assert finished["wrapup"] == "ok"
        assert request["request_id"]  # 卡确实出现过
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0
    assert endpoint.requests[-1]["tool_choice"] == "none", "收尾调用必须带 tool_choice:none"


def test_tp4b_idle_interrupt_is_notice_not_exit(world, endpoint, tmp_path) -> None:
    """TP-4b：空闲时进程收 SIGINT → 进程存活、发 notice（不退出）。"""
    root, episode = world
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        os.kill(proto_proc.proc.pid, signal.SIGINT)
        # 空闲态的 SIGINT：main 循环不在回合里，Python 默认处理器打断的是 reader/main 的等待
        time.sleep(1.0)
        proto_proc.send({"t": "user_message", "text": "还在吗"})
        proto_proc.wait_for("turn_started", timeout=20)
        proto_proc.wait_for("turn_finished", timeout=30)
        proto_proc.shutdown()
        assert proto_proc.finish() == 0, "空闲时的一次 SIGINT 不该弄死进程"
    finally:
        proto_proc.proc.kill()


# ---------------------------------------------------------------------------
# TP-5 / TP-6 / TP-7：EOF 与错误码
# ---------------------------------------------------------------------------


def test_tp5_eof_while_waiting_voids_and_exits_zero(world, endpoint, tmp_path) -> None:
    """TP-5：等答复时关闭 stdin → 请求作废、工具未执行、退出 0、全程无批准。"""
    root, episode = world
    endpoint.replies = [tool_call("test_slow", {"seconds": 0}), {"role": "assistant", "content": "收尾"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "跑慢工具"})
        proto_proc.wait_for("request")
        proto_proc.close_input()
        closed = proto_proc.wait_for("request_closed", timeout=20)
        assert closed["reason"] == "voided"
        rc = proto_proc.finish(timeout=30)
        proto_proc.drain(1.0)
    finally:
        proto_proc.proc.kill()
    assert rc == 0
    assert proto_proc.frames[-1]["t"] == "bye" and proto_proc.frames[-1]["reason"] == "eof"
    assert "slept" not in json.dumps(endpoint.requests[-1]["messages"], ensure_ascii=False)


def test_tp6_busy_and_bad_frames(world, endpoint, tmp_path) -> None:
    """TP-6：回合中再发 user_message → E_BUSY；多键帧 → E_BAD_REQUEST；进程存活。"""
    root, episode = world
    endpoint.replies = [{"role": "assistant", "content": "好"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "甲"})
        proto_proc.send({"t": "user_message", "text": "乙"})
        busy = proto_proc.wait_for("error")
        assert busy["code"] in ("E_BUSY", "E_NOT_READY")
        proto_proc.send({"t": "user_message", "text": "丙", "extra": 1})
        proto_proc.send_raw("{ 这不是 json }\n")
        codes = set()
        for _ in range(2):
            codes.add(proto_proc.wait_for("error")["code"])
        assert "E_BAD_REQUEST" in codes
        proto_proc.wait_for("turn_finished")
        proto_proc.send({"t": "user_message", "text": "丁"})
        proto_proc.wait_for("turn_started")
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0


def test_tp7_unknown_closed_and_wrong_decision(world, endpoint, tmp_path) -> None:
    """TP-7：未知请求号 / 已关闭请求 / 对检查点答 approve → 三种错误码。"""
    root, episode = world
    endpoint.replies = [tool_call("test_slow", {"seconds": 0}), {"role": "assistant", "content": "好"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "跑慢工具"})
        request = proto_proc.wait_for("request")
        proto_proc.send({"t": "answer", "request_id": "nope", "decision": "approve", "feedback": None})
        assert proto_proc.wait_for("error")["code"] == "E_UNKNOWN_REQUEST"

        proto_proc.send({"t": "answer", "request_id": request["request_id"],
                         "decision": "continue", "feedback": None})
        bad = proto_proc.wait_for("error")
        assert bad["code"] == "E_BAD_REQUEST", "decision 必须在 options 里"

        proto_proc.send({"t": "answer", "request_id": request["request_id"],
                         "decision": "approve", "feedback": None})
        proto_proc.wait_for("request_closed")
        proto_proc.send({"t": "answer", "request_id": request["request_id"],
                         "decision": "approve", "feedback": None})
        closed = proto_proc.wait_for("error")
        assert closed["code"] == "E_REQUEST_CLOSED", "重复答复已关闭的号：必须是这个码，不是「没见过」"
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0


# ---------------------------------------------------------------------------
# TP-9：期租约
# ---------------------------------------------------------------------------


def test_tp9_second_protocol_process_exits_3_before_ready(world, endpoint, tmp_path) -> None:
    """TP-9：同期第二个协议进程（带/不带 --continue）→ ready 之前以 error 帧说明并退出 3。"""
    root, episode = world
    first = Protocol(root, endpoint, tmp=tmp_path / "a")
    try:
        first.wait_for_ready()
        for extra in ([], ["--continue"]):
            second = Protocol(root, endpoint, extra=extra, tmp=tmp_path / ("b" + str(len(extra))))
            try:
                err = second.wait_for("error", timeout=20)
                assert err["code"] == "E_SESSION_LOCKED"
                assert "ready" not in second.kinds(), "拿不到租约就不许发 ready"
                assert second.finish(timeout=20) == 3
            finally:
                second.proc.kill()
        first.shutdown()
    finally:
        first.proc.kill()


# ---------------------------------------------------------------------------
# TP-12 / TP-15：stop_points
# ---------------------------------------------------------------------------


def test_tp12_tp15_stop_points_frames(world, endpoint, tmp_path) -> None:
    """TP-12/TP-15：对象键集合精确等于 §3.1；每回合恰好一帧、带本回合 turn_id、可空。

    夹具**必须**造出挂起的停机点：没有它 `items` 恒为空集，键集合断言一个元素都遍历
    不到，变异加上 `mtime_ns` 也照样红不了（MUT-32 就这么漏的）。
    """
    root, episode = world
    expected = {"approval_id", "type", "created_at", "artifacts", "options", "note", "answer_via"}
    endpoint.replies = [{"role": "assistant", "content": "好"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path, env={"AVA_PROTO_STOP_POINT": "1"})
    try:
        ready = proto_proc.wait_for_ready()
        assert set(ready) == {
            "v", "seq", "sid", "t", "episode", "scope", "continue_status", "llm", "degrade_reason",
            "code_freeze_ok", "history_count", "session_bytes", "other_sessions",
        }
        first = [f for f in proto_proc.frames if f.get("t") == "stop_points" and not f.get("turn_id")][0]
        assert len(first["items"]) == 1, "世界里必须有挂起停机点，否则下面的键集合断言是空转"

        for _ in range(2):
            proto_proc.send({"t": "user_message", "text": "在吗"})
            finished = proto_proc.wait_for("turn_finished")
            stop = proto_proc.wait_for("stop_points")
            assert stop["turn_id"] == finished["turn_id"]
            assert len(stop["items"]) == 1
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0

    stops = [f for f in proto_proc.frames if f.get("t") == "stop_points"]
    assert len(stops) == 3, "ready 一帧 + 每回合各一帧"
    items = [item for frame in stops for item in frame["items"]]
    assert len(items) == 3, "每帧各带一个停机点"
    assert all(set(item) == expected for item in items), items
    assert stops[0]["turn_id"] is None and all(f["turn_id"] for f in stops[1:])


# ---------------------------------------------------------------------------
# TP-8：崩溃恢复
# ---------------------------------------------------------------------------


def test_tp8_sigkill_then_continue_resumes_with_history(world, endpoint, tmp_path) -> None:
    """TP-8：等卡时 SIGKILL → `--continue` → resumed、history 帧按 origin 标 role、假端点接受。"""
    root, episode = world
    endpoint.replies = [tool_call("test_slow", {"seconds": 0}), {"role": "assistant", "content": "好"}]
    first = Protocol(root, endpoint, tmp=tmp_path / "a")
    try:
        first.wait_for_ready()
        first.send({"t": "user_message", "text": "跑慢工具"})
        first.wait_for("request")
        os.kill(first.proc.pid, signal.SIGKILL)
        first.finish(timeout=10)
    finally:
        first.proc.kill()
    assert (episode / "session.jsonl").exists()

    second = Protocol(root, endpoint, extra=["--continue"], tmp=tmp_path / "b")
    try:
        ready = second.wait_for_ready()
        assert ready["continue_status"] == "resumed"
        assert ready["history_count"] >= 1
        history = [f for f in second.frames if f.get("t") == "history"]
        assert history and {f["role"] for f in history} <= {"user", "assistant", "tool", "system_note"}
        second.send({"t": "user_message", "text": "继续"})
        second.wait_for("turn_finished", timeout=60)
        second.shutdown()
    finally:
        second.proc.kill()
    assert endpoint.bad_pairings == 0, "修复后的历史必须过配对校验（不是 400）"


# ---------------------------------------------------------------------------
# TP-10：memory_ack
# ---------------------------------------------------------------------------


def test_tp10_memory_ack_command(world, endpoint, tmp_path) -> None:
    """TP-10：`memory_ack` 命令 → `command_result` 带 ack 原文；无待确认时 ok=False 也如实回传。"""
    root, episode = world
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "command", "name": "memory_ack", "arg": None, "rid": "m1"})
        result = proto_proc.wait_for("command_result", timeout=30)
        assert result["name"] == "memory_ack"
        assert result["rid"] == "m1"
        assert isinstance(result["text"], str)
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0


# ---------------------------------------------------------------------------
# TP-11：SIGTERM
# ---------------------------------------------------------------------------


def test_tp11_sigterm_skips_wrapup_and_exits_zero(world, endpoint, tmp_path) -> None:
    """TP-11：回合中 SIGTERM → wrapup == "skipped"、退出 0。"""
    root, episode = world
    endpoint.replies = [{"role": "assistant", "content": "好"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "甲"})
        proto_proc.wait_for("turn_started")
        os.kill(proto_proc.proc.pid, signal.SIGTERM)
        finished = proto_proc.wait_for("turn_finished", timeout=30)
        assert finished["stopped"] == "interrupted"
        assert finished["wrapup"] == "skipped"
        rc = proto_proc.finish(timeout=20)
    finally:
        proto_proc.proc.kill()
    assert rc == 0


# ---------------------------------------------------------------------------
# TP-13：job 子进程读 stdin
# ---------------------------------------------------------------------------


def test_tp13_job_child_gets_eof_and_frames_still_processed(world, endpoint, tmp_path) -> None:
    """TP-13：真子进程读 stdin 得 EOF（fd 0 已换成 /dev/null）；同时 host 的帧照常被处理（E_BUSY）。

    不能用进程内的假工具：那样根本没有读 stdin 的子进程，fd 0 一个字都没测到（MUT-17 漏网）。
    """
    root, episode = world
    endpoint.replies = [tool_call("test_stdin_child", {"seconds": 5}), {"role": "assistant", "content": "好"}]
    proto_proc = Protocol(root, endpoint, tmp=tmp_path)
    try:
        proto_proc.wait_for_ready()
        proto_proc.send({"t": "user_message", "text": "跑读 stdin 的子进程"})
        request = proto_proc.wait_for("request", timeout=30)
        proto_proc.send({"t": "answer", "request_id": request["request_id"],
                         "decision": "approve", "feedback": None})
        proto_proc.send({"t": "user_message", "text": "抢话"})
        busy = proto_proc.wait_for("error", timeout=20)
        assert busy["code"] == "E_BUSY"
        proto_proc.wait_for("turn_finished", timeout=60)
        proto_proc.shutdown()
    finally:
        assert proto_proc.finish() == 0
    # 0 = 立刻 EOF；-1 = 卡到超时；其他 = 把 host 的帧读走了
    payloads = [
        json.loads(m["content"])
        for req in endpoint.requests for m in req["messages"] if m.get("role") == "tool"
    ]
    assert payloads, "工具结果没进模型请求"
    assert payloads[-1]["result"]["stdin_bytes"] == 0, \
        f"子进程没拿到 EOF（-1 = 卡到超时）：{payloads[-1]}"


# ---------------------------------------------------------------------------
# TP-14：大帧与写线程
# ---------------------------------------------------------------------------


def test_tp14_large_frame_survives_interrupt(tmp_path: Path) -> None:
    """TP-14：读端慢速读取时发大帧（≥400 KB），写出途中从**另一个线程**插入第二帧
    → 每行都能解析，没有残帧。

    不赌时长：读端收到两帧才收工（之前靠 `sleep(1.0)`，全量套件下机器一忙就读不完，
    给出假的「已杀死」）。
    """
    read_fd, write_fd = os.pipe()
    writer = proto.FrameWriter(write_fd)
    big = "汉" * 400_000
    done = threading.Event()
    state: dict = {}

    def reader() -> None:
        collected = b""
        try:
            while collected.count(b"\n") < 2:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                collected += chunk
                time.sleep(0.05)  # 故意慢读：把「写出途中」那个窗口撑开
            state["collected"] = collected
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常必须带回主线程，否则假绿
            state["error"] = exc
        finally:
            done.set()

    def kick() -> None:
        time.sleep(0.1)
        writer.send({"t": "bye", "reason": "test"})  # 与主线程的写入并发

    threading.Thread(target=reader, daemon=True).start()
    writer.send({"t": "log", "stream": "stdout", "text": big})
    kicker = threading.Thread(target=kick, daemon=True)
    kicker.start()
    assert done.wait(15.0), "读端没在两帧内收齐（writer 是不是把残帧写出来了？）"
    kicker.join(5.0)
    writer.close()
    os.close(read_fd)

    assert "error" not in state, f"读端异常：{state.get('error')!r}"
    lines = state["collected"].split(b"\n")
    assert lines[-1] == b"", "最后一行为空 = 没有残帧"
    for line in lines[:-1]:
        json.loads(line.decode("utf-8"))  # 主线程直写（MUT-38）会在这里炸
    assert len(lines) - 1 == 2, "两帧都必须整帧到达（写线程整帧写出）"
