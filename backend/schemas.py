from pydantic import BaseModel


class SynthesizeResponse(BaseModel):
    """Audio and word timings required by the TalkingHead lip-sync API."""

    audio_base64: str
    audio_encoding: str
    words: list[str]
    wtimes: list[float]
    wdurations: list[float]
    sample_rate: int = 24000
