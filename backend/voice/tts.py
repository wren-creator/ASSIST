"""Text-to-speech. Returns WAV bytes the browser can play directly.

Engine is config.voice.tts_engine:
  say     macOS built-in, zero install, always works (default)
  kokoro  `pip install kokoro`  - best quality on Apple Silicon
  piper   `pip install piper-tts` + a .onnx voice - light CPU option

Any engine that fails or isn't installed falls back to `say`.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import wave
from pathlib import Path

from .. import config

log = logging.getLogger("assist.tts")


async def synth(text: str) -> tuple[bytes, str]:
    """Returns (audio_bytes, mime). mime is always audio/wav here."""
    text = (text or "").strip()
    if not text:
        return b"", "audio/wav"

    engine = config.get("voice", "tts_engine", default="say")
    try:
        if engine == "kokoro":
            return await asyncio.to_thread(_kokoro, text), "audio/wav"
        if engine == "piper":
            return await asyncio.to_thread(_piper, text), "audio/wav"
    except Exception as e:  # noqa: BLE001 - any engine failure degrades to `say`
        log.warning("tts engine %r failed (%s); falling back to say", engine, e)

    return await asyncio.to_thread(_say, text), "audio/wav"


def _say(text: str) -> bytes:
    if not shutil.which("say"):
        raise RuntimeError("macOS `say` not available")
    voice = config.get("voice", "say_voice", default="Daniel")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        cmd = ["say", "-o", tmp.name, "--data-format=LEI16@22050"]
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        import subprocess

        subprocess.run(cmd, check=True, capture_output=True)
        return Path(tmp.name).read_bytes()


def _kokoro(text: str) -> bytes:
    from kokoro import KPipeline  # type: ignore

    pipe = _kokoro_pipeline()
    voice = config.get("voice", "kokoro_voice", default="am_michael")
    chunks = [audio for _gs, _ps, audio in pipe(text, voice=voice)]
    if not chunks:
        raise RuntimeError("kokoro produced no audio")
    import numpy as np  # kokoro pulls this in

    samples = np.concatenate(chunks)
    pcm16 = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    return _wav_bytes(pcm16.tobytes(), sample_rate=24000)


_KPIPE = None


def _kokoro_pipeline():
    global _KPIPE
    if _KPIPE is None:
        from kokoro import KPipeline  # type: ignore

        _KPIPE = KPipeline(lang_code="a")
    return _KPIPE


def _piper(text: str) -> bytes:
    model = config.get("voice", "piper_model", default="")
    if not model or not Path(model).exists():
        raise RuntimeError("config voice.piper_model is not set to a real .onnx file")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        import subprocess

        subprocess.run(
            ["piper", "--model", model, "--output_file", tmp.name],
            input=text.encode(), check=True, capture_output=True,
        )
        return Path(tmp.name).read_bytes()


def _wav_bytes(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()
