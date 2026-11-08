import json
import os
import socket
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from background import store_metrics, record_metrics
import pipeline


def test_metrics_job_is_idempotent_and_omits_transcripts(tmp_path):
    path = tmp_path / "new" / "metrics.sqlite3"
    metrics = {"status": "ok", "llm_total_ms": 10, "response_segments": ["private speech"]}
    store_metrics("turn-1", metrics, path)
    store_metrics("turn-1", metrics, path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT metrics_json FROM turns").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == {"status": "ok", "llm_total_ms": 10}


@pytest.mark.asyncio
async def test_broker_failure_keeps_local_metrics_and_does_not_fail_turn(monkeypatch, tmp_path):
    path = tmp_path / "new" / "metrics.csv"
    monkeypatch.setattr(pipeline, "_METRICS_FILE", path)
    monkeypatch.setenv("CELERY_ENABLED", "true")
    publish = Mock(side_effect=ConnectionError("Redis unavailable"))
    monkeypatch.setattr(record_metrics, "apply_async", publish)
    await pipeline._save_metrics({"status": "ok", "response_segments": ["hello"]})
    assert path.exists()
    assert "response_segments" not in publish.call_args.kwargs["args"][1]


@pytest.mark.asyncio
async def test_metrics_disk_failure_does_not_fail_turn(monkeypatch):
    monkeypatch.setattr(pipeline, "_append_metrics_csv", Mock(side_effect=OSError("disk full")))
    await pipeline._save_metrics({"status": "ok"})


def test_real_redis_outage_returns_promptly_and_keeps_csv(tmp_path):
    # Reserve a local port without listening, so no external service is used.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        csv_path = tmp_path / "fallback.csv"
        environment = {**os.environ, "CELERY_ENABLED": "true",
                       "CELERY_BROKER_URL": f"redis://127.0.0.1:{port}/0",
                       "CELERY_RESULT_BACKEND": f"redis://127.0.0.1:{port}/1",
                       "METRICS_CSV_PATH": str(csv_path)}
        subprocess.run([sys.executable, "-c",
                        "import asyncio; from pipeline import _save_metrics; "
                        "asyncio.run(_save_metrics({'status': 'ok'}))"],
                       env=environment, check=True, timeout=8, capture_output=True)
    assert csv_path.exists() and "ok" in csv_path.read_text()
