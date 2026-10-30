"""Person detection and pose estimation using YOLO.

Wraps Ultralytics YOLO for single-pass person detection with body keypoints.
The detector is loaded once and reused across frames.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# COCO keypoint indices used by YOLO-pose
KP_NOSE = 0
KP_LEFT_EYE = 1
KP_RIGHT_EYE = 2
KP_LEFT_EAR = 3
KP_RIGHT_EAR = 4
KP_LEFT_SHOULDER = 5
KP_RIGHT_SHOULDER = 6
KP_LEFT_ELBOW = 7
KP_RIGHT_ELBOW = 8
KP_LEFT_WRIST = 9
KP_RIGHT_WRIST = 10
KP_LEFT_HIP = 11
KP_RIGHT_HIP = 12
KP_LEFT_KNEE = 13
KP_RIGHT_KNEE = 14
KP_LEFT_ANKLE = 15
KP_RIGHT_ANKLE = 16


@dataclass
class PersonDetection:
    """One detected person in a single frame."""

    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    confidence: float
    keypoints: np.ndarray | None = None  # shape (17, 3) — x, y, conf
    track_id: int | None = None  # Filled by tracker


class PersonDetector:
    """YOLO-pose detector for people and body keypoints.

    Parameters
    ----------
    model_path : str
        Path or name of the YOLO pose model (e.g. ``"yolov8n-pose.pt"``).
    device : str
        ``"auto"``, ``"cuda"``, or ``"cpu"``.
    confidence : float
        Minimum detection confidence.
    iou_threshold : float
        Non-maximum suppression IoU threshold.
    """

    def __init__(
        self,
        model_path: str = "yolov8n-pose.pt",
        device: str = "auto",
        confidence: float = 0.5,
        iou_threshold: float = 0.45,
    ) -> None:
        self._model_path = model_path
        self._confidence = confidence
        self._iou_threshold = iou_threshold
        self._model: Any = None

        # Resolve device
        if device == "auto":
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = device

    def initialize(self) -> None:
        """Load the YOLO model.  Call once before ``detect``."""
        from ultralytics import YOLO

        logger.info(
            "Loading YOLO pose model '%s' on device '%s'...",
            self._model_path, self._device,
        )
        self._model = YOLO(self._model_path)
        # Warm-up with a dummy image
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self._model.predict(
            dummy, device=self._device, verbose=False,
            conf=self._confidence, iou=self._iou_threshold,
        )
        logger.info("YOLO pose model loaded and warmed up.")

    def detect(self, frame: np.ndarray) -> list[PersonDetection]:
        """Detect people and keypoints in *frame*.

        Returns a list of ``PersonDetection`` objects (no tracking applied).
        """
        if self._model is None:
            raise RuntimeError("Detector not initialized. Call initialize() first.")

        results = self._model.predict(
            frame,
            device=self._device,
            verbose=False,
            conf=self._confidence,
            iou=self._iou_threshold,
        )

        detections: list[PersonDetection] = []
        for result in results:
            boxes = result.boxes
            keypoints_data = result.keypoints

            if boxes is None:
                continue

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i]) if boxes.cls is not None else -1
                # YOLO-pose only detects people (class 0) but check anyway
                if cls_id != 0:
                    continue

                x1, y1, x2, y2 = boxes.xyxy[i].cpu().numpy().tolist()
                conf = float(boxes.conf[i].cpu().numpy())

                kps = None
                if keypoints_data is not None and i < len(keypoints_data):
                    kps_raw = keypoints_data[i]
                    if hasattr(kps_raw, 'data'):
                        kps = kps_raw.data[0].cpu().numpy()  # shape (17, 3)
                    elif hasattr(kps_raw, 'xy'):
                        xy = kps_raw.xy[0].cpu().numpy()  # (17, 2)
                        conf_arr = kps_raw.conf[0].cpu().numpy() if hasattr(kps_raw, 'conf') else np.ones(17)
                        kps = np.column_stack([xy, conf_arr])

                detections.append(PersonDetection(
                    bbox=(x1, y1, x2, y2),
                    confidence=conf,
                    keypoints=kps,
                ))

        return detections

    def detect_with_tracking(self, frame: np.ndarray, tracker_config: str = "bytetrack.yaml") -> list[PersonDetection]:
        """Detect + track people using YOLO's built-in tracker.

        Returns detections with ``track_id`` populated.
        """
        if self._model is None:
            raise RuntimeError("Detector not initialized. Call initialize() first.")

        results = self._model.track(
            frame,
            device=self._device,
            verbose=False,
            conf=self._confidence,
            iou=self._iou_threshold,
            tracker=tracker_config,
            persist=True,
        )

        detections: list[PersonDetection] = []
        for result in results:
            boxes = result.boxes
            keypoints_data = result.keypoints

            if boxes is None:
                continue

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i]) if boxes.cls is not None else -1
                if cls_id != 0:
                    continue

                x1, y1, x2, y2 = boxes.xyxy[i].cpu().numpy().tolist()
                conf = float(boxes.conf[i].cpu().numpy())

                tid = None
                if boxes.id is not None and i < len(boxes.id):
                    tid = int(boxes.id[i].cpu().numpy())

                kps = None
                if keypoints_data is not None and i < len(keypoints_data):
                    kps_raw = keypoints_data[i]
                    if hasattr(kps_raw, 'data'):
                        kps = kps_raw.data[0].cpu().numpy()
                    elif hasattr(kps_raw, 'xy'):
                        xy = kps_raw.xy[0].cpu().numpy()
                        conf_arr = kps_raw.conf[0].cpu().numpy() if hasattr(kps_raw, 'conf') else np.ones(17)
                        kps = np.column_stack([xy, conf_arr])

                detections.append(PersonDetection(
                    bbox=(x1, y1, x2, y2),
                    confidence=conf,
                    keypoints=kps,
                    track_id=tid,
                ))

        return detections
