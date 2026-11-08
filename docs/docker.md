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
Celery and Redis remain enabled for background metrics and memory indexing. The frontend binds to
loopback on this laptop. API and camera WebSockets use the same app origin.

Initial setup downloads container images and model weights. The model
initializers must finish before the API starts. Allow microphone and camera
access when you want to use these features.

```powershell
docker compose ps -a
docker compose logs -f ollama-init nemotron-init nemotron backend cv worker
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
| worker | Celery metrics persistence and memory indexing | Internal only |
| ollama / kokoro | Local chat and speech generation | Internal only |
| nemotron | Streaming speech recognition | Internal only |
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

Nemotron starts with Compose. The `nemotron-init` service downloads the English
0.6B GGUF model into the `nemotron-models` volume. Later starts reuse this file.
The backend connects to `ws://nemotron:8080/v1/audio/transcriptions/realtime`
after the speech server passes its readiness check. No host speech server is
required. Check `docker compose logs nemotron-init nemotron` if startup fails.

The backend merges adjacent camera-history windows with identical observations
before sending them to the chat model. It keeps the full time range and keeps
changes in people, objects, gestures, and pending work separate. This reduces
repeated context without reducing the configured history duration. It does not
change stored conversation memory.

For response-time checks, connect the laptop to power and select Windows Best
performance. Battery power can limit GPU speed even when models run on CUDA.
Model startup still needs a warmup after a restart. Compare warm turns on the
same power mode before changing model settings.

Chat, speech recognition, and speech generation use the RTX GPU under the
override. Camera uses CPU to reduce contention on the shared 6 GB GPU; it does
not receive a GPU reservation. Camera inference can run more slowly on CPU.
The first speech segment has a 6-word cap to start playback sooner. Set
`first_speech_segment_words` to zero to use the regular limits from the start.
Later segments use 8-word minimum and 12-word maximum lengths, with
one synthesis request at a time. The first model load is slower than warm use.

Kokoro can run a silent warmup when audio recording starts. Set
`kokoro_warmup_on_recording` to enable it. The generated audio is discarded.
The warmup runs in the background, skips active synthesis and recent requests,
and uses the selected voice and speed. `kokoro_warmup_idle_seconds` controls
the idle threshold (15 seconds); `kokoro_warmup_timeout_seconds` bounds the
request (2 seconds). Warmup failures do not fail the conversation. It does not
run for text input or on a periodic timer. Measure STT and full-turn latency
before enabling it: Nemotron and the chat model may already wake the GPU.

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
