"""候选复核探针（P1，`docs/plans/2026-08-14-p1-recheck-probe.md`）。

**这是探针工具，不是复核层。** ADR-0005 的推翻条件 2 写死：若探针显示复核本身
也不可靠，候选方向作废——所以复核逻辑（04.5 新机器步）在探针过线之前不许进
主流程，本文件只产出「量化 / 生成工作单 / 判分」三个命令，不改 `pipeline/clips.py`。

    python -m pipeline.recheck diff data/episodes/<期>       # D6：机器 vs 人改 diff 量化
    python -m pipeline.recheck diff --all                    # 扫全部期
    python -m pipeline.recheck probe data/episodes/<期> [--n 8]   # 生成复核工作单
    python -m pipeline.recheck score data/episodes/<期>           # 复核 verdicts × label 判分

复核会话本身不在这里跑——工作单生成即止，复核由零上下文 agent 读
`04-recheck-worklist.md` 完成，跑不跑、谁来跑由用户决定（方案「本方案交付什么」）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import paths

from . import review
from . import vindex
from .clips import _by_character, _ladder, parse_shots
from .ingest import load_sources
from .subindex import INDEX_DIR, load_all

# 手工注记扫描：键名匹配这几个词根，大小写不敏感（ADR-0005 先例 `_manual_fix`）。
# 只认键名不认值——注记可能写中文说明，值本身不适合拿去做正则匹配。
ANNOTATION_KEY = re.compile(r"manual|手改|手修|fix", re.I)

# 判定线（方案第三节写死，不许现场发明；数字理由见方案原文「数字理由」段）。
DETECT_RATE_MIN = 0.60
FALSE_REJECT_MAX = 0.15


# ---------------------------------------------------------------------------
# 第一节：diff（D6 错配量化）
# ---------------------------------------------------------------------------


def _round_clip(c: dict) -> tuple:
    """clip → 可比对的元组。比对前把 float 全部 round 3 位，别把舍入差报成错配。"""
    return (c.get("source"), c.get("season"), c.get("episode"),
            round(c.get("start", 0.0), 3), round(c.get("dur", 0.0), 3))


def _annotation_notes(seg: dict) -> dict:
    """段（含其 clips）里所有键名匹配 `ANNOTATION_KEY` 的键值对。

    人是在 `04-clips.json` 上就地改的（ADR-0005 先例：改 start + 留 `_manual_fix`
    注记，再拷贝成 approved）——这类段落即使坐标算出来「没变」，注记本身就是
    「人工判错过」的 ground truth，必须先于坐标比对被认出来。
    """
    out: dict = {}
    for k, v in seg.items():
        if ANNOTATION_KEY.search(k):
            out[k] = v
    for c in seg.get("clips") or []:
        for k, v in c.items():
            if ANNOTATION_KEY.search(k):
                out[k] = v
    return out


def bucket_segment(machine_seg: dict, approved_seg: dict) -> tuple[str, dict]:
    """单段分桶。返回 (bucket, 发现的注记键值对)。

    顺序即优先级：注记 > human_filled > count_changed > content_changed >
    start_shifted > dur_changed > unchanged——同一段可能同时满足好几条
    （比如内容变了、时长也变了），取错配主嫌疑最重的那个。
    """
    notes = {**_annotation_notes(approved_seg), **_annotation_notes(machine_seg)}
    if notes:
        return "annotated_bad", notes

    m_clips = machine_seg.get("clips") or []
    a_clips = approved_seg.get("clips") or []

    # 机器 no_match（或产物为空）、approved 却有内容 = 人从候选池外自己填的
    if (machine_seg.get("status") == "no_match" or not m_clips) and a_clips:
        return "human_filled", notes

    if len(m_clips) != len(a_clips):
        return "count_changed", notes

    m_norm = [_round_clip(c) for c in m_clips]
    a_norm = [_round_clip(c) for c in a_clips]
    if m_norm == a_norm:
        return "unchanged", notes
    if any(m[:3] != a[:3] for m, a in zip(m_norm, a_norm)):
        return "content_changed", notes
    if any(m[3] != a[3] for m, a in zip(m_norm, a_norm)):
        return "start_shifted", notes
    return "dur_changed", notes


def auto_label(bucket_name: str) -> str | None:
    """`dur_changed` 段自动视为 good（排版微调，非语义错配）。

    `unchanged` 故意不在这里自动填 good——它是唯一允许 `label` 保持 null 的桶
    （方案第二节：探针生成器拒收 label 为 null 的非 unchanged 段）。它压根不需要
    复核：机器和人的最终选择逐位相同，没有分歧可判。
    """
    return "good" if bucket_name == "dur_changed" else None


def _load_existing_labels(ep_dir: Path) -> dict[int, tuple[str, str | None]]:
    """已有 report 里 index -> (bucket, label)，供重跑 diff 时不冲掉人工标注。"""
    p = ep_dir / "04-mismatch-report.json"
    if not p.exists():
        return {}
    out: dict[int, tuple[str, str | None]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        out[row["index"]] = (row["bucket"], row.get("label"))
    return out


def diff_episode(ep_dir: Path) -> dict | None:
    """对一期跑 diff，写 `04-mismatch-report.json`（JSONL，每段一行）。

    **重跑不冲掉已标注的 label**：bucket 没变时沿用已有 label；bucket 变了
    （比如重跑过 clips.py 换了候选）说明旧标注可能已经对不上新内容，重置为待标注。
    两个文件都不存在、或都不齐（04-clips.json 单独存在但没 approved）时返回
    不同的 status，调用方决定怎么显示——不许静默跳过（判据 9）。
    """
    m_path, a_path = ep_dir / "04-clips.json", ep_dir / "04-clips.approved.json"
    if not m_path.exists() and not a_path.exists():
        return None
    if not m_path.exists():
        return {"episode": ep_dir.name, "status": "missing_machine"}
    if not a_path.exists():
        return {"episode": ep_dir.name, "status": "missing_approved"}

    m = json.loads(m_path.read_text(encoding="utf-8"))
    a = json.loads(a_path.read_text(encoding="utf-8"))
    m_by_idx = {s["index"]: s for s in m["segments"]}
    a_by_idx = {s["index"]: s for s in a["segments"]}
    common = sorted(set(m_by_idx) & set(a_by_idx))
    missing_index = sorted(set(m_by_idx) ^ set(a_by_idx))

    existing = _load_existing_labels(ep_dir)
    rows: list[dict] = []
    ann_keys: set[str] = set()
    for idx in common:
        b, notes = bucket_segment(m_by_idx[idx], a_by_idx[idx])
        ann_keys |= set(notes)
        label = auto_label(b)
        prev = existing.get(idx)
        if label is None and prev is not None and prev[0] == b and prev[1] is not None:
            label = prev[1]
        rows.append({
            "index": idx, "bucket": b, "label": label,
            "machine": m_by_idx[idx].get("clips") or [],
            "human": a_by_idx[idx].get("clips") or [],
            "note": "; ".join(f"{k}: {v}" for k, v in notes.items()) if notes else None,
        })

    report_path = ep_dir / "04-mismatch-report.json"
    report_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    return {
        "episode": ep_dir.name, "status": "ok", "total": len(rows),
        "differs": sum(v for k, v in counts.items() if k != "unchanged"),
        "counts": counts, "annotation_keys": sorted(ann_keys),
        "missing_index": missing_index, "report": report_path,
    }


def _print_diff(r: dict) -> None:
    if r.get("status") == "missing_machine":
        print(f"{r['episode']}: 缺 04-clips.json（机器基线丢失，无法比对）")
        return
    if r.get("status") == "missing_approved":
        print(f"{r['episode']}: 缺 04-clips.approved.json（尚未审核，跳过）")
        return
    print(f"{r['episode']}: {r['differs']}/{r['total']} 段 differ")
    for k in ("unchanged", "content_changed", "start_shifted", "dur_changed",
              "count_changed", "human_filled", "annotated_bad"):
        if k in r["counts"]:
            print(f"    {k}: {r['counts'][k]}")
    if r["annotation_keys"]:
        print(f"    发现注记键: {', '.join(r['annotation_keys'])}")
    if r["missing_index"]:
        print(f"    注意 段号不对齐（只在一侧出现）: {r['missing_index']}")
    print(f"    -> {r['report']}")


# ---------------------------------------------------------------------------
# 第二节：probe（生成复核工作单）
# ---------------------------------------------------------------------------


def load_report(ep_dir: Path) -> list[dict]:
    p = ep_dir / "04-mismatch-report.json"
    if not p.exists():
        raise SystemExit(f"FAIL 缺 {p}，先跑 `python -m pipeline.recheck diff {ep_dir}`")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _episode_units(units: list) -> dict[tuple[int, int], list]:
    """units → 按 (season, episode) 分组、组内保持原有时间序的列表。

    `load_all` 按文件 glob 顺序拼接多集索引，但**同一集内部**的单元本就是
    `subindex.parse` 按 `start` 排过序再滑窗切出来的，过滤时原样保留顺序即可
    还原「相邻」关系，不需要重新排序。
    """
    out: dict[tuple[int, int], list] = {}
    for u in units:
        out.setdefault((u.season, u.episode), []).append(u)
    return out


def _context(ep_units: list, u) -> tuple[str, str]:
    """候选 unit 在同集单元序列里的前一句 / 后一句文本，跨边界写「无」。

    `Unit` 是非 frozen dataclass（`__hash__` 被置空，不能进 set/当 dict key），
    用 `is` 身份比较定位，比按字段相等更准确——理论上可能存在内容完全相同
    的两个不同 unit（重复台词），身份比较不会认错。
    """
    idx = next((i for i, x in enumerate(ep_units) if x is u), None)
    if idx is None:
        return "无", "无"
    prev_t = ep_units[idx - 1].text if idx > 0 else "无"
    next_t = ep_units[idx + 1].text if idx < len(ep_units) - 1 else "无"
    return prev_t, next_t


def _rerun_candidates(shot: dict, vecs, units, pres) -> tuple[list, str]:
    """复现机器视角的候选序列：重跑 `_ladder`（有 `人物` 再过 `_by_character`）。

    **确定性前提：索引与模型没变。** `_ladder`/`search` 是纯函数式检索
    （同一份向量 + 同一份查询文本 → 同一组分数），`subindex._check` 在加载索引时
    已经把模型身份（`model_id`/`revision`）钉死、对不上就当场 `SystemExit`——
    这正是「重跑 = 当时机器顺序」这条前提成立的证据链，不是假设。

    **跨段分配冲突导致的降级不在探针范围。** `clips.run()` 的 `_allocate` 是全局
    贪心分配，两段抢同一处画面时谁能拿到取决于其它段的状态；探针只重跑单段的
    `_ladder`，判的是「候选本身排得对不对」，不判「多段之间谁该让谁」。
    """
    hits, used_query, _rung, _scope = _ladder(shot, vecs, units)
    if shot.get("person"):
        hits, _fell_back = _by_character(hits, pres, shot["person"])
    return hits, used_query


def _pseudo_clip(u, sources: dict) -> dict | None:
    """候选 unit → 抽帧用的伪 clip。该集片源未登记时返回 None（跳过抽帧，不报错）。"""
    src = sources.get(f"S{u.season:02d}E{u.episode:02d}")
    if src is None:
        return None
    return {"start": u.start, "dur": u.end - u.start, "source": src["path"]}


def _hhmmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _format_candidate(rank: int, score: float, u, prev_t: str, next_t: str,
                       frame_paths: list[Path], ep_dir: Path) -> str:
    imgs = " / ".join(str(p.relative_to(ep_dir)) for p in frame_paths) \
        if frame_paths else "（该集片源未登记，无法抽帧）"
    return (
        f"### 候选 {rank}（台词分 {score:.3f}）S{u.season:02d}E{u.episode:02d} "
        f"{_hhmmss(u.start)}-{_hhmmss(u.end)}\n"
        f"- 台词上下文：前一句「{prev_t}」｜ 本句「{u.text}」｜ 后一句「{next_t}」\n"
        f"- 画面三帧：{imgs}\n"
    )


def _format_segment(row: dict, shot: dict, used_query: str, cand_blocks: list[str]) -> str:
    person = shot.get("person") or "无"
    episode = shot.get("episode") or "无"
    header = (
        f"## 段 {row['index']} ｜ 口播：{shot['text']}\n"
        f"查询: {used_query} ｜ 人物: {person} ｜ 集: {episode}\n\n"
        f"以下 {len(cand_blocks)} 个候选是检索为这段口播找的画面。逐个判断："
        f"这个画面**拿来配这段口播**能不能用。\n"
        f"判 match / not_match / unsure，各给一句理由。不许参照其他段，不许猜测对错比例。\n"
    )
    return header + "\n".join(cand_blocks)


WORKLIST_INTRO = """# {episode} —— 候选复核工作单

你是一个**零上下文**的复核 agent。你只会拿到这一个文件，看不到任何项目文档、
代码、或产生这个文件的会话——下面是你需要知道的全部背景。

## 背景

这是一条动漫二创视频流水线的「排片」步骤：口播文案每一段配音都要配一段动漫
画面，画面是拿这段口播的文字去检索字幕语义索引找的。检索用的是双塔嵌入模型
（bge-base-zh-v1.5），它只能判断「用词/语气相近」，判不了「这一刻是不是文案
讲的那一刻」——这正是你要帮着补上的判断力。

下面每个「段」块列出机器检索排出的候选画面（按检索分数降序），每个候选给了：
分数、集号时间码、台词上下文（前一句/本句/后一句）、三张抽帧（进点/中间/出点）。

## 你的任务

对**每一个**候选（不只是第一个），逐个判断：**这个画面拿来配这段口播能不能用**。

- `match`：画面对得上这段口播讲的内容
- `not_match`：画面文不对题（比如镜头拍的是别人、演的是别的情节）
- `unsure`：看三帧和上下文判断不了，说不清

每个判断配一句理由（一句话，别写小作文）。

**规则（不许违反）：**
1. **不许参照其他段**——每个段落独立判断，不许「这段和上一段差不多所以…」。
2. **不许猜测对错比例**——不要因为「按经验错配率大概多少」调整判断，只看眼前这一个候选。
3. 判断依据只能是：候选给出的分数、集号时间码、台词上下文、三张抽帧。不许假设你「记得」这部番的剧情。

## 输出格式

判断完全部段落后，在**这一期的目录**（跟这个工作单同一个目录）写一个文件
`04-recheck-verdicts.json`，格式：

```json
{{
  "segments": [
    {{"index": 7, "judgments": [
      {{"rank": 1, "verdict": "not_match", "reason": "镜头拍说话人，不是口播讲的人"}},
      {{"rank": 2, "verdict": "match", "reason": "台词与画面对应，同一场对话"}}
    ]}}
  ]
}}
```

- `index` = 段号（下面每个 `## 段 N` 标题里的 N）
- `judgments` 里每个候选一条，`rank` 对应下面「候选 N」的 N
- **每个列出的候选都要判断，不许漏、不许只判第一个**
- 写完就结束，不用做别的——判分由另一条命令（`recheck score`）读这个文件完成

---

"""


def probe_episode(ep_dir: Path, index_dir: Path = INDEX_DIR, n: int = 8) -> Path:
    """对已标注（label 非 null）的段生成复核工作单 `04-recheck-worklist.md`。

    对每个有 label 的段：`parse_shots` 取该段 → `_rerun_candidates` 复现机器候选
    序列 → 取前 n 个 → 每个候选造伪 clip、调 `review._frames`（复用，不重写 ffmpeg
    逻辑）抽三帧、取同集相邻单元的文本当上下文 → 写进工作单。

    同时写 `04-recheck-candidates.json`：记录每段每个候选的 (season, episode,
    start, end)，供 `score` 算「人选复核率」时把 approved clip 对回候选序号——
    verdicts.json 只有 rank+verdict，没有这份候选清单则无法知道某个 rank 具体
    对应哪一段画面。
    """
    script = ep_dir / "02-script.md"
    clips_path = ep_dir / "04-clips.json"
    for f in (script, clips_path):
        if not f.exists():
            raise SystemExit(f"FAIL 缺 {f}")

    rows = load_report(ep_dir)
    eligible = [r for r in rows if r["label"] in ("bad", "good")]
    skipped = [r["index"] for r in rows
               if r["label"] is None and r["bucket"] != "unchanged"]
    if not eligible:
        raise SystemExit(
            f"FAIL {ep_dir} 没有已标注（label 非 null）的段，无候选可探——"
            f"先按方案第二节人工标注 04-mismatch-report.json")

    shots = {s["index"]: s for s in parse_shots(script)}
    for row in eligible:
        if row["index"] not in shots:
            raise SystemExit(
                f"FAIL 段 {row['index']} 在 04-mismatch-report.json 里，"
                f"但 02-script.md 解析不出对应段落（稿件改过？）")

    anime = json.loads(clips_path.read_text(encoding="utf-8"))["anime"]
    sources = load_sources(anime)
    vecs, units = load_all(index_dir, anime)
    ep_units_index = _episode_units(units)
    pres = vindex.load_presence(anime) if any(
        shots[r["index"]].get("person") for r in eligible) else None

    frames_dir = ep_dir / "probe-frames"
    frames_dir.mkdir(exist_ok=True)

    blocks: list[str] = []
    manifest_segments: list[dict] = []
    for row in eligible:
        idx = row["index"]
        shot = shots[idx]
        hits, used_query = _rerun_candidates(shot, vecs, units, pres)
        top = hits[:n]
        cand_blocks, cand_rows = [], []
        for rank, item in enumerate(top, start=1):
            sc, u = item[0], item[1]
            clip = _pseudo_clip(u, sources)
            frame_paths = review._frames(clip, frames_dir, f"{idx:02d}-{rank}") if clip else []
            prev_t, next_t = _context(ep_units_index.get((u.season, u.episode), []), u)
            cand_blocks.append(_format_candidate(rank, sc, u, prev_t, next_t, frame_paths, ep_dir))
            cand_rows.append({"rank": rank, "score": round(float(sc), 4),
                               "season": u.season, "episode": u.episode,
                               "start": round(u.start, 3), "end": round(u.end, 3)})
        blocks.append(_format_segment(row, shot, used_query, cand_blocks))
        manifest_segments.append({"index": idx, "candidates": cand_rows})

    tail = ""
    if skipped:
        tail = (f"\n---\n（以下段号在错配报告里但尚未标注 label，本次未生成候选，"
                f"跳过：{', '.join(str(i) for i in skipped)}）\n")

    worklist = ep_dir / "04-recheck-worklist.md"
    worklist.write_text(
        WORKLIST_INTRO.format(episode=ep_dir.name) + "\n---\n\n".join(blocks) + tail,
        encoding="utf-8")
    (ep_dir / "04-recheck-candidates.json").write_text(
        json.dumps({"segments": manifest_segments}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return worklist


# ---------------------------------------------------------------------------
# 第三节：score（复核 verdicts × label 判分）
# ---------------------------------------------------------------------------


def _top1_verdict(judgments: list[dict]) -> str | None:
    return next((j["verdict"] for j in judgments if j.get("rank") == 1), None)


def _matches_human_clip(cand: dict, human_clip: dict) -> bool:
    """候选是否对应人最终选中的那个 clip（人选复核率用）。

    候选记的是检索单元的原始 `[start, end)`；approved clip 的 `start` 经过
    `clips.candidate()` 的 PAD/MIN_CLIP 调整，两者数值上不会逐位相等，
    用**区间相交**判断「说的是同一处画面」，而不是要求坐标完全相同。
    """
    if (cand["season"], cand["episode"]) != (human_clip["season"], human_clip["episode"]):
        return False
    c0, c1 = cand["start"], cand["end"]
    h0, h1 = human_clip["start"], human_clip["start"] + human_clip["dur"]
    return c0 < h1 and h0 < c1


def compute_score(rows: list[dict], verdicts_segments: list[dict],
                   candidates_by_index: dict[int, list[dict]]) -> dict:
    """四个指标：检出率 / 误拒率 / unsure 率 / 人选复核率（辅）。

    **label 为 null 的非 unchanged 段直接拒收（`SystemExit`）**，不静默跳过——
    静默跳过会让百分比悄悄算在一个不完整的子集上，而使用者以为覆盖的是全部
    verdicts（判据 9：跳过不是通过）。
    """
    rows_by_index = {r["index"]: r for r in rows}
    bad_total = bad_detected = 0
    good_total = good_false_rejected = 0
    unsure_total = scored_total = 0
    review_total = review_matched = 0

    for seg in verdicts_segments:
        idx = seg["index"]
        row = rows_by_index.get(idx)
        if row is None:
            raise SystemExit(f"FAIL verdicts 段 {idx} 不在 04-mismatch-report.json 里")
        label, bkt = row["label"], row["bucket"]
        if label is None and bkt != "unchanged":
            raise SystemExit(
                f"FAIL 段 {idx}（bucket={bkt}）的 label 仍是 null，不许带糊数据进判分——"
                f"先在 04-mismatch-report.json 里标注 bad/good/skip")
        if label == "skip":
            # skip = 人工弃权（「说不清」），不是 ground truth 也不是待测样本：
            # 不进任何指标的分母（方案第二节「弃权」语义）。段本身正常过了
            # probe（工作单里不会有它——eligible 只收 bad/good），verdicts 里
            # 出现它多半是复核会话越权多判了，这里静默忽略即可。
            continue

        judgments = seg.get("judgments") or []
        top1 = _top1_verdict(judgments)
        if top1 is None:
            raise SystemExit(f"FAIL 段 {idx} 的 judgments 里没有 rank=1 的判定")

        scored_total += 1
        if top1 == "unsure":
            unsure_total += 1
        if label == "bad":
            bad_total += 1
            if top1 in ("not_match", "unsure"):
                bad_detected += 1
        elif label == "good":
            good_total += 1
            if top1 == "not_match":
                good_false_rejected += 1

        if bkt == "content_changed":
            human_clips = row.get("human") or []
            cands = candidates_by_index.get(idx, [])
            if human_clips and cands:
                match_rank = next(
                    (c["rank"] for c in cands if _matches_human_clip(c, human_clips[0])), None)
                if match_rank is not None:
                    review_total += 1
                    verdict = next(
                        (j["verdict"] for j in judgments if j.get("rank") == match_rank), None)
                    if verdict == "match":
                        review_matched += 1

    return {
        "检出率": (bad_detected / bad_total) if bad_total else None,
        "误拒率": (good_false_rejected / good_total) if good_total else None,
        "unsure率": (unsure_total / scored_total) if scored_total else None,
        "人选复核率": (review_matched / review_total) if review_total else None,
        "counts": {"bad": bad_total, "good": good_total, "scored": scored_total,
                   "content_changed_matched": review_total},
    }


def verdict_line(metrics: dict) -> str:
    """按方案第三节写死的判定线，对照算出的两个数给出结论。不现场发明阈值。"""
    d, f = metrics["检出率"], metrics["误拒率"]
    if d is None or f is None:
        return "样本不足（label=bad 或 label=good 均为 0 段），无法对照判定线"
    if d < DETECT_RATE_MIN:
        return f"方向作废：检出率 {d:.0%} < {DETECT_RATE_MIN:.0%}（ADR-0005 推翻条件 2 命中）"
    if f > FALSE_REJECT_MAX:
        return (f"误拒率 {f:.0%} > {FALSE_REJECT_MAX:.0%}：上下文不够（复核被表面相似误导）。"
                f"按方案只许重跑一轮：重跑前需先给 _context/probe 加 ±2 unit 上下文与 "
                f"--n 缩小的参数（当前尚未实现，实现前按作废处理，不许现场发明）")
    return (f"方向成立：检出率 {d:.0%} >= {DETECT_RATE_MIN:.0%} 且误拒率 {f:.0%} <= "
            f"{FALSE_REJECT_MAX:.0%}，第四节接入规格转正式 ADR")


def score_episode(ep_dir: Path) -> dict:
    rows = load_report(ep_dir)
    v_path = ep_dir / "04-recheck-verdicts.json"
    if not v_path.exists():
        raise SystemExit(f"FAIL 缺 {v_path}，先完成复核会话（见 04-recheck-worklist.md）")
    c_path = ep_dir / "04-recheck-candidates.json"
    if not c_path.exists():
        raise SystemExit(f"FAIL 缺 {c_path}，先跑 `python -m pipeline.recheck probe {ep_dir}`")
    verdicts = json.loads(v_path.read_text(encoding="utf-8"))["segments"]
    candidates = json.loads(c_path.read_text(encoding="utf-8"))["segments"]
    candidates_by_index = {c["index"]: c["candidates"] for c in candidates}
    return compute_score(rows, verdicts, candidates_by_index)


def _print_score(m: dict) -> None:
    def pct(x):
        return "—" if x is None else f"{x:.1%}"
    c = m["counts"]
    print(f"检出率:     {pct(m['检出率'])}  (bad={c['bad']})")
    print(f"误拒率:     {pct(m['误拒率'])}  (good={c['good']})")
    print(f"unsure率:   {pct(m['unsure率'])}  (scored={c['scored']})")
    print(f"人选复核率: {pct(m['人选复核率'])}  (content_changed 命中={c['content_changed_matched']})")
    print(verdict_line(m))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="D6 错配量化：机器 04-clips.json vs 人改 approved 逐段分桶")
    d.add_argument("episode", type=Path, nargs="?")
    d.add_argument("--all", action="store_true", help="扫 data/episodes/ 下所有期")
    d.add_argument("--episodes-dir", type=Path, default=paths.EPISODES)

    p = sub.add_parser("probe", help="对已标注段生成候选复核工作单")
    p.add_argument("episode", type=Path)
    p.add_argument("--n", type=int, default=8)
    p.add_argument("--index-dir", type=Path, default=INDEX_DIR)

    s = sub.add_parser("score", help="按复核 verdicts × label 判分")
    s.add_argument("episode", type=Path)

    a = ap.parse_args()
    paths.require_data()

    if a.cmd == "diff":
        if a.all:
            dirs = sorted(x for x in a.episodes_dir.iterdir()
                          if x.is_dir() and not x.name.startswith("."))
            results = [r for r in (diff_episode(x) for x in dirs) if r is not None]
            for r in results:
                _print_diff(r)
            ok = [r for r in results if r.get("status") == "ok"]
            print("=" * 66)
            print(f"{len(ok)} 期可比对 / {len(results)} 期有 04-clips 相关产物；"
                  f"D6 量化：{sum(r['differs'] for r in ok)} 段 differ / "
                  f"{sum(r['total'] for r in ok)} 段总数")
            return 0
        if not a.episode:
            raise SystemExit("FAIL 给一个 episode 目录，或用 --all")
        r = diff_episode(a.episode)
        if r is None:
            raise SystemExit(f"FAIL {a.episode} 没有 04-clips.json 或 04-clips.approved.json")
        _print_diff(r)
        return 0

    if a.cmd == "probe":
        dest = probe_episode(a.episode, a.index_dir, a.n)
        print(f"-> {dest}")
        return 0

    metrics = score_episode(a.episode)
    _print_score(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
