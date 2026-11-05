"""Actual payload construction tested with local fakes, never a live LLM API."""

import asyncio
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import diagnostics
from llm.openai import OpenAIProvider
from pipeline import Conversation


class Replies:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    async def stream_chat(self, messages):
        self.requests.append(messages)
        yield next(self.replies)


@pytest.mark.asyncio
async def test_authentic_roles_preserved_without_automatic_cv_context():
    llm = Replies(["Hello.", "I can use the introduction in this conversation."])
    conversation = Conversation(llm, session_id="test-session")
    with patch("pipeline._get_scene_context", new_callable=AsyncMock) as scene:
        await collect(conversation.stream_reply("My name is Oh."))
        await collect(conversation.stream_reply("Do you know what my name is?"))
        scene.assert_not_awaited()
    messages = llm.requests[1]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "My name is Oh."
    assert messages[2]["content"] == "Hello."
    assert messages[3]["content"] == "Do you know what my name is?"
    assert "[Visual Scene Context]" not in messages[0]["content"]


@pytest.mark.asyncio
async def test_assistant_statement_is_not_turned_into_a_user_message():
    llm = Replies(["My name is Oh.", "I do not know your name yet."])
    conversation = Conversation(llm)
    await collect(conversation.stream_reply("What is your name?"))
    await collect(conversation.stream_reply("Do you know my name?"))
    assert llm.requests[1][2] == {"role": "assistant", "content": "My name is Oh."}
    assert not any(message["role"] == "user" and message["content"] == "My name is Oh."
                   for message in llm.requests[1])


async def collect(stream):
    return "".join([part async for part in stream])


@pytest.mark.asyncio
async def test_trace_matches_http_payload_and_does_not_log_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.settings, "diagnostics_enabled", True)
    entries = []
    monkeypatch.setattr(diagnostics, "emit", lambda kind, payload: entries.append(payload) if kind == "llm" else None)
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Hello."}}]}\n\ndata: [DONE]\n\n')

    client_class = httpx.AsyncClient
    def client(*args, **kwargs):
        return client_class(*args, **kwargs, transport=httpx.MockTransport(handler))

    provider = OpenAIProvider("http://fake.invalid/v1", "fake", api_key="never-log-this")
    messages = [{"role": "system", "content": "System"}, {"role": "user", "content": "Hello"}]
    with patch("llm.openai.httpx.AsyncClient", side_effect=client), diagnostics.scope(session_id="trace-test", turn_id="turn-1"):
        assert await collect(provider.stream_chat(messages)) == "Hello."
    assert entries[0]["payload"] == captured[0]
    assert entries[0]["payload"]["messages"] == messages
    assert entries[0]["session_id"] == "trace-test"
    assert entries[1]["request_id"] == entries[0]["request_id"]
    assert entries[1]["content"] == "Hello."
    assert "never-log-this" not in json.dumps(entries)
    assert "Authorization" not in json.dumps(entries)


@pytest.mark.asyncio
async def test_worker_span_records_native_worker_tid(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.settings, "diagnostics_enabled", True)
    entries = []
    monkeypatch.setattr(diagnostics, "emit", lambda kind, payload: entries.append(payload))
    @diagnostics.traced_stage("test.worker")
    def work():
        return threading.get_native_id()
    loop_tid = threading.get_native_id()
    with diagnostics.scope(turn_id="worker-test"):
        worker_tid = await asyncio.to_thread(work)
    assert worker_tid != loop_tid
    assert entries[0]["tid"] == worker_tid
    assert entries[1]["turn_id"] == "worker-test"
    assert entries[1]["duration_ms"] >= 0


def test_disabled_diagnostics_does_not_create_files(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.settings, "diagnostics_enabled", False)
    entries = []
    monkeypatch.setattr(diagnostics, "emit", lambda kind, payload: entries.append(payload))
    diagnostics.record("llm", "request", payload={"messages": []})
    assert list(tmp_path.iterdir()) == []
