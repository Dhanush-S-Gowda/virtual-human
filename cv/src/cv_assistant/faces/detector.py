"""Face detection within tracked person bounding boxes.

Uses InsightFace for face detection and landmark extraction.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class FaceDetector:
    """Detect faces within person bounding box crops using InsightFace.

    Parameters
    ----------
    model_name : str
        InsightFace model pack name (e.g. ``"buffalo_l"``).
    detection_threshold : float
        Minimum face detection confidence.
    """

    def __init__(
        self,
        model_name: str = "buffalo_s",
        detection_threshold: float = 0.5,
    ) -> None:
        self._model_name = model_name
        self._detection_threshold = detection_threshold
        self._app: Any = None

    def initialize(self) -> None:
        """Load the InsightFace model pack."""
        from insightface.app import FaceAnalysis

        logger.info("Loading InsightFace model pack '%s'...", self._model_name)
        self._app = FaceAnalysis(
            name=self._model_name,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            allowed_modules=["detection", "recognition"],
        )
        self._app.prepare(ctx_id=0, det_thresh=self._detection_threshold, det_size=(640, 640))
        logger.info("InsightFace model loaded.")

    def detect_faces(
        self,
        frame: np.ndarray,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Detect faces in *frame*, optionally cropped to *bbox*.

        Returns a list of dicts with keys:
        - ``bbox``: face bounding box (x1, y1, x2, y2)
        - ``score``: detection confidence
        - ``embedding``: 512-d face embedding (numpy array)
        - ``landmarks``: facial landmarks if available
        - ``age``, ``gender``: if the model provides them
        """
        if self._app is None:
            raise RuntimeError("FaceDetector not initialized.")

        if bbox is not None:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            h, w = frame.shape[:2]
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)
            # Add padding around the body bbox for face detection
            pad_x = int((x2 - x1) * 0.1)
            pad_y = int((y2 - y1) * 0.1)
            x1 = max(0, x1 - pad_x)
            y1 = max(0, y1 - pad_y)
            x2 = min(w, x2 + pad_x)
            y2 = min(h, y2 + pad_y)
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                return []
        else:
            crop = frame
            x1, y1 = 0, 0

        faces = self._app.get(crop)
        results = []
        for face in faces:
            face_bbox = face.bbox.tolist()
            # Offset back to full-frame coordinates
            face_bbox[0] += x1
            face_bbox[1] += y1
            face_bbox[2] += x1
            face_bbox[3] += y1

            result = {
                "bbox": tuple(face_bbox),
                "score": float(face.det_score),
                "embedding": face.normed_embedding if hasattr(face, "normed_embedding") else face.embedding,
                "landmarks": face.landmark_2d_106 if hasattr(face, "landmark_2d_106") else None,
            }
            if hasattr(face, "age"):
                result["age"] = face.age
            if hasattr(face, "gender"):
                result["gender"] = face.gender
            results.append(result)

        return results
