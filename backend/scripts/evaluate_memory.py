"""Check long-term recall with an isolated database and the configured local model."""

import asyncio
import json
from pathlib import Path
import tempfile
from time import perf_counter

from config import settings
from llm.openai import OpenAIProvider
from memory.service import ConversationMemory, OllamaEmbedder
from memory.store import MemoryStore
from pipeline import Conversation


async def run():
    results = []
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "evaluation.sqlite3"
        store = MemoryStore(database)
        store.initialize()
        facts = [("My project access number is 4817.", "4817"),
                 ("I enjoy eating idli for breakfast.", "idli"),
                 ("Actually my preferred breakfast has changed to dosa.", "dosa")]
        ids = [store.save("evaluation", text, "Understood.") for text, _ in facts]
        embedder = OllamaEmbedder(settings.memory.embedding_url, settings.memory.embedding_model, 3)
        memory = ConversationMemory(store, embedder)
        memory.available = True
        for turn_id in ids:
            await memory.index_turn(turn_id)
        for index in range(12):
            store.save("evaluation", f"Discuss unrelated topic {index}.", "Okay.")
        # A new store and conversation reproduce a process losing all in-memory history.
        memory.store = MemoryStore(database)
        provider = OpenAIProvider(settings.llm_url, settings.llm_model,
                                  api_key=settings.llm_api_key, tool_mode=settings.llm_tool_mode,
                                  extra_body=settings.llm_extra_body, max_tokens=settings.max_tokens,
                                  plan_temperature=settings.llm_plan_temperature)
        for question, expected in [("What project access number did I give you?", "4817"),
                                   ("What do I prefer for my morning meal now?", "dosa")]:
            conversation = Conversation(provider, "evaluation", memory)
            started = perf_counter()
            statistics = {}
            reply = "".join([part async for part in conversation.stream_reply(
                question, world_state={"status": "unavailable", "people": [], "speaker": None},
                statistics=statistics)])
            results.append({"question": question, "reply": reply, "expected": expected,
                            "passed": expected in reply.casefold(),
                            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                            "memory_hits": statistics.get("memory_hits"),
                            "retrieval_ms": statistics.get("memory_retrieval_ms")})
        await memory.close()
    print(json.dumps({"model": settings.llm_model, "results": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
