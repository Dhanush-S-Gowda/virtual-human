from collections.abc import AsyncIterator
from typing import Protocol


class LLMProvider(Protocol):
    async def health_check(self) -> bool: ...
    def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...
