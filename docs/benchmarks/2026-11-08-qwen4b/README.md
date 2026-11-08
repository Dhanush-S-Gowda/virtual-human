# qwen3.5:4b recorded conversation benchmark

Change owner: **Bhvan Madhusudhan**.

Analyzed 20 conversation rows from `backend/metrics-current-qwen4b-conversation.csv`. Excluded 101 other-model or provider-only rows.

This is a summary of existing recordings, not a new benchmark after the latest fixes. The source has no per-turn timestamp or prompt ID, so capture dates and question order cannot be verified.

## Performance

All times are seconds. Distributions include successful turns only.

| Metric | N | Mean | Median | P95 | Min | Max |
|---|---:|---:|---:|---:|---:|---:|
| Recording time | 20 | 6.04 | 5.05 | 10.23 | 3.16 | 11.37 |
| Speech to transcript | 20 | 1.27 | 0.93 | 2.49 | 0.83 | 5.85 |
| Transcript to reply | 20 | 1.68 | 1.45 | 2.38 | 0.94 | 5.92 |
| First validated reply text | 20 | 1.65 | 1.44 | 2.37 | 0.93 | 5.89 |
| Release to first audio chunk | 20 | 3.72 | 2.87 | 5.29 | 2.00 | 17.80 |
| Total server turn including recording | 20 | 9.92 | 9.03 | 16.84 | 6.04 | 21.51 |
| Server turn after release | 20 | 3.88 | 2.91 | 5.46 | 2.00 | 18.35 |
| Kokoro total | 20 | 0.92 | 0.53 | 1.78 | 0.20 | 6.57 |

## Reliability

| Metric | Result |
|---|---:|
| Recorded turns | 20 |
| Successful turns | 20/20 (100%) |
| No-speech turns | 0/20 (0%) |
| Other non-success turns | 0/20 (0%) |
| Total turns under 10 seconds | 13/20 (65%) |
| Total turns under 20 seconds | 19/20 (95%) |
| Total turns under 30 seconds | 20/20 (100%) |
| TTS segments | 31 |
| Rejected gesture tags | 1 |

## Interpretation and limits

- P95 uses nearest rank, not interpolation. With 20 samples it is the 19th sorted value; the maximum is shown separately.
- Recording time is the user's input duration. Total turn includes it; release-to-first-audio is the response wait.
- Server turn after release is derived as total turn minus recording time.
- In structured mode, the logged first-token field is the first validated reply text available to the pipeline.
- First audio measures server delivery, not audible browser playback. Decode, buffering, gestures, and device latency are outside this CSV.
- Status ok means the server completed the turn. It does not prove factual accuracy, correct camera recognition, or visual synchronization.
- These 20 samples support a descriptive report, not a stable production P95. Repeat the same questions for at least three runs.
- Source recording rows have no code revision, hardware snapshot, or per-turn timestamp. Do not attribute differences to a specific fix.

## Files

- [Turn snapshot](turns.csv)
- [Performance metrics](summary.csv)
- [Reliability metrics](reliability.csv)
- [Method and raw values](results.json)

The source CSV is unchanged. `source_record` is its one-based data-record number, excluding the header. Source SHA-256: `68c067349509bf1d36f3475c930e3dbb889d99d0f4ed9b22bff69cd4a154f132`.
