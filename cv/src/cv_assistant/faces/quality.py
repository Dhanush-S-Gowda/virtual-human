"""Face quality assessment.

Filters out low-quality face observations before they reach enrollment
or matching.  Quality gates include detection score, face crop size,
and optionally blur/pose estimation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from cv_assistant.config import settings

logger = logging.getLogger(__name__)


@dataclass
class QualityResult:
    """Assessment of a face observation's quality."""

    passed: bool
    score: float  # Composite quality score in [0, 1]
    reasons: list[str]  # Why it failed, if applicable


def assess_quality(
    face: dict[str, Any],
    frame_shape: tuple[int, ...] | None = None,
) -> QualityResult:
    """Evaluate whether a detected face meets quality requirements.

    Parameters
    ----------
    face : dict
        Face detection result with ``bbox``, ``score``, ``embedding``.
    frame_shape : tuple, optional
        ``(height, width, channels)`` of the source frame.
    """
    reasons: list[str] = []
    cfg = settings.face.quality
    scores: list[float] = []

    # 1. Detection score
    det_score = face.get("score", 0.0)
    if det_score < cfg.min_detection_score:
        reasons.append(f"detection_score={det_score:.2f} < {cfg.min_detection_score}")
    scores.append(min(det_score / cfg.min_detection_score, 1.0))

    # 2. Face crop size
    bbox = face.get("bbox")
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        face_width = abs(x2 - x1)
        face_height = abs(y2 - y1)
        if face_width < cfg.min_face_width:
            reasons.append(f"face_width={face_width:.0f}px < {cfg.min_face_width}px")
        size_score = min(face_width / cfg.min_face_width, 1.0)
        scores.append(size_score)
    else:
        reasons.append("no_bbox")
        scores.append(0.0)

    # 3. Embedding validity
    embedding = face.get("embedding")
    if embedding is None:
        reasons.append("no_embedding")
        scores.append(0.0)
    else:
        emb = np.asarray(embedding)
        norm = np.linalg.norm(emb)
        if norm < 0.5:
            reasons.append(f"embedding_norm={norm:.2f} (suspicious)")
            scores.append(0.5)
        else:
            scores.append(1.0)

    composite = sum(scores) / max(len(scores), 1)
    passed = len(reasons) == 0

    return QualityResult(passed=passed, score=composite, reasons=reasons)
