"""Interactive, cache-first model setup shared by Bash and PowerShell."""

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from backend.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
SUPERTONIC_REPO = "supertone-oss-archive/supertonic-3"
SUPERTONIC_REVISION = "aafc6e32416a594460b32413efc49d7fe4ce6d46"
KOKORO_VERSION = "v0.9.2"


@dataclass
class Asset:
    label: str
    present: Callable[[], bool]
    download: Callable[[], None]


def confirm(label):
    try:
        return input(f"{label} is missing or incomplete. Download it? Enter Y [y/N]: ").strip().lower() == "y"
    except (EOFError, OSError):
        return False


def prepare(asset, approve=None):
    if asset.present():
        print(f"{asset.label}: already downloaded.")
        return "existing"
    if not (approve or confirm)(asset.label):
        print(f"{asset.label}: skipped.")
        return "skipped"
    asset.download()
    if not asset.present():
        raise RuntimeError(f"{asset.label}: download did not produce the required files.")
    print(f"{asset.label}: downloaded.")
    return "downloaded"


def nonempty(path):
    return path.is_file() and path.stat().st_size > 0


def whisper_asset():
    from faster_whisper.utils import download_model
    from huggingface_hub.errors import LocalEntryNotFoundError
    cache = Path(settings.faster_whisper_download_root).expanduser()
    if not cache.is_absolute():
        cache = PROJECT_ROOT / cache

    def present():
        try:
            local = Path(settings.faster_whisper_model).expanduser()
            path = local if local.is_dir() else Path(download_model(
                settings.faster_whisper_model, cache_dir=str(cache), local_files_only=True))
        except LocalEntryNotFoundError:
            return False
        return (all(nonempty(path / name) for name in ("config.json", "model.bin", "tokenizer.json"))
                and any(nonempty(path / name) for name in ("vocabulary.txt", "vocabulary.json")))

    return Asset(f"Optional Faster-Whisper {settings.faster_whisper_model}", present,
                 lambda: download_model(settings.faster_whisper_model, cache_dir=str(cache), local_files_only=False))


def supertonic_asset():
    root = BACKEND_DIR / "supertonic/assets"
    required = [f"onnx/{name}" for name in (
        "duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx",
        "vocoder.onnx", "tts.json", "unicode_indexer.json")]
    required += ["voice_styles/F1.json", "voice_styles/M1.json"]

    def download():
        from huggingface_hub import snapshot_download
        snapshot_download(SUPERTONIC_REPO, revision=SUPERTONIC_REVISION, local_dir=str(root),
                          allow_patterns=["onnx/*", "voice_styles/F1.json", "voice_styles/M1.json"])

    return Asset("Optional Supertonic 3 assets", lambda: all(nonempty(root / name) for name in required), download)


def cv_assets():
    from backend.cv.keypoint_det.models import configured_models, ensure_model, model_is_valid, model_path
    from backend.cv.object_det.yolo import detection_model_name, YOLOProvider
    name = detection_model_name(settings.cv.detection.model)
    weights = PROJECT_ROOT / (name if name.endswith(".pt") else name + ".pt")
    yield Asset(f"YOLO {name}", lambda: nonempty(weights), lambda: YOLOProvider(settings)._initialize())
    for name in configured_models(settings):
        yield Asset(f"MediaPipe {name}", lambda name=name: model_is_valid(model_path(name)),
                    lambda name=name: ensure_model(name))
    if settings.cv.face.enabled:
        from backend.cv.face_recog.detector import FaceDetector, model_root
        pack = settings.cv.face.model_name
        root = model_root() / "models" / pack
        # An empty or single-file directory is an incomplete model pack.
        def present():
            files = [file for file in root.glob("*.onnx") if nonempty(file)]
            return len(files) >= 2
        yield Asset(f"InsightFace {pack}", present,
                    lambda: FaceDetector(pack, settings.cv.face.detection_threshold).initialize())


def run_probe(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)


def nvidia_gpu_available():
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = run_probe([executable, "--query-gpu=name", "--format=csv,noheader"])
            if result.returncode == 0 and result.stdout.strip():
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        import torch
        return bool(torch.version.cuda and torch.cuda.is_available())
    except (ImportError, OSError, RuntimeError):
        return False


def prepare_kokoro(approve=None):
    docker = shutil.which("docker")
    if not docker:
        print("Docker is not installed; skipping the optional Kokoro image.")
        return "skipped"
    try:
        if run_probe([docker, "info", "--format", "{{.ServerVersion}}"]).returncode:
            print("Docker is unavailable. Start Docker and rerun setup to prepare Kokoro.")
            return "skipped"
        gpu = nvidia_gpu_available()
        image = f"ghcr.io/remsky/kokoro-fastapi-{'gpu' if gpu else 'cpu'}:{KOKORO_VERSION}"
        print(f"Kokoro image selected: {image} ({'NVIDIA GPU detected' if gpu else 'no usable NVIDIA GPU detected'}).")
        asset = Asset(image, lambda: run_probe([docker, "image", "inspect", image]).returncode == 0,
                      lambda: subprocess.run([docker, "pull", image], check=True))
        result = prepare(asset, approve)
        if result != "skipped":
            options = "--gpus all " if gpu else ""
            print(f"Run when needed: docker run --rm {options}-p 8880:8880 {image}")
        return result
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Kokoro image setup failed: {exc}") from exc


def main():
    # Scripts enter from the repository root, matching the YOLO runtime cache.
    print("Checking local assets. Only an explicit Y permits a missing download.")
    prepare(whisper_asset())
    prepare(supertonic_asset())
    prepare_kokoro()
    for asset in cv_assets():
        prepare(asset)
    print("Setup checks completed. Skipped assets were not installed.")


if __name__ == "__main__":
    main()
