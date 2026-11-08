# Run the Docker demo

Change owner: **Bhvan Madhusudhan**.

## Requirements

Use Docker Desktop with Linux containers and Compose 2.24 or newer. The root
Compose entry includes `devops/compose.gpu.yaml` for NVIDIA GPU access in Ollama
and Kokoro. For a CPU-only machine, use
`docker compose -f devops/compose.yaml` in each command.

## Set up and start

From the repository root:

```powershell
docker compose up --build -d
```

Open http://localhost:4173. The avatar app opens directly without sign-in.
Celery and Redis remain enabled for background metrics. The frontend binds to
loopback on this laptop. API and camera WebSockets use the same app origin.

Initial setup downloads container images and model weights. The model
initializers must finish before the API starts. Allow microphone and camera
access when you want to use these features.

```powershell
docker compose ps -a
docker compose logs -f ollama-init speech-init backend cv worker
docker compose down
```

`down` preserves named volumes. `down -v` deletes them, including models, queued
jobs, and metrics. Do not use it for a routine restart.

## Services and ports

| Service | Purpose | Host access |
|---|---|---|
| talking-avatar | Nginx static files and API/CV routing | 127.0.0.1:4173 |
| backend | Conversation WebSocket and speech pipeline | Internal only |
| redis | Celery broker DB 0 and task results DB 1 | Internal only |
| worker | Celery metrics persistence | Internal only |
| ollama / kokoro | Local chat and speech generation | Internal only |
| cv | Independent camera inference | Internal only |

The browser uses `/api` and `/cv` on the app origin. Nginx proxies these routes,
including WSS when the app uses an HTTPS tunnel. The API has no separate host
port. The older STT/TTS containers are not part of this Compose definition.

## Model settings

The backend image copies `backend/config.docker.yaml` to its internal
`config.yaml`. Host development still uses `backend/config.yaml`. Rebuild after
changing Docker settings. If you change `llm_model`, also change the initializer
command in Compose. Ollama uses structured JSON plans and disabled thinking
output to avoid native XML tool parsing errors and unwanted control markers.

Chat and speech use the RTX GPU under the override. Camera and Faster-Whisper
use CPU. Speech segments use 8-word minimum and 12-word maximum lengths, with
one synthesis request at a time. The first model load is slower than warm use.

Speech speed tags use `[speed:0.85]`, `[speed:1.2]`, and `[speed:1.0]`. The parser
removes them before display and synthesis and applies the rate to subsequent
phrases. Invalid values are ignored. Finite values are clipped to
`tts_speed_min` and `tts_speed_max` (0.75 and 1.5 by default). Limits must stay
within the provider range 0.25 to 4.0. Preference across later turns depends on
the model repeating the tag from its conversation history.

## Background work

Live speech remains on the direct conversation path. After a turn, the API saves
CSV metrics and sends a quantitative summary to Celery. The worker stores it in
`/app/data/metrics.sqlite3` on the `backend-data` volume. Unique event IDs prevent
duplicate rows after task redelivery. No transcript or camera payload is sent
to Redis. If the broker is unavailable, the API retains the CSV fallback and
logs the queue error without failing the conversation.
Broker and result-store connections both use short timeouts. Result-store
reconnection fails promptly during an outage so the CSV fallback can take over.

```powershell
docker compose exec worker celery -A background.celery_app inspect ping
docker compose logs --tail 50 worker
docker compose exec redis redis-cli ping
```

Redis uses append-only persistence. Task results expire after one hour.

## Verification

```powershell
docker compose config --quiet
node --test frontend/tests/*.test.js
```

Run backend tests in the container with the test directory mounted:

```powershell
docker compose run --rm --no-deps -v "${PWD}/backend/tests:/app/backend/tests:ro" backend uv run --no-sync --with pytest --with pytest-asyncio python -m pytest tests -q
```

With the stack running, open http://localhost:4173 and check that the avatar
loads without a login redirect. Check http://localhost:4173/api/health for the
API status. Celery worker ping and Redis ping should both return success.

See [the changelog](../CHANGELOG.md) for the checks run in this workspace.

Reference: [Celery with Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html).
