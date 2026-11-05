import base64
import binascii
import io
import json
import logging
import wave
from collections.abc import AsyncIterator
from math import isfinite
from typing import Any

import httpx
from fastapi import HTTPException

from config import settings
from schemas import SynthesizeResponse

logger = logging.getLogger(__name__)
KOKORO_TIMEOUT_SECONDS = settings.timeout


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
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "speed": speed,
        "response_format": "wav",
        "stream": False,
    }

    endpoint = f"{settings.tts_url}/dev/captioned_speech"

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

    try:
        data = response.json()

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=("Kokoro returned invalid JSON."),
        ) from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail=("Unexpected Kokoro response."),
        )

    encoded_audio = data.get("audio")

    if not isinstance(
        encoded_audio,
        str,
    ):
        raise HTTPException(
            status_code=502,
            detail=("Kokoro response does not contain audio."),
        )

    timestamps = data.get("timestamps")

    try:
        (
            words,
            wtimes,
            wdurations,
        ) = validate_timestamps(timestamps)

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    return SynthesizeResponse(
        audio_base64=encoded_audio,
        audio_encoding="wav",
        words=words,
        wtimes=wtimes,
        wdurations=wdurations,
        sample_rate=settings.tts_sample_rate,
    )


class KokoroProvider:
    def __init__(self):
        self._client = None

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
        self, text: str, voice: str, speed: float
    ) -> AsyncIterator[SynthesizeResponse]:
        """Yield playable WAV chunks with times relative to each chunk.

        Kokoro sends one continuous audio stream, not independent WAV files.
        Request PCM and wrap each piece so existing avatar playback can decode it.
        Provider timestamps are relative to the whole request, in seconds.
        """
        if not text.strip():
            raise HTTPException(status_code=400, detail="Text cannot be empty.")
        sample_rate = 24000  # Kokoro's native mono, signed 16-bit PCM output.
        samples = 0
        emitted = False
        try:
            async with self._get_client().stream(
                "POST", f"{settings.tts_url}/dev/captioned_speech",
                json={"model": "kokoro", "input": text.strip(), "voice": voice,
                      "speed": speed, "response_format": "pcm", "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict) or not isinstance(data.get("audio"), str):
                            raise ValueError("Kokoro stream is missing audio.")
                        if data.get("audio_format") != "audio/pcm":
                            raise ValueError("Kokoro stream did not return PCM audio.")
                        audio = base64.b64decode(data["audio"], validate=True)
                        if not audio:
                            continue
                        if len(audio) % 2:
                            raise ValueError("Kokoro returned incomplete PCM samples.")
                        words, times, durations = validate_timestamps(data.get("timestamps"))
                        if not all(isfinite(value) and value >= 0 for value in [*times, *durations]):
                            raise ValueError("Kokoro returned invalid word times.")
                    except (ValueError, binascii.Error) as exc:
                        raise HTTPException(status_code=502, detail=f"Invalid Kokoro stream: {exc}") from exc
                    origin_ms = samples * 1000 / sample_rate
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(sample_rate)
                        wav.writeframes(audio)
                    samples += len(audio) // 2
                    emitted = True
                    yield SynthesizeResponse(
                        audio_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
                        audio_encoding="wav", words=words,
                        wtimes=[max(0.0, time - origin_ms) for time in times],
                        wdurations=durations, sample_rate=sample_rate,
                    )
            if not emitted:
                raise HTTPException(status_code=502, detail="Kokoro returned an empty audio stream.")
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Kokoro synthesis timed out.") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Kokoro streaming request failed.") from exc

    async def synthesize_segment(
        self, text: str, voice: str, speed: float
    ) -> SynthesizeResponse:
        return await synthesize_segment(text, voice, speed)
