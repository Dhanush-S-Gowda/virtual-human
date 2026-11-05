"""Streaming must deliver playable, ordered chunks before synthesis finishes."""

import asyncio
import base64
import io
import json
import wave
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException, WebSocketDisconnect

from backend.tests.test_backend import FakeLLM, FakeWebSocket
from config import settings
from inline_gestures import Cue, StreamingCueAligner
from schemas import SynthesizeResponse
from tts.kokoro import KokoroProvider
import pipeline


class Lines(httpx.AsyncByteStream):
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def __aiter__(self):
        for item in self.items:
            yield (json.dumps(item) + "\n").encode()

    async def aclose(self):
        self.closed = True


def packet(word="Hello", start=0.0, audio=b"\x01\x00" * 2400):
    return {"audio": base64.b64encode(audio).decode(), "audio_format": "audio/pcm",
            "timestamps": [{"word": word, "start_time": start, "end_time": start + 0.1}]}


def chunk(word):
    return SynthesizeResponse(audio_base64="YQ==", audio_encoding="wav",
                              words=[word], wtimes=[0], wdurations=[100])


@pytest.mark.asyncio
async def test_pcm_chunks_become_independent_wavs_with_local_word_times():
    stream = Lines([packet(), packet("there", 0.125)])

    def respond(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["response_format"] == "pcm"
        return httpx.Response(200, stream=stream)

    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        results = [item async for item in provider.stream_synthesize("Hello there", "af_heart", 1)]
        assert len(results) == 2
        assert results[1].wtimes == pytest.approx([25])
        for result in results:
            with wave.open(io.BytesIO(base64.b64decode(result.audio_base64))) as wav:
                assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
                assert wav.getnframes() == 2400
        assert stream.closed
    finally:
        await provider.close()


@pytest.mark.parametrize("items", [[], [{"error": "failed"}], [{"audio": "???"}],
                                      [packet(audio=b"x")], [packet() | {"timestamps": None}],
                                      [packet() | {"audio_format": "audio/wav"}],
                                      [packet(start=float("inf"))]])
@pytest.mark.asyncio
async def test_invalid_or_empty_streams_fail_clearly_and_close(items):
    stream = Lines(items)
    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)))
    try:
        with pytest.raises(HTTPException) as error:
            _ = [item async for item in provider.stream_synthesize("Hello", "af_heart", 1)]
        assert error.value.status_code == 502
        assert stream.closed
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_http_and_timeout_errors_keep_service_status_codes():
    for failure, status in [(httpx.ReadTimeout("late"), 504), (None, 502)]:
        def respond(request):
            if failure:
                raise failure
            return httpx.Response(503)

        provider = KokoroProvider()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(HTTPException) as error:
                _ = [item async for item in provider.stream_synthesize("Hello", "af_heart", 1)]
            assert error.value.status_code == status
        finally:
            await provider.close()


def test_gestures_follow_chunk_word_offsets_and_never_repeat():
    aligner = StreamingCueAligner("Hello! Well done.", [Cue(0, "greeting"), Cue(7, "clapping")])
    assert aligner.feed({"words": ["Hello", "!"], "wtimes": [0, 100]}) == [
        {"action": "greeting", "at_ms": 0, "word_index": 0}]
    assert aligner.feed({"words": ["Well", "done", "."], "wtimes": [25, 100, 200]}) == [
        {"action": "clapping", "at_ms": 25, "word_index": 0}]
    assert aligner.feed({"words": [], "wtimes": []}) == []
    mismatch = StreamingCueAligner("Hello there", [Cue(6, "clapping")])
    assert mismatch.feed({"words": ["wrong"], "wtimes": [0]}) == []
    assert mismatch.feed({"words": ["Hello", "there"], "wtimes": [0, 100]}) == []


def make_socket(provider, monkeypatch, tmp_path, mode="conversation", llm=None):
    monkeypatch.setattr(settings, "tts_provider", "kokoro")
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=({"speaker": None}, {})))
    app = SimpleNamespace(state=SimpleNamespace(conversations=OrderedDict(), providers=SimpleNamespace(
        llm=llm or FakeLLM(), stt=None, tts=provider)))
    return FakeWebSocket(app, {"type": "start", "mode": mode, "text": "Hello there.", "voice": "af_heart"})


@pytest.mark.parametrize("mode", ["speak", "conversation"])
@pytest.mark.asyncio
async def test_first_chunk_reaches_client_before_provider_finishes(mode, monkeypatch, tmp_path):
    delivered = asyncio.Event()

    class StreamingTTS:
        async def stream_synthesize(self, text, voice, speed):
            yield chunk("Hello")
            await asyncio.wait_for(delivered.wait(), 1)
            yield chunk("there.")

    socket = make_socket(StreamingTTS(), monkeypatch, tmp_path, mode)
    original_send = socket.send_json

    async def send(event):
        await original_send(event)
        if event["type"] == "speech":
            delivered.set()

    socket.send_json = send
    await asyncio.wait_for(pipeline.handle_websocket(socket), 3)
    speech = [event for event in socket.events if event["type"] == "speech"]
    assert [event["chunk_index"] for event in speech] == [0, 1]
    metrics = next(event["metrics"] for event in socket.events if event["type"] == "metrics")
    assert metrics["tts_audio_chunks"] == 2
    assert metrics["status"] == "ok"


@pytest.mark.asyncio
async def test_concurrent_segments_stream_in_order_and_keep_later_gestures(monkeypatch, tmp_path):
    class LLM(FakeLLM):
        async def stream_chat(self, messages):
            yield "[greeting]First part. [clapping]Second part. "

    class StreamingTTS:
        async def stream_synthesize(self, text, voice, speed):
            if text.startswith("First"):
                await asyncio.sleep(0.02)
            for word in text.split():
                yield chunk(word)

    socket = make_socket(StreamingTTS(), monkeypatch, tmp_path, llm=LLM())
    await pipeline.handle_websocket(socket)
    speech = [event for event in socket.events if event["type"] == "speech"]
    assert [(event["index"], event["chunk_index"]) for event in speech] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [event["words"][0] for event in speech] == ["First", "part.", "Second", "part."]
    assert [cue["action"] for event in speech for cue in event["gestures"]] == ["greeting", "clapping"]


@pytest.mark.asyncio
async def test_midstream_failure_is_reported_without_replaying_audio(monkeypatch, tmp_path):
    class StreamingTTS:
        async def stream_synthesize(self, text, voice, speed):
            yield chunk("Hello")
            raise HTTPException(status_code=502, detail="broken stream")

    socket = make_socket(StreamingTTS(), monkeypatch, tmp_path)
    await pipeline.handle_websocket(socket)
    assert len([event for event in socket.events if event["type"] == "speech"]) == 1
    assert any(event["type"] == "error" and event.get("stage") == "tts" for event in socket.events)
    metrics = next(event["metrics"] for event in socket.events if event["type"] == "metrics")
    assert metrics["status"] == "partial_tts_failure"


@pytest.mark.parametrize("mode", ["speak", "conversation"])
@pytest.mark.asyncio
async def test_disconnect_closes_provider_generator(mode, monkeypatch, tmp_path):
    closed = asyncio.Event()

    class StreamingTTS:
        async def stream_synthesize(self, text, voice, speed):
            try:
                yield chunk("Hello")
                await asyncio.Event().wait()
            finally:
                closed.set()

    socket = make_socket(StreamingTTS(), monkeypatch, tmp_path, mode)
    original_send = socket.send_json

    async def send(event):
        if event["type"] == "speech":
            raise WebSocketDisconnect()
        await original_send(event)

    socket.send_json = send
    await asyncio.wait_for(pipeline.handle_websocket(socket), 1)
    assert closed.is_set()


@pytest.mark.parametrize("mode", ["speak", "conversation"])
@pytest.mark.asyncio
async def test_turn_cancellation_closes_stream_and_stops_workers(mode, monkeypatch, tmp_path):
    started, closed = asyncio.Event(), asyncio.Event()

    class StreamingTTS:
        async def stream_synthesize(self, text, voice, speed):
            try:
                started.set()
                yield chunk("Hello")
                await asyncio.Event().wait()
            finally:
                closed.set()

    socket = make_socket(StreamingTTS(), monkeypatch, tmp_path, mode)
    task = asyncio.create_task(pipeline.handle_websocket(socket))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_closing_stream_early_closes_http_response():
    stream = Lines([packet(), packet("there", 0.1)])
    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)))
    chunks = provider.stream_synthesize("Hello there", "af_heart", 1)
    try:
        await anext(chunks)
        await chunks.aclose()
        assert stream.closed
    finally:
        await provider.close()
