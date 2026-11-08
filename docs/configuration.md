# Configuration and stored data

[Documentation index](README.md) | [Docker setup](docker.md)

## Configuration

| Run mode | Configuration | Apply changes |
|---|---|---|
| Docker | `backend/config.docker.yaml`, copied into the image as `config.yaml` | Rebuild and recreate the affected services |
| Host development | `backend/config.yaml` | Restart the backend and, for camera settings, the CV process |
| Frontend Docker build | Build arguments in `devops/compose.yaml` | Rebuild `talking-avatar` |

Docker configuration is baked into the image. Editing `backend/config.yaml`
on the host does not change a running Docker backend. After Docker settings
change, run `docker compose up --build -d`. If you change `llm_model`, also
change the model name in the `ollama-init` command in `devops/compose.yaml`;
the initializer does not read the YAML model setting automatically.

The Docker defaults are:

- Chat: Ollama `qwen3.5:2b`, structured JSON plans, and disabled thinking output.
- Speech recognition: Faster-Whisper `base`, CPU, `int8`.
- Speech synthesis: Kokoro, using CPU or the NVIDIA override.
- Speech segments: 8-word minimum, 12-word maximum, one synthesis request at a time.
- Camera inference: a separate CPU service with persistent model caches.
- OpenObserve diagnostics: disabled in Compose.

`backend/.env` is optional for Docker. Use it for provider credentials when
selecting hosted services; keep it out of version control. Docker service
names such as `ollama`, `kokoro`, and `cv` resolve inside the Compose network.
Host processes must use reachable host addresses instead.

### Speech speed and timing

Users can ask the avatar to speak slowly or faster. The model can emit inline
tags such as `[speed:0.85]`, `[speed:1.2]`, and `[speed:1.0]`. The existing tag
parser removes these tags before display and synthesis. It rejects invalid
values and clips finite values to the configured limits:

```yaml
tts_speed: 1.0
tts_speed_min: 0.75
tts_speed_max: 1.5
min_speech_segment_words: 8
max_speech_segment_words: 12
tts_max_concurrent_segments: 1
```

Keep the speed limits within the provider range of 0.25 to 4.0. A tag applies
to subsequent phrases. Persistence across turns depends on the model repeating
the preference from conversation history.

The Activity panel and metrics separate transcription, reply generation, and
speech timing. First model loads take longer than warm requests. Short segments
can reduce the wait for speech, but actual delay also depends on hardware,
model size, provider timing, and gesture pacing. See
[audio and gesture synchronization](audio-gesture-synchronization.md).

## Celery, Redis, and stored data

Live speech uses the direct conversation pipeline. After a turn, the API writes
CSV metrics and queues a quantitative summary. Celery stores that summary in
SQLite. Event IDs prevent duplicate rows after task redelivery.

Redis database 0 holds the broker queue; database 1 holds task results. Redis
uses append-only persistence, and task results expire after one hour. Broker
and result-store failures use short timeouts so an outage does not hold up turn
completion. A failed queue submission is logged; the CSV remains the fallback.

Queued metrics exclude transcripts and camera payloads. **CSV metrics include
spoken response segments** in `response_segments_json` as well as timing data.

| Named volume | Stored data |
|---|---|
| `backend-data` | `/app/data/metrics.csv` and `/app/data/metrics.sqlite3` |
| `redis-data` | Redis queue and result persistence |
| `ollama-models` | Ollama model weights |
| `whisper-models` | Faster-Whisper cache |
| `cv-models`, `yolo-models` | Camera model caches |
| `cv-data` | Camera identity database and related data |

Check background services with:

```powershell
docker compose exec redis redis-cli ping
docker compose exec worker celery -A background.celery_app inspect ping
docker compose logs --tail 50 worker
```
