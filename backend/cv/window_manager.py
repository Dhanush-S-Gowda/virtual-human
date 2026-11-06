"""Capture-time windows independent of inference latency; no model imports.

Call submit() for ordered browser frames and advance() from the source-clock
heartbeat. Read immutable events with drain_events(). One manager owns one source.
"""

import asyncio
import json
import logging
import uuid
from time import perf_counter
from typing import Any, Callable

from backend.cv import events
from backend.config import settings as global_settings
from backend.diagnostics import record as trace_record
from backend.cv.object_det.state import SceneState

settings = global_settings.cv
logger = logging.getLogger(__name__)

from collections import Counter, deque
from dataclasses import dataclass, field, replace
from math import floor, isfinite

from .events import Frame, FrameRecord, PersonSummary, WindowEvent, WindowSummary
from .face_recog.base import FaceRecognitionProvider
from .keypoint_det.base import KeypointDetectionProvider, KeypointSample
from .object_det.base import ObjectDetectionProvider


@dataclass
class Window:
    window_id: int
    start: float
    end: float
    frames: dict[int, FrameRecord] = field(default_factory=dict)
    pending_tasks: int = 0
    closed: bool = False
    revision: int = 0

    def summary(self) -> WindowSummary:
        objects = {}
        faces = {}
        gestures = {}
        lips = {}
        errors = []
        durations = {}
        for record in sorted(self.frames.values(), key=lambda item: item.timestamp):
            for obj in record.objects:
                objects[obj.track_id] = obj
            for face in record.faces:
                faces[face.track_id] = face
            for points in record.keypoints:
                gestures.setdefault(points.track_id, set()).update(points.gestures)
                if points.schema == "mediapipe_lips":
                    lips[points.track_id] = points
            errors.extend(record.errors)
            for stage, duration in record.stage_ms:
                durations.setdefault(stage, []).append(duration)
        counts = Counter(obj.label for obj in objects.values())
        people = tuple(
            PersonSummary(
                obj.track_id,
                faces[obj.track_id].display_name if obj.track_id in faces else None,
                tuple(sorted(gestures.get(obj.track_id, ()))),
                lips[obj.track_id].mouth_openness if obj.track_id in lips else None,
                lips[obj.track_id].mouth_motion if obj.track_id in lips else None,
            )
            for obj in sorted(objects.values(), key=lambda item: item.track_id)
            if obj.label == "person"
        )
        return WindowSummary(
            self.window_id, self.start, self.end, self.revision, len(objects),
            tuple(sorted(counts.items())), people, self.pending_tasks, tuple(errors),
            tuple((stage, round(sum(values) / len(values), 2), len(values))
                  for stage, values in sorted(durations.items())),
        )


class WindowManager:
    def __init__(
        self,
        object_provider: ObjectDetectionProvider,
        face_provider: FaceRecognitionProvider,
        keypoint_provider: KeypointDetectionProvider,
        *,
        window_seconds: float = 1.0,
        object_fps: float = 10.0,
        keypoint_fps: float = 10.0,
        retained_windows: int = 30,
        history_seconds: float = 3.0,
        history_samples: int = 300,
        max_face_tasks: int = 4,
        face_retry_seconds: float = 1.0,
        face_reentry_seconds: float = .75,
    ):
        for value in (window_seconds, object_fps, keypoint_fps, history_seconds,
                      face_retry_seconds, face_reentry_seconds):
            if not isfinite(value) or value <= 0:
                raise ValueError("Window, rates, and history duration must be positive and finite")
        if retained_windows < 1 or history_samples < 1 or max_face_tasks < 1:
            raise ValueError("Retention limits must be positive")
        self.object_provider = object_provider
        self.face_provider = face_provider
        self.keypoint_provider = keypoint_provider
        self.window_seconds = window_seconds
        self.object_fps = object_fps
        self.keypoint_fps = keypoint_fps
        self.retained_windows = retained_windows
        self.history_seconds = history_seconds
        self.max_face_tasks = max_face_tasks
        self.face_retry_seconds = face_retry_seconds
        self.face_reentry_seconds = face_reentry_seconds
        self._face_attempts = {}
        self._track_generations = {}
        self._updates = {}
        self._last_objects = ()
        self._object_timestamp = -1.
        self._history = deque(maxlen=history_samples)
        self._windows: dict[int, Window] = {}
        self._events: dict[int, WindowEvent] = {}
        self._tasks: set[asyncio.Task] = set()
        self._busy: set[str] = set()
        self._seen_people: dict[int, float] = {}
        self._identities = {}
        self._last_sample = {"objects": float("-inf"), "keypoints": float("-inf")}
        self._watermark = 0.0
        self._last_frame_id = -1
        self._last_timestamp = -1.0
        self._next_close: int | None = None
        self.discarded_results = 0
        self._closed = False

    @classmethod
    def from_settings(cls, object_provider, face_provider, keypoint_provider, settings):
        """Use the global backend settings without importing config or models."""
        return cls(
            object_provider, face_provider, keypoint_provider,
            window_seconds=settings.cv_window_seconds,
            object_fps=settings.cv_object_fps,
            keypoint_fps=settings.cv_keypoint_fps,
            retained_windows=settings.cv_retained_windows,
            history_seconds=settings.cv_keypoint_history_seconds,
            history_samples=settings.cv_keypoint_history_samples,
            max_face_tasks=settings.cv_max_face_tasks,
            face_retry_seconds=getattr(settings, "cv_face_retry_seconds", 1.0),
            face_reentry_seconds=getattr(settings, "cv_face_reentry_seconds", .75),
        )

    async def initialize(self):
        for provider in (self.object_provider, self.face_provider, self.keypoint_provider):
            await provider.initialize()

    def get_window(self, window_id: int) -> tuple[FrameRecord, ...]:
        """Return frame records without exposing mutable manager state."""
        window = self._windows[window_id]
        return tuple(sorted(window.frames.values(), key=lambda item: item.frame_id))

    def drain_events(self) -> tuple[WindowEvent, ...]:
        """Return latest undelivered revision per window, bounding event backlog."""
        events = tuple(self._events.values())
        self._events.clear()
        return events

    def drain_updates(self) -> tuple[FrameRecord, ...]:
        """Frame-local results for overlays; later updates replace earlier ones."""
        updates = tuple(self._updates.values())
        self._updates.clear()
        return updates

    def summaries(self) -> tuple[WindowSummary, ...]:
        return tuple(window.summary() for window in self._windows.values() if window.closed)

    def visual_history_cutoff(self, seconds, windows=0):
        if windows:
            return max(0., (floor(self._watermark / self.window_seconds) - windows + 1) * self.window_seconds)
        return max(0., self._watermark - seconds)

    def visual_history(self, seconds, windows=0):
        """Compact capture-time observations, including open and revised windows."""
        cutoff = self.visual_history_cutoff(seconds, windows)
        result = {"mode": "windows" if windows else "seconds", "requested_seconds": seconds,
                  "window_limit": windows, "window_seconds": self.window_seconds,
                  "windows": []}
        if not windows and not seconds:
            return result
        for window in sorted(self._windows.values(), key=lambda item: item.window_id):
            if window.end <= cutoff or window.start > self._watermark:
                continue
            frames = {key: frame for key, frame in window.frames.items()
                      if cutoff <= frame.timestamp <= self._watermark}
            if not frames:
                continue
            summary = replace(window, frames=frames).summary()
            gestures, faces = {}, {}
            for frame in sorted(frames.values(), key=lambda item: item.timestamp):
                faces.update({face.track_id: face for face in frame.faces})
                for point in frame.keypoints:
                    for action in point.gestures:
                        gestures[(point.track_id, action)] = frame.timestamp
            counts = dict(summary.object_counts)
            result["windows"].append({
                "window_id": window.window_id, "revision": window.revision, "closed": window.closed,
                "start_seconds_ago": round(self._watermark - max(window.start, cutoff), 3),
                "end_seconds_ago": round(max(0., self._watermark - window.end), 3),
                "pending_tasks": window.pending_tasks,
                "objects_observed": any("objects" in frame.completed for frame in frames.values()),
                "counts": {"unique_people": counts.get("person", 0),
                           "unique_objects": sum(count for label, count in counts.items() if label != "person"),
                           "recognized_faces": sum(bool(face.person_id) for face in faces.values())},
                "object_counts": {label: count for label, count in counts.items() if label != "person"},
                "people": [{"track_id": person.track_id, "name": person.display_name or "Unknown"}
                           for person in summary.people],
                "gestures": [{"track_id": track_id,
                              "name": (faces[track_id].display_name or "Unknown") if track_id in faces else "Unknown",
                              "action": action, "last_seen_seconds_ago": round(self._watermark - timestamp, 3)}
                             for (track_id, action), timestamp in sorted(gestures.items(), key=lambda item: item[1], reverse=True)],
            })
        return result

    def advance(self, timestamp: float):
        if not isfinite(timestamp) or timestamp < self._watermark:
            raise ValueError("Capture-clock watermark must be finite and nondecreasing")
        self._watermark = timestamp
        if self._next_close is None:
            return
        current = floor(timestamp / self.window_seconds)
        oldest = max(0, current - self.retained_windows + 1)
        # Bounded catch-up after a long source-clock gap.
        self._next_close = max(self._next_close, oldest)
        for index in range(self._next_close, current):
            window = self._window(index)
            window.closed = True
            self._publish(window)
        self._next_close = max(self._next_close, current)
        for index in list(self._windows):
            if index < oldest:
                del self._windows[index]
                self._events.pop(index, None)
        retained_ids = {frame_id for window in self._windows.values() for frame_id in window.frames}
        self._updates = {key: value for key, value in self._updates.items() if key in retained_ids}
        self._history = deque(
            (sample for sample in self._history if sample.timestamp >= timestamp - self.history_seconds),
            maxlen=self._history.maxlen,
        )
        self._seen_people = {
            track: seen for track, seen in self._seen_people.items()
            if seen >= timestamp - self.retained_windows * self.window_seconds
        }
        self._identities = {
            track: identity for track, identity in self._identities.items()
            if track in self._seen_people
        }
        self._face_attempts = {track: ts for track, ts in self._face_attempts.items()
                               if track in self._seen_people}
        self._track_generations = {track: generation for track, generation in self._track_generations.items()
                                   if track in self._seen_people}

    def _window(self, index):
        if index not in self._windows:
            start = index * self.window_seconds
            self._windows[index] = Window(index, start, start + self.window_seconds)
        return self._windows[index]

    def submit(self, frame: Frame):
        """Sample ordered captures; skip work when that provider is still busy."""
        if self._closed:
            raise RuntimeError("Window manager is closed")
        if frame.frame_id <= self._last_frame_id or frame.timestamp < self._last_timestamp:
            raise ValueError("Frame IDs must increase and capture timestamps must not decrease")
        if frame.timestamp < self._watermark - (self.retained_windows - 1) * self.window_seconds:
            raise ValueError("Frame predates retained capture windows")
        asyncio.get_running_loop()
        self._last_frame_id, self._last_timestamp = frame.frame_id, frame.timestamp
        index = floor(frame.timestamp / self.window_seconds)
        if self._next_close is None:
            self._next_close = index
        self.advance(max(self._watermark, frame.timestamp))
        window = self._window(index)
        if index < self._next_close:
            window.closed = True
        shape = getattr(frame.image, "shape", (0, 0))
        window.frames[frame.frame_id] = FrameRecord(
            frame.frame_id, frame.timestamp, width=int(shape[1]), height=int(shape[0]),
        )
        dependency = None
        for stage, fps in (("objects", self.object_fps), ("keypoints", self.keypoint_fps)):
            if stage not in self._busy and frame.timestamp - self._last_sample[stage] + 1e-9 >= 1 / fps:
                self._last_sample[stage] = frame.timestamp
                task = self._schedule(stage, frame, index, dependency=dependency)
                if stage == "objects":
                    dependency = task
        if window.closed:
            self._publish(window)

    def _schedule(self, stage, frame, index, person=None, dependency=None):
        window = self._windows[index]
        window.pending_tasks += 1
        self._busy.add(stage)
        task = asyncio.create_task(self._infer(stage, frame, index, person, dependency))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _infer(self, stage, frame, index, person, dependency):
        started = perf_counter()
        try:
            if stage == "objects":
                result = tuple(await self.object_provider.detect(frame))
            elif stage == "keypoints":
                tracks = await dependency if dependency is not None else self._last_objects
                frame = replace(frame, tracks=tracks or ())
                history = tuple(sorted(
                    (sample for sample in self._history if sample.timestamp <= frame.timestamp),
                    key=lambda sample: sample.timestamp,
                ))
                result = tuple(await self.keypoint_provider.detect(frame, history))
            else:
                result = (await self.face_provider.recognize(frame, person),)
                if result[0].track_id != person.track_id:
                    raise ValueError("Face provider returned a different person track")
            window = self._windows.get(index)
            if window is None:
                self.discarded_results += 1
                return
            record = window.frames[frame.frame_id]
            field_name = stage if stage in ("objects", "keypoints") else "faces"
            if field_name == "faces":
                result = tuple(face for face in record.faces if face.track_id != person.track_id) + result
            window.frames[frame.frame_id] = replace(record, **{field_name: result})
            if stage == "objects":
                previous_people = {obj.track_id for obj in self._last_objects if obj.label == "person"}
                if frame.timestamp >= self._object_timestamp:
                    self._last_objects, self._object_timestamp = result, frame.timestamp
                for obj in result:
                    if obj.label == "person":
                        last_seen = self._seen_people.get(obj.track_id)
                        if (last_seen is not None and obj.track_id not in previous_people
                                and frame.timestamp - last_seen > self.face_reentry_seconds):
                            forget = getattr(self.face_provider, "forget_track", None)
                            if forget:
                                await forget(obj.track_id)
                            self._identities.pop(obj.track_id, None)
                            self._face_attempts.pop(obj.track_id, None)
                            self._track_generations.pop(obj.track_id, None)
                        self._track_generations.setdefault(obj.track_id, frame.frame_id)
                        self._seen_people[obj.track_id] = frame.timestamp
                        identity = self._identities.get(obj.track_id)
                        if ((identity is None or identity.needs_retry)
                                and f"face:{obj.track_id}" not in self._busy
                                and frame.timestamp - self._face_attempts.get(obj.track_id, -float("inf"))
                                >= self.face_retry_seconds):
                            if sum(name.startswith("face:") for name in self._busy) >= self.max_face_tasks:
                                continue
                            self._face_attempts[obj.track_id] = frame.timestamp
                            # Unknown faces remain eligible; tracker IDs never prove identity.
                            self._schedule(f"face:{obj.track_id}", frame, index, obj)
                cached_faces = tuple(
                    self._identities[obj.track_id] for obj in result
                    if obj.label == "person" and obj.track_id in self._identities
                )
                window.frames[frame.frame_id] = replace(
                    window.frames[frame.frame_id], faces=cached_faces,
                )
            elif stage == "keypoints":
                for detection in result:
                    if frame.timestamp >= self._watermark - self.history_seconds:
                        self._history.append(KeypointSample(frame.frame_id, frame.timestamp, detection))
            else:
                self._identities[person.track_id] = next(face for face in result if face.track_id == person.track_id)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            window = self._windows.get(index)
            if window is not None:
                record = window.frames[frame.frame_id]
                window.frames[frame.frame_id] = replace(
                    record, errors=record.errors + (f"{stage}: {type(exc).__name__}: {exc}",)
                )
        finally:
            self._busy.discard(stage)
            window = self._windows.get(index)
            if window is not None:
                window.pending_tasks -= 1
                record = window.frames[frame.frame_id]
                window.frames[frame.frame_id] = replace(record, completed=record.completed + (stage,))
                current = window.frames[frame.frame_id]
                window.frames[frame.frame_id] = replace(
                    current, stage_ms=current.stage_ms + ((stage.split(":")[0], round((perf_counter() - started) * 1000, 2)),))
                self._updates[frame.frame_id] = window.frames[frame.frame_id]
                if window.closed:
                    self._publish(window)

    def _publish(self, window):
        window.revision += 1
        self._events[window.window_id] = WindowEvent(window.summary())

    async def wait_idle(self):
        """Wait for current inference, including face jobs spawned by objects."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def close(self):
        """Cancel and await inference before releasing the source manager."""
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Tasks cancelled before their first execution cannot run a finally block.
        self._tasks.clear()
        self._busy.clear()
        for window in self._windows.values():
            if window.pending_tasks:
                window.pending_tasks = 0
                if window.closed:
                    self._publish(window)


class VirtualHumanAdapter:
    """Bridge between the CV subsystem and the virtual-human assistant.

    This adapter:
    - Exposes a WebSocket server for the assistant to connect to.
    - Receives STT transcript events from the assistant.
    - Sends CV events to connected clients.
    - Provides synchronous scene state queries.
    """

    def __init__(
        self,
        scene: SceneState,
        on_transcript: Callable[[str], list[events.CVEvent]] | None = None,
        on_detect_objects: Callable[[], list[dict[str, Any]]] | None = None,
        on_frame: Callable[[int, float, bytes], None] | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self._scene = scene
        self._on_transcript = on_transcript
        self._on_detect_objects = on_detect_objects
        self._on_frame = on_frame
        self._host = host or settings.integration.websocket_host
        self._port = port if port is not None else settings.integration.websocket_port
        self._clients: set[Any] = set()
        self._server: Any = None
        self._event_queue: asyncio.Queue[events.CVEvent] = asyncio.Queue(maxsize=100)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket server and event broadcaster."""
        try:
            import websockets
            self._server = await websockets.serve(
                self._handle_client,
                self._host,
                self._port, max_size=global_settings.cv.max_frame_bytes + 20, max_queue=2,
            )
            logger.info(
                "CV adapter WebSocket server listening on ws://%s:%d",
                self._host, self._port,
            )
        except ImportError:
            raise RuntimeError("Install backend CV dependencies to start the WebSocket service")
        except Exception as exc:
            raise RuntimeError("Failed to start CV WebSocket server") from exc

    async def stop(self) -> None:
        """Shut down the WebSocket server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()
        logger.info("CV adapter stopped.")

    def has_clients(self) -> bool:
        """Check if any WebSocket clients are currently connected."""
        return len(self._clients) > 0

    async def broadcast_frame(self, jpeg_bytes: bytes, metadata: dict[str, Any]) -> None:
        """Broadcast a synchronized video frame with detection metadata to clients."""
        if not self._clients:
            return
        meta_json = json.dumps(metadata).encode("utf-8")
        packet = len(meta_json).to_bytes(4, byteorder="big") + meta_json + jpeg_bytes
        disconnected = set()
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send(packet), timeout=0.2)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    # ── Event emission ──────────────────────────────────────────────────

    async def emit_event(self, event: events.CVEvent) -> None:
        """Broadcast a CV event to all connected clients."""
        msg = event.to_json()
        trace_record("cv", event.event_type, data=msg)
        disconnected = set()
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send(msg), timeout=0.2)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    async def broadcast_metadata(self, metadata: dict[str, Any]) -> None:
        """Broadcast metadata JSON to connected clients."""
        if not self._clients:
            return
        msg = json.dumps(metadata)
        disconnected = set()
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send(msg), timeout=0.2)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    def emit_event_sync(self, event: events.CVEvent) -> None:
        """Queue an event for async broadcast (call from sync context)."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full. Dropping event: %s", event.event_type)

    async def drain_events(self) -> None:
        """Send all queued events to connected clients."""
        while not self._event_queue.empty():
            try:
                event = self._event_queue.get_nowait()
                await self.emit_event(event)
            except asyncio.QueueFull:
                break

    # ── Scene queries ───────────────────────────────────────────────────

    def get_current_scene(self) -> dict[str, Any]:
        """Return a compact JSON snapshot of the current scene."""
        return self._scene.get_current_scene()

    def get_visible_people(self) -> list[dict[str, Any]]:
        """Return the list of currently visible people."""
        return self._scene.get_visible_people()

    # ── Transcript receiver ─────────────────────────────────────────────

    async def receive_transcript(self, text: str) -> list[events.CVEvent]:
        """Process an STT transcript from the assistant.

        Delegates to the registered ``on_transcript`` handler (typically
        the speaker association module).
        """
        if self._on_transcript is not None:
            return self._on_transcript(text)
        return []

    # ── WebSocket handler ───────────────────────────────────────────────

    async def _handle_client(self, websocket: Any, *args: Any, **kwargs: Any) -> None:
        """Handle an incoming WebSocket connection."""
        self._clients.add(websocket)
        logger.info("Client connected to CV adapter. Total: %d", len(self._clients))
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await self._handle_binary_message(websocket, message)
                else:
                    try:
                        data = json.loads(message)
                        await self._handle_message(websocket, data)
                    except json.JSONDecodeError:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "message": "Invalid JSON",
                        }))
        except Exception as exc:
            logger.debug("Client disconnected: %s", exc)
        finally:
            self._clients.discard(websocket)
            if websocket is getattr(self, "_owner", None):
                self._owner = None
            logger.info("Client disconnected. Total: %d", len(self._clients))

    async def _handle_binary_message(self, websocket: Any, message: bytes) -> None:
        """Handle incoming binary frame packets from frontend.

        Protocol:
        - 4B Magic: b'FRM1' (0x46524D31)
        - 4B Seq: uint32 big-endian
        - 8B Timestamp: float64 big-endian (ms)
        - 4B Length: uint32 big-endian (JPEG payload size)
        - Raw JPEG bytes
        """
        if len(message) < 20:
            logger.warning("Dropped binary packet: too small (%d bytes)", len(message))
            return

        magic = message[:4]
        if magic != b"FRM1":
            logger.warning("Dropped binary packet: invalid magic %r", magic)
            return

        import struct
        seq, ts_ms, jpeg_len = struct.unpack(">IdI", message[4:20])
        if len(message) != 20 + jpeg_len:
            logger.warning(
                "Dropped binary packet: payload length mismatch (header %d, actual %d)",
                jpeg_len, len(message) - 20,
            )
            return

        jpeg_bytes = message[20:20 + jpeg_len]
        if self._on_frame is not None:
            import inspect
            result = self._on_frame(seq, ts_ms / 1000.0, jpeg_bytes)
            if inspect.isawaitable(result):
                await result

    async def _handle_message(self, websocket: Any, data: dict[str, Any]) -> None:
        """Route incoming messages from clients."""
        msg_type = data.get("type", "")

        if msg_type == "get_scene":
            scene = self.get_current_scene()
            await websocket.send(json.dumps({"type": "scene", "data": scene}))

        elif msg_type == "get_people":
            people = self.get_visible_people()
            await websocket.send(json.dumps({"type": "people", "data": people}))

        elif msg_type == "transcript":
            text = data.get("text", "")
            results = await self.receive_transcript(text)
            for ev in results:
                await self.emit_event(ev)
            await websocket.send(json.dumps({
                "type": "transcript_ack",
                "events_emitted": len(results),
            }))

        elif msg_type == "detect_objects":
            if self._on_detect_objects is not None:
                objs = self._on_detect_objects()
                await websocket.send(json.dumps({"type": "objects", "data": objs}))
            else:
                scene = self.get_current_scene()
                await websocket.send(json.dumps({"type": "objects", "data": scene.get("objects", [])}))

        else:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            }))

    def detect_objects_oneshot(self) -> list[dict[str, Any]]:
        """Run or return one-shot object detection."""
        if self._on_detect_objects is not None:
            return self._on_detect_objects()
        return self.get_current_scene().get("objects", [])


class CVService(VirtualHumanAdapter):
    """Independent, single-camera CV server using the browser's FRM1 protocol.

    Observers may query data; only one connection may supply camera frames.
    A new camera connection resets capture windows and transient tracker state.
    """

    def __init__(self, object_provider, face_provider, keypoint_provider, settings, **kwargs):
        self.settings = settings
        self.providers = (object_provider, face_provider, keypoint_provider)
        self.manager = WindowManager.from_settings(*self.providers, settings)
        self._owner = None
        self._source_id = str(uuid.uuid4())
        self._claim_lock = asyncio.Lock()
        self._clock_origin = None
        self._source_origin = None
        self._latest_objects = None
        self._latest_points = None
        self._recent_gestures = {}
        self._tick_task = None
        self._publish_task = None
        self._outgoing = asyncio.Queue(maxsize=2)
        super().__init__(scene=self, **kwargs)

    async def start(self):
        try:
            await self.manager.initialize()
            await super().start()
            self._tick_task = asyncio.create_task(self._tick())
            self._publish_task = asyncio.create_task(self._publish_outgoing())
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        for task in (self._tick_task, self._publish_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self._tick_task, self._publish_task) if task),
                             return_exceptions=True)
        await super().stop()
        # to_thread inference cannot be force-stopped safely; finish it before
        # resetting or closing providers that may still be using model state.
        await self.manager.wait_idle()
        await self.manager.close()
        for provider in reversed(self.providers):
            close = getattr(provider, "close", None)
            if close:
                await close()

    async def _handle_binary_message(self, websocket, message):
        import struct
        import math
        if len(message) < 20 or message[:4] != b"FRM1":
            return
        seq, ts_ms, size = struct.unpack(">IdI", message[4:20])
        if (not math.isfinite(ts_ms) or ts_ms < 0 or size == 0 or
                size > self.settings.cv.max_frame_bytes or len(message) != 20 + size):
            return
        async with self._claim_lock:
            if self._owner is not None and self._owner is not websocket:
                await websocket.send(json.dumps({"type": "error", "message": "Camera source already connected"}))
                return
            if self._owner is None:
                await self.manager.wait_idle()
                await self.manager.close()
                for provider in self.providers:
                    reset = getattr(provider, "reset", None)
                    if reset:
                        await reset()
                self.manager = WindowManager.from_settings(*self.providers, self.settings)
                self._latest_objects = self._latest_points = None
                self._recent_gestures.clear()
                self._clock_origin = self._source_origin = None
                self._owner = websocket
                self._source_id = str(uuid.uuid4())
        image = await asyncio.to_thread(self._decode, message[20:])
        if image is None:
            return
        timestamp = ts_ms / 1000.
        if self._clock_origin is None:
            self._clock_origin = asyncio.get_running_loop().time()
            self._source_origin = timestamp
        try:
            self.manager.submit(Frame(seq, timestamp, image))
        except ValueError as exc:
            logger.debug("Ignoring invalid camera frame: %s", exc)

    def _decode(self, jpeg):
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[0] * image.shape[1] > self.settings.cv.max_image_pixels:
            return None
        return image

    async def _tick(self):
        from dataclasses import asdict
        while True:
            await asyncio.sleep(.02)
            if self._clock_origin is None:
                continue
            now = asyncio.get_running_loop().time()
            self.manager.advance(max(self.manager._watermark,
                                     self._source_origin + now - self._clock_origin))
            prune = getattr(self.providers[1], "prune", None)
            if prune:
                prune(set(self.manager._seen_people))
            messages = []
            for record in self.manager.drain_updates():
                if "objects" in record.completed:
                    if self._latest_objects is None or record.frame_id >= self._latest_objects.frame_id:
                        self._latest_objects = record
                if "keypoints" in record.completed:
                    self._remember_gestures(record)
                    if self._latest_points is None or record.frame_id >= self._latest_points.frame_id:
                        self._latest_points = record
                messages.append(self.frame_metadata(record))
            messages.extend(asdict(event) for event in self.manager.drain_events())
            if messages:
                for message in messages:
                    trace_record("cv", message.get("event_type", message.get("type", "update")),
                                 source_id=self._source_id, data=message)
                if self._outgoing.full():
                    self._outgoing.get_nowait()
                self._outgoing.put_nowait(messages)

    async def _publish_outgoing(self):
        while True:
            messages = await self._outgoing.get()
            for message in messages:
                await self.broadcast_metadata(message)

    def frame_metadata(self, record):
        from dataclasses import asdict
        identities = self.manager._identities
        lips = {point.track_id: point for point in record.keypoints if point.schema == "mediapipe_lips"}
        people = []
        for obj in record.objects:
            if obj.label != "person":
                continue
            identity = identities.get(obj.track_id)
            mouth = lips.get(obj.track_id)
            people.append({
                "track_id": obj.track_id, "bbox": obj.bbox,
                "generation": self.manager._track_generations.get(obj.track_id),
                "person_id": identity.person_id if identity else None,
                "display_name": identity.display_name if identity else "Unknown",
                "confidence": obj.confidence,
                "actions": tuple(sorted({gesture for point in record.keypoints if point.track_id == obj.track_id
                                         for gesture in point.gestures})),
                "mouth_openness": mouth.mouth_openness if mouth else None,
                "mouth_motion": mouth.mouth_motion if mouth else None,
            })
        return {
            "type": "frame" if "objects" in record.completed else "keypoints",
            "source_id": self._source_id,
            "seq": record.frame_id, "timestamp": record.timestamp,
            "width": record.width, "height": record.height, "people": people,
            "objects": [asdict(obj) for obj in record.objects if obj.label != "person"],
            "keypoints": [asdict(point) for point in record.keypoints],
            "completed": record.completed,
            "errors": record.errors,
            "stage_ms": record.stage_ms,
        }

    def get_current_scene(self):
        import time
        history = self.manager.visual_history(
            getattr(self.settings, "cv_world_history_seconds", 10.),
            getattr(self.settings, "cv_world_history_windows", 0))
        record = self._latest_objects
        if record is None or self.manager._watermark - record.timestamp > 1.:
            return {"people": [], "objects": [], "active_speaker": None, "windows": self._summaries(),
                    "frame_available": False, "source_id": self._source_id,
                    "recent_visual_history": history}
        scene = self.frame_metadata(record)
        self._prune_gestures()
        for person in scene["people"]:
            person["actions"] = tuple(sorted(
                gesture for (track_id, generation, gesture) in self._recent_gestures
                if track_id == person["track_id"] and generation == person["generation"]))
            person["mouth_openness"] = person["mouth_motion"] = None
        if (self._latest_points and abs(self._latest_points.timestamp - record.timestamp) <= .3
                and self.manager._watermark - self._latest_points.timestamp <= .3):
            # Association only: every point retains its own capture provenance.
            for person in scene["people"]:
                mouth = next((point for point in self._latest_points.keypoints
                              if point.track_id == person["track_id"] and point.schema == "mediapipe_lips"), None)
                person["mouth_openness"] = mouth.mouth_openness if mouth else None
                person["mouth_motion"] = mouth.mouth_motion if mouth else None
        scene.update(active_speaker=None, windows=self._summaries(), updated_at=time.time(),
                     frame_available=True, source_id=self._source_id,
                     recent_visual_history=history,
                     gesture_window_seconds=self.manager._watermark - self._gesture_cutoff())
        return scene

    def _remember_gestures(self, record):
        for point in record.keypoints:
            generation = self.manager._track_generations.get(point.track_id)
            for gesture in point.gestures:
                key = (point.track_id, generation, gesture)
                self._recent_gestures[key] = max(
                    record.timestamp, self._recent_gestures.get(key, record.timestamp))
        self._prune_gestures()

    def _prune_gestures(self):
        cutoff = self._gesture_cutoff()
        self._recent_gestures = {key: timestamp for key, timestamp in self._recent_gestures.items()
                                 if timestamp >= cutoff}

    def _gesture_cutoff(self):
        return self.manager.visual_history_cutoff(
            getattr(self.settings, "cv_world_history_seconds", 10.),
            getattr(self.settings, "cv_world_history_windows", 0))

    def _summaries(self):
        from dataclasses import asdict
        return [asdict(summary) for summary in self.manager.summaries()]

    def get_visible_people(self):
        return self.get_current_scene()["people"]

    async def _handle_message(self, websocket, data):
        from dataclasses import replace
        kind = data.get("type")
        request_id = data.get("request_id")
        if kind in ("get_scene", "get_windows", "get_people", "detect_objects"):
            if kind == "get_windows":
                payload, response = self._summaries(), "windows"
            elif kind == "get_people":
                payload, response = self.get_visible_people(), "people"
            elif kind == "detect_objects":
                payload, response = self.get_current_scene()["objects"], "objects"
            else:
                payload, response = self.get_current_scene(), "scene"
            await websocket.send(json.dumps({"type": response, "request_id": request_id, "data": payload}))
        elif kind == "rename_person":
            track_id, name = data.get("track_id"), data.get("name")
            person = next((item for item in self.get_visible_people() if item["track_id"] == track_id), None)
            handler = getattr(self.providers[1], "rename_track", None)
            if (type(track_id) is not int or not isinstance(name, str) or not name.strip() or len(name) > 80
                    or any(ord(char) < 32 for char in name) or person is None or handler is None
                    or data.get("source_id") != self._source_id
                    or data.get("generation") != person.get("generation")):
                await websocket.send(json.dumps({"type": "error", "request_id": request_id,
                                                 "message": "Rename target is invalid or no longer current."}))
                return
            event, status = await handler(track_id, name.strip())
            if event:
                identity = self.manager._identities.get(track_id)
                if identity:
                    self.manager._identities[track_id] = replace(
                        identity, person_id=event.person_id, display_name=event.display_name)
                await self.emit_event(event)
            await websocket.send(json.dumps({"type": "rename_result", "request_id": request_id,
                                             "data": {"ok": status in {"renamed", "no_change"},
                                                      "status": status, "track_id": track_id,
                                                      "name": name.strip()}}))
        elif kind == "transcript":
            handler = getattr(self.providers[1], "handle_transcript", None)
            text = data.get("text", "")
            if not isinstance(text, str):
                return
            events = await handler(text, self.get_visible_people()) if handler else []
            for event in events:
                if event.track_id in self.manager._identities:
                    identity = self.manager._identities[event.track_id]
                    self.manager._identities[event.track_id] = replace(
                        identity, person_id=event.person_id, display_name=event.display_name,
                    )
                await self.emit_event(event)
            await websocket.send(json.dumps({"type": "transcript_ack", "events_emitted": len(events),
                                             "request_id": request_id}))
        else:
            await websocket.send(json.dumps({"type": "error", "message": "Unknown CV message type"}))
