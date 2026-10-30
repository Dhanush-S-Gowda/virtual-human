"""Track lifecycle management.

Wraps the raw detection-with-tracking output and manages per-track state:
stable enter/leave events, timeouts, and identity mappings.  Track IDs
from ByteTrack are **temporary** — this module bridges them to the
persistent identity layer.
"""

from __future__ import annotations

import logging
import time

from cv_assistant import events
from cv_assistant.config import settings
from cv_assistant.state import SceneState, TrackedPerson

logger = logging.getLogger(__name__)


class TrackManager:
    """Manage track lifecycle and emit enter/leave events.

    Parameters
    ----------
    scene : SceneState
        Shared scene state container.
    enter_min_frames : int
        How many consecutive frames before a track is considered stable.
    leave_timeout : float
        Seconds without detections before emitting person_left.
    camera_id : str
        Camera identifier for events.
    """

    def __init__(
        self,
        scene: SceneState,
        enter_min_frames: int | None = None,
        leave_timeout: float | None = None,
        leave_timeout_sec: float | None = None,
        camera_id: str = "default",
    ) -> None:
        self._scene = scene
        self._enter_min_frames = enter_min_frames or settings.tracking.enter_min_frames
        self._leave_timeout = leave_timeout or leave_timeout_sec or settings.tracking.leave_timeout_sec
        self._camera_id = camera_id
        self._pending_events: list[events.CVEvent] = []

    def update(self, detected_track_ids: set[int]) -> list[events.CVEvent]:
        """Update tracks from the latest set of detected track IDs.

        Call this once per processed frame with the set of track IDs that
        were detected.  Returns a list of events to emit.
        """
        self._pending_events.clear()
        now = time.monotonic()

        # Update seen tracks
        for tid in detected_track_ids:
            track = self._scene.get_track(tid)
            if track is None:
                # New track — not yet stable
                self._scene.update_track(tid)
                logger.debug("New track %d", tid)
            else:
                self._scene.update_track(tid)
                # Check if it just became stable
                if not track.stable and track.frames_seen >= self._enter_min_frames:
                    track.stable = True
                    self._pending_events.append(
                        events.person_entered(
                            track_id=tid,
                            camera_id=self._camera_id,
                            person_id=track.person_id,
                            display_name=track.display_name,
                        )
                    )
                    logger.info(
                        "Person entered: track_id=%d person_id=%s",
                        tid, track.person_id,
                    )

        # Check for timed-out tracks
        all_tracks = self._scene.get_all_tracks()
        for track in all_tracks:
            if track.track_id in detected_track_ids:
                continue
            elapsed = now - track.last_seen
            if elapsed >= self._leave_timeout:
                removed = self._scene.remove_track(track.track_id)
                if removed and removed.stable:
                    self._pending_events.append(
                        events.person_left(
                            track_id=removed.track_id,
                            camera_id=self._camera_id,
                            person_id=removed.person_id,
                            display_name=removed.display_name,
                        )
                    )
                    logger.info(
                        "Person left: track_id=%d person_id=%s (%.1fs timeout)",
                        removed.track_id, removed.person_id, elapsed,
                    )

        return list(self._pending_events)

    def map_identity(
        self,
        track_id: int,
        person_id: str,
        display_name: str,
        confidence: float,
    ) -> events.CVEvent | None:
        """Associate a persistent identity with a track.

        Returns a ``person_identified`` event if the mapping is new, or
        ``None`` if the track already has this identity.
        """
        track = self._scene.get_track(track_id)
        if track is None:
            return None
        if track.person_id == person_id:
            # Already mapped — just update confidence
            track.identity_confidence = confidence
            return None

        track.person_id = person_id
        track.display_name = display_name
        track.identity_confidence = confidence
        ev = events.person_identified(
            track_id=track_id,
            person_id=person_id,
            display_name=display_name,
            confidence=confidence,
            camera_id=self._camera_id,
        )
        logger.info(
            "Person identified: track_id=%d -> person_id=%s (%s) conf=%.2f",
            track_id, person_id, display_name, confidence,
        )
        return ev
