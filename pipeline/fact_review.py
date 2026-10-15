"""事实库独立核对（D99 PR-3，施工图 docs/dev/plans/2026-10-15-fact-gate-spec.md §三 PR-3）。

`fact_add` 已经机械地挡住了「没打开过的页」「不在原文里的摘录」「挂错页」三类错；剩下第 ④ 类——摘录是真的，
但「说法」把动机、因果、主语、时间、数字改写了——要「读懂」才能判，交给一个**看不到写稿对话**的核对员：
一次 LLM 调用，只拿每条的说法、摘录、以及摘录在留档原文里的上下文，逐条判「支持 / 部分 / 不支持」。

为什么另开文件而不是塞进 adversarial.py：02.8 审的是稿件对字幕 / 笔记的三类问题，输入、条目形状、报告都不同；
这里沿用它的纪律——不给工具、引用机械核对（核不上标 ⚠）、报告带终审表——但**每条都要判**，
通过的条目全部列出（判据 11 要求的通过清单，这里不抽样、给全量）；漏判的列为「漏判」，不当通过（判据 9）。

判「不支持」的条目：一手 / 二手回落「待核」（level_by=review），阅读版标出。

用法：python -m pipeline.fact_review <卷 / 期 / 企划目录>
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from pipeline import facts
from pipeline.agent import fetch_log

REPORT = "facts-review.md"
HISTORY_DIR = ("_agent", "fact-review-history")
RAW_REPLY = ("_agent", "fact-review-raw.txt")
VERDICTS = ("支持", "部分", "不支持")
# 摘录前后各带多少字原文：动机 / 因果常在摘录前一两句（如三轮采访里「别名义」的两层原因隔着一个问句），
# 300 字约三五句日文，够看前后文；再多就是整页
CONTEXT_PAD = 300
LLM_TIMEOUT_S = 240          # 与 02.8 同：输入几万字
MAX_INPUT_CHARS = 150_000    # 与 02.8 同一上限；超了报错停、不截断（截掉的可能正是被改写的那条）

SYSTEM_PROMPT = """你是独立的事实核对员。你没看过写稿过程，只拿到下面这些事实条目：每条有「说法」（写稿方的中文转述）、
「摘录」（从出处页逐字复制的原文）与「上下文」（摘录在出处原文里前后的文字）。

任务：逐条判断「说法」是否被摘录与上下文支持。重点查这几类改写：
- 动机、原因被换掉或加了原文没有的理由；
- 因果、先后顺序颠倒；
- 主语换人（谁说的、谁做的）；
- 时间、数字、名单与原文不符；
- 把提问者的话当成受访者的话。

每条都要判，不许跳过：
- 支持：说法的每个要点都能在摘录或上下文里找到；
- 部分：主体对，但有一处要点原文里没有或说过头了；
- 不支持：要点与原文矛盾，或主要内容原文里没有。

只输出一个 JSON 对象，不要别的文字：
{"items": [{"id": "F001", "verdict": "支持|部分|不支持", "reason": "一句话说清依据", "quote": "从该条摘录或上下文逐字复制的一小段，作依据"}]}"""


def _window(text: str, excerpt: str) -> str:
    """摘录在原文里的位置前后各 CONTEXT_PAD 字。定位用最长公共子串（原文与摘录空白、引号可能不同）。"""
    sm = difflib.SequenceMatcher(None, excerpt, text, autojunk=False)
    m = sm.find_longest_match(0, len(excerpt), 0, len(text))
    start = max(0, m.b - m.a - CONTEXT_PAD)
    return text[start:m.b - m.a + len(excerpt) + CONTEXT_PAD]


def gather(target: Path, root: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """待核对的条目（未核实的不审：它没有能核对的原文）+ 每条的上下文。返回 (条目, 缺口说明)。"""
    out: list[dict[str, Any]] = []
    gaps: list[str] = []
    for e in facts.load(target):
        if e.get("level") == "未核实":
            continue
        records = {r["id"]: r for r in fetch_log.lookup(str(e.get("url") or ""), root=root)}
        ctx: list[str] = []
        for i, ex in enumerate(e.get("excerpts") or []):
            per = (e.get("check") or {}).get("excerpts") or []
            rid = per[i].get("record") if i < len(per) else None
            rec = records.get(rid) if rid else next(iter(records.values()), None)
            try:
                ctx.append(_window(fetch_log.read_text(rec, root=root), ex) if rec else "")
            except (ValueError, OSError) as exc:
                ctx.append("")
                gaps.append(f"{e.get('id')}：第 {i + 1} 条摘录的留档读不了（{exc}），核对员只看到摘录本身")
        out.append({"id": e["id"], "claim": e.get("claim", ""), "url": e.get("url", ""),
                    "excerpts": list(e.get("excerpts") or []), "contexts": ctx})
    return out, gaps


def build_user_prompt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for it in items:
        lines = [f"### {it['id']}", f"说法：{it['claim']}", f"出处：{it['url']}"]
        for i, (ex, ctx) in enumerate(zip(it["excerpts"], it["contexts"]), 1):
            lines.append(f"摘录 {i}：{ex}")
            lines.append(f"上下文 {i}：{ctx or '（无留档原文）'}")
        blocks.append("\n".join(lines))
    return "## 待核对的事实条目\n\n" + "\n\n".join(blocks)


def parse_reply(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("核对员没有返回 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"核对员返回的 JSON 解析失败：{exc}") from None
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("核对员返回的 JSON 缺 items 列表")
    return [r for r in rows if isinstance(r, dict)]


def verify(rows: list[dict[str, Any]], items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """规范化回复并机械核对：id 要存在、判定要合法、依据要逐字出自该条的摘录或上下文。返回 (判定, 漏判 id)。"""
    by_id = {it["id"]: it for it in items}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        fid = str(r.get("id", "")).strip()
        row = {"id": fid, "verdict": str(r.get("verdict", "")).strip(), "reason": str(r.get("reason", "")).strip(),
               "quote": str(r.get("quote", "")).strip(), "flag": ""}
        problems = []
        it = by_id.get(fid)
        if it is None:
            problems.append("没有这一条")
        elif fid in seen:
            problems.append("重复判了同一条")
        if row["verdict"] not in VERDICTS:
            problems.append(f"判定「{row['verdict']}」不合法")
        if it is not None and row["quote"]:
            pool = facts.norm_exact("".join(it["excerpts"] + it["contexts"]))
            if facts.norm_exact(row["quote"]) not in pool:
                problems.append("依据不在这一条的摘录或上下文里")
        elif it is not None:
            problems.append("没给依据")
        row["flag"] = "；".join(problems)
        if it is not None and fid not in seen:
            seen.add(fid)
        out.append(row)
    missing = [it["id"] for it in items if it["id"] not in seen]
    return out, missing


def apply(target: Path, judged: list[dict[str, Any]], now: dt.datetime) -> list[str]:
    """判「不支持」且引用核得上的：一手 / 二手回落待核；所有判定写进条目的 review 字段。返回被降级的 id。"""
    entries = facts.load(target)
    by_id = {r["id"]: r for r in judged if not r["flag"]}
    demoted: list[str] = []
    for e in entries:
        r = by_id.get(e.get("id"))
        if r is None:
            continue
        e["review"] = {"verdict": r["verdict"], "reason": r["reason"], "ts": now.isoformat(timespec="seconds")}
        if r["verdict"] == "不支持" and e.get("level") in facts.HUMAN_LEVELS:
            e["level"], e["level_by"] = "待核", "review"
            demoted.append(str(e["id"]))
    facts._save(target, entries)
    facts.render(target)
    return demoted


def _cell(s: str) -> str:
    return s.replace("|", "｜").replace("\n", " ").strip() or "—"


def render_report(items: list[dict[str, Any]], judged: list[dict[str, Any]], missing: list[str], gaps: list[str],
                  demoted: list[str], model: str, now: dt.datetime) -> str:
    claim = {it["id"]: it["claim"] for it in items}
    ok = [r for r in judged if not r["flag"]]
    bad = [r for r in judged if r["flag"]]
    group = {v: [r for r in ok if r["verdict"] == v] for v in VERDICTS}
    lines = [
        "# 事实库独立核对", "",
        f"{now.strftime('%Y-%m-%d %H:%M')}，模型 {model}。核对员只看到每条的说法、摘录与留档原文上下文，**看不到写稿对话**。  ",
        f"共 {len(items)} 条：支持 {len(group['支持'])}、部分 {len(group['部分'])}、不支持 {len(group['不支持'])}"
        + (f"；漏判 {len(missing)} 条（不当通过）" if missing else "")
        + (f"；核对员引用核不上 {len(bad)} 条（标 ⚠，先别信）" if bad else "") + "。",
    ]
    if demoted:
        lines.append(f"已按「不支持」把 {'、'.join(demoted)} 从一手 / 二手回落为待核。")
    if gaps:
        lines += ["", "缺口："] + [f"- {g}" for g in gaps]

    def table(title: str, rows: list[dict[str, Any]], with_flag: bool = False) -> None:
        lines.extend(["", f"## {title}（{len(rows)} 条）", ""])
        if not rows:
            lines.append("无。")
            return
        lines.extend(["| 条目 | 说法 | 判定 | 理由 | 依据 |", "|---|---|---|---|---|"])
        for r in rows:
            reason = r["reason"] + (f"<br>⚠ {r['flag']}" if with_flag else "")
            lines.append(f"| {_cell(r['id'])} | {_cell(claim.get(r['id'], ''))} | {_cell(r['verdict'])} | {_cell(reason)} | {_cell(r['quote'])} |")

    table("不支持", group["不支持"])
    table("部分", group["部分"])
    table("支持（通过清单，全量）", group["支持"])
    if bad:
        table("核对员引用核不上", bad, with_flag=True)
    if missing:
        lines += ["", f"## 漏判（{len(missing)} 条）", "", "核对员没判这些条目；不算通过，重跑或由人看：" + "、".join(missing)]
    lines += ["", "## 终审裁决", "", "「不支持」「部分」逐条由写稿会话回原文查证后填「采纳 / 驳回 + 理由」；采纳的改说法后重新 fact_add。",
              "", "| 条目 | 裁决 | 理由 |", "|---|---|---|"]
    lines += [f"| {r['id']} |  |  |" for r in group["不支持"] + group["部分"] + bad]
    return "\n".join(lines) + "\n"


def review(target: Path, *, now: dt.datetime | None = None, root: Path | None = None) -> tuple[str, dict[str, Any]]:
    from pipeline.agent.llm import LLMError, chat_complete, load_llm_config

    now = now or dt.datetime.now()
    items, gaps = gather(target, root=root)
    if not items:
        raise ValueError("事实库里没有可核对的条目（未核实的不审）")
    user = build_user_prompt(items)
    if len(SYSTEM_PROMPT) + len(user) > MAX_INPUT_CHARS:
        raise ValueError(f"核对输入 {len(SYSTEM_PROMPT) + len(user)} 字，超过上限 {MAX_INPUT_CHARS}；未调用模型。分批：先 promote 一部分再审")
    cfg = load_llm_config()
    if cfg is None:
        raise ValueError("缺少 LLM 配置（config/agent.json 与密钥环境变量）")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    try:
        reply = chat_complete(messages, config=cfg, purpose="adversarial", timeout=LLM_TIMEOUT_S)
    except PermissionError as exc:
        raise ValueError(f"出网断言拦下了这次核对：{exc}") from None
    except LLMError as exc:
        raise ValueError(str(exc)) from None
    content = str(reply.get("content") or "")
    try:
        rows = parse_reply(content)
    except ValueError:
        raw = Path(target).joinpath(*RAW_REPLY)
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(content, encoding="utf-8")
        raise
    judged, missing = verify(rows, items)
    demoted = apply(Path(target), judged, now)
    text = render_report(items, judged, missing, gaps, demoted, cfg.model_for("adversarial"), now)
    return text, {"judged": judged, "missing": missing, "demoted": demoted}


def write_report(target: Path, text: str, now: dt.datetime) -> Path:
    """重跑会覆盖：旧报告（可能已填了终审）先留底。"""
    dest = Path(target) / REPORT
    if dest.exists():
        hist = Path(target).joinpath(*HISTORY_DIR)
        hist.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, hist / f"{now.strftime('%Y%m%d-%H%M%S')}.md")
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.fact_review", description=__doc__.splitlines()[0])
    ap.add_argument("target", type=Path)
    a = ap.parse_args(argv)
    now = dt.datetime.now()
    try:
        text, res = review(a.target, now=now)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    write_report(a.target, text, now)
    judged = [r for r in res["judged"] if not r["flag"]]
    count = {v: sum(1 for r in judged if r["verdict"] == v) for v in VERDICTS}
    print(f"[OK] 已写 {REPORT}：支持 {count['支持']}、部分 {count['部分']}、不支持 {count['不支持']}"
          + (f"；漏判 {len(res['missing'])}" if res["missing"] else "")
          + (f"；回落待核 {'、'.join(res['demoted'])}" if res["demoted"] else ""))
    print("下一步：「不支持」「部分」逐条回原文查证，在终审表填「采纳 / 驳回 + 理由」；采纳的改说法后重新 fact_add。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
