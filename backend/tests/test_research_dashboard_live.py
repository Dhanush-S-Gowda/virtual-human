"""Opt-in validation against OpenObserve; uses and removes its own temporary stream."""

import os
import time
import uuid

import httpx
import pytest

from backend.observability import credentials
from backend.research_dashboard import summary_sql


@pytest.mark.skipif(os.getenv("OPENOBSERVE_LIVE_TESTS") != "1", reason="Requires local OpenObserve")
def test_live_summary_percentiles_deduplicate_samples_and_exclude_warmup():
    stream = "vh_research_test_" + uuid.uuid4().hex
    api = os.getenv("OPENOBSERVE_URL", "http://localhost:5080").rstrip("/") + "/api/" + os.getenv("OPENOBSERVE_ORG", "default")
    stamp = time.time_ns() // 1000
    common = {"_timestamp": stamp, "event": "measurement", "metric": "stt_completion_ms", "unit": "ms",
              "host": "test", "experiment_id": "test", "provider": "fake", "model": "fake",
              "image_width": 0, "image_height": 0, "people_count": 0, "phase": "measured", "status": "ok"}
    common["timing_boundary"] = "browser_release_to_final_transcript"
    rows = [{**common, "sample_id": str(index), "value": value} for index, value in enumerate((10., 20., 30., 40.))]
    rows += [rows[0], {**common, "sample_id": "warmup", "value": 9999., "phase": "warmup"},
             {**common, "sample_id": "old-boundary", "value": 3352., "timing_boundary": "backend_with_cleanup"},
             {**common, "sample_id": "error", "value": None, "status": "error"}]
    with httpx.Client(timeout=30, **credentials()) as client:
        created = False
        try:
            response = client.post(f"{api}/{stream}/_json", json=rows)
            response.raise_for_status()
            created = True
            assert not any(item.get("failed", 0) for item in response.json().get("status", []))
            query = summary_sql().replace('"vh_performance"', f'"{stream}"')
            response = client.post(f"{api}/_search", json={"query": {
                "sql": query, "start_time": stamp - 1_000_000, "end_time": stamp + 60_000_000,
                "from": 0, "size": 100}})
            response.raise_for_status()
            result = response.json()
            assert not result.get("is_partial")
            assert len(result["hits"]) == 1
            row = result["hits"][0]
            assert row["Samples"] == 4
            assert row["Errors"] == 1
            assert row["Mean"] == 25.
            assert row["Median (approx)"] == pytest.approx(25., abs=5.)
            assert 30 <= row["P95 (approx)"] <= 40
            assert 30 <= row["P99 (approx)"] <= 40
        finally:
            if created:
                response = client.delete(f"{api}/streams/{stream}", params={"type": "logs"})
                response.raise_for_status()
