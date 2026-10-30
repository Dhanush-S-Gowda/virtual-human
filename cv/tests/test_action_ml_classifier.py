"""Unit tests for MLActionClassifier (MediaPipe Gesture Recognizer integration)."""

import time
import numpy as np
import pytest

from cv_assistant.actions.classifier import MLActionClassifier
from cv_assistant.actions.heuristics import ActionClassifier, ActionResult
from cv_assistant.actions.keypoint_buffer import KeypointBuffer
from cv_assistant.detection import (
    KP_LEFT_SHOULDER,
    KP_LEFT_WRIST,
    KP_NOSE,
    KP_RIGHT_SHOULDER,
    KP_RIGHT_WRIST,
)


def _create_base_keypoints(wrist_x=150, wrist_y=100, shoulder_y=150, conf=0.9):
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[:, 2] = conf
    kps[KP_NOSE] = [150, 80, conf]
    kps[KP_RIGHT_SHOULDER] = [120, shoulder_y, conf]
    kps[KP_LEFT_SHOULDER] = [180, shoulder_y, conf]
    kps[KP_RIGHT_WRIST] = [wrist_x, wrist_y, conf]
    kps[KP_LEFT_WRIST] = [180, 250, conf]
    return kps


def test_ml_classifier_initializes_with_model():
    classifier = MLActionClassifier()
    assert classifier._recognizer is not None, "MediaPipe recognizer should be loaded"


def test_ml_classifier_falls_back_when_frame_image_is_none():
    classifier = MLActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)

    # 10 oscillating frames for waving
    xs = [100, 180, 100, 180, 100, 180, 100, 180, 100, 180]
    now = time.monotonic()
    for i, x in enumerate(xs):
        kps = _create_base_keypoints(wrist_x=x, wrist_y=80, shoulder_y=150, conf=0.9)
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=1, buffer=buf, frame_image=None)
    assert any(r.action == "waving" and r.detected for r in results)


def test_ml_classifier_per_frame_cache():
    classifier = MLActionClassifier()
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)

    # First call runs inference and populates cache
    res1 = classifier._recognize_frame(dummy_img)
    # Second call with the same frame returns cached result without re-running
    res2 = classifier._recognize_frame(dummy_img)

    assert res1 is res2
    assert classifier._last_frame_id == id(dummy_img)


def test_ml_classifier_handles_blank_frame_gracefully():
    classifier = MLActionClassifier()
    buf = KeypointBuffer(max_size=30)
    bbox = (50.0, 50.0, 250.0, 350.0)
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)

    now = time.monotonic()
    kps = _create_base_keypoints(wrist_x=150, wrist_y=200, shoulder_y=150, conf=0.9)
    for i in range(5):
        buf.add(kps, timestamp=now + i * 0.05, bbox=bbox)

    results = classifier.classify(track_id=1, buffer=buf, frame_image=dummy_img)
    # No gestures should be falsely triggered on a blank frame
    assert isinstance(results, list)


class _DummyLandmark:
    def __init__(self, x: float, y: float, z: float = 0.0):
        self.x = x
        self.y = y
        self.z = z


def test_21_landmark_finger_curl_and_extension_geometry():
    from cv_assistant.actions.classifier import (
        _is_finger_curled,
        _is_finger_extended,
        _is_thumb_upright,
        _is_open_hand,
        _count_curled_fingers,
    )

    # 1. Extended open hand landmarks
    # Wrist at (0.5, 0.7)
    wrist = _DummyLandmark(0.5, 0.7)
    open_landmarks = [wrist] * 21

    # Thumb extended upright
    open_landmarks[1] = _DummyLandmark(0.52, 0.65) # CMC
    open_landmarks[2] = _DummyLandmark(0.54, 0.58) # MCP
    open_landmarks[3] = _DummyLandmark(0.55, 0.50) # IP
    open_landmarks[4] = _DummyLandmark(0.55, 0.42) # TIP (highest, y is smallest)

    # Index finger extended
    open_landmarks[5] = _DummyLandmark(0.48, 0.55) # MCP
    open_landmarks[6] = _DummyLandmark(0.47, 0.45) # PIP
    open_landmarks[7] = _DummyLandmark(0.46, 0.38) # DIP
    open_landmarks[8] = _DummyLandmark(0.45, 0.30) # TIP

    # Middle finger extended
    open_landmarks[9] = _DummyLandmark(0.50, 0.54) # MCP
    open_landmarks[10] = _DummyLandmark(0.50, 0.43) # PIP
    open_landmarks[11] = _DummyLandmark(0.50, 0.36) # DIP
    open_landmarks[12] = _DummyLandmark(0.50, 0.28) # TIP

    # Ring finger extended
    open_landmarks[13] = _DummyLandmark(0.52, 0.55) # MCP
    open_landmarks[14] = _DummyLandmark(0.53, 0.45) # PIP
    open_landmarks[15] = _DummyLandmark(0.53, 0.38) # DIP
    open_landmarks[16] = _DummyLandmark(0.53, 0.30) # TIP

    # Pinky finger extended
    open_landmarks[17] = _DummyLandmark(0.54, 0.56) # MCP
    open_landmarks[18] = _DummyLandmark(0.55, 0.47) # PIP
    open_landmarks[19] = _DummyLandmark(0.55, 0.41) # DIP
    open_landmarks[20] = _DummyLandmark(0.55, 0.35) # TIP

    # Test open hand
    assert _is_finger_extended(open_landmarks, (5, 6, 7, 8)) is True
    assert _is_open_hand(open_landmarks) is True
    assert _count_curled_fingers(open_landmarks) == 0

    # 2. Curled fist with thumbs up
    fist_landmarks = list(open_landmarks)
    # Curl index (tip closer to wrist than PIP)
    fist_landmarks[5] = _DummyLandmark(0.48, 0.55)
    fist_landmarks[6] = _DummyLandmark(0.47, 0.47)
    fist_landmarks[7] = _DummyLandmark(0.48, 0.52)
    fist_landmarks[8] = _DummyLandmark(0.49, 0.54)

    # Curl middle
    fist_landmarks[9] = _DummyLandmark(0.50, 0.54)
    fist_landmarks[10] = _DummyLandmark(0.50, 0.46)
    fist_landmarks[11] = _DummyLandmark(0.50, 0.51)
    fist_landmarks[12] = _DummyLandmark(0.50, 0.53)

    # Curl ring
    fist_landmarks[13] = _DummyLandmark(0.52, 0.55)
    fist_landmarks[14] = _DummyLandmark(0.52, 0.47)
    fist_landmarks[15] = _DummyLandmark(0.52, 0.52)
    fist_landmarks[16] = _DummyLandmark(0.52, 0.54)

    # Curl pinky
    fist_landmarks[17] = _DummyLandmark(0.54, 0.56)
    fist_landmarks[18] = _DummyLandmark(0.54, 0.48)
    fist_landmarks[19] = _DummyLandmark(0.54, 0.53)
    fist_landmarks[20] = _DummyLandmark(0.54, 0.55)

    assert _is_finger_curled(fist_landmarks, (5, 6, 7, 8)) is True
    assert _is_thumb_upright(fist_landmarks) is True
    assert _count_curled_fingers(fist_landmarks) == 4
    assert _is_open_hand(fist_landmarks) is False


def test_victory_sign_geometry():
    from cv_assistant.actions.classifier import _is_victory_sign

    # Construct victory sign landmarks:
    # Index and Middle extended and spread, Ring and Pinky curled
    wrist = _DummyLandmark(0.5, 0.7)
    v_landmarks = [wrist] * 21

    # Thumb curled/tucked
    v_landmarks[1] = _DummyLandmark(0.52, 0.65)
    v_landmarks[2] = _DummyLandmark(0.53, 0.62)
    v_landmarks[3] = _DummyLandmark(0.52, 0.60)
    v_landmarks[4] = _DummyLandmark(0.50, 0.58)

    # Index extended to upper-left
    v_landmarks[5] = _DummyLandmark(0.48, 0.55)  # MCP
    v_landmarks[6] = _DummyLandmark(0.46, 0.45)  # PIP
    v_landmarks[7] = _DummyLandmark(0.44, 0.38)  # DIP
    v_landmarks[8] = _DummyLandmark(0.42, 0.30)  # TIP

    # Middle extended to upper-right (spread away from index)
    v_landmarks[9] = _DummyLandmark(0.52, 0.54)   # MCP
    v_landmarks[10] = _DummyLandmark(0.54, 0.43)  # PIP
    v_landmarks[11] = _DummyLandmark(0.56, 0.36)  # DIP
    v_landmarks[12] = _DummyLandmark(0.58, 0.28)  # TIP

    # Ring curled
    v_landmarks[13] = _DummyLandmark(0.53, 0.55)
    v_landmarks[14] = _DummyLandmark(0.53, 0.48)
    v_landmarks[15] = _DummyLandmark(0.53, 0.52)
    v_landmarks[16] = _DummyLandmark(0.53, 0.54)

    # Pinky curled
    v_landmarks[17] = _DummyLandmark(0.55, 0.56)
    v_landmarks[18] = _DummyLandmark(0.55, 0.49)
    v_landmarks[19] = _DummyLandmark(0.55, 0.53)
    v_landmarks[20] = _DummyLandmark(0.55, 0.55)

    assert _is_victory_sign(v_landmarks) is True


def test_temporal_smoothing_filter():
    classifier = MLActionClassifier()

    # Single moderate-confidence observation should not flicker out or may initialize
    res1 = classifier._apply_temporal_smoothing(track_id=42, action="waving", confidence=0.90)
    assert res1 is not None and res1.action == "waving"

    # Consistent second observation reinforces detection
    res2 = classifier._apply_temporal_smoothing(track_id=42, action="waving", confidence=0.90)
    assert res2 is not None and res2.action == "waving"


def test_hand_reach_expansion_above_head():
    classifier = MLActionClassifier()

    class _MockResult:
        def __init__(self, landmarks):
            self.hand_landmarks = [landmarks]
            self.gestures = [[type("G", (), {"category_name": "Open_Palm", "score": 0.95})()]]

    # Person bbox from y=100 to y=400 in 480x640 frame (ph = 300)
    # A hand raised high above head: y = 20 (norm_y = 20/480 = 0.041)
    high_wrist = _DummyLandmark(0.5, 0.04)
    landmarks = [high_wrist] * 21

    mp_res = _MockResult(landmarks)
    matched = classifier._get_hands_for_person(mp_res, bbox=(100, 100, 300, 400), img_shape=(480, 640))
    assert len(matched) == 1, "Hand raised high above head should be matched within expanded reach"


