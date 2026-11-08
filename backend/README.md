# Backend

The Python backend coordinates conversation sessions, speech recognition, chat,
speech synthesis, and turn metrics. Camera inference runs as a separate service.

## Folder map

| Folder | Purpose |
|---|---|
| [cv](cv/README.md) | Camera events, object tracking, landmarks, and identity |
| [llm](llm/README.md) | Chat provider, action plans, and validated tools |
| [memory](memory/README.md) | Persistent turns, semantic retrieval, and reranking |
| [stt](stt/README.md) | Faster-Whisper and Nemotron speech recognition |
| [tts](tts/README.md) | Kokoro and Supertonic speech synthesis |
| [scripts](scripts/README.md) | Model setup, asset sync, benchmarks, and diagnostics |
| [tests](tests/README.md) | Python regression tests and fixtures |
| [supertonic](supertonic/README.md) | Vendored Supertonic source and upstream guides |

## Main files

- `main.py`: FastAPI health and conversation WebSocket endpoints.
- `pipeline.py`: recording, transcription, reply, speech, and metrics coordination.
- `background.py`: Celery metrics persistence and memory embedding jobs.
- `inline_gestures.py`: incremental gesture and speech-speed tag parsing.
- `world.py`: camera scene queries and speaker context.
- `runtime.py` and `provider_process.py`: model workers and process boundaries.
- `config.yaml`: host settings; `config.docker.yaml`: image build settings.
- `pyproject.toml` and `uv.lock`: Python dependencies and locked versions.

## Run and test

The Docker workflow is in [DevOps](../devops/README.md). For host development,
configure providers first, then run from this folder:

```powershell
uv sync --extra dev
uv run python main.py
```

From the repository root, run Python tests with:

```powershell
uv run --project backend --extra dev python -m pytest -c backend/pyproject.toml backend/tests -q
```

See [development](../docs/development.md) for native requirements and Docker
alternatives. See [configuration](../docs/configuration.md) for environment
variables and data paths. Keep provider credentials in the ignored `.env` file.
