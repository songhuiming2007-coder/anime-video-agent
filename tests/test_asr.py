"""云端 Whisper 仲裁的长音频参数（2026-10-17 supercell 卷一段 44）。

模型不进单测（不 mock 模型）；这里只测「什么时长传什么参数」与读时长这两个纯部件，
真机行为在云端实测（见 docs/dev/issues D111）。
"""

import wave
from pathlib import Path

import pytest

from pipeline import asr


@pytest.mark.parametrize("dur, kwargs", [
    (29.0, {}),
    (30.0, {}),                              # 正好 3000 帧 mel：transformers 不进长音频生成
    (30.01, {"return_timestamps": True}),
    (41.5, {"return_timestamps": True}),
])
def test_只有超过一个窗口才开时间戳(dur, kwargs):
    assert asr._whisper_call_kwargs(dur) == kwargs


def test_读tts写出的wav时长(tmp_path: Path):
    p = tmp_path / "seg.wav"
    with wave.open(str(p), "wb") as w:      # 与 tts 的 wavfile.write 同格式：单声道 16-bit 24k
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000 * 31)
    assert asr._wav_seconds(p) == pytest.approx(31.0)
