"""N50：密钥环境变量不再传给 run_pipeline 作业与 browser 工具的 Camoufox（spec 2026-10-15-n50-secret-env-spec.md）。

- 作业：全部密钥变量剥掉；LLM_KEY_MODULES（调模型的模块）只留 LLM 那一个；
- 守卫：调 chat_complete 的模块都在 LLM_KEY_MODULES 里，名单里也没有过时的名字；
- 直接 create_job / execute_job（不经 run_pipeline）环境照旧完整；
- browser：Camoufox 拿到的 env 不含密钥变量；
- 协议：真实 core + 假端点，run_pipeline 跑替身模块，作业里看不到 AVA_TEST_KEY。
crawl（crawl4ai 0.9.4 不开放 env）按人 10-15 裁决 C2 不改，是明示残留。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import jobs, paths
from pipeline.agent import tools, web_browser
from pipeline.agent.tools import LLM_KEY_MODULES, pipeline_job_env_drop, run_pipeline
from tests.test_agent_protocol import Protocol, tool_call
from tests.test_agent_protocol import endpoint, world  # noqa: F401  （pytest fixture）
from tests.test_agent_tools import make_agent_root

LLM_NAME = "AVA_TEST_KEY"      # make_agent_root 的 api_key_env
WEB_NAME = "N50_WEB_API_KEY"   # 名字像密钥的变量（_API_KEY 结尾），代表 web 检索密钥


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_agent_root(tmp_path / "repo", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.setattr(paths, "DATA", root / "data")
    (root / "data" / "episodes" / "ep").mkdir(parents=True)
    monkeypatch.setenv(LLM_NAME, "llm-secret-value-123")
    monkeypatch.setenv(WEB_NAME, "web-secret-value-456")
    monkeypatch.setenv("N50_PLAIN", "plain")
    pub = jobs.EventPublisher(queue_capacity=64, data_root=root / "data")
    pub.start()
    monkeypatch.setattr(jobs, "get_publisher", lambda: pub)
    yield root
    pub.close()


def _spy_popen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """记下作业子进程拿到的 env，argv 换成无害脚本（不真跑模块）。"""
    seen: list[dict[str, str]] = []
    real = subprocess.Popen

    def spy(argv, *a, **kw):
        seen.append(dict(kw["env"]))
        return real([sys.executable, "-c", "pass"], *a, **kw)

    monkeypatch.setattr(jobs.subprocess, "Popen", spy)
    return seen


def test_普通模块作业不带任何密钥变量(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy_popen(monkeypatch)
    out = run_pipeline("status", episode_dir=repo / "data/episodes/ep", confirmed=True)
    assert out["returncode"] == 0
    env = seen[0]
    assert LLM_NAME not in env and WEB_NAME not in env
    assert env["N50_PLAIN"] == "plain" and env["PATH"] and env["HOME"], "普通变量照旧"


@pytest.mark.parametrize("module", sorted(LLM_KEY_MODULES))
def test_调模型的模块只留LLM变量(repo: Path, monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    seen = _spy_popen(monkeypatch)
    monkeypatch.setattr(tools, "validate_pipeline_command",
                        lambda *a, **k: (True, "ok", [sys.executable, "-m", f"pipeline.{module}"]))
    run_pipeline(module, episode_dir=repo / "data/episodes/ep", confirmed=True)
    assert seen[0][LLM_NAME] == "llm-secret-value-123"
    assert WEB_NAME not in seen[0]


def test_env_drop_只认_python_m_的模块名(repo: Path) -> None:
    assert LLM_NAME not in pipeline_job_env_drop([sys.executable, "-m", "pipeline.fact_review"])
    # 不是 `-m pipeline.<模块>` 形态（如 -c 脚本里写着模块名）一律按普通模块剥
    drop_c = pipeline_job_env_drop([sys.executable, "-c", "fact_review"])
    assert LLM_NAME in drop_c and WEB_NAME in drop_c
    assert drop_c == pipeline_job_env_drop([sys.executable, "-m", "pipeline.clips"])


def test_守卫_调模型的模块都登记了() -> None:
    root = Path(__file__).resolve().parents[1] / "pipeline"
    callers = {p.stem for p in root.glob("*.py")
               if re.search(r"\bchat_complete\s*\(", p.read_text(encoding="utf-8"))}
    assert callers, "扫描真找到了调用方（不是空对空）"
    assert callers == set(LLM_KEY_MODULES), (
        f"调 chat_complete 的模块 {sorted(callers)} 与 LLM_KEY_MODULES {sorted(LLM_KEY_MODULES)} 不一致："
        "新模块要调模型就登记进去，否则作业里拿不到密钥"
    )


def test_直接立项执行不剥_现行为不变(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不经 run_pipeline 的 create_job / execute_job：Job.env_drop 默认空，环境完整。"""
    seen = _spy_popen(monkeypatch)
    job, valid, msg = jobs.create_job("status", episode_dir=repo / "data/episodes/ep")
    assert valid, msg
    jobs.execute_job(job)
    assert seen[0][LLM_NAME] == "llm-secret-value-123" and seen[0][WEB_NAME]


def test_browser_env_去掉密钥变量(repo: Path) -> None:
    env = web_browser.browser_env()
    assert LLM_NAME not in env and WEB_NAME not in env
    assert env["N50_PLAIN"] == "plain"


def test_browser_启动Camoufox时传env(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_agent_crawl_browser import _fake_camoufox

    seen = _fake_camoufox(monkeypatch)
    web_browser._default_launch_fn(user_data_dir=repo / "profile", headed=False)
    env = seen["kwargs"]["env"]   # 必须显式传：camoufox 在 env=None 时取整份 os.environ
    assert LLM_NAME not in env and WEB_NAME not in env
    assert env["N50_PLAIN"] == "plain"


_ENV_PROBE = '''"""N50 测试替身：报告作业环境里有没有密钥变量。"""
import os
print("HAS_LLM_KEY=" + str("AVA_TEST_KEY" in os.environ))
print("HAS_PATH=" + str(bool(os.environ.get("PATH"))))
'''


def test_协议_作业里看不到会话的密钥变量(world, endpoint, tmp_path: Path) -> None:  # noqa: F811
    root, episode = world
    ext = tmp_path / "pkgext"
    ext.mkdir()
    (ext / "env_probe.py").write_text(_ENV_PROBE, encoding="utf-8")
    endpoint.replies = [
        tool_call("run_pipeline", {"command": "env_probe"}, call_id="c-env"),
        {"role": "assistant", "content": "看过了"},
    ]
    proc = Protocol(root, endpoint, tmp=tmp_path / "hook",
                    env={"AVA_PROTO_PKG_EXT": str(ext), "AVA_PROTO_EXTRA_MODULES": "env_probe"})
    try:
        proc.wait_for_ready()
        proc.send({"t": "user_message", "text": "看下环境"})
        proc.wait_for("turn_finished")
        proc.shutdown()
    finally:
        assert proc.finish() == 0
    assert len(endpoint.requests) == 2, "会话进程拿得到密钥（请求真发出去了两次）"
    tool_msgs = [m for m in endpoint.requests[1]["messages"] if m.get("role") == "tool"]
    stdout = json.loads(tool_msgs[0]["content"])["result"]["stdout_tail"]
    assert "HAS_LLM_KEY=False" in stdout and "HAS_PATH=True" in stdout
