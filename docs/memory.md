# Persistent conversation memory

The assistant now restores recent history and retrieves relevant older turns.
Memory starts with conversations completed after this feature is enabled.
It cannot recover earlier history that was already lost from RAM.

## Data flow

1. The browser sends its saved conversation ID with each connection.
2. SQLite restores the latest eight turns and the saved speech-rate preference.
3. For each request, FTS5 and local embeddings find older relevant turns.
4. A hybrid reranker selects up to four excerpts within a 6,000-character budget.
5. The model receives those excerpts as historical data beside the new request.
6. A completed reply is saved. Celery indexes its embedding through Redis.

Current camera observations override historical camera statements. Remembered
names do not establish who is speaking now. Retrieved assistant statements can
be wrong; the prompt gives user statements and newer corrections priority.
Retrieval and prompting improve recall, but they do not guarantee every answer.

## Storage and identity

Docker stores `conversation-memory.sqlite3` in the existing `backend-data`
volume at `/app/data`. Host operation stores it under `backend/data`.
Turns and vectors persist across service restarts. The default limit is 2,000
turns per conversation; older rows and their vectors are removed together.

The browser uses local storage. Tabs in the same browser profile share memory.
An existing tab ID is migrated when no persistent ID exists. A different browser
profile has separate memory. Clearing browser storage removes the link to saved
turns. This is a local demo without sign-in: an ID scopes conversation data but
does not authenticate a person. The local database contains conversation text.
Do not commit it. Removing Docker volumes also removes saved memory.

## Configuration

Set `memory` in `backend/config.yaml` for host operation or
`backend/config.docker.yaml` before rebuilding Docker images.

| Setting | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Enable persistent memory |
| `database` | `data/conversation-memory.sqlite3` | Database path |
| `max_turns` | `2000` | Retained turns per conversation |
| `semantic_enabled` | `true` | Enable embedding retrieval |
| `embedding_model` | `all-minilm` | Local embedding model |
| `embedding_url` | Host localhost or Docker ollama, port 11434 | Ollama endpoint |
| `embedding_timeout` | `1.5` | Maximum embedding request time in seconds |
| `top_k` | `4` | Selected older excerpts |
| `candidate_count` | `30` | Candidate limit per search method |
| `context_chars` | `6000` | Maximum serialized excerpt characters |

`MEMORY_DB_PATH` overrides the database path. `MEMORY_ENABLED=false` disables
memory. Disable semantic retrieval to use keyword search alone.

Docker initialization pulls the small `all-minilm` model. For an existing stack:

```powershell
docker compose exec -T ollama ollama pull all-minilm
```

Embedding requests use CPU inference so the chat model can keep its GPU memory.
Indexing happens after saving, outside the reply path. Redis receives row IDs,
not conversation text. If queue publication fails, local indexing is attempted.
Startup retries the latest 20 missing embeddings. Older unindexed turns remain
available through keyword search. An unavailable embedding model uses keyword
retrieval; a database failure leaves recent in-memory conversation available.

Memory retrieval can add latency, especially on a cold embedding model. The
configured timeout bounds the embedding network call; it does not make the
whole response immediate. Metrics include `memory_retrieval_ms`, `memory_hits`,
and `memory_store_ms`. `/api/health` reports memory availability. The activity
panel shows how many saved excerpts and recent turns reach the model.

## Verification

`backend/scripts/evaluate_memory.py` uses an isolated temporary database. It
seeds facts, adds 12 unrelated turns, recreates the conversation and store, then
asks the configured model to recall a number and a corrected food preference.
It does not send fake scenes into the user's saved memory.

The implementation uses the official [Ollama embedding API](https://docs.ollama.com/api/embed),
the [all-minilm model](https://ollama.com/library/all-minilm), and
[SQLite FTS5](https://www.sqlite.org/fts5.html).
