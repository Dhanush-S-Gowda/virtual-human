> Runtime update: local providers now run in dedicated spawned processes, each
> retaining its model executor. Logs and resource telemetry go to one OpenObserve
> dashboard. See [OBSERVABILITY.md](OBSERVABILITY.md); measurements below describe
> the earlier execution topology unless stated otherwise.

# Runtime execution and response latency

Updated 2026-11-01. These limits apply to models loaded inside this application.
Nemotron, Unsloth, and other external services keep their existing CPU policy.

## Processes and functions

| Work | Entry point | Where it executes |
| --- | --- | --- |
| Object detection/tracking | `YOLOProvider._detect` | CV process, dedicated object executor; PyTorch native pool |
| Face matching/name storage | `InsightFaceProvider._recognize`, `_rename_track` | CV process, dedicated face executor; ONNX sessions and SQLite |
| Pose, face/lips, hands | `MediaPipeProvider._detect` | CV process, dedicated keypoint executor; MediaPipe native graph threads |
| Local Faster-Whisper, if selected | `_transcribe` | API process, dedicated STT executor; CTranslate2 native workers |
| Nemotron, when selected | `NemotronStream.send_audio`, `finish` | API event loop handles WebSocket I/O; inference stays in the external service |
| LLM | `OpenAIProvider.stream_events` | API event loop handles HTTP/SSE; inference stays in Unsloth |
| Local TTS | `SupertonicProvider.synthesize_segment`, `_synthesize` | API process, dedicated TTS executor; bounded ONNX sessions |
| JPEG decode | `CVService._decode` | CV process, general executor |
| Avatar, preview, controls | `main.js` | Browser main thread; microphone capture uses AudioWorklet |

An async task is not a CPU thread. Each process has an event loop. Blocking model
calls leave that loop and run on model-specific workers. Native libraries can
create more threads. They are not six independent Python processes, and assigning
one Python thread does not mean that inference is single-threaded.

## CPU policy

`runtime.ModelWorker` creates one executor per local model stage. It initializes,
runs, and closes that provider on its worker. On Linux, the worker narrows its own
thread affinity before model initialization. New native threads inherit that
allowed set. Existing native pools may have their own behavior. ONNX uses explicit
intra-op limits, one inter-op thread, and disabled idle spinning. PyTorch and
Faster-Whisper receive explicit thread counts. MediaPipe does not expose the same
native-thread control through its public task options; its CPU set bounds where
the group can run.

Automatic allocation prefers one logical CPU per physical efficiency core when
Linux topology reports distinct maximum frequencies. On this i5-1235U:

| Local stage | Allowed logical CPUs | Native thread budget |
| --- | --- | --- |
| Objects | 4, 5 | 2 |
| Keypoints | 6, 7 | MediaPipe-managed, within this CPU group |
| Faces | 8 | 1 |
| TTS | 9, 10, 11 | 3 |
| Local STT alternative | 9, 10, 11 | 3 |

The local model groups leave the performance cores, CPUs 0–3, unrestricted for
other work. This is not exclusive core reservation: external services and the OS
can still schedule on the efficiency cores. No external process is pinned,
restarted, or reconfigured. On other hardware, automatic groups are recomputed.
Small machines share groups. Affinity is best effort where the OS lacks the Linux
API. Set `model_workers.<stage>.cpus: []` to disable affinity or specify an allowed
CPU list; `threads` controls supported native pools.

The CV `WindowManager` samples objects/keypoints independently, skips busy stages,
and attaches late results to their original capture windows. A keypoint job awaits
same-frame object results if scheduled together. MediaPipe has separate VIDEO
tracking state per person so one person's crop cannot update another's tracker.
Absent groups expire after one second; visible groups survive sparse input. See
[the measured CV profile](CV_PROFILE.md) for delivery-cadence and graph-rebuild findings. Four face jobs can be outstanding, but
the face executor serializes them. `FrameRecord.stage_ms` holds stage elapsed time
including dependencies/queueing. `WindowSummary.stage_ms` holds stage, mean ms, and
observation count. This is elapsed stage time, not isolated native CPU time.

`ConversationManager` owns bounded sessions and the last 128 turn timing results
separately from CV windows. The
conversation handler streams STT, snapshots the release-time speaker, waits for
the full transcript, fetches current world state, runs bounded model/tool rounds,
and sends ordered TTS segments. Local TTS is serialized on its model worker; the
async queue still bounds outstanding segment tasks.

## World state and tools

Every LLM turn receives objects as distinct label/track pairs, visible people as
name/track pairs, and speaker evidence from that Space hold. No wave or other
gesture observations are included. Names come from CV, not client-provided name
strings. A stale camera session or track generation invalidates the hint. With
several equally plausible speakers, the speaker remains unknown. With one visible
person, attribution is a single-person heuristic, not acoustic diarization.

| Tool | Arguments | Effect |
| --- | --- | --- |
| `rename_person` | `track_id`, `name` | Rename a currently visible identity; persist its name and available embedding |
| `gesture` | `action`: `wave`, `thumbs_up`, `namaste` | Dispatch the matching frontend action |
| `emotion` | `action`: `nod`, `happy` | Dispatch the matching frontend action |

The server validates names, enums, visible targets, camera session, and track
generation. A model request based on an old scene cannot rename a recycled track.
With multiple people, a rename also needs the attributed speaker or a user request
that explicitly selects a unique current name or track ID. Otherwise it returns
an ambiguity error for the model to clarify.
Native tool calls and tool results are separate from spoken content. At most
three model rounds run per turn. Action success means dispatch to the frontend,
not independent verification of rendered movement.

SQLite stores persistent person IDs and face embeddings, not tracker IDs. Unknown
faces keep retrying recognition even after a usable embedding was captured. An
observed absence followed by reentry clears the temporary association and triggers
fresh matching. A saved name without an embedding cannot be recognized by face.

## Push-to-talk and UI

One successful Space hold starts one speaking session. Release/cancel ends it.
Silence, missing landmarks, or track departure do not repeatedly start/end speech.
Lip motion helps choose among multiple visible people; it does not gate recording.
The avatar occupies the stage. Camera details and landmark drawing are collapsed
by default. Perception feedback is a temporary overlay. `/health` supplies the
active STT provider instead of a hardcoded label.

## Measurements and interpretation

These are small checks on this machine, not a representative benchmark:

| Check | Observation |
| --- | --- |
| Local MediaPipe, synthetic blank person crop, three models | Warm mean 61.1 ms across six measured frames; initialization 710 ms |
| Local Supertonic, F1, two steps, synthetic greeting | 547 ms synthesis for 2583 ms of audio; real-time factor 0.21; initialization 1794 ms |
| Nemotron, paced 100 ms packets of that generated greeting | Connection 43 ms, first partial 864 ms, finalization 367 ms; full transcript returned correctly |
| Unsloth LFM2.5-1.2B BF16, test scene + tools | 496 prompt tokens; prompt processing 3336 ms; server decode rate 5.40 tokens/s; client TTFT 5444 ms |
| Native gesture tool round trip | Valid wave dispatch, then a normal model reply consuming the tool result |

The synthetic MediaPipe image had no real face/hand motion. It measures execution
cost, not recognition accuracy or gesture latency. The checks did not reproduce a
full camera workload concurrently with external inference. No UI browser surface
was available for visual verification.

The former tokens/s value was a byte-count estimate divided by total LLM latency.
It included prompt processing and is not comparable to server decoding speed.
Metrics now use server `timings.predicted_per_second`; absent data remains null.
`llm_requests` contains each request's actual token counts and server timings.
The headline rate is for the last response when a turn includes tools.
`recording_ms`, `post_release_ms`, `first_speech_after_release_ms`,
`world_context_ms`, and `stt_stream` separate capture from response processing.
`stt_total_ms` measures work after release; live STT also ran during capture.

A shorter base prompt reduces prompt work, but world data, tool definitions,
history, server queues, cache state, and simultaneous inference still affect
latency. Tool use may require a second LLM call. The model can ignore style rules;
the deterministic TTS normalizer still removes emoji and formatting before speech.
Glances/OpenObserve setup remains in the root task list until installation.

## Structured avatar action plans

`llm_tool_mode: structured` asks the model for a schema-constrained object containing
`reply_kind`, up to three chosen `actions`, and spoken `speech`. This also permits spontaneous
model-selected actions. Native function calling remains available with `native`.
The active Qwen 0.8B endpoint skipped native calls in several live tests, including
one forced-function request; a native example helped but was inconsistent.
Unsloth's `enable_tools: false` disables its server-side tools, not our client-defined
actions. See [Unsloth's API reference](https://unsloth.ai/docs/basics/api#tool-calling).

The app buffers and validates the complete structured response before dispatch.
Partial JSON never reaches TTS or the avatar. Known private identifier patterns
are rejected, with one bounded regeneration attempt. Existing action allowlists
and identity/source-generation checks still apply. Actions dispatch before their
spoken acknowledgement; failures return to the model for a corrected plan or a
simple explanation. No action is selected by keyword routing. Structured output
adds completion-buffering latency before first speech; successful actions require
one model request instead of a native tool call followed by a second response.

The model chooses `speaker_identity` for speaker questions. For that intent the
application renders the validated speaker name, or an explicit unknown answer,
instead of trusting generated identity prose. This prevents contradictory
identity claims and avoids unrelated animations for a speaker-identification reply.
Structured decisions and their short spoken replies use `llm_plan_temperature`
(default 0.2). Camera state follows format examples and sits next to the real
request; examples never dispatch actions or enter persisted conversation history.

Clear, direct current-speaker queries also have a narrow perception lookup in
`Conversation`: they return the validated name without calling the LLM or
animating the avatar. Questions about recordings, languages or multiple topics
continue to the model. This avoids the 0.8B model's observed contradictory
speaker answers and eliminates generation latency for the direct lookup.
Final regression suite: 188 passed. Final acceptance probes returned the grounded
speaker reply and dispatched real model-selected wave and thumbs_up plans through
a test action callback; the live frontend itself was not driven by these probes.

## Nemotron streaming and Sayline comparison (2026-11-02)

The browser sends mono PCM16 in approximately 100 ms AudioWorklet packets while
Space is held. The API opens a Nemotron WebSocket and forwards those packets
immediately. Releasing Space flushes the last packet and commits the stream.
Only committed final text reaches the LLM. This is streaming ASR, not an uploaded
recording followed by batch recognition.

Read-only comparison with `/home/dhanushsgowda/Downloads/sayline/src/controller.cpp`
found that Sayline uses the same realtime transcription protocol. It displays
partial text while listening; our UI displays the final transcript after commit.
Sayline connects directly from its native microphone client, whereas this app
passes through the conversation API. Sayline keeps its owned model warm for five
idle minutes; this app leaves server lifetime to the user. These code differences
explain visible behavior and possible overhead, not a measured engine-speed gap.
Compare `stt_stream.finalization_ms` after release separately from LLM and TTS time.
No Sayline files or external services were changed.

The current app URL uses port 8080. Sayline's source launches its server on port
18080. A refusal at 127.0.0.1:8080 means that connection was not accepted; starting
the intended server there or correcting `nemotron_url` is required. The app now
reports an actionable message rather than an unexplained pipeline failure.

## Frontend voice selection

Voice selection is no longer read from YAML. Frontend Settings stores per-provider
voice choices in browser storage and sends `voice` in the recording's WebSocket
`start` event. The provider remains configured server-side. `/health` advertises
the active provider's allowed voices. `tts.voices.resolve_voice` validates requests:
Kokoro accepts `am_puck` or `af_heart`; Supertonic accepts `F1` or `M1`.
Clients that omit voice use a provider-specific compatibility default. No request
can change the configured provider. Supertonic resolves only the supported local
style names and warms both style files without choosing a global user voice.
