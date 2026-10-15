"""素材候选逐条机器核对（D100，施工图 docs/dev/plans/2026-10-15-asset-candidate-gate-spec.md）。

人看到的候选表里，链接、画质、时长、大小、授权只能来自这里的实测；模型能写的只剩 `why`。
每条核四项，结论四选一：

1. 出处：`found_on` 在抓取留档（`pipeline/agent/fetch_log.py`）里有记录，且 `url` 出现在那页的图片列表、
   链接列表或正文里；`url == found_on` 时只要求这页本身抓过。跨会话都算（与 D99 人定口径一致）。
2. 可得性：视频页走 `yt-dlp -J`（复用 `acquire.probe_argv`）；直链图片只取前 IMAGE_HEAD_BYTES 读宽高；
   其他直链看 HTTP 头。读不出尺寸写「尺寸未知」，不猜。
3. 授权：只有 Wikimedia 走 imageinfo API；其余一律「未核实」。模型写的授权不进表。
4. 门禁预判：scan 类短边 < `acquire.SCAN_MIN_SIDE` 判「门禁会拒」。

只被 `acquire check` 子命令调用（子进程里跑），LLM 工具进程不导入本模块：它出网，而且要用 acquire。
所有出网走注入的 `run`，测试换替身。
"""

from __future__ import annotations

import datetime
import fcntl
import io
import json
import re
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

VERDICTS = ("可下载", "打不开", "出处核不上", "门禁会拒")
# 结论优先级：同一条多项不过时取排前的。「打不开」排在「出处核不上」前：文件根本不存在是更基本的事实
# （10-15 那 6 个 Commons 文件名就是这种）
_PRECEDENCE = ("打不开", "出处核不上", "门禁会拒")

# JPEG 的尺寸在 SOF 段、PNG 在 IHDR，正常在文件开头几 KB；带大块 EXIF 或嵌入缩略图的 JPEG 会把 SOF
# 往后推，256 KB 留足余量。读不出就写「尺寸未知」，不下载全图（施工图第四节）
IMAGE_HEAD_BYTES = 256 * 1024
# 4 路并发：yt-dlp 单条解析 5–10 s（10-15 probe 9,13 实测），53 条串行要 5–9 分钟；4 路压到 2 分钟上下，
# 再多就对同一站点像批量爬取
CHECK_WORKERS = 4
HTTP_TIMEOUT_S = 30
WIKI_HOSTS = ("commons.wikimedia.org", "upload.wikimedia.org")
WIKI_API = "https://commons.wikimedia.org/w/api.php"

Runner = Callable[[list[str], bool], subprocess.CompletedProcess]


def default_run(argv: list[str], binary: bool) -> subprocess.CompletedProcess:
    from pipeline.acquire import PROBE_TIMEOUT_S

    try:
        return subprocess.run(argv, capture_output=True, text=not binary, timeout=PROBE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, b"" if binary else "", f"{PROBE_TIMEOUT_S}s 超时")


# ---------------------------------------------------------------- 出处


def _cmp_url(url: str) -> str:
    """出处比对口径：fetch_log.norm_url 再去掉 `utm_*` 跟踪参数。

    10-15 冒烟实测：Commons 文件页上指向原图的链接带 `?utm_source=commons.wikimedia.org&utm_campaign=…`，
    同一个文件的干净链接按 norm_url 比对不上。只丢 utm_ 开头的参数，其余查询串照旧区分（`?id=1` ≠ `?id=2`）。
    """
    from pipeline.agent.fetch_log import norm_url

    parts = urllib.parse.urlsplit(norm_url(url))
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def check_provenance(c: dict, *, root: Path | None) -> dict[str, Any]:
    """→ {ok, detail, record, ts}。只读抓取留档，不出网。"""
    from pipeline.agent import fetch_log

    found_on = str(c.get("found_on") or "").strip()
    if not found_on:
        return {"ok": False, "detail": "出处未登记（没有 found_on）", "record": None, "ts": None}
    entries = fetch_log.lookup(found_on, root=root)
    if not entries:
        return {"ok": False, "detail": f"found_on 页没有抓取留档：{found_on}", "record": None, "ts": None}
    want = _cmp_url(c["url"])
    if want == _cmp_url(found_on):
        e = entries[0]
        return {"ok": True, "detail": "链接本身抓过", "record": e.get("id"), "ts": e.get("ts")}
    for e in entries:
        try:
            refs = fetch_log.read_refs(e, root=root)
            text = fetch_log.read_text(e, root=root)
        except (ValueError, OSError):
            continue
        urls = {_cmp_url(r.get("url", "")) for r in refs["images"] + refs["links"]}
        if want in urls or str(c["url"]).strip() in text:
            return {"ok": True, "detail": "链接在出处页上", "record": e.get("id"), "ts": e.get("ts")}
    return {"ok": False, "detail": "url 不在 found_on 页的图片、链接或正文里", "record": entries[0].get("id"),
            "ts": entries[0].get("ts")}


# ---------------------------------------------------------------- 可得性


def wiki_file_title(url: str) -> str | None:
    """Wikimedia 链接 → `File:<名>`；不是 Wikimedia 返回 None。缩略图路径取原文件名。"""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in WIKI_HOSTS:
        return None
    path = urllib.parse.unquote(parts.path)
    if host == "commons.wikimedia.org":
        m = re.search(r"/wiki/(?:File|Image|文件):(.+)$", path)
        return f"File:{m.group(1)}" if m else None
    segs = [s for s in path.split("/") if s]
    if "thumb" in segs:  # /wikipedia/commons/thumb/a/ab/Name.jpg/800px-Name.jpg
        i = segs.index("thumb")
        return f"File:{segs[i + 3]}" if len(segs) > i + 3 else None
    return f"File:{segs[-1]}" if segs else None


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def wiki_imageinfo(title: str, run: Runner) -> dict[str, Any]:
    """Commons API → {ok, detail, width, height, license, artist, direct_url}。文件不存在 → ok False。"""
    q = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "imageinfo",
                                "iiprop": "size|url|extmetadata", "titles": title})
    proc = run(["curl", "-sS", "--max-time", str(HTTP_TIMEOUT_S), f"{WIKI_API}?{q}"], False)
    if proc.returncode != 0:
        return {"ok": False, "detail": f"Commons API 请求失败（curl 退出码 {proc.returncode}）"}
    try:
        pages = json.loads(proc.stdout)["query"]["pages"]
        page = next(iter(pages.values()))
    except (ValueError, KeyError, StopIteration, TypeError):
        return {"ok": False, "detail": "Commons API 返回无法解析"}
    if "missing" in page or not page.get("imageinfo"):
        return {"ok": False, "detail": f"Commons 上没有这个文件（{title}）"}
    info = page["imageinfo"][0]
    meta = info.get("extmetadata") or {}
    return {
        "ok": True, "detail": "Commons API 有此文件",
        "width": info.get("width"), "height": info.get("height"),
        "license": _strip_html((meta.get("LicenseShortName") or {}).get("value", "")) or None,
        "artist": _strip_html((meta.get("Artist") or {}).get("value", "")) or None,
        "direct_url": info.get("url"),
    }


def _measure_image(url: str, run: Runner) -> dict[str, Any]:
    proc = run(["curl", "-sSL", "-f", "--max-time", str(HTTP_TIMEOUT_S), "-r", f"0-{IMAGE_HEAD_BYTES - 1}", url], True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else str(proc.stderr or "")
        return {"ok": False, "detail": f"打不开（curl 退出码 {proc.returncode}）{err.strip()[:120]}"}
    try:
        from PIL import Image

        with Image.open(io.BytesIO(proc.stdout)) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 — Pillow 对截断 / 非图片抛的类型不固定；读不出就是「尺寸未知」
        return {"ok": True, "detail": "尺寸未知（前 256 KB 读不出宽高）", "width": None, "height": None}
    return {"ok": True, "detail": f"{w}×{h}", "width": w, "height": h}


def _measure_head(url: str, run: Runner) -> dict[str, Any]:
    proc = run(["curl", "-sSIL", "--max-time", str(HTTP_TIMEOUT_S), url], False)
    out = proc.stdout or ""
    status = re.findall(r"^HTTP/\S+\s+(\d+)", out, re.M)
    if proc.returncode != 0 or not status or status[-1][0] not in "23":
        return {"ok": False, "detail": f"打不开（HTTP {status[-1] if status else '无响应'}，curl 退出码 {proc.returncode}）"}
    ctype = re.findall(r"(?im)^content-type:\s*(\S+)", out)
    clen = re.findall(r"(?im)^content-length:\s*(\d+)", out)
    size = int(clen[-1]) if clen else None
    return {"ok": True, "detail": f"HTTP {status[-1]}" + (f"，{ctype[-1]}" if ctype else ""),
            "size": size, "ctype": ctype[-1] if ctype else None}


def _measure_video(c: dict, run: Runner, ytdlp: list[str], cookies: str | None) -> dict[str, Any]:
    from pipeline.acquire import probe_argv

    proc = run(probe_argv(c["url"], yt_dlp=ytdlp, cookies=cookies), False)
    if proc.returncode != 0:
        tail = " / ".join(str(proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
        return {"ok": False, "detail": f"打不开（yt-dlp 退出码 {proc.returncode}）{tail[:160]}"}
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return {"ok": False, "detail": "打不开（yt-dlp 输出不是 JSON）"}
    fmts = [f for f in doc.get("formats") or [] if isinstance(f, dict)]
    heights = [f["height"] for f in fmts if isinstance(f.get("height"), (int, float))]
    widths = [f["width"] for f in fmts if isinstance(f.get("width"), (int, float))]
    picked = doc.get("requested_formats") or [doc]
    size = sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in picked if isinstance(f, dict)) or None
    dur = doc.get("duration")
    return {"ok": True, "detail": f"《{doc.get('title', '—')}》",
            "height": int(max(heights)) if heights else None, "width": int(max(widths)) if widths else None,
            "duration": float(dur) if isinstance(dur, (int, float)) else None, "size": size}


def measure(c: dict, run: Runner, *, ytdlp: Callable[[], list[str]] | None = None,
            cookies_for: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """可得性实测 + 授权。Wikimedia 以 API 为准（宽高、授权、作者）；其余授权「未核实」。"""
    from pipeline.acquire import IMAGE_EXT, pick_fetcher

    url = c["url"]
    title = wiki_file_title(url)
    if title:
        info = wiki_imageinfo(title, run)
        out = {"kind": "wikimedia", **info}
        out["license_source"] = "Wikimedia API" if info.get("ok") else None
        return out
    path = urllib.parse.urlsplit(url).path.lower()
    if pick_fetcher(url) == "curl":
        if path.endswith(IMAGE_EXT):
            return {"kind": "image", "license": None, **_measure_image(url, run)}
        return {"kind": "direct", "license": None, **_measure_head(url, run)}
    argv = (ytdlp or _default_ytdlp)()
    cookies = (cookies_for or _default_cookies)(url)
    return {"kind": "video", "license": None, **_measure_video(c, run, argv, cookies)}


def _default_ytdlp() -> list[str]:
    from pipeline.acquire import yt_dlp_argv

    return yt_dlp_argv()


def _default_cookies(url: str) -> str | None:
    from pipeline.agent.web_browser import cookies_spec_for

    return cookies_spec_for(url)


# ---------------------------------------------------------------- 结论


def gate_reject(c: dict, m: dict) -> str | None:
    """门禁预判：scan 类短边不够，提前曝光，不等下载完才发现。"""
    from pipeline.acquire import SCAN_MIN_SIDE

    if c.get("type") != "scan" or not m.get("width") or not m.get("height"):
        return None
    short = min(int(m["width"]), int(m["height"]))
    return f"短边 {short} < {SCAN_MIN_SIDE}" if short < SCAN_MIN_SIDE else None


def verdict_of(prov: dict, m: dict, gate: str | None) -> tuple[str, list[str]]:
    reasons: dict[str, str] = {}
    if not m.get("ok"):
        reasons["打不开"] = str(m.get("detail"))
    if not prov.get("ok"):
        reasons["出处核不上"] = str(prov.get("detail"))
    if gate:
        reasons["门禁会拒"] = gate
    for v in _PRECEDENCE:
        if v in reasons:
            return v, [f"{k}：{r}" for k, r in reasons.items()]
    return "可下载", []


def check_one(no: int, c: dict, *, root: Path | None, run: Runner, ytdlp=None, cookies_for=None,
              now: str | None = None) -> dict[str, Any]:
    prov = check_provenance(c, root=root)
    m = measure(c, run, ytdlp=ytdlp, cookies_for=cookies_for)
    gate = gate_reject(c, m) if m.get("ok") else None
    verdict, reasons = verdict_of(prov, m, gate)
    return {
        "no": no, "url": c["url"], "ts": now or _now(), "verdict": verdict, "reasons": reasons,
        "provenance": {"found_on": c.get("found_on"), **prov},
        "measured": {k: m.get(k) for k in ("ok", "kind", "detail", "width", "height", "duration", "size", "ctype",
                                          "direct_url") if m.get(k) is not None},
        "license": ({"short": m.get("license"), "artist": m.get("artist"), "source": m.get("license_source")}
                    if m.get("license_source") else None),
    }


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def check_batch(pairs: list[tuple[int, dict]], *, root: Path | None, run: Runner | None = None,
                ytdlp=None, cookies_for=None, workers: int = CHECK_WORKERS) -> list[dict[str, Any]]:
    """逐条核对，结果按输入顺序。run 缺省时现取模块级 default_run。"""
    run = run or default_run
    now = _now()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(check_one, no, c, root=root, run=run, ytdlp=ytdlp, cookies_for=cookies_for, now=now)
                for no, c in pairs]
        return [f.result() for f in futs]


# ---------------------------------------------------------------- 落盘与读取


def append_checks(records: list[dict[str, Any]], path: Path) -> None:
    """按 URL 追加（candidates.json 只追加、不改既有条目的约定不动；核对结论另记一本）。"""
    with path.with_suffix(".lock").open("a", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            with path.open("a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def latest_check(url: str, path: Path) -> dict[str, Any] | None:
    """该 URL 最近一次核对；从没核过返回 None。坏行跳过。"""
    if not path.exists():
        return None
    from pipeline.agent.fetch_log import norm_url

    want = norm_url(url)
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and norm_url(r.get("url", "")) == want:
            last = r
    return last


# ---------------------------------------------------------------- 机器表

_KIND_LABEL = {"mv": "视频", "live": "视频", "interview": "视频", "scan": "图"}


def _cell(s: Any) -> str:
    return str(s if s is not None else "—").replace("|", "\\|").replace("\n", " ")


def _measured_cell(m: dict) -> str:
    if m.get("ok") is False:
        return "—"  # 打不开的没有实测值，不写「尺寸未知」
    bits = []
    if m.get("width") and m.get("height"):
        bits.append(f"{m['width']}×{m['height']}")
    elif m.get("height"):
        bits.append(f"最高 {m['height']}p")
    elif m.get("kind") in ("image", "wikimedia"):
        bits.append("尺寸未知")
    if m.get("duration"):
        bits.append(f"{int(m['duration'])}s")
    if m.get("size"):
        bits.append(f"{m['size'] / 1048576:.1f} MB")
    return "，".join(bits) or "—"


def render_table(rows: list[tuple[dict, dict]]) -> str:
    """(候选, 核对记录) → Markdown。表头先给统计。模型能写的只有最后一列 why，表里注明。"""
    counts = {v: 0 for v in VERDICTS}
    kinds: dict[str, int] = {}
    for c, r in rows:
        counts[r["verdict"]] += 1
        label = _KIND_LABEL.get(str(c.get("type")), "其他")
        kinds[label] = kinds.get(label, 0) + 1
    head = (f"**素材候选核对**（机器生成；链接、实测、授权、出处均为系统核对结果，why 是模型说明）\n\n"
            f"可下载 {counts['可下载']} 条；打不开 {counts['打不开']}、出处核不上 {counts['出处核不上']}、"
            f"门禁会拒 {counts['门禁会拒']}。按类型：" + "、".join(f"{k} {n}" for k, n in kinds.items()) + "。\n")
    lines = [head, "| 号 | 段 | 标题 | 类型 | 结论 | 实测 | 授权 | 出处页与抓取时间 | why（模型说明） |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c, r in rows:
        lic = r.get("license")
        lic_s = (f"{lic.get('short') or '无授权字段'}（{lic.get('artist') or '作者未列'}，Wikimedia API）"
                 if lic else "未核实")
        prov = r.get("provenance") or {}
        prov_s = f"{prov.get('found_on') or '未登记'}" + (f"（抓取 {prov['ts']}）" if prov.get("ts") else "")
        # reasons 本身以结论名打头、按优先级排（verdict_of），第一条就是结论
        verdict = "；".join(r["reasons"]) if r["reasons"] else r["verdict"]
        lines.append("| " + " | ".join(_cell(x) for x in (
            r["no"], c.get("segment"), c.get("title"), c.get("type"), verdict,
            _measured_cell(r.get("measured") or {}), lic_s, prov_s, c.get("why"))) + " |")
    return "\n".join(lines) + "\n"
