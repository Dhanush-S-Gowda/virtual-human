"""Named providers, cache reuse, atomic downloads, and setup orchestration."""

from io import BytesIO
from pathlib import Path
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import pytest

from backend.config import settings
from backend.cv.keypoint_det import models
from backend.cv.object_det.yolo import YOLOProvider, detection_model_name


def bundle():
    data = BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("landmarker.tflite", b"test fixture model")
    return data.getvalue()


def test_ultralytics_receives_model_name_not_a_path(monkeypatch):
    factory = MagicMock(return_value=SimpleNamespace(task="detect"))
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=factory))
    YOLOProvider(settings)._initialize()
    factory.assert_called_once_with(settings.cv.detection.model, task="detect")


@pytest.mark.parametrize("name", ["models/yolov8n.pt", "/tmp/yolov8n.pt", "yolov8n-pose", "yolov8n-seg.pt"])
def test_paths_and_non_detection_models_rejected(name):
    with pytest.raises(ValueError):
        detection_model_name(name)


def test_mediapipe_download_is_atomic_and_reuses_valid_bundle(tmp_path, monkeypatch):
    download = MagicMock(side_effect=lambda *args, **kwargs: BytesIO(bundle()))
    monkeypatch.setattr(models, "urlopen", download)
    first = models.ensure_model("face_landmarker", tmp_path)
    assert first.is_file() and models.model_is_valid(first)
    assert models.ensure_model("face_landmarker", tmp_path) == first
    assert download.call_count == 1
    assert not list(tmp_path.glob("*.partial"))


def test_failed_download_does_not_install_a_partial_model(tmp_path, monkeypatch):
    destination = models.model_path("hand_landmarker", tmp_path)
    destination.write_bytes(b"invalid old bundle")
    monkeypatch.setattr(models, "urlopen", lambda *args, **kwargs: BytesIO(b"not a model"))
    with pytest.raises(ValueError, match="task bundle"):
        models.ensure_model("hand_landmarker", tmp_path)
    assert destination.read_bytes() == b"invalid old bundle"
    assert not list(tmp_path.glob("*.partial"))


def test_unknown_mediapipe_model_does_not_download(tmp_path, monkeypatch):
    download = MagicMock()
    monkeypatch.setattr(models, "urlopen", download)
    with pytest.raises(ValueError):
        models.ensure_model("../../unknown", tmp_path)
    download.assert_not_called()


def test_setup_helper_uses_configured_names(monkeypatch):
    from backend.scripts import setup_cv_models
    yolo, face, download = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(setup_cv_models, "YOLOProvider", yolo)
    monkeypatch.setattr(setup_cv_models, "FaceDetector", face)
    monkeypatch.setattr(setup_cv_models, "ensure_model", download)
    keypoints = MagicMock()
    monkeypatch.setattr(setup_cv_models, "MediaPipeProvider", keypoints)
    setup_cv_models.main()
    yolo.assert_called_once_with(settings)
    assert [call.args[0] for call in download.call_args_list] == [
        "pose_landmarker_lite", "face_landmarker", "hand_landmarker",
    ]
    face.assert_called_once_with("buffalo_s", settings.cv.face.detection_threshold)
    face.return_value.initialize.assert_called_once()
    keypoints.return_value._initialize.assert_called_once()
    keypoints.return_value._close.assert_called_once()


def test_setup_shell_syncs_cv_and_invokes_helper_from_repository_root(tmp_path):
    root = Path(__file__).resolve().parents[3]
    commands = tmp_path / "commands"
    for command in ("uv", "docker"):
        executable = tmp_path / command
        executable.write_text('#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$*" >> "$COMMAND_LOG"\n')
        executable.chmod(0o755)
    environment = {**os.environ, "PATH": str(tmp_path) + ":/usr/bin:/bin", "COMMAND_LOG": str(commands)}
    subprocess.run(["bash", str(root / "backend/setup.sh")], env=environment,
                   cwd=tmp_path, check=True, capture_output=True, text=True)
    calls = commands.read_text()
    assert "sync" in calls
    assert "--extra cv" not in calls
    assert f"{root}|run --project {root / 'backend'} --no-sync --offline --no-python-downloads python -m backend.scripts.setup_models" in calls
    assert "yolov8n-pose" not in calls


def test_insightface_uses_named_pack_and_managed_cache(monkeypatch):
    from backend.cv.face_recog.detector import FaceDetector, model_root
    application = MagicMock()
    application.models = {"detection": object(), "recognition": SimpleNamespace(model_file="w600k_mbf.onnx")}
    factory = MagicMock(return_value=application)
    monkeypatch.setitem(sys.modules, "insightface", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "insightface.app", SimpleNamespace(FaceAnalysis=factory))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]))
    detector = FaceDetector("buffalo_s")
    detector.initialize()
    assert factory.call_args.kwargs["name"] == "buffalo_s"
    assert factory.call_args.kwargs["root"] == str(model_root())
    assert factory.call_args.kwargs["allowed_modules"] == ["detection", "recognition"]
    assert detector.embedding_model == "w600k_mbf"


def test_setup_flags_match_the_project_manifest():
    import tomllib

    root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((root / "backend/pyproject.toml").read_text())
    dependencies = manifest["project"]["dependencies"]
    for package in ("ultralytics", "mediapipe", "insightface"):
        assert any(dependency.startswith(package + ">=") for dependency in dependencies)
    assert "dev" in manifest["project"]["optional-dependencies"]
    assert "dev" not in manifest["project"]
    assert "--extra cv" not in (root / "backend/setup.sh").read_text()
