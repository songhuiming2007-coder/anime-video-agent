"""拼音直注层：D25 音读泄漏的自动化治理（v2 新建，替代 readings 手工表）。

## 为什么需要这一层

ADR-0006 补记（2026-09-08）定性了根因：Unicode 里中日汉字**共用 Token ID**，
多语种自回归底模在上下文微小漂移时，注意力头会跨语种借调，激活日语音读
（世界→秀界、监督→坚督、全部→权部）。人工逐词打地鼠边际成本无限高。

已验证的物理阻断手段是**拼音直注**（先例 `shí六首`、`pèi乐`、`shēng前` 100% 咬死）：
拼音没有日语语义/声学映射，模型无从借调。

## 本模块的职责边界

**只负责「哪个字该读什么」，不负责「怎么写给引擎」**（架构设计三.2 要点 2）。
`pinyin_injections` 表里统一存 **TONE3**（`shi4jie4`）这一份内部表示，
渲染成各引擎语法由 `format_for_engine` 按 engine 分派：

| 引擎 | 书写形式 | 来源 |
|---|---|---|
| `indextts2` | `SHI4JIE4`（大写字母 + 数字声调） | 官方 docs/README_zh.md 明确支持中英拼混，合法词表在 `checkpoints/pinyin.vocab` |
| `qwen3_tts` | `shìjiè`（小写字母 + 声调符号） | 本项目实测先例：`chéng人`、`ròu身`、`shí六首` 均 100% 生效 |

**IndexTTS2 的语法目前只有文档证据，云端实测确认前不要当既成事实**（S7）。
若实测不通，降级路径是「同音字替换」——即 v1 readings 机制的自动化版，
由 `readings_override()` 兜住，注释就地写明降级原因。

## 与 readings 表的关系

readings 表（84 条）不是被删，而是被**机械转录**：它的替换词本来就是
「正确读音的同音字」，其拼音即原词的应有读音，所以 `pinyin(替换词, TONE3)`
就是该词的注入值。转录结果必须**打印成表交人核对**后才写入 voice.json
（`derive` 子命令，不带 `--write` 只打印不落盘）——静默生效是不可接受的。

转录覆盖情况（2026-09-11 实测）：84 条中 61 条替换词为纯汉字、可机械转录；
23 条替换词含拼音/假名/英文/空格（如 `成人→chéng人`、`Aimer→えめ`），
机械转录会产出垃圾，一律进「需人工」清单，不猜。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypinyin import Style, pinyin
from pypinyin.contrib.tone_convert import to_tone

from . import paths

CONFIG = paths.CONFIG / "voice.json"

# 纯汉字判定：转录只对「替换词全是汉字」的条目做，其余交人。
_HAN_ONLY = re.compile(r"[\u4e00-\u9fff]+")

# TONE3 的音节：字母（含 ü、ê 与 ASCII 化写法的 v）+ 末尾声调数字（1-5）。
# 数字始终在音节末尾，所以这条正则能把紧凑串可靠切回音节。
_TONE3_SYLLABLE = re.compile(r"[a-züê]+[1-5]")

# ADR-0006 补记记载的六例音读泄漏，做成固定样本集（tests 与云端验证共用）。
# 这六条是本项目自己踩出来的泄漏清单，不是从别处抄的（E3）。
ADR0006_LEAK_CASES = {
    "世界": "SHI4JIE4",
    "全部": "QUAN2BU4",
    "早期": "ZAO3QI1",
    "监督": "JIAN1DU1",
    "舞台": "WU3TAI2",
    "五百": "WU3BAI3",
}


def to_tone3(word: str) -> str:
    """汉字词 → TONE3 紧凑串（`世界` → `shi4jie4`）。

    这是 `pinyin_injections` 表的**唯一存储格式**：引擎无关、可读、可比对。
    逐音节拼接而非空格分隔，是为了让一个词条对应一个不可再分的注入单元。
    """
    groups = pinyin(word, style=Style.TONE3, neutral_tone_with_five=True)
    return "".join(g[0].lower() for g in groups)


def format_for_engine(tone3: str, engine: str) -> str:
    """TONE3 内部表示 → 指定引擎的注音语法（纯函数）。

    分派表见模块头。未知引擎按「小写带调拼音」处理——那是本项目已有实测先例
    的形式（`chéng人`），比大写数字形式更可能被一个没见过的引擎接受。

    **必须逐音节调 to_tone。** 2026-09-11 实测：把整个紧凑串喂进去，
    `to_tone('xin1li3ce4liang2zhe3')` 返回 `'xinliceliāngzhe'`——
    声调全丢且有一处标错位置。它对单音节（`'shi4'`→`'shì'`）才是对的。
    """
    kind = (engine or "").lower()
    if kind.startswith("indextts"):
        return tone3.upper()
    syllables = _TONE3_SYLLABLE.findall(tone3)
    if not syllables:
        return to_tone(tone3)
    return "".join(to_tone(s) for s in syllables)


def derive_injections_from_readings(
    readings: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """readings 表 → (可机械转录的注入表, 需人工处理的条目)。纯函数。

    返回两个字典而不是一个：把「转不出来」的条目**显式交出去**，
    让它出现在人核对的表上，而不是被静默丢弃或用垃圾值填充。
    """
    mechanical: dict[str, str] = {}
    manual: dict[str, str] = {}
    for src, rep in readings.items():
        if src.startswith("_"):
            continue
        if _HAN_ONLY.fullmatch(rep):
            mechanical[src] = to_tone3(rep)
        else:
            manual[src] = rep
    return mechanical, manual


def load_injections(cfg: dict) -> dict[str, str]:
    """读 voice.json 的 `pinyin_injections`（键=原文词，值=TONE3 串）。"""
    table = cfg.get("pinyin_injections") or {}
    return {k: v for k, v in table.items() if not k.startswith("_")}


def inject(
    text: str, injections: dict[str, str], engine: str
) -> tuple[str, list[dict]]:
    """局部拼音注入（纯函数），返回 (注入后文本, 注入记录)。

    **注入是局部的，不是全文转拼音。** 全文拼音会摧毁模型对分词与韵律的既有先验
    （架构设计三.2 要点 1），只替换命中的风险词。

    按**词长降序**处理：这是对 readings 时代「键的插入顺序决定成败」那类坑的
    结构性消除——`一个世界`/`世界本来` 必须早于 `世界` 命中，靠人肉排序迟早出错
    （readings 的 _note 里专门写了一段警告），按长度排序则自动成立。

    注入记录进 manifest 的 `g2p_injections` 字段：机器做的替换与 readings 表
    一样需要可审计（要点 3）。

    返回的每条记录对应一个**词条**（不是一次匹配）：`str.replace` 一次换掉
    全部命中位置，命中数记在 `hits` 里。
    """
    applied: list[dict] = []
    for src in sorted(injections, key=len, reverse=True):
        if src and src in text:
            rendered = format_for_engine(injections[src], engine)
            # 一条记录 = 一个词条（str.replace 一次换掉全部命中位置），
            # hits 记录实际改了几处——审计时「以为只改了一处」是常见误判。
            hits = text.count(src)
            text = text.replace(src, rendered)
            applied.append({"from": src, "to": rendered, "tone3": injections[src], "hits": hits})
    return text, applied


def scan_heteronyms(text: str, covered: set[str] | None = None) -> list[dict]:
    """多音字预检（纯函数）：返回**信息性**清单，只报告不拦截。

    用途是给 03.5 顺听提供重点关注清单——多音字是音读泄漏的高危区，
    但「多音字」本身不是错误（绝大多数在上下文里读对），所以这里绝不返回
    布尔值让调用方去拦。

    `covered` 是已被 injections/readings 治理过的字，命中它们的多音字不再重复报警。
    """
    covered = covered or set()
    out: list[dict] = []
    groups = pinyin(text, style=Style.TONE3, heteronym=True, neutral_tone_with_five=True)
    for ch, grp in zip(text, groups):
        if len(grp) < 2 or not _HAN_ONLY.match(ch):
            continue
        if ch in covered:
            continue
        out.append({"char": ch, "readings": sorted(set(grp))})
    return out


def _cmd_derive(args: argparse.Namespace) -> int:
    """打印（或显式写入）readings → pinyin_injections 的转录表。"""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    mechanical, manual = derive_injections_from_readings(cfg.get("readings", {}))

    print("=" * 74)
    print(f"readings → pinyin_injections 机械转录表  （共 {len(mechanical) + len(manual)} 条）")
    print("=" * 74)
    print(f"\n【可机械转录：{len(mechanical)} 条】转录自替换词拼音，请逐条核对读音")
    for src, tone3 in sorted(mechanical.items(), key=lambda kv: -len(kv[0])):
        engine_forms = f"indextts2={format_for_engine(tone3, 'indextts2')}  qwen3={format_for_engine(tone3, 'qwen3_tts')}"
        print(f"  {src:<14} {tone3:<16} {engine_forms}")

    print(f"\n【需人工：{len(manual)} 条】替换词含拼音/假名/英文/空格，机械转录会产垃圾，不猜")
    for src, rep in sorted(manual.items()):
        print(f"  {src:<14} readings={rep!r}")

    if args.write:
        cfg["pinyin_injections"] = {
            "_note": (
                f"TONE3 统一表示（如 shi4jie4），引擎语法由 pipeline/g2p.py 的 "
                f"format_for_engine 按 engine 分派。本表 {len(mechanical)} 条由 "
                f"readings 表机械转录，2026-09-11 生成，**待人工核对**；"
                f"另有 {len(manual)} 条替换词非纯汉字，未纳入（见 g2p.py 模块文档）。"
            ),
            **mechanical,
        }
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[OK] 已写入 {CONFIG} 的 pinyin_injections（{len(mechanical)} 条，标注待核对）")
    else:
        print("\n[i] 未写入。核对无误后加 --write 落盘。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("derive", help="从 readings 转录 pinyin_injections 表（默认只打印）")
    d.add_argument("--write", action="store_true", help="核对无误后写入 config/voice.json")
    args = ap.parse_args()
    if args.cmd == "derive":
        return _cmd_derive(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
