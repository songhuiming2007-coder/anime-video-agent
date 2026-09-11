"""无内封字幕时的兜底：对视频文件本身做 ASR，产出带时间码的字幕。

字幕**必须**出自这个视频文件本身，见 ingest.py 与 CLAUDE.md。

2026-07-28 基准（macOS 内置中文语音合成真值音轨，mlx-whisper large-v3-turbo）：

    word_timestamps=False   起点误差 中位 1.009s   11.2x 实时
    word_timestamps=True    起点误差 中位 0.340s    6.1x 实时   ← 采用

误差全部同向偏早、离散度仅 0.17s，是系统性偏置而非随机误差。

**偏早在本流水线里是安全的**：旁白是自己的 TTS，其时长决定画面轨长度，
字幕时间戳不参与音画同步，只决定从源片何处起切。偏早 0.34s 相当于留了个入点，
比偏晚（切掉第一个字）好得多。因此不做偏置补偿——补过头反而可能切晚。

已知限制：会出同音字错误（实测"只剩下"→"之剩下"）。语义检索对此鲁棒，
但精确文本匹配不要依赖 ASR 结果。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import paths  # 必须在 mlx_whisper 之前：把模型缓存钉到 SSD

REPO = "mlx-community/whisper-large-v3-turbo"


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_audio(video: Path, dest: Path) -> Path:
    """抽单声道 16k 音轨——Whisper 的原生采样率，避免内部重采样。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ar", "16000", "-ac", "1", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def run(video: Path, dest: Path, language: str = "zh") -> Path:
    """视频 → SRT。dest 后缀应为 .srt。"""
    import mlx_whisper

    # **临时音轨走系统临时目录，不写进片源旁边。**
    # 初版是 `video.with_suffix(".16k.wav")` + `tmp = not audio.exists()`：
    # 文件已存在就复用且不清理，于是换了一个同名片源时会拿**上一个文件的音轨**去转录，
    # 结果照常产出字幕、没有任何报错。2026-07-29 审计发现。
    # 顺带解决另一件事：片源目录在外置盘上，往那里写几十 M 的临时 wav 没有道理。
    work = Path(tempfile.mkdtemp(prefix="asr-"))
    audio = work / "16k.wav"
    try:
        extract_audio(video, audio)
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(audio)],
            capture_output=True, text=True, check=True).stdout.strip())

        result = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=REPO,
            language=language,
            word_timestamps=True,   # 见模块头：把起点误差从 1.0s 降到 0.34s
            # Whisper 会在静音段上进入重复循环，把自己的上一段输出当上文喂回去。
            # 2026-07-29 同一集 A/B 实测（S02E03，24 分钟，language=ja）：
            #     True （原状）  248s   非空段 430   连续重复段 31
            #     False（现在）  191s   非空段 419   连续重复段 23
            # 效果是**小幅正向**，不要当成显著改善。代价是长片上下文连贯性略降，
            # 而本流水线只拿字幕做语义检索，这点连贯性不值得拿捏造台词去换。
            condition_on_previous_text=False,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 越界段一律丢弃。判据可证伪：音频只有 dur 秒，不可能有结束于 dur 之后的语音。
    #
    # **注意适用工况。** 这道守卫防的是「短片段 + 尾部静音」——2026-07-29 在
    # verify 里实测到 Whisper 对 60s 片段吐出结束于 75.5s 的「俺し続きました俺」，
    # 后面跟 80 多个 76.0-76.0 的空段，只滤空文本拦不住这种有文字的幻觉段。
    # 但同日在整片转录上 A/B，越界段是 **0 vs 0**：整片有充足上下文，不会跑飞到
    # 这个程度。所以这里是保险，不是热路径——别拿整片的实测去说它有多重要。
    segs = [s for s in result["segments"]
            if s["text"].strip() and s.get("end", 0.0) <= dur + 1.0]
    dropped = len(result["segments"]) - len(segs)
    if dropped:
        print(f"     丢弃 {dropped} 个空段/越界段（音频 {dur:.1f}s）")
    if not segs:
        raise RuntimeError(f"ASR 未产出任何片段：{video}")

    lines = []
    for i, s in enumerate(segs, 1):
        lines += [
            str(i),
            f"{_srt_time(s['start'])} --> {_srt_time(s['end'])}",
            s["text"].strip(),
            "",
        ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# 音频转录后端（v2，架构设计三.5）：TTS 回读质检的算力抽象
# ---------------------------------------------------------------------------
#
# **为什么与上面的 `run()` 分开**：`run()` 是 ingest 的一次性兜底（整集视频 → 带
# 时间码 SRT）；这里是逐段 TTS 音频的短文本回读。共用的只是「ASR 引擎」这一层，
# 所以共用后端表，而不是把两个用途塞进一个函数。
#
# **为什么是级联而非融合**（架构设计三.5）：两个 ASR 的 CER 不可比——不同模型的
# 错误率不是同一个量（S10 同构）。所以是「主读不过才请仲裁」，不是分数混合。
#
# **本地/云端同一份裁决代码，两处算力**：ADR-0014 解耦在代码层的落地。
# 本地 Mac 无 CUDA 时自动走 mlx-whisper，云端走 CUDA 后端。

# 云端模型路径与 config/cloud.json 的 models 段对应（那份是机制配置，这份是代码默认）
CLOUD_WHISPER_DIR = "/root/autodl-tmp/models/openai/whisper-large-v3"
CLOUD_SENSEVOICE_DIR = "/root/autodl-tmp/models/FunAudioLLM/SenseVoiceSmall"

# 后端优先级：主读取第一个可用的；仲裁取与主读不同的下一个
PRIMARY_ORDER = ("sensevoice_cuda", "whisper_large_v3_cuda", "mlx_whisper_local")


def _backend_mlx_whisper(path: Path) -> str:
    """本地 Apple Silicon 通道（mlx-whisper）。"""
    import mlx_whisper

    return mlx_whisper.transcribe(
        str(path), path_or_hf_repo=REPO, language="zh", verbose=None
    )["text"].strip()


def _backend_whisper_cuda(path: Path) -> str:
    """云端 Whisper Large-v3（transformers pipeline）。"""
    from transformers import pipeline

    pipe = pipeline(
        "automatic-speech-recognition",
        model=CLOUD_WHISPER_DIR,
        device=0,
        generate_kwargs={"language": "zh", "task": "transcribe"},
    )
    return pipe(str(path))["text"].strip()


def _backend_sensevoice_cuda(path: Path) -> str:
    """云端 SenseVoice-Small（funasr）。

    主读候选：快（≈15× 实时）且情感标签白送。**情感输出只进 manifest 观测，
    不进任何门禁**——它测的是「模型觉得什么情绪」，不是「念得对不对」（S1）。
    """
    from funasr import AutoModel

    model = AutoModel(model=CLOUD_SENSEVOICE_DIR, disable_update=True)
    res = model.generate(input=str(path), language="zh", use_itn=True)
    return res[0]["text"].strip()


BACKENDS = {
    "mlx_whisper_local": _backend_mlx_whisper,
    "whisper_large_v3_cuda": _backend_whisper_cuda,
    "sensevoice_cuda": _backend_sensevoice_cuda,
}


def backend_unavailable_reason(name: str) -> str | None:
    """后端不可用则返回原因，可用返回 None（纯探测，不加载模型）。"""
    import importlib.util

    if name == "mlx_whisper_local":
        return None if importlib.util.find_spec("mlx_whisper") else "未安装 mlx_whisper（需 Apple Silicon）"
    if name == "whisper_large_v3_cuda":
        if importlib.util.find_spec("transformers") is None:
            return "未安装 transformers"
        if not Path(CLOUD_WHISPER_DIR).is_dir():
            return f"模型目录不存在: {CLOUD_WHISPER_DIR}"
        return None
    if name == "sensevoice_cuda":
        if importlib.util.find_spec("funasr") is None:
            return "未安装 funasr"
        if not Path(CLOUD_SENSEVOICE_DIR).is_dir():
            return f"模型目录不存在: {CLOUD_SENSEVOICE_DIR}"
        # `find_spec` 只回答「包在不在」，不回答「能不能用」。2026-09-11 实测：
        # funasr 装上了，但它依赖的 torch_complex 缺失，`from funasr import
        # AutoModel` 直接抛——于是 pick_backends 选中它当仲裁，整期跑到第一段
        # 才炸。**故障晚发现比一开始不可用贵得多**（那一次白烧了 7 段的算力）。
        # 所以探测必须深到真能拿到 AutoModel。
        try:
            from funasr import AutoModel  # noqa: F401
        except Exception as e:  # noqa: BLE001 —— 探测就是要吞一切异常
            return f"funasr 在但 AutoModel 不可用（{type(e).__name__}: {e}）"
        return None
    return f"未知后端: {name}"


def pick_backends(prefer: str | None = None) -> tuple[str, str | None]:
    """选 (主读后端, 仲裁后端或 None)。纯函数。

    按 PRIMARY_ORDER 取第一个可用的作主读；仲裁取**与主读不同的**下一个可用后端。
    没有第二档时返回 None——此时主读不过就直落既有重试/豁免链，不做无意义的
    「用同一份裁决再判一次」。**不假装仲裁存在**。
    """
    order = list(PRIMARY_ORDER)
    if prefer and prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)
    usable = [n for n in order if backend_unavailable_reason(n) is None]
    if not usable:
        raise RuntimeError(
            "没有任何可用的 ASR 后端："
            + "; ".join(f"{n}({backend_unavailable_reason(n)})" for n in order)
        )
    return usable[0], (usable[1] if len(usable) > 1 else None)


def transcribe_audio(path: Path, backend: str | None = None) -> tuple[str, str]:
    """音频 → (文本, 实际使用的后端名)。接缝函数。

    显式传 backend 时不做降级——调用方指定了就该按它执行，静默换后端会让
    「主读/仲裁」的分工失去意义（而分工正是级联仲裁成立的前提）。
    """
    if backend is not None:
        reason = backend_unavailable_reason(backend)
        if reason:
            raise RuntimeError(f"指定后端 {backend} 不可用: {reason}")
        return BACKENDS[backend](path), backend

    primary, _ = pick_backends()
    return BACKENDS[primary](path), primary
