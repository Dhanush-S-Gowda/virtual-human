"""Conservative hand gestures from capture-time history, without model imports.

Only open_palm, thumbs_up, and wave are emitted. A wave needs cyclic lateral
motion, not merely an open hand. Evidence is per person AND per handedness.
"""

from math import hypot, isfinite
from statistics import median


_FINGERS = ((5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20))


def _distance(first, second):
    return hypot(first[0] - second[0], first[1] - second[1])


def hand_shape(points):
    if len(points) != 21 or any(not isfinite(value) for point in points for value in point):
        return None
    wrist = points[0]
    palm = _distance(points[5], points[17])
    if palm <= 1e-6:
        return None
    extended, curled = [], []
    for base, joint, tip in _FINGERS:
        extended.append(_distance(points[tip], wrist) > _distance(points[joint], wrist) + .15 * palm
                        and _distance(points[tip], points[base]) > .55 * palm)
        curled.append(_distance(points[tip], wrist) < _distance(points[joint], wrist) + .05 * palm
                      and _distance(points[tip], points[base]) < .6 * palm)
    thumb_up = (points[4][1] < points[3][1] < points[2][1]
                and points[2][1] - points[4][1] > .5 * palm
                and points[2][1] - points[4][1] > abs(points[4][0] - points[2][0]))
    if all(curled) and thumb_up:
        return "thumbs_up"
    # Waving rotates the fingers through different image orientations.
    if all(extended) and _distance(points[4], points[5]) > .6 * palm:
        return "open_palm"
    return None


class GestureClassifier:
    def __init__(self, settings):
        self.cfg = settings.cv.gestures
        cfg = self.cfg
        if (not isfinite(cfg.confirmation_seconds) or cfg.confirmation_seconds <= 0
                or cfg.min_frames < 2 or not isfinite(cfg.max_gap_seconds) or cfg.max_gap_seconds <= 0
                or cfg.wave_min_reversals < 2 or cfg.wave_min_amplitude_palms <= 0
                or cfg.stationary_max_motion_palms <= 0):
            raise ValueError("Gesture confirmation needs positive duration/gap and multiple frames/reversals")
        if settings.cv_keypoint_history_seconds < cfg.confirmation_seconds + cfg.max_gap_seconds:
            raise ValueError("Keypoint history must exceed gesture confirmation duration plus maximum gap")

    def classify(self, current, timestamp, history):
        """Return only gestures supported by a continuous, recent evidence span."""
        shape = hand_shape(current.points)
        if shape is None or current.bbox is None:
            return ()
        evidence = [(timestamp, current)]
        previous_time = timestamp
        samples = sorted((sample for sample in history
                          if sample.detection.track_id == current.track_id
                          and sample.detection.schema == current.schema
                          and sample.timestamp < timestamp), key=lambda sample: sample.timestamp, reverse=True)
        for sample in samples:
            if previous_time - sample.timestamp > self.cfg.max_gap_seconds:
                break
            previous_shape = hand_shape(sample.detection.points)
            if previous_shape is None:
                # Brief uncertain observations add no evidence; the gap from
                # the last valid observation still bounds how far we can skip.
                continue
            if sample.detection.bbox is None or previous_shape != shape:
                break
            evidence.append((sample.timestamp, sample.detection))
            previous_time = sample.timestamp
            if timestamp - sample.timestamp + 1e-9 >= self.cfg.confirmation_seconds:
                break
        if (len(evidence) < self.cfg.min_frames
                or timestamp - evidence[-1][0] + 1e-9 < self.cfg.confirmation_seconds):
            return ()
        if shape == "thumbs_up":
            return ("thumbs_up",)
        evidence.reverse()
        # Compare hand motion relative to the tracked body box, so walking across
        # the image with a fixed hand is not itself a wave.
        trajectory, palms = [], []
        for sample_time, detection in evidence:
            x1, y1, x2, y2 = detection.bbox
            width = max(x2 - x1, 1.)
            # Finger motion captures a wrist-pivot wave as well as arm motion.
            tips = [detection.points[index] for index in (8, 12, 16, 20)]
            center = tuple(sum(point[axis] for point in tips) / len(tips) for axis in (0, 1))
            trajectory.append((sample_time, (center[0] - (x1 + x2) / 2) / width,
                               (center[1] - y1) / width))
            palms.append(_distance(detection.points[5], detection.points[17]) / width)
        palm = median(palms)
        xs, ys = [point[1] for point in trajectory], [point[2] for point in trajectory]
        motion = hypot(max(xs) - min(xs), max(ys) - min(ys))
        if motion <= self.cfg.stationary_max_motion_palms * palm:
            return ("open_palm",)
        if max(xs) - min(xs) < self.cfg.wave_min_amplitude_palms * palm:
            return ()
        # Hysteresis rejects tiny jitter rather than counting every derivative
        # sign change as a wave reversal.
        threshold = max(palm * .25, 1e-6)
        direction, extreme = 0, trajectory[0][1]
        turns = []
        for sample_time, x, _ in trajectory[1:]:
            if direction == 0:
                if abs(x - extreme) >= threshold:
                    direction = 1 if x > extreme else -1
                    extreme = x
            elif direction > 0:
                if x > extreme:
                    extreme = x
                elif extreme - x >= threshold:
                    turns.append(sample_time)
                    direction, extreme = -1, x
            else:
                if x < extreme:
                    extreme = x
                elif x - extreme >= threshold:
                    turns.append(sample_time)
                    direction, extreme = 1, x
        if (len(turns) >= self.cfg.wave_min_reversals
                and turns[-1] - turns[0] >= self.cfg.confirmation_seconds / 2
                and timestamp - turns[-1] <= self.cfg.max_gap_seconds * 2
                and max(ys) - min(ys) <= palm * 2):
            return ("wave",)
        return ()
