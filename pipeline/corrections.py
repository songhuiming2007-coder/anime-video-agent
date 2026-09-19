"""顺听极简纠错与期级 overlay 管理（Spec §3）。

包含：
  - 拼音音节切分器 split_pinyin_syllables
  - 纠错行文法解析器 parse_correction
  - corrections.json 落盘与写者纪律（写前指纹校验、单写者、id分配、回读确认）
  - 期级 overlay 加载与生效表生成 load_overlay / effective_injections
  - 增量重配计划 plan_apply
  - 快照备份与按段回滚 backup_segments / revert_segment / retract_correction
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from pypinyin.contrib.tone_convert import to_tone3
from pypinyin.pinyin_dict import pinyin_dict

from . import g2p, paths


class PatchError(Exception):
    """纠错行文法解析错误。带解析状态、原文与正确写法示例。"""


@dataclass
class Patch:
    segment: str  # 稿件段号 label
    kind: str  # "pronunciation" | "timbre"
    word: str | None
    heard: str | None
    target_tone3: str | None  # 如 "zhong4die2"
    issue: str | None
    action: str  # "inject" | "pin_seed"
    scope: str  # "segment"（默认）| "global"
    raw: str


# 受控听感词（换种子）与禁用语速词（无合成支持，明确报错）
CONTROLLED_TIMBRE_WORDS = ("发飘", "断层", "不稳", "闷", "机械", "吞字", "杂音", "赶")
SPEED_WORDS = ("太快", "太慢", "偏快", "偏慢", "有点快", "有点慢")

# 拼音音节与调号常量
_TONE_MARKS = set("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ")
_SYLLABLES_TONED = {s for v in pinyin_dict.values() for s in v.split(",")}
_TONE3_SYLLABLE = re.compile(r"^[a-züêv]+[1-5]$", re.IGNORECASE)


def split_pinyin_syllables(raw: str) -> list[str]:
    """音节切分器（Spec §3.2.1）。

    三档规则：
      1. 空格分隔：每 token 一个音节；
      2. 无空格但每音节带调号或调号数字：按每音节恰好含一个调号或末尾[1-5]贪心切分；
      3. 其余（无声调、隔音符 'xī'ān'、混写）：报错引导。
    """
    s = raw.replace(",", " ").replace("，", " ").strip()
    if not s:
        raise PatchError("拼音串为空，请提供有效拼音。")

    if "'" in s or "’" in s:
        raise PatchError(
            f"遇到隔音符 '{s}'。请用空格分音节或给每个音节标声调，如 'zhòng dié' 或 'zhong4 die2'。"
        )

    # 1. 空格分隔
    tokens = s.split()
    if len(tokens) > 1:
        # 逐 token 校验
        res = []
        for t in tokens:
            t = t.strip(",")
            # 是否有效单音节
            has_mark = any(ch in _TONE_MARKS for ch in t)
            has_num = bool(_TONE3_SYLLABLE.match(t))
            if has_mark and not has_num:
                if t.lower() in _SYLLABLES_TONED:
                    res.append(t)
                    continue
            elif has_num and not has_mark:
                res.append(t)
                continue
            raise PatchError(
                f"音节 '{t}' 声调不合规。请用空格分音节或给每个音节标声调，如 'zhòng dié' 或 'zhong4 die2'。"
            )
        return res

    # 单 token：无空格
    token = tokens[0].strip(",")

    # 检查是否全无调号且无调号数字
    has_any_mark = any(ch in _TONE_MARKS for ch in token)
    has_any_num = bool(re.search(r"[1-5]", token))

    if not has_any_mark and not has_any_num:
        raise PatchError(
            f"拼音 '{token}' 缺少声调标记。请用空格分音节或给每个音节标声调，如 'zhòng dié' 或 'zhong4 die2'。"
        )

    if has_any_mark and has_any_num:
        raise PatchError(
            f"拼音 '{token}' 混用了调号字母与数字。请用空格分音节或给每个音节标声调，如 'zhòng dié' 或 'zhong4 die2'。"
        )

    # 纯数字调号：如 zhong4die2 或 lv4
    if has_any_num:
        parts = re.findall(r"[a-züêv]+[1-5]", token, re.IGNORECASE)
        if "".join(parts).lower() == token.lower() and parts:
            return parts
        raise PatchError(
            f"拼音 '{token}' 无法按数字调号切分。请用空格分音节，如 'zhong4 die2'。"
        )

    # 纯调号字母：如 zhòngdié 或 lǜ
    n = len(token)

    def _dfs(idx: int) -> list[str] | None:
        if idx == n:
            return []
        for end in range(n, idx, -1):
            cand = token[idx:end]
            # 必须恰好含一个调号字母
            if sum(1 for ch in cand if ch in _TONE_MARKS) == 1:
                if cand.lower() in _SYLLABLES_TONED:
                    rem = _dfs(end)
                    if rem is not None:
                        return [cand] + rem
        return None

    split_res = _dfs(0)
    if split_res is not None:
        return split_res

    raise PatchError(
        f"拼音 '{token}' 无法按声调符号切分。请用空格分音节，如 'zhòng dié'。"
    )


def pinyin_to_tone3(raw: str) -> str:
    """将拼音切分并逐音节转为 TONE3 紧凑串（如 'zhòng dié' -> 'zhong4die2'）。"""
    sylls = split_pinyin_syllables(raw)
    return "".join(to_tone3(s) for s in sylls)


# 段号匹配的三条正则（N1+Y1，Spec §3.2）
_RE_SEG_POSTFIX = re.compile(r"(?:第\s*)?(\d+(?:\.\d+)?)\s*段")
_RE_SEG_PREFIX_FULL = re.compile(r"段落\s*(\d+(?:\.\d+)?)")
_RE_SEG_PREFIX_SHORT = re.compile(r"段\s*(\d+(?:\.\d+)?)")


def parse_correction(line: str, segs: list[Any] | dict[str, str]) -> Patch:
    """纠错行文法解析器（纯函数，Spec §3.2）。"""
    raw_input = line
    line = unicodedata.normalize("NFKC", line).strip()
    if not line:
        raise PatchError("输入为空。")

    # 规范化 segs 字典: label -> text
    if isinstance(segs, dict):
        seg_map = {str(k): str(v) for k, v in segs.items()}
    else:
        seg_map = {}
        for s in segs:
            if hasattr(s, "label") and hasattr(s, "text"):
                seg_map[str(s.label)] = str(s.text)
            elif isinstance(s, dict) and "label" in s and "text" in s:
                seg_map[str(s["label"])] = str(s["text"])

    # 1. 段号解析与多段号检测
    matches: list[tuple[int, int, str]] = []  # (start, end, label)
    for pat in (_RE_SEG_PREFIX_FULL, _RE_SEG_POSTFIX, _RE_SEG_PREFIX_SHORT):
        for m in pat.finditer(line):
            matches.append((m.start(), m.end(), m.group(1)))

    # 去重（可能同一位置被不同正则部分匹配，按区间去重）
    unique_matches: list[tuple[int, int, str]] = []
    for m in sorted(matches, key=lambda x: (x[0], -x[1])):
        if not any(not (m[1] <= u[0] or m[0] >= u[1]) for u in unique_matches):
            unique_matches.append(m)

    if not unique_matches:
        raise PatchError(
            f"未在输入中找到有效段号（如 '5段'、'第5段'、'段5' 或 '段落 5.1'）。可用段号：{sorted(seg_map.keys())}"
        )

    if len(unique_matches) > 1:
        found_segs = [m[2] for m in unique_matches]
        raise PatchError(f"一次只纠一段，请分两条打（检测到多段号: {found_segs}）。")

    start, end, seg_label = unique_matches[0]
    if seg_map and seg_label not in seg_map:
        raise PatchError(
            f"段号 '{seg_label}' 不在稿件段落中。可用段号：{sorted(seg_map.keys())}"
        )

    seg_text = seg_map.get(seg_label, "")

    # 从原行中剔除段号部分
    remainder = (line[:start] + " " + line[end:]).strip()

    # 2. 检查语速词（明确报错拒绝）
    for sw in SPEED_WORDS:
        if sw in remainder:
            raise PatchError(
                f"语速调整暂不支持（改语速会改变段时长、连锁排片 refit），可换种子重试或改稿。"
            )

    # 3. 检查全局范围
    scope = "global" if ("全局" in remainder or "所有段" in remainder) else "segment"
    remainder = remainder.replace("全局", "").replace("所有段", "").strip()

    # 4. 提取拼音簇与关键词
    # 关键词正则
    re_target_kw = re.search(r"(改成|应读|读作)\s*", remainder)
    re_heard_kw = re.search(r"(念成|读成|听成)\s*", remainder)

    # 提取拉丁/拼音簇（包含字母、调号、调数字、引号、逗号空格连接的拉丁字符）
    # Latin cluster regex: matched sequences of latin + tone marks
    latin_cluster_pat = re.compile(
        r"[a-zA-ZāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜvVüÜ1-5',]+(?:\s+[a-zA-ZāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜvVüÜ1-5',]+)*"
    )

    heard: str | None = None
    target_raw: str | None = None

    if re_heard_kw:
        after_heard = remainder[re_heard_kw.end():]
        # 如果后面有改成关键词
        target_in_after = re.search(r"(改成|应读|读作)\s*", after_heard)
        if target_in_after:
            heard_part = after_heard[:target_in_after.start()].strip()
            target_part = after_heard[target_in_after.end():].strip()
            # 从 heard_part 提取拉丁簇
            hm = latin_cluster_pat.search(heard_part)
            if hm:
                heard = hm.group(0).strip(" ,")
            tm = latin_cluster_pat.search(target_part)
            if tm:
                target_raw = tm.group(0).strip(" ,")
        else:
            hm = latin_cluster_pat.search(after_heard)
            if hm:
                heard = hm.group(0).strip(" ,")
            # 检查 heard 关键词前是否有改成
            if re_target_kw and re_target_kw.start() < re_heard_kw.start():
                target_part = remainder[re_target_kw.end():re_heard_kw.start()].strip()
                tm = latin_cluster_pat.search(target_part)
                if tm:
                    target_raw = tm.group(0).strip(" ,")
    elif re_target_kw:
        target_part = remainder[re_target_kw.end():].strip()
        tm = latin_cluster_pat.search(target_part)
        if tm:
            target_raw = tm.group(0).strip(" ,")

    # 如果有 heard 但无 target_raw
    if heard is not None and not target_raw:
        raise PatchError("缺目标读音。请指定目标拼音，如 '改成 zhòng dié'。")

    # 如果既无 target 关键词也无 heard 关键词
    if not target_raw and not heard:
        sep_parts = [p.strip() for p in re.split(r"[,，/、;]+|\s+和\s+|\s+或\s+", remainder) if re.search(r"[a-zA-Z]", p)]
        if len(sep_parts) > 1:
            raise PatchError(
                f"存在多个无关键词拼音簇 {sep_parts}，无法识别目标读音，请使用 '改成/应读/读作' 明确指定。"
            )
        compact_words = [w for w in remainder.split() if re.search(r"[a-zA-Z]", w)]
        multi_syllable_tokens = [
            w for w in compact_words
            if sum(1 for ch in w if ch in _TONE_MARKS) > 1 or len(re.findall(r"[1-5]", w)) > 1
        ]
        if len(multi_syllable_tokens) > 1:
            raise PatchError(
                f"存在多个无关键词拼音簇 {compact_words}，无法识别目标读音，请使用 '改成/应读/读作' 明确指定。"
            )
        clusters = [m.group(0).strip(" ,") for m in latin_cluster_pat.finditer(remainder) if m.group(0).strip(" ,")]
        if len(clusters) == 1:
            target_raw = clusters[0]
        elif len(clusters) > 1:
            raise PatchError(
                f"存在多个无关键词拼音簇 {clusters}，无法识别目标读音，请使用 '改成/应读/读作' 明确指定。"
            )

    # 5. 分支判断：读音分支 vs 听感/换种子分支
    if target_raw:
        # 提取汉字词（剥除关键词和拼音）
        cleaned_text = remainder
        for kw in ("改成", "应读", "读作", "念成", "读成", "听成"):
            cleaned_text = cleaned_text.replace(kw, " ")
        if heard:
            cleaned_text = cleaned_text.replace(heard, " ")
        if target_raw:
            cleaned_text = cleaned_text.replace(target_raw, " ")

        # 找纯汉字词
        han_words = re.findall(r"[\u4e00-\u9fa5]+", cleaned_text)
        # 过滤控制性词汇
        han_words = [w for w in han_words if w not in ("换种子", "种子", "段", "段落")]

        if not han_words:
            raise PatchError(f"未找到待纠正的汉字词。原文：{seg_text}")

        word = han_words[0]
        if seg_text and word not in seg_text:
            raise PatchError(f"词 '{word}' 不在第 {seg_label} 段配音文本中。原文：{seg_text}")

        sylls = split_pinyin_syllables(target_raw)
        # 若无关键词且拼音音节数多于汉字字数，判定为存在多个无关键词拼音簇
        if not re_target_kw and len(sylls) > len(word):
            raise PatchError(
                f"存在多个无关键词拼音簇，无法识别目标读音，请使用 '改成/应读/读作' 明确指定。"
            )

        target_tone3 = "".join(to_tone3(s) for s in sylls)

        return Patch(
            segment=seg_label,
            kind="pronunciation",
            word=word,
            heard=heard,
            target_tone3=target_tone3,
            issue=None,
            action="inject",
            scope=scope,
            raw=raw_input,
        )

    # 无拼音簇：检查听感词
    matched_timbre = None
    for tw in CONTROLLED_TIMBRE_WORDS:
        if tw in remainder:
            matched_timbre = tw
            break

    if matched_timbre:
        return Patch(
            segment=seg_label,
            kind="timbre",
            word=None,
            heard=None,
            target_tone3=None,
            issue=matched_timbre,
            action="pin_seed",
            scope=scope,
            raw=raw_input,
        )

    # 既不是合规读音，也不是合规听感词
    # 特别检查是否有非受控表达（如「音色变了」）
    timbre_keywords = ("音色", "语气", "听感", "换种子", "变了", "声音")
    is_timbre_attempt = any(tk in remainder for tk in timbre_keywords)

    words_list_str = "、".join(CONTROLLED_TIMBRE_WORDS)
    if is_timbre_attempt:
        raise PatchError(
            f"未知听感词或表外表达（如 '音色变了'）。受控听感词表为：{words_list_str}。"
        )

    raise PatchError(
        f"未能识别纠错指令。读音纠错示例：'5段 重叠 念成 chóng dié 改成 zhòng dié'；"
        f"听感纠错示例：'12段 语气发飘 换种子'（受控听感词：{words_list_str}）。"
    )


# ---------------------------------------------------------------------------
# corrections.json 落盘与生命周期契约（Spec §3.3）
# ---------------------------------------------------------------------------


def get_corrections_path(episode: Path) -> Path:
    return episode / "03-audio" / "corrections.json"


def _calc_file_fingerprint(p: Path) -> tuple[float, str]:
    if not p.exists():
        return 0.0, ""
    stat = p.stat()
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    return stat.st_mtime, sha


def load_corrections_raw(episode: Path) -> tuple[list[dict], tuple[float, str]]:
    """读取 corrections.json 原始列表并返回写前指纹 (mtime, sha256)。"""
    p = get_corrections_path(episode)
    if not p.exists():
        return [], (0.0, "")
    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, list):
            raise SystemExit(f"FAIL 03-audio/corrections.json 顶层必须为列表，请修复或移走该文件。")
        return data, _calc_file_fingerprint(p)
    except json.JSONDecodeError:
        raise SystemExit(
            f"FAIL 03-audio/corrections.json 格式损坏，请修复或移走该文件后重试。"
        )


def save_corrections_raw(
    episode: Path,
    entries: list[dict],
    expected_fp: tuple[float, str] | None = None,
) -> None:
    """原子写回 corrections.json，强制执行写前指纹校验。"""
    p = get_corrections_path(episode)
    p.parent.mkdir(parents=True, exist_ok=True)

    if expected_fp is not None:
        cur_fp = _calc_file_fingerprint(p)
        if cur_fp != expected_fp:
            raise SystemExit(
                "FAIL 期间 corrections.json 被其他进程改过，请重跑本命令。"
            )

    # # 每段一次整份重写是刻意的（B3-r16）：40 段 = 40 次全文件写，看着 wasteful，
    # 但「窗口归零 > IO 成本」（文件小），免得将来为了省 IO 改回批量写重新打开假完成状态窗口。
    content = json.dumps(entries, ensure_ascii=False, indent=2)
    paths.atomic_write(p, content)

    # y 之后回读一次确认条目在盘（B2-r16）
    assert p.exists(), "corrections.json 写盘后回读校验失败，文件不存在。"


@contextmanager
def apply_patch_lock(episode: Path):
    """--apply-patch 单写者排他锁（Spec §3.3 Y1-r16）。"""
    lock_file = episode / "03-audio" / ".apply_patch.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.exists():
        raise SystemExit("FAIL 应用纠错进行中（.apply_patch.lock 存在），稍后再试。")
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        if lock_file.exists():
            try:
                lock_file.unlink()
            except OSError:
                pass


def append_correction(episode: Path, patch: Patch) -> dict:
    """将 Patch 条目安全追加至 corrections.json。"""
    # 单写者纪律检查
    lock_file = episode / "03-audio" / ".apply_patch.lock"
    if lock_file.exists():
        raise SystemExit("FAIL 应用纠错进行中，稍后再落盘。")

    entries, fp = load_corrections_raw(episode)

    # id 分配去竞争
    existing_ids = {c.get("id") for c in entries if isinstance(c.get("id"), int)}
    new_id = max(existing_ids, default=0) + 1

    entry: dict[str, Any] = {
        "id": new_id,
        "segment": patch.segment,
        "kind": patch.kind,
        "scope": patch.scope,
        "word": patch.word,
        "heard": patch.heard,
        "target_tone3": patch.target_tone3,
        "action": patch.action,
        "raw": patch.raw,
        "applied": False,
        "applied_at": None,
        "affected": None,
        "done_segments": [],
        "created_at": datetime.now().isoformat(),
    }
    if patch.action == "pin_seed":
        entry["issue"] = patch.issue
        entry["seed_pin"] = random.SystemRandom().randint(1, 999999)

    entries.append(entry)
    save_corrections_raw(episode, entries, expected_fp=fp)

    # 回读确认条目在盘
    check_entries, _ = load_corrections_raw(episode)
    if not any(c.get("id") == new_id for c in check_entries):
        raise RuntimeError(f"条目 #{new_id} 落盘后回读未在文件中发现！")

    return entry


def load_overlay(episode: Path, include_pending: bool = False) -> dict:
    """corrections.json → {"injections": {"*": {词: tone3}, "<label>": {词: tone3}},
                          "segment_seeds": {label: pin}}

    常态只收 applied=true (R3)：pending 条目只在 --apply-patch 流程内生效。
    """
    entries, _ = load_corrections_raw(episode)
    injections: dict[str, dict[str, str]] = {}
    segment_seeds: dict[str, int] = {}

    for c in entries:
        if not include_pending and not c.get("applied", False):
            continue

        action = c.get("action")
        if action == "inject" and c.get("word") and c.get("target_tone3"):
            scope_key = "*" if c.get("scope") == "global" else str(c.get("segment"))
            injections.setdefault(scope_key, {})[c["word"]] = c["target_tone3"]
        elif action == "pin_seed" and c.get("seed_pin") is not None:
            seg_lbl = str(c.get("segment"))
            segment_seeds[seg_lbl] = int(c["seed_pin"])

    return {
        "injections": injections,
        "segment_seeds": segment_seeds,
    }


def effective_injections(overlay: dict | None, label: str | None) -> dict[str, str]:
    """段级生效表 = 全局('*') ∪ 该段的注入。纯函数，精确匹配，不做字符串手术。"""
    if not overlay or "injections" not in overlay:
        return {}
    inj = overlay.get("injections", {})
    res = dict(inj.get("*", {}))
    if label is not None and str(label) in inj:
        res.update(inj[str(label)])
    return res


# ---------------------------------------------------------------------------
# 增量重配计划与快照备份/回滚（Spec §3.5）
# ---------------------------------------------------------------------------


def plan_apply(episode: Path, segs: list[Any], cfg: dict) -> dict:
    """计算 --apply-patch 执行计划。

    返回字典:
      - pending: 待应用的 corrections 原始条目列表
      - affected_labels: 受影响段落 label 列表（affected = redo = backup 集）
      - redo: 需要重配的段落 label 列表
      - overlay: pending-inclusive overlay
    """
    entries, fp = load_corrections_raw(episode)
    pending = [c for c in entries if not c.get("applied", False)]
    if not pending:
        return {"pending": [], "affected_labels": [], "redo": [], "overlay": {}}

    mf_path = episode / "03-audio" / "manifest.json"
    if not mf_path.exists():
        raise SystemExit("FAIL 03-audio/manifest.json 缺失，请先跑 03 配音。")

    try:
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    except Exception as ex:
        raise SystemExit(f"FAIL 03-audio/manifest.json 不可读: {ex}")

    # 旧 manifest 报账闸（Y2）
    old_takes = manifest.get("segments", [])
    no_speakable = [t for t in old_takes if t.get("speakable") is None]
    if no_speakable:
        print(
            f"\n"
            f"************************************************************************\n"
            f"WARN 本期 {len(no_speakable)} 段旧产物无 speakable 字段！\n"
            f"     应用纠错将触发全表指纹刷新，可能导致这 {len(no_speakable)} 段全部重配、审听作废！\n"
            f"************************************************************************\n",
            file=sys.stderr,
        )

    # 提取 pending-inclusive overlay
    overlay = load_overlay(episode, include_pending=True)

    # 计算受影响段：点名段 ∪ (pending-inclusive overlay 下判不可复用的段)
    # 受影响段 = redo = backup 集，一个真源（Y1-r6）
    # 判据：speakable 失配 ∪ 钉种子失配 ∪ 点名段
    from . import tts

    affected_set: set[str] = set()

    # 1. 明确点名段
    for c in pending:
        if c.get("segment"):
            affected_set.add(str(c["segment"]))
        if c.get("scope") == "global" and c.get("word"):
            # global 注入影响包含该词的所有段
            for s in segs:
                if c["word"] in getattr(s, "text", ""):
                    affected_set.add(str(getattr(s, "label", "")))

    # 为每个 pending 条目写回 affected 字段
    changed_entries = False
    for c in pending:
        if c.get("scope") == "global" and c.get("word"):
            c_aff = [
                str(getattr(s, "label", ""))
                for s in segs
                if c["word"] in getattr(s, "text", "")
            ]
        else:
            c_aff = [str(c["segment"])]
        if c.get("affected") != c_aff:
            c["affected"] = c_aff
            changed_entries = True

    # 2. 判据减法计算 redo（不可复用段）
    # redo = affected 中「pending-inclusive overlay 下被 _reusable 判为不可复用」的段
    engine_kind = cfg.get("engine", "qwen3_tts")
    merged_seeds = {**cfg.get("segment_seeds", {}), **overlay.get("segment_seeds", {})}

    takes_by_label = {str(t.get("label")): t for t in old_takes}
    segs_by_label = {str(getattr(s, "label", "")): s for s in segs}

    redo_set: set[str] = set()
    for lbl in affected_set:
        take = takes_by_label.get(lbl)
        seg = segs_by_label.get(lbl)
        if not take or not seg:
            redo_set.add(lbl)
            continue

        # speakable 比对
        if take.get("speakable") is not None:
            cur_spk = tts.speakable(seg.text, engine_kind, overlay=overlay, label=lbl)
            if take["speakable"] != cur_spk:
                redo_set.add(lbl)
                continue

        # 钉种子比对
        cur_pins = tts._seed_pins(seg, merged_seeds)
        old_pins = tuple(take.get("seed_pins")) if take.get("seed_pins") is not None else None
        if old_pins != cur_pins:
            redo_set.add(lbl)
            continue

    # P0-2 收口逻辑：检查 pending 条目中是否有受影响段已全部合成完成（不在 redo_set 中且存在于 manifest）
    # 避免 redo=[] 时直接返回导致条目永久停在 applied=False（静默数据损坏）
    now_iso = datetime.now().isoformat()
    for c in pending:
        c_aff = set(c["affected"]) if c.get("affected") is not None else {str(c.get("segment"))}
        if c_aff and not (c_aff & redo_set) and all(lbl in takes_by_label for lbl in c_aff):
            done_segs = set(c.get("done_segments") or [])
            done_segs.update(c_aff)
            c["done_segments"] = sorted(
                done_segs, key=lambda x: [int(p) if p.isdigit() else 0 for p in x.split(".")]
            )
            c["applied"] = True
            c["applied_at"] = now_iso
            changed_entries = True

    if changed_entries:
        save_corrections_raw(episode, entries, expected_fp=fp)
        _, fp = load_corrections_raw(episode)

    # 若 redo 为空但仍有 pending 条目未能收口，报错拒绝，不许打印假完成
    if not redo_set:
        stuck_ids = [c.get("id") for c in pending if not c.get("applied", False)]
        if stuck_ids:
            raise SystemExit(
                f"FAIL 纠错条目无法收口（条目 id={stuck_ids}），相关段落在 manifest 中缺失或状态异常。"
            )

    redo_list = sorted(redo_set, key=lambda x: [int(p) if p.isdigit() else 0 for p in x.split(".")])
    affected_list = sorted(affected_set, key=lambda x: [int(p) if p.isdigit() else 0 for p in x.split(".")])

    return {
        "pending": pending,
        "affected_labels": affected_list,
        "redo": redo_list,
        "overlay": overlay,
    }


def backup_segments(episode: Path, labels: list[str]) -> Path:
    """受影响段的 seg-NN.wav + manifest.json 拷入 03-audio/attic/<YYYYMMDD-HHMMSS>/ 并修剪旧快照。"""
    attic_dir = episode / "03-audio" / "attic"
    attic_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_dir = attic_dir / ts
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    audio_dir = episode / "03-audio"
    mf_path = audio_dir / "manifest.json"
    if mf_path.exists():
        shutil.copy2(mf_path, snapshot_dir / "manifest.json")
        try:
            mf = json.loads(mf_path.read_text(encoding="utf-8"))
            file_map = {str(t.get("label")): t.get("file") for t in mf.get("segments", [])}
            for lbl in labels:
                fn = file_map.get(lbl)
                if fn and (audio_dir / fn).exists():
                    shutil.copy2(audio_dir / fn, snapshot_dir / fn)
        except Exception as ex:
            print(f"WARN 备份段落音频时解析 manifest 失败: {ex}", file=sys.stderr)

    # 修剪保留策略：保留最近 10 份快照
    _prune_attic(attic_dir, keep=10)
    return snapshot_dir


def _prune_attic(attic_dir: Path, keep: int = 10) -> None:
    """清理超出保留窗的 attic 快照并报账。"""
    snapshots = sorted([p for p in attic_dir.iterdir() if p.is_dir() and not p.name.startswith(".")])
    if len(snapshots) <= keep:
        return

    to_delete = snapshots[:-keep]
    summary_lines = []
    for d in to_delete:
        # 扫描该快照含哪些段
        wav_files = sorted(f.name for f in d.glob("seg-*.wav"))
        summary_lines.append(f"{d.name}（含 {len(wav_files)} 个音频文件: {', '.join(wav_files[:5])}{'...' if len(wav_files)>5 else ''}）")
        shutil.rmtree(d, ignore_errors=True)

    print(
        f"[*] 清理 {len(to_delete)} 份旧快照：\n"
        f"    " + "\n    ".join(summary_lines) + "\n"
        f"    ——若这些段仍需回滚，请先 approve 或手工留存。",
        file=sys.stderr,
    )


def revert_segment(episode: Path, label: str) -> dict:
    """从包含该段的最近 attic 快照恢复 wav 与 manifest 段条目，条目回到 pending。"""
    attic_dir = episode / "03-audio" / "attic"
    if not attic_dir.exists():
        raise SystemExit(f"FAIL 未找到包含段 {label} 的 attic 快照（attic 目录不存在）。")

    snapshots = sorted(
        [p for p in attic_dir.iterdir() if p.is_dir() and not p.name.startswith(".")],
        reverse=True,
    )

    audio_dir = episode / "03-audio"
    matched_snap = None
    target_take = None

    for snap in snapshots:
        snap_mf = snap / "manifest.json"
        if not snap_mf.exists():
            continue
        try:
            data = json.loads(snap_mf.read_text(encoding="utf-8"))
            for t in data.get("segments", []):
                if str(t.get("label")) == str(label):
                    wav_name = t.get("file")
                    if wav_name and (snap / wav_name).exists():
                        matched_snap = snap
                        target_take = t
                        break
        except Exception:
            continue
        if matched_snap:
            break

    if not matched_snap or not target_take:
        raise SystemExit(
            f"FAIL 未找到包含段 {label} 的 attic 快照（快照已滚出保留窗，只能重新纠错）。"
        )

    # 1. 恢复 wav 文件
    wav_file = target_take["file"]
    shutil.copy2(matched_snap / wav_file, audio_dir / wav_file)

    # 2. 恢复 manifest 段条目
    cur_mf_path = audio_dir / "manifest.json"
    if cur_mf_path.exists():
        try:
            cur_mf = json.loads(cur_mf_path.read_text(encoding="utf-8"))
            updated_segs = []
            replaced = False
            for t in cur_mf.get("segments", []):
                if str(t.get("label")) == str(label):
                    updated_segs.append(target_take)
                    replaced = True
                else:
                    updated_segs.append(t)
            if not replaced:
                updated_segs.append(target_take)
            cur_mf["segments"] = updated_segs
            paths.atomic_write(cur_mf_path, json.dumps(cur_mf, ensure_ascii=False, indent=2))
        except Exception as ex:
            print(f"WARN 恢复 manifest 条目时失败: {ex}", file=sys.stderr)

    # 3. 将涉及此 label 的条目置回 pending (applied = False, applied_at = None)
    entries, fp = load_corrections_raw(episode)
    reverted_ids = []
    for c in entries:
        aff = c.get("affected") or [str(c.get("segment"))]
        if str(label) in aff:
            c["applied"] = False
            c["applied_at"] = None
            if "done_segments" in c and str(label) in c["done_segments"]:
                c["done_segments"] = [s for s in c["done_segments"] if str(s) != str(label)]
            reverted_ids.append(c.get("id"))

    save_corrections_raw(episode, entries, expected_fp=fp)

    print(
        f"[OK] 已从快照 {matched_snap.name} 恢复段 {label} 音频与 manifest 条目。\n"
        f"     对应纠错条目 {reverted_ids} 已重置为 pending（可改参数再战）。\n"
        f"     提示: 回滚按段号（如: 回滚 {label}），撤回按条目 id（如: 撤回 2）。"
    )
    return {"snapshot": matched_snap.name, "take": target_take, "reverted_ids": reverted_ids}


def retract_correction(episode: Path, item_id: int) -> dict:
    """按条目 id 撤回（删除）纠错条目。"""
    entries, fp = load_corrections_raw(episode)
    target = None
    new_entries = []
    for c in entries:
        if c.get("id") == item_id:
            target = c
        else:
            new_entries.append(c)

    if target is None:
        raise SystemExit(f"FAIL 未找到 id={item_id} 的纠错条目。可用 id：{[c.get('id') for c in entries]}")

    save_corrections_raw(episode, new_entries, expected_fp=fp)

    desc = target.get("word") or target.get("issue") or ""
    print(
        f"[OK] 已撤回纠错 #{item_id}（段 {target.get('segment')}：{desc}）。\n"
        f"     读音沉淀已删，下次配音时该段自动重配回原读音。\n"
        f"     提示: 撤回按条目 id（如: 撤回 {item_id}），回滚按段号（如: 回滚 {target.get('segment')}）。"
    )
    return target
