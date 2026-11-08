"""Streaming must deliver playable, ordered chunks before synthesis finishes."""

import asyncio
import base64
import csv
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


class ByteChunks(httpx.AsyncByteStream):
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def __aiter__(self):
        for item in self.items:
            yield item

    async def aclose(self):
        self.closed = True


def chunk(word):
    return SynthesizeResponse(audio_base64="YQ==", audio_encoding="wav",
                              words=[word], wtimes=[0], wdurations=[100])


def captioned_payload():
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24000)
    return {"audio": base64.b64encode(audio.getvalue()).decode(), "timestamps": [
        {"word": "Hello", "start_time": .1, "end_time": .4},
        {"word": "there", "start_time": .5, "end_time": .9}]}


@pytest.mark.asyncio
async def test_captioned_speech_preserves_provider_word_intervals():
    metrics = {}
    def respond(request):
        payload = json.loads(request.content)
        assert request.url.path == "/dev/captioned_speech"
        assert payload["response_format"] == "wav"
        assert payload["return_timestamps"] and not payload["stream"]
        return httpx.Response(200, json=captioned_payload())
    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        results = [item async for item in provider.stream_synthesize("Hello there.", "af_heart", 1, metrics)]
        assert len(results) == 1
        assert results[0].words == ["Hello", "there"]
        assert results[0].wtimes == [100, 500]
        assert results[0].wdurations == pytest.approx([300, 400])
        assert results[0].timing_quality == "provider"
        assert metrics["kokoro_total_ms"] >= 0
    finally:
        await provider.close()


@pytest.mark.parametrize("bad", [None, [], [{"word": "Hello", "start_time": -1, "end_time": -.5}],
                                    [{"word": "Hello", "start_time": 0, "end_time": 9}]])
@pytest.mark.asyncio
async def test_missing_or_invalid_provider_timestamps_fail_without_estimates(bad):
    provider = KokoroProvider()
    data = {**captioned_payload(), "timestamps": bad}
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=data)))
    try:
        with pytest.raises(HTTPException) as error:
            _ = [item async for item in provider.stream_synthesize("Hello", "af_heart", 1)]
        assert error.value.status_code == 502
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
    monkeypatch.setattr(settings, "min_speech_segment_words", 1)
    monkeypatch.setattr(settings, "tts_max_concurrent_segments", 2)
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
    stream = ByteChunks([json.dumps(captioned_payload()).encode()])
    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)))
    chunks = provider.stream_synthesize("Hello there", "af_heart", 1)
    try:
        first = await anext(chunks)
        assert first.words == ["Hello", "there"]
        await chunks.aclose()
        assert stream.closed
    finally:
        await provider.close()


def test_metrics_csv_schema_expands_without_losing_existing_rows(monkeypatch, tmp_path):
    metrics_file = tmp_path / "metrics.csv"
    metrics_file.write_text("stt_provider,status,historical_field\r\nnemotron,ok,keep-me\r\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "_METRICS_FILE", metrics_file)
    pipeline._append_metrics_csv({"status": "ok", "tts_segment_ms": [120.0],
                                  "kokoro_request_first_byte_ms": [1.5]})
    with metrics_file.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 2
    assert rows[0]["stt_provider"] == "nemotron"
    assert rows[0]["historical_field"] == "keep-me"
    assert rows[0]["llm_gesture_parse_ms"] == ""
    assert rows[1]["status"] == "ok"
    assert rows[1]["tts_segment_ms_json"] == "[120.0]"
    assert rows[1]["kokoro_request_first_byte_ms_json"] == "[1.5]"
    assert "gesture_audio_alignment_ms" in rows[1]


@pytest.mark.asyncio
async def test_request_limit_is_shared_between_simultaneous_turns(monkeypatch):
    monkeypatch.setattr(settings, "tts_max_concurrent_segments", 1)
    first_entered, release = asyncio.Event(), asyncio.Event()
    active, maximum = 0, 0

    class BlockingBytes(ByteChunks):
        async def __aiter__(self):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            first_entered.set()
            try:
                await release.wait()
                async for data in super().__aiter__():
                    yield data
            finally:
                active -= 1

    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=BlockingBytes([json.dumps(captioned_payload()).encode()]))))

    async def collect():
        return [item async for item in provider.stream_synthesize("Hello", "af_heart", 1)]

    first = asyncio.create_task(collect())
    await asyncio.wait_for(first_entered.wait(), 1)
    second = asyncio.create_task(collect())
    try:
        await asyncio.sleep(0)
        assert maximum == 1
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert all(len(result) == 1 for result in results)
        assert maximum == 1
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await provider.close()


@pytest.mark.asyncio
async def test_trimmed_first_word_is_clipped_without_shifting_provider_timeline():
    data = captioned_payload()
    # Captured from the real response to "Of course, Danush. ...".
    data["timestamps"] = [
        {"word": "Of", "start_time": -0.004583333333333328, "end_time": 0.17041666666666666},
        {"word": "course", "start_time": 0.17041666666666666, "end_time": 0.49541666666666667}]
    provider = KokoroProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=data)))
    try:
        result = await provider.synthesize_segment("Of course", "af_heart", 1)
        assert result.wtimes == pytest.approx([0, 170.41666666666666])
        assert result.wdurations == pytest.approx([170.41666666666666, 325])
        assert result.timing_quality == "provider"
        cues = StreamingCueAligner("Of course", [Cue(0, "greeting")]).feed(result.model_dump())
        assert cues == [{"action": "greeting", "at_ms": 0, "word_index": 0,
                         "duration_ms": pytest.approx(170.41666666666666)}]
    finally:
        await provider.close()


@pytest.mark.parametrize("timestamps", [
    [{"word": "bad", "start_time": .5, "end_time": .4}],
    [{"word": "bad", "start_time": float("nan"), "end_time": 1}],
    [{"word": "bad", "start_time": 0, "end_time": float("inf")}],
    [{"word": "first", "start_time": 0, "end_time": .2},
     {"word": "bad", "start_time": -.01, "end_time": .3}],
    [{"word": "first", "start_time": .2, "end_time": .3},
     {"word": "bad", "start_time": .1, "end_time": .4}],
])
def test_invalid_intervals_remain_rejected(timestamps):
    from tts.kokoro import validate_timestamps
    with pytest.raises(ValueError, match="invalid interval"):
        validate_timestamps(timestamps)
