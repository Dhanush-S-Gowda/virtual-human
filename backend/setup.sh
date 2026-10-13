#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

cd "$backend_dir"
uv sync

echo "Downloading and validating the configured faster-whisper model..."
uv run python -c 'from stt.faster_whisper import _load_model; _load_model(local_files_only=False)'

cd "$backend_dir/supertonic"
uv run hf download \
    supertone-oss-archive/supertonic-3 \
    --revision aafc6e32416a594460b32413efc49d7fe4ce6d46 \
    --local-dir assets
