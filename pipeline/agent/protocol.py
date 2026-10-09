"""stdio 会话协议适配器（Spec 9 §2.1、§3、§4.4）。

host spawn 一个长驻子进程，一进程一期，**不开端口**：入站一行一个 JSON（`\n` 结尾），
出站同样。终端接 `input()`，这里接 `FrameReader`；人的答复只从这两条线进来（§2.4.2）。

## 四条纪律

1. **fd 隔离先于 import `pipeline.*`**（§2.1，🔵-11）：`proto_out = dup(1)`、
   `os.dup2(2, 1)`（C 层写 fd 1 与继承 fd 1 的子进程全进 stderr）、`os.dup2(/dev/null, 0)`
   （`input()` 与继承 stdin 的子进程读到 EOF）。TTY 检查必须在 dup2 之前做，否则永远为假。
2. **出站走独立写线程**（🟡-2）：所有帧（含 `log`）进无界队列，写线程对 `proto_out`
   循环 `os.write` 直到整帧写完；主线程只入队，不会把帧写一半就被中断撕裂（MUT-38）。
   EPIPE/EBADF → 标记 host 已断，此后静默丢弃出站帧，不抛给主线程。
3. **答复只从 `_deliver_answer` 进来**（全仓唯一投递点，TK-6 AST 断言）：
   请求号 `secrets.token_hex(16)`，只在出站帧与会话记录里出现，从不进 `messages`。
4. **EOF / `shutdown` / SIGTERM 一律不当作批准**：EOF 与 shutdown 中断当前回合后退出 0；
   SIGTERM 中断 + 跳过收尾（§2.2 第 5 条）。
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import signal
import sys
import threading
import time
from typing import Any, Callable

PROTOCOL_VERSION = 1
MAX_INBOUND_LINE_BYTES = 1_048_576
FRAME_DRAIN_TIMEOUT_S = 2

_RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 入站帧的必需键（S9-R2：`rid` 是可选键，exact-keys 放宽为「必需键 + 可选 rid」）
_INBOUND_KEYS: dict[str, frozenset[str]] = {
    "user_message": frozenset({"text"}),
    "interrupt": frozenset({"turn_id"}),
    "answer": frozenset({"request_id", "decision", "feedback"}),
    "command": frozenset({"name", "arg"}),
    "shutdown": frozenset(),
}
_COMMANDS = frozenset({"memory_ack", "scope"})


def _dump(frame: dict[str, Any]) -> bytes:
    return (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class FrameWriter:
    """无界队列 + 写线程（§2.1 出站）。任何线程都可 `send`。"""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._seq = 0
        self.host_gone = False
        self.sid: str | None = None
        # D45：新会话的 sid 在首回合 `_open_session` 才生成；启动时取一次值会让该会话此后
        # 每一帧的 sid 都是 null（host 因此认不出活会话是哪个）。给了来源就按帧实时取。
        self.sid_source: Callable[[], str | None] | None = None
        self._thread = threading.Thread(target=self._run, name="proto-writer", daemon=True)
        self._thread.start()

    def send(self, frame: dict[str, Any]) -> None:
        if self.host_gone:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
        sid = self.sid_source() if self.sid_source is not None else self.sid
        payload = {"v": PROTOCOL_VERSION, "seq": seq, "sid": sid, **frame}
        self._queue.put(payload)

    def _run(self) -> None:
        while True:
            frame = self._queue.get()
            if frame is None:
                return
            data = _dump(frame)
            written = 0
            while written < len(data):
                try:
                    written += os.write(self._fd, data[written:])
                except OSError:
                    # EPIPE/EBADF：host 断了。静默丢弃，不抛给主线程（收尾与 turn_end 照写盘）
                    self.host_gone = True
                    return

    def close(self, timeout: float = FRAME_DRAIN_TIMEOUT_S) -> None:
        self._queue.put(None)
        self._thread.join(timeout)


class ProtocolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FrameReader(threading.Thread):
    """守护线程：逐行校验，按类型分派（§3.1）。"""

    def __init__(self, fd: int, writer: FrameWriter, slots: dict[str, Any]) -> None:
        super().__init__(name="proto-reader", daemon=True)
        self._fd = fd
        self._writer = writer
        self._slots = slots
        self._buffer = bytearray()
        self._dropping = False

    # ---- 读循环 ----

    def run(self) -> None:  # pragma: no cover - 线程体
        while True:
            try:
                chunk = os.read(self._fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                self._on_eof()
                return
            self._buffer.extend(chunk)
            while True:
                newline = self._buffer.find(b"\n")
                if newline < 0:
                    break
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                self._dropping = False
                self._handle_line(line)
            if len(self._buffer) > MAX_INBOUND_LINE_BYTES and not self._dropping:
                self._dropping = True
                self._buffer.clear()
                self._error("E_TOO_LARGE", f"入站单行超过 {MAX_INBOUND_LINE_BYTES} 字节，已丢弃到下一个换行")

    def _on_eof(self) -> None:
        """stdin EOF：中断当前回合（作废挂起请求），主循环随后退出 0。"""
        slots = self._slots
        slots["eof"] = True
        # 与 shutdown 帧同一条出路（§2.8 表同一行）：有回合才打中断，然后**入队**唤醒主循环。
        # D33（M9 打包版实测）：此前只打中断不入队——空闲时主线程阻塞在 `out.get()` 的锁等上，
        # macOS 上 SIGINT 唤不醒它，进程读过 EOF 后永不退出（host 死后留下 PPID=1 的孤儿）。
        if slots["in_flight"]:
            with contextlib.suppress(Exception):
                slots["interrupt"].request()
        slots["out"].put(("eof", None, None))

    def _error(self, code: str, message: str, rid: str | None = None) -> None:
        frame: dict[str, Any] = {"t": "error", "code": code, "message": message}
        if rid:
            frame["rid"] = rid
        self._writer.send(frame)

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            frame = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error("E_BAD_REQUEST", f"入站行不是合法 JSON：{exc}")
            return
        if not isinstance(frame, dict):
            self._error("E_BAD_REQUEST", "入站帧必须是 JSON 对象")
            return
        rid = frame.get("rid")
        if rid is not None and (not isinstance(rid, str) or not _RID_RE.match(rid)):
            self._error("E_BAD_REQUEST", "rid 必须是 ^[A-Za-z0-9_-]{1,64}$ 的字符串")
            return
        kind = frame.get("t")
        required = _INBOUND_KEYS.get(kind) if isinstance(kind, str) else None
        if required is None:
            self._error("E_BAD_REQUEST", f"未知入站帧类型: {kind!r}", rid)
            return
        # §3.1「公共键：`"v": 1`、`"t"`」对两个方向都成立：入站帧必须带 v 且等于 1。
        # M9 真实联调实测：此前这里只剔除 t/rid，host 按 spec 带上的 v 被当成多余键拒收，
        # 两侧的测试各按自己的理解写期望、各自全绿（Spec 10 RF-8）。
        if frame.get("v") != PROTOCOL_VERSION or isinstance(frame.get("v"), bool):
            self._error("E_BAD_REQUEST", f"入站帧必须带 \"v\": {PROTOCOL_VERSION}", rid)
            return
        keys = set(frame) - {"v", "t", "rid"}
        if keys != required:
            self._error(
                "E_BAD_REQUEST",
                f"{kind} 的键必须恰为 {sorted(required)}（+可选 rid），收到 {sorted(keys)}",
                rid,
            )
            return
        try:
            self._validate(kind, frame)
        except ProtocolError as exc:
            self._error(exc.code, exc.message, rid)
            return
        self._dispatch(kind, frame, rid)

    @staticmethod
    def _validate(kind: str, frame: dict[str, Any]) -> None:
        def need_str(key: str) -> str:
            value = frame.get(key)
            if not isinstance(value, str):
                raise ProtocolError("E_BAD_REQUEST", f"{kind}.{key} 必须是字符串")
            return value

        if kind == "user_message":
            need_str("text")
        elif kind == "interrupt":
            need_str("turn_id")
        elif kind == "answer":
            need_str("request_id")
            need_str("decision")
            if frame.get("feedback") is not None and not isinstance(frame["feedback"], str):
                raise ProtocolError("E_BAD_REQUEST", "answer.feedback 必须是字符串或 null")
        elif kind == "command":
            name = need_str("name")
            if name not in _COMMANDS:
                raise ProtocolError("E_BAD_REQUEST", f"未知命令: {name!r}（可用: {sorted(_COMMANDS)}）")
            arg = frame.get("arg")
            if name == "scope":
                if arg not in ("asset", "auto"):
                    raise ProtocolError("E_BAD_REQUEST", "command{scope} 的 arg ∈ {asset, auto}")
            elif arg is not None:
                raise ProtocolError("E_BAD_REQUEST", f"command{{{name}}} 不接受 arg")

    # ---- 分派 ----

    def _dispatch(self, kind: str, frame: dict[str, Any], rid: str | None) -> None:
        slots = self._slots
        if kind == "answer":
            _deliver_answer(slots, frame, rid)
            return
        if kind == "interrupt":
            current = slots.get("turn_id")
            if current is None or frame["turn_id"] != current:
                self._error("E_STALE", f"当前回合是 {current!r}，{frame['turn_id']!r} 已过期", rid)
                return
            slots["interrupt"].request()
            return
        if kind == "shutdown":
            slots["shutdown"] = True
            if slots["in_flight"]:
                with contextlib.suppress(Exception):
                    slots["interrupt"].request()
            slots["out"].put(("shutdown", frame, rid))
            return
        # user_message / command：跑起来之后才处理，但忙/未就绪要**立刻**回错
        if not slots["ready"]:
            self._error("E_NOT_READY", "会话尚未 ready", rid)
            return
        if slots["in_flight"]:
            self._error("E_BUSY", "上一轮还在进行中", rid)
            return
        # 旗标由读者置、主循环清：否则「帧已入队、主循环还没开始跑」的那几毫秒里，
        # 第二条消息会被当成新回合排进去（真机手验实测）。
        slots["in_flight"] = True
        slots["out"].put((kind, frame, rid))


def _deliver_answer(slots: dict[str, Any], frame: dict[str, Any], rid: str | None = None) -> None:
    """全仓唯一的答复投递点（TK-6 用 AST 断言调用点恰 1 处）。

    严格绑定：请求必须打开中、`decision` 必须在 options 里、`feedback` 只在允许时接受。
    EOF、超时、进程退出一律作废，从不算批准。
    """
    writer: FrameWriter = slots["writer"]
    if frame["request_id"] in slots.get("closed_requests", ()):
        # 已关闭的请求号单独一个错误码（§3.1）：与「从没见过这个号」必须可区分（MUT-20）
        writer.send({"t": "error", "code": "E_REQUEST_CLOSED",
                     "message": f"请求 {frame['request_id']} 已关闭"})
        return
    entry = slots["pending"].get(frame["request_id"])
    if entry is None:
        writer.send({"t": "error", "code": "E_UNKNOWN_REQUEST",
                     "message": f"没有打开中的请求 {frame['request_id']}"})
        return
    if frame["decision"] not in entry["options"]:
        writer.send({"t": "error", "code": "E_BAD_REQUEST",
                     "message": f"decision 必须 ∈ {list(entry['options'])}"})
        return
    feedback = frame.get("feedback")
    if feedback and not entry["feedback_allowed"]:
        writer.send({"t": "error", "code": "E_BAD_REQUEST",
                     "message": f"请求 {frame['request_id']} 不接受 feedback"})
        return
    entry["answer"] = {
        "request_id": frame["request_id"],
        "decision": frame["decision"],
        "feedback": feedback,
        "channel": "protocol",
        "latency_s": max(0.0, time.time() - entry["opened_at"]),
    }
    if rid:
        entry["rid"] = rid
    entry["event"].set()


class _LogStream:
    """`sys.stdout` 的替身：逐行转 `log` 帧（§2.1 第 4 步）。"""

    def __init__(self, writer: FrameWriter, stream: str = "stdout") -> None:
        self._writer = writer
        self._stream = stream
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:  # 行缓冲加锁（🟡-C：`_drain` 线程也在写）
            self._buffer += text
            while "\n" in self._buffer:
                line, _, self._buffer = self._buffer.partition("\n")
                self._writer.send({"t": "log", "stream": self._stream, "text": line})
        return len(text)

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                self._writer.send({"t": "log", "stream": self._stream, "text": self._buffer})
                self._buffer = ""

    def isatty(self) -> bool:
        return False


class ProtocolChannel:
    """HumanChannel 的协议实现（§4.4）。"""

    name = "protocol"

    def __init__(self, writer: FrameWriter, slots: dict[str, Any]) -> None:
        self.writer = writer
        self.slots = slots

    def is_tty(self) -> bool:
        # S7-R1 明确承认这只是「只能由人在 UI 上点击触发」，不受 isatty 保护
        return True

    # ---- 出站 ----

    def show(self, kind: str, payload: dict[str, Any]) -> None:
        if kind in ("stdout", "stderr"):
            self.writer.send({"t": "log", "stream": kind, "text": str(payload.get("text", ""))})
        elif kind == "tool":
            frame = {"t": "tool", "turn_id": self.slots.get("turn_id")}
            frame.update(payload)
            self.writer.send(frame)
        elif kind == "notice":
            self.writer.send({
                "t": "notice", "level": str(payload.get("level", "info")),
                "code": str(payload.get("code", "")), "text": str(payload.get("text", "")),
            })
        # "echo" / "card"：终端专用，协议下由 tool 帧的 summary 与 request 帧承担

    # ---- 人审 ----

    def ask(self, request) -> Any:
        from pipeline.agent.session import HumanAnswer

        entry = {
            "options": tuple(request.options),
            "feedback_allowed": bool(request.feedback_allowed),
            "event": threading.Event(),
            "answer": None,
            "closed": False,
            "opened_at": time.time(),
            "rid": None,
        }
        self.slots["pending"][request.request_id] = entry
        self.writer.send({
            "t": "request",
            "request_id": request.request_id,
            "kind": request.kind,
            "turn_id": request.turn_id,
            "title": request.title,
            "card_text": request.card_text,
            "fields": request.fields,
            "options": list(request.options),
            "feedback_allowed": bool(request.feedback_allowed),
        })
        try:
            while not entry["event"].wait(0.2):
                if self.slots.get("eof") or self.slots.get("shutdown"):
                    break  # 一律作废，从不算批准
            if entry["answer"] is None:
                self._close(request.request_id, reason="voided", decision=None, rid=None)
                raise KeyboardInterrupt
            answer = entry["answer"]
            self._close(request.request_id, reason="answered", decision=answer["decision"],
                        rid=entry["rid"])
            return HumanAnswer(
                answer["request_id"], answer["decision"], answer["feedback"],
                "protocol", answer["latency_s"],
            )
        except BaseException:
            self._close(request.request_id, reason="voided", decision=None, rid=None)
            raise
        finally:
            entry["closed"] = True
            # 关掉的号要留在册上：重复答复回 E_REQUEST_CLOSED，而不是「没见过」
            self.slots.setdefault("closed_requests", set()).add(request.request_id)
            self.slots["pending"].pop(request.request_id, None)

    def _close(self, request_id: str, *, reason: str, decision: str | None,
               rid: str | None) -> None:
        entry = self.slots["pending"].get(request_id) or {}
        if entry.get("closed"):
            return
        entry["closed"] = True
        # §3.1 键集恰为 request_id / reason / decision（作废时 decision 为 null）+ 回显的 rid。
        # D36（M9 实测）：此前作废帧省略 decision、另带表外的 cause——host 的 parseOutFrame
        # 按 §3.1 要求 decision 在场，整帧判 malformed 丢弃，作废的卡于是永远留在待答区与徽标里。
        # 作废原因只进盘（session.jsonl 的 request_closed 记录带 cause），不上帧（Spec 10 🔵-2）。
        frame: dict[str, Any] = {"t": "request_closed", "request_id": request_id, "reason": reason,
                                 "decision": decision}
        if rid:
            frame["rid"] = rid
        self.writer.send(frame)


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def _approval_items(ep_dir) -> tuple[list[dict[str, Any]], str | None]:
    """`stop_points.items`（§6.3：只取这几个键，绝不多带 mtime_ns 之类）。"""
    from pipeline import approvals

    try:
        pendings = approvals.list_pending(ep_dir)
    except Exception as exc:  # ensure_pending 失败也要照发（TP-15）
        return [], str(exc)
    return [
        {
            "approval_id": item.approval_id,
            "type": item.type,
            "created_at": item.created_at,
            "artifacts": [a.path for a in item.artifacts],
            "options": list(item.options),
            "note": item.note,
            "answer_via": "decision_bar",
        }
        for item in pendings
    ], None


def _send_stop_points(writer: FrameWriter, ep_dir, turn_id: str | None) -> None:
    items, error = _approval_items(ep_dir)
    writer.send({"t": "stop_points", "items": items, "turn_id": turn_id})
    if error:
        writer.send({"t": "notice", "level": "warn", "code": "stop_points_unavailable",
                     "text": f"读取挂起停机点失败：{error}"})


def _idle_notice(writer: FrameWriter) -> None:
    """空闲态（等消息 / ready 之后的收尾）收到中断：与终端一致——不退出，只提示。"""
    writer.send({"t": "notice", "level": "info", "code": "interrupt_ignored",
                 "text": "空闲态收到中断，已忽略（只有回合内才有停止语义）"})


def _idle_tolerated(writer: FrameWriter, action) -> None:
    """ready 之后、主循环之前的那些动作：期间的中断按空闲中断处理（MUT-31）。"""
    try:
        action()
    except KeyboardInterrupt:
        _idle_notice(writer)


def _ensure_pending(ep_dir, status) -> None:
    from pipeline import approvals

    with contextlib.suppress(Exception):
        approvals.ensure_pending(ep_dir, status)


def _role_of(origin: str) -> str:
    if origin == "user":
        return "user"
    if origin == "assistant":
        return "assistant"
    if origin in ("tool", "synthetic_tool"):
        return "tool"
    return "system_note"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # ① TTY 检查必须在 fd 0 被换掉之前（§3.6：stdin 是 TTY → 2）
    try:
        if sys.stdin.isatty():
            print("protocol: 本入口只服务 host 的管道（stdin 是 TTY）", file=sys.stderr)
            return 2
    except (OSError, ValueError):
        return 2

    # ② fd 隔离（先于 import 任何 pipeline.*，🔵-11）
    try:
        proto_out = os.dup(1)
        proto_in = os.dup(0)
    except OSError as exc:
        print(f"protocol: 无法隔离协议 fd：{exc}", file=sys.stderr)
        return 2
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(2, 1)          # C 层写 fd 1 / 继承 fd 1 的子进程 → stderr
    os.dup2(devnull, 0)    # input() 与继承 stdin 的子进程读到 EOF
    os.close(devnull)

    writer = FrameWriter(proto_out)
    sys.stdout = _LogStream(writer, "stdout")
    slots: dict[str, Any] = {
        "writer": writer, "pending": {}, "in_flight": False, "ready": False, "eof": False,
        "shutdown": False, "turn_id": None, "terminating": False,
        "out": queue.Queue(), "interrupt": None,
    }
    reader = FrameReader(proto_in, writer, slots)
    reader.start()

    # ③ 到这里才 import 仓内模块
    from pipeline import paths
    from pipeline.agent import status_card
    from pipeline.agent.assembly import SessionContextTracker, assemble_resident_prompt
    from pipeline.agent.cli import check_code_freeze, resolve_episode_target
    from pipeline.agent.llm import load_llm_config
    from pipeline.agent.session import SessionHost, prepare_resume, resume_idea
    from pipeline.agent.session_log import (
        DataUnreachable,
        EpisodeLease,
        SessionLocked,
        SessionLogBroken,
        acquire_idea_lease,
        list_sessions,
        read_log,
        resume_target,
    )
    from pipeline.agent.status_card import build_idea_card, build_status_card
    from pipeline.status import inspect_episode

    def fail(code: str, message: str, rc: int) -> int:
        writer.send({"t": "error", "code": code, "message": message})
        writer.send({"t": "bye", "reason": "error"})
        writer.close()
        return rc

    # ④ 解析参数：<期目录> [--continue [<前缀>]] 或 --idea
    idea = "--idea" in args
    rest = [a for a in args if a != "--idea"]
    resume_prefix: str | None = None
    want_continue = False
    if "--continue" in rest:
        index = rest.index("--continue")
        want_continue = True
        tail = rest[index + 1:]
        rest = rest[:index]
        if len(tail) > 1:
            return fail("E_BAD_REQUEST", "用法: <期目录> --continue [<会话号前缀>]", 2)
        resume_prefix = tail[0] if tail else None
    if idea:
        if rest:
            return fail("E_BAD_REQUEST", "--idea 不接受期目录", 2)
        ep_dir = None
    else:
        if len(rest) != 1:
            return fail("E_BAD_REQUEST", "用法: <期目录> [--continue [<前缀>]] 或 --idea", 2)
        ep_dir = resolve_episode_target(rest[0])
        if ep_dir is None:
            return fail("E_NO_EPISODE", f"期目录不存在：{rest[0]}", 4)
    # idea 与期会话同一道闸（Spec 18 §3.1：idea 也落盘了，data/ 不可达就无处可写）
    if not (paths.ROOT / "data").is_dir():
        return fail("E_DATA_UNREACHABLE", f"data/ 不可达：{paths.ROOT / 'data'}", 4)

    # ⑤ 协议进程一律在读文件之前取租约（§2.5）；idea 取库级 `data/_idea` 的租约，
    # 取不到一律报错退出——不许静默退回「退出即丢」（Spec 18 §3.1 🟡-1）
    lease = None
    try:
        lease = EpisodeLease.acquire(ep_dir) if ep_dir is not None else acquire_idea_lease(paths.ROOT)
    except DataUnreachable as exc:
        return fail("E_DATA_UNREACHABLE", str(exc), 4)
    except (SessionLocked, SessionLogBroken) as exc:
        return fail("E_SESSION_LOCKED", str(exc), 3)

    host = SessionHost(
        ep_dir, root=paths.ROOT, channel=ProtocolChannel(writer, slots),
        log_dir=lease.ep_dir if lease is not None else None,
    )
    host.lease = lease
    slots["interrupt"] = host.interrupt

    # ⑥ --continue 的会话选择
    continue_status = "new"
    resume_state: dict[str, Any] | None = None
    others: list[dict[str, Any]] = []
    if ep_dir is not None:
        summaries = list_sessions(read_log(ep_dir))
        sid = lease.sid if lease is not None else None
        others = [
            {"sid": s.sid, "messages": s.messages, "last_activity": s.last_activity}
            for s in summaries if s.sid != sid
        ]
        if want_continue:
            target, candidates = resume_target(summaries, resume_prefix)
            if candidates:
                return fail("E_BAD_REQUEST",
                            "会话号前缀不唯一：" + ", ".join(c.sid[:8] for c in candidates), 2)
            if target is None:
                continue_status = "no_session"
            else:
                state = prepare_resume(host, target.sid)
                continue_status = state["status"]
                if state["status"] == "resumed":
                    resume_state = state
                    host.sid = target.sid
    else:
        # idea：恒恢复最近的可恢复段，不需要 --continue（Spec 18 §3.1）
        state = resume_idea(host)
        if state is not None:
            continue_status = state["status"]
            if state["status"] == "resumed":
                resume_state = state
                host.sid = state["sid"]
    writer.sid = host.sid
    writer.sid_source = lambda: host.sid

    # ⑦ ready
    config = load_llm_config(paths.ROOT)
    status = inspect_episode(ep_dir) if ep_dir is not None else None
    freeze_ok = check_code_freeze()
    # 先置 ready 再发帧：host 的契约是「收到 ready 就开始发消息」，反过来会必踩
    # 「帧已发出、旗标未置」的几毫秒窗口 → 首条消息被 E_NOT_READY 丢掉（真机冒烟实测）。
    slots["ready"] = True
    writer.send({
        "t": "ready",
        "episode": ep_dir.name if ep_dir is not None else None,
        "scope": "auto",
        "continue_status": continue_status,
        "llm": "ok" if config is not None else "degraded",
        "degrade_reason": None if config is not None else "缺少 config/agent.json 或环境变量密钥",
        "code_freeze_ok": bool(freeze_ok),
        "history_count": len(resume_state["messages"]) if resume_state else 0,
        "session_bytes": len(lease.read()) if lease is not None else 0,
        "other_sessions": others,
    })
    if not freeze_ok:
        writer.send({"t": "notice", "level": "warn", "code": "code_freeze",
                     "text": "pipeline/ 源码存在未提交改动，Code Freeze 护栏生效"})

    # ⑧ 历史帧（按 origin 映射 role；S9-R1 的 tool 角色带 ok/text）
    tracker = SessionContextTracker()
    messages: list[dict[str, Any]] = []
    if ep_dir is None:
        # idea：与终端 `ava idea` 同一语义——落 `data/_idea/session.jsonl`，启动即恢复最近段；
        # 常驻层按 idea.md 现装（messages[0] 不取旧的，与期会话 --continue 同一机制）
        host.bind_main(messages)
        tracker.resident_prompt = assemble_resident_prompt("idea", root=paths.ROOT).content
        tracker.active_scope = "idea"
        if resume_state:
            messages.append({
                "role": "system",
                "content": tracker.get_initial_system_prompt(build_idea_card()),
            })
            messages.extend(resume_state["messages"])
            _send_history(writer, resume_state["records"])
    else:
        host.bind_main(messages)
        scope = _scope_of(status)
        tracker.resident_prompt = assemble_resident_prompt(scope, root=paths.ROOT).content
        tracker.active_scope = scope
        if resume_state:
            tracker.injected_paths = set((resume_state.get("docs") or {}).keys())
            tracker.active_step_key = resume_state.get("step_key")
            messages.append({
                "role": "system",
                "content": tracker.get_initial_system_prompt(
                    build_status_card(ep_dir, status, scope=scope)
                ),
            })
            messages.extend(resume_state["messages"])
            _send_history(writer, resume_state["records"])
    def _startup_tail() -> None:
        if ep_dir is not None:
            _ensure_pending(ep_dir, status)
            _send_stop_points(writer, ep_dir, None)
        else:
            # 回合外（ready 时）的那帧 turn_id 为 null（S9-R4 的括注）
            writer.send({"t": "stop_points", "items": [], "turn_id": None})

    _idle_tolerated(writer, _startup_tail)

    # ⑨ SIGTERM：中断 + 跳过收尾（§2.2 第 5 条）
    def _on_term(signum, frame):  # pragma: no cover - 信号处理
        # §2.2 第 5 条：中断 + 跳过收尾 + 写 turn_end{wrapup:"skipped"} + **退出 0**
        slots["terminating"] = True
        host.interrupt.skip_wrapup = True
        # 先入队、再打中断：`request()` 会在本线程**立刻**抛 KeyboardInterrupt 冲出处理器
        # （suppress(Exception) 拦不住它），放在后面这行就永远到不了 —— 回合已经结束，
        # 主循环却还在等队列（TP-11 实测：帧都对，进程就是不退）。中断照旧抛出，
        # 由回合内的延迟区/落点表接住。
        slots["out"].put(("shutdown", {"t": "shutdown"}, None))
        # 空闲时入队就足以唤醒主循环；再打中断只会落进 MUT-31 的空闲分支，
        # 在 bye 前多一条「空闲态收到中断」notice（N33，与 shutdown 帧的 `in_flight` 判定一致）
        if not slots["in_flight"]:
            return
        try:
            host.interrupt.request()
        except OSError:  # 线程已退出等：中断这条路走不通，但退出这件事已经排上了
            pass

    signal.signal(signal.SIGTERM, _on_term)
    # N40：中断走 `pthread_kill(SIGINT)` 打到主线程，靠的是 Python 的默认处理器把它变成
    # KeyboardInterrupt。以 SIGINT=SIG_IGN 继承启动（非交互 shell 的 `&` 后台进程）时 Python
    # 不装这个处理器，信号被内核丢弃、回合停不下，所以这里显式装回。必须是
    # `default_int_handler` 而不是 SIG_DFL：后者收到 SIGINT 直接杀进程，空闲 notice（MUT-31）
    # 与回合内的延迟区/落点表都接不住。
    signal.signal(signal.SIGINT, signal.default_int_handler)

    exit_code = 0
    try:
        while True:
            try:
                kind, frame, rid = slots["out"].get()
            except KeyboardInterrupt:
                # MUT-31：这里不忽略，进程就会被一次空闲点按死
                _idle_notice(writer)
                continue
            if kind == "shutdown":
                writer.send({"t": "bye", "reason": "shutdown"})
                break
            if slots["eof"]:
                writer.send({"t": "bye", "reason": "eof"})
                break
            if kind == "user_message":
                # idea 会话（--idea）照样开回合：scope 固定 idea、无期目录（写期文件前须先建期）、落库级 data/_idea（Spec 18 §3.1）。
                # M9 真实联调前这里回 E_NO_EPISODE——§3.1 的 user_message 错误表里没有它，桌面端「选题」对话因此开不了回合。
                exit_code = _run_turn(host, writer, slots, messages, tracker, ep_dir, frame, rid)
                if slots["eof"]:
                    writer.send({"t": "bye", "reason": "eof"})
                    break
                continue
            if kind == "command":
                try:
                    _run_command(host, writer, slots, frame, rid)
                finally:
                    slots["in_flight"] = False
    finally:
        slots["ready"] = False
        if lease is not None:
            lease.close()
        sys.stdout.flush()
        writer.close()
    return exit_code


def _scope_of(status) -> str:
    from pipeline.agent.resolver import scope_of

    return scope_of(status) if status is not None else "creative"


def _send_history(writer: FrameWriter, records: list[dict[str, Any]]) -> None:
    index = 0
    for record in records:
        if record.get("k") != "msg":
            continue
        message = record.get("message") or {}
        role = _role_of(str(record.get("origin", "")))
        frame: dict[str, Any] = {
            "t": "history", "index": index, "role": role,
            "text": str(message.get("content") or ""), "name": None,
        }
        if role == "tool":
            # 延迟 import：协议进程 fd 隔离（main ②）必须先于任何 pipeline.* import，
            # 本函数是模块级的，拿不到 main() 里的局部 import（N48 实测 NameError）
            from pipeline.agent.session import tool_flags

            ok, text = tool_flags(message.get("content"))
            frame["ok"] = ok
            frame["text"] = "" if ok else (text or "")
            frame["name"] = None
        writer.send(frame)
        index += 1


def _egress_blocked_text(error: str) -> str:
    """`egress_blocked` notice 正文：模式名只从模式表里认，不转述报错原文（不让请求内容借道出现）。"""
    from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS

    hits = [p for p in RESTRICTED_EGRESS_PATTERNS if f"'{p}'" in error]
    what = f"受限路径「{hits[0]}」" if hits else "受限路径"
    return (
        f"本轮被出网护栏拦下并已回滚，请求没有发出：对话里出现了{what}。"
        "常见来源：对话里打出了该路径，或模型在工具参数里写了该路径。换个说法重问即可。"
    )


def _run_turn(host, writer, slots, messages, tracker, ep_dir, frame, rid) -> int:
    """跑一轮（§4.7）：turn_started → run_turn → turn_finished → stop_points 恰好一帧。"""
    import secrets

    from pipeline.agent.resolver import scope_of
    from pipeline.status import inspect_episode

    turn_id = secrets.token_hex(8)
    slots["turn_id"] = turn_id
    slots["closed_requests"] = set()  # 「已关闭请求号」每回合清零，不让它无限长
    idea = ep_dir is None
    status = None if idea else inspect_episode(ep_dir)
    override = None if idea else slots.get("scope_override")
    if override:
        host.set_scope_override(override)
    scope = "idea" if idea else (override or scope_of(status))
    writer.send({"t": "turn_started", "turn_id": turn_id, **({"rid": rid} if rid else {})})

    # idea 也是主会话、落盘（Spec 18 §3.1）；落盘与否只看 host 有没有租约
    session = host.session(persist=True, scope_mode=scope)
    outcome: dict[str, Any] = {}
    try:
        outcome = session.run_turn(
            frame["text"], messages=messages, scope=scope, status=status,
            tracker=tracker, root=host.root, turn_id=turn_id,
        )
    finally:
        slots["in_flight"] = False
        slots["turn_id"] = None

    final = outcome.get("final") or {}
    text = str(final.get("content") or "")
    if text:
        kind = "wrapup" if outcome.get("stopped") not in (None, "done") and outcome.get("wrapup") == "ok" else "answer"
        writer.send({"t": "assistant", "turn_id": turn_id, "kind": kind, "text": text})
    if outcome.get("local_note"):
        writer.send({"t": "assistant", "turn_id": turn_id, "kind": "local_note",
                     "text": str(outcome["local_note"])})
    if outcome.get("stopped") == "blocked":
        # N52/D52：桌面端原先只收到 turn_finished{blocked}，看不到原因。只给模式名，不给请求体
        writer.send({"t": "notice", "level": "error", "code": "egress_blocked",
                     "text": _egress_blocked_text(str(outcome.get("error") or ""))})
    writer.send({
        "t": "turn_finished",
        "turn_id": turn_id,
        "stopped": outcome.get("stopped", "error"),
        "llm_calls": outcome.get("llm_calls", 0),
        "tool_calls": outcome.get("tool_calls_made", 0),
        "tool_executions": outcome.get("tool_executions", 0),
        "duplicates_rejected": outcome.get("duplicates_rejected", 0),
        "checkpoints": outcome.get("checkpoints", 0),
        "wrapup": outcome.get("wrapup", "none"),
        "duration_s": round(float(outcome.get("elapsed_s", 0.0)), 3),
        "prompt_chars": outcome.get("prompt_chars", 0),
        "lookups": outcome.get("lookups"),  # D48 ①：本回合查证调用按类计数；本地指令等无模型回合为 None
    })
    # 「收尾后」区（§2.2 状态表）：中断落在这里只置标志、不抛（回合内已无事可中断）。
    with host.interrupt.absorbed():
        if idea:
            # S9-R4：每回合恰好一帧 stop_points、带本回合 turn_id；idea 没有期目录，也就没有停机点
            writer.send({"t": "stop_points", "items": [], "turn_id": turn_id})
        else:
            _ensure_pending(ep_dir, status)
            _send_stop_points(writer, ep_dir, turn_id)
    if host.interrupt.clear_dropped():
        writer.send({"t": "notice", "level": "info", "code": "interrupt_dropped",
                     "text": "回合已结束，这次中断被丢弃（未带进空闲态）。"})
    return 0


def _run_command(host, writer, slots, frame: dict[str, Any], rid: str | None) -> None:
    name = frame["name"]
    if name == "scope":
        value = None if frame.get("arg") in (None, "auto") else frame.get("arg")
        slots["scope_override"] = value
        host.set_scope_override(value)
        writer.send({"t": "command_result", "name": "scope", "ok": True,
                     "text": "已切回自动推导" if value is None else f"已切换到 {value} scope",
                     **({"rid": rid} if rid else {})})
        return
    session = host.session(persist=False, scope_mode="creative")
    try:
        ok, text = session.memory_ack()
    except Exception as exc:  # 记忆层任何失败都如实回传，不假装成功
        writer.send({"t": "command_result", "name": "memory_ack", "ok": False,
                     "text": f"{type(exc).__name__}: {exc}", **({"rid": rid} if rid else {})})
        return
    writer.send({"t": "command_result", "name": "memory_ack", "ok": bool(ok), "text": text,
                 **({"rid": rid} if rid else {})})


if __name__ == "__main__":
    raise SystemExit(main())
