$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    & uv sync
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE"
    }

    Write-Host "Downloading and validating the configured faster-whisper model..."
    & uv run python -c 'from stt.faster_whisper import _load_model; _load_model(local_files_only=False)'
    if ($LASTEXITCODE -ne 0) {
        throw "Faster-Whisper model setup failed with exit code $LASTEXITCODE"
    }

    Push-Location (Join-Path $PSScriptRoot "supertonic")
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
}
finally {
    Pop-Location
}
