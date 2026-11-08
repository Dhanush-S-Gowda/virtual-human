import base64
import asyncio
import io
import wave
import logging
from contextlib import aclosing
from time import perf_counter
from typing import Any
from math import isfinite

import httpx
from fastapi import HTTPException

from config import settings
from schemas import SynthesizeResponse
from diagnostics import record

KOKORO_TIMEOUT_SECONDS = settings.timeout
logger = logging.getLogger(__name__)


def _tts_auth_headers() -> dict[str, str] | None:
    api_key = settings.tts_api_key
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


def validate_timestamps(
    timestamps: Any,
) -> tuple[list[str], list[float], list[float]]:

    if not isinstance(timestamps, list):
        raise ValueError("Kokoro did not return timestamp data.")

    words: list[str] = []
    times: list[float] = []
    durations: list[float] = []

    for item in timestamps:
        if not isinstance(item, dict):
            continue

        word = item.get("word")

        start = item.get("start_time")

        end = item.get("end_time")

        if not isinstance(word, str) or not word.strip():
            continue

        if not isinstance(start, (int, float)):
            continue

        if not isinstance(end, (int, float)):
            continue

        if (isinstance(start, bool) or isinstance(end, bool)
                or not isfinite(start) or not isfinite(end) or end < start or end < 0
                or (times and (start < 0 or start * 1000 < times[-1]))):
            raise ValueError(
                f"Kokoro returned an invalid interval for {word!r}: start={start}, end={end}.")

        # Kokoro may return the first word slightly before zero after audio
        # trimming. Preserve its measured end and clip only the unavailable
        # leading portion; do not shift the rest of the provider timeline.
        start_ms = max(0.0, float(start) * 1000.0)

        end_ms = float(end) * 1000.0

        duration_ms = max(
            0.0,
            end_ms - start_ms,
        )

        words.append(word)

        times.append(start_ms)

        durations.append(duration_ms)

    if not words:
        raise ValueError("No usable word timestamps were returned.")

    return (
        words,
        times,
        durations,
    )


def _captioned_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/dev/captioned_speech"):
        return base
    if base.endswith("/v1"):
        base = base[:-3]
    return base + "/dev/captioned_speech"


def _captioned_result(data) -> SynthesizeResponse:
    words, times, durations = validate_timestamps(data.get("timestamps"))
    audio = data.get("audio")
    if not isinstance(audio, str):
        raise ValueError("Kokoro returned no captioned audio.")
    decoded = base64.b64decode(audio, validate=True)
    with wave.open(io.BytesIO(decoded), "rb") as wav:
        duration_ms = wav.getnframes() * 1000 / wav.getframerate()
        sample_rate = wav.getframerate()
    if duration_ms <= 0 or any(start + duration > duration_ms + 50
                               for start, duration in zip(times, durations)):
        raise ValueError("Kokoro timestamps exceed the audio duration.")
    return SynthesizeResponse(audio_base64=audio, audio_encoding="wav", words=words,
                              wtimes=times, wdurations=durations, sample_rate=sample_rate,
                              timing_quality="provider")


class KokoroProvider:
    supports_timing_metrics = True

    def __init__(self):
        self._client = None
        self._request_slots = asyncio.Semaphore(settings.tts_max_concurrent_segments)
        self._warmup_task = None
        self._last_activity = None
        self._active_requests = 0

    async def initialize(self):
        self._get_client()

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=KOKORO_TIMEOUT_SECONDS)
        return self._client

    async def close(self):
        if self._warmup_task is not None:
            self._warmup_task.cancel()
            await asyncio.gather(self._warmup_task, return_exceptions=True)
            self._warmup_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def prepare_for_recording(self, voice, speed):
        """Start a best-effort silent warmup without delaying microphone input."""
        if (not settings.kokoro_warmup_on_recording or self._active_requests
                or self._request_slots.locked()
                or (self._warmup_task is not None and not self._warmup_task.done())
                or (self._last_activity is not None
                    and perf_counter() - self._last_activity < settings.kokoro_warmup_idle_seconds)):
            return False
        self._last_activity = perf_counter()
        self._warmup_task = asyncio.create_task(self._warm_for_recording(voice, speed))
        return True

    async def _warm_for_recording(self, voice, speed):
        # A real request may have started since this task was scheduled.
        if self._active_requests or self._request_slots.locked():
            return
        started = perf_counter()
        try:
            async with asyncio.timeout(settings.kokoro_warmup_timeout_seconds):
                await self.synthesize_segment("Ready to speak.", voice, speed)
            record("tts", "recording_warmup", status="ok",
                   elapsed_ms=round((perf_counter() - started) * 1000, 2))
        except Exception as exc:
            # Warmup is optional; its failures must not fail the user's turn.
            logger.debug("Kokoro recording warmup skipped: %s", type(exc).__name__)
            record("tts", "recording_warmup", status="failed",
                   error_type=type(exc).__name__)

    async def stream_synthesize(self, text, voice, speed, timing_metrics=None):
        """Yield a complete captioned segment; never substitute estimated timings."""
        if not text.strip():
            raise HTTPException(status_code=400, detail="Text cannot be empty.")
        waiting = perf_counter()
        async with self._request_slots:
            started = perf_counter()
            self._active_requests += 1
            if timing_metrics is not None:
                timing_metrics["kokoro_queue_wait_ms"] = timing_metrics.get("kokoro_queue_wait_ms", 0) + (started - waiting) * 1000
            try:
                response = await self._get_client().post(
                    _captioned_endpoint(settings.tts_url),
                    json={"model": settings.tts_model, "input": text.strip(), "voice": voice,
                          "speed": speed, "response_format": "wav", "stream": False,
                          "return_timestamps": True}, headers=_tts_auth_headers())
                response.raise_for_status()
                result = _captioned_result(response.json())
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail="Kokoro synthesis timed out.") from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail="Kokoro captioned speech request failed.") from exc
            except (ValueError, TypeError, AttributeError, wave.Error, EOFError) as exc:
                raise HTTPException(status_code=502, detail="Kokoro returned invalid audio or word timestamps.") from exc
            finally:
                self._active_requests -= 1
                self._last_activity = perf_counter()
            elapsed = (perf_counter() - started) * 1000
            if timing_metrics is not None:
                timing_metrics.setdefault("kokoro_request_first_byte_ms", []).append(round(elapsed, 3))
                timing_metrics.setdefault("kokoro_first_byte_at", perf_counter())
                timing_metrics.setdefault("kokoro_first_byte_ms", round(elapsed, 3))
                timing_metrics["kokoro_total_ms"] = timing_metrics.get("kokoro_total_ms", 0) + elapsed
            record("tts", "captioned_segment_complete", provider="kokoro", total_ms=elapsed,
                   word_count=len(result.words), timing_quality="provider")
            yield result

    async def synthesize_segment(self, text, voice, speed):
        async with aclosing(self.stream_synthesize(text, voice, speed)) as chunks:
            async for result in chunks:
                return result


async def synthesize_segment(text: str, voice: str, speed: float) -> SynthesizeResponse:
    provider = KokoroProvider()
    try:
        return await provider.synthesize_segment(text, voice, speed)
    finally:
        await provider.close()
