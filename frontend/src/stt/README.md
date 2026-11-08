# Microphone and conversation transport

`STTManager.js` captures microphone audio, manages the conversation WebSocket,
and handles cancellation and turn events. `pcm-worklet.js` produces PCM chunks.
`keyboardPushToTalk.js` handles recording controls. `session.js` manages the
browser conversation-session identifier.

Send the actual AudioContext sample rate. Capture cleanup and delayed flushes
must stay associated with the recording that created them.

See [frontend tests](../../tests/README.md) for PCM and cancellation checks,
and [API interfaces](../../../docs/development.md#interfaces) for message flow.
