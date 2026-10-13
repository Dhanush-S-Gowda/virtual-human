from typing import Protocol

from schemas import SynthesizeResponse


class TTSProvider(Protocol):
    async def initialize(self) -> None: ...

    async def synthesize_segment(
        self, text: str, voice: str, speed: float
    ) -> SynthesizeResponse: ...
