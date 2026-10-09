from typing import Any
import logging
import httpx
from fastapi import HTTPException
from backend.schemas import SynthesizeRequest, SynthesizeResponse
from backend.config import settings
logger = logging.getLogger(__name__)
KOKORO_URL = settings.kokoro_url
KOKORO_TIMEOUT_SECONDS = settings.timeout

def validate_timestamps(
    timestamps: Any,
) -> tuple[list[str], list[float], list[float]]:

    if not isinstance(timestamps, list):
        raise ValueError(
            "Kokoro did not return timestamp data."
        )

    words: list[str] = []
    times: list[float] = []
    durations: list[float] = []

    for item in timestamps:

        if not isinstance(item, dict):
            continue

        word = item.get("word")

        start = item.get("start_time")

        end = item.get("end_time")

        if (
            not isinstance(word, str)
            or not word.strip()
        ):
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
        raise ValueError(
            "No usable word timestamps were returned."
        )

    return (
        words,
        times,
        durations,
    )


async def synthesize(
    request: SynthesizeRequest,
) -> SynthesizeResponse:

    text = request.text.strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    payload = {
        "model": "kokoro",

        "input": text,

        "voice": request.voice,

        "speed": request.speed,

        "response_format": "wav",

        "stream": False,
    }

    endpoint = (
        f"{KOKORO_URL}"
        "/dev/captioned_speech"
    )

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

        logger.exception(
            "Kokoro request timed out."
        )

        raise HTTPException(
            status_code=504,
            detail="Kokoro synthesis timed out.",
        ) from exc

    except httpx.HTTPStatusError as exc:

        logger.exception(
            "Kokoro returned HTTP error."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Kokoro returned an error: "
                f"{exc.response.status_code}"
            ),
        ) from exc

    except httpx.HTTPError as exc:

        logger.exception(
            "Failed to communicate with Kokoro."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to communicate with Kokoro."
            ),
        ) from exc

    try:

        data = response.json()

    except ValueError as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Kokoro returned invalid JSON."
            ),
        ) from exc

    if not isinstance(data, dict):

        raise HTTPException(
            status_code=502,
            detail=(
                "Unexpected Kokoro response."
            ),
        )

    encoded_audio = data.get("audio")

    if not isinstance(
        encoded_audio,
        str,
    ):

        raise HTTPException(
            status_code=502,
            detail=(
                "Kokoro response does not contain audio."
            ),
        )

    timestamps = data.get(
        "timestamps"
    )

    try:

        (
            words,
            wtimes,
            wdurations,
        ) = validate_timestamps(
            timestamps
        )

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

        sample_rate=24000,
    )



class KokoroProvider:
    async def synthesize(self, request: SynthesizeRequest) -> SynthesizeResponse:
        return await synthesize(request)
