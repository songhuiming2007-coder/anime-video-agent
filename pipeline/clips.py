"""素材检索排片（流程第 04 步）。

把稿件每段的 `查询` 送进字幕索引，按该段**配音的真实时长**排出够填满的片段，
产出 `04-clips.json` 交人抽检。

    python -m pipeline.clips data/episodes/<本期>

时长一律取自 `03-audio/manifest.json`，不用字数估算——CLAUDE.md「顺序不可交换：
必须先配音拿到每段真实时长，再排画面轨」。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from . import paths

from . import vindex
from .ingest import load_sources
from .subindex import INDEX_DIR, load_all, search

# 单个片段的下限。比这更短会闪，观众来不及看清画面。
MIN_CLIP = 2.5

# 台词开口前留一点，避免切进上一句的尾音
PAD = 0.25

# 检索命中判为「没找到」的分数线。
#
# **不是拍脑袋的数，是拿零假设组量出来的。** 2026-07-29 用 10 条本片绝不可能有的查询
# （驾驶机甲、法庭陈词、颠勺炒菜……）打这个索引，Top-1 落在 [0.276, 0.431]；
# 而稿件里 21 条真实查询落在 [0.468, 0.741]。两组不重叠，0.45 正好在中间。
#
# 别改成「越高越严」——早先试过用绝对分数当质量阈值，实测 0.532 的
# 「角色说讨厌对方那样的做法」命中的是「我讨厌你那么做」，近乎逐字。
# 低分不代表差，这条线只用来判「压根没找到」。
NO_MATCH = 0.45

# 取够多的候选，因为滑窗索引（window=2 step=1）会让 Top-2 常常只是 Top-1 挪一行，
# 跨段去重又会再吃掉一批。
TOPK = 24

# 两处画面被认为是同一处的间隔。判定用台词的**真实跨度**（`span`），
# 不用拉长到 MIN_CLIP 之后的 `dur`——后者是排版结果，不是画面身份。
# 2026-07-29 踩过：段 10 用 15:12「别再那样自我牺牲了」，自然跨度 2.4s 被拉到
# MIN_CLIP=2.5s 再加 1.0s 间隔，把 6 秒后段 11 要的 15:18「牺牲？开什么玩笑」
# 也圈掉了，段 11 因此无片可用。这两句是同一场对话的连续两句，本来就该各用各的镜头。
OVERLAP_GAP = 0.5

# presence 参与排序的带（ADR-0004）：相邻候选台词分差 ≤ 这条带子才视为
# 「同一档」，用在场分 tie-break；差超过带子，台词分绝对优先。量纲 = bge 余弦
# 在「同一语义、不同措辞」的查询下的分数波动，2026-08-09 实测回填：本期
# 20 段查询 × 2 个同义改写变体，对同一 top-1 候选打分，40 对分差
# min 0.000 / median 0.060 / P75 0.109 / max 0.243。取中位数：台词分差
# 不超过噪声中位数 = 排序本来就不可区分，presence 才发言；超过 = 台词分是
# 可靠信号，presence 无权翻盘。0.03 是拍脑袋初值，被实测推翻（噪声是它两倍）。
PRESENCE_BAND = 0.06

# 锚点时间码（ADR-0008）：`锚点: S01E01 17:50` 或区间 `S01E01 17:50-18:20`。
# 分钟允许三位（剧场版 96:08 之类），秒固定两位、允许小数秒。
_TC = r"(\d{1,3}):(\d{2}(?:\.\d+)?)"
_ANCHOR = re.compile(rf"^S(\d{{1,2}})E(\d{{1,2}})\s+{_TC}(?:\s*[-–~]\s*{_TC})?$", re.I)


def _parse_anchor(raw: str, seg_no: int) -> dict | None:
    """`锚点` 字段原文 → {season, episode, t0, t1, raw}；`锚点: 无 …` 返回 None。

    ADR-0008：锚点是番剧笔记里的分钟级剧情时间码，排片的一等公民——
    双塔检索只证「用词相近」，锚点是写稿阶段就锁定的 Ground Truth。
    `锚点: 无` 必须带理由（机检查），这里只认字。
    """
    if raw.startswith("无"):
        return None
    m = _ANCHOR.fullmatch(raw)
    if not m:
        raise SystemExit(
            f"FAIL 段落 {seg_no} 的 `锚点` 写的是「{raw}」，机器认不了。\n"
            f"     写规范形 `S01E01 17:50` 或区间 `S01E01 17:50-18:20`；"
            f"确实无剧情锚点可写 `锚点: 无（理由）`——锚点是排片的确定性输入，\n"
            f"     写错 = 画面直接指到错误的时间码，比检索错配更糟")

    def _sec(mm: str, ss: str) -> float:
        return int(mm) * 60 + float(ss)

    t0 = _sec(m.group(3), m.group(4))
    t1 = _sec(m.group(5), m.group(6)) if m.group(5) else None
    if t1 is not None and t1 <= t0:
        raise SystemExit(
            f"FAIL 段落 {seg_no} 的 `锚点` 区间「{raw}」终点不在起点之后。\n"
            f"     区间写 `起点-终点`，如 `S01E01 17:50-18:20`")
    return {"season": int(m.group(1)), "episode": int(m.group(2)),
            "t0": t0, "t1": t1, "raw": raw}


def parse_shots(path: Path) -> list[dict]:
    """稿件 → 每段的 配音 / 查询 / 备选 / 人物 / 场景 / 集 / 锚点。

    `tts.parse_script` 只取配音，这里要连分镜一起拿，所以按段落块重新切。
    正则口径与 tts / check_script 保持一致：`## 段落 N` + `配音：`。

    `人物` 与 `场景` 是 ADR-0003 加的通道选择器，二选一，都不写就是现状：

        画面：
          人物: 雪乃                    ← 台词检索 + 角色在场过滤
          查询: 雪之下同学 你果然什么都做得很好啊
          备选: 我只是习惯了而已

        画面：
          场景: 黄昏时分空无一人的天台     ← 画面语义检索，不看台词

    **ADR 原文写的字段名是 `画面`，实现改成了 `场景`**：稿件里 `画面：` 已经是
    包着 `查询/备选` 的块名，同名会让人和正则都分不清哪个是哪个。

    `锚点` 是 ADR-0008 加的排片主通道（2026-08-27）：番剧笔记的分钟级剧情
    时间码直通分镜——写了锚点的段不走检索（起点吸附到含锚点的镜头切点）；
    `锚点: 无（理由）` 是显式声明无锚点可用，退回台词检索作氛围补位。
    锚点自带集号，写了锚点 `集` 字段可省；两个都写且不一致当场报错。

    `集` 是 ADR-0004 加的检索约束（2026-08-09）：锁死本段检索的季/集，
    防止跨季/跨集语义高分顶掉正确画面。格式 `S01E07` / `S1E7`（归一化为
    `S01E07`），中文写法当场报错——集号是机器接口，写错 = 检索范围错了。
    """
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"^##\s*段落\s*\S+\s*$", text, flags=re.M)[1:]
    out = []
    for i, b in enumerate(blocks, 1):
        vo = re.search(r"^配音[：:]\s*(.+)$", b, re.M)
        q = re.search(r"^\s*查询[：:]\s*(.+)$", b, re.M)
        alt = re.search(r"^\s*备选[：:]\s*(.+)$", b, re.M)
        who = re.search(r"^\s*人物[：:]\s*(.+)$", b, re.M)
        scene = re.search(r"^\s*场景[：:]\s*(.+)$", b, re.M)
        ep = re.search(r"^\s*集[：:]\s*(.+)$", b, re.M)
        anc = re.search(r"^\s*锚点[：:]\s*(.+)$", b, re.M)
        if not vo:
            continue
        if who and scene:
            raise SystemExit(
                f"FAIL 段落 {i} 同时写了 `人物` 和 `场景`。**一段只走一个通道**"
                f"（ADR-0003）：台词分数和画面分数不是同一个量，并跑之后没有任何办法"
                f"把它们放在一起排序")
        ep_norm = None
        if ep:
            raw = ep.group(1).strip()
            m = re.fullmatch(r"S(\d{1,2})E(\d{1,2})", raw, re.I)
            if not m:
                raise SystemExit(
                    f"FAIL 段落 {i} 的 `集` 写的是「{raw}」，机器认不了。\n"
                    f"     写规范形 `S01E07`（季两位、集两位，都补齐），别写中文「第一季第七集」"
                    f"——集号是检索约束，写错 = 检索范围错了")
            ep_norm = f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
        anchor = None
        anchor_none = False
        if anc:
            anchor = _parse_anchor(anc.group(1).strip(), i)
            anchor_none = anchor is None
            if anchor is not None:
                if who or scene:
                    raise SystemExit(
                        f"FAIL 段落 {i} 同时写了 `锚点` 和 `{'人物' if who else '场景'}`。"
                        f"锚点段不检索（ADR-0008），人物/场景过滤器用不上，"
                        f"写了只会被静默忽略——删掉其中一个")
                akey = f"S{anchor['season']:02d}E{anchor['episode']:02d}"
                if ep_norm and ep_norm != akey:
                    raise SystemExit(
                        f"FAIL 段落 {i} 的 `集`（{ep_norm}）与 `锚点`（{akey}）不是同一集。\n"
                        f"     两个字段指同一处剧情，写叉了排片必错——以引用核对为准改成一个")
                ep_norm = ep_norm or akey       # 锚点自带集号，`集` 字段可省
        out.append({
            "index": i,
            "text": vo.group(1).strip(),
            "query": q.group(1).strip() if q else "",
            "alt": alt.group(1).strip() if alt else None,
            "person": who.group(1).strip() if who else None,
            "scene": scene.group(1).strip() if scene else None,
            "episode": ep_norm,
            "anchor": anchor,
            "anchor_none": anchor_none,
        })
    if not out:
        raise SystemExit(f"FAIL 没从 {path} 解析出任何段落")
    return out


def _overlaps(cand, chosen) -> bool:
    """候选与已选片段是否指向同一处画面。"""
    for c in chosen:
        if (c["season"], c["episode"]) != (cand["season"], cand["episode"]):
            continue
        a0, a1 = cand["start"], cand["start"] + cand["span"]
        b0, b1 = c["start"], c["start"] + c["span"]
        if a0 < b1 + OVERLAP_GAP and b0 < a1 + OVERLAP_GAP:
            return True
    return False


def candidate(score: float, u, sources: dict, anime: str | None = None,
              floor: float = NO_MATCH, presence: float | None = None) -> dict | None:
    """一个检索命中 → 一个可切的片段。不可用返回 None。

    **每个片段都要过 NO_MATCH，不只是每段的第一个。** 初版只卡 `hits[0]`，
    后面凑时长的片段照单全收——段 5 因此混进一个 0.417 的一色选举片段，
    而那段配音讲的是修学旅行。凑时长不是接受画文不符的理由。

    番名再校验一次。`load_all` 已经按番过滤过，这里是第二道——代价是一次字符串比较，
    而漏掉的后果是**切出别的番的画面且全程不报错**。这类错今天踩了一整天，
    多一道显式判断换掉一次静默错，值。

    `presence` 是在场分（0.0 = 未检出）：只落盘给人看，**不参与任何排序**
    （ADR-0004 带内次级排序发生在 `_by_character`，这里已是定序后的结果）。
    """
    if anime is not None and u.anime != anime:
        return None
    if score < floor:                   # 门槛随通道走，画面通道有自己的一条
        return None
    src = sources.get(f"S{u.season:02d}E{u.episode:02d}")
    if src is None:                 # 该集没登记（没验过或没下完）——不许用
        return None
    start = max(0.0, u.start - PAD)
    dur = max(u.end - start, MIN_CLIP)
    # 截取守卫第 1 条：切之前校验，不足就放弃这个候选而不是截断
    if start + dur > src["duration"]:
        dur = src["duration"] - start
        if dur < MIN_CLIP:
            return None
    return {
        "season": u.season, "episode": u.episode, "source": src["path"],
        "start": round(start, 3), "dur": round(dur, 3),
        # 台词自身的跨度，只用于判断「是不是同一处画面」，不参与排版
        "span": round(u.end - start, 3), "limit": src["duration"],
        "score": round(float(score), 4), "line": u.text[:60],
        "presence": round(float(presence), 3) if presence is not None else None,
    }


def _anchor_candidate(anchor: dict, sources: dict, anime: str) -> dict | None:
    """锚点 → 单个片段，起点吸附到镜头切点（ADR-0008）。不可用返回 None。

    **吸附的是起点，不是时长。** 锚点指向「那一刻」，而切进一个镜头的中间
    会让观众先看半秒上个镜头的尾巴；所以起点取「含锚点的镜头」的切点。
    时长交给 `size()` 水填——往后多放几秒是同一场戏的延续，天然成立。
    区间锚点（t1）的自然跨度 = 含起点镜头切点 → 含终点镜头的尾切点。

    与 `candidate()` 同形（season/episode/source/start/dur/span/limit/score/line/
    presence），下游 `_overlaps` / `size` / render 零改动。score 置 None——
    锚点没有检索分，review 页按既有约定显示「手工」档（非检索产物）。
    """
    from . import shots as _shots      # 函数内 import：run() 里有同名局部变量
    key = f"S{anchor['season']:02d}E{anchor['episode']:02d}"
    src = sources.get(key)
    if src is None:                     # 该集没登记——与 candidate() 同一条规矩
        return None
    table = _shots.load(anime, key)["shots"]    # load 自带切分参数一致性校验
    s0 = _shots.at(table, anchor["t0"])
    if s0 is None:                      # 锚点超出片长
        return None
    start = s0["start"]
    if anchor["t1"] is not None:
        s1 = _shots.at(table, anchor["t1"])
        end = (s1 or table[-1])["end"]
    else:
        end = s0["end"]
    span = max(end - start, 0.0)
    dur = max(span, MIN_CLIP)
    # 截取守卫与 candidate() 同源：超出片尾就放弃，不靠 ffmpeg 静默截断
    if start + dur > src["duration"]:
        dur = src["duration"] - start
        if dur < MIN_CLIP:
            return None
    return {"season": anchor["season"], "episode": anchor["episode"],
            "source": src["path"], "start": round(start, 3),
            "dur": round(dur, 3), "span": round(span, 3),
            "limit": src["duration"], "score": None,
            "line": f"锚点 {anchor['raw']}", "presence": None}


def size(chosen: list[dict], need: float) -> tuple[list[dict], str]:
    """把选好的片段裁到总长精确等于 need 秒。

    1. **凑不满时拉长已选片段，不要退而求其次拿弱命中填。** 同一场戏多放两秒
       仍然对题，换一个不相干的镜头就不对题了。
    2. **超出时不能等比例缩。** 初版只裁末尾，段 17 被裁到 1.8s；改等比例后
       段 6 被压到 1.72s——等比例保的是相对长短，保不住下限。
    """
    if not chosen:
        return [], "no_source"

    # 先定片段数上限，再水填。
    #
    # **不能用等比例缩。** 等比例保的是各片段的相对长短，保不住下限——段 6 的自然
    # 时长是 [8.0, 3.0, 2.5] 而只需要 9.3s，等比例乘 0.689 就把末尾压到 1.72s，
    # 比 MIN_CLIP 还短。先按 need // MIN_CLIP 砍掉放不下的片段，剩下的每个先垫够
    # MIN_CLIP，多出来的时长再按自然长短分，下限就成了结构保证而不是事后检查。
    cap = max(1, int(need // MIN_CLIP))
    chosen = chosen[:cap]                       # hits 已按分数降序，截断即保留最强的
    n = len(chosen)

    # 可用余量要算**两个方向**：向后到 limit，向前到 floor。
    # 初版只算向后，段 11 因此被误判 short——它向后只有 5.12s（被段 12 挡住），
    # 但向前还有 3.6s 空画面没人用，合起来足够。
    room = [c["limit"] - c["start"] for c in chosen]
    back = [c["start"] - c.get("floor", 0.0) for c in chosen]
    if sum(room) + sum(back) < need - 0.05:     # 两头都拉满仍然不够
        for c in chosen:
            c.pop("limit", None); c.pop("span", None); c.pop("floor", None)
        return chosen, "short"

    base = min(MIN_CLIP, need / n)
    extra = need - base * n
    weights = [c["dur"] for c in chosen]
    wsum = sum(weights) or 1.0
    for c, w, rm in zip(chosen, weights, room):
        c["dur"] = round(min(base + extra * w / wsum, rm), 3)

    # 被上限卡住而少掉的时长，补给还有余量的片段
    drift = round(need - sum(c["dur"] for c in chosen), 3)
    for c, rm in zip(chosen, room):
        if drift <= 0.0005:
            break
        add = min(drift, rm - c["dur"])
        if add > 0:
            c["dur"] = round(c["dur"] + add, 3)
            drift = round(drift - add, 3)

    # 还不够就**往前拉**：提前一点起切，在剪辑上完全正常（先看到人物再听见台词）。
    # 只往后拉是个不必要的限制——2026-07-29 实测，段 11 向后被段 12 挡住差 0.96s，
    # 而它前面有 3.5 秒没人用的画面。往前的边界是上一个已分配片段的结尾（floor）。
    for c in chosen:
        if drift <= 0.0005:
            break
        back = min(drift, c["start"] - c.get("floor", 0.0))
        if back > 0:
            c["start"] = round(c["start"] - back, 3)
            c["dur"] = round(c["dur"] + back, 3)
            drift = round(drift - back, 3)

    for c in chosen:
        c.pop("limit", None); c.pop("span", None); c.pop("floor", None)
    if drift > 0.05:
        return chosen, "short"
    return chosen, "ok"


# 段级不变量（SEG_TOL / verify_alignment / refit）在 `pipeline/align.py`：
# 单独叶子模块，review/render 的纯文件校验不因 import 本模块（连带
# sentence_transformers）而背上 ML 依赖。本模块的 `--refit` 与 size() 语义
# 从那里 import。
from .align import SEG_TOL, refit, verify_alignment  # noqa: F401  （re-export：既有调用方 `clips.verify_alignment` 不改名）


def _allocate(live, by_index, sources, anime, quota, pre=()):
    """全局贪心分配，段内按带内序尝试。返回已占用的片段列表。

    `pre`：锚点段（ADR-0008）已定死的片段——它们是一等公民，先占位，
    检索段的 `_overlaps` 判定自然绕着它们走，不会抢走锚点指向的画面。

    每段一个指针，按 `hits` 的**段内序**（`_by_character` 已做带内修正：
    presence 只在台词分差 ≤ `PRESENCE_BAND` 的同一档内决胜负，漏检垫底）
    逐个尝试；每轮从未满段的当前候选中选 (通道, 台词分) 最高的试一次。
    跨段仍按台词分贪心（S10：presence 绝不进跨段比较），段内尝试顺序
    被完整保留。失败的候选（切不了 / 与已占重叠）推进指针，下轮试下一个。

    **不能摊平成全局池**：池排序键 (通道, 台词分) 会把段内带内序抹掉——
    稳定排序只保留严格同分序，presence 端到端只剩「同分优先」，等于没实现。
    2026-08-09 实测确认：段内差 0.023 的两个候选，在场 0.973 的没能排在
    0.969 前面（ADR-0004 的带内语义在 pool 层失效）。这就是重写成指针轮转
    的原因。
    """
    used: list[dict] = list(pre)
    got = {p["index"]: 0.0 for p in live}
    pointer = {p["index"]: 0 for p in live}
    while True:
        best_key, best = None, None
        for p in live:
            i = p["index"]
            if got[i] >= quota[i] or pointer[i] >= len(p["hits"]):
                continue
            sc, u, *rest = p["hits"][pointer[i]]
            key = (0 if p["channel"] == "line" else 1, -float(sc))
            if best_key is None or key < best_key:
                best_key, best = key, (p, sc, u, rest[0] if rest else None)
        if best is None:
            return used
        p, sc, u, pr = best
        pointer[p["index"]] += 1
        cand = candidate(sc, u, sources, anime, floor=p["threshold"], presence=pr)
        if cand is None or _overlaps(cand, used):
            continue
        p["clips"].append(cand)
        got[p["index"]] += cand["dur"]
        used.append(cand)


def _parse_ep_scope(episode: str | None) -> tuple[list, int]:
    """段落 `集` 字段 → 作用域链 + 初始 scope。

    写了集 → `[(S,E), (S,None), (None,None)]`（集→季→全空间），初始 scope=0；
    没写 → `[(None,None)]`（全空间，与今天逐字节一致），初始 scope=2。
    scope 是降级级别的记录：0=集级命中、1=季级、2=全空间。
    """
    if not episode:
        return [(None, None)], 2
    m = re.fullmatch(r"S(\d+)E(\d+)", episode)
    s, e = int(m.group(1)), int(m.group(2))
    return [(s, e), (s, None), (None, None)], 0


def _ladder(shot: dict, vecs, units) -> tuple[list, str, int, int]:
    """查询阶梯：`查询` → `备选` → `配音` 原文，逐级重试直到 top-1 够格。

    返回（命中列表, 实际用的查询, 用到第几级, 最终 scope）。

    **只救不比。** 上一级够格就立刻返回，绝不因为下一级分数更高而换掉。
    这一条是硬的，2026-07-30 实测过反面：配音原文在 21 段里有 11 段分数最高，
    按分数取最优很诱人，但它会毁掉已经对的段落——

    - 段 17 的查询是逐字引语「别跟我道歉 我讨厌你的做法」，0.855；配音原文 0.559
    - 段 4 的配音全文就是「雪之下雪乃。」，0.750；可那个高分来自「有人说出她名字」，
      **镜头拍的是说话人，不是她**

    分数量的是「旁白与字幕的语义相似度」，不是「这个镜头是否呈现旁白讲的那一刻」。
    两者只在查询写成台词语义时才重合。**一个量能用来卡门槛，不代表能用来排序**——
    同一条教训第三次出现（前两次是拉普拉斯方差给封面排序、回读 CER 判轴）。

    门槛 `NO_MATCH` 不随级数放松：加大的是找的力度，不是放低的标准。

    **ADR-0004：作用域链（集→季→全空间）。** 集号是剧情知识，比语义分更强；
    「只救不比」同构延伸到检索空间——集级出及格命中就立刻返回，**即使季级/全空间
    级会有更高的分也不许换**。降级只发生在「集级没及格」时，且每次降级都记 scope
    落进 04-clips.json 给 05 看。末端全空间 = 没写集字段的今天的行为。
    """
    best: tuple[list, str, int] = ([], shot["query"], 1)
    best_scope = 2
    scopes, first_scope = _parse_ep_scope(shot.get("episode"))
    for scope, (s, e) in enumerate(scopes, start=first_scope):
        for rung, q in enumerate((shot["query"], shot["alt"], shot["text"]), 1):
            if not q:
                continue
            hits = search(q, vecs, units, TOPK, season=s, episode=e)
            if hits and hits[0][0] >= NO_MATCH:
                return hits, q, rung, scope
            # 全级都不够格时报最高分那次：它最能说明「差多少」，
            # 而报最后一次只是碰巧的顺序。
            if hits and (not best[0] or hits[0][0] > best[0][0][0]):
                best = hits, q, rung
                best_scope = scope
    return best[0], best[1], best[2], best_scope


def scene_no_match(anime: str) -> float:
    """画面通道自己的门槛。**按番存，没有默认值，缺配置就失败。**

    ADR-0003 写死：「不许沿用台词通道的 0.45，两个空间不同」。
    台词分数量的是「旁白文本与字幕文本的语义相似度」，画面分数量的是
    「这个镜头与这句中文描述在 CLIP 空间里的相似度」——两个 0.45 长得一样纯属巧合。

    定法：拿一组本片绝不可能有的画面查询打这个索引，看 Top-1 落在哪（噪声地板），
    再看真实查询落在哪，取中间。跑 `python -m pipeline.vprobe scene <番> <集>`。

    **为什么按番存而不是放 `visual` 那个全局块**：这个数是拿该番的反例查询、
    在该番的镜头上量出来的，换一部番就不再成立。它和量它用的那两张查询表
    存在同一个地方（`config/scenes.json`），改了查询表就一眼看得出旁边这个数可疑了。
    """
    # 显式传 `vindex.SCENES` 而不是靠它的默认参数：默认参数在 def 时就绑死了，
    # 测试改不动它，于是这条失败路径**测不了**——而它正是要保证会失败的那条
    t = vindex.scene_conf(anime, vindex.SCENES).get("no_match")
    if t is None:
        raise SystemExit(
            f"FAIL 有段落声明了 `场景`，但 config/scenes.json 的《{anime}》没有 no_match。\n"
            f"     画面通道的门槛必须**按番**另测另定，不许沿用台词通道的 0.45，\n"
            f"     也不许照抄别的番量出来的数（ADR-0003）：\n"
            f"     python -m pipeline.vprobe scene {anime} <集号>")
    return float(t)


def _ladder_scene(shot: dict, svecs, sunits, thr: float) -> tuple[list, str, int, int]:
    """画面通道的查询阶梯：`场景` → `备选`。**只有两级。**

    台词通道的第 3 级是「配音原文」，画面通道没有对应物——拿一整句口播去查 CLIP
    的图像空间，问的是「哪个镜头看起来像这句话」，那不是一个有意义的问题。
    **阶梯只在通道内部升级**：不许因为画面通道两级都不及格就改走台词通道，
    那等于用换通道来降标准（ADR-0003）。

    作用域链（集→季→全空间）与台词通道的 `_ladder` 同构（ADR-0004）；
    画面通道当前虽不可用（探针没过，写 `场景` 当场报错），机制先摆好，
    免得将来启用时又要补。
    """
    best: tuple[list, str, int] = ([], shot["scene"], 1)
    best_scope = 2
    scopes, first_scope = _parse_ep_scope(shot.get("episode"))
    for scope, (s, e) in enumerate(scopes, start=first_scope):
        for rung, q in enumerate((shot["scene"], shot["alt"]), 1):
            if not q:
                continue
            hits = vindex.search_scene(q, svecs, sunits, TOPK, season=s, episode=e)
            if hits and hits[0][0] >= thr:
                return hits, q, rung, scope
            if hits and (not best[0] or hits[0][0] > best[0][0][0]):
                best = hits, q, rung
                best_scope = scope
    return best[0], best[1], best[2], best_scope


def _by_character(hits: list, pres, name: str) -> tuple[list, bool]:
    """角色在场参与排序：**带内次级排序**（ADR-0004）。

    旧实现是布尔硬过滤：「没检出 X」就把镜头整段枪毙。可侧脸、背影、遮挡都会
    漏——2026-08-09 段 19 的 S01E08 正确画面（0.653）就是这么被滤掉的，
    排片退回跨季 S04E05。检测的不对称（ADR-0003）决定了「没检出」不能当
    「不在场」用，所以：

    1. **不再删镜头**。每个命中都带在场分（没检出 = 0.0），排在检出者后面
       而不是枪毙——漏检的代价从「正确画面整段消失」降为「垫底」。
    2. **带内次级排序**：presence 只在台词分同桶内（差 ≤ PRESENCE_BAND）
       做 tie-break；台词分差超过带子，台词分绝对优先。结构性不可能顶掉
       清晰的台词命中，不踩 S2 判据（没有复合分，同一轴细粒度化）。
    3. **软过滤退回保留**：一个都没检出（kept 空）或第一命中低于门槛时，
       退回原 hits 原顺序（fell_back=True），一字不改。

    返回的命中是 (台词分, unit, 在场分) 三元组；退回时三元组照带，顺序不变。

    **这一条不写下来，三个月后一定会有人把它改成硬过滤。**
    """
    tagged = [(sc, u, pres.presence_score(u.season, u.episode, u.start, u.end, name))
              for sc, u in hits]
    present_hits = [h for h in tagged if h[2] > 0.0]
    absent = [h for h in tagged if h[2] == 0.0]     # 漏检者垫底，保留不枪毙
    # 全漏检，或检出者里的最高台词分仍不及格 → 退回原顺序（软过滤底线，ADR-0003）。
    # 顺序判定用检出者而非 tagged 全体：漏检的 0.8 不该替它本家 0.30 的及格背书。
    if not present_hits or present_hits[0][0] < NO_MATCH:
        return tagged, True
    present_hits.sort(key=lambda h: h[0], reverse=True)   # 检出者内台词分降序
    # **相邻对修正**：相邻两个检出候选台词分差 ≤ PRESENCE_BAND 且后者在场分
    # 更高 → 交换。逐对扫描到无交换为止。候选数 ≤ TOPK=24，冒泡开销可忽略。
    #
    # 为什么不用桶化（floor(score/BAND)）？0.50 和 0.52 差 0.02 < BAND，
    # 但 0.51 是桶边界，floor 会把它们分进两个桶，presence 白带——桶化的
    # 「同桶」和「差 ≤ BAND」不是一回事。相邻对修正没有边界，语义精确：
    # presence 只对**分差小到可以算同一档的相邻候选**生效。
    changed = True
    while changed:
        changed = False
        for i in range(len(present_hits) - 1):
            a, b = present_hits[i], present_hits[i + 1]
            if a[0] - b[0] <= PRESENCE_BAND and b[2] > a[2]:
                present_hits[i], present_hits[i + 1] = b, a
                changed = True
    return present_hits + absent, False


def run(episode: Path, index_dir: Path = INDEX_DIR,
        anime: str | None = None) -> Path:
    script = episode / "02-script.md"
    manifest = episode / "03-audio" / "manifest.json"
    for f in (script, manifest):
        if not f.exists():
            raise SystemExit(f"FAIL 缺 {f}")

    anime = anime or paths.conf("anime.default")
    if not anime:
        raise SystemExit("FAIL 没指定番名：给 --anime，或在 config/project.json 里设 anime.default")

    shots = parse_shots(script)
    audio = json.loads(manifest.read_text(encoding="utf-8"))["segments"]
    if len(shots) != len(audio):
        raise SystemExit(
            f"FAIL 稿件 {len(shots)} 段、配音 {len(audio)} 段，对不上。"
            f"稿件改过就要重跑 `pipeline.tts`"
        )

    sources = load_sources(anime)
    vecs, units = load_all(index_dir, anime)

    # 两个视觉通道**按需加载**：没有段落声明 `人物` / `场景` 时，
    # 这一整套索引都不必存在，排片行为与从前一模一样。
    pres = vindex.load_presence(anime) if any(s["person"] for s in shots) else None
    if pres is not None:
        # **「名表里有」不等于「索引里有」。** 没有任何簇贴给他的角色，过滤永远为空、
        # 永远退回，每一段看起来都像漏检——而它其实是 Phase 0 的贴名没做完。
        # 这两种情况的修法完全不同，所以必须分开报。
        indexed = pres.indexed()
        # `tag_of` 顺带把名字写错的情况在这里就报掉，不等到逐段过滤时才发现
        missing = sorted(p for p in {s["person"] for s in shots if s["person"]}
                         if pres.tag_of(p) not in indexed)
        if missing:
            print(f"  注意  {'、'.join(missing)} 在角色名表里，但视觉索引里一个镜头都没有——"
                  f"这几段的角色过滤会全部退回。\n"
                  f"        多半是没有簇贴给他：python -m pipeline.faces sheet {anime}")
    svecs = sunits = None
    if any(s["scene"] for s in shots):
        svecs, sunits = vindex.load_scene(anime)

    # 先把每段的候选查出来，再决定谁先挑画面。
    prep = []
    for shot, a in zip(shots, audio):
        fell_back = False
        if shot["anchor"] is not None:          # 锚点通道（ADR-0008）：不检索，直通镜头表
            prep.append({**shot, "duration": a["duration"], "hits": [],
                         "used_query": None, "fallback": False, "rung": 1,
                         "ep_scope": 0, "ep_fell_back": False,
                         "channel": "anchor", "threshold": NO_MATCH,
                         "filter_fell_back": False, "top_score": None,
                         "anchor_cand": _anchor_candidate(shot["anchor"], sources, anime),
                         "anchor_conflict": False})
            continue
        if shot["scene"]:                       # 画面语义通道
            floor = scene_no_match(anime)
            hits, used_query, rung, scope = _ladder_scene(shot, svecs, sunits, floor)
            channel = "scene"
        else:                                   # 台词通道（现状）
            hits, used_query, rung, scope = _ladder(shot, vecs, units)
            channel, floor = "line", NO_MATCH
            if shot["person"]:
                hits, fell_back = _by_character(hits, pres, shot["person"])
        prep.append({**shot, "duration": a["duration"], "hits": hits,
                     "used_query": used_query, "fallback": rung > 1, "rung": rung,
                     "ep_scope": scope,
                     # 没写集字段的段落 scope=2 只是「本来就没锁」，不是降级；
                     # 降级只对写了集号却未在集级及格的情况成立（ADR-0004）
                     "ep_fell_back": bool(shot["episode"]) and scope > 0,
                     "channel": channel, "threshold": floor,
                     "filter_fell_back": fell_back,
                     # 段级门槛用**重排前**的最高分（hits 进来时按台词分降序，
                     # max 即首位）。取重排后的 hits[0] 会被带内交换拖下水：
                     # 0.46 与 0.42 差 ≤ PRESENCE_BAND 且后者在场分高时交换，
                     # 首位变 0.42 < 0.45 → 段被误判 no_match，而及格的 0.46
                     # 明明还在候选里（2026-08-16 审计 2-3）。带内重排只该
                     # 改变分配的尝试顺序，不许改「这段有没有找到」的答案。
                     "top_score": round(float(max(h[0] for h in hits)), 4) if hits else 0.0})

    # **按片段分配，不按段落分配。** 同一处画面整期只能用一次，所以这是个分派问题，
    # 而分派的单位是「某段对某个画面的诉求」，不是段落本身。
    #
    # 两版都错过：按段落顺序分配是先到先得，段 13 拿平冢老师那句当填充（0.508）
    # 就把它占了，而段 15 逐字引用那句话（0.601）只能捡剩的；改成按段落 top_score
    # 排序仍然错，因为冲突发生在具体画面上而不是段落上——段 1 的首选是 0.759，
    # 于是它连带把 0.547 的填充画面也优先占了，而段 11 对同一画面的诉求是 0.556，
    # 更强却排在后面，最后无片可用。
    #
    # **现行实现是指针轮转（见 `_allocate` docstring）**，不是早期版本的
    # 「三元组摊平全局贪心」——摊平池的排序键会抹掉段内带内序，ADR-0004 的
    # presence 语义在池层失效，2026-08-09 实测后重写。跨段仍按台词分贪心
    # （S10：presence 绝不进跨段比较），分配顺序与播放顺序无关。
    #
    # **两个通道分两轮，绝不把两种分数放在一起排序。** 这是 ADR-0003 最硬的一条：
    # 台词的 0.7 和画面的 0.7 不是同一个量，长得像纯属巧合。全局按分数贪心时若把
    # 两种分数混在一个序里，排出来的顺序照样存在、照样单调、照样能取 Top-K，
    # **只是它不对应任何真实的东西**——这正是本项目最难修的那类 bug 的定义。
    #
    # 冲突仍然要解（两段可能都想要同一处画面），解法是**显式的通道优先级**而不是
    # 比分数：台词段先挑。理由是它们要的是「那句话发生的那一刻」，具体且不可替换；
    # 画面段要的是氛围，可替换的余地大得多。
    # 命中形状：写了 `人物` 的台词段经 _by_character 后是 (台词分, unit, 在场分)
    # 三元组；画面段与未写人物的台词段仍是 (分, unit) 二元组。*rest 兼容两种，
    # 在场分取不出来就是 None。**presence 绝不进跨段比较**（S10 的落实点）：
    # _allocate 每轮跨段只比当前候选的台词分，presence 只决定段内候选的尝试顺序。
    # 锚点段先定死（一等公民，ADR-0008）：检索段的分配绕着它们走。
    # 锚段之间撞车不自动挪——两段锚同一处多半是写稿问题，标出来交 05 处理，
    # 不静默移花接木。
    placed: list[dict] = []
    for p in prep:
        cand = p.get("anchor_cand")
        if cand is None:
            continue
        if _overlaps(cand, placed):
            p["anchor_cand"] = None
            p["anchor_conflict"] = True
        else:
            placed.append(cand)

    by_index = {p["index"]: p for p in prep}
    live = [p for p in prep if p["hits"] and p["top_score"] >= p["threshold"]]

    # 分配和排版互为前提：排版能拉多长取决于「下一个片段占在哪」，
    # 而下一个片段占在哪又取决于分配。所以迭代——每轮给上一轮没填满的段多分一个片段。
    # 三轮足够：实测第二轮就收敛。
    quota = {p["index"]: p["duration"] for p in live}
    for _ in range(3):
        for p in prep:
            p["clips"] = []
        used = _allocate(live, by_index, sources, anime, quota, pre=placed)

        # 拉伸上限收到「同一集里下一个已分配片段的起点」，不能只收到片尾。
        # 2026-07-29 实测：段 11 的 15:18 被拉到 5.8s 之后撞进了段 12 的 15:24。
        by_ep: dict[tuple[int, int], list[dict]] = {}
        for c in used:
            by_ep.setdefault((c["season"], c["episode"]), []).append(c)
        for cs in by_ep.values():
            cs.sort(key=lambda c: c["start"])
            for a, b in zip(cs, cs[1:]):
                a["limit"] = min(a["limit"], b["start"] - OVERLAP_GAP)
                b["floor"] = round(a["start"] + a["span"] + OVERLAP_GAP, 3)


        short = []
        for p in live:
            _, status = size([dict(c) for c in p["clips"]], p["duration"])
            if status == "short":
                short.append(p["index"])
        if not short:
            break
        for idx in short:              # 下一轮多要一点，好多分到一个片段
            quota[idx] += MIN_CLIP

    for p in prep:
        if p.get("channel") == "anchor":
            if p["anchor_conflict"]:
                p["status"], p["clips"] = "anchor_overlap", []
            elif p["anchor_cand"] is None:
                # 该集没登记或锚点超出片长——与检索落空同一个终态，交 05 人工指定
                p["status"], p["clips"] = "no_match", []
            else:
                p["clips"], p["status"] = size([p["anchor_cand"]], p["duration"])
            continue
        if not p["hits"] or p["top_score"] < p["threshold"]:
            p["status"], p["clips"] = "no_match", []
            continue
        p["clips"], p["status"] = size(p["clips"], p["duration"])

    out = [{k: v for k, v in p.items() if k not in ("hits", "got", "anchor_cand",
                                                    "anchor_conflict")}
           for p in sorted(prep, key=lambda x: x["index"])]

    dest = episode / "04-clips.json"
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(json.dumps({
        "anime": anime,
        "total_duration": sum(s["duration"] for s in out),
        "segments": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dest)        # 原子落盘（审计 2-27）：产物即状态，不许半份
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--anime", default=paths.conf("anime.default"))
    ap.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    ap.add_argument("--refit", action="store_true",
                     help="人审改过 start/source 之后，把每段 dur 重排到满足段级不变量。"
                          "只读写 04-clips.json，不碰检索——别跟不带 --refit 的正常调用搞混，"
                          "后者会重新检索并覆盖人改结果")
    a = ap.parse_args()

    if a.refit:
        src = a.episode / "04-clips.json"
        if not src.exists():
            raise SystemExit(f"FAIL 缺 {src}")
        manifest_path = a.episode / "03-audio" / "manifest.json"
        if not manifest_path.exists():
            raise SystemExit(f"FAIL 缺 {manifest_path}")
        data = json.loads(src.read_text(encoding="utf-8"))
        audio = json.loads(manifest_path.read_text(encoding="utf-8"))["segments"]
        sources = load_sources(data["anime"])
        segments, report = refit(data["segments"], audio, sources)
        data["segments"] = segments
        # refit 改的是人审（--approve）之后的产物，先备份再原子重写（审计 2-27）：
        # 写一半崩溃连「人改过什么样」都恢复不了
        (src.with_name(src.name + ".prerefit.bak")
         ).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        tmp = src.with_name(src.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, src)
        print("\n".join(report) if report else "已对齐，无需调整")
        print(f"→ {src}")
        return 0

    dest = run(a.episode, a.index_dir, a.anime)
    data = json.loads(dest.read_text(encoding="utf-8"))
    segs = data["segments"]

    bad = 0
    for s in segs:
        eps = sorted({f"S{c['season']:02d}E{c['episode']:02d}" for c in s["clips"]})
        mark = "    " if s["status"] == "ok" else "★   "
        if s["status"] != "ok":
            bad += 1
        # 用到第几级要显示出来：第 2、3 级说明那条 `查询` 写得不行，
        # 是回去改稿的信号，不是可以忽略的细节。
        rung = {2: "  [第2级·备选]", 3: "  [第3级·配音原文]"}.get(s.get("rung", 1), "")
        # 通道与过滤要显示出来：**画面分数和台词分数不可比**，不标出来看的人会拿
        # 一列数横着比。退回也要显示——它说明那一段的角色过滤没起作用。
        chan = ("锚点" if s.get("channel") == "anchor"
                else "画面" if s.get("channel") == "scene" else "台词")
        who = f"·{s['person']}" if s.get("person") else ""
        if s.get("filter_fell_back"):
            who += "(过滤为空→退回)"
        # 集号与降级要显示出来：集级没命中掉到季内/全空间，说明集号或查询
        # 写得不稳，是回去改稿的信号（ADR-0004）；没写集字段的段不显示。
        ep = f"·{s['episode']}" if s.get("episode") else ""
        if s.get("ep_fell_back"):
            ep += " (→季内)" if s.get("ep_scope") == 1 else " (→全空间)"
        # 锚点段没有检索分（ADR-0008）：分数位显示锚点原文，别让人拿
        # 一个不存在的分数横着比。
        score = (f"{s['top_score']:.3f}" if s.get("top_score") is not None
                 else (s.get("anchor") or {}).get("raw", "—"))
        print(f"{mark}段{s['index']:2d}  {s['duration']:5.1f}s  "
              f"{len(s['clips'])} 片段  {chan}{who} {score}  "
              f"{'/'.join(eps) or '—'}  {s['status']}{rung}{ep}")

    n_clips = sum(len(s["clips"]) for s in segs)
    print("-" * 66)
    print(f"{len(segs)} 段 / {n_clips} 个片段 / {data['total_duration']:.1f}s → {dest}")
    if bad:
        print(f"★ {bad} 段不是 ok，渲染前必须处理")
    print("下一步：人抽检时间码，确认后另存为 04-clips.approved.json")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
