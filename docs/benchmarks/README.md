# Benchmarks

This folder contains reports with explicit source data, sample selection,
statistical methods, and validation limits.

| Report | Sample | Purpose |
|---|---|---|
| [2026-11-08 Qwen 4B](2026-11-08-qwen4b/README.md) | 20 recorded conversation turns | Summarize existing STT, chat, speech, and total-turn metrics |

The [Excel workbook](../../outputs/benchmark-metrics-20261108/benchmark-metrics.xlsx)
contains the same sample with live formulas and traceable source records.

Each report keeps selected rows in `turns.csv`, timing statistics in
`summary.csv`, reliability counts in `reliability.csv`, and source provenance in
`results.json`. Original recordings remain unchanged.

Generate the recorded-turn report from the repository root:

```powershell
python backend/scripts/summarize_benchmarks.py backend/metrics-current-qwen4b-conversation.csv --model qwen3.5:4b --output docs/benchmarks/2026-11-08-qwen4b
```

To collect a new full comparison, use the same
[benchmark questions and protocol](../../BENCHMARK-QUESTIONS.md) for each run.
Keep provider-only tests separate from recorded end-to-end conversations.

See [development and verification](../development.md) for application tests.
