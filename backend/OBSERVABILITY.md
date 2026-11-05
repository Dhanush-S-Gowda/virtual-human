# Runtime logs, model resources, and inline gestures

## OpenObserve dashboard

Open [OpenObserve](http://localhost:5080), then choose **Dashboards → Virtual Human**.
The single dashboard has 13 tiles covering CV events and latency, STT, LLM, TTS,
conversation events, execution spans, model CPU, RSS, private RAM (USS), and
application logs. The dashboard ID is `7522936825833324544`.

Event streams remain separate for querying: `vh_cv`, `vh_stages`,
`vh_conversation`, `vh_llm`, `vh_stt`, `vh_tts`, `vh_resources`, and
`vh_application`. Session IDs, turn IDs, request IDs, segment indexes, process
IDs, and worker thread IDs provide correlation where applicable.

## Configuration and startup

Keep credentials in `backend/.env`, never in a tracked example:

```dotenv
OPENOBSERVE_URL=http://localhost:5080
OPENOBSERVE_ORG=default
OPENOBSERVE_USER=your-account
OPENOBSERVE_PASSWORD=your-password
OPENOBSERVE_ENABLED=true
```

Alternatively, `OPENOBSERVE_TOKEN` accepts the base64 Basic-auth credential value
without the `Basic ` prefix. Set `OPENOBSERVE_ENABLED=false` for isolated tests.

Restart both application services after this change: the conversation API
(`uv run main.py` in `backend`) and CV (`uv run cv/main.py` in `backend`).
The frontend must also be rebuilt or refreshed. Existing processes keep their
old code until restarted. External inference servers are not restarted or
reconfigured by this implementation.

From the repository root, provision and verify without loading inference models:

```bash
backend/.venv/bin/python backend/scripts/setup_observability.py --consolidate
backend/.venv/bin/python backend/scripts/setup_observability.py --verify
```

The verification checks real exporter delivery to all streams, runs each saved
dashboard query, and checks table column configuration. Table tiles enable dynamic
result columns. To repair an older dashboard without changing its queries or
layout, run `backend/.venv/bin/python backend/scripts/setup_observability.py --repair-tables`. Provisioning preserves an existing combined dashboard.
Consolidation verifies its panels before deleting the seven separately named
Virtual Human dashboards from the earlier setup; other dashboards are untouched.

## Model process and resource ownership

The CV coordinator starts three persistent spawned provider processes:
`cv.objects`, `cv.faces`, and `cv.keypoints`. Each keeps its initialized models,
track state, and existing executor/CPU policy. Frame and result messages cross a
serialized process pipe. Local Faster Whisper and Supertonic providers also run
in separate persistent processes when selected. The API and CV coordinators
are measured separately.

Each process samples CPU and memory every two seconds. CPU 100% means one
logical core, so multithreaded inference can exceed 100%. RSS includes the Python
runtime and shared pages; USS shows private resident memory. These values are
attributable to the provider process, not just its model weights. GPU memory is
not measured. Process isolation adds Python/runtime memory and frame-copy costs.

For configured local HTTP/WebSocket inference servers, the API samples the
listener process when the operating system exposes its PID. These records are
labeled `external_listener_process`. A container proxy is not a measurement of
the model inside that container. Remote services, including the configured hosted
LLM, do not expose their host RAM/CPU to this application. Their request timings
are still logged; host resource telemetry must run on their own machines.

## Delivery and local files

Application diagnostics and Python logging export through a bounded in-memory
queue. Credentials are redacted. Transcripts and model requests/replies are
retained in OpenObserve. The exporter retries failed batches, reports dropped
record counts, and never writes a disk spool. If OpenObserve is not installed, unreachable, or unconfigured, a clear startup
warning disables remote logging without stopping the API, CV, or model processes.
An ingestion failure after retries also disables logging until restart. Queue
overflow can lose events; the drop counter is included in subsequent records.
There is no local log-file fallback. Explicit provisioning/verification commands
still report errors if the observability service is unavailable.

`backend/metrics.csv` remains the application CSV output. File diagnostics and
resource-monitor file output are removed. The standalone resource monitor now
exports measurements to OpenObserve; its former `--output` option is rejected.

Thirty legacy log files were exported successfully and removed from
`backend/logs`. OpenObserve rejected events outside its five-hour ingestion
window, so historical imports use their import time as `_timestamp` and retain
the original time in `timestamp` and `original_event_timestamp_us`, with
`historical_import=true`. Use those original fields for historical analysis.

## Inline animation tags

`TalkingHead/animation_catalog.json` is shared by the parser and frontend.
The prompt lists only its allowed tags. The current tags are `[clapping]`,
`[enthusiasm]`, `[greeting]`, `[laughing]`, `[nod_no]`, `[nod_yes]`, `[talking]`,
`[thankful]`, and `[thinking]`. For example:

```text
You did it! [clapping]Congratulations!
```

The incremental parser handles tags split across model chunks and ignores
surrounding whitespace in allowlisted tags. It sanitizes unsupported symbols and
controls before sending displayed deltas, removes tags from speech, drops unknown
bracket tags, and rejects incomplete or oversized tags. Gesture cues travel separately in
each speech segment. Anchors are mapped through speech normalization to provider
word timestamps; mismatched word lists cause the cue to be omitted.

The frontend preloads required clips and schedules cues from the actual audio
source start, including later audio-playlist items. It uses the audio clock for
cue dispatch. Valid non-talking tags are mandatory and queue in order when the body is busy;
Talking cues alone may be skipped. Queued foreground gestures can start after
their word, because speech is not paused. Foreground tags preempt camera reactions
and Talking motion. The speech-to-audio playlist preserves each start callback.
This aligns available clip starts to words; precise stroke alignment would require
annotated stroke offsets in the catalog. Supertonic timings remain estimated;
Kokoro uses provider timestamps.

During speech, automatic hand motion randomly alternates among `Talking.fbx`,
`Talking2.fbx`, and `Talking3.fbx`, without immediate repeats. Only shoulder, arm,
and hand channels are used. Tagged gestures preempt these background clips;
background motion waits when a foreground cue is imminent and resumes only
after foreground actions finish, if audio is still playing. Stop clears audio cues,
clip loading, and active animations. Manual buttons remain sequential.

Camera detections also produce animation responses: wave → Greeting, thumbs up →
Enthusiasm, open palm → Nod Yes. Repeated detections are debounced. Camera
reactions wait behind higher-priority actions and expire after five seconds;
inline speech tags take priority. Activity text alone is not the response path.

After adding or renaming FBX assets, run:

```bash
python3 backend/scripts/sync_animation_assets.py
```

This updates deployed files, the shared catalog, and the allowed-tag line in the
prompt, removing obsolete deployed FBX files. Restart the backend and refresh or
rebuild the frontend afterward. Talking variants are grouped under `[talking]`.

## Validation

Backend regressions cover parser chunk boundaries, speech cleanup, cue alignment,
model-process isolation, cancellation, log redaction, and bounded ingestion.
Frontend checks cover cue timing, collisions, random talking selection,
preemption, and hand-only playback. OpenObserve verification uses real ingestion
and search. Browser visual acceptance and a concurrent live-model session are
separate from these lightweight checks.
