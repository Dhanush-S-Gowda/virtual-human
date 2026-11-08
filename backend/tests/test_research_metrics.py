"""Research samples retain boundaries, failures, and scene/workload metadata."""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend import diagnostics
from backend.cv.events import Frame
from backend.cv.window_manager import WindowManager
from backend.tests.test_cv_windows import Objects, Faces, Points


@pytest.mark.asyncio
async def test_conversation_exports_each_stage_sample_once(monkeypatch, tmp_path):
    from backend.tests.test_backend import FakeLLM, FakeSTT, FakeTTS, FakeWebSocket
    import pipeline
    from main import create_app

    measured = []
    monkeypatch.setattr(pipeline, "measure", lambda metric, value, **fields: measured.append((metric, value, fields)))
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline, "_forward_transcript_to_cv", AsyncMock())
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=(
        {"status": "unavailable", "people": [], "objects": [], "speaker": None}, {})))
    providers = SimpleNamespace(llm=FakeLLM(), stt=FakeSTT(), tts=FakeTTS())
    app = create_app(providers)
    app.state.providers = providers
    app.state.conversations = OrderedDict()
    websocket = FakeWebSocket(app, {"type": "start", "mime_type": "audio/webm"}, [
        {"type": "websocket.receive", "bytes": b"sample"},
        {"type": "websocket.receive", "text": '{"type":"end"}'},
    ])
    await pipeline.handle_websocket(websocket)
    assert any(event["type"] == "done" for event in websocket.events)
    for key in ("tts_first_audio_ms", "llm_gesture_parse_ms",
                "gesture_cue_assignment_ms", "gesture_audio_alignment_ms"):
        values = [value for metric, value, _ in measured if metric == key]
        assert len(values) == 1 and values[0] >= 0, key
    assert not any(metric == "stt_completion_ms" for metric, _, _ in measured)
    transcript = next(event for event in websocket.events if event["type"] == "transcript")
    assert "stt_measurement_id" in transcript


@pytest.mark.asyncio
async def test_tts_measurement_includes_first_result_only_and_records_failures(monkeypatch):
    from backend.tests import test_backend
    import pipeline

    measured = []
    clock = [10.]
    monkeypatch.setattr(pipeline, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(pipeline, "measure", lambda metric, value, **fields: measured.append((metric, value, fields)))

    class Streaming:
        async def stream_synthesize(self, text, voice, speed):
            clock[0] += .02
            yield "first"
            clock[0] += .2
            yield "second"

    assert [chunk async for chunk in pipeline._synthesize_chunks(Streaming(), "Hello", "F1", 1., input_mode="speak")] == ["first", "second"]
    assert measured[0][1] == pytest.approx(20.)
    assert measured[0][2]["input_mode"] == "speak"
    assert len(measured) == 1

    class Broken:
        async def synthesize_segment(self, *args):
            raise ValueError("synthesis failed")

    with pytest.raises(ValueError):
        _ = [chunk async for chunk in pipeline._synthesize_chunks(Broken(), "Hello", "F1", 1.)]
    assert len(measured) == 2
    assert measured[-1][1] is None
    assert measured[-1][2]["status"] == "error_or_cancelled"


def test_measure_keeps_unknown_values_missing_and_errors_separate(monkeypatch):
    rows = []
    monkeypatch.setattr(diagnostics.settings, "diagnostics_enabled", True)
    monkeypatch.setattr(diagnostics, "emit", lambda stream, row: rows.append((stream, row)))
    monkeypatch.setenv("VH_MEASUREMENT_PHASE", "warmup")
    for value in (None, -1, float("nan"), float("inf"), True):
        diagnostics.measure("llm_ttft_ms", value)
    assert rows == []
    diagnostics.measure("llm_ttft_ms", 12.5, request_id="a")
    diagnostics.measure("llm_ttft_ms", None, status="error", request_id="b")
    assert len(rows) == 2
    assert all(stream == "performance" for stream, _ in rows)
    first, second = [row for _, row in rows]
    assert first["value"] == 12.5 and first["phase"] == "warmup"
    assert first["sample_id"] != second["sample_id"]
    assert second["value"] is None and second["status"] == "error"
    assert first["model"] == diagnostics.settings.llm_model


def test_cv_stage_uses_execution_duration_and_excludes_no_person_poll(monkeypatch):
    rows = []
    clock = [10.]
    monkeypatch.setattr(diagnostics.settings, "diagnostics_enabled", True)
    monkeypatch.setattr(diagnostics.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(diagnostics, "emit", lambda stream, row: rows.append((stream, row)))

    with diagnostics.stage("cv.keypoint_detection", people_count=0):
        clock[0] += .1
    assert not any(stream == "performance" for stream, _ in rows)
    with diagnostics.stage("cv.keypoint_detection", people_count=2, image_width=640, image_height=480):
        clock[0] += .04
    sample = next(row for stream, row in rows if stream == "performance")
    assert sample["value"] == pytest.approx(40.)
    assert sample["people_count"] == 2
    with pytest.raises(ValueError):
        with diagnostics.stage("cv.object_detection"):
            raise ValueError("failed")
    errors = [row for stream, row in rows if stream == "performance" and row["status"] != "ok"]
    assert len(errors) == 1 and errors[0]["value"] is None


@pytest.mark.asyncio
async def test_cv_throughput_counts_completions_not_window_revisions():
    manager = WindowManager(Objects(), Faces(), Points(), person_min_area_ratio=0.)
    manager._throughput_started = 0.
    try:
        manager.submit(Frame(1, .1, SimpleNamespace(shape=(10, 10, 3))))
        await manager.wait_idle()
        # Re-publishing the same observation must not inflate achieved FPS.
        manager._publish(manager._window(0))
        manager._publish(manager._window(0))
        sample = manager.throughput_sample(2.)
        assert sample["object_fps"] == .5
        assert sample["keypoint_fps"] == .5
        assert sample["image_width"] == 10
        assert manager.throughput_sample(3.) is None
        assert manager.throughput_sample(4.)["object_fps"] == 0.
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cv_no_person_polls_and_busy_skips_are_not_successful_landmark_frames():
    class Empty(Objects):
        async def detect(self, frame):
            return ()

    gate = asyncio.Event()
    manager = WindowManager(Objects(gate), Faces(), Points())
    manager._throughput_started = 0.
    try:
        manager.submit(Frame(1, .1, SimpleNamespace(shape=(10, 10, 3))))
        await asyncio.sleep(0)
        manager.submit(Frame(2, .2, SimpleNamespace(shape=(10, 10, 3))))
        sample = manager.throughput_sample(2.)
        assert sample["object_fps"] == 0.
        assert sample["object_busy_skip_percent"] == 50.
    finally:
        gate.set()
        await manager.close()
    manager = WindowManager(Empty(), Faces(), Points())
    manager._throughput_started = 0.
    try:
        manager.submit(Frame(1, .1, SimpleNamespace(shape=(10, 10, 3))))
        await manager.wait_idle()
        sample = manager.throughput_sample(2.)
        assert sample["object_fps"] == .5
        assert sample["keypoint_fps"] == 0.
        assert sample["keypoint_no_person"] == 1
    finally:
        await manager.close()
