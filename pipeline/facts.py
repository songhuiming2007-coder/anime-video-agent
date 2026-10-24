"""事实库：结构化事实条目 + 写入时机器校验（D99 PR-2，施工图 docs/dev/plans/2026-10-15-fact-gate-spec.md）。

每个卷 / 期 / 企划目录一份 `facts.jsonl`（权威，一行一条），`facts.md` 是从它生成的阅读版。
写入走 agent 工具 `fact_add`（→ `add`）：每条摘录必须出现在它声称的 URL 的抓取留档里
（`pipeline/agent/fetch_log.py`），级别由系统定、模型只能提议；升「一手 / 二手」只能由人（`promote`）。

比对规则（§二 实测：卷一事实文件 65 条摘录、16 个出处，用 AVA 自己的 web_fetch / crawl 重抓）：
- 逐字（L4q）：两边都做 NFKC → 去 markdown 标记 → 去全部空白 → 引号括号归一（『』“”"'‘’ → 「」）。
  被归一化掉的只有排版：实测 6 条摘录把原文的「」抄成『』、2 条带了作者自加的粗体、fetch 文本在行内标签处多空格。
- 近似（L5）：只留字母、数字、组合附标。实测只在这一级命中的 2–3 条全是抄写时动过原文（加说话人冒号、删脚注号）。
  不判通过——判据 5：问号与句号的差别（问句被写成陈述）正好在被丢掉的标点里。
- 都不命中：只能以「未核实」写入。

用法（人用的子命令；`promote` 进人工门禁，ava 的 bash 碰它弹卡）：
    python -m pipeline.facts check <目录>                  # 按留档重做每条校验；手改过的条目在这里现形
    python -m pipeline.facts render <目录>                 # 重新生成 facts.md
    python -m pipeline.facts add <目录> <条目.json> [--caller claude-code]   # 别的 agent 写事实（D136）
    python -m pipeline.facts promote <目录> --level 一手 (--ids F001,F003 | --url <URL>)
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import fcntl
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from pipeline import paths
from pipeline.agent import fetch_log

FACTS_FILE = "facts.jsonl"
RENDER_FILE = "facts.md"
LEVELS = ("一手", "二手", "待核", "未核实")
HUMAN_LEVELS = ("一手", "二手")
PROPOSABLE = ("一手", "二手", "未核实")
_QUOTES = str.maketrans({c: "「" for c in "『“\"‘'"} | {c: "」" for c in "』”’"})
# 原文里最接近的片段给模型对照时，前后多带的字数：够看出差在哪，又不把整页塞回上下文
NEAR_PAD = 20


def _strip_md(s: str) -> str:
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"(\*\*|__|\*|`|~~)", "", s).replace("\\", "")


def norm_exact(s: str) -> str:
    """L4q：NFKC → 去 markdown → 去空白 → 引号括号归一。"""
    return re.sub(r"\s+", "", _strip_md(unicodedata.normalize("NFKC", s))).translate(_QUOTES)


def norm_letters(s: str) -> str:
    """L5：只留字母、数字、组合附标。"""
    return "".join(c for c in unicodedata.normalize("NFKC", _strip_md(s)) if unicodedata.category(c)[0] in "LNM")


def check_excerpt(excerpt: str, text: str) -> tuple[str, str]:
    """→ (match, near)。match ∈ exact / approx / none；near 是 L4q 视图下原文里最接近的片段（exact 时为空）。"""
    ex, tx = norm_exact(excerpt), norm_exact(text)
    if ex and ex in tx:
        return "exact", ""
    sm = difflib.SequenceMatcher(None, ex, tx, autojunk=False)
    m = sm.find_longest_match(0, len(ex), 0, len(tx))
    start = max(0, m.b - m.a - NEAR_PAD)
    near = tx[start:start + len(ex) + 2 * NEAR_PAD]
    lx = norm_letters(excerpt)
    if lx and lx in norm_letters(text):
        return "approx", near
    return "none", near


_RANK = {"exact": 0, "approx": 1, "none": 2}


def _verify(url: str, excerpts: list[str], root: Path | None,
            fetcher: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """每条摘录在该 URL 任一份留档里取最好的命中；整条取最差的那条摘录。人裁决：跨会话的留档也算。

    D136：给了 fetcher（写入时）且这个 URL 没有留档，先由系统当场抓一次再比对；重做校验（check / promote）
    不给，只认已有留档。
    """
    records = fetch_log.lookup(url, root=root) if url else []
    fetch_error = None
    if not records and url and fetcher is not None:
        fetch_error = fetcher(url)
        records = fetch_log.lookup(url, root=root)
    if not records:
        reason = ("这个 URL 没有抓取留档，系统当场抓取也失败：" + fetch_error
                  + "；要登录态的页先用 AVA 的 browser 或 `python -m pipeline.agent.fetch <url> --browser` 抓一次"
                  if fetch_error else "这个 URL 没有抓取留档：先用 web_fetch / crawl / browser 打开它，再写这条")
        return {"match": "none", "record": None, "excerpts": [], "reason": reason}
    texts = []
    for r in records:
        try:
            texts.append((r, fetch_log.read_text(r, root=root)))
        except (ValueError, OSError):
            continue  # 被改过或读不到的留档不作依据
    if not texts:
        return {"match": "none", "record": None, "excerpts": [], "reason": "这个 URL 的留档都读不了或被改过"}
    per: list[dict[str, Any]] = []
    for ex in excerpts:
        best = min(((check_excerpt(ex, t), r) for r, t in texts), key=lambda x: _RANK[x[0][0]])
        (match, near), rec = best
        per.append({"match": match, "record": rec["id"], "origin": fetch_log.origin_label(rec),
                    **({"near": near} if match != "exact" else {})})
    worst = max(per, key=lambda p: _RANK[p["match"]])["match"] if per else "none"
    return {"match": worst, "record": per[0]["record"] if per else None,
            "origin": per[0]["origin"] if per else None, "excerpts": per,
            **({} if per else {"reason": "没有原文摘录"})}


def _level_for(proposed: str, match: str) -> str:
    """系统定级：模型提「未核实」就是未核实；逐字 / 近似通过一律先落「待核」，等人升级；不通过只能未核实。"""
    if proposed == "未核实" or match == "none":
        return "未核实"
    return "待核"


def facts_path(target: Path) -> Path:
    return Path(target) / FACTS_FILE


def load(target: Path) -> list[dict[str, Any]]:
    p = facts_path(target)
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{p} 第 {i} 行不是合法 JSON：{e.msg}") from None
    return out


def _save(target: Path, entries: list[dict[str, Any]]) -> None:
    paths.atomic_write(facts_path(target), "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def _validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("每条事实必须是对象")
    claim = str(raw.get("claim") or "").strip()
    if not claim:
        raise ValueError("claim（说法）不能为空")
    url = str(raw.get("url") or "").strip()
    if url and urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"url 必须是 http(s)：{url!r}")
    excerpts = raw.get("excerpts") or []
    if not isinstance(excerpts, list) or not all(isinstance(x, str) and x.strip() for x in excerpts):
        raise ValueError("excerpts 必须是非空字符串数组（原文逐字摘录）")
    proposed = str(raw.get("proposed_level") or "未核实")
    if proposed not in PROPOSABLE:
        raise ValueError(f"proposed_level 只能是 {PROPOSABLE}")
    return {"claim": claim, "url": url, "excerpts": [x.strip() for x in excerpts],
            "proposed_level": proposed, "topic": str(raw.get("topic") or "").strip()}


def add(raw_entries: list[Any], *, target: Path, sid: str | None, root: Path | None = None,
        fetcher: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """校验并追加事实条目；逐条回执。整批在 facts.lock 内完成（编号不撞）。

    出处 URL 没有留档时由系统当场抓（D136，fetcher 缺省用 `pipeline.agent.fetch.system_fetcher`）。
    """
    target = Path(target)
    if not target.is_dir():
        raise ValueError(f"事实库所在目录不存在：{target}")
    if fetcher is None:
        from pipeline.agent.fetch import system_fetcher
        fetcher = system_fetcher(root)
    results: list[dict[str, Any]] = []
    lock = target / "facts.lock"
    with lock.open("a", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            entries = load(target)
            n = max((int(str(e.get("id", "F0"))[1:]) for e in entries if str(e.get("id", "")).startswith("F")), default=0)
            for raw in raw_entries:
                try:
                    e = _validate(raw)
                except ValueError as exc:
                    results.append({"ok": False, "error": str(exc)})
                    continue
                check = _verify(e["url"], e["excerpts"], root, fetcher=fetcher)
                n += 1
                entry = {"id": f"F{n:03d}", **e, "level": _level_for(e["proposed_level"], check["match"]),
                         "level_by": "system", "check": check,
                         "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "sid": sid}
                entries.append(entry)
                results.append({"ok": True, "id": entry["id"], "level": entry["level"], "match": check["match"],
                                "hint": _hint(entry)})
            _save(target, entries)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    render(target)
    return {"results": results, "file": str(facts_path(target).name), "view": RENDER_FILE}


def _hint(entry: dict[str, Any]) -> str:
    c = entry["check"]
    if c["match"] == "exact":
        return "摘录逐字在原文里；级别「待核」，一手 / 二手由人定"
    if c["match"] == "approx":
        bad = [p for p in c["excerpts"] if p["match"] != "exact"]
        return ("摘录只在去掉全部标点后才对得上原文（抄写时动过原文：加了冒号 / 删了脚注号 / 改了标点？）。"
                f"按原文逐字重抄再写一条；原文最接近的片段：{bad[0].get('near', '')}") if bad else ""
    if c.get("reason"):
        return c["reason"] + "；本条已按「未核实」写入"
    bad = [p for p in c["excerpts"] if p["match"] == "none"]
    return ("摘录不在这个 URL 的原文里（编的、拼接的、或挂错了页）；本条已按「未核实」写入。"
            f"原文最接近的片段：{bad[0].get('near', '') if bad else ''}")


def check(target: Path, root: Path | None = None) -> list[str]:
    """按留档重做每条校验，返回问题清单（空 = 全部对得上）。"""
    problems: list[str] = []
    for e in load(target):
        fid = e.get("id", "?")
        level = e.get("level")
        if level not in LEVELS:
            problems.append(f"{fid}：级别 {level!r} 不合法")
            continue
        if level in HUMAN_LEVELS and e.get("level_by") != "human":
            problems.append(f"{fid}：级别「{level}」不是人定的（level_by={e.get('level_by')!r}）——一手 / 二手只能经 promote")
        if level == "未核实":
            continue
        now = _verify(str(e.get("url") or ""), list(e.get("excerpts") or []), root)["match"]
        if now == "none":
            problems.append(f"{fid}：标「{level}」，但摘录现在对不上任何留档（被手改过，或留档没了）")
        elif now == "approx" and level in HUMAN_LEVELS:
            problems.append(f"{fid}：标「{level}」，但摘录只是近似命中")
    return problems


def promote(target: Path, *, level: str, ids: list[str] | None = None, url: str | None = None,
            root: Path | None = None) -> list[str]:
    """人把「待核」升为一手 / 二手（按条目号或按 URL 批量）。只升逐字命中的条目；返回升了哪些。"""
    if level not in HUMAN_LEVELS:
        raise ValueError(f"只能升为 {HUMAN_LEVELS}")
    if not ids and not url:
        raise ValueError("给 --ids 或 --url 之一")
    entries = load(target)
    done: list[str] = []
    want = fetch_log.norm_url(url) if url else None
    for e in entries:
        hit = (ids and e.get("id") in ids) or (want and fetch_log.norm_url(str(e.get("url") or "")) == want)
        if not hit:
            continue
        if e.get("level") not in ("待核", *HUMAN_LEVELS):
            continue
        if _verify(str(e.get("url") or ""), list(e.get("excerpts") or []), root)["match"] != "exact":
            continue  # 近似命中的先让 AVA 按原文重抄，不替它背书
        e["level"], e["level_by"] = level, "human"
        done.append(str(e["id"]))
    _save(target, entries)
    render(target)
    return done


def render(target: Path) -> Path:
    """facts.jsonl → facts.md（阅读版）。"""
    entries = load(target)
    lines = ["# 事实库（阅读版）", "", f"> 本文件由 `{FACTS_FILE}` 生成，手改无效。写入用 AVA 的 `fact_add` 或 "
             "`python -m pipeline.facts add`；一手 / 二手由人用 `python -m pipeline.facts promote` 定。", ""]
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_topic.setdefault(e.get("topic") or "（未分节）", []).append(e)
    mark = {"exact": "", "approx": "（摘录只近似命中）", "none": "（摘录对不上原文）"}
    for topic, items in by_topic.items():
        lines += [f"## {topic}", ""]
        for e in items:
            rv = (e.get("review") or {}).get("verdict")
            seen = f"（独立核对：{rv}）" if rv in ("部分", "不支持") else ""
            lines.append(f"- **{e['id']}**［{e['level']}］{e['claim']}{mark.get(e['check']['match'], '')}{seen}")
            if e.get("url"):
                origin = (e.get("check") or {}).get("origin")
                lines.append(f"  - 出处：{e['url']}" + (f"（留档：{origin}）" if origin else ""))
            for ex in e.get("excerpts") or []:
                lines.append(f"  > {ex}")
        lines.append("")
    out = Path(target) / RENDER_FILE
    paths.atomic_write(out, "\n".join(lines))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.facts", description="事实库：校验 / 生成阅读版 / 人工定级（D99）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "render"):
        sub.add_parser(name).add_argument("target", type=Path)
    # D136：别的 agent（Claude Code 等）写事实的入口，校验与 fact_add 同一套（出处页没留档就当场抓）
    pa = sub.add_parser("add")
    pa.add_argument("target", type=Path, help="卷 / 期 / 企划目录")
    pa.add_argument("entries", type=Path, help="JSON 数组文件，每条 {claim, url, excerpts, proposed_level?, topic?}")
    pa.add_argument("--caller", default="cli", help="调用方标签，记进条目的 sid（如 claude-code）")
    p = sub.add_parser("promote")
    p.add_argument("target", type=Path)
    p.add_argument("--level", required=True, choices=HUMAN_LEVELS)
    p.add_argument("--ids", default=None, help="逗号分隔的条目号，如 F001,F003")
    p.add_argument("--url", default=None, help="把这个 URL 下所有逐字命中的待核条目一起升级")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        problems = check(a.target)
        for line in problems:
            print("FAIL " + line)
        print(f"{len(load(a.target))} 条，{len(problems)} 处问题")
        return 1 if problems else 0
    if a.cmd == "render":
        print(render(a.target))
        return 0
    if a.cmd == "add":
        raw = json.loads(a.entries.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            print("FAIL entries 文件必须是非空 JSON 数组", file=sys.stderr)
            return 2
        out = add(raw, target=a.target, sid=f"cli:{a.caller.strip() or 'cli'}")
        for r in out["results"]:
            print(f"{r['id']} ［{r['level']}］{r['match']}　{r['hint']}" if r["ok"] else f"FAIL {r['error']}")
        return 0 if all(r["ok"] for r in out["results"]) else 1
    ids = [x.strip() for x in a.ids.split(",")] if a.ids else None
    done = promote(a.target, level=a.level, ids=ids, url=a.url)
    print(f"已升为「{a.level}」：{', '.join(done) or '无（只升逐字命中的待核条目）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
