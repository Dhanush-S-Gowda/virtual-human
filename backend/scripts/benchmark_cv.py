"""Measure the migrated CV providers (replaces both scratch memory scripts).

Run from repository root: uv run --project backend python -m
backend.scripts.benchmark_cv --frames 50 --label baseline [--video path.mp4]
Compare saved JSON outputs from two revisions; no hard-coded memory baseline.
"""

import argparse
import asyncio
import json
import resource
import time

from backend.config import settings
from backend.cv.__main__ import build_providers
from backend.cv.events import Frame
from backend.cv.window_manager import WindowManager


async def benchmark(args):
    import cv2
    import numpy as np

    providers = build_providers()
    manager = WindowManager.from_settings(*providers, settings)
    video = cv2.VideoCapture(args.video) if args.video else None
    if video and not video.isOpened():
        raise ValueError(f"Cannot open video: {args.video}")
    latencies = []
    try:
        initialized = time.perf_counter()
        await manager.initialize()
        initialization_ms = (time.perf_counter() - initialized) * 1000
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        synthetic = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.circle(synthetic, (320, 150), 40, (200, 200, 200), -1)
        cv2.rectangle(synthetic, (270, 190), (370, 380), (180, 180, 180), -1)
        started = time.perf_counter()
        for sequence in range(args.frames):
            ok, image = video.read() if video else (True, synthetic.copy())
            if not ok:
                break
            before = time.perf_counter()
            manager.submit(Frame(sequence, sequence / 10., image))
            await manager.wait_idle()
            latencies.append((time.perf_counter() - before) * 1000)
        elapsed = time.perf_counter() - started
        print(json.dumps({
            "label": args.label, "frames": len(latencies), "initialization_ms": initialization_ms,
            "mean_frame_ms": float(np.mean(latencies)) if latencies else None,
            "p95_frame_ms": float(np.percentile(latencies, 95)) if latencies else None,
            "processed_frames_per_second": len(latencies) / elapsed,
            "peak_cpu_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "peak_gpu_allocated_mb": torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else 0,
            "errors": [error for window in manager._windows.values()
                       for record in window.frames.values() for error in record.errors],
        }, indent=2))
    finally:
        if video:
            video.release()
        await manager.wait_idle()
        await manager.close()
        for provider in reversed(providers):
            await provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--label", default="current")
    parser.add_argument("--video")
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("--frames must be positive")
    asyncio.run(benchmark(args))
