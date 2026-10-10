# Start the Docker-based Virtual Human stack with Ollama Qwen3.5 2B.
param([switch]$Stop, [switch]$Rebuild)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    if ($Stop) {
        docker compose down
        if ($LASTEXITCODE -ne 0) { throw "Could not stop Docker services." }
        exit 0
    }
    if ($Rebuild) {
        docker compose up -d --build --wait --wait-timeout 900
    } else {
        docker compose up -d --wait --wait-timeout 900
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not start the Virtual Human stack." }
    Write-Host "Virtual Human ready: http://localhost:4173 (Ollama qwen3.5:2b)"
    Write-Host "Latency logs: docker compose logs -f stt-server tts-server"
} finally {
    Pop-Location
}
