"""Unit tests for SceneState manager."""

import time
import pytest

from cv_assistant.state import SceneState, DetectedObject


def test_track_lifecycle_in_scene():
    scene = SceneState()

    # Track initially not stable
    t1 = scene.update_track(track_id=1, bbox=(0, 0, 10, 10), confidence=0.8, stable=False)
    assert t1.track_id == 1
    assert t1.frames_seen == 1
    assert len(scene.get_stable_tracks()) == 0

    # Update track to stable
    scene.update_track(track_id=1, stable=True, person_id="pid-1", display_name="Alice")
    assert len(scene.get_stable_tracks()) == 1
    assert len(scene.get_tracks_with_identity()) == 1

    found = scene.find_track_by_person_id("pid-1")
    assert found is not None
    assert found.track_id == 1

    # Remove track
    removed = scene.remove_track(1)
    assert removed is not None
    assert scene.get_track(1) is None
    assert len(scene.get_all_tracks()) == 0


def test_object_management():
    scene = SceneState()
    objs = [
        DetectedObject(label="cup", confidence=0.91, bbox=(10, 10, 20, 20)),
        DetectedObject(label="laptop", confidence=0.85, bbox=(50, 50, 100, 100)),
    ]
    scene.update_objects(objs)

    snapshot = scene.get_current_scene()
    assert len(snapshot["objects"]) == 2
    assert snapshot["objects"][0]["label"] == "cup"
    assert len(scene.get_objects()) == 2
    assert scene.get_objects()[0].label == "cup"


def test_cooldown_mechanism():
    scene = SceneState()
    key = "track-1:waving"

    # First check passes
    assert scene.check_cooldown(key, cooldown_sec=1.0) is True

    # Immediate second check fails
    assert scene.check_cooldown(key, cooldown_sec=1.0) is False

    # Different key passes
    assert scene.check_cooldown("track-2:waving", cooldown_sec=1.0) is True


def test_scene_snapshot_structure():
    scene = SceneState()
    scene.update_track(
        track_id=10,
        person_id="p-10",
        display_name="Bob",
        stable=True,
        identity_confidence=0.92,
        actions=["waving"],
    )

    snapshot = scene.get_current_scene()
    assert "people" in snapshot
    assert "objects" in snapshot
    assert "updated_at" in snapshot

    assert len(snapshot["people"]) == 1
    p = snapshot["people"][0]
    assert p["person_id"] == "p-10"
    assert p["display_name"] == "Bob"
    assert p["track_id"] == 10
    assert p["actions"] == ["waving"]
    assert p["identity_confidence"] == 0.92

    visible = scene.get_visible_people()
    assert len(visible) == 1
    assert visible[0]["display_name"] == "Bob"
