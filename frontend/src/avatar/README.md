# Avatar playback and actions

These modules prepare audio, schedule gestures, and control avatar motion.
`AvatarController.js` coordinates the avatar. `GestureSpeechPacer.js`,
`GestureTimeline.js`, and `AudioGesturePlayer.js` align gestures with speech.
`ActionQueue.js` and `SpeechActions.js` coordinate actions. The remaining
modules handle clips, speech audio, listening, talking, and laughter motion.

Shared gesture definitions come from `TalkingHead/animation_catalog.json`.
See [speech timing](../../../docs/audio-gesture-synchronization.md) and
[frontend tests](../../tests/README.md) before changing playback scheduling.
