"""pipeline.jobs: ava 流水线 Job 调度与异步事件总线（Spec 2 / ADR-0020 §2）。"""

from __future__ import annotations

import atexit
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any
import uuid

from pipeline import paths

# ---------------------------------------------------------------------------
# 1. 基础数据模型与状态机
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


VALID_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.BLOCKED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.BLOCKED: frozenset(),
}


class EventType(str, Enum):
    JOB_CREATED = "job_created"
    JOB_BLOCKED = "job_blocked"
    JOB_STARTED = "job_started"
    JOB_HEARTBEAT = "job_heartbeat"  # 预留枚举（Spec 8 桌面端接入长任务监控）
    JOB_FINISHED = "job_finished"
    APPROVAL_REQUESTED = "approval_requested"  # 预留 Spec 3
    APPROVAL_RESOLVED = "approval_resolved"    # 预留 Spec 3
    HUMAN_TIME_RECORDED = "human_time_recorded"
    SIDECAR_DEGRADED = "sidecar_degraded"  # Sidecar 自愈诊断（熔断恢复后补发）


@dataclass
class Job:
    job_id: str
    command: str
    scope: str
    episode_dir: Path | None
    argv: list[str] = field(default_factory=list)
    status: JobStatus = JobStatus.PENDING
    pid: int | None = None
    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    returncode: int | None = None
    duration_s: float = 0.0
    message: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, JobStatus):
            self.status = JobStatus(self.status)
        if isinstance(self.episode_dir, str):
            self.episode_dir = Path(self.episode_dir)

    def transition_to(self, target: JobStatus | str) -> None:
        """执行确定性五状态机流转；非法状态转移当场抛出 ValueError。"""
        target_status = target if isinstance(target, JobStatus) else JobStatus(target)
        allowed = VALID_JOB_TRANSITIONS.get(self.status, frozenset())
        if target_status not in allowed:
            raise ValueError(f"非法 Job 状态转移: {self.status.value} -> {target_status.value}")
        self.status = target_status


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: str
    episode: str                        # 相对路径字符串，如 "EGOIST/01-Live" 或 "_global"
    type: EventType
    payload: dict[str, Any]

    def to_jsonl_line(self) -> str:
        """本机字节级确定性序列化为单行 JSONL 字符串（sort_keys=True）。"""
        data = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "episode": self.episode,
            "type": self.type.value if isinstance(self.type, EventType) else str(self.type),
            "payload": self.payload,
        }
        return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"


def _utc_now_iso() -> str:
    """统一 UTC 时间戳：ISO 8601 带微秒与 Z 标记（契约见 §3.3，红队三轮 M2）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 2. 事件 Sidecar 发布器与持久化落地（借 Hermes 原语，atexit + fcntl 排他锁）
# ---------------------------------------------------------------------------


class EventPublisher:
    """事件发布器 Sidecar：daemon 线程、队列满即丢、故障熔断、主循环零阻塞。"""

    def __init__(self, queue_capacity: int = 1024, data_root: Path | str | None = None) -> None:
        self._queue: queue.Queue[tuple[Event, Path | None] | None] = queue.Queue(maxsize=queue_capacity)
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._atexit_registered = False
        self._dropped_count = 0
        self._failure_count = 0
        self._circuit_broken_until = 0.0
        self._circuit_dropped = 0  # 熔断窗口内丢弃计数（恢复后随 sidecar_degraded 自描述事件上报，红队三轮 m4）
        # sink 覆写通道（红队三轮 B3）：测试经构造参数或 AVA_EVENTS_ROOT 环境变量重定向，严禁污染真实审计日志
        if data_root is None and os.environ.get("AVA_EVENTS_ROOT"):
            data_root = os.environ["AVA_EVENTS_ROOT"]
        self._data_root = Path(data_root) if data_root else paths.ROOT / "data"
        self._episodes_root_raw: Path = self._data_root / "episodes"
        # episodes_root 仅在 init 时 resolve 一次并缓存；emit 主线程零文件系统 syscall（红队三轮 M3）
        try:
            self._episodes_root: Path | None = self._episodes_root_raw.resolve()
        except Exception:
            # 降级语义（红队四轮）：resolve 失败后嵌套子期永久退化为 ep_path.name，
            # 两个同名子期（如两个企划的 01-Live）事件标识将撞车——已知上限，调用方可
            # 经 _episodes_root is None 自检发现
            self._episodes_root = None

    def start(self) -> None:
        with self._lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._closed = False
                self._worker_thread = threading.Thread(
                    target=self._consume_loop,
                    name="ava-event-publisher-sidecar",
                    daemon=True,
                )
                self._worker_thread.start()
                if not self._atexit_registered:
                    atexit.register(self.close)
                    self._atexit_registered = True

    def emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        episode_dir: Path | str | None = None,
    ) -> None:
        """非阻塞派发事件。任何情况下绝不向调用方抛出异常。"""
        now_ts = _utc_now_iso()
        # 主线程零 syscall：不再 resolve（调用方 create_job 已解析过一次），仅做纯路径运算（红队三轮 M3）
        ep_path = Path(episode_dir) if episode_dir else None

        # 严格真子集判断：同级边界下载入 _global，嵌套子期保留相对路径（如 EGOIST/01-Live）
        ep_repr = "_global"
        if ep_path is not None:
            try:
                for root in (self._episodes_root, self._episodes_root_raw):
                    if root is not None:
                        if ep_path == root:
                            ep_repr = "_global"
                            break
                        if root in ep_path.parents:
                            ep_repr = ep_path.relative_to(root).as_posix()
                            break
                else:
                    ep_repr = ep_path.name
            except Exception:
                ep_repr = ep_path.name

        evt = Event(
            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
            timestamp=now_ts,
            episode=ep_repr,
            type=event_type,
            payload=payload,
        )

        try:
            self._queue.put_nowait((evt, ep_path))
        except queue.Full:
            self._dropped_count += 1
            # 满即丢，静默不阻塞主任务
        except Exception:
            pass

    def _consume_loop(self) -> None:
        """后台批处理消费循环：严格以毒丸哨兵（None）为唯一合法退出条件，辅以状态双重守卫。"""
        while True:
            if self._closed and self._queue.empty():
                break

            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                # 收到毒丸哨兵，表示队列已排空，退出消费
                self._queue.task_done()
                break

            event, target_ep_dir = item
            try:
                self._write_event_safely(event, target_ep_dir)
            finally:
                self._queue.task_done()

    def _write_event_safely(self, event: Event, episode_dir: Path | None) -> None:
        """安全写入物理文件，带 fcntl 排他锁、熔断保护与外置盘守卫。严禁自动建目录。"""
        now = time.time()
        if now < self._circuit_broken_until:
            # 熔断窗口内仍计数（红队三轮 m4）：恢复后可区分「无事件」与「熔断丢事件」
            self._dropped_count += 1
            self._circuit_dropped += 1
            return  # 处于熔断保护期，直接短路跳过物理 I/O

        try:
            target_path = self._resolve_target_path(episode_dir)
            if target_path is None:
                return

            # 存储红线：父目录必须已在磁盘真实存在；不存在直接放弃，严禁 parent.mkdir()
            if not target_path.parent.exists():
                return

            with target_path.open("a", encoding="utf-8") as f:
                # 使用 fcntl 排他文件锁防止多进程并发交织损坏
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(event.to_jsonl_line())
                    # 熔断恢复首写：同一把 flock 内补发自描述诊断事件，上报熔断期丢弃计数（红队三轮 m4）
                    if self._circuit_dropped > 0:
                        recovered = Event(
                            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
                            timestamp=_utc_now_iso(),
                            episode=event.episode,
                            type=EventType.SIDECAR_DEGRADED,
                            payload={"dropped_during_circuit": self._circuit_dropped},
                        )
                        f.write(recovered.to_jsonl_line())
                        self._circuit_dropped = 0
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            self._failure_count = 0  # 写入成功，重置失败计数
        except Exception:
            self._failure_count += 1
            if self._failure_count >= 3:
                # 连续 3 次写盘失败触发 30s 熔断
                self._circuit_broken_until = now + 30.0

    def _resolve_target_path(self, episode_dir: Path | None) -> Path | None:
        """解析落盘路径：无期走 data/_events.jsonl，有期严格走期目录自身。"""
        try:
            if episode_dir is None:
                if not self._data_root.exists():
                    return None
                return self._data_root / "_events.jsonl"

            # 遵循 paths.py 铁律：外置盘未挂载时不写
            if not episode_dir.exists():
                return None

            return episode_dir / "events.jsonl"
        except Exception:
            return None

    def close(self, timeout: float = 0.5) -> None:
        """有界等待后台队列排空并停机（毒丸强塞协议）。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # 投递毒丸：带超时写入；若满则抽除队头旧事件插入毒丸，确保消费者必然收到哨兵
        try:
            self._queue.put(None, timeout=0.2)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except Exception:
                pass
        except Exception:
            pass

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

        if self._atexit_registered:
            try:
                atexit.unregister(self.close)
                self._atexit_registered = False
            except Exception:
                pass


# 全局单例 Sidecar（按需延迟启动）
_GLOBAL_PUBLISHER: EventPublisher | None = None
_INIT_LOCK = threading.Lock()


def get_publisher() -> EventPublisher:
    global _GLOBAL_PUBLISHER
    if _GLOBAL_PUBLISHER is None:
        with _INIT_LOCK:
            if _GLOBAL_PUBLISHER is None:
                pub = EventPublisher()
                pub.start()
                _GLOBAL_PUBLISHER = pub
    return _GLOBAL_PUBLISHER


def _reset_global_publisher_for_testing() -> None:
    """测试夹具专用：重置全局 Sidecar 单例，消灭跨用例测试污染。"""
    global _GLOBAL_PUBLISHER
    with _INIT_LOCK:
        if _GLOBAL_PUBLISHER is not None:
            _GLOBAL_PUBLISHER.close(timeout=0.2)
            _GLOBAL_PUBLISHER = None
