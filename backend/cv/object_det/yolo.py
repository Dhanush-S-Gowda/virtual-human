"""Ultralytics detection and persistent instance tracking for all classes."""

import asyncio
import re

from .base import ObjectDetection

from backend.diagnostics import traced_stage
from backend.runtime import ModelWorker


def detection_model_name(name):
    """Accept named detection weights, never a filesystem path or pose model."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.pt)?", name):
        raise ValueError("cv.detection.model must be an Ultralytics model name, such as yolov8n")
    if any(suffix in name.lower() for suffix in ("-pose", "-seg", "-cls", "-obb")):
        raise ValueError("cv.detection.model must select object detection, not pose or another task")
    return name


class YOLOProvider:
    def __init__(self, settings):
        self.settings = settings
        self._model = None
        self._worker = ModelWorker(settings, "objects")

    async def initialize(self):
        await self._worker.run(self._initialize)

    def _initialize(self):
        name = detection_model_name(self.settings.cv.detection.model)
        from ultralytics import YOLO
        import torch
        torch.set_num_threads(self._worker.threads)
        self._model = YOLO(name, task="detect")
        if self._model.task != "detect":
            raise ValueError(f"Ultralytics model {name!r} is not an object detection model")

    async def detect(self, frame):
        return await self._worker.run(self._detect, frame)

    @traced_stage("cv.object_detection")
    def _detect(self, frame):
        if self._model is None:
            raise RuntimeError("Object provider is not initialized")
        cfg = self.settings.cv
        results = self._model.track(
            frame.image, persist=True, tracker=cfg.tracking.tracker,
            device=None if cfg.detection.device == "auto" else cfg.detection.device,
            conf=cfg.detection.confidence, iou=cfg.detection.iou_threshold, verbose=False,
        )
        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is None or boxes.id is None:
                continue
            # Tracking IDs, not labels, define unique instances.
            for coords, confidence, category, track_id in zip(
                boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(),
                boxes.cls.cpu().tolist(), boxes.id.cpu().tolist(),
            ):
                label = result.names[int(category)]
                if cfg.detection.classes and label not in cfg.detection.classes and label != "person":
                    continue
                detections.append(ObjectDetection(
                    int(track_id), label, float(confidence), tuple(float(x) for x in coords),
                ))
        return tuple(detections)

    async def reset(self):
        if self._model is not None:
            predictor = getattr(self._model, "predictor", None)
            for tracker in getattr(predictor, "trackers", ()):
                tracker.reset()

    async def close(self):
        self._model = None
        await self._worker.close()
