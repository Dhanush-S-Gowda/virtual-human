"""Unit and integration tests for STT name binding and speaker association."""

import tempfile
from pathlib import Path
import pytest

from cv_assistant.speaker.association import parse_name_intent, SpeakerAssociation
from cv_assistant.identity.service import IdentityService
from cv_assistant.identity.store import IdentityStore
from cv_assistant.faces.matcher import FaceMatcher
from cv_assistant.state import SceneState


@pytest.fixture
def identity_env():
    """Setup a temporary store, matcher, scene, and identity service."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_identities.db"
        store = IdentityStore(db_path=db_path)
        store.open()
        matcher = FaceMatcher()
        scene = SceneState()
        service = IdentityService(store=store, matcher=matcher, scene=scene)
        yield store, matcher, scene, service
        store.close()


def test_parse_name_intent_positive():
    cases = [
        ("My name is Bablu", "Bablu"),
        ("Hello, my name is Alice!", "Alice"),
        ("I am Bob", "Bob"),
        ("I'm Charlie", "Charlie"),
        ("Please call me David", "David"),
        ("They call me Eve", "Eve"),
    ]
    for text, expected_name in cases:
        res = parse_name_intent(text)
        assert res.detected is True, f"Failed on '{text}'"
        assert res.name == expected_name, f"Expected {expected_name}, got {res.name}"


def test_parse_name_intent_negative():
    cases = [
        "What is the weather today?",
        "Can you help me with this?",
        "Hello there!",
        "Nice to meet you",
        "My car is blue",
    ]
    for text in cases:
        res = parse_name_intent(text)
        assert res.detected is False, f"False positive on '{text}'"


def test_speaker_association_single_person(identity_env):
    store, matcher, scene, service = identity_env

    # 1. Create a person in DB and add a stable track in scene
    pid = store.create_person(display_name="Unknown")
    scene.update_track(
        track_id=1,
        bbox=(10, 10, 100, 200),
        confidence=0.9,
        stable=True,
        person_id=pid,
        display_name="Unknown",
    )

    speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)

    # 2. Process introduction
    events = speaker_assoc.handle_transcript("My name is Bablu")
    assert len(events) == 1
    rename_event = events[0]
    assert rename_event.event_type == "person_renamed"
    assert rename_event.person_id == pid
    assert rename_event.display_name == "Bablu"

    # 3. Check DB and scene were updated while person_id remained unchanged
    person = store.get_person(pid)
    assert person["display_name"] == "Bablu"
    assert person["person_id"] == pid

    updated_track = scene.get_track(1)
    assert updated_track.display_name == "Bablu"


def test_speaker_association_ambiguous(identity_env):
    store, matcher, scene, service = identity_env

    # 2 stable people in scene
    pid1 = store.create_person(display_name="Unknown")
    pid2 = store.create_person(display_name="Unknown")

    scene.update_track(track_id=1, bbox=(0, 0, 50, 50), stable=True, person_id=pid1)
    scene.update_track(track_id=2, bbox=(100, 0, 150, 50), stable=True, person_id=pid2)

    speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)

    events = speaker_assoc.handle_transcript("My name is Bablu")
    assert len(events) == 1
    ambig_event = events[0]
    assert ambig_event.event_type == "speaker_association_ambiguous"
    assert "Bablu" in ambig_event.data["transcript"]

    # Verify NEITHER person was renamed
    assert store.get_person(pid1)["display_name"] == "Unknown"
    assert store.get_person(pid2)["display_name"] == "Unknown"


def test_speaker_association_no_people(identity_env):
    store, matcher, scene, service = identity_env
    speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)

    events = speaker_assoc.handle_transcript("My name is Bablu")
    assert len(events) == 0


def test_speaker_association_unenrolled_track(identity_env):
    store, matcher, scene, service = identity_env

    # Single stable track without person_id yet
    scene.update_track(
        track_id=5,
        bbox=(50, 50, 150, 250),
        confidence=0.9,
        stable=True,
        person_id=None,
        display_name="Unknown",
    )

    speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)
    events = speaker_assoc.handle_transcript("My name is Alice")
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "person_renamed"
    assert ev.display_name == "Alice"

    # Verify track has been assigned person_id and display_name
    track = scene.get_track(5)
    assert track.person_id is not None
    assert track.display_name == "Alice"

    # Verify in DB
    person = store.get_person(track.person_id)
    assert person is not None
    assert person["display_name"] == "Alice"
    assert person["consent_status"] == "granted"


def test_speaker_association_off_camera_speaker_passive_listener(identity_env):
    """When a single visible person is silent/passive and an off-camera speaker speaks."""
    store, matcher, scene, service = identity_env

    pid = store.create_person(display_name="Unknown")
    track = scene.update_track(
        track_id=10,
        bbox=(20, 20, 120, 220),
        confidence=0.9,
        stable=True,
        person_id=pid,
        display_name="Unknown",
    )

    # Populate mouth history with stationary / zero-motion values (still mouth)
    track.mouth_history = [
        (1.0, 0.001),
        (1.1, 0.001),
        (1.2, 0.001),
        (1.3, 0.001),
        (1.4, 0.001),
    ]

    speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)
    events = speaker_assoc.handle_transcript("My name is OffCameraUser")

    assert len(events) == 1
    assert events[0].event_type == "speaker_association_ambiguous"

    # Ensure passive visible person was NOT renamed
    assert store.get_person(pid)["display_name"] == "Unknown"
    assert scene.get_track(10).display_name == "Unknown"


def test_speaker_association_verified_mouth_motion(identity_env):
    """When active mouth motion confirms the visible person is speaking."""
    store, matcher, scene, service = identity_env

    pid = store.create_person(display_name="Unknown")
    track = scene.update_track(
        track_id=11,
        bbox=(20, 20, 120, 220),
        confidence=0.9,
        stable=True,
        person_id=pid,
        display_name="Unknown",
    )

    # Active fluctuating mouth motion indicating speech
    track.mouth_history = [
        (1.0, 0.01),
        (1.1, 0.15),
        (1.2, 0.02),
        (1.3, 0.18),
        (1.4, 0.03),
        (1.5, 0.14),
    ]

    speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)
    events = speaker_assoc.handle_transcript("My name is Vikram")

    assert len(events) == 1
    assert events[0].event_type == "person_renamed"
    assert events[0].display_name == "Vikram"
    assert store.get_person(pid)["display_name"] == "Vikram"

