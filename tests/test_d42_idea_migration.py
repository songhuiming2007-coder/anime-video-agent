"""D42 / Spec 18（选题会话落盘与建期迁移）的新增验收用例 T-D42-1 ~ T-D42-9、T-D42-3b。

对应 Spec 18 §6「新增」清单；TP-16 的改写在 test_agent_protocol.py 原处。变异 MUT-D42-a ~ h
的指定杀手见各用例 docstring（登记在 scripts/verify_mutations.py 的 shipped 矩阵）。

两类驱动：协议子进程（真 `python -m pipeline.agent.protocol`，复用 TP-* 的假端点与 import 钩子），
以及进程内调 `cli.main(["new", ..., "--from-idea"])`（`paths.ROOT` 指向临时根）。
一律在 tmp 假仓库根上跑，绝不碰真实 data/。
"""

from __future__ import annotations

import json
import re
import shutil
import stat
from pathlib import Path

import pytest

from pipeline import paths
from pipeline.agent import cli
from pipeline.agent.session import SessionHost, prepare_resume
from pipeline.agent.session_log import (
    LOG_NAME,
    EpisodeLease,
    list_sessions,
    load_session,
    rebuild_messages,
)
from tests.test_agent_protocol import (  # noqa: F401  （fixture 经导入生效）
    Protocol,
    endpoint,
    tool_call,
    world,
)

REPO = Path(__file__).resolve().parent.parent
MARKER_RE = re.compile(r"^\[from-idea\] migrated=(true|false) sid=([0-9a-f]+|-) messages=(\d+)$")


# ---------------------------------------------------------------------------
# 夹具与小工具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_leases():
    EpisodeLease._reset_for_testing()
    yield
    EpisodeLease._reset_for_testing()


class _Channel:
    """进程内恢复用的哑通道（prepare_resume 不问人）。"""

    def show(self, kind, payload):  # noqa: ANN001
        return None

    def ask(self, request):  # noqa: ANN001
        raise AssertionError("恢复路径不该问人")


def _bare_root(tmp_path: Path) -> Path:
    """最小合法骨架（`require_data_at` 要 data/library 与 data/episodes）。"""
    root = tmp_path / "repo"
    (root / "data" / "episodes").mkdir(parents=True)
    (root / "data" / "library").mkdir(parents=True)
    return root


def _idea_log(root: Path) -> Path:
    return root / "data" / "_idea" / LOG_NAME


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _seed_log(log_dir: Path, sid: str, messages: list[dict], *, extra: list[dict] | None = None) -> None:
    """用生产写者（EpisodeLease.append）造一段会话记录：session_start + 一个回合的消息。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    lease = EpisodeLease.acquire(log_dir)
    try:
        lease.begin(sid)
        lease.append({"k": "session_start", "schema": 1, "episode": "", "scope_mode": "idea",
                      "resident_sha256": "0" * 64, "pid": 1, "turn_id": "t0"})
        lease.append({"k": "turn_start", "scope": "idea", "step_key": None, "turn_id": "t1"})
        for message in messages:
            lease.append({"k": "msg", "message": message, "origin": message["role"], "turn_id": "t1"})
        for record in extra or []:
            lease.append(record)
        lease.append({"k": "turn_end", "stopped": "done", "turn_id": "t1"})
    finally:
        lease.close()


def _new_from_idea(root: Path, monkeypatch, capsys, name: str = "02-x") -> tuple[int, str, str]:
    monkeypatch.setattr(paths, "ROOT", root)
    rc = cli.main(["new", name, "--from-idea"])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _markers(stdout: str) -> list[re.Match]:
    return [m for m in (MARKER_RE.match(line) for line in stdout.splitlines()) if m]


def _copy_resident(root: Path) -> None:
    """把常驻层与 01 规程复制进假仓库根（复制而非软链：装配器按白名单根 resolve 后判定）。"""
    for rel in ("config/agent/assembly.json", "AGENTS.md", "docs/WORKFLOW.md",
                "docs/runbook/01-topic.md"):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest)
    shutil.copytree(REPO / "config" / "agent" / "scopes", root / "config" / "agent" / "scopes",
                    dirs_exist_ok=True)


# ---------------------------------------------------------------------------
# T-D42-1：落盘与恒恢复（MUT-D42-e 的杀手）
# ---------------------------------------------------------------------------


def test_t_d42_1_idea_persists_and_always_resumes(world, endpoint, tmp_path) -> None:
    """T-D42-1：idea 两轮后退出 → `_idea/session.jsonl` 含两轮消息；再开 `--idea`（不带
    --continue）→ resumed、历史回放进 messages，常驻层按 idea 现装；期列表不含 `_idea`。

    杀 MUT-D42-e（`--idea` 恒开新段不恢复）：第二个进程 continue_status 不是 resumed、
    请求里没有第一个进程的对话。
    """
    root, _episode = world
    endpoint.replies = [{"role": "assistant", "content": "先聊聊"}, {"role": "assistant", "content": "接着聊"}]
    first = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "a")
    try:
        ready = first.wait_for_ready()
        assert ready["continue_status"] == "new"
        for text in ("想做一期杂谈", "换个角度"):
            first.send({"t": "user_message", "text": text})
            first.wait_for("turn_finished")
            first.wait_for("stop_points")
        first.shutdown()
    finally:
        assert first.finish() == 0
    log = _idea_log(root)
    msgs = [r["message"] for r in _records(log) if r.get("k") == "msg"]
    assert [m["content"] for m in msgs if m["role"] in ("user", "assistant")] == [
        "想做一期杂谈", "先聊聊", "换个角度", "接着聊"
    ]

    endpoint.replies = [{"role": "assistant", "content": "还记得"}]
    mark = len(endpoint.requests)
    second = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "b")
    try:
        ready = second.wait_for_ready()
        assert ready["continue_status"] == "resumed"
        assert ready["history_count"] >= 4
        history = [f["text"] for f in second.frames if f.get("t") == "history"]
        assert "想做一期杂谈" in history and "接着聊" in history
        second.send({"t": "user_message", "text": "刚才聊到哪了"})
        second.wait_for("turn_finished")
        second.shutdown()
    finally:
        assert second.finish() == 0
    request = endpoint.requests[mark]["messages"]
    contents = [m.get("content") for m in request]
    assert {"想做一期杂谈", "先聊聊", "换个角度", "接着聊", "刚才聊到哪了"} <= set(contents)
    # messages[0] 是本进程现装的 idea 常驻层 + idea 状态卡（不取盘上那份）
    assert request[0]["role"] == "system" and "模式: 选题会话（无期）" in request[0]["content"]
    episodes, _hidden = cli.get_episodes_list(root=root)
    assert "_idea" not in {p.name for p in episodes}


# ---------------------------------------------------------------------------
# T-D42-2：迁移全链（MUT-D42-a / MUT-D42-b 的杀手）
# ---------------------------------------------------------------------------


def test_t_d42_2_migration_full_chain(world, endpoint, tmp_path, monkeypatch, capsys) -> None:
    """T-D42-2：真 idea 进程聊一轮 → `ava new 02-x --from-idea` → 返回 0、marker 恰好一行
    migrated=true；新期 `session.jsonl` 与原段逐字节一致；`_idea/session.jsonl` 为空文件；
    新期 `--continue` → 请求含 idea 回合的用户与 assistant 原文，常驻层已是 creative + 01 规程。

    杀 MUT-D42-a（只建期不复制）：新期无 session.jsonl、字节不一致。
    杀 MUT-D42-b（复制后不清空）：`_idea` 不是空文件。
    """
    root, _episode = world
    _copy_resident(root)
    endpoint.replies = [{"role": "assistant", "content": "草案：张力是「守护与放手」"}]
    idea = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "a")
    try:
        idea.wait_for_ready()
        idea.send({"t": "user_message", "text": "帮我定一个选题草案"})
        idea.wait_for("turn_finished")
        idea.shutdown()
    finally:
        assert idea.finish() == 0
    source = _idea_log(root).read_bytes()
    sid = list_sessions(source)[-1].sid

    rc, out, err = _new_from_idea(root, monkeypatch, capsys)
    assert rc == 0, err
    markers = _markers(out)
    assert len(markers) == 1, out
    assert markers[0].group(1) == "true" and markers[0].group(2) == sid
    assert int(markers[0].group(3)) == list_sessions(source)[-1].messages
    new_ep = root / "data" / "episodes" / "02-x"
    assert (new_ep / LOG_NAME).is_file(), "新期没有会话记录：迁移只建了期、没复制"
    assert (new_ep / LOG_NAME).read_bytes() == source
    assert _idea_log(root).is_file() and _idea_log(root).stat().st_size == 0

    endpoint.replies = [{"role": "assistant", "content": "好，接着写"}]
    mark = len(endpoint.requests)
    cont = Protocol(root, endpoint, episode="02-x", extra=["--continue"], tmp=tmp_path / "b")
    try:
        ready = cont.wait_for_ready()
        assert ready["continue_status"] == "resumed"
        cont.send({"t": "user_message", "text": "把刚才的草案写进 01-topic.md"})
        cont.wait_for("turn_finished")
        cont.shutdown()
    finally:
        assert cont.finish() == 0
    request = endpoint.requests[mark]["messages"]
    contents = [str(m.get("content")) for m in request]
    assert "帮我定一个选题草案" in contents and "草案：张力是「守护与放手」" in contents
    assert "Creative" in request[0]["content"] or "creative" in request[0]["content"]
    assert "模式: 选题会话（无期）" not in request[0]["content"]
    runbook = (REPO / "docs" / "runbook" / "01-topic.md").read_text(encoding="utf-8").strip()
    assert any(c.startswith("[系统提示更新]") and runbook[:200] in c for c in contents), \
        "新期首回合应按 creative + 01 工序注入规程"


# ---------------------------------------------------------------------------
# T-D42-3：原子性（MUT-D42-c 的杀手）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["dir_at_log_path", "readonly_episode_dir"])
def test_t_d42_3_copy_failure_keeps_empty_episode(tmp_path, monkeypatch, capsys, how) -> None:
    """T-D42-3：复制失败（新期 session.jsonl 路径被目录占住 / 期目录只读）→ 退出非零、
    stderr 写明「建期成功、迁移失败」、新期空且合法（目录与模板在、无半份）、`_idea` 逐字节不动。

    杀 MUT-D42-c（复制失败时回滚建期）：新期目录不在了。
    """
    root = _bare_root(tmp_path)
    _seed_log(root / "data" / "_idea", "a" * 16, [
        {"role": "user", "content": "选题"}, {"role": "assistant", "content": "草案"},
    ])
    before = _idea_log(root).read_bytes()
    new_ep = root / "data" / "episodes" / "02-x"
    real_create = cli.create_new_episode

    def create_then_sabotage(name: str) -> int:
        rc = real_create(name)
        if how == "dir_at_log_path":
            (new_ep / LOG_NAME).mkdir()
        else:
            new_ep.chmod(stat.S_IRUSR | stat.S_IXUSR)
        return rc

    monkeypatch.setattr(cli, "create_new_episode", create_then_sabotage)
    try:
        rc, out, err = _new_from_idea(root, monkeypatch, capsys)
    finally:
        if new_ep.exists():
            new_ep.chmod(stat.S_IRWXU)
    assert rc == cli.RC_MIGRATE_FAILED
    assert "建期成功、迁移失败" in err
    assert not _markers(out)
    assert new_ep.is_dir() and (new_ep / "01-topic.md").is_file()
    assert not (new_ep / LOG_NAME).is_file(), "新期不许出现半份或整份会话记录"
    assert sorted(p.name for p in new_ep.iterdir()) == (
        ["01-topic.md", LOG_NAME] if how == "dir_at_log_path" else ["01-topic.md"]
    ), "临时文件必须清掉"
    assert _idea_log(root).read_bytes() == before


# ---------------------------------------------------------------------------
# T-D42-3b：清空失败（MUT-D42-h 的杀手）
# ---------------------------------------------------------------------------


def test_t_d42_3b_clear_failure_is_loud(tmp_path, monkeypatch, capsys) -> None:
    """T-D42-3b：清空步骤失败 → 退出非零、stderr 明示「建期成功、迁移成功、清空失败……请手动清空」、
    新期副本完整、`_idea` 逐字节不动、不输出 migrated=true 的 marker。

    杀 MUT-D42-h（清空失败静默返回 0）。
    """
    root = _bare_root(tmp_path)
    _seed_log(root / "data" / "_idea", "b" * 16, [
        {"role": "user", "content": "选题"}, {"role": "assistant", "content": "草案"},
    ])
    before = _idea_log(root).read_bytes()

    def broken_clear(self) -> None:  # noqa: ANN001
        raise OSError(5, "注入：ftruncate 失败")

    monkeypatch.setattr(EpisodeLease, "clear", broken_clear)
    rc, out, err = _new_from_idea(root, monkeypatch, capsys)
    assert rc == cli.RC_CLEAR_FAILED
    assert "建期成功、迁移成功、清空失败" in err and "请手动清空 data/_idea/session.jsonl" in err
    assert not _markers(out)
    assert (root / "data" / "episodes" / "02-x" / LOG_NAME).read_bytes() == before
    assert _idea_log(root).read_bytes() == before


# ---------------------------------------------------------------------------
# T-D42-4：无记录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["no_idea_dir", "user_only"])
def test_t_d42_4_nothing_to_migrate(tmp_path, monkeypatch, capsys, shape) -> None:
    """T-D42-4：`_idea` 不存在 / 只有 user 无 assistant 的段 → migrated=false、返回 0、新期为空；
    前者不顺手建 `_idea`，后者 `_idea` 记录不动（没东西可带就不清）。"""
    root = _bare_root(tmp_path)
    if shape == "user_only":
        _seed_log(root / "data" / "_idea", "c" * 16, [{"role": "user", "content": "只说了一句"}])
    before = _idea_log(root).read_bytes() if shape == "user_only" else None
    rc, out, err = _new_from_idea(root, monkeypatch, capsys)
    assert rc == 0, err
    markers = _markers(out)
    assert len(markers) == 1 and markers[0].groups() == ("false", "-", "0")
    new_ep = root / "data" / "episodes" / "02-x"
    assert sorted(p.name for p in new_ep.iterdir()) == ["01-topic.md"]
    if shape == "no_idea_dir":
        assert not (root / "data" / "_idea").exists()
    else:
        assert _idea_log(root).read_bytes() == before


# ---------------------------------------------------------------------------
# T-D42-5：隐藏（MUT-D42-d 的杀手）
# ---------------------------------------------------------------------------


def test_t_d42_5_idea_dir_never_listed_as_episode(tmp_path, monkeypatch, capsys) -> None:
    """T-D42-5：`data/_idea` 在场时，期列表（`get_episodes_list`，也是 `list_episodes` 工具与
    `ava` 看板的唯一来源）、看板输出、`resolve_episode_target` 都见不到它。

    杀 MUT-D42-d（`_idea` 进期列表）。
    """
    root = tmp_path / "repo"
    ep = root / "data" / "episodes" / "01-a"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    _seed_log(root / "data" / "_idea", "d" * 16, [
        {"role": "user", "content": "选题"}, {"role": "assistant", "content": "草案"},
    ])
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.chdir(tmp_path)
    episodes, _hidden = cli.get_episodes_list(root=root)
    assert [p.name for p in episodes] == ["01-a"]

    from pipeline.agent.tools import ToolContext, execute_tool

    listed = execute_tool("list_episodes", {}, ToolContext(scope="idea", episode_dir=None, root=root))
    assert "_idea" not in json.dumps(listed, ensure_ascii=False)
    assert cli.resolve_episode_target("_idea") is None
    capsys.readouterr()
    assert cli.main([]) == 0  # 非 TTY：只打看板
    assert "_idea" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# T-D42-6：转正后可写
# ---------------------------------------------------------------------------


def test_t_d42_6_migrated_session_can_write_topic(world, endpoint, tmp_path, monkeypatch, capsys) -> None:
    """T-D42-6：迁移后新期会话里模型调 write_episode_file("01-topic.md") → 弹人审卡（不再是
    「先建期」），批准后落盘。"""
    root, _episode = world
    _seed_log(root / "data" / "_idea", "e" * 16, [
        {"role": "user", "content": "选题"}, {"role": "assistant", "content": "草案：番：X"},
    ])
    rc, _out, err = _new_from_idea(root, monkeypatch, capsys)
    assert rc == 0, err
    body = "# 02-x 选题配置\n\n番：X\n类型：人物志\n"
    endpoint.replies = [
        tool_call("write_episode_file", {"filename": "01-topic.md", "content": body}),
        {"role": "assistant", "content": "写好了"},
    ]
    proc = Protocol(root, endpoint, episode="02-x", extra=["--continue"], tmp=tmp_path / "b")
    try:
        assert proc.wait_for_ready()["continue_status"] == "resumed"
        proc.send({"t": "user_message", "text": "把刚才的草案写进 01-topic.md"})
        request = proc.wait_for("request")
        assert request["kind"] == "tool_call"
        proc.send({"t": "answer", "request_id": request["request_id"], "decision": "approve",
                   "feedback": None})
        proc.wait_for("turn_finished")
        proc.shutdown()
    finally:
        assert proc.finish() == 0
    assert (root / "data" / "episodes" / "02-x" / "01-topic.md").read_text(encoding="utf-8") == body


# ---------------------------------------------------------------------------
# T-D42-7：单一选题会话（真子进程）
# ---------------------------------------------------------------------------


def test_t_d42_7_single_idea_session_across_processes(world, endpoint, tmp_path, monkeypatch, capsys) -> None:
    """T-D42-7：第一个 `--idea` 进程存活时，第二个 `--idea` 进程拿到锁冲突错误、不开第二份；
    同一时刻 `ava new --from-idea` 也拿不到 `_idea` 租约 → 退出非零、新期留空期、选题记录不动。

    必须真子进程：`_LEASES` 进程内单例会让同进程双 acquire 假绿（🔵-4）。
    """
    root, _episode = world
    endpoint.replies = [{"role": "assistant", "content": "先聊聊"}]
    first = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "a")
    try:
        first.wait_for_ready()
        first.send({"t": "user_message", "text": "想做一期杂谈"})
        first.wait_for("turn_finished")
        second = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "b")
        try:
            error = second.wait_for("error")
            assert error["code"] == "E_SESSION_LOCKED"
            assert "另一个选题会话进行中" in error["message"]
            assert second.finish() == 3
            assert "ready" not in second.kinds()
        finally:
            second.proc.kill()

        before = _idea_log(root).read_bytes()
        rc, out, err = _new_from_idea(root, monkeypatch, capsys)
        assert rc == cli.RC_IDEA_BUSY
        assert "选题会话进行中" in err and not _markers(out)
        assert sorted(p.name for p in (root / "data" / "episodes" / "02-x").iterdir()) == ["01-topic.md"]
        assert _idea_log(root).read_bytes() == before
        first.shutdown()
    finally:
        assert first.finish() == 0


# ---------------------------------------------------------------------------
# T-D42-8：启动与降级路径（MUT-D42-f 的杀手）
# ---------------------------------------------------------------------------


def test_t_d42_8a_missing_idea_dir_is_created(world, endpoint, tmp_path) -> None:
    """T-D42-8 ①：夹具**不**预建 `_idea`——首启由 core 自建，会话正常落盘。"""
    root, _episode = world
    assert not (root / "data" / "_idea").exists()
    endpoint.replies = [{"role": "assistant", "content": "好"}]
    proc = Protocol(root, endpoint, episode="--idea", tmp=tmp_path)
    try:
        proc.wait_for_ready()
        proc.send({"t": "user_message", "text": "开个头"})
        proc.wait_for("turn_finished")
        proc.shutdown()
    finally:
        assert proc.finish() == 0
    assert [r["message"]["content"] for r in _records(_idea_log(root))
            if r.get("k") == "msg" and r.get("origin") == "user"] == ["开个头"]


def _first_of(proc: Protocol, kinds: set[str], timeout: float = 30.0) -> dict:
    """读帧直到出现 kinds 里的任一类，返回那一帧——「先到 ready 还是先到 error」当场判，不靠等超时。"""
    import queue
    import time

    end = time.time() + timeout
    while time.time() < end:
        for frame in proc.frames:
            if frame.get("t") in kinds:
                return frame
        try:
            proc.next_frame(timeout=max(0.1, end - time.time()))
        except queue.Empty:
            break
    raise AssertionError(f"{timeout}s 内既没有 {sorted(kinds)}；已收到 {proc.kinds()}")


def _make_data_dangling(root: Path, tmp_path: Path) -> None:
    shutil.move(str(root / "data"), str(tmp_path / "data-away"))
    (root / "data").symlink_to(tmp_path / "unmounted-volume" / "data")


def test_t_d42_8b_data_unreachable_refuses_idea(world, endpoint, tmp_path, monkeypatch, capsys) -> None:
    """T-D42-8 ②：`data/` 悬空 → 协议 `--idea` 回 E_DATA_UNREACHABLE 退 4；终端 idea 退 2——与期会话同闸。"""
    root, _episode = world
    _make_data_dangling(root, tmp_path)
    proc = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "p")
    try:
        error = proc.wait_for("error")
        assert error["code"] == "E_DATA_UNREACHABLE"
        assert proc.finish() == 4
        assert "ready" not in proc.kinds()
    finally:
        proc.proc.kill()

    import pipeline.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "load_llm_config",
                        lambda r=None: llm_mod.LLMConfig(base_url="http://x", model="m", api_key="k"))
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("data/ 不可达时不该进入对话"))
    assert cli.run_agent_loop(None, scope_mode="idea", root=root) == 2
    assert "data/ 不可达" in capsys.readouterr().err


def test_t_d42_8c_lease_failure_refuses_idea(world, endpoint, tmp_path, monkeypatch, capsys) -> None:
    """T-D42-8 ③：租约拿不到（`_idea` 被一个普通文件占住，建不了目录）→ 协议显式错误帧、退 3、
    不发 ready；终端退 3。选题会话禁止静默非持久运行。

    杀 MUT-D42-f（租约失败被静默吞，idea 退回非持久）：协议进程照发 ready、开得了回合。
    """
    root, _episode = world
    (root / "data" / "_idea").write_text("占位", encoding="utf-8")
    proc = Protocol(root, endpoint, episode="--idea", tmp=tmp_path / "p")
    try:
        error = _first_of(proc, {"error", "ready"})
        assert error["t"] == "error", f"租约失败却发了 ready（静默非持久运行）：{proc.kinds()}"
        assert error["code"] == "E_SESSION_LOCKED"
        assert "选题会话记录目录" in error["message"]
        assert proc.finish() == 3
        assert "ready" not in proc.kinds()
    finally:
        proc.proc.kill()

    import pipeline.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "load_llm_config",
                        lambda r=None: llm_mod.LLMConfig(base_url="http://x", model="m", api_key="k"))
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("租约失败时不该进入对话"))
    assert cli.run_agent_loop(None, scope_mode="idea", root=root) == 3
    assert "选题会话记录目录" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T-D42-9：plan_repairs 不重复修复（MUT-D42-g 的杀手）
# ---------------------------------------------------------------------------


_CRASHED = [
    {"role": "user", "content": "跑个工具"},
    {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-x", "type": "function", "function": {"name": "read_status", "arguments": "{}"},
    }]},
]


def _resume_once(log_dir: Path, sid: str) -> list[dict]:
    host = SessionHost(None, channel=_Channel(), log_dir=log_dir)
    host.lease = EpisodeLease.acquire(log_dir)
    try:
        state = prepare_resume(host, sid)
        assert state["status"] == "resumed"
        return state["messages"]
    finally:
        host.lease.close()


def _tool_messages(messages: list[dict], call_id: str) -> list[dict]:
    return [m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == call_id]


def _repair_records(path: Path) -> list[dict]:
    return [r for r in _records(path) if r.get("k") == "repair_tool_results"]


def test_t_d42_9a_double_resume_does_not_repeat_repair(tmp_path) -> None:
    """T-D42-9 ①（红队探针 probe_double_resume 的回归化）：缺 tool 结果的崩溃段纯单期两次恢复——
    第一次补一份修复，第二次不再补；重建出的 tool 消息恰一条。"""
    ep = tmp_path / "ep"
    sid = "f" * 16
    _seed_log(ep, sid, _CRASHED)
    first = _resume_once(ep, sid)
    assert len(_tool_messages(first, "call-x")) == 1
    assert len(_repair_records(ep / LOG_NAME)) == 1
    second = _resume_once(ep, sid)
    assert len(_tool_messages(second, "call-x")) == 1, "同一 tool_call_id 重建出了两条 tool 消息"
    assert len(_repair_records(ep / LOG_NAME)) == 1


def test_t_d42_9b_migrated_idea_with_repair_not_repeated(tmp_path, monkeypatch, capsys) -> None:
    """T-D42-9 ②：idea 崩溃 → 下次 idea 恒恢复（修复#1 落 `_idea`）→ 迁移进新期 → 新期 --continue
    （不许出现修复#2）；重建的 tool 消息恰一条、新期盘上 repair 记录仍是 1 条。"""
    root = _bare_root(tmp_path)
    idea_dir = root / "data" / "_idea"
    sid = "9" * 16
    _seed_log(idea_dir, sid, [*_CRASHED, {"role": "assistant", "content": "（恢复后）接着聊"}])
    _resume_once(idea_dir, sid)  # 修复#1
    assert len(_repair_records(_idea_log(root))) == 1
    rc, out, err = _new_from_idea(root, monkeypatch, capsys)
    assert rc == 0 and _markers(out)[0].group(1) == "true", err
    new_log = root / "data" / "episodes" / "02-x"
    messages = _resume_once(new_log, sid)
    assert len(_tool_messages(messages, "call-x")) == 1
    assert len(_repair_records(new_log / LOG_NAME)) == 1
    # 盘上读回与内存一致（rebuild 走的是同一套记录）
    assert len(_tool_messages(rebuild_messages(load_session((new_log / LOG_NAME).read_bytes(), sid)),
                              "call-x")) == 1


# ---------------------------------------------------------------------------
# D58（2026-10-09）：选题会话可存多段——指定段恢复 / 新开 / 删除 / 建期只带一段
# ---------------------------------------------------------------------------

SID_OLD, SID_NEW, SID_BARE = "a" * 16, "b" * 16, "c" * 16


def _two_idea_sessions(root: Path) -> tuple[bytes, bytes]:
    """`_idea` 里先后两段（旧：甲，新：乙）；返回各自的整行字节（按 sid 拆，顺序同文件）。"""
    from pipeline.agent.session_log import split_by_sid

    _seed_log(root / "data" / "_idea", SID_OLD, [
        {"role": "user", "content": "选题甲"}, {"role": "assistant", "content": "草案甲"}])
    _seed_log(root / "data" / "_idea", SID_NEW, [
        {"role": "user", "content": "选题乙"}, {"role": "assistant", "content": "草案乙"}])
    raw = _idea_log(root).read_bytes()
    old, _ = split_by_sid(raw, SID_OLD)
    new, _ = split_by_sid(raw, SID_NEW)
    return b"".join(old), b"".join(new)


def test_d58_protocol_idea_continue_sid_and_fresh(world, endpoint, tmp_path) -> None:
    """`--idea --continue <前缀>` 恢复指定段；`--idea --fresh` 开新段不回放；裸 `--idea` 照旧恢复最近段。"""
    root, _episode = world
    _two_idea_sessions(root)

    def history_of(extra: list[str], tag: str) -> tuple[str, list[str]]:
        proc = Protocol(root, endpoint, episode="--idea", extra=extra, tmp=tmp_path / tag)
        try:
            ready = proc.wait_for_ready()
            texts = [f["text"] for f in proc.frames if f.get("t") == "history"]
            proc.shutdown()
        finally:
            assert proc.finish() == 0
        return ready["continue_status"], texts

    status, texts = history_of(["--continue", SID_OLD[:8]], "old")
    assert status == "resumed" and "选题甲" in texts and "选题乙" not in texts
    status, texts = history_of(["--fresh"], "fresh")
    assert status == "new" and texts == []
    status, texts = history_of([], "bare")
    assert status == "resumed" and "选题乙" in texts and "选题甲" not in texts


def test_d58_protocol_idea_bad_flag_combos(world, endpoint, tmp_path) -> None:
    root, _episode = world
    for i, extra in enumerate((["--continue"], ["--fresh", "--continue", "aaaa"])):
        proc = Protocol(root, endpoint, episode="--idea", extra=extra, tmp=tmp_path / f"bad{i}")
        assert proc.finish() == 2, extra


def test_d58_from_idea_sid_moves_only_that_session(tmp_path, monkeypatch, capsys) -> None:
    """人裁决「只带当前这段」：`--from-idea=<旧段>` → 新期只有旧段，`_idea` 只剩新段（逐字节）。"""
    root = _bare_root(tmp_path)
    old, new = _two_idea_sessions(root)
    monkeypatch.setattr(paths, "ROOT", root)
    assert cli.main(["new", "02-x", f"--from-idea={SID_OLD}"]) == 0
    out = capsys.readouterr().out
    assert [m.group(0) for m in _markers(out)] == [f"[from-idea] migrated=true sid={SID_OLD} messages=2"]
    assert (root / "data" / "episodes" / "02-x" / LOG_NAME).read_bytes() == old
    assert _idea_log(root).read_bytes() == new


def test_d58_from_idea_without_sid_takes_latest(tmp_path, monkeypatch, capsys) -> None:
    root = _bare_root(tmp_path)
    old, new = _two_idea_sessions(root)
    rc, out, _err = _new_from_idea(root, monkeypatch, capsys)
    assert rc == 0 and f"sid={SID_NEW}" in out
    assert (root / "data" / "episodes" / "02-x" / LOG_NAME).read_bytes() == new
    assert _idea_log(root).read_bytes() == old


def test_d58_from_idea_unresumable_sid_does_not_fall_back(tmp_path, monkeypatch, capsys) -> None:
    """指定段还没有回复（不可恢复）→ 不带，也不退回去带最近的别的段；`_idea` 一字不动。"""
    root = _bare_root(tmp_path)
    _two_idea_sessions(root)
    _seed_log(root / "data" / "_idea", SID_BARE, [{"role": "user", "content": "只说了一句"}])
    before = _idea_log(root).read_bytes()
    monkeypatch.setattr(paths, "ROOT", root)
    assert cli.main(["new", "02-x", f"--from-idea={SID_BARE}"]) == 0
    assert [m.group(0) for m in _markers(capsys.readouterr().out)] == ["[from-idea] migrated=false sid=- messages=0"]
    assert not (root / "data" / "episodes" / "02-x" / LOG_NAME).exists()
    assert _idea_log(root).read_bytes() == before


def test_d58_from_idea_rejects_malformed_sid(tmp_path, monkeypatch) -> None:
    root = _bare_root(tmp_path)
    monkeypatch.setattr(paths, "ROOT", root)
    assert cli.main(["new", "02-x", "--from-idea=../x"]) == 2
    assert not (root / "data" / "episodes" / "02-x").exists()


def test_d58_idea_delete_session_moves_to_trash(tmp_path, monkeypatch, capsys) -> None:
    """`ava idea /delete-session --sid=` → 该段整行进 `_idea/_agent/session-trash/`，其余逐字节不变；租约被占 → 3。"""
    root = _bare_root(tmp_path)
    old, new = _two_idea_sessions(root)
    monkeypatch.setattr(paths, "ROOT", root)
    import subprocess
    import sys
    import textwrap

    # 另一个进程持 `_idea` 租约（选题会话正在进行）→ 拒删、一字不动（同进程重复取租约是可重入的，造不出冲突）
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import time
            from pipeline.agent.session_log import EpisodeLease
            EpisodeLease.acquire({str(root / "data" / "_idea")!r}); print('held', flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        assert cli.main(["idea", "/delete-session", f"--sid={SID_OLD}"]) == 3
        assert _idea_log(root).read_bytes() == old + new
    finally:
        holder.kill()
        holder.wait()
    capsys.readouterr()
    assert cli.main(["idea", "/delete-session", f"--sid={SID_OLD}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["moved"] == old.count(b"\n")
    assert Path(out["trash"]).read_bytes() == old
    assert Path(out["trash"]).parent == root / "data" / "_idea" / "_agent" / "session-trash"
    assert _idea_log(root).read_bytes() == new
    assert cli.main(["idea", "/delete-session", "--sid=bad"]) == 2
