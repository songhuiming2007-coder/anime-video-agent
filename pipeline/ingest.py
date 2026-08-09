"""把一个视频文件变成可检索的字幕索引。

    视频 → 探测内封字幕 ─┬─ 有 → ffmpeg 提取
                        └─ 无 → ASR 转录该文件
                      → subindex 建索引

字幕的轴**必须**是照这个视频文件打的。跨版本配对（BD 字幕配 TV 视频等）的差异是
中间插入与删除、不是恒定偏移，而失败是静默的：视频照常渲染，只是全程画面对不上话。

同发布的外挂字幕（压制组把 `.ass` 单独随片发）是允许的第三种来源，但**必须先过 verify**：
文件名对得上只能证明打包时放在一起，证明不了轴没错——只有拿视频自己的声音去对才能证伪。
判据见 CLAUDE.md。

用法：
    python -m pipeline.ingest intact <视频>...                # 片源完整性，重新拷贝后先跑这个
    python -m pipeline.ingest probe  <视频>
    python -m pipeline.ingest subs   <视频> -o out.ass
    python -m pipeline.ingest verify <视频> <字幕>          # 外挂字幕对轴校验
    python -m pipeline.ingest run    <视频> --anime 春物 --season 1 --episode 3
    python -m pipeline.ingest phase0 <视频>... --anime 春物 --season 2   # 整季入库
"""

from __future__ import annotations

from . import paths  # 必须在任何 HF 库之前：把模型缓存钉到 SSD

import argparse
import json
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .subindex import INDEX_DIR, build, _clean

# 字幕轨优先级：简体 > 未标注中文 > 繁体。日文与"注释/staff"轨永不选中。
LANG_ZH = {"chi", "zho", "zh", "chs", "cht", "zh-cn", "zh-hans", "zh-hant"}
SIMPLIFIED = re.compile(r"(简|簡体|chs|hans|gb)", re.I)
TRADITIONAL = re.compile(r"(繁|正體|cht|hant|big5)", re.I)
JAPANESE = re.compile(r"(日本語|日文|jpn|jp)", re.I)
NON_DIALOGUE = re.compile(r"(注释|註釋|comment|staff|songs?|op|ed|karaoke)", re.I)


@dataclass
class SubTrack:
    index: int
    codec: str
    language: str
    title: str

    def score(self) -> int:
        """越大越优先。负分表示不可用。"""
        blob = f"{self.language} {self.title}"
        if JAPANESE.search(blob) and not SIMPLIFIED.search(blob):
            return -100
        if NON_DIALOGUE.search(self.title):
            return -50
        s = 0
        if self.language.lower() in LANG_ZH:
            s += 10
        if SIMPLIFIED.search(blob):
            s += 5
        if TRADITIONAL.search(blob):
            s -= 2  # 繁体可用但不优先，转简成本另算
        if self.codec in ("ass", "ssa"):
            s += 1  # ASS 通常带说话人字段，信息更全
        return s


def probe(video: Path) -> list[SubTrack]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index,codec_name:stream_tags=language,title",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    out = []
    for s in json.loads(r.stdout).get("streams", []):
        tags = s.get("tags", {})
        out.append(SubTrack(
            index=s["index"],
            codec=s.get("codec_name", ""),
            language=tags.get("language", ""),
            title=tags.get("title", ""),
        ))
    return out


def extract(video: Path, dest: Path, track: SubTrack | None = None) -> Path:
    """提取内封字幕。track 为空则自动挑最优轨。"""
    if track is None:
        tracks = [t for t in probe(video) if t.score() >= 0]
        if not tracks:
            raise LookupError("无可用中文内封字幕轨")
        track = max(tracks, key=lambda t: t.score())

    dest.parent.mkdir(parents=True, exist_ok=True)
    # -map 用的是流的绝对 index，需换算成 s:N 序号
    sub_indices = [t.index for t in probe(video)]
    rel = sub_indices.index(track.index)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-map", f"0:s:{rel}", "-c:s", "copy" if dest.suffix == ".ass" and
         track.codec in ("ass", "ssa") else "text", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def transcribe(video: Path, dest: Path) -> Path:
    """无内封字幕时的兜底：对这个文件本身做 ASR。"""
    from .asr import run as asr_run  # 延迟导入，未装 ASR 依赖时不影响提取路径

    return asr_run(video, dest)


# ---------- 外挂字幕对轴校验 ----------

VERIFY_WINDOW = 60.0    # 每个采样窗口的秒数
VERIFY_SAMPLES = 2      # 采样窗口数。取两处，避开只有片头对上的情况
VERIFY_SHIFT = 120.0    # 对照窗偏移量。**必须大于窗长**，否则两窗重叠、对照组不干净
VERIFY_MARGIN = 0.15    # 对齐窗要比对照窗低这么多才算有信号
VERIFY_CEILING = 0.80   # 绝对上限，兜住整段听不出东西的情况
# ASR 输出比字幕长这么多倍就判定「这次转录坏了」，弃用该窗，**不算轴对不上**。
# 同一段语音，字幕和转录的长度本该相当；长出两倍以上只有一种成因——Whisper 进了
# 复读循环。2026-07-30 实测 S2E13 的 953s 窗吐出「pada pada pada…」，CER 1297%。
# 门禁于是报「轴对不上」，可真相是「ASR 坏了」——把这两件事混为一谈，
# 结果就是一集轴完全正确的片源被挡在索引外面，而且换个 seed 又能过，看起来像玄学。
VERIFY_BABBLE = 2.0


INTACT_DURATION_TOLERANCE = 2.0
# 真残缺是硬断：零填充的空洞打穿 EBML/MPEG 结构，处理到的时长会明显短于
# ffprobe 报的时长，不是差一两秒的量级。2 秒够吸收容器时间戳取整、
# 尾包边界这类正常噪声，同时远小于任何一次真正的截断缺口。


def intact(video: Path) -> tuple[bool, str]:
    """片源完整性：ffmpeg 只解复用地走一遍全片，判据是**处理到的时长够不够**，
    不是「有没有报错」。

    **不要用「ffprobe 能不能打开」来判完整。** 没下完的文件是稀疏文件——中间是零填充的
    空洞，文件头往往还在，ffprobe 照样读出时长，只有解到空洞处才报错。
    2026-07-29 就这么误判过：38 集里有 16 集"通过"了文件头检查，实际全都残缺。
    下面仍然会解复用全片，ffprobe 只用来拿一个「应该处理到多少秒」的参照值，
    不是拿它「能打开」这件事本身当判据。

    **也不要用「实占块 ÷ 逻辑大小」。** 那是本函数最初的实现，2026-07-29 推翻——
    拿 qBittorrent 的 piece 级校验当真值比对 89 个 mkv，块判据漏放 14/72 个残缺文件，
    最大高估 83pp（真实 16.7% 的文件读出 100.1%）。成因见 CLAUDE.md 同名小节：
    piece 校验失败后下载器只在逻辑上把该片标回缺失，磁盘上的字节还留着。

    块比例只当**前置快筛**：少块一定是残缺（不会凭空少块），够块不代表完整。
    快筛能免掉大部分文件的全片扫描，实测过判据的文件平均 0.88s。

    **判据也不是「stderr 是否为空」。** 2026-08-07 实测东京喰种 12/12 集在
    `-v error` 下都吐一行 `Error parsing Opus packet header`，集中在片尾最后
    ~90s 内，退出码 0，`-progress` 显示处理到的时长与 ffprobe 报的时长差
    <0.1s——是该压制组 Opus 编码器收尾时写出的一个畸形包，libavcodec 的包头
    解析器报错但没打断解复用，属于良性噪声，不是本函数要防的「文件没下完」。
    旧版把这两件事混为一谈：凡是 stderr 非空一律判残缺，等于把「有没有产生
    任何警告」当成了「文件是否完整」的代理，而这条检查真正该测的是后者。
    """
    # 批量校验时一个路径写错不该打断整批：报 FAIL，让其余文件照常跑完
    try:
        st = video.stat()
    except OSError as e:
        return False, f"读不到：{e.strerror}"
    if st.st_size == 0:
        return False, "0 字节"
    pct = st.st_blocks * 512 / st.st_size
    if pct < 0.99:  # 单向快筛：只在判「残缺」时可信
        return False, f"实占块仅 {pct:.0%}，未下完"

    try:
        expected = _video_duration(video)
    except (subprocess.CalledProcessError, ValueError):
        return False, "ffprobe 读不出时长，容器已损坏"

    # -c copy 不解码，只走容器：零填充的空洞会直接打断 EBML/MPEG 结构，
    # 足以抓出所有残缺，比全解码快一个量级。实测 89 个文件零漏放零误杀。
    # -progress 把实际处理到的时长吐到 stdout，用来跟 expected 比。
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-progress", "pipe:1",
         "-i", str(video), "-c", "copy", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    processed = 0.0
    for ln in proc.stdout.splitlines():
        # ffmpeg 的 -progress 有个多年未修的命名 bug：out_time_ms 实际是微秒
        # （与 out_time_us 数值相同），按名字当毫秒用会把时长算小一千倍。
        if ln.startswith("out_time_us="):
            v = ln.split("=", 1)[1].strip()
            if v.lstrip("-").isdigit():
                processed = int(v) / 1_000_000

    err = proc.stderr.strip()
    if proc.returncode != 0 or expected - processed > INTACT_DURATION_TOLERANCE:
        # 剥掉 ffmpeg 的模块前缀「[matroska,webm @ 0x7d4c38000] 」，它占掉半行又不含信息
        first = (re.sub(r"^\[[^\]]+@\s*0x[0-9a-f]+\]\s*", "", err.splitlines()[0])
                  if err else f"退出码 {proc.returncode}")
        return False, f"只处理到 {processed:.0f}/{expected:.0f}s：{first[:36]}"
    note = "（有非致命解码警告，未截断处理）" if err else ""
    return True, f"{st.st_size / 2**20:.0f}M 全片解复用通过{note}"


def _video_duration(video: Path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


KANA = re.compile(r"[ぁ-んァ-ヴー]")   # 双语字幕里日文行的判据。汉字两边都有，假名只有日文有


def _subs_in_window(sub_path: Path, start: float, end: float) -> str:
    """字幕在 [start, end) 内的**日文**文本，拼成一串。

    这里必须取日文而不是中文：番剧音轨是日语，中文那条是译文。
    拿译文去跟 ASR 出来的日语比字符错误率，无论轴对不对都会 100% 不过——
    2026-07-29 第一版 verify 就是这么误判的。
    """
    import pysubs2

    out = []
    for ev in pysubs2.load(str(sub_path)):
        if ev.is_comment:
            continue
        t0 = ev.start / 1000.0
        if start <= t0 < end and KANA.search(_clean(ev.text)):
            out.append(_clean(ev.text))
    return "".join(out)


def _clip_audio(video: Path, dest: Path, start: float, dur: float) -> Path:
    """切一段 16k 单声道音频。-ss 放在 -i 前面走快速定位。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
         "-i", str(video), "-t", f"{dur:.3f}",
         "-vn", "-ar", "16000", "-ac", "1", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _hear(clip_path: Path, mlx_whisper) -> str:
    """把一段音频转成文本，并挡住 Whisper 在尾部静音上的重复幻觉。

    2026-07-29 实测：春物 S2E02 的 905s 窗，对话部分与字幕逐字吻合，但 Whisper 在
    片尾静音处进入重复循环，吐出「俺し続きました俺」并**把时间码写到 75.5s——
    片段本身只有 60s**，后面还跟着 80 多个 76.0-76.0 的空段。幻觉长度每次不同，
    同一时间点两次跑出 375% 和 166% 的 CER，把一集轴完全正确的片源判成了对不上。

    两道防线，都针对成因而不是调阈值：

    1. `condition_on_previous_text=False`——循环的成因就是模型把自己的输出当上文喂回去
    2. 丢掉结束时间超出片段长度的段落。这是**可证伪的**判据：60 秒的音频里
       不可能有结束于 75 秒的语音，凡是超出的一定是幻觉
    """
    from .asr import REPO  # 与 verify() 一样延迟导入：模型库很重，不进 import 时开销

    res = mlx_whisper.transcribe(
        str(clip_path), path_or_hf_repo=REPO, language="ja", verbose=None,
        condition_on_previous_text=False,
    )
    kept = [s["text"] for s in res.get("segments", [])
            if s.get("end", 0.0) <= VERIFY_WINDOW + 1.0]   # +1s 容忍边界上的取整
    return "".join(kept).strip() if kept else res["text"].strip()


def verify(video: Path, sub_path: Path, seed: int = 0) -> tuple[bool, list[str]]:
    """拿视频自己的声音去对字幕的轴。

    对若干个时间窗做 ASR，把听到的日语跟字幕比字符错误率。文件名对得上只能说明
    打包时放在一起，证明不了轴——只有拿视频自己的声音去对才能证伪。

    **判据是对照组，不是绝对阈值。** 日语 ASR 在专有名词上噪声极大（实测同一句
    「私は雪ノ下陽乃ね」被听成「私は雪の下春のね」，CER 57% 但轴完全正确），
    绝对阈值要么放到无效，要么把对的也毙掉。所以同一段音频比两次：
    一次跟对齐窗，一次跟偏移 VERIFY_SHIFT 秒的对照窗。轴对 → 对齐窗明显更低；
    轴错 → 两边一样烂，差值消失。
    """
    from .asr import REPO
    from .tts import cer, normalize
    import mlx_whisper

    dur = _video_duration(video)
    rng = random.Random(seed)
    # 避开片头片尾：只在中段取样
    lo, hi = dur * 0.2, dur * 0.8 - VERIFY_WINDOW
    starts = sorted(rng.uniform(lo, hi) for _ in range(VERIFY_SAMPLES))

    # 走系统临时目录，不写进片源旁边：片源在外置盘上，而且同一个视频并发跑两次
    # 会用同一个 `.verify.wav` 互相覆盖，结果是两边都在听对方的音频。
    work = Path(tempfile.mkdtemp(prefix="verify-"))
    clip_path = work / "clip.wav"
    lines, ok = [], True
    try:
        for st in starts:
            want = _subs_in_window(sub_path, st, st + VERIFY_WINDOW)
            ctrl = _subs_in_window(sub_path, st + VERIFY_SHIFT,
                                   st + VERIFY_SHIFT + VERIFY_WINDOW)
            if len(normalize(want)) < 20 or len(normalize(ctrl)) < 20:
                lines.append(f"  {st:6.0f}s  跳过（该窗日文字幕过少）")
                continue
            _clip_audio(video, clip_path, st, VERIFY_WINDOW)
            heard = _hear(clip_path, mlx_whisper)
            # 复读幻觉：弃用该窗，不参与判定。**不能算「对不上」**——
            # 这一窗没提供任何关于轴的证据，把它记成反证等于凭 ASR 的故障给片源定罪。
            if len(normalize(heard)) > len(normalize(want)) * VERIFY_BABBLE:
                lines.append(f"  {st:6.0f}s  跳过（ASR 复读幻觉，输出长 "
                             f"{len(normalize(heard)) / max(1, len(normalize(want))):.1f} 倍）")
                lines.append(f"          听到「{heard[:20]}…」")
                continue
            _, err = cer(want, heard)
            _, err_ctrl = cer(ctrl, heard)
            good = err <= VERIFY_CEILING and err + VERIFY_MARGIN <= err_ctrl
            ok &= good
            lines.append(
                f"  {st:6.0f}s  对齐窗 {err:5.0%}  对照窗 {err_ctrl:5.0%}  "
                f"差 {err_ctrl - err:+5.0%}  {'对上' if good else '对不上'}"
            )
            lines.append(f"          字幕「{want[:20]}…」")
            lines.append(f"          听到「{heard[:20]}…」")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if not any("对齐窗" in ln for ln in lines):
        return False, lines + ["  没有可用采样窗，无法判定——不许当作通过"]
    return ok, lines


SOURCES = paths.DATA / "library" / "sources.json"


def probe_video(video: Path) -> dict:
    """渲染需要的片源规格：时长、帧率、分辨率。

    帧率取 `r_frame_rate` 的分数原样（如 `24000/1001`），不要先转成 23.976 再用——
    截取守卫的「1 帧」容差要按精确值算，浮点近似会在长片上攒出误差。
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate,width,height", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    d = json.loads(out)
    s = d["streams"][0]
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    return {
        "duration": float(d["format"]["duration"]),
        "fps": s["r_frame_rate"],
        "frame": den / num,
        "width": s["width"],
        "height": s["height"],
    }


def register(video: Path, anime: str, season: int, episode: int,
             path: Path = SOURCES) -> dict:
    """把一集片源登记进 sources.json，登记前强制过完整性校验。

    渲染阶段要从 (season, episode) 找到 mkv，而索引里只有集号没有路径——
    这张表就是那座桥。**它同时是「这一集验过」的凭证**：`intact` 不过就不许登记，
    所以渲染永远不可能切到残缺片源上。

    合并写入，不覆盖其他集。
    """
    ok, detail = intact(video)
    if not ok:
        raise SystemExit(f"FAIL 片源不完整，不予登记：{detail}\n     {video}")
    db = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = f"S{season:02d}E{episode:02d}"
    entry = {"path": str(video), **probe_video(video)}
    db.setdefault(anime, {})[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def load_sources(anime: str, path: Path = SOURCES) -> dict[str, dict]:
    if not path.exists():
        raise SystemExit(f"FAIL 没有片源登记表 {path}，先跑 `ingest sources`")
    db = json.loads(path.read_text(encoding="utf-8"))
    if anime not in db:
        raise SystemExit(f"FAIL 片源登记表里没有《{anime}》，先跑 `ingest sources`")
    return db[anime]


# ---------- 整季入库 ----------

# 集号从文件名取。压制组的命名千差万别，但「[NN]」这一段几乎是通例；
# 不合的用 --pattern 换掉，不要去改这个默认值。
#
# OVA 也收，编号为该季的 E00。它是正片内容不是花絮（春物三季的 OVA 都是正传后日谈），
# 漏掉等于凭「文件名里没有数字」把三集素材扔了。一季只有一个 OVA，不会撞号。
EP_PATTERN = r"\[(?P<episode>\d{2}|OVA)\]"
# 双语字幕：verify 要日文行去对 ASR，subindex 要中文行进索引，同一个文件两用。
SUB_GLOB = "*.Chs&Jap.ass"


def _find_sub(video: Path, glob: str) -> Path | None:
    """只在**同目录、同主文件名**下找字幕。

    这是 CLAUDE.md「第 3 种字幕来源」判据的第 1 条。放宽到全目录搜索就等于
    允许拿隔壁集的字幕配这一集——轴会整集错开，而且不报错。
    """
    stem = video.stem
    hits = [p for p in video.parent.glob(glob) if p.name.startswith(stem)]
    return hits[0] if len(hits) == 1 else None


def phase0(videos: list[Path], anime: str, season: int, pattern: str = EP_PATTERN,
           glob: str = SUB_GLOB, index_dir: Path = INDEX_DIR,
           force: bool = False, reindex: bool = False) -> int:
    """一整季入库：验对轴 → 建索引 → 登记片源。返回失败集数。

    **这是 Phase 0 动作，一部番跑一次。** 单独跑 verify / subindex build / sources
    三条命令也能达到同样效果，写成一条是因为漏掉其中任何一条都是静默失败：
    漏 verify → 轴没验过的字幕进了索引；漏 register → 渲染时找不到片源；
    只建了半季 → 检索池悄悄比你以为的小，选题会在一个假的素材边界里做。

    最后那条不是假想。2026-07-30 发现片源 41 集早已完整，索引却只建了 6 集——
    中间每一期都在 6 集的池子里挑素材，没有任何地方报错。
    """
    rows, bad = [], 0

    def say(status: str, tag: str, detail: str) -> None:
        # 逐集打印而不是攒到最后：整季要跑二十来分钟，攒着的话既看不到进度，
        # 也要等到最后才发现「这个发布的字幕后缀不一样」这类系统性失败。
        rows.append((status, tag, detail))
        print(f"{status:<5} {tag:<12} {detail}", flush=True)

    for video in sorted(videos):
        m = re.search(pattern, video.name)
        if not m:
            say("FAIL", video.name, "文件名里找不到集号")
            bad += 1
            continue
        raw = m.group("episode")
        ep = int(raw) if raw.isdigit() else 0
        tag = f"S{season:02d}E{ep:02d}"

        if not (force or reindex) and (index_dir / f"{anime}_{tag}.npy").exists():
            say("SKIP", tag, "已索引，--force 可重建")
            continue

        sub_path = _find_sub(video, glob)
        if sub_path is None:
            say("FAIL", tag, f"同名字幕不唯一或缺失（{glob}）")
            bad += 1
            continue

        # --reindex 免跑 verify，但**只对已登记的集生效**。
        # 登记过 = 当初过了 verify（register 是 phase0 里 verify 之后才做的），
        # 而 verify 判的是「这个字幕的轴对不对得上这个视频」——解析器改了不影响这个结论。
        # 把条件绑在登记表上而不是绑在一个裸开关上，是为了让它没法被用来
        # 把没验过的字幕塞进索引：没登记的集，--reindex 一样要老老实实跑 verify。
        registered = (reindex and SOURCES.exists()
                      and tag in json.loads(SOURCES.read_text(encoding="utf-8"))
                      .get(anime, {}))
        if not registered:
            ok, detail = verify(video, sub_path)
            if not ok:
                # 把 verify 的采样明细一起打出来。**门禁只说「不过」不说为什么，
                # 等于逼人重跑一遍去猜**，而重跑要花半分钟 ASR；更糟的是猜不出来的人
                # 会直接去关掉这道门禁。
                say("FAIL", tag, "对轴校验不过，不许进索引")
                for ln in detail:
                    print(ln, flush=True)
                bad += 1
                continue

        n = build(sub_path, anime, season, ep, index_dir)
        register(video, anime, season, ep)
        say("OK", tag, f"{n} 个检索单元" + ("（重建，未重跑 verify）" if registered else ""))

    print("-" * 60)
    done = sum(r[0] == "OK" for r in rows)
    print(f"{done} 集入库，{sum(r[0] == 'SKIP' for r in rows)} 集跳过，{bad} 集失败")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="列出字幕轨与选择评分")
    p.add_argument("video", type=Path)

    e = sub.add_parser("subs", help="提取字幕（无内封则 ASR）")
    e.add_argument("video", type=Path)
    e.add_argument("-o", "--out", type=Path, required=True)

    i = sub.add_parser("intact", help="片源完整性（重新拷贝后必跑）")
    i.add_argument("videos", type=Path, nargs="+")

    v = sub.add_parser("verify", help="外挂字幕对轴校验（进索引前必跑）")
    v.add_argument("video", type=Path)
    v.add_argument("subtitle", type=Path)
    v.add_argument("--seed", type=int, default=0)

    r = sub.add_parser("run", help="提取字幕并建索引")
    r.add_argument("video", type=Path)
    r.add_argument("--anime", required=True)
    r.add_argument("--season", type=int, default=1)
    r.add_argument("--episode", type=int, required=True)
    r.add_argument("--index-dir", type=Path, default=INDEX_DIR)

    g = sub.add_parser("sources", help="把片源登记进 sources.json（渲染要靠它找文件）")
    g.add_argument("video", type=Path)
    g.add_argument("--anime", required=True)
    g.add_argument("--season", type=int, required=True)
    g.add_argument("--episode", type=int, required=True)

    z = sub.add_parser("phase0", help="整季入库：验对轴 → 建索引 → 登记片源")
    z.add_argument("videos", type=Path, nargs="+")
    z.add_argument("--anime", required=True)
    z.add_argument("--season", type=int, required=True)
    z.add_argument("--pattern", default=EP_PATTERN, help="从文件名取集号的正则，需含 (?P<episode>…)")
    z.add_argument("--sub-glob", default=SUB_GLOB, help="同名字幕的通配符")
    z.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    z.add_argument("--force", action="store_true", help="已索引的也重建（含重跑 verify）")
    z.add_argument("--reindex", action="store_true",
                   help="重建索引但不重跑 verify，仅对已登记的集生效（解析器改了之后用）")

    a = ap.parse_args()

    if a.cmd == "phase0":
        bad = phase0(a.videos, a.anime, a.season, a.pattern, a.sub_glob,
                     a.index_dir, a.force, a.reindex)
        raise SystemExit(1 if bad else 0)

    if a.cmd == "sources":
        e = register(a.video, a.anime, a.season, a.episode)
        print(f"OK  {a.anime} S{a.season:02d}E{a.episode:02d}  "
              f"{e['duration']:.1f}s  {e['fps']}  {e['width']}x{e['height']}")
        return

    if a.cmd == "probe":
        tracks = probe(a.video)
        if not tracks:
            print("无内封字幕轨 → 需走 ASR 兜底")
            return
        print(f"{'idx':<5}{'codec':<8}{'lang':<7}{'score':<7}title")
        for t in sorted(tracks, key=lambda t: -t.score()):
            mark = " ←选中" if t is max(tracks, key=lambda x: x.score()) and t.score() >= 0 else ""
            print(f"{t.index:<5}{t.codec:<8}{t.language:<7}{t.score():<7}{t.title}{mark}")
        return

    if a.cmd == "intact":
        bad = 0
        for f in sorted(a.videos):
            ok, detail = intact(f)
            bad += not ok
            print(f"{'OK  ' if ok else 'FAIL'}  {detail:<48}{f.name}")
        print("-" * 60)
        print(f"{len(a.videos) - bad}/{len(a.videos)} 完整")
        raise SystemExit(1 if bad else 0)

    if a.cmd == "verify":
        ok, lines = verify(a.video, a.subtitle, a.seed)
        print(f"{a.video.name}")
        for ln in lines:
            print(ln)
        print("PASS 轴对得上，可以进索引" if ok else "FAIL 轴对不上，不许进索引")
        raise SystemExit(0 if ok else 1)

    if a.cmd == "subs":
        try:
            out = extract(a.video, a.out)
            print(f"OK 内封提取 → {out}")
        except LookupError as ex:
            print(f"无内封字幕（{ex}），转 ASR")
            out = transcribe(a.video, a.out)
            print(f"OK ASR 转录 → {out}")
        return

    subs_path = a.video.with_suffix(".zh.ass")
    try:
        extract(a.video, subs_path)
        src = "内封"
    except LookupError:
        subs_path = a.video.with_suffix(".zh.srt")
        transcribe(a.video, subs_path)
        src = "ASR"
    n = build(subs_path, a.anime, a.season, a.episode, a.index_dir)
    print(f"OK [{src}] {a.video.name} → {n} 个检索单元")


if __name__ == "__main__":
    main()
