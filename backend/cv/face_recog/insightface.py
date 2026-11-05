"""InsightFace adapter around the migrated quality/matching/identity services."""

import asyncio
from pathlib import Path
from threading import RLock

from .base import FaceRecognition

from backend.diagnostics import traced_stage
from backend.runtime import ModelWorker, onnx_options


class InsightFaceProvider:
    def __init__(self, settings):
        self.settings = settings
        self._lock = RLock()
        self._scene = None
        self._store = None
        self._detector = None
        self._service = None
        self._worker = ModelWorker(settings, "faces")

    async def initialize(self):
        await self._worker.run(self._initialize)

    def _initialize(self):
        from ..object_det.state import SceneState
        from .detector import FaceDetector
        from .matcher import FaceMatcher
        from .service import IdentityService
        from .speaker import SpeakerAssociation
        from .store import IdentityStore

        cfg = self.settings.cv
        path = Path(cfg.identity.database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        self._store = IdentityStore(path)
        self._store.open()
        self._scene = SceneState()
        if cfg.face.enabled:
            self._detector = FaceDetector(cfg.face.model_name, cfg.face.detection_threshold,
                                          session_options=onnx_options(self.settings, "faces"))
            self._detector.initialize()
        model_version = self._detector.embedding_model if self._detector else None
        self._service = IdentityService(self._store, FaceMatcher(), self._scene, model_version=model_version)
        self._speaker = SpeakerAssociation(self._scene, self._service)

    async def recognize(self, frame, person):
        return await self._worker.run(self._recognize, frame, person)

    @traced_stage("cv.face_recognition")
    def _recognize(self, frame, person):
        with self._lock:
            track = self._scene.update_track(person.track_id, bbox=person.bbox, stable=True)
            if self._detector is not None:
                faces = self._detector.detect_faces(frame.image, person.bbox)
                if faces:
                    # Prefer the largest face inside this person's crop.
                    face = max(faces, key=lambda item: (item["bbox"][2] - item["bbox"][0]) *
                               (item["bbox"][3] - item["bbox"][1]))
                    self._service.process_face_observation(person.track_id, face, frame.image.shape)
            captured = (person.track_id in self._service._pending_embeddings
                        or (track.person_id and self._service.has_face_embedding(track.person_id)))
            return FaceRecognition(
                person.track_id, track.person_id, track.display_name, track.identity_confidence,
                needs_retry=track.person_id is None or not captured,
            )

    async def handle_transcript(self, text, visible_people):
        return await self._worker.run(self._handle_transcript, text, visible_people)

    def _handle_transcript(self, text, visible_people):
        with self._lock:
            visible = {person["track_id"] for person in visible_people}
            for track in self._scene.get_all_tracks():
                if track.track_id not in visible:
                    self._scene.remove_track(track.track_id)
            for person in visible_people:
                self._scene.update_track(person["track_id"], stable=True)
            return self._speaker.handle_transcript(text)

    async def reset(self):
        if self._scene is not None:
            self._scene.clear()
            self._service._pending_embeddings.clear()

    async def forget_track(self, track_id):
        """A temporary tracker ID is not evidence of identity after an absence."""
        await self._worker.run(self._forget_track, track_id)

    def _forget_track(self, track_id):
        with self._lock:
            self._scene.remove_track(track_id)
            self._service.forget_track(track_id)

    async def rename_track(self, track_id, name):
        return await self._worker.run(self._rename_track, track_id, name)

    def _rename_track(self, track_id, name):
        with self._lock:
            self._scene.update_track(track_id, stable=True)
            return self._service.bind_name_to_active_person(name, track_id)

    def prune(self, retained_tracks):
        """Bound transient identity state without waiting for model inference."""
        if self._scene is None or not self._lock.acquire(blocking=False):
            return
        try:
            for track in self._scene.get_all_tracks():
                if track.track_id not in retained_tracks:
                    self._scene.remove_track(track.track_id)
                    self._service._pending_embeddings.pop(track.track_id, None)
        finally:
            self._lock.release()

    async def close(self):
        if self._store is not None:
            self._store.close()
            self._store = None
        await self._worker.close()
