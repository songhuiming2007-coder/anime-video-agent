"""Spec 4 (ADR-0021) 网络工具第一批 web_search + web_fetch 测试套件。"""

from __future__ import annotations

import email.message
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pipeline.agent import web
from pipeline.agent.web import (
    WebConfig,
    _GuardedRedirectHandler,
    fetch_web,
    load_web_config,
    search_web,
)

SO_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><title>一色彩羽_360搜索</title></head>
<body>
  <ul class="result">
    <li class="res-list">
      <div id="mohe-biu_graph" class="g-mohe">
        <style>#mohe-biu_graph .mh-long { width: 540px; }</style>
        <h3 class="g-title"><a href="https://baike.so.com/doc/8402832-32396489.html"><em>一色彩羽</em><span>百科</span></a></h3>
      </div>
    </li>
    <li class="res-list">
      <h3 class="res-title">
        <a href="https://ai.so.com/search/so863814e0bc75d452d2fc420b6c9dd2a1" data-replaceurl="https://wenda.so.com/q/1608159187210264" target="_blank"><em>一色彩羽</em>的介绍</a>
      </h3>
      <div class="res-rich res-rich-wenda_box">
        <div class="best-ans">
          <span class="res-list-summary"><em>一色彩羽是轻小说作品《我的青春恋爱物语果然有问题。</em>》及其衍生作品中女主角之一。</span>
        </div>
      </div>
    </li>
    <li class="res-list">
      <h3 class="res-title">
        <a href="https://www.so.com/link?m=eTXclcn2" data-mdurl="https://www.bilibili.com/read/cv3434398/" target="_blank">if线:<em>一色彩羽</em> - 哔哩哔哩</a>
      </h3>
      <div class="res-rich so-rich-image clearfix">
        <div class="res-comm-con">
          <span class="res-list-summary">美少女,没错她正是现任高三的学生会长——<em>一色</em>彩羽。</span>
        </div>
      </div>
    </li>
    <li class="res-list">
      <h3 class="res-title ">
        <a href="https://www.so.com/link?m=uOpIru5Z" data-mdurl="https://www.zhihu.com/topic/20598738/questions" target="_blank"><em>一色彩羽</em>- 知乎</a>
      </h3>
      <p class="res-desc"><span class="gray g-c-gray">2026年3月10日&nbsp;-&nbsp;</span><em>一色彩羽</em>是在轻小说作品《我的青春恋爱物语果然有问题》中登场的人物，扮演天然系学妹。</p>
    </li>
    <li class="res-list">
      <h3 class="res-title">
        <a href="https://baike.so.com/doc/8402832-32396489.html" target="_blank"><em>一色彩羽</em>_360百科</a>
      </h3>
      <div class="res-rich res-baike clearfix">
        <div class="res-comm-con">
          <p class="res-desc">比企谷八幡对一色彩羽的评价是完全不可爱的小町。</p>
        </div>
      </div>
    </li>
  </ul>
</body>
</html>
"""

EMPTY_SO_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<body>
  <div class="so-tips">抱歉，未找到相关结果。</div>
</body>
</html>
"""


def _make_config(
    *,
    api_key: str = "",
    api_key_param: str = "",
    max_fetch_bytes: int = 1_000_000,
    max_fetch_chars: int = 30_000,
    trusted_fake_ip_ranges: tuple[Any, ...] = (),
) -> WebConfig:
    return WebConfig(
        search_endpoint="https://www.so.com/s",
        search_query_param="q",
        api_key=api_key,
        api_key_param=api_key_param,
        search_timeout_s=20.0,
        fetch_timeout_s=30.0,
        max_fetch_bytes=max_fetch_bytes,
        max_fetch_chars=max_fetch_chars,
        trusted_fake_ip_ranges=trusted_fake_ip_ranges,
    )


def _pin_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """§7.1 网络隔离铁律（🟡-10）：凡过 _guard_url 的直调腿一律钉死公网 IP，零真实 DNS。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ],
    )


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = "text/html; charset=utf-8",
        final_url: str = "https://example.org/page",
        status: int = 200,
    ) -> None:
        self._body = body
        self._pos = 0
        self.bytes_read = 0
        self.headers = {"Content-Type": content_type}
        self._final_url = final_url
        self.status = status

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            chunk = self._body[self._pos :]
        else:
            chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        self.bytes_read += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self._final_url


def test_search_parses_results_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """T1 (PR1): 搜索解析 fixture HTML（360 搜索 DOM），data-mdurl/data-replaceurl/href 解码与 limit 截断。"""
    _pin_public_dns(monkeypatch)
    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        calls.append((req, timeout))
        return _FakeResponse(SO_FIXTURE_HTML.encode("utf-8"))

    out = search_web("一色彩羽", limit=3, config=_make_config(), opener=fake_opener)
    assert len(calls) == 1
    assert out["query"] == "一色彩羽"
    assert out["provider"] == "https://www.so.com/s"
    assert out["truncated"] is True
    assert len(out["results"]) == 3
    assert out["results"][0] == {
        "title": "一色彩羽的介绍",
        "url": "https://wenda.so.com/q/1608159187210264",
        "snippet": "一色彩羽是轻小说作品《我的青春恋爱物语果然有问题。》及其衍生作品中女主角之一。",
    }
    assert out["results"][1] == {
        "title": "if线:一色彩羽 - 哔哩哔哩",
        "url": "https://www.bilibili.com/read/cv3434398/",
        "snippet": "美少女,没错她正是现任高三的学生会长——一色彩羽。",
    }
    assert out["results"][2] == {
        "title": "一色彩羽- 知乎",
        "url": "https://www.zhihu.com/topic/20598738/questions",
        "snippet": "2026年3月10日 - 一色彩羽是在轻小说作品《我的青春恋爱物语果然有问题》中登场的人物，扮演天然系学妹。",
    }

    # limit=5 时返回全部 4 条（含第 4 条无 data-mdurl 时回落 href）且 truncated=False
    out_all = search_web("一色彩羽", limit=5, config=_make_config(), opener=fake_opener)
    assert out_all["truncated"] is False
    assert len(out_all["results"]) == 4
    assert out_all["results"][3] == {
        "title": "一色彩羽_360百科",
        "url": "https://baike.so.com/doc/8402832-32396489.html",
        "snippet": "比企谷八幡对一色彩羽的评价是完全不可爱的小町。",
    }


def test_fetch_strips_html_and_caps_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """T2 (PR1): HTML 剥离、max_chars 字符截断与 max_bytes 流式封顶。"""
    _pin_public_dns(monkeypatch)
    html_doc = """
    <html>
      <head>
        <style>body { color: red; } .secret-css { content: "CSS_LEAK"; }</style>
        <script>const token = "JS_SECRET_PAYLOAD"; alert(1);</script>
      </head>
      <body>
        <noscript>NOSCRIPT_FALLBACK_TEXT</noscript>
        <article data-secret="ATTR_SECRET" onclick="evil()">
          <h1>标题净文</h1>
          <p>第一段正文。</p>
          <a href="https://evil.example/payload">链接可见文字</a>
        </article>
      </body>
    </html>
    """
    resp1 = _FakeResponse(html_doc.encode("utf-8"), final_url="https://example.org/a")
    out1 = fetch_web(
        "https://example.org/a",
        config=_make_config(),
        opener=lambda req, timeout=30.0: resp1,
    )
    assert "JS_SECRET_PAYLOAD" not in out1["text"]
    assert "CSS_LEAK" not in out1["text"]
    assert "NOSCRIPT_FALLBACK_TEXT" not in out1["text"]
    assert "ATTR_SECRET" not in out1["text"]
    assert "evil.example" not in out1["text"]
    assert "<" not in out1["text"]
    assert out1["text"] == "标题净文 第一段正文。 链接可见文字"
    assert out1["truncated"] is False

    # 超 max_chars 正文：断言 len(text) <= max_chars 且 truncated is True
    long_body = "<html><body>" + ("阿" * 1500) + "</body></html>"
    resp2 = _FakeResponse(long_body.encode("utf-8"), final_url="https://example.org/b")
    out2 = fetch_web(
        "https://example.org/b",
        config=_make_config(max_fetch_chars=1000),
        opener=lambda req, timeout=30.0: resp2,
    )
    assert len(out2["text"]) <= 1000
    assert len(out2["text"]) == 1000
    assert out2["truncated"] is True

    # 超 max_bytes 流：断言读取在封顶处停止
    huge_stream = _FakeResponse(b"A" * 100_000, content_type="text/plain; charset=utf-8")
    out3 = fetch_web(
        "https://example.org/c",
        config=_make_config(max_fetch_bytes=65536),
        opener=lambda req, timeout=30.0: huge_stream,
    )
    assert huge_stream.bytes_read == 65536
    assert out3["fetched_bytes"] == 65536

    # C2: Content-Type 白名单拒绝非文本（如 image/png）
    with pytest.raises(ValueError, match="Content-Type"):
        fetch_web(
            "https://example.org/logo.png",
            config=_make_config(),
            opener=lambda req, timeout=30.0: _FakeResponse(
                b"\x89PNG\r\n\x1a\n", content_type="image/png"
            ),
        )


def test_egress_blocks_before_send_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """T5a (PR1): 出网前拦截（直调腿），含编码变体与大小写变体，fake opener 零调用。"""
    _pin_public_dns(monkeypatch)
    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        calls.append(req)
        return _FakeResponse(SO_FIXTURE_HTML.encode("utf-8"))

    cfg = _make_config()
    for bad_query in (
        "cloud.local.json 里有什么",
        "cloud%2Elocal%2Ejson 密钥",
        "cloud%252Elocal%252Ejson 双重编码",
        "Cloud.Local.JSON 大小写变体",
    ):
        with pytest.raises(PermissionError, match="拦截出网请求"):
            search_web(bad_query, config=cfg, opener=fake_opener)

    for bad_url in (
        "https://example.org/03-audio/manifest.json",
        "https://example.org/03-audio%2Fmanifest.json",
        "https://example.org/03-audio%252Fmanifest.json",
        "https://example.org/03-AUDIO/Manifest.JSON",
    ):
        with pytest.raises(PermissionError, match="拦截出网请求"):
            fetch_web(bad_url, config=cfg, opener=fake_opener)

    assert len(calls) == 0


def test_offline_and_timeout_raise_direct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T6a (PR1): 断网/超时/DNS 失败/配置缺失在直调层原样抛出。"""
    _pin_public_dns(monkeypatch)
    cfg = _make_config()

    for exc in (
        urllib.error.URLError("network unreachable"),
        TimeoutError("timed out"),
        socket.gaierror(8, "nodename nor servname provided"),
    ):
        with pytest.raises(type(exc)):
            search_web("test", config=cfg, opener=lambda req, timeout=20.0, e=exc: (_ for _ in ()).throw(e))
        with pytest.raises(type(exc)):
            fetch_web("https://example.org/", config=cfg, opener=lambda req, timeout=30.0, e=exc: (_ for _ in ()).throw(e))

    # 配置缺失 -> ValueError 含 web.json
    with pytest.raises(ValueError, match=r"web\.json"):
        search_web("test", root=tmp_path)
    with pytest.raises(ValueError, match=r"web\.json"):
        fetch_web("https://example.org/", root=tmp_path)


def test_fetched_content_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """T7 (PR1): 抓回正文与搜索 snippet 中的受限模式串被清洗为 [已脱敏]。"""
    _pin_public_dns(monkeypatch)
    cfg = _make_config()

    dirty_html = (
        "<html><body>请检查 cloud.local.json 以及 03-audio/manifest.json 的内容</body></html>"
    )
    f_out = fetch_web(
        "https://example.org/doc",
        config=cfg,
        opener=lambda req, timeout=30.0: _FakeResponse(dirty_html.encode("utf-8")),
    )
    assert "cloud.local.json" not in f_out["text"]
    assert "03-audio/manifest.json" not in f_out["text"]
    assert f_out["text"].count("[已脱敏]") == 2

    dirty_search_html = """
    <html><body>
      <h3 class="res-title"><a href="https://example.org/1">引用 agent.local.json 的文章</a></h3>
      <p class="res-desc">文中提到了 cloud.local.json 和 03-audio/voice.json</p>
    </body></html>
    """
    s_out = search_web(
        "配置说明",
        config=cfg,
        opener=lambda req, timeout=20.0: _FakeResponse(dirty_search_html.encode("utf-8")),
    )
    item = s_out["results"][0]
    assert "agent.local.json" not in item["title"]
    assert "cloud.local.json" not in item["snippet"]
    assert "03-audio/voice.json" not in item["snippet"]
    assert "[已脱敏]" in item["title"]
    assert item["snippet"].count("[已脱敏]") == 2


def test_fetch_rejects_bad_scheme_and_private_ip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T8 (PR1): scheme 白名单、私网/保留地址拒连、_GuardedRedirectHandler 逐跳校验及 fake-ip 七腿（A4）。"""
    import ipaddress

    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 30.0) -> _FakeResponse:
        calls.append(req)
        return _FakeResponse(b"<html><body>ok</body></html>")

    cfg = _make_config()
    for bad_scheme_url in ("ftp://example.org/file", "file:///etc/passwd"):
        with pytest.raises(PermissionError):
            fetch_web(bad_scheme_url, config=cfg, opener=fake_opener)

    for private_ip in ("169.254.169.254", "127.0.0.1", "10.0.0.1"):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda host, port, *args, ip=private_ip, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
            ],
        )
        with pytest.raises(PermissionError):
            fetch_web(f"http://{private_ip}/secret", config=cfg, opener=fake_opener)

    assert len(calls) == 0

    # 重定向腿（v0.3 机理对齐）：最小四属性桩 fake req 单测 _GuardedRedirectHandler
    class _FakeRedirectReq:
        full_url = "https://example.org/start"
        headers: dict[str, str] = {}
        origin_req_host = "example.org"

        def get_method(self) -> str:
            return "GET"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ],
    )
    handler = _GuardedRedirectHandler()
    with pytest.raises(PermissionError):
        handler.redirect_request(
            _FakeRedirectReq(),
            None,
            302,
            "Found",
            email.message.Message(),
            "http://169.254.169.254/latest/meta-data/",
        )

    # C3: search_web 路径守卫——搜索端点解析到 10.0.0.1 时必抛 PermissionError 且 opener 零调用
    search_guard_calls: list[Any] = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80))
        ],
    )
    def _search_guard_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        search_guard_calls.append(req)
        return _FakeResponse(SO_FIXTURE_HTML.encode("utf-8"))

    with pytest.raises(PermissionError):
        search_web("芙莉莲", config=cfg, opener=_search_guard_opener)
    assert len(search_guard_calls) == 0

    # C1: 生产 _default_opener(...) 接线——OpenerDirector.handlers 含 _GuardedRedirectHandler 且 _trusted_ranges 吻合
    fake_net = ipaddress.ip_network("198.18.0.0/15")
    web._OPENER_CACHE.clear()
    prod_open_fn = web._default_opener((fake_net,))
    prod_director = getattr(prod_open_fn, "__self__", None)
    assert isinstance(prod_director, urllib.request.OpenerDirector)
    guarded_handlers = [
        h for h in prod_director.handlers if isinstance(h, _GuardedRedirectHandler)
    ]
    assert len(guarded_handlers) == 1
    assert guarded_handlers[0]._trusted_ranges == (fake_net,)

    # ---- A4 fake-ip 七腿（①–⑦） ----
    cfg_with_fake_range = _make_config(trusted_fake_ip_ranges=(fake_net,))

    # ① 清单空 + 域名解析到 198.18.1.1 → PermissionError
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.1", 80))
        ],
    )
    with pytest.raises(PermissionError):
        fetch_web("https://example.org/page", config=cfg, opener=fake_opener)
    assert len(calls) == 0

    # ② 清单含 198.18.0.0/15 + 域名解析到 198.18.1.1 → 放行，fake opener 恰调用 1 次
    out_leg2 = fetch_web(
        "https://example.org/page", config=cfg_with_fake_range, opener=fake_opener
    )
    assert out_leg2["status"] == 200
    assert len(calls) == 1

    # ③ 同清单 + 字面量 http://198.18.1.1/ 及非标准 IPv4 字面量（十进制/缩写/十六进制/八进制，D） → 仍 PermissionError
    for literal_url in (
        "http://198.18.1.1/secret",
        "http://3323068673/",
        "http://198.18.1/",
        "http://0xc6120101/",
        "http://0306.022.1.1/",
    ):
        with pytest.raises(PermissionError):
            fetch_web(literal_url, config=cfg_with_fake_range, opener=fake_opener)
    assert len(calls) == 1

    # ④ 同清单 + 域名解析到 127.0.0.1 → 仍 PermissionError（其它私网/回环仍拒）
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ],
    )
    with pytest.raises(PermissionError):
        fetch_web("https://example.org/local", config=cfg_with_fake_range, opener=fake_opener)
    assert len(calls) == 1

    # ⑤ 同清单(198.18.0.0/15) + 域名解析到 198.19.255.1（段内另一端）放行；
    #    解析到段外私网 198.51.100.1（TEST-NET-2，因 198.20.0.1 属 ARIN 公网 is_global=True）
    #    及将清单收窄为 198.18.0.0/16 时解析到 198.19.0.1（段外）拒——钉死按段动态判定
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.19.255.1", 80))
        ],
    )
    out_leg5 = fetch_web(
        "https://example.org/upper-bound", config=cfg_with_fake_range, opener=fake_opener
    )
    assert out_leg5["status"] == 200
    assert len(calls) == 2

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.1", 80))
        ],
    )
    with pytest.raises(PermissionError):
        fetch_web("https://example.org/out-of-range-1", config=cfg_with_fake_range, opener=fake_opener)
    assert len(calls) == 2

    cfg_narrow_16 = _make_config(
        trusted_fake_ip_ranges=(ipaddress.ip_network("198.18.0.0/16"),)
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.19.0.1", 80))
        ],
    )
    with pytest.raises(PermissionError):
        fetch_web("https://example.org/out-of-range-2", config=cfg_narrow_16, opener=fake_opener)
    assert len(calls) == 2

    # ⑥ 重定向 handler 带清单：跳到解析为 198.18.x 的域名放行，跳到解析为 169.254.169.254 的域名拒
    handler_with_range = _GuardedRedirectHandler(trusted_ranges=(fake_net,))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.42.9", 80))
        ],
    )
    redirected_req = handler_with_range.redirect_request(
        _FakeRedirectReq(),
        None,
        302,
        "Found",
        email.message.Message(),
        "https://cdn.example.org/target",
    )
    assert redirected_req is not None
    assert redirected_req.full_url == "https://cdn.example.org/target"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ],
    )
    with pytest.raises(PermissionError):
        handler_with_range.redirect_request(
            _FakeRedirectReq(),
            None,
            302,
            "Found",
            email.message.Message(),
            "http://meta.example.org/latest/meta-data/",
        )

    # ⑦ load_web_config：缺键→()；合法清单→解析为 network；"198.18.0.0/99" 或非 list → None
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    base_json = {
        "search": {
            "endpoint": "https://html.duckduckgo.com/html/",
            "query_param": "q",
            "api_key_env": "",
            "api_key_param": "",
            "timeout_s": 20,
        },
        "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
    }
    # 缺键 -> ()
    (cfg_dir / "web.json").write_text(json.dumps(base_json), encoding="utf-8")
    loaded_default = load_web_config(tmp_path)
    assert loaded_default is not None
    assert loaded_default.trusted_fake_ip_ranges == ()

    # 合法清单 -> 解析为 network 元组
    (cfg_dir / "web.json").write_text(
        json.dumps({**base_json, "trusted_fake_ip_ranges": ["198.18.0.0/15", "2001:2::/48"]}),
        encoding="utf-8",
    )
    loaded_valid = load_web_config(tmp_path)
    assert loaded_valid is not None
    assert loaded_valid.trusted_fake_ip_ranges == (
        ipaddress.IPv4Network("198.18.0.0/15"),
        ipaddress.IPv6Network("2001:2::/48"),
    )

    # 非法 CIDR "198.18.0.0/99" -> None
    (cfg_dir / "web.json").write_text(
        json.dumps({**base_json, "trusted_fake_ip_ranges": ["198.18.0.0/99"]}),
        encoding="utf-8",
    )
    assert load_web_config(tmp_path) is None

    # 非 list -> None
    for bad_val in ("198.18.0.0/15", {"cidr": "198.18.0.0/15"}, 123, [123], [""]):
        (cfg_dir / "web.json").write_text(
            json.dumps({**base_json, "trusted_fake_ip_ranges": bad_val}),
            encoding="utf-8",
        )
        assert load_web_config(tmp_path) is None


def test_http_403_fails_honestly_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """T9 (PR1): HTTP 403 / 500 诚实失败含升级 crawl 提示，且调用计数恰为 1（零自动重试）。"""
    _pin_public_dns(monkeypatch)
    cfg = _make_config()

    for code, reason in ((403, "Forbidden"), (500, "Internal Server Error")):
        count = 0

        def failing_opener(req: Any, timeout: float = 30.0) -> _FakeResponse:
            nonlocal count
            count += 1
            raise urllib.error.HTTPError(
                req.full_url, code, reason, email.message.Message(), None
            )

        with pytest.raises(ValueError, match="升级 crawl"):
            fetch_web("https://example.org/protected", config=cfg, opener=failing_opener)
        assert count == 1


def test_web_config_local_override_and_credential_hygiene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T10 (PR1): web.local.json 整文件覆盖与凭据四不泄漏。"""
    _pin_public_dns(monkeypatch)
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True)

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
                "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
            }
        ),
        encoding="utf-8",
    )
    (cfg_dir / "web.local.json").write_text(
        json.dumps(
            {
                "search": {
                    "endpoint": "https://search.example.org/api",
                    "query_param": "query",
                    "api_key_env": "AVA_TEST_WEB_SECRET_KEY",
                    "api_key_param": "api_key",
                    "timeout_s": 15,
                },
                "fetch": {"timeout_s": 25, "max_bytes": 131072, "max_chars": 5000},
            }
        ),
        encoding="utf-8",
    )

    # 指名了环境变量但未设置 -> 返回 None（不静默退无凭据模式）
    monkeypatch.delenv("AVA_TEST_WEB_SECRET_KEY", raising=False)
    assert load_web_config(tmp_path) is None

    # 注入假密钥
    fake_secret = "test-key-0000"
    monkeypatch.setenv("AVA_TEST_WEB_SECRET_KEY", fake_secret)
    loaded = load_web_config(tmp_path)
    assert loaded is not None
    assert loaded.search_endpoint == "https://search.example.org/api"
    assert loaded.api_key == fake_secret

    captured_urls: list[str] = []

    def search_opener(req: Any, timeout: float = 15.0) -> _FakeResponse:
        captured_urls.append(req.full_url)
        return _FakeResponse(SO_FIXTURE_HTML.encode("utf-8"))

    s_res = search_web("frieren", root=tmp_path, opener=search_opener)
    assert len(captured_urls) == 1
    assert f"api_key={fake_secret}" in captured_urls[0]
    assert fake_secret not in json.dumps(s_res, ensure_ascii=False)

    f_res = fetch_web(
        f"https://example.org/data?api_key={fake_secret}",
        root=tmp_path,
        opener=lambda req, timeout=25.0: _FakeResponse(
            f"<html><body>ok {fake_secret}</body></html>".encode("utf-8"),
            final_url=f"https://example.org/final?api_key={fake_secret}",
        ),
    )
    assert fake_secret not in json.dumps(f_res, ensure_ascii=False)
    assert "***" in f_res["final_url"]


def test_web_module_pure_and_no_heavy_imports() -> None:
    """T13 (PR1): 独立子进程断言 pipeline.agent.web 顶层零重依赖、零第三方 HTTP/HTML 库。"""
    probe_code = (
        "import pipeline.agent.web, sys; "
        "forbidden = ('numpy', 'torch', 'moviepy', 'transformers', "
        "             'requests', 'httpx', 'bs4', 'lxml'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'pipeline.agent.web 顶层违规引入: {leaked}'"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe_code], capture_output=True, text=True
    )
    assert res.returncode == 0, f"依赖纯洁性检验失败:\n{res.stderr}"


def test_search_empty_parse_raises_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """T16 (PR1): 空结果纪律——解析 0 条结果必须抛 ValueError 且含「无结果或结构变更」。"""
    _pin_public_dns(monkeypatch)
    with pytest.raises(ValueError, match="无结果或结构变更"):
        search_web(
            "不存在的词",
            config=_make_config(),
            opener=lambda req, timeout=20.0: _FakeResponse(
                EMPTY_SO_FIXTURE_HTML.encode("utf-8")
            ),
        )


def test_pipeline_scope_masks_web_tools() -> None:
    """T3 (PR2): mask-don't-remove 三层证据（注册表常驻 + schema 层掩码 + execute_tool 双闸）。"""
    from pipeline.agent.tools import (
        TOOL_SCHEMAS,
        ToolContext,
        build_tool_schemas,
        execute_tool,
    )

    # ① 注册表常驻
    assert "web_search" in TOOL_SCHEMAS
    assert "web_fetch" in TOOL_SCHEMAS

    # ② schema 层按 scope 掩码
    for masked_scope in ("pipeline", "idea"):
        masked_names = [
            s["function"]["name"] for s in build_tool_schemas(masked_scope)
        ]
        assert "web_search" not in masked_names
        assert "web_fetch" not in masked_names

    for visible_scope in ("creative", "asset"):
        visible_names = [
            s["function"]["name"] for s in build_tool_schemas(visible_scope)
        ]
        assert "web_search" in visible_names
        assert "web_fetch" in visible_names

    # ③ 执行层第二道闸
    ctx_pipeline = ToolContext(scope="pipeline")
    for tool_name, sample_args in (
        ("web_search", {"query": "芙莉莲"}),
        ("web_fetch", {"url": "https://example.org/"}),
    ):
        denied = execute_tool(tool_name, sample_args, ctx_pipeline)
        assert denied["ok"] is False
        assert "白名单" in denied["error"]


def test_idea_and_pipeline_tables_unchanged() -> None:
    """T4 (PR2): pipeline 与 idea 两 scope 的工具表经 build_tool_schemas 取清单逐字不变。"""
    from pipeline.agent.tools import build_tool_schemas

    idea_names = [s["function"]["name"] for s in build_tool_schemas("idea")]
    pipeline_names = [s["function"]["name"] for s in build_tool_schemas("pipeline")]

    assert idea_names == [
        "read_artifact",
        "list_episodes",
        "read_status",
        "search_notes",
    ]
    assert pipeline_names == [
        "read_artifact",
        "read_status",
        "list_episodes",
        "run_pipeline",
    ]


def test_egress_blocks_via_execute_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """T5b (PR2): egress 拦截经 execute_tool 返回错误数据且请求零发出。"""
    from pipeline.agent.tools import ToolContext, execute_tool

    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        calls.append(req)
        return _FakeResponse(SO_FIXTURE_HTML.encode("utf-8"))

    monkeypatch.setattr(web, "_default_opener", lambda *_: fake_opener)
    ctx = ToolContext(scope="creative")

    res_s = execute_tool(
        "web_search", {"query": "cloud.local.json 密钥"}, ctx
    )
    assert res_s["ok"] is False
    assert "拦截出网请求" in res_s["error"]

    res_f = execute_tool(
        "web_fetch", {"url": "https://example.org/03-audio/manifest.json"}, ctx
    )
    assert res_f["ok"] is False
    assert "拦截出网请求" in res_f["error"]
    assert len(calls) == 0


def test_offline_and_timeout_degrade_via_execute_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T6b (PR2): 断网/超时/DNS 失败/配置缺失经 execute_tool 显式降级为结构化错误数据。"""
    from pipeline.agent.tools import ToolContext, execute_tool

    _pin_public_dns(monkeypatch)
    ctx = ToolContext(scope="creative")

    for exc in (
        urllib.error.URLError("offline"),
        TimeoutError("timed out"),
        socket.gaierror(8, "nodename nor servname"),
    ):
        monkeypatch.setattr(
            web,
            "_default_opener",
            lambda *_, e=exc: (lambda req, timeout=20.0: (_ for _ in ()).throw(e)),
        )
        out_s = execute_tool("web_search", {"query": "frieren"}, ctx)
        assert out_s["ok"] is False
        assert type(exc).__name__ in out_s["error"]

        out_f = execute_tool(
            "web_fetch", {"url": "https://example.org/"}, ctx
        )
        assert out_f["ok"] is False
        assert type(exc).__name__ in out_f["error"]

    # 配置缺失：构造有 tools.json 但无 web.json 的 tmp_path root
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "tools.json").write_text(
        json.dumps({"creative": ["web_search", "web_fetch"]}), encoding="utf-8"
    )
    ctx_missing = ToolContext(scope="creative", root=tmp_path)
    out_missing_s = execute_tool("web_search", {"query": "frieren"}, ctx_missing)
    assert out_missing_s["ok"] is False
    assert "web.json" in out_missing_s["error"]

    out_missing_f = execute_tool(
        "web_fetch", {"url": "https://example.org/"}, ctx_missing
    )
    assert out_missing_f["ok"] is False
    assert "web.json" in out_missing_f["error"]


def test_tool_schemas_protocol_keys_whitelist() -> None:
    """T11 (PR2): build_tool_schemas 协议键白名单，side_effect 与 adr 零泄漏。"""
    from pipeline.agent.tools import build_tool_schemas

    for scope in ("creative", "pipeline", "asset", "idea"):
        schemas = build_tool_schemas(scope)
        for item in schemas:
            assert set(item["function"].keys()) == {
                "name",
                "description",
                "parameters",
            }
        serialized = json.dumps(schemas, ensure_ascii=False)
        assert "side_effect" not in serialized
        assert '"adr"' not in serialized


def test_every_tool_has_existing_adr() -> None:
    """T12 (PR2): 工具表变更必须登记现存 ADR 编号（数字段 glob 恰中 1），总数 12 <= 12。"""
    import re
    from pipeline import paths
    from pipeline.agent.tools import TOOL_SCHEMAS

    assert len(TOOL_SCHEMAS) == 12     # Spec 7 PR3 登记 write_memory，占满 ADR-0021 的预留位
    assert len(TOOL_SCHEMAS) <= 12

    adr_dir = paths.ROOT / "docs" / "dev" / "adr"
    for name, schema in TOOL_SCHEMAS.items():
        adr_val = str(schema.get("adr", ""))
        m = re.match(r"^ADR-(\d{4})$", adr_val)
        assert m is not None, f"工具 '{name}' 缺少合法 ADR 编号: {adr_val!r}"
        num = m.group(1)
        matches = list(adr_dir.glob(f"{num}-*.md"))
        assert len(matches) == 1, (
            f"工具 '{name}' 的 ADR-{num} 在 {adr_dir} 中命中 {len(matches)} 个文件: {matches}"
        )


def test_readonly_web_tools_auto_approve_no_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T15 (PR2): web_search / web_fetch 标 side_effect=False，免弹卡回显且不写 approvals.jsonl。"""
    from pipeline.agent import cli

    def _forbid_input(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("只读工具严禁调用 input() 弹卡")

    monkeypatch.setattr("builtins.input", _forbid_input)

    ok1, reason1 = cli._default_approve(
        "web_search", {"query": "芙莉莲"}, ep_dir=tmp_path, scope="creative"
    )
    assert (ok1, reason1) == (True, "")

    ok2, reason2 = cli._default_approve(
        "web_fetch",
        {"url": "https://bgm.tv/subject/292970"},
        ep_dir=tmp_path,
        scope="creative",
    )
    assert (ok2, reason2) == (True, "")

    out = capsys.readouterr().out
    assert "[tool] web_search 芙莉莲" in out
    assert "[tool] web_fetch https://bgm.tv/subject/292970" in out
    assert not (tmp_path / "_agent" / "approvals.jsonl").exists()


def test_egress_hit_aborts_turn_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T17 (PR2): live-loop 三腿验证——egress 命中时请求零发出、PermissionError 穿透 run_tool_loop、本轮 [BLOCKED] 交人。"""
    from pipeline.agent import cli, llm
    from pipeline.agent.assembly import SessionContextTracker
    from pipeline.agent.tools import ToolContext

    # ① chat_complete 层：messages 含受限模式串时发送前 assert_egress_boundary 触发，urlopen 零调用
    urlopen_calls_1: list[Any] = []
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda *args, **kwargs: urlopen_calls_1.append(args),
    )
    fake_llm_cfg = llm.LLMConfig(
        base_url="https://api.example.org/v1", model="mock", api_key="sk-fake"
    )
    with pytest.raises(PermissionError):
        llm.chat_complete(
            [
                {"role": "user", "content": "查一下"},
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": "PermissionError: 拦截出网请求: 'cloud.local.json'",
                },
            ],
            tools=[],
            config=fake_llm_cfg,
        )
    assert len(urlopen_calls_1) == 0

    # ② run_tool_loop 层：首轮 LLM 返回带模式串 tool_call -> execute_tool 捕获入史 -> 次轮 chat_complete 断言炸穿 run_tool_loop
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True)
    (tmp_path / "config" / "agent.json").write_text(
        json.dumps(
            {
                "base_url": "https://api.example.org/v1",
                "model": "mock-model",
                "api_key_env": "AVA_T17_KEY",
            }
        ),
        encoding="utf-8",
    )
    (cfg_dir / "tools.json").write_text(
        json.dumps({"creative": ["web_search", "web_fetch"]}), encoding="utf-8"
    )
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
                "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AVA_T17_KEY", "sk-t17-secret")

    web_opener_calls: list[Any] = []
    monkeypatch.setattr(
        web, "_default_opener", lambda *_: (lambda req, timeout=20.0: web_opener_calls.append(req))
    )

    llm_urlopen_count = 0

    class _MockLLMHttpResp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _MockLLMHttpResp:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def fake_llm_urlopen(req: Any, timeout: float = 60.0) -> _MockLLMHttpResp:
        nonlocal llm_urlopen_count
        llm_urlopen_count += 1
        return _MockLLMHttpResp(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_egress_1",
                                    "type": "function",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": json.dumps(
                                            {"query": "cloud.local.json 内容"},
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_llm_urlopen)
    with pytest.raises(PermissionError):
        llm.run_tool_loop(
            [{"role": "user", "content": "开始检索"}],
            ctx=ToolContext(scope="creative", root=tmp_path),
            approve=lambda name, args: (True, ""),
        )
    assert llm_urlopen_count == 1
    assert len(web_opener_calls) == 0

    # ③ _dispatch_agent_turn 层：PermissionError 被捕获，打印 [BLOCKED] 出网被拦截 并 pop 回滚用户消息
    (cfg_dir / "scopes").mkdir(parents=True, exist_ok=True)
    (cfg_dir / "scopes" / "creative.md").write_text("creative scope", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# AGENTS", encoding="utf-8")
    (cfg_dir / "context_routes.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        llm,
        "run_tool_loop",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("拦截出网请求: 'cloud.local.json'")
        ),
    )
    tracker = SessionContextTracker()
    messages: list[dict[str, Any]] = []
    user_line = "查一下受限内容"
    outcome = cli._dispatch_agent_turn(
        user_line,
        messages,
        ep_dir=None,
        scope="creative",
        root=tmp_path,
        tracker=tracker,
    )
    captured = capsys.readouterr().out
    assert "[BLOCKED] 出网被拦截" in captured
    assert outcome["stopped"] == "error"
    assert all(m.get("content") != user_line for m in messages)

