# User interface helpers

These modules manage call settings, conversation rendering, camera resizing,
avatar framing, and tracked-person cards. `trackList.js` renders identity data
as text so model or camera names cannot become HTML.

Keep presentation helpers independent of inference services where possible.
Use [frontend tests](../../tests/README.md) for rendering and interaction checks.
See the [source map](../README.md) for application wiring.
