import time
import os
import psutil
import torch
import numpy as np
import cv2
from cv_assistant.pipeline import CVPipeline
from cv_assistant.camera import Frame

def measure_optimized(num_frames=50):
    process = psutil.Process(os.getpid())
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()

    mem_before_mb = process.memory_info().rss / 1024 / 1024
    gpu_before_mb = torch.cuda.memory_allocated() / 1024 / 1024 if torch.cuda.is_available() else 0.0

    print(f"Memory Before Init: CPU RAM: {mem_before_mb:.2f} MB, GPU VRAM: {gpu_before_mb:.2f} MB")

    pipeline = CVPipeline(no_camera=True)
    pipeline.initialize()

    mem_after_init_mb = process.memory_info().rss / 1024 / 1024
    gpu_after_init_mb = torch.cuda.memory_allocated() / 1024 / 1024 if torch.cuda.is_available() else 0.0
    print(f"Memory After Init: CPU RAM: {mem_after_init_mb:.2f} MB, GPU VRAM: {gpu_after_init_mb:.2f} MB")

    # Identical workload synthetic frames with a person-like bounding box
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:] = (20, 24, 33)
    cv2.circle(img, (320, 150), 40, (200, 200, 200), -1)
    cv2.rectangle(img, (270, 190), (370, 380), (180, 180, 180), -1)

    latencies = []
    start_all = time.monotonic()

    for seq in range(num_frames):
        frame = Frame(
            image=img.copy(),
            seq=seq,
            timestamp=time.monotonic(),
            width=640,
            height=480,
        )
        t0 = time.monotonic()
        pipeline._process_frame(frame)
        t1 = time.monotonic()
        latencies.append((t1 - t0) * 1000.0)

    total_time = time.monotonic() - start_all
    avg_latency = np.mean(latencies)
    p95_latency = np.percentile(latencies, 95)
    fps = num_frames / total_time

    mem_end_mb = process.memory_info().rss / 1024 / 1024
    gpu_end_mb = torch.cuda.memory_allocated() / 1024 / 1024 if torch.cuda.is_available() else 0.0
    gpu_reserved_mb = torch.cuda.memory_reserved() / 1024 / 1024 if torch.cuda.is_available() else 0.0
    gpu_peak_mb = torch.cuda.max_memory_allocated() / 1024 / 1024 if torch.cuda.is_available() else 0.0

    print("=" * 60)
    print("OPTIMIZED MEASUREMENT RESULTS:")
    print(f"Frames Processed: {num_frames}")
    print(f"Average Latency: {avg_latency:.2f} ms")
    print(f"P95 Latency: {p95_latency:.2f} ms")
    print(f"Throughput: {fps:.2f} FPS")
    print(f"Model Init CPU RAM: {mem_after_init_mb:.2f} MB")
    print(f"Steady-State CPU RAM: {mem_end_mb:.2f} MB")
    print(f"CPU RAM Delta from Baseline: {mem_end_mb - 1821.95:.2f} MB")
    print(f"Peak GPU VRAM Allocated: {gpu_peak_mb:.2f} MB")
    print(f"Peak GPU VRAM Reserved: {gpu_reserved_mb:.2f} MB")
    print("=" * 60)

    pipeline.stop()

if __name__ == "__main__":
    measure_optimized(50)

