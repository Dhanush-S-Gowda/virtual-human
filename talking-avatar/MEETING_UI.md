# Avatar call interface

The avatar has a dedicated stage with the supplied room background. Camera,
activity updates and conversation occupy the side column. On narrow screens,
the side column moves below the stage. Typed speech controls have been removed.

Hold Space on the call surface, or hold the microphone button, to speak. Release
to send the final recording. The animation menu contains only actions from
`TalkingHead/animations`. Dance, Pose, and the previous built-in action buttons
have been removed. The square stops the current response. Space on a focused
control retains its normal button behavior.

The companion is named Alex. The allowed inline tags are generated from
`TalkingHead/animation_catalog.json`: `[clapping]`, `[enthusiasm]`, `[greeting]`,
`[laughing]`, `[nod_no]`, `[nod_yes]`, `[talking]`, `[thankful]`, and `[thinking]`.
Tags are removed before transcript display and TTS. Separate cue metadata is
aligned to TTS word times and scheduled from each audio source's actual start.
Valid non-talking tags always queue for playback; only Talking cues are optional.
When another foreground action is busy, a tag can play later than its speech
anchor. Tagged actions preempt camera reactions and Talking motion.
Manual animation buttons remain sequential. Stop and avatar changes cancel
speech preparation, pending cues, and active animations.

While speech audio plays, one of the three Talking variants is selected randomly
without immediately repeating the same variant. Automatic Talking motion uses
only shoulder, arm, and hand channels. Tagged actions interrupt it; talking
motion resumes if audio is still playing and no tagged action is active.

To add or rename FBX files, run `python3 backend/scripts/sync_animation_assets.py`
from the repository root, restart the backend, and rebuild/refresh the frontend.
The sync updates the catalog, deployable files, and allowed tags in the prompt.

The supplied Casual Guy model is deferred: it contains neither a skeleton nor
facial blend shapes and cannot be used by TalkingHead until it is rigged.

The final head pose stays near the viewer's eye line, allowing about 3° upward
and 13° downward movement relative to the camera. This applies after idle,
look-at, and FBX rotation layers, so source clips cannot hold the chin high.
Nods and small head movements remain; head yaw, roll, and body animation are
preserved. Visual acceptance across the avatar presets remains pending.

During microphone capture, Alex occasionally nods when recent audio is above the
activity threshold. Checks occur at random intervals of four to eight seconds.
Silence, a hidden page, speech playback, and other avatar actions suppress nods.
Capture ending or Stop cancels the nod. These listening acknowledgements run in
the frontend; they do not require a model request.

Conversation messages remain visible for the current page session, including
earlier turns and errors. The history panel scrolls to the latest message and
follows replies as they stream. Scroll up to read earlier messages; focus the
panel to scroll with the keyboard. Reloading the page starts a new display log.

Drag the camera's lower-left handle to resize. Focus the handle and use arrow keys
for small adjustments, Shift plus arrows for larger steps, or Home to reset.
Resizing is bounded to preserve room for the conversation.

The camera frames the avatar's head and torso as a meeting participant, with
15% headroom above the face and the lower body cropped out. The initial framing
is 39.15% closer than the original meeting view. Settings includes a zoom slider
(100–200% of the original framing), avatar selection, and voice selection for
the active TTS provider. Zoom, avatar, and voice choices persist in browser
storage. Camera rotation, panning, and direct camera zoom remain disabled; use
the settings slider to adjust framing. Fitting updates when the stage or
selected model changes. Panel shadows retain slight depth; a floor shadow is
not used for this torso view.

Use the sliders button at the bottom left to open Settings. Choose one of the six
local TalkingHead models and a voice for the backend's active TTS provider:
Kokoro offers `am_puck` and `af_heart`; Supertonic offers `F1` and `M1`.
Save changes applies the avatar and stores preferences in this browser. Voice
changes apply to the next recording. The provider stays in backend YAML.

Avatar presets reuse `TalkingHead/siteconfig.js` skeleton corrections and body
metadata; the T-pose Brunette uses the matching reference avatar. Loading is
serialized, and a failed avatar change attempts to restore the previous model.

Validation uses lightweight unit tests and a memory-limited production build.
Automated browser rendering was stopped after the user reported a system crash;
visual acceptance at desktop/mobile sizes remains pending in the user's browser.

Settings, camera and resizing use inline [Lucide icons](https://github.com/lucide-icons/lucide).
Their ISC notice is included in `public/licenses/lucide.txt`.
