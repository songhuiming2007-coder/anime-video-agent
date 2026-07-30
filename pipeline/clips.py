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
import re
from pathlib import Path

from . import paths

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


def parse_shots(path: Path) -> list[dict]:
    """稿件 → 每段的 配音 / 查询 / 备选。

    `tts.parse_script` 只取配音，这里要连分镜一起拿，所以按段落块重新切。
    正则口径与 tts / check_script 保持一致：`## 段落 N` + `配音：`。
    """
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"^##\s*段落\s*\S+\s*$", text, flags=re.M)[1:]
    out = []
    for i, b in enumerate(blocks, 1):
        vo = re.search(r"^配音[：:]\s*(.+)$", b, re.M)
        q = re.search(r"^\s*查询[：:]\s*(.+)$", b, re.M)
        alt = re.search(r"^\s*备选[：:]\s*(.+)$", b, re.M)
        if not vo:
            continue
        out.append({
            "index": i,
            "text": vo.group(1).strip(),
            "query": q.group(1).strip() if q else "",
            "alt": alt.group(1).strip() if alt else None,
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


def candidate(score: float, u, sources: dict, anime: str | None = None) -> dict | None:
    """一个检索命中 → 一个可切的片段。不可用返回 None。

    **每个片段都要过 NO_MATCH，不只是每段的第一个。** 初版只卡 `hits[0]`，
    后面凑时长的片段照单全收——段 5 因此混进一个 0.417 的一色选举片段，
    而那段配音讲的是修学旅行。凑时长不是接受画文不符的理由。

    番名再校验一次。`load_all` 已经按番过滤过，这里是第二道——代价是一次字符串比较，
    而漏掉的后果是**切出别的番的画面且全程不报错**。这类错今天踩了一整天，
    多一道显式判断换掉一次静默错，值。
    """
    if anime is not None and u.anime != anime:
        return None
    if score < NO_MATCH:
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
    }


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


def _ladder(shot: dict, vecs, units) -> tuple[list, str, int]:
    """查询阶梯：`查询` → `备选` → `配音` 原文，逐级重试直到 top-1 够格。

    返回（命中列表, 实际用的查询, 用到第几级）。

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
    """
    best: tuple[list, str, int] = ([], shot["query"], 1)
    for rung, q in enumerate((shot["query"], shot["alt"], shot["text"]), 1):
        if not q:
            continue
        hits = search(q, vecs, units, TOPK)
        if hits and hits[0][0] >= NO_MATCH:
            return hits, q, rung
        # 全级都不够格时报最高分那次：它最能说明「差多少」，
        # 而报最后一次只是碰巧的顺序。
        if hits and (not best[0] or hits[0][0] > best[0][0][0]):
            best = hits, q, rung
    return best


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

    # 先把每段的候选查出来，再决定谁先挑画面。
    prep = []
    for shot, a in zip(shots, audio):
        hits, used_query, rung = _ladder(shot, vecs, units)
        prep.append({**shot, "duration": a["duration"], "hits": hits,
                     "used_query": used_query, "fallback": rung > 1, "rung": rung,
                     "top_score": round(float(hits[0][0]), 4) if hits else 0.0})

    # **按片段分配，不按段落分配。** 同一处画面整期只能用一次，所以这是个分派问题，
    # 而分派的单位是「某段对某个画面的诉求」，不是段落本身。
    #
    # 两版都错过：按段落顺序分配是先到先得，段 13 拿平冢老师那句当填充（0.508）
    # 就把它占了，而段 15 逐字引用那句话（0.601）只能捡剩的；改成按段落 top_score
    # 排序仍然错，因为冲突发生在具体画面上而不是段落上——段 1 的首选是 0.759，
    # 于是它连带把 0.547 的填充画面也优先占了，而段 11 对同一画面的诉求是 0.556，
    # 更强却排在后面，最后无片可用。
    #
    # 正解是把所有 (段, 画面, 分数) 三元组摊平，全局按分数降序贪心。
    # 分配顺序与播放顺序无关，最后按 index 还原。
    pool = sorted(
        ((float(sc), p["index"], u) for p in prep for sc, u in p["hits"]),
        key=lambda t: -t[0],
    )
    by_index = {p["index"]: p for p in prep}
    live = [p for p in prep if p["hits"] and p["top_score"] >= NO_MATCH]

    # 分配和排版互为前提：排版能拉多长取决于「下一个片段占在哪」，
    # 而下一个片段占在哪又取决于分配。所以迭代——每轮给上一轮没填满的段多分一个片段。
    # 三轮足够：实测第二轮就收敛。
    quota = {p["index"]: p["duration"] for p in live}
    for _ in range(3):
        for p in prep:
            p["clips"] = []
        used: list[dict] = []
        got = {p["index"]: 0.0 for p in live}
        for sc, idx, u in pool:
            p = by_index.get(idx)
            if p is None or idx not in got or got[idx] >= quota[idx]:
                continue
            cand = candidate(sc, u, sources, anime)
            if cand is None or _overlaps(cand, used):
                continue
            p["clips"].append(cand)
            got[idx] += cand["dur"]
            used.append(cand)

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
        if not p["hits"] or p["top_score"] < NO_MATCH:
            p["status"], p["clips"] = "no_match", []
            continue
        p["clips"], p["status"] = size(p["clips"], p["duration"])

    out = [{k: v for k, v in p.items() if k not in ("hits", "got")}
           for p in sorted(prep, key=lambda x: x["index"])]

    dest = episode / "04-clips.json"
    dest.write_text(json.dumps({
        "anime": anime,
        "total_duration": sum(s["duration"] for s in out),
        "segments": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--anime", default=paths.conf("anime.default", "春物"))
    ap.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    a = ap.parse_args()

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
        print(f"{mark}段{s['index']:2d}  {s['duration']:5.1f}s  "
              f"{len(s['clips'])} 片段  {s.get('top_score', 0):.3f}  "
              f"{'/'.join(eps) or '—'}  {s['status']}{rung}")

    n_clips = sum(len(s["clips"]) for s in segs)
    print("-" * 66)
    print(f"{len(segs)} 段 / {n_clips} 个片段 / {data['total_duration']:.1f}s → {dest}")
    if bad:
        print(f"★ {bad} 段不是 ok，渲染前必须处理")
    print("下一步：人抽检时间码，确认后另存为 04-clips.approved.json")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
