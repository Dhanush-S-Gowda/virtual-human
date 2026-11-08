# Qwen prompt evaluation - 2026-11-08

Change owner: **Bhvan Madhusudhan**.

## Scope

The same 15 scenarios (18 model turns) were run through the conversation and
structured-plan path using local `qwen3.5:4b`, temperature 0.2, and fixed
scene fixtures. Each scenario starts a new conversation. Memory, scene-refresh,
and voice-preference cases include a second turn. Actions are disabled.

This is an application-level smoke test of prompt/context and speech-control
changes. It does not isolate prompt wording from code changes, and is not a
general accuracy benchmark. No microphone, physical camera, or TTS was used.

## Results

- Original: 13/15 basic answer/control checks passed.
- Final: 15/15 basic answer/control checks passed.
- Backend regression suite: 337 tests and 7 subtests passed; 1 optional model-assets test skipped. All 51 targeted checks passed after the final speaker-rate adjustment.

| Scenario | Original | Final |
|---|---|---|
| arithmetic | Pass | Pass |
| factual | Pass | Pass |
| reasoning | Pass | Pass |
| memory | Pass | Pass |
| people count | Pass | Pass |
| empty scene | Pass | Pass |
| unavailable scene | Pass | Pass |
| fresh scene | Pass | Pass |
| unrelated identity | Pass | Pass |
| speed memory | Fail | Pass |
| unsupported action | Pass | Pass |
| runtime facts | Fail | Pass |
| missing visual detail | Pass | Pass |
| no invented memory | Pass | Pass |
| scene data not instruction | Pass | Pass |

## Changes verified

- Configured chat, recognition, and speech model facts are supplied to the assistant, without API keys or service URLs.
- Fictional user/assistant demonstrations are removed from the real conversation message list; the JSON schema defines structured output.
- Current camera observations stay next to the latest request, separate from old history and runtime facts.
- Explicit direct English voice preferences such as "speak slowly" are stored per session and supplied as current context.
- In structured mode, ordinary replies preserve an explicit rate even if the model omits or changes the speed tag. Unrequested rate changes are removed. Performances can vary speed.
- The revised prompt covers direct answers, actual memory, zero-versus-unavailable camera states, missing visual details, and supported animations.

## Limits and manual review

- Automated checks use patterns and control-tag rules. Correct-looking words do not establish complete semantic accuracy.
- Some final replies still used unnecessary nod or greeting tags. One reasoning reply used `[nod_yes]` before "No". Gesture choice remains a model limitation despite the clearer instructions.
- Camera scenes were controlled fixtures; these results do not validate recognition on a live camera.
- These were single local-model samples with several development iterations, not a blinded or statistically controlled comparison.
- Average reported prompt tokens per turn were 1325.8 originally and 1347.2 finally. No latency improvement is claimed; the new runtime facts add context.
- The checker accepts digit or spoken-number forms for 378 and engine names in any order. Baseline results were rechecked using the same acceptance rules.

## Evidence and rerun

- [Original replies](before.json)
- [Final replies](after.json)
- Development candidates: [v1](candidate-v1.json), [v2](candidate-v2.json), [v3](candidate-v3.json).
- [Evaluation script](../../../backend/scripts/evaluate_prompt.py)
- [Active system prompt](../../../backend/llm/prompts/system.txt)

From the repository root, rebuild first, then run:

```powershell
docker compose build backend
docker compose run --rm --no-deps -v "${PWD}/docs/prompt-evaluation/2026-11-08:/results" backend python -m scripts.evaluate_prompt --label rerun --output /results/rerun.json
```

The baseline records the original prompt at commit `0c3a0f6`; raw replies and
prompt hashes are retained. Repeating a run can produce different wording.
