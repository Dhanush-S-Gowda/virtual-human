# Qwen 2B and 4B comparison

Date: 2026-11-09. Change owner: Bhvan Madhusudhan.

Keep Qwen 3.5 4B as the Docker chat model. The installed 2B model gave faster
typical replies but failed three of five memory checks and produced an invalid
action plan. It did not meet the requirement to preserve recall quality.

| Measurement | Qwen 3.5 4B | Qwen 3.5 2B |
|---|---:|---:|
| Installed quantization | Q4_K_M | Q8_0 |
| Median model reply | 1.247 s | 0.941 s |
| Mean model reply | 1.384 s | 1.493 s |
| Slowest model reply | 2.455 s | 10.613 s |
| Prompt checks | 13/15 | 13/15 |
| Memory checks | 5/5 | 2/5 |

The reply timings cover 18 prompt-evaluation turns per model, after a separate
warmup. They include structured plan validation and any retry. They exclude
speech recognition, synthesis, and browser playback. The 2B invalid-plan case
increased its mean and maximum time despite the lower median.

## Procedure

Both runs used the same active system prompt, structured tool mode, temperature
0.2, and fixed camera fixtures from `backend/scripts/evaluate_prompt.py`.
The old Whisper-only runtime-facts expectation was changed in the test harness
to accept Nemotron as well. Production prompts and model settings were not
changed for this comparison.

`backend/scripts/evaluate_memory.py` used isolated temporary databases. It
checked exact digits, the latest requested number, leading zeros, and retrieved
older facts after restoring eight recent turns following 12 unrelated turns.
It did not modify the user's stored conversation. Only one chat model was kept
loaded at a time. The 4B model was restored after the comparison.

## Quality limits

Each scenario ran once per model. These small samples do not establish general
accuracy or stable live latency. Prompt checks use word patterns and can miss
semantic errors. Manual review found that 2B added an unsupported statement to
the reasoning answer and irrelevant identity claims to camera answers. It
failed to recall `347` in two memory scenarios and failed to return retrieved
`4817`, even though retrieval supplied a saved excerpt.

The 4B model also failed two checks: it claimed an unavailable animation and
invented a number when no memory was supplied. Retaining 4B does not resolve
those model limitations. The two installed models also use different
quantization, so this is a deployment comparison, not an isolated test of
parameter count.

Raw prompt and memory reports and the timing summary are in this folder.
Their system-prompt hashes identify the tested prompt. No physical camera,
microphone, or speech generation was part of these checks.
