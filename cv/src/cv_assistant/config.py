"""Centralized configuration loader.

Loads settings from a YAML file and provides typed access.  Supports
environment variable override for the config file path via ``CV_CONFIG_PATH``.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ── Default values ──────────────────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    "camera": {
        "source": "frontend",
        "device_index": 0,
        "width": 640,
        "height": 480,
        "fps": 30,
        "camera_id": "default",
        "frame_queue_size": 2,
    },
    "detection": {
        "model": "yolov8n-pose.pt",
        "device": "auto",
        "target_fps": 12,
        "confidence": 0.5,
        "iou_threshold": 0.45,
    },
    "tracking": {
        "tracker": "bytetrack.yaml",
        "track_buffer": 30,
        "enter_min_frames": 3,
        "leave_timeout_sec": 3.0,
    },
    "actions": {
        "use_ml_model": True,
        "model_path": "models/hand_landmarker.task",
        "buffer_size": 30,
        "waving": {
            "min_wrist_above_shoulder": True,
            "min_oscillations": 2,
            "window_frames": 20,
            "cooldown_sec": 3.0,
            "min_confidence": 0.30,
        },
        "hand_raised": {
            "min_wrist_above_head_ratio": 0.85,
            "min_hold_frames": 5,
            "cooldown_sec": 3.0,
            "min_confidence": 0.4,
        },
        "thumbs_up": {
            "min_hold_frames": 4,
            "cooldown_sec": 3.0,
            "min_confidence": 0.35,
        },
        "namaste": {
            "max_wrist_distance": 0.12,
            "min_hold_frames": 4,
            "cooldown_sec": 3.0,
            "min_confidence": 0.35,
        },
    },
    "face": {
        "enabled": True,
        "model_name": "buffalo_s",
        "embedding_model": "w600k_mbf",
        "detection_threshold": 0.5,
        "embedding_interval_sec": 2.0,
        "recheck_interval_sec": 30.0,
        "quality": {
            "min_face_width": 80,
            "min_detection_score": 0.6,
            "max_embeddings_per_person": 10,
            "min_quality_for_update": 0.7,
        },
        "matching": {
            "threshold": 0.45,
            "ambiguity_margin": 0.05,
        },
    },
    "identity": {
        "database_path": "data/identities.db",
        "auto_enroll": True,
        "consent_required": True,
    },
    "objects": {
        "enabled": True,
        "model": "yolo26n.pt",
        "target_fps": 5,
        "confidence": 0.5,
        "classes": [],
    },
    "speaker": {
        "name_patterns": [
            r"(?:my name is|i am|i'm|call me|they call me)\s+(\w+)",
        ],
        "single_person_confidence": 0.7,
        "mouth_motion_weight": 0.6,
        "ambiguity_threshold": 0.3,
    },
    "integration": {
        "websocket_host": "0.0.0.0",
        "websocket_port": 8765,
        "event_cooldown_sec": 1.0,
    },
    "logging": {
        "level": "INFO",
    },
}


def _dict_to_namespace(d: dict) -> SimpleNamespace:
    """Recursively convert a dict to a nested SimpleNamespace."""
    ns = SimpleNamespace()
    for key, value in d.items():
        if isinstance(value, dict):
            setattr(ns, key, _dict_to_namespace(value))
        else:
            setattr(ns, key, value)
    return ns


def load_config(path: str | Path | None = None) -> SimpleNamespace:
    """Load configuration from *path*, falling back to defaults.

    If *path* is ``None``, the ``CV_CONFIG_PATH`` environment variable is
    checked, followed by ``config/config.yaml`` relative to the ``cv/``
    project root.
    """
    if path is None:
        path = Path(os.environ.get("CV_CONFIG_PATH", _DEFAULT_CONFIG_PATH))
    else:
        path = Path(path)

    user_config: dict[str, Any] = {}
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            user_config = yaml.safe_load(fh) or {}

    merged = _deep_merge(DEFAULTS, user_config)
    return _dict_to_namespace(merged)


# Module-level singleton loaded on first import.
settings = load_config()
