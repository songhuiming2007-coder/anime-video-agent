"""口播稿机检。

write-script 的自检清单里可判定的那部分，交稿前跑，不靠 agent 自觉。
主观项（张力立不立得住、公道话像不像反方）机器判不了，仍需人看。

    python -m pipeline.check_script <稿件.md>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import bgm, paths
from .keys import SP_NUM_LOOSE, key_sort, sp_key

# 中文口播字/分钟，只用来粗筛。**真实时长一律以 03-audio/manifest.json 为准。**
#
# 2026-07-29 三次实测：
#     886 字 / 139.5s = 380   音色 audio_01
#     886 字 / 122.5s = 434   音色 audio_03
#    1024 字 / 185.4s = 331   音色 audio_03，引用台词多、短句多
#
# 原值 280 是拍脑袋的假设，已推翻。但也别把某一次实测当常数：后两次同一个音色差了 24%，
# 变量是**标点密度**——引号、句号一多，停顿就多。语速同时受音色和文风影响，不是稳定量。
# 所以这里取观测区间的中值，只求量级对，不求准。
CPM = paths.conf("script.cpm", 380)
# 按 331–434 这个区间反推，870–1300 字对应 2.0–3.9 分钟，两端都落在 2–4 分钟目标内。
# 换音色或文风大变时重测上面三个数，确认这条带子还罩得住。同步在 skills/write-script/SKILL.md
#
# 这是**默认带**，对短打驳论类选题成立（CLAUDE.md「内容参数」的账号实测依据）。
# 不是所有选题都该套这条——人物志/传记类天然需要更多篇幅，把默认值改宽松等于
# 拿驳论类的数据给不同体裁的内容背书。所以留默认不动，另开一条 per-episode 的路：
# 见 episode_duration_override()。
MIN_CHARS = paths.conf("script.min_chars", 870)
MAX_CHARS = paths.conf("script.max_chars", 1300)
MIN_ANCHOR = paths.conf("script.min_anchors", 3)   # 剧情锚点下限：带画面集号字段的段落数

# `01-topic.md` 的 `缩段不注水: 是`：字数下限 × shrink_factor。
# 依据 ADR-0005 补记三：字数下限逼 agent 注水，被骂的机器味句子相当部分是
# 凑字数补出来的。这是「允许承认这段没料、缩短」，不是放宽默认带。
#     不锚 `$`，理由同下面 DURATION_FIELD 的同款教训：01-topic.md 允许行尾挂
#     `# 备注`，锚了会静默退回默认档且零报错——2026-08-14 审计实测踩到。
SHRINK_FACTOR = paths.conf("script.shrink_factor", 0.8)
SHRINK_FIELD = re.compile(r"^\s*缩段不注水\s*[:：]\s*是\b", re.M)

# `01-topic.md` 里 `时长目标: 7-8分钟` 这样的字段，覆盖本期的字数/时长带。
# 不填就是上面 MIN_CHARS–MAX_CHARS 这条默认带，行为不变。
#     不锚 `$`：01-topic.md 允许行尾挂 `# 备注`（BGM 字段已经这么用），
#     锚了 `$` 会把注释一起吃进数字组，2026-08-09 实测直接踩到——
#     字段写了但正则不匹配，静默退回默认档，没有任何报错。
DURATION_FIELD = re.compile(r"^\s*时长目标\s*[:：]\s*([\d.]+)\s*[-–~]\s*([\d.]+)\s*分钟", re.M)
# 「写了字段」按行首判（冒号可漏），正文提及不算——与 qc.py 同款，见那边注释
DURATION_FIELD_HINT = re.compile(r"^\s*时长目标\s*[:：]?", re.M)


def episode_duration_override(script_path: Path) -> tuple[float, float] | None:
    """从这一期 `01-topic.md` 的 `时长目标` 字段读目标时长（分钟）。

    没有这个文件、或没写这个字段，返回 None，调用方退回默认带——这是刻意的
    软失败：字段是可选项，不是「文件必须存在」的前置检查。

    **行首**写了字段但格式认不出 → 直接报错，不静默回退（2026-08-16 审计
    2-10）：稿子按错字数带写、机检按默认带判，两边对不上且报告里看不出来。
    正文/备注里**提到**字段名不算写了字段，不拦（二次审计 §6-1 收窄）。
    qc.py 的同名函数同款行为，两处口径必须一致。
    """
    topic = script_path.parent / "01-topic.md"
    if not topic.exists():
        return None
    text = topic.read_text(encoding="utf-8")
    m = DURATION_FIELD.search(text)
    if m is None and DURATION_FIELD_HINT.search(text):
        raise SystemExit(
            "FAIL 01-topic.md 的 `时长目标` 格式认不出，应为 `时长目标: 7-8分钟`"
            "（半角连字符 - / – / ~，范围写法）。\n"
            "     静默回退默认字数带会把这篇稿子按错标准判——先改对格式再跑")
    return (float(m.group(1)), float(m.group(2))) if m else None


def shrink_no_padding(script_path: Path) -> bool:
    """从这一期 `01-topic.md` 读 `缩段不注水: 是`。

    没有这个文件、或没写这个字段，返回 False——默认下限不变。这是显式声明
    「这段没料、宁可缩短」，不是默认放宽（ADR-0005 补记三：字数下限逼注水）。
    """
    topic = script_path.parent / "01-topic.md"
    if not topic.exists():
        return False
    return bool(SHRINK_FIELD.search(topic.read_text(encoding="utf-8")))

# 六种题材（见 skills/write-script 第 4 节）。`类型` 行允许带括号备注
# （「人物志（经历+点评，编年体）」），按关键词匹配主词，不在六种里就返回空串。
GENRES = ("人物志", "剧情回顾", "杂谈", "盘点", "共鸣", "纪录片")
TOPIC_FIELDS = re.compile(r"^[ \t]*(类型|模式)[ \t]*[:：][ \t]*(.+)", re.M)


def episode_genre(script_path: Path) -> tuple[str, str]:
    """这一期的（类型，模式），从 `01-topic.md` 读。

    类型只对杂谈有区分意义（驳论/立论/吐槽各自判据不同），其余题材不区分模式。
    读不到文件或字段，返回 ("", "")——软失败，不报错，调用方退回通用提示。
    2026-08-09 改：主观项提示原先焊死「张力、公道话」，人物志/剧情回顾/共鸣
    根本没有这两个字段，提示每期都念错题——机检在证伪，提示却在逼人去查
    本期不存在的东西。
    """
    topic = script_path.parent / "01-topic.md"
    if not topic.exists():
        return "", ""
    genre = mode = ""
    for key, value in TOPIC_FIELDS.findall(topic.read_text(encoding="utf-8")):
        value = value.strip()
        if key == "类型":
            genre = next((g for g in GENRES if g in value), "")
        elif key == "模式":
            mode = value
    return genre, mode


def subjective_hint(genre: str, mode: str) -> str:
    """机检末尾的主观项提示。判据按题材来，见 write-script 第 4、9 节——机检只证伪，
    主观项立不立得住留给人看，所以提示必须对得上这一期的题材，否则就成了逼人去查
    本期不存在的东西。"""
    common = "骨架是否与上一期不同"
    if genre == "杂谈":
        if mode == "吐槽":
            return f"吐槽不吃张力/公道话判据；{common}。"
        if mode == "立论":
            return f"疑点真说不通、每段有具体剧情+原台词当证据、标了推测和真缺点；{common}。"
        return f"靶子是真听过的说法、论据回答不这样会怎样/为什么不是别的原因、公道话像不像反方；{common}。"
    if genre == "人物志":
        return f"有没有给角色安论点（刻意找张力=哗众取宠）、点评是否贴着当下这一幕；{common}。"
    if genre == "剧情回顾":
        return f"因果链讲没讲清、是不是时间线流水账、结尾是否回环/一句判断/悬念；{common}。"
    if genre == "盘点":
        return f"标准立没立住、每段落点是不是观众、篇幅是否不平均；{common}。"
    if genre == "共鸣":
        return f"有没有归纳道理、情感是否靠形容词堆、结尾落没落具体东西；{common}。"
    if genre == "纪录片":
        return f"实证段是否死死咬住 01-hero-shots 物证、思辨段是否留出意象留白、是否有为了凑镜头而强行错配画面的妥协段落（无物证必须停下来调 acquire 补料）；{common}。"
    return f"{common}、篇幅有没有平均分配。"

# 单句上限。**2026-08-04 从 40 提到 90，因为 40 是「三期零长句」的直接成因。**
#
# 旧值的注释写的是「超过念着断气」，而这份文档自己的核心发现是**断气发生在逗号之间，
# 不在整句**——两句话互相矛盾，实际执行时数字赢了，文字输了。
# 真正的换气约束现在由 MAX_BREATH 承担，这里只留一道防失控的网。
MAX_SENT = paths.conf("script.max_sentence", 90)
# 单个气口段上限：这才是「一口气念得完吗」的真判据。
# 按实测语速 434 字/分（7.2 字/秒），30 字 = 4.2 秒，一口气绰绰有余。
MAX_BREATH = paths.conf("script.max_breath", 30)

# 节奏。**2026-08-04 推翻了 08-03 的判据。**
#
# 08-03 加的是「气口段均长 ≥11 字」，理由是三期稿子都在 8.5–9.2 而 bangumi 长评是 13.3。
# 这条是误诊，08-04 拿 11 篇人类文本重测时被打脸：
#
#     样本                          气口均长   句长 CV
#     知乎「外卖骑手」30400 赞          7.1      0.45
#     知乎「中年男人的悲哀」18399 赞    7.5      0.67
#
# **赞数最高的两篇都会被 ≥11 判 FAIL。** 气口均长根本不区分好坏，
# 它只是在惩罚短句多的文风。所以这道门禁删掉。
#
# 真正分得开的是**起伏**（业界叫 burstiness），不是均值：
#
#                        句长 CV   最长句   超 60 字的句子
#     AI 稿·自我牺牲        0.46     40 字      0%
#     AI 稿·适合大老师      0.48     35 字      0%
#     AI 稿·谁最适合你      0.34     36 字      0%
#     人类 11 篇            0.45–0.81  43–212 字   0–35%（中位 16%）
#
# 注意第三期是三期里最均匀的一篇——08-03 那次修改把均值调对了，把方差调没了。
# 人耳听出来的「每期一个样」，量出来就是这个 0.34。
#
# 阈值 0.55：人类 11 篇里 10 篇过，三期 AI 稿全不过。
#
# **已知这条可以被游戏化**（塞一句超长的再塞一堆超短的，CV 就上去了）。
# 挡这个的是 MAX_STACCATO——短句配额还在，两条一起才逼得出真起伏。
# 但要清楚：机检只能证伪，不能生成。CV 过了不等于稿子好。
MIN_BURST = paths.conf("script.min_burstiness", 0.55)   # 整句字数的变异系数下限
MIN_LONG_SENT = paths.conf("script.min_long_sentences", 2)  # 超 45 字的整句下限
LONG_SENT_LEN = 45
MAX_STACCATO = paths.conf("script.max_staccato", 5)  # ≤8 字整句的全篇配额
STACCATO_LEN = 8
BREATH_SPLIT = re.compile(r"[，,、；;：:。！？…—\s]+")

# ---------------------------------------------------------------------------
# 量过但**故意不设门禁**的两项。留在这里是为了下次不要有人再去加：
#
# 「不是 X，是 Y」这类否定平行结构：AI 稿 0.22–0.37 次/百字，人类样本 0–0.47。
#   人类里最高的那篇（bgm-361121）比三期 AI 稿都高。**分不开就不许当门禁。**
#   它确实是 AI 味的来源之一，但那是写法问题，写进 SKILL.md 当判据，不写成正则。
#
# 语气词密度（啊/吧/嘛/哇…）：AI 稿 0–0.33 次/百字，口语体人类样本 1.21。
#   看着差得很远，但口语体样本**只有一篇**。按 CLAUDE.md「凡是要写进配置的判断，
#   都不许带『可能』。验不了就先不写」，样本量到之前不立这条规矩。
# ---------------------------------------------------------------------------

# 论文连接词。**必须出现在句首或句读之后**才算，否则「不因此而放弃」这类内嵌用法会误报。
#
# 「最后」单独一档：它是这几个词里唯一身兼时间词的，「最后那一集」「最后他还是走了」
# 都是正常叙事。论文腔的那个「最后」后面必带逗号——「最后，我们可以看到……」。
# 所以只有 `最后，` 算违规。
#
# 2026-07-30 实测踩过：「最后那一集，她说了一句」被判违规，而它完全正常。
# **检查项误报一次，人就会开始怀疑其余十条**，所以这里宁可漏判也不要误判。
# 只留一个捕获组：findall 的结果要直接进 detail 字符串，多组会返回元组。
# 「最后」的逗号用前瞻匹配，不吃进组里。
PAPER_WORDS = re.compile(
    r"(?:^|[。？！，、；：\s])(因此|然而|此外|综上|总之|首先|其次|再者|最后(?=[，,]))")
# 跨期套话。2026-08-03 增补，依据是前三期的实测复用率：
#
#     「当然要说句公道话／实话」  3/3 期
#     「我知道你要说什么」        2/3 期
#     「这就是我的答案」          2/3 期
#     「所以别误会」              2/3 期
#
# 这些短语一条内容都不携带，作用只是宣告「下面这句重要」或「我要让步了」。
# 它们能跨期复用恰恰因为跟话题无关——**跟话题无关的句子，就是每期长得一样的原因。**
#
# 判据必须是精确串，不做近义扩展：模糊匹配会把正常叙事判死，而
# 「检查项误报一次，人就会开始怀疑其余十条」。「注意」「你看」要求紧跟逗号，
# 否则「你看他那个表情」这类正常句子会中枪。
STOCK = re.compile(
    r"(说句公道话|说句实话|我知道你要说什么|这就是我的答案|所以别误会|你先别急"
    r"|(?:^|[。？！\s])注意[，,]|(?:^|[。？！\s])你看[，,])")
# 报菜名：句首的「第一条／第二点」并列。单独一个是正常写法，凑够两个就是在念清单。
# 「第三季第十一集」不会命中——它没有「条」「点」，也不在句首。
ENUM = re.compile(r"(?:^|[。？！\s])(第[一二三四五][条点])")
PARENS = re.compile(r"[（）()]")
QUOTES = re.compile(r"[“”‘’「」『』\"']")
# 阿拉伯数字：**TTS 对它的读法不可控。**
#
# 2026-08-03 实测 IndexTTS 把「平时只做2件事」念成「平时只做**匪**件事」。
# 注意这个「匪」字——引号那次（「谁更该“赢”」→「谁更该非赢匪」）吐的也是它。
# 同一个模型遇到念不出来的字符就拿垃圾音节去凑，字符类别不同，病因是同一个。
#
# **回读质检抓不住，而且不该指望它抓。** 59 个字的段落里错 1 个字，CER 1.7%，
# 绝对错数 1——`MIN_EDITS` 那道闸本来就是为了不让单字替换去冤枉短段落，
# 它保护的正是这种形状。调低它会让整条门禁天天误报，是拿一个真问题换一堆假问题。
#
# 所以挡在这里，与「无括号」同源：**稿子里出现念不出来的东西，是稿子的问题。**
# （「无引号」原先也在此列，2026-10-24 降为只提示，理由见该检查处，D134。）
# 不在 `tts.speakable` 里自动转中文，因为转法有歧义——2 可以是二、两、俩，
# 机器替人选一个，等于把念法的决定权从写稿的人手里拿走且不留痕迹。
#
# 拉丁字母不在此列：同一期的 yy 与 coding 都念得正常，没有证据就不立规矩。
#
# **读音表接管的词条豁免**（2026-09-13）：规则的理由是「TTS 对阿拉伯数字的读法不可控」，
# 而 `config/voice.json` 的 readings 表把读法钉死了，理由不再成立（三期 `M2U → M two U`，
# 同 redjuice 先例）。豁免范围只限**表内键的原样命中**，不做模式匹配——否则一旦放宽成
# 「含字母的串就放行」，这条规则就基本作废了。豁免了谁一并打在 detail 里，不静默放行。
#
# 拼音注入表不参与豁免：它的值是 TONE3 拼音，本来就写不出拉丁字母+数字的词条，
# 那种词条只能落在 readings 上。
DIGITS = re.compile(r"[0-9０-９]")


@lru_cache(maxsize=1)
def _readings_keys() -> tuple[str, ...]:
    """`config/voice.json` readings 表的键（长键在前）。

    只服务「无阿拉伯数字」的豁免判定，见 DIGITS 上方的理由。直接读 voice.json
    而不 import tts：tts 拖着 pypinyin/asr 一串重依赖，而机检只跑稿件时
    不需要碰音频链路。
    """
    cfg = json.loads((paths.CONFIG / "voice.json").read_text(encoding="utf-8"))
    keys = [k for k in cfg.get("readings", {}) if not k.startswith("_")]
    return tuple(sorted(keys, key=len, reverse=True))


def _strip_readings_covered(body: str) -> tuple[str, list[str]]:
    """把已被读音表接管的词条从正文里剔除，返回 (剔后正文, 剔掉的词条)。

    长键在前，与 `g2p.inject` 同一套降序理由：短键先吃会拆掉长键的命中。
    """
    exempt = []
    for key in _readings_keys():
        if DIGITS.search(key) and key in body:
            body = body.replace(key, "")
            exempt.append(key)
    return body, exempt
VISUAL = re.compile(r"(中景|近景|远景|全景|特写|逆光|镜头|构图|俯拍|仰拍|机位)")
VAGUE = re.compile(r"(那几次事情|那些人|某些|有些人|某个角色|某部作品|后面这句|这类人)")
# 片尾套话。它是固定收尾、不承担内容，不占正文段数也不进字数——
# 否则每期都会把段数门槛顶穿一格，检查项一旦长期误报就没人看了。
OUTRO = re.compile(r"(下期再见|下期见|就到这里|我们下期|感谢观看|拜拜)")
@dataclass
class Check:
    name: str
    ok: bool
    detail: str


# D104：写稿模型给拿不准的地方加的待办标记。标记加得对，但 02.5 照样能批准封板——
# supercell 卷一第一期批准时稿里还有 5 处【待核】、1 处【待你定】，其中一处是错的。
# 机检与 02.5 批准用同一个扫描，口径一致。
PENDING_MARK = re.compile(r"【(?:待核|待你定)[^】\n]*】?")


def pending_marks(text: str) -> list[tuple[str, str]]:
    """稿里没处理完的【待核】【待你定】→ [(段落号或「段外」, 标记原文前 30 字)]。"""
    out: list[tuple[str, str]] = []
    label = "段外"
    for line in text.splitlines():
        h = re.match(r"^##\s*段落\s*(\S+)", line)
        if h:
            label = h.group(1)
            continue
        if re.match(r"^#{1,2}\s", line):   # 其他一二级标题（引用核对区等）出了段落；### 仍算段内
            label = "段外"
        out.extend((label, m.group(0)[:30]) for m in PENDING_MARK.finditer(line))
    return out


def pending_marks_summary(marks: list[tuple[str, str]], limit: int = 5) -> str:
    shown = "；".join(f"{'段外' if l == '段外' else '段' + l}：{s}" for l, s in marks[:limit])
    return f"{len(marks)} 处：{shown}" + ("……" if len(marks) > limit else "")


def parse(text: str) -> tuple[list[str], list[str], list[str]]:
    vo = re.findall(r"^配音[：:]\s*(.+)$", text, re.M)
    q = re.findall(r"^\s*查询[：:]\s*(.+)$", text, re.M)
    alt = re.findall(r"^\s*备选[：:]\s*(.+)$", text, re.M)
    return vo, q, alt


def parse_episodes(text: str) -> list[tuple[str, str]]:
    """段落块内的 `集:` 字段 → [(段落号, 原文)]（ADR-0004）。

    **必须按块切。** 引用核对区里写「S01E01 21:21」是逐字引语的自查记录，
    不是检索约束，全文抓会把「21:21」之类整行当集号。切块口径与
    `clips.parse_shots` 一致：`## 段落 N` 到下一个标题之间。
    """
    out = []
    for m in re.finditer(r"^##\s*段落\s*(\S+)\s*\n(.*?)(?=^##\s*段落|\Z)",
                         text, re.M | re.S):
        ep = re.search(r"^\s*集[：:]\s*(.+)$", m.group(2), re.M)
        if ep:
            out.append((m.group(1), ep.group(1).strip()))
    return out


def _norm_ep(raw: str) -> str | None:
    """集号归一化为 `SxxEyy` / `SPxx`（特典）规范形。允许带语义后缀如 `SP41-ninelie`。"""
    m = re.fullmatch(r"S(\d{1,2})E(\d{1,2})", raw, re.I)
    if m:
        return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
    m = re.fullmatch(rf"SP({SP_NUM_LOOSE})(?:[-_][^\s:]+)?", raw, re.I)
    return sp_key(int(m.group(1))) if m else None


# 锚点时间码（ADR-0008，2026-08-27 加；2026-09-06 加可选番名前缀支持跨番；
# 2026-09-07 加 SP 特典集号，ADR-0010 决策二；支持 SP 带语义后缀如 SP41-ninelie）。
# 与 clips._ANCHOR 同口径：`S01E01 17:50` 或区间 `S01E01 17:50-18:20`，跨番
# `罪恶王冠 S01E01 17:50`，特典 `EGOIST SP01 108:45`；分钟允许三位（剧场版 96:08），
# 秒两位、允许小数秒。两处各自维护（与 配音/查询 字段的三处同口径先例一致），
# 改一处必须同步另一处。组号全部具名：anime/season/episode/sp/m0/s0/m1/s1。
ANCHOR_TC = re.compile(
    rf"^(?:(?P<anime>.+?)\s+)?"
    rf"(?:S(?P<season>\d{{1,2}})E(?P<episode>\d{{1,2}})|SP(?P<sp>{SP_NUM_LOOSE})(?:[-_][^\s:]+)?)\s+"
    rf"(?P<m0>\d{{1,3}}):(?P<s0>\d{{2}}(?:\.\d+)?)"
    rf"(?:\s*[-–~～]\s*(?P<m1>\d{{1,3}}):(?P<s1>\d{{2}}(?:\.\d+)?))?$", re.I)

# 与 clips._ANCHOR_FIELD 同口径（2026-09-07 单段多锚点）：`锚点:` 行后续的
# 缩进行都是锚点列表的一部分，直到下一个已知字段、空行或段落块尾。
_ANCHOR_FIELD = re.compile(
    r"^\s*锚点[：:][ \t]*([^\n]+(?:\n[ \t]+(?!\s*(?:查询|备选|人物|场景|集|画面|锚点|复用)[：:])[^\n]*)*)",
    re.M)


def _anchor_items(raw_field: str) -> list[str]:
    """锚点字段原文 → 条目列表：逗号（半/全角）与续行都是分隔符（同 clips）。"""
    return [x.strip()
            for line in raw_field.splitlines()
            for x in re.split(r"[,，]", line)
            if x.strip()]


def _patch_pool_name(episode: Path) -> str | None:
    """本期补丁池名（`04-patch/pool.json` 的 `pool` 字段），没有补丁池返回 None（D84 PR-B）。

    只读名字、不做完整性自校验：前缀校验只需要知道「这个名字是不是本期补丁池」，
    池子坏没坏由「锚点指向素材」那一项经 `ingest_patch.load_pool` 判（坏了就报跳过不可判定）。
    """
    try:
        return json.loads((episode / "04-patch" / "pool.json").read_text(encoding="utf-8")).get("pool")
    except (OSError, ValueError, AttributeError):
        return None


def _patch_sources(episode: Path) -> dict:
    """补丁池的 `{SPxx: {path, duration, …}}`；池坏了照 `load_pool` 原样 SystemExit。

    与 clips 同一入口（`ingest_patch.load_pool`），两边对「补丁池里有什么」口径一致。
    """
    from . import ingest_patch  # 延迟 import：只在锚点真指向补丁池时才需要（它拖 numpy）
    pool = ingest_patch.load_pool(episode)
    if pool is None:
        raise SystemExit(f"FAIL {episode.name} 没有补丁池（缺 04-patch/）")
    return {k: v for k, v in pool["sources"].items() if isinstance(k, str)}


#: 同一张图片微动 SP 在一个企划（不属于企划的期就是本期）全部成片里最多出现几次（2026-10-17 人定）。
STILL_SP_MAX = 2

#: 人定超限复用（2026-10-20 人定，D126）：段落画面块的 `复用:` 列出本段超出 STILL_SP_MAX 仍要用的
#: 图片微动 SP，逗号分隔，括号里写理由，如 `复用: supercell SP70（人定：唯一物证）`。
#: 用途是唯一物证在后续卷回扣时不必降级成更弱的画面；由人决定写，机检照样计数，只放行带声明的超出次数，
#: 并把每处声明列在结果里给 02.5 的人看。
#: 视频 SP 的声明带起点时间码（D137），如 `复用: supercell SP12 00:20（理由）`，只放行本段从这个时间起的那个锚点。
_REUSE_FIELD = re.compile(r"^\s*复用[：:][ \t]*(.+)$", re.M)
_REUSE_ITEM = re.compile(
    rf"(?:(?P<anime>.+?)\s+)?SP(?P<sp>{SP_NUM_LOOSE})(?:\s+(?P<m>\d{{1,3}}):(?P<s>\d{{2}}(?:\.\d+)?))?", re.I)

#: 视频 SP（MV、Live，`_is_still` 为假）同一处画面的上限（2026-10-24 人定，D137）：以每个锚点的起点为中心、
#: 前后各 VIDEO_SP_WINDOW_S 秒之内（含边界），同一 SP 的锚点（含它自己）在企划所有卷合计至多 VIDEO_SP_MAX 个。
#: 人在「任意 10 秒窗口」与「每个锚点前后 ±10 秒」两种算法里选了后者，所以 0 s、9 s、18 s 三处也算超。
#: 按片段数计，同段写两遍记两次；区间锚点按起点算。超出的次数落在带时间码 `复用:` 声明的段才放行。
VIDEO_SP_MAX = 2
VIDEO_SP_WINDOW_S = 10.0


def parse_reuse(text: str) -> dict[str, list[str]]:
    """每个段落块的 `复用:` 条目 → {段落号: [条目原文, …]}；括号里的理由去掉。切块口径同 parse_anchors。"""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"^##\s*段落\s*(\S+)\s*\n(.*?)(?=^##\s*段落|\Z)", text, re.M | re.S):
        for f in _REUSE_FIELD.finditer(m.group(2)):
            raw = re.sub(r"[（(][^）)]*[）)]", "", f.group(1))
            out.setdefault(m.group(1), []).extend(x.strip() for x in re.split(r"[,，、]", raw) if x.strip())
    return out


def _is_still(src: dict) -> bool:
    """图片微动素材。与 `clips._is_micro` 同口径：显式 `still` 标（D84 起转图登记），
    或没标的老池按片长 ≤ 8 s 认（EGOIST 池的微动图都没有标）。"""
    return bool(src.get("still")) or src.get("duration", 999) <= 8.0


@dataclass
class StillUses:
    """一期稿件里图片微动 SP 的用法。`uses`：{(池, SPxx): [段号, …]}；`reuse`：{(池, SPxx): {段号}}，
    人定超限复用的声明；`bad_reuse`：本期声明写错的（格式不对，或本段没锚这张图片微动 SP）；
    `unavailable`：读不到的池（报错原文）。"""
    uses: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    reuse: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    bad_reuse: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    #: D137：视频 SP 的锚点 {(池, SPxx): [(段号, 起点秒), …]}、带时间码的声明 {(池, SPxx): {(段号, 起点秒)}}、
    #: 写错的视频 SP 声明（没带时间码，或本段没有从这个时间起的锚点）
    video: dict[tuple[str, str], list[tuple[str, float]]] = field(default_factory=dict)
    video_reuse: dict[tuple[str, str], set[tuple[str, float]]] = field(default_factory=dict)
    bad_video_reuse: list[str] = field(default_factory=list)


def still_sp_uses(episode: Path) -> StillUses:
    """本期 `02-script.md` 引用图片微动 SP 的锚点条目与 `复用:` 声明。

    一个锚点条目排出一个片段，所以条目数就是片段数；同一段写两遍记两次。
    格式不对、池名不在番表、未入库的条目不在这里计，它们由「锚点格式」「锚点指向素材」两项报。
    池的归属与「锚点指向素材」一致：SP 锚点没写池名归企划池；`复用:` 条目同一口径。
    """
    out = StillUses()
    f = episode / "02-script.md"
    if not f.exists():
        return out
    animes = bgm.animes_of(episode)
    if not animes:
        return out
    anime_main, main_plan = animes[0], bgm.anime_of(episode)
    patch_name = _patch_pool_name(episode)
    allowed = set(animes) | {x for x in (main_plan, patch_name) if x}
    uses = out.uses
    unavailable = out.unavailable
    cache: dict[str, dict | None] = {}
    text = f.read_text(encoding="utf-8")
    for label, raw in parse_anchors(text):
        if not raw or raw.startswith("无"):
            continue
        for item in _anchor_items(raw):
            m = ANCHOR_TC.fullmatch(item)
            if not m or m.group("sp") is None:
                continue
            pool = (m.group("anime") or "").strip() or main_plan or anime_main
            if pool not in allowed:
                continue
            if pool not in cache:
                try:
                    if patch_name and pool == patch_name:
                        cache[pool] = _patch_sources(episode)
                    else:
                        from . import ingest  # 延迟 import：与「锚点指向素材」同规矩
                        cache[pool] = ingest.load_sources(pool)
                except SystemExit as e:
                    cache[pool] = None
                    unavailable.append(f"{episode.name}：{e}")
            src = (cache[pool] or {}).get(f"SP{int(m.group('sp')):02d}")
            if src and _is_still(src):
                uses.setdefault((pool, f"SP{int(m.group('sp')):02d}"), []).append(label)
            elif src:
                start = int(m.group("m0")) * 60 + float(m.group("s0"))
                out.video.setdefault((pool, f"SP{int(m.group('sp')):02d}"), []).append((label, start))
    for label, items in parse_reuse(text).items():
        for item in items:
            m = _REUSE_ITEM.fullmatch(item)
            if not m:
                out.bad_reuse.append(f"段{label} 复用条目「{item}」格式不对"
                                     "（写 `<池> SPxx`，视频 SP 带起点 `<池> SPxx mm:ss`）")
                continue
            key = ((m.group("anime") or "").strip() or main_plan or anime_main, f"SP{int(m.group('sp')):02d}")
            in_video = [s for l, s in out.video.get(key, []) if l == label]
            if m.group("m") is not None:
                start = int(m.group("m")) * 60 + float(m.group("s"))
                hit = [s for s in in_video if abs(s - start) < 0.01]
                if hit:
                    out.video_reuse.setdefault(key, set()).add((label, hit[0]))
                    continue
                if not in_video and label in uses.get(key, []):
                    out.reuse.setdefault(key, set()).add(label)   # 图片微动 SP 带了时间码：时间码不参与
                    continue
                out.bad_video_reuse.append(f"段{label} 复用 {key[0]} {key[1]} {_mmss(start)}，"
                                           "但本段锚点里没有从这个时间起的片段")
                continue
            if label in uses.get(key, []):
                out.reuse.setdefault(key, set()).add(label)
                continue
            if in_video:
                out.bad_video_reuse.append(f"段{label} 复用 {key[0]} {key[1]} 没写时间码：视频 SP 的复用要指明"
                                           f"哪一处，写 `{key[0]} {key[1]} {_mmss(in_video[0])}（理由）`")
                continue
            out.bad_reuse.append(f"段{label} 复用 {key[0]} {key[1]}，但本段锚点里没有这张图片微动 SP")
    return out


def _mmss(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}".rstrip("0").rstrip(".") if s % 1 else f"{int(m)}:{int(s):02d}"


def _check_sp_caps(path: Path, add) -> None:
    """企划池 SP 的两项次数上限（图片微动 D126、视频 SP 同一处 D137）：本期与企划其他卷的用法只读一遍。"""
    from . import project
    ep = path.parent
    own = still_sp_uses(ep)
    if not (own.uses or own.video or own.unavailable or own.bad_reuse or own.bad_video_reuse):
        return
    proj = project.project_of(ep)
    others = [(m.name, still_sp_uses(m)) for m in (project.project_members(proj) if proj else [])
              if m.name != ep.name]
    if own.uses or own.unavailable or own.bad_reuse:
        _check_still_sp_cap(ep, own, others, add)
    if own.video or own.unavailable or own.bad_video_reuse:
        _check_video_sp_window(ep, own, others, add)


def _check_still_sp_cap(ep: Path, own: StillUses, others: list[tuple[str, StillUses]], add) -> None:
    """图片微动 SP 次数上限：本期用到的每张，连同企划内其他卷的用法合计不超过 STILL_SP_MAX。

    只报本期用到的那几张：别的卷自己超了，由那一卷的机检报。本期没引用图片微动 SP 就不出这一项。
    超出的次数落在带 `复用:` 声明的段（任何一卷）里时放行，声明逐处列在结果里（D126）。
    """
    total = {k: [(ep.name, l) for l in v] for k, v in own.uses.items()}
    declared = {k: {(ep.name, l) for l in v} for k, v in own.reuse.items()}
    unavailable = list(own.unavailable)
    for vol, other in others:
        unavailable += other.unavailable
        for k, v in other.uses.items():
            if k in total:
                total[k].extend((vol, l) for l in v)
        for k, v in other.reuse.items():
            if k in total:
                declared.setdefault(k, set()).update((vol, l) for l in v)
    name = f"图片微动 SP 全企划至多 {STILL_SP_MAX} 次"
    if unavailable:
        add(name, False, f"跳过不可判定：{'；'.join(unavailable[:2])}（S9 跳过不是通过）")
        return
    if own.bad_reuse:
        add(name, False, "；".join(own.bad_reuse[:4])
            + "　`复用:` 只写本段锚点里用到、由人决定超限复用的图片微动 SP")
        return

    def where_text(key: tuple[str, str], where: list[tuple[str, str]]) -> str:
        by_vol: dict[str, list[str]] = {}
        for vol, label in where:
            mark = "〔复用〕" if (vol, label) in declared.get(key, set()) else ""
            by_vol.setdefault(vol, []).append(f"段{label}{mark}")
        return (f"{key[0]} {key[1]} 共 {len(where)} 次（"
                + "；".join(f"{v} {'、'.join(ls)}" for v, ls in by_vol.items()) + "）")

    over = {k: v for k, v in total.items() if len(v) > STILL_SP_MAX}
    failing = {k: v for k, v in over.items()
               if sum(w not in declared.get(k, set()) for w in v) > STILL_SP_MAX}
    if not failing:
        if over:
            add(name, True, f"本期 {len(own.uses)} 张图片微动 SP；超出 {STILL_SP_MAX} 次的都有人定复用声明："
                + "；".join(where_text(k, v) for k, v in sorted(over.items())))
        else:
            add(name, True, f"本期 {len(own.uses)} 张图片微动 SP，合计都不超过 {STILL_SP_MAX} 次")
        return
    msgs = [where_text(k, v) for k, v in sorted(failing.items())]
    add(name, False, "；".join(msgs[:4])
        + f"　同一张图片微动素材在整个企划里最多用 {STILL_SP_MAX} 次（2026-10-17 人定），"
          "多出来的段换别的素材，或由人决定后在该段画面块写 `复用: <池> SPxx（理由）`")


def _check_video_sp_window(ep: Path, own: StillUses, others: list[tuple[str, StillUses]], add) -> None:
    """视频 SP 同一处画面上限（D137）：以本期每个视频 SP 锚点的起点为中心、前后 VIDEO_SP_WINDOW_S 秒内，
    同一 SP 的锚点（含它自己、含企划其他卷）合计不超过 VIDEO_SP_MAX。

    只报以本期锚点为中心的窗口：别的卷内部超了，由那一卷的机检报。不带声明的锚点数超过上限才 FAIL，
    带时间码 `复用:` 声明的锚点（任何一卷）放行，逐处列在结果里给 02.5 看。
    """
    name = f"视频 SP 同一处（前后 {VIDEO_SP_WINDOW_S:g} s）全企划至多 {VIDEO_SP_MAX} 次"
    unavailable = list(own.unavailable) + [u for _, o in others for u in o.unavailable]
    if unavailable:
        add(name, False, f"跳过不可判定：{'；'.join(unavailable[:2])}（S9 跳过不是通过）")
        return
    if own.bad_video_reuse:
        add(name, False, "；".join(own.bad_video_reuse[:4])
            + "　视频 SP 的 `复用:` 写本段锚点里那一处的起点，如 `<池> SPxx 0:20（理由）`")
        return
    # 判定按窗口，报告按簇：同一 SP 相互重叠的超限窗口并成一簇报一次（0/5/15/20 s 是两个窗口、一处画面）
    keep: list[tuple[tuple[str, str], list[tuple[str, str, float, bool]]]] = []
    failing: list[tuple[tuple[str, str], list[tuple[str, str, float, bool]]]] = []
    for key, mine in sorted(own.video.items()):
        entries = [(ep.name, l, s) for l, s in mine]
        entries += [(vol, l, s) for vol, o in others for l, s in o.video.get(key, [])]
        declared = {(ep.name, l, s) for l, s in own.video_reuse.get(key, set())}
        declared |= {(vol, l, s) for vol, o in others for l, s in o.video_reuse.get(key, set())}
        clusters: list[tuple[set[int], bool]] = []
        for _, _, center in entries[:len(mine)]:
            idx = {i for i, e in enumerate(entries) if abs(e[2] - center) <= VIDEO_SP_WINDOW_S + 1e-9}
            if len(idx) <= VIDEO_SP_MAX:
                continue
            bad = sum(entries[i] not in declared for i in idx) > VIDEO_SP_MAX
            for c in [c for c in clusters if c[0] & idx]:
                clusters.remove(c)
                idx, bad = idx | c[0], bad or c[1]
            clusters.append((idx, bad))
        for idx, bad in clusters:
            win = [(*entries[i], entries[i] in declared) for i in sorted(idx)]
            (failing if bad else keep).append((key, win))

    def where_text(key: tuple[str, str], win: list[tuple[str, str, float, bool]]) -> str:
        by_vol: dict[str, list[str]] = {}
        for vol, label, start, dec in sorted(win, key=lambda e: e[2]):
            by_vol.setdefault(vol, []).append(f"段{label} {_mmss(start)}{'〔复用〕' if dec else ''}")
        return (f"{key[0]} {key[1]} 共 {len(win)} 处（"
                + "；".join(f"{v} {'、'.join(ls)}" for v, ls in by_vol.items()) + "）")

    n_own = sum(len(v) for v in own.video.values())
    if not failing:
        if keep:
            add(name, True, f"本期 {n_own} 处视频 SP 锚点；超出 {VIDEO_SP_MAX} 处的都有人定复用声明："
                + "；".join(where_text(k, v) for k, v in keep))
        else:
            add(name, True, f"本期 {n_own} 处视频 SP 锚点，同一处都不超过 {VIDEO_SP_MAX} 次")
        return
    add(name, False, "；".join(where_text(k, v) for k, v in failing[:4])
        + f"　同一支视频 SP 的同一处画面（起点前后 {VIDEO_SP_WINDOW_S:g} s）在整个企划里最多用 {VIDEO_SP_MAX} 次"
          "（2026-10-24 人定），多出来的段换一处画面，或由人决定后在该段画面块写 `复用: <池> SPxx mm:ss（理由）`")


#: 补丁池名的后缀（`ingest_patch.sanitize_pool_name`：`<净化期名>-patch`，撞名再加 `-N`）。
#: 前缀长这样、却不是本期补丁池时，报错指路。
PATCH_NAME = re.compile(r"-patch(?:-\d+)?$")


def _has_visual_source(block: str) -> bool:
    """段落块有没有画面来源：`查询`（检索通道）或可解析的时间码 `锚点`（ADR-0008）。

    多锚点段（2026-09-07）：任一锚点条目可解析就算有画面来源；
    个别条目坏掉由「锚点格式」专项报，不在这里双报。
    """
    if re.search(r"^\s*查询[：:]\s*(.+)$", block, re.M):
        return True
    anc = _ANCHOR_FIELD.search(block)
    return bool(anc and any(ANCHOR_TC.fullmatch(x)
                            for x in _anchor_items(anc.group(1).strip())))


def parse_emotion_fields(text: str) -> list[tuple[str, str | None, str | None]]:
    """每个段落块的 情绪/语速 字段 → [(段落号, 情绪, 语速)]（未写为 None）。纯函数。

    与 parse_anchors / tts.parse_script 同口径：`## 段落 N` 到下一个段落标题之间。
    **两个字段都是可选的**（不写 = 平静叙述/中，行为与 v1 一致，旧稿零迁移）。
    """
    out: list[tuple[str, str | None, str | None]] = []
    for m in re.finditer(r"^##\s*段落\s*(\S+)\s*\n(.*?)(?=^##\s*段落|\Z)", text, re.M | re.S):
        label, block = m.group(1), m.group(2)
        emo = re.search(r"^情绪[：:]\s*(.+)$", block, re.M)
        spd = re.search(r"^语速[：:]\s*(.+)$", block, re.M)
        out.append((
            label,
            emo.group(1).strip() if emo else None,
            spd.group(1).strip() if spd else None,
        ))
    return out


def validate_emotion_speed(
    fields: list[tuple[str, str | None, str | None]],
    allowed_emotions: set[str],
    allowed_speed: set[str],
) -> list[tuple[str, str, str]]:
    """返回受控词表外的 [(段落号, 字段名, 值)]（空列表 = 全部合法）。纯函数。

    **为什么必须是受控词表**（架构设计 3.3）：自由文本情绪 = 每期发挥不稳定，
    且无法进 manifest 审计（情感参数要与合成结果一起进 manifest，
    eval 才能按情绪分组统计 f0/停顿，回答「情感声明是否真的改变了声学输出」）。
    只校验**写了**的段落——不写就是默认值，不算违规。
    """
    bad: list[tuple[str, str, str]] = []
    for label, emo, spd in fields:
        if emo is not None and emo not in allowed_emotions:
            bad.append((label, "情绪", emo))
        if spd is not None and spd not in allowed_speed:
            bad.append((label, "语速", spd))
    return bad


def parse_anchors(text: str) -> list[tuple[str, str | None]]:
    """每个段落块的 `锚点:` 字段 → [(段落号, 原文或 None)]。None = 没写这个字段。

    切块口径与 parse_episodes 一致。锚点是排片的确定性输入（ADR-0008），
    缺失本身就是要拦的事，所以这里连「没写」也一并报出来。
    多锚点段（2026-09-07）：原文含续行与逗号分隔，这里原样带回，拆分在校验侧。
    """
    out = []
    for m in re.finditer(r"^##\s*段落\s*(\S+)\s*\n(.*?)(?=^##\s*段落|\Z)",
                         text, re.M | re.S):
        anc = _ANCHOR_FIELD.search(m.group(2))
        out.append((m.group(1), anc.group(1).strip() if anc else None))
    return out


def _music_seconds(script_path: Path) -> float:
    """计算试听型稿件中的音乐前景试听与自然收尾占用的时间（秒）。普通期返回 0.0。

    解析失败不许静默归 0（红队 R6）：归 0 会把乐评稿按纯口播带卡字数，
    误杀合规稿件且报告里看不出原因。SystemExit（稿件格式错）本来就会穿透，
    这里兜的是 music 模块自身的意外异常——同样是错就要报出来。"""
    try:
        from . import music
        blocks = music.parse_script_music(script_path)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(
            f"FAIL 音乐段解析异常（{type(e).__name__}: {e}）——不能静默按 0 秒算，\n"
            f"     那会把试听型稿件按纯口播标准卡字数。先修好解析再跑机检") from e
    if not blocks:
        return 0.0
    # 背景铺底块是 BGM 不是前景试听，不计入试听时长（t1 为 None 本就被跳过，
    # 声明了上限的铺底块也要排除——铺底不占口播外的试听预算）
    dur = sum((b.t1 - b.t0) for b in blocks
              if b.t1 is not None and not b.bgm_only)
    # 自然收尾按**稿子里声明的那一首**算（2026-09-13 修）。旧实现在正文里扫
    # 「第一个出现过的曲名」、而且每个池各加一次：三期实测把 EGOIST 的《最後の花弁》
    # 与 罪恶王冠 的《Departures》都当成了收尾曲，比真正的《Last Song》多算 124s，
    # 白压窄了 617 字的口播带。
    # 末尾 -90s 是本估算自己的安全余量（真实收尾长度以 manifest 为准），一直如此。
    dur += max(0.0, music.natural_outro_tail(script_path.parent, blocks) - 90.0)
    return dur


def run(path: Path) -> list[Check]:
    text = path.read_text(encoding="utf-8")
    vo, queries, alts = parse(text)

    music_sec = _music_seconds(path)
    music_mins = music_sec / 60.0

    override = episode_duration_override(path)
    if override:
        dur_lo, dur_hi = override
        vo_dur_lo = max(1.0, dur_lo - music_mins)
        vo_dur_hi = max(1.0, dur_hi - music_mins)
        min_chars, max_chars = round(vo_dur_lo * CPM), round(vo_dur_hi * CPM)
    else:
        dur_lo, dur_hi = MIN_CHARS / CPM, MAX_CHARS / CPM
        vo_dur_lo, vo_dur_hi = dur_lo, dur_hi
        min_chars, max_chars = MIN_CHARS, MAX_CHARS
    if shrink_no_padding(path):
        min_chars = round(min_chars * SHRINK_FACTOR)   # 只降下限，上界不动

    # 末段若是片尾套话，从正文统计里摘出去（仍然会被配音，只是不参与内容判定）
    outro = vo[-1] if vo and len(vo[-1]) <= 25 and OUTRO.search(vo[-1]) else None
    if outro:
        vo = vo[:-1]

    body = " ".join(vo)
    chars = sum(len(v) for v in vo)
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, ok, detail))

    if not vo:
        # 空稿/没解析出任何「配音：」段落：句长统计没有分母（mean([]) 会裸抛
        # StatisticsError，2026-08-16 审计 2-15）。段落数这条把原因说清，
        # 其余检查全部依赖段落、无从谈起——非零退出，干净失败（E10）。
        add("段落数 8–20", False,
            "0 段——稿件里没有任何「配音：」段落。检查是不是选错文件、"
            "写空了，或段落标题不是 `## 段落 N` 格式")
        return checks



    tail = "（另有片尾 1 段，不计入）" if outro else ""
    # 视听微单元模式（2026-09-07 变革）：允许 8-12 秒一个微单元分镜，避免长段落单画面拖沓。
    # 上限 = 时长上界 ÷ 8 s（skill §8.1 微单元下限，两边同一个数），不低于 script.max_segs。
    # D84：原先只在有时长目标时才这么算，默认带下固定 20——1300 字 ≈ 303 s 按 10 s 一段要 30 段，
    # 照 skill 写反而过不了机检，写稿侧只好憋大长段。
    max_segs = max(paths.conf("script.max_segs", 20), int(dur_hi * 60 / 8.0))
    add(f"段落数 8–{max_segs}", 8 <= len(vo) <= max_segs, f"{len(vo)} 段{tail}")
    add(f"字数 {min_chars}–{max_chars}", min_chars <= chars <= max_chars, f"{chars} 字{tail}"
        + ("　（01-topic.md 时长目标覆盖）" if override else ""))
    dur_detail = f"{chars / CPM:.1f} 分钟"
    if music_sec > 0:
        dur_detail += f"（口播，含试听/收尾约 {(chars / CPM) + music_mins:.1f} 分钟）"
    add(f"时长 {dur_lo:g}–{dur_hi:g} 分钟", vo_dur_lo <= chars / CPM <= vo_dur_hi, dur_detail)
    # 每段都要有画面来源：`查询`（检索通道）或时间码 `锚点`（ADR-0008 直通通道）。
    # 锚点段不检索，不强求查询；其余段照旧每段一条。
    block_list = [(m.group(1), m.group(2)) for m in re.finditer(
        r"^##\s*段落\s*(\S+)\s*\n(.*?)(?=^##\s*段落|\Z)", text, re.M | re.S)]
    no_visual = [label for label, b in block_list if not _has_visual_source(b)]
    add("每段都有查询或锚点", not no_visual,
        f"{len(block_list) - len(no_visual)}/{len(block_list)} 段有画面来源"
        + ("，缺：" + "、".join(f"段{l}" for l in no_visual[:5]) if no_visual else ""))

    marks = pending_marks(text)
    add("无【待核】【待你定】标记", not marks,
        pending_marks_summary(marks) + "　核掉后改正文或删掉这句，交人定的等人回话再改；标记留着不能批准 02.5"
        if marks else "没有待办标记")

    # 情绪/语速字段机检（架构设计 3.3，2026-09-11 加）：表外值当场报错。
    # 受控词表的真源在 config/voice.json，不在这里硬编码——词表会随引擎实测调整，
    # 抄一份到代码里必然分叉。
    voice_cfg = json.loads((paths.CONFIG / "voice.json").read_text(encoding="utf-8"))
    allowed_emo = {k for k in (voice_cfg.get("emotions") or {}) if not k.startswith("_")}
    allowed_spd = {k for k in (voice_cfg.get("speed") or {}) if not k.startswith("_")}
    emo_fields = parse_emotion_fields(text)
    bad_es = validate_emotion_speed(emo_fields, allowed_emo, allowed_spd)
    declared = sum(1 for _, e, s in emo_fields if e or s)
    add("情绪/语速在受控词表内", not bad_es,
        f"{declared}/{len(emo_fields)} 段声明了情绪/语速"
        + ("；表外：" + "、".join(f"段{l}的{f}={v!r}" for l, f, v in bad_es[:3]) if bad_es else ""))

    # 集号（ADR-0004，2026-08-09 加）：格式校验不依赖外部文件、永远可跑；
    # 存在性校验要番名 + 素材库，读不到就显式 FAIL「跳过不可判定」——
    # S9「跳过不是通过」。**无集字段不判失败**：人物志这类选题有已知落空的
    # 段落，强制每段写 = 逼人编集号。覆盖率只作信息报在 detail 里。
    eps = parse_episodes(text)
    covered = f"{len(eps)}/{len(vo)} 段写了集号，没写的段全空间检索"
    bad_ep = [(p, r) for p, r in eps if not _norm_ep(r)]
    if bad_ep:
        add("集号格式", False,
            "、".join(f"段{p}「{r}」" for p, r in bad_ep[:3])
            + "　写规范形 S01E07（季两位、集两位都补齐），别写中文「第一季第七集」"
              "——集号是检索约束，写错 = 排片检索范围错了。"
            + covered)
    else:
        add("集号格式", True, covered)

    # 跨番（2026-09-06）：段 → 素材番的分派规则是「锚点写了番名跟锚点，没写归主番」。
    # 集号本身不带番名，它的检索范围由同段锚点决定（锚点自带集号时 `集` 字段可省）。
    # SP 特典（ADR-0010）：锚点/集号指向企划名自己的素材池，不在素材番池里。
    animes = bgm.animes_of(path.parent)
    anime_main = animes[0] if animes else None
    main_plan = bgm.anime_of(path.parent)      # 企划名（番: 行第一个）；单番期 = anime_main
    # 期内补丁池（D84 PR-B）：只收 SP 键，锚点写 `<池名> SPxx mm:ss`，与 clips 的 extra_pools 同口径
    patch_name = _patch_pool_name(path.parent)
    # 多锚点（2026-09-07）：段的锚点字段可写逗号/续行列表，逐条校验
    anc_match = {label: [(x, ANCHOR_TC.fullmatch(x)) for x in _anchor_items(raw)]
                 for label, raw in parse_anchors(text)
                 if raw and not raw.startswith("无")}

    def _seg_anime(label: str) -> str | None:
        for _x, m in anc_match.get(label, []):
            if m:
                return (m.group("anime").strip() if m.group("anime") else None) or anime_main
        return anime_main

    if eps:
        if not animes:
            add("集号在素材库", False,
                "跳过不可判定：01-topic.md 没写「番:」字段，读不到番名（S9 跳过不是通过）")
        else:
            from . import ingest  # 延迟 import：只在写了集号时才需要素材库
            cache: dict[str, dict] = {}

            def _sources(a: str) -> dict:
                if a not in cache:
                    cache[a] = (_patch_sources(path.parent) if patch_name and a == patch_name
                                else ingest.load_sources(a))
                return cache[a]

            unavailable = []
            missing = []
            for p, r in eps:
                norm = _norm_ep(r)
                if not norm:
                    continue
                a = _seg_anime(p)
                # SP 特典登记在企划池（番: 行第一个），不在素材番池
                if norm.startswith("SP") and a == anime_main and main_plan:
                    a = main_plan
                try:
                    sources = _sources(a)
                except SystemExit as e:
                    unavailable.append(str(e))
                    continue
                if norm not in sources:
                    missing.append((p, r, a, sources))
            if unavailable:
                add("集号在素材库", False,
                    f"跳过不可判定：{'；'.join(unavailable[:2])}（S9 跳过不是通过）")
            elif missing:
                msgs = []
                for p, r, a, sources in missing:
                    norm = _norm_ep(r)
                    if norm.startswith("SP"):
                        same = sorted((k for k in sources if k.startswith("SP")), key=key_sort)
                        msgs.append(
                            f"段{p}「{r}」：《{a}》池里没有 {norm}"
                            + (f"（现有特典：{'、'.join(same[:4])}）" if same
                               else "（该池一条 SP 特典都没有）")
                            + "——SP 素材先登记再引用")
                        continue
                    season = norm[1:3]
                    same = sorted(k for k in sources
                                  if k.startswith(f"S{season}"))
                    if same:
                        msgs.append(
                            f"段{p}「{r}」：《{a}》S{season} 季有 "
                            f"{len(same)} 集入库（如 {same[0]}），唯独没有 {norm}"
                            f"——多半是集号写错；确定没写错就是这集没入库，"
                            f"补 `python -m pipeline.ingest phase0`")
                    else:
                        msgs.append(
                            f"段{p}「{r}」：《{a}》S{season} 季在素材库里一集都没有"
                            f"——不是集号写错，是整季没入库，"
                            f"先跑 `python -m pipeline.ingest phase0`")
                add("集号在素材库", False, "；".join(msgs[:3]))
            else:
                add("集号在素材库", True, f"{len(eps)} 段集号都在素材库里")

    # 锚点（ADR-0008，2026-08-27 加）：排片主通道的剧情时间码，三段递进——
    # 字段在不在 → 格式对不对 → 指向的集与时间码真不真（素材库）。
    anc_fields = parse_anchors(text)
    missing_anc = [label for label, raw in anc_fields if raw is None]
    add("每段都有锚点字段", not missing_anc,
        ("缺：" + "、".join(f"段{l}" for l in missing_anc[:6])
         + "　没有锚点的段写 `锚点: 无（理由）`，不许留空——锚点是排片的确定性输入"
         if missing_anc else f"{len(anc_fields)}/{len(anc_fields)} 段已声明"))

    ep_by_label = dict(parse_episodes(text))
    bad_fmt, no_reason, anchors_tc = [], [], []
    for label, raw in anc_fields:
        if raw is None:
            continue
        if raw.startswith("无"):
            if not raw[1:].strip(" 　（）()"):
                no_reason.append(label)     # ADR-0008：「无」必须附理由，不许留空蒙混
            continue
        for item, m in anc_match.get(label, []):   # 上面已按同一条正则解析过，口径一致
            if not m:
                bad_fmt.append((label, item))
                continue
            # 番名前缀（2026-09-06）：写了就必须在 01-topic.md 的番表里——
            # 番名写错 = 画面指到另一部番的同季同集，全程不报错。
            # SP 锚点（2026-09-07）额外允许指向企划名：特典素材登记在企划池
            prefix = m.group("anime")
            is_sp = m.group("sp") is not None
            if prefix and animes and prefix.strip() not in animes:
                pre = prefix.strip()
                if patch_name and pre == patch_name:
                    if not is_sp:
                        bad_fmt.append((label, f"{item}（补丁池 {pre} 只有 SPxx 键，没有季集）"))
                        continue
                elif not (is_sp and main_plan and pre == main_plan):
                    hint = ""
                    if PATCH_NAME.search(pre):
                        hint = (f"；本期补丁池是 {patch_name}" if patch_name else
                                "；本期还没有补丁池：先 `acquire register <图> --to-patch`，"
                                "再 `ingest_patch --anchor-only`")
                    bad_fmt.append((label, f"{item}（《{pre}》不在 01-topic.md 番表："
                                           f"{'、'.join(animes)}{hint}）"))
                    continue
            t0 = int(m.group("m0")) * 60 + float(m.group("s0"))
            t1 = (int(m.group("m1")) * 60 + float(m.group("s1"))) if m.group("m1") else None
            if t1 is not None and t1 <= t0:
                bad_fmt.append((label, f"{item}（终点不在起点之后）"))
                continue
            akey = (f"SP{int(m.group('sp')):02d}" if is_sp
                    else f"S{int(m.group('season')):02d}E{int(m.group('episode')):02d}")
            ep_raw = ep_by_label.get(label)
            if ep_raw and _norm_ep(ep_raw) not in (None, akey):
                bad_fmt.append((label, f"{item}（与集号 {ep_raw} 不是同一集）"))
                continue
            anchors_tc.append((label, prefix.strip() if prefix else None, akey, t0, t1, is_sp))
    problems = [f"段{l}「{r}」" for l, r in bad_fmt] + \
               [f"段{l} 写「无」没说理由" for l in no_reason]
    add("锚点格式", not problems,
        "；".join(problems[:4]) if problems
        else f"{len(anchors_tc)} 段时间码锚点，格式全对")

    if anchors_tc:
        if not animes:
            add("锚点指向素材", False,
                "跳过不可判定：01-topic.md 没写「番:」字段，读不到番名（S9 跳过不是通过）")
        else:
            from . import ingest  # 延迟 import：与上方「集号在素材库」同规矩
            bad_src, unavailable = [], []
            cache: dict[str, dict] = {}
            for l, a, k, t0, t1, sp in anchors_tc:
                # SP 锚点未写番名时归企划池（与 clips._parse_anchor 的默认一致）；
                # 单番期企划名即主番，行为不变
                pool = a or ((main_plan or anime_main) if sp else anime_main)
                try:
                    if pool not in cache:
                        cache[pool] = (_patch_sources(path.parent) if patch_name and pool == patch_name
                                       else ingest.load_sources(pool))
                    sources = cache[pool]
                except SystemExit as e:
                    unavailable.append(str(e))
                    continue
                if k not in sources:
                    bad_src.append(f"段{l}「{pool} {k}」集未入库")
                elif t0 >= sources[k]["duration"]:
                    bad_src.append(f"段{l}「{pool} {k} {int(t0 // 60)}:{int(t0 % 60):02d}」"
                                   f"超出片长（{sources[k]['duration']:.0f}s）")
            if unavailable:
                add("锚点指向素材", False,
                    f"跳过不可判定：{'；'.join(unavailable[:2])}（S9 跳过不是通过）")
            else:
                add("锚点指向素材", not bad_src,
                    "；".join(bad_src[:4]) if bad_src
                    else f"{len(anchors_tc)} 段锚点都落在已入库集的片长内")

    _check_sp_caps(path, add)

    sents = [s.strip() for v in vo for s in re.split(r"[。？！]", v) if s.strip()]

    over = [s for s in sents if len(s) > MAX_SENT]
    add(f"无超 {MAX_SENT} 字长句", not over,
        "、".join(f"{len(s)}字「{s[:18]}…」" for s in over[:3]) or "无")

    # 气口段：逗号之间的那一段，才是念稿时真正要一口气念完的单位。
    # 卡的是**单段上限**（念不念得完），不是均值（均值不区分好坏，见上方注释）。
    breaths = [b for b in BREATH_SPLIT.split(body) if b]
    winded = [b for b in breaths if len(b) > MAX_BREATH]
    add(f"气口段 ≤{MAX_BREATH} 字", not winded,
        "、".join(f"{len(b)}字「{b[:16]}…」" for b in winded[:3]) or
        f"最长 {max((len(b) for b in breaths), default=0)} 字")

    # 句长起伏。整齐是「机器味」最直接的量化形态，见上方对照表。
    lens = [len(s) for s in sents]
    burst = (statistics.pstdev(lens) / statistics.mean(lens)) if len(lens) > 1 else 0.0
    add(f"句长起伏 CV ≥{MIN_BURST:g}", burst >= MIN_BURST,
        f"{burst:.2f}（均长 {statistics.mean(lens):.1f} 字，最长 {max(lens, default=0)}）"
        + ("　长短太齐，像一台机器抛光过" if burst < MIN_BURST else ""))

    # 长句下限。CV 说「不够起伏」，这条说清楚该往哪边补——三期实测一句长句都没有。
    longs = [s for s in sents if len(s) > LONG_SENT_LEN]
    add(f"超 {LONG_SENT_LEN} 字长句 ≥{MIN_LONG_SENT}", len(longs) >= MIN_LONG_SENT,
        f"{len(longs)} 句" + ("　全篇没有一句敢长，靠逗号撑开一两句" if len(longs) < MIN_LONG_SENT else ""))

    staccato = [s for s in sents if len(s) <= STACCATO_LEN]
    add(f"≤{STACCATO_LEN}字短句 ≤{MAX_STACCATO} 处", len(staccato) <= MAX_STACCATO,
        f"{len(staccato)} 处：" + "、".join(f"「{s}」" for s in staccato[:5])
        if staccato else "无")

    paper = PAPER_WORDS.findall(body)
    add("无论文连接词", not paper, "、".join(sorted(set(paper))) or "无")

    parens = PARENS.findall(body)
    add("无括号", not parens, f"{len(parens)} 处" if parens else "无")

    # 引号：**只提示，不拦**（2026-10-24 人定，D134）。
    # 原先判 FAIL，起因是 2026-07-30 IndexTTS 把弯引号当字符各吐一个音节——
    # 「谁更该“赢”」念成「谁更该非赢匪」。那之后两件事让这个起因不成立：
    # 合成前 `tts._MUTE` 一律剥掉引号，换哪个引擎都到不了模型；配音已换成 Qwen3-TTS。
    # 字幕用原文，引号照常上屏，写引用、书名、宣传语时人确实需要它。
    # 剩下的只是文风信号：那句话在靠视觉标记表达引用，口播里要靠语气或引导词让人听出来，
    # 所以照旧报出位置，交人判断。
    quotes = QUOTES.findall(body)
    add("无引号", True,
        f"⚠ {len(quotes)} 处：{'、'.join(sorted(set(quotes)))}　合成前会剥掉、字幕照常显示；"
        "引用要靠语气或引导词让人听出来" if quotes else "无")

    digit_body, exempt_digits = _strip_readings_covered(body)
    digits = DIGITS.findall(digit_body)
    detail = (f"{len(digits)} 处：{''.join(digits[:8])}　改成中文数字，TTS 的读法不可控"
              if digits else "无")
    if exempt_digits:
        detail += f"（{'、'.join(exempt_digits)} 已由读音表接管，豁免）"
    add("无阿拉伯数字", not digits, detail)

    vague = VAGUE.findall(body)
    add("无模糊指代", not vague, "、".join(sorted(set(vague))) or "无")

    stock = [s.strip("。？！ ") for s in STOCK.findall(body)]
    add("无跨期套话", not stock, "、".join(f"「{s}」" for s in sorted(set(stock))) or "无")

    enum = ENUM.findall(body)
    add("论据不报菜名", len(set(enum)) < 2,
        "、".join(f"「{e}」" for e in sorted(set(enum))) + "　并列平铺，挑一个挖到底"
        if len(set(enum)) >= 2 else "无")

    # 剧情锚点：带 `集:` 字段或时间码 `锚点:` 的段落数（集号格式由上方
    # 「集号格式/集号在素材库」把关，锚点格式由「锚点格式」把关）。ADR-0008 后
    # 锚点自带集号、`集` 字段可省——只数集号会逼写稿人加冗余字段，
    # 检查项长期误报 = 没有检查（S3）。
    # **不再从正文找「第X集」字样**——正文要不要点明集数、剧情对不对、
    # 文字像不像人，是 02.5 人审的活，机检逼正文写集数 = 让机器决定内容（2026-08-12 改）。
    anchor_segs = ({l for l, r in parse_episodes(text) if _norm_ep(r)}
                   | {l for l, *_ in anchors_tc})
    add(f"剧情锚点 ≥{MIN_ANCHOR}", len(anchor_segs) >= MIN_ANCHOR,
        f"{len(anchor_segs)}/{len(vo)} 段带集号或时间码锚点"
        if anchor_segs else "0 段，全篇没有画面集号/锚点")

    # 纪录片题材专门门禁（2026-09-11 增补）：
    # 1. 强制存在 01-hero-shots.md 物证表（写稿前必须有物证卡片，严禁无物证空想写稿）
    # 2. 实证锚点覆盖率：全篇必须有 ≥3 处 SP 物理物证锚点；实证段必须死死咬住物理物证，严禁架空硬凑
    genre, mode = episode_genre(path)
    if genre == "纪录片":
        hero_shots_path = path.parent / "01-hero-shots.md"
        add("纪录片物证表 01-hero-shots.md 在位", hero_shots_path.exists(),
            f"{hero_shots_path.name} 存在" if hero_shots_path.exists()
            else "缺失！纪录片题材必须先有物证卡片表（01-hero-shots.md），严禁无物证空想写稿")

        sp_anchors = [
            l for l, prefix, akey, t0, t1, is_sp in anchors_tc
            if is_sp or (akey and akey.startswith("SP"))
        ]
        add("纪录片实证物证锚点 ≥3 处", len(sp_anchors) >= 3,
            f"全篇共 {len(sp_anchors)} 处引用了 SP 物证锚点"
            if len(sp_anchors) >= 3
            else f"仅 {len(sp_anchors)} 处引用了 SP 物证锚点（需≥3），实证段必须死死咬住物理物证，缺素材须触发 acquire 动态补料")

    visual = VISUAL.findall(" ".join(queries))
    add("查询无构图词", not visual, "、".join(sorted(set(visual))) or "无")

    # 两段用同一条查询是**静默失败**：排片是全局贪心分派、同一处画面整期只用一次，
    # 所以第二段拿不到那句台词的画面，会退到明显更差的命中，而且不报 no_match。
    # 2026-07-30 实测：改稿合并段落时把上一段的查询整条带了过来，检查全绿。
    dup = sorted({q for q in queries if queries.count(q) > 1})
    add("查询不重复", not dup,
        "、".join(f"「{q[:14]}…」×{queries.count(q)}" for q in dup[:3]) or "无")

    add("备选覆盖（允许留空）", True, f"{len(alts)}/{len(vo)}")

    _check_avsync_subs(path, add)
    _check_bgm_source(path, add)
    _check_music_gaps(path, add)

    return checks


def _check_avsync_subs(path: Path, add) -> None:
    """D86「试看字幕」：切自番剧集的试看块，窗口里有几句原片台词会上屏。

    写稿时就让人看到：0 句（多半是纯画面，或窗口写错了）、缺该集字幕索引（渲出来是
    无字幕的日语对白），不必等到 06 渲染才炸。SP 素材（Live / MV）不配字幕、不计入；
    全篇没有番剧集试看块时这一项不出现。
    """
    from . import music, subcues
    from .render import _resolve_tag

    rows, bad = [], []
    for b in music.parse_script_music(path):
        if not b.avsync or re.search(r"(?:^|\s)SP\d", b.avsync, re.I):
            continue                      # SP 素材不配字幕：这一项对它无话可说（归属错由渲染的切片守卫报）
        try:
            anime, key = _resolve_tag(path.parent, b.avsync)
        except SystemExit as e:
            bad.append(f"{b.label}：{str(e).removeprefix('FAIL ')}")
            continue
        if not b.src_subs:
            rows.append(f"{b.label}：已关（原声字幕: 关）")
            continue
        all_cues = subcues.load_cues(anime, key)
        if all_cues is None:
            bad.append(f"{b.label}：《{anime}》{key} 没有字幕索引——先建这一集的索引，或写 `原声字幕: 关`")
            continue
        n = len(subcues.window(all_cues, b.t0, b.t1))
        if n == 0:
            bad.append(f"{b.label}：窗口 {b.t0:.1f}-{b.t1:.1f}s 里一句台词都没有——"
                       f"纯画面就写 `原声字幕: 关`，否则多半是窗口写错了")
        else:
            rows.append(f"{b.label}：{n} 句")
    if rows or bad:
        add("试看字幕", not bad, "；".join(bad + rows))


#: 配音前的结构判定给每段的时长（秒，D133）：短到任何铺底封顶、曲目长度都盖得住，剩下的空档就与时长无关——
#: 前景块淡出之后、下一个音乐段之前的段落，配多长都是干口播，写稿时就能报。
_STRUCTURAL_SEG_S = 0.01


def _check_music_gaps(path: Path, add) -> None:
    """D133「无音乐空档」：试听型稿件（有 `## 音乐段`）的成片时间轴上，不许有没有任何音乐的口播区间。

    配音后（有 manifest、查的是 02-script.md）按真实段长复算，空档超过 MUSIC_GAP_MAX_S 判 FAIL；
    配音前（或查草稿）按结构判：每段取极短时长跑同一条时间轴，报淡出后无人接管的段落，
    与时长有关的那部分（铺底封顶、曲目播完）照实写明要配音后复核。普通期不出这一项。
    """
    from . import music

    if not music.parse_script_music(path):
        return
    name = f"无音乐空档（>{music.MUSIC_GAP_MAX_S:g} s）"
    ep = path.parent
    mf = ep / "03-audio" / "manifest.json"
    exact = path.name == "02-script.md" and mf.exists()
    try:
        if exact:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        else:
            labels = [int(m.group(2)) for m in music._HEAD.finditer(path.read_text(encoding="utf-8"))
                      if m.group(1) == "段落"]
            manifest = {"segments": [{"index": n, "duration": _STRUCTURAL_SEG_S} for n in labels]}
        plan = music.build_timeline(ep, manifest, music.load_tracks_multi(ep), script=path)
    except SystemExit as e:
        add(name, False, f"音乐时间轴建不起来：{str(e).removeprefix('FAIL ').strip()}"
            + ("" if not exact else "（稿件改过段落就先重配音，manifest 要与稿件对得上）"))
        return
    gaps = music.music_gaps(plan)
    fix = ("　每段口播都要有音乐垫着（2026-10-23 人定）：在空档前加背景铺底块，"
           "或把前一个前景块的 `过渡:` 改成 `降为 BGM`")
    if exact:
        bad = [g for g in gaps if g["dur"] > music.MUSIC_GAP_MAX_S]
        if bad:
            add(name, False, f"按配音时长复算有 {len(bad)} 处空档、共 {sum(g['dur'] for g in bad):.1f} s："
                + music.describe_gaps(bad) + fix)
        else:
            add(name, True, f"按配音时长复算，没有超过 {music.MUSIC_GAP_MAX_S:g} s 的空档")
        return
    bad = [g for g in gaps if g["segments"]]
    if bad:
        segs = "；".join(f"段{g['segments'][0]}" if len(g["segments"]) == 1
                        else f"段{g['segments'][0]}–{g['segments'][-1]}" for g in bad)
        add(name, False, f"按稿件结构，这些段落没有任何音乐（配多长都是干口播）：{segs}" + fix)
    else:
        add(name, True, "⚠ 还没按配音时长复算（缺 03-audio/manifest.json 或查的是草稿）：结构上每段都有音乐接管；"
            "铺底封顶、曲目播完这类与时长有关的空档，配音后再跑一次本检查")


def _check_bgm_source(path: Path, add) -> None:
    """D89「BGM 出处」：稿件有音乐段时 BGM 只认稿件里的铺底块，01 的 `BGM:` 列表不生效。

    判据在 `music.bgm_source_issue`（渲染入口同一份）。普通期、写法一致时这一项不出现；
    WARN 记成通过并在说明里带 ⚠（两处说法打架，不拦）。
    """
    from . import music

    issue = music.bgm_source_issue(music.parse_script_music(path), bgm.topic_bgm_list(path.parent))
    if issue is not None:
        level, text = issue
        add("BGM 出处", level != "FAIL", text if level == "FAIL" else f"⚠ {text}")


# ---------- D18 人物提示（INFO，只报不拦）----------

# 切块口径与 run/parse_episodes/parse_emotion_fields（本文件）及
# tts.parse_script/clips.parse_shots 一致：`## 段落 N` 到下一个段落标题之间。
# 与 clips 同款的多处同口径先例，改一处必须同步另一处。
_HINT_BLOCK = re.compile(r"^##\s*段落\s*(\S+)\s*\n(.*?)(?=^##\s*段落|\Z)", re.M | re.S)
_HINT_VO = re.compile(r"^配音[：:]\s*(.+)$", re.M)
_HINT_WHO = re.compile(r"^\s*人物[：:]\s*(.+)$", re.M)


def _character_alias_tables(episode: Path) -> dict[str, dict[str, str]]:
    """本期每部番 → {别名: 规范键}。任一步拿不到就安静返回空表（判据 4：跳过不定罪）。

    与 `vindex.alias_map` 同口径（规范键自身也算别名、`_` 前缀是注释键），但
    **不能直接调它**：vindex 顶层 `import numpy`，而 check_script 是纯 stdlib
    机检，必须零重依赖可跑（Spec 11 §5.2 的纯洁性断言就建在这条上）。
    """
    chars = paths.CONFIG / "characters.json"
    if not chars.exists():
        return {}
    db = json.loads(chars.read_text(encoding="utf-8"))
    tables: dict[str, dict[str, str]] = {}
    for anime in bgm.animes_of(episode):
        table = db.get(anime)
        if not isinstance(table, dict):      # 番不在表：这一番不报，其余番照常
            continue
        out: dict[str, str] = {}
        for tag, names in table.items():
            if tag.startswith("_"):
                continue
            out.setdefault(tag, tag)         # 规范键自己也当别名（同 vindex）
            for n in names:
                out.setdefault(str(n).strip(), tag)   # 先到先得，同 vindex.alias_map
        tables[anime] = out
    return tables


def _character_aliases(episode: Path) -> dict[str, str]:
    """本期全部番的别名表合并：键冲突一律先到先得（番序、表内序），与旧版及 vindex.alias_map 同。"""
    merged: dict[str, str] = {}
    for table in _character_alias_tables(episode).values():
        for alias, tag in table.items():
            merged.setdefault(alias, tag)
    return merged


def _alias_hits(text: str, aliases: dict[str, str]) -> list[str]:
    """最长优先、位置不重叠地匹配别名；返回按首现位置升序、已去重的别名列表。

    朴素子串匹配会双报嵌套别名（「大老师」里的「老师」、真实期命中别名总数
    12 → 22）。顺序必须由位置决定，命中收集**禁用 set**——set 迭代序随
    PYTHONHASHSEED 变，期望值不可复现（Spec 11 红队 🔴-1 的教训）。
    """
    spans: list[tuple[int, int, int, str]] = []
    for alias in aliases:
        if not alias:
            continue
        i = text.find(alias)
        while i >= 0:
            spans.append((-len(alias), i, i + len(alias), alias))
            i = text.find(alias, i + 1)
    spans.sort()                             # (取负长度, 起点, 终点, 别名)：长匹配先占位
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for _, s, e, alias in spans:
        if any(s < te and ts < e for ts, te in taken):
            continue                         # 与已占位的长匹配重叠，丢弃
        taken.append((s, e))
        hits.append((s, alias))
    out: list[str] = []
    seen: set[str] = set()
    for _, alias in sorted(hits):
        if alias not in seen:
            seen.add(alias)
            out.append(alias)
    return out


def character_hints(path: Path) -> list[str]:
    """D18 收口（Spec 11 §2.5）：口播段提到已登记角色却没写 `人物:` 的 INFO 提示行。

    **只报 INFO、永不进 FAIL、永不影响退出码。** 依据（issues archive D18，
    2026-09-25 拍板）：「该写没写」不可证伪（提到名字 ≠ 画面需求是该角色，
    强制每段写 = 逼人编字段），所以不立判据，`人物` 保持「写了就走过滤、
    不写不勉强」的纯可选通道。

    跳过规则：写了 `人物:`（人已显式选择）或 `锚点:`（锚点与人物/场景互斥，
    锚点段写人物本来就是 FAIL，提示它去写是教犯错）的段不报；写了 `场景:`
    的段照样列出——人选了场景通道，提示只是给人扫一眼。
    """
    aliases = _character_aliases(path.parent)
    if not aliases:
        return []
    out: list[str] = []
    for m in _HINT_BLOCK.finditer(path.read_text(encoding="utf-8")):
        label, block = m.group(1), m.group(2)
        vo = _HINT_VO.search(block)
        if not vo or _HINT_WHO.search(block) or _ANCHOR_FIELD.search(block):
            continue
        names = _alias_hits(vo.group(1).strip(), aliases)
        if names:
            out.append(f"INFO 段{label} 提到已登记角色但未写 `人物:`：{'、'.join(names)}")
    return out


# ---------- N47 人物贴名缺口提示（INFO，只报不拦）----------


def _named_cluster_tags(anime: str, vindex_dir: Path) -> set[str] | None:
    """该番 clusters 里已贴名的规范键集合；文件缺席/不可读 → None（判据 4：跳过不定罪）。

    簇的 `name` 是规范键（`faces.py` 贴名时过 alias 映射写入）。纯 json 读取，
    不碰 vindex/faces（numpy 依赖，check_script 必须零重依赖）。
    """
    p = vindex_dir / f"{anime}.clusters.json"
    if not p.exists():
        return None
    try:
        db = json.loads(p.read_text(encoding="utf-8"))
        clusters = db["clusters"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {str(c["name"]) for c in clusters.values()
            if isinstance(c, dict) and c.get("name")}


def faceless_character_hints(path: Path, *, vindex_dir: Path | None = None) -> list[str]:
    """N47：段里写了 `人物:`，但该角色在该番 clusters 里零个已贴名簇 → INFO。

    **只 INFO、永不 FAIL**（与 D18 同口径「写了就走过滤、不写不勉强」——这条报的是
    「写了也白写」：没有任何簇贴名时在场过滤恒为空并静默退回，人会把 Phase 0
    贴名缺口当成检索漏检）。clusters 文件缺席/不可读的番安静跳过（判据 4）。
    """
    tables = _character_alias_tables(path.parent)
    if not tables:
        return []
    vdir = vindex_dir if vindex_dir is not None else paths.DATA / "library" / "vindex"
    named = {a: tags for a in tables if (tags := _named_cluster_tags(a, vdir)) is not None}
    out: list[str] = []
    for m in _HINT_BLOCK.finditer(path.read_text(encoding="utf-8")):
        label, block = m.group(1), m.group(2)
        who = _HINT_WHO.search(block)
        if not who:
            continue
        name = who.group(1).strip()
        for anime, table in tables.items():
            tag = table.get(name)
            if tag is None:
                continue
            if anime in named and tag not in named[anime]:
                out.append(
                    f"INFO 段{label} `人物:` 的「{name}」在《{anime}》索引里无代表脸，"
                    f"在场过滤对本段无效（贴名：python -m pipeline.faces sheet {anime}）"
                )
            break
    return out


def rhythm(text: str) -> dict[str, float]:
    """只量节奏，不判定。给 `--raw` 用：拿任意一篇文章当参照样本重算基线。

    存在的理由是**基线必须可复核**。`skills/write-script/BASELINE.md` 里那几个数
    （人类样本 CV 0.45–0.81 等）如果只能靠一次性脚本算出来，下次想推翻它的人
    就没有对等的工具，只能选择相信——而这个项目里每一条阈值都是被实测推翻过一次的。
    """
    sents = [s.strip() for s in re.split(r"[。？！\n]+", text) if len(s.strip()) >= 2]
    breaths = [b for b in BREATH_SPLIT.split(text) if b]
    lens = [len(s) for s in sents] or [0]
    return {
        "字数": len(re.findall(r"[一-鿿]", text)),
        "整句均长": round(statistics.mean(lens), 1),
        "句长CV": round(statistics.pstdev(lens) / statistics.mean(lens), 2) if len(lens) > 1 else 0.0,
        "最长句": max(lens),
        f"超{LONG_SENT_LEN}字句占比%": round(100 * sum(1 for x in lens if x > LONG_SENT_LEN) / len(lens), 1),
        "气口均长": round(statistics.mean([len(b) for b in breaths]), 1) if breaths else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("script", type=Path, nargs="+")
    ap.add_argument("--raw", action="store_true",
                    help="把整个文件当正文，只打印节奏指标不做判定。用来量参照样本。")
    a = ap.parse_args()

    if a.raw:
        for p in a.script:
            m = rhythm(p.read_text(encoding="utf-8"))
            print(f"{p.stem:<28}" + "  ".join(f"{k} {v}" for k, v in m.items()))
        return 0

    if len(a.script) > 1:
        print("FAIL 判定模式一次只收一篇稿件", file=sys.stderr)
        return 2
    if not a.script[0].exists():
        print(f"FAIL 找不到 {a.script[0]}", file=sys.stderr)
        return 2

    checks = run(a.script[0])
    width = max(len(c.name) for c in checks) + 2
    failed = 0
    for c in checks:
        mark = "PASS" if c.ok else "FAIL"
        if not c.ok:
            failed += 1
        print(f"{mark}  {c.name:<{width}}{c.detail}")

    for line in character_hints(a.script[0]):
        print(line)
    for line in faceless_character_hints(a.script[0]):
        print(line)

    print("-" * 60)
    genre, mode = episode_genre(a.script[0])
    subjective = f"主观项仍需人看：{subjective_hint(genre, mode)}"
    print(f"{failed} 项未过。{subjective}" if failed else f"机检全过。{subjective}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
