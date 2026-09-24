"""Spec 2 (ADR-0020 §2) 单元测试：Job 数据模型、五状态机与异步事件 Sidecar（PR1–PR2）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import pytest

from pipeline import paths
from pipeline.agent.tools import run_pipeline
from pipeline.jobs import (
    Event,
    EventPublisher,
    EventType,
    Job,
    JobStatus,
    _reset_global_publisher_for_testing,
    _utc_now_iso,
    cancel_job,
    create_job,
    execute_job,
    get_job,
    get_publisher,
    list_pending,
)


# ---------------------------------------------------------------------------
# PR1：数据模型、状态机与确定性序列化（T1）
# ---------------------------------------------------------------------------


def test_job_state_machine_transitions(tmp_path: Path) -> None:
    """T1 / 门禁 1：验证 Job 五状态合法流转与非法流转拦截。"""
    # 1. pending -> running -> succeeded
    job_ok = Job(job_id="job_1", command="status", scope="pipeline", episode_dir=tmp_path)
    assert job_ok.status == JobStatus.PENDING
    job_ok.transition_to(JobStatus.RUNNING)
    assert job_ok.status == JobStatus.RUNNING
    job_ok.transition_to(JobStatus.SUCCEEDED)
    assert job_ok.status == JobStatus.SUCCEEDED

    # 2. pending -> running -> failed
    job_fail = Job(job_id="job_2", command="tts", scope="pipeline", episode_dir=tmp_path)
    job_fail.transition_to(JobStatus.RUNNING)
    job_fail.transition_to(JobStatus.FAILED)
    assert job_fail.status == JobStatus.FAILED

    # 3. pending -> blocked
    job_blk = Job(job_id="job_3", command="render --force", scope="pipeline", episode_dir=tmp_path)
    job_blk.transition_to(JobStatus.BLOCKED)
    assert job_blk.status == JobStatus.BLOCKED

    # 4. 非法流转必须当场抛出 ValueError
    illegal_cases = [
        (JobStatus.PENDING, JobStatus.SUCCEEDED),
        (JobStatus.PENDING, JobStatus.FAILED),
        (JobStatus.PENDING, JobStatus.PENDING),
        (JobStatus.RUNNING, JobStatus.PENDING),
        (JobStatus.RUNNING, JobStatus.BLOCKED),
        (JobStatus.RUNNING, JobStatus.RUNNING),
        (JobStatus.SUCCEEDED, JobStatus.RUNNING),
        (JobStatus.SUCCEEDED, JobStatus.FAILED),
        (JobStatus.SUCCEEDED, JobStatus.BLOCKED),
        (JobStatus.SUCCEEDED, JobStatus.PENDING),
        (JobStatus.FAILED, JobStatus.RUNNING),
        (JobStatus.FAILED, JobStatus.SUCCEEDED),
        (JobStatus.FAILED, JobStatus.BLOCKED),
        (JobStatus.BLOCKED, JobStatus.RUNNING),
        (JobStatus.BLOCKED, JobStatus.SUCCEEDED),
        (JobStatus.BLOCKED, JobStatus.FAILED),
    ]
    for start_state, bad_target in illegal_cases:
        j = Job(
            job_id="job_bad",
            command="status",
            scope="pipeline",
            episode_dir=tmp_path,
            status=start_state,
        )
        with pytest.raises(ValueError, match="非法 Job 状态转移"):
            j.transition_to(bad_target)


def test_event_deterministic_jsonl_serialization() -> None:
    """PR1：验证 Event.to_jsonl_line() 本机字节级确定性排序（首键为 episode）与 Z 时间戳。"""
    ts = _utc_now_iso()
    assert ts.endswith("Z")
    assert "+00:00" not in ts

    evt = Event(
        event_id="evt_1790089200100_01a2",
        timestamp=ts,
        episode="EGOIST/01-Live",
        type=EventType.JOB_CREATED,
        payload={"job_id": "job_1", "command": "tts --redo 3"},
    )
    raw_line = evt.to_jsonl_line()
    assert raw_line.endswith("\n")
    # sort_keys=True 下字典顶层键按字母序排列：episode 必然排在首位
    assert re.match(r'^\{"episode":\s*"EGOIST/01-Live"', raw_line), (
        f"首键不是 episode: {raw_line}"
    )
    parsed = json.loads(raw_line)
    assert parsed["type"] == "job_created"
    assert parsed["payload"]["command"] == "tts --redo 3"


# ---------------------------------------------------------------------------
# PR2：异步事件 Sidecar、熔断自愈、队列拥塞与零污染隔离（T5, T6, T8）
# ---------------------------------------------------------------------------


def test_sidecar_fail_silent_on_disk_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T5 / 门禁 3 / MUT-2：磁盘只读或写异常时 Sidecar 静默吞错、触发熔断且恢复后补发诊断事件。"""
    pub = EventPublisher(queue_capacity=64, data_root=tmp_path)
    pub.start()

    orig_open = Path.open
    should_fail = True

    def flaky_open(self_path: Path, *args, **kwargs):
        if should_fail and self_path.name in ("events.jsonl", "_events.jsonl"):
            raise OSError("Simulated read-only file system")
        return orig_open(self_path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)

    # 连续发射 3 条事件触发连续 3 次写盘失败
    for i in range(3):
        pub.emit(EventType.JOB_CREATED, {"seq": i}, episode_dir=tmp_path)

    pub._queue.join()
    assert pub._failure_count >= 3
    assert pub._circuit_broken_until > 0.0, "连续写失败后熔断器必须激活（_circuit_broken_until > 0）"
    assert pub._worker_thread is not None and pub._worker_thread.is_alive()

    # 熔断窗口内再发射 2 条事件，验证短路计数累加且不抛异常
    pub.emit(EventType.JOB_STARTED, {"seq": 3}, episode_dir=tmp_path)
    pub.emit(EventType.JOB_FINISHED, {"seq": 4}, episode_dir=tmp_path)
    pub._queue.join()
    assert pub._circuit_dropped == 2
    assert pub._dropped_count >= 2

    # 模拟熔断期结束且磁盘恢复可写：首写应同锁补发 sidecar_degraded 事件
    should_fail = False
    pub._circuit_broken_until = 0.0
    pub.emit(EventType.JOB_FINISHED, {"seq": 5, "recovered": True}, episode_dir=tmp_path)

    # 同步点铁律（B2）：显式 close() 排空并 join 后再读取物理文件
    pub.close()

    events_file = tmp_path / "events.jsonl"
    assert events_file.exists()
    lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    types = [item["type"] for item in lines]
    assert types == ["job_finished", "sidecar_degraded"]
    assert lines[1]["payload"]["dropped_during_circuit"] == 2
    assert pub._circuit_dropped == 0
    assert pub._failure_count == 0


def test_queue_congestion_drop_on_full(tmp_path: Path) -> None:
    """T6 / MUT-1：队列容量为 2 且不启动消费线程时，连续 emit 10 次主线程不阻塞（<5ms）且满即丢。"""
    pub = EventPublisher(queue_capacity=2, data_root=tmp_path)
    # 故意不调用 pub.start()，模拟后台消费停滞导致队列满载

    t0 = time.perf_counter()
    for i in range(10):
        pub.emit(EventType.JOB_HEARTBEAT, {"seq": i}, episode_dir=tmp_path)
    elapsed_s = time.perf_counter() - t0

    assert elapsed_s < 0.005, f"emit 在队列满时发生了阻塞，耗时 {elapsed_s * 1000:.2f}ms >= 5ms"
    assert pub._queue.qsize() == 2
    assert pub._dropped_count == 8


def test_sidecar_poison_pill_and_unmounted_volume_guard(tmp_path: Path) -> None:
    """PR2：验证毒丸在满队列下强塞退出、嵌套期路径解析及未挂载目录绝不自动 mkdir（RF-2）。"""
    episodes_root = tmp_path / "episodes"
    nested_ep = episodes_root / "EGOIST" / "01-Live"
    nested_ep.mkdir(parents=True)
    unmounted_ep = tmp_path / "unmounted_volume" / "ghost_ep"

    pub = EventPublisher(queue_capacity=8, data_root=tmp_path)
    pub.start()
    assert pub._atexit_registered is True

    # 1. 未挂载路径：不得自动 mkdir
    pub.emit(EventType.JOB_CREATED, {"job_id": "j_ghost"}, episode_dir=unmounted_ep)
    # 2. 同级边界 episodes_root：应落入 _global
    pub.emit(EventType.JOB_CREATED, {"job_id": "j_root"}, episode_dir=episodes_root)
    # 3. 嵌套子期路径：episode 字段应为 "EGOIST/01-Live"
    pub.emit(EventType.JOB_CREATED, {"job_id": "j_nested"}, episode_dir=nested_ep)

    # 同步点铁律（B2）：close() 排空后再读
    pub.close()
    assert pub._atexit_registered is False
    assert pub._worker_thread is not None and not pub._worker_thread.is_alive()

    assert not unmounted_ep.exists() and not unmounted_ep.parent.exists(), "严禁对不存在的外置目录自动 mkdir"

    nested_events = [
        json.loads(line)
        for line in (nested_ep / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(nested_events) == 1
    assert nested_events[0]["episode"] == "EGOIST/01-Live"

    root_events = [
        json.loads(line)
        for line in (episodes_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(root_events) == 1
    assert root_events[0]["episode"] == "_global"


def test_no_write_to_real_data_root(tmp_path: Path) -> None:
    """T8（红队三轮 B3）：验证 conftest 将 AVA_EVENTS_ROOT 定向 tmp_path，真实 data/ 零写入。"""
    env_root = os.environ.get("AVA_EVENTS_ROOT")
    assert env_root is not None
    assert Path(env_root) == tmp_path
    assert Path(env_root).resolve() != (paths.ROOT / "data").resolve()

    real_events = paths.ROOT / "data" / "_events.jsonl"
    existed_before = real_events.exists()
    stat_before = (real_events.stat().st_size, real_events.stat().st_mtime_ns) if existed_before else None

    pub = get_publisher()
    pub.emit(EventType.JOB_CREATED, {"job_id": "job_t8_probe"}, episode_dir=None)
    # 同步点铁律（B2）：重置全局单例以完成毒丸排空
    _reset_global_publisher_for_testing()

    assert (tmp_path / "_events.jsonl").exists()
    if not existed_before:
        assert not real_events.exists(), f"真实 {real_events} 被测试污染创建"
    else:
        stat_after = (real_events.stat().st_size, real_events.stat().st_mtime_ns)
        assert stat_after == stat_before


# ---------------------------------------------------------------------------
# PR3：执行器与兼容适配层（T2, T3, T4a, T4b, T7）
# ---------------------------------------------------------------------------


def test_create_job_validation_and_blocked(tmp_path: Path) -> None:
    """T2 / MUT-6: 校验失败（如 --force）直接进入 BLOCKED，落盘 job_blocked 事件且不入注册表。"""
    ep_dir = tmp_path / "01-test"
    ep_dir.mkdir(parents=True)
    pub = EventPublisher(queue_capacity=64, data_root=tmp_path)
    pub.start()

    job, valid, msg = create_job("tts --force", episode_dir=ep_dir, scope="pipeline", publisher=pub)
    assert valid is False
    assert job.status == JobStatus.BLOCKED
    assert "禁止" in msg or "拒绝" in msg or "参数" in msg
    assert get_job(job.job_id) is None, "BLOCKED 任务不应留在注册表中"
    assert list_pending() == []

    pub.close()
    events_file = ep_dir / "events.jsonl"
    assert events_file.exists()
    lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["type"] == "job_blocked"
    assert lines[0]["payload"]["job_id"] == job.job_id


def test_execute_job_and_events_trail(tmp_path: Path) -> None:
    """T3 / MUT-5: 正常子进程执行与事件流完整性，验证确定性排序首键为 episode。"""
    ep_dir = tmp_path / "01-test-trail"
    ep_dir.mkdir(parents=True)
    pub = EventPublisher(queue_capacity=64, data_root=tmp_path)
    pub.start()

    cmd_script = "import sys; print('OUTPUT_DATA'); sys.exit(0)"
    from unittest.mock import patch
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys.executable, "-c", cmd_script])):
        job, valid, msg = create_job("check_script", episode_dir=ep_dir, scope="pipeline", publisher=pub)
        assert valid is True
        assert job.status == JobStatus.PENDING
        assert get_job(job.job_id) is not None
        assert job in list_pending()

        executed = execute_job(job, publisher=pub)
        assert executed.status == JobStatus.SUCCEEDED
        assert executed.returncode == 0
        assert "OUTPUT_DATA" in executed.stdout_tail
        assert get_job(job.job_id) is None, "终态任务应移出注册表"

    pub.close()

    events_file = ep_dir / "events.jsonl"
    assert events_file.exists()
    raw_lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3

    # MUT-5: 首行 raw string 顶层首键必须按 sort_keys=True 以 "episode" 开头
    first_raw = raw_lines[0]
    assert re.match(r'^\{"episode":\s*"01-test-trail"', first_raw), f"首键不是 episode: {first_raw}"

    events = [json.loads(line) for line in raw_lines]
    types = [e["type"] for e in events]
    assert types == ["job_created", "job_started", "job_finished"]
    assert events[1]["payload"]["pid"] == executed.pid
    assert events[2]["payload"]["status"] == "succeeded"
    assert events[2]["payload"]["returncode"] == 0
    assert "OUTPUT_DATA" in events[2]["payload"]["stdout_tail"]


def test_child_process_killed_by_signal(tmp_path: Path) -> None:
    """T4a / 门禁 2 / MUT-4: 子进程被外部 SIGKILL 强杀，返回 FAILED 且事件层完整还原轨迹。"""
    ep_dir = tmp_path / "01-test-sigkill"
    ep_dir.mkdir(parents=True)
    pub = EventPublisher(queue_capacity=64, data_root=tmp_path)
    pub.start()

    kill_script = (
        "import os, sys, time\n"
        "sys.stdout.write('CHILD_READY\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(10)\n"
    )
    from unittest.mock import patch
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys.executable, "-c", kill_script])):
        job, valid, msg = create_job("check_script", episode_dir=ep_dir, scope="pipeline", publisher=pub)
        assert valid is True

        import signal
        import threading
        def killer():
            for _ in range(50):
                if job.pid is not None:
                    break
                time.sleep(0.05)
            if job.pid is not None:
                try:
                    os.kill(job.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        t_kill = threading.Thread(target=killer)
        t_kill.start()

        executed = execute_job(job, publisher=pub)
        t_kill.join()

        assert executed.status == JobStatus.FAILED
        assert executed.returncode == -signal.SIGKILL
        assert "CHILD_READY" in executed.stdout_tail

    pub.close()

    events_file = ep_dir / "events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    types = [e["type"] for e in events]
    assert types == ["job_created", "job_started", "job_finished"]
    finished_payload = events[2]["payload"]
    assert finished_payload["status"] == "failed"
    assert finished_payload["returncode"] == -signal.SIGKILL
    assert "CHILD_READY" in finished_payload["stdout_tail"]


def test_parent_process_interrupted_by_sigint(tmp_path: Path) -> None:
    """T4b: 父进程捕获 KeyboardInterrupt 中断，杀死子进程、抓取 tail、发射 job_finished 并向外抛出。"""
    ep_dir = tmp_path / "01-test-sigint"
    ep_dir.mkdir(parents=True)
    pub = EventPublisher(queue_capacity=64, data_root=tmp_path)
    pub.start()

    killed_pids: list[int] = []

    class FakeInterruptProc:
        def __init__(self):
            import io
            self.stdout = io.BytesIO(b"PARTIAL_BEFORE_INT\n")
            self.stderr = io.BytesIO(b"")
            self.pid = 99999

        def wait(self, *a, **k):
            raise KeyboardInterrupt("Simulated Ctrl-C")

        def kill(self):
            killed_pids.append(self.pid)

        def poll(self):
            return -2

    from unittest.mock import patch
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys.executable, "-m", "pipeline.clips"])):
        job, valid, msg = create_job("clips", episode_dir=ep_dir, scope="pipeline", publisher=pub)
        assert valid is True

        with pytest.raises(KeyboardInterrupt, match="Simulated Ctrl-C"):
            execute_job(job, publisher=pub, popen_factory=lambda *a, **k: FakeInterruptProc())

    assert killed_pids == [99999], "父进程中断时子进程必须被 kill"
    assert job.status == JobStatus.FAILED
    assert job.returncode == -2
    assert "PARTIAL_BEFORE_INT" in job.stdout_tail
    assert get_job(job.job_id) is None

    pub.close()

    events_file = ep_dir / "events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    types = [e["type"] for e in events]
    assert types == ["job_created", "job_started", "job_finished"]
    assert events[2]["payload"]["status"] == "failed"
    assert events[2]["payload"]["returncode"] == -2
    assert "PARTIAL_BEFORE_INT" in events[2]["payload"]["stdout_tail"]


def test_run_pipeline_backward_compatibility(tmp_path: Path) -> None:
    """T7 / MUT-6: run_pipeline 兼容适配器，confirmed=False 零幽灵 Job，confirmed=True 正常流转。"""
    ep_dir = tmp_path / "01-test-compat"
    ep_dir.mkdir(parents=True)

    # 1. confirmed=False 纯 dry-run：不创建 Job，不落盘 events.jsonl
    dry_res = run_pipeline("status", episode_dir=ep_dir, scope="pipeline", confirmed=False)
    assert dry_res["ok"] is True
    assert dry_res["message"] == "待人类确认"
    assert dry_res["job_id"] is None
    assert dry_res["returncode"] is None

    _reset_global_publisher_for_testing()
    events_file = ep_dir / "events.jsonl"
    assert not events_file.exists(), "confirmed=False 绝对不能生成 events.jsonl（防幽灵 Job）"
    assert list_pending() == []

    # 2. confirmed=True 正式执行：包含 job_id，且落盘事件
    cmd_script = "import sys; print('COMPAT_SUCCESS'); sys.exit(0)"
    from unittest.mock import patch
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys.executable, "-c", cmd_script])):
        real_res = run_pipeline("check_script", episode_dir=ep_dir, scope="pipeline", confirmed=True)

    assert real_res["ok"] is True
    assert real_res["returncode"] == 0
    assert real_res["job_id"] is not None
    assert real_res["job_id"].startswith("job_")
    assert "COMPAT_SUCCESS" in real_res["stdout_tail"]
    assert real_res["duration_s"] >= 0.0

    _reset_global_publisher_for_testing()
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    types = [e["type"] for e in events]
    assert types == ["job_created", "job_started", "job_finished"]
    assert events[0]["payload"]["job_id"] == real_res["job_id"]


def test_cancel_job_blocked_transition(tmp_path: Path) -> None:
    """PR3: cancel_job 将 PENDING 任务显式置为 BLOCKED，发射 job_blocked 并移出注册表。"""
    ep_dir = tmp_path / "01-test-cancel"
    ep_dir.mkdir(parents=True)
    pub = EventPublisher(queue_capacity=64, data_root=tmp_path)
    pub.start()

    from unittest.mock import patch
    with patch("pipeline.agent.tools.validate_pipeline_command", return_value=(True, "ok", [sys.executable, "-m", "pipeline.status"])):
        job, valid, msg = create_job("status", episode_dir=ep_dir, scope="pipeline", publisher=pub)
        assert valid is True
        assert job.status == JobStatus.PENDING
        assert get_job(job.job_id) is not None

        cancelled = cancel_job(job, reason="human_aborted", publisher=pub)
        assert cancelled.status == JobStatus.BLOCKED
        assert cancelled.message == "human_aborted"
        assert get_job(job.job_id) is None
        assert list_pending() == []

    pub.close()
    events_file = ep_dir / "events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    types = [e["type"] for e in events]
    assert types == ["job_created", "job_blocked"]
    assert events[1]["payload"]["reason"] == "human_aborted"


def test_log_approval_decision_emits_approval_resolved(tmp_path: Path) -> None:
    """PR4 / 钉死结论: log_approval_decision 发射 APPROVAL_RESOLVED，且无期会话（ep_dir=None）落 _global。"""
    from pipeline.agent.status_card import log_approval_decision

    ep_dir = tmp_path / "01-test-approval"
    ep_dir.mkdir(parents=True)

    # 1. 有期会话：按 n 拒绝
    log_approval_decision(ep_dir, "run_pipeline", "tts --redo 3", "n", latency_s=1.23)
    # 有期会话：按 y 批准
    log_approval_decision(ep_dir, "run_pipeline", "render", "y", latency_s=0.45)

    # 2. 无期会话（ep_dir=None）：emit 必须在早退前执行，落 _global
    log_approval_decision(None, "run_pipeline", "status", "n", latency_s=0.88)

    _reset_global_publisher_for_testing()

    # 验证有期事件
    events_file = ep_dir / "events.jsonl"
    assert events_file.exists()
    ep_events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    assert len(ep_events) == 2
    assert ep_events[0]["type"] == "approval_resolved"
    assert ep_events[0]["payload"]["decision"] == "rejected"
    assert ep_events[0]["payload"]["command"] == "tts --redo 3"
    assert ep_events[1]["type"] == "approval_resolved"
    assert ep_events[1]["payload"]["decision"] == "approved"
    assert ep_events[1]["payload"]["command"] == "render"

    # 验证有期 approvals.jsonl（保持原格式 "y"/"n"）
    approvals_file = ep_dir / "_agent" / "approvals.jsonl"
    assert approvals_file.exists()
    app_lines = [json.loads(line) for line in approvals_file.read_text(encoding="utf-8").splitlines()]
    assert len(app_lines) == 2
    assert app_lines[0]["decision"] == "n"
    assert app_lines[1]["decision"] == "y"

    # 验证无期会话落 _global（由 conftest 重定向至 tmp_path/_events.jsonl）
    global_events_file = tmp_path / "_events.jsonl"
    assert global_events_file.exists()
    global_events = [json.loads(line) for line in global_events_file.read_text(encoding="utf-8").splitlines()]
    matching = [e for e in global_events if e.get("type") == "approval_resolved" and e["payload"].get("command") == "status"]
    assert len(matching) == 1
    assert matching[0]["episode"] == "_global"
    assert matching[0]["payload"]["decision"] == "rejected"



# ---------------------------------------------------------------------------
# 门禁 4 & 门禁 5：真相源零污染与顶层依赖纯洁性
# ---------------------------------------------------------------------------


def test_jobs_module_pure_and_no_numpy_in_hot_path() -> None:
    """门禁 5 / MUT-3：在独立全新 Python 子进程中验证 pipeline.jobs 顶层不引入 numpy 等重包。"""
    probe_code = (
        "import pipeline.jobs, sys; "
        "forbidden = ('numpy', 'torch', 'moviepy', 'transformers'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'pipeline.jobs 顶层违规引入了重量级包: {leaked}'"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe_code],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"依赖纯洁性检验失败:\n{res.stderr}"


def test_status_module_zero_events_dependency() -> None:
    """门禁 4：验证 pipeline/status.py 源码不引用 events.jsonl 或 pipeline.jobs。"""
    status_src = (paths.ROOT / "pipeline" / "status.py").read_text(encoding="utf-8")
    assert "events.jsonl" not in status_src
    assert "pipeline.jobs" not in status_src
    assert "from . import jobs" not in status_src
    assert "import jobs" not in status_src
