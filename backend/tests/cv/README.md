# Camera tests

These tests cover object state, identity matching and storage, landmarks,
gestures, capture windows, scene history, and camera service integration.

Run from the repository root:

```powershell
uv run --project backend --extra dev python -m pytest -c backend/pyproject.toml backend/tests/cv -q
```

Most checks use fixtures. Real MediaPipe model checks need downloaded assets.
See [backend tests](../README.md) and [camera service](../../cv/README.md).
