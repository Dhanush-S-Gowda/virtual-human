import json

import httpx
import pytest

from backend.memory.store import MemoryStore, MemoryReranker, normalize
from backend.memory.service import ConversationMemory
from backend.tests.test_backend import FakeLLM
from pipeline import Conversation


@pytest.fixture
def store(tmp_path):
    result = MemoryStore(tmp_path / "memory.sqlite3")
    result.initialize()
    return result


def test_restart_retention_and_owner_isolation(store):
    store.max_turns = 2
    first = store.save("a", "My code is 4817", "Saved", .8)
    store.put_vector(first, "test", [1, 0])
    store.save("b", "Private secret", "Other user")
    store.save("a", "second", "reply")
    store.save("a", "third", "reply")
    restored, speed = MemoryStore(store.database).restore("a", 8)
    assert [row["user_text"] for row in restored] == ["second", "third"]
    assert speed == .8
    assert store.turn(first) is None
    assert not store.candidates("a", "4817", (), vector=[1, 0], model="test")[0].get("semantic_score")
    store.clear("a")
    assert store.restore("a", 8) == ([], None)
    assert len(store.restore("b", 8)[0]) == 1


def test_semantic_paraphrase_and_exclusion(store):
    target = store.save("a", "I enjoy eating dosa", "Understood")
    store.put_vector(target, "test", [1, 0])
    other = store.save("b", "Secret favorite meal", "Private")
    store.put_vector(other, "test", [1, 0])
    candidates = store.candidates("a", "preferred breakfast", (), vector=[1, 0], model="test")
    assert MemoryReranker().rank("preferred breakfast", candidates)[0]["id"] == target
    assert store.candidates("a", "preferred breakfast", [target], vector=[1, 0], model="test") == []


@pytest.mark.parametrize("value", [[], [0, 0], [float("nan")], [True], "bad"])
def test_invalid_vectors_rejected(value):
    with pytest.raises(ValueError):
        normalize(value)


def test_reranker_prefers_correction_and_deduplicates(store):
    store.save("a", "My favorite food is rice", "Okay")
    latest = store.save("a", "Actually my favorite food changed to dosa", "Okay")
    candidates = store.candidates("a", "favorite food", ())
    for row in candidates:
        row["semantic_score"] = .9
    ranked = MemoryReranker().rank("favorite food", candidates + candidates)
    assert ranked[0]["id"] == latest
    assert len(ranked) == 2


@pytest.mark.asyncio
async def test_old_memory_reaches_model_after_restart(store):
    store.save("profile", "My project code is 4817", "I will remember")
    for number in range(12):
        store.save("profile", f"Unrelated topic {number}", "Understood")
    memory = ConversationMemory(MemoryStore(store.database))
    await memory.start()
    llm = FakeLLM()
    conversation = Conversation(llm, "profile", memory)
    await anext(conversation.stream_reply("What is my project code?"))
    sent = json.dumps(llm.calls[0])
    assert "4817" in sent
    assert "historical" in sent.lower()
    assert len(conversation.history_ids) == 8
    await memory.close()


@pytest.mark.asyncio
async def test_embedding_failure_uses_keyword_memory(store):
    class Offline:
        model = "test"

        async def embed(self, text):
            raise httpx.ConnectError("offline")

        async def close(self):
            pass

    store.save("a", "My project code is 4817", "Saved")
    memory = ConversationMemory(store, Offline())
    memory.available = True
    results, info = await memory.retrieve("a", "project code")
    assert "4817" in results[0]["user_said"]
    assert not info["semantic"]
    assert memory.embedding_status == "unavailable_keyword_fallback"
    await memory.close()


@pytest.mark.asyncio
async def test_failed_reply_is_not_saved(store):
    memory = ConversationMemory(store)
    await memory.start()
    llm = FakeLLM()
    llm.fail = True
    with pytest.raises(httpx.ConnectError):
        _ = [part async for part in Conversation(llm, "a", memory).stream_reply("Hello")]
    assert store.restore("a", 8)[0] == []
    await memory.close()


@pytest.mark.asyncio
async def test_completed_reply_and_preference_survive_restart(store):
    memory = ConversationMemory(store)
    await memory.start()
    conversation = Conversation(FakeLLM(), "a", memory)
    conversation.speech_speed_preference = .8
    await conversation.remember_turn("Hello", "Welcome")
    restored = Conversation(FakeLLM(), "a", memory)
    await restored.restore_history()
    assert restored.history[0]["content"] == "Hello"
    assert restored.speech_speed_preference == .8
    await memory.close()
