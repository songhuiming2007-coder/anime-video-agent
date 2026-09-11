"""口播配音：`02-script.md` → 每段一个 wav + 它的真实时长。

**顺序不可交换**：本步必须先于排画面轨。画面轨的长度由这里产出的真实时长决定，
反过来做全程对不齐（见 CLAUDE.md）。

自回归 TTS 的典型失败不是报错，是**静默地念错**——漏掉半句、把一句念两遍、
或者在长句中间跑飞。所以每段生成后都用 Whisper 回读一遍，和原文比字符错误率；
不过就换种子重生成，重试用尽仍不过就整体失败退出，绝不把坏音频留在盘上。
这是 CLAUDE.md「诚实失败优于凑合交付」在本阶段的落地。

**唯一例外是 ASR 盲区豁免**：三个不同种子生成的音频 Whisper 全部严重失真
（CER 远超门槛）而时长达标时，判为 ASR 对音色的盲区而非 TTS 念错——跳过样本
不定罪（S4），保留音频并标 `qc_skip`，成片交人前必须人耳确认。
判据与 2026-08-16 段落 5.1 的实证见 `_render_one`。

用法：
    python -m pipeline.tts <每期目录>              # 读 config/voice.json
    python -m pipeline.tts <每期目录> --force      # 重跑已存在的段落
    python -m pipeline.tts probe "一句测试文本"     # 只试音，不进每期目录
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

from pypinyin import Style, pinyin

from . import paths  # 必须在任何 HF 库之前，把模型缓存钉到 SSD
from . import asr
from . import g2p
from .align import compute_script_vo_hash
from .align import verify_alignment   # 叶子模块，无环
from .qc import episode_duration_band

CONFIG = paths.CONFIG / "voice.json"

CPM = paths.conf("script.cpm", 380)   # 与 check_script 同源同 fallback；真值在
                                      # config/project.json（2026-08-10 校准 315），
                                      # 注释不再声称「同源」却各写各的数
DUR_BAND = (0.5, 2.0)   # 实际时长 / 估算时长 的允许区间，超出说明念飞了
# 成片总时长带是另一条：run() 末尾从 config/project.json 的 video.duration_band
# 取默认带，有 01-topic.md `时长目标` 就按该期覆盖（复用 qc.episode_duration_band，
# 不再抄第三份正则——tts/check_script/qc 曾经各写一份）。跟上面这条 DUR_BAND
# 是两个不同的量，名字像不代表是同一件事。
MAX_CER = 0.20      # 回读音节错误率上限。ASR 自身的听岔约 5%，留了余量
MIN_EDITS = 3       # 与上一条同时越线才判失败，避免短段落被 ASR 的一两处听岔冤枉
ATTEMPTS = 3

# 归一化时丢掉的东西：标点、空白、以及口播里不发音的符号
_DROP = re.compile(r"[\s，。、；：？！…—－·\-—\"'“”‘’《》〈〉（）()\[\]【】,.;:?!~]+")

# **同音字要折叠**，否则回读质检会稳定误判。
#
# 2026-07-30 踩的：「她帮你，但她不让你欠她。」被判不合格，回读听成「他帮你，但他不让你欠他」。
# 十个字里三个「她」听成「他」，CER 30%，绝对错字数 3——两条阈值一起越线，重试三次全一样，
# 整期配音退出。**而合成出来的音频完全正确。**
#
# 道理跟去标点是同一条：回读质检要抓的是漏读、重复、跑飞，也就是「有没有念对声音」。
# 他/她/它 三个字读音完全相同，ASR 不产出任何能区分它们的信息——
# 拿这个维度比对，得到的不是证据，是纯噪声。
#
# **只折叠读音完全一致的。** 的/得/地 看着像同一档，但「得」有 dé（获得）、
# 「地」有 dì（土地），折了会掩盖真的念错，所以不碰。
# 2026-08-03 起 `syllables()` 按拼音比对，这张表在回读那条路上已被包含（都是 ta1）。
# 留着是因为 `normalize` 还供字数统计用，且它长度不变、零成本；删了反而多一处行为变更。
_FOLD = str.maketrans({"她": "他", "它": "他", "牠": "他", "妳": "你"})


@dataclass
class Segment:
    """稿件里的一段口播。label 是稿件标注的段落号，可能不连续。

    `emotion`/`speed` 是 v2 新增的**可选**字段（架构设计 3.3）：不写时为 None，
    由 `resolve_emotion`/`resolve_speed` 落到默认值（平静叙述/中），
    行为与 v1 一致——旧稿零迁移。
    """

    index: int      # 出现顺序，1-based，决定文件名与渲染顺序
    label: str      # 稿件里写的「段落 N」
    text: str
    emotion: str | None = None   # 受控词表内的情绪名（check_script 已拦表外值）
    speed: str | None = None     # 慢/中/快（check_script 已拦表外值）


@dataclass
class Take:
    """一段的最终成品。duration 由 ffprobe 复核，不信模型自报。

    `sentences` 记每一句在本段内的起点与时长。**这是字幕分卡的依据**——
    按句合成本来就是分开量的，把它记下来，字幕就能按句上屏而不必整段一张卡。
    整段一张卡的后果是长段落要折三行糊住半个画面。
    """

    index: int
    label: str
    text: str
    file: str
    duration: float
    cer: float
    attempts: int
    sentences: list[dict] | None = None
    qc_skip: str | None = None   # "asr-blind" = ASR 对音色严重失真，CER 不可用，交人前必须人耳确认
    speakable: str | None = None  # 实际喂给模型的文本（剥引号+读音替换后）：段级复用比对的依据
    # v2 新增（架构设计 3.3）：情感与语速声明必须与合成结果一起进 manifest，
    # eval 才能按情绪分组统计 f0/停顿——回答「情感声明是否真的改变了声学输出」，
    # 防止情绪字段沦为写了不生效的安慰剂。
    emotion: str | None = None
    speed: str | None = None
    # 级联仲裁通过（架构设计三.5）：主读不过、仲裁读通过。落痕是为了观察
    # 仲裁率——某段仲裁率 >30% 说明主读对该音色系统性失真，需与 ASR 盲区同源审查。
    asr_arbitrated: bool | None = None
    # 拼音直注的替换记录（架构设计三.2 要点 3）：机器做的替换与 readings 表
    # 一样需要可审计。
    g2p_injections: list[dict] | None = None


# ---------- 稿件解析 ----------

def parse_script(path: Path) -> list[Segment]:
    """稿件 → 口播段。**按段落块切，`配音：` 可在块内任意位置**（2026-08-16
    审计 2-19 改）：旧正则要求「配音：」紧跟段落标题，标题与配音之间夹一个
    `画面：` 块的段会被静默跳过、后续段序号全部前移，只能靠 clips 的段数
    对账兜住且报错指错方向。与 clips.parse_shots / check_script.parse 同口径。
    """
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^##\s*段落\s*(\S+)\s*$", text, flags=re.M)
    segs: list[Segment] = []
    for label, block in zip(parts[1::2], parts[2::2]):
        vo = re.search(r"^配音[：:]\s*(.+)$", block, re.M)
        if not vo:
            continue
        # v2 可选字段（架构设计 3.3）：不写 = None，与 check_script 同口径。
        # 全角/半角冒号都认——稿件里中英冒号混用是常态。
        emo = re.search(r"^情绪[：:]\s*(.+)$", block, re.M)
        spd = re.search(r"^语速[：:]\s*(.+)$", block, re.M)
        segs.append(Segment(
            len(segs) + 1, label, vo.group(1).strip(),
            emo.group(1).strip() if emo else None,
            spd.group(1).strip() if spd else None,
        ))
    if not segs:
        raise SystemExit(f"FAIL 没从 {path} 解析出任何「## 段落 N + 配音：」，检查稿件格式")
    return segs


# ---------- 质检 ----------

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
# 多字数字串（年份/编号/数量词）：一律转。四个数字字连排不可能是人名。
_CN_NUM_MULTI = re.compile(r"[零一二两三四五六七八九十百千]{2,}")
# 单字：**只在后接时间或量词时转**。汉字人名/地名里单个数字字很常见
# （八幡、千反田、千叶县），转了就制造假阳性；而「十月」这类必须转，
# 因为 Whisper 回读会写成「10月」。
_CN_NUM_ONE = re.compile(r"[零一二两三四五六七八九十百千](?=[年月日岁个次份人集口种条部张只篇])")


def _cn_number_to_arabic(seq: str) -> str:
    """中文数字 → 阿拉伯数字。只覆盖稿件实际出现的两类形态。

    **为什么需要**（2026-09-11 实测）：Whisper 回读把「二零一一年十月」转写成
    「2011年10月」，而稿件写的是中文数字。两者在音节层面毫无交集
    （`er4 ling2 yi1 yi1` vs `2 0 1 1`），CER 直接假阳性——v1 的
    seg01/05/11 就是这样被误判成「ASR 盲区」跳过的。

    覆盖两类，不做通用中文数字解析（那是 cn2an 的活，但为判据函数引依赖不划算）：
      - 逐位读法（年份/编号）：二零一一 → 2011
      - 数量词：十五 → 15、两千 → 2000

    混杂非数字字符的串原样返回，交回调用方。
    """
    if not seq:
        return seq
    if all(c in _CN_DIGITS for c in seq):
        return "".join(str(_CN_DIGITS[c]) for c in seq)
    if not all(c in _CN_DIGITS or c in _CN_UNITS for c in seq):
        return seq
    total, cur = 0, 0
    for ch in seq:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        else:
            total += (cur or 1) * _CN_UNITS[ch]
            cur = 0
    return str(total + cur)


def _unify_numbers(s: str) -> str:
    """把中文数字统一成阿拉伯数字，消除 ASR 转写形态差异造成的假阳性。

    **单向**（中文 → 阿拉伯），因为 Whisper 的输出习惯就是阿拉伯数字，
    对齐到它最省事。反过来把 "15" 写成「一五」还是「十五」是二选一，
    而两者读音不同，猜错会制造新的假阳性。
    """
    # 顺序要紧：先多字（长匹配优先），再单字，否则单字规则会切碎多字串
    s = _CN_NUM_MULTI.sub(lambda m: _cn_number_to_arabic(m.group()), s)
    return _CN_NUM_ONE.sub(lambda m: _cn_number_to_arabic(m.group()), s)


def normalize(s: str) -> str:
    """回读比对与字数统计的统一口径：去标点、转小写、折叠同音字。

    折叠放在这里而不是只放在 `cer` 里，是因为它长度不变，字数统计不受影响，
    而放在一处能保证参考文本和回读文本走的是同一条路——两边口径不一致
    才是这类比对最容易出的错。
    """
    return _unify_numbers(_DROP.sub("", s)).lower().translate(_FOLD)


def syllables(s: str) -> list[str]:
    """归一化后的文本 → 音节序列。汉字取**带声调**拼音，其余字符按原样逐个保留。

    **回读质检要抓的是「有没有念对声音」，所以比对必须发生在声音的维度上。**
    上面 `_FOLD` 里手写的 他/她/它 就是这条规则的残缺版——它只列了三个字，
    而同一个毛病在专有名词上是系统性的，且列表永远追不上：

        户冢彩加 → 户种采家（3 处）    比企谷小町 → 比奇古小丁（4 处）
        川崎沙希 → 穿其沙西（3 处）    雪之下雪乃 → 雪之下雪奶（1 处）

    2026-08-03 实测这一整张表，**每一个日文专名都被 Whisper 听成同音或近音字**。
    合成出来的音频全是对的。长段落靠字数稀释侥幸过关（段 7 CER 20% 擦线），
    14 个字的短句 3 个错字直接 21%，重试三次全一样，整期退出。
    按字形比 CER，量到的是「ASR 认不认识这个人名」，不是「TTS 念没念对」。

    列名单那条路走不通：这次听成「户种」，下次是「户中」，穷举不完。
    转拼音是把两边都投影到 ASR 真正能提供信息的那个维度上。

    **它不会把真缺陷一起归一掉。** 漏读、重复、跑飞改变的是音节数量与顺序，
    拼音照样抓得住；引号被当字念出来（谁更该赢 → 谁更该非赢匪）多出两个音节，
    也躲不过。声调保留是关键：得 dé 与 地 dì 不同音，`_FOLD` 当初不敢碰的
    「的/得/地」在这里自动分开，不会被误折。
    """
    return [x[0] for x in pinyin(s, style=Style.TONE3,
                                 errors=lambda t: list(t), heteronym=False)]


def _titles() -> list[str]:
    """回读比对豁免表：`config/voice.json` 的 `titles` 字段（歌名列表）。

    Qwen3-TTS 能念英日歌名，但 Whisper 回读把歌名听岔（エウテルペ→EUTERPE），
    CER 虚高到 100%+，TTS 念对也被门禁误杀（2026-08-15 实测段落 5.1 三次重试全挂）。
    歌名区段不参与 CER 比对；歌名前后的中文照常比对。
    只读一次，不能每句读文件。
    """
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    v = cfg.get("titles", {})
    if isinstance(v, dict):
        v = [k for k in v if not k.startswith("_")]
    return [t for t in v if t]


def _title_durs() -> dict[str, float]:
    """歌名 → 实测念白时长（秒）：`config/voice.json` 的 `titles` 值。

    这张表 2026-08-16 之前硬编码在 tts.py（`_TITLE_DURS`，五首罪恶王冠
    歌名 + seg7 实测值）——换番要改代码、换音色后数字过期，都违反
    「机制进代码、内容进配置」。值为 null 表示未实测：该歌名按 cpm 估算，
    `run()` 会 WARN（cpm 估英日歌名会高估 1.5-2 倍，见 `expected_duration`）。
    """
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    v = cfg.get("titles", {})
    if isinstance(v, list):        # 老格式：裸列表 = 全部未实测
        return {}
    return {t: float(secs) for t, secs in v.items()
            if not t.startswith("_") and isinstance(secs, (int, float))}


def _unmeasured_titles(texts: list[str]) -> list[str]:
    """文本里出现、但 titles 没记实测时长的歌名——按 cpm 估会高估的那批。"""
    measured = {normalize(t) for t in _title_durs()}
    return sorted({t for t in _titles() for tx in texts
                   if normalize(t) in normalize(tx) and normalize(t) not in measured})


# 连续的非中文音节段（英文字母/日文假名），回读比对时按长度窗口挖掉歌名变体。
# 歌名被 Whisper 念岔后长度基本不变（EUTERPE vs エウテルペ 都是 5-7 个音节），
# 滑动窗口找与歌名长度最接近的一段挖掉；窗口限 ±2 音节防误挖中文。
#
# 注意：汉字拼音（pypinyin TONE3）形如 yi1/jin4，**带声调数字**；
# 英日字母音节（e/u/t 或 エ/ウ/テ）不带数字。区分它们靠数字——
# 只把「不含数字的音节」当作歌名变体候选，拼音天然被排除。
def _is_nonhan_syl(s: str) -> bool:
    return not any(ch.isdigit() for ch in s)


def _excise_titles(syls: list[str], titles: list[str], fuzzy: bool = False) -> list[str]:
    """从音节序列里挖掉歌名。

    ref 侧精确匹配（歌名原文经过 normalize 后的字符序列）；
    hyp 侧模糊挖（Whisper 念岔的变体，如 EUTERPE），按「连续非中文段」的长度
    滑动窗口找与某个歌名长度差 ≤ 1 的一段挖掉——歌名念岔后长度基本不变，
    而中文音节是带数字的拼音（yi1/jin4），与英日字母音节天然可区分。
    窗口只给 ±1：±2 实测误挖（段落 5.1 的「クランテイエンロ」8 音节
    被 Planetes 的 8±2 窗口误命中，而该句根本没有 Planetes）。
    """
    if not titles:
        return syls
    n = len(syls)
    if not fuzzy:
        # 精确匹配：ref 侧
        title_syls = {t: list(normalize(t)) for t in titles}
        out: list[str] = []
        i = 0
        while i < n:
            matched = None
            for t, ts in title_syls.items():
                if syls[i:i + len(ts)] == ts:
                    matched = len(ts)
                    break
            if matched:
                i += matched
            else:
                out.append(syls[i])
                i += 1
        return out

    # 模糊匹配：hyp 侧。找所有连续非中文段（不含数字的音节），
    # 长度与任一歌名差 ≤ 1 就挖掉。
    out: list[str] = []
    lens = sorted({len(list(normalize(t))) for t in titles})
    i = 0
    while i < n:
        if not _is_nonhan_syl(syls[i]):
            out.append(syls[i])
            i += 1
            continue
        j = i
        while j < n and _is_nonhan_syl(syls[j]):
            j += 1
        span_len = j - i
        if any(abs(span_len - tl) <= 1 for tl in lens):
            i = j  # 挖掉整段
        else:
            out.append(syls[i])
            i += 1
    return out


def cer(ref: str, hyp: str) -> tuple[int, float]:
    """回读比对，返回（编辑距离, 音节错误率）。段落都在百字以内，朴素 DP 足够。

    要两个数字是因为短段落上比率极不稳：十个音节的段落错一个就是 10%，
    而那只是 ASR 自己听岔了。判定时比率与绝对错数要同时越线。

    歌名豁免：比对前先把 `titles` 里的歌名从两侧挖掉（ref 精确、hyp 按长度
    窗口），歌名念岔不计入 CER。歌名前后的中文照常参与比对。
    """
    titles = _titles()
    a, b = syllables(normalize(ref)), syllables(normalize(hyp))
    if titles:
        # 只豁免**这句话里真的出现**的歌名（2026-08-16 审计 2-18）：hyp 侧的
        # 模糊挖除按长度窗口找变体，窗口若对准全部歌名，正文里无关的英文/
        # 日文词（如 "coding" 长 6，落在 エウテルペ(5)±1 窗内）会被误挖——
        # ref 侧保留、hyp 侧被挖，CER 凭空虚高，误杀重试。收窄到 ref 侧
        # 出现的歌名，与歌名无关的句子不再有窗口。
        present = [ti for ti in titles if normalize(ti) in normalize(ref)]
        a, b = _excise_titles(a, present, fuzzy=False), _excise_titles(b, present, fuzzy=True)
    if not a:
        return (len(b), 1.0 if b else 0.0)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return (prev[-1], prev[-1] / len(a))


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def expected_duration(text: str) -> float:
    """估算一段话念多久。中文按 cpm（字/分钟）算，歌名按实测时长算。

    **歌名不能按字数估算。** Qwen3-TTS 念英文/日文歌名比念中文快得多，
    按 cpm 估算会高估 1.5-2 倍（エウテルペ 5 个假名按 cpm 估 2.9s，实测 1.68s），
    导致实际/估算 < 0.5× 被时长门禁误杀（2026-08-15 段落 5.1 实测踩到）。
    歌名实测时长在 `config/voice.json` 的 `titles` 值里（换引擎/换音色要重测；
    没实测的按 cpm 估，`run()` 对出现在稿件里的这类歌名打 WARN）。
    """
    total = len(normalize(text))
    for t, secs in _title_durs().items():
        n = normalize(text).count(normalize(t))
        if n:
            total -= len(normalize(t)) * n
            total += secs * CPM / 60 * n
    return total / CPM * 60


# ---------- 引擎 ----------

def _resolve_model(ref: str) -> str | Path:
    """本地目录（相对仓库根）优先，否则当作 HuggingFace 仓库名。"""
    local = paths.ROOT / ref
    return local if local.is_dir() else ref


def _load_indextts(ref: str | Path):
    """IndexTTS 要手工搭一下。

    mlx-audio 的 ModelArgs 把 `tokenizer_name` 列为必填，而 mlx-community 的
    仓库 config.json 里没有这个字段——直接 `load()` 会 TypeError。
    tokenizer.model 本来就和权重同目录，补上指向自身即可。
    """
    import mlx.core as mx
    from mlx_audio.tts.models.indextts import Model
    from mlx_audio.utils import get_model_path, load_config, load_weights

    path = ref if isinstance(ref, Path) else get_model_path(str(ref))
    cfg = load_config(path)
    cfg["tokenizer_name"] = str(path)
    if "dataset" in cfg and "sample_rate" in cfg["dataset"]:
        cfg["sample_rate"] = cfg["dataset"]["sample_rate"]

    model = Model(cfg)
    model.load_weights(list(model.sanitize(load_weights(path)).items()), strict=False)
    mx.eval(model.parameters())
    model.eval()
    return model


def _load_generic(ref: str | Path):
    from mlx_audio.tts.utils import load

    return load(str(ref))


def _load_qwen3(ref: str | Path):
    from mlx_audio.tts.utils import load_model

    return load_model(str(ref))


# Qwen3-TTS PyTorch 版只认语言**全名**，不认 ISO 码（2026-09-11 云端实测抓出：
# 传 lang_code="zh" 直接 ValueError）。voice.json 的 lang_code 是给 mlx 版用的，
# 两版对同一字段的解释不同，所以这里必须做一层翻译，不能让配置原样透传。
_QWEN3_LANG_NAMES = {
    "zh": "chinese", "zh-cn": "chinese", "chinese": "chinese",
    "en": "english", "english": "english",
    "ja": "japanese", "jp": "japanese", "japanese": "japanese",
    "ko": "korean", "korean": "korean",
    "fr": "french", "french": "french",
    "de": "german", "german": "german",
    "it": "italian", "italian": "italian",
    "pt": "portuguese", "portuguese": "portuguese",
    "ru": "russian", "russian": "russian",
    "es": "spanish", "spanish": "spanish",
    "auto": "auto",
}


def qwen3_language_name(code: str | None) -> str:
    """把 voice.json 的 lang_code 翻成 Qwen3-TTS 认的语言名（纯函数）。

    未知码**报错而非猜**：猜错的语言会静默改变输出（比如把中文旁白按日语念），
    而这是听感上要整期返工的错误，必须早失败（S1）。
    """
    key = (code or "auto").strip().lower()
    if key not in _QWEN3_LANG_NAMES:
        raise SystemExit(
            f"FAIL 未知语言码 {code!r}。Qwen3-TTS 只认 "
            f"{sorted(set(_QWEN3_LANG_NAMES.values()))}，请检查 config/voice.json 的 lang_code。"
        )
    return _QWEN3_LANG_NAMES[key]


def _load_qwen3_cuda(cfg: dict, model_dir: str | Path):
    """Qwen3-TTS 的 **PyTorch/CUDA** 版 loader（云端主选，2026-09-11 实测通过）。

    与 `_load_qwen3` 是同一个模型的两条实现路径：
    - `_load_qwen3`（mlx）：本地 Apple Silicon，v1 一直在用
    - `_load_qwen3_cuda`（本函数）：云端 CUDA，`pip install qwen-tts`

    两者**权重相同**，所以音色一致——用户实听「音色相当好，很纯净」，
    这是选它作 v2 云端引擎的决定性理由。

    ## 克隆路径必须走 x-vector（ADR-0006 的教训）

    `generate_voice_clone(..., x_vector_only_mode=True)`：官方 PyTorch 版把
    x-vector 路径做成了显式开关。mlx 版的 ICL 路径（传 ref_text）有 bug——
    模型会把参考转录当正文念出来；x-vector 路径（纯说话人嵌入）实测正常。
    本 loader 固定用它，**不传 ref_text**（我们的参考干声是日文，本来也没有可靠转录）。

    ## Index 系列为何出局（2026-09-11 记录，避免以后重走）

    架构设计 3.1 原定 IndexTTS2 主选，实测否决：
    1. **不是多语种模型**（官方：仅中文+拼音；日语是 IndexTTS2.5 才加的），
       而本项目旁白含英日歌名——与 ADR-0006 换掉 IndexTTS-1.5 的理由直接冲突；
    2. 用户实听 seg6 参考下「音色浑浊、字听不清」且语速异常；
    3. 部署成本高：IndexTTS2 与 2.5 的 transformers 版本冲突。
    另 CosyVoice2 亦因 pip 卡死未能评估，留待以后。
    """
    from qwen_tts import Qwen3TTSModel

    opts = cfg.get("qwen3_cuda") or {}
    import torch

    return Qwen3TTSModel.from_pretrained(
        str(model_dir),
        device_map=opts.get("device_map", "cuda:0"),
        dtype=getattr(torch, opts.get("dtype", "bfloat16")),
    )


LOADERS = {"indextts": _load_indextts, "qwen3_tts": _load_qwen3}


def _indextts_audio(model, ref_mel, text: str, temp: float, top_k: int, max_tokens: int):
    """IndexTTS 的自回归解码。**不要改回 model.generate()。**

    mlx-audio 0.4.6 的 `Model.generate` 有两处错：
      1. 把 start_mel_token 塞进 text_embedding 查表，它属于 mel 码本；
      2. mel 位置编码从「前缀长度」起算，应当从 0 起算。
    症状不是报错而是念漏字、提前收口——2026-07-29 实测
    「这不叫牺牲，这叫止损」被念成「这不叫止损」，回读 CER 44%。
    照下面这样自己解码，同一句 CER 归 0。

    与上游对齐后可以删掉本函数，删之前先跑 probe 验证。
    """
    import mlx.core as mx
    import numpy as np
    from mlx_lm.models.cache import KVCache
    from mlx_lm.sample_utils import make_sampler
    from mlx_audio.tts.models.indextts import normalize as tts_norm

    g = model.args.gpt
    tokens = model.tokenizer.encode(
        tts_norm.tokenize_by_CJK_char(tts_norm.normalize(text))
    )
    tokens = [g.start_text_token, *tokens, g.stop_text_token]
    tok = mx.array(tokens)[None, :]

    prefix = mx.concat(
        [
            model.get_conditioning(ref_mel),
            model.text_embedding(tok) + model.text_pos_embedding(tok),
            model.mel_embedding(mx.array([[g.start_mel_token]]))
            + model.mel_pos_embedding(mx.array([[g.start_mel_token]]), 0),
        ],
        axis=1,
    )

    cache = [KVCache() for _ in range(g.layers)]
    sampler = make_sampler(temp=temp, top_k=top_k)
    inputs, latents, pos = prefix, [], 1
    for _ in range(max_tokens):
        hidden = model.final_norm(model.gpt(inputs, cache=cache))
        latents.append(hidden[:, -1:, :])
        nxt = sampler(model.mel_head(hidden[:, -1:, :]))
        if nxt.item() == g.stop_mel_token:
            break
        inputs = model.mel_embedding(nxt) + model.mel_pos_embedding(nxt, pos)
        pos += 1

    latent = mx.concat(latents, axis=-2)
    audio = model.bigvgan(latent.transpose(0, 2, 1), ref_mel.transpose(0, 2, 1))
    mx.clear_cache()
    return np.asarray(audio.squeeze(), dtype=np.float32), model.sample_rate


class Engine:
    """TTS 引擎的接缝。换引擎只改 config/voice.json，别处不动。"""

    # 重试时逐次降温：第一次自然，不行就换种子并收紧采样换稳定
    SAMPLING = [(0.8, 30), (0.7, 20), (0.5, 10)]
    MAX_MEL_TOKENS = 1200   # ≈ 51s，远超单段上限，纯粹兜底防死循环

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.kind = cfg["engine"]
        self.ref_audio = str((paths.ROOT / cfg["ref_audio"]).resolve())
        self.ref_text = cfg.get("ref_text")
        self.lang_code = cfg.get("lang_code", "auto")
        self.seed_offset = int(cfg.get("seed_offset", 0))
        self.segment_seeds = {str(k): int(v) for k, v in cfg.get("segment_seeds", {}).items()}
        if not Path(self.ref_audio).exists():
            raise SystemExit(f"FAIL 参考干声不存在：{self.ref_audio}")

        self.voice_prompt = None
        if self.kind == "qwen3_tts_cuda":
            self.model = _load_qwen3_cuda(cfg, _resolve_model(cfg["model"]))
            # **说话人嵌入只提一次，全程复用。** 2026-09-11 人耳验收抓出：
            # 每句各自提特征会让段内音色漂移（听感是「同一段里像换了好几个人」）。
            # x_vector 只依赖参考音频，本来就不必每句重算——缓存它既省时间又锁音色。
            self.voice_prompt = self.model.create_voice_clone_prompt(
                ref_audio=self.ref_audio, x_vector_only_mode=True
            )
        else:
            loader = LOADERS.get(self.kind, _load_generic)
            self.model = loader(_resolve_model(cfg["model"]))

        self.ref_mel = None
        if self.kind == "indextts":
            from mlx_audio.utils import load_audio
            from mlx_audio.tts.models.indextts.mel import log_mel_spectrogram

            # 参考梅尔谱每段都一样，算一次就够
            self.ref_mel = log_mel_spectrogram(
                load_audio(self.ref_audio, sample_rate=self.model.sample_rate)
            )

    def synthesize(
        self, text: str, dest: Path, attempt: int, seed: int,
        emo_params: dict | None = None,
    ) -> int:
        """生成一段并落盘，返回采样率。attempt 从 1 起，决定采样温度。

        `emo_params` 来自 voice.json 的情绪词表（架构设计 3.3）。**各引擎能力不同**：
        - indextts2：按实测确认的形式消费（emo_text / emo_vector，以云端 probe 为准）；
        - qwen3_tts（本地 mlx）：**没有情感控制参数**，此处有意忽略——但情绪声明
          仍会进 manifest 供 eval 按情绪分组统计（「声明是否真的改变了声学输出」
          在本地通道上注定答「没改变」，这是该通道的已知能力边界，不是 bug）。
        """
        import numpy as np
        from scipy.io import wavfile

        # **云端路径与 mlx 路径必须彻底分开**：云端没有 mlx（Apple Silicon 专有），
        # 任何无条件 import 都会让 qwen3_tts_cuda 直接 ImportError——而它正是云端
        # 唯一的引擎。2026-09-11 由 TestQwen3CudaLoader 抓出，故拆成两条独立分支。
        if self.kind == "qwen3_tts_cuda":
            audio, rate = self._synthesize_cuda(text, seed)
        else:
            audio, rate = self._synthesize_mlx(text, attempt, seed)

        audio = audio.reshape(-1)
        if not audio.size or float(np.max(np.abs(audio))) == 0.0:
            raise RuntimeError("模型产出全静音")

        dest.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(dest, rate, (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16))
        return rate

    def _synthesize_cuda(self, text: str, seed: int):
        """云端 CUDA 通道（Qwen3-TTS PyTorch 版）。**不碰 mlx。**

        三件事缺一不可，每一件都对应一次实测故障：

        1. `x_vector_only_mode=True`：ICL 路径（传 ref_text）会把参考转录当正文
           念出来（ADR-0006 实测）；x-vector 是纯说话人嵌入路径，只传 ref_audio。
        2. `voice_clone_prompt`（__init__ 里预算好）：每句各自提特征会让段内音色
           漂移——2026-09-11 人耳验收听到「同一段里像换人」。
        3. `torch.manual_seed`：mlx 版一直有 `mx.random.seed`，云端版最初漏了，
           于是默认采样每句随机起步——这是段内语气/语速突变的另一半原因。
           云端比本地明显，正是因为本地有种子、云端没有。
        """
        import numpy as np
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        wavs, rate = self.model.generate_voice_clone(
            text=text,
            language=qwen3_language_name(self.lang_code),
            voice_clone_prompt=self.voice_prompt,
            x_vector_only_mode=True,
        )
        return np.asarray(wavs[0]).reshape(-1), rate

    def _synthesize_mlx(self, text: str, attempt: int, seed: int):
        """本地 mlx 通道（Apple Silicon，v1 一直在用）。返回 (audio, rate)。"""
        import mlx.core as mx

        mx.random.seed(seed)
        temp, top_k = self.SAMPLING[min(attempt, len(self.SAMPLING)) - 1]

        if self.kind == "indextts":
            audio, rate = _indextts_audio(
                self.model, self.ref_mel, text, temp, top_k, self.MAX_MEL_TOKENS
            )
        elif self.kind == "qwen3_tts":
            # Qwen3-TTS 只传 ref_audio，不传 ref_text。
            #
            # 新版 mlx-audio 的 ICL 克隆路径（ref_audio + ref_text 齐传）有 bug：
            # 模型会把 ref_text 当正文复述出来（2026-08-15 实测，输出念的是参考
            # 转录而非目标文本）。只传 ref_audio 走 x-vector 说话人嵌入路径，
            # 念的是目标文本，音色照样来自参考。不传 ref_text 也就绕过了
            # transcripts.json 的依赖。
            #
            # lang_code 来自 config/voice.json（zh = 中文旁白 + 英日歌名混读）。
            # 采样参数照 Engine.SAMPLING 逐次降温；Qwen3 默认 temperature 0.9、
            # top_k 50，首测即达标时这两个参数不生效，只是兜底。
            kwargs: dict = {
                "text": text,
                "ref_audio": self.ref_audio,
                "lang_code": self.lang_code,
                "temperature": temp,
                "top_k": top_k,
            }
            chunks, rate = [], None
            for r in self.model.generate(**kwargs):
                chunks.append(np.asarray(r.audio, dtype=np.float32).reshape(-1))
                rate = r.sample_rate
            if not chunks:
                raise RuntimeError("模型没有产出任何音频")
            audio = np.concatenate(chunks)
        else:
            kwargs: dict = {"text": text, "ref_audio": self.ref_audio}
            if self.ref_text is not None:
                kwargs["ref_text"] = self.ref_text
            # generate 是生成器：长文本会分块 yield，只取最后一块会丢音频
            chunks, rate = [], None
            for r in self.model.generate(**kwargs):
                chunks.append(np.asarray(r.audio, dtype=np.float32).reshape(-1))
                rate = r.sample_rate
            if not chunks:
                raise RuntimeError("模型没有产出任何音频")
            audio = np.concatenate(chunks)


@lru_cache(maxsize=1)
def _asr_backends() -> tuple[str, str | None]:
    """选定 (主读, 仲裁) 后端，进程内复用。

    探测有成本，且选定结果不会中途变化；每段都重探是白费。
    """
    return asr.pick_backends()


def readback_passes(edits: int, err: float) -> bool:
    """回读判据（纯函数）。

    仲裁必须与主读用**同一判据**——否则「仲裁通过」与「主读通过」不是同一件事，
    级联就失去了可比性（S10：不同模型的 CER 不是同一个量，所以判据要同一把尺）。
    """
    return err <= MAX_CER or edits < MIN_EDITS


def transcribe(path: Path) -> str:
    """回读：音频 → 文本，用来抓漏读 / 重复 / 跑飞。

    v2 起走 `asr` 的后端表：本地 Mac 无 CUDA 自动落 mlx-whisper，
    云端落 SenseVoice/Whisper-CUDA——**同一份裁决代码，两处算力**（ADR-0014 的代码层落地）。
    """
    primary, _ = _asr_backends()
    return asr.transcribe_audio(path, backend=primary)[0]


# ---------- 主流程 ----------

def _ref_text_for(ref: Path) -> str | None:
    """参考干声的文本。VoxCPM 这类引擎要 ref_text，IndexTTS 用不上。"""
    book = (paths.ROOT / ref).resolve().parent / "transcripts.json"
    if not book.exists():
        return None
    return json.loads(book.read_text(encoding="utf-8")).get(Path(ref).name)


def load_config(path: Path = CONFIG) -> dict:
    """读配音配置，支持 `_extends` 覆盖层。

    **为什么要覆盖层**（2026-09-11）：本地 Mac 只装得了 mlx（qwen3_tts），
    云端只装得了 CUDA（indextts2），同一个 `engine` 字段喂不了两边。
    若让云端配置整份复制，两份必然分叉——加一条 readings 词条要改两处，
    漏一处就是静默不一致（而读音分叉的代价是某一期突然念错）。
    覆盖层只写差异，其余字段从基配置继承。
    """
    if not path.exists():
        raise SystemExit(
            f"FAIL 缺少 {path}。先跑 `python -m pipeline.tts probe` 选定音色，"
            f"再把结论写进这个文件。"
        )
    # 前置校验（2026-08-18 复盘②）：坏 JSON / 缺必要键在加载处当场报——
    # 不拦的话，流到 Engine 构造或 _voice_fingerprint 才是裸 KeyError，
    # 报错离病根隔了好几层
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"FAIL {path} 不是合法 JSON：{e}")

    base_name = cfg.get("_extends")
    if base_name:
        base_path = Path(base_name)
        if not base_path.is_absolute():
            base_path = paths.CONFIG / base_name
        if not base_path.exists():
            raise SystemExit(f"FAIL {path} 的 _extends 指向不存在的 {base_path}")
        base = load_config(base_path)
        base.update({k: v for k, v in cfg.items() if k != "_extends"})
        cfg = base

    missing = [k for k in ("engine", "model", "ref_audio") if not cfg.get(k)]
    if missing:
        raise SystemExit(
            f"FAIL {path} 缺必要字段：{'、'.join(missing)}。"
            f"engine/model/ref_audio 缺一不可，对照 config/voice.json 的 _note 补齐"
        )
    return cfg


# 按句合成，不按段合成。
#
# **IndexTTS 对整句时长有先验，文本一长就压着念。** 2026-07-29 实测段 17：
#     整段 82 字一次合成  →  14.46s = 5.67 字/秒
#     拆 4 句分别合成      →  20.22s = 4.05 字/秒，句间 3.55–4.51
# 全篇按段合成时语速落在 3.66–5.67（极差 1.55×），且与字数正相关 r=+0.613——
# 也就是说长段落必然念得快，这是系统性的，不是随机波动。听感上就是「语速不统一」。
#
# 按句合成还有个附带好处：单句更短，回读质检的粒度也更细，出问题时能定位到句。
SENT_END = re.compile(r"(?<=[。？！])")
# 单个合成单元的字数上限。超过就在逗号处再断。
#
# 2026-07-29 实测：段 4 那句「在户部告白之前，当着所有人的面走过去，说我从很早以前
# 就开始喜欢你了，请和我交往吧。」40 字一口气念完 6.79 秒，明显比别处快，还吞了字
# （回读 CER 9%，没到 20% 的门槛所以没被拦下）。模型对整句时长有先验，
# 单元越长压得越狠——把单元控制在 30 字以内，语速就稳了。
MAX_SYNTH_CHARS = 30
_COMMA = "，、；："
# 句间停顿。TTS 每句自带的首尾静音会被裁掉，改由这个常数统一控制节奏。
SENT_GAP = 0.18
# 裁静音的门限。TTS 的「静音」不是绝对零，是很小的底噪。
TRIM_DB = 1e-3

# **送进合成器之前剥掉的符号：纯书面标记，念不出来。**
#
# 2026-07-30 实测：「谁更该“赢”。」念成「谁更该**非赢匪**」，
# 「她那句“好啊”后面」念成「她那句**非好啊非**」——IndexTTS 把弯引号
# 当成字符各吐了一个音节出来。回读 CER 只有 7%，**没到 20% 的门槛，门禁放行了**，
# 要人听出来才发现。
#
# 与 write-script 里那条同源：「补充信息写成短句，不塞括号——括号念不出来」。
# 引号当时漏了，而它比括号更常用。
#
# **只剥没有读音的符号。** 逗号、句号、破折号一律保留：它们控制停顿，
# IndexTTS 靠它们断气口，剥了语速会乱。破折号实测不会被念出音（回读里它安静地消失了）。
#
# 字幕那一侧不受影响——引号照常显示，剥的只是喂给合成器的那份文本。
_MUTE = re.compile(r"[“”‘’\"'「」『』《》〈〉（）()\[\]【】]")


@lru_cache(maxsize=1)
def _voice_cfg() -> dict:
    """voice.json 只读一次。

    `speakable` 每句都要调，绝不能每句读一次盘（下面 `_readings` 的注释
    早就写明了这条约束）。v2 把读数集中到这一个入口，免得每加一张表
    就多一次文件读取。
    """
    return json.loads(CONFIG.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _readings() -> dict[str, str]:
    """`config/voice.json` 的读音替换表（键 = 原文词，值 = 合成侧同音替换词）。

    **IndexTTS 念错多音字/生僻字没有参数能修**——读音由模型内部判断。
    只能在合成文本上换成模型不会念错的同音字（2026-08-09 实测：
    喰种→餐种、绚都→绚督 有效）。字幕用稿子原文，替换只发生在合成侧。
    每次合成句都调 speakable，表必须只读一次，不能每句读文件。

    **v2 起降级为「逐案 override」**：音读泄漏改由 `g2p` 的拼音直注层治理，
    本表只保留自动注音仍读错的残余个例。
    """
    return _voice_cfg().get("readings", {})


@lru_cache(maxsize=1)
def _injections() -> dict[str, str]:
    """voice.json 的 `pinyin_injections`（TONE3 统一表示，引擎语法由 g2p 分派）。"""
    return g2p.load_injections(_voice_cfg())


def speakable_traced(s: str, engine_kind: str) -> tuple[str, list[dict]]:
    """念得出来的那份文本 + 注入记录（v2 链路，架构设计三.2）。

    链路：剥不发音符号 → 拼音直注 → readings 逐案 override。
    readings 排在**最后**不是随意：它是「自动注音仍读错」的残留兜底，
    与 g2p 是两层而非替代关系；顺序反了，注音之后的拼音串会绕过它。
    """
    s = _MUTE.sub("", s)
    s, applied = g2p.inject(s, _injections(), engine_kind)
    for src, rep in _readings().items():
        s = s.replace(src, rep)
    return s, applied


def speakable(s: str, engine_kind: str = "qwen3_tts") -> str:
    """念得出来的那份文本。字幕用原文，合成用这个。"""
    return speakable_traced(s, engine_kind)[0]


# 默认情绪/语速（架构设计 3.3）：不写字段的段落落到这两个值，行为与 v1 一致。
DEFAULT_EMOTION = "平静叙述"
DEFAULT_SPEED = "中"


def resolve_emotion(cfg: dict, emotion: str | None) -> tuple[str, dict]:
    """情绪名 → (实际生效的情绪名, 引擎参数字典)。纯函数。

    表外词在 check_script 已拦下；这里若再遇一次（比如绕过 check 直接调 run）
    就报错而不静默回退——静默回退会造出「写了但没生效」的安慰剂，
    而防止这种安慰剂正是这两个字段进 manifest 的目的。
    """
    table = {k: v for k, v in (cfg.get("emotions") or {}).items() if not k.startswith("_")}
    name = emotion or DEFAULT_EMOTION
    if name not in table:
        raise SystemExit(
            f"FAIL 情绪 '{name}' 不在 voice.json 的受控词表内（{sorted(table)}）。"
            f"表外词无法映射到引擎参数，也不会进 manifest——不静默回退。"
        )
    return name, dict(table[name])


def resolve_speed(cfg: dict, speed: str | None) -> tuple[str, float]:
    """语速名 → (实际生效的语速名, 时长系数)。纯函数。"""
    table = {k: v for k, v in (cfg.get("speed") or {}).items() if not k.startswith("_")}
    name = speed or DEFAULT_SPEED
    if name not in table:
        raise SystemExit(
            f"FAIL 语速 '{name}' 不在 voice.json 的受控词表内（{sorted(table)}）。"
        )
    return name, float(table[name])


def split_sentences(text: str) -> list[str]:
    out = []
    for s in (x.strip() for x in SENT_END.split(text) if x.strip()):
        out.extend(_split_at_commas(s) if len(normalize(s)) > MAX_SYNTH_CHARS else [s])
    return out


def _split_at_commas(s: str) -> list[str]:
    """长句在逗号处断成若干合成单元，尽量均匀。"""
    pieces, cur = [], ""
    for ch in s:
        cur += ch
        if ch in _COMMA and len(normalize(cur)) >= MAX_SYNTH_CHARS * 0.5:
            pieces.append(cur)
            cur = ""
    if cur:
        # 尾巴太短就并回上一段，别留下三五个字的碎片
        if pieces and len(normalize(pieces[-1] + cur)) <= MAX_SYNTH_CHARS:
            pieces[-1] += cur
        else:
            pieces.append(cur)
    return pieces or [s]


def _tail_artifact(a: list[int], sr: int, hi: int) -> int:
    """找出结尾那段「机械声」的起点；没有就返回 hi。

    **IndexTTS 在停止符附近会吐出一段条件不良的帧，BigVGAN 把它渲成一声低频闷响。**
    2026-07-29 实测「那不是牺牲。」：话音结束后先安静 100ms，最后 100ms 又冒出能量，
    0–1.5kHz 回到接近正常语音的强度，而 4–12kHz 比正常语音低 15–20dB——
    低频足、高频缺，听感就是「登」。58 句里 39 句有这个尾巴，中位 100ms。

    按响度裁不掉它（它够响，不算静音），得按形状认：
    **主体语音 → 一段明显的谷 → 又冒起来 → 结束**。认这个谷，从谷处切。
    没有谷就说明结尾是正常收音，不动。
    """
    win = int(sr * 0.02)                        # 20ms 一格
    look = min(hi, int(sr * 0.6))
    seg = a[hi - look:hi]
    env = [max((abs(x) for x in seg[i * win:(i + 1) * win]), default=0)
           for i in range(look // win)]
    if len(env) < 6:
        return hi
    peak = max(env) or 1

    # 找最后一段**连续的谷**（≥40ms 低于峰值 12%），谷之后只要还有东西就是尾巴。
    #
    # 判据的关键是「谷」，不是「谷后面有多响」。初版要求谷后冒到峰值 50% 以上，
    # 结果段 2 的两句只冒到 45% 和 29%，全漏了——而人耳照样听得见。
    # 谷后面那截**是长是短**才是可靠的区分：机械声是 60–140ms 的一小截，
    # 真语音接着说下去会长得多，所以用 TAIL_MAX 卡住，不用响度卡。
    QUIET, MIN_RUN, TAIL_MAX = 0.12, 2, 0.25
    runs, rs = [], None
    for i, v in enumerate(env + [peak]):        # 末尾补一格，让最后一段谷也能收口
        if v < peak * QUIET:
            if rs is None:
                rs = i
        else:
            if rs is not None and i - rs >= MIN_RUN:
                runs.append((rs, i))
            rs = None
    if not runs:
        return hi
    start, end = runs[-1]                       # 最后一段谷
    if end >= len(env):
        return hi                               # 谷一直到结尾，本来就是正常收尾
    burst = env[end:]                           # **只量谷之后那一小截**，不含谷本身。
    # 初版把谷也算进长度，而那两句的谷有 460ms，一量就超限，反倒把本来能裁的漏掉了。
    if max(burst) < peak * 0.15:
        return hi                               # 谷之后没东西
    if len(burst) * win > sr * TAIL_MAX:
        return hi                               # 谷之后还很长，那是真语音，不敢动
    return hi - look + start * win              # 从谷的起点切


def _trim_silence(path: Path, check_tail: bool = True) -> None:
    """裁掉首尾静音与结尾的机械声，就地重写。

    每句自带的首尾静音加起来能有 0.4s，60 来句就是 20 多秒的死时间，
    而且长短不一，节奏没法控制。裁干净之后由 SENT_GAP 统一给停顿。

    `check_tail`：结尾机械声检测只对 IndexTTS 开——那是 BigVGAN 对停止符附近
    条件不良帧的渲染产物；Qwen3-TTS 没有这个成因（2026-09-05 审计复核：
    Qwen3 时代 80 句产物零触发），白跑一道启发式只会增加误裁句尾真音的机会。
    """
    import wave
    import struct

    with wave.open(str(path)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a = list(struct.unpack(f"<{n}h", w.readframes(n)))
    thr = 32768 * TRIM_DB
    lo = next((i for i, x in enumerate(a) if abs(x) > thr), 0)
    hi = next((i for i in range(n - 1, -1, -1) if abs(a[i]) > thr), n - 1) + 1
    if check_tail:
        hi = _tail_artifact(a, sr, hi)            # 先砍掉结尾的机械声
    lo = max(0, lo - int(sr * 0.01))          # 前后各留 10ms，别把气口裁掉
    hi = min(n, hi + int(sr * 0.01))
    if hi - lo < int(sr * 0.05):              # 兜底：别把整句裁没了
        lo, hi = 0, n
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(struct.pack(f"<{hi - lo}h", *a[lo:hi]))


def render_segment(engine: Engine, seg: Segment, dest: Path) -> Take:
    """生成一段：逐句合成、裁静音、按固定停顿拼起来。"""
    sents = split_sentences(seg.text)
    if len(sents) <= 1:
        take = _render_one(engine, seg, dest)
        speak = take.duration
        _pad_tail(dest, _para_gap())
        take.duration = round(probe_duration(dest), 3)
        take.sentences = [{"text": seg.text, "start": 0.0, "duration": round(speak, 3)}]
        return take

    tmp_dir = dest.parent / f".{dest.stem}-sents"
    tmp_dir.mkdir(exist_ok=True)
    try:
        parts, worst_cer, tries, skipped = [], 0.0, 1, False
        meta, at = [], 0.0
        for i, s in enumerate(sents, 1):
            p = tmp_dir / f"{i:02d}.wav"
            take = _render_one(engine, Segment(seg.index * 100 + i, f"{seg.label}.{i}", s), p)
            parts.append(p)
            d = probe_duration(p)
            meta.append({"text": s, "start": round(at, 3), "duration": round(d, 3)})
            at += d + SENT_GAP
            worst_cer = max(worst_cer, take.cer)
            tries = max(tries, take.attempts)
            skipped = skipped or take.qc_skip is not None
        _concat_with_gap(parts, dest, SENT_GAP)
        _pad_tail(dest, _para_gap())
        # 段级 Take 的情绪/语速按段落声明记录（与上面逐句渲染无关：
        # 一个段落的情绪对它内部每个句子都成立）；注入记录按段汇总去重。
        emo_name, _ = resolve_emotion(engine.cfg, seg.emotion)
        spd_name, _ = resolve_speed(engine.cfg, seg.speed)
        seg_injections = speakable_traced(seg.text, getattr(engine, "kind", "qwen3_tts"))[1]
        return Take(seg.index, seg.label, seg.text, dest.name,
                    round(probe_duration(dest), 3), round(worst_cer, 4), tries, meta,
                    qc_skip="asr-blind" if skipped else None,
                    speakable=speakable(seg.text, getattr(engine, "kind", "qwen3_tts")),
                    emotion=emo_name, speed=spd_name,
                    g2p_injections=seg_injections or None)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# 每句首尾的淡入淡出长度。
#
# **句子边界必须淡，不能硬拼。** IndexTTS 的 mel 解码遇到停止符就收，尾音是被硬切的，
# `_trim_silence` 也救不了——它按阈值裁，而最后一个「有声样本」本来就在文件末尾，裁无可裁。
# 2026-07-29 实测：37 个句子边界的句尾峰值中位 1513、最高 3022，硬拼上数字静音就是
# 一次包络阶跃，听感是「登」的一声。改成按句合成之后爆点从 20 个涨到 57 个，比改之前更吵。
# 20ms 对语音来说听不出（一个音素通常 50–200ms），但足以把阶跃磨掉。
SENT_FADE = 0.020


def _fade_edges(samples: list[int], sr: int) -> list[int]:
    """余弦淡入淡出。

    **不要用线性。** 线性淡出在淡出的起点留下一个折角——包络连续但导数不连续，
    那个折角本身就会响。2026-07-29 实测 10ms 线性淡出后仍有 6/37 个句边界
    超过 -29 dBFS，最响 -23 dBFS。余弦曲线两端导数都为零，没有折角。
    """
    k = min(int(sr * SENT_FADE), len(samples) // 2)
    if k <= 0:
        return samples
    out = list(samples)
    for i in range(k):
        g = 0.5 * (1.0 - math.cos(math.pi * i / k))
        out[i] = int(out[i] * g)
        out[-1 - i] = int(out[-1 - i] * g)
    return out


def _concat_with_gap(parts: list[Path], dest: Path, gap: float) -> None:
    import wave
    import struct

    with wave.open(str(parts[0])) as w:
        sr = w.getframerate()
    silence = [0] * int(sr * gap)
    out: list[int] = []
    for i, p in enumerate(parts):
        with wave.open(str(p)) as w:
            n = w.getnframes()
            out.extend(_fade_edges(list(struct.unpack(f"<{n}h", w.readframes(n))), sr))
        if i < len(parts) - 1:
            out.extend(silence)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(struct.pack(f"<{len(out)}h", *out))


def _para_gap() -> float:
    """段与段之间的停顿秒数（段落呼吸），`config/project.json` 的 `script.para_gap`。

    2026-08-10 用户定 0.4s：段间原本背靠背硬拼接、毫无停顿，听感急促；而且 12ms
    的淡入淡出只磨掉波形阶跃的一小段，段边界咔嗒声仍在。0.4s 纯静音一次解决两个：
    说话人换气的常规量级 + 「静音→静音」没有幅度跳变，不可能咔嗒。
    """
    return float(paths.conf("script.para_gap", 0.4))


def _pad_tail(path: Path, secs: float) -> None:
    """段尾追加 `secs` 秒纯静音。必须在 `_trim_silence` / `_concat_with_gap` 之后调用，
    否则会被裁掉。纯数字零不会产生包络阶跃（见 SENT_FADE 注释）。
    """
    if secs <= 0:
        return
    import wave
    import struct
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        data = w.readframes(w.getnframes())
    extra = int(round(sr * secs))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(data + struct.pack(f"<{extra}h", *([0] * extra)))


def _take_path(dest: Path, attempt: int) -> Path:
    """第 attempt 次尝试的临时音频文件。"""
    return dest.parent / f".{dest.stem}.{attempt}.wav"


def _cleanup_takes(dest: Path, attempts: int) -> None:
    """删掉全部尝试的临时文件。"""
    for i in range(1, attempts + 1):
        _take_path(dest, i).unlink(missing_ok=True)


def _best_take(dest: Path, want: float) -> tuple[int, float] | None:
    """豁免路径：从几次尝试里挑「时长达标且最接近估算时长」的一次。

    ASR 盲区段的回读内容不可信，时长是唯一客观质量指标——漏读/重复会改变
    时长（超带被排除），同一句不同种子之间「哪个念得稳」只有时长能比。
    """
    best = None
    for i in range(1, ATTEMPTS + 1):
        p = _take_path(dest, i)
        if not p.exists():
            continue
        d = probe_duration(p)
        ratio = d / want if want else 0.0
        if not (DUR_BAND[0] <= ratio <= DUR_BAND[1]):
            continue
        if best is None or abs(d - want) < abs(best[1] - want):
            best = (i, d)
    return best


def _render_one(engine: Engine, seg: Segment, dest: Path) -> Take:
    """生成一句，直到它通过质检；用尽重试仍不过则抛错。

    **ASR 盲区豁免（2026-08-15 加，2026-08-16 改判据）：** Qwen3-TTS 用日语参考
    音色念中文时，Whisper 会把整句听成假名/近音字（「世界忽然退远了」→
    「クランテイエンロ」），CER 30-100%，而音频实际念对了（人耳确认）。
    这是 ASR 对音色的失真，不是 TTS 随机念错（S4：门禁拿不到能证伪的信息时，
    跳过样本，不定罪）。

    **豁免判据就是「3 次不同种子全部不达标 + 时长达标」。**
    旧判据还要求回读文本两两相似（_reads_agree），2026-08-16 段落 5.1 实测
    证明它是错的：三次回读在「假名乱码」与「近音中文」两种失真模式间摇摆，
    CER 60/60/30%、内容互不相似，而音频是对的——「转录内容稳定」只是盲区的
    充分条件，不是必要条件。到这一步时，三个不同种子生成的音频 Whisper
    全部听不出正常内容，「无论 TTS 怎么生成 ASR 都严重失真」本身就是盲区证据。
    跑飞风险由两道闸兜住：漏读/重复会改变时长（被 ok_dur 拦下）；豁免只保留
    音频并标 `qc_skip`，成片交人前必须人耳确认——跳过不是通过。
    """
    want = expected_duration(seg.text)
    last = ""
    heard_all: list[str] = []
    # 情绪/语速在循环外解析一次（架构设计 3.3）。表外词在这里报错而不是静默回退：
    # 静默回退会造出「写了但没生效」的安慰剂，而防这种安慰剂正是它们进 manifest 的目的。
    emo_name, emo_params = resolve_emotion(engine.cfg, seg.emotion)
    spd_name, _spd_coef = resolve_speed(engine.cfg, seg.speed)
    engine_kind = getattr(engine, "kind", "qwen3_tts")
    # 每次尝试写独立临时文件（最后 os.replace 到 dest），豁免时要挑 3 次里最好的。
    # 旧实现每次覆盖同一 dest：豁免路径保留的是**最后一次**尝试，而三次是不同种子，
    # 最后一次可能最差（2026-08-16 段落 5.1：attempt 3 把エウテルペ 念成 3s，
    # 前两次歌名时长正常——对 ASR 盲区段，时长是唯一客观质量指标）。
    for attempt in range(1, ATTEMPTS + 1):
        # 只有喂给模型的这份要剥引号。**回读比对那边不用管**——
        # `normalize` 早就把引号算进 `_DROP` 里了，这也正是这个 bug 藏得住的原因：
        # 参考文本没引号，回读多出「非」「匪」两个字，只算 2 处插入，
        # CER 7% 远在 20% 门槛之下，门禁照常放行，要人听出来才发现。
        tmp = dest.parent / f".{dest.stem}.{attempt}.wav"
        custom_seed = getattr(engine, "segment_seeds", {}).get(str(seg.label))
        seed = custom_seed if custom_seed is not None else (attempt * 1000 + seg.index + getattr(engine, "seed_offset", 0))
        # v2 链路（架构设计三.2）：剥符号 → 拼音直注 → readings override。
        # 注入记录进 Take.g2p_injections：机器做的替换与 readings 表一样要可审计。
        spk_text, injections = speakable_traced(seg.text, engine_kind)
        engine.synthesize(spk_text, tmp, attempt, seed=seed, emo_params=emo_params)
        # **裁剪要排在回读之前。** 裁掉的是首尾静音与结尾的机械声，但判据是启发式的，
        # 万一切进了句尾真实的字，只有回读能发现。放在回读之后裁就没人管了。
        # 机械声检测只对 IndexTTS 有意义（Qwen3 无此成因，80 句零触发——见 _trim_silence）。
        _trim_silence(tmp, check_tail=(engine.kind == "indextts"))
        dur = probe_duration(tmp)
        ratio = dur / want if want else 0.0
        heard = transcribe(tmp)
        edits, err = cer(seg.text, heard)
        heard_all.append(heard)

        ok_dur = DUR_BAND[0] <= ratio <= DUR_BAND[1]
        ok_cer = readback_passes(edits, err)

        # 级联仲裁（架构设计三.5）：**主读不过才请仲裁**，不是分数融合——
        # 两个 ASR 的 CER 不可比，混合分数等于把两把不同的尺子接起来量。
        arbitrated = False
        if not ok_cer:
            _, arbiter = _asr_backends()
            if arbiter:
                try:
                    alt = asr.transcribe_audio(tmp, backend=arbiter)[0]
                    a_edits, a_err = cer(seg.text, alt)
                    if readback_passes(a_edits, a_err):
                        heard, edits, err = alt, a_edits, a_err
                        ok_cer = True
                        arbitrated = True
                        heard_all.append(f"[仲裁/{arbiter} 通过] {alt}")
                    else:
                        heard_all.append(f"[仲裁/{arbiter} 也未过] {alt}")
                except Exception as ex:
                    # 不静默：仲裁后端故障会让「主读不过」直接掉进重试链，
                    # 若不说清楚，人看到的现象是「质检莫名变严」
                    print(f"    WARN 仲裁后端 {arbiter} 执行失败: {type(ex).__name__}: {ex}",
                          file=sys.stderr)
        if ok_dur and ok_cer:
            os.replace(tmp, dest)
            _cleanup_takes(dest, ATTEMPTS)
            return Take(seg.index, seg.label, seg.text, dest.name,
                        round(dur, 3), round(err, 4), attempt,
                        speakable=spk_text, emotion=emo_name, speed=spd_name,
                        g2p_injections=injections or None, asr_arbitrated=arbitrated)

        why = []
        if not ok_dur:
            why.append(f"时长 {dur:.1f}s / 估算 {want:.1f}s = {ratio:.2f}×")
        if not ok_cer:
            why.append(f"回读差 {edits} 音节（CER {err:.0%}）「{heard[:40]}」")
        last = "；".join(why)
        print(f"  第 {attempt} 次不过：{last}", file=sys.stderr)

    # 3 次重试都用尽且全不达标。判是否 ASR 盲区：时长达标即豁免，不要求
    # 回读文本两两相似（2026-08-16 段落 5.1：三次回读 CER 60/60/30%、
    # 失真模式在假名乱码与近音中文间摇摆，内容互不相似而音频实际念对）。
    best = _best_take(dest, want)
    if best is not None:
        attempt, dur = best
        os.replace(dest.parent / f".{dest.stem}.{attempt}.wav", dest)
        _cleanup_takes(dest, ATTEMPTS)
        print(f"  ASR 盲区豁免（3 次回读均严重失真，人耳确认）：{seg.text[:30]}…",
              file=sys.stderr)
        print(f"    回读：{['「' + h[:30] + '」' for h in heard_all]}", file=sys.stderr)
        return Take(seg.index, seg.label, seg.text, dest.name,
                    round(dur, 3), round(err, 4), ATTEMPTS,
                    sentences=None, qc_skip="asr-blind",
                    speakable=spk_text, emotion=emo_name, speed=spd_name,
                    g2p_injections=injections or None)

    _cleanup_takes(dest, ATTEMPTS)
    raise SystemExit(
        f"FAIL 段落 {seg.label} 重试 {ATTEMPTS} 次仍不达标（{last}）。\n"
        f"     原文：{seg.text}\n"
        f"     不降标准硬交。可能是这段太长或有生僻表达，改稿或换参考干声后重跑。"
    )


# **合成逻辑版本。任何改变合成输出的代码改动都必须 bump 它。**
#
# 为什么需要这个号：增量复用的判据是「文本没变 + 音色没变 + 合成文本没变」，
# 它看不出**引擎代码**变了。2026-09-11 实测栽在这里——修完 seed 与
# voice_clone_prompt 缓存后跑增量重跑，27 段被静默复用为修复前的随机采样产物，
# 只有因注入表变动而 speakable 变化的 7 段重跑，产出一期「新旧混血」音频，
# 而 manifest 已经是新的，看起来一切正常。
#
# 这是 S1「判据测错了对象」在复用维度上的翻版：判据测的是「配置有没有变」，
# 实际需要的是「合成逻辑有没有变」。配置比对覆盖不了代码。
#
# 版本历史：
#   1 —— Qwen3 CUDA 通道初始（每句重新提取说话人嵌入、无种子控制）
#   2 —— 加 torch.manual_seed + voice_clone_prompt 预计算缓存（2026-09-11）
SYNTH_LOGIC_VERSION = 2


def _voice_fingerprint(cfg: dict) -> dict:
    """增量重跑的音色指纹：决定旧 wav 能不能复用。

    manifest 顶层从第一天就存了 engine/model/ref_audio，却从没参与比对——
    换音色后忘带 `--force` 会静默复用旧 wav，出一期混两种音色的成片且零警告
    （2026-08-16 审计 2-6；当天恰好发生 seg7→seg6 换音色）。readings 影响
    合成文本，一并入指纹（换读音表现在自动重做受影响的段）。
    此外还含 `synth_logic`：**配置指纹拦不住引擎代码变化**，2026-09-11 因此
    产出过一期「新旧混血」音频（见 SYNTH_LOGIC_VERSION 注释）。
    """
    return {
        "engine": cfg["engine"],
        "model": cfg["model"],
        "ref_audio": cfg["ref_audio"],
        "readings": hashlib.sha256(json.dumps(
            cfg.get("readings", {}), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest()[:16],
        # 引擎代码行为版本（见 SYNTH_LOGIC_VERSION 的注释）
        "synth_logic": SYNTH_LOGIC_VERSION,
    }


def _reusable(old: dict, segs: list[Segment], out_dir: Path, cfg: dict) -> dict[int, Take]:
    """从旧 manifest 里挑可复用的段：文本没变 + 音色没变 + 合成文本没变 + wav 在盘。

    读音表的比对是**段级**的：readings 影响的是每段实际喂给模型的文本，
    改一个词只该重做「念出来不一样」的段（WORKFLOW 承诺的「自动重做受影响
    的段」）。此前按全表指纹一刀切，改一个词九段全废（2026-09-02 须贺期
    实录：修段 2/8/9 的读音，指纹一变所有段都不能复用）。段级比对还顺带
    覆盖全表指纹漏检的**键序变化**——指纹 sort_keys 对顺序不敏感，而
    str.replace 按插入序生效，只调键序不改词条时旧行为会静默复用旧音频。
    """
    fp = _voice_fingerprint(cfg)
    texts = {s.index: s.text for s in segs}
    voice_changed = [k for k in ("engine", "model", "ref_audio") if old.get(k) != fp[k]]
    if voice_changed:
        print(f"     音色配置变了（{'、'.join(voice_changed)}），旧配音全部重做")
    readings_changed = old.get("readings") != fp["readings"]
    done: dict[int, Take] = {}
    affected: list[int] = []
    for t in old.get("segments", []):
        take = Take(**{**t, "qc_skip": t.get("qc_skip")})
        if texts.get(take.index) != take.text:
            continue
        if voice_changed:
            continue
        if not (out_dir / take.file).exists():
            continue
        if take.speakable is not None:
            if take.speakable != speakable(take.text):
                affected.append(take.index)
                continue
        elif readings_changed:
            # 旧 manifest 没存 speakable：退回全表指纹比对，行为与从前一致
            continue
        done[take.index] = take
    if readings_changed and not voice_changed and old.get("segments"):
        if affected:
            print(f"     读音表变了，重做受影响段：{'、'.join(map(str, sorted(affected)))}")
        elif any(t.get("speakable") is not None for t in old["segments"]):
            print("     读音表变了，但各段合成文本不变，全部复用")
    return done


def _stale_downstream(episode: Path, audio: list[dict]) -> list[str]:
    """级联校验（2026-08-18 复盘①）：本次重跑改了段时长后，下游 04-clips*.json
    里哪些段级时长已经不对齐。

    局部重跑只重写 manifest，`04-clips.json` / `04-clips.approved.json` 存的还是
    旧时长——approve 与 render 的闸当然会拦，但那是几小时后要渲染时才发现，
    排查还得倒推「哪一步改了什么」。脏数据在产生的这一刻就指出来。
    返回告警行列表，空 = 下游干净或还不存在（还没排片，正常）。
    """
    out: list[str] = []
    for name in ("04-clips.json", "04-clips.approved.json"):
        p = episode / name
        if not p.exists():
            continue
        segs = json.loads(p.read_text(encoding="utf-8"))["segments"]
        violations = verify_alignment(segs, audio)
        if violations:
            out.append(f"{name} 与新配音不对齐（{len(violations)} 段）："
                       + "；".join(violations[:3])
                       + ("…" if len(violations) > 3 else ""))
    return out


# 03.5 打点的允许维度复用 eval 的五键约束（架构设计 2.3）。
# 抄一份到这里必然分叉——评审口径只该有一个真源。
_REVIEW_ALIASES = {"voice": "voice_stability"}


def parse_review_arg(raw: str) -> dict[str, int]:
    """解析 --review 参数（纯函数）：`voice=4,prosody=3` → {"voice_stability": 4, ...}。

    越界/未知键/格式错**当场报错**（E10）：打点是人工评审的唯一入口，
    写错维度名还继续跑，会得到一份「看起来打过点」的空 manifest。
    """
    from .eval import ALLOWED_HUMAN_REVIEW_KEYS

    out: dict[str, int] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SystemExit(
                f"FAIL --review 项 '{item}' 缺 '='（格式：voice_stability=4,prosody=3,misread=4）"
            )
        k, v = item.split("=", 1)
        key = _REVIEW_ALIASES.get(k.strip(), k.strip())
        if key not in ALLOWED_HUMAN_REVIEW_KEYS:
            raise SystemExit(
                f"FAIL 未知评审维度 '{k.strip()}'。允许：{sorted(ALLOWED_HUMAN_REVIEW_KEYS)}"
                f"（另有别名：{sorted(_REVIEW_ALIASES)}）"
            )
        try:
            score = int(v.strip())
        except ValueError:
            raise SystemExit(f"FAIL 评审分 '{v.strip()}' 不是整数")
        if not 1 <= score <= 5:
            raise SystemExit(f"FAIL 评审分 {score} 越界（合法范围 1-5）")
        out[key] = score
    if not out:
        raise SystemExit("FAIL --review 没解析出任何维度")
    return out


def write_review(episode: Path, review: dict[str, int]) -> Path:
    """把 03.5 打点写进已有 manifest 的 human_review 槽位。

    独立于合成：打点发生在听完音频之后，不该触发重合成。
    **合并而不是覆盖**——一次只打 03.5 的三项、下次再补 05 的两项是正常流程。
    """
    manifest_path = episode / "03-audio" / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"FAIL 找不到 {manifest_path}，先跑配音再打点")
    mf = json.loads(manifest_path.read_text(encoding="utf-8"))
    merged = dict(mf.get("human_review") or {})
    merged.update(review)
    mf["human_review"] = merged
    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, manifest_path)
    print(f"[OK] 打点已写入 {manifest_path}")
    for k, v in sorted(review.items()):
        print(f"     {k} = {v}")
    return manifest_path


def run(episode: Path, force: bool = False, cfg_path: Path = CONFIG,
        review: dict[str, int] | None = None) -> Path:
    paths.require_data()
    script = episode / "02-script.md"
    if not script.exists():
        raise SystemExit(f"FAIL 找不到 {script}")

    # --review 是独立动作：听完音频后打点，不该触发重合成
    if review is not None:
        return write_review(episode, review)

    cfg = load_config(cfg_path)
    segs = parse_script(script)
    out_dir = episode / "03-audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    # 重跑合成必须保住已有打点：manifest 是整份重写的，不先取出就会把
    # 人工评审结果抹掉（而打点是有人时成本的动作）
    existing_review = None
    if manifest_path.exists():
        try:
            existing_review = json.loads(manifest_path.read_text(encoding="utf-8")).get("human_review")
        except Exception as e:
            print(f"WARN 旧 manifest 不可读，human_review 将丢失: {e}", file=sys.stderr)

    done: dict[int, Take] = {}
    if manifest_path.exists() and not force:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        done = _reusable(old, segs, out_dir, cfg)

    miss = _unmeasured_titles([s.text for s in segs])
    if miss:
        print(f"WARN 歌名 {'、'.join(miss)} 在 voice.json 的 titles 里没有实测时长"
              f"（值为 null），按 cpm 估算——cpm 估英日歌名会高估 1.5-2 倍，"
              f"可能触发时长门禁误杀。试音补测："
              f"python -m pipeline.tts probe \"{' '.join(miss)}\"", file=sys.stderr)

    engine: Engine | None = None
    takes: list[Take] = []
    t0 = time.perf_counter()
    vo_hash = compute_script_vo_hash(script)
    def _save_manifest():
        total = sum(t.duration for t in takes)
        # 原子写（2026-08-16 审计 2-27）：写一半崩溃的 manifest 会让下次重跑在
        # json.loads 上裸抛，比「没有 manifest」更难办
        tmp = manifest_path.with_name(manifest_path.name + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "script_vo_hash": vo_hash,
                    **_voice_fingerprint(cfg),
                    "total_duration": round(total, 3),
                    # 03.5/05 人工打点槽位（架构设计 2.3）：eval 从这里取主观分。
                    # 无打点时为 null——**不是 0 分**，缺失与低分必须可区分（E10）。
                    "human_review": existing_review,
                    "segments": [asdict(t) for t in takes],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, manifest_path)

    for seg in segs:
        if seg.index in done:
            takes.append(done[seg.index])
            print(f"skip 段落 {seg.label}（已有 {done[seg.index].duration:.1f}s）")
            continue
        if engine is None:
            print(f"载入 {cfg['model']} …")
            engine = Engine(cfg)
        dest = out_dir / f"seg-{seg.index:02d}.wav"
        take = render_segment(engine, seg, dest)
        takes.append(take)
        _save_manifest()
        print(f"OK   段落 {seg.label}  {take.duration:5.1f}s  CER {take.cer:4.0%}  "
              f"{take.attempts} 次  {seg.text[:20]}…")

    total = sum(t.duration for t in takes)
    _save_manifest()
    print("-" * 60)
    print(f"OK {len(takes)} 段，总时长 {total / 60:.1f} 分钟，"
          f"耗时 {time.perf_counter() - t0:.0f}s → {manifest_path}")
    stale = _stale_downstream(episode, [asdict(t) for t in takes])
    if stale:
        print("WARN 本次配音改了段时长，下游排片已脏：\n"
              + "".join(f"     {s}\n" for s in stale)
              + f"     渲染前必须清算：跑 `python -m pipeline.clips {episode} --refit`"
                f" 把差额吸进末片（人改过的 start/source 不动）；差额过大 refit 会拒绝，"
                f"那就整条重跑 `python -m pipeline.clips {episode}` 并重走 05。",
              file=sys.stderr)
    band = episode_duration_band(episode) or tuple(paths.conf("video.duration_band", [120.0, 240.0]))
    if not band[0] <= total <= band[1]:
        print(f"WARN 成片时长 {total / 60:.1f} 分钟，超出目标区间", file=sys.stderr)
    skipped = [t for t in takes if t.qc_skip]
    if skipped:
        print(f"WARN {len(skipped)} 段 ASR 盲区豁免（回读稳定失真，CER 不可用）："
              f"{', '.join(t.label for t in skipped)}。"
              f"成片交人前必须人耳听一遍这些段。", file=sys.stderr)
    return manifest_path


def probe(text: str, cfg_path: Path, dest: Path, ref: Path | None = None) -> None:
    """试音：单句生成 + 回读，用来比较引擎和参考干声。

    选音色是 Phase 0 的一次性动作，不进每期循环。
    """
    cfg = load_config(cfg_path)
    if ref is not None:
        cfg = {**cfg, "ref_audio": str(ref), "ref_text": _ref_text_for(ref)}
    engine = Engine(cfg)
    t0 = time.perf_counter()
    engine.synthesize(speakable(text), dest, attempt=1, seed=0)
    dur = probe_duration(dest)
    heard = transcribe(dest)
    edits, err = cer(text, heard)
    print(f"{dest}  {dur:.2f}s  {dur / (time.perf_counter() - t0):.2f}× 实时")
    print(f"  回读差 {edits} 音节（CER {err:.0%}）：{heard}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="给一期稿件配音（默认命令）")
    r.add_argument("episode", type=Path)
    r.add_argument("--force", action="store_true", help="忽略已有产物，全部重生成")
    r.add_argument("--config", type=Path, default=CONFIG)
    r.add_argument("--review", type=str, default=None,
                   help="写入 03.5 结构化打点（如 voice=4,prosody=3,misread=4），不触发重合成")

    p = sub.add_parser("probe", help="单句试音")
    p.add_argument("text")
    p.add_argument("--config", type=Path, default=CONFIG)
    p.add_argument("--out", type=Path, default=paths.VOICE / "probe" / "probe.wav")
    p.add_argument("--ref", type=Path, help="临时换参考干声（相对仓库根），用于比音色")

    argv = sys.argv[1:]
    if argv and argv[0] not in {"run", "probe", "-h", "--help"}:
        argv = ["run", *argv]
    a = ap.parse_args(argv)

    if a.cmd == "probe":
        probe(a.text, a.config, a.out, a.ref)
    else:
        run(a.episode, a.force, a.config,
            parse_review_arg(a.review) if a.review else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
