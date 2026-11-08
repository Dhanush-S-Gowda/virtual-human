# Development and verification

[Documentation index](README.md) | [Project overview](../README.md)

Run commands from the repository root unless a step changes the directory.

## Host development

Use Docker for the ready-to-run local demo. Host development additionally
requires Python 3.13 or newer, `uv`, Node.js 22.12 or newer, npm, and any native
libraries required by the model packages.

The checked-in host configuration differs from Docker: it selects OpenRouter
for chat, Nemotron at `127.0.0.1:18080` for STT, and Kokoro at
`localhost:8880` for TTS. Configure those services and credentials, or change
`backend/config.yaml` to your local providers before starting the API.

For local inference, select `stt_provider: faster-whisper`, an accessible
Ollama URL and model, and either a running Kokoro service or
`tts_provider: supertonic`. Prepare local models from the repository root:

```powershell
.\backend\setup.ps1
```

On Linux, use `bash backend/setup.sh`. These scripts request confirmation for
missing dependencies and model downloads. They do not start external Ollama,
Kokoro, or Nemotron services. Compose services are internal-only by default;
a host backend cannot reach them through `localhost` without separate port
configuration.

Start the backend from its directory:

```powershell
cd backend
uv sync --extra dev
uv run python main.py
```

Start CV in a separate terminal from the repository root:

```powershell
uv run --project backend python -m backend.cv
```

Start the frontend in another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the host backend on port
8000 and `/cv` to the CV service on port 8765. `npm run build` creates the
frontend bundle. Docker serves its bundle with Nginx on port 4173.

Celery is disabled by default outside Compose. A host deployment must provide
Redis and set `CELERY_ENABLED`, `CELERY_BROKER_URL`, and
`CELERY_RESULT_BACKEND` for the API, plus matching worker settings. Host CSV
metrics normally use `backend/metrics.csv`; Docker uses the named volume above.

## Interfaces

The Docker health endpoint is http://localhost:4173/api/health. Host development
also exposes `http://localhost:8000/health`. This checks that the API responds
and reports provider settings; it does not verify successful model inference.

The browser uses `/api/ws` for conversations. A direct host API client uses
`ws://localhost:8000/ws`. Start a turn with a JSON message such as:

```json
{
  "type": "start",
  "session_id": "browser-session-id",
  "mime_type": "audio/pcm",
  "sample_rate": 16000,
  "voice": "af_heart",
  "speed": 1.0
}
```

Send the actual capture sample rate, then binary audio chunks and
`{"type":"end"}`. Cancel with `{"type":"cancel"}`. Events include `transcript`,
`llm_delta`, `llm_complete`, `speech`, `metrics`, `error`, and `done`;
partial transcripts depend on the selected STT provider. Speech packets carry
audio, timing, segment identifiers, and gesture cues.

For direct synthesis, send a `start` message with `mode: "speak"` and a `text`
value. This bypasses STT and LLM processing and uses the selected TTS provider.

## Verification

From the repository root:

```powershell
node --test frontend/tests/*.test.js
uv run --project backend --extra dev python -m pytest -c backend/pyproject.toml backend/tests -q
```

To run backend tests with Docker instead of the host environment:

```powershell
docker compose run --rm --no-deps -v "${PWD}/backend/tests:/app/backend/tests:ro" backend uv run --no-sync --with pytest --with pytest-asyncio python -m pytest tests -q
```

Validate the Compose configuration with `docker compose config --quiet`.
For CPU-only testing, include `-f devops/compose.yaml`. Tests that require
optional model assets may be skipped when those assets are unavailable.
See [CHANGELOG.md](../CHANGELOG.md) for recorded checks and their limits.

For a manual smoke check, open the app, verify `/api/health`, record a short
phrase, and confirm that a reply and speech arrive. Check camera preview and
CV status separately. Redis ping and Celery worker ping should both succeed.
