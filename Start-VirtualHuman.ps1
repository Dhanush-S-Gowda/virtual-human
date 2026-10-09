# =============================================================================
# Start-VirtualHuman.ps1
#
# One-command launcher for the complete Virtual Human stack:
#   1. Starts llama.cpp server on RTX 4050 (Vulkan0, port 8080)
#   2. Waits until the LLM is ready
#   3. Brings up all Docker services
#
# Usage:
#   .\Start-VirtualHuman.ps1
#
# Stop everything:
#   .\Start-VirtualHuman.ps1 -Stop
# =============================================================================

param(
    [switch]$Stop,
    [switch]$Rebuild
)

# ---------------------------------------------------------------------------
# Config — adjust these if your setup differs
# ---------------------------------------------------------------------------

$LLM_HF_REPO    = "Qwen/Qwen3-8B-GGUF"
$LLM_HF_FILE    = "qwen3-8b-q4_k_m.gguf"
$LLM_DEVICE     = "Vulkan0"        # RTX 4050 Laptop GPU
$LLM_GPU_LAYERS = 99
$LLM_CTX_SIZE   = 2048
$LLM_HOST       = "0.0.0.0"
$LLM_PORT       = 8080
$LLM_HEALTH_URL = "http://localhost:$LLM_PORT/health"
$LLM_READY_WAIT = 120              # seconds to wait for model to load

# ---------------------------------------------------------------------------
# Stop mode
# ---------------------------------------------------------------------------

if ($Stop) {
    Write-Host "`n[Virtual Human] Stopping all services..." -ForegroundColor Yellow

    # Stop Docker stack
    Write-Host "[Docker] Stopping containers..." -ForegroundColor Cyan
    docker compose down

    # Kill any llama serve process
    $llama = Get-Process -Name "llama" -ErrorAction SilentlyContinue
    if ($llama) {
        Write-Host "[LLM] Stopping llama.cpp (PID $($llama.Id))..." -ForegroundColor Cyan
        Stop-Process -Id $llama.Id -Force
        Write-Host "[LLM] llama.cpp stopped." -ForegroundColor Green
    } else {
        Write-Host "[LLM] llama.cpp was not running." -ForegroundColor Gray
    }

    Write-Host "`n[Virtual Human] All services stopped.`n" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

Write-Host @"

  ╔══════════════════════════════════════════════════════╗
  ║       Virtual Human — Local 3D Talking Avatar        ║
  ║  STT (faster-whisper) → LLM (Qwen3-8B) → TTS (Kokoro) ║
  ╚══════════════════════════════════════════════════════╝

"@ -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Step 1: Check if llama.cpp is already running
# ---------------------------------------------------------------------------

function Test-LLMReady {
    try {
        $r = Invoke-RestMethod -Uri $LLM_HEALTH_URL -TimeoutSec 3 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

$llamaAlreadyRunning = Test-LLMReady

if ($llamaAlreadyRunning) {
    Write-Host "[LLM] llama.cpp already running on port $LLM_PORT ✓" -ForegroundColor Green
} else {
    # ---------------------------------------------------------------------------
    # Step 2: Start llama.cpp in a new minimized window
    # ---------------------------------------------------------------------------

    Write-Host "[LLM] Starting llama.cpp server..." -ForegroundColor Cyan
    Write-Host "      Model  : $LLM_HF_FILE" -ForegroundColor Gray
    Write-Host "      Device : $LLM_DEVICE (RTX 4050 via Vulkan)" -ForegroundColor Gray
    Write-Host "      Port   : $LLM_PORT" -ForegroundColor Gray
    Write-Host ""

    $llamaArgs = @(
        "serve"
        "--hf-repo",  $LLM_HF_REPO
        "--hf-file",  $LLM_HF_FILE
        "--device",   $LLM_DEVICE
        "--gpu-layers", $LLM_GPU_LAYERS
        "--ctx-size", $LLM_CTX_SIZE
        "--parallel", 1
        "--host",     $LLM_HOST
        "--port",     $LLM_PORT
        "--reasoning", "off"
    )

    # Launch in a separate window so its output is visible but doesn't block
    $llamaProcess = Start-Process `
        -FilePath "llama" `
        -ArgumentList $llamaArgs `
        -PassThru `
        -WindowStyle Normal

    Write-Host "[LLM] llama.cpp started (PID $($llamaProcess.Id))" -ForegroundColor Green
    Write-Host "[LLM] Waiting for model to load (up to $LLM_READY_WAIT seconds)..." -ForegroundColor Yellow

    # ---------------------------------------------------------------------------
    # Step 3: Wait for /health to respond
    # ---------------------------------------------------------------------------

    $elapsed = 0
    $ready   = $false
    $spinner = @('|', '/', '-', '\')
    $si      = 0

    while ($elapsed -lt $LLM_READY_WAIT) {
        if (Test-LLMReady) {
            $ready = $true
            break
        }

        Write-Host -NoNewline "`r[LLM] Loading $($spinner[$si % 4])  ($elapsed s elapsed)   " -ForegroundColor Yellow
        $si++
        $elapsed += 2
        Start-Sleep -Seconds 2
    }

    Write-Host ""   # newline after spinner

    if (-not $ready) {
        Write-Host "`n[ERROR] llama.cpp did not become ready within $LLM_READY_WAIT seconds." -ForegroundColor Red
        Write-Host "        Check the llama.cpp window for errors." -ForegroundColor Red
        Write-Host "        Common causes:" -ForegroundColor Yellow
        Write-Host "          - Model file not downloaded yet" -ForegroundColor Yellow
        Write-Host "          - Wrong --device (try Vulkan1 or cpu)" -ForegroundColor Yellow
        Write-Host "          - Not enough VRAM (6 GB required for Q4_K_M)" -ForegroundColor Yellow
        Write-Host "`n        Run manually to diagnose:" -ForegroundColor Yellow
        Write-Host "          llama serve --hf-repo $LLM_HF_REPO --hf-file $LLM_HF_FILE --device $LLM_DEVICE --port $LLM_PORT" -ForegroundColor Gray
        Write-Host "`n[Docker] Starting containers anyway (LLM will show as unavailable)..." -ForegroundColor Yellow
    } else {
        Write-Host "[LLM] llama.cpp is ready ✓" -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# Step 4: Start Docker stack
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[Docker] Starting containers..." -ForegroundColor Cyan

if ($Rebuild) {
    Write-Host "[Docker] Rebuilding images first (-Rebuild flag)..." -ForegroundColor Yellow
    docker compose up --build -d
} else {
    docker compose up -d
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] docker compose failed. Check Docker Desktop is running." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Step 5: Final status
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[Docker] Waiting for services to initialise..." -ForegroundColor Gray
Start-Sleep -Seconds 4

Write-Host ""
docker compose ps
Write-Host ""

# Quick health checks
function Show-Health($label, $url) {
    try {
        $r = Invoke-RestMethod -Uri $url -TimeoutSec 5 -ErrorAction Stop
        Write-Host "  $label  ✓" -ForegroundColor Green
    } catch {
        Write-Host "  $label  ✗  (still starting up)" -ForegroundColor Yellow
    }
}

Write-Host "Health checks:" -ForegroundColor Cyan
Show-Health "STT   http://localhost:8001/health  " "http://localhost:8001/health"
Show-Health "TTS   http://localhost:8000/health  " "http://localhost:8000/health"
Show-Health "LLM   http://localhost:8000/llm/health" "http://localhost:8000/llm/health"
Show-Health "UI    http://localhost:4173          " "http://localhost:4173"

Write-Host ""
Write-Host "  ┌─────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  Open http://localhost:4173 in browser  │" -ForegroundColor Cyan
Write-Host "  │  Hold SPACE to speak to the avatar      │" -ForegroundColor Cyan
Write-Host "  └─────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To stop everything: .\Start-VirtualHuman.ps1 -Stop" -ForegroundColor Gray
Write-Host ""
