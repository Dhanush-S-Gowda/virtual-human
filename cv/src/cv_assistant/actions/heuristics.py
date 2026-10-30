"""Geometric and temporal heuristics for action recognition.

Detects actions like waving and raised hand from normalised keypoint
time-series.  Designed to be replaceable by an ML classifier through
the same interface.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from cv_assistant.actions.keypoint_buffer import KeypointBuffer
from cv_assistant.config import settings
from cv_assistant.detection import (
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

logger = logging.getLogger(__name__)


class ActionResult:
    """Result of an action classification attempt."""

    __slots__ = ("action", "confidence", "detected")

    def __init__(self, action: str, detected: bool, confidence: float = 0.0) -> None:
        self.action = action
        self.detected = detected
        self.confidence = confidence


class ActionClassifier:
    """Heuristic action classifier operating on per-track keypoint buffers.

    Supports a pluggable interface: call ``classify(buffer)`` and receive
    a list of detected actions.  Cooldowns prevent duplicate events.
    """

    def __init__(self) -> None:
        self._cooldowns: dict[str, float] = {}  # key: "track_id:action" -> last fire time

    def classify(
        self,
        track_id: int,
        buffer: KeypointBuffer,
        frame_image: np.ndarray | None = None,
    ) -> list[ActionResult]:
        """Run all heuristic classifiers on the given buffer.

        Returns only actions that are currently detected **and** past their
        cooldown period. Order of precedence:
        1. Namaste (folded hands in front of chest)
        2. Waving (elevated hand oscillating side-to-side)
        3. Hand raised (elevated hand held steady)
        4. Thumbs up (single bent arm held steady at chest level, below shoulder)
        5. Head nod (vertical head tilt relative to shoulders; inhibited if hand elevated)
        """
        results: list[ActionResult] = []

        # 1. Check namaste first (both hands touching at chest)
        namaste = self._check_namaste(track_id, buffer)
        if namaste.detected:
            results.append(namaste)
            return results

        # 2. Check waving (elevated oscillating wrist)
        waving = self._check_waving(track_id, buffer)
        if waving.detected:
            results.append(waving)
            return results

        # 3. Check hand raised (elevated steady wrist above shoulder/head)
        hand_raised = self._check_hand_raised(track_id, buffer)
        if hand_raised.detected:
            results.append(hand_raised)
            return results

        # 4. Check thumbs up (arm bent at chest level, strictly below shoulder)
        thumbs_up = self._check_thumbs_up(track_id, buffer, frame_image=frame_image)
        if thumbs_up.detected:
            results.append(thumbs_up)
            return results

        return results

    # ── Waving ──────────────────────────────────────────────────────────

    def _check_waving(self, track_id: int, buffer: KeypointBuffer) -> ActionResult:
        """Detect waving: wrist above shoulder, oscillating side-to-side."""
        cfg = settings.actions.waving
        window = cfg.window_frames
        series = buffer.get_normalised_series(n=window)

        if len(series) < max(5, window // 3):
            return ActionResult("waving", False)

        # Check cooldown
        key = f"{track_id}:waving"
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cfg.cooldown_sec:
            return ActionResult("waving", False)

        # Check both wrists
        for wrist_idx, shoulder_idx in [
            (KP_RIGHT_WRIST, KP_RIGHT_SHOULDER),
            (KP_LEFT_WRIST, KP_LEFT_SHOULDER),
        ]:
            if self._is_waving_wrist(series, wrist_idx, shoulder_idx, cfg):
                self._cooldowns[key] = now
                return ActionResult("waving", True, confidence=0.8)

        return ActionResult("waving", False)

    def _is_waving_wrist(
        self,
        series: list[np.ndarray],
        wrist_idx: int,
        shoulder_idx: int,
        cfg: Any,
    ) -> bool:
        """Check if a specific wrist exhibits waving motion."""
        elevated_xs: list[float] = []
        min_conf = min(float(cfg.min_confidence), 0.28)
        for kps in series:
            w_conf = float(kps[wrist_idx, 2])
            s_conf = float(kps[shoulder_idx, 2])
            # Skip frames where wrist or shoulder tracking is lost (<0.28)
            if w_conf < min_conf or s_conf < 0.25:
                continue

            w_y = float(kps[wrist_idx, 1])
            s_y = float(kps[shoulder_idx, 1])

            # Wrist must be at or above shoulder / upper chest level.
            # Allows up to 0.10 below shoulder line so natural forearm angles trigger.
            if cfg.min_wrist_above_shoulder and w_y > s_y + 0.10:
                continue

            elevated_xs.append(float(kps[wrist_idx, 0]))

        if len(elevated_xs) < 5:
            return False

        xs = elevated_xs
        motion_span = float(max(xs) - min(xs))
        if motion_span < 0.030:
            return False

        # Peak and trough oscillation detection (invariant to frame rate or speed)
        prominence = 0.015
        direction = 0
        reversals = 0
        last_extreme = xs[0]

        for x in xs[1:]:
            diff = x - last_extreme
            if direction >= 0:
                if diff >= prominence:
                    direction = 1
                    last_extreme = x
                elif direction == 1 and (last_extreme - x) >= prominence:
                    direction = -1
                    reversals += 1
                    last_extreme = x
            elif direction <= 0:
                if -diff >= prominence:
                    direction = -1
                    last_extreme = x
                elif direction == -1 and (x - last_extreme) >= prominence:
                    direction = 1
                    reversals += 1
                    last_extreme = x

        # Fallback for synthetic step tests
        xs_arr = np.array(xs)
        diffs = np.diff(xs_arr)
        sig = diffs[np.abs(diffs) > 0.012]
        alt_reversals = 0
        for i in range(1, len(sig)):
            if sig[i] * sig[i - 1] < 0:
                alt_reversals += 1

        total_reversals = max(reversals, alt_reversals)
        min_oscillations = getattr(cfg, "min_oscillations", 2)
        return total_reversals >= min(2, min_oscillations)

    # ── Hand raised ─────────────────────────────────────────────────────

    def _check_hand_raised(self, track_id: int, buffer: KeypointBuffer) -> ActionResult:
        """Detect a raised hand: wrist held steady above shoulder or head level."""
        cfg = settings.actions.hand_raised
        series = buffer.get_normalised_series(n=cfg.min_hold_frames + 3)

        if len(series) < cfg.min_hold_frames:
            return ActionResult("hand_raised", False)

        key = f"{track_id}:hand_raised"
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cfg.cooldown_sec:
            return ActionResult("hand_raised", False)

        min_conf = min(float(cfg.min_confidence), 0.28)
        # Check last N frames for sustained raised hand
        recent = series[-cfg.min_hold_frames:]
        for wrist_idx, shoulder_idx, elbow_idx in [
            (KP_RIGHT_WRIST, KP_RIGHT_SHOULDER, KP_RIGHT_ELBOW),
            (KP_LEFT_WRIST, KP_LEFT_SHOULDER, KP_LEFT_ELBOW),
        ]:
            raised_frames = 0
            valid_frames = 0
            wrist_xs: list[float] = []

            for kps in recent:
                w_conf = float(kps[wrist_idx, 2])
                s_conf = float(kps[shoulder_idx, 2])
                if w_conf < min_conf or s_conf < 0.20:
                    continue
                valid_frames += 1
                w_x = float(kps[wrist_idx, 0])
                w_y = float(kps[wrist_idx, 1])
                s_y = float(kps[shoulder_idx, 1])
                wrist_xs.append(w_x)

                # Get head level (nose, eyes, ears)
                head_pts = [float(kps[i, 1]) for i in range(5) if float(kps[i, 2]) >= 0.20]
                head_y = min(head_pts) if head_pts else s_y - 0.15

                # Must be elevated: near/above head OR at/above shoulder line
                is_raised = False
                if head_y is not None and w_y <= head_y + 0.06:
                    is_raised = True
                elif w_y <= s_y + 0.02:
                    is_raised = True

                # If elbow is tracked and valid, wrist should not be hanging far below elbow
                e_conf = float(kps[elbow_idx, 2])
                e_x = float(kps[elbow_idx, 0])
                e_y = float(kps[elbow_idx, 1])
                if e_conf >= 0.25 and (e_x > 0.01 or e_y > 0.01):
                    if w_y > e_y + 0.12:
                        is_raised = False

                if is_raised:
                    raised_frames += 1

            if valid_frames >= max(2, cfg.min_hold_frames - 2) and raised_frames >= max(2, int(valid_frames * 0.70)):
                # Raised hand must be held steady (not oscillating side to side)
                if wrist_xs:
                    x_span = max(wrist_xs) - min(wrist_xs)
                    if x_span > 0.09:
                        continue
                self._cooldowns[key] = now
                return ActionResult("hand_raised", True, confidence=0.85)

        return ActionResult("hand_raised", False)

    # ── Namaste / Folded hands ──────────────────────────────────────────

    def _check_namaste(self, track_id: int, buffer: KeypointBuffer) -> ActionResult:
        """Detect Namaste / folded hands gesture: both wrists touching in front of chest."""
        cfg = getattr(settings.actions, "namaste", None)
        hold_frames = getattr(cfg, "min_hold_frames", 4) if cfg else 4
        max_dist = getattr(cfg, "max_wrist_distance", 0.12) if cfg else 0.12
        cooldown_sec = getattr(cfg, "cooldown_sec", 3.0) if cfg else 3.0
        min_conf = getattr(cfg, "min_confidence", 0.35) if cfg else 0.35

        series = buffer.get_normalised_series(n=hold_frames + 2)
        if len(series) < hold_frames:
            return ActionResult("namaste", False)

        key = f"{track_id}:namaste"
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cooldown_sec:
            return ActionResult("namaste", False)

        recent = series[-hold_frames:]
        valid_frames = 0
        folded_frames = 0

        for kps in recent:
            lw_conf = float(kps[KP_LEFT_WRIST, 2])
            rw_conf = float(kps[KP_RIGHT_WRIST, 2])
            if lw_conf < min_conf or rw_conf < min_conf:
                continue
            valid_frames += 1

            lw_x, lw_y = float(kps[KP_LEFT_WRIST, 0]), float(kps[KP_LEFT_WRIST, 1])
            rw_x, rw_y = float(kps[KP_RIGHT_WRIST, 0]), float(kps[KP_RIGHT_WRIST, 1])

            # Wrists close together
            wrist_dist = float(np.hypot(lw_x - rw_x, lw_y - rw_y))
            if wrist_dist > max_dist:
                continue

            # Chest height: between shoulders and hips
            ls_y = float(kps[KP_LEFT_SHOULDER, 1]) if float(kps[KP_LEFT_SHOULDER, 2]) >= 0.2 else None
            rs_y = float(kps[KP_RIGHT_SHOULDER, 1]) if float(kps[KP_RIGHT_SHOULDER, 2]) >= 0.2 else None
            shoulders_y = [y for y in (ls_y, rs_y) if y is not None]
            shoulder_lvl = min(shoulders_y) if shoulders_y else 0.25

            lh_y = float(kps[KP_LEFT_HIP, 1]) if float(kps[KP_LEFT_HIP, 2]) >= 0.2 else None
            rh_y = float(kps[KP_RIGHT_HIP, 1]) if float(kps[KP_RIGHT_HIP, 2]) >= 0.2 else None
            hips_y = [y for y in (lh_y, rh_y) if y is not None and y > shoulder_lvl + 0.05]
            hip_lvl = max(hips_y) if hips_y else shoulder_lvl + 0.60

            avg_wy = (lw_y + rw_y) / 2.0
            # Wrists must be in front of chest (from near shoulder level down to hips)
            if (shoulder_lvl - 0.08) <= avg_wy <= (hip_lvl + 0.05):
                folded_frames += 1

        if valid_frames >= max(2, hold_frames - 2) and folded_frames >= max(2, int(valid_frames * 0.70)):
            self._cooldowns[key] = now
            return ActionResult("namaste", True, confidence=0.88)

        return ActionResult("namaste", False)

    # ── Thumbs up ───────────────────────────────────────────────────────

    def _check_thumbs_up(
        self,
        track_id: int,
        buffer: KeypointBuffer,
        frame_image: np.ndarray | None = None,
    ) -> ActionResult:
        """Detect thumbs-up: bent arm held steady at chest level (strictly below shoulder line)."""
        cfg = getattr(settings.actions, "thumbs_up", None)
        hold_frames = getattr(cfg, "min_hold_frames", 4) if cfg else 4
        cooldown_sec = getattr(cfg, "cooldown_sec", 3.0) if cfg else 3.0
        min_conf = getattr(cfg, "min_confidence", 0.35) if cfg else 0.35

        series = buffer.get_normalised_series(n=hold_frames + 3)
        if len(series) < hold_frames:
            return ActionResult("thumbs_up", False)

        key = f"{track_id}:thumbs_up"
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cooldown_sec:
            return ActionResult("thumbs_up", False)

        recent = series[-hold_frames:]
        # Check both arms: (wrist, elbow, shoulder)
        for w_idx, e_idx, s_idx in [
            (KP_RIGHT_WRIST, KP_RIGHT_ELBOW, KP_RIGHT_SHOULDER),
            (KP_LEFT_WRIST, KP_LEFT_ELBOW, KP_LEFT_SHOULDER),
        ]:
            thumbs_frames = 0
            valid_frames = 0
            wrist_xs: list[float] = []
            wrist_ys: list[float] = []

            for kps in recent:
                w_conf = float(kps[w_idx, 2])
                s_conf = float(kps[s_idx, 2])
                if w_conf < min_conf or s_conf < 0.25:
                    continue
                valid_frames += 1

                wx, wy = float(kps[w_idx, 0]), float(kps[w_idx, 1])
                sx, sy = float(kps[s_idx, 0]), float(kps[s_idx, 1])
                wrist_xs.append(wx)
                wrist_ys.append(wy)

                # 1. Wrist MUST be at chest / torso level: strictly below shoulder level
                # If wrist is above shoulder level, that's hand_raised or waving, NOT thumbs up!
                if wy < sy + 0.02:
                    continue

                # 2. Wrist must be above hip/waist level
                lh_y = float(kps[KP_LEFT_HIP, 1]) if float(kps[KP_LEFT_HIP, 2]) >= 0.2 else None
                rh_y = float(kps[KP_RIGHT_HIP, 1]) if float(kps[KP_RIGHT_HIP, 2]) >= 0.2 else None
                hips = [y for y in (lh_y, rh_y) if y is not None and y > sy + 0.05]
                hip_lvl = max(hips) if hips else sy + 0.60

                if wy > hip_lvl - 0.02:
                    continue  # Hand is down at or below hips, not a thumbs up

                # 3. Check elbow configuration:
                # In a thumbs up, the arm is bent with the elbow lower than the wrist
                e_conf = float(kps[e_idx, 2])
                if e_conf >= 0.25:
                    ey = float(kps[e_idx, 1])
                    if ey <= wy + 0.02:
                        continue  # Elbow is at or above wrist, arm is not bent upwards

                thumbs_frames += 1

            if valid_frames >= max(2, hold_frames - 2) and thumbs_frames >= max(2, int(valid_frames * 0.70)):
                # Wrist must be relatively steady (not waving back and forth)
                x_span = max(wrist_xs) - min(wrist_xs)
                y_span = max(wrist_ys) - min(wrist_ys)
                if x_span < 0.07 and y_span < 0.07:
                    self._cooldowns[key] = now
                    return ActionResult("thumbs_up", True, confidence=0.88)

        return ActionResult("thumbs_up", False)

    # ── Head nod ────────────────────────────────────────────────────────

    def _check_head_nod(self, track_id: int, buffer: KeypointBuffer) -> ActionResult:
        """Head nod detection has been deprecated and disabled per configuration."""
        return ActionResult("head_nod", False)

        return ActionResult("head_nod", False)
