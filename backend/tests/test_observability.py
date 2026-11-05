import json
import logging
import time
import asyncio
from types import SimpleNamespace

import httpx
import psutil
import pytest

from backend.observability import Exporter, OpenObserveHandler, redact, resource_sample


def test_missing_openobserve_disables_logging_without_raising(monkeypatch, capsys):
    def unavailable(request):
        raise httpx.ConnectError("connection refused", request=request)
    client = httpx.Client(transport=httpx.MockTransport(unavailable))
    monkeypatch.setattr("backend.observability.httpx.Client", lambda **kwargs: client)
    exporter = Exporter()
    exporter.thread.join(timeout=2)
    exporter.submit("cv", {"event": "frame"})
    assert exporter.disabled
    assert exporter.queue.empty()
    assert "not installed" in capsys.readouterr().err
    exporter.close()


def test_missing_credentials_disable_only_logging(monkeypatch, capsys):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})))
    monkeypatch.setattr("backend.observability.httpx.Client", lambda **kwargs: client)
    for key in ("OPENOBSERVE_USER", "OPENOBSERVE_PASSWORD", "OPENOBSERVE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    exporter = Exporter()
    exporter.thread.join(timeout=2)
    assert exporter.disabled
    assert exporter.disabled_reason == "ValueError"
    assert "all other application processes continue" in capsys.readouterr().err
    exporter.close()


def test_exporter_startup_failure_is_contained_at_the_logging_boundary(monkeypatch, capsys):
    import backend.observability as module
    monkeypatch.setenv("OPENOBSERVE_ENABLED", "true")
    monkeypatch.setattr(module, "_transport_disabled", False)
    monkeypatch.setattr(module, "_exporter", None)
    def fail():
        raise RuntimeError("cannot start exporter thread")
    monkeypatch.setattr(module, "Exporter", fail)
    module.emit("cv", {"event": "frame"})
    assert not module.enabled()
    assert "all other application processes continue" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_api_starts_and_serves_health_when_openobserve_is_not_installed(monkeypatch):
    from backend.tests.test_backend import FakeLLM, FakeSTT, FakeTTS
    from main import create_app
    import observability as app_observability

    def unavailable(request):
        raise httpx.ConnectError("connection refused", request=request)
    client = httpx.Client(transport=httpx.MockTransport(unavailable))
    monkeypatch.setattr(app_observability.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(app_observability, "_exporter", None)
    monkeypatch.setenv("OPENOBSERVE_ENABLED", "true")
    previous_handlers = list(logging.getLogger().handlers)
    app = create_app(SimpleNamespace(llm=FakeLLM(), stt=FakeSTT(), tts=FakeTTS()))
    try:
        async with app.router.lifespan_context(app):
            for _ in range(100):
                if app_observability._exporter.disabled:
                    break
                await asyncio.sleep(0.01)
            assert app_observability._exporter.disabled
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
                response = await api.get("/health")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
    finally:
        app_observability.shutdown()
        logging.getLogger().handlers[:] = previous_handlers


def test_exporter_routes_events_without_files_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENOBSERVE_PASSWORD", "secret-value")
    requests = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request:
        requests.append(request) or httpx.Response(200, json={"status": [{"failed": 0}]})))
    exporter = Exporter(client)
    try:
        exporter.submit("cv", {"message": "secret-value", "authorization": "private"})
        exporter.submit("resources", {"task": "cv.objects", "rss_mib": 12.})
        for _ in range(100):
            if len(requests) == 2:
                break
            time.sleep(0.005)
    finally:
        exporter.close()
        client.close()
    assert len(requests) == 2
    assert {request.url.path for request in requests} == {"/api/default/vh_cv/_json", "/api/default/vh_resources/_json"}
    assert "secret-value" not in str([request.content for request in requests])
    assert list(tmp_path.iterdir()) == []


def test_failed_ingestion_is_bounded_and_reports_drops():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request:
        calls.append(request) or httpx.Response(503)))
    exporter = Exporter(client)
    exporter.submit("llm", {"event": "request"})
    for _ in range(150):
        if exporter.dropped:
            break
        time.sleep(0.01)
    exporter.close()
    client.close()
    assert len(calls) == 3
    assert exporter.dropped == 1


def test_process_metrics_include_private_memory_and_task_identity():
    process = psutil.Process()
    process.cpu_percent(None)
    sample = resource_sample(process, "cv.keypoints")
    assert sample["pid"] == process.pid
    assert sample["task"] == "cv.keypoints"
    assert sample["rss_mib"] > 0
    assert sample["memory_scope"] == "dedicated_process"
    assert "uss_mib" in sample
