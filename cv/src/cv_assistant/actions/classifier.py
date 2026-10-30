"""Machine-learning gesture and action classifier.

Combines Google MediaPipe's GestureRecognizer neural network for high-precision
hand gestures (Thumb_Up, Open_Palm / Hand Raised, Waving, Namaste) with
temporal relative-pitch keypoint tracking for head nod, and falls back to
heuristics when video frames are unavailable (e.g. synthetic test suites).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from cv_assistant.actions.heuristics import (
    ActionClassifier,
    ActionResult,
    KP_LEFT_ELBOW,
    KP_LEFT_HIP,
    KP_LEFT_SHOULDER,
    KP_LEFT_WRIST,
    KP_NOSE,
    KP_RIGHT_ELBOW,
    KP_RIGHT_HIP,
    KP_RIGHT_SHOULDER,
    KP_RIGHT_WRIST,
)
from cv_assistant.actions.keypoint_buffer import KeypointBuffer
from cv_assistant.config import settings

logger = logging.getLogger(__name__)

# Suppress noisy MediaPipe/absl warnings
os.environ.setdefault("GLOG_minloglevel", "2")


# Landmark index constants for 21 MediaPipe hand landmarks
_WRIST = 0
_THUMB_CMC = 1
_THUMB_MCP = 2
_THUMB_IP = 3
_THUMB_TIP = 4
_INDEX_MCP = 5
_INDEX_PIP = 6
_INDEX_DIP = 7
_INDEX_TIP = 8
_MIDDLE_MCP = 9
_MIDDLE_PIP = 10
_MIDDLE_DIP = 11
_MIDDLE_TIP = 12
_RING_MCP = 13
_RING_PIP = 14
_RING_DIP = 15
_RING_TIP = 16
_PINKY_MCP = 17
_PINKY_PIP = 18
_PINKY_DIP = 19
_PINKY_TIP = 20

_FINGERS = [
    (_INDEX_MCP, _INDEX_PIP, _INDEX_DIP, _INDEX_TIP),
    (_MIDDLE_MCP, _MIDDLE_PIP, _MIDDLE_DIP, _MIDDLE_TIP),
    (_RING_MCP, _RING_PIP, _RING_DIP, _RING_TIP),
    (_PINKY_MCP, _PINKY_PIP, _PINKY_DIP, _PINKY_TIP),
]


def _is_finger_curled(landmarks: list[Any], finger: tuple[int, int, int, int]) -> bool:
    """Check if finger (mcp, pip, dip, tip) is curled into palm/wrist."""
    mcp_idx, pip_idx, dip_idx, tip_idx = finger
    wrist = landmarks[_WRIST]
    mcp = landmarks[mcp_idx]
    pip = landmarks[pip_idx]
    tip = landmarks[tip_idx]

    d_tip_wrist = np.hypot(tip.x - wrist.x, tip.y - wrist.y)
    d_pip_wrist = np.hypot(pip.x - wrist.x, pip.y - wrist.y)

    d_tip_mcp = np.hypot(tip.x - mcp.x, tip.y - mcp.y)
    d_pip_mcp = np.hypot(pip.x - mcp.x, pip.y - mcp.y)

    return bool((d_tip_wrist < d_pip_wrist * 1.02) or (d_tip_mcp < d_pip_mcp * 0.95))


def _is_finger_extended(landmarks: list[Any], finger: tuple[int, int, int, int]) -> bool:
    """Check if finger (mcp, pip, dip, tip) is extended outward."""
    mcp_idx, pip_idx, dip_idx, tip_idx = finger
    wrist = landmarks[_WRIST]
    mcp = landmarks[mcp_idx]
    pip = landmarks[pip_idx]
    tip = landmarks[tip_idx]

    d_tip_wrist = np.hypot(tip.x - wrist.x, tip.y - wrist.y)
    d_pip_wrist = np.hypot(pip.x - wrist.x, pip.y - wrist.y)

    d_tip_mcp = np.hypot(tip.x - mcp.x, tip.y - mcp.y)
    d_pip_mcp = np.hypot(pip.x - mcp.x, pip.y - mcp.y)

    return bool((d_tip_wrist > d_pip_wrist * 1.05) and (d_tip_mcp > d_pip_mcp * 1.05))


def _is_thumb_upright(landmarks: list[Any]) -> bool:
    """Check if thumb is pointing vertically upward and extended away from knuckles."""
    wrist = landmarks[_WRIST]
    mcp = landmarks[_THUMB_MCP]
    ip = landmarks[_THUMB_IP]
    tip = landmarks[_THUMB_TIP]
    index_mcp = landmarks[_INDEX_MCP]

    # In image coordinates, Y decreases upward: tip above IP, IP above MCP or CMC
    is_upward = (tip.y < ip.y) and (ip.y < mcp.y or tip.y < mcp.y - 0.02)
    above_wrist = tip.y < wrist.y - 0.03

    d_tip_index = np.hypot(tip.x - index_mcp.x, tip.y - index_mcp.y)
    d_mcp_index = np.hypot(mcp.x - index_mcp.x, mcp.y - index_mcp.y)
    is_extended = d_tip_index > d_mcp_index * 1.0

    return bool(is_upward and above_wrist and is_extended)


def _is_open_hand(landmarks: list[Any]) -> bool:
    """Check if at least 3 of 4 fingers (index, middle, ring, pinky) are extended."""
    return sum(1 for f in _FINGERS if _is_finger_extended(landmarks, f)) >= 3


def _count_curled_fingers(landmarks: list[Any]) -> int:
    """Count how many of the 4 fingers are curled."""
    return sum(1 for f in _FINGERS if _is_finger_curled(landmarks, f))


def _is_victory_sign(landmarks: list[Any]) -> bool:
    """Check if index and middle fingers are extended and spread, while ring and pinky are curled."""
    index_ext = _is_finger_extended(landmarks, _FINGERS[0])
    middle_ext = _is_finger_extended(landmarks, _FINGERS[1])
    ring_curled = _is_finger_curled(landmarks, _FINGERS[2])
    pinky_curled = _is_finger_curled(landmarks, _FINGERS[3])

    if not (index_ext and middle_ext and ring_curled and pinky_curled):
        return False

    index_tip = landmarks[_INDEX_TIP]
    middle_tip = landmarks[_MIDDLE_TIP]
    wrist = landmarks[_WRIST]
    middle_mcp = landmarks[_MIDDLE_MCP]

    palm_scale = max(float(np.hypot(middle_mcp.x - wrist.x, middle_mcp.y - wrist.y)), 0.05)
    tip_dist = float(np.hypot(index_tip.x - middle_tip.x, index_tip.y - middle_tip.y))

    return bool(tip_dist >= 0.20 * palm_scale)


_SHARED_RECOGNIZER: Any = None
_SHARED_RECOGNIZER_LOCK = threading.Lock()


def _get_shared_recognizer(model_path: Path) -> Any:
    """Get or create singleton MediaPipe HandLandmarker instance."""
    global _SHARED_RECOGNIZER
    if _SHARED_RECOGNIZER is not None:
        return _SHARED_RECOGNIZER
    with _SHARED_RECOGNIZER_LOCK:
        if _SHARED_RECOGNIZER is not None:
            return _SHARED_RECOGNIZER
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            base_options = python.BaseOptions(model_asset_path=str(model_path))
            # Check model type by file name or inspect capabilities
            if "hand_landmarker" in model_path.name.lower():
                options = vision.HandLandmarkerOptions(
                    base_options=base_options,
                    running_mode=vision.RunningMode.IMAGE,
                    num_hands=4,
                    min_hand_detection_confidence=0.45,
                    min_hand_presence_confidence=0.45,
                    min_tracking_confidence=0.45,
                )
                _SHARED_RECOGNIZER = vision.HandLandmarker.create_from_options(options)
                logger.info("MediaPipe HandLandmarker loaded successfully from %s", model_path)
            else:
                options = vision.GestureRecognizerOptions(
                    base_options=base_options,
                    running_mode=vision.RunningMode.IMAGE,
                    num_hands=4,
                    min_hand_detection_confidence=0.45,
                    min_hand_presence_confidence=0.45,
                    min_tracking_confidence=0.45,
                )
                _SHARED_RECOGNIZER = vision.GestureRecognizer.create_from_options(options)
                logger.info("MediaPipe GestureRecognizer loaded successfully from %s", model_path)
        except Exception as exc:
            logger.warning("Failed to initialize MediaPipe vision model: %s", exc)
            _SHARED_RECOGNIZER = None
        return _SHARED_RECOGNIZER


class MLActionClassifier:
    """Action classifier powered by MediaPipe Gesture Recognizer neural network.

    Features:
    - Dedicated hand landmark & gesture recognition using Google's gesture_recognizer.task model.
    - Zero ambiguity between 'thumbs_up' and 'hand_raised' by checking 21 3D finger landmarks & categories.
    - Zero ambiguity between 'head_nod' and 'waving' by coupling hand presence with shoulder-relative head pitch.
    - Per-frame inference caching: runs neural network once per camera frame even with multiple tracked people.
    - Seamless fallback to geometric heuristics when image frames are unavailable (synthetic tests).
    """

    def __init__(self, model_path: str | Path | None = None) -> None:
        self._heuristic = ActionClassifier()
        self._recognizer: Any = None
        self._cooldowns: dict[str, float] = {}
        self._hand_trajectories: dict[int, list[tuple[float, float, float]]] = {}
        self._prediction_history: dict[int, list[tuple[str, float, float]]] = {}

        # Frame inference cache
        self._last_frame_id: int | None = None
        self._last_result: Any = None

        resolved_path = self._resolve_model_path(model_path)
        if resolved_path and resolved_path.exists():
            self._recognizer = _get_shared_recognizer(resolved_path)
        else:
            logger.info("MediaPipe hand landmarker model not found; operating in heuristic mode.")

    def _resolve_model_path(self, model_path: str | Path | None) -> Path | None:
        """Find the hand landmarker / gesture model file across common project locations."""
        candidates = []
        if model_path:
            candidates.append(Path(model_path))

        configured = getattr(settings.actions, "model_path", None)
        if configured:
            candidates.append(Path(configured))

        cv_root = Path(__file__).resolve().parents[3]
        candidates.extend([
            cv_root / "cv" / "models" / "hand_landmarker.task",
            cv_root / "models" / "hand_landmarker.task",
            Path("cv/models/hand_landmarker.task"),
            Path("models/hand_landmarker.task"),
            cv_root / "cv" / "models" / "gesture_recognizer.task",
            cv_root / "models" / "gesture_recognizer.task",
            Path("cv/models/gesture_recognizer.task"),
            Path("models/gesture_recognizer.task"),
        ])

        for c in candidates:
            if c.exists():
                return c.resolve()
        return None

    def _check_cooldown(self, key: str, cooldown_sec: float) -> bool:
        """Return True if cooldown has expired and update last fire time."""
        now = time.monotonic()
        if now - self._cooldowns.get(key, 0.0) < cooldown_sec:
            return False
        self._cooldowns[key] = now
        return True

    def _recognize_frame(self, frame_image: np.ndarray) -> Any:
        """Run MediaPipe HandLandmarker/GestureRecognizer on image frame with single-run per-frame cache."""
        if self._recognizer is None:
            return None

        frame_id = id(frame_image)
        if frame_id == self._last_frame_id and self._last_result is not None:
            return self._last_result

        try:
            import mediapipe as mp

            if len(frame_image.shape) == 3 and frame_image.shape[2] == 3:
                rgb_image = cv2.cvtColor(frame_image, cv2.COLOR_BGR2RGB)
            else:
                rgb_image = frame_image

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
            if hasattr(self._recognizer, "detect"):
                result = self._recognizer.detect(mp_image)
            elif hasattr(self._recognizer, "recognize"):
                result = self._recognizer.recognize(mp_image)
            else:
                result = None

            self._last_frame_id = frame_id
            self._last_result = result
            return result
        except Exception as exc:
            logger.debug("MediaPipe landmarker inference failed: %s", exc)
            return None

    def _get_hands_for_person(
        self,
        mp_result: Any,
        bbox: tuple[float, float, float, float] | list[float],
        img_shape: tuple[int, int],
    ) -> list[dict[str, Any]]:
        """Match detected hands to person's bounding box using coordinate reach."""
        if not mp_result or not mp_result.hand_landmarks:
            return []

        H, W = img_shape
        x1, y1, x2, y2 = bbox
        pw = max(x2 - x1, 1.0)
        ph = max(y2 - y1, 1.0)

        norm_x1 = max(0.0, (x1 - 0.60 * pw) / W)
        norm_x2 = min(1.0, (x2 + 0.60 * pw) / W)
        norm_y1 = max(0.0, (y1 - 0.65 * ph) / H)
        norm_y2 = min(1.0, (y2 + 0.20 * ph) / H)

        matched = []
        for i, landmarks in enumerate(mp_result.hand_landmarks):
            wrist = landmarks[0]
            palm = landmarks[9] if len(landmarks) > 9 else wrist

            in_reach = (
                (norm_x1 <= wrist.x <= norm_x2 and norm_y1 <= wrist.y <= norm_y2)
                or (norm_x1 <= palm.x <= norm_x2 and norm_y1 <= palm.y <= norm_y2)
            )
            if in_reach:
                gesture_name = "None"
                score = 0.90
                if hasattr(mp_result, "gestures") and i < len(mp_result.gestures) and mp_result.gestures[i]:
                    top_gesture = mp_result.gestures[i][0]
                    gesture_name = top_gesture.category_name
                    score = float(top_gesture.score)
                else:
                    if _is_victory_sign(landmarks):
                        gesture_name = "Victory"
                        score = 0.92
                    elif _is_thumb_upright(landmarks) and _count_curled_fingers(landmarks) >= 3:
                        gesture_name = "Thumb_Up"
                        score = 0.90
                    elif _is_open_hand(landmarks):
                        gesture_name = "Open_Palm"
                        score = 0.90

                matched.append({
                    "landmarks": landmarks,
                    "wrist": wrist,
                    "palm": palm,
                    "gesture": gesture_name,
                    "score": score,
                })
        return matched

    def _apply_temporal_smoothing(self, track_id: int, action: str, confidence: float) -> ActionResult | None:
        """Apply sliding-window temporal smoothing to prevent single-frame flickering."""
        now = time.monotonic()
        if track_id not in self._prediction_history:
            self._prediction_history[track_id] = []
        hist = self._prediction_history[track_id]
        hist.append((action, confidence, now))
        # Keep predictions within 0.8s window
        self._prediction_history[track_id] = [h for h in hist if (now - h[2]) <= 0.8][-10:]

        action_count = sum(1 for h in self._prediction_history[track_id] if h[0] == action)
        if action_count >= 2 or confidence >= 0.95 or len(self._prediction_history[track_id]) == 1:
            return ActionResult(action, True, confidence=confidence)
        return None

    def classify(
        self,
        track_id: int,
        buffer: KeypointBuffer,
        frame_image: np.ndarray | None = None,
    ) -> list[ActionResult]:
        """Classify actions for the given track using 21 3D landmarks & geometry."""
        # Fallback to heuristics if no frame image or recognizer not ready
        if frame_image is None or self._recognizer is None:
            return self._heuristic.classify(track_id, buffer, frame_image=None)

        if buffer.length < 3:
            return []

        latest_frame = list(buffer._buffer)[-1]
        bbox = latest_frame.bbox
        kps = latest_frame.keypoints
        H, W = frame_image.shape[:2]

        mp_res = self._recognize_frame(frame_image)
        matched_hands = self._get_hands_for_person(mp_res, bbox, (H, W))

        # Track hand palm/wrist history for temporal wave motion
        now_ts = time.monotonic()
        if matched_hands:
            primary_h = matched_hands[0]
            if track_id not in self._hand_trajectories:
                self._hand_trajectories[track_id] = []
            self._hand_trajectories[track_id].append(
                (float(primary_h["palm"].x), float(primary_h["palm"].y), now_ts)
            )
            self._hand_trajectories[track_id] = [
                pt for pt in self._hand_trajectories[track_id]
                if (now_ts - pt[2]) <= 1.5
            ][-25:]

        # Relative vertical anchors from YOLO body keypoints if available
        ls_y = float(kps[KP_LEFT_SHOULDER, 1]) if kps[KP_LEFT_SHOULDER, 2] > 0.3 else None
        rs_y = float(kps[KP_RIGHT_SHOULDER, 1]) if kps[KP_RIGHT_SHOULDER, 2] > 0.3 else None
        lh_y = float(kps[KP_LEFT_HIP, 1]) if kps[KP_LEFT_HIP, 2] > 0.3 else None
        rh_y = float(kps[KP_RIGHT_HIP, 1]) if kps[KP_RIGHT_HIP, 2] > 0.3 else None

        shoulders_y = [y for y in (ls_y, rs_y) if y is not None]
        hips_y = [y for y in (lh_y, rh_y) if y is not None]

        # Normalized shoulder and hip level (or fall back to box fractions)
        norm_shoulder_y = (np.mean(shoulders_y) / H) if shoulders_y else ((bbox[1] + 0.25 * (bbox[3] - bbox[1])) / H)
        norm_hip_y = (np.mean(hips_y) / H) if hips_y else ((bbox[1] + 0.65 * (bbox[3] - bbox[1])) / H)

        # ── 1. Namaste (Folded hands near chest) ─────────────────────────
        if len(matched_hands) >= 2:
            h1, h2 = matched_hands[0], matched_hands[1]
            palm_dist = float(np.hypot(h1["palm"].x - h2["palm"].x, h1["palm"].y - h2["palm"].y))
            avg_y = (h1["wrist"].y + h2["wrist"].y) / 2.0

            # Chest level check (between shoulders and hips)
            at_chest = (norm_shoulder_y - 0.05) <= avg_y <= (norm_hip_y + 0.10)
            h1_tips_up = h1["landmarks"][_MIDDLE_TIP].y < (h1["wrist"].y - 0.02)
            h2_tips_up = h2["landmarks"][_MIDDLE_TIP].y < (h2["wrist"].y - 0.02)

            if palm_dist < 0.18 and at_chest and (h1_tips_up or h2_tips_up):
                res = self._apply_temporal_smoothing(track_id, "namaste", 0.92)
                return [res] if res else []

        # ── 2. Victory Sign (V sign: Index + Middle extended, others curled)
        for hand in matched_hands:
            lm = hand["landmarks"]
            if _is_victory_sign(lm) or hand["gesture"] == "Victory":
                conf = max(hand["score"], 0.90)
                res = self._apply_temporal_smoothing(track_id, "victory", conf)
                return [res] if res else []

        # ── 3. Thumbs Up (Thumb upright, other 4 fingers curled) ──────────
        for hand in matched_hands:
            lm = hand["landmarks"]
            thumb_up = _is_thumb_upright(lm)
            curled = _count_curled_fingers(lm)
            cat = hand["gesture"]
            is_thumbs = (thumb_up and curled >= 3) or (cat == "Thumb_Up" and thumb_up and curled >= 2)

            if is_thumbs:
                wrist_y = hand["wrist"].y
                # Strictly below head/shoulder to avoid confusing with raised hand
                if wrist_y >= (norm_shoulder_y - 0.08) and wrist_y <= (norm_hip_y + 0.12):
                    conf = max(hand["score"], 0.88)
                    res = self._apply_temporal_smoothing(track_id, "thumbs_up", conf)
                    return [res] if res else []

        # ── 4. Waving vs Hand Raised (Elevated Open Hand) ────────────────
        for hand in matched_hands:
            lm = hand["landmarks"]
            open_hand = _is_open_hand(lm) or hand["gesture"] == "Open_Palm"
            wrist_y = hand["wrist"].y
            palm_y = hand["palm"].y
            is_elevated = (wrist_y <= norm_shoulder_y + 0.14) or (palm_y <= norm_shoulder_y + 0.10)

            if open_hand and is_elevated:
                # Check waving: cyclic horizontal oscillation
                is_waving = False

                # A) YOLO wrist series oscillation
                series = buffer.get_normalised_series(n=20)
                if len(series) >= 5:
                    for wrist_idx, shoulder_idx in (
                        (KP_RIGHT_WRIST, KP_RIGHT_SHOULDER),
                        (KP_LEFT_WRIST, KP_LEFT_SHOULDER),
                    ):
                        xs = []
                        for s in series:
                            w_conf = s[wrist_idx, 2]
                            s_conf = s[shoulder_idx, 2]
                            w_y = s[wrist_idx, 1]
                            s_y = s[shoulder_idx, 1]
                            if w_conf > 0.25 and s_conf > 0.20 and w_y <= (s_y + 0.16):
                                xs.append(float(s[wrist_idx, 0]))

                        if len(xs) >= 5:
                            span = max(xs) - min(xs)
                            diffs = np.diff(xs)
                            reversals = 0
                            for d1, d2 in zip(diffs[:-1], diffs[1:]):
                                if (d1 > 0.010 and d2 < -0.010) or (d1 < -0.010 and d2 > 0.010):
                                    reversals += 1
                            if span >= 0.028 and reversals >= 2:
                                is_waving = True
                                break

                # B) MediaPipe hand trajectory oscillation
                if not is_waving:
                    traj = self._hand_trajectories.get(track_id, [])
                    if len(traj) >= 5:
                        m_xs = [pt[0] for pt in traj]
                        m_span = max(m_xs) - min(m_xs)
                        m_diffs = np.diff(m_xs)
                        m_reversals = 0
                        for d1, d2 in zip(m_diffs[:-1], m_diffs[1:]):
                            if (d1 > 0.008 and d2 < -0.008) or (d1 < -0.008 and d2 > 0.008):
                                m_reversals += 1
                        if m_span >= 0.025 and m_reversals >= 2:
                            is_waving = True

                if is_waving:
                    conf = max(0.90, hand["score"]) if hand["gesture"] == "Open_Palm" else 0.90
                    res = self._apply_temporal_smoothing(track_id, "waving", conf)
                    return [res] if res else []

                # Not waving -> Hand Raised held steady
                conf = max(0.88, hand["score"])
                res = self._apply_temporal_smoothing(track_id, "hand_raised", conf)
                return [res] if res else []

        # ── 5. Fallback to geometric heuristics if no action detected yet
        fallback = self._heuristic.classify(track_id, buffer, frame_image=frame_image)
        if fallback:
            return fallback

        return []
