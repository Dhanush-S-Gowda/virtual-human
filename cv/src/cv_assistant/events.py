"""Typed, versioned CV event definitions.

Every event emitted by the CV subsystem uses one of these dataclasses.
Events are JSON-serializable and include schema version, timestamp, and
camera ID for traceability.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CVEvent:
    """Base event emitted by the CV subsystem."""

    event_type: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: int = SCHEMA_VERSION
    camera_id: str = "default"
    track_id: int | None = None
    person_id: str | None = None
    display_name: str | None = None
    confidence: float | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for JSON serialization."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CVEvent:
        """Reconstruct an event from a dict."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Convenience constructors ────────────────────────────────────────────────

def person_entered(
    track_id: int,
    camera_id: str = "default",
    person_id: str | None = None,
    display_name: str | None = None,
    confidence: float | None = None,
) -> CVEvent:
    return CVEvent(
        event_type="person_entered",
        camera_id=camera_id,
        track_id=track_id,
        person_id=person_id,
        display_name=display_name,
        confidence=confidence,
    )


def person_left(
    track_id: int,
    camera_id: str = "default",
    person_id: str | None = None,
    display_name: str | None = None,
) -> CVEvent:
    return CVEvent(
        event_type="person_left",
        camera_id=camera_id,
        track_id=track_id,
        person_id=person_id,
        display_name=display_name,
    )


def person_identified(
    track_id: int,
    person_id: str,
    display_name: str,
    confidence: float,
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="person_identified",
        camera_id=camera_id,
        track_id=track_id,
        person_id=person_id,
        display_name=display_name,
        confidence=confidence,
    )


def person_renamed(
    person_id: str,
    old_name: str,
    new_name: str,
    track_id: int | None = None,
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="person_renamed",
        camera_id=camera_id,
        track_id=track_id,
        person_id=person_id,
        display_name=new_name,
        data={"old_name": old_name, "new_name": new_name},
    )


def action_detected(
    track_id: int,
    action: str,
    confidence: float | None = None,
    person_id: str | None = None,
    display_name: str | None = None,
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="action_detected",
        camera_id=camera_id,
        track_id=track_id,
        person_id=person_id,
        display_name=display_name,
        confidence=confidence,
        data={"action": action},
    )


def action_ended(
    track_id: int,
    action: str,
    person_id: str | None = None,
    display_name: str | None = None,
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="action_ended",
        camera_id=camera_id,
        track_id=track_id,
        person_id=person_id,
        display_name=display_name,
        data={"action": action},
    )


def objects_detected(
    objects: list[dict[str, Any]],
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="objects_detected",
        camera_id=camera_id,
        data={"objects": objects},
    )


def speaker_association_ambiguous(
    transcript: str,
    candidates: list[dict[str, Any]],
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="speaker_association_ambiguous",
        camera_id=camera_id,
        data={"transcript": transcript, "candidates": candidates},
    )


def camera_status_changed(
    status: str,
    detail: str = "",
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="camera_status_changed",
        camera_id=camera_id,
        data={"status": status, "detail": detail},
    )


def cv_error(
    message: str,
    stage: str = "",
    component: str = "",
    camera_id: str = "default",
) -> CVEvent:
    return CVEvent(
        event_type="cv_error",
        camera_id=camera_id,
        data={"message": message, "stage": stage or component, "component": component or stage},
    )
