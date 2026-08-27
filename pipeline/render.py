"""渲染成片（流程第 06 步）。

    python -m pipeline.render data/episodes/<本期>

吃 `04-clips.approved.json` + `03-audio/`，吐 `05-final.mp4`。
**只吃 approved 版**——`04-clips.json` 是机器排的，没过人眼不许进渲染，
见 CLAUDE.md「人类在 02.5 / 05 / 09 出现」。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import bgm, paths
from .music import FOREGROUND_LUFS  # 试听型前景响度，music.py 单源
from .align import verify_alignment   # 叶子模块，无环；不挂 clips（否则把检索的 ML 栈拖进渲染）

# 输出规格。片源是 1920x1080 / 23.976fps / yuv420p10le，成片降到 8bit：
# 抖音和 B 站的转码链对 10bit 支持不稳，而我们又不做调色，10bit 没有收益。
W = paths.conf("video.width", 1920)
H = paths.conf("video.height", 1080)
FPS = paths.conf("video.fps", "24000/1001")
# 二压素材，源已经压过一次，这里给足码率避免二次劣化
CRF = paths.conf("video.crf", "18")
PRESET = paths.conf("video.preset", "medium")

# 字幕样式。1080p 下 54px 约等于画面高度的 5%，是竖屏解说号的常见字号；
# MarginV 90 把字托在下方安全区内，MarginL/R 各 120 保证不顶到边——
# 这三个数直接决定质检的「字幕不超框」过不过。
#
# **字体名必须是本机 fontconfig 认得的。** libass 找不到会静默退到默认字体，
# 而默认字体多半没有中文字形——渲出来是一屏方块，不报错。换机器先确认这个名字存在：
#     fc-list :lang=zh family | head
FONT = paths.conf("subtitle.font", "Hiragino Sans GB")
FONT_SIZE = paths.conf("subtitle.size", 54)
MARGIN_V = paths.conf("subtitle.margin_v", 90)
MARGIN_LR = paths.conf("subtitle.margin_lr", 120)

# 响度目标，见 CLAUDE.md「质检门禁」
LUFS = paths.conf("audio.lufs", -16)
TRUE_PEAK = paths.conf("audio.true_peak_max", -1.0) - 0.5   # 归一留 0.5dB 余量给编码
LRA = 11

# 段落首尾的淡入淡出。见下方拼人声轨处的注释——TTS 的尾音是被硬切的，
# 不磨掉阶跃就会在每个段落交界听到「蹬」的一声。
FADE_IN, FADE_OUT = 0.008, 0.012

# BGM 相关。曲目在 config/bgm.json，见 CLAUDE.md「BGM 约定」。
#
# **靠闪避，不靠选曲。** 2026-07-29 量过 8 首原声碟曲子在语音带（300–3400Hz）的能量
# 占比，全都是 67–82%——管弦和钢琴本来就是宽频的，指望挑出一首「不占语音带」的不现实。
# 所以用侧链压缩让 BGM 在口播出声时自动让路，选曲就可以纯按听感来。
# 参数是量出来的，不是拍的。2026-07-29 把闪避后的 BGM 单独输出来量，人声 -20.9 dBFS：
#     gain=0.22 thr=0.02 ratio=6    BGM -54.8 dBFS   低于人声 33.9dB  →  听不见，等于没加
#     gain=0.30 thr=0.06 ratio=3    BGM -41.7 dBFS   低于人声 20.8dB  →  偏轻
#     gain=0.35 thr=0.10 ratio=2.5  BGM -37.2 dBFS   低于人声 16.3dB  ←  取这个
#     gain=0.40 thr=0.15 ratio=2    BGM -34.0 dBFS   低于人声 13.1dB  →  偏抢
# 旁白类视频的常规是 BGM 低于人声 15–22dB。
#
# **别在句尾后 100ms 之内量闪避深度**——释放时间 200ms，那时压缩器还没松开，
# 量到的是过程不是稳态。初版就这么量的，得出「闪避 26dB」的错误结论。
#
# **音量控制量是「归一到目标响度」，不是「乘一个固定系数」。** 初版写的是
# volume=0.35，那个 0.35 是对着 繋ぎとめた世界（-16.9 LUFS）调出来的，只对这一首成立。
# 2026-07-29 量完全表才看清：劇伴 -14.4…-18.1，OP/ED 单曲伴奏 -7.1…-13.2，极差 11dB。
# 换成 芽ぐみの雨（-7.1）而不改系数，BGM 会凭空大 9.8dB 直接压过口播——
# 而且不会报错，只会某一期突然特别吵。所以按每首的实测 LUFS 折算增益，
# 目标值就取当初那组参数的等效值：-16.9 + 20·log10(0.35) = -26.0。
BGM_TARGET_LUFS = -26.0
BGM_THRESHOLD = 0.10   # 侧链触发门限
BGM_RATIO = 2.5        # 压缩比
BGM_ATTACK, BGM_RELEASE = 20, 200   # ms
BGM_FADE = 2.0         # 片头淡入 / 片尾淡出的秒数

# 结尾换曲（CLAUDE.md 允许「最多两首：正文一首、结尾一首」）。
# 换曲点卡在**段落边界**而不是固定秒数——BGM 在句子中间换会很突兀。
# 往回找最后一个「离片尾还有 ≥ BGM_OUTRO_MIN 秒」的段落起点：本期最后一段只有 2.35s，
# 卡在那里等于没换，退到段 20 才给得出 19.3 秒的收尾。
BGM_OUTRO_MIN = 15.0
BGM_XFADE = 3.0


def _ffprobe(path: Path, entries: str, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", str(path)]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def duration(path: Path) -> float:
    return float(_ffprobe(path, "format=duration").split(",")[0])


def frame_time(path: Path) -> float:
    """源片一帧多少秒。

    **不许写死 0.034（30fps）。** CLAUDE.md 原文那个括号是按 30fps 写的，
    但番剧片源普遍是 23.976（24000/1001），一帧 0.0417s——按 0.034 卡会把
    合格的切片判成不合格。这里从 r_frame_rate 现算。
    """
    num, den = (int(x) for x in _ffprobe(path, "stream=r_frame_rate", "v:0").split("/"))
    return den / num


def cut(clip: dict, dest: Path) -> None:
    """切一个片段，切前切后都校验（CLAUDE.md「截取守卫」，缺一不可）。

    ffmpeg 在请求时长超出源片剩余长度时**静默截断且不报错**，踩到就整条音画错位
    而没有任何提示。所以两头都要卡。负起点是同一类静默错：ffmpeg 的 `-ss`
    对负值按片尾倒数解释，切出完全不相干的画面，而时长复核照样通过
    （2026-08-16 审计 2-12，人工写 `画面:` 时间码的试听型路径会踩到）。
    """
    src = Path(clip["source"])
    want = clip["dur"]

    # 守卫 0：负起点（在守卫 1 之前，连 ffprobe 都不必跑就能拦下）
    if clip["start"] < 0:
        raise SystemExit(
            f"FAIL 截取起点为负：{src.name} start={clip['start']:.3f}s。\n"
            f"     ffmpeg 的 -ss 对负值按片尾倒数解释，会切出错误画面且不报错。"
            f"检查 `画面:` 行的时间码"
        )

    # 守卫 1：切之前
    src_dur = duration(src)
    if clip["start"] + want > src_dur + 1e-6:
        raise SystemExit(
            f"FAIL 截取越界：{src.name}\n"
            f"     请求 {clip['start']:.3f}+{want:.3f}={clip['start'] + want:.3f}s"
            f" > 源时长 {src_dur:.3f}s"
        )

    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
         "-ss", f"{clip['start']:.3f}", "-i", str(src), "-t", f"{want:.3f}",
         "-an", "-sn",
         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p",
         "-r", FPS, "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
         str(dest)],
        check=True, capture_output=True,
    )

    # 守卫 2：切之后，容差 = 源片一帧
    got = duration(dest)
    tol = frame_time(src)
    if abs(got - want) > tol:
        raise SystemExit(
            f"FAIL 切后时长不符：{dest.name}\n"
            f"     要 {want:.3f}s 实得 {got:.3f}s 差 {abs(got - want) * 1000:.0f}ms"
            f" > 1 帧 {tol * 1000:.1f}ms"
        )


def _ass_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def usable_width() -> float:
    """边距之内一行放得下多少格（一个汉字 = 1 格）。这是**硬上限**，质检按它卡。"""
    return (W - MARGIN_LR * 2) / FONT_SIZE


def per_line() -> float:
    """折行的目标宽度，比硬上限少半格。

    这半格是留给避头尾的：收尾标点即使超出目标也要留在本行，最多多占半格。
    折行目标和硬上限分开，超宽检查才不会被这个设计内的溢出误报。
    """
    return int(usable_width()) - 0.5


# 不能出现在行首的标点（避头尾）。断在这里会让标点孤零零挂在下一行开头。
_NO_LINE_START = "，。、；：？！）」』】〉》…”’·—"
_OPENING = "（「『【〈《“‘"


def char_width(ch: str) -> float:
    """字符视觉宽度，以一个汉字为 1。

    中文标点虽然是全角码位，渲染出来只占半格左右；ASCII 也是半格。
    按字数一刀切会把「31 个汉字 + 1 个逗号」误判成超宽——它实际只有 31.5 格。
    """
    if ch in _NO_LINE_START or ch in _OPENING:
        return 0.5
    return 1.0 if ord(ch) > 0x2E80 else 0.5


def line_width(s: str) -> float:
    return sum(char_width(c) for c in s)


def wrap(text: str, width: float) -> list[str]:
    """按视觉宽度折行。

    **必须自己折，不能指望 libass。** ASS 的 WrapStyle 依赖空格找断点，
    中文整句没有空格，libass 就不折——2026-07-29 实测，91 字的段落被渲成一整行
    横贯画面，两端各被切掉十几个字，而质检的「字幕不超框」还判了通过
    （它算的是「应该折成几行」，没验「实际折没折」）。
    """
    lines, cur, w = [], "", 0.0
    for ch in text:
        cw = char_width(ch)
        # 避头尾：收尾标点即使超宽也留在本行，不许挂到下一行开头
        if w + cw > width and ch not in _NO_LINE_START and cur:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines


# 句内可以断卡的地方，按优先级。只在单句一行放不下时才用。
_SUB_BREAK = "，、；：？！"
# 破折号也是断卡点。它在这个稿子的声音里**本来就是一个停顿**（见 skills/write-script/VOICE.md
# 「破折号做转折与补充，念的时候正好是一个停顿」），所以在这儿断卡与配音天然合拍。
#
# 2026-07-30 加的：「跟这种人相处最省心的就在这儿——你永远不用猜她那句"好啊"后面藏着什么。」
# 33.5 格，一行放不下，而句中一个逗号都没有，`_SUB_BREAK` 找不到断点只能让它折两行。
# 唯一的天然停顿就是那个破折号。
#
# **中文破折号是两个字符，必须等一对补全再断**，否则会留下孤零零一个「—」在行尾。
_DASH = "——"


def _split_long(sent: dict) -> list[dict]:
    """单句一行放不下时，在逗号处再切，时长按字数比例分。

    合成的单位是句子，字幕的单位是「一行放得下的一段」——两者不必相同。
    2026-07-29 实测 46 张卡里有 2 张是单句就有 40 字（「在户部告白之前，当着所有人的
    面走过去，说我从很早以前就开始喜欢你了，请和我交往吧。」），一行塞不下。
    在句中断卡不影响配音，因为音频是连续的，只是字幕换一张。
    按字数比例分时长是近似，但同一句内语速本来就基本恒定，误差在几十毫秒量级。
    """
    text = sent["text"]
    if line_width(text) <= per_line():
        return [sent]

    # 断点候选：标点之后，或破折号**成对**之后。
    # 中文破折号是两个字符，断在第一个后面会在行尾留下孤零零一个「—」。
    breaks = [i + 1 for i, ch in enumerate(text)
              if ch in _SUB_BREAK or text[max(0, i - 1):i + 1] == _DASH]
    if not breaks:                     # 整句没有可断点，只能让它折行
        return [sent]

    # **贪心断行：在放得下的断点里取最远的。**
    #
    # 原先的写法是「攒够半行且遇到标点就断」，那个 0.5 的系数是个魔法数，
    # 而且它的失败方式是错的：断点不达标就整句放弃、折成两行——
    # **而折行比稍短的卡更糟**，正是这个检查项要防的东西。
    # 2026-07-30 踩的：「跟这种人相处最省心的就在这儿——你永远不用猜…」
    # 破折号处正好 15.0 格，半行门槛 15.25，差 0.25 格，于是折了两行。
    #
    # 换成贪心之后没有可调参数：能放下就继续往后，放不下就在上一个断点收。
    pieces, start = [], 0
    while start < len(text):
        fits = [b for b in breaks
                if b > start and line_width(text[start:b]) <= per_line()]
        if fits:
            start, piece = fits[-1], text[start:fits[-1]]
        else:
            # 一个断点都放不下（两个标点之间就超宽），取最近的那个：
            # 宁可这一片超宽，也别把整句退回去折行
            nxt = [b for b in breaks if b > start]
            if not nxt:
                break
            start, piece = nxt[0], text[start:nxt[0]]
        pieces.append(piece)
    if start < len(text):              # 末尾没有标点收尾时的残余
        pieces.append(text[start:])

    if len(pieces) > 1 and line_width(pieces[-1]) < per_line() * 0.25 \
            and line_width(pieces[-2] + pieces[-1]) <= per_line():
        pieces[-2:] = [pieces[-2] + pieces[-1]]   # 尾巴太短就并回上一片
    if len(pieces) <= 1:
        return [sent]

    total = sum(line_width(p) for p in pieces) or 1.0
    out, at = [], sent["start"]
    for p in pieces:
        d = sent["duration"] * line_width(p) / total
        out.append({"text": p, "start": round(at, 3), "duration": round(d, 3)})
        at += d
    return out


def cards(take: dict, seg_dur: float) -> list[tuple[str, float, float]]:
    """把一段拆成若干张字幕卡：(文本, 段内起点, 时长)。

    **一张卡放一行，不许折行。** 整段一张卡的时候，91 字的段落要折三行糊住半个画面。
    分卡的天然单位是句子——按句合成本来就把每句的时长单独量出来了（`sentences`），
    时间轴直接拿来用，与配音严丝合缝，不需要估算。

    短句合并：连着的短句只要合起来还能塞进一行就并成一张卡，
    否则「对。」「你没听错。」这种四五个字的句子会一闪一张，反而更花。
    """
    sents = take.get("sentences")
    if not sents:                      # 旧 manifest 没这个字段，退回整段一张卡
        return [(take["text"], 0.0, seg_dur)]

    sents = [x for s in sents for x in _split_long(s)]
    out: list[tuple[str, float, float]] = []
    buf, buf_start, buf_end = "", None, None
    for s in sents:
        merged = buf + s["text"]
        if buf and line_width(merged) > per_line():
            out.append((buf, buf_start, buf_end - buf_start))
            buf, buf_start = "", None
        if buf_start is None:
            buf_start = s["start"]
        buf += s["text"]
        buf_end = s["start"] + s["duration"]
    if buf:
        # 最后一张卡挂到段末，别在段尾留一小段没字幕的空档
        out.append((buf, buf_start, max(buf_end, seg_dur) - buf_start))
    return out


def _ass_escape(text: str) -> str:
    r"""ASS 正文转义（2026-08-16 审计 2-24；二次审计 §6-3 补反斜杠）：
    `{...}` 是 override 标签块的语法，稿件文本带着花括号会被 libass 当标签
    整段吃掉——字幕内容凭空消失且不报错；换行会拆坏 Dialogue 行结构；
    `\N`/`\h` 在花括号**外**同样是标签（折行/硬空格），字面反斜杠进稿件
    会被吃成折行或丢字。花括号与反斜杠都换成全角同形字符，换行压成空格——
    全角替换在中文语境里视觉几乎无差，且和花括号同一条取舍。"""
    return (text.replace("\\", "＼")
                .replace("{", "｛").replace("}", "｝")
                .replace("\r", " ").replace("\n", " "))


def build_ass(segments: list[dict], audio: list[dict], dest: Path,
              starts: dict[int, float] | None = None) -> Path:
    """口播原文按句上屏。时间轴取自配音每句的真实时长，天然严丝合缝。

    试听型的段落起点带音乐段偏移（`starts`：段落号 → 成片起点）；
    `starts` 为 None 时段落连续累计（普通期，行为不变）。

    用 ASS 而不是 SRT：SRT 没有样式，字号边距要在滤镜里另外传，
    而 `force_style` 对中文字体名的转义在不同 ffmpeg 构建上表现不一致。
    """
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: VO,{FONT},{FONT_SIZE},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,{MARGIN_LR},{MARGIN_LR},{MARGIN_V},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines, t = [], 0.0
    for s, a in zip(segments, audio):
        if starts is not None:
            t = starts.get(s["index"], t)
        for card, start, dur in cards(a, s["duration"]):
            if not card.strip():
                raise SystemExit(
                    f"FAIL 段落 {s['index']} 有一张空字幕卡——空内容报错，"
                    f"不静默渲一张空卡（qc 的「字幕无空段」查的是段落文本，"
                    f"这里的守卫兜分卡后的空片）")
            body = r"\N".join(wrap(_ass_escape(card.strip()), per_line()))
            lines.append(
                f"Dialogue: 0,{_ass_time(t + start)},{_ass_time(t + start + dur)},"
                f"VO,,0,0,0,,{body}"
            )
        t += s["duration"]
    dest.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return dest


def _extract_loudnorm_json(stderr: str) -> dict:
    """从 `loudnorm=...:print_format=json` 的 stderr 提取测量 JSON。

    纯函数，好测。ffmpeg 把 JSON 打到 stderr（info 级别），前后混着
    `[Parsed_loudnorm_0 @ ...]` 前缀和普通日志——取第一个 `{` 到最后一个 `}`。
    """
    start, end = stderr.find("{"), stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise SystemExit("FAIL loudnorm 测量输出里没有 JSON")
    return json.loads(stderr[start:end + 1])


def _measure_loudnorm(path: Path) -> dict:
    """两遍归一的第一遍：量整条音频的 input_i/input_tp/input_lra/thresh/offset。

    为什么两遍（2026-08-16 改，试听型实测踩的坑）：loudnorm 单次调用是
    **动态模式**（逐帧增益），音乐响的时段把增益压低后，紧随其后的旁白段被
    连带压掉——实测段落 8 人声 -13.3 dBFS vs 段落 1 -8.2，差 5dB，而两段
    TTS 原始电平几乎相同。普通期音乐全程平铺没触发；试听型前景/natural
    大音量音乐 + 旁白交替必踩。第二遍用测到的值做**线性增益**重放
    （linear=true），段落间相对响度与混音时完全一致。
    """
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "info",
           "-i", str(path),
           "-af", f"loudnorm=I={LUFS}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    m = _extract_loudnorm_json(r.stderr)
    return {k: m[k] for k in ("input_i", "input_tp", "input_lra",
                              "input_thresh", "target_offset")}


def _outro_start(starts: list[float], total: float) -> float | None:
    """结尾曲从哪一秒切入。返回 None 表示这条片子太短，不值得换曲。

    卡段落边界，不卡固定秒数——BGM 在句子中间换掉会很突兀。
    取「离片尾还有 ≥ BGM_OUTRO_MIN 秒」的最后一个段落起点。
    """
    ok = [s for s in starts if total - s >= BGM_OUTRO_MIN]
    return max(ok) if ok else None


BODY_ENTRY = re.compile(r"^\s*BGM正文切入点\s*[:：]\s*([\d.]+)\s*秒", re.M)


def _body_entry_delay(episode: Path) -> float:
    """`01-topic.md` 的 `BGM正文切入点: N秒` → 正文 BGM 从第 N 秒才开始（前面留静音，给片头曲/开场让位）。

    没写就是 0（BGM 从片头就铺）。写了但格式不对直接报错——静默退回 0 是
    「写错了却不报」，等于让人以为配了实际没配。
    """
    topic = episode / "01-topic.md"
    if not topic.exists():
        return 0.0
    text = topic.read_text(encoding="utf-8")
    m = BODY_ENTRY.search(text)
    if m:
        return float(m.group(1))
    if "BGM正文切入点" in text:
        raise SystemExit("FAIL `BGM正文切入点` 格式不对，应为 `BGM正文切入点: 36秒`")
    return 0.0


BGM_LIST_HEAD = re.compile(r"^\s*BGM\s*[:：]\s*$")
BGM_ITEM = re.compile(r"^-\s*(.+?)\s*$")
BGM_ITEM_AT = re.compile(r"^(.+?)\s*@\s*([\d.]+)\s*秒$")
BGM_MID_ENTRY = re.compile(r"^\s*BGM中段切入点\s*[:：]\s*([\d.]+)\s*秒", re.M)
BGM_OUTRO_ENTRY = re.compile(r"^\s*BGM结尾切入点\s*[:：]\s*([\d.]+)\s*秒", re.M)


def _bgm_list(episode: Path) -> list[tuple[str, float | None]] | None:
    """读 `01-topic.md` 的 `BGM:` 列表——通用 N 首拼接格式。

    ```
    BGM:
      - Departures ～あなたにおくるアイの歌～ @36秒
      - 想いを巡らす100の事象
      - Planetes @470秒
    ```
    每行一首；`@N秒` = 绝对起点，不写 = 自动接上一首自然结尾（第一首自动 = 0）。
    行里带了 `@` 但格式不对直接报错——静默当自动接等于写错了却不报。
    没 `BGM:` 块返回 None，退回命名槽位（`BGM正文`/`BGM中段`/`BGM结尾`，老格式）。
    """
    topic = episode / "01-topic.md"
    if not topic.exists():
        return None
    lines = topic.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if not BGM_LIST_HEAD.match(line):
            continue
        seq: list[tuple[str, float | None]] = []
        for ln in lines[idx + 1:]:
            s = ln.strip()
            if not s:
                continue
            m = BGM_ITEM.match(s)
            if not m:
                break  # 非 `- ` 行 = 块结束
            item = m.group(1).strip()
            if mm := BGM_ITEM_AT.match(item):
                seq.append((mm.group(1).strip(), float(mm.group(2))))
            elif "@" in item:
                raise SystemExit(
                    f"FAIL `BGM:` 列表行 @ 起点格式不对：{item}\n"
                    f"     应为 `- 曲名 @N秒`")
            else:
                seq.append((item, None))
        return seq
    return None


def _seg_entry(episode: Path, slot: str, pattern: re.Pattern) -> float | None:
    """读 `BGM{slot}切入点: N秒`。写了但格式不对直接报错，不静默退回。"""
    topic = episode / "01-topic.md"
    if not topic.exists():
        return None
    text = topic.read_text(encoding="utf-8")
    m = pattern.search(text)
    if m:
        return float(m.group(1))
    if f"BGM{slot}切入点" in text:
        raise SystemExit(f"FAIL `BGM{slot}切入点` 格式不对，应为 `BGM{slot}切入点: N秒`")
    return None


def _bgm_plan(episode: Path, total: float, starts: list[float]) -> list[tuple[dict, float]] | None:
    """拼 BGM 段序列 [(曲目记录, 绝对起点)]。起点显式或自动接上一首结尾。

    没配曲目表返回 None（渲染无 BGM 版，允许状态）。`BGM:` 列表优先，
    没有就退回命名槽位。

    配了 BGM 却读不到 `番:` 字段 → 报错，不静默渲无 BGM 版（2026-08-16
    审计 2-11）。「没配曲库不拦渲染」指的是允许不配 BGM；配了却被静默丢弃
    是「写错了却不报」，与试听型路径（`_maybe_music_plan`）同一口径。
    """
    seq = _bgm_list(episode)
    anime = bgm.anime_of(episode)
    if not anime:
        if seq or bgm.episode_choice(episode, "正文"):
            raise SystemExit(
                "FAIL 01-topic.md 配了 BGM（`BGM:` 列表或 `BGM正文`）但读不到"
                " `番: ` 字段，渲染会静默丢掉全部 BGM。补 `番: <番名>` 再跑")
        return None

    seq = _bgm_list(episode)
    if seq is not None:
        plan: list[tuple[dict, float]] = []
        for name, at in seq:
            rec = bgm.resolve(anime, "列表", name)
            if at is None:
                at = plan[-1][1] + plan[-1][0]["dur"] if plan else 0.0
            plan.append((rec, at))
        return plan

    body = bgm.resolve(anime, "正文", bgm.episode_choice(episode, "正文"))
    if not body:
        return None
    plan = [(body, _body_entry_delay(episode))]
    mid = bgm.episode_choice(episode, "中段")
    if mid:
        c1 = _seg_entry(episode, "中段", BGM_MID_ENTRY)
        if c1 is None:
            raise SystemExit("FAIL 有 `BGM中段` 就必须写 `BGM中段切入点: N秒`")
        plan.append((bgm.resolve(anime, "中段", mid), c1))
    outro = bgm.episode_choice(episode, "结尾")
    if outro:
        c2 = _seg_entry(episode, "结尾", BGM_OUTRO_ENTRY)
        if c2 is None:
            c2 = _outro_start(starts, total)
        if c2 is not None:
            plan.append((bgm.resolve(anime, "结尾", outro), c2))
    return plan


def _bgm_layout(plan: list[tuple[dict, float]], total: float) -> tuple[list[float], list[int]]:
    """每段的循环输入长度 + 各 run 的首段下标。纯函数，好测。

    - 非末段、后一段起点 ≤ 本段自然结尾 → 交叉，长度 = 距后一段 + XFADE。
    - 非末段、后一段起点 > 本段自然结尾 → 停顿，长度 = 曲长（放一遍即止）。
    - 末段 → 铺到片尾，长度 = total - 起点。
    run = 连续可交叉的一段链；run 与 run 之间有停顿，靠 adelay 各自定位、amix 合并。
    """
    n = len(plan)
    lens: list[float] = []
    run_starts: list[int] = []
    for i, (rec, s) in enumerate(plan):
        if i == n - 1:
            lens.append(total - s)
        elif plan[i + 1][1] <= s + rec["dur"]:
            lens.append(plan[i + 1][1] - s + BGM_XFADE)
        else:
            lens.append(rec["dur"])
        if i == 0 or plan[i][1] > plan[i - 1][1] + plan[i - 1][0]["dur"]:
            run_starts.append(i)
    return lens, run_starts


def _bgm_bed(plan: list[tuple[dict, float]] | None, total: float, work: Path) -> Path | None:
    """铺 BGM 垫底轨：`01-topic.md` 的 BGM 序列逐首拼接，全长 `total` 秒。

    `plan` 由 run() 入口的 `_bgm_plan` 解析（配置前置校验，切片前就把曲名/
    文件/响度的错报掉）；这里只执行。每首从自己的起点播放：
    - 后一首起点 ≤ 前一首自然结尾 → acrossfade 交叉（qsin，功率恒定），
      前一首多播 XFADE 秒供重叠。
    - 后一首起点 > 前一首自然结尾 → 停顿（前一首放完即止，间隙留白）。
    最后一首循环/裁剪铺到片尾。

    没配曲目表（plan 为 None）就返回 None，渲染出无 BGM 版——这是允许的状态，不报错。
    """
    if not plan:
        return None

    for i, (rec, s) in enumerate(plan):
        if s < 0 or s >= total - BGM_FADE:
            raise SystemExit(
                f"FAIL 第 {i + 1} 首 BGM 起点 {s:g}s 越界，须在 "
                f"[0, {total - BGM_FADE:.0f})（太晚会盖掉片尾淡出）")
        if i and s <= plan[i - 1][1]:
            raise SystemExit(
                f"FAIL BGM 起点必须严格递增：第 {i + 1} 首 {s:g}s 不晚于"
                f" 第 {i} 首 {plan[i - 1][1]:g}s")

    def gain(rec: dict) -> str:
        return f"{BGM_TARGET_LUFS - rec['lufs']:.2f}dB"

    lens, run_starts = _bgm_layout(plan, total)
    start_run = set(run_starts)
    bed = work / "bgm.wav"
    fades = (f"afade=t=in:st={plan[0][1]:.3f}:d={BGM_FADE},"
             f"afade=t=out:st={max(0.0, total - BGM_FADE):.3f}:d={BGM_FADE}")

    inputs, fg = [], []
    for i, (rec, s) in enumerate(plan):
        inputs += ["-stream_loop", "-1", "-t", f"{lens[i]:.3f}", "-i", str(rec["path"])]
        v = f"volume={gain(rec)}"
        if i in start_run and s > 0:
            v += f",adelay={int(round(s * 1000))}:all=1"
        fg.append(f"[{i}:a]{v}[s{i}];")

    # 每个 run 内部 acrossfade 串成一条链；run 与 run 之间不重叠（有停顿），amix 合并。
    labels, i = [], 0
    while i < len(plan):
        j = i
        while (j + 1 < len(plan)
               and plan[j + 1][1] <= plan[j][1] + plan[j][0]["dur"]):
            j += 1
        if j == i:
            labels.append(f"[s{i}]")
        else:
            cur = f"[s{i}]"
            for k in range(i, j):
                out = f"[x{k}]"
                # 曲线用 qsin（四分之一正弦）不用默认的 tri。两首不同的曲子互不相关，
                # 功率相加而不是幅度相加：线性交叉走到中点时两边各剩半幅，
                # 合成功率只有 √(0.5²+0.5²)=0.707，听感上是个坑。实测 tri 掉 4–5dB。
                # qsin 满足 sin²+cos²=1，不相关信号的功率全程恒定。
                fg.append(f"{cur}[s{k + 1}]acrossfade=d={BGM_XFADE}:"
                          f"c1=qsin:c2=qsin{out};")
                cur = out
            labels.append(cur)
        i = j + 1

    if len(labels) == 1:
        fg.append(f"{labels[0]}{fades}[out]")
    else:
        fg.append("".join(labels)
                  + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
                  + f"{fades}[out]")

    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
           *inputs, "-filter_complex", "".join(fg),
           "-map", "[out]", "-ac", "1", "-ar", "48000",
           "-c:a", "pcm_s16le", str(bed)]
    subprocess.run(cmd, check=True, capture_output=True)

    print("  BGM  " + " → ".join(
        f"{rec['name']}（{s:.0f}s 起）" for rec, s in plan))
    return bed


# 前景↔BGM 音量切换的斜坡秒数。阶跃切换会「登」；0.5s 对听感平滑且
# 短到不影响任何语义边界（段落/音乐段边界都远大于 0.5s）。
VOL_RAMP = 0.5


def _runs(events: list[dict]) -> list[list[dict]]:
    """把「曲目内位置连续」的事件合并成一个 run。

    连续 = 前一事件 t1 与后一事件 t0 相同（同一音轨继续播放，只是音量状态
    变化）。run 内用音量表达式做斜坡过渡（审查 F2：各事件独立成段再做
    淡入淡出会在交界处剪出 V 形断口，违反「不重新切一份音频」）。
    """
    runs: list[list[dict]] = []
    for ev in events:
        if runs and abs(runs[-1][-1]["t1"] - ev["t0"]) < 1e-6:
            runs[-1].append(ev)
        else:
            runs.append([ev])
    return runs


def _vol_expr(events: list[dict], lufs: float) -> str:
    """run 内音量表达式：每事件一个目标增益，切换处 VOL_RAMP 秒线性斜坡。

    **表达式必须用线性域，不能用 dB 值。** volume 滤镜的 dB 后缀只对字面量
    有效（volume=-4.00dB 是 -4dB），放进表达式（volume='if(...)dB'）直接解析
    报错，而裸数字（volume='-4.00'）被当成**线性倍率**——-4.00 是 4 倍放大、
    相位反转，-15.90 比 -4.00 响得多，前景/BGM 关系完全反转（2026-08-16
    二轮审查实测：BGM 平台比前景响 8dB、整床响 20-36dB）。
    所以这里把每个平台的 dB 增益预算为线性常量 pow(10, dB/20)，斜坡在
    线性值之间插值（对音量来说线性插值就是对数域的 dB 斜坡，听感平滑）。
    """
    targets = [(ev["t1"] - ev["t0"],
                10 ** ((BGM_TARGET_LUFS - lufs if ev["vol"] == "bgm"
                        else FOREGROUND_LUFS - lufs) / 20))
               for ev in events]
    if len(targets) == 1:
        return f"{targets[0][1]:.4f}"
    parts, t, closes = [], 0.0, 0
    for i in range(len(targets) - 1):
        dur, g = targets[i]
        ng = targets[i + 1][1]
        switch = t + dur
        parts.append(f"if(lt(t,{switch:.3f}),{g:.4f},"
                     f"if(lt(t,{switch + VOL_RAMP:.3f}),"
                     f"{g:.4f}+({ng:.4f}-{g:.4f})*(t-{switch:.3f})/{VOL_RAMP:.2f},")
        t = switch + VOL_RAMP
        closes += 2
    return "".join(parts) + f"{targets[-1][1]:.4f}" + ")" * closes


def _music_bed(plan: dict, work: Path) -> Path | None:
    """试听型音乐床：按 `music.build_timeline` 的事件序列生成整条音乐轨。

    每个 run（曲目内位置连续的事件链）切成一个输入段，音量用表达式自动化
    （前景 -14 / BGM -26，切换处 0.5s 斜坡）；淡入淡出只加在真正跳点的两端
    （run 开头淡入、run 末尾淡出）。natural（自然收尾）不淡出——
    cue 原则「让歌曲自然结束」。事件按成片起点 adelay 后 amix 合并。
    侧链闪避在混音阶段统一做（前景段无口播触发，自然不被压）。
    """
    inputs, fg, labels = [], [], []
    n = 0
    runs_by_track = {tr["name"]: _runs(tr["events"]) for tr in plan["tracks"]}
    for tr in plan["tracks"]:
        for run in runs_by_track[tr["name"]]:
            t0, t1 = run[0]["t0"], run[-1]["t1"]
            dur, at = t1 - t0, run[0]["at"]
            inputs += ["-ss", f"{t0:.3f}", "-t", f"{dur:.3f}",
                       "-i", str(tr["path"])]
            out_s = f"s{n}"
            chain = (f"volume='{_vol_expr(run, tr['lufs'])}':eval=frame,"
                     f"aresample=48000,afade=t=in:st=0:d={VOL_RAMP:.2f}")
            if run[-1]["vol"] != "natural":
                chain += (f",afade=t=out:st={max(0.0, dur - VOL_RAMP):.3f}"
                          f":d={VOL_RAMP:.2f}")
            fg.append(f"[{n}:a]{chain},"
                      f"adelay={int(round(at * 1000))}:all=1[{out_s}];")
            labels.append(f"[{out_s}]")
            n += 1
    if not labels:
        return None
    fg.append("".join(labels)
              + f"amix=inputs={n}:duration=longest:normalize=0[out]")
    bed = work / "music-bed.wav"
    print(f"  音乐床 {n} 个 run…", file=sys.stderr)
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
           *inputs, "-filter_complex", "".join(fg),
           "-map", "[out]", "-ac", "1", "-ar", "48000",
           "-c:a", "pcm_s16le", str(bed)]
    subprocess.run(cmd, check=True, capture_output=True)
    print("  音乐床 " + " + ".join(
        f"{name}({len(runs)}段)" for name, runs in runs_by_track.items()))
    return bed


def _maybe_music_plan(episode: Path, manifest: dict) -> dict | None:
    """试听型判定：稿子有 `音乐段` 块就构建音乐时间轴，否则返回 None（普通 BGM 路径）。

    换期通用：只依赖稿子的标准格式（`音乐段`/`音乐:`/`过渡:`）与 bgm.json 的
    曲库结构，不绑任何一部番。普通期的行为与旧版完全一致。
    """
    from . import music as music_mod
    script = episode / "02-script.md"
    if not script.exists() or not music_mod.parse_script_music(script):
        return None
    anime = bgm.anime_of(episode)
    if anime is None:
        raise SystemExit(f"FAIL 试听型稿子需要 01-topic.md 的 `番: `字段")
    block = bgm.load(anime)
    if not block or "tracks" not in block:
        raise SystemExit(
            f"FAIL 试听型稿子需要「{anime}」的曲库（config/bgm.json 的 tracks）")
    return music_mod.build_timeline(episode, manifest, block)


def _still_frames(episode: Path, plan: dict,
                  parts_by_seg: dict[int, list[Path]], work: Path
                  ) -> list[tuple[Path, float, float]]:
    """音乐段画面。

    前景音乐段（blocks）与结尾自然收尾段（natural 事件）都要画面——
    声明了 `画面:` 的段从源片切实画面（OP/ED/插曲/回忆场景，2026-08-16
    用户拍板：不允许静止画面）；没声明的回退为上一段最后一帧定格。
    natural 段继承同曲目最后前景块的画面源并连续接续。
    返回 [(视频段, 时长, 成片起点)]。
    """
    seg_starts = sorted((s["index"], s["start"]) for s in plan["segments"])
    # 画面占位段 = 前景块 + natural 事件（at 在最后一个段落之后）。
    # natural 段继承同曲目最后一个前景块的画面源并**连续接续**（起点 += 前景时长），
    # 保证同一曲目的画面与音乐一样不重切。
    visual: list[tuple[float, float, str | None]] = []
    for b in plan["blocks"]:
        visual.append((b["start"], b["dur"], b.get("visual")))
    last_seg_end = max(s["start"] + s["dur"] for s in plan["segments"])
    for tr in plan["tracks"]:
        for ev in tr["events"]:
            if ev["vol"] == "natural" and ev["at"] >= last_seg_end - 1e-6:
                vis = None
                fgs = [b for b in plan["blocks"] if b["title"] == tr["name"]]
                if fgs and fgs[-1].get("visual"):
                    ep_tag, start_s = fgs[-1]["visual"].rsplit(" ", 1)
                    # 统一走 _hhmmss_to_sec（审查 B3'：float() 只认纯秒数，
                    # 换期写 `1:33.75` 冒号格式会 ValueError 崩溃）
                    vis = f"{ep_tag} {_hhmmss_to_sec(start_s) + fgs[-1]['dur']:.2f}"
                visual.append((ev["at"], ev["t1"] - ev["t0"], vis))
    visual.sort(key=lambda v: v[0])

    out = []
    for k, (start, dur, vis) in enumerate(visual):
        # 声明了 `画面:` 的段从源片切；否则上一段最后一帧定格。
        if vis:
            ep_tag, start_s = vis.rsplit(" ", 1)
            src = _source_path(episode, ep_tag)
            clip = {"source": str(src), "start": _hhmmss_to_sec(start_s),
                    "dur": dur}
            seg_path = work / f"visual-{k:02d}.mp4"
            cut(clip, seg_path)
            out.append((seg_path, dur, start))
            continue
        prev = [idx for idx, st in seg_starts if st <= start + 1e-6]
        if not prev:
            # 片头音乐段（成片第 0 秒）前面没有段落——取第一段的第一帧预告画面
            first = min(seg_starts, key=lambda x: x[1])[0]
            src, sseof = parts_by_seg[first][0], False
        else:
            src, sseof = parts_by_seg[prev[-1]][-1], True
        frame = work / f"still-{k:02d}.png"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
             *(["-sseof", "-0.05"] if sseof else []),
             "-i", str(src), "-frames:v", "1", str(frame)],
            check=True, capture_output=True,
        )
        still = work / f"still-{k:02d}.mp4"
        # 抽帧存 PNG 而非 JPEG（审查 B1：JPEG 是 yuvj420p 全范围，
        # -pix_fmt 只打标签不做范围转换，concat 后整轨按限幅解释，
        # 黑场压扁白场溢出）；PNG 的 RGB→限幅 YUV 转换定义良好，
        # 再加 -vf format=yuv420p 显式走滤镜转换双保险。
        # 定格段用 faster 而非 PRESET(medium)：单帧循环帧间冗余极大，
        # medium 编码 285s 定格要几分钟而 faster 只要几十秒，观感无差别。
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
             "-loop", "1", "-framerate", FPS, "-i", str(frame),
             "-t", f"{dur:.3f}",
             "-vf", "format=yuv420p",
             "-c:v", "libx264", "-preset", "faster", "-crf", CRF,
             "-pix_fmt", "yuv420p", "-r", FPS, str(still)],
            check=True, capture_output=True,
        )
        print(f"  定格段 {k + 1}/{len(visual)}（{dur:.0f}s）", file=sys.stderr)
        out.append((still, dur, start))
    return out


def _source_path(episode: Path, ep_tag: str) -> Path:
    """`S01E01` → 该集片源路径（data/library/sources.json）。"""
    sources = json.loads((paths.ROOT / "data" / "library" / "sources.json")
                         .read_text(encoding="utf-8"))
    anime = bgm.anime_of(episode)
    if anime is None:
        raise SystemExit(f"FAIL 音乐段画面需要 01-topic.md 的 `番: `字段")
    rec = sources.get(anime, {}).get(ep_tag)
    if not rec:
        raise SystemExit(f"FAIL sources.json 里没有 {anime}/{ep_tag} 的片源")
    return paths.ROOT / rec["path"]


def _hhmmss_to_sec(s: str) -> float:
    """`0:00` / `1:33.75` / `93.75` → 秒。负数当场报错——它会绕过截取守卫的
    上界检查（`-2 > src_dur` 为假），切出片尾倒数的错误画面（2026-08-16 审计 2-12）。"""
    parts = [float(x) for x in s.split(":")]
    sec = sum(p * (60 ** (len(parts) - 1 - i)) for i, p in enumerate(parts))
    if sec < 0:
        raise SystemExit(f"FAIL 画面时间码是负数：{s}。检查 `画面:` 行写对了没有")
    return sec


def run(episode: Path, keep: bool = False) -> Path:
    plan_path = episode / "04-clips.approved.json"
    if not plan_path.exists():
        raise SystemExit(
            f"FAIL 缺 {plan_path}\n"
            f"     04-clips.json 是机器排的，要人抽检过时间码、另存为 approved 版才能渲染"
        )
    audio_dir = episode / "03-audio"
    manifest = json.loads((audio_dir / "manifest.json").read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segments = plan["segments"]

    bad = [s["index"] for s in segments if s["status"] != "ok"]
    if bad:
        raise SystemExit(f"FAIL 这些段落状态不是 ok，不许渲染：{bad}")

    # 段级不变量第二道闸（approve 是第一道）：防绕过 approve 直接改 approved 文件。
    violations = verify_alignment(segments, manifest["segments"])
    if violations:
        raise SystemExit("FAIL 段级时长不对齐（人审改过画面没 refit？）：\n  " + "\n  ".join(violations))

    # 配置前置校验（fail-fast，2026-08-18 复盘②）：曲名打错、曲目文件不存在、
    # 缺 lufs、音乐段越界——这些只读 JSON 就能判，而切片要几分钟。先判再切，
    # 不许切完半片才炸。acc 与后面 voice 的实测等长（concat 不改时长），
    # 供 _bgm_plan 的结尾切入点自动定位用。
    starts, acc = [], 0.0
    for s in manifest["segments"]:
        starts.append(acc)
        acc += s["duration"]
    music_plan = _maybe_music_plan(episode, manifest)
    bgm_plan = None if music_plan is not None else _bgm_plan(episode, acc, starts)

    work = Path(tempfile.mkdtemp(prefix="render-", dir=str(episode)))
    try:
        # 1. 逐个切片（两道守卫在 cut 里，多线程并行加速）
        clips_flat = [c for s in segments for c in s["clips"]]
        n = len(clips_flat)
        jobs = [(c, work / f"c{i:03d}.mp4", i) for i, c in enumerate(clips_flat, 1)]
        workers = min(6, os.cpu_count() or 1)
        done_cnt = 0
        def _do_cut(j):
            nonlocal done_cnt
            cut(j[0], j[1])
            done_cnt += 1
            print(f"\r  切片 {done_cnt}/{n}", end="", flush=True)
            return j[1]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(_do_cut, jobs))
        print()

        # 2. 拼画面轨。
        #
        # 段落切片、定格段、OP 画面段全部用同一组编码参数
        # （cut 的 -vf/-r 与定格段的 -framerate/-r 都来自同一常量），
        # 所以统一走 concat demuxer + `-c copy`，秒级完成，不做全量重编码。
        # music_plan 已在 run() 入口解析（配置前置校验）。
        video = work / "video.mp4"
        if music_plan is not None:
            parts_by_seg: dict[int, list[Path]] = {}
            i = 0
            for s in segments:
                parts_by_seg[s["index"]] = parts[i:i + len(s["clips"])]
                i += len(s["clips"])
            stills = _still_frames(episode, music_plan, parts_by_seg, work)
            # 按成片时间交错：段落切片序列与定格段按起点排序后依次 concat。
            seg_start = {s["index"]: s["start"] for s in music_plan["segments"]}
            elems: list[tuple[float, list[Path]]] = [
                (seg_start[s["index"]], parts_by_seg[s["index"]])
                for s in segments]
            for still, _dur, start in stills:
                elems.append((start, [still]))
            elems.sort(key=lambda e: e[0])
            concat_parts = [f for _at, files in elems for f in files]
        else:
            concat_parts = parts
        lst = work / "concat.txt"
        lst.write_text("".join(f"file '{p.name}'\n" for p in concat_parts),
                       encoding="utf-8")
        print(f"  拼画面轨（{len(concat_parts)} 段）…", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat",
             "-safe", "0", "-i", str(lst), "-c", "copy", str(video)],
            check=True, capture_output=True, cwd=work,
        )

        # 3. 拼人声轨。**每段首尾各加一小段淡入淡出再拼。**
        #
        # IndexTTS 的 mel 解码遇到停止符就收，尾音是被硬切掉的：2026-07-29 实测，
        # 段 1 最后 5ms 的包络峰值还有 6409，下一段开头是 1——从满音量直接掉到静音，
        # 这个包络阶跃就是听感上那声「蹬」。段落越响越明显。
        # 12ms 短到听不出是淡出，但足以把阶跃磨掉；不改时长，所以排片时间轴不受影响。
        faded = []
        for s in manifest["segments"]:
            src = (audio_dir / s["file"]).resolve()
            dst = work / f"v{s['index']:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
                 "-af", f"afade=t=in:st=0:d={FADE_IN},"
                        f"afade=t=out:st={max(0.0, s['duration'] - FADE_OUT):.4f}:d={FADE_OUT}",
                 "-c:a", "pcm_s16le", str(dst)],
                check=True, capture_output=True,
            )
            faded.append(dst)

        # 3.5 拼人声轨。
        # 普通期：段落 concat 连续拼接（旧行为）。
        # 试听型：段落按 music_plan 的成片起点 adelay 到时间轴上再 amix——
        # 音乐段（前景/BGM）占的成片时间必须留空，旁白只在段落位置出声
        # （审查 F1：人声不接时间轴 = 旁白全挤在 0-222s，前景音乐被持续侧链压制）。
        if music_plan is not None:
            seg_start = {s["index"]: s["start"] for s in music_plan["segments"]}
            vfg, vin = [], []
            for i, s in enumerate(manifest["segments"]):
                vin.append(["-i", str(faded[i])])
                vfg.append(f"[{i}:a]adelay={int(round(seg_start[s['index']] * 1000))}"
                           f":all=1[v{i}];")
            vfg.append("".join(f"[v{i}]" for i in range(len(vin)))
                       + f"amix=inputs={len(vin)}:duration=longest:normalize=0"
                       + "[outv]")
            voice = work / "voice.wav"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                 *[x for pair in vin for x in pair],
                 "-filter_complex", "".join(vfg),
                 "-map", "[outv]", "-ac", "1", "-ar", "48000",
                 "-c:a", "pcm_s16le", str(voice)],
                check=True, capture_output=True, cwd=work,
            )
        else:
            alst = work / "audio.txt"
            alst.write_text("".join(f"file '{p.name}'\n" for p in faded),
                            encoding="utf-8")
            voice = work / "voice.wav"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat",
                 "-safe", "0", "-i", str(alst), "-c", "copy", str(voice)],
                check=True, capture_output=True, cwd=work,
            )

        # 4. 音乐（可选）。试听型走音乐床（前景/BGM/自然播放事件序列）；
        # 普通期走全片 BGM 铺底（旧路径，行为不变）。
        total = duration(voice)
        if music_plan is not None:
            bed = _music_bed(music_plan, work)
        else:
            bed = _bgm_bed(bgm_plan, total, work)
        voice_raw = voice  # 4.5 要查混音前的人声轨（B1'：混音总长测不出人声位置错）
        if bed:
            # sidechaincompress 的输出时长 = **先 EOF 的输入**（ffmpeg 8 实测，
            # 不是主输入时长）——试听型音乐床 796s、人声 510s，侧链先 EOF 会把
            # 音乐床尾部整段截掉（三审 S1'：natural 收尾 510-796s 静音消失）。
            # 侧链分支先 pad 到全轴：普通期 bed==voice 恒等长不踩，统一 pad 无害；
            # pad 出来的静音不触发压缩，旁白结束后的音乐段自然不被压。
            want = (music_plan["total_duration"] if music_plan is not None
                    else total)
            mixed = work / "mixed.wav"
            # sidechaincompress：以人声为触发，口播一出声就把 BGM 压下去。
            # 前景段旁白停、无触发信号，自然不被压——同一床两个状态，机制复用。
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                 "-i", str(bed), "-i", str(voice),
                 "-filter_complex",
                 f"[1:a]apad=whole_dur={want:.3f},asplit=2[sc][vo];"
                 f"[0:a][sc]sidechaincompress=threshold={BGM_THRESHOLD}:ratio={BGM_RATIO}:"
                 f"attack={BGM_ATTACK}:release={BGM_RELEASE}:makeup=1[duck];"
                 f"[duck][vo]amix=inputs=2:duration=longest:normalize=0[out]",
                 "-map", "[out]", "-ac", "1", "-ar", "48000",
                 "-c:a", "pcm_s16le", str(mixed)],
                check=True, capture_output=True,
            )
            voice = mixed

        # 4.5 试听型不变量：三条音视频轨分别校验时长，防「有轨没接时间轴」。
        # 分开查（二轮审查 B1'）：混音后总长 = amix longest = max(bed, voice)，
        # 人声位置错（挤在 0-222s）不改总长，查混音结果是测不出 F1 类回归的。
        # 普通期不适用（没有时间轴）。
        if music_plan is not None:
            want = music_plan["total_duration"]
            # 人声轨（混音前）必须铺到最后一段落结束；音乐床必须铺满全轴；
            # 视频轨按帧量化上界容差（每段时长向上取整到帧，最多多 1 帧/段）。
            voice_end = max(s["start"] + s["dur"]
                            for s in music_plan["segments"])
            video_tol = len(concat_parts) * frame_time(video) + 0.05
            checks = [("人声轨", voice_raw, voice_end, 0.05)]
            if bed:
                checks.append(("音乐床", bed, want, 0.05))
            checks.append(("混音轨", voice, want, 0.05))
            checks.append(("视频轨", video, want, video_tol))
            for name, p, want_len, tol in checks:
                got = duration(p)
                if abs(got - want_len) > tol:
                    raise SystemExit(
                        f"FAIL {name}时长 {got:.1f}s != 应达 {want_len:.1f}s"
                        f"（容差 {tol:.2f}s）——有轨没接时间轴")

        # 5. 字幕 + 合成 + 响度归一
        # 试听型的段落起点带音乐段偏移（starts），普通期 None = 段落连续（旧行为）。
        seg_starts = None
        if music_plan is not None:
            seg_starts = {s["index"]: s["start"]
                          for s in music_plan["segments"]}
        ass = build_ass(segments, manifest["segments"], work / "vo.ass",
                        starts=seg_starts)
        print(f"  最终合成（13 分钟重编码，约 5-10 分钟）…", file=sys.stderr)
        # 响度归一两遍（2026-08-16 改）：先量整条音频，再线性增益重放——
        # 单次 loudnorm 动态模式会压掉音乐段后的旁白（实测差 5dB），
        # linear=true + measured_* 保证段落间相对响度与混音时一致。
        loud = _measure_loudnorm(voice)
        out = episode / "05-final.mp4"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
             "-i", str(video), "-i", str(voice),
             # ass 滤镜要在工作目录里跑，路径带冒号/方括号会把滤镜串解析炸掉
             "-vf", f"ass={ass.name}",
             "-af", (f"loudnorm=I={LUFS}:TP={TRUE_PEAK}:LRA={LRA}:linear=true"
                     f":measured_I={loud['input_i']}"
                     f":measured_TP={loud['input_tp']}"
                     f":measured_LRA={loud['input_lra']}"
                     f":measured_thresh={loud['input_thresh']}"
                     f":offset={loud['target_offset']}"),
             "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-map", "0:v:0", "-map", "1:a:0", "-shortest",
             "-movflags", "+faststart", str(out.resolve())],
            check=True, capture_output=True, cwd=work,
        )
        return out
    finally:
        if keep:
            print(f"  中间文件留在 {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--keep", action="store_true", help="保留中间切片，便于排查")
    a = ap.parse_args()

    out = run(a.episode, a.keep)
    v = duration(out)
    plan = json.loads((a.episode / "04-clips.approved.json").read_text(encoding="utf-8"))
    # 审查 B7：试听型的成片基准是音乐时间轴，不是 04-clips 的段落排片
    music_plan = _maybe_music_plan(a.episode, json.loads(
        (a.episode / "03-audio" / "manifest.json").read_text(encoding="utf-8")))
    base = music_plan["total_duration"] if music_plan else plan["total_duration"]
    print(f"OK  {out}  {v:.3f}s  （时间轴 {base:.3f}s，"
          f"差 {abs(v - base) * 1000:.0f}ms）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
