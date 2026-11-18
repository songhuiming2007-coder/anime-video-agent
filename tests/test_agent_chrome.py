"""tests/test_agent_chrome.py — `chrome` 工具（chrome-devtools-mcp --autoConnect）单测与护栏验证。"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import json
from pathlib import Path
import socket
import sys
from typing import Any

import pytest

from pipeline.agent import vision
from pipeline.agent.web_chrome import (
    _reset_session_for_testing,
    chrome_action,
    load_chrome_section,
)


def _tiny_png_bytes(width: int = 120, height: int = 80) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class _FakeMcpSession:
    """模拟 `chrome-devtools-mcp` 会话，记录每次 MCP 调用参数与页表状态。"""

    pages: dict[int, dict[str, Any]] = field(
        default_factory=lambda: {
            1: {"url": "https://user-daily.example.com/inbox", "title": "User Inbox", "selected": True},
        }
    )
    next_page_id: int = 2
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    alive: bool = True
    closed: bool = False
    eval_payload: dict[str, Any] = field(
        default_factory=lambda: {
            "url": "https://example.org/article",
            "title": "Example Article",
            "text": "Episode 12 staff: Director Jane Doe.\nLine two.",
            "html": (
                "<html><body><h1>Example Article</h1>"
                "<p>Episode 12 staff: Director Jane Doe.</p>"
                '<a href="https://example.org/staff">Staff List</a>'
                '<img src="https://example.org/poster.jpg" alt="poster"/>'
                "</body></html>"
            ),
        }
    )
    network_detail_text: str = ""
    redirect_after_open_url: str | None = None
    screenshot_inline: bool = False

    def is_alive(self) -> bool:
        return self.alive and not self.closed

    def close(self) -> None:
        self.closed = True
        self.alive = False

    def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_s: float = 10.0) -> dict[str, Any]:
        del timeout_s
        self.calls.append((name, dict(arguments)))

        if name == "list_pages":
            lines = [
                f"{pid}: {info['url']}" + (" [selected]" if info.get("selected") else "")
                for pid, info in sorted(self.pages.items())
            ]
            return {"content": [{"type": "text", "text": "## Pages\n" + "\n".join(lines)}]}

        if name == "new_page":
            pid = self.next_page_id
            self.next_page_id += 1
            final_url = self.redirect_after_open_url or str(arguments.get("url") or "")
            self.pages[pid] = {"url": final_url, "title": "Opened Page", "selected": False}
            return {"content": [{"type": "text", "text": f"## Pages\n{pid}: {final_url}"}]}

        if name == "select_page":
            pid = int(arguments["pageId"])
            if pid not in self.pages:
                raise ValueError(f"unknown page {pid}")
            return {"content": [{"type": "text", "text": f"selected {pid}"}]}

        if name == "navigate_page":
            pid = int(arguments["pageId"])
            if arguments.get("type") == "url" and arguments.get("url"):
                self.pages[pid]["url"] = str(arguments["url"])
            return {"content": [{"type": "text", "text": f"navigated {self.pages[pid]['url']}"}]}

        if name == "close_page":
            pid = int(arguments["pageId"])
            self.pages.pop(pid, None)
            return {"content": [{"type": "text", "text": f"closed {pid}"}]}

        if name == "take_snapshot":
            return {"content": [{"type": "text", "text": 'uid=1_0 button "Expand comments"\nuid=1_1 textbox "Query"'}]}

        if name == "click":
            return {"content": [{"type": "text", "text": f"clicked {arguments['uid']}"}]}

        if name == "fill":
            return {"content": [{"type": "text", "text": f"filled {arguments['uid']}"}]}

        if name == "wait_for":
            return {"content": [{"type": "text", "text": f"matched {arguments['text']}"}]}

        if name == "list_network_requests":
            return {"content": [{"type": "text", "text": "reqid=42 GET https://api.example.org/comments 200 xhr"}]}

        if name == "get_network_request":
            text = self.network_detail_text or (
                '{"url":"https://api.example.org/comments","requestHeaders":'
                '{"Cookie":"session=SUPER_SECRET","Authorization":"Bearer tok_123","Accept":"application/json"},'
                '"responseHeaders":{"Set-Cookie":"sid=secret2; HttpOnly","Content-Type":"application/json"},'
                '"body":{"staff":"Director Jane Doe"}}'
            )
            return {"content": [{"type": "text", "text": text}]}

        if name == "evaluate_script":
            fn = str(arguments.get("function") or "")
            if "outerHTML" in fn:
                payload = dict(self.eval_payload)
                pid = int(arguments["pageId"])
                if not payload.get("url") and pid in self.pages:
                    payload["url"] = self.pages[pid]["url"]
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Script ran on page and returned:\n```json\n"
                            + json.dumps(payload, ensure_ascii=False)
                            + "\n```",
                        }
                    ]
                }
            if "window.location" in fn:
                pid = int(arguments["pageId"])
                info = self.pages.get(pid, {"url": "", "title": ""})
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "```json\n"
                            + json.dumps({"url": info["url"], "title": info["title"]}, ensure_ascii=False)
                            + "\n```",
                        }
                    ]
                }
            return {"content": [{"type": "text", "text": "```json\n{\"ok\": true}\n```"}]}

        if name == "take_screenshot":
            import tempfile

            png = _tiny_png_bytes()
            if self.screenshot_inline or not arguments.get("filePath"):
                return {
                    "content": [
                        {"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"}
                    ]
                }
            fpath = Path(str(arguments["filePath"])).resolve()
            tmp_root = Path(tempfile.gettempdir()).resolve()
            if fpath != tmp_root and tmp_root not in fpath.parents:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Access denied: {fpath} is not within {tmp_root}"}],
                }
            fpath.write_bytes(png)
            return {"content": [{"type": "text", "text": f"Saved screenshot to {fpath}"}]}

        raise AssertionError(f"unexpected MCP call: {name}")


def _write_minimal_repo(tmp_path: Path, *, with_local_no_chrome: bool = False) -> Path:
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    web_json = {
        "search": {"providers": [{"name": "exa", "endpoint": "https://api.exa.ai/search", "api_key_env": "EXA_API_KEY"}]},
        "fetch": {"timeout_s": 10, "max_bytes": 100000, "max_chars": 20000},
        "crawl": {"timeout_s": 45, "max_chars": 20000},
        "browser": {"profile_dir": "data/browser-profile-firefox", "headed": True, "timeout_s": 60, "cookies_for": []},
        "chrome": {
            "command": "npx",
            "args": ["-y", "chrome-devtools-mcp@latest", "--autoConnect"],
            "timeout_s": 45,
            "max_chars": 20000,
            "sensitive_hosts": ["accounts.google.com", "studio.youtube.com", "pay.bilibili.com"],
        },
        "trusted_fake_ip_ranges": [],
    }
    (cfg_dir / "web.json").write_text(json.dumps(web_json), encoding="utf-8")
    if with_local_no_chrome:
        local = {k: v for k, v in web_json.items() if k != "chrome"}
        (cfg_dir / "web.local.json").write_text(json.dumps(local), encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_chrome_session(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    _reset_session_for_testing()
    yield
    _reset_session_for_testing()


def test_load_chrome_section_falls_back_when_local_omits_chrome(tmp_path: Path):
    root = _write_minimal_repo(tmp_path, with_local_no_chrome=True)
    cfg = load_chrome_section(root)
    assert cfg.command == "npx"
    assert cfg.args == ("-y", "chrome-devtools-mcp@latest", "--autoConnect")
    assert cfg.timeout_s == 45.0
    assert cfg.max_chars == 20000
    assert "accounts.google.com" in cfg.sensitive_hosts


def test_mcp_stdio_client_real_subprocess_roundtrip_and_connect_hint(tmp_path: Path, monkeypatch):
    root = _write_minimal_repo(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "sk-secret-env-should-not-leak")
    server_code = r"""
import json, os, sys
seen_secret = os.environ.get("EXA_API_KEY")
for line in sys.stdin:
    msg = json.loads(line)
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}}}) + "\n")
        sys.stdout.flush()
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "list_pages":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"## Pages\n1: https://example.com/?secret_seen={seen_secret}"}]}}) + "\n")
        elif name == "new_page":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": "Could not connect to Chrome via --autoConnect"}]}}) + "\n")
        sys.stdout.flush()
"""
    cfg_dir = root / "config" / "agent"
    data = json.loads((cfg_dir / "web.json").read_text(encoding="utf-8"))
    data["chrome"]["command"] = sys.executable
    data["chrome"]["args"] = ["-c", server_code]
    (cfg_dir / "web.json").write_text(json.dumps(data), encoding="utf-8")

    listed = chrome_action("list_pages", root=root)
    assert listed["pages"][0]["page_id"] == 1
    assert "secret_seen=None" in listed["pages"][0]["url"], "MCP 子进程须经 browser_env() 剥离密钥环境变量 (N50)"

    with pytest.raises(ValueError) as exc_info:
        chrome_action("open", url="https://example.org/", root=root)
    assert "chrome://inspect/#remote-debugging" in str(exc_info.value)


def test_background_focus_and_tab_ownership_isolation(tmp_path: Path):
    root = _write_minimal_repo(tmp_path)
    fake = _FakeMcpSession()

    # TOOL_SCHEMAS 不得暴露 background / bringToFront
    from pipeline.agent.tools import TOOL_SCHEMAS

    props = TOOL_SCHEMAS["chrome"]["parameters"]["properties"]
    assert "background" not in props
    assert "bringToFront" not in props

    # open 硬编码 background=True，且登记归属
    opened = chrome_action("open", url="https://example.org/news", root=root, launch_fn=lambda _cfg: fake)
    pid = opened["page_id"]
    assert pid == 2
    assert opened["owned"] is True
    assert opened["background"] is True
    new_page_calls = [args for name, args in fake.calls if name == "new_page"]
    assert len(new_page_calls) == 1
    assert new_page_calls[0]["background"] is True

    # 自有页操作 select_page 绝不带 bringToFront=True
    chrome_action("snapshot", page_id=pid, root=root, launch_fn=lambda _cfg: fake)
    select_calls = [args for name, args in fake.calls if name == "select_page"]
    assert select_calls and all(c.get("bringToFront") is False for c in select_calls)

    # 非自有用户既有页 (page_id=1)：未传 allow_existing=True 拒绝访问
    with pytest.raises(PermissionError, match="allow_existing=true"):
        chrome_action("extract_text", page_id=1, root=root, launch_fn=lambda _cfg: fake)

    # 传了 allow_existing=True 才允许读取
    res_user = chrome_action("extract_text", page_id=1, allow_existing=True, root=root, launch_fn=lambda _cfg: fake)
    assert "Example Article" in res_user["title"]

    # close 严禁关闭非自有页（即使传了 allow_existing=True）
    with pytest.raises(PermissionError, match="拒绝关闭非 AVA 创建的标签页"):
        chrome_action("close", page_id=1, allow_existing=True, root=root, launch_fn=lambda _cfg: fake)
    assert 1 in fake.pages

    # atexit / _reset_session_for_testing 仅关闭 AVA 自建 Tab，不动用户 Tab 1
    _reset_session_for_testing()
    assert pid not in fake.pages
    assert 1 in fake.pages
    assert fake.closed is True


def test_ssrf_egress_and_redirect_cleanup(tmp_path: Path):
    root = _write_minimal_repo(tmp_path)
    fake = _FakeMcpSession()

    for bad_url in (
        "http://127.0.0.1:8080/admin",
        "file:///etc/passwd",
        "chrome://settings",
        "chrome-extension://abcdef/popup.html",
    ):
        with pytest.raises(PermissionError):
            chrome_action("open", url=bad_url, root=root, launch_fn=lambda _cfg: fake)
    assert fake.calls == [], "SSRF URL 必须在调用 MCP new_page 之前拦截"

    # 重定向落到内网地址时须立即自动关闭新建页并抛 PermissionError
    fake.redirect_after_open_url = "http://127.0.0.1/internal"
    with pytest.raises(PermissionError):
        chrome_action("open", url="https://example.org/redirect", root=root, launch_fn=lambda _cfg: fake)
    assert 2 not in fake.pages


def test_get_network_strips_cookies_auth_and_scrubs_secrets(tmp_path: Path, monkeypatch):
    root = _write_minimal_repo(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "exa_super_secret_token_xyz")
    fake = _FakeMcpSession()
    fake.network_detail_text = (
        "## Request headers\n"
        "Cookie: session=MY_REAL_COOKIE; foo=bar\n"
        "Authorization: Bearer MY_JWT_SECRET\n"
        "X-CSRF-Token: csrf_secret_999\n"
        "## Response headers\n"
        "Set-Cookie: sid=NEW_COOKIE; HttpOnly\n"
        "## Body\n"
        '{"staff":"Director Jane Doe","leak":"exa_super_secret_token_xyz"}'
    )
    opened = chrome_action("open", url="https://example.org/", root=root, launch_fn=lambda _cfg: fake)
    out = chrome_action("get_network", page_id=opened["page_id"], reqid=42, root=root, launch_fn=lambda _cfg: fake)
    text = out["text"]
    assert "MY_REAL_COOKIE" not in text
    assert "MY_JWT_SECRET" not in text
    assert "csrf_secret_999" not in text
    assert "NEW_COOKIE" not in text
    assert "exa_super_secret_token_xyz" not in text
    assert "[已剥离敏感头]" in text
    assert "Director Jane Doe" in text


def test_tool_chrome_extract_text_keeps_fetch_log_record(tmp_path: Path, monkeypatch):
    root = _write_minimal_repo(tmp_path)
    ep = root / "data" / "episodes" / "01-test"
    ep.mkdir(parents=True)
    fake = _FakeMcpSession()

    import pipeline.agent.web_chrome as wc
    from pipeline.agent.tools import ToolContext, _tool_chrome

    opened = chrome_action("open", url="https://example.org/article", root=root, launch_fn=lambda _cfg: fake)
    monkeypatch.setattr(wc, "_default_launch_fn", lambda _cfg: fake)

    res = _tool_chrome(
        {"action": "extract_text", "page_id": opened["page_id"]},
        ToolContext(episode_dir=ep, scope="creative", root=root, session_id="s-chrome-1"),
    )
    assert "full_text" not in res
    assert isinstance(res["fetch_record"], str) and res["fetch_record"]
    log_path = root / "data" / "library" / "fetch-log" / "index.jsonl"
    assert log_path.is_file()
    log_lines = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(
        rec.get("id") == res["fetch_record"]
        and rec.get("kind") == "browser"
        and rec.get("url") == "https://example.org/article"
        for rec in log_lines
    )


def test_screenshot_cache_inline_vs_save_evidence(tmp_path: Path, monkeypatch):
    root = _write_minimal_repo(tmp_path)
    (root / "data" / "library" / "incoming").mkdir(parents=True)
    ep = root / "data" / "episodes" / "01-test"
    ep.mkdir(parents=True)
    fake = _FakeMcpSession()
    fake.screenshot_inline = True

    import pipeline.agent.web_chrome as wc
    from pipeline.agent import ocr
    from pipeline.agent.tools import ToolContext, _tool_chrome

    monkeypatch.setattr(wc, "_default_launch_fn", lambda _cfg: fake)
    opened = chrome_action("open", url="https://example.org/article", root=root)

    # 1) 默认模式：只写 data/_cache/chrome-shots/，带 _image 供模型同轮直读，不进 incoming
    casual = _tool_chrome(
        {"action": "screenshot", "page_id": opened["page_id"]},
        ToolContext(episode_dir=ep, scope="creative", root=root, session_id="s-shot"),
    )
    assert vision.OUTCOME_IMAGE_KEY in casual
    shot_file = Path(casual["file"])
    assert shot_file.is_file()
    assert "_cache/chrome-shots" in str(shot_file).replace("\\", "/")
    assert list((root / "data" / "library" / "incoming").iterdir()) == []
    clean_outcome, image_ref = vision.split_outcome({"ok": True, "result": casual})
    assert vision.OUTCOME_IMAGE_KEY not in clean_outcome["result"]
    assert image_ref and Path(image_ref["abs_path"]) == shot_file

    # 2) save_evidence=True 强制要求 uid / expect_text、禁止 full_page=True，且拒绝整页比例与不匹配的 expect_text
    ctx = ToolContext(episode_dir=ep, scope="creative", root=root, session_id="s-shot")
    with pytest.raises(ValueError, match="uid"):
        _tool_chrome(
            {
                "action": "screenshot",
                "page_id": opened["page_id"],
                "save_evidence": True,
                "reason": "证片尾职员表",
                "expect_text": "Jane Doe",
            },
            ctx,
        )
    with pytest.raises(ValueError, match="full_page=true"):
        _tool_chrome(
            {
                "action": "screenshot",
                "page_id": opened["page_id"],
                "uid": "node-staff",
                "full_page": True,
                "save_evidence": True,
                "reason": "证片尾职员表",
                "expect_text": "Jane Doe",
            },
            ctx,
        )
    with pytest.raises(ValueError, match="expect_text"):
        _tool_chrome(
            {
                "action": "screenshot",
                "page_id": opened["page_id"],
                "uid": "node-staff",
                "save_evidence": True,
                "reason": "证片尾职员表",
            },
            ctx,
        )
    with pytest.raises(ValueError, match="没有保存"):
        _tool_chrome(
            {
                "action": "screenshot",
                "page_id": opened["page_id"],
                "uid": "node-staff",
                "save_evidence": True,
                "reason": "证片尾职员表",
                "expect_text": "不存在的这句话",
            },
            ctx,
        )
    assert list((root / "data" / "library" / "incoming").iterdir()) == []

    # 3) save_evidence=True + uid + OCR 命中时按 D122 进 incoming + 台账 + fetch-log；若 MCP 返回竖长整页图则拒
    from pipeline import paths

    monkeypatch.setattr(paths, "DATA", root / "data")
    monkeypatch.setattr(ocr, "recognize", lambda _png: "Episode 12 staff: Director Jane Doe.")
    saved = _tool_chrome(
        {
            "action": "screenshot",
            "page_id": opened["page_id"],
            "uid": "node-staff",
            "save_evidence": True,
            "reason": "证片尾导演署名",
            "expect_text": "Director Jane Doe",
            "name": "ed-credits",
        },
        ctx,
    )
    assert saved["ocr_match"] == "exact"
    assert Path(saved["file"]).parent == root / "data" / "library" / "incoming"
    assert Path(saved["file"]).is_file()

    # 4) save_evidence=True 返回超长竖条 PNG 时在落 incoming 前被局部构图门禁拒收
    tall_png = _tiny_png_bytes(width=1360, height=5000)
    orig_call = fake.call_tool

    def _tall_call(name: str, arguments: dict[str, Any], *, timeout_s: float = 10.0) -> dict[str, Any]:
        if name == "take_screenshot":
            return {"content": [{"type": "image", "data": base64.b64encode(tall_png).decode("ascii"), "mimeType": "image/png"}]}
        return orig_call(name, arguments, timeout_s=timeout_s)

    monkeypatch.setattr(fake, "call_tool", _tall_call)
    before_files = set((root / "data" / "library" / "incoming").iterdir())
    with pytest.raises(ValueError, match="高宽比"):
        _tool_chrome(
            {
                "action": "screenshot",
                "page_id": opened["page_id"],
                "uid": "node-staff",
                "save_evidence": True,
                "reason": "证片尾导演署名",
                "expect_text": "Director Jane Doe",
            },
            ctx,
        )
    assert set((root / "data" / "library" / "incoming").iterdir()) == before_files


def test_review_tool_call_and_danger_tags_no_default_card(tmp_path: Path):
    root = _write_minimal_repo(tmp_path)
    ep = root / "data" / "episodes" / "01-ep"
    ep.mkdir(parents=True)
    fake = _FakeMcpSession()

    from pipeline.agent.session import review_tool_call
    from pipeline.agent.status_card import chrome_danger_tags, render_approval_card

    # 常规无自有 Tab 危险：open -> 免卡 allow
    v_open = review_tool_call("chrome", {"action": "open", "url": "https://example.org/x"}, ep_dir=ep, scope="creative", root=root)
    assert v_open.action == "allow"

    opened = chrome_action("open", url="https://example.org/x", root=root, launch_fn=lambda _cfg: fake)
    pid = opened["page_id"]

    # 自有后台页上的 snapshot / extract_text / click / fill / screenshot / network / close 均免卡
    for call_args in (
        {"action": "list_pages"},
        {"action": "snapshot", "page_id": pid},
        {"action": "extract_text", "page_id": pid},
        {"action": "click", "page_id": pid, "uid": "1_0"},
        {"action": "fill", "page_id": pid, "uid": "1_1", "value": "hello"},
        {"action": "list_network", "page_id": pid},
        {"action": "get_network", "page_id": pid, "reqid": 42},
        {"action": "screenshot", "page_id": pid},
        {"action": "close", "page_id": pid},
    ):
        v = review_tool_call("chrome", call_args, ep_dir=ep, scope="creative", root=root)
        assert v.action == "allow", f"常规动作不应弹卡: {call_args}"

    # 1) evaluate 必弹卡 [JS 执行]，且不开放 session_allow (allow_key=None)
    v_eval = review_tool_call(
        "chrome",
        {"action": "evaluate", "page_id": pid, "function": "() => document.title"},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_eval.action == "ask"
    assert v_eval.allow_key is None
    assert "[JS 执行]" in v_eval.request.card_text

    # 2) 用户既有非自有页 (page_id=1) 未传 allow_existing=True 时弹卡前直接 reject
    v_unowned_no_flag = review_tool_call(
        "chrome",
        {"action": "extract_text", "page_id": 1},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_unowned_no_flag.action == "reject"

    # 3) 用户既有非自有页传 allow_existing=True 时弹卡 [用户既有标签页]，并允许按 page_id 会话内免重复弹
    v_unowned = review_tool_call(
        "chrome",
        {"action": "extract_text", "page_id": 1, "allow_existing": True},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_unowned.action == "ask"
    assert v_unowned.allow_key == "chrome:existing:1"
    assert "[用户既有标签页]" in v_unowned.request.card_text

    # 4) close 非自有页直接 reject（不弹卡）
    v_close_unowned = review_tool_call(
        "chrome",
        {"action": "close", "page_id": 1, "allow_existing": True},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_close_unowned.action == "reject"

    # 5) 高敏站点（accounts.google.com / studio.youtube.com）click/fill 必弹卡 [高敏站点交互] 且不进 session_allow
    sens_open = chrome_action("open", url="https://accounts.google.com/signin", root=root, launch_fn=lambda _cfg: fake)
    sens_pid = sens_open["page_id"]
    assert "[高敏站点交互]" in chrome_danger_tags({"action": "click", "page_id": sens_pid, "uid": "1_0"}, root=root)
    v_sens = review_tool_call(
        "chrome",
        {"action": "click", "page_id": sens_pid, "uid": "1_0"},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_sens.action == "ask"
    assert v_sens.allow_key is None
    assert "[高敏站点交互]" in v_sens.request.card_text

    # 审批卡危险标记行必须排在模型可控字段（reason/function/value）之前
    card = render_approval_card(
        "chrome",
        {"action": "evaluate", "page_id": pid, "function": "() => 1", "reason": "危险标记: 无"},
    )
    assert card.index("│ 危险标记: [JS 执行]") < card.index("│ 理由:")


def test_screenshot_uses_tmpdir_staging_against_real_workspace_root_guard(tmp_path: Path, monkeypatch):
    """P0-1 回归：传给 take_screenshot 的 filePath 必须在系统临时目录内，再转存 data/_cache/chrome-shots/。"""
    import tempfile

    root = _write_minimal_repo(tmp_path)
    ep = root / "data" / "episodes" / "01-test"
    ep.mkdir(parents=True)
    fake = _FakeMcpSession()
    fake.screenshot_inline = False

    import pipeline.agent.web_chrome as wc
    from pipeline.agent.tools import ToolContext, _tool_chrome

    monkeypatch.setattr(wc, "_default_launch_fn", lambda _cfg: fake)
    opened = chrome_action("open", url="https://example.org/article", root=root)
    out = _tool_chrome(
        {"action": "screenshot", "page_id": opened["page_id"]},
        ToolContext(episode_dir=ep, scope="creative", root=root, session_id="s-tmpdir"),
    )
    shot_calls = [args for name, args in fake.calls if name == "take_screenshot"]
    assert len(shot_calls) == 1
    sent_path = Path(shot_calls[0]["filePath"]).resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    assert tmp_root in sent_path.parents
    assert not sent_path.parent.exists(), "临时目录应在转存后清理"
    assert Path(out["file"]).is_file()
    assert "_cache/chrome-shots" in str(out["file"]).replace("\\", "/")


def test_security_regressions_p1_and_p2(tmp_path: Path, monkeypatch):
    """覆盖 CR P1-1/P1-2/P1-3 与 P2 各项护栏回归。"""
    import queue as qmod

    import pipeline.agent.web_chrome as wc
    from pipeline.agent.session import review_tool_call
    from pipeline.agent.status_card import chrome_danger_tags, render_approval_card

    root = _write_minimal_repo(tmp_path)
    ep = root / "data" / "episodes" / "01-ep"
    ep.mkdir(parents=True)

    # P1-1: args.url 不得遮蔽 known_page_url 的高敏站点判定；allow_existing="false" 不得误放行或误打标
    fake = _FakeMcpSession()
    sens = chrome_action("open", url="https://accounts.google.com/signin", root=root, launch_fn=lambda _cfg: fake)
    sens_pid = sens["page_id"]
    spoof_args = {"action": "fill", "page_id": sens_pid, "uid": "1_1", "value": "secret", "url": "https://example.org"}
    tags = chrome_danger_tags(spoof_args, root=root)
    assert "[高敏站点交互]" in tags
    v_spoof = review_tool_call("chrome", spoof_args, ep_dir=ep, scope="creative", root=root)
    assert v_spoof.action == "ask"
    assert v_spoof.allow_key is None
    assert "accounts.google.com" in v_spoof.request.card_text

    v_str_false = review_tool_call(
        "chrome",
        {"action": "extract_text", "page_id": 1, "allow_existing": "false"},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_str_false.action == "reject"
    # 自有页传 allow_existing="false" 不得被误判为 [用户既有标签页]
    assert chrome_danger_tags({"action": "snapshot", "page_id": sens_pid, "allow_existing": "false"}, root=root) == []

    # 组合标记 [JS 执行] + [用户既有标签页] 不得开放 allow_key；且既有页卡面需提示 session_allow 范围
    v_combo = review_tool_call(
        "chrome",
        {"action": "evaluate", "page_id": 1, "allow_existing": True, "function": "() => 1"},
        ep_dir=ep,
        scope="creative",
        root=root,
    )
    assert v_combo.action == "ask"
    assert v_combo.allow_key is None
    assert "[JS 执行]" in v_combo.request.card_text and "[用户既有标签页]" in v_combo.request.card_text
    card_existing = render_approval_card("chrome", {"action": "snapshot", "page_id": 1, "allow_existing": True}, root=root)
    assert "常规操作均放行" in card_existing

    # P1-2 & P2-1: click 触发跳转到高敏页时必须刷新 _PAGE_URLS；跳转到内网时自动关闭自有页并拦停
    _reset_session_for_testing()
    fake2 = _FakeMcpSession()
    opened = chrome_action("open", url="https://example.org/entry", root=root, launch_fn=lambda _cfg: fake2)
    pid = opened["page_id"]

    orig_call = fake2.call_tool

    def _redirecting_click(name: str, arguments: dict[str, Any], *, timeout_s: float = 10.0) -> dict[str, Any]:
        out = orig_call(name, arguments, timeout_s=timeout_s)
        if name == "click":
            fake2.pages[pid]["url"] = "https://accounts.google.com/v3/signin"
        return out

    monkeypatch.setattr(fake2, "call_tool", _redirecting_click)
    chrome_action("click", page_id=pid, uid="1_0", root=root)
    assert wc.known_page_url(pid) == "https://accounts.google.com/v3/signin"
    v_after_click = review_tool_call(
        "chrome", {"action": "fill", "page_id": pid, "uid": "1_1", "value": "pw"}, ep_dir=ep, scope="creative", root=root
    )
    assert v_after_click.action == "ask"
    assert "[高敏站点交互]" in v_after_click.request.card_text

    # 非自有用户页若停在 chrome://settings 或 127.0.0.1，snapshot/list_network/screenshot 前置拦截（不动用户页）
    fake2.pages[1]["url"] = "chrome://settings"
    with pytest.raises(PermissionError):
        chrome_action("snapshot", page_id=1, allow_existing=True, root=root)
    with pytest.raises(PermissionError):
        chrome_action("list_network", page_id=1, allow_existing=True, root=root)
    assert 1 in fake2.pages

    # P1-3: 并发用户手动开页（id 更大）时 open 必须按 URL 精准认领 AVA 页，绝不误纳用户页
    _reset_session_for_testing()
    fake_race = _FakeMcpSession()
    orig_race_call = fake_race.call_tool

    def _race_new_page(name: str, arguments: dict[str, Any], *, timeout_s: float = 10.0) -> dict[str, Any]:
        out = orig_race_call(name, arguments, timeout_s=timeout_s)
        if name == "new_page":
            # 模拟用户几乎同时手动新开 id=3 的 Gmail 标签页，且 id > AVA 的 id=2
            uid_user = fake_race.next_page_id
            fake_race.next_page_id += 1
            fake_race.pages[uid_user] = {"url": "https://mail.google.com/mail/u/0/#inbox", "title": "Gmail", "selected": True}
        return out

    monkeypatch.setattr(fake_race, "call_tool", _race_new_page)
    raced = chrome_action("open", url="https://example.org/target", root=root, launch_fn=lambda _cfg: fake_race)
    assert raced["page_id"] == 2
    assert wc.is_owned_page(2) is True
    assert wc.is_owned_page(3) is False
    with pytest.raises(PermissionError, match="拒绝关闭非 AVA 创建"):
        chrome_action("close", page_id=3, allow_existing=True, root=root)
    assert 3 in fake_race.pages

    # P2-2 & P2-3: _wake_all_with_eof 满队列不阻塞；server->client 请求不污染 _pending
    dummy = wc._McpStdioClient.__new__(wc._McpStdioClient)
    dummy._pending_lock = wc.threading.Lock()
    full_q: qmod.Queue[dict[str, Any]] = qmod.Queue(maxsize=1)
    full_q.put({"result": {"ok": True}})
    dummy._pending = {1: full_q}
    dummy._stderr_tail = wc.deque(maxlen=4)
    dummy._proc = type("_P", (), {"poll": staticmethod(lambda: 0)})()
    dummy._wake_all_with_eof()  # 不应挂死或抛 queue.Full

    sent_replies: list[dict[str, Any]] = []
    dummy._send_json = lambda payload: sent_replies.append(payload)  # type: ignore[method-assign]
    req_q: qmod.Queue[dict[str, Any]] = qmod.Queue(maxsize=1)
    dummy._pending = {7: req_q}
    dummy._proc = type(
        "_P2",
        (),
        {
            "poll": staticmethod(lambda: None),
            "stdout": [
                json.dumps({"jsonrpc": "2.0", "id": 7, "method": "roots/list"}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"ok": "real"}}) + "\n",
            ],
        },
    )()
    dummy._read_stdout()
    assert req_q.get_nowait()["result"] == {"ok": "real"}
    assert sent_replies and sent_replies[0]["error"]["code"] == -32601

    # P2-4: --experimentalStructuredContent 开启时，正文 fenced JSON 优先于 wrapper structuredContent
    parsed = wc._parse_evaluate_payload(
        {
            "structuredContent": {"message": "Script ran on page and returned:"},
            "content": [{"type": "text", "text": '```json\n{"url":"https://example.org","text":"real body"}\n```'}],
        }
    )
    assert parsed == {"url": "https://example.org", "text": "real body"}

    # P2-5: 参数类型不合法给出干净 ValueError，不漏裸 TypeError
    with pytest.raises(ValueError, match="timeout_ms"):
        chrome_action("wait_for", page_id=2, text="ok", timeout_ms="abc", root=root)  # type: ignore[arg-type]

    # P2-6: 新增敏感头与 URL query 敏感参数剥离
    redacted = wc._strip_sensitive_headers_text(
        "X-Access-Token: acc_secret\nX-Amz-Security-Token: aws_tok\n"
        "GET https://api.example.org/v1?page=1&csrf=csrf_123&sig=hmac_abc"
    )
    assert "acc_secret" not in redacted
    assert "aws_tok" not in redacted
    assert "csrf_123" not in redacted
    assert "hmac_abc" not in redacted
    assert "page=1" in redacted

    # P2-7: SSRF URL 在 review_tool_call 阶段直接 reject（不弹卡也不启动子进程）
    called = False

    def _never_launch(_cfg):
        nonlocal called
        called = True
        return fake_race

    assert review_tool_call("chrome", {"action": "open", "url": "http://127.0.0.1/admin"}, ep_dir=ep, scope="creative", root=root).action == "reject"
    with pytest.raises(PermissionError):
        chrome_action("open", url="http://127.0.0.1/admin", root=root, launch_fn=_never_launch)
    assert called is False

    # P2-8: close 已不在浏览器的自有页清理 _OWNED_PAGES
    def _close_missing(name: str, arguments: dict[str, Any], *, timeout_s: float = 10.0) -> dict[str, Any]:
        if name == "close_page":
            raise ValueError("No page found for given pageId")
        return orig_race_call(name, arguments, timeout_s=timeout_s)

    monkeypatch.setattr(fake_race, "call_tool", _close_missing)
    closed = chrome_action("close", page_id=2, root=root)
    assert closed["closed"] is True and closed.get("already_closed") is True
    assert wc.is_owned_page(2) is False

    # P2-9: 真实 list_pages 格式 `{id}: {title} ({url}) [selected]` 解析标题无残留 `()`
    parsed_pages = wc._parse_pages(
        {
            "content": [
                {
                    "type": "text",
                    "text": "## Pages\n1: Example Domain (2026) (https://example.com/) [selected]",
                }
            ]
        },
        update_cache=False,
    )
    assert parsed_pages == [
        {"page_id": 1, "url": "https://example.com/", "title": "Example Domain (2026)", "selected": True}
    ]

    # P3 复核：args 键校验、子域重定向竞态、Saved screenshot 句尾标点、list_network query 剥离、_tool_chrome 字符串布尔
    assert review_tool_call(
        "chrome",
        {"action": "evaluate", "page_id": 1, "allow_existing": True, "function": "() => 1", "args": "not-a-list"},
        ep_dir=ep,
        scope="creative",
        root=root,
    ).action == "reject"

    # 子域重定向 (example.org -> www.example.org) + 并发用户页，优先匹配子域或 [selected] 而不误纳用户页
    pid_sub = wc._resolve_opened_page_id(
        before_ids={1},
        open_res={"content": [{"type": "text", "text": "## Pages\n1: User (https://user.test/)"}]},
        after_pages=[
            {"page_id": 1, "url": "https://user.test/", "title": "User", "selected": False},
            {"page_id": 5, "url": "https://www.example.org/landing", "title": "Landing", "selected": False},
            {"page_id": 6, "url": "https://other.test/", "title": "Other", "selected": True},
        ],
        target_url="https://example.org/start",
    )
    assert pid_sub == 5

    # Saved screenshot to {path}. 句尾句号回退解析 + list_network query 剥离 + save_evidence="false" 走缓存不进 incoming
    import tempfile

    with tempfile.TemporaryDirectory() as td_shot:
        alt_shot = Path(td_shot) / "alt.png"
        alt_shot.write_bytes(_tiny_png_bytes())
        fake_p3 = _FakeMcpSession()
        orig_p3 = fake_p3.call_tool

        def _p3_call(name: str, arguments: dict[str, Any], *, timeout_s: float = 10.0) -> dict[str, Any]:
            if name == "take_screenshot":
                return {"content": [{"type": "text", "text": f"Saved screenshot to {alt_shot}."}]}
            if name == "list_network_requests":
                return {"content": [{"type": "text", "text": "reqid=9 GET https://api.example.org/feed?token=secret_tok&page=2 200"}]}
            return orig_p3(name, arguments, timeout_s=timeout_s)

        monkeypatch.setattr(fake_p3, "call_tool", _p3_call)
        _reset_session_for_testing()
        op3 = chrome_action("open", url="https://example.org/p3", root=root, launch_fn=lambda _cfg: fake_p3)
        net_list = chrome_action("list_network", page_id=op3["page_id"], root=root)
        assert "secret_tok" not in net_list["text"] and "page=2" in net_list["text"]

        from pipeline.agent.tools import ToolContext, _tool_chrome

        monkeypatch.setattr(wc, "_default_launch_fn", lambda _cfg: fake_p3)
        shot3 = _tool_chrome(
            {"action": "screenshot", "page_id": op3["page_id"], "save_evidence": "false"},
            ToolContext(episode_dir=ep, scope="creative", root=root, session_id="s-p3"),
        )
        assert vision.OUTCOME_IMAGE_KEY in shot3 and Path(shot3["file"]).is_file()
