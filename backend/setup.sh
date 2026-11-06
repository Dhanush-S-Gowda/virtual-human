#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$backend_dir/.." && pwd)"

confirm_download() {
    local answer
    read -r -p "$1 Enter Y to continue [y/N]: " answer || return 1
    [[ "$answer" == "Y" || "$answer" == "y" ]]
}

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required. Install uv and rerun this script. Nothing was downloaded."
    exit 1
fi

cd "$backend_dir"
if uv sync --check --offline --no-python-downloads >/dev/null 2>&1; then
    echo "Backend dependencies are already installed and synchronized."
elif confirm_download "Backend dependencies are missing or out of date. Install/sync Python and packages?"; then
    uv sync
else
    echo "Dependency installation skipped. Model setup requires a synchronized backend environment."
    exit 0
fi

cd "$project_root"
# uv itself must not sync packages or download Python before the helper prompts.
uv run --project "$backend_dir" --no-sync --offline --no-python-downloads \
    python -m backend.scripts.setup_models
