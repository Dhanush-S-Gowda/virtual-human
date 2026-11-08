# Kokoro first-request latency

Date: 2026-11-09. Change owner: Bhvan Madhusudhan.

The test supports GPU power-state recovery as a contributor to slower speech
after idle time. It does not establish the cause of every earlier latency spike.
No production configuration was changed during this test.

## Controlled test

The running GPU Kokoro service synthesized the same six-word text with the
same `af_heart` voice and speed 1.0. The backend used its real captioned-speech
adapter and validated returned word timestamps.

| Condition | Complete captioned response |
|---|---:|
| Initial request | 850 ms |
| Immediate repeat | 140 ms |
| After 20 seconds idle | 488 ms |
| Immediate repeat after idle | 138 ms |
| After a structured Qwen 4B request | 147 ms |
| Immediate repeat after chat | 137 ms |

Before the initial and post-idle requests, the GPU reported P8 with graphics
clock 210 MHz and memory clock 405 MHz. Before the post-chat request, it reported
P0 with graphics clock 2730 MHz and memory clock 8201 MHz. GPU memory use was
5646 MiB in each snapshot. These are snapshots before requests, not a continuous
trace of clock changes or memory residency.

The installed Kokoro code warms the default voice at startup. Its idle-unload
timeout defaults to zero, and no environment override was set. The logged
five-second request had negligible backend queue wait. Our adapter waits for a
complete JSON response containing audio and timestamps, so its displayed
"first PCM" value includes that complete operation.

## Research and interpretation

NVIDIA documents latency when GPU clocks rise from idle and warns that changing
clocks can produce variable inference times. This matches the observed pattern,
but snapshots alone do not isolate clock recovery from other driver or runtime
work. See [NVIDIA inference optimization guidance](https://docs.nvidia.com/deeplearning/tensorrt/latest/performance/optimization.html).

CUDA can load modules lazily, moving initialization costs into first execution.
This explains some cold-path overhead, but startup warmup already exercises
Kokoro. It does not by itself prove why a later request was slow. See
[CUDA lazy loading](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html)
and [Kokoro startup warmup](https://github.com/remsky/Kokoro-FastAPI/blob/master/api/src/inference/model_manager.py).

NVIDIA also documents slowdowns near GPU memory limits from system-memory
fallback on Windows. That is a plausible additional factor for a shared 6 GB
GPU, but this test did not measure fallback or demonstrate it. Docker/WSL is
not identical to the native Windows application used in NVIDIA's example. See
[NVIDIA memory-fallback explanation](https://nvidia.custhelp.com/app/answers/detail/a_id/5490/~/system-memory-fallback-for-stable-diffusion).

## Next experiment and limits

Test a silent synthesis warmup when push-to-talk starts, so its cost can overlap
the user's speaking time. Measure STT and total first-audio latency as well:
warmup shares the GPU with live recognition and could add contention. Do not
enable periodic background synthesis or change driver fallback settings without
a measured benefit.

Only one short sequence and one idle interval were tested. The experiment did
not reproduce the earlier 1.94-second or five-second responses. Those spikes
still need continuous GPU and per-stage profiling. Raw measurements are in
`kokoro-idle-results.json`.

## Recording-start warmup trial

An optional background warmup was implemented and tested with the same voice
and speed, a 15-second idle threshold, and a two-second timeout. It discards
audio, skips busy providers and text requests, suppresses warmup failures, and
cancels its task when the provider closes. The targeted suite passed 92 tests
and seven subtests, including silent behavior, cooldown, concurrency, timeout,
shutdown, STT, speech segmentation, and gesture timing.

After a 20-second idle period for each condition, a known generated sentence
was streamed to real Nemotron in paced 100 ms PCM chunks. No chat request was
made between transcription and synthesis, to expose any idle recovery cost.

| Measurement | Warmup off | Warmup on |
|---|---:|---:|
| STT finalization | 55.50 ms | 51.59 ms |
| Captioned speech response | 117.78 ms | 122.69 ms |
| Release to audio ready | 173.94 ms | 174.97 ms |

Both transcripts were "Hello, my name is Alex". One paired trial cannot rule
out benefits under other conditions, but it showed no useful latency gain.
Live STT may already activate the GPU. The feature remains disabled by default
and has not been enabled in the running application. Raw results are in
`kokoro-warmup-trial.json`. This trial did not test longer idle periods or the
full chat and browser playback path.
