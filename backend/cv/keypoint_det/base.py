"""Providers infer points and temporal gestures using bounded capture history."""

from dataclasses import dataclass
from typing import Protocol

from ..events import Frame


@dataclass(frozen=True)
class KeypointDetection:
    track_id: int
    schema: str
    points: tuple[tuple[float, ...], ...]
    gestures: tuple[str, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    landmark_indices: tuple[int, ...] = ()
    mouth_openness: float | None = None
    mouth_motion: float | None = None


@dataclass(frozen=True)
class KeypointSample:
    frame_id: int
    timestamp: float
    detection: KeypointDetection


class KeypointDetectionProvider(Protocol):
    async def initialize(self) -> None: ...
    async def detect(
        self, frame: Frame, history: tuple[KeypointSample, ...]
    ) -> tuple[KeypointDetection, ...]: ...
