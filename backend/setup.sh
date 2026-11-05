#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$backend_dir/.." && pwd)"

echo "============================================================"
echo " Setting up Virtual Human & Computer Vision Models"
echo "============================================================"

# ── 1. Virtual Human Backend STT & TTS ────────────────────────
echo -e "\n[1/4] Syncing backend virtual environment..."
cd "$backend_dir"
uv sync

echo -e "\n[2/4] Downloading and validating configured Faster-Whisper model..."
uv run python -c 'from stt.faster_whisper import _load_model; _load_model(local_files_only=False)'

echo -e "\n[3/4] Downloading Supertonic 3 TTS ONNX assets..."
mkdir -p "$backend_dir/supertonic/assets"
(
    cd "$backend_dir/supertonic"
    uv run --project "$backend_dir" hf download \
        supertone-oss-archive/supertonic-3 \
        --revision aafc6e32416a594460b32413efc49d7fe4ce6d46 \
        --local-dir assets
)

# Optional Kokoro TTS Docker image
if command -v docker >/dev/null 2>&1; then
    echo -e "\n[*] Pulling Kokoro TTS Docker image (CPU)..."
    docker pull ghcr.io/remsky/kokoro-fastapi-cpu:v0.9.2 || echo "Warning: Docker pull failed, skipping Kokoro pull."
else
    echo -e "\n[*] Docker not found. If using Kokoro TTS, pull: docker pull ghcr.io/remsky/kokoro-fastapi-cpu:v0.9.2"
fi

# ── 2. Computer Vision Models ─────────────────────────────────
echo -e "\n[4/4] Preparing configured CV models (existing valid assets are reused)..."
cd "$project_root"
uv run --project "$backend_dir" python -m backend.scripts.setup_cv_models

echo -e "\n============================================================"
echo " Model setup completed successfully."
echo "============================================================"
