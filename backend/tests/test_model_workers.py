"""Local model work is isolated, context-preserving, and does not pin the caller."""

import asyncio
from contextvars import ContextVar
import os
import threading
from types import SimpleNamespace

import pytest

from backend.config import settings
from backend.runtime import ModelWorker, automatic_groups, worker_policy


@pytest.mark.asyncio
async def test_dedicated_workers_use_different_threads_and_preserve_call_context():
    objects, keypoints = ModelWorker(settings, "objects"), ModelWorker(settings, "keypoints")
    tag = ContextVar("test-model-tag", default="missing")
    tag.set("turn-1")
    before = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else None
    def inspect():
        return threading.get_native_id(), tag.get(), (os.sched_getaffinity(0)
                if hasattr(os, "sched_getaffinity") else None)
    try:
        first, second = await asyncio.gather(objects.run(inspect), keypoints.run(inspect))
        assert first[0] != second[0] != threading.get_native_id()
        assert first[1] == second[1] == "turn-1"
        assert (await objects.run(inspect))[0] == first[0]
        if before is not None:
            assert first[2] == set(objects.cpus)
            assert second[2] == set(keypoints.cpus)
            assert os.sched_getaffinity(0) == before
    finally:
        await asyncio.gather(objects.close(), keypoints.close())


def test_invalid_cpu_and_thread_budgets_fail_explicitly():
    config = SimpleNamespace(model_workers=SimpleNamespace(objects=SimpleNamespace(cpus=[-1], threads=1)))
    with pytest.raises(ValueError, match="unavailable CPUs"):
        worker_policy(config, "objects")
    config.model_workers.objects.cpus = []
    config.model_workers.objects.threads = 0
    with pytest.raises(ValueError, match="positive integer"):
        worker_policy(config, "objects")


def test_automatic_allocation_keeps_local_stt_and_tts_on_the_same_phase_budget():
    groups = automatic_groups()
    assert groups["stt"] == groups["tts"]
    assert all(groups.values())


def test_cv_cpu_groups_do_not_overlap_when_four_physical_cores_are_available():
    groups = automatic_groups()
    # Small systems intentionally share CPUs; on this host all CV groups differ.
    if len(set().union(*(set(group) for group in groups.values()))) < 4:
        pytest.skip("Not enough distinct CPU groups")
    assert set(groups["objects"]).isdisjoint(groups["keypoints"])
    assert set(groups["objects"]).isdisjoint(groups["faces"])
    assert set(groups["keypoints"]).isdisjoint(groups["faces"])
