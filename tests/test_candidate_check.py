"""D100 素材候选关卡（施工图 docs/dev/plans/2026-10-15-asset-candidate-gate-spec.md 第六节）。

要点：交人批的表里，链接、画质、尺寸、授权只能来自机器实测；核不上的进不了下载。
出网一律走注入的替身 runner，不碰真实网络。
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from pipeline import acquire as A
from pipeline import candidate_check as CC
from pipeline.agent import fetch_log, tools
from pipeline.agent.tools import ToolContext
from pipeline.agent.web import _extract_links, fetch_web
from tests.test_agent_web import _FakeResponse, _make_config, _pin_public_dns

PAGE = "https://supercell.jp/news/1"
IMG = "https://supercell.jp/img/full/melt.jpg"
COMMONS_FAKES = ["Tokyo_Night_Street_Winter.jpg", "Logic_Pro_X_screenshot.png", "Piano_Keys_Sheet_Music.jpg",
                 "Classroom_in_Japan.jpg", "Neumann_U87_microphone.jpg", "Tokyo_Big_Sight_Comiket.jpg"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "library" / "incoming").mkdir(parents=True)
    return tmp_path


def _png(w: int, h: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h)).save(buf, format="PNG")
    return buf.getvalue()


def _cand(url=IMG, found_on=PAGE, type_="scan", **kw) -> dict:
    return {"title": "メルト 原图", "url": url, "type": type_, "source": "官网", "why": "段 3 要的原投稿画面",
            "found_on": found_on, **kw}


class FakeRun:
    """按 argv 分派的出网替身：记下每次调用，返回预设结果。"""

    def __init__(self, *, image: bytes | None = None, image_rc: int = 0, ytdlp: dict | None = None,
                 ytdlp_rc: int = 0, head: str = "HTTP/2 200\ncontent-type: video/mp4\ncontent-length: 2097152\n",
                 wiki: dict | None = None):
        self.image, self.image_rc, self.ytdlp, self.ytdlp_rc, self.head, self.wiki = (
            image, image_rc, ytdlp, ytdlp_rc, head, wiki)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], binary: bool) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if "yt_dlp" in " ".join(argv) or argv[0].endswith("yt-dlp"):
            return subprocess.CompletedProcess(argv, self.ytdlp_rc, json.dumps(self.ytdlp or {}), "ERROR: x")
        if argv[0] == "curl" and "-r" in argv:
            return subprocess.CompletedProcess(argv, self.image_rc, self.image or b"", b"curl: (22) 404")
        if argv[0] == "curl" and "-sSIL" in argv:
            return subprocess.CompletedProcess(argv, 0, self.head, "")
        if argv[0] == "curl" and CC.WIKI_API in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.wiki or {}), "")
        raise AssertionError(f"未预期的出网：{argv}")


def _ytdlp():
    return ["yt-dlp"]


def _no_cookies(url):
    return None


def _record_page(root: Path, *, url=PAGE, text="正文", images=(), links=()):
    return fetch_log.record(url=url, final_url=None, kind="fetch", text=text, sid="s", root=root,
                            images=[{"url": u} for u in images], links=[{"url": u, "anchor": ""} for u in links])


# ---------------------------------------------------------------- A 抓取留档


class TestRefs:
    def test_留档存图片与链接列表_改过就拒读(self, root):
        _record_page(root, images=[IMG], links=["https://x.example/a"])
        e = fetch_log.lookup(PAGE, root=root)[0]
        refs = fetch_log.read_refs(e, root=root)
        assert [r["url"] for r in refs["images"]] == [IMG] and [r["url"] for r in refs["links"]] == ["https://x.example/a"]
        (fetch_log.log_dir(root) / e["refs_file"]).write_text('{"images": [], "links": []}', encoding="utf-8")
        with pytest.raises(ValueError, match="sha256"):
            fetch_log.read_refs(e, root=root)

    def test_旧记录没有refs回空列表(self, root):
        fetch_log.record(url=PAGE, final_url=None, kind="fetch", text="t", sid=None, root=root)
        e = fetch_log.lookup(PAGE, root=root)[0]
        assert "refs_file" not in e and fetch_log.read_refs(e, root=root) == {"images": [], "links": []}

    def test_留档链接保留无锚文本的原图链接_给模型的清单照旧丢(self):
        html = '<a href="/img/full/melt.jpg"><img src="/img/thumb.jpg"></a><a href="/about">关于</a>'
        assert [e["url"] for e in _extract_links(html, PAGE)] == ["https://supercell.jp/about"]
        kept = [e["url"] for e in _extract_links(html, PAGE, keep_unanchored=True)]
        assert IMG in kept and "https://supercell.jp/about" in kept

    def test_fetch_web的keep_full带完整列表_不受条数帽(self, monkeypatch):
        _pin_public_dns(monkeypatch)
        imgs = "".join(f'<img src="/i/{n}.jpg">' for n in range(80))
        body = f'<html><body>{imgs}<a href="/img/full/melt.jpg"><img src="/t.jpg"></a></body></html>'.encode()
        out = fetch_web(PAGE, config=_make_config(), keep_full=True,
                        opener=lambda req, timeout=30.0: _FakeResponse(body, final_url=PAGE))
        assert len(out["full_images"]) == 81 and out["images_truncated"] is True
        assert IMG in {e["url"] for e in out["full_links"]}
        assert IMG not in {e["url"] for e in out["links"]}, "给模型的链接清单照旧丢空锚"

    def test_工具包装层把列表进留档_不进返回值(self, root, monkeypatch):
        monkeypatch.setattr("pipeline.agent.web.fetch_web", lambda url, root=None, keep_full=False: {
            "url": url, "final_url": url, "text": "t", **({"full_text": "全文", "full_images": [{"url": IMG}],
                                                           "full_links": []} if keep_full else {})})
        res = tools._tool_web_fetch({"url": PAGE}, ToolContext(scope="creative", root=root, session_id="s"))
        assert not {"full_text", "full_images", "full_links"} & set(res)
        e = fetch_log.lookup(PAGE, root=root)[0]
        assert fetch_log.read_refs(e, root=root)["images"] == [{"url": IMG}]

    def test_crawl与browser的keep_full也带列表(self, monkeypatch, tmp_path):
        from pipeline.agent.web_browser import browser_action
        from pipeline.agent.web_crawl import CrawlSection, crawl_page
        from tests.test_agent_crawl_browser import _FakeBrowserSession, _make_full_web_root

        _pin_public_dns(monkeypatch)
        html = '<a href="/img/full/melt.jpg"><img src="/img/thumb.jpg"></a>'
        res = crawl_page(PAGE, "403", section=CrawlSection(timeout_s=30.0, max_chars=1000), keep_full=True,
                         crawl_fn=lambda url, *, stealth, timeout_s: {"final_url": url, "markdown": "m", "html": html})
        assert IMG in {e["url"] for e in res["full_links"]}
        assert "https://supercell.jp/img/thumb.jpg" in {e["url"] for e in res["full_images"]}

        class Sess(_FakeBrowserSession):
            def extract_text(self, *, timeout_s: float = 60.0):
                return {**super().extract_text(timeout_s=timeout_s), "html": html}

        root = _make_full_web_root(tmp_path)
        monkeypatch.setenv("AVA_EVENTS_ROOT", str(root / "data"))
        sess = Sess(final_url=PAGE)
        browser_action("navigate", "登录", url=PAGE, launch_fn=lambda **k: sess, root=root)
        out = browser_action("extract_text", "读", launch_fn=lambda **k: sess, root=root, keep_full=True)
        assert IMG in {e["url"] for e in out["full_links"]} and "html" not in out


# ---------------------------------------------------------------- B 字段


def test_候选字段_found_on必须是URL_旧条目没有不报错():
    from pipeline.candidates import load_candidates

    old = {"title": "t", "url": IMG, "type": "scan", "source": "s", "why": "w"}
    assert load_candidates(json.dumps([old, _cand(segment="3")]))
    with pytest.raises(SystemExit, match="found_on"):
        load_candidates(json.dumps([_cand(found_on="supercell 官网")]))


def test_工具缺found_on拒写(root):
    ctx = ToolContext(scope="asset", root=root, episode_dir=root / "data")
    c = _cand()
    c.pop("found_on")
    with pytest.raises(ValueError, match="found_on"):
        tools._tool_acquire_propose({"candidates": [c]}, ctx)
    assert "found_on" in tools.TOOL_SCHEMAS["acquire_propose"]["parameters"]["properties"]["candidates"]["items"][
        "required"]


# ---------------------------------------------------------------- C 出处


class TestProvenance:
    def test_url在found_on页的图片列表里_过(self, root):
        _record_page(root, images=[IMG])
        assert CC.check_provenance(_cand(), root=root)["ok"] is True

    def test_url在链接列表或正文里_过(self, root):
        _record_page(root, links=[IMG + "#frag"])
        assert CC.check_provenance(_cand(), root=root)["ok"] is True
        _record_page(root, url="https://b.example/p", text=f"原图在 {IMG} 下载")
        assert CC.check_provenance(_cand(found_on="https://b.example/p"), root=root)["ok"] is True

    def test_页上链接带utm跟踪参数_干净链接也算(self, root):
        """10-15 冒烟实测：Commons 文件页指向原图的链接带 utm_ 参数。其他查询参数照旧区分。"""
        _record_page(root, links=[IMG + "?utm_source=commons.wikimedia.org&utm_campaign=index"])
        assert CC.check_provenance(_cand(), root=root)["ok"] is True
        assert CC.check_provenance(_cand(url=IMG + "?utm_medium=x"), root=root)["ok"] is True
        _record_page(root, url="https://q.example/", links=["https://q.example/p?id=1"])
        assert CC.check_provenance(_cand(url="https://q.example/p?id=2", found_on="https://q.example/"),
                                   root=root)["ok"] is False

    def test_url只在别的页出现_不过(self, root):
        _record_page(root, url="https://other.example/", images=[IMG])
        _record_page(root, images=["https://supercell.jp/img/other.jpg"])
        r = CC.check_provenance(_cand(), root=root)
        assert r["ok"] is False and "不在 found_on 页" in r["detail"]

    def test_found_on没抓过_不过(self, root):
        r = CC.check_provenance(_cand(), root=root)
        assert r["ok"] is False and "没有抓取留档" in r["detail"]

    def test_url等于found_on且抓过_过(self, root):
        _record_page(root, url="https://www.nicovideo.jp/watch/sm1097445")
        c = _cand(url="https://www.nicovideo.jp/watch/sm1097445/#c", found_on="https://www.nicovideo.jp/watch/sm1097445")
        assert CC.check_provenance(c, root=root)["ok"] is True

    def test_没有found_on判出处未登记(self, root):
        c = _cand()
        c.pop("found_on")
        assert CC.check_provenance(c, root=root)["detail"].startswith("出处未登记")

    def test_留档被改过不作依据(self, root):
        _record_page(root, images=[IMG])
        e = fetch_log.lookup(PAGE, root=root)[0]
        (fetch_log.log_dir(root) / e["refs_file"]).write_text('{"images": [{"url": "%s"}]}' % IMG, encoding="utf-8")
        assert CC.check_provenance(_cand(), root=root)["ok"] is False


# ---------------------------------------------------------------- C 可得性 / 授权 / 门禁


class TestMeasure:
    def test_图片读宽高(self):
        m = CC.measure(_cand(), FakeRun(image=_png(2400, 1800)))
        assert (m["ok"], m["width"], m["height"]) == (True, 2400, 1800)

    def test_图片只取前256KB(self):
        run = FakeRun(image=_png(10, 10))
        CC.measure(_cand(), run)
        assert run.calls[0][run.calls[0].index("-r") + 1] == f"0-{256 * 1024 - 1}"

    def test_读不出尺寸写尺寸未知不猜(self):
        m = CC.measure(_cand(), FakeRun(image=b"\x00not an image"))
        assert m["ok"] is True and m["width"] is None and "尺寸未知" in m["detail"]

    def test_图片404打不开(self):
        m = CC.measure(_cand(), FakeRun(image_rc=22))
        assert m["ok"] is False and "打不开" in m["detail"]

    def test_视频页取最高分辨率时长大小(self):
        doc = {"title": "メルト", "duration": 312.4,
               "formats": [{"height": 480, "width": 854}, {"height": 1080, "width": 1920}],
               "requested_formats": [{"filesize": 40 * 1048576}, {"filesize_approx": 5 * 1048576}]}
        m = CC.measure(_cand(url="https://www.youtube.com/watch?v=x", type_="mv"), FakeRun(ytdlp=doc),
                       ytdlp=_ytdlp, cookies_for=_no_cookies)
        assert (m["height"], m["width"], m["duration"], m["size"]) == (1080, 1920, 312.4, 45 * 1048576)

    def test_视频页解析失败打不开(self):
        m = CC.measure(_cand(url="https://www.youtube.com/watch?v=x", type_="mv"), FakeRun(ytdlp_rc=1),
                       ytdlp=_ytdlp, cookies_for=_no_cookies)
        assert m["ok"] is False

    def test_其他直链看HTTP头(self):
        m = CC.measure(_cand(url="https://cdn.example.com/a.mp4", type_="live"), FakeRun())
        assert m["ok"] is True and m["size"] == 2097152 and m["ctype"] == "video/mp4"
        bad = CC.measure(_cand(url="https://cdn.example.com/a.mp4", type_="live"), FakeRun(head="HTTP/2 404\n"))
        assert bad["ok"] is False

    def test_非Wikimedia授权一律未核实(self):
        m = CC.measure(_cand(license="CC0", 授权="CC0"), FakeRun(image=_png(2000, 2000)))
        assert m.get("license") is None and m.get("license_source") is None

    def test_Wikimedia授权取API值(self):
        wiki = {"query": {"pages": {"5": {"imageinfo": [{
            "width": 4000, "height": 3000, "url": "https://upload.wikimedia.org/a/ab/X.jpg",
            "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "Artist": {"value": "<a>某人</a>"}}}]}}}}
        m = CC.measure(_cand(url="https://commons.wikimedia.org/wiki/File:X.jpg"), FakeRun(wiki=wiki))
        assert (m["ok"], m["width"], m["license"], m["artist"]) == (True, 4000, "CC BY-SA 4.0", "某人")

    def test_Wikimedia文件不存在打不开(self):
        wiki = {"query": {"pages": {"-1": {"title": "File:X.jpg", "missing": ""}}}}
        m = CC.measure(_cand(url="https://commons.wikimedia.org/wiki/File:X.jpg"), FakeRun(wiki=wiki))
        assert m["ok"] is False and "没有这个文件" in m["detail"]

    @pytest.mark.parametrize("url,title", [
        ("https://commons.wikimedia.org/wiki/File:Tokyo_Night.jpg", "File:Tokyo_Night.jpg"),
        ("https://upload.wikimedia.org/wikipedia/commons/a/ab/Tokyo_Night.jpg", "File:Tokyo_Night.jpg"),
        ("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tokyo_Night.jpg/800px-Tokyo_Night.jpg",
         "File:Tokyo_Night.jpg"),
        ("https://example.com/File:X.jpg", None),
    ])
    def test_Wikimedia文件名(self, url, title):
        assert CC.wiki_file_title(url) == title

    def test_门禁预判_scan转视频要放大(self):
        assert CC.gate_reject(_cand(), {"width": 1920, "height": 1080}) == "转视频要放大 1.15 倍（上限 1.00）"
        assert CC.gate_reject(_cand(), {"width": 1500, "height": 2400}) is None   # 竖图缩进安全框，是缩小
        assert CC.gate_reject(_cand(type_="mv"), {"width": 640, "height": 360}) is None
        assert CC.gate_reject(_cand(), {"width": None, "height": None}) is None


class TestVerdict:
    def test_四种结论与优先级(self, root):
        _record_page(root, images=[IMG])
        ok = CC.check_one(1, _cand(), root=root, run=FakeRun(image=_png(2400, 1800)))
        assert ok["verdict"] == "可下载" and ok["reasons"] == []
        small = CC.check_one(1, _cand(), root=root, run=FakeRun(image=_png(800, 600)))
        assert small["verdict"] == "门禁会拒"
        nowhere = CC.check_one(1, _cand(found_on="https://never.example/"), root=root, run=FakeRun(image=_png(800, 600)))
        assert nowhere["verdict"] == "出处核不上" and len(nowhere["reasons"]) == 2
        dead = CC.check_one(1, _cand(found_on="https://never.example/"), root=root, run=FakeRun(image_rc=22))
        assert dead["verdict"] == "打不开"

    def test_真实回放_10月15日的6个Commons文件名全部打不开(self, root):
        missing = {"query": {"pages": {"-1": {"missing": ""}}}}
        pairs = [(i, _cand(url=f"https://commons.wikimedia.org/wiki/File:{n}", found_on="https://commons.wikimedia.org/",
                           license="CC0")) for i, n in enumerate(COMMONS_FAKES, 1)]
        recs = CC.check_batch(pairs, root=root, run=FakeRun(wiki=missing), workers=2)
        assert [r["verdict"] for r in recs] == ["打不开"] * 6
        assert all(r["license"] is None for r in recs), "模型写的 CC0 不进记录"

    def test_真实出处页的候选可下载(self, root):
        _record_page(root, images=[IMG])
        rec = CC.check_batch([(7, _cand())], root=root, run=FakeRun(image=_png(3000, 2000)))[0]
        assert rec["verdict"] == "可下载" and rec["measured"]["width"] == 3000 and rec["no"] == 7


# ---------------------------------------------------------------- 表与落盘


def test_机器表_统计_授权未核实_why标模型说明(root):
    _record_page(root, images=[IMG])
    c1 = _cand(segment="3")
    c2 = _cand(url="https://commons.wikimedia.org/wiki/File:X.jpg", license="CC0")
    r1 = CC.check_one(1, c1, root=root, run=FakeRun(image=_png(3000, 2000)))
    r2 = CC.check_one(2, c2, root=root, run=FakeRun(wiki={"query": {"pages": {"-1": {"missing": ""}}}}))
    table = CC.render_table([(c1, r1), (c2, r2)])
    assert "可下载 1 条；打不开 1" in table and "why 是模型说明" in table
    row1 = next(ln for ln in table.splitlines() if ln.startswith("| 1 |"))
    assert "| 3 |" in row1 and "3000×2000" in row1 and "未核实" in row1 and "抓取 " in row1
    assert "CC0" not in table, "模型写的授权不进表"
    row2 = next(ln for ln in table.splitlines() if ln.startswith("| 2 |"))
    assert "| 打不开：Commons 上没有这个文件" in row2 and "打不开：打不开" not in row2
    assert "尺寸未知" not in row2, "打不开的没有实测值"


def test_checks只追加_取最近一次(tmp_path):
    p = tmp_path / "checks.jsonl"
    CC.append_checks([{"url": IMG, "verdict": "打不开"}], p)
    CC.append_checks([{"url": IMG + "#x", "verdict": "可下载"}, {"url": "https://o.example", "verdict": "打不开"}], p)
    assert CC.latest_check(IMG, p)["verdict"] == "可下载"
    assert CC.latest_check("https://never.example", p) is None
    assert len(p.read_text(encoding="utf-8").splitlines()) == 3


# ---------------------------------------------------------------- D 流程：acquire check / fetch


@pytest.fixture
def repo(root, monkeypatch):
    from pipeline import paths

    inc = root / "data" / "library" / "incoming"
    cands = [_cand(), _cand(url="https://cdn.example.com/b.png", found_on="https://never.example/")]
    (inc / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
    (inc / "fetched.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(paths, "DATA", root / "data")
    _record_page(root, images=[IMG])
    return inc


def test_acquire_check写记录与机器表_末行REVIEW_FILE(repo, monkeypatch, capsys):
    monkeypatch.setattr(CC, "default_run", FakeRun(image=_png(3000, 2000)))
    assert A.cmd_check("all") == 0
    out = capsys.readouterr().out.strip().splitlines()
    review = Path(out[-1].split(" ", 1)[1])
    assert out[-1].startswith("REVIEW_FILE ") and review.parent == repo and review.name.startswith("review-")
    assert "| 1 |" in review.read_text(encoding="utf-8")
    verdicts = [json.loads(ln)["verdict"] for ln in (repo / "checks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert verdicts == ["可下载", "出处核不上"]


def test_fetch只抓可下载_其余说原因(repo, monkeypatch, capsys):
    CC.append_checks([{"url": IMG, "verdict": "可下载", "reasons": []},
                      {"url": "https://cdn.example.com/b.png", "verdict": "出处核不上",
                       "reasons": ["出处核不上：found_on 页没有抓取留档"]}], repo / "checks.jsonl")
    ran: list[list[str]] = []

    def fake(argv, *a, **k):
        ran.append(argv)
        Path(argv[argv.index("-o") + 1]).write_bytes(b"\0")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(A.subprocess, "run", fake)
    assert A.cmd_fetch("1,2") == 1
    out = capsys.readouterr().out
    assert len(ran) == 1 and ran[0][-1] == IMG
    assert "核对结论是「出处核不上」" in out


def test_fetch没核对过的拒并指路(repo, monkeypatch, capsys):
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("没核对过不该出网"))
    assert A.cmd_fetch("1") == 1
    assert "先 acquire check" in capsys.readouterr().out


def test_acquire_check经run_pipeline放行免卡(tmp_path):
    from pipeline.agent.session import review_tool_call
    from pipeline.agent.tools import validate_pipeline_command

    ok, msg, argv = validate_pipeline_command("acquire check 1,3")
    assert ok, msg
    assert argv[-3:] == ["pipeline.acquire", "check", "1,3"]
    ep = tmp_path / "data" / "episodes" / "01"
    ep.mkdir(parents=True)
    assert review_tool_call("run_pipeline", {"command": "acquire check 1"}, ep_dir=ep, scope="asset").action == "allow"
