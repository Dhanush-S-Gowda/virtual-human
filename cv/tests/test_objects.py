"""Unit tests for the optional object detection module."""

from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from cv_assistant.objects import ObjectDetector, DEFAULT_CLASSES
from cv_assistant.state import DetectedObject, SceneState
from cv_assistant.integration.virtual_human_adapter import VirtualHumanAdapter


def test_object_detector_device_resolution():
    detector = ObjectDetector(device="cpu")
    assert detector._device == "cpu"

    detector_cuda = ObjectDetector(device="cuda")
    assert detector_cuda._device == "cuda"


def test_object_detector_class_filtering():
    detector = ObjectDetector(device="cpu", classes=["cup", "laptop"])
    assert detector._classes == {"cup", "laptop"}


def test_object_detector_mocked_inference():
    detector = ObjectDetector(device="cpu", classes=["cup", "laptop"], confidence=0.5)

    # Mock ultralytics YOLO model
    mock_box1 = MagicMock()
    mock_box1.cls = [0]  # person
    mock_box1.conf = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 0.9))]
    mock_box1.xyxy = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: MagicMock(tolist=lambda: [0, 0, 50, 50])))]

    mock_box2 = MagicMock()
    mock_box2.cls = [1]  # cup
    mock_box2.conf = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 0.85))]
    mock_box2.xyxy = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: MagicMock(tolist=lambda: [10, 10, 30, 30])))]

    mock_box3 = MagicMock()
    mock_box3.cls = [2]  # car (not in allowed classes)
    mock_box3.conf = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: 0.75))]
    mock_box3.xyxy = [MagicMock(cpu=lambda: MagicMock(numpy=lambda: MagicMock(tolist=lambda: [40, 40, 80, 80])))]

    mock_result = MagicMock()
    mock_result.boxes = [mock_box1, mock_box2, mock_box3]
    mock_result.names = {0: "person", 1: "cup", 2: "car"}

    mock_yolo = MagicMock()
    mock_yolo.predict.return_value = [mock_result]
    detector._model = mock_yolo

    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    detected = detector.detect(dummy_frame)

    # 'person' should be skipped, 'car' should be filtered out -> only 'cup' remains
    assert len(detected) == 1
    assert detected[0].label == "cup"
    assert detected[0].confidence == 0.85
    assert detected[0].bbox == (10, 10, 30, 30)


def test_adapter_detect_objects_oneshot():
    scene = SceneState()
    scene.update_objects([
        DetectedObject(label="book", confidence=0.88, bbox=(5, 5, 25, 25))
    ])

    mock_oneshot = MagicMock(return_value=[{"label": "laptop", "confidence": 0.95}])
    adapter = VirtualHumanAdapter(scene=scene, on_detect_objects=mock_oneshot)

    res = adapter.detect_objects_oneshot()
    assert len(res) == 1
    assert res[0]["label"] == "laptop"
    mock_oneshot.assert_called_once()
