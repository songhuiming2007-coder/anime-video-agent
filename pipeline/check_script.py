"""口播稿机检。

write-script 的自检清单里可判定的那部分，交稿前跑，不靠 agent 自觉。
主观项（张力立不立得住、公道话像不像反方）机器判不了，仍需人看。

    python -m pipeline.check_script <稿件.md>
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import paths

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
MIN_CHARS = paths.conf("script.min_chars", 870)
MAX_CHARS = paths.conf("script.max_chars", 1300)
MAX_SENT = paths.conf("script.max_sentence", 40)   # 单句上限，超过念着断气
MIN_ANCHOR = paths.conf("script.min_anchors", 3)   # 剧情锚点下限

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
PARENS = re.compile(r"[（）()]")
QUOTES = re.compile(r"[“”‘’「」『』\"']")
VISUAL = re.compile(r"(中景|近景|远景|全景|特写|逆光|镜头|构图|俯拍|仰拍|机位)")
VAGUE = re.compile(r"(那几次事情|那些人|某些|有些人|某个角色|某部作品|后面这句|这类人)")
# 片尾套话。它是固定收尾、不承担内容，不占正文段数也不进字数——
# 否则每期都会把段数门槛顶穿一格，检查项一旦长期误报就没人看了。
OUTRO = re.compile(r"(下期再见|下期见|就到这里|我们下期|感谢观看|拜拜)")
# 可指认的剧情锚点：季/集/话（通用写法），或具名场合（**逐番不同，来自配置**）。
#
# 具名场合原先写死在这条正则里，全是春物的专有名词（文化祭、修学旅行、侍奉部……）。
# 换一部番这一半就一个都命中不了，而锚点检查会因此永远判「0 处，全篇抽象」——
# 检查项长期误报等于没有检查。2026-07-29 审计时挪进 config/project.json。
_SCENES = paths.conf("anime.anchor_words", [
    "文化祭", "体育祭", "修学旅行", "社团", "侍奉部", "奉仕部",
    "毕业", "开学", "生日", "告白", "决赛", "最终话",
])
ANCHOR = re.compile(
    r"(第[一二三四五六七八九十百\d]+[季集话]|S\d+E?\d*"
    + ("|" + "|".join(re.escape(w) for w in _SCENES) if _SCENES else "")
    + r")")


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


def run(path: Path) -> list[Check]:
    text = path.read_text(encoding="utf-8")
    vo, queries, alts = parse(text)

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
    add(f"字数 {MIN_CHARS}–{MAX_CHARS}", MIN_CHARS <= chars <= MAX_CHARS, f"{chars} 字{tail}")
    add("时长 2–4 分钟", 2 <= chars / CPM <= 4, f"{chars / CPM:.1f} 分钟")
    # 查询按含片尾的总段数比，因为每一段都要出画面
    add("每段都有查询", len(queries) == len(vo) + bool(outro),
        f"查询 {len(queries)} / 段落 {len(vo) + bool(outro)}")

    long_sents = [s.strip() for v in vo for s in re.split(r"[。？！]", v)
                  if len(s.strip()) > MAX_SENT]
    add(f"无超 {MAX_SENT} 字长句", not long_sents,
        "、".join(f"{len(s)}字「{s[:18]}…」" for s in long_sents[:3]) or "无")

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

    vague = VAGUE.findall(body)
    add("无模糊指代", not vague, "、".join(sorted(set(vague))) or "无")

    anchors = ANCHOR.findall(body)
    add(f"剧情锚点 ≥{MIN_ANCHOR}", len(anchors) >= MIN_ANCHOR,
        f"{len(anchors)} 处：{'、'.join(sorted(set(anchors))[:5])}" if anchors else "0 处，全篇抽象")

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("script", type=Path)
    a = ap.parse_args()

    if not a.script.exists():
        print(f"FAIL 找不到 {a.script}", file=sys.stderr)
        return 2

    checks = run(a.script)
    width = max(len(c.name) for c in checks) + 2
    failed = 0
    for c in checks:
        mark = "PASS" if c.ok else "FAIL"
        if not c.ok:
            failed += 1
        print(f"{mark}  {c.name:<{width}}{c.detail}")

    print("-" * 60)
    if failed:
        print(f"{failed} 项未过。主观项仍需人看：张力是否立得住、公道话是否像反方、"
              f"第 7 节转回是否写了。")
    else:
        print("机检全过。主观项仍需人看：张力、公道话、转回。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
