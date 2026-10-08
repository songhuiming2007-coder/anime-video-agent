"""会话日志 `data/episodes/<期>/session.jsonl` 的纯函数与期租约（Spec 9 §2.5、§2.8、§3.3、§4.2）。

选题会话（idea）的日志落在库级 `data/_idea/session.jsonl`（Spec 18 §3.1）：同一格式、同一租约，
`EpisodeLease` 管的是「日志目录」，不要求它是期目录（`acquire_idea_lease`）。

三条纪律：

1. **append-only**：已提交的整行一字不改；修复一律写成**追加记录**，由 `rebuild_messages`
   在重建时插回正确位置。截断只有两处：`truncate_torn_tail()`（末尾没换行的残行 = 未提交
   记录）；以及 `clear()`——`ava new --from-idea` 把 `_idea` 记录整段复制进新期**成功之后**、
   持租约把 `_idea/session.jsonl` 截为空文件（Spec 18 §3.2 d，2026-10-08 D42 修订注记）；
   以及 `move_session_to_trash()`——人在桌面端点「删除会话」并过确认框后，持租约把该 sid 的
   整行搬进 `_agent/session-trash/`、其余行逐字节写回（D45，2026-10-08）。
2. **单写者**：一个文件多段会话，靠进程级 `flock(LOCK_EX|LOCK_NB)`；每进程每期至多打开一次
   （同进程第二个 fd 会被 flock 拒掉——R3 实测 Errno 35——所以要在进程内先做单例）。
3. **零重依赖**：只用 stdlib（§5）。

记录格式见 §3.3：公共键 `k` / `sid` / `seq`（会话内单调，跨段连续）/ `ts`（UTC ISO）。
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
LOG_NAME = "session.jsonl"
FIRST_USER_CHARS = 60
#: 选题会话的库级日志目录名（`data/_idea/`）。`_` 前缀：`_episode_name_problem` 禁止这样的期名，
#: 而且它在 `data/` 下、不在 `data/episodes/` 下，任何期枚举都够不着（Spec 18 §3.1 / R4）。
IDEA_DIR = "_idea"

# 无法判断归属时的保守取舍：坏行出现在目标会话的 `session_start` 之后 → 拒绝恢复。
_STATUS_OK = "ok"
_STATUS_CORRUPT = "corrupt"
_STATUS_SCHEMA_UNKNOWN = "schema_unknown"

# 修复用的两句话（§2.8 第 4 步，逐字）。
REPAIR_STARTED = "执行已开始、结果未知：会话在执行期间中断。若是 run_pipeline，请用 read_status 核实产物"
REPAIR_NOT_STARTED = "未执行：会话中断（人审未答复或尚未开始）"

_LEASES: dict[str, "EpisodeLease"] = {}
_LEASES_LOCK = threading.Lock()


class SessionLocked(RuntimeError):
    """同一期已被另一个进程持有（跨进程互斥）。"""


class SessionLogBroken(RuntimeError):
    """会话记录写盘失败：本回合按 error 停止，此后不再写盘（§2.5）。"""


class DataUnreachable(RuntimeError):
    """`data/` 不可达（盘没挂 / 悬空链接）：选题会话与期会话同一道闸，拒绝启动（Spec 18 §3.1）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def dumps(record: dict[str, Any]) -> bytes:
    """确定性序列化（§2.5）：sort_keys + ensure_ascii=False + 一个换行。"""
    return (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _parse_lines(raw: bytes) -> tuple[list[tuple[int, dict[str, Any] | None]], int]:
    """切成带行号的条目（`None` = 无法解析的完整行）。返回 (entries, 残行字节数)。

    末尾没有换行的碎片是**未提交记录**：只忽略、不截断（截断要持锁，见 `truncate_torn_tail`）。
    坏行的**位置**很重要：它决定能否归属到某个会话（§2.8 第 2 步）。
    """
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return [], 0
    complete, _, tail = text.rpartition("\n")
    torn = len(tail.encode("utf-8"))
    if not complete:
        return [], len(raw)
    entries: list[tuple[int, dict[str, Any] | None]] = []
    for index, line in enumerate(complete.split("\n")):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            entries.append((index, None))
            continue
        entries.append((index, parsed if isinstance(parsed, dict) else None))
    return entries, torn


@dataclass(frozen=True)
class SessionSummary:
    """`--sessions` / `ready.other_sessions` 用的会话摘要。"""

    sid: str
    messages: int = 0
    assistants: int = 0
    last_activity: str = ""
    segments: int = 0
    first_user: str = ""   # 首条 user 消息（截 FIRST_USER_CHARS 字），会话列表的标题

    @property
    def resumable(self) -> bool:
        """「有实质对话」的唯一判据：含 ≥1 条 assistant 消息（§2.5）。"""
        return self.assistants > 0


@dataclass
class LoadedSession:
    sid: str
    records: list[dict[str, Any]] = field(default_factory=list)
    status: str = _STATUS_OK
    torn_tail_bytes: int = 0
    bad_line: bool = False

    @property
    def last_seq(self) -> int:
        return max((int(r.get("seq", 0)) for r in self.records), default=0)


def list_sessions(raw: bytes) -> list[SessionSummary]:
    """按 sid 聚合（记录可以交错：恢复较早会话时段尾追加）。末尾残行只忽略。"""
    entries, _torn = _parse_lines(raw)
    order: list[str] = []
    counters: dict[str, dict[str, Any]] = {}
    for _index, record in entries:
        if record is None:
            continue
        sid = record.get("sid")
        if not isinstance(sid, str) or not sid:
            continue
        if sid not in counters:
            counters[sid] = {"messages": 0, "assistants": 0, "last": "", "segments": 0, "first_user": ""}
            order.append(sid)
        bucket = counters[sid]
        ts = str(record.get("ts") or "")
        if ts > bucket["last"]:
            bucket["last"] = ts
        kind = record.get("k")
        if kind == "msg":
            bucket["messages"] += 1
            message = record.get("message") or {}
            if message.get("role") == "assistant":
                bucket["assistants"] += 1
            elif message.get("role") == "user" and not bucket["first_user"]:
                text = " ".join(str(message.get("content") or "").split())
                bucket["first_user"] = text[:FIRST_USER_CHARS]
        elif kind == "session_start":
            bucket["segments"] += 1
    return [
        SessionSummary(
            sid=sid,
            messages=counters[sid]["messages"],
            assistants=counters[sid]["assistants"],
            last_activity=counters[sid]["last"],
            segments=counters[sid]["segments"],
            first_user=counters[sid]["first_user"],
        )
        for sid in order
    ]


def move_session_to_trash(lease: "EpisodeLease", sid: str) -> tuple[int, Path]:
    """把会话 `sid` 的整行搬进 `<日志目录>/_agent/session-trash/`，其余行逐字节写回（D45）。

    只在持租约时调用（没有别的写者）。顺序：先截末尾残行 → 回收站文件落盘成功 → 再原子替换日志。
    解析不了的坏行无法归属，一律留在原文件。**调用后租约即作废并关闭**：日志已换成新文件，
    旧 fd 指向的是被替换掉的那份，继续 append 会写进孤儿文件。返回（搬走的行数，回收站文件）。
    """
    from pipeline.paths import atomic_write

    lease.truncate_torn_tail()
    raw = lease.read()
    kept: list[bytes] = []
    moved: list[bytes] = []
    for line in raw.split(b"\n")[:-1] if raw else []:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            record = None
        owner = record.get("sid") if isinstance(record, dict) else None
        (moved if owner == sid else kept).append(line + b"\n")
    if not moved:
        raise ValueError(f"会话 {sid} 不存在")
    trash_dir = lease.ep_dir / "_agent" / "session-trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    dest = trash_dir / f"{sid}.jsonl"
    if dest.exists():
        dest = trash_dir / f"{sid}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.jsonl"
    atomic_write(dest, b"".join(moved))
    atomic_write(lease.path, b"".join(kept))
    lease.close()
    return len(moved), dest


def load_session(raw: bytes, sid: str) -> LoadedSession:
    """取某个会话的记录（文件序）。坏行归属判断见 §2.8 第 2 步。"""
    entries, torn = _parse_lines(raw)
    mine: list[dict[str, Any]] = []
    start_index: int | None = None
    for index, record in entries:
        if record is None or record.get("sid") != sid:
            continue
        if record.get("k") == "session_start":
            if start_index is None:
                start_index = index
            if record.get("schema") != SCHEMA:
                return LoadedSession(sid, mine, _STATUS_SCHEMA_UNKNOWN, torn, True)
        mine.append(record)
    # 每行都带 sid，坏行无法归属：它出现在目标会话的 session_start **之后**就保守拒恢；
    # 全在它之前时视为别的会话的残骸，正常恢复（§2.8 第 2 步）。
    bad_after_start = start_index is not None and any(
        record is None and index > start_index for index, record in entries
    )
    bad_anywhere = any(record is None for _index, record in entries)
    return LoadedSession(
        sid, mine, _STATUS_CORRUPT if bad_after_start else _STATUS_OK, torn, bad_anywhere
    )


def _assistant_index(records: list[dict[str, Any]]) -> dict[str, int]:
    """tool_call_id → 它所属 assistant 记录的位置（只认同一会话的记录序）。"""
    owner: dict[str, int] = {}
    for index, record in enumerate(records):
        if record.get("k") != "msg":
            continue
        message = record.get("message") or {}
        for call in message.get("tool_calls") or []:
            owner[str(call.get("id"))] = index
    return owner


def plan_repairs(session: LoadedSession) -> list[dict[str, Any]]:
    """崩溃恢复要追加的记录（§2.8 第 4–6 步）。返回的记录**不含** sid/seq/ts，由租约补。

    已提交的整行一字不改——修复全部是追加，由 `rebuild_messages` 插回正确位置。
    """
    records = session.records
    repairs: list[dict[str, Any]] = []

    owner = _assistant_index(records)
    started = {
        str(r.get("tool_call_id"))
        for r in records
        if r.get("k") == "tool_exec_started"
    }
    satisfied: dict[str, bool] = {}
    for record in records:
        if record.get("k") != "msg":
            continue
        message = record.get("message") or {}
        if message.get("role") != "tool":
            continue
        satisfied[str(message.get("tool_call_id"))] = True
    # 已追加过的修复同样算「已有结果」（Spec 18 §3.2 第 6 条，人 2026-10-08 裁决 (A)）：
    # 不认它，同一段每恢复一次就再补一份，重建出两条同 tool_call_id 的 tool 消息（API 直接 400）。
    # id 级口径：同 session 内 tool_call_id 复用属病态历史，不在本修复范围。
    for record in records:
        if record.get("k") == "repair_tool_results":
            for item in record.get("results") or []:
                satisfied[str(item.get("tool_call_id"))] = True

    for index, record in enumerate(records):
        if record.get("k") != "msg":
            continue
        message = record.get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            continue
        missing = [str(c.get("id")) for c in calls if not satisfied.get(str(c.get("id")))]
        if not missing:
            continue
        repairs.append({
            "k": "repair_tool_results",
            "after_seq": int(record.get("seq", 0)),
            "results": [
                {
                    "tool_call_id": cid,
                    "content": json.dumps(
                        {"ok": False, "error": REPAIR_STARTED if cid in started else REPAIR_NOT_STARTED},
                        ensure_ascii=False,
                    ),
                }
                for cid in missing
            ],
        })

    opened = {str(r.get("request_id")) for r in records if r.get("k") == "request_opened"}
    closed = {str(r.get("request_id")) for r in records if r.get("k") == "request_closed"}
    for request_id in sorted(opened - closed):
        repairs.append({
            "k": "request_closed",
            "request_id": request_id,
            "reason": "voided",
            "decision": None,
            "channel": None,
            "latency_s": None,
            "cause": "session_ended",
        })

    ended = {str(r.get("turn_id")) for r in records if r.get("k") == "turn_end"}
    started_turns: list[str] = []
    rolled_back = {str(r.get("turn_id")) for r in records if r.get("k") == "turn_rollback"}
    for record in records:
        if record.get("k") == "turn_start":
            turn_id = str(record.get("turn_id"))
            if turn_id in ended or turn_id in rolled_back or turn_id in started_turns:
                continue
            started_turns.append(turn_id)
    for turn_id in started_turns:
        repairs.append({
            "k": "turn_end",
            "turn_id": turn_id,
            "stopped": "crashed",
            "recovered": True,
            "llm_calls": 0, "tool_calls": 0, "tool_executions": 0,
            "duplicates_rejected": 0, "checkpoints": 0,
            "wrapup": "none", "duration_s": 0.0, "prompt_chars": 0,
        })
        repairs.append({
            "k": "msg",
            "turn_id": turn_id,
            "origin": "recovery_note",
            "message": {
                "role": "user",
                "content": (
                    "[恢复] 上一次会话在这轮中途结束（进程被杀或断电）。这一轮没有收尾总结，"
                    "它的工具结果只保留了已写进会话的部分；产物与审批状态不受影响，"
                    "需要的话用 read_status 核实后再继续。"
                ),
            },
        })
    return repairs


def rebuild_messages(session: LoadedSession) -> list[dict[str, Any]]:
    """按记录重建 `messages[1:]`（不含 system）：丢弃回滚回合，按 after_seq 插回修复结果。"""
    rolled_back = {str(r.get("turn_id")) for r in session.records if r.get("k") == "turn_rollback"}
    rebuilt: list[dict[str, Any]] = []
    positions: dict[int, int] = {}   # seq → rebuilt 下标
    for record in session.records:
        kind = record.get("k")
        if kind == "msg":
            if str(record.get("turn_id")) in rolled_back:
                continue
            positions[int(record.get("seq", 0))] = len(rebuilt)
            rebuilt.append(json.loads(json.dumps(record.get("message"), ensure_ascii=False)))
    # 修复结果插在所属 assistant 之后、其已有 tool 消息之后（§2.8 第 4 步）
    for record in session.records:
        if record.get("k") != "repair_tool_results":
            continue
        anchor = positions.get(int(record.get("after_seq", 0)))
        if anchor is None:
            continue
        cursor = anchor + 1
        while cursor < len(rebuilt) and rebuilt[cursor].get("role") == "tool":
            cursor += 1
        for offset, item in enumerate(record.get("results") or []):
            rebuilt.insert(cursor + offset, {
                "role": "tool",
                "tool_call_id": str(item.get("tool_call_id")),
                "content": str(item.get("content", "")),
            })
        positions = {seq: (index + len(record.get("results") or []) if index > anchor else index)
                     for seq, index in positions.items()}
    return rebuilt


def resume_target(
    summaries: list[SessionSummary], prefix: str | None = None
) -> tuple[SessionSummary | None, list[SessionSummary]]:
    """`--continue` 的选择规则（§2.5）。

    - 无前缀：取「最后活动时间最新、且含 ≥1 条 assistant 消息」的会话；没有就返回 None；
    - 有前缀：只在 sid 前缀命中里选；命中多个 → 返回全部候选（调用方列出来并退出 2）。
      显式指定不受「必须有 assistant 消息」限制（用户知道自己要哪个）。
    """
    if prefix:
        hits = [s for s in summaries if s.sid.startswith(prefix)]
        if len(hits) > 1:
            return None, hits
        return (hits[0] if hits else None), []
    resumable = [s for s in summaries if s.resumable]
    if not resumable:
        return None, []
    return max(resumable, key=lambda s: s.last_activity), []


class EpisodeLease:
    """期租约（§2.5）：进程生命期持有的单一 fd + `flock`，`os.write` 循环写满。

    每进程每期至多一个实例（`acquire` 内部查表）；同进程再要同一个期，拿到的是**同一个对象**，
    所以子会话不会自己把自己锁在门外（R3 实测：同进程第二个 fd 的 flock 会被拒）。
    """

    def __init__(self, ep_dir: Path, fd: int) -> None:
        self.ep_dir = ep_dir
        self.path = ep_dir / LOG_NAME
        self._fd = fd
        self._lock = threading.Lock()
        self.sid: str | None = None
        self.seq = 0

    @classmethod
    def acquire(cls, ep_dir: Path | str) -> "EpisodeLease":
        resolved = Path(ep_dir).resolve()
        key = str(resolved)
        with _LEASES_LOCK:
            existing = _LEASES.get(key)
            if existing is not None:
                return existing
            if not resolved.is_dir():
                raise SessionLogBroken(f"期目录不存在，拒绝建目录与写会话记录: {resolved}")
            fd = os.open(str(resolved / LOG_NAME), os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                raise SessionLocked(
                    f"该期已有活跃会话（{resolved / LOG_NAME} 被另一个进程持有）"
                ) from None
            lease = cls(resolved, fd)
            _LEASES[key] = lease
            return lease

    @classmethod
    def _reset_for_testing(cls) -> None:
        with _LEASES_LOCK:
            for lease in _LEASES.values():
                try:
                    os.close(lease._fd)
                except OSError:
                    pass
            _LEASES.clear()

    def begin(self, sid: str, *, resumed_from_seq: int = 0) -> None:
        """登记本进程当前会话的 sid；`seq` 跨段连续（恢复时从上一段的末号续）。"""
        self.sid = sid
        self.seq = resumed_from_seq

    def read(self) -> bytes:
        try:
            with open(self.path, "rb") as handle:
                return handle.read()
        except OSError:
            return b""

    def append(self, record: dict[str, Any]) -> None:
        """补 sid/seq/ts 后整行写出。失败抛 `SessionLogBroken`（绝不静默吞掉）。"""
        payload = dict(record)
        payload.setdefault("sid", self.sid)
        if "seq" not in payload:
            self.seq += 1
            payload["seq"] = self.seq
        else:
            self.seq = max(self.seq, int(payload["seq"]))
        payload.setdefault("ts", _now_iso())
        line = dumps(payload)
        with self._lock:
            written = 0
            while written < len(line):
                try:
                    written += os.write(self._fd, line[written:])
                except OSError as exc:
                    raise SessionLogBroken(f"会话记录写入失败: {exc}") from None
            try:
                os.fsync(self._fd)
            except OSError:
                pass

    def close(self) -> None:
        """注销并释放（§2.6：登记的生命期就是上下文管理器的范围）。"""
        with _LEASES_LOCK:
            if _LEASES.get(str(self.ep_dir)) is self:
                _LEASES.pop(str(self.ep_dir), None)
        with self._lock:
            try:
                os.close(self._fd)
            except OSError:
                pass

    def clear(self) -> None:
        """把整个日志截为空文件（第二个截断点，Spec 18 §3.2 d）：只用于 `_idea`，且只在
        整段复制进新期**成功之后**、持租约时调用。失败照抛 `OSError`，由调用方如实报错。"""
        with self._lock:
            os.ftruncate(self._fd, 0)
            os.fsync(self._fd)

    def truncate_torn_tail(self) -> int:
        """截断末尾残行（只在持锁后调用；另一处截断见 `clear`）。返回被丢弃的字节数。"""
        with self._lock:
            raw = self.read()
            if not raw or raw.endswith(b"\n"):
                return 0
            cut = raw.rfind(b"\n") + 1
            os.ftruncate(self._fd, cut)
            try:
                os.fsync(self._fd)
            except OSError:
                pass
            return len(raw) - cut


def acquire_idea_lease(root: Path) -> EpisodeLease:
    """取选题会话的库级租约（Spec 18 §3.1「启动与降级路径」，终端与协议同口径）。

    - `data/` 不可达 → `DataUnreachable`（与期会话同闸；**绝不**创建 `data/`）；
    - `data/` 可达而 `_idea/` 不存在 → 由这里创建（先例：`log_approval_decision` 自建 `_agent/`）；
    - 建不了目录 / 拿不到锁 → 抛出，调用方**必须**报错退出：选题会话不许静默退回「退出即丢」。
    """
    data = Path(root) / "data"
    if not data.is_dir():
        raise DataUnreachable(f"data/ 不可达：{data}")
    log_dir = data / IDEA_DIR
    try:
        log_dir.mkdir(exist_ok=True)
    except OSError as exc:
        raise SessionLogBroken(f"无法建立选题会话记录目录 {log_dir}: {exc}") from None
    try:
        return EpisodeLease.acquire(log_dir)
    except SessionLocked:
        raise SessionLocked(
            f"另一个选题会话进行中（{log_dir / LOG_NAME} 被另一个进程持有），请先退出它"
        ) from None


def read_log(ep_dir: Path | str) -> bytes:
    """未持锁时的只读入口（启动提示、`--sessions`）：只忽略残行，绝不截断。"""
    try:
        with open(Path(ep_dir).resolve() / LOG_NAME, "rb") as handle:
            return handle.read()
    except OSError:
        return b""
