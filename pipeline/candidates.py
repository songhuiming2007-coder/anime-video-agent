"""pipeline.candidates: 素材候选清单（candidates.json）的 schema 纯函数层与受控写入。

纪律：
1. 本模块是 candidates.json 的唯一程序化写入点（propose_candidates）；
2. 提案只是提案：本模块零网络能力（顶层无 urllib/socket/requests/subprocess），
   fetch 永远由人批准后走 pipeline.acquire；
3. append-only：不删、不改、不重排既有条目（fetch <N> 的 N 是数组序号）；
4. data/ 不可达即 PermissionError，绝不自动创建 data 根（paths.py:require_data 铁律）；
5. load_candidates 等纯函数自 acquire.py 物理下移（2026-09-23，Spec 6）：
   acquire.py 顶层经 ingest → subindex 拉 numpy/sentence_transformers，
   LLM 工具侧必须绕开该 import 链。
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import urlparse

from pipeline import paths

TYPES = ("live", "mv", "scan", "interview")


# ---- 自 acquire.py 原样下移（re-export 保持 acquire API 零回归）----


def incoming() -> Path:
    """人机交接目录 `data/library/incoming/`。

    走 `require_data()` 再建目录：T7 没挂时 `mkdir` 会在 /Volumes 下建出实体目录，
    几十 G 静默写进系统盘（数据与存储标准）。
    """
    root = paths.require_data()
    d = root / "library" / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def candidates_path() -> Path:
    return incoming() / "candidates.json"


def ledger_path() -> Path:
    return incoming() / "fetched.json"


def _line_of(raw: str, needle: str | None) -> int | None:
    """在原文里定位某个字符串所在行（1 起）。找不到返回 None。

    报错点行号是为了**能直接跳过去改**：这份文件是 agent 写的，说「第 3 条错了」
    等于让人自己数一遍。
    """
    if not needle:
        return None
    for i, ln in enumerate(raw.splitlines(), 1):
        if needle in ln:
            return i
    return None


def load_candidates(raw: str) -> list[dict]:
    """校验 candidates.json，坏条目**一次报全**并点名位置（E10：批量里一个坏不打断整批）。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"FAIL candidates.json 不是合法 JSON：第 {e.lineno} 行 {e.msg}")
    if not isinstance(data, list):
        raise SystemExit("FAIL candidates.json 顶层必须是数组（plan §6.1 的 schema）")

    bad: list[str] = []
    for i, c in enumerate(data, 1):
        line = _line_of(raw, (c.get("url") or c.get("title"))
                        if isinstance(c, dict) else None)
        where = f"第 {line} 行" if line else f"第 {i} 条"
        if not isinstance(c, dict):
            bad.append(f"  {where}：不是对象")
            continue
        missing = [k for k in ("title", "url", "type", "source", "why") if not c.get(k)]
        if missing:
            bad.append(f"  {where}（{c.get('title') or '无标题'}）：缺字段 {'/'.join(missing)}")
        if c.get("type") and c["type"] not in TYPES:
            bad.append(f"  {where}（{c.get('title')}）：type={c['type']!r} 不在 {TYPES}")
        if c.get("url") and urlparse(str(c["url"])).scheme not in ("http", "https"):
            bad.append(f"  {where}（{c.get('title')}）：url 不可解析 {c['url']!r}")
        if c.get("expected_dur") is not None and not isinstance(c["expected_dur"], (int, float)):
            bad.append(f"  {where}（{c.get('title')}）：expected_dur 须为秒数或 null")
    if bad:
        raise SystemExit("FAIL candidates.json 有问题的条目（修好再跑 fetch）：\n" + "\n".join(bad))
    return data


def slugify(title: str) -> str:
    """标题 → 安全的文件名前缀（英文/数字保留，其余折成 `_`）。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_")
    return (s or "asset")[:80]


def ledger_load(path: Path | None = None) -> list[dict]:
    """抓取台账。**为什么不用 sources.json 记 URL**：那张表是 ingest 的，按 SP 键存
    视音频规格、没有 url 字段，而且 register 是整条覆盖写入——往里塞自定义字段
    下次重登记就没了。所以 URL 历史放交接目录里，与 candidates.json 同源。"""
    p = path or ledger_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"FAIL 台账不是合法 JSON：{p}（修好或移走再跑）")


def ledger_save(entries: list[dict], path: Path | None = None) -> None:
    p = path or ledger_path()
    paths.atomic_write(p, json.dumps(entries, ensure_ascii=False, indent=2))


def ledger_seen_urls(entries: list[dict]) -> set[str]:
    return {e["url"] for e in entries if e.get("url")}


_PROPOSE_LOCK = threading.Lock()
_WHY_MIN_CHARS = 20  # skill §一 ✅ 示例均 ≥40 字、❌ 示例实测 17/4 字（len() 口径），20 是保守下界（软 WARN，不硬拒）


def propose_candidates(
    entries: list[dict[str, Any]],
    *,
    data_root: Path | str,
) -> dict[str, Any]:
    """受控追加素材候选到 incoming/candidates.json（唯一程序化写入点）。

    流程（全程在 candidates.lock 的 flock LOCK_EX 内）：
    1. data_root 可达性检查（悬空/骨架不全 → PermissionError，零 mkdir data 根）；
    2. incoming/ 不存在则 mkdir（data 可达前提下，acquire.incoming() 先例）；
    3. 双端 resolve：candidates.json 的 resolve 父目录必须等于 incoming 的 resolve
       （symlink 穿透 → PermissionError）；
    4. 读既有清单（缺失视为 []）与 fetched.json 台账（缺失视为 []）；
       台账损坏时 ledger_load raise SystemExit（candidates.py:ledger_load）——与 step 7 同挂
       catch SystemExit → ValueError 捕获面（🟡-8：捕获面覆盖 step 4/7 两处，
       漏包即穿透 tools.py:execute_tool 异常白名单杀掉 REPL）；
    5. URL 精确串双源判重，命中进 skipped；
    6. 新条目尾部追加（既有条目不删不改不重排）；
    7. 合并整卷过 load_candidates（catch SystemExit → ValueError，文件字节不动）；
    8. why 软 lint（<20 字 / 含「可能」→ warnings，不阻断）；
    9. paths.atomic_write 整卷覆写（indent=2, ensure_ascii=False）。

    返回 {"path", "added", "skipped", "warnings"}。
    只抛 PermissionError / ValueError / OSError（tools.py:execute_tool 异常白名单
    四件套内），绝不 SystemExit、绝不发起任何网络调用。
    """
    if not isinstance(entries, list) or not entries or not all(isinstance(c, dict) for c in entries):
        raise ValueError("candidates 必须是非空的对象数组")

    root = Path(data_root)
    if not root.exists() or not root.is_dir():
        raise PermissionError(f"data 根目录不可达: {root}（绝不自动创建 data 根）")
    lib_dir = root / "library"
    if not lib_dir.exists() or not lib_dir.is_dir():
        raise PermissionError(f"data/library 骨架不全: {lib_dir}")

    inc_dir = lib_dir / "incoming"
    inc_dir.mkdir(parents=True, exist_ok=True)
    cand_file = inc_dir / "candidates.json"

    lib_resolved = lib_dir.resolve()
    inc_resolved = inc_dir.resolve()
    cand_resolved = cand_file.resolve()
    if inc_resolved.parent != lib_resolved or cand_resolved.parent != inc_resolved:
        raise PermissionError(
            f"写入目标路径越界（symlink 穿透）: incoming={inc_resolved}, target={cand_resolved}"
        )

    lock_path = inc_dir / "candidates.lock"
    with _PROPOSE_LOCK:
        with lock_path.open("a", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                if cand_file.exists():
                    raw_existing = cand_file.read_text(encoding="utf-8")
                    try:
                        existing = json.loads(raw_existing)
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            f"FAIL candidates.json 不是合法 JSON：第 {e.lineno} 行 {e.msg}"
                        ) from e
                    if not isinstance(existing, list):
                        raise ValueError("FAIL candidates.json 顶层必须是数组（plan §6.1 的 schema）")
                else:
                    existing = []

                try:
                    ledger_entries = ledger_load(inc_dir / "fetched.json")
                except SystemExit as exc:
                    raise ValueError(str(exc)) from exc
                if not isinstance(ledger_entries, list) or not all(
                    isinstance(e, dict) for e in ledger_entries
                ):
                    raise ValueError("FAIL 台账顶层必须是对象数组")

                seen_in_candidates: set[str] = {
                    str(c.get("url")).strip()
                    for c in existing
                    if isinstance(c, dict) and c.get("url")
                }
                seen_in_ledger: set[str] = {
                    u.strip() for u in ledger_seen_urls(ledger_entries) if isinstance(u, str) and u.strip()
                }

                added: list[str] = []
                skipped: list[dict[str, str]] = []
                warnings: list[dict[str, str]] = []
                to_add: list[dict[str, Any]] = []

                for item in entries:
                    title_str = str(item.get("title") or "")
                    raw_url = item.get("url")
                    url_str = raw_url.strip() if isinstance(raw_url, str) else ""

                    if url_str and url_str in seen_in_candidates:
                        skipped.append({
                            "title": title_str,
                            "url": url_str,
                            "reason": "url 已在候选清单（candidates.json）",
                        })
                        continue
                    if url_str and url_str in seen_in_ledger:
                        skipped.append({
                            "title": title_str,
                            "url": url_str,
                            "reason": "url 已在抓取台账（fetched.json）",
                        })
                        continue

                    norm_item = dict(item)
                    if isinstance(raw_url, str) and url_str:
                        norm_item["url"] = url_str
                        seen_in_candidates.add(url_str)

                    to_add.append(norm_item)
                    added.append(title_str)

                    why_raw = str(norm_item.get("why") or "")
                    why_compact = re.sub(r"\s+", "", why_raw)
                    reasons: list[str] = []
                    if len(why_compact) < _WHY_MIN_CHARS:
                        reasons.append(f"why 仅 {len(why_compact)} 字（<{_WHY_MIN_CHARS}），说不清补哪个缺口")
                    if "可能" in why_raw:
                        reasons.append("why 含「可能」（违反判据 S7，须写实证依据）")
                    if reasons:
                        warnings.append({
                            "title": title_str,
                            "warning": "；".join(reasons) + "——按 skill §一 改写或删除",
                        })

                merged = list(existing) + to_add
                serialized = json.dumps(merged, ensure_ascii=False, indent=2)
                try:
                    load_candidates(serialized)
                except SystemExit as exc:
                    raise ValueError(str(exc)) from exc

                paths.atomic_write(cand_file, serialized)
                return {
                    "path": str(cand_file),
                    "added": added,
                    "skipped": skipped,
                    "warnings": warnings,
                }
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
