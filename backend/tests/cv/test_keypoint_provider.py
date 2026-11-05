"""MediaPipe body, face/lips and hands retain frame/track coordinates."""

from types import SimpleNamespace
import numpy as np
import pytest

from backend.config import settings
from backend.cv.events import Frame
from backend.cv.keypoint_det.base import KeypointDetection, KeypointSample
from backend.cv.keypoint_det.gestures import GestureClassifier
from backend.cv.keypoint_det.mediapipe import LIP_INDICES, MediaPipeProvider
from backend.cv.object_det.base import ObjectDetection


def landmark(x=.5, y=.2, z=0., visibility=1.):
    return SimpleNamespace(x=x, y=y, z=z, visibility=visibility)


def fake_provider():
    provider = MediaPipeProvider(settings)
    body = [landmark() for _ in range(33)]
    body[15], body[16] = landmark(.5, .75), landmark(.7, .75)
    face = [landmark() for _ in range(478)]
    face[61], face[291] = landmark(.4, .3), landmark(.6, .3)
    face[13], face[14] = landmark(.5, .3), landmark(.5, .32)
    hands = [landmark(.5, .75) for _ in range(21)]
    provider._image = lambda crop: crop
    provider._pose = SimpleNamespace(detect=lambda image: SimpleNamespace(pose_landmarks=[body]))
    provider._face = SimpleNamespace(detect=lambda image: SimpleNamespace(face_landmarks=[face]))
    provider._hands = SimpleNamespace(detect=lambda image: SimpleNamespace(
        hand_landmarks=[hands], handedness=[[SimpleNamespace(category_name="Left", score=.99)]],
    ))
    return provider


@pytest.mark.asyncio
async def test_all_schemas_and_lips_are_associated_with_person_and_frame():
    provider = fake_provider()
    box = (40., 40., 100., 160.)
    frame = Frame(10, 1., np.zeros((200, 200, 3), dtype=np.uint8),
                  (ObjectDetection(42, "person", .9, box),))
    history = tuple(KeypointSample(i, .6 + i * .1, KeypointDetection(
        42, "mediapipe_lips", (), mouth_openness=.01 + i * .01,
    )) for i in range(3))
    result = await provider.detect(frame, history)
    assert {item.schema for item in result} == {
        "mediapipe_pose33", "mediapipe_face478", "mediapipe_lips", "mediapipe_left_hand21",
    }
    assert all(item.track_id == 42 for item in result)
    body = next(item for item in result if item.schema == "mediapipe_pose33")
    assert body.points[0][0] == pytest.approx(70.)
    lips = next(item for item in result if item.schema == "mediapipe_lips")
    assert lips.landmark_indices == LIP_INDICES
    assert lips.mouth_openness > 0
    assert lips.mouth_motion > 0
    assert all(not item.gestures for item in result)  # No premature confirmation.


@pytest.mark.asyncio
async def test_missing_body_retains_face_data_but_does_not_assign_hands():
    provider = fake_provider()
    provider._pose = SimpleNamespace(detect=lambda image: SimpleNamespace(pose_landmarks=[]))
    frame = Frame(0, 0., np.zeros((200, 200, 3), dtype=np.uint8),
                  (ObjectDetection(1, "person", .9, (0, 0, 200, 200)),))
    result = await provider.detect(frame, ())
    assert {item.schema for item in result} == {"mediapipe_face478", "mediapipe_lips"}


@pytest.mark.asyncio
async def test_no_person_does_not_invent_tracks_or_call_models():
    assert await MediaPipeProvider(settings).detect(Frame(0, 0., None), ()) == ()


@pytest.mark.asyncio
async def test_video_models_keep_separate_track_state_and_reset_after_absence():
    provider = fake_provider()
    created = []

    class VideoModel:
        def __init__(self):
            self.times = []
            self.closed = False

        def detect_for_video(self, image, timestamp):
            assert not self.times or timestamp > self.times[-1]
            self.times.append(timestamp)
            return SimpleNamespace(pose_landmarks=[], face_landmarks=[], hand_landmarks=[], handedness=[])

        def close(self):
            self.closed = True

    def make_group():
        group = tuple(VideoModel() for _ in range(3))
        created.append(group)
        return group

    provider._model_factory = make_group
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    tracks = tuple(ObjectDetection(track_id, "person", .9, (0, 0, 200, 200)) for track_id in (1, 2))
    try:
        await provider.detect(Frame(0, .0001, image, tracks), ())
        await provider.detect(Frame(1, .0002, image, tracks), ())
        assert len(created) == 2
        assert all(model.times == [0, 1] for group in created for model in group)
        # Sparse input or slow inference must retain a visible person's graphs.
        await provider.detect(Frame(2, 1.5, image, tracks), ())
        assert len(created) == 2
        assert all(model.times == [0, 1, 1500] and not model.closed
                   for group in created for model in group)
        await provider.detect(Frame(3, 3., image, ()), ())
        assert all(model.closed for group in created for model in group)
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_confirmation_and_lip_data_survive_window_boundaries():
    from copy import deepcopy
    from backend.cv.face_recog.base import FaceRecognition
    from backend.cv.window_manager import WindowManager
    from backend.tests.cv.test_gestures import hand

    provider = fake_provider()
    config = deepcopy(settings)
    config.cv.gestures.confirmation_seconds = 2.
    config.cv.gestures.min_frames = 8
    provider._classifier = GestureClassifier(config)
    body = [landmark() for _ in range(33)]
    body[15], body[16] = landmark(.5, .9), landmark(.7, .9)
    provider._pose = SimpleNamespace(detect=lambda image: SimpleNamespace(pose_landmarks=[body]))
    hand_points = [landmark(x / 100, y / 100, z) for x, y, z in hand().points]
    provider._hands = SimpleNamespace(detect=lambda image: SimpleNamespace(
        hand_landmarks=[hand_points], handedness=[[SimpleNamespace(category_name="Left", score=.99)]],
    ))

    class Objects:
        async def detect(self, frame):
            return (ObjectDetection(42, "person", .9, (0., 0., 200., 200.)),)

    class Faces:
        async def recognize(self, frame, person):
            return FaceRecognition(person.track_id)

    manager = WindowManager.from_settings(Objects(), Faces(), provider, settings)
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    try:
        for index in range(21):
            manager.submit(Frame(index, index / 10, image))
            await manager.wait_idle()
        manager.advance(3.)
        summaries = manager.summaries()
        assert not summaries[0].people[0].gestures
        assert not summaries[1].people[0].gestures
        assert summaries[2].people[0].gestures == ("open_palm",)
        assert summaries[2].people[0].mouth_openness > 0
        assert summaries[2].people[0].mouth_motion == pytest.approx(0.)
    finally:
        await manager.close()
