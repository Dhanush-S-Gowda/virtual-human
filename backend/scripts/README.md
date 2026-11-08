# Backend utility scripts

These scripts prepare assets and inspect the application. Run Python module
commands from the repository root so package imports resolve.

| Script | Purpose |
|---|---|
| `setup_models.py` | Check and download selected local model assets |
| `setup_cv_models.py` | Prepare camera inference models |
| `check_cv_environment.py` | Inspect CV dependencies and model availability |
| `benchmark_cv.py` | Measure camera provider performance |
| `benchmark_current_architecture.py` | Measure current Docker Qwen 4B and Kokoro providers |
| `summarize_benchmarks.py` | Export one model's recorded-turn statistics, reliability, and source snapshot |
| `evaluate_prompt.py` | Run local-model answer and control checks with fixed scenes and isolated conversations |
| `monitor_resources.py` | Inspect process and resource use |
| `setup_observability.py` | Configure optional observability resources |
| `sync_animation_assets.py` | Copy animations into the frontend and update catalog/prompt tags |

Use `backend/setup.ps1` on Windows or `bash backend/setup.sh` on Linux for model
setup. The scripts request confirmation before downloading missing assets.

Animation sync reads `TalkingHead/animations/`, writes
`frontend/public/animations/`, and updates the catalog and prompt. It removes
stale destination FBX files. Run it only when intending to synchronize assets:

```powershell
uv run --project backend python -m backend.scripts.sync_animation_assets
```

See [backend guide](../README.md) and [asset tests](../tests/README.md).
Use the repository [benchmark question set](../../BENCHMARK-QUESTIONS.md) for
same-input architecture comparisons.

Run the current Docker provider benchmark from the repository root:

```powershell
uv run --project backend python -m backend.scripts.benchmark_current_architecture
```

Use `--iterations 10` for ten measurements per provider or `--output
backend/my-metrics.csv` to choose another CSV path. This measures provider
latency; full speech-to-response rows require spoken microphone input.
