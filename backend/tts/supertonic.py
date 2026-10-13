"""Supertonic ONNX text-to-speech provider."""

import asyncio
import base64
import logging
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException

from config import settings
from schemas import SynthesizeResponse

BACKEND_DIR = Path(__file__).resolve().parents[1]
ONNX_DIR = BACKEND_DIR / "supertonic/assets/onnx"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_model():
    """Load the ONNX sessions once, on first synthesis rather than API startup."""
    from supertonic.py import helper

    device = str(settings.tts_device).strip().lower()
    if device == "cpu":
        logger.info("Loading Supertonic with CPUExecutionProvider")
        return helper.load_text_to_speech(str(ONNX_DIR), use_gpu=False)
    if device not in {"auto", "gpu"}:
        raise ValueError("tts_device must be 'auto', 'gpu', or 'cpu'.")

    import onnxruntime as ort

    available = set(ort.get_available_providers())
    providers = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "OpenVINOExecutionProvider" in available:
        providers.append(("OpenVINOExecutionProvider", {"device_type": "GPU"}))
    providers.append("CPUExecutionProvider")

    options = ort.SessionOptions()
    cfgs = helper.load_cfgs(str(ONNX_DIR))
    sessions = helper.load_onnx_all(str(ONNX_DIR), options, providers)
    text_processor = helper.load_text_processor(str(ONNX_DIR))
    logger.info(
        "Loaded Supertonic ONNX sessions with providers: %s",
        [session.get_providers() for session in sessions],
    )
    return helper.TextToSpeech(cfgs, text_processor, *sessions)


@lru_cache(maxsize=16)
def _load_style(style_path: str):
    from supertonic.py.helper import load_voice_style

    return load_voice_style([style_path])


def _resolve_style(voice: str) -> Path:
    """Resolve Supertonic style names while preserving Kokoro voice compatibility."""
    candidate = Path(voice).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    style_name = candidate.stem if candidate.suffix.lower() == ".json" else voice
    if re.fullmatch(r"[FM]\d+", style_name, flags=re.IGNORECASE):
        style_path = (
            BACKEND_DIR
            / "supertonic/assets/voice_styles"
            / f"{style_name.upper()}.json"
        )
        if style_path.is_file():
            return style_path

    # Kokoro voice IDs (for example af_bella) are not style paths. Use the
    # configured Supertonic style so existing callers need no provider changes.
    configured = Path(settings.tts_supertonic_voice_style).expanduser()
    if not configured.is_absolute():
        configured = BACKEND_DIR / configured
    if not configured.is_file():
        raise FileNotFoundError(f"Supertonic voice style does not exist: {configured}")
    return configured.resolve()


def _approximate_word_timings(
    text: str,
    duration_seconds: float,
) -> tuple[list[str], list[float], list[float]]:
    """Create evenly distributed timing entries for the shared TalkingHead schema."""
    words = re.findall(r"\S+", text)
    if not words:
        return [], [], []
    weights = [max(1, len(re.sub(r"\W", "", word))) for word in words]
    total_weight = sum(weights)
    times: list[float] = []
    durations: list[float] = []
    cursor_ms = 0.0
    for weight in weights:
        word_duration_ms = duration_seconds * 1000.0 * weight / total_weight
        times.append(cursor_ms)
        durations.append(word_duration_ms)
        cursor_ms += word_duration_ms
    return words, times, durations


def _synthesize(text: str, voice: str, speed: float) -> SynthesizeResponse:
    import numpy as np
    import soundfile as sf

    style_path = _resolve_style(voice)
    model = _load_model()
    style = _load_style(str(style_path))
    language = settings.tts_language
    steps = int(settings.tts_supertonic_steps)

    audio_batch, durations = model(text, language, style, steps, speed)

    duration = float(durations[0])
    audio = np.asarray(
        audio_batch[0][: int(model.sample_rate * duration)], dtype=np.float32
    )
    if audio.size == 0:
        raise ValueError("Supertonic returned empty audio.")
    if not np.isfinite(audio).all():
        raise ValueError("Supertonic returned non-finite audio samples.")

    # Normalize each streamed segment to -1 dBFS without clipping.
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio *= (10 ** (-1 / 20)) / peak

    wav = BytesIO()
    sf.write(wav, audio, model.sample_rate, format="WAV", subtype="PCM_16")
    encoded_audio = base64.b64encode(wav.getvalue()).decode("ascii")
    words, wtimes, wdurations = _approximate_word_timings(
        text, audio.size / model.sample_rate
    )
    return SynthesizeResponse(
        audio_base64=encoded_audio,
        audio_encoding="wav",
        words=words,
        wtimes=wtimes,
        wdurations=wdurations,
        sample_rate=model.sample_rate,
    )


def _initialize_model() -> None:
    """Load the ONNX model and configured voice style before serving."""
    _load_model()
    _load_style(str(_resolve_style(settings.tts_voice)))


class SupertonicProvider:
    async def initialize(self) -> None:
        await asyncio.to_thread(_initialize_model)

    async def synthesize_segment(
        self, text: str, voice: str, speed: float
    ) -> SynthesizeResponse:
        text = text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Text cannot be empty.")
        if speed <= 0:
            raise HTTPException(
                status_code=400,
                detail="Speech speed must be greater than zero.",
            )
        try:
            return await asyncio.to_thread(_synthesize, text, voice, speed)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Supertonic speech synthesis failed.",
            ) from exc
