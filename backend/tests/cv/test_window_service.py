"""Exercise the actual WebSocket service with fake inference and image decode."""

import asyncio
import json
import struct
from types import SimpleNamespace

import pytest
import websockets

from backend.config import settings
from backend.cv.window_manager import CVService
from backend.tests.test_cv_windows import Objects, Faces, Points


async def receive_until(ws, predicate):
    async with asyncio.timeout(2.):
        while True:
            message = json.loads(await ws.recv())
            if predicate(message):
                return message


def packet(seq=0, timestamp_ms=0.):
    return b"FRM1" + struct.pack(">IdI", seq, timestamp_ms, 4) + b"jpeg"


@pytest.mark.asyncio
async def test_window_boundary_revision_queries_and_source_isolation():
    gate = asyncio.Event()
    config = SimpleNamespace(**{**vars(settings), "cv_window_seconds": .1})
    service = CVService(Objects(gate), Faces(), Points(), config, host="127.0.0.1", port=0)
    service._decode = lambda body: SimpleNamespace(shape=(480, 640, 3))
    await service.start()
    try:
        port = service._server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as camera:
            await camera.send(packet())
            initial = await receive_until(camera, lambda msg: msg.get("event_type") == "window_summary")
            assert initial["summary"]["pending_tasks"] == 2
            assert initial["summary"]["unique_objects"] == 0
            gate.set()
            revised = await receive_until(camera, lambda msg: msg.get("event_type") == "window_summary"
                                          and msg["summary"]["window_id"] == 0
                                          and msg["summary"]["pending_tasks"] == 0)
            assert revised["summary"]["revision"] > initial["summary"]["revision"]
            assert revised["summary"]["unique_objects"] == 3
            async with websockets.connect(f"ws://127.0.0.1:{port}") as observer:
                await observer.send(json.dumps({"type": "get_scene", "request_id": "query-1"}))
                scene = await receive_until(observer, lambda msg: msg.get("request_id") == "query-1")
                assert scene["type"] == "scene"
                assert scene["data"]["people"][0]["display_name"] == "Alex"
                assert scene["data"]["windows"][0]["unique_objects"] == 3
                await observer.send(packet(1, 100.))
                error = await receive_until(observer, lambda msg: msg.get("type") == "error")
                assert "already connected" in error["message"]
            assert service.providers[0].calls == [0]
    finally:
        gate.set()
        await service.stop()


@pytest.mark.asyncio
async def test_empty_detection_clears_scene_and_reconnect_resets_clock():
    class Empty(Objects):
        async def detect(self, frame):
            return () if frame.frame_id else await super().detect(frame)

    service = CVService(Empty(), Faces(), Points(), settings, host="127.0.0.1", port=0)
    service._decode = lambda body: SimpleNamespace(shape=(480, 640, 3))
    await service.start()
    try:
        port = service._server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as camera:
            await camera.send(packet())
            await receive_until(camera, lambda msg: msg.get("type") == "frame" and msg.get("people"))
            await camera.send(packet(1, 200.))
            await receive_until(camera, lambda msg: msg.get("type") == "frame" and msg.get("seq") == 1)
            assert service.get_visible_people() == []
        async with websockets.connect(f"ws://127.0.0.1:{port}") as camera:
            await camera.send(packet(0, 0.))
            await receive_until(camera, lambda msg: msg.get("type") == "frame" and msg.get("people"))
            assert len(service.get_visible_people()) == 1
    finally:
        await service.stop()


def test_frame_metadata_marks_empty_landmark_completion_and_errors():
    from backend.cv.events import FrameRecord

    service = CVService(Objects(), Faces(), Points(), settings)
    record = FrameRecord(
        frame_id=42, timestamp=1.25, width=640, height=480,
        completed=("objects", "keypoints"), errors=("keypoints: RuntimeError: failed",),
    )
    metadata = service.frame_metadata(record)
    assert metadata["completed"] == ("objects", "keypoints")
    assert metadata["keypoints"] == []
    assert metadata["seq"] == 42
    assert metadata["timestamp"] == 1.25
    assert metadata["errors"] == record.errors


def test_scene_retains_all_gestures_in_three_second_window():
    from dataclasses import replace
    from backend.cv.events import FrameRecord
    from backend.cv.keypoint_det.base import KeypointDetection
    from backend.cv.object_det.base import ObjectDetection

    config = SimpleNamespace(**{**vars(settings), "cv_world_history_seconds": 3., "cv_world_history_windows": 0})
    service = CVService(Objects(), Faces(), Points(), config)
    service.manager._track_generations[1] = 10
    record = FrameRecord(
        frame_id=1, timestamp=1., width=640, height=480,
        objects=(ObjectDetection(1, "person", .9, (0, 0, 10, 10)),),
        keypoints=(KeypointDetection(1, "test", ((1., 2., .9),), ("wave",)),),
        completed=("objects", "keypoints"),
    )
    service._remember_gestures(record)
    second = replace(record, frame_id=2, timestamp=2., keypoints=(
        replace(record.keypoints[0], gestures=("thumbs_up",)),))
    service._remember_gestures(second)
    # New frames without gestures must not erase recent observations.
    service._latest_objects = replace(record, frame_id=3, timestamp=3.9, keypoints=())
    service._latest_points = replace(service._latest_objects, completed=("keypoints",))
    service._remember_gestures(service._latest_points)
    service.manager._watermark = 4.
    scene = service.get_current_scene()
    assert scene["people"][0]["actions"] == ("thumbs_up", "wave")
    assert scene["gesture_window_seconds"] == 3
    service.manager._watermark = 4.01
    assert service.get_current_scene()["people"][0]["actions"] == ("thumbs_up",)
    # A recycled track ID must not inherit the previous person's gestures.
    service.manager._track_generations[1] = 11
    assert service.get_current_scene()["people"][0]["actions"] == ()
    service.manager._track_generations[1] = 10
    service._latest_objects = replace(record, timestamp=5.1, keypoints=())
    service.manager._watermark = 5.1
    assert service.get_current_scene()["people"][0]["actions"] == ()
    service.manager._watermark = 6.2
    assert not service.get_current_scene()["frame_available"]
