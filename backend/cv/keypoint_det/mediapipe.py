"""MediaPipe-only body, face/lip, and hand landmarks on canonical person crops."""

import asyncio
from dataclasses import replace
from math import hypot, isfinite
from statistics import pvariance

from .base import KeypointDetection

from backend.diagnostics import traced_stage
from backend.runtime import ModelWorker
from .gestures import GestureClassifier
from .models import configured_models, ensure_model


# Stable FaceMesh vertex IDs, preserved alongside the extracted lip points.
LIP_INDICES = (0, 13, 14, 17, 37, 39, 40, 61, 78, 80, 81, 82, 84, 87, 88, 91,
               95, 146, 178, 181, 185, 191, 267, 269, 270, 291, 308, 310, 311,
               312, 314, 317, 318, 321, 324, 375, 402, 405, 409, 415)


def _create_group(constructors):
    models = []
    try:
        for create in constructors:
            models.append(create())
        return tuple(models)
    except BaseException:
        for model in models:
            model.close()
        raise


class MediaPipeProvider:
    def __init__(self, settings):
        self.settings = settings
        self._pose = self._face = self._hands = None
        self._mp = None
        self._classifier = GestureClassifier(settings)
        self._worker = ModelWorker(settings, "keypoints")
        self._groups = {}
        self._group_seen = {}
        self._group_times = {}
        self._spare_group = None
        self._model_factory = None

    async def initialize(self):
        await self._worker.run(self._initialize)

    def _initialize(self):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        cfg = self.settings.cv.keypoints
        if not 0 < cfg.min_confidence <= 1 or not 0 <= cfg.crop_padding <= .5:
            raise ValueError("Keypoint confidence must be in (0, 1]; crop padding must be in [0, .5]")
        paths = [ensure_model(name) for name in configured_models(self.settings)]
        self._mp = mp
        constructors = (
            lambda: vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(paths[0])),
                running_mode=vision.RunningMode.VIDEO, num_poses=1,
                min_pose_detection_confidence=cfg.min_confidence,
                min_pose_presence_confidence=cfg.min_confidence,
            )),
            lambda: vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(paths[1])),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                min_face_detection_confidence=cfg.min_confidence,
                min_face_presence_confidence=cfg.min_confidence,
            )),
            lambda: vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(paths[2])),
                running_mode=vision.RunningMode.VIDEO, num_hands=2,
                min_hand_detection_confidence=cfg.min_confidence,
                min_hand_presence_confidence=cfg.min_confidence,
            )),
        )
        self._model_factory = lambda: _create_group(constructors)
        self._pose, self._face, self._hands = self._model_factory()
        self._spare_group = (self._pose, self._face, self._hands)

    async def detect(self, frame, history):
        return await self._worker.run(self._detect, frame, history)

    def _image(self, crop):
        import cv2
        import numpy as np
        rgb = np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        return self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)

    @staticmethod
    def _points(landmarks, crop_box, frame_width, visibility=False):
        x1, y1, x2, y2 = crop_box
        width, height = x2 - x1, y2 - y1
        output = []
        for landmark in landmarks:
            point = (float(x1 + landmark.x * width), float(y1 + landmark.y * height),
                     float(landmark.z * width / frame_width))
            if visibility:
                point += (float(getattr(landmark, "visibility", 1.)),)
            if not all(isfinite(value) for value in point):
                return ()
            output.append(point)
        return tuple(output)

    @traced_stage("cv.keypoint_detection")
    def _detect(self, frame, history):
        people = [track for track in frame.tracks if track.label == "person"]
        visible_ids = {person.track_id for person in people}
        for track_id in list(self._groups):
            # A slow sample is not a departure. Rebuilding all three graphs for
            # a still-visible person adds cold-start latency to the next frame.
            if track_id not in visible_ids and frame.timestamp - self._group_seen[track_id] > 1.:
                for model in self._groups.pop(track_id):
                    model.close()
                del self._group_seen[track_id]
                self._group_times.pop(track_id, None)
        if not people:
            return ()
        if any(model is None for model in (self._pose, self._face, self._hands)):
            raise RuntimeError("MediaPipe provider is not initialized")
        height, width = frame.image.shape[:2]
        output = []
        for person in people:
            if self._model_factory is not None:
                if person.track_id not in self._groups:
                    self._groups[person.track_id] = self._spare_group or self._model_factory()
                    self._spare_group = None
                pose_model, face_model, hand_model = self._groups[person.track_id]
                self._group_seen[person.track_id] = frame.timestamp
                timestamp_ms = max(int(frame.timestamp * 1000), self._group_times.get(person.track_id, -1) + 1)
                self._group_times[person.track_id] = timestamp_ms
                detect = lambda model, image: model.detect_for_video(image, timestamp_ms)
            else:
                # Model-independent fixtures use IMAGE-mode stubs.
                pose_model, face_model, hand_model = self._pose, self._face, self._hands
                detect = lambda model, image: model.detect(image)
            bx1, by1, bx2, by2 = person.bbox
            pad = self.settings.cv.keypoints.crop_padding
            pw, ph = bx2 - bx1, by2 - by1
            crop_box = (max(0, int(bx1 - pw * pad)), max(0, int(by1 - ph * pad)),
                        min(width, int(bx2 + pw * pad)), min(height, int(by2 + ph * pad)))
            x1, y1, x2, y2 = crop_box
            if x2 <= x1 or y2 <= y1:
                continue
            image = self._image(frame.image[y1:y2, x1:x2])
            poses = detect(pose_model, image).pose_landmarks
            body = self._points(poses[0], crop_box, width, visibility=True) if poses else ()
            if body:
                output.append(KeypointDetection(person.track_id, "mediapipe_pose33", body, bbox=person.bbox))
            faces = detect(face_model, image).face_landmarks
            face = self._points(faces[0], crop_box, width) if faces else ()
            # Reject a neighboring face when its nose disagrees with this body's
            # nose. Without body geometry retain face data, but infer no speaker.
            if (face and body and body[0][3] >= self.settings.cv.keypoints.min_confidence
                    and hypot(face[1][0] - body[0][0], face[1][1] - body[0][1]) > pw * .35):
                face = ()
            if len(face) > max(LIP_INDICES):
                output.append(KeypointDetection(person.track_id, f"mediapipe_face{len(face)}", face, bbox=person.bbox))
                mouth_width = hypot(face[61][0] - face[291][0], face[61][1] - face[291][1])
                openness = (hypot(face[13][0] - face[14][0], face[13][1] - face[14][1]) / mouth_width
                            if mouth_width > 1e-6 else None)
                recent = [sample.detection.mouth_openness for sample in history
                          if sample.detection.track_id == person.track_id
                          and sample.detection.schema == "mediapipe_lips"
                          and frame.timestamp - 1. <= sample.timestamp < frame.timestamp
                          and sample.detection.mouth_openness is not None]
                if openness is not None:
                    recent.append(openness)
                motion = pvariance(recent) if len(recent) >= 3 else None
                output.append(KeypointDetection(
                    person.track_id, "mediapipe_lips", tuple(face[index] for index in LIP_INDICES),
                    bbox=person.bbox, landmark_indices=LIP_INDICES,
                    mouth_openness=openness, mouth_motion=motion,
                ))
            hands = detect(hand_model, image)
            seen_labels = set()
            for index, landmarks in enumerate(hands.hand_landmarks):
                categories = hands.handedness[index] if index < len(hands.handedness) else ()
                if not categories or categories[0].score < self.settings.cv.keypoints.min_confidence:
                    continue
                label = categories[0].category_name.lower()
                if label not in ("left", "right") or label in seen_labels:
                    continue
                points = self._points(landmarks, crop_box, width)
                if not points:
                    continue
                # Only associate a hand with this person when a reliable body
                # wrist anchors it. This avoids gestures from neighboring crops.
                wrists = [body[i] for i in (15, 16) if len(body) > i
                          and body[i][3] >= self.settings.cv.keypoints.min_confidence]
                if not wrists or min(hypot(points[0][0] - wrist[0], points[0][1] - wrist[1])
                                     for wrist in wrists) > pw * .3:
                    continue
                seen_labels.add(label)
                detection = KeypointDetection(person.track_id, f"mediapipe_{label}_hand21", points,
                                               bbox=person.bbox)
                output.append(replace(detection, gestures=self._classifier.classify(detection, frame.timestamp, history)))
        return tuple(output)

    async def reset(self):
        if self._model_factory is not None:
            await self._worker.run(self._reset)

    def _reset(self):
        self._close()
        self._pose, self._face, self._hands = self._model_factory()
        self._spare_group = (self._pose, self._face, self._hands)

    async def close(self):
        await self._worker.run(self._close)
        await self._worker.close()

    def _close(self):
        groups = list(self._groups.values())
        if self._spare_group:
            groups.append(self._spare_group)
        if self._model_factory is None:
            groups.append((self._pose, self._face, self._hands))
        for group in groups:
            for model in group:
                if model is not None:
                    model.close()
        self._groups.clear()
        self._group_seen.clear()
        self._group_times.clear()
        self._spare_group = None
        self._pose = self._face = self._hands = None
