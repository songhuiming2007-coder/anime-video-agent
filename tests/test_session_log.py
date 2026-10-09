"""会话日志（Spec 9 §7.1 TS-*，PR2）。

全部在 tmp 假仓库里跑：真实的 `data/episodes/*/session.jsonl` 一律不碰。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.agent import session_log as slog


@pytest.fixture(autouse=True)
def _clean_leases():
    """租约表是进程级单例：每个用例前后都清干净，免得上一个用例的锁泄漏到下一个。"""
    slog.EpisodeLease._reset_for_testing()
    yield
    slog.EpisodeLease._reset_for_testing()


@pytest.fixture
def ep(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "episodes" / "01-smoke"
    path.mkdir(parents=True)
    (path / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    return path


def _start(sid: str) -> dict:
    return {"k": "session_start", "sid": sid, "schema": slog.SCHEMA, "episode": "01-smoke",
            "scope_mode": "auto", "resident_sha256": "abc", "pid": os.getpid()}


def _user(turn: str, text: str) -> dict:
    return {"k": "msg", "turn_id": turn, "origin": "user",
            "message": {"role": "user", "content": text}}


def _assistant(turn: str, text: str, calls: list[str] | None = None) -> dict:
    message: dict = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function",
             "function": {"name": "read_status", "arguments": json.dumps({"episode": "01-smoke"})}}
            for cid in calls
        ]
    return {"k": "msg", "turn_id": turn, "origin": "assistant", "message": message}


def _tool(turn: str, cid: str, ok: bool = True) -> dict:
    return {"k": "msg", "turn_id": turn, "origin": "tool",
            "message": {"role": "tool", "tool_call_id": cid,
                        "content": json.dumps({"ok": ok, "result": {}})}}


def _write(ep: Path, records: list[dict]) -> None:
    """把记录逐条写进租约；sid 取自记录本身，同一 sid 的 seq 连续。"""
    lease = slog.EpisodeLease.acquire(ep)
    for record in records:
        sid = record.get("sid")
        if sid and sid != lease.sid:
            lease.begin(sid, resumed_from_seq=lease.seq if lease.sid == sid else 0)
        lease.append(record)


# ---------------------------------------------------------------------------
# TS-1 / TS-2 / TS-3
# ---------------------------------------------------------------------------


def test_ts1_write_then_load_is_deterministic(ep: Path) -> None:
    """TS-1：写入再加载——确定性序列化；`rebuild_messages` == 写入时的 messages。"""
    records = [_start("sid-a"), _user("t1", "甲"), _assistant("t1", "乙")]
    _write(ep, records)
    raw = slog.read_log(ep)

    # 确定性序列化的可检验形状：sort_keys + 一个换行，且逐行可解析
    lines = raw.decode("utf-8").splitlines()
    assert len(lines) == len(records)
    assert all(json.loads(line)["sid"] == "sid-a" for line in lines)
    assert all(line == json.dumps(json.loads(line), sort_keys=True, ensure_ascii=False) for line in lines)

    loaded = slog.load_session(raw, "sid-a")
    assert loaded.status == "ok"
    assert slog.rebuild_messages(loaded) == [
        {"role": "user", "content": "甲"},
        {"role": "assistant", "content": "乙"},
    ]


def test_ts2_torn_tail_is_truncated_and_prefix_untouched(ep: Path) -> None:
    """TS-2：末尾残行被截断，此前字节逐字节不变（唯一的截断动作）。"""
    _write(ep, [_start("sid-a"), _user("t1", "甲")])
    with open(ep / slog.LOG_NAME, "ab") as handle:
        handle.write(b'{"k": "msg", "sid": "sid-a", "seq": 9')  # 没有换行、也不完整
    before = slog.read_log(ep)

    lease = slog.EpisodeLease.acquire(ep)
    dropped = lease.truncate_torn_tail()
    after = slog.read_log(ep)

    assert dropped == len(b'{"k": "msg", "sid": "sid-a", "seq": 9')
    assert after == before[: len(before) - dropped]
    assert after.endswith(b"\n")
    assert slog.EpisodeLease.acquire(ep).truncate_torn_tail() == 0  # 幂等


def test_ts3_bad_line_after_session_start_is_corrupt(ep: Path) -> None:
    """TS-3：目标会话 `session_start` 之后有坏行 → corrupt；坏行在它之前时正常恢复。"""
    _write(ep, [_start("sid-a"), _user("t1", "甲")])
    with open(ep / slog.LOG_NAME, "ab") as handle:
        handle.write(b"{ this is not json }\n")

    loaded = slog.load_session(slog.read_log(ep), "sid-a")
    assert loaded.status == "corrupt"

    # 坏行在 session_start 之前（前一段会话的残骸）→ 正常恢复
    other = ep.parent / "02-other"
    other.mkdir()
    with open(other / slog.LOG_NAME, "wb") as handle:
        handle.write(b"{ broken }\n")
        handle.write(slog.dumps({**_start("sid-b"), "seq": 1}))
        handle.write(slog.dumps({**_user("t1", "甲"), "sid": "sid-b", "seq": 2}))
    recovered = slog.load_session(slog.read_log(other), "sid-b")
    assert recovered.status == "ok"
    assert slog.rebuild_messages(recovered) == [{"role": "user", "content": "甲"}]


# ---------------------------------------------------------------------------
# TS-4 / TS-5 / TS-12
# ---------------------------------------------------------------------------


def test_ts4_repairs_are_append_only_and_prefix_is_byte_identical(ep: Path) -> None:
    """TS-4：断掉的调用（有/无 tool_exec_started）、未关闭请求、无 turn_end 的回合。

    修复记录正确，且**原前缀逐字节不变**——所以夹具旧行故意写成非默认空白（多余空格、
    键序打乱），任何「重写整个文件」的实现都会在这里露馅（MUT-23）。
    """
    dirty_lines = [
        b'{"k": "turn_start", "sid": "sid-a", "seq": 0, "turn_id": "t1", "scope": "creative", "step_key": "01"}\n',
        b'{ "k" : "session_start" , "sid" : "sid-a" , "seq" : 1 , "schema" : 1 , "episode" : "01-smoke" , "scope_mode" : "auto" , "resident_sha256" : "abc" , "pid" : 1 }\n',
        '{"origin": "user", "message": {"content": "甲", "role": "user"}, "k": "msg", "seq": 2, "sid": "sid-a", "turn_id": "t1"}\n'.encode("utf-8"),
        '{"k": "request_opened", "request_id": "rq1", "seq": 3, "sid": "sid-a", "title": "工具卡", "turn_id": "t1", "kind": "tool_call"}\n'.encode("utf-8"),
        slog.dumps({**_assistant("t1", None, ["c1", "c2"]), "sid": "sid-a", "seq": 4}),
        slog.dumps({**_tool("t1", "c1"), "sid": "sid-a", "seq": 5}),
        slog.dumps({"k": "tool_exec_started", "turn_id": "t1", "tool_call_id": "c2",
                    "name": "run_pipeline", "sid": "sid-a", "seq": 6}),
    ]
    with open(ep / slog.LOG_NAME, "wb") as handle:
        handle.write(b"".join(dirty_lines))
    prefix = (ep / slog.LOG_NAME).read_bytes()
    assert prefix == b"".join(dirty_lines), "夹具自己必须先逐字节成立"

    # 走**生产入口** prepare_resume（M9：此前这里复刻了一遍它的修复流程，生产路径退化成
    # 「重写整个文件」时本用例照样绿——S9-MUT-23 在 HEAD 上实跑 red=0 即此因）
    from pipeline.agent.session import SessionHost, prepare_resume

    class _Quiet:
        name = "tty"

        def show(self, kind: str, payload: dict) -> None:
            pass

        def ask(self, request):  # 修复路径不问人
            raise AssertionError("prepare_resume 不应向人提问")

    lease = slog.EpisodeLease.acquire(ep)
    host = SessionHost(ep, channel=_Quiet())
    host.lease = lease
    state = prepare_resume(host, "sid-a")
    assert state["status"] == "resumed"

    after = (ep / slog.LOG_NAME).read_bytes()
    assert after.startswith(prefix), "原前缀必须逐字节不变（修复只许追加）"
    repairs = [json.loads(line) for line in after[len(prefix):].splitlines() if line.strip()]

    kinds = [r["k"] for r in repairs]
    assert kinds.count("repair_tool_results") == 1
    assert kinds.count("request_closed") == 1
    assert kinds.count("turn_end") == 1

    results = repairs[0]["results"]
    assert [r["tool_call_id"] for r in results] == ["c2"], "c1 已有 tool 消息，只补 c2"
    assert slog.REPAIR_STARTED in results[0]["content"], "有 tool_exec_started → 执行已开始、结果未知"
    assert repairs[1]["reason"] == "voided" and repairs[1]["cause"] == "session_ended"
    assert repairs[2]["stopped"] == "crashed" and repairs[2]["recovered"] is True

    rebuilt = slog.rebuild_messages(slog.load_session(after, "sid-a"))
    assert [m["role"] for m in rebuilt] == ["user", "assistant", "tool", "tool", "user"]
    inserted = json.loads(rebuilt[3]["content"])
    assert "执行期间中断" in inserted["error"]
    assert rebuilt[3]["tool_call_id"] == "c2"


def test_ts4b_repair_without_tool_exec_started_says_not_started(ep: Path) -> None:
    """TS-4 的另一半：没有 tool_exec_started 的缺失 id → 「未执行：会话中断」。"""
    _write(ep, [
        {**_start("sid-a"), "seq": 1},
        {**_user("t1", "甲"), "seq": 2},
        {**_assistant("t1", None, ["c1"]), "seq": 3},
    ])
    loaded = slog.load_session(slog.read_log(ep), "sid-a")
    repairs = slog.plan_repairs(loaded)
    results = repairs[0]["results"]
    assert slog.REPAIR_NOT_STARTED in results[0]["content"]


def test_ts5_rolled_back_turn_messages_are_dropped(ep: Path) -> None:
    """TS-5：含 `turn_rollback` 的回合，其消息不出现在重建结果里。"""
    _write(ep, [
        _start("sid-a"), _user("t1", "保留"),
        _user("t2", "丢弃"), _assistant("t2", "也丢弃"),
        {"k": "turn_rollback", "turn_id": "t2"},
        _assistant("t1", "保留的回答"),
    ])
    rebuilt = slog.rebuild_messages(slog.load_session(slog.read_log(ep), "sid-a"))
    assert rebuilt == [{"role": "user", "content": "保留"}, {"role": "assistant", "content": "保留的回答"}]


def test_ts12_repair_inserts_after_the_owning_assistant_not_at_tail(ep: Path) -> None:
    """TS-12：历史**中段**存在缺配对的 assistant → 修复插在它之后，不是文件尾。"""
    _write(ep, [
        _start("sid-a"),
        _user("t1", "甲"), {**_assistant("t1", None, ["c1"]), "seq": 3},
        _user("t1", "乙"), _assistant("t1", "丙"),
    ])
    loaded = slog.load_session(slog.read_log(ep), "sid-a")
    repairs = slog.plan_repairs(loaded)
    lease = slog.EpisodeLease.acquire(ep)
    lease.begin("sid-a", resumed_from_seq=loaded.last_seq)
    for record in repairs:
        lease.append(record)

    rebuilt = slog.rebuild_messages(slog.load_session(lease.read(), "sid-a"))
    assert [m["role"] for m in rebuilt] == ["user", "assistant", "tool", "user", "assistant"]
    assert rebuilt[2]["tool_call_id"] == "c1"


# ---------------------------------------------------------------------------
# TS-8 / TS-8b / TS-13
# ---------------------------------------------------------------------------


def test_ts8_resume_prefers_the_last_session_with_assistant_messages(ep: Path) -> None:
    """TS-8：3 个会话，第 3 个没有 assistant 消息 → 默认恢复第 2 个；显式前缀恢复第 3 个。"""
    _write(ep, [
        _start("aaa1"), _user("t1", "甲"), _assistant("t1", "乙"),
        _start("bbb2"), _user("t1", "丙"), _assistant("t1", "丁"),
        _start("ccc3"), _user("t1", "戊"),
    ])
    summaries = slog.list_sessions(slog.read_log(ep))
    assert [s.sid for s in summaries] == ["aaa1", "bbb2", "ccc3"]

    target, ambiguous = slog.resume_target(summaries)
    assert ambiguous == [] and target is not None and target.sid == "bbb2"

    explicit, ambiguous = slog.resume_target(summaries, "ccc")
    assert ambiguous == [] and explicit is not None and explicit.sid == "ccc3"

    ambiguous_target, candidates = slog.resume_target(summaries, "")
    assert ambiguous_target is None or candidates == []  # 空前缀等价于不指定


def test_ts8b_interleaved_records_aggregate_by_sid(ep: Path) -> None:
    """TS-8b：记录交错时按 sid 正确聚合（恢复较早会话时段尾追加）。"""
    _write(ep, [_start("aaa1"), _user("t1", "甲")])
    _write(ep, [_start("bbb2"), _user("t1", "乙")])
    lease = slog.EpisodeLease.acquire(ep)
    lease.begin("aaa1", resumed_from_seq=99)
    lease.append({"k": "segment_start", "resident_sha256": "def", "pid": 1, "resumed_from_seq": 2})
    lease.append(_user("t2", "丙"))

    raw = slog.read_log(ep)
    first = slog.rebuild_messages(slog.load_session(raw, "aaa1"))
    second = slog.rebuild_messages(slog.load_session(raw, "bbb2"))
    assert first == [{"role": "user", "content": "甲"}, {"role": "user", "content": "丙"}]
    assert second == [{"role": "user", "content": "乙"}]
    assert slog.load_session(raw, "aaa1").last_seq == 101


def test_ts13_sessions_list_reads_without_truncating_under_foreign_lock(ep: Path) -> None:
    """TS-13：另一进程持租约时 `--sessions` 读一个末尾有残行的文件 → 列表正确、文件字节不变。"""
    _write(ep, [_start("sid-a"), _user("t1", "甲"), _assistant("t1", "乙")])
    with open(ep / slog.LOG_NAME, "ab") as handle:
        handle.write(b'{"k": "msg", "sid": "sid-a"')
    before = (ep / slog.LOG_NAME).read_bytes()
    slog.EpisodeLease._reset_for_testing()   # 本进程先放开租约，交给另一个进程持有

    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, sys, time\n"
        f"fd = open({str(ep / slog.LOG_NAME)!r}, 'a')\n"
        "fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "print('locked', flush=True)\n"
        "time.sleep(30)\n"
    )], stdout=subprocess.PIPE)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == b"locked"
        summaries = slog.list_sessions(slog.read_log(ep))  # 未持锁读：只忽略残行
        assert [s.sid for s in summaries] == ["sid-a"]
        assert summaries[0].messages == 2 and summaries[0].assistants == 1
        assert (ep / slog.LOG_NAME).read_bytes() == before
        with pytest.raises(slog.SessionLocked):
            slog.EpisodeLease.acquire(ep)
    finally:
        holder.kill()
        holder.wait(timeout=5)

    assert (ep / slog.LOG_NAME).read_bytes() == before
    # 持锁进程被杀后立即可取（持锁者死亡 = 自动释放，进程级 flock 的性质）
    assert slog.EpisodeLease.acquire(ep) is not None


# ---------------------------------------------------------------------------
# TS-9：观测层不参与状态（门禁 12）
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent


def test_ts9_status_json_does_not_read_the_session_log(ep: Path) -> None:
    """TS-9：删掉 `session.jsonl` 前后 `python -m pipeline.status <期> --json` 逐字节相同。

    观测层（status）不许读会话日志，否则「观测」就参与了状态。走真子进程 + 真 argv；
    期目录用 tmp 里的绝对路径（`status.main` 只按 argv 解析，不碰真 data）。
    MUT-26 给 status.py 加一行读会话日志 → 输出就不同。
    """
    _write(ep, [_start("sid-a"), _user("t1", "甲"), _assistant("t1", "乙")])
    log = ep / slog.LOG_NAME
    assert log.exists()

    def status_json() -> bytes:
        proc = subprocess.run(
            [sys.executable, "-m", "pipeline.status", str(ep), "--json"],
            capture_output=True, cwd=REPO,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]
        return proc.stdout

    before = status_json()
    assert before.strip().startswith(b"{"), f"输出不是 JSON（这条断言会空转）：{before[:120]!r}"
    log.unlink()
    assert status_json() == before, "status --json 的输出不许因为会话日志的有无而改变（MUT-26）"


# ---------------------------------------------------------------------------
# D64：恢复时带回的上下文读数
# ---------------------------------------------------------------------------


def _end(turn: str, tokens, chars) -> dict:
    return {"k": "turn_end", "turn_id": turn, "stopped": "done", "prompt_tokens": tokens, "prompt_chars": chars}


@pytest.mark.parametrize(("ends", "want"), [
    ([], (None, None)),
    ([_end("t1", 100, 50), _end("t2", 900, 400)], (900, 400)),          # 取最后一次
    ([_end("t1", 900, 400), _end("t2", None, 0)], (900, 400)),          # 没测到的回合不覆盖
    ([_end("t1", None, 400)], (None, 400)),                             # 服务商不给 token：只带字数
    ([_end("t1", 0, 400)], (0, 400)),                                    # 0 token 是测出来的值
    ([_end("t1", True, 400)], (None, 400)),                              # bool 不是计数
    ([_end("t1", -1, -5)], (None, None)),
])
def test_d64_last_context_reading(ep: Path, ends, want) -> None:
    _write(ep, [_start("sid-a"), _user("t1", "甲"), _assistant("t1", "乙"), *ends])
    loaded = slog.load_session(slog.read_log(ep), "sid-a")
    assert slog.last_context_reading(loaded) == want


def test_d64_last_context_reading_skips_rolled_back_turn(ep: Path) -> None:
    """回滚回合的消息不在重建历史里，它的读数偏大，不能带回。"""
    _write(ep, [
        _start("sid-a"), _user("t1", "甲"), _assistant("t1", "乙"), _end("t1", 900, 400),
        _user("t2", "丙"), {"k": "turn_rollback", "turn_id": "t2"}, _end("t2", 5000, 2000),
    ])
    loaded = slog.load_session(slog.read_log(ep), "sid-a")
    assert slog.last_context_reading(loaded) == (900, 400)
