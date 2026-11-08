"""Bound expensive inference without removing background detections."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.cv.events import Frame
from backend.cv.face_recog.base import FaceRecognition
from backend.cv.object_det.base import ObjectDetection
from backend.cv.window_manager import WindowManager
from backend.tests.test_cv_windows import Faces, Objects, Points


def person(track_id, box):
    return ObjectDetection(track_id, "person", .9, box)


def test_area_threshold_clips_boxes_and_selects_largest_two_deterministically():
    manager = WindowManager(Objects(), Faces(), Points())
    frame = Frame(1, 1., SimpleNamespace(shape=(100, 100, 3)))
    tracks = (
        person(3, (0, 0, 60, 100)),
        person(2, (0, 0, 70, 100)),
        person(1, (0, 0, 70, 100)),
        person(4, (0, 0, 29, 100)),
        ObjectDetection(5, "chair", .9, (0, 0, 100, 100)),
    )
    assert [p.track_id for p in manager.select_people(frame, tracks)] == [1, 2]
    assert manager.select_people(frame, (person(6, (0, 0, 30, 100)),))[0].track_id == 6
    assert manager.select_people(frame, (person(4, (0, 0, 29, 100)),)) == ()
    assert manager.select_people(frame, (person(7, (-100, 0, 20, 100)),)) == ()
    assert manager.select_people(frame, (person(7, (30, 0, 20, 100)),)) == ()
    assert manager.select_people(frame, (person(7, (0, 0, float("nan"), 100)),)) == ()
    assert manager.select_people(replace(frame, image=None), tracks) == ()


@pytest.mark.parametrize("kwargs", [
    {"person_max_targets": 0}, {"person_max_targets": True},
    {"person_min_area_ratio": -0.1}, {"person_min_area_ratio": 1.1},
    {"person_min_area_ratio": float("nan")},
])
def test_invalid_selection_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        WindowManager(Objects(), Faces(), Points(), **kwargs)


@pytest.mark.asyncio
async def test_only_selected_people_get_inference_and_background_identity_is_preserved():
    tracks = (person(1, (0, 0, 80, 100)), person(2, (0, 0, 60, 100)),
              person(3, (0, 0, 40, 100)), person(4, (0, 0, 20, 100)))

    class SceneObjects(Objects):
        async def detect(self, frame):
            return tracks

    class SelectedPoints(Points):
        async def detect(self, frame, history):
            self.calls.append(tuple(p.track_id for p in frame.tracks))
            return ()

    faces, points = Faces(), SelectedPoints()
    manager = WindowManager(SceneObjects(), faces, points)
    manager._seen_people[4] = .1
    manager._identities[4] = FaceRecognition(4, "known", "Sam", .9)
    try:
        manager.submit(Frame(1, .1, SimpleNamespace(shape=(100, 100, 3))))
        await manager.wait_idle()
        assert points.calls == [(1, 2)]
        assert {track for _, track in faces.calls} == {1, 2}
        record = manager.get_window(0)[0]
        assert record.objects == tracks
        assert {face.track_id for face in record.faces} == {1, 2, 4}
        assert manager._identities[4].display_name == "Sam"
        assert set(manager._seen_people) == {1, 2, 3, 4}
        # A formerly background person qualifies when a larger person disappears.
        tracks = tracks[1:]
        manager.submit(Frame(2, .3, SimpleNamespace(shape=(100, 100, 3))))
        await manager.wait_idle()
        assert points.calls[-1] == (2, 3)
        assert {track for _, track in faces.calls} == {1, 2, 3}
        # No qualifying box means no landmarks or new face attempts.
        tracks = tracks[-1:]
        manager.submit(Frame(3, .5, SimpleNamespace(shape=(100, 100, 3))))
        await manager.wait_idle()
        assert points.calls[-1] == ()
        assert len(faces.calls) == 3
        assert manager.get_window(0)[-1].objects == tracks
    finally:
        await manager.close()
