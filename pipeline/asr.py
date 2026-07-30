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
