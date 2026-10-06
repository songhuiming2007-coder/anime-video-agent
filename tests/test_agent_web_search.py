"""Spec 15（D29）web_search provider 链测试：T-P1～T-P17（T-P13 在 test_agent_web.py）。

铁律（Spec 15 §7）：零真实出网（opener 注入 fake，_pin_public_dns 钉死公网 IP；T-P14b 只连
127.0.0.1）；密钥一律假值；fixture 取自 PR0 真实响应（tests/fixtures/web_search/）。
exa_api 暂不注册（Spec 15 §12 施工偏差 2），原写给它的腿改用 tavily 承担。
"""

from __future__ import annotations

import ast
import email.message
import http.server
import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import pytest

from pipeline.agent import web
from pipeline.agent.web import (
    SEARCH_SNIPPET_MAX_CHARS,
    ProviderCfg,
    WebConfig,
    fetch_web,
    load_web_config,
    search_web,
    web_key_env_names,
)

FIXTURES = Path(__file__).parent / "fixtures" / "web_search"
EXA_OK = (FIXTURES / "exa_mcp_ok.sse.txt").read_bytes()
TAVILY_OK = (FIXTURES / "tavily_ok.json").read_bytes()
FAKE_KEY = "test-key-0000"
_REAL_GETADDRINFO = socket.getaddrinfo  # T-P14b 只连回环地址，需要绕开 autouse 的 DNS 钉
FAKE_KEY_2 = "test-key-1111"

EXA = ProviderCfg(name="exa_mcp")
TAVILY = ProviderCfg(name="tavily", api_key_env="TAVILY_API_KEY", api_key=FAKE_KEY)
TAVILY_UNSET = ProviderCfg(name="tavily", api_key_env="TAVILY_API_KEY", api_key="")


def _cfg(*providers: ProviderCfg, secrets: tuple[str, ...] | None = None) -> WebConfig:
    return WebConfig(
        search_providers=providers,
        search_timeout_s=20.0,
        fetch_timeout_s=30.0,
        max_fetch_bytes=1_000_000,
        max_fetch_chars=30_000,
        secret_values=(
            secrets if secrets is not None else tuple(p.api_key for p in providers if p.api_key)
        ),
    )


DEFAULT_CHAIN = _cfg(EXA, TAVILY)


def _pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )


@pytest.fixture(autouse=True)
def _pin_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch)


class _Resp:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self._body = body
        self._pos = 0
        self.headers = {"Content-Type": content_type}
        self.status = 200

    def read(self, amt: int | None = None) -> bytes:
        end = len(self._body) if amt is None else self._pos + amt
        chunk = self._body[self._pos : end]
        self._pos += len(chunk)
        return chunk

    def geturl(self) -> str:
        return "https://example.org/page"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", email.message.Message(), None)


def _exa_sse(blocks: list[tuple[str, str, str]]) -> bytes:
    """按实测形态（exa_mcp_ok.sse.txt）拼 SSE，供需要特定内容的腿使用。"""
    text = "\n\n---\n\n".join(
        f"Title: {t}\nURL: {u}\nPublished: N/A\nAuthor: N/A\nHighlights:\n{h}"
        for t, u, h in blocks
    )
    msg = {"result": {"content": [{"type": "text", "text": text}]}, "jsonrpc": "2.0", "id": 1}
    return ("event: message\ndata: " + json.dumps(msg, ensure_ascii=False) + "\n\n").encode()


def _exa_in_band(text: str) -> bytes:
    msg = {"result": {"content": [{"type": "text", "text": text}], "isError": True}, "jsonrpc": "2.0", "id": 1}
    return ("event: message\ndata: " + json.dumps(msg, ensure_ascii=False) + "\n\n").encode()


def _tavily(n: int) -> bytes:
    return json.dumps(
        {
            "query": "q",
            "results": [
                {"url": f"https://t.example/{i}", "title": f"T{i}", "content": f"c{i}", "score": 0.5}
                for i in range(n)
            ],
        }
    ).encode()


def _family(req: Any) -> str:
    if req.full_url.startswith("https://mcp.exa.ai/"):
        return "exa_mcp"
    if req.full_url.startswith("https://api.tavily.com/"):
        return "tavily"
    raise AssertionError(f"未知端点 {req.full_url}")


class _Router:
    """按端点分派的 fake opener，逐家计数、记录请求。"""

    def __init__(self, **behaviour: Any) -> None:
        self.behaviour = behaviour
        self.calls: dict[str, int] = {"exa_mcp": 0, "tavily": 0}
        self.requests: list[Any] = []

    def __call__(self, req: Any, timeout: float = 20.0) -> _Resp:
        fam = _family(req)
        self.calls[fam] += 1
        self.requests.append(req)
        b = self.behaviour[fam]
        if isinstance(b, BaseException):
            raise b
        if isinstance(b, Callable):  # type: ignore[arg-type]
            return b(req)
        content_type = "text/event-stream" if fam == "exa_mcp" else "application/json"
        return _Resp(b, content_type)


# ——— T-P1 / T-P2：解析 ———


def test_tp1_exa_mcp_parses_real_fixture() -> None:
    """T-P1：实测 SSE → 逐字段三元组；\\n\\n---\\n\\n 分块；limit 生效。"""
    router = _Router(exa_mcp=EXA_OK)
    out = search_web("一色彩羽", limit=4, config=_cfg(EXA), opener=router)
    assert router.calls == {"exa_mcp": 1, "tavily": 0}
    assert out["provider"] == "exa_mcp"
    assert [r["url"] for r in out["results"]] == [
        "https://myanimelist.net/character/110743/Iroha_Isshiki",
        "https://lndb.info/light_novel/Yahari_Ore_no_Seishun_Rom-Com_wa_Machigatteiru./char/749",
        "https://anilist.co/character/88727/Isshiki-Iroha",
        "https://anihk.com/en/character/iroha-isshiki-my-teen-romantic-comedy-snafu-too",
    ]
    assert out["results"][1]["title"] == "Iroha Isshiki - LNDB.info - The Light Novel Database"
    assert out["results"][1]["snippet"].startswith("# Iroha Isshiki | Other character |")
    # Highlights 之后的正文是 snippet，块头字段（Published/Author）不混进去
    assert all("Published:" not in r["snippet"] for r in out["results"])
    assert out["truncated"] is True
    # 全部 11 块都能解析出来（fixture 用 numResults=11 实测，§2.3 上限核实）
    assert len(web._parse_exa_mcp(EXA_OK.decode())) == 11


def test_tp1_sse_takes_first_message_with_result() -> None:
    """T-P1（M12）：SSE 里多条 data: 时取第一条带 result 的，不取最后一条。"""
    good = _exa_sse([("A", "https://a.example/", "aaa")]).decode()
    later = _exa_sse([("Z", "https://z.example/", "zzz")]).decode()
    noise = 'event: ping\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
    out = web._parse_exa_mcp(noise + good + later)
    assert [r["url"] for r in out] == ["https://a.example/"]


def test_tp2_tavily_parses_real_fixture() -> None:
    """T-P2：Tavily 实测响应 → 统一三元组。"""
    router = _Router(tavily=TAVILY_OK)
    out = search_web("一色彩羽", limit=10, config=_cfg(TAVILY), opener=router)
    assert out["provider"] == "tavily"
    assert len(out["results"]) == 10
    assert out["truncated"] is True
    assert out["results"][0] == {
        "title": "Iroha Isshiki - AI Character Cards",
        "url": "https://character-tavern.com/character/otaya/iroha_isshiki",
        "snippet": (
            "Aug 30, 2025 — Iroha Isshiki is a high school girl with short, light brown "
            "wavy hair, hazel-brown eyes, and a soft, cute face that makes her seem "
            "innocent ...Read more"
        ),
    }
    assert set(out) == {"query", "provider", "results", "truncated"}


# ——— T-P3：POST body 的 egress ———


@pytest.mark.parametrize(
    "bad_query",
    ["cloud.local.json 里写了什么", "%2563loud.local.json 双重编码", "Cloud.Local.JSON"],
)
def test_tp3_egress_blocks_post_body_before_any_send(bad_query: str) -> None:
    """T-P3：受限 query → PermissionError，每家 opener 零调用。"""
    router = _Router(exa_mcp=EXA_OK, tavily=TAVILY_OK)
    with pytest.raises(PermissionError, match="拦截出网请求"):
        search_web(bad_query, config=DEFAULT_CHAIN, opener=router)
    assert router.calls == {"exa_mcp": 0, "tavily": 0}


def test_tp3_egress_second_provider_leg() -> None:
    """T-P3 第二家腿（M2）：链首未就绪被跳过，真正发送的第二家同样先断言。"""
    router = _Router(exa_mcp=EXA_OK, tavily=TAVILY_OK)
    with pytest.raises(PermissionError, match="拦截出网请求"):
        search_web("agent.local.json", config=_cfg(TAVILY_UNSET, EXA), opener=router)
    assert router.calls == {"exa_mcp": 0, "tavily": 0}


def test_tp3_egress_asserted_after_primary_failure() -> None:
    """T-P3：主家失败落到备家时，备家发送前也断言（断言用归一后的 query）。"""
    seen: list[tuple[str, Any]] = []
    real = web.assert_egress_boundary

    def spy(endpoint: str, content: Any, **kw: Any) -> None:
        seen.append((endpoint, content))
        real(endpoint, content, **kw)

    router = _Router(exa_mcp=_http_error(429), tavily=TAVILY_OK)
    import pytest as _pt

    with _pt.MonkeyPatch.context() as mp:
        mp.setattr(web, "assert_egress_boundary", spy)
        search_web("a%2562c", config=DEFAULT_CHAIN, opener=router)
    assert seen == [
        ("https://mcp.exa.ai/mcp?tools=web_search_exa", {"query": "abc"}),
        ("https://api.tavily.com/search", {"query": "abc"}),
    ]


# ——— T-P4：落下一家 ———


@pytest.mark.parametrize(
    "primary, reason",
    [
        (_http_error(429), "HTTP 429（限流）"),
        (TimeoutError("timed out"), "超时（TimeoutError）"),
        (_http_error(503), "HTTP 503"),
        (urllib.error.URLError("refused"), "连接失败（URLError）"),
        (_exa_sse([]), "解析为空（无结果或结构变更）"),
        (b"<html>not sse</html>", "响应结构不符"),
    ],
)
def test_tp4_fall_through_to_backup(primary: Any, reason: str) -> None:
    """T-P4（M4/M13）：主家可落下一家的故障 → 备家答；主家恰调 1 次（零重试）。"""
    router = _Router(exa_mcp=primary, tavily=TAVILY_OK)
    out = search_web("frieren", config=DEFAULT_CHAIN, opener=router)
    assert out["provider"] == "tavily"
    assert len(out["results"]) == 5
    assert router.calls == {"exa_mcp": 1, "tavily": 1}
    # 同一故障在全链失败时出现在汇总里（原因措辞钉住）
    router2 = _Router(exa_mcp=primary, tavily=_http_error(500))
    with pytest.raises(ValueError) as exc_info:
        search_web("frieren", config=DEFAULT_CHAIN, opener=router2)
    assert f"exa_mcp: {reason}" in str(exc_info.value)


# ——— T-P5：401/403 ———


@pytest.mark.parametrize("code", [401, 403])
def test_tp5a_keyed_provider_auth_failure_is_fatal(code: int) -> None:
    """T-P5a（M3a）：配了 key 的家 401/403 → 直接失败，下一家零调用，消息指向 key。"""
    router = _Router(tavily=_http_error(code), exa_mcp=EXA_OK)
    with pytest.raises(ValueError, match=f"tavily 的密钥被拒（HTTP {code}）.*TAVILY_API_KEY"):
        search_web("frieren", config=_cfg(TAVILY, EXA), opener=router)
    assert router.calls == {"tavily": 1, "exa_mcp": 0}


def test_tp5a_lists_previous_attempts() -> None:
    """T-P5a：§11 Q3「错误汇总里同时列出已尝试各家的原因」。"""
    router = _Router(exa_mcp=_http_error(429), tavily=_http_error(401))
    with pytest.raises(ValueError, match="此前已尝试：exa_mcp: HTTP 429"):
        search_web("frieren", config=DEFAULT_CHAIN, opener=router)


def test_tp5b_keyless_provider_auth_failure_falls_through() -> None:
    """T-P5b（M3b）：免 key 的家 403 → 备家顶上；备家也挂则汇总写明免 key 入口被拒。"""
    router = _Router(exa_mcp=_http_error(403), tavily=TAVILY_OK)
    out = search_web("frieren", config=DEFAULT_CHAIN, opener=router)
    assert out["provider"] == "tavily"

    router2 = _Router(exa_mcp=_http_error(403), tavily=_http_error(429))
    with pytest.raises(ValueError) as exc_info:
        search_web("frieren", config=DEFAULT_CHAIN, opener=router2)
    msg = str(exc_info.value)
    assert "exa_mcp: Exa 免 key 入口被拒（HTTP 403）" in msg
    assert "tavily: HTTP 429（限流）" in msg


# ——— T-P6 / T-P7：全链失败与空结果 ———


def test_tp6_all_failed_lists_every_provider() -> None:
    """T-P6（M6）：逐家列原因；不含 query 全文、不含密钥。"""
    query = "一个独特的查询串-xyz"
    router = _Router(exa_mcp=_http_error(429), tavily=TimeoutError())
    with pytest.raises(ValueError) as exc_info:
        search_web(query, config=DEFAULT_CHAIN, opener=router)
    msg = str(exc_info.value)
    assert msg == "web_search 全部检索服务失败：exa_mcp: HTTP 429（限流）；tavily: 超时（TimeoutError）"
    assert query not in msg and FAKE_KEY not in msg


def test_tp7_all_empty_raises_never_empty_list() -> None:
    """T-P7（M5）：各家都 0 条 → 抛错，严禁返回空 list。"""
    router = _Router(exa_mcp=_exa_sse([]), tavily=_tavily(0))
    with pytest.raises(ValueError, match="exa_mcp: 解析为空.*tavily: 解析为空"):
        search_web("frieren", config=DEFAULT_CHAIN, opener=router)


# ——— T-P8：凭据卫生 ———


def test_tp8_search_key_hygiene(capsys: pytest.CaptureFixture[str]) -> None:
    """T-P8（M10）：key 只在请求头；不进返回值 / 错误消息 / provider / stdout / stderr。"""
    echo = json.loads(TAVILY_OK)
    echo["results"][0]["title"] += f" {FAKE_KEY}"
    echo["results"][0]["content"] += f" Bearer {FAKE_KEY}"
    echo["results"][1]["url"] = f"https://t.example/?k={FAKE_KEY}"
    router = _Router(tavily=json.dumps(echo).encode())
    out = search_web("frieren", config=_cfg(TAVILY), opener=router)
    assert router.requests[0].unredirected_hdrs["Authorization"] == f"Bearer {FAKE_KEY}"
    dumped = json.dumps(out, ensure_ascii=False)
    assert FAKE_KEY not in dumped
    assert dumped.count("***") == 3

    for failing in (_http_error(401), _http_error(500), _tavily(0)):
        with pytest.raises(ValueError) as exc_info:
            search_web("frieren", config=_cfg(TAVILY), opener=_Router(tavily=failing))
        assert FAKE_KEY not in str(exc_info.value)

    in_band = _Router(exa_mcp=_exa_in_band(f"rate limited for key {FAKE_KEY}"), tavily=_tavily(0))
    with pytest.raises(ValueError) as exc_info:
        search_web("frieren", config=DEFAULT_CHAIN, opener=in_band)
    assert FAKE_KEY not in str(exc_info.value)

    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out and FAKE_KEY not in captured.err


def test_tp8_fetch_redacts_every_chain_key() -> None:
    """T-P8 fetch 腿（M21）：链上两个 key 任一出现在正文 / final_url / 链接清单 / url 都被替换。"""
    cfg = _cfg(TAVILY, secrets=(FAKE_KEY, FAKE_KEY_2))
    for key in (FAKE_KEY, FAKE_KEY_2):
        html = f'<html><body>echo {key} <a href="/p?k={key}">anchor {key}</a></body></html>'

        class _FetchResp(_Resp):
            def geturl(self, _k: str = key) -> str:
                return f"https://example.org/final?k={_k}"

        out = fetch_web(
            f"https://example.org/data?k={key}",
            config=cfg,
            opener=lambda req, timeout=30.0, h=html: _FetchResp(h.encode(), "text/html"),
        )
        dumped = json.dumps(out, ensure_ascii=False)
        assert key not in dumped, key
        assert "***" in out["text"] and "***" in out["final_url"] and "***" in out["url"]
        assert "***" in out["links"][0]["url"] and "***" in out["links"][0]["anchor"]


# ——— T-P9：配置校验 ———


def _write_cfg(root: Path, search: dict[str, Any], *, llm_env: str | None = None) -> Path:
    cfg_dir = root / "config" / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "web.json").write_text(
        json.dumps(
            {
                "search": search,
                "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
            }
        ),
        encoding="utf-8",
    )
    if llm_env is not None:
        (root / "config" / "agent.json").write_text(
            json.dumps({"base_url": "https://llm.example/v1", "model": "m", "api_key_env": llm_env}),
            encoding="utf-8",
        )
    return root


INVALID_SEARCH_SECTIONS = {
    "legacy": {
        "endpoint": "https://www.so.com/s",
        "query_param": "q",
        "api_key_env": "",
        "api_key_param": "",
        "timeout_s": 20,
    },
    "legacy_mixed_in": {"timeout_s": 20, "providers": [{"name": "exa_mcp"}], "endpoint": "x"},
    "unknown": {"timeout_s": 20, "providers": [{"name": "duckduckgo"}]},
    "exa_api_not_registered": {
        "timeout_s": 20,
        "providers": [{"name": "exa_api", "api_key_env": "EXA_API_KEY"}],
    },
    "duplicate": {"timeout_s": 20, "providers": [{"name": "exa_mcp"}, {"name": "exa_mcp"}]},
    "empty": {"timeout_s": 20, "providers": []},
    "too_many": {"timeout_s": 20, "providers": [{"name": "exa_mcp"}] * 5},
    "foreign_family": {
        "timeout_s": 20,
        "providers": [{"name": "tavily", "api_key_env": "CPA_API_KEY"}],
    },
    "bad_shape": {"timeout_s": 20, "providers": [{"name": "tavily", "api_key_env": "TAVILY_X"}]},
    "missing_env_field": {"timeout_s": 20, "providers": [{"name": "tavily"}]},
    "exa_mcp_with_key": {
        "timeout_s": 20,
        "providers": [{"name": "exa_mcp", "api_key_env": "EXA_API_KEY"}],
    },
}


@pytest.mark.parametrize("case", sorted(INVALID_SEARCH_SECTIONS))
def test_tp9_invalid_search_config(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P9（M11/M22）：旧字段 / 未知名 / 重名 / 空链 / >4 家 / 借名 → None，opener 零调用。"""
    monkeypatch.setenv("CPA_API_KEY", "llm-secret")
    monkeypatch.setenv("TAVILY_X", FAKE_KEY)
    monkeypatch.setenv("EXA_API_KEY", FAKE_KEY)
    root = _write_cfg(tmp_path, INVALID_SEARCH_SECTIONS[case])
    assert load_web_config(root) is None
    assert web_key_env_names(root) == []
    router = _Router(exa_mcp=EXA_OK, tavily=TAVILY_OK)
    with pytest.raises(ValueError, match="缺少或无效的 config/agent/web.json"):
        search_web("frieren", root=root, opener=router)
    assert router.calls == {"exa_mcp": 0, "tavily": 0}


def test_tp9_legacy_message_points_to_providers(tmp_path: Path) -> None:
    root = _write_cfg(tmp_path, INVALID_SEARCH_SECTIONS["legacy"])
    with pytest.raises(ValueError, match=r"旧字段.*search\.providers"):
        search_web("frieren", root=root, opener=_Router())
    with pytest.raises(ValueError, match=r"旧字段.*search\.providers"):
        fetch_web("https://example.org/", root=root, opener=_Router())


def test_tp9_borrowing_llm_key_name_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P9 借名腿（R2-2）：web 链指名的变量与 LLM 密钥同名 → 整份无效，LLM key 不外发。"""
    monkeypatch.setenv("TAVILY_LLM_API_KEY", "llm-secret")
    search = {"timeout_s": 20, "providers": [{"name": "tavily", "api_key_env": "TAVILY_LLM_API_KEY"}]}
    root = _write_cfg(tmp_path, search, llm_env="TAVILY_LLM_API_KEY")
    assert load_web_config(root) is None
    assert web_key_env_names(root) == []
    # 对照：LLM 换个名字，同一份 web 配置就合法
    root2 = _write_cfg(tmp_path / "ok", search, llm_env="CPA_API_KEY")
    assert load_web_config(root2) is not None
    assert web_key_env_names(root2) == ["TAVILY_LLM_API_KEY"]


def test_tp9_unset_key_marks_provider_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P9（M14）：指名 key 变量为空 → 该家未就绪而非整体无效；该家零调用，汇总写缺哪个变量。"""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    root = _write_cfg(
        tmp_path,
        {"timeout_s": 20, "providers": [{"name": "exa_mcp"}, {"name": "tavily", "api_key_env": "TAVILY_API_KEY"}]},
    )
    cfg = load_web_config(root)
    assert cfg is not None
    assert cfg.search_providers == (EXA, TAVILY_UNSET)
    assert cfg.secret_values == ()

    router = _Router(exa_mcp=_http_error(429), tavily=TAVILY_OK)
    with pytest.raises(ValueError) as exc_info:
        search_web("frieren", root=root, opener=router)
    assert router.calls == {"exa_mcp": 1, "tavily": 0}
    msg = str(exc_info.value)
    assert msg.startswith("web_search 全部检索服务失败：exa_mcp: HTTP 429（限流）；tavily: 需要环境变量 TAVILY_API_KEY")
    assert "security add-generic-password -s ava -a TAVILY_API_KEY -w" in msg

    # 链上只剩未就绪的家 → 「无可用检索服务」，零请求
    router2 = _Router()
    with pytest.raises(ValueError, match="^web_search 无可用检索服务：tavily: 需要环境变量 TAVILY_API_KEY"):
        search_web("frieren", config=_cfg(TAVILY_UNSET), opener=router2)
    assert router2.calls == {"exa_mcp": 0, "tavily": 0}


def test_tp9_web_key_env_names_reads_no_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2.7 第 1 条：只回答名字、保持链序，不读环境变量。"""
    monkeypatch.setattr(web.os.environ, "get", lambda *a, **k: pytest.fail("读了环境变量"))
    root = _write_cfg(
        tmp_path,
        {"timeout_s": 20, "providers": [{"name": "exa_mcp"}, {"name": "tavily", "api_key_env": "TAVILY_API_KEY"}]},
    )
    assert web_key_env_names(root) == ["TAVILY_API_KEY"]


# ——— T-P10：归一化 ———


def test_tp10_normalisation() -> None:
    """T-P10（M8）：snippet 封顶 500；URL 去重；javascript: 丢弃。"""
    long = "长" * 900
    body = _exa_sse(
        [
            ("A", "https://a.example/", long),
            ("A dup", "https://a.example/", "dup"),
            ("JS", "javascript:alert(1)", "x"),
            ("B", "https://b.example/", "b"),
        ]
    )
    out = search_web("frieren", config=_cfg(EXA), opener=_Router(exa_mcp=body))
    assert [r["url"] for r in out["results"]] == ["https://a.example/", "https://b.example/"]
    assert out["results"][0]["snippet"] == "长" * SEARCH_SNIPPET_MAX_CHARS
    assert SEARCH_SNIPPET_MAX_CHARS == 500
    assert out["truncated"] is False

    only_js = _exa_sse([("JS", "javascript:alert(1)", "x")])
    with pytest.raises(ValueError, match="解析为空"):
        search_web("frieren", config=_cfg(EXA), opener=_Router(exa_mcp=only_js))


# ——— T-P11：_guard_url 每家生效 ———


def test_tp11_guard_url_per_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P11（M9）：provider 端点解析到 127.0.0.1 → PermissionError，opener 零调用。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    for cfg in (DEFAULT_CHAIN, _cfg(TAVILY_UNSET, TAVILY)):
        router = _Router(exa_mcp=EXA_OK, tavily=TAVILY_OK)
        with pytest.raises(PermissionError, match="内网或保留地址"):
            search_web("frieren", config=cfg, opener=router)
        assert router.calls == {"exa_mcp": 0, "tavily": 0}


# ——— T-P12：退役守卫 + 依赖白名单（门禁 3） ———


def test_tp12_retired_parser_and_stdlib_only() -> None:
    src_path = Path(web.__file__)
    src = src_path.read_text(encoding="utf-8")
    assert "_SearchResultParser" not in src
    assert "result__a" not in src
    assert "_decode_ddg_href" not in src
    tree = ast.parse(src)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    import sys

    third_party = {m for m in top_level if m not in sys.stdlib_module_names and m not in ("pipeline", "__future__")}
    assert third_party == set()


# ——— T-P14：重定向 ———


def test_tp14a_key_header_is_unredirected() -> None:
    """T-P14a（M15）：密钥只在 unredirected_hdrs；exa_mcp 不带任何鉴权头。"""
    req = web._build_tavily("https://api.tavily.com/search", "q", 6, TAVILY)
    assert req.unredirected_hdrs.get("Authorization") == f"Bearer {FAKE_KEY}"
    assert "Authorization" not in req.headers
    assert all(FAKE_KEY not in v for v in req.headers.values())
    exa_req = web._build_exa_mcp("https://mcp.exa.ai/mcp?tools=web_search_exa", "q", 6, EXA)
    assert "Authorization" not in exa_req.headers and not exa_req.unredirected_hdrs


def test_tp14b_provider_opener_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P14b（M16）：127.0.0.1 上 A 回 302 指向 B → HTTPError 302 浮出，B 零命中。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(web, "_PROVIDER_DIRECTOR", None)
    b_hits: list[str] = []

    class _B(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            b_hits.append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET  # noqa: N815

        def log_message(self, *args: Any) -> None:
            pass

    server_b = http.server.HTTPServer(("127.0.0.1", 0), _B)
    target = f"http://127.0.0.1:{server_b.server_port}/landing"

    class _A(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server_a = http.server.HTTPServer(("127.0.0.1", 0), _A)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (server_a, server_b)]
    for t in threads:
        t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_a.server_port}/search", data=b"{}", method="POST"
        )
        req.add_unredirected_header("Authorization", f"Bearer {FAKE_KEY}")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            web._provider_opener(req, timeout=5)
        assert exc_info.value.code == 302
        assert b_hits == []
    finally:
        for s in (server_a, server_b):
            s.shutdown()
            s.server_close()

    # search_web 把 3xx 归为可落下一家（回到公网 DNS 钉，零真实解析）
    _pin(monkeypatch)
    router = _Router(exa_mcp=_http_error(302), tavily=TAVILY_OK)
    assert search_web("frieren", config=DEFAULT_CHAIN, opener=router)["provider"] == "tavily"
    router2 = _Router(exa_mcp=_http_error(302), tavily=_http_error(500))
    with pytest.raises(ValueError, match=r"exa_mcp: HTTP 302 重定向（未跟随）"):
        search_web("frieren", config=DEFAULT_CHAIN, opener=router2)


# ——— T-P15：MCP 带内错误 ———


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("exa_mcp_iserror_unknown_tool.sse.txt", "带内错误：MCP error -32602: Tool no_such_tool not found"),
        ("exa_mcp_rpc_error.sse.txt", "带内错误：Method not found"),
        ("exa_mcp_iserror_validation.sse.txt", "带内错误：MCP error -32602: Input validation error"),
    ],
)
def test_tp15_in_band_error_falls_through(fixture: str, expected: str) -> None:
    """T-P15（M17）：HTTP 200 + error / isError → 落到 tavily；全链失败时原因是带内文案而非「解析为空」。"""
    body = (FIXTURES / fixture).read_bytes()
    router = _Router(exa_mcp=body, tavily=TAVILY_OK)
    assert search_web("frieren", config=DEFAULT_CHAIN, opener=router)["provider"] == "tavily"

    router2 = _Router(exa_mcp=body, tavily=_http_error(500))
    with pytest.raises(ValueError) as exc_info:
        search_web("frieren", config=DEFAULT_CHAIN, opener=router2)
    msg = str(exc_info.value)
    assert f"exa_mcp: {expected}" in msg
    assert "exa_mcp: 解析为空" not in msg
    reason = msg.split("exa_mcp: ", 1)[1].split("；tavily:", 1)[0]
    assert len(reason) <= len("带内错误：") + 200


def test_tp15_in_band_text_never_echoes_query() -> None:
    """T-P15（R2-6）：带内文案回显原始或归一后的 query → 换成 <query>。"""
    query = "secret%20plan 一色彩羽"
    text = f"Rate limit exceeded for query 'secret%20plan 一色彩羽' (normalised: 'secret plan 一色彩羽'), key {FAKE_KEY}"
    router = _Router(exa_mcp=_exa_in_band(text), tavily=_http_error(500))
    with pytest.raises(ValueError) as exc_info:
        search_web(query, config=DEFAULT_CHAIN, opener=router)
    msg = str(exc_info.value)
    assert "secret plan" not in msg and "secret%20plan" not in msg
    assert msg.count("<query>") == 2
    assert FAKE_KEY not in msg


def test_sent_url_is_the_checked_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """发送地址 = egress 断言与 _guard_url 校验的地址（端点只在注册表里写一处）。"""
    import dataclasses

    for name in ("exa_mcp", "tavily"):
        moved = "https://moved.example/" + name
        monkeypatch.setitem(
            web._PROVIDERS, name, dataclasses.replace(web._PROVIDERS[name], endpoint=moved)
        )
    sent: list[str] = []
    checked: list[str] = []
    real = web._guard_url
    monkeypatch.setattr(web, "_guard_url", lambda url, **kw: (checked.append(url), real(url, **kw)))

    def opener(req: Any, timeout: float = 20.0) -> _Resp:
        sent.append(req.full_url)
        raise _http_error(500)

    with pytest.raises(ValueError, match="全部检索服务失败"):
        search_web("frieren", config=DEFAULT_CHAIN, opener=opener)
    assert sent == checked == ["https://moved.example/exa_mcp", "https://moved.example/tavily"]


# ——— T-P16：limit 映射与 truncated ———


@pytest.mark.parametrize("limit", [1, 5, 10])
def test_tp16_request_count_is_limit_plus_one(limit: int) -> None:
    """T-P16（M23）：请求条数 = limit + 1；返回 limit + 1 条 → 截到 limit 且 truncated。"""
    router = _Router(exa_mcp=_http_error(429), tavily=_tavily(limit + 1))
    out = search_web("frieren", limit=limit, config=DEFAULT_CHAIN, opener=router)
    exa_body = json.loads(router.requests[0].data)
    tavily_body = json.loads(router.requests[1].data)
    assert exa_body["params"]["arguments"]["numResults"] == limit + 1
    assert tavily_body["max_results"] == limit + 1
    assert len(out["results"]) == limit
    assert out["truncated"] is True

    exact = search_web("frieren", limit=limit, config=_cfg(TAVILY), opener=_Router(tavily=_tavily(limit)))
    assert len(exact["results"]) == limit
    assert exact["truncated"] is False


def test_exa_mcp_request_shape() -> None:
    """请求形态与 PR0 实测一致；objective 为固定通用文案（§12 偏差 1），不含 query。"""
    req = web._build_exa_mcp(web._PROVIDERS["exa_mcp"].endpoint, "一色彩羽", 6, EXA)
    body = json.loads(req.data)
    assert req.get_method() == "POST"
    assert req.full_url == "https://mcp.exa.ai/mcp?tools=web_search_exa"
    assert req.headers["Accept"] == "application/json, text/event-stream"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "web_search_exa"
    assert body["params"]["arguments"] == {
        "query": "一色彩羽",
        "numResults": 6,
        "objective": web.EXA_MCP_OBJECTIVE,
    }
    tav = json.loads(web._build_tavily(web._PROVIDERS["tavily"].endpoint, "一色彩羽", 6, TAVILY).data)
    assert tav == {"query": "一色彩羽", "max_results": 6, "search_depth": "basic"}


# ——— T-P17：本机形态配置 ———


def test_tp17_local_shape_config(tmp_path: Path) -> None:
    """T-P17（R2-1）：本机形态 web.local.json（search 为新链）可读、fetch 可用；删 search 段 → None。"""
    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "web.json").write_text(
        json.dumps(
            {
                "search": {"timeout_s": 20, "providers": [{"name": "exa_mcp"}]},
                "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
            }
        ),
        encoding="utf-8",
    )
    local = {
        "search": {
            "timeout_s": 20,
            "providers": [{"name": "exa_mcp"}, {"name": "tavily", "api_key_env": "TAVILY_API_KEY"}],
        },
        "fetch": {"timeout_s": 30, "max_bytes": 1000000, "max_chars": 30000},
        "trusted_fake_ip_ranges": ["198.18.0.0/15", "2001:2::/48"],
        "crawl": {"timeout_s": 60, "max_chars": 30000},
        "browser": {"profile_dir": "data/browser-profile", "headed": True, "timeout_s": 60},
    }
    (cfg_dir / "web.local.json").write_text(json.dumps(local), encoding="utf-8")
    cfg = load_web_config(tmp_path)
    assert cfg is not None
    assert [p.name for p in cfg.search_providers] == ["exa_mcp", "tavily"]
    assert len(cfg.trusted_fake_ip_ranges) == 2
    out = fetch_web(
        "https://example.org/x",
        root=tmp_path,
        opener=lambda req, timeout=30.0: _Resp(b"<html><body>ok</body></html>", "text/html"),
    )
    assert out["text"] == "ok"

    del local["search"]
    (cfg_dir / "web.local.json").write_text(json.dumps(local), encoding="utf-8")
    assert load_web_config(tmp_path) is None
    with pytest.raises(ValueError, match="缺少或无效"):
        fetch_web("https://example.org/x", root=tmp_path, opener=lambda *a, **k: None)


def test_repo_default_chain_is_valid(tmp_path: Path) -> None:
    """入库的 config/agent/web.json 默认链 = exa_mcp → tavily（§2.1 人拍板），能被新 loader 读出。"""
    from pipeline import paths

    cfg_dir = tmp_path / "config" / "agent"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "web.json").write_text(
        (paths.ROOT / "config" / "agent" / "web.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    cfg = load_web_config(tmp_path)
    assert cfg is not None
    assert [(p.name, p.api_key_env) for p in cfg.search_providers] == [
        ("exa_mcp", ""),
        ("tavily", "TAVILY_API_KEY"),
    ]
    assert web_key_env_names(tmp_path) == ["TAVILY_API_KEY"]
