"""Fetch configured CV models; named providers reuse their managed caches."""

from backend.config import settings
from backend.cv.face_recog.detector import FaceDetector
from backend.cv.keypoint_det.models import configured_models, ensure_model
from backend.cv.keypoint_det.mediapipe import MediaPipeProvider
from backend.cv.object_det.yolo import YOLOProvider


def main():
    print(f"Preparing Ultralytics detection model: {settings.cv.detection.model}")
    YOLOProvider(settings)._initialize()
    for name in configured_models(settings):
        print(f"Preparing MediaPipe model: {name}")
        ensure_model(name)
    landmarker = MediaPipeProvider(settings)
    try:
        landmarker._initialize()
    finally:
        landmarker._close()
    if settings.cv.face.enabled:
        print(f"Preparing InsightFace model pack: {settings.cv.face.model_name}")
        FaceDetector(settings.cv.face.model_name, settings.cv.face.detection_threshold).initialize()
    print("Configured CV models are available.")


if __name__ == "__main__":
    main()
