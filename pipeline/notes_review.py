"""番剧笔记零上下文对抗审查（D61）：笔记三步流水线的第二步，由 ava 自己跑。

    python -m pipeline.notes_review <番> [--episodes S01E01,S01E02] [--mechanical-only]

`docs/dev/postmortems/workflow-history.md`「番剧笔记：三步流水线」原本靠 pi 的零上下文子代理；
这里把它能机械化的部分做成确定性检查，剩下的交给每集一次、不给工具的 LLM 调用：

- 第零层 厚度准入（机械）：按 `### SxxEyy` 切集，每集编号场景 ≥ 4、带时间码的逐字台词 ≥ 2；
  总行数 ≥ 集数 × 30。不达标 = 写薄（致命）。
- 第二层 引用核销（机械）：「台词」+ 紧跟的 h:mm:ss[-h:mm:ss]，到该集字幕 [t−3 s, t_end+5 s]
  里找归一化原文：通过 / 时间码错（给实际位置）/ 全集找不到（待复核：简繁或转述）。
- 第一层 剧情 diff（LLM）：每集一次零上下文调用，只拿该集笔记节与整集字幕；只报与字幕矛盾、
  说话人 / 顺序存疑、字幕无据三类。每条逐字引用笔记原文、给字幕 mm:ss，机械自校，核不上标 ⚠。
- 第三层 元层 / 网源：不在这里做，留给终审（ava 用 web 工具核「某源如此说」）。

报告写 `data/library/notes/<番>-对抗审查报告-<日期>[-n].md`，**从不覆盖**（旧报告永久留档）；
终审由 ava 逐条裁决，经 `write_note`（target=review）填终审表、（target=notes）改笔记。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import paths

MIN_SCENES = 4
MIN_TC_QUOTES = 2
MIN_LINES_PER_EP = 30
WINDOW_BEFORE_S = 3.0
WINDOW_AFTER_S = 5.0
CITE_TOL_S = 1.5
LLM_TIMEOUT_S = 240
MAX_EP_INPUT_CHARS = 60_000      # 单集一次调用的输入上限（笔记节 + 整集字幕，常见 1–2 万字）
SAMPLE_PASSED = 10               # 报告里随机抽样列出的「通过」条数（只报错不报过是选择偏差，判据 S11）
REPORT_MARK = "-对抗审查报告"
CATEGORIES = ("与字幕矛盾", "说话人存疑", "顺序存疑", "字幕无据")

EP_HEADING = re.compile(r"^###\s+(S\d{2}E\d{2})\b.*$", re.M)
SCENE_ITEM = re.compile(r"^\s{0,3}\d+[.、]\s", re.M)
_TC = r"\d{1,2}:\d{2}(?::\d{2})?"
# 引号与时间码之间允许少量连接文字（「…」0:02:41 / 「…」（0:02:41）/ 「…」，0:04:31），但不许吃数字——
# 否则贪婪匹配会把 0:14:19 的「0:1」吞掉、只剩 4:19（真实笔记实测踩到）
QUOTE_TC = re.compile(r"「([^「」]{2,}?)」[^「」\n（(\d]{0,8}[（(]?\s*(" + _TC + r")(?:\s*[-–~～]\s*(" + _TC + r"))?")
_NORM = re.compile(r"[\s\W_]+", re.UNICODE)


class NotesReviewError(Exception):
    pass


def norm(s: str) -> str:
    return _NORM.sub("", s)


def tc_seconds(tc: str) -> float:
    parts = [float(x) for x in tc.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _mmss(t: float) -> str:
    total = int(t)
    h, rem = divmod(total, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}"


@dataclass
class Section:
    key: str           # SxxEyy
    title: str         # 标题行原文
    start_line: int    # 1 起
    text: str          # 本节正文（不含标题行）


def split_episodes(note: str) -> list[Section]:
    """按 `### SxxEyy` 标题切集；一节到下一个 `##` / `###` 标题为止。同一集出现两次只取第一次。"""
    heads = list(EP_HEADING.finditer(note))
    out: list[Section] = []
    seen: set[str] = set()
    for m in heads:
        body_start = m.end() + 1
        nxt = re.search(r"^#{2,3}\s", note[body_start:], re.M)
        body = note[body_start: body_start + nxt.start()] if nxt else note[body_start:]
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append(Section(m.group(1), m.group(0).lstrip("# ").strip(), note.count("\n", 0, m.start()) + 1, body))
    return out


def flat(text: str) -> str:
    """把折行接回去（笔记里一句台词常被换行打断），再找「台词」+ 时间码。"""
    return re.sub(r"\n\s*", "", text)


@dataclass
class Quote:
    key: str
    text: str
    start: float
    end: float
    tc: str


def quotes_of(sec: Section) -> list[Quote]:
    out = []
    for m in QUOTE_TC.finditer(flat(sec.text)):
        start = tc_seconds(m.group(2))
        end = tc_seconds(m.group(3)) if m.group(3) else start
        tc = m.group(2) + (f"-{m.group(3)}" if m.group(3) else "")
        out.append(Quote(sec.key, m.group(1), start, max(start, end), tc))
    return out


# ---------- 第零层：厚度 ----------


@dataclass
class Thickness:
    key: str
    scenes: int
    tc_quotes: int

    @property
    def ok(self) -> bool:
        return self.scenes >= MIN_SCENES and self.tc_quotes >= MIN_TC_QUOTES


def thickness(note: str, sections: list[Section], n_eps_expected: int | None) -> tuple[list[Thickness], list[str]]:
    rows = [Thickness(s.key, len(SCENE_ITEM.findall(s.text)), len(quotes_of(s))) for s in sections]
    problems = []
    if not sections:
        problems.append("没有按 `### SxxEyy` 展开的分集小节——分集速查未按集展开（写薄，致命）")
    n_lines = note.count("\n") + 1
    n_eps = n_eps_expected or len(sections)
    if n_eps and n_lines < n_eps * MIN_LINES_PER_EP:
        problems.append(f"总行数 {n_lines} < 集数 {n_eps} × {MIN_LINES_PER_EP}（写薄，致命）")
    if n_eps_expected and len(sections) < n_eps_expected:
        problems.append(f"分集小节 {len(sections)} 个，片源登记 {n_eps_expected} 集——缺 {n_eps_expected - len(sections)} 集")
    return rows, problems


# ---------- 第二层：引用核销 ----------


def index_dir() -> Path:
    return paths.DATA / "library" / "index"


def subtitle_lines(anime: str, key: str, cache: dict) -> list[tuple[float, str]] | None:
    path = index_dir() / f"{anime}_{key}.json"
    if path not in cache:
        if not path.exists():
            cache[path] = None
        else:
            from pipeline.agent.tools import subtitle_lines as _lines

            cache[path] = _lines(json.loads(path.read_text(encoding="utf-8"))["units"], path)
    return cache[path]


@dataclass
class Check:
    quote: Quote
    verdict: str        # 通过 / 时间码错 / 字幕里找不到 / 该集无字幕索引
    detail: str = ""


def _find_all(lines: list[tuple[float, str]], needle: str) -> list[float]:
    """needle（已归一化）在连续字幕里每一次出现时，起始那句的时间（同一句台词全片可能出现多次）。"""
    joined, starts = "", []
    for t, text in lines:
        starts.append((len(joined), t))
        joined += norm(text)
    out, pos = [], joined.find(needle)
    while pos >= 0:
        t_at = starts[0][1]
        for off, t in starts:
            if off > pos:
                break
            t_at = t
        out.append(t_at)
        pos = joined.find(needle, pos + 1)
    return out


def check_quote(q: Quote, lines: list[tuple[float, str]] | None) -> Check:
    if lines is None:
        return Check(q, "该集无字幕索引")
    needle = norm(q.text)
    if not needle:
        return Check(q, "字幕里找不到", "引文归一化后为空")
    # 判据看「引文从哪一句起头」：起头那句落在 [t−3 s, t_end+5 s] 就算行级对准（S12）——
    # 长引文跨好几句、往后延伸多远不影响
    hits = _find_all(lines, needle)
    if any(q.start - WINDOW_BEFORE_S <= at <= q.end + WINDOW_AFTER_S for at in hits):
        return Check(q, "通过")
    if hits:
        return Check(q, "时间码错", "字幕里在 " + "、".join(_mmss(at) for at in hits[:3]))
    return Check(q, "字幕里找不到", "可能是简繁差异、转述或省略号拼接，待复核")


# ---------- 第一层：LLM 剧情 diff ----------

SYSTEM_PROMPT = """你是零上下文的番剧笔记审查者。你没看过写笔记的过程，只拿到某一集的笔记小节和这一集的整集字幕（带时间码，只有台词、不带说话人）。

只找下面四类问题，没有问题的不要列。「类别」只能是这四个词之一，一条只填一个：
- 与字幕矛盾：笔记写的台词、事件与字幕对不上；
- 说话人存疑：笔记把台词归给某人，字幕里的语境不支持；
- 顺序存疑：笔记写的先后顺序与字幕不符；
- 字幕无据：笔记写的关键剧情事实在字幕里找不到任何对应（可能来自网源，需要核实，不等于错）。

每条必须：
1. 「原文」逐字摘自笔记小节（不少于 6 个字，不许改写）；
2. 「字幕」给字幕里的 h:mm:ss（字幕无据类可留空）；
3. 「说明」一句话讲清依据。
不要评价文风，不要给改写稿。只输出 JSON：{"items": [{"类别": "...", "原文": "...", "字幕": "...", "说明": "..."}]}"""


def build_user_prompt(anime: str, sec: Section, lines: list[tuple[float, str]]) -> str:
    subs = "\n".join(f"{_mmss(t)} {s}" for t, s in lines)
    return f"番：{anime}\n集：{sec.key}\n\n## 笔记小节（{sec.title}）\n{sec.text.strip()}\n\n## 整集字幕\n{subs}\n"


def verify_llm_item(item: dict, sec: Section, lines: list[tuple[float, str]]) -> dict:
    cat = str(item.get("类别", "")).strip()
    if cat not in CATEGORIES:
        # 模型偶尔把两类合写（「说话人存疑 / 顺序存疑」，罪恶王冠实跑 11 条）：取第一个认得的
        cat = next((c for c in re.split(r"\s*[/／、,，]\s*", cat) if c in CATEGORIES), cat)
    quote = str(item.get("原文", "")).strip()
    cite = str(item.get("字幕", "")).strip()
    out = {"类别": cat if cat in CATEGORIES else f"{cat}（类别不认识）", "原文": quote, "字幕": cite,
           "说明": str(item.get("说明", "")).strip(), "核对": []}
    if len(norm(quote)) < 6 or norm(quote) not in norm(flat(sec.text)):
        out["核对"].append("⚠ 原文在笔记小节里找不到（审查者引用不存在）")
    if cite:
        if not re.fullmatch(_TC, cite):
            out["核对"].append(f"⚠ 字幕时间码格式不认识：{cite}")
        elif not any(abs(t - tc_seconds(cite)) <= CITE_TOL_S for t, _s in lines):
            out["核对"].append(f"⚠ 字幕里 {cite} 附近没有台词（审查者引用不存在）")
    elif cat != "字幕无据":
        out["核对"].append("⚠ 没给字幕时间码")
    return out


def llm_review(anime: str, sec: Section, lines: list[tuple[float, str]]) -> list[dict]:
    from pipeline.adversarial import AdversarialError, parse_reply
    from pipeline.agent.llm import LLMError, chat_complete, load_llm_config

    user = build_user_prompt(anime, sec, lines)
    if len(SYSTEM_PROMPT) + len(user) > MAX_EP_INPUT_CHARS:
        raise NotesReviewError(f"{sec.key} 的输入 {len(SYSTEM_PROMPT) + len(user)} 字超过上限 {MAX_EP_INPUT_CHARS}，未调用模型")
    cfg = load_llm_config()
    if cfg is None:
        raise NotesReviewError("缺少 LLM 配置（config/agent.json 与密钥环境变量）")
    try:
        reply = chat_complete([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
                              config=cfg, purpose="adversarial", timeout=LLM_TIMEOUT_S)
        items = parse_reply(str(reply.get("content") or ""))
    except PermissionError as exc:
        raise NotesReviewError(f"出网断言拦下了这次审查：{exc}") from None
    except (LLMError, AdversarialError) as exc:
        raise NotesReviewError(f"{sec.key}：{exc}") from None
    return [verify_llm_item(it, sec, lines) for it in items]


# ---------- 报告 ----------


@dataclass
class Result:
    anime: str
    sections: list[Section]
    reviewed: list[str]
    thick_rows: list[Thickness]
    thick_problems: list[str]
    checks: list[Check]
    llm_items: dict[str, list[dict]] = field(default_factory=dict)
    llm_errors: dict[str, str] = field(default_factory=dict)
    mechanical_only: bool = False


def _cell(s: str) -> str:
    return s.replace("|", "｜").replace("\n", " ")


def render(r: Result, now: datetime, model: str | None) -> str:
    L: list[str] = []
    L.append(f"# 《{r.anime}》笔记对抗审查报告（{now:%Y-%m-%d %H:%M}）\n")
    L.append(f"生成：`notes_review`（D61）。第一层模型：{model or '未调用（--mechanical-only）'}。"
             "第三层（网源 / 「某源如此说」）不在本报告内，由终审用 web 工具核。\n")
    n_pass = sum(c.verdict == "通过" for c in r.checks)
    L.append("## 覆盖率声明\n")
    L.append(f"- 分集小节 {len(r.sections)} 个，本次审查 {len(r.reviewed)} 集：{'、'.join(r.reviewed) or '无'}")
    L.append(f"- 引用核销：带时间码的台词 {len(r.checks)} 条，通过 {n_pass}，"
             f"时间码错 {sum(c.verdict == '时间码错' for c in r.checks)}，"
             f"找不到 {sum(c.verdict == '字幕里找不到' for c in r.checks)}，"
             f"无字幕索引 {sum(c.verdict == '该集无字幕索引' for c in r.checks)}")
    if not r.mechanical_only:
        done = [k for k in r.reviewed if k in r.llm_items]
        L.append(f"- 剧情 diff：{len(done)}/{len(r.reviewed)} 集完成" +
                 (f"；失败 {len(r.llm_errors)} 集（见文末）" if r.llm_errors else ""))
    L.append("")
    L.append("## 第零层 · 厚度准入\n")
    for p in r.thick_problems:
        L.append(f"- **{p}**")
    thin = [t for t in r.thick_rows if not t.ok and t.key in r.reviewed]
    if thin:
        L.append(f"\n写薄的集（场景 ≥ {MIN_SCENES}、带时间码台词 ≥ {MIN_TC_QUOTES}）：\n")
        L.append("| 集 | 场景 | 带时间码台词 |\n|---|---|---|")
        L += [f"| {t.key} | {t.scenes} | {t.tc_quotes} |" for t in thin]
    if not r.thick_problems and not thin:
        L.append("- 通过")
    L.append("")
    issues: list[tuple[str, str, str, str]] = []   # (编号, 集, 问题, 详情)
    bad = [c for c in r.checks if c.verdict != "通过"]
    L.append("## 第二层 · 引用核销（问题）\n")
    if bad:
        L.append("| # | 集 | 笔记时间码 | 引文 | 结论 | 详情 |\n|---|---|---|---|---|---|")
        for i, c in enumerate(bad, 1):
            L.append(f"| Q{i} | {c.quote.key} | {c.quote.tc} | {_cell(c.quote.text[:40])} | {c.verdict} | {_cell(c.detail)} |")
            issues.append((f"Q{i}", c.quote.key, c.verdict, c.quote.text[:30]))
    else:
        L.append("- 无")
    L.append("")
    passed = [c for c in r.checks if c.verdict == "通过"]
    if passed:
        L.append(f"### 随机抽样通过清单（{min(SAMPLE_PASSED, len(passed))}/{len(passed)}）\n")
        for c in random.Random(len(passed)).sample(passed, min(SAMPLE_PASSED, len(passed))):
            L.append(f"- {c.quote.key} {c.quote.tc}「{_cell(c.quote.text[:40])}」")
        L.append("")
    if not r.mechanical_only:
        L.append("## 第一层 · 剧情 diff（零上下文模型，每集一次）\n")
        n = 0
        for key in r.reviewed:
            for it in r.llm_items.get(key, []):
                n += 1
                mark = "；".join(it["核对"])
                L.append(f"- **F{n}** {key} · {it['类别']} · 原文「{_cell(it['原文'][:60])}」"
                         f" · 字幕 {it['字幕'] or '—'} · {_cell(it['说明'])}" + (f" · {mark}" if mark else ""))
                issues.append((f"F{n}", key, it["类别"], it["原文"][:30]))
        if not n:
            L.append("- 无")
        for key, err in r.llm_errors.items():
            L.append(f"- {key} 审查失败：{_cell(err)}")
        L.append("")
    L.append("## 终审裁决（ava 逐条填：采纳 / 驳回 + 理由 / 存疑 + 还差什么证据）\n")
    L.append("标 ⚠ 的是审查者自己引用核不上的条目，先当它没说。采纳的经 `write_note` 改笔记，"
             "并在笔记「已知更正记录」节记一笔。\n")
    L.append("| # | 集 | 问题 | 摘要 | 裁决 |\n|---|---|---|---|---|")
    L += [f"| {i} | {k} | {_cell(q)} | {_cell(s)} |  |" for i, k, q, s in issues]
    return "\n".join(L) + "\n"


def report_path(anime: str, now: datetime) -> Path:
    base = paths.DATA / "library" / "notes" / f"{anime}{REPORT_MARK}-{now:%Y-%m-%d}"
    dest, n = base.with_suffix(".md"), 2
    while dest.exists():
        dest = base.with_name(f"{base.name}-{n}.md")
        n += 1
    return dest


def film_key(anime: str) -> str:
    from .ingest import load_sources

    keys = [k for k in load_sources(anime) if re.fullmatch(r"S\d{2}E\d{2}", k)]
    return keys[0] if keys else "S01E01"


def expected_episodes(anime: str) -> int | None:
    from .ingest import load_sources

    try:
        return sum(1 for k in load_sources(anime) if re.fullmatch(r"S\d{2}E\d{2}", k) and not k.startswith("S00"))
    except (SystemExit, OSError, ValueError):
        return None


def review(anime: str, *, episodes: list[str] | None = None, mechanical_only: bool = False) -> Result:
    note_path = paths.DATA / "library" / "notes" / f"{anime}.md"
    if not note_path.exists():
        raise NotesReviewError(f"没有《{anime}》的笔记：{note_path}")
    note = note_path.read_text(encoding="utf-8")
    sections = split_episodes(note)
    expected = expected_episodes(anime) or None
    if not sections and expected == 1:
        # 剧场版：片源只登记一集，笔记按场景写、不分集——整份笔记当这一集的小节
        sections = [Section(film_key(anime), "全片", 1, note)]
    rows, problems = thickness(note, sections, expected)
    chosen = [s for s in sections if not episodes or s.key in episodes]
    missing = sorted(set(episodes or []) - {s.key for s in sections})
    if missing:
        problems.append(f"指定的集在笔记里没有小节：{'、'.join(missing)}")
    cache: dict = {}
    checks = [check_quote(q, subtitle_lines(anime, s.key, cache)) for s in chosen for q in quotes_of(s)]
    res = Result(anime, sections, [s.key for s in chosen], rows, problems, checks, mechanical_only=mechanical_only)
    if not mechanical_only:
        for s in chosen:
            lines = subtitle_lines(anime, s.key, cache)
            if lines is None:
                res.llm_errors[s.key] = "该集无字幕索引，跳过剧情 diff"
                continue
            try:
                res.llm_items[s.key] = llm_review(anime, s, lines)
            except NotesReviewError as exc:
                res.llm_errors[s.key] = str(exc)
            print(f"[*] {s.key} 剧情 diff：{len(res.llm_items.get(s.key, []))} 条"
                  + (f"（失败：{res.llm_errors[s.key]}）" if s.key in res.llm_errors else ""), flush=True)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.notes_review", description=__doc__.splitlines()[0])
    ap.add_argument("anime")
    ap.add_argument("--episodes", default=None, help="只审这几集，逗号分隔（如 S01E01,S01E02）")
    ap.add_argument("--mechanical-only", action="store_true", help="只跑厚度与引用核销，不调模型")
    a = ap.parse_args(argv)
    paths.require_data()
    eps = [e.strip().upper() for e in a.episodes.split(",") if e.strip()] if a.episodes else None
    now = datetime.now()
    try:
        res = review(a.anime, episodes=eps, mechanical_only=a.mechanical_only)
    except NotesReviewError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    model = None
    if not a.mechanical_only:
        from pipeline.agent.llm import load_llm_config

        cfg = load_llm_config()
        model = cfg.model_for("adversarial") if cfg else None
    dest = report_path(a.anime, now)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(render(res, now, model), encoding="utf-8")
    tmp.replace(dest)
    n_bad = sum(c.verdict != "通过" for c in res.checks)
    n_llm = sum(len(v) for v in res.llm_items.values())
    print(f"OK 报告 → {dest}\n   厚度问题 {len(res.thick_problems) + sum(not t.ok for t in res.thick_rows if t.key in res.reviewed)} 项，"
          f"引用核销问题 {n_bad}/{len(res.checks)}，剧情 diff {n_llm} 条；下一步：逐条终审（write_note target=review）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
