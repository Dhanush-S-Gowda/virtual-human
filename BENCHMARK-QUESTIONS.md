# Architecture Benchmark Questions

Use these questions in the exact order for both architectures. Speak naturally,
but do not repeat or rephrase a question. Record one complete turn per question.

## Test protocol

1. Restart the architecture before testing.
2. Confirm the active STT, LLM, and TTS models.
3. Use the same browser, microphone, voice, and network conditions.
4. Wait for the previous response to finish before starting the next question.
5. Hold the microphone button while speaking, then release it.
6. Record the generated metrics CSV without editing it.
7. Repeat the complete set at least three times for reliable P50/P95 values.

Do not compare provider-only measurements with end-to-end conversation
measurements. The primary comparison must use the full turn metrics:

- `release_to_transcript_ms`
- `transcript_to_reply_ms`
- `llm_first_token_ms`
- `release_to_first_audio_chunk_ms`
- `turn_total_ms`
- `kokoro_total_ms`
- `status`

## Questions

| # | Category | Question |
|---:|---|---|
| 1 | Greeting | Hello, how are you today? |
| 2 | Short answer | What is the capital of France? |
| 3 | Factual | Who wrote the novel 1984? |
| 4 | Counting | Count from one to ten slowly. |
| 5 | Calculation | What is 27 multiplied by 14? |
| 6 | Instruction | Give me three practical tips for better sleep. |
| 7 | Explanation | Explain what a GPU does in simple words. |
| 8 | Comparison | What is the difference between RAM and storage? |
| 9 | Reasoning | If all roses are flowers and some flowers fade quickly, can we conclude that all roses fade quickly? |
| 10 | Planning | Create a simple three-step plan for learning Python. |
| 11 | Context | Remember this number for the next question: 4817. |
| 12 | Memory | What number did I ask you to remember? |
| 13 | Long response | Explain the water cycle in four concise sentences. |
| 14 | Gesture | Please greet me, nod yes, then give a thumbs up. |
| 15 | Speech speed | Say this sentence slowly: testing speech timing is useful. |
| 16 | Multi-sentence | Tell me a short story with exactly three sentences. |
| 17 | Ambiguous | What is the best way to become successful? |
| 18 | Camera context | What can you see in the current camera view? |
| 19 | Recovery | I changed my mind. Give me one short joke instead. |
| 20 | Final response | Say goodbye in one sentence. |

## Results worksheet

For each architecture, preserve the original CSV and record:

| Architecture | CSV | STT | LLM | TTS | Test date |
|---|---|---|---|---|---|
| Current Docker | | Faster-Whisper base | Qwen 3.5 4B | Kokoro GPU | |
| Comparison | | | | | |

Calculate median (P50), P95, mean, minimum, and maximum for each timing
column. Also report success rate, no-speech rate, and the number of rejected
gesture tags.

## Decision rule

Prefer the architecture with:

1. Higher successful-turn rate.
2. Lower median `turn_total_ms`.
3. Lower P95 `turn_total_ms`.
4. Lower median `release_to_first_audio_chunk_ms`.
5. Fewer no-speech results and rejected gesture tags.

If one architecture wins latency but loses reliability, report the trade-off
instead of declaring a single winner.
