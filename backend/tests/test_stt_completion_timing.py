"""STT completion is a browser receipt, independent of speaking and socket close."""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.tests.test_backend import FakeLLM, FakeTTS, FakeWebSocket
from main import create_app
import pipeline
import stt_metrics


def test_receipts_are_one_use_bounded_and_expire(monkeypatch):
    clock = [0.]
    rows = []
    monkeypatch.setattr(stt_metrics, "measure", lambda *args, **kwargs: rows.append((args, kwargs)))
    receipts = stt_metrics.TranscriptMeasurements(limit=2, ttl_seconds=10, clock=lambda: clock[0])
    old = receipts.issue({"turn_id": "old"})
    receipts.issue({})
    active = receipts.issue({"turn_id": "active"}, recording_ms=9000.)
    assert not receipts.receive(old, 40.)
    assert not receipts.receive(active, float("nan"))
    assert receipts.receive(active, 40.)
    assert not receipts.receive(active, 50.)
    assert rows[0][0] == ("stt_completion_ms", 40.)
    assert rows[0][1]["timing_boundary"] == stt_metrics.STT_TIMING_BOUNDARY
    assert rows[0][1]["recording_ms"] == 9000.
    expired = receipts.issue({})
    clock[0] = 11.
    assert not receipts.receive(expired, 1.)


@pytest.mark.asyncio
async def test_receipt_endpoint_rejects_invalid_values_and_duplicate_samples(monkeypatch):
    rows = []
    monkeypatch.setattr(stt_metrics, "measure", lambda *args, **kwargs: rows.append((args, kwargs)))
    app = create_app()
    identifier = app.state.transcript_measurements.issue({"turn_id": "test"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/telemetry/stt", json={"measurement_id": identifier, "elapsed_ms": -1})
        assert response.status_code == 422
        payload = {"measurement_id": identifier, "elapsed_ms": 75.}
        assert (await client.post("/telemetry/stt", json=payload)).json() == {"accepted": True}
        assert (await client.post("/telemetry/stt", json=payload)).json() == {"accepted": False}
    assert len(rows) == 1 and rows[0][0][1] == 75.


@pytest.mark.asyncio
async def test_slow_socket_cleanup_cannot_delay_transcript(monkeypatch, tmp_path):
    clock = [0.]
    closing = asyncio.Event()
    transcript_sent = asyncio.Event()
    transcript_times = []

    class Stream:
        async def send_audio(self, chunk):
            clock[0] = 10.  # Ten seconds of speaking are not post-release STT time.

        async def finish(self):
            clock[0] += .05
            return {"text": "Hello", "stream_metrics": {"finalization_ms": 50.}}

        async def close(self):
            await closing.wait()
            clock[0] += 3.

    stream = Stream()
    monkeypatch.setattr(pipeline, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=(
        {"status": "unavailable", "people": [], "objects": [], "speaker": None}, {})))
    monkeypatch.setattr(pipeline, "_forward_transcript_to_cv", AsyncMock())
    providers = SimpleNamespace(stt=SimpleNamespace(open_stream=AsyncMock(return_value=stream)), llm=FakeLLM(), tts=FakeTTS())
    app = create_app(providers)
    app.state.providers = providers
    app.state.conversations = OrderedDict()

    class Client(FakeWebSocket):
        async def receive(self):
            try:
                return next(self.incoming)
            except StopIteration:
                await asyncio.Future()

        async def send_json(self, event):
            if event["type"] == "transcript":
                assert not closing.is_set()
                transcript_times.append(clock[0])
                transcript_sent.set()
            await super().send_json(event)

    client = Client(app, {"type": "start", "mime_type": "audio/pcm", "sample_rate": 16000}, [
        {"type": "websocket.receive", "bytes": b"\0\0" * 10},
        {"type": "websocket.receive", "text": '{"type":"end"}'},
    ])
    task = asyncio.create_task(pipeline.handle_websocket(client))
    try:
        await asyncio.wait_for(transcript_sent.wait(), 1)
        assert transcript_times == [10.05]
    finally:
        closing.set()
        await task
    metrics = next(event["metrics"] for event in client.events if event["type"] == "metrics")
    assert metrics["release_to_transcript_ms"] == 50.
