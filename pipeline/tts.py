"""口播配音：`02-script.md` → 每段一个 wav + 它的真实时长。

**顺序不可交换**：本步必须先于排画面轨。画面轨的长度由这里产出的真实时长决定，
反过来做全程对不齐（见 CLAUDE.md）。

自回归 TTS 的典型失败不是报错，是**静默地念错**——漏掉半句、把一句念两遍、
或者在长句中间跑飞。所以每段生成后都用 Whisper 回读一遍，和原文比字符错误率；
不过就换种子重生成，重试用尽仍不过就整体失败退出，绝不把坏音频留在盘上。
这是 CLAUDE.md「诚实失败优于凑合交付」在本阶段的落地。

用法：
    python -m pipeline.tts <每期目录>              # 读 config/voice.json
    python -m pipeline.tts <每期目录> --force      # 重跑已存在的段落
    python -m pipeline.tts probe "一句测试文本"     # 只试音，不进每期目录
"""

from __future__ import annotations

import argparse
import json
import math
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

CONFIG = paths.CONFIG / "voice.json"

CPM = 280           # 中文口播字/分钟，与 check_script 同源
DUR_BAND = (0.5, 2.0)   # 实际时长 / 估算时长 的允许区间，超出说明念飞了
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
    """稿件里的一段口播。label 是稿件标注的段落号，可能不连续。"""

    index: int      # 出现顺序，1-based，决定文件名与渲染顺序
    label: str      # 稿件里写的「段落 N」
    text: str


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


# ---------- 稿件解析 ----------

def parse_script(path: Path) -> list[Segment]:
    text = path.read_text(encoding="utf-8")
    segs: list[Segment] = []
    for m in re.finditer(
        r"^##\s*段落\s*(\S+)\s*$\s*^配音[：:]\s*(.+)$", text, re.M
    ):
        segs.append(Segment(len(segs) + 1, m.group(1), m.group(2).strip()))
    if not segs:
        raise SystemExit(f"FAIL 没从 {path} 解析出任何「## 段落 N + 配音：」，检查稿件格式")
    return segs


# ---------- 质检 ----------

def normalize(s: str) -> str:
    """回读比对与字数统计的统一口径：去标点、转小写、折叠同音字。

    折叠放在这里而不是只放在 `cer` 里，是因为它长度不变，字数统计不受影响，
    而放在一处能保证参考文本和回读文本走的是同一条路——两边口径不一致
    才是这类比对最容易出的错。
    """
    return _DROP.sub("", s).lower().translate(_FOLD)


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


def cer(ref: str, hyp: str) -> tuple[int, float]:
    """回读比对，返回（编辑距离, 音节错误率）。段落都在百字以内，朴素 DP 足够。

    要两个数字是因为短段落上比率极不稳：十个音节的段落错一个就是 10%，
    而那只是 ASR 自己听岔了。判定时比率与绝对错数要同时越线。
    """
    a, b = syllables(normalize(ref)), syllables(normalize(hyp))
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
    return len(normalize(text)) / CPM * 60


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


LOADERS = {"indextts": _load_indextts}


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
        if not Path(self.ref_audio).exists():
            raise SystemExit(f"FAIL 参考干声不存在：{self.ref_audio}")

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

    def synthesize(self, text: str, dest: Path, attempt: int, seed: int) -> int:
        """生成一段并落盘，返回采样率。attempt 从 1 起，决定采样温度。"""
        import mlx.core as mx
        import numpy as np
        from scipy.io import wavfile

        mx.random.seed(seed)
        temp, top_k = self.SAMPLING[min(attempt, len(self.SAMPLING)) - 1]

        if self.kind == "indextts":
            audio, rate = _indextts_audio(
                self.model, self.ref_mel, text, temp, top_k, self.MAX_MEL_TOKENS
            )
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

        audio = audio.reshape(-1)
        if not audio.size or float(np.max(np.abs(audio))) == 0.0:
            raise RuntimeError("模型产出全静音")

        dest.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(dest, rate, (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16))
        return rate


def transcribe(path: Path) -> str:
    """回读：把生成的音频交给 Whisper，用来抓漏读 / 重复 / 跑飞。"""
    import mlx_whisper
    from .asr import REPO

    return mlx_whisper.transcribe(
        str(path), path_or_hf_repo=REPO, language="zh", verbose=None
    )["text"].strip()


# ---------- 主流程 ----------

def _ref_text_for(ref: Path) -> str | None:
    """参考干声的文本。VoxCPM 这类引擎要 ref_text，IndexTTS 用不上。"""
    book = (paths.ROOT / ref).resolve().parent / "transcripts.json"
    if not book.exists():
        return None
    return json.loads(book.read_text(encoding="utf-8")).get(Path(ref).name)


def load_config(path: Path = CONFIG) -> dict:
    if not path.exists():
        raise SystemExit(
            f"FAIL 缺少 {path}。先跑 `python -m pipeline.tts probe` 选定音色，"
            f"再把结论写进这个文件。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


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
def _readings() -> dict[str, str]:
    """`config/voice.json` 的读音替换表（键 = 原文词，值 = 合成侧同音替换词）。

    **IndexTTS 念错多音字/生僻字没有参数能修**——读音由模型内部判断。
    只能在合成文本上换成模型不会念错的同音字（2026-08-09 实测：
    喰种→餐种、绚都→绚督 有效）。字幕用稿子原文，替换只发生在合成侧。
    每次合成句都调 speakable，表必须只读一次，不能每句读文件。
    """
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return cfg.get("readings", {})


def speakable(s: str) -> str:
    """念得出来的那份文本。字幕用原文，合成用这个。"""
    s = _MUTE.sub("", s)
    for src, rep in _readings().items():
        s = s.replace(src, rep)
    return s


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


def _trim_silence(path: Path) -> None:
    """裁掉首尾静音与结尾的机械声，就地重写。

    每句自带的首尾静音加起来能有 0.4s，60 来句就是 20 多秒的死时间，
    而且长短不一，节奏没法控制。裁干净之后由 SENT_GAP 统一给停顿。
    """
    import wave
    import struct

    with wave.open(str(path)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a = list(struct.unpack(f"<{n}h", w.readframes(n)))
    thr = 32768 * TRIM_DB
    lo = next((i for i, x in enumerate(a) if abs(x) > thr), 0)
    hi = next((i for i in range(n - 1, -1, -1) if abs(a[i]) > thr), n - 1) + 1
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
        take.sentences = [{"text": seg.text, "start": 0.0, "duration": take.duration}]
        return take

    tmp_dir = dest.parent / f".{dest.stem}-sents"
    tmp_dir.mkdir(exist_ok=True)
    try:
        parts, worst_cer, tries = [], 0.0, 1
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
        _concat_with_gap(parts, dest, SENT_GAP)
        return Take(seg.index, seg.label, seg.text, dest.name,
                    round(probe_duration(dest), 3), round(worst_cer, 4), tries, meta)
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


def _render_one(engine: Engine, seg: Segment, dest: Path) -> Take:
    """生成一句，直到它通过质检；用尽重试仍不过则抛错。"""
    want = expected_duration(seg.text)
    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        # 只有喂给模型的这份要剥引号。**回读比对那边不用管**——
        # `normalize` 早就把引号算进 `_DROP` 里了，这也正是这个 bug 藏得住的原因：
        # 参考文本没引号，回读多出「非」「匪」两个字，只算 2 处插入，
        # CER 7% 远在 20% 门槛之下，门禁照常放行，要人听出来才发现。
        engine.synthesize(speakable(seg.text), dest, attempt,
                          seed=attempt * 1000 + seg.index)
        # **裁剪要排在回读之前。** 裁掉的是首尾静音与结尾的机械声，但判据是启发式的，
        # 万一切进了句尾真实的字，只有回读能发现。放在回读之后裁就没人管了。
        _trim_silence(dest)
        dur = probe_duration(dest)
        ratio = dur / want if want else 0.0
        heard = transcribe(dest)
        edits, err = cer(seg.text, heard)

        ok_dur = DUR_BAND[0] <= ratio <= DUR_BAND[1]
        ok_cer = err <= MAX_CER or edits < MIN_EDITS
        if ok_dur and ok_cer:
            return Take(seg.index, seg.label, seg.text, dest.name,
                        round(dur, 3), round(err, 4), attempt)

        why = []
        if not ok_dur:
            why.append(f"时长 {dur:.1f}s / 估算 {want:.1f}s = {ratio:.2f}×")
        if not ok_cer:
            why.append(f"回读差 {edits} 音节（CER {err:.0%}）「{heard[:40]}」")
        last = "；".join(why)
        print(f"  第 {attempt} 次不过：{last}", file=sys.stderr)

    dest.unlink(missing_ok=True)
    raise SystemExit(
        f"FAIL 段落 {seg.label} 重试 {ATTEMPTS} 次仍不达标（{last}）。\n"
        f"     原文：{seg.text}\n"
        f"     不降标准硬交。可能是这段太长或有生僻表达，改稿或换参考干声后重跑。"
    )


def run(episode: Path, force: bool = False, cfg_path: Path = CONFIG) -> Path:
    paths.require_data()
    script = episode / "02-script.md"
    if not script.exists():
        raise SystemExit(f"FAIL 找不到 {script}")

    cfg = load_config(cfg_path)
    segs = parse_script(script)
    out_dir = episode / "03-audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    done: dict[int, Take] = {}
    if manifest_path.exists() and not force:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        for t in old.get("segments", []):
            take = Take(**t)
            if (out_dir / take.file).exists() and take.text == next(
                (s.text for s in segs if s.index == take.index), None
            ):
                done[take.index] = take

    engine: Engine | None = None
    takes: list[Take] = []
    t0 = time.perf_counter()
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
        print(f"OK   段落 {seg.label}  {take.duration:5.1f}s  CER {take.cer:4.0%}  "
              f"{take.attempts} 次  {seg.text[:20]}…")

    total = sum(t.duration for t in takes)
    manifest_path.write_text(
        json.dumps(
            {
                "engine": cfg["engine"],
                "model": cfg["model"],
                "ref_audio": cfg["ref_audio"],
                "total_duration": round(total, 3),
                "segments": [asdict(t) for t in takes],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print("-" * 60)
    print(f"OK {len(takes)} 段，总时长 {total / 60:.1f} 分钟，"
          f"耗时 {time.perf_counter() - t0:.0f}s → {manifest_path}")
    if not 120 <= total <= 240:
        print(f"WARN 成片时长 {total / 60:.1f} 分钟，超出 2–4 分钟区间", file=sys.stderr)
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
        run(a.episode, a.force, a.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
