$ErrorActionPreference = "Stop"

$backendDir = $PSScriptRoot
$projectRoot = (Resolve-Path (Join-Path $backendDir "..\..")).Path
$cvDir = Join-Path $projectRoot "cv"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Setting up Virtual Human & Computer Vision Models (PowerShell)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Push-Location $backendDir
try {
    # ── 1. Virtual Human Backend STT & TTS ────────────────────────
    Write-Host "`n[1/5] Syncing backend virtual environment with uv..." -ForegroundColor Green
    & uv sync
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE"
    }

    Write-Host "`n[2/5] Downloading and validating configured Faster-Whisper model..." -ForegroundColor Green
    & uv run python -c 'from stt.faster_whisper import _load_model; _load_model(local_files_only=False)'
    if ($LASTEXITCODE -ne 0) {
        throw "Faster-Whisper model setup failed with exit code $LASTEXITCODE"
    }

    Write-Host "`n[3/5] Downloading Supertonic 3 TTS ONNX assets..." -ForegroundColor Green
    $supertonicDir = Join-Path $backendDir "supertonic"
    $assetsDir = Join-Path $supertonicDir "assets"
    if (-not (Test-Path $assetsDir)) {
        New-Item -ItemType Directory -Path $assetsDir -Force | Out-Null
    }

    Push-Location $supertonicDir
    try {
        & uv run hf download `
            supertone-oss-archive/supertonic-3 `
            --revision aafc6e32416a594460b32413efc49d7fe4ce6d46 `
            --local-dir assets
        if ($LASTEXITCODE -ne 0) {
            throw "Supertonic asset download failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    # Optional Kokoro TTS Docker image
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCmd) {
        Write-Host "`n[*] Pulling Kokoro TTS Docker image (CPU)..." -ForegroundColor Yellow
        try {
            & docker pull ghcr.io/remsky/kokoro-fastapi-cpu:latest
        }
        catch {
            Write-Host "Warning: Docker daemon not running or pull failed, skipping Kokoro pull." -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "`n[*] Docker not found. If using Kokoro TTS, pull: docker pull ghcr.io/remsky/kokoro-fastapi-cpu:latest" -ForegroundColor Gray
    }

    # ── 2. Computer Vision Models ─────────────────────────────────
    Write-Host "`n[4/5] Setting up Computer Vision models (YOLO, MediaPipe, InsightFace)..." -ForegroundColor Green

    $modelsDir = Join-Path $cvDir "models"
    if (-not (Test-Path $modelsDir)) {
        New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    }

    # MediaPipe Hand Landmarker
    $handLandmarkerPath = Join-Path $modelsDir "hand_landmarker.task"
    if (-not (Test-Path $handLandmarkerPath)) {
        Write-Host "Downloading MediaPipe Hand Landmarker task model..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task" -OutFile $handLandmarkerPath
    }
    else {
        Write-Host "MediaPipe Hand Landmarker task model already present." -ForegroundColor Gray
    }

    # MediaPipe Gesture Recognizer fallback
    $gestureRecognizerPath = Join-Path $modelsDir "gesture_recognizer.task"
    if (-not (Test-Path $gestureRecognizerPath)) {
        Write-Host "Downloading MediaPipe Gesture Recognizer fallback model..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task" -OutFile $gestureRecognizerPath
    }
    else {
        Write-Host "MediaPipe Gesture Recognizer model already present." -ForegroundColor Gray
    }

    # YOLOv8n-pose
    $yoloPosePath = Join-Path $cvDir "yolov8n-pose.pt"
    if (-not (Test-Path $yoloPosePath)) {
        Write-Host "Downloading YOLOv8n-pose model..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-pose.pt" -OutFile $yoloPosePath
    }
    else {
        Write-Host "YOLOv8n-pose model already present." -ForegroundColor Gray
    }
    $rootPosePath = Join-Path $projectRoot "yolov8n-pose.pt"
    if (-not (Test-Path $rootPosePath)) {
        Copy-Item $yoloPosePath $rootPosePath -Force
    }

    # YOLO26n / YOLOv8n general object detection
    $rootYoloObjPath = Join-Path $projectRoot "yolo26n.pt"
    $cvYoloObjPath = Join-Path $cvDir "yolo26n.pt"
    if (Test-Path $rootYoloObjPath) {
        if (-not (Test-Path $cvYoloObjPath)) {
            Copy-Item $rootYoloObjPath $cvYoloObjPath -Force
        }
        Write-Host "YOLO26n object detection model verified." -ForegroundColor Gray
    }
    elseif (-not (Test-Path $cvYoloObjPath)) {
        Write-Host "Downloading YOLO general object detection model..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt" -OutFile (Join-Path $cvDir "yolov8n.pt")
    }

    # InsightFace buffalo_s
    Write-Host "`n[5/5] Downloading and initializing InsightFace buffalo_s model pack..." -ForegroundColor Green
    $cvPython = Join-Path $cvDir ".venv\Scripts\python.exe"
    if (Test-Path $cvPython) {
        & $cvPython -c "from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_s', allowed_modules=['detection', 'recognition']); app.prepare(ctx_id=-1, det_size=(640, 640)); print('InsightFace buffalo_s ready.')"
    }
    else {
        Write-Host "CV virtualenv not found at $cvPython; skipping direct InsightFace warmup." -ForegroundColor Yellow
    }

    Write-Host "`n============================================================" -ForegroundColor Cyan
    Write-Host " All models downloaded, validated, and ready!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Cyan
}
finally {
    Pop-Location
}
