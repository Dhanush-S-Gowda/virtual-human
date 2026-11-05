"""Named MediaPipe Tasks assets, downloaded atomically from Google's registry."""

import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen
import zipfile


_BASE = "https://storage.googleapis.com/mediapipe-models"
MODEL_URLS = {
    "pose_landmarker_lite": f"{_BASE}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "pose_landmarker_full": f"{_BASE}/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "pose_landmarker_heavy": f"{_BASE}/pose_landmarker/pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task",
    "face_landmarker": f"{_BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "hand_landmarker": f"{_BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
}


def model_path(name, cache_dir=None):
    if name not in MODEL_URLS:
        raise ValueError(f"Unknown MediaPipe model {name!r}; choose from {', '.join(MODEL_URLS)}")
    root = Path(cache_dir) if cache_dir is not None else Path(__file__).resolve().parents[2] / "assets/cv/mediapipe"
    return root / f"{name}.task"


def model_is_valid(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return bool(archive.namelist()) and archive.testzip() is None
    except (OSError, zipfile.BadZipFile, EOFError):
        return False


def ensure_model(name, cache_dir=None):
    """Reuse a valid asset; a failed download never replaces an existing file."""
    destination = model_path(name, cache_dir)
    if model_is_valid(destination):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".partial", delete=False) as output:
            temporary = Path(output.name)
            with urlopen(MODEL_URLS[name], timeout=60) as response:
                shutil.copyfileobj(response, output)
        if not model_is_valid(temporary):
            raise ValueError(f"Downloaded MediaPipe model {name!r} is not a valid task bundle")
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def configured_models(settings):
    cfg = settings.cv.keypoints
    names = (cfg.pose_model, cfg.face_model, cfg.hand_model)
    if not cfg.pose_model.startswith("pose_landmarker_") or cfg.face_model != "face_landmarker" or cfg.hand_model != "hand_landmarker":
        raise ValueError("Select a pose_landmarker variant, face_landmarker, and hand_landmarker for their matching tasks")
    for name in names:
        model_path(name)  # Validate names before starting downloads.
    return names
