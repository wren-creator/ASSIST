"""Speech-to-text with faster-whisper. Same model/config the youtube-bridge uses.

The model is loaded lazily and kept hot for the life of the process (first call
pays the load cost). transcribe() takes whatever audio blob the browser sent
(webm/opus, ogg, wav) and returns plain text; PyAV inside faster-whisper does
the decode, so we just hand it a temp file path.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .. import config

log = logging.getLogger("assist.stt")

_model = None


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        size = config.get("voice", "stt_model", default="base")
        log.info("loading faster-whisper model %r (cpu/int8)", size)
        _model = WhisperModel(size, device="cpu", compute_type="int8")
    return _model


def _transcribe_sync(audio: bytes, suffix: str) -> str:
    model = _load()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(audio)
        tmp.flush()
        segments, _info = model.transcribe(
            tmp.name,
            vad_filter=True,
            initial_prompt="Claude Code session. Ask Claude to explain the diff, the build, the tests.",
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


async def transcribe(audio: bytes, mime: str = "audio/webm") -> str:
    """Off-thread so the event loop keeps serving the WebSocket feed."""
    suffix = ".webm"
    if "ogg" in mime:
        suffix = ".ogg"
    elif "wav" in mime:
        suffix = ".wav"
    elif "mp4" in mime or "m4a" in mime:
        suffix = ".m4a"
    return await asyncio.to_thread(_transcribe_sync, audio, suffix)
