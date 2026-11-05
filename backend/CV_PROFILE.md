# CV latency profile

Measured 2026-11-01 from existing `backend/logs/stages-83298.jsonl`.
These are observed application runs, not a controlled foreground-camera benchmark.
No external service settings or CPU affinity were changed.

## Worker placement

The recorded worker TIDs were objects 83299 on CPUs 4–5, keypoints 83631 on
CPUs 6–7, and faces 83525 on CPU 8. CV already uses separate CPU groups.
Native inference can run concurrently; same-frame keypoints still await object
boxes, and MediaPipe runs pose, face and hands sequentially per person.

## Recorded stage durations

| Stage | Sample scope | Mean | 95th percentile |
| --- | --- | --- | --- |
| Objects | Last 300 calls | 271.9 ms | 452.6 ms |
| Keypoints | Last 300 calls | 212.6 ms | 1665.2 ms |
| Faces | All 223 calls | 36.6 ms | 78.3 ms |

The keypoint mean hides empty scenes: 216 of the last 300 calls took under 5 ms.
Of the remaining calls, 32 took over 500 ms, averaging 1653.9 ms.
The slowest was 2543.7 ms. These durations are inside model workers; they exclude
WindowManager dependency wait. They do not separate graph initialization from inference.

## Delivery cadence and model lifetime

During the final recorded minute, 60 object calls had a median source timestamp
gap of 1.004 seconds and median frame-sequence gap of one. That indicates roughly
one frame sent per second in this segment, despite the frontend's 66 ms timer.
It does not establish whether browser background throttling, main-thread load,
or another capture problem caused the slow delivery. Repeat the measurement with
the call page in the foreground before attributing it to normal foreground use.

At one frame per second, a four-sample gesture with a maximum permitted gap of
0.5 seconds cannot reliably confirm a natural two-second action.

Code inspection found that MediaPipe discarded all three graphs after a one-second
sample gap even when the same person was visible in the new frame. The provider
now preserves a currently visible person's models and expires absent people only.
A regression test covers sparse timestamps and subsequent departure.

A real MediaPipe smoke test with a synthetic blank image, a forced person crop,
and timestamps 0, 0.1, 0.2, 1.5, 3 and 4.5 seconds created no extra model groups.
Frame times were 84.15, 63.65, 61.44, 64.20, 66.13 and 59.80 ms. This proves model
reuse across gaps; it does not measure real-person accuracy or full concurrent load.

## Remaining measurement

Restart our CV process and profile a foreground camera session. Compare source
cadence, keypoint durations and gesture sampling after the graph-lifetime fix.
If keypoint inference remains slow with normal delivery, measure pose, face and
hand phases separately before changing CPU budgets or adding processes.
