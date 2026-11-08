"""SQLite turns, full-text search, and local embedding vectors."""

from contextlib import contextmanager
from datetime import datetime, timezone
from heapq import nlargest
from math import isfinite, sqrt
from pathlib import Path
import re
import sqlite3
import struct
import time


STOPWORDS = set("a an the is are was were be been to of for and or in on at with do does did can could would should i me my you your we our it this that what which how please have has had tell about".split())


def tokens(text):
    return [word for word in re.findall(r"\w+", text.casefold()) if word not in STOPWORDS][:24]


def normalize(vector):
    if (not isinstance(vector, list) or not vector or len(vector) > 4096
            or any(type(value) not in (int, float) or not isfinite(value) for value in vector)):
        raise ValueError("Invalid embedding vector.")
    length = sqrt(sum(value * value for value in vector))
    if not length:
        raise ValueError("Embedding vector has zero length.")
    return [value / length for value in vector]


class MemoryStore:
    def __init__(self, database, max_turns=2000):
        self.database = Path(database)
        self.max_turns = max_turns

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.database, timeout=.5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memory_owner ON memory_turns(conversation_id, id);
                CREATE TABLE IF NOT EXISTS memory_preferences (
                    conversation_id TEXT PRIMARY KEY,
                    speech_speed REAL
                );
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    turn_id INTEGER PRIMARY KEY REFERENCES memory_turns(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    embedding BLOB NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
                    user_text, assistant_text, content='memory_turns', content_rowid='id',
                    tokenize='porter unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memory_turns BEGIN
                    INSERT INTO memory_search(rowid, user_text, assistant_text)
                    VALUES (new.id, new.user_text, new.assistant_text);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_delete AFTER DELETE ON memory_turns BEGIN
                    INSERT INTO memory_search(memory_search, rowid, user_text, assistant_text)
                    VALUES ('delete', old.id, old.user_text, old.assistant_text);
                END;
            """)

    def save(self, conversation_id, user, assistant, speech_speed=None, created_at=None):
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO memory_turns(conversation_id,user_text,assistant_text,created_at) VALUES(?,?,?,?)",
                (conversation_id, user, assistant, time.time() if created_at is None else created_at))
            turn_id = cursor.lastrowid
            if speech_speed is not None:
                db.execute("INSERT INTO memory_preferences VALUES(?,?) ON CONFLICT(conversation_id) "
                           "DO UPDATE SET speech_speed=excluded.speech_speed", (conversation_id, speech_speed))
            db.execute("DELETE FROM memory_turns WHERE conversation_id=? AND id NOT IN "
                       "(SELECT id FROM memory_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?)",
                       (conversation_id, conversation_id, self.max_turns))
            return turn_id

    def restore(self, conversation_id, limit):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM memory_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                              (conversation_id, limit)).fetchall()
            preference = db.execute("SELECT speech_speed FROM memory_preferences WHERE conversation_id=?",
                                    (conversation_id,)).fetchone()
            return [dict(row) for row in reversed(rows)], preference[0] if preference else None

    def turn(self, turn_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM memory_turns WHERE id=?", (turn_id,)).fetchone()
            return dict(row) if row else None

    def put_vector(self, turn_id, model, vector):
        values = normalize(vector)
        with self.connection() as db:
            # A retained turn may be pruned while its background task is running.
            if not db.execute("SELECT 1 FROM memory_turns WHERE id=?", (turn_id,)).fetchone():
                return False
            db.execute("INSERT INTO memory_vectors VALUES(?,?,?,?) ON CONFLICT(turn_id) DO UPDATE SET "
                       "model=excluded.model,dimensions=excluded.dimensions,embedding=excluded.embedding",
                       (turn_id, model, len(values), struct.pack(f"<{len(values)}f", *values)))
            return True

    def pending(self, model, limit=20):
        with self.connection() as db:
            return [row[0] for row in db.execute(
                "SELECT t.id FROM memory_turns t LEFT JOIN memory_vectors v ON v.turn_id=t.id "
                "WHERE v.turn_id IS NULL OR v.model<>? ORDER BY t.id DESC LIMIT ?", (model, limit))]

    def has_older(self, conversation_id, excluded):
        with self.connection() as db:
            return any(row[0] not in excluded for row in db.execute(
                "SELECT id FROM memory_turns WHERE conversation_id=?", (conversation_id,)))

    def candidates(self, conversation_id, query, excluded, limit=30, vector=None, model=""):
        excluded = set(excluded)
        found = {}
        with self.connection() as db:
            words = list(dict.fromkeys(tokens(query)))
            if words:
                match = " OR ".join('"' + word + '"' for word in words)
                rows = db.execute(
                    "SELECT t.*,bm25(memory_search,3.0,0.5) AS relevance FROM memory_search "
                    "JOIN memory_turns t ON t.id=memory_search.rowid "
                    "WHERE memory_search MATCH ? AND t.conversation_id=? ORDER BY relevance LIMIT ?",
                    (match, conversation_id, limit + len(excluded))).fetchall()
                for rank, row in enumerate(row for row in rows if row["id"] not in excluded):
                    found[row["id"]] = {**dict(row), "lexical_rank": rank}
            if vector is not None:
                query_vector = normalize(vector)
                scores = []
                for row in db.execute(
                        "SELECT v.* FROM memory_vectors v JOIN memory_turns t ON t.id=v.turn_id "
                        "WHERE t.conversation_id=? AND v.model=?", (conversation_id, model)):
                    if row["turn_id"] in excluded or row["dimensions"] != len(query_vector):
                        continue
                    values = struct.unpack(f"<{row['dimensions']}f", row["embedding"])
                    score = sum(a * b for a, b in zip(query_vector, values))
                    if score >= .25:
                        scores.append((score, row["turn_id"]))
                for score, turn_id in nlargest(limit, scores):
                    if turn_id not in found:
                        found[turn_id] = dict(db.execute("SELECT * FROM memory_turns WHERE id=?", (turn_id,)).fetchone())
                    found[turn_id]["semantic_score"] = score
            for row in db.execute("SELECT * FROM memory_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                                  (conversation_id, 4 + len(excluded))):
                if row["id"] not in excluded:
                    found.setdefault(row["id"], dict(row))
        return list(found.values())

    def clear(self, conversation_id):
        with self.connection() as db:
            db.execute("DELETE FROM memory_turns WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM memory_preferences WHERE conversation_id=?", (conversation_id,))


class MemoryReranker:
    """Combine semantic, lexical, user-text, correction, and recency evidence."""

    def rank(self, query, candidates, top_k=4):
        query_words = set(tokens(query))
        now = time.time()
        recall = bool(re.search(r"\b(?:remember|earlier|previous|discussed|forgot)\b", query, re.I))
        ranked = []
        for item in candidates:
            user_words = set(tokens(item["user_text"]))
            overlap = len(query_words & user_words) / max(1, len(query_words))
            lexical = 1 / (1 + item["lexical_rank"]) if "lexical_rank" in item else 0
            semantic = max(0., item.get("semantic_score", 0.))
            recency = 1 / (1 + max(0., now - item["created_at"]) / 86400)
            correction = .15 if re.search(r"\b(?:actually|correction|instead|changed)\b", item["user_text"], re.I) else 0
            score = .6 * semantic + .25 * overlap + .2 * lexical + .04 * recency + correction
            if not lexical and semantic < .25 and not overlap:
                if not recall:
                    continue
                score += .15
            ranked.append({**item, "score": score})
        ranked.sort(key=lambda item: (item["score"], item["id"]), reverse=True)
        selected, seen = [], set()
        for item in ranked:
            key = (item["user_text"].casefold(), item["assistant_text"].casefold())
            if key not in seen:
                selected.append(item)
                seen.add(key)
            if len(selected) == top_k:
                break
        return selected


def memory_excerpt(item, max_chars=1400):
    return {"recorded_at": datetime.fromtimestamp(item["created_at"], timezone.utc).isoformat(),
            "user_said": item["user_text"][:max_chars * 2 // 3],
            "assistant_replied": item["assistant_text"][:max_chars // 3]}
