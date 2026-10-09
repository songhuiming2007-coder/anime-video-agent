"""02.8 零上下文对抗审查（D48 ②，`docs/dev/plans/2026-10-09-adversarial-review-spec.md`）。

审查者是**一次** LLM 调用，不给工具、看不到写稿对话：只拿稿件、番剧笔记、逐段锚点窗口内的
字幕与在场索引（全部机械预取），按三类挑问题——事实（错误与模糊）、衔接（转折不畅）、
语言（AI 味太浓、语病）。它不能自己去查，也就没有「查到一半开始编」的余地。

审查者也会编：渲染前逐条机械核对它的引用（原文必须逐字在稿件里；事实类的字幕时间码、
在场镜头、笔记摘句必须在喂给它的那批证据里），核不上的标「审查者引用不存在」，不静默放过。

衔接与语言只诊断、不给改写句（语感归人判断）；事实类由写稿会话查证后改稿，并在报告的
终审表里逐条填「采纳 / 驳回 + 理由」。

用法：python -m pipeline.adversarial <期目录> [--script 02-script.md|02-script.draft.md]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import paths

REPORT = "02-adversarial.md"
HISTORY_DIR = ("_agent", "adversarial-history")
RAW_REPLY = ("_agent", "adversarial-raw.txt")
SCRIPT_NAMES = ("02-script.md", "02-script.draft.md")
INDEX_DIR = paths.DATA / "library" / "index"
NOTES_DIR = paths.DATA / "library" / "notes"

# 锚点窗口：[锚点 − 缓冲, 锚点终点（没写就按配音字数 / 语速估）+ 缓冲]。缓冲覆盖「画面在说这句之前」
WINDOW_PAD_S = 15.0
CHARS_PER_SEC = 7.2          # 实测语速 434 字/分（check_script 同一口径）
LLM_TIMEOUT_S = 240          # 输入几万 token，默认 60 s 不够
MAX_INPUT_CHARS = 150_000    # 一次审查的输入上限：防一份超长笔记把账单打穿
CITE_TOL_S = 1.5             # 引用时间码写到秒（mm:ss），比对允许的取整误差

CATEGORIES = ("事实", "衔接", "语言")
FACT_VERDICTS = ("与证据矛盾", "无出处", "表述模糊")
SOURCE_TYPES = ("字幕", "在场索引", "笔记", "无")
NOTES_EXCERPT_MIN = 6


class AdversarialError(Exception):
    pass


@dataclass
class SegEvidence:
    label: str
    voice: str
    anchors: list[str] = field(default_factory=list)
    subs: list[tuple[str, float, str]] = field(default_factory=list)   # (集号, 起点秒, 台词)
    presence: list[str] = field(default_factory=list)                 # vindex.who_lines 的输出行
    gaps: list[str] = field(default_factory=list)                     # 证据缺口（人要知道审查者没看到什么）


@dataclass
class Evidence:
    script_name: str
    script_text: str
    segments: list[SegEvidence]
    notes: dict[str, str]
    gaps: list[str]


# ---------------------------------------------------------------- 证据预取


def _mmss(t: float) -> str:
    total = int(t)
    return f"{total // 60:02d}:{total % 60:02d}"


def _subtitle_file_lines(anime: str, key: str, cache: dict) -> list[tuple[float, str]] | None:
    path = INDEX_DIR / f"{anime}_{key}.json"
    if path not in cache:
        if not path.exists():
            cache[path] = None
        else:
            from pipeline.agent.tools import subtitle_lines

            cache[path] = subtitle_lines(json.loads(path.read_text(encoding="utf-8"))["units"], path)
    return cache[path]


def presence_lines(anime: str, key: str, start: float, end: float, cache: dict) -> list[str]:
    """该窗口的 `vindex who` 输出（含覆盖率尾注）。索引不可用时抛 AdversarialError（由调用方记成证据缺口）。"""
    from . import vindex

    if anime not in cache:
        try:
            cache[anime] = (vindex.load_presence(anime), vindex.display_names(anime))
        except SystemExit as exc:
            cache[anime] = AdversarialError(str(exc).splitlines()[0] if str(exc) else "在场索引不可用")
    got = cache[anime]
    if isinstance(got, AdversarialError):
        raise got
    pres, names = got
    return vindex.who_lines(pres.by_ep.get(key), names, start, end)


def gather_evidence(ep: Path, script_name: str) -> Evidence:
    from . import check_script, tts
    from .bgm import animes_of

    script_path = ep / script_name
    if not script_path.exists():
        raise AdversarialError(f"没有 {script_name}")
    text = script_path.read_text(encoding="utf-8")
    segs = tts.parse_script(script_path)
    if not segs:
        raise AdversarialError(f"{script_name} 里没有解析出任何「配音：」段落")
    anchors = dict(check_script.parse_anchors(text))
    animes = animes_of(ep)
    gaps: list[str] = []
    if not animes:
        gaps.append("01-topic.md 没写「番:」：没有笔记、字幕与在场索引可给审查者，只能审语言与衔接")

    notes: dict[str, str] = {}
    for anime in animes:
        p = NOTES_DIR / f"{anime}.md"
        if p.exists():
            notes[anime] = p.read_text(encoding="utf-8")
        else:
            gaps.append(f"没有《{anime}》的番剧笔记")

    sub_cache: dict = {}
    pres_cache: dict = {}
    out: list[SegEvidence] = []
    for s in segs:
        se = SegEvidence(label=s.label, voice=s.text)
        raw = anchors.get(s.label)
        if raw and not raw.startswith("无"):
            for item in check_script._anchor_items(raw):
                m = check_script.ANCHOR_TC.fullmatch(item)
                if not m or m.group("sp") is not None:
                    continue  # SP 特典没有字幕索引；格式坏的由机检报
                anime = (m.group("anime") or "").strip() or (animes[0] if animes else "")
                if not anime:
                    continue
                key = f"S{int(m.group('season')):02d}E{int(m.group('episode')):02d}"
                t0 = int(m.group("m0")) * 60 + float(m.group("s0"))
                t1 = (int(m.group("m1")) * 60 + float(m.group("s1"))) if m.group("m1") else t0 + len(s.text) / CHARS_PER_SEC
                w0, w1 = max(0.0, t0 - WINDOW_PAD_S), t1 + WINDOW_PAD_S
                se.anchors.append(f"{anime} {key} {_mmss(t0)}")
                lines = _subtitle_file_lines(anime, key, sub_cache)
                if lines is None:
                    se.gaps.append(f"没有《{anime}》{key} 的字幕索引")
                else:
                    se.subs.extend((key, st, ln) for st, ln in lines if w0 <= st <= w1)
                try:
                    se.presence.extend(presence_lines(anime, key, w0, w1, pres_cache))
                except AdversarialError as exc:
                    se.gaps.append(f"在场索引不可用：{exc}")
        out.append(se)
    return Evidence(script_name=script_name, script_text=text, segments=out, notes=notes, gaps=gaps)


# ---------------------------------------------------------------- 提示词


SYSTEM_PROMPT = """你是零上下文的稿件审查者。你没看过写稿过程，只拿到下面的稿件、番剧笔记与逐段证据。
任务：挑出稿件里的问题，只列问题，不列没问题的地方。三类：

1. 事实：剧情事实的错误或模糊。谁说的、谁在场、在哪、做了什么、什么神态、发生在哪集，都是剧情事实。
   判定只能是三选一：
   - 与证据矛盾：给出的字幕、在场索引或笔记与稿件说法冲突；
   - 无出处：稿件断言了具体事实，但给你的证据里找不到支撑（字幕不带说话人，说话人与在场人物只能靠在场索引或笔记）；
   - 表述模糊：说法含糊到看不出指的是哪件事、哪个人。
   在场索引只认得已贴名、脸被检出的角色：没列出不等于不在场，不能据此判「与证据矛盾」。
2. 衔接：上下段之间转折不畅、因果断开、没看过剧的人不知道为什么会这样。
3. 语言：明显不正常的表达，如 AI 味太浓（空泛排比、套话、翻译腔）、语病、指代不清。只挑明显的，句式风格不算。

规则：
- 「原文」必须从稿件里逐字复制（可以只取相关的一小段），不许改写。
- 事实类必须给「出处」。类型与引用写法：
  字幕 → 引用写「集号 mm:ss」，如「S02E07 19:54」，只能引用证据里列出的字幕行；
  在场索引 → 引用写该镜头的起点「mm:ss」，只能引用证据里列出的在场行；
  笔记 → 引用从笔记里逐字摘一句（至少 6 个字）；
  无 → 只用于「无出处」「表述模糊」。
- 衔接与语言只写问题在哪、为什么不通，不给改写句。
- 只输出一个 JSON 对象，不要任何别的文字：
{"items": [{"段": "21", "类": "事实", "原文": "...", "问题": "...", "判定": "与证据矛盾", "出处": {"类型": "字幕", "引用": "S02E07 19:54"}}]}
衔接与语言的条目省略「判定」与「出处」。没有问题就输出 {"items": []}。"""


def build_user_prompt(ev: Evidence) -> str:
    parts = [f"## 稿件（{ev.script_name}）\n\n{ev.script_text.strip()}"]
    for anime, text in ev.notes.items():
        parts.append(f"## 番剧笔记《{anime}》\n\n{text.strip()}")
    seg_parts = []
    for s in ev.segments:
        head = f"### 段 {s.label}" + (f"（锚点：{'；'.join(s.anchors)}）" if s.anchors else "（无锚点）")
        body = []
        if s.subs:
            body.append("字幕（锚点窗口内）：\n" + "\n".join(f"{k} {_mmss(t)} {ln}" for k, t, ln in s.subs))
        if s.presence:
            body.append("在场索引：\n" + "\n".join(s.presence))
        if s.gaps:
            body.append("证据缺口：" + "；".join(s.gaps))
        seg_parts.append(head + ("\n" + "\n".join(body) if body else "\n（只有稿件与笔记）"))
    parts.append("## 逐段证据\n\n" + "\n\n".join(seg_parts))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 回复解析与引用自校


def parse_reply(content: str) -> list[dict]:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AdversarialError("审查者没有返回 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AdversarialError(f"审查者返回的 JSON 解析失败：{exc}") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdversarialError("审查者返回的 JSON 缺 items 列表")
    return [it for it in items if isinstance(it, dict)]


_PRESENCE_LINE = re.compile(r"^\s*(\d+):(\d+(?:\.\d+)?)-")
_CITE_SUBS = re.compile(r"^\s*(S\d+E\d+)\s+(\d+):(\d+(?:\.\d+)?)\s*$", re.I)
_CITE_TIME = re.compile(r"^\s*(\d+):(\d+(?:\.\d+)?)\s*$")
_SPACE = re.compile(r"\s+")


def _squash(s: str) -> str:
    return _SPACE.sub("", s)


def verify_item(item: dict, ev: Evidence) -> dict:
    """规范化一条审查意见，并机械核对它的引用。核不上的写进 `核对`，不丢条目。"""
    out = {
        "段": str(item.get("段", "")).strip(),
        "类": str(item.get("类", "")).strip(),
        "原文": str(item.get("原文", "")).strip(),
        "问题": str(item.get("问题", "")).strip(),
        "判定": str(item.get("判定", "")).strip(),
        "出处": item.get("出处") if isinstance(item.get("出处"), dict) else {},
        "核对": "",
    }
    seg = next((s for s in ev.segments if s.label == out["段"]), None)
    problems: list[str] = []
    if seg is None:
        problems.append(f"稿件里没有段 {out['段'] or '（空）'}")
    if out["类"] not in CATEGORIES:
        problems.append(f"类别「{out['类']}」不在 事实/衔接/语言 之内")
    if len(_squash(out["原文"])) < 2 or _squash(out["原文"]) not in _squash(ev.script_text):
        problems.append("原文不在稿件里")
    if out["类"] == "事实":
        problems += _verify_fact(out, seg, ev)
    else:
        out["判定"] = "请人看"
        out["出处"] = {}
    out["核对"] = "；".join(problems)
    return out


def _verify_fact(out: dict, seg: SegEvidence | None, ev: Evidence) -> list[str]:
    if out["判定"] not in FACT_VERDICTS:
        return [f"判定「{out['判定']}」不在 {'/'.join(FACT_VERDICTS)} 之内"]
    kind = str(out["出处"].get("类型", "无")).strip() or "无"
    ref = str(out["出处"].get("引用", "")).strip()
    out["出处"] = {"类型": kind, "引用": ref}
    if kind not in SOURCE_TYPES:
        return [f"出处类型「{kind}」不认得"]
    if kind == "无":
        return ["「与证据矛盾」必须给出处"] if out["判定"] == "与证据矛盾" else []
    if kind == "笔记":
        if len(_squash(ref)) < NOTES_EXCERPT_MIN or not any(_squash(ref) in _squash(t) for t in ev.notes.values()):
            return ["笔记摘句不在笔记里"]
        return []
    if seg is None:
        return []
    if kind == "字幕":
        m = _CITE_SUBS.match(ref)
        if not m:
            return [f"字幕引用「{ref}」不是「集号 mm:ss」"]
        key, t = m.group(1).upper(), int(m.group(2)) * 60 + float(m.group(3))
        if not any(k == key and abs(st - t) <= CITE_TOL_S for k, st, _ in seg.subs):
            return [f"证据里没有 {key} {ref.split()[-1]} 这句字幕"]
        return []
    m = _CITE_TIME.match(ref)
    if not m:
        return [f"在场索引引用「{ref}」不是「mm:ss」"]
    t = int(m.group(1)) * 60 + float(m.group(2))
    starts = []
    for line in seg.presence:
        pm = _PRESENCE_LINE.match(line)
        if pm:
            starts.append(int(pm.group(1)) * 60 + float(pm.group(2)))
    if not any(abs(st - t) <= CITE_TOL_S for st in starts):
        return [f"证据里没有 {ref} 起的在场镜头"]
    return []


# ---------------------------------------------------------------- 报告


def _cell(s: str) -> str:
    return s.replace("|", "｜").replace("\n", " ").strip() or "—"


def render_report(ev: Evidence, items: list[dict], model: str, now: datetime) -> str:
    facts = [it for it in items if it["类"] == "事实"]
    flows = [it for it in items if it["类"] == "衔接"]
    langs = [it for it in items if it["类"] == "语言"]
    others = [it for it in items if it["类"] not in CATEGORIES]
    ids: list[tuple[str, dict]] = []
    for prefix, group in (("F", facts), ("C", flows), ("L", langs), ("X", others)):
        ids += [(f"{prefix}{i}", it) for i, it in enumerate(group, 1)]
    by_id = {id(it): code for code, it in ids}
    bad = sum(1 for it in items if it["核对"])
    anchored = sum(1 for s in ev.segments if s.anchors)

    lines = [
        "# 02.8 零上下文对抗审查",
        "",
        f"审查对象：`{ev.script_name}`（{now.strftime('%Y-%m-%d %H:%M')}，模型 {model}）  ",
        f"审查者看到的：稿件、番剧笔记{'《' + '》《'.join(ev.notes) + '》' if ev.notes else '（无）'}、"
        f"{anchored} 个锚点段窗口内的字幕与在场索引；**看不到写稿对话**。  ",
        f"结果：事实 {len(facts)} 条、衔接 {len(flows)} 条、语言 {len(langs)} 条"
        + (f"；**审查者引用核不上 {bad} 条**（标 ⚠，先别信）" if bad else "") + "。",
    ]
    seg_gaps = [f"段 {s.label}：{'；'.join(s.gaps)}" for s in ev.segments if s.gaps]
    if ev.gaps or seg_gaps:
        lines += ["", "证据缺口（审查者没看到这些，相应结论偏弱）："] + [f"- {g}" for g in ev.gaps + seg_gaps]

    def table(title: str, group: list[dict], with_fact_cols: bool) -> None:
        lines.extend(["", f"## {title}（{len(group)} 条）", ""])
        if not group:
            lines.append("无。")
            return
        if with_fact_cols:
            lines.extend(["| 编号 | 段 | 原文 | 问题 | 判定 | 出处 |", "|---|---|---|---|---|---|"])
        else:
            lines.extend(["| 编号 | 段 | 原文 | 问题 |", "|---|---|---|---|"])
        for it in group:
            code = by_id[id(it)]
            flag = f"⚠ 审查者引用不存在（{it['核对']}）" if it["核对"] else ""
            if with_fact_cols:
                src = it["出处"]
                src_text = f"{src.get('类型', '无')} {src.get('引用', '')}".strip() if src else "无"
                verdict = it["判定"] + (f"<br>{flag}" if flag else "")
                lines.append(f"| {code} | {_cell(it['段'])} | {_cell(it['原文'])} | {_cell(it['问题'])} | {_cell(verdict)} | {_cell(src_text)} |")
            else:
                problem = it["问题"] + (f"<br>{flag}" if flag else "")
                lines.append(f"| {code} | {_cell(it['段'])} | {_cell(it['原文'])} | {_cell(problem)} |")

    table("事实：错误与模糊", facts, True)
    table("衔接：转折不畅", flows, False)
    table("语言：明显不正常", langs, False)
    if others:
        table("其他（类别不认得）", others, False)

    lines += [
        "",
        "## 终审裁决",
        "",
        "事实类由写稿会话查证（字幕 → 在场索引 → 笔记）后逐条填「采纳 / 驳回 + 理由」，采纳的改进稿件；"
        "衔接与语言类只诊断不改写，交人在 02.5 定（「人定」）。",
        "",
        "| 编号 | 裁决 | 理由 |",
        "|---|---|---|",
    ]
    for code, it in ids:
        lines.append(f"| {code} | {'人定' if it['类'] in ('衔接', '语言') else ''} |  |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 入口


def pick_script(ep: Path, name: str | None) -> str:
    if name:
        if name not in SCRIPT_NAMES:
            raise AdversarialError(f"--script 只能是 {' / '.join(SCRIPT_NAMES)}")
        return name
    for cand in SCRIPT_NAMES:
        if (ep / cand).exists():
            return cand
    raise AdversarialError("本期没有 02-script.md 或 02-script.draft.md")


def review(ep: Path, script_name: str, *, now: datetime | None = None) -> tuple[str, list[dict]]:
    """跑一次审查：预取证据 → 一次 LLM 调用 → 解析 → 引用自校 → 渲染。返回 (报告正文, 条目)。"""
    from pipeline.agent.llm import LLMError, chat_complete, load_llm_config

    ev = gather_evidence(ep, script_name)
    user = build_user_prompt(ev)
    if len(SYSTEM_PROMPT) + len(user) > MAX_INPUT_CHARS:
        raise AdversarialError(
            f"审查输入 {len(SYSTEM_PROMPT) + len(user)} 字，超过上限 {MAX_INPUT_CHARS}（多半是笔记太长）；未调用模型")
    cfg = load_llm_config()
    if cfg is None:
        raise AdversarialError("缺少 LLM 配置（config/agent.json 与密钥环境变量）")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    try:
        reply = chat_complete(messages, config=cfg, purpose="adversarial", timeout=LLM_TIMEOUT_S)
    except PermissionError as exc:
        raise AdversarialError(f"出网断言拦下了这次审查：{exc}") from None
    except LLMError as exc:
        raise AdversarialError(str(exc)) from None
    content = str(reply.get("content") or "")
    try:
        raw_items = parse_reply(content)
    except AdversarialError:
        raw = ep.joinpath(*RAW_REPLY)
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(content, encoding="utf-8")
        raise
    items = [verify_item(it, ev) for it in raw_items]
    return render_report(ev, items, cfg.model_for("adversarial"), now or datetime.now()), items


def write_report(ep: Path, text: str, now: datetime) -> Path:
    """重跑会覆盖：旧报告（可能已填了终审）先留底到 `_agent/adversarial-history/`。"""
    dest = ep / REPORT
    if dest.exists():
        hist = ep.joinpath(*HISTORY_DIR)
        hist.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, hist / f"{now.strftime('%Y%m%d-%H%M%S')}.md")
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.adversarial", description=__doc__.splitlines()[0])
    ap.add_argument("episode", type=Path)
    ap.add_argument("--script", choices=SCRIPT_NAMES, default=None,
                    help="审哪份稿件（默认：有定稿审定稿，否则审草稿）")
    a = ap.parse_args(argv)
    ep = a.episode
    now = datetime.now()
    try:
        name = pick_script(ep, a.script)
        text, items = review(ep, name, now=now)
    except AdversarialError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    write_report(ep, text, now)
    count = {c: sum(1 for it in items if it["类"] == c) for c in CATEGORIES}
    facts = [it for it in items if it["类"] == "事实"]
    by_verdict = "、".join(f"{v} {n}" for v in FACT_VERDICTS if (n := sum(1 for it in facts if it["判定"] == v)))
    bad = sum(1 for it in items if it["核对"])
    print(f"[OK] 已写 {REPORT}（审 {name}）：事实 {count['事实']} 条" + (f"（{by_verdict}）" if by_verdict else "")
          + f"、衔接 {count['衔接']} 条、语言 {count['语言']} 条"
          + (f"；审查者引用核不上 {bad} 条（报告里标 ⚠）" if bad else ""))
    print("下一步：事实类逐条查证后改稿并在终审表填「采纳 / 驳回 + 理由」；衔接与语言类原样转给人，不自行改写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
