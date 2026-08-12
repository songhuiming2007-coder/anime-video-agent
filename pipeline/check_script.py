"""口播稿机检。

write-script 的自检清单里可判定的那部分，交稿前跑，不靠 agent 自觉。
主观项（张力立不立得住、公道话像不像反方）机器判不了，仍需人看。

    python -m pipeline.check_script <稿件.md>
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from . import bgm, paths

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

# `01-topic.md` 里 `时长目标: 7-8分钟` 这样的字段，覆盖本期的字数/时长带。
# 不填就是上面 MIN_CHARS–MAX_CHARS 这条默认带，行为不变。
#     不锚 `$`：01-topic.md 允许行尾挂 `# 备注`（BGM 字段已经这么用），
#     锚了 `$` 会把注释一起吃进数字组，2026-08-09 实测直接踩到——
#     字段写了但正则不匹配，静默退回默认档，没有任何报错。
DURATION_FIELD = re.compile(r"^\s*时长目标\s*[:：]\s*([\d.]+)\s*[-–~]\s*([\d.]+)\s*分钟", re.M)


def episode_duration_override(script_path: Path) -> tuple[float, float] | None:
    """从这一期 `01-topic.md` 的 `时长目标` 字段读目标时长（分钟）。

    没有这个文件、或没写这个字段，返回 None，调用方退回默认带——这是刻意的
    软失败：字段是可选项，不是「文件必须存在」的前置检查。
    """
    topic = script_path.parent / "01-topic.md"
    if not topic.exists():
        return None
    m = DURATION_FIELD.search(topic.read_text(encoding="utf-8"))
    return (float(m.group(1)), float(m.group(2))) if m else None

# 五种题材（见 skills/write-script 第 4 节）。`类型` 行允许带括号备注
# （「人物志（经历+点评，编年体）」），按关键词匹配主词，不在五种里就返回空串。
GENRES = ("人物志", "剧情回顾", "杂谈", "盘点", "共鸣")
TOPIC_FIELDS = re.compile(r"^\s*(类型|模式)\s*[:：]\s*(.+)", re.M)


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
# 所以挡在这里，与「无括号」「无引号」同源：**稿子里出现念不出来的东西，是稿子的问题。**
# 不在 `tts.speakable` 里自动转中文，因为转法有歧义——2 可以是二、两、俩，
# 机器替人选一个，等于把念法的决定权从写稿的人手里拿走且不留痕迹。
#
# 拉丁字母不在此列：同一期的 yy 与 coding 都念得正常，没有证据就不立规矩。
DIGITS = re.compile(r"[0-9０-９]")
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
    """集号归一化为 `SxxEyy` 规范形。认不出返回 None（调用方判格式 FAIL）。"""
    m = re.fullmatch(r"S(\d{1,2})E(\d{1,2})", raw, re.I)
    if not m:
        return None
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


def run(path: Path) -> list[Check]:
    text = path.read_text(encoding="utf-8")
    vo, queries, alts = parse(text)

    override = episode_duration_override(path)
    if override:
        dur_lo, dur_hi = override
        min_chars, max_chars = round(dur_lo * CPM), round(dur_hi * CPM)
    else:
        dur_lo, dur_hi = MIN_CHARS / CPM, MAX_CHARS / CPM
        min_chars, max_chars = MIN_CHARS, MAX_CHARS

    # 末段若是片尾套话，从正文统计里摘出去（仍然会被配音，只是不参与内容判定）
    outro = vo[-1] if vo and len(vo[-1]) <= 25 and OUTRO.search(vo[-1]) else None
    if outro:
        vo = vo[:-1]

    body = " ".join(vo)
    chars = sum(len(v) for v in vo)
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, ok, detail))

    tail = "（另有片尾 1 段，不计入）" if outro else ""
    add("段落数 8–20", 8 <= len(vo) <= 20, f"{len(vo)} 段{tail}")
    add(f"字数 {min_chars}–{max_chars}", min_chars <= chars <= max_chars, f"{chars} 字{tail}"
        + ("　（01-topic.md 时长目标覆盖）" if override else ""))
    add(f"时长 {dur_lo:g}–{dur_hi:g} 分钟", dur_lo <= chars / CPM <= dur_hi, f"{chars / CPM:.1f} 分钟")
    # 查询按含片尾的总段数比，因为每一段都要出画面
    add("每段都有查询", len(queries) == len(vo) + bool(outro),
        f"查询 {len(queries)} / 段落 {len(vo) + bool(outro)}")

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

    if eps:
        anime = bgm.anime_of(path.parent)
        if not anime:
            add("集号在素材库", False,
                "跳过不可判定：01-topic.md 没写「番:」字段，读不到番名（S9 跳过不是通过）")
        else:
            try:
                from . import ingest  # 延迟 import：只在写了集号时才需要素材库
                sources = ingest.load_sources(anime)
            except SystemExit as e:
                add("集号在素材库", False,
                    f"跳过不可判定：{e}（S9 跳过不是通过）")
            else:
                missing = [(p, r) for p, r in eps
                           if _norm_ep(r) and _norm_ep(r) not in sources]
                if missing:
                    msgs = []
                    for p, r in missing:
                        norm = _norm_ep(r)
                        season = norm[1:3]
                        same = sorted(k for k in sources
                                      if k.startswith(f"S{season}"))
                        if same:
                            msgs.append(
                                f"段{p}「{r}」：《{anime}》S{season} 季有 "
                                f"{len(same)} 集入库（如 {same[0]}），唯独没有 {norm}"
                                f"——多半是集号写错；确定没写错就是这集没入库，"
                                f"补 `python -m pipeline.ingest phase0`")
                        else:
                            msgs.append(
                                f"段{p}「{r}」：S{season} 季在素材库里一集都没有"
                                f"——不是集号写错，是整季没入库，"
                                f"先跑 `python -m pipeline.ingest phase0`")
                    add("集号在素材库", False, "；".join(msgs[:3]))
                else:
                    add("集号在素材库", True, f"{len(eps)} 段集号都在素材库里")

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

    # 引号与括号同理：**纯书面符号，念不出来。**
    # 2026-07-30 实测 IndexTTS 把弯引号当字符各吐一个音节——「谁更该“赢”」
    # 念成「谁更该非赢匪」。管道侧已经在合成前剥掉（`tts.speakable`），
    # 但稿件里出现引号仍然是个信号：它说明那句话在依赖视觉标记表达强调或引用，
    # 而口播只有语气可用。**念得出来是这份稿子的最终判据。**
    quotes = QUOTES.findall(body)
    add("无引号", not quotes, f"{len(quotes)} 处：{'、'.join(sorted(set(quotes)))}"
        if quotes else "无")

    digits = DIGITS.findall(body)
    add("无阿拉伯数字", not digits,
        f"{len(digits)} 处：{''.join(digits[:8])}　改成中文数字，TTS 的读法不可控"
        if digits else "无")

    vague = VAGUE.findall(body)
    add("无模糊指代", not vague, "、".join(sorted(set(vague))) or "无")

    stock = [s.strip("。？！ ") for s in STOCK.findall(body)]
    add("无跨期套话", not stock, "、".join(f"「{s}」" for s in sorted(set(stock))) or "无")

    enum = ENUM.findall(body)
    add("论据不报菜名", len(set(enum)) < 2,
        "、".join(f"「{e}」" for e in sorted(set(enum))) + "　并列平铺，挑一个挖到底"
        if len(set(enum)) >= 2 else "无")

    # 剧情锚点：以每段画面块的 `集:` 字段为准（ADR-0004 的解析结果，集号格式
    # 已由上方「集号格式/集号在素材库」两项把关）。**不再从正文找「第X集」字样**
    # ——正文要不要点明集数、剧情对不对、文字像不像人，是 02.5 人审的活，
    # 机检逼正文写集数 = 让机器决定内容，方向反了（2026-08-12 改）。
    anchors = [r for _, r in parse_episodes(text) if _norm_ep(r)]
    add(f"剧情锚点 ≥{MIN_ANCHOR}", len(anchors) >= MIN_ANCHOR,
        f"{len(anchors)}/{len(vo)} 段画面块带集号" if anchors else "0 段，全篇没有画面集号锚点")

    visual = VISUAL.findall(" ".join(queries))
    add("查询无构图词", not visual, "、".join(sorted(set(visual))) or "无")

    # 两段用同一条查询是**静默失败**：排片是全局贪心分派、同一处画面整期只用一次，
    # 所以第二段拿不到那句台词的画面，会退到明显更差的命中，而且不报 no_match。
    # 2026-07-30 实测：改稿合并段落时把上一段的查询整条带了过来，检查全绿。
    dup = sorted({q for q in queries if queries.count(q) > 1})
    add("查询不重复", not dup,
        "、".join(f"「{q[:14]}…」×{queries.count(q)}" for q in dup[:3]) or "无")

    add("备选覆盖（允许留空）", True, f"{len(alts)}/{len(vo)}")

    return checks


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

    print("-" * 60)
    genre, mode = episode_genre(a.script[0])
    subjective = f"主观项仍需人看：{subjective_hint(genre, mode)}"
    print(f"{failed} 项未过。{subjective}" if failed else f"机检全过。{subjective}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
