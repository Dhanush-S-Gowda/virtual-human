# Natural dialogue and gesture coordination

## Status and recommendation

The application now uses the user's chosen inline-tag format. Allowed tags come
from the shared animation catalog, are parsed before display/TTS, and become
separate word-timed speech cues. See [OBSERVABILITY.md](OBSERVABILITY.md) for the
implemented protocol, audio-clock scheduling, and current limits.

The sections below retain earlier research and future architecture suggestions;
they are not a description of the current wire format. The current implementation
uses one conversation model and deterministic cue playback, without a second LLM.

## Conversation and affect

Use a compact persona, clear tone adaptation, and contrasting examples. Alex
should follow playful energy, celebrate achievements, and respond gently to
distress. Frustration calls for calm engagement, rather than anger imitation.
Examples include replies with no gesture so expressivity does not become a
mandatory performance on every turn.

The current dialogue model receives transcribed words and world observations.
It does not receive an acoustic emotion representation. Textual mood adaptation
cannot recover all sarcasm, laughter, urgency, or vocal affect. Acoustic cues
would need an audio-capable model or an additional perception stage. The current
TTS interface accepts text, voice, and speed; it does not accept a model-generated
emotion or delivery instruction. Prompting alone does not add expressive voice
control. See [PerceptiveAgent](https://arxiv.org/abs/2406.12707) for speech-style
perception, and [Empathic Grounding](https://arxiv.org/abs/2407.01824) for affective
listening responses.

## Model output and growing the animation library

Extend the current structured plan with segment IDs, optional delivery intent,
and exact textual anchors. For example:

```json
{
  "segments": [{
    "id": "s1",
    "text": "You did it! Congratulations!",
    "delivery": {"tone": "excited", "energy": 0.7},
    "gestures": [{
      "intent": "celebrate",
      "anchor_text": "Congratulations",
      "occurrence": 0
    }]
  }]
}
```

This is an illustrative future schema, not an accepted request payload today.
The model selects intent and an anchor, not millisecond timestamps. Validate
anchors against the final spoken text. Reject unknown intents and invalid spans;
omit their gestures while preserving valid speech. Keep identity-changing tools,
such as rename_person, as validated state-changing operations.

Maintain an asset catalog with semantic tags, compatible rigs, body channels,
duration, preparation/stroke/retraction markers, intensity, cooldown, and safe
blend boundaries. Map celebrate to an appropriate clapping variant, for example.
When the library grows, filter or retrieve a small candidate set for the model.
Adding another FBX should usually mean adding metadata, rather than another tool
and another system-prompt paragraph.

Inline tags such as `[greet]Hello` can express the same intent, but require a
streaming parser that handles split tags, malformed tags, escaping, and unknown
actions. Extract tags before text display, normalization, and TTS; retain their
positions in the cleaned text. The current normalizer does not remove generic
gesture tags. Separate structured fields fit this application's existing plan
validation more directly.

## Speech synchronization

1. Finalize and normalize a speech segment while retaining a mapping from gesture
   anchors to normalized text. Assign turn and segment IDs before synthesis.
2. Synthesize only spoken text. Obtain word timestamps from TTS or align that exact
   synthesized waveform to its transcript. Resolve anchors to those timestamps.
3. Resolve the clip's stroke marker: the meaningful part of the movement, such as
   hand contact in clapping, rather than simply its first frame. Prepare assets
   before committing the segment to playback.
4. At the actual audio source start, record its scheduled AudioContext time. For
   an anchor at word time `w`, schedule clip start at `audio_start + w - stroke`.
   Use seconds consistently; existing `wtimes` values are milliseconds.
5. Advance animation from the audio timeline. Use output timestamps to account
   for device latency where available. A late render frame should sample the
   correct clip position rather than shift the whole gesture. Do not schedule
   from network arrival time or a chain of setTimeout callbacks.
6. If preparation would start before audio, reserve a short preparation interval
   before starting speech or omit the cue. For conflicting hand gestures, keep
   the higher-priority anchored cue or omit one; putting it at the end of a FIFO
   can destroy its intended speech alignment. Independent channels may overlap
   only when their animations support masking and blending.
7. On interruption, cancel the turn's audio and gesture schedule together. Bound
   lookahead to a short phrase or sentence so the avatar remains responsive.

This follows BML's separation between behavior planning and realization, with
speech markers and gesture phases linked by synchronization constraints:
[Behavior Markup Language](https://projects.cs.ru.is/projects/behavior-markup-language/wiki/Wiki).
The timing reference is the actual audio source schedule:
[Web Audio specification](https://www.w3.org/TR/webaudio/).

## Existing implementation limits

- The frontend now binds inline cues to word times and each actual audio source
  start. Animation stroke offsets are not yet annotated.
- The current action queue waits for an entire gesture and its return transition.
  This prevents overlap, but is not a speech-anchored scheduler.
- Kokoro consumes provider word timestamps. Supertonic currently estimates word
  times from text length and total duration. Those estimates cannot support
  reliable word-level gesture alignment; add alignment or native timing first.
- Full-body FBX playback cannot yet freely combine arm gestures with separate
  head motion. A larger library will benefit from body-channel masks and blending.

## Validation before expanding the architecture

Compare the old and new prompts on fixed multi-turn cases: joking, celebration,
quiet satisfaction, frustration, sadness, factual questions, ambiguity, and explicit
gesture sequences. Check natural phrasing, context continuity, emotional fit,
unnecessary questions, gesture relevance, repetition, and schema validity. Unit
tests validate examples and dispatch, not perceived conversational naturalness.
For scheduling, measure stroke-to-anchor error and interruption response on
actual playback, including delayed TTS segments and slow frames.
