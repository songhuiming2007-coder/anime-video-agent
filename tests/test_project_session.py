"""D81 PR-1：企划会话与卷会话的安全语义（读域、写 00-project.md、卡上绑定、企划记忆、注入、协议）。

全部在 tmp 假仓库里跑，不碰真实 data/。夹具企划 P：卷表 01-a / 02-b（02-b 之后还有不存在的 03-c），
另一个企划 Q（同名卷 01-a，用来测跨企划软链与同名卷），普通期 E。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pipeline import paths
from pipeline.agent import cli, memory
from pipeline.agent import session as session_mod
from pipeline.agent.assembly import SessionContextTracker
from pipeline.agent.session import AgentSession, HumanAnswer, SessionHost, review_tool_call
from pipeline.agent.session_log import EpisodeLease
from pipeline.agent.tools import ToolContext, execute_tool
from tests.test_agent_tools import make_agent_root

PROJECT_DOC = """# 00 企划

企划: 测试编年史
番: 测试番
合集: 00-合集

## 卷表

| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |
|---|---|---|---|---|
| 1 | 01-a | 2011–2014 | 14-16分钟 | 余韵18秒 |
| 2 | 02-b | 2015–2019 | 14-16分钟 | 余韵18秒 |
| 3 | 03-c | 2020–2023 | 18-20分钟 | 完整保留 |

## 总纲
总纲散文第一版。
"""


class FakeChannel:
    name = "tty"

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = list(answers or [])
        self.shown: list[tuple[str, dict]] = []
        self.requests: list = []

    def show(self, kind: str, payload: dict) -> None:
        self.shown.append((kind, payload))

    def ask(self, request):
        self.requests.append(request)
        decision = self.answers.pop(0) if self.answers else request.options[-1]
        return HumanAnswer(request.request_id, decision, None, "tty", 0.1)

    def is_tty(self) -> bool:
        return True

    def codes(self) -> list[str]:
        return [str(p.get("code")) for k, p in self.shown if k == "notice"]


def _ep(path: Path, script: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "01-topic.md").write_text("# 选题\n", encoding="utf-8")
    if script is not None:
        (path / "02-script.md").write_text(script, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clean_leases():
    EpisodeLease._reset_for_testing()
    yield
    EpisodeLease._reset_for_testing()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = make_agent_root(tmp_path / "repo", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.setattr(paths, "DATA", root / "data")
    monkeypatch.setenv("AVA_TEST_KEY", "k")
    eps = root / "data" / "episodes"
    lib = root / "data" / "library"
    (lib / "notes").mkdir(parents=True)
    (lib / "notes" / "测试番.md").write_text("# 测试番笔记\n", encoding="utf-8")
    P = eps / "P"
    _ep(P / "01-a", "## 段落 1\n配音：第一卷独有句。\n")
    _ep(P / "02-b", "## 段落 1\n配音：第二卷独有句。\n")
    (P / "00-project.md").write_text(PROJECT_DOC, encoding="utf-8")
    Q = eps / "Q"
    _ep(Q / "01-a", "## 段落 1\n配音：Q 企划的句子。\n")
    E = _ep(eps / "E")
    return root, P, Q, E


def vol_ctx(root: Path, P: Path, vol: str = "01-a", **kw) -> ToolContext:
    return ToolContext(scope="creative", episode_dir=P / vol, project_dir=P, session_kind="volume", root=root, **kw)


def proj_ctx(root: Path, P: Path, **kw) -> ToolContext:
    return ToolContext(scope="project", episode_dir=P, project_dir=P, session_kind="project", root=root, **kw)


def read(ctx: ToolContext, path: str, area: str | None = None) -> dict:
    args = {"path": path} if area is None else {"path": path, "area": area}
    return execute_tool("read_artifact", args, ctx)


# ---------------------------------------------------------------------------
# 读域（§4.3）
# ---------------------------------------------------------------------------


class TestReadDomain:
    def test_卷会话读企划文件(self, world) -> None:
        root, P, _Q, _E = world
        for area in (None, "project"):
            out = read(vol_ctx(root, P), "00-project.md", area)
            assert out["ok"] and "测试编年史" in out["result"]["text"], out

    def test_卷会话不能借读兄弟卷_四条路径(self, world) -> None:
        root, P, _Q, _E = world
        (P / "evil.md").symlink_to(Path("02-b") / "02-script.md")
        (P / "01-a" / "x.md").symlink_to(Path("..") / "02-b" / "02-script.md")
        ctx = vol_ctx(root, P)
        for path, area in (("../02-b/02-script.md", None), ("02-b/02-script.md", "project"),
                           ("evil.md", None), ("evil.md", "project"), ("x.md", None)):
            out = read(ctx, path, area)
            assert out["ok"] is False, (path, area, out)

    def test_企划候选只认一段_多段经软链落回企划顶层也拒(self, world) -> None:
        """M4：去掉「只有一段」后，`02-b/up.md -> ../00-project.md` 的父目录恰是企划目录，父目录检查拦不住。"""
        root, P, _Q, _E = world
        (P / "02-b" / "up.md").symlink_to(Path("..") / "00-project.md")
        assert read(vol_ctx(root, P), "02-b/up.md", "project")["ok"] is False

    def test_企划会话读整个企划(self, world, tmp_path: Path) -> None:
        root, P, Q, _E = world
        outside = _ep(tmp_path / "elsewhere" / "linked", "## 段落 1\n配音：软链卷的句子。\n")
        (P / "04-linked").symlink_to(outside)
        ctx = proj_ctx(root, P)
        assert "第二卷独有句" in read(ctx, "02-b/02-script.md")["result"]["text"]
        assert "第一卷独有句" in read(ctx, "01-a/02-script.md", "project")["result"]["text"]
        assert "软链卷的句子" in read(ctx, "04-linked/02-script.md")["result"]["text"]
        (P / "q.md").symlink_to(Path("..") / "Q" / "01-a" / "02-script.md")
        assert read(ctx, "q.md")["ok"] is False

    @pytest.mark.parametrize("kind", ["volume", "project"])
    def test_硬排除_大小写与目录软链(self, world, kind: str) -> None:
        root, P, _Q, _E = world
        for vol in ("01-a", "02-b"):
            (P / vol / "03-audio").mkdir()
            (P / vol / "03-audio" / "manifest.json").write_text("{}", encoding="utf-8")
            (P / vol / "04-patch").mkdir()
            (P / vol / "04-patch" / "x.json").write_text("{}", encoding="utf-8")
        (P / "01-a" / "audio").symlink_to("03-audio")
        ctx = vol_ctx(root, P) if kind == "volume" else proj_ctx(root, P)
        prefix = "" if kind == "volume" else "01-a/"
        for path in (f"{prefix}03-AUDIO/manifest.json", f"{prefix}04-Patch/x.json", f"{prefix}audio/manifest.json"):
            out = read(ctx, path)
            assert out["ok"] is False and "硬排除" in out["error"], (path, out)

    @pytest.mark.parametrize("kind", ["volume", "project"])
    def test_记忆文件与日志不可读(self, world, kind: str) -> None:
        root, P, _Q, _E = world
        lib = root / "data" / "library"
        for name in ("memory.md", "memory.log.jsonl"):
            (lib / name).write_text("全局\n", encoding="utf-8")
        (P / "00-memory.md").write_text("企划记忆\n", encoding="utf-8")
        (P / "00-memory.log.jsonl").write_text("{}\n", encoding="utf-8")
        here = P / "01-a" if kind == "volume" else P
        (here / "m.md").symlink_to(P / "00-memory.md")
        (here / "x.json").symlink_to(P / "00-memory.log.jsonl")
        (here / "y.json").symlink_to(lib / "memory.log.jsonl")
        (P / "01-a" / "session.jsonl").write_text("{}\n", encoding="utf-8")
        (here / "z.md").symlink_to(P / "01-a" / "session.jsonl")
        ctx = vol_ctx(root, P) if kind == "volume" else proj_ctx(root, P)
        cases = [("00-memory.md", "project"), ("00-MEMORY.md", "project"), ("m.md", None),
                 ("memory.md", "library"), ("x.json", None), ("y.json", None), ("z.md", None)]
        for path, area in cases:
            out = read(ctx, path, area)
            assert out["ok"] is False, (path, area, out)

    def test_企划会话读library(self, world) -> None:
        root, P, _Q, _E = world
        assert read(proj_ctx(root, P), "notes/测试番.md", "library")["ok"] is True

    def test_普通期不能用area_project(self, world) -> None:
        root, _P, _Q, E = world
        out = read(ToolContext(episode_dir=E, root=root), "00-project.md", "project")
        assert out["ok"] is False and "area=project" in out["error"]


# ---------------------------------------------------------------------------
# 写 00-project.md（§4.4）
# ---------------------------------------------------------------------------


def review(name: str, args: dict, *, kind: str, ep: Path, P: Path | None, root: Path):
    return review_tool_call(name, args, ep_dir=ep, scope="project", root=root,
                            session_kind=kind, project_dir=P)


def write_args(content: str) -> dict:
    return {"filename": "00-project.md", "content": content}


class TestProjectWrite:
    def test_卷会话写企划文件拒_企划会话写选题拒(self, world) -> None:
        root, P, _Q, _E = world
        v = review("write_episode_file", write_args(PROJECT_DOC), kind="volume", ep=P / "01-a", P=P, root=root)
        assert v.action == "reject"
        assert execute_tool("write_episode_file", write_args(PROJECT_DOC), vol_ctx(root, P, confirmed=True))["ok"] is False
        v = review("write_episode_file", {"filename": "01-topic.md", "content": "x"}, kind="project", ep=P, P=P, root=root)
        assert v.action == "reject" and "只认 00-project.md" in v.reason

    def test_首次立项与原文件不可解析都弹卡(self, world) -> None:
        root, P, _Q, _E = world
        (P / "00-project.md").unlink()
        v = review("write_episode_file", write_args(PROJECT_DOC), kind="project", ep=P, P=P, root=root)
        assert v.action == "ask" and v.allow_key is None and v.bind == {"base_sha": "absent"}
        (P / "00-project.md").write_text("坏的立项文件\n", encoding="utf-8")
        v = review("write_episode_file", write_args(PROJECT_DOC), kind="project", ep=P, P=P, root=root)
        assert v.action == "ask"

    def test_只改散文免卡且留底(self, world) -> None:
        root, P, _Q, _E = world
        after = PROJECT_DOC.replace("总纲散文第一版。", "总纲散文第二版。")
        v = review("write_episode_file", write_args(after), kind="project", ep=P, P=P, root=root)
        assert v.action == "allow"
        out = execute_tool("write_episode_file", write_args(after), proj_ctx(root, P, confirmed=True))
        assert out["ok"] and out["result"]["machine_fields_changed"] is False
        assert (P / "00-project.md").read_text(encoding="utf-8") == after
        hist = list((P / "_agent" / "file-history").iterdir())
        assert len(hist) == 1 and hist[0].read_text(encoding="utf-8") == PROJECT_DOC

    def test_改卷表一格弹卡带diff_只改对齐免卡(self, world) -> None:
        root, P, _Q, _E = world
        after = PROJECT_DOC.replace("| 18-20分钟 |", "| 19-21分钟 |")
        v = review("write_episode_file", write_args(after), kind="project", ep=P, P=P, root=root)
        assert v.action == "ask" and v.allow_key is None
        assert "+ | 3 | 03-c | 2020–2023 | 19-21分钟 | 完整保留 |" in v.request.card_text
        aligned = PROJECT_DOC.replace("| 1 | 01-a |", "|  1  |   01-a   |")
        assert review("write_episode_file", write_args(aligned), kind="project", ep=P, P=P, root=root).action == "allow"

    @pytest.mark.parametrize("mutate", [
        lambda s: s.replace("企划: 测试编年史\n", ""),
        lambda s: s.replace("| 2 | 02-b", "| 4 | 02-b"),
        lambda s: s.replace("| 完整保留 |", "| 余韵18秒 |"),
        lambda s: s.replace("| 02-b |", "| 02/b |"),
        lambda s: s.replace("| 01-a |", "| 01-A |").replace("| 02-b |", "| 01-a |"),
    ])
    def test_写后不可解析弹卡前拒(self, world, mutate) -> None:
        root, P, _Q, _E = world
        v = review("write_episode_file", write_args(mutate(PROJECT_DOC)), kind="project", ep=P, P=P, root=root)
        assert v.action == "reject" and "不可解析" in v.reason, v

    def test_执行层_裸调用与卡后被改与伪造参数(self, world) -> None:
        root, P, _Q, _E = world
        after = PROJECT_DOC.replace("| 18-20分钟 |", "| 19-21分钟 |")
        sha = hashlib.sha256((P / "00-project.md").read_bytes()).hexdigest()
        # ① 裸调用：confirmed 但不是人批
        out = execute_tool("write_episode_file", write_args(after), proj_ctx(root, P, confirmed=True))
        assert out["ok"] is False and "人在卡上批准" in out["error"]
        # ② 人批了，但卡面依据的 sha 与盘上不符（卡后被改）
        out = execute_tool("write_episode_file", write_args(after),
                           proj_ctx(root, P, confirmed=True, human_approved=True, bound={"base_sha": "0" * 64}))
        assert out["ok"] is False and "看卡之后变了" in out["error"]
        # ③ 模型在参数里伪造「已确认」（R-10）：ctx 未人批，args 带正确 sha
        forged = {**write_args(after), "confirmed": True, "human_approved": True, "bound": {"base_sha": sha}}
        out = execute_tool("write_episode_file", forged, proj_ctx(root, P, confirmed=True))
        assert out["ok"] is False and "人在卡上批准" in out["error"]
        assert (P / "00-project.md").read_text(encoding="utf-8") == PROJECT_DOC
        # 人批 + sha 一致 → 落盘
        out = execute_tool("write_episode_file", write_args(after),
                           proj_ctx(root, P, confirmed=True, human_approved=True, bound={"base_sha": sha}))
        assert out["ok"] is True and (P / "00-project.md").read_text(encoding="utf-8") == after

    def test_企划文件是指向别处的软链拒(self, world, tmp_path: Path) -> None:
        root, P, _Q, _E = world
        real = tmp_path / "elsewhere.md"
        real.write_text(PROJECT_DOC, encoding="utf-8")
        (P / "00-project.md").unlink()
        (P / "00-project.md").symlink_to(real)
        after = PROJECT_DOC.replace("总纲散文第一版。", "改")
        assert review("write_episode_file", write_args(after), kind="project", ep=P, P=P, root=root).action == "reject"
        assert execute_tool("write_episode_file", write_args(after), proj_ctx(root, P, confirmed=True))["ok"] is False
        assert real.read_text(encoding="utf-8") == PROJECT_DOC


# ---------------------------------------------------------------------------
# 卡上绑定：真实 _review → 执行层收到的 ctx（§4.4、R-10）
# ---------------------------------------------------------------------------


def _session(root: Path, ep: Path, channel: FakeChannel) -> AgentSession:
    host = SessionHost(ep, root=root, channel=channel)
    lease = host.ensure_lease()
    lease.begin("s" * 16)
    host.sid = "s" * 16
    return AgentSession(host, scope_mode="project", persist=True)


def test_卡上绑定只走人批那一支(world, monkeypatch: pytest.MonkeyPatch) -> None:
    root, P, _Q, _E = world
    from pipeline.agent import llm as llm_mod

    seen: list[tuple[str, bool, object]] = []
    real = llm_mod.execute_tool

    def spy(name, args, ctx):
        seen.append((name, ctx.human_approved, ctx.bound))
        return real(name, args, ctx)

    monkeypatch.setattr(llm_mod, "execute_tool", spy)
    after = PROJECT_DOC.replace("| 18-20分钟 |", "| 19-21分钟 |")
    calls = [
        ("write_episode_file", write_args(after)),                       # 人批
        ("read_artifact", {"path": "00-project.md"}),                    # card_free
        ("bash", {"command": "rm -f nothing.txt", "description": "删"}),  # 卡上点「本会话都批准」
        ("bash", {"command": "rm -f nothing.txt", "description": "删"}),  # session_allow
    ]
    script = [{"role": "assistant", "content": None, "tool_calls": [{
        "id": f"c{i}", "type": "function",
        "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}]} for i, (n, a) in enumerate(calls)]
    script.append({"role": "assistant", "content": "好"})
    state = {"n": 0}

    def scripted(messages, tools=None, **kw):
        state["n"] += 1
        return script[min(state["n"] - 1, len(script) - 1)]

    monkeypatch.setattr(llm_mod, "chat_complete", scripted)
    sha = hashlib.sha256((P / "00-project.md").read_bytes()).hexdigest()
    channel = FakeChannel(["approve", session_mod.SESSION_ALLOW_DECISION])
    sess = _session(root, P, channel)
    sess.run_turn("改卷表", messages=[], scope="project", status=None, tracker=SessionContextTracker(), root=root)
    assert seen == [
        ("write_episode_file", True, {"base_sha": sha}),
        ("read_artifact", False, None),
        ("bash", True, None),
        ("bash", False, None),
    ]
    assert (P / "00-project.md").read_text(encoding="utf-8") == after


def test_review_本会话已批的同类卡不带绑定(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """M9：即使某张卡同时开放「本会话都批准」又带 bind，session_allow 那一支的 Decision 也不带 bind。"""
    root, P, _Q, _E = world
    from pipeline.agent.session import HumanRequest, ToolVerdict

    req = HumanRequest(request_id="r1", kind="tool_call", turn_id="t", title="x", card_text="x",
                       fields={"target": "x"}, options=("approve", "approve_session", "reject"), feedback_allowed=True)
    monkeypatch.setattr(session_mod, "review_tool_call",
                        lambda *a, **k: ToolVerdict("ask", request=req, allow_key="k", bind={"base_sha": "s"}))
    channel = FakeChannel(["approve"])
    sess = _session(root, P, channel)
    sess.host.session_allow.add("k")
    d = sess._review("bash", {}, "t", None, root, None)
    assert d.ok and d.provenance == "session_allow" and d.bind is None
    sess.host.session_allow.clear()
    d = sess._review("bash", {}, "t", None, root, None)
    assert d.ok and d.provenance == "human" and d.bind == {"base_sha": "s"}


# ---------------------------------------------------------------------------
# 企划会话的其余工具（§4.2、R-3、R-7）
# ---------------------------------------------------------------------------


class TestProjectSessionTools:
    def test_封面与抓取提案拒_母目录不被写(self, world) -> None:
        root, P, _Q, _E = world
        marker = time.time()
        time.sleep(0.01)
        for name, args in (("cover_edit", {"action": "render"}), ("acquire_propose", {"candidates": [{"title": "x"}]})):
            v = review(name, args, kind="project", ep=P, P=P, root=root)
            assert v.action == "reject" and "卷级操作" in v.reason
            out = execute_tool(name, args, proj_ctx(root, P, confirmed=True))
            assert out["ok"] is False and "卷级操作" in out["error"]
        newer = [p for p in P.rglob("*") if p.stat().st_mtime > marker]
        assert newer == []

    def test_run_pipeline分流(self, world) -> None:
        root, P, _Q, _E = world
        v = review("run_pipeline", {"command": "tts"}, kind="project", ep=P, P=P, root=root)
        assert v.action == "reject" and "到对应卷的会话里跑" in v.reason and "01-a" in v.reason
        v = review("run_pipeline", {"command": "status"}, kind="project", ep=P, P=P, root=root)
        assert v.action == "reject" and "read_status" in v.reason
        v = review("run_pipeline", {"command": "subgrep --anime 测试番 句子"}, kind="project", ep=P, P=P, root=root)
        assert v.action == "allow"
        out = execute_tool("run_pipeline", {"command": "tts"}, proj_ctx(root, P, confirmed=True))
        assert out["ok"] is False and "卷级工序" in out["error"]

    def test_crosscheck补位_企划会话补企划_卷会话补所属企划(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """D81 §9.3 ⑥：补位取宿主绑定的企划目录，卷会话补的不是卷目录；校验与立项（执行前再校一次）同口径。"""
        root, P, _Q, _E = world
        from pipeline import jobs

        tail = ["-m", "pipeline.crosscheck", str(P)]
        for ctx in (proj_ctx(root, P), vol_ctx(root, P)):
            out = execute_tool("run_pipeline", {"command": "crosscheck"}, ctx)
            assert out["result"]["argv"][1:] == tail, ctx.session_kind
        ran: list[list[str]] = []

        def fake_execute(job):
            ran.append(list(job.argv))
            job.status, job.returncode = jobs.JobStatus.SUCCEEDED, 0
            return job

        monkeypatch.setattr(jobs, "execute_job", fake_execute)
        out = execute_tool("run_pipeline", {"command": "crosscheck --json"}, vol_ctx(root, P, confirmed=True))
        assert out["result"]["ok"] is True and ran == [[ran[0][0], *tail, "--json"]]

    def test_crosscheck只读免卡_卷会话带write拒(self, world) -> None:
        root, P, _Q, _E = world
        for kind, ep in (("project", P), ("volume", P / "01-a")):
            v = review("run_pipeline", {"command": "crosscheck"}, kind=kind, ep=ep, P=P, root=root)
            assert v.action == "allow", kind
        v = review("run_pipeline", {"command": "crosscheck --write"}, kind="project", ep=P, P=P, root=root)
        assert v.action == "allow"   # ADR-0030：白名单写命令同样免卡
        for cmd in ("crosscheck --write", "crosscheck --wr", "crosscheck --write=1"):
            v = review("run_pipeline", {"command": cmd}, kind="volume", ep=P / "01-a", P=P, root=root)
            assert v.action == "reject" and "企划会话" in v.reason, cmd
            out = execute_tool("run_pipeline", {"command": cmd}, vol_ctx(root, P, confirmed=True))
            assert out["ok"] is False and "只读的 crosscheck" in out["error"], cmd
        assert not (P / "00-crosscheck.md").exists()

    def test_企划级模块分流用替身模块(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """assemble 在 PR-5 才进白名单：用替身登记它，验证分流（企划会话放行、卷会话拒）。"""
        root, P, _Q, _E = world
        from pipeline.agent import tools

        monkeypatch.setattr(tools, "PIPELINE_MODULES", tools.PIPELINE_MODULES | {"assemble"})
        assert review("run_pipeline", {"command": "assemble"}, kind="project", ep=P, P=P, root=root).action != "reject"
        v = review("run_pipeline", {"command": "assemble"}, kind="volume", ep=P / "01-a", P=P, root=root)
        assert v.action == "reject" and "企划会话" in v.reason

    def test_期目录守卫(self, world) -> None:
        root, P, _Q, _E = world
        out = execute_tool("write_episode_file", {"filename": "01-topic.md", "content": "x"},
                           ToolContext(episode_dir=P, root=root, confirmed=True))
        assert out["ok"] is False and "企划目录" in out["error"]
        assert not (P / "01-topic.md").exists()

    def test_list_episodes同名卷不同name(self, world) -> None:
        root, _P, _Q, _E = world
        out = execute_tool("list_episodes", {}, ToolContext(root=root))["result"]
        assert "P/01-a" in out["episodes"] and "Q/01-a" in out["episodes"]
        assert {"name": "P", "has_project_file": True, "members": ["P/01-a", "P/02-b"]} in out["projects"]

    def test_read_status裸卷名先在本企划内解析_歧义给候选(self, world) -> None:
        root, P, _Q, _E = world
        assert execute_tool("read_status", {"episode": "01-a"}, vol_ctx(root, P))["ok"] is True
        out = execute_tool("read_status", {"episode": "01-a"}, ToolContext(episode_dir=world[3], root=root))
        assert out["ok"] is False and "P/01-a" in out["error"] and "Q/01-a" in out["error"]


# ---------------------------------------------------------------------------
# 判定一致（R-13）与 CLI
# ---------------------------------------------------------------------------


def test_判定一致(world) -> None:
    root, P, Q, E = world
    from pipeline.agent.tools import resolve_episode_dir

    eps = root / "data" / "episodes"
    for d in (P, Q, E, P / "01-a", Q / "01-a"):
        kind = cli.classify_dir(d)
        tree = cli.get_episode_tree(root)
        in_tree = ("project" if any(p.dir == d for p in tree.projects) else
                   "volume" if any(d in p.members for p in tree.projects) else
                   "episode" if d in tree.plain else None)
        assert kind == in_tree, d
        resolved = cli.resolve_episode_target(d.relative_to(eps).as_posix())
        assert (resolved is not None) == (kind in ("episode", "volume")), d
        if kind == "project":
            with pytest.raises(PermissionError, match="企划目录"):
                resolve_episode_dir(d, root=root)
        else:
            resolve_episode_dir(d, root=root)
    assert cli.create_new_episode("新卷", project="E") == 2      # 普通期不能当企划
    assert not (E / "新卷").exists()
    assert cli.create_new_episode("03-c", project="P") == 0
    assert cli.classify_dir(P / "03-c") == "volume"


class TestCliCreate:
    def test_new_project(self, world, capsys) -> None:
        root, _P, _Q, _E = world
        assert cli.main(["new-project", "supercell 编年史"]) == 0
        doc = (root / "data/episodes/supercell 编年史/00-project.md").read_text(encoding="utf-8")
        from pipeline import project

        parsed = project.parse(doc)
        assert parsed.name == "supercell 编年史" and parsed.volumes == ()
        assert cli.main(["new-project", "supercell 编年史"]) == 1
        assert cli.main(["new-project", "坏/名"]) == 2
        assert cli.main(["new-project", "x" * 41]) == 2
        assert cli.main(["new", "--project", "supercell 编年史", "01-第一卷"]) == 0
        assert cli.classify_dir(root / "data/episodes/supercell 编年史/01-第一卷") == "volume"
        assert cli.main(["new", "--project", "supercell 编年史", "坏名。"]) == 2

    def test_data不可达退2且不建(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(paths, "ROOT", tmp_path / "nowhere")
        assert cli.main(["new-project", "企划"]) == 2
        assert cli.main(["new", "--project", "企划", "卷"]) == 2
        assert not (tmp_path / "nowhere").exists()

    def test_project_meta(self, world, capsys) -> None:
        root, P, _Q, E = world
        capsys.readouterr()
        assert cli.main(["/project-meta", f"--dir={P}"]) == 0
        meta = json.loads(capsys.readouterr().out)
        assert meta["name"] == "测试编年史" and [v["dir"] for v in meta["volumes"]] == ["01-a", "02-b", "03-c"]
        assert meta["hints"] == ["卷表列了 `03-c` 但目录不存在", "卷表列了 `00-合集` 但目录不存在"]
        assert cli.main(["/project-meta", f"--dir={E}"]) == 2


    def test_企划会话的会话管理命令认企划目录(self, world, capsys) -> None:
        """D81 PR-2b 施工中发现：`ava <企划> /list-sessions` 等经 resolve_episode_dir 一律被拒，侧栏企划会话列表报 E_CORE。"""
        _root, P, _Q, _E = world
        capsys.readouterr()
        assert cli.main([str(P), "/list-sessions"]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert cli.main([str(P), "/list-trash"]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert cli.main([str(P), "/rename-session", "--sid=aaaaaaaaaaaaaaa1", "--title=x"]) == 1
        assert "会话 aaaaaaaaaaaaaaa1 不存在" in capsys.readouterr().err   # 过了目录校验，败在 sid 上
        assert cli.main([str(P / "01-a"), "/list-sessions"]) == 0         # 卷照旧
        capsys.readouterr()
        assert cli.main([str(P), "/status"]) != 0                         # 其余期级命令照拒


# ---------------------------------------------------------------------------
# 企划记忆（§4.5）
# ---------------------------------------------------------------------------


def mem_add(ctx: ToolContext, pattern: str, evidence: list[str], target: str = "project") -> dict:
    args = {"op": "add", "target": target, "pattern": pattern, "evidence": evidence, "boundary": "本系列各卷"}
    return execute_tool("write_memory", args, ctx)


class TestProjectMemory:
    def test_卷会话写企划记忆_全局不动(self, world) -> None:
        root, P, _Q, _E = world
        lib = root / "data" / "library"
        out = mem_add(vol_ctx(root, P, confirmed=True), "第二卷的旁白读音统一按第一卷", ["P/01-a"])
        assert out["ok"], out
        assert "第二卷的旁白读音统一按第一卷" in (P / "00-memory.md").read_text(encoding="utf-8")
        assert (P / "00-memory.log.jsonl").exists()
        assert not (lib / "memory.md").exists() and not (lib / "memory.log.jsonl").exists()

    def test_普通期会话target_project拒(self, world) -> None:
        root, _P, _Q, E = world
        out = mem_add(ToolContext(episode_dir=E, root=root, confirmed=True), "某模式", ["E"])
        assert out["ok"] is False and "卷会话与企划会话" in out["error"]

    def test_证据引用另一企划拒(self, world) -> None:
        root, P, _Q, _E = world
        out = mem_add(vol_ctx(root, P, confirmed=True), "某模式", ["Q/01-a"])
        assert out["ok"] is False and "MemoryRuleError" in out["error"] and "不在本企划" in out["error"]

    def test_超3000拒且字节不变(self, world) -> None:
        root, P, _Q, _E = world
        ctx = vol_ctx(root, P, confirmed=True)
        n = 0
        while True:
            pattern = f"第{n:02d}条经验" + "甲乙丙丁戊己庚辛壬癸" * 28
            out = mem_add(ctx, pattern, ["P/01-a"])
            if not out["ok"]:
                break
            n += 1
        body = (P / "00-memory.md").read_text(encoding="utf-8")
        assert "MemoryBudgetError" in out["error"] and "> 3000" in out["error"], out
        assert 3000 - 400 < len(body) <= 3000, len(body)   # 停在 3000 之下（4000 的预算会多写两条）

    def test_企划目录不存在拒且不建(self, world, tmp_path: Path) -> None:
        root, _P, _Q, _E = world
        ghost = root / "data" / "episodes" / "GHOST"
        with pytest.raises(PermissionError):
            memory.project_store(ghost, root)
        assert not ghost.exists()

    def test_来源确认_外部改动告警_ack后恢复(self, world) -> None:
        root, P, _Q, _E = world
        assert mem_add(vol_ctx(root, P, confirmed=True), "第二卷的旁白读音统一按第一卷", ["P/01-a"])["ok"]
        store = memory.project_store(P, root)
        assert memory.render_injection(root, store=store).startswith("[企划记忆 · 测试编年史")
        text = (P / "00-memory.md").read_text(encoding="utf-8").replace("统一按第一卷", "统一按第二卷")
        (P / "00-memory.md").write_text(text, encoding="utf-8")   # 模拟 bash 直接改正文
        warn = memory.render_injection(root, store=store)
        assert warn.startswith("[企划记忆未注入]") and "/memory --project ack" in warn
        assert memory.ack_external(root=root, store=store, confirm=lambda t: (True, 0.1), is_tty=lambda: True)
        assert "统一按第二卷" in memory.render_injection(root, store=store)


# ---------------------------------------------------------------------------
# 注入（§4.6）：真实装配 → 假模型收到的请求
# ---------------------------------------------------------------------------


def _turns(root: Path, ep: Path, monkeypatch, lines: list[str], *, scope: str = "creative",
           between=None, channel: FakeChannel | None = None) -> tuple[list[list[dict]], dict]:
    from pipeline.agent import llm as llm_mod

    requests: list[list[dict]] = []

    def scripted(messages, tools=None, **kw):
        requests.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return {"role": "assistant", "content": "好"}

    monkeypatch.setattr(llm_mod, "chat_complete", scripted)
    sess = _session(root, ep, channel or FakeChannel())
    messages: list[dict] = []
    tracker = SessionContextTracker()
    outcome: dict = {}
    for i, line in enumerate(lines):
        if i and between is not None:
            between()
        outcome = sess.run_turn(line, messages=messages, scope=scope, status=None, tracker=tracker, root=root)
    return requests, outcome


def _text(request: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in request)


def _seed_global_memory(root: Path) -> None:
    ctx = ToolContext(episode_dir=root / "data/episodes/E", root=root, confirmed=True)
    out = execute_tool("write_memory", {"op": "add", "pattern": "全局经验句子不该进卷会话", "evidence": ["E"],
                                        "boundary": "所有番"}, ctx)
    assert out["ok"], out


class TestInjection:
    def test_卷会话首轮含卷表与企划记忆(self, world, monkeypatch) -> None:
        root, P, _Q, _E = world
        assert mem_add(vol_ctx(root, P, confirmed=True), "第二卷的旁白读音统一按第一卷", ["P/01-a"])["ok"]
        requests, _ = _turns(root, P / "01-a", monkeypatch, ["开写"])
        text = _text(requests[0])
        assert "| 2 | 02-b | 2015–2019 | 14-16分钟 | 余韵18秒 |" in text
        assert "第二卷的旁白读音统一按第一卷" in text
        assert "企划 测试编年史 · 第 1 卷" in text   # 状态卡首行

    def test_停在03的卷新会话_含企划记忆不含全局记忆(self, world, monkeypatch) -> None:
        root, P, _Q, _E = world
        _seed_global_memory(root)
        assert mem_add(vol_ctx(root, P, confirmed=True), "第二卷的旁白读音统一按第一卷", ["P/01-a"])["ok"]
        requests, _ = _turns(root, P / "01-a", monkeypatch, ["配音"], scope="pipeline")
        text = _text(requests[0])
        assert "第二卷的旁白读音统一按第一卷" in text
        assert "全局经验句子不该进卷会话" not in text

    def test_超8000只注机读部分与章节标题(self, world, monkeypatch) -> None:
        root, P, _Q, _E = world
        long_doc = PROJECT_DOC + "\n## 踩坑全录\n" + ("中段正文标记句。" * 1200) + "\n## 尾声\n结束。\n"
        (P / "00-project.md").write_text(long_doc, encoding="utf-8")
        requests, _ = _turns(root, P / "01-a", monkeypatch, ["开写"])
        text = _text(requests[0])
        assert "| 3 | 03-c |" in text and "章节: 卷表 / 总纲 / 踩坑全录 / 尾声" in text
        assert "中段正文标记句" not in text and 'read_artifact(path="00-project.md", area="project")' in text

    def test_两轮之间改卷表_第二轮含已更新(self, world, monkeypatch) -> None:
        root, P, _Q, _E = world
        new_doc = PROJECT_DOC.replace("| 18-20分钟 |", "| 19-21分钟 |")
        requests, _ = _turns(root, P / "01-a", monkeypatch, ["一", "二"],
                             between=lambda: (P / "00-project.md").write_text(new_doc, encoding="utf-8"))
        assert "（已更新）" not in _text(requests[0])
        second = _text(requests[1])
        assert "（已更新）" in second and "19-21分钟" in second

    def test_受限字样脱敏_请求不被拦(self, world, monkeypatch) -> None:
        root, P, _Q, _E = world
        (P / "00-project.md").write_text(PROJECT_DOC.replace("总纲散文第一版。", "参考 03-audio/manifest.json 的时长。"),
                                         encoding="utf-8")
        requests, outcome = _turns(root, P / "01-a", monkeypatch, ["开写"])
        assert outcome["stopped"] == "done"
        text = _text(requests[0])
        assert "[已脱敏]" in text and "manifest.json" not in text

    def test_企划记忆被外部改动_注告警发notice(self, world, monkeypatch) -> None:
        root, P, _Q, _E = world
        assert mem_add(vol_ctx(root, P, confirmed=True), "第二卷的旁白读音统一按第一卷", ["P/01-a"])["ok"]
        body = P / "00-memory.md"
        body.write_text(body.read_text(encoding="utf-8").replace("第一卷", "别卷"), encoding="utf-8")
        channel = FakeChannel()
        requests, _ = _turns(root, P / "01-a", monkeypatch, ["开写"], channel=channel)
        text = _text(requests[0])
        assert "[企划记忆未注入]" in text and "别卷" not in text
        assert channel.codes().count("project_memory_unconfirmed") == 1


# ---------------------------------------------------------------------------
# 协议（真实 core 子进程）
# ---------------------------------------------------------------------------


def test_协议_企划会话与期目录守卫(world, tmp_path: Path) -> None:
    from tests.test_agent_protocol import FakeEndpoint, Protocol

    root, P, _Q, _E = world
    endpoint = FakeEndpoint()
    try:
        (root / "config" / "agent.json").write_text(json.dumps(
            {"base_url": endpoint.url, "model": "mock", "api_key_env": "AVA_TEST_KEY"}), encoding="utf-8")
        tools_json = root / "config" / "agent" / "tools.json"
        listed = json.loads(tools_json.read_text(encoding="utf-8"))["tools"]
        tools_json.write_text(json.dumps({"tools": [*listed, "test_ping", "test_writer", "test_slow", "test_stdin_child"]}),
                              encoding="utf-8")   # 协议钩子注册的测试工具（D43 单表反向分叉检查）
        endpoint.replies = [{"role": "assistant", "content": "好"}]
        proc = Protocol(root, endpoint, episode="--project", extra=[str(P)], tmp=tmp_path / "hook")
        try:
            ready = proc.wait_for_ready()
            assert ready["session_kind"] == "project" and ready["project"] == "P"
            proc.send({"t": "user_message", "text": "定卷表"})
            proc.wait_for("turn_finished")
            proc.send({"t": "user_message", "text": "/compact"})
            proc.wait_for("turn_finished")
            proc.shutdown()
        finally:
            assert proc.finish() == 0
        turns = [f["turn_id"] for f in proc.frames if f.get("t") == "turn_started"]
        assert len(turns) == 2
        for turn in turns:
            sp = [f for f in proc.frames if f.get("t") == "stop_points" and f.get("turn_id") == turn]
            assert [f["items"] for f in sp] == [[]], (turn, sp)
        assert "测试编年史" in _text(endpoint.requests[0]["messages"])
        # 期模式打开企划目录 → E_BAD_REQUEST
        bad = Protocol(root, endpoint, episode=str(P), tmp=tmp_path / "hook2")
        err = bad.wait_for("error")
        assert err["code"] == "E_BAD_REQUEST" and "企划目录" in err["message"]
        bad.finish()
        # --project 指向普通期 → E_BAD_REQUEST
        bad = Protocol(root, endpoint, episode="--project", extra=[str(world[3])], tmp=tmp_path / "hook3")
        assert bad.wait_for("error")["code"] == "E_BAD_REQUEST"
        bad.finish()
    finally:
        endpoint.close()
