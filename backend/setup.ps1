$ErrorActionPreference = "Stop"
# Inspect nonzero native exit codes ourselves, including on PowerShell 7.
$PSNativeCommandUseErrorActionPreference = $false

$backendDir = $PSScriptRoot
$projectRoot = (Resolve-Path (Join-Path $backendDir "..")).Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv and rerun this script. Nothing was downloaded."
}

Push-Location $backendDir
try {
    & uv sync --check --offline --no-python-downloads *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Backend dependencies are already installed and synchronized."
    }
    else {
        $answer = [string](Read-Host "Backend dependencies are missing or out of date. Install/sync Python and packages? Enter Y to continue [y/N]")
        if ($answer.Trim() -ine "Y") {
            Write-Host "Dependency installation skipped. Model setup requires a synchronized backend environment."
            return
        }
        & uv sync
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
    }

    Set-Location $projectRoot
    & uv run --project $backendDir --no-sync --offline --no-python-downloads `
        python -m backend.scripts.setup_models
    if ($LASTEXITCODE -ne 0) {
        throw "Model setup failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
