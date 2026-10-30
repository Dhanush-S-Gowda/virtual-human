"""End-to-end lifecycle integration test covering the entire CV flow:

1. Person enters -> new temporary track_id (e.g. 4)
2. Face observation -> auto-enrolled with consent as "Unknown"
3. Spoken transcript "My name is Bablu" -> speaker associated, record renamed to "Bablu"
4. Person leaves -> track removed
5. Person returns with new temporary track_id (e.g. 12)
6. Face observation -> matched to stored embedding -> re-identified as "Bablu"
7. Person waves -> action detected event emitted
"""

import tempfile
from pathlib import Path
import numpy as np
import pytest

from cv_assistant import events
from cv_assistant.actions.heuristics import ActionClassifier
from cv_assistant.actions.keypoint_buffer import KeypointBuffer
from cv_assistant.faces.embeddings import normalise_embedding
from cv_assistant.faces.matcher import FaceMatcher
from cv_assistant.identity.service import IdentityService
from cv_assistant.identity.store import IdentityStore
from cv_assistant.speaker.association import SpeakerAssociation
from cv_assistant.state import SceneState
from cv_assistant.tracking import TrackManager


@pytest.fixture
def e2e_system():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "e2e_identities.db"
        store = IdentityStore(db_path=db_path)
        store.open()
        try:
            matcher = FaceMatcher(threshold=0.5, ambiguity_margin=0.05)
            scene = SceneState()
            service = IdentityService(store=store, matcher=matcher, scene=scene)
            speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)
            track_manager = TrackManager(scene=scene, enter_min_frames=2, leave_timeout_sec=0.1)
            action_classifier = ActionClassifier()

            yield {
                "store": store,
                "matcher": matcher,
                "scene": scene,
                "service": service,
                "speaker_assoc": speaker_assoc,
                "track_manager": track_manager,
                "action_classifier": action_classifier,
            }
        finally:
            store.close()


def test_full_cv_lifecycle(e2e_system):
    store = e2e_system["store"]
    scene = e2e_system["scene"]
    service = e2e_system["service"]
    speaker_assoc = e2e_system["speaker_assoc"]
    track_manager = e2e_system["track_manager"]
    action_classifier = e2e_system["action_classifier"]

    # Generate a unique ground-truth face embedding for Bablu
    rng = np.random.RandomState(42)
    bablu_face_emb = normalise_embedding(rng.randn(512).astype(np.float32))

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: Person enters frame with temporary track_id = 4
    # ─────────────────────────────────────────────────────────────────────────
    scene.update_track(track_id=4, bbox=(100, 100, 300, 400), confidence=0.9)
    # First call brings frames_seen to 2 which satisfies enter_min_frames=2
    evs1 = track_manager.update(detected_track_ids={4})
    entered_events = [e for e in evs1 if e.event_type == "person_entered"]
    assert len(entered_events) == 1
    assert entered_events[0].track_id == 4

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: Face observation occurs -> enroll with consent
    # ─────────────────────────────────────────────────────────────────────────
    face_obs_1 = {
        "score": 0.95,
        "bbox": (140, 110, 260, 250),
        "embedding": bablu_face_emb,
    }
    # Auto-enroll with granted consent
    service.auto_enroll = True
    scene.get_track(4).consent_status = "granted"
    match_res_1, face_evs_1 = service.process_face_observation(
        track_id=4,
        face=face_obs_1,
    )
    assert match_res_1.outcome == "enrolled"
    assigned_person_id = match_res_1.person_id
    assert assigned_person_id is not None
    assert store.get_person(assigned_person_id)["display_name"] == "Unknown"

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: Person says "My name is Bablu"
    # ─────────────────────────────────────────────────────────────────────────
    rename_evs = speaker_assoc.handle_transcript("Hello, my name is Bablu!")
    assert len(rename_evs) == 1
    assert rename_evs[0].event_type == "person_renamed"
    assert rename_evs[0].person_id == assigned_person_id
    assert rename_evs[0].display_name == "Bablu"

    # Verify database display_name is updated, but person_id is preserved
    record = store.get_person(assigned_person_id)
    assert record["person_id"] == assigned_person_id
    assert record["display_name"] == "Bablu"

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4: Person leaves frame (no detections for track 4)
    # ─────────────────────────────────────────────────────────────────────────
    import time
    time.sleep(0.15)  # Exceed leave_timeout_sec
    leave_evs = track_manager.update(detected_track_ids=set())
    left_events = [e for e in leave_evs if e.event_type == "person_left"]
    assert len(left_events) == 1
    assert left_events[0].track_id == 4
    assert scene.get_track(4) is None

    # ─────────────────────────────────────────────────────────────────────────
    # Step 5 & 6: Person returns later with NEW temporary track_id = 12
    # ─────────────────────────────────────────────────────────────────────────
    scene.update_track(track_id=12, bbox=(120, 100, 320, 400), confidence=0.92)
    track_manager.update(detected_track_ids={12})
    track_manager.update(detected_track_ids={12})
    assert scene.get_track(12).stable is True

    # Slightly perturbed observation of the same face (e.g. slight lighting change)
    noise = rng.randn(512).astype(np.float32) * 0.05
    query_face_emb = normalise_embedding(bablu_face_emb + noise)

    face_obs_2 = {
        "score": 0.93,
        "bbox": (150, 110, 270, 250),
        "embedding": query_face_emb,
    }
    match_res_2, face_evs_2 = service.process_face_observation(
        track_id=12,
        face=face_obs_2,
    )

    # Re-identification succeeds!
    assert match_res_2.outcome == "match"
    assert match_res_2.person_id == assigned_person_id
    assert match_res_2.display_name == "Bablu"

    # Verify track 12 in scene is now associated with Bablu
    updated_track = scene.get_track(12)
    assert updated_track.person_id == assigned_person_id
    assert updated_track.display_name == "Bablu"

    # ─────────────────────────────────────────────────────────────────────────
    # Step 7: Bablu waves at the camera -> action detected
    # ─────────────────────────────────────────────────────────────────────────
    buf = KeypointBuffer(max_size=30)
    bbox = (120.0, 100.0, 320.0, 400.0)
    xs = [150, 250, 150, 250, 150, 250, 150, 250, 150, 250]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = np.zeros((17, 3), dtype=np.float32)
        kps[:, 2] = 0.95
        kps[0] = [200, 130, 0.95]  # Nose
        kps[6] = [170, 190, 0.95]  # Right shoulder
        kps[10] = [x, 140, 0.95]   # Right wrist oscillating above shoulder
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    actions = action_classifier.classify(track_id=12, buffer=buf)
    assert any(a.action == "waving" and a.detected for a in actions)

    # Check that scene snapshot now shows Bablu with waving action
    updated_track.actions = ["waving"]
    scene_summary = scene.get_current_scene()
    people = scene_summary["people"]
    assert len(people) == 1
    assert people[0]["display_name"] == "Bablu"
    assert people[0]["track_id"] == 12
    assert "waving" in people[0]["actions"]


@pytest.mark.parametrize("face_before_name", [True, False])
def test_name_registration_persists_face_for_reentry(e2e_system, face_before_name):
    store = e2e_system["store"]
    scene = e2e_system["scene"]
    service = e2e_system["service"]
    speaker_assoc = e2e_system["speaker_assoc"]

    rng = np.random.RandomState(7)
    face_embedding = normalise_embedding(rng.randn(512).astype(np.float32))
    face_observation = {
        "score": 0.95,
        "bbox": (140, 110, 260, 250),
        "embedding": face_embedding,
    }

    service.auto_enroll = False
    scene.update_track(
        track_id=21,
        bbox=(100, 100, 300, 400),
        stable=True,
        consent_status="granted",
    )

    if face_before_name:
        result, _ = service.process_face_observation(21, face_observation)
        assert result is not None
        assert result.outcome == "unknown"

    rename_events = speaker_assoc.handle_transcript("My name is Alice")
    assert len(rename_events) == 1
    person_id = rename_events[0].person_id
    assert person_id is not None

    if not face_before_name:
        result, _ = service.process_face_observation(21, face_observation)
        assert result is not None
        assert result.outcome == "match"

    assert store.get_embedding_count(person_id) == 1

    scene.remove_track(21)
    scene.update_track(track_id=34, bbox=(120, 100, 320, 400), stable=True)
    returning_result, _ = service.process_face_observation(34, face_observation)

    assert returning_result is not None
    assert returning_result.outcome == "match"
    assert returning_result.person_id == person_id
    assert returning_result.display_name == "Alice"
