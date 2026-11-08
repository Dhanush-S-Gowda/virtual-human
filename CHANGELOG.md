# Changelog

## Unreleased — local demo integration

Change owner: **Bhvan Madhusudhan**.

This entry covers the local changes made during this implementation session.
It does not claim authorship of upstream commits.

### Added

- Redis persistence for the Celery broker and task results, using separate
  Redis database numbers.
- A Celery worker that saves quantitative conversation metrics to SQLite.
  Event IDs make redelivery idempotent. Transcripts and camera data are excluded
  from queued metrics. Existing CSV metrics remain available as a fallback.
- Inline speech-speed tags through the existing parser: `[speed:0.85]` for
  slower speech and `[speed:1.2]` for faster speech. The parser rejects invalid
  values and clips finite values to configurable minimum and maximum limits.
- Persistent volumes for chat, speech, and camera models, metrics, and camera
  identity data.
- Camera inference in Compose, with its model assets and required EGL/GLES
  runtime libraries. The browser uses a same-origin camera WebSocket proxy.
- Tests for speed clipping, invalid and chunked tags, TTS rates, direct demo
  access, metrics idempotency, and storage failure handling.

### Changed

- Verified the folder reorganization: 321 backend tests and 7 subtests passed
  (1 optional model test skipped), and 109 frontend tests passed. Both Docker
  images built; GPU and CPU Compose files validated; the rebuilt app and API
  health endpoints returned HTTP 200.

- Organized the application into `backend/`, `frontend/`, and `devops/`.
  Moved container builds, Nginx configuration, GPU overrides, and the Windows
  launcher under `devops/`. Retained the root Compose entry point and updated
  asset synchronization, build paths, commands, and documentation.

- Refreshed the README with separate GPU, CPU-only, and host setup steps,
  current service routing, speech controls, Celery/Redis storage, and checks.
  Removed obsolete sign-in and Docker configuration instructions.

- Replaced the frontend preview server with a multi-stage Node/Nginx image.
  Builds include the shared TalkingHead catalog and avatar assets.
- Added Docker-specific local model settings. Model initialization downloads
  Faster-Whisper and Ollama assets and warms chat before API startup.
- Added an NVIDIA override for RTX acceleration in Ollama and Kokoro. The base
  Compose file remains available for CPU-only machines.
- Changed Qwen chat to validated structured JSON plans to avoid malformed
  native XML tool calls. Added stop markers for leaked thinking-control text.
- Reduced Docker speech segments to 8–12 words with one synthesis request at
  a time. GPU Kokoro provides measured word timestamps for gesture alignment.
- Reduced camera inference rates to limit CPU contention while retaining the
  independent local preview.
- Routed browser API requests through the app origin. Only the frontend has
  a host port; API and camera sockets are proxied through Nginx.
- Updated Docker documentation and Python dependency locks.

### Fixed

- Bug review on 2026-11-08: bounded Redis result-store retries as well as broker
  timeouts. A real isolated outage returned in 0.23 s, compared with 19.27 s
  before the fix. Local CSV metrics remain available when publishing fails.
- Prevented a cancelled microphone flush from sending an end event to the next
  recording or disconnecting its audio resources. New captures release any
  previous capture resources before replacing them.
- Rendered camera names, actions, and IDs as text instead of inserting them
  into HTML. Names containing markup can no longer create elements or handlers.
- Corrected text encoding in the README and changelog.
- Preserved local work during GitHub pulls using backup stashes and restored
  the changes after fast-forward updates.
- Resolved API host-port conflicts with the old STT/TTS containers. The current
  stack exposes the API through the frontend Nginx proxy.
- Included the backend's required animation catalog in its container image.
- Fixed HTTPS camera connections and allowed preview startup before CV is ready.
- Prevented duplicate camera acquisition attempts and improved camera-permission
  error messages and the distinction between preview and connected inference.
- Created missing metrics directories and prevented disk or broker failures
  from aborting an otherwise successful conversation.
- Preserved historical CSV columns during schema migration and restored
  per-segment speech timing columns.
- Returned a clear validation error for malformed speech-speed inputs instead
  of a generic conversation-pipeline failure.
- Kept shell scripts in LF format to prevent Bash failures after Windows
  checkouts. Removed optional downloaded voice assets from unit-test fixtures.
- Updated stale test assumptions about configurable segmentation and speaker
  display labels; explicit fixtures now define segmentation expectations.

### Upstream updates included

- `2f5ea72`: Synchronize Gestures With Captioned Speech.
- `0e98e9e`: Add laughter mouth motion with speech priority.

### Removed at user request

- Removed Keycloak, the OAuth2 Proxy gateway, PostgreSQL identity service,
  custom login themes, sign-in scripts, account records, and authentication
  checks. The local demo opens directly at http://localhost:4173.
- Removed sign-out controls and the account guide. Celery, Redis, quantitative
  metrics persistence, speech-speed controls, and camera fixes remain enabled.
- Stopped and removed only the authentication containers. Existing Docker
  volumes were retained.

### Validation and limits

The latest full review passed 320 backend tests and 109 frontend tests, including
7 speech-speed parameter subtests. One optional MediaPipe model-assets test was
skipped because its downloaded assets were not mounted into the test container.
New regression tests cover a real Redis outage, microphone cancellation, and
literal rendering of camera identity data. Both Docker image builds passed.
The avatar page and API health returned HTTP 200 without a login
redirect. Speech and camera WebSockets worked without cookies or identity
headers. A real Celery task passed through Redis and saved its result in SQLite.

Earlier conversation checks measured warm first audio at 2.76 s in one
sample; an earlier sample after startup took 8.15 s. These measurements do not
guarantee latency. Physical microphone capture and camera recognition were not
verified in the browser. Speech-speed preference across later turns depends on
the model retaining and repeating the tag in conversation history. CV remains
a shared local scene.
