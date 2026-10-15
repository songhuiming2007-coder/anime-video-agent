"""D83：run_pipeline 的结果出网前过 scrub_secrets（与 bash 对齐）。

缺口（D82 红队 R-9）：`_tool_run_pipeline` 原样返回作业的 stdout / stderr 尾部，`_tool_message`
只过 scrub_restricted——模块把密钥值打进输出，就原样进会话日志并发给模型服务商。

两层：
- 单元：真跑子进程（替身脚本把密钥打进 stdout / stderr / 非 0 退出），断言工具结果里只有 [已脱敏]；
- 协议：真实 core + 假端点，替身模块 `pipeline.leak_probe` 读 config/*.local.json 的密钥打印出来，
  断言工具帧、会话日志、假端点收到的请求里都没有密钥原文。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import jobs, paths
from pipeline.agent.tools import ToolContext, execute_tool
from tests.test_agent_protocol import Protocol, tool_call
from tests.test_agent_protocol import endpoint, world  # noqa: F401  （pytest fixture）
from tests.test_agent_tools import make_agent_root

SECRET = "sk-d83-SECRET-0123456789abcdef"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_agent_root(tmp_path / "repo", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.setattr(paths, "DATA", root / "data")
    ep = root / "data" / "episodes" / "ep"
    ep.mkdir(parents=True)
    (ep / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    (root / "config" / "cloud.local.json").write_text(
        json.dumps({"provider": {"api_key": SECRET}}), encoding="utf-8"
    )
    pub = jobs.EventPublisher(queue_capacity=64, data_root=root / "data")
    pub.start()
    monkeypatch.setattr(jobs, "get_publisher", lambda: pub)
    yield root
    pub.close()


def _leaky_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    """把作业 argv 换成一段真脚本：密钥进 stdout 与 stderr、退出码 1（execute_job 照常排水、取尾部）。"""
    real = subprocess.Popen
    script = (
        "import sys;"
        f"print('stdout 里有 {SECRET} 一行');"
        f"print('Traceback 末行 RuntimeError: key={SECRET}', file=sys.stderr);"
        "sys.exit(1)"
    )

    def spy(argv, *a, **kw):
        return real([sys.executable, "-c", script], *a, **kw)

    monkeypatch.setattr(jobs.subprocess, "Popen", spy)


def test_run_pipeline结果的stdout与stderr过密钥脱敏(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _leaky_popen(monkeypatch)
    ctx = ToolContext(scope="creative", episode_dir=repo / "data/episodes/ep", root=repo, confirmed=True)
    out = execute_tool("run_pipeline", {"command": "status"}, ctx)
    result = out["result"]
    assert result["returncode"] == 1, "替身真跑了（不是校验未执行那一支）"
    assert SECRET not in json.dumps(out, ensure_ascii=False)
    assert result["stdout_tail"] == "stdout 里有 [已脱敏] 一行\n"
    assert result["stderr_tail"] == "Traceback 末行 RuntimeError: key=[已脱敏]\n"


def test_干净输出逐字不变(repo: Path) -> None:
    """脱敏对不含密钥的文本是恒等：真实 status 模块的输出不被改动。"""
    ctx = ToolContext(scope="creative", episode_dir=repo / "data/episodes/ep", root=repo, confirmed=True)
    out = execute_tool("run_pipeline", {"command": "status"}, ctx)
    from pipeline.agent import tools

    raw = tools.run_pipeline("status", episode_dir=repo / "data/episodes/ep", confirmed=True)
    assert out["result"]["stdout_tail"] == raw["stdout_tail"]
    assert out["result"]["stdout_tail"].strip(), "status 有输出，这条比对不是空对空"


_LEAK_PROBE = '''"""D83 测试替身：把 config/*.local.json 里的密钥值打进 stdout 与 stderr，退出码 1。"""
import json
import sys
from pathlib import Path

from pipeline import paths

secret = json.loads((Path(paths.ROOT) / "config" / "cloud.local.json").read_text())["provider"]["api_key"]
print(f"诊断：当前密钥 {secret}")
print(f"RuntimeError: auth failed for {secret}", file=sys.stderr)
sys.exit(1)
'''


def test_协议_会话日志与出网请求里没有密钥原文(world, endpoint, tmp_path: Path) -> None:  # noqa: F811
    root, episode = world
    (root / "config" / "cloud.local.json").write_text(
        json.dumps({"provider": {"api_key": SECRET}}), encoding="utf-8"
    )
    ext = tmp_path / "pkgext"
    ext.mkdir()
    (ext / "leak_probe.py").write_text(_LEAK_PROBE, encoding="utf-8")
    endpoint.replies = [
        tool_call("run_pipeline", {"command": "leak_probe"}, call_id="c-leak"),
        {"role": "assistant", "content": "看过了"},
    ]
    proc = Protocol(root, endpoint, tmp=tmp_path / "hook",
                    env={"AVA_PROTO_PKG_EXT": str(ext), "AVA_PROTO_EXTRA_MODULES": "leak_probe"})
    try:
        proc.wait_for_ready()
        proc.send({"t": "user_message", "text": "跑一下诊断"})
        proc.wait_for("turn_finished")  # run_pipeline 免卡（ADR-0030），不经审批卡
        proc.shutdown()
    finally:
        assert proc.finish() == 0
    # 替身真跑过：第二次请求里带着工具结果（退出码 1 与脱敏标记）
    assert len(endpoint.requests) == 2
    tool_msgs = [m for m in endpoint.requests[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    content = json.loads(tool_msgs[0]["content"])
    assert content["result"]["returncode"] == 1
    assert content["result"]["stdout_tail"] == "诊断：当前密钥 [已脱敏]\n"
    assert content["result"]["stderr_tail"] == "RuntimeError: auth failed for [已脱敏]\n"
    # 都没有原文：出网请求、会话日志、工具帧（宿主据此渲染工具行与历史）。
    assert SECRET not in json.dumps(endpoint.requests, ensure_ascii=False)
    log = (episode / "session.jsonl").read_text(encoding="utf-8")
    assert "[已脱敏]" in log and SECRET not in log
    tool_frames = [f for f in proc.frames if f.get("t") == "tool"]
    assert tool_frames and SECRET not in json.dumps(tool_frames, ensure_ascii=False)
    # D83 观察项（2026-10-15 修）：execute_job 的实时回显——stdout 进宿主 `log` 帧、stderr 进协议进程
    # stderr——以及作业结束事件落盘的尾部，同样只有 [已脱敏]。
    logs = [f for f in proc.frames if f.get("t") == "log"]
    assert any("诊断：当前密钥 [已脱敏]" in f["text"] for f in logs), "回显真到了 log 帧"
    assert SECRET not in json.dumps(logs, ensure_ascii=False)
    stderr = "".join(proc.stderr_lines)
    assert "RuntimeError: auth failed for [已脱敏]" in stderr and SECRET not in stderr
    events = (episode / "events.jsonl").read_text(encoding="utf-8")
    assert "job_finished" in events and SECRET not in events


# ---------------------------------------------------------------------------
# D83 观察项：execute_job 源头脱敏（回显 + 尾部 + 结束事件）
# ---------------------------------------------------------------------------


class _ChunkedPipe:
    """按给定字节块返回的假管道：read1 每次吐一块，用来把密钥切在两次读之间。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def read1(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
        pass


class _FakeProc:
    def __init__(self, out: list[bytes], err: list[bytes]) -> None:
        self.stdout = _ChunkedPipe(out)
        self.stderr = _ChunkedPipe(err)
        self.pid = 4242

    def wait(self, *a, **k) -> int:
        return 0

    def poll(self) -> int:
        return 0

    def kill(self) -> None:
        pass


def _run_fake(repo: Path, out: list[bytes], err: list[bytes] | None = None) -> jobs.Job:
    job, valid, msg = jobs.create_job("status", episode_dir=repo / "data/episodes/ep", scope="pipeline")
    assert valid, msg
    return jobs.execute_job(job, popen_factory=lambda *a, **k: _FakeProc(out, err or []))


def test_回显_密钥被读块切成两半也换掉(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    half = len(SECRET) // 2
    line = f"key={SECRET} 完\n".encode()
    cut = line.index(SECRET.encode()) + half
    done = _run_fake(repo, [line[:cut], line[cut:]], [b"err " + SECRET.encode(), b" tail\n"])
    got = capsys.readouterr()
    assert got.out == "key=[已脱敏] 完\n"
    assert got.err == "err [已脱敏] tail\n"
    assert done.stdout_tail == "key=[已脱敏] 完\n"
    assert done.stderr_tail == "err [已脱敏] tail\n"


class _Spy:
    """记下每一次 write，用来断言回显的时序（哪一块在什么时候写出）。"""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, t: str) -> None:
        self.writes.append(t)

    def flush(self) -> None:
        pass


def test_回显_回车分隔的进度行逐段写出(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tqdm 一类用 \\r 刷新的进度行不等 \\n 才写；末尾不带换行的残段在管道关闭时写出。"""
    spy = _Spy()
    monkeypatch.setattr(jobs.sys, "stdout", spy)
    _run_fake(repo, [b"10%\r20%\r", ("tail " + SECRET).encode()])
    assert spy.writes == ["10%\r20%\r", "tail [已脱敏]"]


def test_回显_没有已知密钥时直通不攒行(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "config" / "cloud.local.json").unlink()
    from pipeline.agent.tools import secret_env_names

    for name in secret_env_names(repo):
        monkeypatch.delenv(name, raising=False)  # 本机 shell 里的真密钥变量也算已知密钥
    assert jobs._job_secret_values() == []
    spy = _Spy()
    monkeypatch.setattr(jobs.sys, "stdout", spy)
    _run_fake(repo, [b"no newline ", b"yet\n"])
    assert spy.writes == ["no newline ", "yet\n"], "无密钥时每块到即写，时序与改动前一致"


def test_结束事件落盘的尾部已脱敏(repo: Path) -> None:
    _run_fake(repo, [f"out {SECRET}\n".encode()], [f"err {SECRET}\n".encode()])
    jobs.get_publisher().close()
    events = [json.loads(x) for x in (repo / "data/episodes/ep/events.jsonl").read_text("utf-8").splitlines()]
    fin = [e for e in events if e["type"] == "job_finished"]
    assert fin and fin[0]["payload"]["stdout_tail"] == "out [已脱敏]\n"
    assert SECRET not in json.dumps(events, ensure_ascii=False)


def test_回显_无换行的提示即时写出(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有已知密钥时也不按行攒：「…[y/N]: 」这类半行提示到了就写（🔴-2），不等换行。"""
    spy = _Spy()
    monkeypatch.setattr(jobs.sys, "stdout", spy)
    _run_fake(repo, [b"[*] \xe7\xa1\xae\xe8\xae\xa4\xef\xbc\x9f[y/N]: ", b"y\n"])
    assert spy.writes == ["[*] 确认？[y/N]: ", "y\n"]


def test_回显_末尾像密钥开头的一截先扣住(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只扣住恰好是密钥开头的尾巴：后半截来了就整体换掉；没来（管道关了）就原样写出。"""
    spy = _Spy()
    monkeypatch.setattr(jobs.sys, "stdout", spy)
    _run_fake(repo, [b"a " + SECRET[:5].encode(), SECRET[5:].encode() + b" b ", SECRET[:3].encode()])
    assert spy.writes == ["a ", "[已脱敏] b ", SECRET[:3]]


@pytest.mark.parametrize("cut", [1, len(SECRET) - 1])
def test_回显_密钥切在第一个或最后一个字符处(repo: Path, capsys: pytest.CaptureFixture[str], cut: int) -> None:
    _run_fake(repo, [SECRET[:cut].encode(), SECRET[cut:].encode() + b"\n"])
    assert capsys.readouterr().out == "[已脱敏]\n"


def test_中断路径的尾部与结束事件也脱敏(repo: Path) -> None:
    class Interrupted(_FakeProc):
        def wait(self, *a, **k) -> int:
            raise KeyboardInterrupt("模拟 Ctrl-C")

    job, valid, msg = jobs.create_job("status", episode_dir=repo / "data/episodes/ep", scope="pipeline")
    assert valid, msg
    proc = Interrupted([f"out {SECRET}\n".encode()], [])
    with pytest.raises(KeyboardInterrupt):
        jobs.execute_job(job, popen_factory=lambda *a, **k: proc)
    assert job.stdout_tail == "out [已脱敏]\n"
    jobs.get_publisher().close()
    assert SECRET not in (repo / "data/episodes/ep/events.jsonl").read_text("utf-8")
