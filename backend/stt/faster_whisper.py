"""In-process faster-whisper provider with a cached model instance."""

import asyncio
import logging
import threading
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException

from config import settings
from diagnostics import traced_stage
from runtime import ModelWorker, worker_policy

logger = logging.getLogger(__name__)
_INFERENCE_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _load_model(local_files_only: bool = True):
    """Load the selected faster-whisper model once and reuse it."""
    from faster_whisper import WhisperModel

    download_root = Path(settings.faster_whisper_download_root).expanduser()
    if not download_root.is_absolute():
        # Resolve relative paths from the repository root, independent of the
        # process working directory (for example, when launched from backend/).
        project_root = Path(__file__).resolve().parents[2]
        download_root = project_root / download_root
    download_root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Loading faster-whisper model '%s' on '%s' (%s).",
        settings.faster_whisper_model,
        settings.faster_whisper_device,
        settings.faster_whisper_compute_type,
    )
    model = WhisperModel(
        settings.faster_whisper_model,
        device=settings.faster_whisper_device,
        compute_type=settings.faster_whisper_compute_type,
        download_root=str(download_root),
        local_files_only=local_files_only,
        cpu_threads=worker_policy(settings, "stt")[1],
        num_workers=1,
    )
    logger.info("faster-whisper model loaded.")
    return model


def _decode_audio(audio: bytes):
    from faster_whisper.audio import decode_audio

    return decode_audio(BytesIO(audio))


@traced_stage("stt.transcribe")
def _transcribe(audio: bytes) -> dict:
    if not audio:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")

    with _INFERENCE_LOCK:
        model = _load_model()
        waveform = _decode_audio(audio)
        segments, info = model.transcribe(
            waveform,
            language="en",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        texts = [segment.text.strip() for segment in segments]

    return {
        "text": " ".join(text for text in texts if text).strip(),
        "language": getattr(info, "language", "en") or "en",
        "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
    }


class FasterWhisperProvider:
    def __init__(self):
        self._worker = ModelWorker(settings, "stt")

    async def initialize(self) -> None:
        """Load or download the configured CTranslate2 model at startup."""
        await self._worker.run(_load_model)

    async def close(self):
        await self._worker.close()

    async def transcribe(self, audio: bytes, content_type: str) -> dict:
        """Transcribe the recording with the cached in-process model."""
        del content_type  # PyAV identifies the encoded audio from its container.
        try:
            return await self._worker.run(_transcribe, audio)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("faster-whisper transcription failed.")
            raise HTTPException(
                status_code=502,
                detail="Failed to transcribe audio. Please try again.",
            ) from exc
