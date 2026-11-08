# Changelog

Local change owner: **Bhvan Madhusudhan**. Upstream commits retain their original
authorship. Entries describe implemented changes and the checks actually run.

## 2026-11-08 - Prompt grounding and response quality

Change owner: **Bhvan Madhusudhan**.

### Changed

- Reorganized the assistant prompt around direct answers, real conversation
  memory, current camera evidence, supported expression, and honest runtime facts.
- Removed fictional demonstration turns from structured model requests. Real
  history stays intact, while the response schema supplies the output format.
- Added configured model names and speed limits without exposing credentials
  or provider URLs. Moved runtime facts beside the latest user request.
- Stored common direct English speech preferences per conversation. Structured
  replies retain an explicit user rate and drop unrequested speed changes;
  performances can still vary speed.
- Kept the prompt's allowed animation list synchronized with the catalog while
  excluding automatic talking tags from suggested model output.

### Validation and limits

- Added a repeatable 15-scenario local Qwen comparison, with original/final
  replies and development candidates under `docs/prompt-evaluation/`.
- Basic application-level answer/control checks improved from 13/15 to 15/15
  in one local-model comparison. This includes code/context changes and is not
  an isolated prompt-wording experiment or a general accuracy score.
- 337 backend tests and 7 subtests passed; 1 optional model-assets test skipped.
  All 51 targeted checks also passed after the final speaker-rate adjustment.
  Some unnecessary or semantically mismatched gesture tags remain in model
  responses. Physical camera and TTS behavior were not part of this evaluation.

## 2026-11-08 - Recorded Qwen 4B benchmark report

Change owner: **Bhvan Madhusudhan**.

### Added

- Created a recorded-conversation benchmark under
  `docs/benchmarks/2026-11-08-qwen4b/` with timing distributions, reliability,
  selected turn CSV, machine-readable results, and a source SHA-256.
- Added an Excel workbook with formula-based timing summaries, reliability,
  successful-turn measurements, and the preserved selected source records.
- Added `summarize_benchmarks.py` to select an exact model, exclude provider-only
  tests, retain unsuccessful turns in reliability, and use nearest-rank P95.
- Added regression tests for sample selection, failure denominators, percentile
  calculation, missing-versus-zero measurements, and source preservation.

### Results and scope

- Selected 20 Qwen 4B conversation rows from 121 source records. Other-model
  rows were excluded. This analyzes existing recordings; no new full
  conversation benchmark was run after the latest camera and gesture fixes.
- Completed turns: 20/20. Release to first audio: mean 3.72 s, median 2.87 s,
  P95 5.29 s, maximum 17.80 s. Total turn includes recording time.
- Four new summary tests passed. Workbook formulas matched the independent
  Python calculations and responded to input changes. Source CSV retained.

## 2026-11-08 - Camera grounding and word-aligned gesture playback

Change owner: **Bhvan Madhusudhan**.

### Fixed

- Scoped unidentified-speaker instructions to identity questions. People-count
  questions now explicitly use the current scene counts without an unrelated
  claim that the model lacks camera access.
- Kept fresh camera queries before every turn and added a regression test that
  sends two successive frames through the CV WebSocket and conversation path.
  The model receives updated counts, not the first turn's cached scene.
- Added camera frame age and availability reasons. The model and Activity panel
  distinguish missing frames, expired observations, query timeouts, and service
  failures from a current scene with zero people.
- Changed default gesture pacing to follow tagged words with 100 ms cross-fades.
  Different clip lengths no longer add pauses before later emotional phrases.
  Full-clip completion remains available with `gesture_pacing: complete`.
- Preloaded gesture clips while idle and shared in-flight loads with arriving
  speech so the same clip is not downloaded twice.
- Added a duration-based word/token budget for requested performances and one
  bounded retry for short drafts. Exact spoken duration remains model-dependent.

### Validation

- 326 backend tests and 7 subtests passed; 1 optional model-assets test skipped.
  After the last prompt adjustment, all 75 targeted regression tests passed.
  All 111 frontend tests passed. Backend and frontend Docker images built.
- Real local Ollama/Kokoro checks with controlled world-state fixtures answered
  two people, then one person, and reported unavailable observations honestly.
  These fixtures do not verify physical camera recognition.
- Ran the requested drama prompt through local chat and Kokoro: seven timed
  gestures and speed changes from 0.85 to 1.2 completed without errors. The
  final sample lasted 42.35 seconds for a 30-second request; timing is approximate.
  Browser visual alignment and physical camera recognition still need a live
  device check.
- Explained the difference between recording, server response, and browser
  playback timing. Instant audio is not guaranteed by this sequential pipeline.

## 2026-11-08 - Folder guides and workspace cleanup

### Added

- Added a README to each maintained application folder and subfolder: 28 guides
  across backend, frontend, DevOps, and documentation. The guides explain
  responsibilities, important files, related folders, and relevant commands.
- Added focused guides for runtime architecture, configuration and stored data,
  host development and verification, and troubleshooting under `docs/`.
- Added a Virtual Human integration section to the existing TalkingHead README.
  Preserved upstream documentation and licenses, including Supertonic guides.

### Changed

- Shortened the root README to the demo commands, folder map, service overview,
  and links to detailed guides. Moved the technical explanations into `docs/`.
- Grouped changelog entries and corrected the previous full-suite result to
  321 backend tests, with 109 frontend tests, 7 subtests, and 1 optional skip.

### Removed

- Removed empty `auth/`, `deploy/`, and `.local/keycloak/` leftovers from the
  retired sign-in setup. Removed the empty `.local/` container folder as well.
- Removed obsolete root `scripts/` and `tts-server/` folders after verifying
  they contained only generated Python bytecode and no tracked source files.

### Validation

- Checked 37 project Markdown files and 154 local links, including filename
  case and section anchors. All links passed, and every maintained application
  folder has a README.
- Validated the GPU and CPU Compose configurations and checked whitespace.
  Application tests were not rerun for this documentation and cache cleanup.

## Local demo integration - unreleased

The application layout and demo improvements below are recorded in commit
`96ebe34`. The earlier upstream changes are listed separately.

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

The full regression run after folder reorganization passed 321 backend tests,
109 frontend tests, and 7 subtests. These results predate the documentation-only
cleanup recorded above. One optional MediaPipe model-assets test was
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
