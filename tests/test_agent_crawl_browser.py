"""Spec 5 (ADR-0021): crawl + browser 网络工具单元测试（T1–T18）。

隔离铁律：全部测试零真实出网、零真实浏览器进程、零真实 profile 目录。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from pipeline import jobs, paths
from pipeline.agent import cli, llm, tools, web_browser, web_crawl
from pipeline.agent.tools import (
    TOOL_SCHEMAS,
    ToolContext,
    build_tool_schemas,
    execute_tool,
)
from pipeline.agent.web import load_web_config
from pipeline.agent.web_browser import (
    BrowserSection,
    _PlaywrightSessionAdapter,
    _assert_profile_isolation,
    _default_launch_fn,
    _reset_session_for_testing,
    browser_action,
    load_browser_section,
)
from pipeline.agent.web_crawl import (
    CrawlSection,
    _default_crawl_fn,
    crawl_page,
    load_crawl_section,
)


class FakePWError(Exception):
    """模拟 playwright.sync_api.Error / TimeoutError（Exception 直接子类，不在 tools.py 捕获面内）。"""


@pytest.fixture(scope="session", autouse=True)
def _restore_crawl4ai_base_dir_env() -> Any:
    """会话级 env 还原（S16 🟡）：进入会话前的 `CRAWL4_AI_BASE_DIRECTORY` 在这里存、也在这里还。

    为什么不能放在函数级夹具：它只能早于 monkeypatch 的 undo 收尾（T21 的
    `monkeypatch.setenv` 记录的是「执行时该变量不存在」，其 undo 会把父进程原有的值一并删掉），
    且 monkeypatch.delenv 在变量本就不存在时不记录任何 undo（_pytest/monkeypatch.py: delitem
    只在 `name in dic` 时才记）。会话级夹具的 teardown 晚于所有函数级夹具（含 monkeypatch），
    所以只有它是权威的归还点。
    """
    had_env = "CRAWL4_AI_BASE_DIRECTORY" in os.environ
    prev_env = os.environ.get("CRAWL4_AI_BASE_DIRECTORY")
    yield
    os.environ.pop("CRAWL4_AI_BASE_DIRECTORY", None)
    if had_env and prev_env is not None:
        os.environ["CRAWL4_AI_BASE_DIRECTORY"] = prev_env


@pytest.fixture(autouse=True)
def _clean_browser_and_publisher() -> Any:
    """每个用例前后重置 browser 会话单例、全局 EventPublisher、crawl4ai 进程态与基目录 env。

    crawl4ai 隔离（S16 2b/2d）：用假包导入过的 `crawl4ai*` 模块与 `_default_crawl_fn`
    写入的 `CRAWL4_AI_BASE_DIRECTORY` 都是进程级残留，不清会让后一个用例看见前一个的绑定。
    env 的「进入会话前值」由 `_restore_crawl4ai_base_dir_env` 归还（见其 docstring），
    本夹具只负责把环境变量清成「不存在」，保证用例体确定性地走「自己赋值」那条路。
    """
    _reset_session_for_testing()
    jobs._reset_global_publisher_for_testing()
    os.environ.pop("CRAWL4_AI_BASE_DIRECTORY", None)
    pre_existing = {m for m in sys.modules if m == "crawl4ai" or m.startswith("crawl4ai.")}
    yield
    for mod in [m for m in sys.modules if m == "crawl4ai" or m.startswith("crawl4ai.")]:
        if mod not in pre_existing:
            del sys.modules[mod]
    os.environ.pop("CRAWL4_AI_BASE_DIRECTORY", None)
    _reset_session_for_testing()
    jobs._reset_global_publisher_for_testing()


def _pin_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """钉死 DNS 解析返回公网测试地址 93.184.216.34，杜绝真实 DNS 出网。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ],
    )


def _make_full_web_root(tmp_path: Path) -> Path:
    """构造含合法 search/fetch/crawl/browser 四段 web.json、tools.json 与 data/ 的临时仓库根。"""
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (cfg_dir / "web.json").write_text(
        json.dumps(
            {
                "search": {
                    "endpoint": "https://html.duckduckgo.com/html/",
                    "query_param": "q",
                    "api_key_env": "",
                    "api_key_param": "",
                    "timeout_s": 20,
                },
                "fetch": {
                    "timeout_s": 30,
                    "max_bytes": 1000000,
                    "max_chars": 30000,
                },
                "crawl": {
                    "timeout_s": 60,
                    "max_chars": 30000,
                },
                "browser": {
                    "profile_dir": "data/browser-profile",
                    "headed": True,
                    "timeout_s": 60,
                },
            }
        ),
        encoding="utf-8",
    )
    (cfg_dir / "tools.json").write_text(
        json.dumps(
            {
                "creative": [
                    "read_artifact",
                    "write_episode_file",
                    "list_episodes",
                    "read_status",
                    "search_notes",
                    "web_search",
                    "web_fetch",
                    "crawl",
                    "browser",
                ],
                "pipeline": ["read_artifact", "read_status", "list_episodes", "run_pipeline"],
                "asset": ["web_search", "web_fetch", "acquire_propose", "crawl", "browser"],
                "idea": ["read_artifact", "list_episodes", "read_status", "search_notes"],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# PR1: T1–T6, T14a, T17 (crawl)
# ---------------------------------------------------------------------------


def test_crawl_returns_scrubbed_capped_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """T1 (PR1): crawl 解析、_scrub 脱敏、max_chars 封顶与空 reason 拒收。"""
    _pin_public_dns(monkeypatch)
    sec = CrawlSection(timeout_s=30.0, max_chars=1200)

    def fake_crawl(url: str, *, stealth: bool, timeout_s: float) -> dict[str, str]:
        body = "前缀机密 cloud.local.json 泄露测试 " + ("正文段落" * 400)
        return {"final_url": "https://example.com/final-article", "markdown": body}

    res = crawl_page(
        "https://example.com/article",
        "web_fetch 返回 403 Forbidden",
        stealth=True,
        section=sec,
        crawl_fn=fake_crawl,
    )
    assert res["url"] == "https://example.com/article"
    assert res["final_url"] == "https://example.com/final-article"
    assert "cloud.local.json" not in res["markdown"]
    assert "[已脱敏]" in res["markdown"]
    assert len(res["markdown"]) == 1200
    assert res["truncated"] is True
    assert res["stealth"] is True

    # reason 为空 → ValueError
    with pytest.raises(ValueError, match="reason"):
        crawl_page("https://example.com/article", "   ", section=sec, crawl_fn=fake_crawl)

    # 渲染失败 → 包装为含升级 browser 与严禁水百科提示的 ValueError
    def failing_crawl(url: str, *, stealth: bool, timeout_s: float) -> dict[str, str]:
        raise RuntimeError("Cloudflare turnstile blocked")

    with pytest.raises(ValueError, match="升级 browser"):
        crawl_page(
            "https://example.com/article",
            "web_fetch 403",
            section=sec,
            crawl_fn=failing_crawl,
        )


def test_crawl_egress_blocked_before_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """T2 (PR1): crawl 出网前拦截（编码形态与大小写变体），fake crawl_fn 零调用。"""
    _pin_public_dns(monkeypatch)
    sec = CrawlSection(timeout_s=30.0, max_chars=5000)
    calls: list[str] = []

    def fake_crawl(url: str, *, stealth: bool, timeout_s: float) -> dict[str, str]:
        calls.append(url)
        return {"final_url": url, "markdown": "ok"}

    for bad_url in (
        "http://example.com/?q=cloud%2Elocal%2Ejson",
        "https://example.com/path/Cloud.Local.JSON",
    ):
        with pytest.raises(PermissionError, match="拦截出网请求"):
            crawl_page(bad_url, "web_fetch 403", section=sec, crawl_fn=fake_crawl)
    assert len(calls) == 0


def test_crawl_rejects_bad_scheme_and_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """T3 (PR1): crawl 拒绝非 http/https 与私网/保留 IP，fake 零调用。"""
    sec = CrawlSection(timeout_s=30.0, max_chars=5000)
    calls: list[str] = []

    def fake_crawl(url: str, *, stealth: bool, timeout_s: float) -> dict[str, str]:
        calls.append(url)
        return {"final_url": url, "markdown": "ok"}

    for bad_scheme in ("ftp://example.com/a", "file:///etc/passwd"):
        with pytest.raises(PermissionError):
            crawl_page(bad_scheme, "web_fetch 失败", section=sec, crawl_fn=fake_crawl)

    for private_ip in ("169.254.169.254", "127.0.0.1"):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda host, port, *a, ip=private_ip, **kw: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
            ],
        )
        with pytest.raises(PermissionError):
            crawl_page("https://example.com/internal", "web_fetch 失败", section=sec, crawl_fn=fake_crawl)

    assert len(calls) == 0


def test_extras_missing_hides_and_blocks_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T4 (PR1): 核心验收 1——未装 extras 三层证据（注册表常驻 + schema 隐藏 + execute_tool 能力闸）。"""
    monkeypatch.setattr(tools, "_extra_available", lambda dist: False)
    root = _make_full_web_root(tmp_path)

    # ① 注册表常驻
    assert "crawl" in TOOL_SCHEMAS
    assert "browser" in TOOL_SCHEMAS

    # ② schema 层隐藏
    for scope in ("creative", "asset"):
        names = [s["function"]["name"] for s in build_tool_schemas(scope, root=root)]
        assert "crawl" not in names
        assert "browser" not in names

    # ③ execute_tool 能力闸显式报错、不抛异常、fake 零调用
    crawl_calls: list[str] = []
    monkeypatch.setattr(
        web_crawl,
        "crawl_page",
        lambda *a, **kw: crawl_calls.append("called") or {},
    )
    ctx = ToolContext(scope="creative", root=root)
    out_crawl = execute_tool(
        "crawl", {"url": "https://example.com", "reason": "web_fetch 403"}, ctx
    )
    assert out_crawl["ok"] is False
    assert "可选依赖" in out_crawl["error"]
    assert "当前环境未安装" in out_crawl["error"]
    assert len(crawl_calls) == 0

    out_browser = execute_tool(
        "browser",
        {"action": "navigate", "url": "https://example.com", "reason": "crawl 遇盾"},
        ctx,
    )
    assert out_browser["ok"] is False
    assert "可选依赖" in out_browser["error"]
    assert "当前环境未安装" in out_browser["error"]


def test_extras_present_exposes_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T5 (PR1): 能力掩码正向腿 + 协议键白名单 + 参数 schema 钉死。"""
    monkeypatch.setattr(tools, "_extra_available", lambda dist: True)
    root = _make_full_web_root(tmp_path)

    for scope in ("creative", "asset"):
        schemas = build_tool_schemas(scope, root=root)
        by_name = {s["function"]["name"]: s["function"] for s in schemas}
        assert "crawl" in by_name
        assert "browser" in by_name
        for fn in by_name.values():
            assert set(fn.keys()) == {"name", "description", "parameters"}
            assert "requires_extra" not in fn
            assert "side_effect" not in fn
            assert "adr" not in fn

        crawl_params = by_name["crawl"]["parameters"]
        assert crawl_params["required"] == ["url", "reason"]
        assert set(crawl_params["properties"].keys()) == {"url", "reason", "stealth"}

        browser_params = by_name["browser"]["parameters"]
        assert set(browser_params["properties"].keys()) == {"action", "url", "reason"}
        assert browser_params["properties"]["action"]["enum"] == ["navigate", "extract_text"]
        assert browser_params["required"] == ["action", "reason"]


def test_crawl_browser_modules_pure() -> None:
    """T6 (PR1): §5.2 双腿子进程纯洁性探针（顶层零重包 + 探测路径零重包副作用）。"""
    probe_import = (
        "import pipeline.agent.web_crawl, pipeline.agent.web_browser, sys; "
        "forbidden = ('crawl4ai', 'playwright', 'camoufox', 'numpy', 'torch', "
        "             'moviepy', 'transformers', 'requests', 'httpx', 'bs4', 'lxml'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'顶层违规引入: {leaked}'"
    )
    res1 = subprocess.run([sys.executable, "-c", probe_import], capture_output=True, text=True)
    assert res1.returncode == 0, f"纯洁性检验第一腿失败:\n{res1.stderr}"

    probe_capability = (
        "from pipeline.agent import tools; "
        "tools._extra_available('crawl4ai'); tools._extra_available('playwright'); "
        "tools.build_tool_schemas('creative'); "
        "import sys; "
        "leaked = [m for m in ('crawl4ai', 'playwright') if m in sys.modules]; "
        "assert not leaked, f'探测路径泄漏重包: {leaked}'"
    )
    res2 = subprocess.run([sys.executable, "-c", probe_capability], capture_output=True, text=True)
    assert res2.returncode == 0, f"探测侧效应检验第二腿失败:\n{res2.stderr}"


# ---------------------------------------------------------------------------
# PR2: T7–T13, T14b, T16, T17 (browser), T18
# ---------------------------------------------------------------------------


def test_profile_isolation_guard(tmp_path: Path) -> None:
    """T7 (PR2): 核心验收 2——profile 隔离守卫放行 data/ 子树，拒绝 Chrome Default 等 6 类危险路径。"""
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True, exist_ok=True)

    resolved = _assert_profile_isolation("data/browser-profile", root)
    assert resolved == (root / "data" / "browser-profile").resolve()

    dangerous_paths = [
        "~/Library/Application Support/Google/Chrome/Default",
        "~/Library/Application Support/Google/Chrome",
        "~/.config/pi-browser-profile",
        str(root),
        ".",
        "/tmp/x",
        "data",
        str(root / "data"),
    ]
    for bad in dangerous_paths:
        with pytest.raises(PermissionError):
            _assert_profile_isolation(bad, root)


def test_browser_schema_has_no_profile_override() -> None:
    """T8 (PR2): TOOL_SCHEMAS['browser']['parameters'] 全文不含 profile/headless/user_data_dir。"""
    raw_params = json.dumps(TOOL_SCHEMAS["browser"]["parameters"], ensure_ascii=False).lower()
    for forbidden_word in ("profile", "headless", "user_data_dir"):
        assert forbidden_word not in raw_params


class _FakeBrowserSession:
    def __init__(
        self,
        *,
        pid: int | None = 48123,
        final_url: str = "https://example.com/home",
        title: str = "Example Title",
        text: str = "干净页面正文",
        alive: bool = True,
        fail_on_op: Exception | None = None,
        fail_on_probe: Exception | None = None,
    ) -> None:
        self.pid = pid
        self.final_url = final_url
        self.title = title
        self.text = text
        self.alive = alive
        self.fail_on_op = fail_on_op
        self.fail_on_probe = fail_on_probe
        self.closed = False
        self.goto_calls: list[str] = []
        self.extract_calls = 0

    def probe(self) -> bool:
        if self.fail_on_probe is not None:
            raise self.fail_on_probe
        return self.alive

    def goto(self, url: str, *, timeout_s: float = 60.0) -> dict[str, str]:
        self.goto_calls.append(url)
        if self.fail_on_op is not None:
            raise self.fail_on_op
        return {"final_url": self.final_url, "title": self.title}

    def extract_text(self, *, timeout_s: float = 60.0) -> dict[str, str]:
        self.extract_calls += 1
        if self.fail_on_op is not None:
            raise self.fail_on_op
        return {"url": self.final_url, "text": self.text}

    def close(self) -> None:
        self.closed = True


def test_browser_launch_uses_pinned_profile_and_emits_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T9 (PR2): 核心验收 3——启动使用钉死 profile 路径 + publisher.close() 后落盘 browser_session_started。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    monkeypatch.setenv("AVA_EVENTS_ROOT", str(root / "data"))
    jobs._reset_global_publisher_for_testing()

    launch_records: list[dict[str, Any]] = []

    def fake_launch(*, user_data_dir: Path, headed: bool) -> _FakeBrowserSession:
        launch_records.append({"user_data_dir": user_data_dir, "headed": headed})
        return _FakeBrowserSession(
            pid=48123,
            final_url="https://example.com/dashboard",
            title="Creator Dashboard",
        )

    out = browser_action(
        "navigate",
        "web_fetch 403，crawl 遇盾",
        url="https://example.com/dashboard",
        launch_fn=fake_launch,
        scope="asset",
        root=root,
    )

    expected_profile = (root / "data" / "browser-profile").resolve()
    # ① fake 收到钉死路径与 headed=True
    assert len(launch_records) == 1
    assert launch_records[0]["user_data_dir"] == expected_profile
    assert launch_records[0]["headed"] is True

    # ② 返回值含 final_url 与 title
    assert out["final_url"] == "https://example.com/dashboard"
    assert out["title"] == "Creator Dashboard"

    # ③ publisher.close() 后读 events.jsonl
    jobs.get_publisher().close()
    events_file = root / "data" / "_events.jsonl"
    assert events_file.exists()
    events = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    started_events = [e for e in events if e["type"] == "browser_session_started"]
    assert len(started_events) == 1
    payload = started_events[0]["payload"]
    for req_key in ("profile_dir", "headed", "scope", "trigger"):
        assert req_key in payload
    assert payload["profile_dir"] == str(expected_profile)
    assert payload["headed"] is True
    assert payload["scope"] == "asset"
    assert payload["trigger"]["reason"] == "web_fetch 403，crawl 遇盾"
    assert payload["trigger"]["url"] == "https://example.com/dashboard"
    if "pid" in payload:
        assert payload["pid"] > 0
    assert "approved_via" not in payload

    # ④ 全文不含 cookie 字段
    assert "cookie" not in json.dumps(started_events[0], ensure_ascii=False).lower()

    # ⑤ profile 目录首建权限 0700（§2.2 / RF-14）
    assert stat.S_IMODE(expected_profile.stat().st_mode) == 0o700


def test_browser_card_per_call_and_reject_blocks_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T10 (PR2): 核心验收 4——live-loop 下逐调用过 _default_approve 人审卡，拒绝则零启动零事件，target 含所批 URL。"""
    _pin_public_dns(monkeypatch)
    monkeypatch.setattr(tools, "_extra_available", lambda dist: True)

    root = _make_full_web_root(tmp_path)
    (root / "config" / "agent.json").write_text(
        json.dumps(
            {
                "base_url": "https://api.example.com/v1",
                "model": "mock-model",
                "api_key_env": "AVA_TEST_KEY",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AVA_TEST_KEY", "sk-test-key")
    monkeypatch.setenv("AVA_EVENTS_ROOT", str(root / "data"))
    jobs._reset_global_publisher_for_testing()

    ep_dir = root / "data" / "episodes" / "01-browser-card"
    ep_dir.mkdir(parents=True, exist_ok=True)

    launch_calls: list[Path] = []

    def fake_launch(*, user_data_dir: Path, headed: bool) -> _FakeBrowserSession:
        launch_calls.append(user_data_dir)
        return _FakeBrowserSession(final_url="https://example.com/login", title="Login")

    monkeypatch.setattr(web_browser, "_default_launch_fn", fake_launch)

    # 构造 LLM 响应序列
    replies: list[dict[str, Any]] = []

    class _FakeLLMResp:
        def __init__(self, body: dict[str, Any]) -> None:
            self._raw = json.dumps({"choices": [{"message": body}]}).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _FakeLLMResp:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    call_idx = 0

    def fake_urlopen(req: Any, timeout: float = 60.0) -> _FakeLLMResp:
        nonlocal call_idx
        msg = replies[min(call_idx, len(replies) - 1)]
        call_idx += 1
        return _FakeLLMResp(msg)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)

    tc_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_b1",
                "type": "function",
                "function": {
                    "name": "browser",
                    "arguments": json.dumps(
                        {
                            "action": "navigate",
                            "url": "https://example.com/login",
                            "reason": "crawl 遇登录墙",
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }

    # 第一轮：人输入 "n" 拒绝
    replies[:] = [tc_msg, {"role": "assistant", "content": "已收到拒绝"}]
    call_idx = 0
    monkeypatch.setattr("builtins.input", lambda: "n")

    ctx = ToolContext(scope="creative", episode_dir=ep_dir, root=root)
    outcome_reject = llm.run_tool_loop(
        [{"role": "user", "content": "打开登录页"}],
        ctx=ctx,
        approve=lambda n, a: cli._default_approve(n, a, ep_dir=ep_dir, scope="creative", root=root),
        root=root,
    )
    assert len(launch_calls) == 0
    jobs.get_publisher().close()
    ep_events_file = ep_dir / "events.jsonl"
    if ep_events_file.exists():
        evts = [json.loads(l) for l in ep_events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert [e for e in evts if e["type"] == "browser_session_started"] == []
    tool_msgs = [m for m in outcome_reject["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0]["content"])["ok"] is False
    assert "拒绝" in json.loads(tool_msgs[0]["content"])["error"]

    # 第二轮：人输入 "y" 批准
    jobs._reset_global_publisher_for_testing()
    replies[:] = [tc_msg, {"role": "assistant", "content": "已完成导航"}]
    call_idx = 0
    monkeypatch.setattr("builtins.input", lambda: "y")

    llm.run_tool_loop(
        [{"role": "user", "content": "打开登录页"}],
        ctx=ctx,
        approve=lambda n, a: cli._default_approve(n, a, ep_dir=ep_dir, scope="creative", root=root),
        root=root,
    )
    assert len(launch_calls) == 1
    jobs.get_publisher().close()
    assert ep_events_file.exists()
    evts2 = [json.loads(l) for l in ep_events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len([e for e in evts2 if e["type"] == "browser_session_started"]) == 1

    # approvals.jsonl 含两条记录且 target 含所批 URL
    ledger = ep_dir / "_agent" / "approvals.jsonl"
    assert ledger.exists()
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2
    assert [r["decision"] for r in rows] == ["n", "y"]
    for r in rows:
        assert "https://example.com/login" in r["target"]


def test_browser_session_reuse_no_duplicate_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T11 (PR2): 同进程会话复用不重复启动/不重复发事件；死会话自愈分流（探活死→重启+新事件，探活活→直接包装不重启）。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    monkeypatch.setenv("AVA_EVENTS_ROOT", str(root / "data"))
    jobs._reset_global_publisher_for_testing()

    sessions_to_return: list[_FakeBrowserSession] = []
    launch_count = 0

    def fake_launch(*, user_data_dir: Path, headed: bool) -> _FakeBrowserSession:
        nonlocal launch_count
        launch_count += 1
        if sessions_to_return:
            return sessions_to_return.pop(0)
        return _FakeBrowserSession()

    # 连续 3 次调用（navigate -> extract_text -> navigate）
    browser_action("navigate", "r1", url="https://example.com/1", launch_fn=fake_launch, root=root)
    browser_action("extract_text", "r2", launch_fn=fake_launch, root=root)
    browser_action("navigate", "r3", url="https://example.com/2", launch_fn=fake_launch, root=root)

    assert launch_count == 1
    jobs.get_publisher().close()
    events_file = root / "data" / "_events.jsonl"
    evts = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len([e for e in evts if e["type"] == "browser_session_started"]) == 1

    # _reset_session_for_testing() 后再调 → launch +1、事件 +1
    _reset_session_for_testing()
    jobs._reset_global_publisher_for_testing()
    browser_action("navigate", "r4", url="https://example.com/3", launch_fn=fake_launch, root=root)
    assert launch_count == 2
    jobs.get_publisher().close()
    evts2 = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len([e for e in evts2 if e["type"] == "browser_session_started"]) == 2

    # 死会话腿：操作与探活均抛 FakePWError → 丢弃旧会话、自动重启（launch +1，事件 +1）并重试成功
    monkeypatch.setattr(web_browser, "_pw_error_types", lambda: (FakePWError,))
    dead_curr = web_browser._SESSION
    assert isinstance(dead_curr, _FakeBrowserSession)
    dead_curr.fail_on_op = FakePWError("Target page, context or browser has been closed")
    dead_curr.fail_on_probe = FakePWError("Browser closed")

    healthy_replacement = _FakeBrowserSession(final_url="https://example.com/recovered", title="Recovered")
    sessions_to_return.append(healthy_replacement)
    jobs._reset_global_publisher_for_testing()

    recovered_out = browser_action(
        "navigate", "r5", url="https://example.com/recovered", launch_fn=fake_launch, root=root
    )
    assert recovered_out["title"] == "Recovered"
    assert launch_count == 3
    jobs.get_publisher().close()
    evts3 = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len([e for e in evts3 if e["type"] == "browser_session_started"]) == 3

    # 对照腿（🟡-R5）：探活存活时，操作抛 FakePWError 直接包装为 ValueError「操作失败」，零重启
    healthy_replacement.fail_on_op = FakePWError("Navigation timeout of 60000 ms exceeded")
    healthy_replacement.fail_on_probe = None
    healthy_replacement.alive = True
    with pytest.raises(ValueError, match="browser 操作失败"):
        browser_action(
            "navigate", "r6", url="https://example.com/slow", launch_fn=fake_launch, root=root
        )
    assert launch_count == 3


def test_browser_navigate_egress_and_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T12 (PR2): browser 出网双闸 + 无会话禁 extract_text + 落地 final_url 重定向复跑 _guard_url。"""
    root = _make_full_web_root(tmp_path)
    launch_count = 0

    def fake_launch(*, user_data_dir: Path, headed: bool) -> _FakeBrowserSession:
        nonlocal launch_count
        launch_count += 1
        return _FakeBrowserSession()

    # 1. egress 拦截先于启动
    _pin_public_dns(monkeypatch)
    with pytest.raises(PermissionError, match="拦截出网请求"):
        browser_action(
            "navigate",
            "r",
            url="https://example.com/?leak=cloud%2Elocal%2Ejson",
            launch_fn=fake_launch,
            root=root,
        )
    assert launch_count == 0

    # 2. 私网 IP 拦截先于启动
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ],
    )
    with pytest.raises(PermissionError):
        browser_action(
            "navigate",
            "r",
            url="https://internal.example.com",
            launch_fn=fake_launch,
            root=root,
        )
    assert launch_count == 0

    # 3. 无存活会话时 extract_text 抛 ValueError「未启动」，不隐式启动
    _pin_public_dns(monkeypatch)
    with pytest.raises(ValueError, match="未启动"):
        browser_action("extract_text", "r", launch_fn=fake_launch, root=root)
    assert launch_count == 0

    # 4. 重定向腿（🟡-4 / 🟡-R3）：分主机钉死 getaddrinfo，初始 URL 合法公网，落地 final_url 跳进 169.254.169.254
    def per_host_dns(host: str, port: Any, *a: Any, **kw: Any) -> list[Any]:
        ip = "169.254.169.254" if host in ("169.254.169.254", "metadata.internal") else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]

    monkeypatch.setattr(socket, "getaddrinfo", per_host_dns)

    redirect_sess = _FakeBrowserSession(
        final_url="http://169.254.169.254/latest/meta-data/",
        title="INTERNAL_SECRET_META",
    )
    _reset_session_for_testing()
    with pytest.raises(PermissionError):
        browser_action(
            "navigate",
            "r",
            url="https://example.com/jump-to-meta",
            launch_fn=lambda **kw: redirect_sess,
            root=root,
        )

    # 5. extract_text 落地复跑腿（S16 🔵-1）：navigate 合法、当前页 URL 已在内网 → 复跑 _guard_url 拦截
    _reset_session_for_testing()
    live_sess = _FakeBrowserSession(final_url="https://example.com/ok", title="ok")
    browser_action(
        "navigate", "r", url="https://example.com/ok", launch_fn=lambda **kw: live_sess, root=root
    )
    live_sess.final_url = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(PermissionError):
        browser_action("extract_text", "r", launch_fn=lambda **kw: live_sess, root=root)
    assert live_sess.extract_calls == 1  # 证到的是落地复跑，而非导航腿先炸


def test_browser_extract_text_scrubbed_and_capped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T13 (PR2): extract_text 结果经 _scrub 脱敏与 max_chars 截断。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    sec = BrowserSection(profile_dir="data/browser-profile", headed=True, timeout_s=30.0, max_chars=1000)

    dirty_text = "发现敏感词 cloud.local.json 以及 03-audio/manifest.json " + ("长文本填充" * 300)
    sess = _FakeBrowserSession(final_url="https://example.com/post", text=dirty_text)

    browser_action(
        "navigate",
        "先导航启动会话",
        url="https://example.com/post",
        section=sec,
        launch_fn=lambda **kw: sess,
        root=root,
    )
    res = browser_action(
        "extract_text",
        "提取正文",
        section=sec,
        launch_fn=lambda **kw: sess,
        root=root,
    )
    assert "cloud.local.json" not in res["text"]
    assert "03-audio" not in res["text"]
    assert "[已脱敏]" in res["text"]
    assert len(res["text"]) == 1000
    assert res["truncated"] is True


def test_crawl_section_independent_degradation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T14a (PR1): 缺/坏 crawl 段 → load_crawl_section 返回 None、crawl 显式报错，不影响 Spec 4 基座段。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    web_cfg_path = root / "config" / "agent" / "web.json"

    # 删掉 crawl 段
    data = json.loads(web_cfg_path.read_text(encoding="utf-8"))
    del data["crawl"]
    web_cfg_path.write_text(json.dumps(data), encoding="utf-8")

    assert load_crawl_section(root) is None
    assert load_web_config(root) is not None
    with pytest.raises(ValueError, match="crawl 段"):
        crawl_page("https://example.com", "r", root=root, crawl_fn=lambda *a, **kw: {})

    # 损坏 crawl 段（类型错）
    data["crawl"] = {"timeout_s": "bad", "max_chars": 30000}
    web_cfg_path.write_text(json.dumps(data), encoding="utf-8")
    assert load_crawl_section(root) is None
    assert load_web_config(root) is not None


def test_browser_section_independent_degradation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T14b (PR2): 缺/坏 browser 段 → None 且不影响 crawl 段；profile_dir 指向 ../outside → PermissionError。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    web_cfg_path = root / "config" / "agent" / "web.json"

    data = json.loads(web_cfg_path.read_text(encoding="utf-8"))
    del data["browser"]
    web_cfg_path.write_text(json.dumps(data), encoding="utf-8")

    assert load_browser_section(root) is None
    assert load_crawl_section(root) is not None
    with pytest.raises(ValueError, match="browser 段"):
        browser_action("navigate", "r", url="https://example.com", root=root)

    # profile_dir 越界属于对抗性配置，必须抛 PermissionError 而非 None
    data["browser"] = {"profile_dir": "../outside", "headed": True, "timeout_s": 60}
    web_cfg_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PermissionError):
        load_browser_section(root)
    with pytest.raises(PermissionError):
        browser_action("navigate", "r", url="https://example.com", root=root)


def test_partial_install_import_failure_is_explicit_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T17 (PR1/PR2): 🔴-1 find_spec 假阳性（部分/损坏安装）下 ImportError 包装为 ValueError，不穿透 execute_tool。

    两条腿都钉死 `paths.DATA`（S16 🟡）：不钉就会去读真实仓库的 data/——
    那是外置盘符号链接，拔盘时本用例会先报「data/ 不可达」而非「导入失败」，
    违反 pyproject「测试不需要外置盘，clone 下来装完就能跑」。
    """
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    monkeypatch.setattr(paths, "DATA", root / "data")

    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    # 腿①：直调默认实现抛 ValueError（含导入失败/重装），绝非 ImportError
    with pytest.raises(ValueError, match="导入失败"):
        _default_crawl_fn("https://example.com", stealth=False, timeout_s=60.0)

    with pytest.raises(ValueError, match="导入失败"):
        _default_launch_fn(user_data_dir=root / "data" / "browser-profile", headed=True)

    # 腿②：_extra_available 钉 True 模拟 find_spec 假阳性，execute_tool 返回显式错误数据且不抛异常
    monkeypatch.setattr(tools, "_extra_available", lambda dist: True)
    ctx = ToolContext(scope="creative", root=root)

    res_crawl = execute_tool(
        "crawl", {"url": "https://example.com", "reason": "web_fetch 403"}, ctx
    )
    assert res_crawl["ok"] is False
    assert "导入失败" in res_crawl["error"]

    res_browser = execute_tool(
        "browser",
        {"action": "navigate", "url": "https://example.com", "reason": "crawl 遇盾"},
        ctx,
    )
    assert res_browser["ok"] is False
    assert "导入失败" in res_browser["error"]


def test_playwright_errors_wrapped_as_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T18 (PR2): 🔴-2 playwright 异常族包装为 ValueError，非 playwright 异常原样穿透。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    monkeypatch.setattr(web_browser, "_pw_error_types", lambda: (FakePWError,))

    # 1. 启动抛 FakePWError → 包装为 ValueError 含「browser 操作失败」
    def failing_launch(*, user_data_dir: Path, headed: bool) -> Any:
        raise FakePWError("Failed to launch persistent context: SingletonLock")

    with pytest.raises(ValueError, match="browser 操作失败"):
        browser_action(
            "navigate",
            "r",
            url="https://example.com",
            launch_fn=failing_launch,
            root=root,
        )

    # 2. navigate 超时抛 FakePWError（探活桩存活）→ 包装为 ValueError 含「browser 操作失败」
    timeout_sess = _FakeBrowserSession(
        alive=True,
        fail_on_op=FakePWError("Timeout 60000ms exceeded"),
    )
    with pytest.raises(ValueError, match="browser 操作失败"):
        browser_action(
            "navigate",
            "r",
            url="https://example.com",
            launch_fn=lambda **kw: timeout_sess,
            root=root,
        )

    # 3. 对照腿：非 playwright 异常（PermissionError、参数 ValueError）原样穿透不被误包
    with pytest.raises(PermissionError):
        browser_action(
            "navigate",
            "r",
            url="ftp://example.com/bad",
            launch_fn=lambda **kw: _FakeBrowserSession(),
            root=root,
        )
    with pytest.raises(ValueError, match="未知 browser action"):
        browser_action(
            "click",
            "r",
            url="https://example.com",
            launch_fn=lambda **kw: _FakeBrowserSession(),
            root=root,
        )

    # 4. 操作阶段对照腿（S16 🔵-2）：fake 会话 goto 抛 RuntimeError → 原样抛 RuntimeError
    _reset_session_for_testing()
    crash_sess = _FakeBrowserSession(alive=True, fail_on_op=RuntimeError("driver crashed"))
    with pytest.raises(RuntimeError, match="driver crashed"):
        browser_action(
            "navigate",
            "r",
            url="https://example.com",
            launch_fn=lambda **kw: crash_sess,
            root=root,
        )


# ---------------------------------------------------------------------------
# PR3 / S16 补测（🟡-1/🟡-2/🟡-3/🔵-5/🔵-1/🔵-2 六条腿；全部零真实出网、零真实浏览器）
# ---------------------------------------------------------------------------


class _FakePW:
    """假 playwright handle：只需 stop()。"""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakePWContext:
    """假 persistent context：与 playwright 同构的探活面（on/pages/close）。"""

    def __init__(self) -> None:
        self.pages: list[Any] = []
        self.closed = False
        self._listeners: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def close(self) -> None:
        """关窗：触发已注册的 "close" 监听（playwright 的真实行为）。"""
        if self.closed:
            return
        self.closed = True
        for cb in self._listeners.get("close", []):
            cb(self)

    def new_page(self) -> Any:
        raise AssertionError("探活不应触发 new_page")


def test_adapter_probe_marks_dead_on_context_close() -> None:
    """T19 (S16 🟡-1): adapter 订阅 context.on("close")——关窗后 probe() 由 True 转 False。

    context.pages 在 playwright 中只是本地列表拷贝，关窗后访问不抛异常，不能用于探活。
    """
    pw = _FakePW()
    ctx = _FakePWContext()
    adapter = _PlaywrightSessionAdapter(pw=pw, context=ctx)

    assert adapter.probe() is True
    ctx.close()  # 模拟浏览器窗口被关闭
    assert adapter.probe() is False

    adapter.close()
    assert pw.stopped is True


_FAKE_CRAWL4AI_INIT = '''\
"""假 crawl4ai 包：与真包同构的最小面（S16 2b/2d）。

真包 import 链会拉入 crawl4ai.async_database，基目录在那里定死（async_database.py:17-19）。
"""
import os

from . import async_database  # noqa: F401
from .crawler import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig  # noqa: F401

# import 时记录当时生效的 CRAWL4_AI_BASE_DIRECTORY，供测试断言「赋值先于 import」
_CAPTURED_ENV = os.environ.get("CRAWL4_AI_BASE_DIRECTORY")
'''

_FAKE_CRAWL4AI_ASYNC_DB = '''\
import os

# 与真包 crawl4ai/async_database.py:17-19 逐字同构（真包用 getenv(..., Path.home()) 兜底，
# 假包用 [] 直取：env 未设即 KeyError 大声失败，好让「删赋值行」的变异必红）
base_directory = os.path.join(os.environ["CRAWL4_AI_BASE_DIRECTORY"], ".crawl4ai")
'''

_FAKE_CRAWL4AI_CRAWLER = '''\
class BrowserConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class CrawlerRunConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Markdown:
    fit_markdown = "# fit 正文"
    raw_markdown = "# raw 正文"


class _Result:
    success = True
    error_message = ""
    markdown = _Markdown()
    redirected_url = "https://example.com/final"


class AsyncWebCrawler:
    def __init__(self, config=None):
        self.config = config
        self.arun_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def arun(self, url, config=None):
        self.arun_calls.append(url)
        return _Result()
'''


def _make_fake_crawl4ai_pkg(tmp_path: Path) -> Path:
    """在 tmp 下造一个与真包同构的假 crawl4ai 包，返回可前插 sys.path 的父目录。"""
    pkg = tmp_path / "fakepkgs" / "crawl4ai"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_FAKE_CRAWL4AI_INIT, encoding="utf-8")
    (pkg / "async_database.py").write_text(_FAKE_CRAWL4AI_ASYNC_DB, encoding="utf-8")
    (pkg / "crawler.py").write_text(_FAKE_CRAWL4AI_CRAWLER, encoding="utf-8")
    return tmp_path / "fakepkgs"


def _purge_crawl4ai_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    for mod in [m for m in sys.modules if m == "crawl4ai" or m.startswith("crawl4ai.")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)


def test_default_crawl_fn_runs_coroutine_in_worker_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T20 (S16 🟡-2): 当前线程已有 running loop 时，_default_crawl_fn 仍能跑完协程并返回 markdown。

    协程走 ThreadPoolExecutor 的独立线程，故主线程遗留的 _set_running_loop 状态不影响它。
    """
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA", root / "data")
    monkeypatch.syspath_prepend(str(_make_fake_crawl4ai_pkg(tmp_path)))
    _purge_crawl4ai_modules(monkeypatch)

    loop = asyncio.new_event_loop()
    asyncio._set_running_loop(loop)
    try:
        out = _default_crawl_fn("https://example.com/rendered", stealth=False, timeout_s=30.0)
    finally:
        asyncio._set_running_loop(None)
        loop.close()

    assert out == {"final_url": "https://example.com/final", "markdown": "# fit 正文"}


def test_default_crawl_fn_forces_base_dir_env_before_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T21 (S16 🔵-5): 假包 import 时记录的 CRAWL4_AI_BASE_DIRECTORY 必为目标 data/crawl4ai。

    预设成别的值（模拟 pi 侧为 agent_crawl 设的值）仍被覆盖；已导入且绑定别处的包 → ValueError。
    """
    root = tmp_path / "repo"
    data = root / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA", data)
    monkeypatch.syspath_prepend(str(_make_fake_crawl4ai_pkg(tmp_path)))
    _purge_crawl4ai_modules(monkeypatch)

    preset = str(tmp_path / "pi-shared-home")
    monkeypatch.setenv("CRAWL4_AI_BASE_DIRECTORY", preset)

    _default_crawl_fn("https://example.com/x", stealth=False, timeout_s=30.0)

    target = str(data / "crawl4ai")
    assert preset  # 预设值确实在场，不是空跑
    assert sys.modules["crawl4ai"]._CAPTURED_ENV == target
    assert os.path.normpath(sys.modules["crawl4ai.async_database"].base_directory) == os.path.normpath(
        str(data / "crawl4ai" / ".crawl4ai")
    )

    # 已导入且绑定别处：诚实失败，绝不静默污染 home/共用目录，且不改 env
    _purge_crawl4ai_modules(monkeypatch)
    fake_pkg = types.ModuleType("crawl4ai")
    fake_db = types.ModuleType("crawl4ai.async_database")
    fake_db.base_directory = os.path.join(str(tmp_path / "elsewhere"), ".crawl4ai")
    fake_pkg.async_database = fake_db
    monkeypatch.setitem(sys.modules, "crawl4ai", fake_pkg)
    monkeypatch.setitem(sys.modules, "crawl4ai.async_database", fake_db)

    with pytest.raises(ValueError, match="无法重绑定"):
        _default_crawl_fn("https://example.com/x", stealth=False, timeout_s=30.0)
    assert os.environ["CRAWL4_AI_BASE_DIRECTORY"] == target

    # 第三腿（S16 X-6）：已导入但绑定目录不可知（无 crawl4ai.async_database）→ 同样拒绝，不静默写 home
    _purge_crawl4ai_modules(monkeypatch)
    _default_crawl_fn("https://example.com/x", stealth=False, timeout_s=30.0)
    fake_pkg = sys.modules["crawl4ai"]
    monkeypatch.delitem(sys.modules, "crawl4ai.async_database", raising=False)
    monkeypatch.delattr(fake_pkg, "async_database", raising=False)
    assert web_crawl._resolve_bound_crawl4ai_dir() is None
    with pytest.raises(ValueError, match="未知目录"):
        _default_crawl_fn("https://example.com/x", stealth=False, timeout_s=30.0)


def test_missing_data_dir_is_explicit_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T22 (S16 🟡-3): data/ 不可达 → ValueError 含「data/ 不可达」，且绝不自动创建 data/。"""
    _pin_public_dns(monkeypatch)
    root = _make_full_web_root(tmp_path)
    shutil.rmtree(root / "data")

    launch_calls: list[dict[str, Any]] = []

    def fake_launch(*, user_data_dir: Path, headed: bool) -> _FakeBrowserSession:
        launch_calls.append({"user_data_dir": user_data_dir, "headed": headed})
        return _FakeBrowserSession()

    with pytest.raises(ValueError, match="data/ 不可达"):
        browser_action(
            "navigate",
            "r",
            url="https://example.com",
            launch_fn=fake_launch,
            root=root,
        )
    assert launch_calls == []
    assert not (root / "data").exists()

    monkeypatch.setattr(paths, "DATA", root / "data")
    with pytest.raises(ValueError, match="data/ 不可达"):
        _default_crawl_fn("https://example.com", stealth=False, timeout_s=10.0)
    assert not (root / "data").exists()
