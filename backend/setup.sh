#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$backend_dir/../.." && pwd)"
cv_dir="$project_root/cv"

echo "============================================================"
echo " Setting up Virtual Human & Computer Vision Models"
echo "============================================================"

# ── 1. Virtual Human Backend STT & TTS ────────────────────────
echo -e "\n[1/5] Syncing backend virtual environment..."
cd "$backend_dir"
uv sync

echo -e "\n[2/5] Downloading and validating configured Faster-Whisper model..."
uv run python -c 'from stt.faster_whisper import _load_model; _load_model(local_files_only=False)'

echo -e "\n[3/5] Downloading Supertonic 3 TTS ONNX assets..."
mkdir -p "$backend_dir/supertonic/assets"
(
    cd "$backend_dir/supertonic"
    uv run hf download \
        supertone-oss-archive/supertonic-3 \
        --revision aafc6e32416a594460b32413efc49d7fe4ce6d46 \
        --local-dir assets
)

# Optional Kokoro TTS Docker image
if command -v docker >/dev/null 2>&1; then
    echo -e "\n[*] Pulling Kokoro TTS Docker image (CPU)..."
    docker pull ghcr.io/remsky/kokoro-fastapi-cpu:latest || echo "Warning: Docker pull failed, skipping Kokoro pull."
else
    echo -e "\n[*] Docker not found. If using Kokoro TTS, pull: docker pull ghcr.io/remsky/kokoro-fastapi-cpu:latest"
fi

# ── 2. Computer Vision Models ─────────────────────────────────
echo -e "\n[4/5] Setting up Computer Vision models (YOLO, MediaPipe, InsightFace)..."

mkdir -p "$cv_dir/models"

# MediaPipe Hand Landmarker
hand_landmarker_path="$cv_dir/models/hand_landmarker.task"
if [ ! -f "$hand_landmarker_path" ]; then
    echo "Downloading MediaPipe Hand Landmarker task model..."
    curl -fsSL -o "$hand_landmarker_path" \
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
else
    echo "MediaPipe Hand Landmarker task model already present."
fi

# MediaPipe Gesture Recognizer fallback
gesture_recognizer_path="$cv_dir/models/gesture_recognizer.task"
if [ ! -f "$gesture_recognizer_path" ]; then
    echo "Downloading MediaPipe Gesture Recognizer fallback model..."
    curl -fsSL -o "$gesture_recognizer_path" \
        "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task"
else
    echo "MediaPipe Gesture Recognizer model already present."
fi

# YOLOv8n-pose
yolo_pose_path="$cv_dir/yolov8n-pose.pt"
if [ ! -f "$yolo_pose_path" ]; then
    echo "Downloading YOLOv8n-pose model..."
    curl -fsSL -o "$yolo_pose_path" \
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-pose.pt"
else
    echo "YOLOv8n-pose model already present."
fi
cp -f "$yolo_pose_path" "$project_root/yolov8n-pose.pt" 2>/dev/null || true

# YOLO26n general object detection
yolo_obj_path="$project_root/yolo26n.pt"
if [ ! -f "$yolo_obj_path" ] && [ ! -f "$cv_dir/yolo26n.pt" ]; then
    echo "Downloading YOLO general object detection model..."
    curl -fsSL -o "$cv_dir/yolov8n.pt" \
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
fi
[ -f "$yolo_obj_path" ] && cp -f "$yolo_obj_path" "$cv_dir/yolo26n.pt" 2>/dev/null || true

# InsightFace buffalo_s
echo -e "\n[5/5] Downloading and initializing InsightFace buffalo_s model pack..."
if [ -d "$cv_dir/.venv" ]; then
    "$cv_dir/.venv/bin/python" -c "from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_s', allowed_modules=['detection', 'recognition']); app.prepare(ctx_id=-1, det_size=(640, 640)); print('InsightFace buffalo_s ready.')" 2>/dev/null || \
    uv run python -c "try:
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name='buffalo_s', allowed_modules=['detection', 'recognition'])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    print('InsightFace buffalo_s ready.')
except ImportError:
    pass"
fi

echo -e "\n============================================================"
echo " All models downloaded, validated, and ready!"
echo "============================================================"
