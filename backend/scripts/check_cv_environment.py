"""Report CV packages and managed assets without downloading or opening a camera."""

import importlib.util
import json

from backend.config import settings
from backend.cv.face_recog.detector import model_root
from backend.cv.keypoint_det.models import configured_models, model_is_valid, model_path
from backend.cv.object_det.yolo import detection_model_name


def main():
    modules = ("ultralytics", "cv2", "insightface", "mediapipe", "websockets")
    pack = model_root() / "models" / settings.cv.face.model_name
    report = {
        "packages": {name: importlib.util.find_spec(name) is not None for name in modules},
        "ultralytics_model": detection_model_name(settings.cv.detection.model),
        "mediapipe_models": {name: model_is_valid(model_path(name)) for name in configured_models(settings)},
        "insightface_model": {"name": settings.cv.face.model_name, "cached_onnx_files": len(list(pack.glob("*.onnx")))},
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
