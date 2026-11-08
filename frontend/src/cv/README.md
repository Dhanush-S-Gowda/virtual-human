# Browser camera events

These modules consume camera inference events, draw landmark overlays, gather
speaker evidence, and select camera-driven avatar responses. Camera capture
and socket wiring are coordinated by `src/main.js`.

`gestureEvents.js` and `cameraGestureResponses.js` handle camera gestures.
`landmarkOverlay.js` draws landmarks. `speakingEvents.js` uses current tracks,
microphone activity, and mouth movement to select speaker hints.

See [runtime architecture](../../../docs/architecture.md) and
[frontend tests](../../tests/README.md). Local preview can work before inference
is connected.
