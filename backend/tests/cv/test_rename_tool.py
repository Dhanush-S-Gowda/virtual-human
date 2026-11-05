"""A validated rename persists a face identity, not a temporary tracker ID."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from backend.config import settings
from backend.cv.events import Frame
from backend.cv.face_recog.insightface import InsightFaceProvider
from backend.cv.face_recog.matcher import FaceMatcher
from backend.cv.face_recog.service import IdentityService
from backend.cv.face_recog.store import IdentityStore
from backend.cv.object_det.state import SceneState
from backend.cv.window_manager import CVService
from backend.tests.test_cv_windows import Objects, Points


@pytest.mark.asyncio
async def test_tool_rename_rejects_stale_target_and_recognizes_a_new_track_after_reopen(tmp_path):
    path = tmp_path / "identities.db"
    store = IdentityStore(path)
    store.open()
    provider = InsightFaceProvider(settings)
    provider._store, provider._scene = store, SceneState()
    provider._service = IdentityService(store, FaceMatcher(), provider._scene, model_version="test")
    face = {"score": .99, "bbox": (10, 10, 150, 180), "embedding": np.array([1., 0., 0.])}
    provider._detector = SimpleNamespace(detect_faces=lambda *args: [face])
    service = CVService(Objects(), provider, Points(), settings)

    class Client:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(json.loads(message))

    client = Client()
    try:
        service.manager.submit(Frame(0, 0., SimpleNamespace(shape=(480, 640, 3))))
        await service.manager.wait_idle()
        service._latest_objects = service.manager.get_window(0)[0]
        assert service.manager._identities[1].needs_retry  # Even a valid unknown face must retry.
        request = {"type": "rename_person", "request_id": "rename", "track_id": 1,
                   "name": "Maya", "source_id": service._source_id, "generation": 0}
        await service._handle_message(client, {**request, "generation": -1})
        assert client.messages[-1]["type"] == "error"
        await service._handle_message(client, request)
        assert client.messages[-1]["data"]["ok"]
        person_id = provider._scene.get_track(1).person_id
        assert store.get_person(person_id)["display_name"] == "Maya"
        assert store.get_embedding_count(person_id, model_version="test") == 1
        columns = [column[1] for column in store._db.execute("PRAGMA table_info(people)")]
        assert "track_id" not in columns
    finally:
        await service.manager.close()
        await provider.close()
    reopened = IdentityStore(path)
    reopened.open()
    try:
        scene = SceneState()
        scene.update_track(99, stable=True)
        identity = IdentityService(reopened, FaceMatcher(), scene, model_version="test")
        match, _ = identity.process_face_observation(99, face)
        assert match.display_name == "Maya"
    finally:
        reopened.close()
