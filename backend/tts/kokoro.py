import base64
import asyncio
import io
import logging
import re
import wave
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

import httpx
from fastapi import HTTPException

from config import settings
from schemas import SynthesizeResponse
from diagnostics import record

logger = logging.getLogger(__name__)
KOKORO_TIMEOUT_SECONDS = settings.timeout
_TIMING_WORDS = re.compile(r"[\w]+(?:['’][\w]+)*", re.UNICODE)
_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.IGNORECASE)


def _tts_auth_headers() -> dict[str, str] | None:
    api_key = settings.tts_api_key
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


def _speech_endpoint(base_url: str) -> str:
    """Build the speech route for either a service root or an OpenAI /v1 base URL."""
    base = base_url.rstrip("/")
    route = "/audio/speech" if base.endswith("/v1") else "/v1/audio/speech"
    return f"{base}{route}"


def estimate_word_timings(text: str, speed: float) -> tuple[list[str], list[float], list[float], float]:
    """Estimate word intervals from text and rate for low-latency PCM chunk playback."""
    matches = list(_TIMING_WORDS.finditer(text.strip()))
    tokens = [match.group() for match in matches]
    if not tokens:
        return [], [], [], 0.0
    weights = []
    source = text.strip()
    for index, match in enumerate(matches):
        letters = match.group()
        syllables = max(1, len(_VOWEL_GROUPS.findall(letters)))
        if letters.casefold().endswith("e") and not letters.casefold().endswith(("le", "ye")) and syllables > 1:
            syllables -= 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        gap = source[match.end():end]
        punctuation = 0.45 if any(mark in gap for mark in ",;:") else (
            0.8 if any(mark in gap for mark in ".!?") else 0.0)
        weights.append(syllables + punctuation)
    total_weight = sum(weights)
    estimated_duration_ms = total_weight * 260 / max(0.25, speed)
    times, durations = [], []
    cursor = 0.0
    for weight in weights:
        word_duration = estimated_duration_ms * weight / total_weight
        times.append(cursor)
        durations.append(word_duration)
        cursor += word_duration
    return tokens, times, durations, estimated_duration_ms


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

        start_ms = float(start) * 1000.0

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


async def synthesize_segment(text: str, voice: str, speed: float) -> SynthesizeResponse:
    text = text.strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    payload = {
        "model": settings.tts_model,
        "input": text,
        "voice": voice,
        "speed": speed,
        "response_format": "pcm",
    }

    endpoint = _speech_endpoint(settings.tts_url)

    logger.info(
        "Synthesizing text: %s",
        text,
    )

    try:
        async with httpx.AsyncClient(
            timeout=KOKORO_TIMEOUT_SECONDS,
        ) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=_tts_auth_headers(),
            )

            response.raise_for_status()

    except httpx.TimeoutException as exc:
        logger.exception("Kokoro request timed out.")

        raise HTTPException(
            status_code=504,
            detail="Kokoro synthesis timed out.",
        ) from exc

    except httpx.HTTPStatusError as exc:
        logger.exception("Kokoro returned HTTP error.")

        raise HTTPException(
            status_code=502,
            detail=(f"Kokoro returned an error: {exc.response.status_code}"),
        ) from exc

    except httpx.HTTPError as exc:
        logger.exception("Failed to communicate with Kokoro.")

        raise HTTPException(
            status_code=502,
            detail=("Unable to communicate with Kokoro."),
        ) from exc

    pcm = response.content

    if not pcm or len(pcm) % 2:
        raise HTTPException(status_code=502, detail="Kokoro returned incomplete PCM audio.")

    words, wtimes, wdurations, _ = estimate_word_timings(text, speed)
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)

    return SynthesizeResponse(
        audio_base64=base64.b64encode(wav_buffer.getvalue()).decode("ascii"),
        audio_encoding="wav",
        words=words,
        wtimes=wtimes,
        wdurations=wdurations,
        sample_rate=24000,
    )


class KokoroProvider:
    supports_timing_metrics = True

    def __init__(self):
        self._client = None
        self._request_slots = asyncio.Semaphore(settings.tts_max_concurrent_segments)

    async def initialize(self) -> None:
        """Reuse the HTTP connection; the service owns model loading."""
        self._get_client()

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=KOKORO_TIMEOUT_SECONDS)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def stream_synthesize(
        self, text: str, voice: str, speed: float, timing_metrics=None
    ) -> AsyncIterator[SynthesizeResponse]:
        """Stream raw PCM promptly, estimate local word times, and wrap playable WAV chunks."""
        if not text.strip():
            raise HTTPException(status_code=400, detail="Text cannot be empty.")
        sample_rate = 24000
        first_byte_ms = None
        timing_started = perf_counter()
        words, word_starts, word_durations, _estimated_duration_ms = estimate_word_timings(text, speed)
        timing_ms = (perf_counter() - timing_started) * 1000
        if timing_metrics is not None:
            timing_metrics["kokoro_word_timing_ms"] = (
                timing_metrics.get("kokoro_word_timing_ms", 0.0) + timing_ms)
        if not words:
            raise HTTPException(status_code=400, detail="Text has no words after normalization.")
        buffer_bytes = sample_rate * 2 // 4  # 250 ms keeps first playback responsive without tiny packets.
        pending = bytearray()
        sample_offset = 0
        word_offset = 0
        audio_chunks = 0
        slot_wait_started = perf_counter()
        await self._request_slots.acquire()
        queue_wait_ms = (perf_counter() - slot_wait_started) * 1000
        request_started = perf_counter()
        if timing_metrics is not None:
            timing_metrics["kokoro_queue_wait_ms"] = (
                timing_metrics.get("kokoro_queue_wait_ms", 0.0) + queue_wait_ms)
        try:
            async with self._get_client().stream(
                "POST", _speech_endpoint(settings.tts_url),
                json={"model": settings.tts_model, "input": text.strip(), "voice": voice,
                      "speed": speed, "response_format": "pcm"},
                headers=_tts_auth_headers(),
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if not chunk:
                        continue
                    if first_byte_ms is None:
                        first_byte_ms = (perf_counter() - request_started) * 1000
                        if timing_metrics is not None and timing_metrics.get("kokoro_first_byte_at") is None:
                            timing_metrics["kokoro_first_byte_at"] = perf_counter()
                    pending.extend(chunk)
                    while len(pending) >= buffer_bytes:
                        pcm = bytes(pending[:buffer_bytes])
                        del pending[:buffer_bytes]
                        if len(pcm) % 2:
                            pending[:0] = pcm[-1:]
                            pcm = pcm[:-1]
                        if pcm:
                            result = self._pcm_result(
                                pcm, sample_offset, words, word_starts, word_durations,
                                sample_rate,
                                word_offset,
                            )
                            sample_offset += len(pcm) // 2
                            word_offset += len(result.words)
                            audio_chunks += 1
                            yield result
            if len(pending) % 2:
                raise HTTPException(status_code=502, detail="Kokoro returned incomplete PCM samples.")
            if pending:
                result = self._pcm_result(
                    bytes(pending), sample_offset, words, word_starts, word_durations,
                    sample_rate, word_offset,
                )
                sample_offset += len(pending) // 2
                word_offset += len(result.words)
                audio_chunks += 1
                yield result
            if not sample_offset:
                raise HTTPException(status_code=502, detail="Kokoro returned an empty audio stream.")
            total_ms = (perf_counter() - request_started) * 1000
            if timing_metrics is not None:
                timing_metrics.setdefault("kokoro_request_first_byte_ms", []).append(
                    round(first_byte_ms if first_byte_ms is not None else total_ms, 3))
                if timing_metrics.get("kokoro_first_byte_ms") is None:
                    timing_metrics["kokoro_first_byte_ms"] = round(
                        first_byte_ms if first_byte_ms is not None else total_ms, 3)
                timing_metrics["kokoro_total_ms"] = (
                    timing_metrics.get("kokoro_total_ms", 0.0) + total_ms)
            record("tts", "audio_stream_complete", provider="kokoro", transport="pcm",
                   first_byte_ms=round(first_byte_ms if first_byte_ms is not None else total_ms, 3),
                   total_ms=round(total_ms, 3),
                   word_timing_ms=round(timing_ms, 3), audio_duration_ms=round(sample_offset * 1000 / sample_rate, 3),
                   audio_chunks=audio_chunks)
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Kokoro synthesis timed out.") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Kokoro streaming request failed.") from exc
        finally:
            self._request_slots.release()

    @staticmethod
    def _pcm_result(pcm, sample_offset, words, word_starts, word_durations,
                    sample_rate, word_offset):
        offset_ms = sample_offset * 1000 / sample_rate
        chunk_duration_ms = len(pcm) * 1000 / (sample_rate * 2)
        end_ms = offset_ms + chunk_duration_ms
        first_word = word_offset
        while first_word < len(words) and word_starts[first_word] < offset_ms:
            first_word += 1
        end_word = first_word
        while end_word < len(words) and word_starts[end_word] < end_ms:
            end_word += 1
        local_starts = [max(0.0, word_starts[index] - offset_ms) for index in range(first_word, end_word)]
        local_durations = [word_durations[index] for index in range(first_word, end_word)]
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return SynthesizeResponse(
            audio_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            audio_encoding="wav", words=words[first_word:end_word],
            wtimes=local_starts, wdurations=local_durations, sample_rate=sample_rate,
        )

    async def synthesize_segment(
        self, text: str, voice: str, speed: float
    ) -> SynthesizeResponse:
        return await synthesize_segment(text, voice, speed)
