"""Scene state manager.

Maintains the current view of the scene — visible people, their actions,
detected objects — in a compact, serializable form suitable for the assistant.
Implements debounce/cooldown to avoid event spam.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TrackedPerson:
    """Per-track runtime state for one visible person."""

    track_id: int
    person_id: str | None = None
    display_name: str | None = None
    identity_confidence: float = 0.0
    bbox: tuple[float, float, float, float] | None = None  # x1, y1, x2, y2
    keypoints: Any = None  # Latest keypoints array
    actions: list[str] = field(default_factory=list)
    last_action_time: float = 0.0
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    stable: bool = False  # True once enter_min_frames threshold is met
    frames_seen: int = 0
    last_face_query: float = 0.0
    last_face_quality: float = 0.0
    consent_status: str = "unknown"
    # Mouth aspect ratio history for speaker detection
    mouth_history: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class DetectedObject:
    """A general object detected in the scene."""

    label: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None


class SceneState:
    """Thread-safe container for current CV scene state.

    The assistant queries this to get a compact JSON snapshot of what the
    camera currently sees, without receiving raw frames.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tracks: dict[int, TrackedPerson] = {}
        self._objects: list[DetectedObject] = []
        self._updated_at: str = datetime.now(timezone.utc).isoformat()
        self._event_cooldowns: dict[str, float] = {}

    # ── Track management ────────────────────────────────────────────────

    def update_track(self, track_id: int, **kwargs: Any) -> TrackedPerson:
        """Create or update a tracked person entry."""
        with self._lock:
            if track_id not in self._tracks:
                self._tracks[track_id] = TrackedPerson(track_id=track_id)
            track = self._tracks[track_id]
            for key, value in kwargs.items():
                if hasattr(track, key):
                    setattr(track, key, value)
            track.last_seen = time.monotonic()
            track.frames_seen += 1
            self._updated_at = datetime.now(timezone.utc).isoformat()
            return track

    def get_track(self, track_id: int) -> TrackedPerson | None:
        with self._lock:
            return self._tracks.get(track_id)

    def remove_track(self, track_id: int) -> TrackedPerson | None:
        with self._lock:
            return self._tracks.pop(track_id, None)

    def get_all_tracks(self) -> list[TrackedPerson]:
        with self._lock:
            return list(self._tracks.values())

    def get_stable_tracks(self) -> list[TrackedPerson]:
        """Return only tracks that have been confirmed stable (entered)."""
        with self._lock:
            return [t for t in self._tracks.values() if t.stable]

    def get_tracks_with_identity(self) -> list[TrackedPerson]:
        """Return tracks that have a mapped persistent identity."""
        with self._lock:
            return [t for t in self._tracks.values() if t.person_id is not None]

    def find_track_by_person_id(self, person_id: str) -> TrackedPerson | None:
        """Find a track mapped to a specific persistent identity."""
        with self._lock:
            for track in self._tracks.values():
                if track.person_id == person_id:
                    return track
            return None

    # ── Object management ───────────────────────────────────────────────

    def update_objects(self, objects: list[DetectedObject]) -> None:
        with self._lock:
            self._objects = list(objects)
            self._updated_at = datetime.now(timezone.utc).isoformat()

    def get_objects(self) -> list[DetectedObject]:
        """Return a copy of the current detected objects list."""
        with self._lock:
            return list(self._objects)

    # ── Event cooldown ──────────────────────────────────────────────────

    def check_cooldown(self, event_key: str, cooldown_sec: float) -> bool:
        """Return True if the event is allowed (cooldown has elapsed)."""
        now = time.monotonic()
        with self._lock:
            last = self._event_cooldowns.get(event_key, 0.0)
            if now - last >= cooldown_sec:
                self._event_cooldowns[event_key] = now
                return True
            return False

    # ── Queries for the assistant ────────────────────────────────────────

    def get_current_scene(self) -> dict[str, Any]:
        """Compact JSON-safe snapshot of current scene state."""
        with self._lock:
            people = []
            now = time.monotonic()
            for t in self._tracks.values():
                if not t.stable:
                    continue
                people.append({
                    "person_id": t.person_id,
                    "display_name": t.display_name or "Unknown",
                    "track_id": t.track_id,
                    "actions": list(t.actions),
                    "last_seen_seconds_ago": round(now - t.last_seen, 2),
                    "identity_confidence": round(t.identity_confidence, 2),
                })
            objects = [
                {"label": o.label, "confidence": round(o.confidence, 2)}
                for o in self._objects
            ]

            # Resolve active speaker
            active_speaker = None
            stable_tracks = [t for t in self._tracks.values() if t.stable]
            if len(stable_tracks) == 1:
                st = stable_tracks[0]
                active_speaker = {
                    "track_id": st.track_id,
                    "person_id": st.person_id,
                    "display_name": st.display_name or "Unknown",
                    "confidence": 0.85,
                }
            elif len(stable_tracks) > 1:
                # Rank stable tracks by recent mouth motion / activity
                scored_tracks = []
                for st in stable_tracks:
                    m_score = 0.0
                    if len(st.mouth_history) >= 2:
                        vals = [v for _, v in st.mouth_history[-15:]]
                        import numpy as np
                        m_score = float(np.var(vals) * 2.0 + np.mean(vals) / 20.0)
                    scored_tracks.append((m_score, st))
                scored_tracks.sort(key=lambda item: item[0], reverse=True)
                top_score, top_track = scored_tracks[0]

                # If the top track has an unknown name, but an identified user is in frame,
                # prefer the identified user unless the unknown person's mouth motion is significantly higher
                if not top_track.display_name or top_track.display_name == "Unknown":
                    identified = [(score, st) for score, st in scored_tracks if st.display_name and st.display_name != "Unknown"]
                    if identified:
                        best_id_score, best_id_track = max(identified, key=lambda item: item[0])
                        if (top_score - best_id_score < 0.25) or (top_score < 0.15):
                            top_track = best_id_track
                            top_score = max(best_id_score, 0.75)

                active_speaker = {
                    "track_id": top_track.track_id,
                    "person_id": top_track.person_id,
                    "display_name": top_track.display_name or "Unknown",
                    "confidence": round(min(top_score, 1.0), 2) if top_score > 0 else 0.5,
                }

            return {
                "people": people,
                "objects": objects,
                "active_speaker": active_speaker,
                "updated_at": self._updated_at,
            }

    def get_visible_people(self) -> list[dict[str, Any]]:
        """Return only the people portion of the scene snapshot."""
        return self.get_current_scene()["people"]

    def clear(self) -> None:
        """Remove all state (for shutdown)."""
        with self._lock:
            self._tracks.clear()
            self._objects.clear()
            self._event_cooldowns.clear()
