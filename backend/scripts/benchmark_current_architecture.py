"""Benchmark the current Docker LLM and TTS providers and write CSV metrics."""

import argparse
import csv
import subprocess
import time
from pathlib import Path


FIELDS = [
    "stt_provider",
    "stt_model",
    "llm_provider",
    "llm_model",
    "tts_provider",
    "tts_model",
    "recording_ms",
    "release_to_transcript_ms",
    "transcript_to_reply_ms",
    "llm_first_token_ms",
    "llm_gesture_parse_ms",
    "gesture_cue_assignment_ms",
    "gesture_audio_alignment_ms",
    "tts_text_buffer_ms",
    "tts_queue_wait_ms",
    "kokoro_queue_wait_ms",
    "kokoro_first_pcm_ms",
    "kokoro_total_ms",
    "release_to_first_audio_chunk_ms",
    "turn_total_ms",
    "audio_bytes",
    "tts_segments",
    "tts_segment_ms_json",
    "kokoro_request_first_byte_ms_json",
    "response_segments_json",
    "tts_audio_chunks",
    "gesture_rejected_tags",
    "status",
    "test_type",
    "iteration",
    "stt_device",
    "tts_image",
]


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def benchmark_llm(iteration: int) -> dict[str, object]:
    started = time.perf_counter()
    docker(
        "exec",
        "-T",
        "ollama",
        "ollama",
        "run",
        "--think=false",
        "qwen3.5:4b",
        "Reply with exactly GPU_TEST_OK",
    )
    return {
        "test_type": "llm_provider",
        "iteration": iteration,
        "transcript_to_reply_ms": round((time.perf_counter() - started) * 1000, 2),
        "status": "ok",
    }


def benchmark_tts(iteration: int) -> dict[str, object]:
    code = (
        "import json,time,urllib.request;"
        "p=json.dumps({'model':'kokoro','input':'This is a GPU latency test.',"
        "'voice':'af_heart','response_format':'wav'}).encode();"
        "q=urllib.request.Request('http://127.0.0.1:8880/v1/audio/speech',"
        "data=p,headers={'Content-Type':'application/json'});"
        "t=time.perf_counter();r=urllib.request.urlopen(q,timeout=120);"
        "first=time.perf_counter()-t;d=r.read();"
        "print(f'{first*1000:.2f},{(time.perf_counter()-t)*1000:.2f},{len(d)}')"
    )
    output = docker("exec", "-T", "kokoro", "python", "-c", code)
    first_byte, total, audio_bytes = output.splitlines()[-1].split(",")
    return {
        "test_type": "tts_provider",
        "iteration": iteration,
        "kokoro_request_first_byte_ms_json": f"[{first_byte}]",
        "kokoro_first_pcm_ms": first_byte,
        "kokoro_total_ms": total,
        "release_to_first_audio_chunk_ms": first_byte,
        "audio_bytes": audio_bytes,
        "tts_segments": 1,
        "tts_audio_chunks": 1,
        "status": "ok",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Qwen 4B and Kokoro in the current Docker architecture."
    )
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/metrics-current-qwen4b-gpu.csv"),
    )
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")

    rows = []
    for iteration in range(1, args.iterations + 1):
        rows.append(benchmark_llm(iteration))
    for iteration in range(1, args.iterations + 1):
        rows.append(benchmark_tts(iteration))

    common = {
        "llm_model": "qwen3.5:4b",
        "llm_provider": "openai",
        "stt_provider": "faster-whisper",
        "stt_model": "base",
        "stt_device": "cpu",
        "tts_provider": "kokoro",
        "tts_model": "kokoro",
        "tts_image": "ghcr.io/remsky/kokoro-fastapi-gpu:v0.9.2",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**common, **row})
    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
