"""Verify the real socket protocol and final-only LLM handoff with fake ASR."""

import asyncio
import json
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import wave

import pytest
from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings
import pipeline
from stt.nemotron import NemotronProvider, NemotronStream
from backend.tests.test_backend import FakeLLM, FakeTTS, FakeWebSocket


@pytest.mark.asyncio
async def test_connection_refusal_reaches_frontend_with_actionable_message(monkeypatch):
    monkeypatch.setattr(settings, "nemotron_url", "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime")
    monkeypatch.setattr("stt.nemotron.connect", AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused")))
    app = SimpleNamespace(state=SimpleNamespace(
        conversations=OrderedDict(), providers=SimpleNamespace(
            llm=FakeLLM(), tts=FakeTTS(), stt=NemotronProvider())))
    client = FakeWebSocket(app, {"type": "start", "mime_type": "audio/pcm", "sample_rate": 16000}, [])
    await pipeline.handle_websocket(client)
    error = next(event for event in client.events if event.get("type") == "error")
    assert "Cannot connect to the Nemotron speech server at 127.0.0.1:8080" in error["message"]
    assert "nemotron_url" in error["message"]
    assert "Start your nemo-speech server" in error["message"]


@pytest.mark.asyncio
async def test_live_socket_audio_precedes_release_and_llm_gets_only_full_transcript(tmp_path, monkeypatch):
    received_audio = asyncio.Event()
    llm = FakeLLM()
    packets = []

    async def server(socket):
        await socket.send(json.dumps({"type": "session.created"}))
        update = json.loads(await socket.recv())
        assert update["session"]["sample_rate"] == 16000
        assert update["session"]["language"] == "en-US"
        await socket.send(json.dumps({"type": "session.updated"}))
        async for message in socket:
            if isinstance(message, bytes):
                packets.append(message)
                await socket.send(json.dumps({"type": "conversation.item.input_audio_transcription.delta",
                                              "delta": "unstable words"}))
                if len(packets) == 1:
                    await socket.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                                                  "transcript": "Hello"}))
                received_audio.set()
            else:
                assert json.loads(message)["type"] == "input_audio_buffer.commit"
                assert not llm.calls
                await socket.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                                              "transcript": "my name is Alex."}))
                await socket.send(json.dumps({"type": "input_audio_buffer.committed"}))

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        monkeypatch.setattr(settings, "nemotron_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
        monkeypatch.setattr(pipeline, "_forward_transcript_to_cv", AsyncMock())
        app = SimpleNamespace(state=SimpleNamespace(
            providers=SimpleNamespace(stt=NemotronProvider(), llm=llm, tts=FakeTTS()),
            conversations=OrderedDict()))

        class LiveClient(FakeWebSocket):
            async def receive(self):
                try:
                    message = await super().receive()
                except RuntimeError:  # Exhausted fake input: remain connected until finalization.
                    await asyncio.Future()
                if message.get("text"):
                    await asyncio.wait_for(received_audio.wait(), 1)
                    assert not llm.calls  # Recording has reached ASR, but LLM has not started.
                return message

        client = LiveClient(app, {"type": "start", "mime_type": "audio/pcm", "sample_rate": 16000}, [
            {"type": "websocket.receive", "bytes": b"\x01\x00" * 1600},
            {"type": "websocket.receive", "text": '{"type":"end"}'},
        ])
        await pipeline.handle_websocket(client)
        assert packets == [b"\x01\x00" * 1600]
        transcript = next(event for event in client.events if event["type"] == "transcript")
        assert transcript["text"] == "Hello my name is Alex."
        partial = next(event for event in client.events if event["type"] == "transcript_partial")
        assert partial["text"] == "unstable words"
        assert client.events.index(partial) < client.events.index(transcript)
        assert llm.calls[0][-1]["content"].endswith("User question:\nHello my name is Alex.")
        assert "unstable words" not in llm.calls[0][-1]["content"]


class Socket:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def recv(self):
        return json.dumps(await self.events.get())

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_silence_commit_returns_empty_transcript_without_using_partials():
    socket = Socket()
    stream = NemotronStream(socket, 1, "en-US")
    socket.events.put_nowait({"type": "conversation.item.input_audio_transcription.delta", "delta": "noise"})
    socket.events.put_nowait({"type": "input_audio_buffer.committed"})
    try:
        assert (await stream.finish())["text"] == ""
    finally:
        await stream.close()
    assert stream.receiver.done() and socket.closed


@pytest.mark.asyncio
async def test_server_errors_and_finalization_timeout_are_reported():
    socket = Socket()
    stream = NemotronStream(socket, .01, "en-US")
    socket.events.put_nowait({"type": "error", "error": {"message": "model unavailable"}})
    try:
        with pytest.raises(ValueError, match="model unavailable"):
            await stream.finish()
    finally:
        await stream.close()
    stream = NemotronStream(Socket(), .01, "en-US")
    try:
        with pytest.raises(ValueError, match="timed out"):
            await stream.finish()
    finally:
        await stream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["cancel", "disconnect"])
async def test_cancel_or_disconnect_closes_upstream_without_llm(command):
    socket = Socket()
    stream = NemotronStream(socket, 1, "en-US")
    llm = FakeLLM()
    provider = SimpleNamespace(open_stream=AsyncMock(return_value=stream))
    app = SimpleNamespace(state=SimpleNamespace(providers=SimpleNamespace(stt=provider, llm=llm),
                                               conversations=OrderedDict()))
    last = ({"type": "websocket.disconnect"} if command == "disconnect"
            else {"type": "websocket.receive", "text": '{"type":"cancel"}'})
    client = FakeWebSocket(app, {"type": "start", "mime_type": "audio/pcm"}, [
        {"type": "websocket.receive", "bytes": b"\x00\x00"}, last,
    ])
    await pipeline.handle_websocket(client)
    assert socket.closed and stream.receiver.done()
    assert not llm.calls
    assert not any(isinstance(message, str) and "commit" in message for message in socket.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["cancel", "disconnect"])
async def test_cancel_during_finalization_stops_upstream_without_llm(command):
    socket = Socket()
    stream = NemotronStream(socket, 120, "en-US")
    llm = FakeLLM()
    app = SimpleNamespace(state=SimpleNamespace(conversations=OrderedDict(), providers=SimpleNamespace(
        stt=SimpleNamespace(open_stream=AsyncMock(return_value=stream)), llm=llm)))
    last = ({"type": "websocket.disconnect"} if command == "disconnect"
            else {"type": "websocket.receive", "text": '{"type":"cancel"}'})
    client = FakeWebSocket(app, {"type": "start", "mime_type": "audio/pcm"}, [
        {"type": "websocket.receive", "bytes": b"\x00\x00"},
        {"type": "websocket.receive", "text": '{"type":"end"}'}, last,
    ])
    await asyncio.wait_for(pipeline.handle_websocket(client), 1)
    assert socket.closed and stream.receiver.done()
    assert not llm.calls


@pytest.mark.asyncio
async def test_pcm_is_wrapped_as_wav_for_nonstreaming_provider(tmp_path, monkeypatch):
    async def transcribe(audio, content_type):
        assert content_type == "audio/wav"
        with wave.open(BytesIO(audio), "rb") as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 48000)
            assert wav.readframes(10) == b"\x01\x00" * 10
        return {"text": ""}

    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline, "_forward_transcript_to_cv", AsyncMock())
    app = SimpleNamespace(state=SimpleNamespace(conversations=OrderedDict(), providers=SimpleNamespace(
        llm=FakeLLM(), stt=SimpleNamespace(transcribe=transcribe))))
    client = FakeWebSocket(app, {"type": "start", "mime_type": "audio/pcm", "sample_rate": 48000}, [
        {"type": "websocket.receive", "bytes": b"\x01\x00" * 10},
        {"type": "websocket.receive", "text": '{"type":"end"}'},
    ])
    await pipeline.handle_websocket(client)
    assert any(event["type"] == "done" for event in client.events)
