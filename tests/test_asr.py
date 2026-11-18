"""云端 Whisper 仲裁的长音频参数（2026-10-17 supercell 卷一段 44）。

模型不进单测（不 mock 模型）；这里只测「什么时长传什么参数」与读时长这两个纯部件，
真机行为在云端实测（见 docs/dev/issues D111）。
"""

import sys
import types
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


def test_sensevoice_本地目录在位或禁HF回退时直喂目录不传hub(monkeypatch, tmp_path):
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(tmp_path))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "colab")
    monkeypatch.delenv("AVA_CLOUD_HF_FALLBACK", raising=False)
    local_dir = tmp_path / "FunAudioLLM" / "SenseVoiceSmall"
    local_dir.mkdir(parents=True)
    assert asr._sensevoice_model_kwargs() == {
        "model": str(local_dir),
        "disable_update": True,
    }

    missing_root = tmp_path / "empty_models"
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(missing_root))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "autodl")
    expected_dir = str(missing_root / "FunAudioLLM" / "SenseVoiceSmall")
    assert asr._sensevoice_model_kwargs() == {
        "model": expected_dir,
        "disable_update": True,
    }


def test_sensevoice_Colab冷启动显式传hub_hf并透传至AutoModel(monkeypatch, tmp_path):
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(tmp_path / "missing_models"))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "colab")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))

    assert asr._sensevoice_model_kwargs() == {
        "model": "FunAudioLLM/SenseVoiceSmall",
        "hub": "hf",
        "disable_update": True,
    }

    seen: list[dict[str, object]] = []

    class _FakeAutoModel:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def generate(self, **_kwargs):
            return [{"text": " 你好 "}]

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.AutoModel = _FakeAutoModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)

    wav = tmp_path / "a.wav"
    assert asr._backend_sensevoice_cuda(wav) == "你好"
    assert seen == [{
        "model": "FunAudioLLM/SenseVoiceSmall",
        "hub": "hf",
        "disable_update": True,
    }]


def test_sensevoice_Colab首段已落HF缓存后改喂本地snapshot且半残缓存不误认(monkeypatch, tmp_path):
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(tmp_path / "missing_models"))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "colab")
    hub = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))

    repo_cache = hub / "models--FunAudioLLM--SenseVoiceSmall"
    (repo_cache / "refs").mkdir(parents=True)
    (repo_cache / "refs" / "main").write_text("deadbeef", encoding="utf-8")
    snap = repo_cache / "snapshots" / "deadbeef"
    snap.mkdir(parents=True)
    (snap / "configuration.json").write_text("{}", encoding="utf-8")

    # 仅有 configuration.json、缺少 model.pt 的半残缓存不能误认
    assert asr._sensevoice_model_kwargs() == {
        "model": "FunAudioLLM/SenseVoiceSmall",
        "hub": "hf",
        "disable_update": True,
    }

    # 权重也落盘后，直接喂本地 snapshot 目录并省掉 hub="hf"
    (snap / "model.pt").write_bytes(b"pt")
    assert asr._sensevoice_model_kwargs() == {
        "model": str(snap),
        "disable_update": True,
    }
