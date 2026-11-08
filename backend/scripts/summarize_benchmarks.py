"""Summarize one model's recorded conversation turns without mixing test types."""

import argparse
import csv
from datetime import datetime
from hashlib import sha256
import json
from math import ceil, isfinite
from pathlib import Path
from statistics import mean, median


METRICS = (
    ("recording_ms", "Recording time"),
    ("release_to_transcript_ms", "Speech to transcript"),
    ("transcript_to_reply_ms", "Transcript to reply"),
    ("llm_first_token_ms", "First validated reply text"),
    ("release_to_first_audio_chunk_ms", "Release to first audio chunk"),
    ("turn_total_ms", "Total server turn including recording"),
    ("server_after_release_ms", "Server turn after release"),
    ("kokoro_total_ms", "Kokoro total"),
)


def number(value):
    """Keep missing values missing; reject corrupt timing samples."""
    if value is None or value == "":
        return None
    result = float(value)
    if not isfinite(result) or result < 0:
        raise ValueError(f"Expected a finite, nonnegative measurement, got {value!r}")
    return result


def percentile(values, probability=.95):
    """Nearest-rank percentile: sorted value at ceil(probability * N)."""
    if not values:
        return None
    if not 0 < probability <= 1:
        raise ValueError("Percentile probability must be in (0, 1].")
    return sorted(values)[ceil(probability * len(values)) - 1]


def select_conversations(rows, model):
    selected = []
    for record, row in enumerate(rows, 1):
        if row.get("llm_model") != model:
            continue
        # Provider-only rows are never comparable to a full conversation.
        if row.get("test_type", "") not in ("", "conversation", "recorded_conversation"):
            continue
        selected.append({**row, "source_record": record})
    return selected


def summarize(rows):
    successful = [row for row in rows if row.get("status") == "ok"]
    summary = []
    for key, label in METRICS:
        values = []
        for row in successful:
            if key == "server_after_release_ms":
                total, recording = number(row.get("turn_total_ms")), number(row.get("recording_ms"))
                value = total - recording if total is not None and recording is not None else None
                if value is not None and value < 0:
                    raise ValueError("Total turn is shorter than its recording time.")
            else:
                value = number(row.get(key))
            if value is not None:
                values.append(value / 1000)
        summary.append({
            "metric": label, "field": key, "samples": len(values), "unit": "seconds",
            "mean": mean(values) if values else None,
            "median": median(values) if values else None,
            "p95": percentile(values), "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        })
    reliability = [
        {"metric": "Recorded turns", "count": len(rows), "denominator": None},
        {"metric": "Successful turns", "count": len(successful), "denominator": len(rows)},
        {"metric": "No-speech turns", "count": sum(row.get("status") == "no_speech" for row in rows), "denominator": len(rows)},
        {"metric": "Other non-success turns", "count": sum(row.get("status") not in {"ok", "no_speech"} for row in rows), "denominator": len(rows)},
    ]
    for seconds in (10, 20, 30):
        eligible = [row for row in rows if number(row.get("turn_total_ms")) is not None]
        reliability.append({"metric": f"Total turns under {seconds} seconds", "count": sum(
            number(row["turn_total_ms"]) < seconds * 1000 for row in eligible), "denominator": len(eligible)})
    for key, label in (("tts_segments", "TTS segments"), ("gesture_rejected_tags", "Rejected gesture tags")):
        values = [number(row.get(key)) for row in rows]
        reliability.append({"metric": label, "count": sum(value for value in values if value is not None),
                            "denominator": None, "measured_turns": sum(value is not None for value in values)})
    return summary, reliability


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_report(source, model, output):
    original = source.read_bytes()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or ())
        rows = list(reader)
    selected = select_conversations(rows, model)
    if not selected:
        raise ValueError(f"No recorded conversation rows found for {model}.")
    summary, reliability = summarize(selected)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "turns.csv", selected, ["source_record", *fields])
    write_csv(output / "summary.csv", summary, ["metric", "field", "samples", "unit", "mean", "median", "p95", "minimum", "maximum"])
    write_csv(output / "reliability.csv", reliability, ["metric", "count", "denominator", "measured_turns"])
    manifest = {
        "source": source.as_posix(), "source_sha256": sha256(original).hexdigest(),
        "source_rows": len(rows), "selected_rows": len(selected), "excluded_rows": len(rows) - len(selected),
        "model": model, "analysis_date": datetime.now().astimezone().date().isoformat(),
        "selection": "Exact model match; excludes provider-only test_type rows; keeps all statuses.",
        "percentile": "Nearest rank: sorted values[ceil(0.95 * N) - 1].",
        "summary_population": "Successful conversation turns; missing measurements excluded per metric.",
        "fresh_run": False, "rows": selected, "summary": summary, "reliability": reliability,
    }
    (output / "results.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    def formatted(value):
        return "n.a." if value is None else f"{value:.2f}"
    lines = [
        f"# {model} recorded conversation benchmark", "", "Change owner: **Bhvan Madhusudhan**.", "",
        f"Analyzed {len(selected)} conversation rows from `{source.as_posix()}`. "
        f"Excluded {len(rows) - len(selected)} other-model or provider-only rows.", "",
        "This is a summary of existing recordings, not a new benchmark after the latest fixes. "
        "The source has no per-turn timestamp or prompt ID, so capture dates and question order cannot be verified.", "",
        "## Performance", "", "All times are seconds. Distributions include successful turns only.", "",
        "| Metric | N | Mean | Median | P95 | Min | Max |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(f"| {row['metric']} | {row['samples']} | " + " | ".join(
            formatted(row[key]) for key in ("mean", "median", "p95", "minimum", "maximum")) + " |")
    lines += ["", "## Reliability", "", "| Metric | Result |", "|---|---:|"]
    for row in reliability:
        count, denominator = row["count"], row["denominator"]
        result = f"{count:g}/{denominator} ({count / denominator:.0%})" if denominator else f"{count:g}"
        lines.append(f"| {row['metric']} | {result} |")
    lines += ["", "## Interpretation and limits", "",
        "- P95 uses nearest rank, not interpolation. With 20 samples it is the 19th sorted value; the maximum is shown separately.",
        "- Recording time is the user's input duration. Total turn includes it; release-to-first-audio is the response wait.",
        "- Server turn after release is derived as total turn minus recording time.",
        "- In structured mode, the logged first-token field is the first validated reply text available to the pipeline.",
        "- First audio measures server delivery, not audible browser playback. Decode, buffering, gestures, and device latency are outside this CSV.",
        "- Status ok means the server completed the turn. It does not prove factual accuracy, correct camera recognition, or visual synchronization.",
        "- These 20 samples support a descriptive report, not a stable production P95. Repeat the same questions for at least three runs.",
        "- Source recording rows have no code revision, hardware snapshot, or per-turn timestamp. Do not attribute differences to a specific fix.",
        "", "## Files", "", "- [Turn snapshot](turns.csv)", "- [Performance metrics](summary.csv)",
        "- [Reliability metrics](reliability.csv)", "- [Method and raw values](results.json)", "",
        "The source CSV is unchanged. `source_record` is its one-based data-record number, excluding the header. "
        f"Source SHA-256: `{manifest['source_sha256']}`.", "",
    ]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_report(args.source, args.model, args.output)
    print(f"Selected {result['selected_rows']} of {result['source_rows']} rows. Report: {args.output / 'README.md'}")
    for row in result["summary"]:
        print(row["metric"] + ": " + ", ".join(
            f"{key}={row[key]:.2f}s" if row[key] is not None else f"{key}=n.a."
            for key in ("mean", "median", "p95")))


if __name__ == "__main__":
    main()
