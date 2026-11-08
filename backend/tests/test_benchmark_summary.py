"""Recorded benchmark populations, missing data, and tail statistics stay explicit."""

import csv
from hashlib import sha256

import pytest

from backend.scripts.summarize_benchmarks import build_report, number, percentile, select_conversations, summarize


def test_model_filter_excludes_provider_rows_without_hiding_failures():
    rows = [
        {"llm_model": "4b", "status": "ok"},
        {"llm_model": "2b", "status": "ok"},
        {"llm_model": "4b", "status": "error"},
        {"llm_model": "4b", "test_type": "tts_provider", "status": "ok"},
    ]
    chosen = select_conversations(rows, "4b")
    assert [row["source_record"] for row in chosen] == [1, 3]
    _, reliability = summarize(chosen)
    success = next(row for row in reliability if row["metric"] == "Successful turns")
    assert (success["count"], success["denominator"]) == (1, 2)


def test_nearest_rank_p95_keeps_maximum_separate():
    assert percentile(list(range(1, 21))) == 19
    assert percentile([123]) == 123
    assert percentile([]) is None


def test_missing_timings_are_not_zeros_and_recording_is_removed_from_derived_latency():
    summary, _ = summarize([
        {"status": "ok", "recording_ms": "1000", "turn_total_ms": "3000", "kokoro_total_ms": "0"},
        {"status": "ok", "recording_ms": "2000", "turn_total_ms": "5000", "kokoro_total_ms": ""},
        {"status": "error", "kokoro_total_ms": "999999"},
    ])
    by_field = {row["field"]: row for row in summary}
    assert by_field["kokoro_total_ms"]["samples"] == 1
    assert by_field["kokoro_total_ms"]["mean"] == 0
    assert by_field["release_to_transcript_ms"]["mean"] is None
    assert by_field["server_after_release_ms"]["mean"] == 2.5
    for invalid in ("nan", "inf", "-1"):
        with pytest.raises(ValueError):
            number(invalid)


def test_report_preserves_source_and_exports_traceable_records(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("llm_model,status,recording_ms,turn_total_ms\n4b,ok,1000,3000\n2b,ok,1000,9000\n")
    before = source.read_bytes()
    manifest = build_report(source, "4b", tmp_path / "report")
    assert source.read_bytes() == before
    assert manifest["source_sha256"] == sha256(before).hexdigest()
    assert manifest["selected_rows"] == 1
    assert manifest["excluded_rows"] == 1
    with (tmp_path / "report/turns.csv").open(newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert exported[0]["source_record"] == "1"
    assert exported[0]["turn_total_ms"] == "3000"
