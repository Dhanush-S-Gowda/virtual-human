"""Optional real-model smoke check after running backend/setup.sh."""

import importlib.util
import numpy as np
import pytest

from backend.config import settings
from backend.cv.keypoint_det.mediapipe import MediaPipeProvider
from backend.cv.keypoint_det.models import configured_models, model_is_valid, model_path


_ready = importlib.util.find_spec("mediapipe") is not None and all(
    model_is_valid(model_path(name)) for name in configured_models(settings)
)


@pytest.mark.skipif(not _ready, reason="Run backend/setup.sh to install MediaPipe and all three task assets")
@pytest.mark.asyncio
async def test_body_face_and_hand_models_initialize_and_process_an_image():
    provider = MediaPipeProvider(settings)
    try:
        await provider.initialize()
        image = provider._image(np.zeros((256, 256, 3), dtype=np.uint8))
        assert provider._pose.detect_for_video(image, 0).pose_landmarks == []
        assert provider._face.detect_for_video(image, 0).face_landmarks == []
        assert provider._hands.detect_for_video(image, 0).hand_landmarks == []
    finally:
        await provider.close()
