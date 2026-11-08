# Model prompts

`system.txt` contains the assistant's behavior, camera-grounding rules, supported
expression tags, speech controls, and naming boundaries. The backend loads the
file selected by `system_prompt_file`.

## Input assembly

The conversation coordinator supplies actual bounded history and configured
runtime facts. Camera data is refreshed before the model turn. In structured
mode, runtime facts and the current scene are placed beside the latest request.
The JSON schema defines the response format; fictional demonstrations are not
inserted as user or assistant messages.

Runtime facts include model names, speed limits, and an explicit session voice
preference. They contain no provider credentials or service URLs. Structured
output enforces the requested speed for ordinary replies; performances can
vary speed. Native streaming remains model-driven for inline rate tags.

The animation sync script updates the `Allowed gesture tags:` line from the
catalog, excluding automatic talking motion. Keep prompt changes consistent
with the parser and action validator. Rebuild the backend image for Docker;
restart the host backend for native development.

See [the recorded comparison](../../../docs/prompt-evaluation/2026-11-08/README.md),
[chat adapter](../README.md), and [speech timing](../../../docs/audio-gesture-synchronization.md).
