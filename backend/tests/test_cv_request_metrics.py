"""CV fetch duration and capture freshness reach OpenObserve request records."""

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from backend.tests import test_backend  # Establish application import path.
from backend.config import settings
from backend.cv.window_manager import CVService, WindowManager
from backend.tests.cv.test_visual_history import add, record
from backend.tests.test_cv_windows import Objects, Faces, Points
from llm.openai import OpenAIProvider
from pipeline import Conversation, _METRICS_FIELDS
import pipeline
import world


@pytest.mark.asyncio
async def test_fetch_duration_stops_at_matching_response_before_socket_close(monkeypatch):
    clock = [10.]

    class Socket:
        async def send(self, message):
            self.request_id = json.loads(message)["request_id"]

        async def recv(self):
            clock[0] += .025
            return json.dumps({"type": "scene", "request_id": self.request_id, "data": {}})

    @asynccontextmanager
    async def connect(*args, **kwargs):
        clock[0] += .010
        yield Socket()
        clock[0] += .5

    monkeypatch.setattr(world.settings, "cv_adapter_url", "ws://fake.invalid")
    monkeypatch.setattr(world.websockets, "connect", connect)
    monkeypatch.setattr(world, "perf_counter", lambda: clock[0])
    events = []
    monkeypatch.setattr(world, "record", lambda *args, **fields: events.append((args, fields)))
    state, scene = await world.current_world()
    assert scene["cv_world_state_fetch_ms"] == 35.
    assert events[0][0] == ("cv", "world_state_fetch")
    assert events[0][1]["cv_world_state_fetch_ms"] == 35.
    assert "cv_world_state_fetch_ms" not in state


def test_history_freshness_uses_latest_included_result_not_window_end_or_pending_frame():
    manager = WindowManager(Objects(), Faces(), Points())
    add(manager, record(1.2))
    add(manager, replace(record(1.8), completed=(), objects=(), faces=(), keypoints=()))
    manager.advance(8.)
    assert manager.visual_history(10.)["latest_observation_timestamp"] == 1.2
    assert manager.visual_history(1.)["latest_observation_timestamp"] is None
    assert manager.visual_history(0.)["latest_observation_timestamp"] is None


@pytest.mark.parametrize("current", [True, False])
def test_scene_observation_age_includes_elapsed_time_since_tick(monkeypatch, current):
    config = SimpleNamespace(**{**vars(settings), "cv_world_history_seconds": 10., "cv_world_history_windows": 0})
    service = CVService(Objects(), Faces(), Points(), config)
    observed = record(1.2)
    add(service.manager, observed)
    service._latest_objects = observed
    service.manager.advance(2. if current else 8.)
    service._source_origin, service._clock_origin = 1., 100.
    monkeypatch.setattr("backend.cv.window_manager.asyncio.get_running_loop",
                        lambda: SimpleNamespace(time=lambda: 108.))
    monkeypatch.setattr("time.time", lambda: 1000.)
    scene = service.get_current_scene()
    assert scene["frame_available"] is current
    assert scene["latest_visual_observation_at"] == pytest.approx(992.2)


def test_missing_observations_and_request_context_isolation(monkeypatch):
    monkeypatch.setattr(world, "time", lambda: 100.)
    with world.world_request_context({"cv_world_state_fetch_ms": 12., "latest_visual_observation_at": 99.5}):
        assert world.world_request_metrics() == {"cv_world_state_fetch_ms": 12., "cv_latest_event_age_ms": 500.}
        with world.world_request_context({}):
            assert world.world_request_metrics() == {"cv_world_state_fetch_ms": None, "cv_latest_event_age_ms": None}
        assert world.world_request_metrics()["cv_latest_event_age_ms"] == 500.
    assert world.world_request_metrics()["cv_latest_event_age_ms"] is None


@pytest.mark.asyncio
async def test_invalid_model_event_resets_request_context():
    class InvalidPlan:
        async def stream_events(self, messages, tools):
            yield {"type": "action_plan", "speech": "", "actions": []}

    with pytest.raises(ValueError, match="no speech"):
        _ = [part async for part in Conversation(InvalidPlan()).stream_reply(
            "Hello", scene={"cv_world_state_fetch_ms": 35.})]
    assert world.world_request_metrics()["cv_world_state_fetch_ms"] is None


@pytest.mark.asyncio
async def test_tool_round_uses_refreshed_world_metrics(monkeypatch):
    monkeypatch.setattr(world, "time", lambda: 100.)
    seen = []

    class ToolLLM:
        async def stream_events(self, messages, tools):
            seen.append(world.world_request_metrics())
            if len(seen) == 1:
                yield {"type": "tool_call", "name": "unknown", "arguments": "{}", "id": "call-1"}
            else:
                yield {"type": "text", "text": "Hello."}

    state = {"status": "available", "speaker": None, "people": [], "objects": []}

    async def refresh(hint):
        return state, {"cv_world_state_fetch_ms": 20., "latest_visual_observation_at": 99.9}

    monkeypatch.setattr(pipeline, "current_world", refresh)
    _ = [part async for part in Conversation(ToolLLM()).stream_reply(
        "Hello", world_state=state,
        scene={"cv_world_state_fetch_ms": 35., "latest_visual_observation_at": 99.})]
    assert seen == [
        {"cv_world_state_fetch_ms": 35., "cv_latest_event_age_ms": 1000.},
        {"cv_world_state_fetch_ms": 20., "cv_latest_event_age_ms": 100.},
    ]
    assert "cv_world_state_fetch_ms" not in _METRICS_FIELDS
    assert "cv_latest_event_age_ms" not in _METRICS_FIELDS


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["native", "structured"])
async def test_actual_model_request_logs_include_fresh_age_without_prompt_metadata(monkeypatch, mode):
    clock = [100.]
    monkeypatch.setattr(world, "time", lambda: clock[0])
    logged = []
    monkeypatch.setattr("llm.openai.record", lambda kind, event, **fields: logged.append((kind, event, fields)))

    def handler(request):
        payload = json.loads(request.content)
        assert "cv_world_state_fetch_ms" not in json.dumps(payload)
        assert "latest_visual_observation_at" not in json.dumps(payload)
        speech = "Hello." if mode == "native" else json.dumps({
            "reply_kind": "conversation", "speech": "Hello.", "actions": []})
        return httpx.Response(200, text='data: ' + json.dumps({"choices": [{"delta": {"content": speech}}]})
                              + '\n\ndata: [DONE]\n\n')

    client = httpx.AsyncClient
    scene = {"cv_world_state_fetch_ms": 35., "latest_visual_observation_at": 99.}
    conversation = Conversation(OpenAIProvider("http://fake.invalid/v1", "fake", tool_mode=mode))
    state = {"status": "available", "people": [], "objects": [], "speaker": None}
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: client(
            **kwargs, transport=httpx.MockTransport(handler))):
        for now in (100., 102.):
            clock[0] = now
            _ = [part async for part in conversation.stream_reply("Who is here?", world_state=state, scene=scene)]
    requests = [fields for kind, event, fields in logged if event == "request"]
    assert [item["cv_world_state_fetch_ms"] for item in requests] == [35., 35.]
    assert [item["cv_latest_event_age_ms"] for item in requests] == [1000., 3000.]
    assert world.world_request_metrics()["cv_latest_event_age_ms"] is None
