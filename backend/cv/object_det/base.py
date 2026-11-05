"""Object detection providers own instance tracking, including person tracks."""

from dataclasses import dataclass
from typing import Protocol

from ..events import Frame


@dataclass(frozen=True)
class ObjectDetection:
    track_id: int
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]


class ObjectDetectionProvider(Protocol):
    async def initialize(self) -> None: ...
    async def detect(self, frame: Frame) -> tuple[ObjectDetection, ...]: ...
