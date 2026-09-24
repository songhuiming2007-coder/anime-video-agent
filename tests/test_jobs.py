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
from pipeline.jobs import (
    Event,
    EventPublisher,
    EventType,
    Job,
    JobStatus,
    _reset_global_publisher_for_testing,
    _utc_now_iso,
    get_publisher,
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
