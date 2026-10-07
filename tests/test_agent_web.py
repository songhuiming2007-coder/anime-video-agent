"""Spec 4 (ADR-0021) 网络工具第一批 web_search + web_fetch 测试套件。"""

from __future__ import annotations

import email.message
import gzip
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

import pytest

from pipeline.agent import web
from pipeline.agent.web import (
    ProviderCfg,
    WebConfig,
    _GuardedRedirectHandler,
    fetch_web,
    load_web_config,
    search_web,
)

# Spec 15 T-P13：DDG HTML fixture 随 _SearchResultParser 退役，搜索用例换成 PR0 实测的
# provider 原始响应（tests/fixtures/web_search/，2026-10-06 真实请求，中性查询词）。
WEB_SEARCH_FIXTURES = Path(__file__).parent / "fixtures" / "web_search"
EXA_OK_SSE = (WEB_SEARCH_FIXTURES / "exa_mcp_ok.sse.txt").read_bytes()
NEW_SEARCH_SECTION = {"timeout_s": 20, "providers": [{"name": "exa_mcp"}]}


def _exa_sse(blocks: list[tuple[str, str, str]]) -> bytes:
    """按实测 exa_mcp 形态（exa_mcp_ok.sse.txt：SSE + 文本块 \n\n---\n\n 分隔）拼响应，
    供需要特定内容（受限串、空结果）的用例使用。"""
    text = "\n\n---\n\n".join(
        f"Title: {t}\nURL: {u}\nPublished: N/A\nAuthor: N/A\nHighlights:\n{h}"
        for t, u, h in blocks
    )
    msg = {"result": {"content": [{"type": "text", "text": text}]}, "jsonrpc": "2.0", "id": 1}
    return ("event: message\ndata: " + json.dumps(msg, ensure_ascii=False) + "\n\n").encode(
        "utf-8"
    )


def _make_config(
    *,
    providers: tuple[ProviderCfg, ...] = (ProviderCfg(name="exa_mcp"),),
    secret_values: tuple[str, ...] = (),
    max_fetch_bytes: int = 1_000_000,
    max_fetch_chars: int = 30_000,
    trusted_fake_ip_ranges: tuple[Any, ...] = (),
) -> WebConfig:
    return WebConfig(
        search_providers=providers,
        secret_values=secret_values,
        search_timeout_s=20.0,
        fetch_timeout_s=30.0,
        max_fetch_bytes=max_fetch_bytes,
        max_fetch_chars=max_fetch_chars,
        trusted_fake_ip_ranges=trusted_fake_ip_ranges,
    )


def _make_web_root(tmp_path: Path) -> Path:
    """新 schema web.json + creative 工具表的临时仓库根（Spec 15 T-P13：execute_tool 层用例
    不再读真实仓库的 config/，本机 web.local.json 的形态不得左右测试结论）。"""
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "tools.json").write_text(
        json.dumps({"creative": ["web_search", "web_fetch"]}), encoding="utf-8"
    )
    (cfg_dir / "web.json").write_text(
        json.dumps(
            {
                "search": NEW_SEARCH_SECTION,
                "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


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
        content_encoding: str | None = None,
    ) -> None:
        self._body = body
        self._pos = 0
        self.bytes_read = 0
        self.headers = {"Content-Type": content_type}
        if content_encoding is not None:
            self.headers["Content-Encoding"] = content_encoding
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
    """T1 (PR1；Spec 15 T-P13 换管道)：exa_mcp 实测 fixture 解析与 limit 截断。"""
    _pin_public_dns(monkeypatch)
    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        calls.append((req, timeout))
        return _FakeResponse(EXA_OK_SSE, content_type="text/event-stream")

    out = search_web("一色彩羽", limit=3, config=_make_config(), opener=fake_opener)
    assert len(calls) == 1
    assert out["query"] == "一色彩羽"
    assert out["provider"] == "exa_mcp"
    assert out["truncated"] is True
    assert len(out["results"]) == 3
    first = out["results"][0]
    assert set(first) == {"title", "url", "snippet"}
    assert first["title"] == "Isshiki, Iroha"
    assert first["url"] == "https://myanimelist.net/character/110743/Iroha_Isshiki"
    assert first["snippet"].startswith(
        "Iroha Isshiki (Yahari Ore no Seishun Love Comedy wa Machigatteiru. Zoku) - MyAnimeList.net"
    )
    assert len(first["snippet"]) == 469
    assert out["results"][1]["url"] == (
        "https://lndb.info/light_novel/Yahari_Ore_no_Seishun_Rom-Com_wa_Machigatteiru./char/749"
    )
    assert out["results"][2]["url"] == "https://anilist.co/character/88727/Isshiki-Iroha"


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


def test_fetch_content_encoding_gzip_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：服务器强制 gzip（未声明也压缩，python.org 实测形态）→ 流式解压出正常净文与链接。"""
    _pin_public_dns(monkeypatch)
    html = '<html><body><p>正文一段</p><a href="/x">链接甲</a></body></html>'
    raw = html.encode("utf-8")
    resp = _FakeResponse(gzip.compress(raw), content_encoding="gzip")
    out = fetch_web(
        "https://example.org/g",
        config=_make_config(),
        opener=lambda req, timeout=30.0: resp,
    )
    assert out["text"] == "正文一段 链接甲"
    assert [item["url"] for item in out["links"]] == ["https://example.org/x"]
    # fetched_bytes 为进入解码的字节数：压缩响应报解压后字节
    assert out["fetched_bytes"] == len(raw)


def test_fetch_content_encoding_deflate(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：deflate（zlib 包封）同样流式解压。"""
    _pin_public_dns(monkeypatch)
    html = "<html><body><p>deflate 正文</p></body></html>"
    resp = _FakeResponse(
        zlib.compress(html.encode("utf-8")), content_encoding="deflate"
    )
    out = fetch_web(
        "https://example.org/d",
        config=_make_config(),
        opener=lambda req, timeout=30.0: resp,
    )
    assert out["text"] == "deflate 正文"


def test_fetch_content_encoding_bomb_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：解压炸弹——解压后字节同受 max_fetch_bytes 封顶。"""
    _pin_public_dns(monkeypatch)
    body = gzip.compress(b"A" * 1_000_000)
    resp = _FakeResponse(
        body, content_type="text/plain; charset=utf-8", content_encoding="gzip"
    )
    out = fetch_web(
        "https://example.org/bomb",
        config=_make_config(max_fetch_bytes=65536),
        opener=lambda req, timeout=30.0: resp,
    )
    assert out["fetched_bytes"] == 65536
    assert out["text"] == "A" * 30000  # max_fetch_chars 默认 3 万
    assert out["truncated"] is True


def test_fetch_content_encoding_deflate_bomb_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45（评审补）：deflate 分支的解压炸弹同样受 max_fetch_bytes 封顶——
    gzip 版用例守不到 deflate 分支自己的 max_length 限流。"""
    _pin_public_dns(monkeypatch)
    body = zlib.compress(b"A" * 1_000_000)
    resp = _FakeResponse(
        body, content_type="text/plain; charset=utf-8", content_encoding="deflate"
    )
    # 末尾 out[:limit] 会兜住输出，所以「不限流也得到同样输出」的变异只有靠间谍抓：
    # 每次 decompress 必须带 max_length，且不超过 limit+1（内存上界，不是事后截断）
    real = zlib.decompressobj
    max_lengths: list[int] = []

    class _Spy:
        def __init__(self) -> None:
            self._inner = real()

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            max_lengths.append(max_length)
            return self._inner.decompress(data, max_length)

        def flush(self) -> bytes:
            return self._inner.flush()

    monkeypatch.setattr(zlib, "decompressobj", lambda *a, **k: _Spy())
    out = fetch_web(
        "https://example.org/deflate-bomb",
        config=_make_config(max_fetch_bytes=65536),
        opener=lambda req, timeout=30.0: resp,
    )
    assert out["fetched_bytes"] == 65536
    assert out["truncated"] is True
    assert max_lengths and all(0 < m <= 65537 for m in max_lengths), max_lengths


def test_fetch_content_encoding_unsupported_is_honest_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：未知编码（br 等）明确报错并附升级 crawl 提示，严禁静默错解。"""
    _pin_public_dns(monkeypatch)
    resp = _FakeResponse(b"\x1b\x00", content_encoding="br")
    with pytest.raises(ValueError, match="Content-Encoding.*crawl"):
        fetch_web(
            "https://example.org/br",
            config=_make_config(),
            opener=lambda req, timeout=30.0: resp,
        )


def test_fetch_content_encoding_corrupt_gzip_is_honest_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：损坏的 gzip 流诚实报错（不静默产出乱码）。"""
    _pin_public_dns(monkeypatch)
    resp = _FakeResponse(b"\x1f\x8b broken", content_encoding="gzip")
    with pytest.raises(ValueError, match="gzip"):
        fetch_web(
            "https://example.org/corrupt",
            config=_make_config(),
            opener=lambda req, timeout=30.0: resp,
        )


def test_fetch_content_encoding_identity_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：显式 identity 与原路径一致。"""
    _pin_public_dns(monkeypatch)
    html = "<html><body><p>identity 正文</p></body></html>"
    resp = _FakeResponse(html.encode("utf-8"), content_encoding="identity")
    out = fetch_web(
        "https://example.org/i",
        config=_make_config(),
        opener=lambda req, timeout=30.0: resp,
    )
    assert out["text"] == "identity 正文"


def test_search_content_encoding_gzip_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """N45：search_web 同一读取路径——端点强制 gzip 时仍解析出结果。"""
    _pin_public_dns(monkeypatch)
    resp = _FakeResponse(
        gzip.compress(EXA_OK_SSE), content_type="text/event-stream", content_encoding="gzip"
    )
    out = search_web(
        "一色彩羽",
        limit=3,
        config=_make_config(),
        opener=lambda req, timeout=20.0: resp,
    )
    assert len(out["results"]) >= 1
    assert "myanimelist.net" in out["results"][0]["url"]


def test_egress_blocks_before_send_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """T5a (PR1): 出网前拦截（直调腿），含编码变体与大小写变体，fake opener 零调用。"""
    _pin_public_dns(monkeypatch)
    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        calls.append(req)
        return _FakeResponse(EXA_OK_SSE, content_type="text/event-stream")

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
        # Spec 15 §2.2：超时/断网对 search 是「可落下一家」，全链失败时汇总成 ValueError，
        # 原因里点名异常类型（不再原样穿出）；fetch 腿语义不变。
        with pytest.raises(ValueError, match=f"全部检索服务失败.*{type(exc).__name__}"):
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

    dirty_search_sse = _exa_sse(
        [
            (
                "引用 agent.local.json 的文章",
                "https://example.org/1",
                "文中提到了 cloud.local.json 和 03-audio/voice.json",
            )
        ]
    )
    s_out = search_web(
        "配置说明",
        config=cfg,
        opener=lambda req, timeout=20.0: _FakeResponse(dirty_search_sse),
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
        return _FakeResponse(EXA_OK_SSE, content_type="text/event-stream")

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
        "search": NEW_SEARCH_SECTION,
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
    """T10 (PR1；Spec 15 T-P13 换管道)：web.local.json 整文件覆盖与凭据四不泄漏。"""
    _pin_public_dns(monkeypatch)
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True)

    (cfg_dir / "web.json").write_text(
        json.dumps(
            {
                "search": NEW_SEARCH_SECTION,
                "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
            }
        ),
        encoding="utf-8",
    )
    (cfg_dir / "web.local.json").write_text(
        json.dumps(
            {
                "search": {
                    "timeout_s": 15,
                    "providers": [
                        {"name": "tavily", "api_key_env": "TAVILY_AVA_TEST_API_KEY"}
                    ],
                },
                "fetch": {"timeout_s": 25, "max_bytes": 131072, "max_chars": 5000},
            }
        ),
        encoding="utf-8",
    )

    captured: list[Any] = []

    def search_opener(req: Any, timeout: float = 15.0) -> _FakeResponse:
        captured.append(req)
        body = json.loads((WEB_SEARCH_FIXTURES / "tavily_ok.json").read_text("utf-8"))
        body["results"][0]["content"] += f" echo {fake_secret}"
        return _FakeResponse(json.dumps(body).encode("utf-8"), content_type="application/json")

    # 指名了环境变量但未设置 -> 该家未就绪（Spec 15 §11 Q2），绝不静默退无凭据模式发裸请求
    fake_secret = "test-key-0000"
    monkeypatch.delenv("TAVILY_AVA_TEST_API_KEY", raising=False)
    unset = load_web_config(tmp_path)
    assert unset is not None
    assert unset.search_providers == (
        ProviderCfg(name="tavily", api_key_env="TAVILY_AVA_TEST_API_KEY", api_key=""),
    )
    with pytest.raises(ValueError, match="无可用检索服务.*TAVILY_AVA_TEST_API_KEY"):
        search_web("frieren", root=tmp_path, opener=search_opener)
    assert captured == []

    # 注入假密钥
    monkeypatch.setenv("TAVILY_AVA_TEST_API_KEY", fake_secret)
    loaded = load_web_config(tmp_path)
    assert loaded is not None
    assert [p.name for p in loaded.search_providers] == ["tavily"]
    assert loaded.search_providers[0].api_key == fake_secret
    assert loaded.secret_values == (fake_secret,)

    s_res = search_web("frieren", root=tmp_path, opener=search_opener)
    assert len(captured) == 1
    assert captured[0].unredirected_hdrs["Authorization"] == f"Bearer {fake_secret}"
    assert fake_secret not in json.dumps(s_res, ensure_ascii=False)
    assert "***" in s_res["results"][0]["snippet"]

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
    """T16 (PR1；Spec 15 T-P13 换管道)：空结果纪律——解析 0 条结果必须抛 ValueError 且含「无结果或结构变更」。"""
    _pin_public_dns(monkeypatch)
    with pytest.raises(ValueError, match="无结果或结构变更"):
        search_web(
            "不存在的词",
            config=_make_config(),
            opener=lambda req, timeout=20.0: _FakeResponse(_exa_sse([])),
        )


def test_web_tools_visible_in_all_scopes(tmp_path: Path) -> None:
    """T3 (PR2) 按 D43 / Spec 17 改写：原「mask-don't-remove 三层证据」的按 scope 掩码已废除——
    注册表常驻不变；schema 层单表全量可见；执行层不再有按 scope 的第二道闸。"""
    from pipeline.agent.tools import (
        TOOL_SCHEMAS,
        ToolContext,
        build_tool_schemas,
        execute_tool,
    )

    # ① 注册表常驻
    assert "web_search" in TOOL_SCHEMAS
    assert "web_fetch" in TOOL_SCHEMAS

    # ② schema 层：单表全量可见（不再按 scope 掩码）
    names = [s["function"]["name"] for s in build_tool_schemas()]
    assert "web_search" in names
    assert "web_fetch" in names

    # ③ 执行层：pipeline scope 调 web 工具拿到的不再是「白名单」拒因
    #    （root 无 web.json → 报配置缺失，证明调用已穿过已删除的 scope 闸）
    ctx_pipeline = ToolContext(scope="pipeline", root=tmp_path)
    for tool_name, sample_args in (
        ("web_search", {"query": "芙莉莲"}),
        ("web_fetch", {"url": "https://example.org/"}),
    ):
        out = execute_tool(tool_name, sample_args, ctx_pipeline)
        assert "白名单" not in out.get("error", "")


def test_all_scope_tables_equal_full_set() -> None:
    """T4 (PR2) 按 D43 / Spec 17 改写：pipeline 与 idea 不再逐字等于旧 4 件——
    单表全量，且与注册集一致（请求层逐字节相等的等强断言见 TA-1）。"""
    from pipeline.agent.tools import TOOL_SCHEMAS, _extra_available, build_tool_schemas

    names = [s["function"]["name"] for s in build_tool_schemas()]
    assert sorted(names) == sorted(
        n for n, s in TOOL_SCHEMAS.items()
        if not s.get("requires_extra") or _extra_available(str(s["requires_extra"]))
    )


def test_egress_blocks_via_execute_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T5b (PR2): egress 拦截经 execute_tool 返回错误数据且请求零发出。"""
    from pipeline.agent.tools import ToolContext, execute_tool

    calls: list[Any] = []

    def fake_opener(req: Any, timeout: float = 20.0) -> _FakeResponse:
        calls.append(req)
        return _FakeResponse(EXA_OK_SSE, content_type="text/event-stream")

    monkeypatch.setattr(web, "_default_opener", lambda *_: fake_opener)
    # Spec 15 T-P13：搜索走 _provider_opener，只 patch _default_opener 拦不住真实出网
    monkeypatch.setattr(web, "_provider_opener", fake_opener)
    ctx = ToolContext(scope="creative", root=_make_web_root(tmp_path))

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
    ctx = ToolContext(scope="creative", root=_make_web_root(tmp_path / "repo"))

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
        monkeypatch.setattr(
            web,
            "_provider_opener",
            lambda req, timeout=20.0, e=exc: (_ for _ in ()).throw(e),
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
    """T11 (PR2): build_tool_schemas 协议键白名单，side_effect 与 adr 零泄漏（D43 后单表）。"""
    from pipeline.agent.tools import build_tool_schemas

    schemas = build_tool_schemas()
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
    """T12 (PR2): 工具表变更必须登记现存 ADR 编号（数字段 glob 恰中 1），13 <= 14。"""
    import re
    from pipeline import paths
    from pipeline.agent.tools import TOOL_SCHEMAS

    assert len(TOOL_SCHEMAS) == 13     # Spec 12 登记 cover_edit（ADR-0025 上调封顶 12 → 14）
    assert len(TOOL_SCHEMAS) <= 14     # 上限断言：ADR-0021 口径，第 15 个须再立 ADR

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
    """T17 (PR2): live-loop 三腿验证——egress 命中时请求零发出、run_tool_loop 以 `blocked` 收口、本轮 [BLOCKED] 交人。"""
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
    _pin_public_dns(monkeypatch)  # Spec 15 T-P13：变异下 _guard_url 也不做真实 DNS 解析
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
        # D43 / Spec 17：单表（恰为注册全集，否则反向分叉检查报错）
        json.dumps({"tools": [
            "read_artifact", "write_episode_file", "list_episodes", "read_status",
            "run_pipeline", "search_notes", "web_search", "web_fetch", "acquire_propose",
            "crawl", "browser", "write_memory", "cover_edit",
        ]}),
        encoding="utf-8",
    )
    # Spec 15 T-P13：旧 schema 的 web.json 会被新 loader 判无效，换成新 schema（做不到字面不改）
    (cfg_dir / "web.json").write_text(
        json.dumps(
            {
                "search": NEW_SEARCH_SECTION,
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
    monkeypatch.setattr(
        web, "_provider_opener", lambda req, timeout=20.0: web_opener_calls.append(req)
    )
    # Spec 15 T-P13 / R2-4：间谍记下 tool 结果——光看 blocked 不够，配置无效时
    # tool_call 参数里的受限串照样会让下一轮 chat_complete 拦下，整条腿仍绿
    tool_outcomes: list[dict[str, Any]] = []
    real_execute_tool = llm.execute_tool

    def _spy_execute_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        res = real_execute_tool(*args, **kwargs)
        tool_outcomes.append(res)
        return res

    monkeypatch.setattr(llm, "execute_tool", _spy_execute_tool)

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
    outcome = llm.run_tool_loop(
        [{"role": "user", "content": "开始检索"}],
        ctx=ToolContext(scope="creative", root=tmp_path),
        approve=lambda name, args: (True, ""),
    )
    # Spec 9 §2.3 第 1 条：出网断言拒绝 = `blocked` + 回滚（不再穿透成异常，见 TL-13）
    assert outcome["stopped"] == "blocked"
    assert outcome["rollback"] is True
    assert llm_urlopen_count == 1
    assert len(web_opener_calls) == 0
    assert len(tool_outcomes) == 1
    assert tool_outcomes[0]["ok"] is False
    assert "拦截出网请求" in tool_outcomes[0]["error"]
    assert "web.json" not in tool_outcomes[0]["error"]  # 不是「配置无效」

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
    # 腿③走的是**外层**捕获（打桩直接把异常抛到 run_tool_loop 之外，🔵-10）：与现状同为 error
    assert outcome["stopped"] == "error"
    assert all(m.get("content") != user_line for m in messages)


# ——— Spec 13 §7.1：web_fetch 链接清单（T1–T11，全部 fixture HTML，零真网依赖） ———

LINKS_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head>
  <script>var x = '<a href="/from-script">脚本内链接</a>';</script>
  <style>/* <a href="/from-style">样式内链接</a> */</style>
</head>
<body>
  <!-- T1/T4：同一 URL 先空锚（纯图片）后文字锚，回填后保留 -->
  <a href="/a"><img src="/i.png" alt="图"></a>
  <a href="/a#frag">甲页</a>
  <a href="https://ta.example/a">甲页重复</a>
  <a href="/img-only"><img src="/x.png" alt="无文字"></a>
  <a href="b">乙页</a>
  <a href="https://tb.example/ext">跨站页</a>
</body>
</html>
"""

SCHEME_FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
  <a href="/ok">可用页</a>
  <a href="javascript:alert(1)">脚本伪链接</a>
  <a href="mailto:a@ta.example">邮件</a>
  <a href="ftp://files.tb.example/x">FTP</a>
  <a href="data:text/html,hi">数据链接</a>
</body></html>
"""

INTERLEAVED_FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
  <a href="https://tb.example/ext1">跨1</a>
  <a href="/s1">站内1</a>
  <a href="https://tb.example/ext2">跨2</a>
  <a href="/s2">站内2</a>
</body></html>
"""

LONG_ANCHOR_FIXTURE_HTML = (
    '<!DOCTYPE html><html><body><a href="/long">' + ("字" * 80) + "</a></body></html>"
)

CAP_CONSTRAINT_HOST = "https://s.test/"
MANY_LINKS_FIXTURE_HTML = (
    "<!DOCTYPE html><html><body>"
    + "".join(
        f'<a href="p{i}">n{i}</a>' for i in range(250)
    )
    + "</body></html>"
)

LONG_URL_FIXTURE_HTML = (
    "<!DOCTYPE html><html><body>"
    + "".join(
        f'<a href="https://ta.example/{"x" * 1480}{i}">{i}</a>' for i in range(10)
    )
    + "</body></html>"
)

DIRTY_LINKS_FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
  <a href="https://ta.example/03-audio/manifest.json">受限路径</a>
  <a href="/safe">引用 Cloud.Local.JSON 与 agent.local.json 的锚</a>
</body></html>
"""

NO_LINKS_FIXTURE_HTML = """<!DOCTYPE html>
<html><body><h1>纯文本页</h1><p>没有任何链接。</p></body></html>
"""


def _fetch_fixture(
    html_text: str,
    *,
    final_url: str = "https://ta.example/dir/page",
    requested_url: str = "https://ta.example/dir/page",
    monkeypatch: pytest.MonkeyPatch,
    cfg: WebConfig | None = None,
) -> dict[str, Any]:
    _pin_public_dns(monkeypatch)
    return fetch_web(
        requested_url,
        config=cfg or _make_config(),
        opener=lambda req, timeout=30.0: _FakeResponse(
            html_text.encode("utf-8"), final_url=final_url
        ),
    )


def test_extract_links_absolutize_dedupe_and_anchor_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1 (Spec 13 门禁 2): 绝对化、去 fragment 去重、锚文本回填、同站优先。"""
    out = _fetch_fixture(LINKS_FIXTURE_HTML, monkeypatch=monkeypatch)
    links = out["links"]
    assert [item["url"] for item in links] == [
        "https://ta.example/a",
        "https://ta.example/dir/b",
        "https://tb.example/ext",
    ]
    # 回填：首个非空锚胜出（先出现的空锚不覆盖后出现的文字锚）
    assert links[0]["anchor"] == "甲页"
    assert links[1]["anchor"] == "乙页"
    # script/style 内链接不进清单（复用 _SKIP_TAGS 语义）
    assert all("from-script" not in item["url"] for item in links)
    assert all("from-style" not in item["url"] for item in links)


def test_extract_links_scheme_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    """T2 (门禁 2): 仅 http/https 存活。"""
    out = _fetch_fixture(SCHEME_FIXTURE_HTML, monkeypatch=monkeypatch)
    assert [item["url"] for item in out["links"]] == ["https://ta.example/ok"]


def test_extract_links_same_site_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """T3 (门禁 2): 同站全体在前且组内文档序，跨站殿后。"""
    out = _fetch_fixture(INTERLEAVED_FIXTURE_HTML, monkeypatch=monkeypatch)
    assert [item["url"] for item in out["links"]] == [
        "https://ta.example/s1",
        "https://ta.example/s2",
        "https://tb.example/ext1",
        "https://tb.example/ext2",
    ]


def test_extract_links_drops_empty_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    """T4 (门禁 2): 纯图片链接（无文字锚）不进清单；同 URL 有文字锚时回填保留。"""
    out = _fetch_fixture(LINKS_FIXTURE_HTML, monkeypatch=monkeypatch)
    urls = [item["url"] for item in out["links"]]
    assert "https://ta.example/img-only" not in urls
    assert "https://ta.example/a" in urls  # 空锚先出现但被文字锚回填


def test_extract_links_anchor_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """T5 (门禁 2): 锚文本截断至 ANCHOR_MAX_CHARS。"""
    out = _fetch_fixture(LONG_ANCHOR_FIXTURE_HTML, monkeypatch=monkeypatch)
    assert len(out["links"]) == 1
    assert len(out["links"][0]["anchor"]) == 60


def test_fetch_links_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """T6 (门禁 3): 条数帽 200 触发且字符帽不先触发（fixture 单条 JSON ≤50 字符）。"""
    entries = [
        {"url": f"{CAP_CONSTRAINT_HOST}p{i}", "anchor": f"n{i}"} for i in range(250)
    ]
    sizes = [len(json.dumps(e, ensure_ascii=False)) for e in entries]
    assert max(sizes) <= 50, "fixture 约束：单条 JSON 计长 ≤50 字符（🟡-4）"
    assert sum(sizes[:200]) <= 10000, "fixture 约束：200 条累计 ≤10000 < 12000"

    out = _fetch_fixture(
        MANY_LINKS_FIXTURE_HTML,
        final_url="https://s.test/page",
        requested_url="https://s.test/page",
        monkeypatch=monkeypatch,
    )
    assert len(out["links"]) == 200
    assert out["links_truncated"] is True


def test_fetch_links_char_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """T7 (门禁 3): 字符帽 12000 触发而条数帽不触发（fixture 条数 <200）。"""
    out = _fetch_fixture(LONG_URL_FIXTURE_HTML, monkeypatch=monkeypatch)
    assert len(out["links"]) < 200
    assert 0 < len(out["links"]) < 10, "长 URL 使字符帽在中途触发"
    assert out["links_truncated"] is True
    used = sum(len(json.dumps(e, ensure_ascii=False)) for e in out["links"])
    assert used <= 12000


def test_fetch_links_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """T8 (门禁 4): 链接 URL/锚文本同过 _scrub，受限字样不进上下文（会话不炸）。"""
    out = _fetch_fixture(DIRTY_LINKS_FIXTURE_HTML, monkeypatch=monkeypatch)
    dumped = json.dumps(out["links"], ensure_ascii=False)
    assert "03-audio/manifest.json" not in dumped
    assert "Cloud.Local.JSON" not in dumped
    assert "agent.local.json" not in dumped
    assert "[已脱敏]" in dumped


def test_fetch_links_contract_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """T9 (门禁 1): 返回含 links/links_truncated 两键，既有 7 键不动。"""
    out = _fetch_fixture(NO_LINKS_FIXTURE_HTML, monkeypatch=monkeypatch)
    for key in (
        "url",
        "final_url",
        "status",
        "content_type",
        "text",
        "truncated",
        "fetched_bytes",
    ):
        assert key in out
    assert isinstance(out["links"], list)
    assert isinstance(out["links_truncated"], bool)


def test_fetch_no_links_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """T10 (门禁 2): 无 <a> 页 → 空清单且未截断（空清单是合法结果）。"""
    out = _fetch_fixture(NO_LINKS_FIXTURE_HTML, monkeypatch=monkeypatch)
    assert out["links"] == []
    assert out["links_truncated"] is False


def test_fetch_links_base_is_final_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """T11 (门禁 2): 相对链接以重定向后的 final_url 绝对化。"""
    out = _fetch_fixture(
        '<html><body><a href="next">下一跳</a></body></html>',
        requested_url="https://ta.example/start",
        final_url="https://tb.example/deep/page",
        monkeypatch=monkeypatch,
    )
    assert [item["url"] for item in out["links"]] == ["https://tb.example/deep/next"]
