# Kokoro audio streaming

## Operation

The backend streams audio from the current Kokoro-FastAPI 0.8.1 service.
Restart the application backend to load this change. The Kokoro container and
frontend do not need changes.

Each request sends a complete text segment to `/dev/captioned_speech` with
`stream: true` and `response_format: pcm`. Kokoro returns newline-delimited JSON
containing base64 audio and word timestamps. The adapter wraps each 24 kHz,
mono, 16-bit PCM chunk in a WAV header. Existing frontend playback can decode
each chunk independently.

The backend subtracts the preceding PCM duration from each chunk's word times.
It aligns gesture cues against successive provider words and sends each cue once.
An invalid word alignment suppresses cues for that segment. Concurrent synthesis
remains bounded by `tts_max_concurrent_segments`; delivery preserves segment and
chunk order. Each segment's output queue holds at most two pending chunks.

Both conversation mode and direct speech mode use streaming. Other providers
keep their complete-segment synthesis path. Partial provider failures produce an
error without replaying audio already sent. Cancelling the handler or detecting
a failed client send closes the active stream and cancels its synthesis tasks.

This change does not add streamed text input. Kokoro 0.8.1 does not provide the
new upstream text-input WebSocket API. It also does not change the application's
text segmentation or guarantee pause-free playback.

## Measured performance

The local test ran on 2026-11-05 against the existing CPU service on port 8880.
The API reported version 0.8.1. The voice was `af_heart`, speed 1.0, with a
101-word passage producing approximately 35.09 seconds of audio. A short request
warmed the service before the comparison. Requests ran sequentially.

| Path | First playable audio | Request complete | Audio chunks |
| --- | ---: | ---: | ---: |
| Previous non-streaming WAV request | 35.44 s | 35.44 s | 1 |
| Service-only WAV streaming probe | 30.92 s | 39.80 s | 3 |
| Implemented PCM-to-WAV streaming and application delivery | 21.08 s | 29.24 s | 3 |

The implemented path made the first audio available 14.36 seconds sooner,
approximately 40.5% earlier than the non-streaming baseline. Its chunks arrived
at 21.08, 21.10, and 29.24 seconds and contained 11.87, 14.85, and 8.37 seconds
of playable audio. If playback starts on the first chunk, the later chunks in
this run arrive before the preceding audio runs out.

These are single-run observations, not a statistically controlled benchmark.
The application test called the real provider through direct speech mode and
validated delivery and decoding into independent WAVs at a test WebSocket sink.
It did not measure microphone capture, STT, LLM generation, browser decoding,
physical playback, or perceived lip sync. The baseline and implemented path use
different transport formats. System load, service buffering, and run-to-run
variation can affect the result; a separate WAV streaming probe was slower
overall. Small text requests may return only one audio chunk and gain little.

The first two chunks arrived together in both streaming tests. The current
service still delays the first audio substantially. The result does not prove
that application streaming alone removes the reported pauses.

Raw measurement records are in `/tmp/kokoro-stream-benchmark.json` and
`/tmp/kokoro-app-stream-benchmark.json` for this session.

## Verification

Focused regressions cover independent WAV chunks, relative timestamps, ordered
delivery before synthesis finishes, gesture alignment, invalid responses, HTTP
errors, partial failure, disconnects, cancellation, and response cleanup.

Validation passed: 66 focused backend regressions, 52 additional action/tool/
conversation regressions, and all 17 frontend test files. One additional
diagnostics worker-thread test (`test_worker_span_records_native_worker_tid`)
did not finish within the bounded test run and was excluded from the 52-test
rerun. No browser was launched and no frontend build was required.
