# Conversation memory

This package stores completed turns and retrieves relevant older conversation
data. See the [memory guide](../../docs/memory.md) for operation and limits.

- `store.py`: SQLite turns, FTS5 keyword search, normalized embedding vectors,
  retention, and the hybrid reranker.
- `service.py`: recent-history restore, semantic retrieval, context limits,
  background indexing, and fallback when services fail.

The reranker combines semantic similarity, keyword rank, overlap with the
user's words, recency, and explicit corrections. It removes duplicate excerpts.
It is a deterministic hybrid reranker, not a neural cross-encoder.

Run regression checks from the backend environment:

```shell
python -m pytest tests/test_memory.py -q
python scripts/evaluate_memory.py
```
