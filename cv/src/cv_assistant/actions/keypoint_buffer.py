"""Per-track keypoint history buffer.

Maintains a rolling window of normalised keypoints per tracked person,
used by heuristic and ML-based action classifiers.
"""

from __future__ import annotations

import numpy as np
from collections import deque
from dataclasses import dataclass, field


@dataclass
class KeypointFrame:
    """One snapshot of normalised keypoints for a person."""

    keypoints: np.ndarray          # shape (17, 3): x, y, confidence
    timestamp: float               # time.monotonic()
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2


class KeypointBuffer:
    """Rolling buffer of keypoint frames for one tracked person.

    Keypoints are stored both raw and normalised (relative to the bounding
    box) to make gesture recognition invariant to distance from the camera.
    """

    def __init__(self, max_size: int = 30) -> None:
        self._buffer: deque[KeypointFrame] = deque(maxlen=max_size)

    def add(self, keypoints: np.ndarray, timestamp: float, bbox: tuple[float, float, float, float]) -> None:
        """Append a keypoint snapshot to the buffer."""
        self._buffer.append(KeypointFrame(
            keypoints=keypoints.copy(),
            timestamp=timestamp,
            bbox=bbox,
        ))

    def get_normalised_series(self, n: int | None = None) -> list[np.ndarray]:
        """Return the last *n* keypoint arrays normalised to [0, 1] within
        their bounding boxes.

        Each array has shape ``(17, 3)`` where columns are
        ``(norm_x, norm_y, confidence)``.
        """
        frames = list(self._buffer)
        if n is not None:
            frames = frames[-n:]
        result = []
        for kf in frames:
            x1, y1, x2, y2 = kf.bbox
            w = max(x2 - x1, 1.0)
            h = max(y2 - y1, 1.0)
            norm = kf.keypoints.copy()
            norm[:, 0] = (norm[:, 0] - x1) / w
            norm[:, 1] = (norm[:, 1] - y1) / h
            # Column 2 is confidence — keep as-is
            result.append(norm)
        return result

    def get_raw_series(self, n: int | None = None) -> list[np.ndarray]:
        """Return the last *n* raw keypoint arrays."""
        frames = list(self._buffer)
        if n is not None:
            frames = frames[-n:]
        return [kf.keypoints for kf in frames]

    def get_timestamps(self, n: int | None = None) -> list[float]:
        """Return timestamps of the last *n* frames."""
        frames = list(self._buffer)
        if n is not None:
            frames = frames[-n:]
        return [kf.timestamp for kf in frames]

    @property
    def length(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()
