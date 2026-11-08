# Backend tests

Tests cover the conversation pipeline, providers, speed tags, Celery persistence,
model setup, animation synchronization, and camera integration. The
[cv](cv/README.md) subfolder contains camera-specific tests.

From the repository root:

```powershell
uv run --project backend --extra dev python -m pytest -c backend/pyproject.toml backend/tests -q
```

`conftest.py` disables background queue submission by default. Individual tests
explicitly enable it when required. Fixtures isolate model and network work;
optional tests that need real model assets can be skipped when assets are absent.

Use [development and verification](../../docs/development.md) for the Docker
test command. Add a regression test when changing application behavior.
