"""Unit tests for action recognition heuristics (waving, hand raised)."""

import time
import numpy as np
import pytest

from cv_assistant.actions.heuristics import ActionClassifier
from cv_assistant.actions.keypoint_buffer import KeypointBuffer
from cv_assistant.detection import (
    KP_NOSE,
    KP_RIGHT_SHOULDER,
    KP_RIGHT_WRIST,
    KP_LEFT_SHOULDER,
    KP_LEFT_WRIST,
)


def _create_base_keypoints(wrist_x=150, wrist_y=100, shoulder_y=150, conf=0.9):
    """Create a 17x3 COCO keypoint array with default positions."""
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[:, 2] = conf  # default confidence

    # Nose at (150, 80)
    kps[KP_NOSE] = [150, 80, conf]
    # Right shoulder at (120, shoulder_y)
    kps[KP_RIGHT_SHOULDER] = [120, shoulder_y, conf]
    # Left shoulder at (180, shoulder_y)
    kps[KP_LEFT_SHOULDER] = [180, shoulder_y, conf]
    # Right wrist at (wrist_x, wrist_y)
    kps[KP_RIGHT_WRIST] = [wrist_x, wrist_y, conf]
    # Left wrist down at (180, 250)
    kps[KP_LEFT_WRIST] = [180, 250, conf]
    # Hips at y=250
    from cv_assistant.detection import KP_LEFT_HIP, KP_RIGHT_HIP
    kps[KP_LEFT_HIP] = [130, 250, conf]
    kps[KP_RIGHT_HIP] = [170, 250, conf]

    return kps


def test_synthetic_waving_detected():
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Generate oscillating wrist motion: side to side 6 times
    xs = [100, 180, 100, 180, 100, 180, 100, 180, 100, 180]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=80, shoulder_y=150, conf=0.9)
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=1, buffer=buf)
    waving = [r for r in results if r.action == "waving"]
    assert len(waving) == 1
    assert waving[0].detected is True
    assert waving[0].confidence >= 0.7


def test_waving_rejected_when_wrist_below_shoulder():
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Oscillating wrist, but wrist_y (200) > shoulder_y (150) -> wrist is BELOW shoulder
    xs = [100, 180, 100, 180, 100, 180, 100, 180]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=200, shoulder_y=150, conf=0.9)
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=1, buffer=buf)
    waving = [r for r in results if r.action == "waving"]
    assert len(waving) == 0


def test_waving_rejected_on_low_confidence():
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    xs = [100, 180, 100, 180, 100, 180, 100, 180]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=80, shoulder_y=150, conf=0.2)  # Low confidence
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=1, buffer=buf)
    waving = [r for r in results if r.action == "waving"]
    assert len(waving) == 0


def test_waving_cooldown():
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    xs = [100, 180, 100, 180, 100, 180, 100, 180, 100, 180]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=80, shoulder_y=150, conf=0.9)
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    # First call fires
    res1 = classifier.classify(track_id=1, buffer=buf)
    assert any(r.action == "waving" for r in res1)

    # Immediate second call should be suppressed by cooldown
    res2 = classifier.classify(track_id=1, buffer=buf)
    assert not any(r.action == "waving" for r in res2)


def test_synthetic_hand_raised():
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Nose at y=100 (normalised ~0.167). Wrist at y=60 (normalised ~0.033).
    # Wrist is well above head/nose for 10 frames
    now = time.monotonic()
    for i in range(10):
        kps = _create_base_keypoints(wrist_x=120, wrist_y=60, shoulder_y=150, conf=0.9)
        kps[KP_NOSE] = [150, 100, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=2, buffer=buf)
    raised = [r for r in results if r.action == "hand_raised"]
    assert len(raised) == 1
    assert raised[0].detected is True


def test_hand_raised_at_shoulder_level():
    """Verify natural hand raised at shoulder/chin level is recognized."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Wrist at y=140, shoulder at y=150 (wrist slightly above shoulder level)
    now = time.monotonic()
    for i in range(10):
        kps = _create_base_keypoints(wrist_x=120, wrist_y=140, shoulder_y=150, conf=0.85)
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=3, buffer=buf)
    raised = [r for r in results if r.action == "hand_raised"]
    assert len(raised) == 1
    assert raised[0].detected is True


def test_smooth_real_world_waving():
    """Verify smooth oscillating waving with small frame-to-frame diffs is detected."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Smooth sinusoidal-like wave: 100 -> 140 -> 100 -> 140
    xs = [100, 110, 125, 140, 130, 115, 100, 110, 125, 140, 125, 105]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=80, shoulder_y=150, conf=0.75)
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=4, buffer=buf)
    waving = [r for r in results if r.action == "waving"]
    assert len(waving) == 1
    assert waving[0].detected is True


def test_spurious_hand_detection_filtered():
    """Verify spurious isolated hand boxes are dropped and do not spawn ghost tracks."""
    from cv_assistant.pipeline import CVPipeline
    from cv_assistant.detection import PersonDetection

    pipeline = CVPipeline()
    # Person detection (full body with head and shoulders)
    person_kps = _create_base_keypoints(wrist_x=140, wrist_y=100, shoulder_y=150, conf=0.9)
    person_kps[KP_NOSE] = [150, 80, 0.95]
    person_det = PersonDetection(
        bbox=(50.0, 50.0, 250.0, 350.0),
        confidence=0.88,
        keypoints=person_kps,
        track_id=1,
    )

    # Spurious hand detection near the shoulder/arm with no head or torso anchor
    hand_kps = np.zeros((17, 3), dtype=np.float32)
    hand_kps[KP_RIGHT_WRIST] = [170, 90, 0.85]  # better wrist confidence
    hand_det = PersonDetection(
        bbox=(150.0, 70.0, 210.0, 130.0),
        confidence=0.45,
        keypoints=hand_kps,
        track_id=2,
    )

    filtered = pipeline._filter_spurious_detections([person_det, hand_det])
    # Must only keep 1 person
    assert len(filtered) == 1
    assert filtered[0].track_id == 1
    # Wrist should be merged into person
    assert filtered[0].keypoints[KP_RIGHT_WRIST, 2] >= 0.85


def test_synthetic_namaste_detected():
    """Verify Namaste folded hands is recognized when both wrists touch in front of chest."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    now = time.monotonic()
    for i in range(8):
        kps = _create_base_keypoints(conf=0.9)
        # Shoulders at y=150, hips at y=250
        # Both wrists brought together in center chest: x=150, y=180
        kps[KP_RIGHT_WRIST] = [148, 180, 0.9]
        kps[KP_LEFT_WRIST] = [152, 180, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=5, buffer=buf)
    namaste = [r for r in results if r.action == "namaste"]
    assert len(namaste) == 1
    assert namaste[0].detected is True


def test_synthetic_thumbs_up_detected():
    """Verify Thumbs Up is recognized when bent arm is held steady at chest level."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    now = time.monotonic()
    for i in range(8):
        kps = _create_base_keypoints(conf=0.9)
        # Shoulders at y=150
        # Right wrist held steady at chest level (y=170), elbow lower (y=210)
        from cv_assistant.detection import KP_RIGHT_ELBOW
        kps[KP_RIGHT_WRIST] = [140, 170, 0.9]
        kps[KP_RIGHT_ELBOW] = [120, 210, 0.85]
        # Left wrist down
        kps[KP_LEFT_WRIST] = [180, 280, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=6, buffer=buf)
    thumbs = [r for r in results if r.action == "thumbs_up"]
    assert len(thumbs) == 1
    assert thumbs[0].detected is True


def test_head_nod_disabled():
    """Verify head nod is disabled and not detected even with oscillating nose y-series."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Nose moves down and up: 80 -> 100 -> 80 -> 100 -> 80
    ys = [80, 85, 95, 100, 95, 85, 80, 85, 95, 100, 95, 85, 80]
    now = time.monotonic()
    for i, y in enumerate(ys):
        kps = _create_base_keypoints(wrist_x=120, wrist_y=250, conf=0.9)
        kps[KP_NOSE] = [150, y, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=7, buffer=buf)
    nod = [r for r in results if r.action == "head_nod" and r.detected]
    assert len(nod) == 0


def test_hand_raised_not_confused_with_thumbs_up():
    """Verify hand raised at ear/head level is detected as hand_raised, NEVER thumbs_up."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    now = time.monotonic()
    for i in range(8):
        # Wrist at y=70 (ear level, well above shoulder y=150)
        kps = _create_base_keypoints(wrist_x=120, wrist_y=70, shoulder_y=150, conf=0.9)
        kps[KP_NOSE] = [150, 80, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=8, buffer=buf)
    actions = [r.action for r in results if r.detected]
    assert "hand_raised" in actions
    assert "thumbs_up" not in actions


def test_thumbs_up_not_confused_with_hand_raised():
    """Verify thumbs up at chest level is detected as thumbs_up, NEVER hand_raised."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    from cv_assistant.detection import KP_RIGHT_ELBOW
    now = time.monotonic()
    for i in range(8):
        # Wrist at y=175 (chest level, below shoulder y=150), elbow lower at y=210
        kps = _create_base_keypoints(conf=0.9)
        kps[KP_RIGHT_WRIST] = [135, 175, 0.9]
        kps[KP_RIGHT_ELBOW] = [120, 210, 0.85]
        kps[KP_LEFT_WRIST] = [180, 280, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=9, buffer=buf)
    actions = [r.action for r in results if r.detected]
    assert "thumbs_up" in actions
    assert "hand_raised" not in actions


def test_head_nod_not_confused_with_waving():
    """Verify head movement when hands are resting does NOT trigger waving."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Nose moves down and up: 80 -> 100 -> 80 -> 100 -> 80
    ys = [80, 85, 95, 100, 95, 85, 80, 85, 95, 100, 95, 85, 80]
    now = time.monotonic()
    for i, y in enumerate(ys):
        # Wrists resting down at y=250 (chest/hips)
        kps = _create_base_keypoints(wrist_x=120, wrist_y=250, shoulder_y=150, conf=0.9)
        kps[KP_NOSE] = [150, y, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=10, buffer=buf)
    actions = [r.action for r in results if r.detected]
    assert "head_nod" not in actions
    assert "waving" not in actions


def test_waving_inhibits_head_nod():
    """Verify that an active waving hand is recognized as waving without head_nod."""
    classifier = ActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # Oscillating elevated wrist (waving), while nose moves slightly due to arm movement
    xs = [100, 180, 100, 180, 100, 180, 100, 180, 100, 180]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=80, shoulder_y=150, conf=0.9)
        # Small vertical nose shift (e.g. 80 -> 85)
        kps[KP_NOSE] = [150, 80 + (i % 2) * 6, 0.9]
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=11, buffer=buf)
    actions = [r.action for r in results if r.detected]
    assert "waving" in actions
    assert "head_nod" not in actions


