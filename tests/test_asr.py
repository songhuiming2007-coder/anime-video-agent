"""云端 Whisper 仲裁的长音频参数（2026-10-17 supercell 卷一段 44）。

模型不进单测（不 mock 模型）；这里只测「什么时长传什么参数」与读时长这两个纯部件，
真机行为在云端实测（见 docs/dev/issues D111）。
"""

import sys
import types
import wave
from pathlib import Path

import pytest

from pipeline import asr, tts


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

    import importlib.machinery

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.__spec__ = importlib.machinery.ModuleSpec("funasr", loader=None)
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


def test_strip_asr_tags_剥离SenseVoice与事件标签且不误伤普通符号():
    assert asr.strip_asr_tags("<|zh|><|NEUTRAL|><|Speech|><|withitn|>他们必须摸索出1种") == "他们必须摸索出1种"
    assert asr.strip_asr_tags("  <|HAPPY|><|Laughter|>哈哈<|/Laughter|>你好  ") == "哈哈你好"
    assert asr.strip_asr_tags("<|nospeech|>") == ""
    assert asr.strip_asr_tags("a < b | c > d") == "a < b | c > d"


def test_sensevoice_剥离控制标签后tts_cer归零(monkeypatch, tmp_path):
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(tmp_path))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "autodl")
    local_dir = tmp_path / "FunAudioLLM" / "SenseVoiceSmall"
    local_dir.mkdir(parents=True)

    class _FakeAutoModel:
        def __init__(self, **_kwargs):
            pass

        def generate(self, **_kwargs):
            return [{"text": "<|zh|><|NEUTRAL|><|Speech|><|withitn|>他们必须摸索出1种"}]

    import importlib.machinery

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.__spec__ = importlib.machinery.ModuleSpec("funasr", loader=None)
    fake_funasr.AutoModel = _FakeAutoModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)

    wav = tmp_path / "seg.wav"
    heard, used = asr.transcribe_audio(wav, backend="sensevoice_cuda")
    assert used == "sensevoice_cuda"
    assert heard == "他们必须摸索出1种"
    assert tts.cer("他们必须摸索出一种", heard) == (0, 0.0)


def test_sensevoice_模型按配置缓存仅初始化一次_冷启动落盘不双载_参数变化或清理后重建(monkeypatch, tmp_path):
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(tmp_path / "missing_models"))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "colab")
    hub = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))

    init_calls: list[dict[str, object]] = []
    gen_calls: list[str] = []

    class _FakeAutoModel:
        def __init__(self, **kwargs):
            init_calls.append(kwargs)

        def generate(self, **kwargs):
            gen_calls.append(str(kwargs["input"]))
            return [{"text": "<|zh|><|NEUTRAL|><|Speech|><|withitn|>你好世界"}]

    import importlib.machinery

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.__spec__ = importlib.machinery.ModuleSpec("funasr", loader=None)
    fake_funasr.AutoModel = _FakeAutoModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)

    wav1 = tmp_path / "1.wav"
    wav2 = tmp_path / "2.wav"
    assert asr._backend_sensevoice_cuda(wav1) == "你好世界"
    assert len(init_calls) == 1

    # 模拟 Colab 首段推理后权重已落进 HF_HUB_CACHE：第 2 段必须复用同一模型实例，不双载
    repo_cache = hub / "models--FunAudioLLM--SenseVoiceSmall"
    (repo_cache / "refs").mkdir(parents=True)
    (repo_cache / "refs" / "main").write_text("deadbeef", encoding="utf-8")
    snap = repo_cache / "snapshots" / "deadbeef"
    snap.mkdir(parents=True)
    (snap / "configuration.json").write_text("{}", encoding="utf-8")
    (snap / "model.pt").write_bytes(b"pt")

    assert asr._backend_sensevoice_cuda(wav2) == "你好世界"
    assert len(init_calls) == 1, "冷启动落盘后第 2 段不应再次实例化 AutoModel"
    assert len(gen_calls) == 2

    # 切换本地模型目录后应重建实例
    new_root = tmp_path / "local_models"
    (new_root / "FunAudioLLM" / "SenseVoiceSmall").mkdir(parents=True)
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(new_root))
    assert asr._backend_sensevoice_cuda(wav1) == "你好世界"
    assert len(init_calls) == 2

    # 显式清理缓存后再次调用应重建实例
    asr.clear_model_cache()
    assert asr._backend_sensevoice_cuda(wav1) == "你好世界"
    assert len(init_calls) == 3


def test_whisper_cuda_模型按fp16懒加载缓存仅初始化一次_清理或切目录后重建(monkeypatch, tmp_path):
    import torch

    root1 = tmp_path / "models1"
    (root1 / "openai" / "whisper-large-v3").mkdir(parents=True)
    root2 = tmp_path / "models2"
    (root2 / "openai" / "whisper-large-v3").mkdir(parents=True)
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(root1))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "autodl")

    def _mk_wav(name: str, secs: int) -> Path:
        p = tmp_path / name
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000 * secs)
        return p

    short_wav = _mk_wav("short.wav", 5)
    long_wav = _mk_wav("long.wav", 31)

    pipe_inits: list[dict[str, object]] = []
    pipe_calls: list[tuple[str, dict[str, object]]] = []

    def _fake_pipeline(task, **kwargs):
        pipe_inits.append({"task": task, **kwargs})

        def _run(audio_path, **call_kw):
            pipe_calls.append((audio_path, call_kw))
            return {"text": " <|zh|>第一句 "}

        return _run

    fake_tf = types.ModuleType("transformers")
    fake_tf.pipeline = _fake_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)

    assert asr._backend_whisper_cuda(short_wav) == "第一句"
    assert asr._backend_whisper_cuda(long_wav) == "第一句"
    assert len(pipe_inits) == 1
    assert pipe_inits[0]["dtype"] == torch.float16
    assert pipe_inits[0]["model"] == str(root1 / "openai" / "whisper-large-v3")
    assert pipe_calls == [
        (str(short_wav), {}),
        (str(long_wav), {"return_timestamps": True}),
    ]

    # 切目录后重建
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(root2))
    assert asr._backend_whisper_cuda(short_wav) == "第一句"
    assert len(pipe_inits) == 2

    # 清理缓存后重建
    asr.clear_model_cache()
    assert asr._backend_whisper_cuda(short_wav) == "第一句"
    assert len(pipe_inits) == 3


def test_whisper_cuda_兼容旧版transformers仅含torch_dtype形参(monkeypatch, tmp_path):
    import torch

    root = tmp_path / "models_legacy"
    (root / "openai" / "whisper-large-v3").mkdir(parents=True)
    monkeypatch.setenv("AVA_REMOTE_MODELS", str(root))
    monkeypatch.setenv("AVA_CLOUD_PROVIDER", "autodl")

    wav = tmp_path / "legacy.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 2)

    seen: list[dict[str, object]] = []

    def _legacy_pipeline(task, model=None, device=None, generate_kwargs=None, torch_dtype=None):
        seen.append({
            "task": task,
            "model": model,
            "device": device,
            "generate_kwargs": generate_kwargs,
            "torch_dtype": torch_dtype,
        })
        return lambda audio_path, **_kw: {"text": "<|zh|>兼容旧版"}

    fake_tf = types.ModuleType("transformers")
    fake_tf.pipeline = _legacy_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)

    assert asr._backend_whisper_cuda(wav) == "兼容旧版"
    assert len(seen) == 1
    assert seen[0]["torch_dtype"] == torch.float16
