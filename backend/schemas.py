from pydantic import BaseModel, Field

class SynthesizeRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=2000,
    )

    voice: str = "af_bella"

    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
    )


class SynthesizeResponse(BaseModel):
    audio_base64: str

    audio_encoding: str

    words: list[str]

    wtimes: list[float]

    wdurations: list[float]

    sample_rate: int = 24000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    reset_history: bool = False
    session_id: str = Field(default="default", min_length=1, max_length=128)


class ChatResponse(BaseModel):
    reply: str
    history_length: int


class LLMHealthResponse(BaseModel):
    available: bool
    enabled: bool
    base_url: str
    model: str


