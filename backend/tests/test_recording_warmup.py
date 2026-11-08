"""Silent warmup must not block recording, duplicate work, or leak tasks."""

import asyncio
from unittest.mock import Mock

import httpx
import pytest

from backend.tests.test_kokoro_streaming import captioned_payload, make_socket
from config import settings
import pipeline
from tts.kokoro import KokoroProvider


@pytest.fixture(autouse=True)
def warmup_settings(monkeypatch):
    monkeypatch.setattr(settings, "kokoro_warmup_on_recording", True)
    monkeypatch.setattr(settings, "kokoro_warmup_idle_seconds", 15)
    monkeypatch.setattr(settings, "kokoro_warmup_timeout_seconds", 1)


@pytest.mark.asyncio
async def test_warmup_is_background_deduplicated_and_throttled(monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    requests = []

    async def respond(request):
        requests.append(request)
        started.set()
        await finish.wait()
        return httpx.Response(200, json=captioned_payload())

    clock = [100.0]
    monkeypatch.setattr("tts.kokoro.perf_counter", lambda: clock[0])
    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        assert provider.prepare_for_recording("af_heart", 1)
        await asyncio.wait_for(started.wait(), 1)
        assert not provider._warmup_task.done()
        assert not provider.prepare_for_recording("af_heart", 1)
        finish.set()
        assert await provider._warmup_task is None
        assert not provider.prepare_for_recording("af_heart", 1)
        clock[0] += 16
        assert provider.prepare_for_recording("af_heart", 1)
        await provider._warmup_task
        assert len(requests) == 2
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_disabled_warmup_does_not_create_work(monkeypatch):
    monkeypatch.setattr(settings, "kokoro_warmup_on_recording", False)
    provider = KokoroProvider()
    assert not provider.prepare_for_recording("af_heart", 1)
    assert provider._warmup_task is None
    await provider.close()


@pytest.mark.asyncio
async def test_real_synthesis_takes_priority_over_scheduled_warmup():
    started, finish = asyncio.Event(), asyncio.Event()
    requests = []

    async def respond(request):
        requests.append(request)
        started.set()
        await finish.wait()
        return httpx.Response(200, json=captioned_payload())

    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        # Queue real work first; it must not wait for a queued warmup to finish.
        actual = asyncio.create_task(provider.synthesize_segment("Hello there.", "af_heart", 1))
        assert provider.prepare_for_recording("af_heart", 1)
        await asyncio.wait_for(started.wait(), 1)
        await provider._warmup_task
        assert not provider.prepare_for_recording("af_heart", 1)
        finish.set()
        await actual
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize("failure", ["http", "timeout"])
@pytest.mark.asyncio
async def test_warmup_failures_are_best_effort(failure, monkeypatch):
    monkeypatch.setattr(settings, "kokoro_warmup_timeout_seconds", .01)

    async def respond(request):
        if failure == "timeout":
            await asyncio.Event().wait()
        return httpx.Response(503)

    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        assert provider.prepare_for_recording("af_heart", 1)
        assert await provider._warmup_task is None
        assert provider._active_requests == 0
        assert not provider.prepare_for_recording("af_heart", 1)
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_warmup_before_closing_client():
    started = asyncio.Event()

    async def respond(request):
        started.set()
        await asyncio.Event().wait()

    provider = KokoroProvider()
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    provider._client = client
    assert provider.prepare_for_recording("af_heart", 1)
    task = provider._warmup_task
    await asyncio.wait_for(started.wait(), 1)
    await provider.close()
    assert task.cancelled()
    assert client.is_closed
    assert provider._active_requests == 0


@pytest.mark.asyncio
async def test_audio_start_prepares_provider_without_sending_warmup_audio(monkeypatch, tmp_path):
    provider = KokoroProvider()
    provider.prepare_for_recording = Mock()
    socket = make_socket(provider, monkeypatch, tmp_path)
    socket.start.pop("text")
    socket.incoming = iter([{"type": "websocket.disconnect"}])
    await pipeline.handle_websocket(socket)
    provider.prepare_for_recording.assert_called_once_with("af_heart", settings.tts_speed)
    assert not any(event["type"] == "speech" for event in socket.events)
    await provider.close()


@pytest.mark.parametrize("mode", ["speak", "conversation"])
@pytest.mark.asyncio
async def test_text_requests_skip_recording_warmup(mode, monkeypatch, tmp_path):
    provider = KokoroProvider()
    provider.prepare_for_recording = Mock()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=captioned_payload())))
    try:
        socket = make_socket(provider, monkeypatch, tmp_path, mode=mode)
        await pipeline.handle_websocket(socket)
        provider.prepare_for_recording.assert_not_called()
    finally:
        await provider.close()
