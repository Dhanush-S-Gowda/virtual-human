"""Run the independent CV service: uv run cv/main.py from backend/."""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Direct script execution puts cv/, rather than the repository root, on sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import settings
from backend.cv.window_manager import CVService
from backend.provider_process import ProviderProcess
from backend.observability import ResourceReporter, setup


def build_providers():
    # Imports are lazy: FastAPI and window tests never load CV models.

    cfg = settings.cv
    try:
        return (
            ProviderProcess("backend.cv.object_det.yolo", {"yolo": "YOLOProvider"}[cfg.object_provider], settings, role="cv.objects"),
            ProviderProcess("backend.cv.face_recog.insightface", {"insightface": "InsightFaceProvider"}[cfg.face_provider], settings, role="cv.faces"),
            ProviderProcess("backend.cv.keypoint_det.mediapipe", {"mediapipe": "MediaPipeProvider"}[cfg.keypoint_provider], settings, role="cv.keypoints"),
        )
    except KeyError as exc:
        raise ValueError(f"Unknown CV provider: {exc.args[0]}") from exc


async def main():
    setup("cv_service")
    resources = ResourceReporter("cv_service")
    service = CVService(*build_providers(), settings)
    try:
        await service.start()
        await asyncio.gather(service._tick_task, service._publish_task)
    finally:
        await service.stop()
        resources.close()


def cli():
    """Parse CLI options and run the service or its environment check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-env", action="store_true", help="Report dependencies and model asset availability")
    args = parser.parse_args()
    if args.check_env:
        from backend.scripts.check_cv_environment import main as check_environment
        check_environment()
        raise SystemExit(0)
    logging.basicConfig(level=getattr(logging, settings.cv.logging.level.upper()))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
