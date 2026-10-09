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
    docker compose up -d --wait ollama
    if ($LASTEXITCODE -ne 0) { throw "Ollama failed to start. Check Docker Desktop GPU support." }
    docker compose exec -T ollama ollama pull qwen3.5:2b
    if ($LASTEXITCODE -ne 0) { throw "Model download failed. Run this script again to resume." }
    if ($Rebuild) {
        docker compose up -d --build
    } else {
        docker compose up -d
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not start the Virtual Human stack." }
    Write-Host "Virtual Human ready: http://localhost:4173 (Ollama qwen3.5:2b)"
    Write-Host "Latency logs: docker compose logs -f stt-server tts-server"
} finally {
    Pop-Location
}
