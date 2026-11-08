# Research dashboard

Open the **Virtual Human** dashboard and select **Performance**. The
**Performance Summary** table contains sample count, reported errors, mean,
median, P95, and P99. Median and percentiles are approximate. Select the time
range before you export the table through OpenObserve.

Restart the conversation backend and CV service after installing this change.
Existing records do not contain all the new measurements. The table remains
empty until measured work arrives. A missing row or value does not mean zero
latency or zero resource use. Schema initialization records never enter the
research statistics.

## Select an experiment

Set these environment variables in `backend/.env` on every participating host:

```dotenv
VH_EXPERIMENT_ID=trial-001
VH_MEASUREMENT_PHASE=measured
```

Use `VH_MEASUREMENT_PHASE=warmup` during warm-up runs. Restart processes when
you change environment values. Only samples with phase `measured` enter the
summary and performance graphs. GPU status shows the latest availability
regardless of phase. Warm-up exclusion is explicit; the application does not guess when a
model has finished warming up.

Use the dashboard filter control to select `experiment_id`, `host`, `provider`,
`model`, or the available scene dimensions. The table separates experiments,
hosts, providers, models, image sizes, and person counts. A null scene dimension
means it does not apply to that sample. Resource charts use host and service
labels. CV throughput charts also separate camera source IDs.

Keep audio length, speech text length, camera resolution, and eligible-person
count comparable when you compare trials. Raw samples retain recording/audio
duration and TTS text length where available. Do not treat a small set of
observations as a stable estimate of P99. The table flags fewer than 1,000
successful samples as a small sample; this is an interpretation aid, not a
statistical guarantee.

## Measurement boundaries

| Row | One successful sample |
| --- | --- |
| STT completion | Space/button release in the browser to receipt of the final transcript in that same browser. Includes capture flush and network transport; excludes speaking time. The backend delivers the transcript without waiting for STT socket cleanup. |
| LLM time to first token | One actual HTTP model request to its first content or tool-call delta. Structured plan retries and tool follow-ups are separate requests. Only successful requests supply latency values. Deterministic speaker replies do not call the LLM. |
| TTS first usable audio | One successful synthesis segment, from its scheduling entry to its first audio result with timestamps. Includes provider queuing within that call; excludes waiting for enough LLM text and earlier application speech-queue waits. |
| Object detection | One successful YOLO detection/tracking execution inside its model worker, including preprocessing and postprocessing. Excludes parent RPC/serialization and dependency waits. |
| Keypoint estimation | One successful MediaPipe execution across eligible person crops, including pose, face, hands, and gesture classification. The person count and all three model names accompany it. No-person polls are excluded. |
| Object/keypoint throughput | Successful analyzed frames per elapsed interval of approximately two seconds while the camera connection is active. An interval with no completions is zero. A no-person landmark poll is not an analyzed frame. |
| Gesture parser | Accumulated inline-tag parser and cue bookkeeping cost per completed turn. |
| Gesture cue assignment | Accumulated time assigning cues to synthesized speech segments per completed turn. |
| Gesture/audio alignment | Accumulated mapping and payload preparation cost against TTS word timestamps per completed turn. |
| CV world-state fetch | Connection, request, and receipt of one matching CV WebSocket response; excludes socket close and report formatting. |
| Visual observation age | Age of the newest included capture observation at each LLM request assembly. Retained history can remain available after the current scene expires. |

Gesture computation costs do not measure visible gesture-to-audio timing error.
Browser playback timing is a separate boundary and is not currently exported
as a research synchronization-accuracy measurement. TTS does not report HTTP
time to first byte: Kokoro returns a complete captioned segment before the
application can play it.

The summary reads raw `vh_performance` samples and deduplicates by sample ID.
Errors have no latency value and are counted separately. Revised CV window
summaries do not contribute additional inference or throughput samples.
Resource sampling runs independently at approximately two-second intervals.

STT completion uses the browser's monotonic clock. After receiving a final
transcript, the browser submits its elapsed duration to `/telemetry/stt` with a
one-use ID issued by the backend for that turn. Backend diagnostics retain
server-side finalization and release-to-send timings separately. A receipt does
not delay transcript display or the LLM request. Receipts expire after three
minutes and pending receipts are bounded to 512 per API process. The current
single-worker launch supports this; a multi-worker proxy must route receipts
to the same worker as the conversation.

Older STT samples without the browser timing boundary are excluded from the
summary; they remain in the raw logs. Restart the backend and reload the
browser after updating. Clients that do not report receipts do not contribute
successful STT completion samples. If Nemotron takes several seconds after
commit to return its final transcript, the measurement retains that delay;
partial transcripts are not substituted for the final result.
OpenObserve export has bounded queues and retries, so dropped logs can reduce
sample counts. These statistics describe exported observations, not an
independent guarantee that every attempted operation was recorded.

## Read the graphs

- Host CPU and per-core heatmap show usage visible to the collecting OS.
  Existing process CPU charts use 100% for one logical core; a process can
  exceed 100%. These are different denominators.
- CPU scheduling pressure uses Linux PSI `some avg10`: the share of time
  runnable tasks waited for CPU during the preceding ten seconds. It is not
  CPU busy percentage and is absent on unsupported systems.
- GPU activity, engine activity, VRAM, and resident-memory charts show only
  available measurements. Dedicated VRAM is separate from GPU resident/shared
  system memory.
- CV FPS compares achieved completions with configured sampling limits.
  Busy-skip charts show the share of input frames skipped because that stage
  was occupied. Skips due solely to the sampling rate are recorded separately.
- Visual observation age shows whether model requests use fresh camera
  observations even when throughput is acceptable.

FPS measures throughput, not inference latency. Do not calculate achieved FPS
as `1000 / mean_latency_ms`; sampling, dependencies, person selection, transport,
and idle intervals affect the actual completion rate. Higher FPS percentiles
describe high-throughput intervals, whereas high latency percentiles describe
slow responses.

## GPU portability and service attribution

GPU collection is automatic and optional. NVIDIA devices, including the RTX
4060 Laptop GPU, use `pynvml` when its library and driver are available. The
locked CV environment already includes `nvidia-ml-py` through Ultralytics.
Otherwise, collection falls back to the driver's `nvidia-smi` utility. No CUDA
model is loaded by the collector. Failed GPU probes do not stop the app.

On Linux, non-NVIDIA processes can report DRM engine activity and resident
memory when the driver exposes `fdinfo` statistics. The collector deduplicates
DRM clients and calculates engine activity from counter deltas. It does not
invent a dedicated VRAM capacity for integrated GPUs. Driver support and OS
permissions determine which fields are available.

**VRAM by service** shows memory for known local processes and descendants of
configured local inference servers. Dedicated CV model processes retain their
roles. In-process models share the API's memory and cannot be separated.
Hosted inference has no locally observable model VRAM. Browser/avatar graphics
memory is not attributed to the LLM or TTS. Windows WDDM and container PID
isolation can prevent process-memory reporting even when device totals work.

Viewing the dashboard on a GPU laptop does not collect that laptop's GPU.
Run the application or a collector on the machine doing the work. For a backend
container without host GPU access, run this optional collector on the host:

```bash
uv run --project backend python -m backend.scripts.monitor_resources --seconds 0
```

Use that host's OpenObserve settings and experiment ID. Stop with Ctrl+C.
The monitor also accepts `--pid role=PID` for explicit local service attribution.
Local service attribution requires visible listener processes and their child
PIDs; remote servers and container proxy processes cannot prove model ownership.
Host/container measurements have separate host labels. Check **GPU status** and
its `last_seen` time before interpreting an empty chart.

## Provision and verify

Apply the Performance tab to an existing Virtual Human dashboard:

```bash
uv run --project backend python backend/scripts/setup_observability.py --update-research
```

The command establishes field types, validates all Performance queries, saves
the tab, and checks the saved query/axis/layout contract. It preserves Overview
and custom dashboard variables. Repeating it updates the managed Performance
tab without adding duplicate panels.

Run the opt-in statistics check against a local OpenObserve instance:

```bash
OPENOBSERVE_LIVE_TESTS=1 uv run --project backend --extra dev python -m pytest \
  -c backend/pyproject.toml backend/tests/test_research_dashboard_live.py -q
```

This test creates a uniquely named temporary stream and removes it afterward.
It checks mean and percentiles, duplicate sample IDs, errors, and warm-up
exclusion. It does not add synthetic samples to the research stream.
