# Persistent recall check

Date: 2026-11-08. Model: local Qwen 3.5 4B. Embeddings: all-minilm on CPU.
Run: `python scripts/evaluate_memory.py` in the backend container.

The isolated database contained three personal statements followed by 12
unrelated turns. A new store and conversation restored only the recent eight
turns. Retrieval supplied older facts to each new request.

| Request | Result | Saved excerpts | Retrieval | Full reply generation |
|---|---|---|---|---|
| Recall project access number | Correct: 4817 | 1 | 26.30 ms | 31,008.04 ms |
| Recall current morning meal | Correct: dosa, after changing from idli | 2 | 314.67 ms | 2,599.64 ms |

Both checks passed. Full generation includes a cold model start in the first
case. These are two functional checks, not a latency benchmark or proof that
all future memories will be recalled. The test recreates Python memory objects;
it does not restart Docker. SQLite persistence and isolation also have unit
tests. No microphone, camera, or speech synthesis was used.

## Repeat verification

A second run on the same date also passed both scenarios using a fresh isolated
database. Number recall used one excerpt, with 41.61 ms retrieval and 5,974.32 ms
full reply generation. Corrected meal recall used two excerpts, with 56.80 ms
retrieval and 2,336.56 ms full reply generation. The 12 memory regression tests
also passed again. They cover persistence, retention, conversation isolation,
semantic matching, recent-turn exclusion, vector validation, correction ranking,
deduplication, keyword fallback, failed replies, and speech preference restore.
