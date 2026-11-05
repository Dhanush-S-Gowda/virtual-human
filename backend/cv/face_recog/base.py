"""Identity providers receive the original frame and its new person track."""

from dataclasses import dataclass
from typing import Protocol

from ..events import Frame
from ..object_det.base import ObjectDetection


@dataclass(frozen=True)
class FaceRecognition:
    track_id: int
    person_id: str | None = None
    display_name: str | None = None
    confidence: float = 0.0
    needs_retry: bool = False


class FaceRecognitionProvider(Protocol):
    async def initialize(self) -> None: ...
    async def recognize(self, frame: Frame, person: ObjectDetection) -> FaceRecognition: ...
