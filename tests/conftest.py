"""pytest 全局夹具：事件总线 sink 隔离与真实 data/ 零污染守卫（Spec 2 B3 / T8）。"""

from __future__ import annotations

from pathlib import Path
import pytest

from pipeline import paths
from pipeline.jobs import _reset_global_publisher_for_testing

_REAL_EVENTS_SNAPSHOT: dict[str, object] = {}


@pytest.fixture(scope="session", autouse=True)
def _guard_real_data_events_zero_pollution():
    """会话级守卫：记录真实 paths.ROOT/'data'/_events.jsonl 快照，会话结束断言零写入。

    并发会话 caveat（红队四轮）：若测试运行期间本机另有 REPL/agent 会话在写真实日志，
    快照断言可能触发告警；会话前不存在时严格断言会话后仍未创建。
    """
    real_events = paths.ROOT / "data" / "_events.jsonl"
    existed = real_events.exists()
    stat_before = (
        (real_events.stat().st_size, real_events.stat().st_mtime_ns)
        if existed
        else None
    )
    _REAL_EVENTS_SNAPSHOT["path"] = real_events
    _REAL_EVENTS_SNAPSHOT["existed"] = existed
    _REAL_EVENTS_SNAPSHOT["stat_before"] = stat_before

    yield

    _reset_global_publisher_for_testing()
    if not existed:
        assert not real_events.exists(), (
            f"测试会话违规污染真实 data 目录：创建了 {real_events}"
        )
    else:
        stat_after = (real_events.stat().st_size, real_events.stat().st_mtime_ns)
        assert stat_after == stat_before, (
            f"测试会话违规修改了真实 {real_events}: before={stat_before}, after={stat_after}"
        )


@pytest.fixture(autouse=True)
def _isolate_ava_events_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """函数级 autouse 夹具：将 AVA_EVENTS_ROOT 重定向至当前测试的 tmp_path 并排空重置单例。"""
    monkeypatch.setenv("AVA_EVENTS_ROOT", str(tmp_path))
    _reset_global_publisher_for_testing()
    yield tmp_path
    _reset_global_publisher_for_testing()
