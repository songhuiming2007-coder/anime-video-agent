"""pipeline.jobs: ava 流水线 Job 调度与异步事件总线（Spec 2 / ADR-0020 §2）。"""

from __future__ import annotations

import atexit
import codecs
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable
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
    BROWSER_SESSION_STARTED = "browser_session_started"  # Spec 5 browser 进程启动事件


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


# ---------------------------------------------------------------------------
# 3. Job 注册表、创建、执行与调度入口
# ---------------------------------------------------------------------------

# 最小内存 Job 注册表（红队三轮 M5）：create_job 注册、终态移除；供 cancel_job 与
# Spec 3 审批队列枚举 pending 任务。进程内索引，不落盘、不参与 status.py 状态推导。
_JOB_REGISTRY: dict[str, Job] = {}
_REGISTRY_LOCK = threading.Lock()


def get_job(job_id: str) -> Job | None:
    with _REGISTRY_LOCK:
        return _JOB_REGISTRY.get(job_id)


def list_pending() -> list[Job]:
    with _REGISTRY_LOCK:
        return [j for j in _JOB_REGISTRY.values() if j.status == JobStatus.PENDING]


def _register_job(job: Job) -> None:
    with _REGISTRY_LOCK:
        _JOB_REGISTRY[job.job_id] = job


def _unregister_job(job: Job) -> None:
    with _REGISTRY_LOCK:
        _JOB_REGISTRY.pop(job.job_id, None)


def create_job(
    command: str | list[str],
    episode_dir: Path | str | None = None,
    *,
    scope: str = "pipeline",
    publisher: EventPublisher | None = None,
) -> tuple[Job, bool, str]:
    """显式立项创建一个 Job 对象。通过校验则进入 PENDING，未通过进入 BLOCKED。

    返回: (job, is_valid, validation_message)
    """
    from pipeline.agent.tools import validate_pipeline_command

    cmd_str = shlex.join(command) if isinstance(command, list) else str(command).strip()
    ep_path = Path(episode_dir).resolve() if episode_dir else None
    job_id = f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
    created_at = _utc_now_iso()

    valid, msg, argv = validate_pipeline_command(command, scope=scope, ep_dir=ep_path)

    job = Job(
        job_id=job_id,
        command=cmd_str,
        scope=scope,
        episode_dir=ep_path,
        argv=argv,
        status=JobStatus.PENDING if valid else JobStatus.BLOCKED,
        created_at=created_at,
        message=msg,
    )

    pub = publisher or get_publisher()
    if valid:
        pub.emit(
            EventType.JOB_CREATED,
            {"job_id": job.job_id, "command": job.command, "scope": job.scope, "argv": job.argv},
            episode_dir=ep_path,
        )
    else:
        pub.emit(
            EventType.JOB_BLOCKED,
            {"job_id": job.job_id, "command": job.command, "reason": msg},
            episode_dir=ep_path,
        )

    _register_job(job)
    if not valid:
        _unregister_job(job)  # BLOCKED 即刻终态，不留注册表
    return job, valid, msg


def cancel_job(
    job: Job,
    reason: str = "human_rejected",
    *,
    publisher: EventPublisher | None = None,
) -> Job:
    """显式取消一个 PENDING 状态的 Job，闭环终态，防止幽灵悬空。"""
    if job.status != JobStatus.PENDING:
        return job

    job.transition_to(JobStatus.BLOCKED)
    job.ended_at = _utc_now_iso()
    job.message = reason
    _unregister_job(job)

    pub = publisher or get_publisher()
    pub.emit(
        EventType.JOB_BLOCKED,
        {"job_id": job.job_id, "command": job.command, "reason": reason},
        episode_dir=job.episode_dir,
    )
    return job


class _LockedTailBuffer:
    """尾部缓冲双端持锁包装（红队三轮 M4）：防止 join(timeout=3.0) 超时窗口内
    get_tail() 迭代 deque 时存活 drain 线程并发 append 抛 RuntimeError。"""

    def __init__(self, max_bytes: int = 4096) -> None:
        from pipeline.agent.tools import _TailBuffer

        self._inner = _TailBuffer(max_bytes)
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            self._inner.append(chunk)

    def get_tail(self) -> tuple[str, bool]:
        with self._lock:
            return self._inner.get_tail()


_REAL_POPEN = subprocess.Popen


def _kill_process_group(proc: Any) -> None:
    """杀灭子进程所在的整个进程组（N28：start_new_session=True 下的唯一中断入口）。

    当 start_new_session=True 时，子进程（如 render.py）及其派生的全部孙进程（如并行
    切片或最终合成的 ffmpeg）同属 pgid=proc.pid 的独立进程组，不再直接接收终端前台
    进程组的 SIGINT。无论中断来自终端 Ctrl-C 还是非终端 `kill -INT <ava pid>`，
    均由宿主捕获 BaseException 后在此处统一调用 os.killpg(proc.pid, signal.SIGKILL)
    将整个子进程组连根拔起，再调 proc.kill() 兜底（兼容非 Popen 假对象）。
    """
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0 and isinstance(proc, _REAL_POPEN):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except Exception:
        pass
    if isinstance(proc, _REAL_POPEN):
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass


def execute_job(
    job: Job,
    *,
    publisher: EventPublisher | None = None,
    popen_factory: Callable[..., Any] | None = None,
) -> Job:
    """真实执行一个处于 PENDING 状态的 Job。

    负责双管排水、尾部缓冲抓取、生命周期事件发射及异常守护。
    支持通过 popen_factory 注入执行器（供单元测试与 PR6 测试打桩）。
    注意：popen_factory 默认为 None 并在函数体内调用时解析——默认参数在 def 时
    绑定会穿透运行期 mock（红队三轮 B1），严禁写回 subprocess.Popen 作默认值。
    """
    if job.status != JobStatus.PENDING:
        raise ValueError(f"无法执行状态为 {job.status} 的 Job")

    # 调用时解析执行器（红队三轮 B1）：默认参数在 def 时绑定会穿透运行期 mock；
    # is None 判断（红队四轮）：防止 __bool__ 返回 False 的可调用对象被静默替换
    if popen_factory is None:
        popen_factory = subprocess.Popen

    pub = publisher or get_publisher()

    # 注册表安全网（红队四轮 M5 残留）：except 面外的意外异常（如工厂抛 TypeError）也必须移出注册表
    try:
        t_start = time.time()
        job.started_at = _utc_now_iso()

        try:
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            proc = popen_factory(
                job.argv,
                cwd=paths.ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            job.status = JobStatus.FAILED
            job.ended_at = _utc_now_iso()
            job.duration_s = round(time.time() - t_start, 2)
            job.message = f"执行器启动异常: {exc}"
            pub.emit(
                EventType.JOB_FINISHED,
                {"job_id": job.job_id, "status": job.status.value, "returncode": None, "message": job.message},
                episode_dir=job.episode_dir,
            )
            return job

        stdout_buf = _LockedTailBuffer(4096)
        stderr_buf = _LockedTailBuffer(4096)

        def _drain(pipe: Any, buf: _LockedTailBuffer, is_stderr: bool) -> None:
            target_stream = sys.stderr if is_stderr else sys.stdout
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                for chunk in iter(lambda: pipe.read1(4096), b""):
                    text = decoder.decode(chunk)
                    try:
                        target_stream.write(text)
                        target_stream.flush()
                    except Exception:
                        pass
                    buf.append(chunk)
                final_text = decoder.decode(b"", final=True)
                if final_text:
                    try:
                        target_stream.write(final_text)
                        target_stream.flush()
                    except Exception:
                        pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out: threading.Thread | None = None
        t_err: threading.Thread | None = None
        retcode: int | None = None
        try:
            job.pid = proc.pid
            job.status = JobStatus.RUNNING
            pub.emit(
                EventType.JOB_STARTED,
                {"job_id": job.job_id, "pid": proc.pid, "started_at": job.started_at},
                episode_dir=job.episode_dir,
            )

            t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_buf, False), daemon=True)
            t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_buf, True), daemon=True)
            t_out.start()
            t_err.start()

            retcode = proc.wait()
        except BaseException:
            # Ctrl-C（终端前台进程组 SIGINT）或外部单点信号（kill -INT <ava pid>）：
            # 子进程因 start_new_session=True 处于独立进程组，不再自收终端 SIGINT，
            # 这里是中断的唯一入口，必须用 _kill_process_group (os.killpg SIGKILL)
            # 连同其派生的所有 ffmpeg 孙进程一并强杀（N28）。
            _kill_process_group(proc)
            if t_out is not None:
                t_out.join(2)
            if t_err is not None:
                t_err.join(2)

            job.status = JobStatus.FAILED
            job.ended_at = _utc_now_iso()
            job.duration_s = round(time.time() - t_start, 2)
            actual_code = proc.poll() if proc.poll() is not None else -9
            job.returncode = actual_code
            job.message = f"进程被中断终止 (BaseException, code={actual_code})"

            stdout_tail, out_trunc = stdout_buf.get_tail()
            stderr_tail, err_trunc = stderr_buf.get_tail()
            job.stdout_tail = stdout_tail
            job.stderr_tail = stderr_tail
            job.truncated = out_trunc or err_trunc

            pub.emit(
                EventType.JOB_FINISHED,
                {
                    "job_id": job.job_id,
                    "status": job.status.value,
                    "returncode": job.returncode,
                    "duration_s": job.duration_s,
                    "message": job.message,
                    "stdout_tail": job.stdout_tail,
                    "stderr_tail": job.stderr_tail,
                    "truncated": job.truncated,
                },
                episode_dir=job.episode_dir,
            )
            raise

        # 正常退出加 3 秒防御性超时，防止派生孙进程继承管道导致死锁挂死
        t_out.join(timeout=3.0)
        t_err.join(timeout=3.0)
        job.duration_s = round(time.time() - t_start, 2)
        job.ended_at = _utc_now_iso()
        job.returncode = retcode
        job.status = JobStatus.SUCCEEDED if retcode == 0 else JobStatus.FAILED
        job.message = f"退出码 {retcode}"

        stdout_tail, out_trunc = stdout_buf.get_tail()
        stderr_tail, err_trunc = stderr_buf.get_tail()
        job.stdout_tail = stdout_tail
        job.stderr_tail = stderr_tail
        job.truncated = out_trunc or err_trunc

        pub.emit(
            EventType.JOB_FINISHED,
            {
                "job_id": job.job_id,
                "status": job.status.value,
                "returncode": job.returncode,
                "duration_s": job.duration_s,
                "message": job.message,
                "stdout_tail": job.stdout_tail,
                "stderr_tail": job.stderr_tail,
                "truncated": job.truncated,
            },
            episode_dir=job.episode_dir,
        )

        return job
    finally:
        _unregister_job(job)  # 任何退出路径（含 except 面外意外异常）均移出注册表

