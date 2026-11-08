"""Nonblocking memory orchestration; semantic failures fall back to FTS5."""

import asyncio
from collections import OrderedDict
import json
import logging
import os
from pathlib import Path
import sqlite3
from time import perf_counter

import httpx

from .store import MemoryReranker, MemoryStore, memory_excerpt, normalize


logger = logging.getLogger(__name__)


def embedding_text(turn):
    return "User: " + turn["user_text"][:1600] + "\nAssistant: " + turn["assistant_text"][:400]


class OllamaEmbedder:
    def __init__(self, url, model, timeout=1.5):
        self.url, self.model = url.rstrip("/"), model
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, .5)))
        self.cache = OrderedDict()

    async def embed(self, text):
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        response = await self.client.post(self.url + "/api/embed", json={
            "model": self.model, "input": text[:2000], "truncate": True,
            "keep_alive": "30m", "options": {"num_gpu": 0}})
        response.raise_for_status()
        vector = normalize(response.json()["embeddings"][0])
        self.cache[text] = vector
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return vector

    async def close(self):
        await self.client.aclose()


class ConversationMemory:
    def __init__(self, store, embedder=None, top_k=4, candidate_count=30, context_chars=6000):
        self.store, self.embedder = store, embedder
        self.top_k, self.candidate_count, self.context_chars = top_k, candidate_count, context_chars
        self.reranker = MemoryReranker()
        self.available = False
        self.embedding_status = "not_checked" if embedder else "disabled"
        self.tasks = set()
        self.index_slot = asyncio.Semaphore(1)

    async def start(self):
        try:
            await asyncio.to_thread(self.store.initialize)
            self.available = True
            if self.embedder:
                pending = await asyncio.to_thread(self.store.pending, self.embedder.model)
                for turn_id in pending:
                    self.schedule_index(turn_id)
        except (OSError, sqlite3.Error):
            logger.exception("Persistent conversation memory could not be initialized")

    def status(self):
        return {"enabled": True, "available": self.available, "retrieval": "semantic+fts5" if self.embedder else "fts5",
                "reranker": "hybrid", "embedding_status": self.embedding_status,
                "embedding_model": self.embedder.model if self.embedder else None}

    async def restore(self, conversation_id, limit):
        if not self.available:
            return [], None
        try:
            return await asyncio.to_thread(self.store.restore, conversation_id, limit)
        except (OSError, sqlite3.Error):
            logger.exception("Saved conversation could not be restored")
            return [], None

    async def retrieve(self, conversation_id, query, excluded=()):
        started = perf_counter()
        metadata = {"available": self.available, "retrieved": 0, "candidates": 0, "semantic": False}
        if not self.available:
            return [], metadata
        try:
            if not await asyncio.to_thread(self.store.has_older, conversation_id, set(excluded)):
                return [], {**metadata, "retrieval_ms": round((perf_counter() - started) * 1000, 2)}
            vector = None
            if self.embedder:
                try:
                    vector = await self.embedder.embed(query)
                    self.embedding_status = "ready"
                    metadata["semantic"] = True
                except (httpx.HTTPError, ValueError, KeyError, IndexError):
                    self.embedding_status = "unavailable_keyword_fallback"
                    logger.warning("Memory embeddings unavailable; using full-text retrieval")
            candidates = await asyncio.to_thread(
                self.store.candidates, conversation_id, query, excluded, self.candidate_count,
                vector, self.embedder.model if self.embedder else "")
            ranked = self.reranker.rank(query, candidates, self.top_k)
            metadata["semantic"] = any("semantic_score" in item for item in ranked)
            selected = []
            for item in ranked:
                excerpt = memory_excerpt(item, min(1400, self.context_chars // 2))
                if len(json.dumps([*selected, excerpt], ensure_ascii=False)) <= self.context_chars:
                    selected.append(excerpt)
            metadata.update(retrieved=len(selected), candidates=len(candidates))
            return selected, {**metadata, "retrieval_ms": round((perf_counter() - started) * 1000, 2)}
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("Memory retrieval failed; continuing with recent history")
            return [], {**metadata, "available": False}

    async def remember(self, conversation_id, user, assistant, speech_speed=None):
        if not self.available:
            return None
        try:
            turn_id = await asyncio.to_thread(self.store.save, conversation_id, user, assistant, speech_speed)
        except (OSError, sqlite3.Error):
            logger.exception("Conversation memory write failed")
            return None
        if self.embedder:
            task = asyncio.create_task(self.enqueue_index(turn_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return turn_id

    async def enqueue_index(self, turn_id):
        if os.getenv("CELERY_ENABLED", "false").lower() in {"1", "true", "yes"}:
            try:
                from background import index_memory_turn
                await asyncio.to_thread(index_memory_turn.apply_async, args=[turn_id], retry=False)
                return
            except Exception:
                logger.warning("Memory indexing queue unavailable; indexing locally")
        await self.index_turn(turn_id)

    def schedule_index(self, turn_id):
        task = asyncio.create_task(self.index_turn(turn_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def index_turn(self, turn_id):
        if not self.embedder:
            return
        async with self.index_slot:
            try:
                turn = await asyncio.to_thread(self.store.turn, turn_id)
                if turn:
                    vector = await self.embedder.embed(embedding_text(turn))
                    await asyncio.to_thread(self.store.put_vector, turn_id, self.embedder.model, vector)
                    self.embedding_status = "ready"
            except (OSError, sqlite3.Error, httpx.HTTPError, ValueError, KeyError, IndexError):
                self.embedding_status = "unavailable_keyword_fallback"
                logger.warning("Memory embedding job failed; keyword memory remains available")

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.embedder:
            await self.embedder.close()


def configured_memory(settings):
    config = settings.memory
    if not config.enabled or os.getenv("MEMORY_ENABLED", "true").lower() in {"0", "false", "no"}:
        return None
    database = os.getenv("MEMORY_DB_PATH") or str(Path(__file__).resolve().parents[1] / config.database)
    store = MemoryStore(database, config.max_turns)
    embedder = OllamaEmbedder(config.embedding_url, config.embedding_model, config.embedding_timeout) if config.semantic_enabled else None
    return ConversationMemory(store, embedder, config.top_k, config.candidate_count, config.context_chars)
