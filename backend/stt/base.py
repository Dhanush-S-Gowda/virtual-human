from typing import Protocol


class STTProvider(Protocol):
    async def initialize(self) -> None: ...

    async def transcribe(self, body: bytes, content_type: str) -> dict: ...
