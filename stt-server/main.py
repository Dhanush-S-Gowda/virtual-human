from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("stt-server")

# Global reference for preloaded model
whisper_model: Optional[WhisperModel] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    logger.info(
        "Loading faster-whisper model '%s' on device '%s' with compute type '%s'...",
        WHISPER_MODEL,
        WHISPER_DEVICE,
        WHISPER_COMPUTE_TYPE,
    )
    try:
        whisper_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        logger.info("faster-whisper model loaded successfully.")
    except Exception as exc:
        logger.exception("Failed to load faster-whisper model: %s", exc)
        raise exc

    yield

    logger.info("Shutting down STT server...")
    whisper_model = None


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Local Avatar STT Server",
    description="Local Speech-to-Text inference using faster-whisper",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://[^/]+$",
    allow_origins=[
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://0.0.0.0:4173",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    model: str


class TranscribeResponse(BaseModel):
    text: str
    language: str = "en"
    duration: float = 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service="stt-server",
        model="faster-whisper",
    )


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile = File(...)):
    global whisper_model
    if whisper_model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech recognition model is not initialized yet.",
        )

    # Validate audio upload
    if not audio.filename and not audio.content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No audio file provided.",
        )

    # Determine file suffix based on content_type or filename
    suffix = ".webm"
    if audio.filename and "." in audio.filename:
        suffix = os.path.splitext(audio.filename)[1]
    elif audio.content_type:
        if "wav" in audio.content_type:
            suffix = ".wav"
        elif "ogg" in audio.content_type:
            suffix = ".ogg"
        elif "mp4" in audio.content_type:
            suffix = ".mp4"

    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            shutil.copyfileobj(audio.file, temp_file)

        # Check if file has non-zero size
        file_size = os.path.getsize(temp_path)
        if file_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded audio file is empty.",
            )

        logger.info(
            "Transcribing audio (size: %d bytes, type: %s)...",
            file_size,
            audio.content_type or "unknown",
        )

        # Run transcription with faster-whisper
        # vad_filter=True filters out silence / non-speech
        segments, info = whisper_model.transcribe(
            temp_path,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        segment_texts = [segment.text.strip() for segment in segments]
        transcribed_text = " ".join([t for t in segment_texts if t]).strip()

        duration = round(float(getattr(info, "duration", 0.0) or 0.0), 2)
        language = getattr(info, "language", "en") or "en"

        logger.info(
            "Transcription complete: '%s' (lang: %s, duration: %.2fs)",
            transcribed_text,
            language,
            duration,
        )

        return TranscribeResponse(
            text=transcribed_text,
            language=language,
            duration=duration,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error during speech transcription: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to transcribe audio. Please try again.",
        ) from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError as err:
                logger.warning("Could not delete temp audio file %s: %s", temp_path, err)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        log_level="info",
    )
