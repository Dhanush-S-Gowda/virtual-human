"""Visual history uses capture ages, including late results and absent people."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import settings
from backend.cv.events import Frame, FrameRecord
from backend.cv.face_recog.base import FaceRecognition
from backend.cv.keypoint_det.base import KeypointDetection
from backend.cv.object_det.base import ObjectDetection
from backend.cv.window_manager import CVService, WindowManager
from backend.tests.test_cv_windows import Objects, Faces, Points


def record(timestamp, gesture="wave"):
    return FrameRecord(
        frame_id=int(timestamp * 100), timestamp=timestamp,
        objects=(ObjectDetection(1, "person", .9, (0, 0, 10, 10)),
                 ObjectDetection(2, "cup", .9, (20, 0, 30, 10))),
        faces=(FaceRecognition(1, "alex-id", "Alex", .9),),
        keypoints=(KeypointDetection(1, "test", ((1., 2., .9),), (gesture,)),),
        completed=("objects", "keypoints", "face:1"))


def add(manager, observation):
    window = manager._window(int(observation.timestamp / manager.window_seconds))
    window.frames[observation.frame_id] = observation
    return window


def test_seconds_history_keeps_people_objects_and_gestures_after_they_leave():
    manager = WindowManager(Objects(), Faces(), Points())
    add(manager, record(1.2))
    add(manager, record(1.8, "thumbs_up"))
    manager.advance(8.)
    history = manager.visual_history(10.)
    window = history["windows"][0]
    assert window["counts"] == {"unique_people": 1, "unique_objects": 1, "recognized_faces": 1}
    assert window["object_counts"] == {"cup": 1}
    assert window["people"] == [{"track_id": 1, "name": "Alex"}]
    assert window["gestures"] == [
        {"track_id": 1, "name": "Alex", "action": "thumbs_up", "last_seen_seconds_ago": 6.2},
        {"track_id": 1, "name": "Alex", "action": "wave", "last_seen_seconds_ago": 6.8}]
    manager.advance(11.5)
    assert [item["action"] for item in manager.visual_history(10.)["windows"][0]["gestures"]] == ["thumbs_up"]
    manager.advance(11.9)
    assert manager.visual_history(10.)["windows"] == []
    assert manager.visual_history(0.)["windows"] == []


def test_window_history_includes_open_window_and_obeys_window_count():
    manager = WindowManager(Objects(), Faces(), Points())
    for timestamp in (2.8, 3.2, 4.2):
        add(manager, record(timestamp))
    manager.advance(4.5)
    result = manager.visual_history(10., windows=2)
    assert result["mode"] == "windows"
    assert [item["window_id"] for item in result["windows"]] == [3, 4]
    assert result["windows"][-1]["closed"] is False
    assert [item["window_id"] for item in manager.visual_history(1.)["windows"]] == [4]


@pytest.mark.asyncio
async def test_late_model_results_revise_the_original_capture_window():
    gate = asyncio.Event()
    manager = WindowManager(Objects(gate), Faces(), Points())
    try:
        manager.submit(Frame(1, 1.2, None))
        await asyncio.sleep(0)
        manager.advance(3.)
        before = manager.visual_history(10.)["windows"][0]
        assert before["pending_tasks"] > 0
        assert before["closed"] is True
        gate.set()
        await manager.wait_idle()
        after = manager.visual_history(10.)["windows"][0]
        assert after["revision"] > before["revision"]
        assert after["counts"]["unique_objects"] == 2
        assert after["counts"]["recognized_faces"] == 1
        assert after["gestures"][0]["action"] == "wave"
        assert after["gestures"][0]["last_seen_seconds_ago"] == 1.8
    finally:
        gate.set()
        await manager.close()


@pytest.mark.asyncio
async def test_history_reaches_model_context_even_when_current_frame_expired(monkeypatch):
    from backend.tests.test_backend import FakeLLM  # Establish application import path.
    import world
    config = SimpleNamespace(**{**vars(settings), "cv_world_history_seconds": 10., "cv_world_history_windows": 0})
    service = CVService(Objects(), Faces(), Points(), config)
    observed = record(1.2)
    add(service.manager, observed)
    service.manager.advance(8.)
    service._latest_objects = observed
    scene = service.get_current_scene()
    assert scene["frame_available"] is False
    monkeypatch.setattr(world, "cv_request", AsyncMock(return_value={"data": scene}))
    context, _ = await world.current_world()
    assert context["status"] == "unavailable"
    assert context["people"] == []
    assert context["recent_visual_history"]["windows"][0]["gestures"][0]["action"] == "wave"
    from pipeline import Conversation
    llm = FakeLLM()
    _ = [part async for part in Conversation(llm).stream_reply("What did I do?", world_state=context)]
    assert '"action":"wave"' in llm.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_each_turn_gets_new_history_instead_of_the_first_turn_snapshot():
    from backend.tests.test_backend import FakeLLM
    from pipeline import Conversation
    llm = FakeLLM()
    conversation = Conversation(llm)
    for action in ("wave", "thumbs_up"):
        state = {"status": "available", "speaker": None, "people": [], "objects": [],
                 "recent_visual_history": {"windows": [{"gestures": [{"action": action}]}]}}
        _ = [part async for part in conversation.stream_reply("What did I do?", world_state=state)]
    assert '"action":"wave"' in llm.calls[0][0]["content"]
    assert '"action":"thumbs_up"' in llm.calls[1][0]["content"]
    assert '"action":"wave"' not in llm.calls[1][0]["content"]
