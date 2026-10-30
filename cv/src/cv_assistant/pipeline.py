"""Main CV pipeline — orchestrates all subsystems.

Ties together camera capture, detection, tracking, action recognition,
face identity, speaker association, and the integration adapter into
a single runnable pipeline with configurable inference rates.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from typing import Any

import cv2
import numpy as np

from cv_assistant import events
from cv_assistant.actions.heuristics import ActionClassifier
from cv_assistant.actions.keypoint_buffer import KeypointBuffer
from cv_assistant.camera import CameraService, Frame
from cv_assistant.config import settings
from cv_assistant.detection import (
    PersonDetection,
    PersonDetector,
    KP_LEFT_WRIST,
    KP_RIGHT_WRIST,
)
from cv_assistant.faces.detector import FaceDetector
from cv_assistant.faces.matcher import FaceMatcher
from cv_assistant.identity.service import IdentityService
from cv_assistant.identity.store import IdentityStore
from cv_assistant.integration.virtual_human_adapter import VirtualHumanAdapter
from cv_assistant.speaker.association import SpeakerAssociation
from cv_assistant.objects import ObjectDetector
from cv_assistant.state import DetectedObject, SceneState
from cv_assistant.tracking import TrackManager

logger = logging.getLogger(__name__)


class CVPipeline:
    """Real-time CV pipeline coordinating all subsystems.

    Call ``run()`` to start the pipeline.  Call ``stop()`` to shut down.
    """

    def __init__(
        self,
        config_overrides: dict[str, Any] | None = None,
        video_file: str | None = None,
        no_camera: bool = False,
    ) -> None:
        self._video_file = video_file
        self._no_camera = no_camera

        # Core components
        self._scene = SceneState()

        device = video_file or settings.camera.device_index
        self._camera = CameraService(
            device=device,
            width=settings.camera.width,
            height=settings.camera.height,
            fps=settings.camera.fps,
            queue_size=settings.camera.frame_queue_size,
        ) if not no_camera else None

        self._detector = PersonDetector(
            model_path=settings.detection.model,
            device=settings.detection.device,
            confidence=settings.detection.confidence,
            iou_threshold=settings.detection.iou_threshold,
        )

        self._track_manager = TrackManager(
            scene=self._scene,
            camera_id=settings.camera.camera_id,
        )

        if getattr(settings.actions, "use_ml_model", True):
            try:
                from cv_assistant.actions.classifier import MLActionClassifier
                self._action_classifier = MLActionClassifier()
            except Exception as e:
                logger.warning("Failed to initialize MLActionClassifier, falling back to heuristics: %s", e)
                self._action_classifier = ActionClassifier()
        else:
            self._action_classifier = ActionClassifier()
        self._keypoint_buffers: dict[int, KeypointBuffer] = {}

        # Face recognition (lazily initialized)
        self._face_detector: FaceDetector | None = None
        self._identity_store = IdentityStore(db_path=settings.identity.database_path)
        self._matcher = FaceMatcher()
        self._identity_service: IdentityService | None = None

        # Speaker association
        self._speaker_assoc: SpeakerAssociation | None = None

        # Integration adapter
        self._adapter: VirtualHumanAdapter | None = None

        # Runtime state
        self._running = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Object detection
        self._object_detector: ObjectDetector | None = None
        self._last_object_time: float = 0.0
        self._latest_frame_image: np.ndarray | None = None

        # Frontend camera frame queue (bounded to prevent latency buildup)
        self._frontend_frame_queue: collections.deque[Frame] = collections.deque(maxlen=2)

        # Performance counters
        self._frame_count = 0
        self._start_time = 0.0
        self._last_face_time: dict[int, float] = {}

    def _handle_frontend_frame(self, seq: int, timestamp: float, jpeg_bytes: bytes) -> None:
        """Decode incoming frontend JPEG frame and enqueue into bounded queue."""
        try:
            nparr = np.frombuffer(jpeg_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                frame = Frame(image=img, timestamp=timestamp, seq=seq, width=w, height=h)
                self._frontend_frame_queue.append(frame)
        except Exception as exc:
            logger.debug("Failed to decode frontend frame %d: %s", seq, exc)

    # ── Initialization ──────────────────────────────────────────────────

    def initialize(self) -> None:
        """Load models and open the identity store.  Call before ``run()``."""
        logger.info("Initializing CV pipeline...")

        # Person/pose detector
        self._detector.initialize()

        # Identity store
        self._identity_store.open()
        self._identity_service = IdentityService(
            store=self._identity_store,
            matcher=self._matcher,
            scene=self._scene,
        )

        # Face detector
        if settings.face.enabled:
            self._face_detector = FaceDetector(
                model_name=settings.face.model_name,
                detection_threshold=settings.face.detection_threshold,
            )
            try:
                self._face_detector.initialize()
            except Exception as exc:
                logger.error("Face detector init failed: %s. Continuing without face recognition.", exc)
                self._face_detector = None

        # Optional object detector
        if getattr(settings.objects, "enabled", False):
            try:
                self._object_detector = ObjectDetector(
                    model_path=settings.objects.model,
                    device=settings.detection.device,
                    confidence=settings.objects.confidence,
                    classes=getattr(settings.objects, "classes", None),
                )
                self._object_detector.initialize()
            except Exception as exc:
                logger.error("Object detector init failed: %s. Continuing without object detection.", exc)
                self._object_detector = None

        # Speaker association
        self._speaker_assoc = SpeakerAssociation(
            scene=self._scene,
            identity_service=self._identity_service,
        )

        # Integration adapter
        self._adapter = VirtualHumanAdapter(
            scene=self._scene,
            on_transcript=self._speaker_assoc.handle_transcript,
            on_detect_objects=self.detect_objects_oneshot,
            on_frame=self._handle_frontend_frame,
        )

        logger.info("CV pipeline initialized.")

    # ── Main loop ───────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the pipeline (blocking).  Run in a thread or with asyncio."""
        if self._no_camera:
            logger.info("Running in no-camera mock mode.")
            self._run_mock()
            return

        if self._camera is None:
            raise RuntimeError("Camera not configured.")

        self._camera.start()
        self._running.set()
        self._start_time = time.monotonic()
        self._frame_count = 0

        logger.info("CV pipeline running. Press Ctrl+C to stop.")

        target_interval = 1.0 / max(settings.detection.target_fps, 1)
        try:
            while self._running.is_set():
                loop_start = time.monotonic()

                frame = self._camera.get_frame(timeout=1.0)
                if frame is None:
                    if self._camera.is_video_file and not self._camera.is_running:
                        logger.info("Video file ended.")
                        break
                    continue

                self._process_frame(frame)

                # Pace to target FPS
                elapsed = time.monotonic() - loop_start
                if elapsed < target_interval:
                    time.sleep(target_interval - elapsed)

        except KeyboardInterrupt:
            logger.info("Interrupted.")
        finally:
            self.stop()

    async def run_async(self) -> None:
        """Async version of ``run()`` for integration with async event loops."""
        self._loop = asyncio.get_event_loop()

        if self._adapter:
            await self._adapter.start()

        if self._no_camera:
            logger.info("Running in no-camera mock mode (async). Adapter active.")
            self._running.set()
            try:
                frame_count = 0
                while self._running.is_set():
                    await asyncio.sleep(0.1)
                    if self._adapter:
                        await self._adapter.drain_events()
                        if self._adapter.has_clients():
                            frame_count += 1
                            mock_img = np.zeros((480, 640, 3), dtype=np.uint8)
                            mock_img[:] = (20, 24, 33)
                            cv2.putText(mock_img, "CV Subsystem: Mock Live Feed", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 240, 255), 2)
                            cv2.rectangle(mock_img, (160, 100), (480, 420), (34, 197, 94), 2)
                            cv2.putText(mock_img, "Bablu (#t1)", (165, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (34, 197, 94), 2)
                            
                            metadata = {
                                "timestamp": time.time(),
                                "frame_number": frame_count,
                                "people": [{
                                    "track_id": 1,
                                    "person_id": "mock-pid-1",
                                    "display_name": "Bablu",
                                    "confidence": 0.96,
                                    "bbox": [160, 100, 480, 420],
                                    "actions": ["waving"] if (frame_count // 30) % 2 == 1 else ["idle"]
                                }],
                                "objects": []
                            }
                            jpeg_bytes = await asyncio.to_thread(self._encode_jpeg, mock_img)
                            if jpeg_bytes:
                                await self._adapter.broadcast_frame(jpeg_bytes, metadata)
            except asyncio.CancelledError:
                pass
            finally:
                await self.stop_async()
            return

        is_frontend_source = getattr(settings.camera, "source", "frontend") == "frontend" and not self._video_file
        if not is_frontend_source and self._camera:
            self._camera.start()
        self._running.set()
        self._start_time = time.monotonic()

        target_interval = 1.0 / max(settings.detection.target_fps, 1)
        try:
            while self._running.is_set():
                loop_start = time.monotonic()

                frame = None
                if is_frontend_source:
                    # Pop newest frame from bounded frontend queue
                    if self._frontend_frame_queue:
                        frame = self._frontend_frame_queue.pop()
                        self._frontend_frame_queue.clear()
                    else:
                        await asyncio.sleep(0.01)
                        continue
                else:
                    frame = await asyncio.to_thread(
                        self._camera.get_frame, 1.0
                    ) if self._camera else None

                if frame is None:
                    if self._camera and self._camera.is_video_file and not self._camera.is_running:
                        break
                    await asyncio.sleep(0.05)
                    continue

                all_events = await asyncio.to_thread(self._process_frame, frame)

                # Broadcast events
                if self._adapter and all_events:
                    for ev in all_events:
                        await self._adapter.emit_event(ev)

                # Broadcast synchronized detection metadata
                if self._adapter and self._adapter.has_clients():
                    metadata = self.get_frame_metadata(frame)
                    if is_frontend_source:
                        # Frontend already has local frame image! Send lightweight JSON metadata
                        await self._adapter.broadcast_metadata(metadata)
                    else:
                        # Backend camera mode: encode and send frame + metadata
                        jpeg_bytes = await asyncio.to_thread(self._encode_jpeg, frame.image)
                        if jpeg_bytes:
                            await self._adapter.broadcast_frame(jpeg_bytes, metadata)

                # Drain queued events
                if self._adapter:
                    await self._adapter.drain_events()

                elapsed = time.monotonic() - loop_start
                if elapsed < target_interval:
                    await asyncio.sleep(target_interval - elapsed)

        except asyncio.CancelledError:
            pass
        finally:
            await self.stop_async()

    def _encode_jpeg(self, image: np.ndarray) -> bytes | None:
        """Encode an image to JPEG bytes."""
        try:
            success, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if success:
                return bytes(buf)
        except Exception as exc:
            logger.debug("Failed to encode JPEG: %s", exc)
        return None

    def _filter_spurious_detections(self, detections: list[PersonDetection]) -> list[PersonDetection]:
        """Filter out spurious limb/hand boxes so raised hands are not detected as separate people."""
        if len(detections) <= 1:
            return detections

        def bbox_area(b: tuple[float, float, float, float]) -> float:
            return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

        # Sort detections by bounding box area descending (largest person body first)
        sorted_dets = sorted(detections, key=lambda d: bbox_area(d.bbox), reverse=True)
        kept: list[PersonDetection] = []

        for det in sorted_dets:
            b = det.bbox
            kps = det.keypoints
            area_b = bbox_area(b)

            # Check 1: Must have upper body or torso anchor (nose, eyes, shoulders, hips)
            if kps is not None:
                head_conf = float(np.max(kps[0:5, 2])) if len(kps) > 4 else 0.0
                shoulder_conf = float(np.max(kps[5:7, 2])) if len(kps) > 6 else 0.0
                hip_conf = float(np.max(kps[11:13, 2])) if len(kps) > 12 else 0.0
                if max(head_conf, shoulder_conf, hip_conf) < 0.35:
                    # Isolated limb without head, shoulders, or hips
                    # Merge any valid wrist into the primary person body
                    if kept and det.keypoints is not None and kept[0].keypoints is not None:
                        for w_idx in (KP_LEFT_WRIST, KP_RIGHT_WRIST):
                            if det.keypoints[w_idx, 2] > kept[0].keypoints[w_idx, 2]:
                                kept[0].keypoints[w_idx] = det.keypoints[w_idx]
                    continue

            # Check 2: Check if this detection is a sub-part or arm/hand near an existing person
            is_sub_part = False
            for parent in kept:
                pa = parent.bbox
                area_pa = bbox_area(pa)
                if area_pa < 1.3 * area_b:
                    continue

                # Intersection containment
                ix1 = max(b[0], pa[0])
                iy1 = max(b[1], pa[1])
                ix2 = min(b[2], pa[2])
                iy2 = min(b[3], pa[3])
                inter_w = max(0.0, ix2 - ix1)
                inter_h = max(0.0, iy2 - iy1)
                containment = (inter_w * inter_h) / max(1.0, area_b)

                # Arm reach proximity check: hand box within reach of parent person
                px1, py1, px2, py2 = pa
                pw = px2 - px1
                ph = py2 - py1
                cx = (b[0] + b[2]) / 2.0
                cy = (b[1] + b[3]) / 2.0
                in_reach = (
                    (px1 - pw * 0.45) <= cx <= (px2 + pw * 0.45)
                    and (py1 - ph * 0.40) <= cy <= (py2 + ph * 0.10)
                )

                det_hip = float(np.max(kps[11:13, 2])) if kps is not None and len(kps) > 12 else 0.0
                det_head = float(np.max(kps[0:5, 2])) if kps is not None and len(kps) > 4 else 0.0

                if containment > 0.35 or (in_reach and area_b < 0.40 * area_pa and (det_hip < 0.30 or det_head < 0.40)):
                    # Sub-part / hand detection inside or adjacent to the person
                    is_sub_part = True
                    # If this sub-part has better wrist detection, merge into parent
                    if det.keypoints is not None and parent.keypoints is not None:
                        for w_idx in (KP_LEFT_WRIST, KP_RIGHT_WRIST):
                            if det.keypoints[w_idx, 2] > parent.keypoints[w_idx, 2]:
                                parent.keypoints[w_idx] = det.keypoints[w_idx]
                    break

            if not is_sub_part:
                kept.append(det)

        return kept

    def get_frame_metadata(self, frame: Frame) -> dict[str, Any]:
        """Construct synchronized frame metadata."""
        tracks = self._scene.get_all_tracks()
        now = time.monotonic()
        people = []
        for t in tracks:
            # Ignore unconfirmed noise tracks (must be stable or seen for at least 3 frames)
            if not t.stable and t.frames_seen < 3:
                continue
            # Ignore tracks not seen in current/recent frames (within 0.5s)
            if now - t.last_seen > 0.5:
                continue
            bbox_list = [round(float(coord), 1) for coord in t.bbox] if t.bbox is not None else None
            people.append({
                "track_id": t.track_id,
                "bbox": bbox_list,
                "person_id": t.person_id,
                "display_name": t.display_name or "Unknown",
                "actions": list(t.actions) if t.actions else [],
                "confidence": round(float(t.identity_confidence), 2),
                "consent_status": t.consent_status,
            })
        objects = []
        for o in self._scene.get_objects():
            o_bbox = [round(float(coord), 1) for coord in o.bbox] if o.bbox is not None else None
            objects.append({
                "label": o.label,
                "confidence": round(float(o.confidence), 2),
                "bbox": o_bbox,
            })
        return {
            "type": "frame",
            "seq": frame.seq,
            "timestamp": frame.timestamp,
            "width": frame.width,
            "height": frame.height,
            "people": people,
            "objects": objects,
        }

    def _process_frame(self, frame: Frame) -> list[events.CVEvent]:
        """Process a single frame through all pipeline stages."""
        self._frame_count += 1
        all_events: list[events.CVEvent] = []

        try:
            # 1. Detection + tracking
            raw_detections = self._detector.detect_with_tracking(
                frame.image,
                tracker_config=settings.tracking.tracker,
            )
            detections = self._filter_spurious_detections(raw_detections)

            detected_ids: set[int] = set()
            for det in detections:
                if det.track_id is None:
                    continue
                detected_ids.add(det.track_id)

                # Update track state
                track = self._scene.update_track(
                    det.track_id,
                    bbox=det.bbox,
                    keypoints=det.keypoints,
                )

                # Mouth motion tracking for active visual speaker detection
                if track and det.bbox is not None and frame.image is not None:
                    self._update_mouth_motion(track, det, frame.image)

                # 2. Keypoint buffer + action recognition
                if det.keypoints is not None:
                    if det.track_id not in self._keypoint_buffers:
                        self._keypoint_buffers[det.track_id] = KeypointBuffer(
                            max_size=settings.actions.buffer_size,
                        )
                    self._keypoint_buffers[det.track_id].add(
                        det.keypoints, frame.timestamp, det.bbox,
                    )

                    actions = self._action_classifier.classify(
                        det.track_id,
                        self._keypoint_buffers[det.track_id],
                        frame_image=frame.image,
                    )
                    track = self._scene.get_track(det.track_id)
                    now_ts = time.monotonic()
                    prev_actions = list(track.actions) if (track and track.actions) else []
                    if track:
                        if actions:
                            track.actions = [a.action for a in actions]
                            track.last_action_time = now_ts
                        else:
                            track.actions = []

                    # If an action ended on this frame, emit action_ended event
                    if prev_actions and (not track or not track.actions):
                        for ended_action in prev_actions:
                            all_events.append(events.action_ended(
                                track_id=det.track_id,
                                action=ended_action,
                                person_id=track.person_id if track else None,
                                display_name=track.display_name if track else None,
                                camera_id=settings.camera.camera_id,
                            ))

                    for action in actions:
                        if self._scene.check_cooldown(
                            f"action:{det.track_id}:{action.action}",
                            settings.integration.event_cooldown_sec,
                        ):
                            all_events.append(events.action_detected(
                                track_id=det.track_id,
                                action=action.action,
                                confidence=action.confidence,
                                person_id=track.person_id if track else None,
                                display_name=track.display_name if track else None,
                                camera_id=settings.camera.camera_id,
                            ))

                # 3. Face recognition (rate-limited per track)
                if (
                    self._face_detector is not None
                    and self._identity_service is not None
                    and det.track_id is not None
                ):
                    self._maybe_run_face(det.track_id, frame)

            # 4. Track lifecycle (enter/leave)
            lifecycle_events = self._track_manager.update(detected_ids)
            all_events.extend(lifecycle_events)

            # 5. Optional general object detection (rate-limited)
            self._latest_frame_image = frame.image
            if self._object_detector is not None:
                now = time.monotonic()
                target_obj_interval = 1.0 / max(getattr(settings.objects, "target_fps", 2), 0.1)
                if now - self._last_object_time >= target_obj_interval:
                    self._last_object_time = now
                    try:
                        detected_objs = self._object_detector.detect(frame.image)
                        self._scene.update_objects(detected_objs)
                        if detected_objs and self._scene.check_cooldown("objects_detected", settings.integration.event_cooldown_sec):
                            all_events.append(events.objects_detected(
                                objects=[
                                    {"label": o.label, "confidence": o.confidence, "bbox": o.bbox}
                                    for o in detected_objs
                                ],
                                camera_id=settings.camera.camera_id,
                            ))
                    except Exception as exc:
                        logger.debug("Object detection pass failed: %s", exc)

            # Clean up keypoint buffers for removed tracks
            active_ids = {t.track_id for t in self._scene.get_all_tracks()}
            stale_tracks = (
                set(self._keypoint_buffers) | set(self._last_face_time)
            ) - active_ids
            for tid in stale_tracks:
                self._keypoint_buffers.pop(tid, None)
                self._last_face_time.pop(tid, None)
                if self._identity_service is not None:
                    self._identity_service.forget_track(tid)

        except Exception as exc:
            logger.exception("Error processing frame %d: %s", self._frame_count, exc)
            all_events.append(events.cv_error(
                message=str(exc),
                stage="frame_processing",
                camera_id=settings.camera.camera_id,
            ))

        return all_events

    def _update_mouth_motion(
        self,
        track: TrackedPerson,
        det: PersonDetection,
        image: np.ndarray,
    ) -> None:
        """Compute visual mouth motion for active speaker disambiguation."""
        try:
            h, w = image.shape[:2]
            mx, my = None, None
            scale = 40.0
            if det.keypoints is not None:
                n_conf = float(det.keypoints[KP_NOSE, 2])
                if n_conf >= 0.25:
                    nx, ny = float(det.keypoints[KP_NOSE, 0]), float(det.keypoints[KP_NOSE, 1])
                    ls_conf = float(det.keypoints[KP_LEFT_SHOULDER, 2])
                    rs_conf = float(det.keypoints[KP_RIGHT_SHOULDER, 2])
                    if ls_conf >= 0.25 and rs_conf >= 0.25:
                        ls_x = float(det.keypoints[KP_LEFT_SHOULDER, 0])
                        rs_x = float(det.keypoints[KP_RIGHT_SHOULDER, 0])
                        scale = max(25.0, abs(ls_x - rs_x) * 0.35)
                    else:
                        scale = max(25.0, (det.bbox[2] - det.bbox[0]) * 0.22)
                    mx = int(nx)
                    my = int(ny + scale * 0.65)

            if mx is None or my is None:
                bx1, by1, bx2, by2 = det.bbox
                mx = int((bx1 + bx2) / 2.0)
                my = int(by1 + (by2 - by1) * 0.22)
                scale = max(25.0, (bx2 - bx1) * 0.20)

            half_w = int(scale * 0.45)
            half_h = int(scale * 0.35)
            x1 = max(0, mx - half_w)
            x2 = min(w, mx + half_w)
            y1 = max(0, my - half_h)
            y2 = min(h, my + half_h)

            if x2 > x1 + 5 and y2 > y1 + 5:
                crop = image[y1:y2, x1:x2]
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (24, 18))
                now_m = time.monotonic()
                if hasattr(track, "_last_mouth_crop") and getattr(track, "_last_mouth_crop", None) is not None:
                    diff = float(np.mean(np.abs(small.astype(np.float32) - track._last_mouth_crop.astype(np.float32))))
                    track.mouth_history.append((now_m, diff))
                    if len(track.mouth_history) > 30:
                        track.mouth_history = track.mouth_history[-30:]
                track._last_mouth_crop = small
        except Exception as exc:
            logger.debug("Mouth motion tracking error: %s", exc)

    def _maybe_run_face(self, track_id: int, frame: Frame) -> None:
        """Run face detection/matching for a track if the cooldown has elapsed."""
        now = time.monotonic()
        track = self._scene.get_track(track_id)
        if track is None:
            return

        # Keep checking until speech-created identities get a face embedding.
        has_face_embedding = (
            track.person_id is not None
            and self._identity_service is not None
            and self._identity_service.has_face_embedding(track.person_id)
        )
        if has_face_embedding:
            interval = settings.face.recheck_interval_sec
        else:
            interval = settings.face.embedding_interval_sec

        last = self._last_face_time.get(track_id, 0.0)
        if now - last < interval:
            return

        self._last_face_time[track_id] = now

        if track.bbox is None or self._face_detector is None or self._identity_service is None:
            return

        try:
            faces = self._face_detector.detect_faces(
                frame.image, bbox=track.bbox,
            )
            if faces:
                # Use the best-scoring face
                best_face = max(faces, key=lambda f: f.get("score", 0))
                result, face_events = self._identity_service.process_face_observation(
                    track_id=track_id,
                    face=best_face,
                    frame_shape=frame.image.shape,
                )
                # Queue events for emission
                if self._adapter:
                    for ev in face_events:
                        self._adapter.emit_event_sync(ev)

        except Exception as exc:
            logger.debug("Face processing failed for track %d: %s", track_id, exc)

    def _run_mock(self) -> None:
        """Run in mock mode for testing without a camera."""
        self._running.set()
        logger.info("Mock pipeline running. Send events via adapter.")
        try:
            while self._running.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # ── Shutdown ─────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Stop the pipeline and release resources."""
        self._running.clear()
        if self._camera:
            self._camera.stop()
        self._identity_store.close()
        self._scene.clear()
        self._keypoint_buffers.clear()
        self._last_face_time.clear()
        logger.info(
            "CV pipeline stopped. Frames processed: %d",
            self._frame_count,
        )

    async def stop_async(self) -> None:
        """Async shutdown."""
        if self._adapter:
            await self._adapter.stop()
        self.stop()

    # ── Accessors ───────────────────────────────────────────────────────

    @property
    def scene(self) -> SceneState:
        return self._scene

    @property
    def adapter(self) -> VirtualHumanAdapter | None:
        return self._adapter

    @property
    def identity_store(self) -> IdentityStore:
        return self._identity_store

    @property
    def identity_service(self) -> IdentityService | None:
        return self._identity_service

    def detect_objects_oneshot(self, frame_image: np.ndarray | None = None) -> list[dict[str, Any]]:
        """Run on-demand one-shot object detection (e.g. for 'What is this?')."""
        target_img = frame_image if frame_image is not None else self._latest_frame_image
        if target_img is None:
            return [
                {"label": o.label, "confidence": o.confidence, "bbox": o.bbox}
                for o in self._scene.get_objects()
            ]

        if self._object_detector is None:
            self._object_detector = ObjectDetector(
                model_path=settings.objects.model,
                device=settings.detection.device,
                confidence=settings.objects.confidence,
                classes=getattr(settings.objects, "classes", None),
            )
            self._object_detector.initialize()

        objs = self._object_detector.detect(target_img)
        self._scene.update_objects(objs)
        return [{"label": o.label, "confidence": o.confidence, "bbox": o.bbox} for o in objs]

    def get_stats(self) -> dict[str, Any]:
        """Return performance statistics."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return {
            "frames_processed": self._frame_count,
            "elapsed_seconds": round(elapsed, 2),
            "avg_fps": round(self._frame_count / elapsed, 2) if elapsed > 0 else 0,
            "active_tracks": len(self._scene.get_all_tracks()),
            "stable_tracks": len(self._scene.get_stable_tracks()),
        }
