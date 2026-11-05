# Virtual human issues and implementation tasks

## Kokoro streaming and performance

- [x] Inspect the current synthesis, ordered delivery, and avatar playback paths.
- [x] Check the active Kokoro API version and supported streaming contract.
- [x] Confirm whether to retain Kokoro 0.8.1 or upgrade for streamed text input.
- [x] Implement the selected streaming path with word and gesture timing preserved.
- [x] Add focused streaming, ordering, cancellation, and error regressions.
- [x] Compare first playable audio and total synthesis time against non-streaming.
- [x] Run relevant checks and report measured performance and limits.

Kept the active Kokoro 0.8.1 Docker service. Audio output now streams as playable
WAV chunks with local word times and ordered gesture delivery. The 101-word live
probe produced 35.09 seconds of speech: first playable audio arrived at 21.08 s
instead of the non-streaming baseline's 35.44 s; completion was 29.24 s instead
of 35.44 s. This single-run comparison excludes browser playback and LLM/STT;
short requests may gain little. See `backend/KOKORO_STREAMING.md` for limits.

## Required foreground gestures, camera responses, and optional logging

- [x] Restore animation responses for camera wave, thumbs up, and open palm.
- [x] Debounce camera reactions and give inline tags priority over them.
- [x] Make non-talking tags mandatory, queued rather than dropped if busy or late.
- [x] Keep Talking motion lowest priority and suppress its start before an imminent foreground cue.
- [x] Disable unavailable/unconfigured OpenObserve without breaking core services or creating log files.
- [x] Add queue-priority, camera response, missing-service, and missing-credentials regressions.
- [x] Complete final frontend/backend checks and the bounded build.

The latest user instruction supersedes the earlier skip-conflicting-cues policy:
valid non-talking tags must play unless cancelled or the asset fails. Speech keeps
flowing, so queued gestures may start later than their word. Camera wave maps to
Greeting, thumbs up to Enthusiasm, and open palm to Nod Yes. These are animation
responses, not spoken acknowledgments. Missing logging infrastructure now warns
and disables only remote logging; the API startup/health regression proves this.

## Dashboard table visibility and runtime prompt check

- [x] Verify that live event streams contain CV, STT, LLM, TTS, and application records.
- [x] Verify the latest actual LLM request includes the inline-tag instructions.
- [x] Identify empty table field mappings as the cause of blank non-resource tiles.
- [x] Configure table fields and update the existing dashboard safely.
- [x] Add field/data validation, regression coverage, and verify the saved dashboard.

## Missing playback of extracted inline gestures

- [x] Confirm actual TTS logs contain validated greeting/clapping cues with provider word times.
- [x] Trace the dropped onAudioStart callback between speech and audio playlists.
- [x] Preserve the callback and test real TalkingHead queue traversal and action priority.
- [x] Run focused regressions and bounded frontend build.

Latest validation: 79 frontend tests and 56 focused backend regressions passed;
the bounded production build passed. Live OpenObserve verification confirmed
delivery/search in all eight streams and data/visible columns for all 13 saved
dashboard tiles. No browser or real inference model was launched. Existing
metrics.csv CRLF whitespace was left unchanged.

## Inline gestures and OpenObserve

- [x] Inspect and confirm OpenObserve access, organization, and resource attribution limits.
- [x] Parse and validate inline gesture tags before display and TTS.
- [x] Attach gesture cues to synthesized speech and schedule them with audio playback.
- [x] Replace application file logs with bounded OpenObserve ingestion and structured event streams.
- [x] Isolate locally loaded providers in persistent processes and collect CPU/RSS/USS telemetry.
- [x] Consolidate the seven earlier dashboards into one verified dashboard with 13 tiles.
- [x] Migrate and remove all 30 local application log files, retaining metrics.csv.
- [x] Run parser, synchronization, logging, resource, and pipeline regressions plus frontend build.

The final dashboard is **Virtual Human**, ID `7522936825833324544`. The user chose
dedicated local model processes for attributable RAM and skipping conflicting or
late co-speech gestures rather than delaying speech. Actual exporter delivery to
all eight event streams and all 13 tile queries were verified against OpenObserve.
Its five-hour ingestion limit required importing older records at migration time;
original event times remain in the records. A final file scan found only the
application's `backend/metrics.csv`, with no application .log/.jsonl files.

## Renamed assets and automatic talking animation

- [x] Build a shared catalog from the current TalkingHead/animations files.
- [x] Remove Dance, Pose, old built-in buttons, and obsolete deployed FBX files.
- [x] Generate allowed inline tags and animation controls from the shared catalog.
- [x] Randomly alternate the three Talking variants during speech, using only hand/arm channels.
- [x] Give tagged actions priority and resume background talking motion while speech continues.
- [x] Test current clips, random choice, interruption, and channel masking.

There are 11 FBX files and nine public tags: clapping, enthusiasm, greeting,
laughing, nod_no, nod_yes, talking, thankful, and thinking. The user selected
automatic talking motion with tag priority. `sync_animation_assets.py` updates
the catalog, prompt tag list, controls' data, and deployed files after asset edits.

Final validation: 221 backend regressions passed with the real MediaPipe smoke
test excluded. Three focused process tests also passed, including an additional
crash/shutdown regression added afterward. All 71 frontend tests and the bounded
production build passed. OpenObserve delivery/search and every dashboard query
passed live verification. No browser or real inference models were launched.
Restart the API and CV services to apply logging/process changes; refresh or
rebuild the frontend. See `backend/OBSERVABILITY.md` for operation and limits.

## Natural dialogue prompting and gesture synchronization research

- [x] Inspect current prompting, TTS word timings, speech playback, and gesture dispatch.
- [x] Research primary sources on affective dialogue and speech/gesture coordination.
- [x] Refine the compact Alex prompt and contrasting shared examples.
- [x] Document a recommendation for scalable gesture selection and audio synchronization.
- [x] Validate examples and run focused prompt/tool regressions.

Alex's base prompt is 168 words and shared native/structured examples cover seven
contrasting situations, including playful conversation, celebration, frustration,
sadness, factual replies, identity uncertainty, and an explicit action sequence.
Primary-source research and the implementation-specific recommendation are in
`backend/GESTURE_COORDINATION.md`. The recommendation uses the existing model for
speech/gesture intent and a deterministic audio-clock scheduler for realization.
It records the current transcript-only affect input, lack of emotional TTS
controls, approximate Supertonic word timing, and FIFO playback limitations.
The proposed scheduler and future schema are not implemented in this change.

Validation: all 48 focused backend prompt/tool/plan regressions passed. No live
model evaluation, audio perception changes, browser, or benchmark was run.

## Additional animation assets

- [x] Inspect new FBX clips and the new avatar's skeleton and blend shapes.
- [x] Confirm deferring Casual Guy: the supplied GLB is a static mesh.
- [x] Add Hands Forward Gesture, Thankful, and Thinking to frontend playback and tools.
- [x] Copy deployable assets and verify clip/tool/control coverage.
- [x] Run focused regressions and the bounded frontend build.

All three supplied FBX files parse as Mixamo clips and are deployed through a
shared clip registry. Controls and model actions retain sequential playback.
Casual Guy remains excluded at the user's request until a rigged version with
facial blend shapes is available. Validation: 48 focused backend tests, 53 frontend
tests, and the bounded production build passed without opening a browser.

## Raised-chin investigation and correction

- [x] Confirm the symptom spans avatars, including idle, and affects head pitch.
- [x] Inspect authored clip pitch and TalkingHead's additive pose layers.
- [x] Confirm viewer-facing head movement rather than exact authored head poses.
- [x] Limit final head pitch relative to the viewer while preserving nods and other movement.
- [x] Test idle/FBX pitch, transformed parents, and head movement within the limit.
- [x] Run frontend regressions and the bounded build.

The supplied clapping clip includes roughly 22–26 degrees of upward head pitch;
Hands Forward also includes upward head poses. TalkingHead adds idle/head and
per-avatar pitch offsets after FBX playback. A final viewer-relative pitch limit
now constrains the combined pose to about 3 degrees upward and 13 degrees
downward while preserving head yaw, roll, natural small movements, and nods.
The correction runs after animation layers via the existing update callback.
An actual-clapping regression verifies corrected pitch and unchanged arm motion.
Validation: all 58 frontend tests and the final bounded production build passed;
48 focused backend tests passed for the new animation tools. No browser or model
benchmark was launched. Visual verification across avatar presets remains open.

## Alex personality and expressive actions

- [x] Inspect prompts, native/structured examples, audio state, and FBX playback.
- [x] Confirm a neutral name: Alex.
- [x] Compact the prompt and add relevant few-shot examples for both model modes.
- [x] Add validated clapping tool, queued FBX playback, and frontend control.
- [x] Add occasional listening nods and align model actions with speech playback.
- [x] Run focused backend/frontend regressions and bounded production build.

Alex uses a 99-word base prompt and five shared demonstrations covering greeting,
encouragement, celebration, a reply without actions, and unknown speaker identity.
Clapping is allowlisted in native tools and structured plans. The supplied FBX
parses as a 1.17-second Mixamo clip; the frontend plays one cycle and waits for
the pose return. Aborting an FBX load prevents late playback. Model actions wait
for response audio, and capture nods require recent microphone activity while
other actions are idle. The participant and conversation labels now say Alex.

Validation: 42 focused backend tests, 50 frontend tests, and the bounded
production build passed. FBX parsing was verified without a browser. No real
model inference or live microphone/avatar visual acceptance was performed.

## Scrollable conversation history

- [x] Trace message replacement, streaming updates, and panel sizing.
- [x] Preserve messages and follow new messages and streamed reply text.
- [x] Keep history scrollable and keyboard accessible across existing layouts.
- [x] Test conversation history and run frontend checks and the bounded build.

Current-page messages and errors append without clearing earlier turns. Each
streamed chunk follows the latest reply. The history panel has a keyboard focus
target, stable scrollbar space, and a height bound in short desktop layouts.
All 44 frontend tests and the bounded production build passed. Browser visual
acceptance remains pending; no browser or real model was launched.

## Sequential avatar tool actions

- [x] Trace tool event dispatch and gesture completion behavior.
- [x] Queue supported gestures and emotions through playback and return transitions.
- [x] Cancel pending playback on Stop and avatar changes; prevent camera mirroring conflicts.
- [x] Add focused queue regressions and run lightweight frontend checks and build.

The frontend now serializes LLM gesture/emotion events and the corresponding
buttons. Action waits include return transitions; Stop, Idle, and avatar changes
abort waits and discard queued actions. Camera mirroring pauses during playback.
Five queue regressions and the frontend suite passed. The production build passed
with a 512 MB Node heap and two worker threads. Live visual acceptance remains
pending; no browser or real model was launched.

## Keypoint visibility investigation (2026-11-02)

- [x] Trace backend scheduling, WebSocket metadata, and frontend drawing conditions.
- [x] Check existing CV execution traces without launching models or a browser.
- [x] Run the frontend landmark regression suite; it passed. The focused backend
  pytest command was interrupted after it produced no output; no result is claimed.
- [x] Confirm the user's Camera details badge: landmarks are hidden because canvas
  drawing is gated on the disclosure state. The current request resolves the
  desired overlay behavior; “contake” was an accidental transcription.

The frontend draws landmarks only while Camera details is expanded; the disclosure
is closed by default. It also hides results older than 1.5 seconds from capture.
The inspected `backend/logs/stages-9684.jsonl` snapshot contained 1,554 completed
keypoint calls with status `ok`, a 246.2 ms median, and a 6,933.3 ms maximum.
Execution spans do not record output point counts, so they establish execution,
but do not establish that landmarks were returned or displayed. No product code
was changed during this investigation.

## Camera overlay toggle and action/TTS flow

- [x] Trace camera overlay display separately from CV streaming and decisions.
- [x] Add a top-right, default-on overlay toggle independent of Camera details.
- [x] Trace action ownership in live responses and window/world-state compilation.
- [x] Trace talking motion, inline tags, response actions, and TTS timing to playback.
- [x] Record the delivered UI behavior and evidence in this task summary.

The camera eye control is default-on; hiding overlays only suppresses canvas
drawing. Keypoint frames continue to update and feed browser gesture responses.
Object/person boxes, labels, and landmarks are controlled together; Camera details
still controls the details disclosure and track list.

Human gesture detection and temporal aggregation run in backend CV. The frontend
maps confirmed camera gestures to local avatar clips. The LLM turn snapshot
contains visible people, objects, and validated speaker evidence, not gestures;
window summaries separately aggregate detected CV gestures. LLM-selected avatar
expressions currently use inline tags, stripped from visible/spoken text, aligned
to TTS word timings, and delivered on each `speech` event with audio and timings.
The browser schedules those cues on the audio clock. Automatic talking-arm clips
are low priority and have an audio-duration stop boundary; source inspection alone
does not reproduce the reported action-blocking symptom.

## Sanitize streamed LLM speech and tolerate tag whitespace

- [x] Define a Unicode-safe text filter shared by display and TTS paths.
- [x] Apply it incrementally without corrupting gesture cue offsets.
- [x] Normalize whitespace around allowlisted gesture tags.
- [x] Add regressions for emoji, unsupported symbols, chunk boundaries, and tags.
- [x] Run focused backend regressions and report the provider behavior.

LLM text is filtered before deltas/history, with symbol/control characters
replaced by spaces and punctuation/letters/numbers retained. The TTS segment
normalizer applies the same policy before synthesis. Gesture tags allow outer
whitespace and case differences; cue offsets are calculated on sanitized text.

Requested 2026-11-01. Keep this list as the task record, including deferred setup.

- [ ] Set up Glances + OpenObserve OSS after the user installs them. Separate
  resource metrics, perception events, and conversation events with correlated views.
- [x] Clarify and fix speaking events around the Space push-to-talk session;
  prevent repeated start/end events during one utterance and preserve speaker attribution.
- [x] Replace the large system prompt with a compact prompt suitable for the
  configured CPU-hosted model; measure prompt processing and decoding separately.
- [x] Supply current world state: all detected objects and visible people with
  track IDs and known/unknown names, plus supported speaker attribution. Exclude gestures.
- [x] Add a validated model tool to rename the selected visible track and persist
  the associated face identity. Preserve ambiguity handling and tool-result feedback.
- [x] Investigate recognition after camera occlusion/reentry. Verify embeddings,
  rather than track IDs, are persistent; retry recognition when face evidence changes.
- [x] Add model gesture tools for wave, thumbs_up, and namaste; add emotion tools
  for nod and happy. Dispatch only supported actions to the frontend.
- [x] Investigate MediaPipe inference latency and temporal gesture requirements;
  make natural approximately two-second gestures usable and measure actual throughput.
- [x] Fix the UI STT label from active backend configuration.
- [x] Center the avatar; put a smaller camera preview at top right like a video
  call; reduce counters/details; replace the long event list with temporary overlays.
- [x] Document actual process/thread execution for object detection, face recognition,
  keypoints, STT, LLM, and TTS. Inspect topology, native thread budgets, and contention.
- [x] Apply the clarified CPU policy: control only models loaded inside our app.
  Use distinct local-model workers and leave Nemotron/Unsloth settings unchanged.
- [x] Measure Nemotron streaming/finalization latency, LLM prompt processing and
  generation speed, and TTS latency. Correct misleading tokens/second measurements.
- [x] Run backend/frontend regressions, build the frontend, and perform local
  model/streaming probes. See `backend/EXECUTION.md` for results and limits.
- [ ] Verify the call layout visually at desktop and 320/375/414/768-pixel widths.
  No browser surface is available through the UI tool in this session.
- [x] Add a persistent avatar zoom slider in Settings, defaulting to 15% closer
  than the previous framing.
- [ ] Verify natural two-second gestures, camera occlusion/reentry, and speaker
  attribution on live camera/audio under concurrent CV, STT, LLM, and TTS load.

## Implementation notes

- One Space hold now owns one speaking start/end pair.
- The base prompt is compact; per-turn world data excludes gesture observations.
- Model tools are `rename_person`, `gesture`, and `emotion`; names are stored with
  face identities, and stale camera/track-generation requests are rejected.
- Local model workers are separate from external inference servers. On this
  host, auto groups are objects 4–5, keypoints 6–7, faces 8, TTS/local STT 9–11.
- Glances/OpenObserve installation and live-camera/browser acceptance remain open.

Validation: 164 backend tests passed before the final multi-person rename guard;
31 focused backend/tool tests passed after that guard. All 23 frontend tests and
the production build passed. Live probes confirmed world-state grounding, a native
gesture tool round trip, local TTS, and Nemotron streaming/finalization. The UI tool
reported no available browser, so visual and full concurrent camera acceptance
remain open above.

## CV entry point

- [x] Add `backend/cv/main.py` for `uv run cv/main.py` from `backend`.
- [x] Preserve module launch compatibility and environment checks.
- [x] Update launch documentation and verify entry points.

CV entry-point validation: 6 focused tests passed; the requested uv command
was verified with `--help` from the backend folder.

## Natural replies, avatar actions, and CV lag

- [x] Inspect prompt, native tool dispatch, and CV CPU affinity.
- [x] Keep spoken replies free of implementation terminology and clarify speaker grounding.
- [x] Fix requested avatar action handling and add regression coverage.
- [x] Verify worker CPU separation and document remaining CV dependencies.
- [x] Run focused checks and report live verification limits.

The selected approach is model-driven structured plans, with validated actions
and private spoken output. Speaker-identification intent uses the validated
speaker name rather than generated identity claims. Current CV traces and the
sparse-input model-lifetime fix are documented in `backend/CV_PROFILE.md`.
Foreground live-camera sampling and gesture acceptance remain pending above.

Final validation for these changes: 188 backend tests passed. The acceptance
probe returned “You're speaking, Dhanush.” with no unrelated animation; the real
model selected and dispatched wave and thumbs_up plans for the action requests.
Clear current-speaker questions are served directly from validated perception;
other questions and every avatar-action decision remain model-driven. The sparse
MediaPipe smoke test reused all graphs across simulated long frame gaps.

## Meeting UI and streaming ASR review

- [x] Replace overlapping panels with a meeting stage, side conversation, and bottom controls.
- [x] Remove typed speech controls; add accessible emoji action buttons.
- [x] Enlarge camera preview and add bounded pointer/keyboard resizing.
- [x] Use the supplied room background and move perception events to the side.
- [x] Compare Nemotron streaming/finalization with Sayline without changing that project.
- [ ] Verify the meeting layout visually in the user’s browser; no further automated browser launch.
- [x] Run lightweight frontend unit tests; 27 passed.
- [x] Verify the production build with bounded memory and reduced worker settings.

Follow-up after the headless browser run crashed the user's system:

- [x] Use only lightweight checks; do not launch another browser or real-model benchmark.
- [x] Add static contact shading and fit the full avatar to the background rug.
- [x] Reverse the camera resize icon's diagonal.
- [x] Turn Nemotron connection refusal into a clear, actionable frontend error.

The new Nemotron connection-refusal regression passed without loading models or
opening a real server connection. See `talking-avatar/MEETING_UI.md` for controls
and `backend/EXECUTION.md` for the read-only Sayline streaming comparison.

## Meeting framing and frontend settings

- [x] Replace full-body framing with a head-and-torso meeting view.
- [x] Add an accessible settings dialog for the local TalkingHead avatar presets.
- [x] Remember avatar and per-provider voice choices in the browser and apply them per recording.
- [x] Remove YAML voice selection and validate provider-specific voices in the backend.
- [x] Run lightweight unit tests and a bounded production build; no browser/model benchmark.

Meeting settings validation: 33 frontend tests passed; 16 focused backend tests
passed for voice validation, synthesis handoff, capability metadata, YAML removal,
and Supertonic style resolution. The bounded production build passed. No browser
or real model was launched. Visual checks of the selected avatars remain pending
in the user's browser. Restart the API and refresh the frontend for these changes.

## Avatar placement and control icons

- [x] Lower the torso framing slightly and enlarge the avatar by 10%.
- [x] Disable user camera rotation, panning, and zoom.
- [x] Move Settings to the bottom left and replace settings/camera controls with Lucide icons.
- [x] Run lightweight frontend checks and the bounded production build.

Placement update validation: 33 frontend tests and the bounded production build
passed. No browser or real model was launched.
