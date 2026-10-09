from typing import Protocol
from backend.schemas import SynthesizeRequest, SynthesizeResponse

class TTSProvider(Protocol):
    async def synthesize(self, request: SynthesizeRequest) -> SynthesizeResponse: ...
