import asyncio
import os

import pytest

from backend.provider_process import ProviderProcess


@pytest.mark.asyncio
async def test_models_are_stateful_and_isolated_in_distinct_processes():
    first = ProviderProcess("backend.tests.process_fixture", "Provider", role="test.first")
    second = ProviderProcess("backend.tests.process_fixture", "Provider", role="test.second")
    try:
        pids = await asyncio.gather(first.initialize(), second.initialize())
        assert len(set([os.getpid(), *pids])) == 3
        assert (await first.detect("one"))["count"] == 1
        assert (await first.detect("two"))["count"] == 2
        await first.reset()
        assert (await first.detect("three"))["count"] == 1
        with pytest.raises(RuntimeError, match="fixture failure"):
            await second.detect("fail")
        assert (await second.detect("works"))["count"] == 1
    finally:
        await asyncio.gather(first.close(), second.close())
    assert not first.process.is_alive() and not second.process.is_alive()


@pytest.mark.asyncio
async def test_cancellation_does_not_mix_the_previous_rpc_result_with_the_next():
    provider = ProviderProcess("backend.tests.process_fixture", "Provider", role="test.cancel")
    try:
        await provider.initialize()
        task = asyncio.create_task(provider.detect("cancelled"))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        result = await provider.detect("next")
        assert result["value"] == "next"
        assert result["count"] == 2
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_crashed_provider_reports_failure_and_still_closes_cleanly():
    provider = ProviderProcess("backend.tests.process_fixture", "Provider", role="test.crash")
    await provider.initialize()
    provider.process.terminate()
    await asyncio.to_thread(provider.process.join, 2)
    with pytest.raises(RuntimeError, match="not running"):
        await provider.detect("next")
    await provider.close()
    assert not provider.process.is_alive()
