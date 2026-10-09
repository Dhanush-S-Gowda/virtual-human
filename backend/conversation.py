import asyncio
from collections import deque
from fastapi import HTTPException
from backend.config import settings

class Conversation:
    """Bounded history; routes allocate a separate instance per browser session."""
    def __init__(self, llm):
        self.llm = llm
        self.history = deque(maxlen=settings.history_turns * 2)
        self.lock = asyncio.Lock()

    async def reply(self, text, reset=False):
        if not settings.enabled:
            raise HTTPException(503, "LLM is disabled")
        if not text.strip():
            raise HTTPException(400, "Message cannot be empty")
        async with self.lock:
            if reset:
                self.history.clear()
            messages = [{"role": "system", "content": settings.system_prompt}, *self.history, {"role": "user", "content": text}]
            reply = await self.llm.chat(messages)
            # Failed provider calls never become conversation history.
            self.history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
            return {"reply": reply, "history_length": len(self.history)}
