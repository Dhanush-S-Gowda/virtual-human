"""Keep unit tests independent of the deployment's background services."""

import pytest


@pytest.fixture(autouse=True)
def isolated_deployment(monkeypatch):
    monkeypatch.setenv("CELERY_ENABLED", "false")
    monkeypatch.setenv("MEMORY_ENABLED", "false")
