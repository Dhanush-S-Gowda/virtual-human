"""Dedicated local-model workers; HTTP/WebSocket servers are never controlled."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def available_cpus():
    return sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))


_HOST_CPUS = available_cpus()  # Capture before narrowing any model-worker thread.


def automatic_groups():
    """Prefer one logical CPU per physical efficiency core when topology is known."""
    cores = {}
    for cpu in _HOST_CPUS:
        root = Path(f"/sys/devices/system/cpu/cpu{cpu}")
        try:
            key = ((root / "topology/physical_package_id").read_text().strip(),
                   (root / "topology/core_id").read_text().strip())
            maximum = int((root / "cpufreq/cpuinfo_max_freq").read_text())
        except (OSError, ValueError):
            key, maximum = (0, cpu), 0
        cores.setdefault(key, (cpu, maximum))
    values = sorted(cores.values())
    frequencies = {frequency for _, frequency in values if frequency}
    if len(frequencies) > 1:
        values = [(cpu, frequency) for cpu, frequency in values if frequency == min(frequencies)]
    cpus = [cpu for cpu, _ in values] or _HOST_CPUS
    if len(cpus) >= 8:
        groups = {"objects": cpus[:2], "keypoints": cpus[2:4], "faces": cpus[4:5], "tts": cpus[5:]}
    else:
        groups = {name: [cpus[index % len(cpus)]] for index, name in enumerate(
            ("objects", "keypoints", "faces", "tts"))}
    groups["stt"] = groups["tts"]  # Recording inference and reply synthesis are sequential.
    return groups


def worker_policy(settings, stage):
    config = getattr(settings, "model_workers", None)
    stage_config = getattr(config, stage, None)
    requested = getattr(stage_config, "cpus", "auto")
    cpus = automatic_groups()[stage] if requested == "auto" else list(requested)
    if any(type(cpu) is not int or cpu not in _HOST_CPUS for cpu in cpus):
        raise ValueError(f"model_workers.{stage}.cpus contains unavailable CPUs")
    threads = getattr(stage_config, "threads", 2)
    if type(threads) is not int or threads < 1:
        raise ValueError(f"model_workers.{stage}.threads must be a positive integer")
    return cpus, min(threads, len(cpus)) if cpus else threads


class ModelWorker:
    def __init__(self, settings, stage):
        self.stage = stage
        self.cpus, self.threads = worker_policy(settings, stage)
        self._executor = None

    def _initialize_thread(self):
        if self.cpus and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, self.cpus)
        logger.info("Local model worker %s: CPUs=%s thread_budget=%d", self.stage, self.cpus, self.threads)

    async def run(self, function, *args):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"model-{self.stage}",
                                                initializer=self._initialize_thread)
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, partial(copy_context().run, function, *args))

    async def close(self):
        if self._executor is not None:
            executor, self._executor = self._executor, None
            await asyncio.to_thread(executor.shutdown, wait=True)


def onnx_options(settings, stage):
    import onnxruntime as ort
    _, threads = worker_policy(settings, stage)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return options
