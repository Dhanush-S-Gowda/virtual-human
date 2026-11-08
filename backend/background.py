"""Celery jobs for durable, idempotent conversation timing records."""

import json
import os
import sqlite3
from pathlib import Path

from celery import Celery


celery_app = Celery(
    "virtual_human",
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
)
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    result_expires=3600, task_acks_late=True, worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True, broker_connection_retry_on_startup=True,
    broker_connection_timeout=1, task_publish_retry=False,
    broker_transport_options={"socket_connect_timeout": 1, "socket_timeout": 1,
                              "visibility_timeout": 300},
    # Publishing also opens a result-store subscription. Its defaults retry
    # for ~20 seconds even when task publishing retries are disabled.
    redis_socket_connect_timeout=1, redis_socket_timeout=1,
    redis_retry_on_timeout=False,
    result_backend_transport_options={"retry_policy": {"max_retries": 0}},
    task_soft_time_limit=20, task_time_limit=30,
)


def store_metrics(event_id, metrics, database=None):
    """Store quantitative fields only; redelivery cannot duplicate a turn."""
    path = Path(database or os.getenv("METRICS_DB_PATH", "/app/data/metrics.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {key: value for key, value in metrics.items()
            if type(value) in (int, float) or key in {
                "status", "stt_provider", "stt_model", "tts_provider", "tts_model", "llm_model"}}
    with sqlite3.connect(path, timeout=10) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS turns (event_id TEXT PRIMARY KEY, "
                           "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, metrics_json TEXT NOT NULL)")
        connection.execute("INSERT OR IGNORE INTO turns(event_id, metrics_json) VALUES (?, ?)",
                           (event_id, json.dumps(safe, allow_nan=False)))
    return {"event_id": event_id, "stored": True}


@celery_app.task(name="virtual_human.record_metrics", autoretry_for=(sqlite3.OperationalError,),
                 retry_backoff=True, max_retries=3)
def record_metrics(event_id, metrics):
    return store_metrics(event_id, metrics)
