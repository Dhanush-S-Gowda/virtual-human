"""Optional general-object detection module.

Provides a configurable object-detection stage for common objects (phone, cup,
book, chair, laptop, etc.) using a compatible YOLO model.  Supports both
rate-limited background polling and on-demand one-shot queries.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from pathlib import Path

from cv_assistant.config import settings
from cv_assistant.state import DetectedObject

logger = logging.getLogger(__name__)

# Default common object classes of interest from COCO
DEFAULT_CLASSES: set[str] = {
    "bottle",
    "cup",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "book",
    "clock",
    "vase",
    "scissors",
    "backpack",
    "handbag",
}


class ObjectDetector:
    """YOLO-based object detector for common objects.

    Parameters
    ----------
    model_path : str
        Model name or path, e.g. ``"yolov8n.pt"``.
    device : str
        ``"auto"``, ``"cuda"``, or ``"cpu"``.
    confidence : float
        Minimum detection confidence.
    classes : list[str] | None
        Allowed object class names. If empty/None, uses all detected classes
        or DEFAULT_CLASSES.
    """

    def __init__(
        self,
        model_path: str = "yolo26n.pt",
        device: str = "auto",
        confidence: float = 0.5,
        classes: list[str] | None = None,
    ) -> None:
        self._model_path = model_path
        self._confidence = confidence
        self._classes = set(classes) if classes else None
        self._model: Any = None

        # Resolve device
        if device == "auto":
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = device

    def _resolve_model_path(self, path: str) -> str:
        """Resolve model path across root, cv, and relative directories."""
        p = Path(path)
        if p.exists():
            return str(p.resolve())

        # Check project root and cv directories
        parents = list(Path(__file__).resolve().parents)
        candidates = [
            p,
            Path("cv") / p,
            parents[2] / p if len(parents) > 2 else p,
            parents[3] / p if len(parents) > 3 else p,
            parents[2] / "models" / p.name if len(parents) > 2 else p,
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())

        return path

    def initialize(self) -> None:
        """Load and warm up the YOLO object detection model."""
        from ultralytics import YOLO

        resolved = self._resolve_model_path(self._model_path)
        logger.info(
            "Loading YOLO object detection model '%s' (resolved: '%s') on device '%s'...",
            self._model_path,
            resolved,
            self._device,
        )
        self._model = YOLO(resolved)
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self._model.predict(
            dummy,
            device=self._device,
            verbose=False,
            conf=self._confidence,
        )
        logger.info("YOLO object detection model initialized.")

    def detect(self, frame: np.ndarray) -> list[DetectedObject]:
        """Detect objects in *frame*.

        Returns a list of ``DetectedObject`` with label, confidence, and bbox.
        """
        if self._model is None:
            raise RuntimeError("ObjectDetector not initialized. Call initialize() first.")

        results = self._model.predict(
            frame,
            device=self._device,
            verbose=False,
            conf=self._confidence,
        )

        detected_objects: list[DetectedObject] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue

            names = getattr(result, "names", {}) or {}

            if hasattr(boxes, "cls") and boxes.cls is not None:
                for i in range(len(boxes)):
                    cls_id = int(boxes.cls[i])
                    label = names.get(cls_id, f"class_{cls_id}")
                    if label == "person":
                        continue
                    if self._classes is not None and len(self._classes) > 0 and label not in self._classes:
                        continue
                    conf_val = boxes.conf[i]
                    conf = float(conf_val.cpu().numpy()) if hasattr(conf_val, "cpu") else float(conf_val)
                    xyxy_val = boxes.xyxy[i]
                    bbox = xyxy_val.cpu().numpy().tolist() if hasattr(xyxy_val, "cpu") else list(xyxy_val)
                    detected_objects.append(DetectedObject(label=label, confidence=conf, bbox=tuple(bbox)))
            else:
                for b in boxes:
                    cls_val = b.cls[0] if isinstance(b.cls, (list, tuple)) else b.cls
                    cls_id = int(cls_val)
                    label = names.get(cls_id, f"class_{cls_id}")
                    if label == "person":
                        continue
                    if self._classes is not None and len(self._classes) > 0 and label not in self._classes:
                        continue
                    conf_val = b.conf[0] if isinstance(b.conf, (list, tuple)) else b.conf
                    conf = float(conf_val.cpu().numpy()) if hasattr(conf_val, "cpu") else float(conf_val)
                    xyxy_val = b.xyxy[0] if isinstance(b.xyxy, (list, tuple)) else b.xyxy
                    bbox = xyxy_val.cpu().numpy().tolist() if hasattr(xyxy_val, "cpu") else list(xyxy_val)
                    detected_objects.append(DetectedObject(label=label, confidence=conf, bbox=tuple(bbox)))

        return detected_objects
