"""Unit tests for typed CV event definitions and serialization."""

import json
from datetime import datetime
import pytest

from cv_assistant import events


def test_event_serialization_and_fields():
    ev = events.person_entered(
        track_id=12,
        camera_id="cam_main",
        person_id="p-uuid-1234",
        display_name="Bablu",
        confidence=0.95,
    )

    assert ev.schema_version == 1
    assert ev.event_type == "person_entered"
    assert ev.camera_id == "cam_main"
    assert ev.track_id == 12
    assert ev.person_id == "p-uuid-1234"
    assert ev.display_name == "Bablu"
    assert ev.confidence == 0.95

    # Check timestamp is ISO-8601
    dt = datetime.fromisoformat(ev.timestamp)
    assert dt is not None

    # Serialization
    d = ev.to_dict()
    assert isinstance(d, dict)
    assert d["event_type"] == "person_entered"

    json_str = ev.to_json()
    parsed = json.loads(json_str)
    assert parsed["track_id"] == 12
    assert parsed["schema_version"] == 1

    # Reconstruction
    reconstructed = events.CVEvent.from_dict(parsed)
    assert reconstructed.event_type == ev.event_type
    assert reconstructed.track_id == ev.track_id
    assert reconstructed.person_id == ev.person_id


def test_person_renamed_event():
    ev = events.person_renamed(
        person_id="pid-101",
        old_name="Unknown",
        new_name="Alice",
        camera_id="default",
    )
    assert ev.event_type == "person_renamed"
    assert ev.display_name == "Alice"
    assert ev.data["old_name"] == "Unknown"
    assert ev.data["new_name"] == "Alice"


def test_action_detected_event():
    ev = events.action_detected(
        track_id=3,
        action="waving",
        confidence=0.88,
        person_id="pid-101",
        display_name="Alice",
    )
    assert ev.event_type == "action_detected"
    assert ev.data["action"] == "waving"
    assert ev.confidence == 0.88


def test_action_ended_event():
    ev = events.action_ended(
        track_id=3,
        action="waving",
        person_id="pid-101",
        display_name="Alice",
    )
    assert ev.event_type == "action_ended"
    assert ev.data["action"] == "waving"
    assert ev.track_id == 3
    assert ev.person_id == "pid-101"
    assert ev.display_name == "Alice"


def test_objects_detected_event():
    objs = [{"label": "cup", "confidence": 0.92, "bbox": [10, 20, 30, 40]}]
    ev = events.objects_detected(objects=objs)
    assert ev.event_type == "objects_detected"
    assert len(ev.data["objects"]) == 1


def test_speaker_association_ambiguous_event():
    cands = [{"track_id": 1, "person_id": "p1"}, {"track_id": 2, "person_id": "p2"}]
    ev = events.speaker_association_ambiguous(transcript="My name is Bob", candidates=cands)
    assert ev.event_type == "speaker_association_ambiguous"
    assert ev.data["transcript"] == "My name is Bob"
    assert len(ev.data["candidates"]) == 2


def test_cv_error_event():
    ev = events.cv_error("Camera disconnect", component="camera")
    assert ev.event_type == "cv_error"
    assert ev.data["message"] == "Camera disconnect"
    assert ev.data["component"] == "camera"
