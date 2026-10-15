"""抓取全文留档（D99 PR-1，施工图 docs/dev/plans/2026-10-15-fact-gate-spec.md）。

web_fetch / crawl / browser(extract_text) 每抓一页，把**截断前**的全文（已过 `_scrub` / 密钥替换，
与 LLM 看到的同口径脱敏）落进 `data/library/fetch-log/`，并在 `index.jsonl` 记一行。事实条目写入时
（`fact_add`）按 URL 找这里的记录做摘录比对：「这页真的抓过、这句话真的在原文里」由系统核，不由模型自报。

为什么存截断前的全文：实测 ORICON 2009 存档 crawl 全文 50,817 字，超过 LLM 看到的 30,000 字上限；
只存截断版，摘录落在第 3 万字之后的真话会被判成编造。
为什么放 data/library/ 而不是会话目录：出处是全库共用的（同一篇采访多卷都引），人裁决「跨会话也算」
（2026-10-15），记录里只把会话号留作追溯。
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pipeline import paths

KINDS = ("fetch", "crawl", "browser")


def _data_root(root: Path | None) -> Path:
    data = Path(root or paths.ROOT) / "data"
    if not data.is_dir():
        raise ValueError(f"data/ 不可达（{data}）：插盘或 preflight --init（严禁自动创建 data/）")
    return data


def log_dir(root: Path | None = None) -> Path:
    return _data_root(root) / "library" / "fetch-log"


def norm_url(url: str) -> str:
    """比对用的 URL：去 fragment、scheme / host 小写、路径末尾的 `/` 去掉（根路径除外）。"""
    parts = urlsplit(str(url or "").strip())
    path = parts.path[:-1] if len(parts.path) > 1 and parts.path.endswith("/") else parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def record(*, url: str, final_url: str | None, kind: str, text: str, sid: str | None,
           root: Path | None = None) -> str:
    """落一份全文 + index 一行，返回记录号。"""
    if kind not in KINDS:
        raise ValueError(f"未知抓取类型 {kind!r}")
    base = log_dir(root)
    key = hashlib.sha256(norm_url(final_url or url).encode("utf-8")).hexdigest()[:16]
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rid = f"{key}-{ts}-{kind}"
    file = base / key / f"{ts}-{kind}.txt"
    file.parent.mkdir(parents=True, exist_ok=True)
    paths.atomic_write(file, text)
    entry = {
        "id": rid, "url": url, "final_url": final_url or url, "kind": kind, "sid": sid, "ts": ts,
        "chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "file": str(file.relative_to(base)),
    }
    with (base / "index.lock").open("a", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            with (base / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    return rid


def lookup(url: str, *, sid: str | None = None, root: Path | None = None) -> list[dict[str, Any]]:
    """按 URL（请求 URL 或重定向后的最终 URL）找留档，新的在前。`sid` 给了只回该会话的。坏行跳过。"""
    index = log_dir(root) / "index.jsonl"
    if not index.exists():
        return []
    want = norm_url(url)
    hits: list[dict[str, Any]] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict) or want not in (norm_url(e.get("url", "")), norm_url(e.get("final_url", ""))):
            continue
        if sid is not None and e.get("sid") != sid:
            continue
        hits.append(e)
    return sorted(hits, key=lambda e: str(e.get("ts", "")), reverse=True)


def read_text(entry: dict[str, Any], root: Path | None = None) -> str:
    """读一条记录的全文；文件被动过（sha256 对不上）就报错，不拿改过的原文做比对。"""
    base = log_dir(root)
    file = (base / str(entry["file"])).resolve()
    if not file.is_relative_to(base.resolve()):
        raise ValueError(f"留档路径越界：{entry['file']}")
    text = file.read_text(encoding="utf-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != entry.get("sha256"):
        raise ValueError(f"留档 {entry.get('id')} 的全文与登记的 sha256 不符（被改过），不能作比对依据")
    return text
