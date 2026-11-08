# Camera inference

This service receives browser camera frames and publishes scene, track, identity,
and landmark updates. `main.py` coordinates providers; `window_manager.py`
groups observations by capture time; `events.py` defines event data.

- [object_det](object_det/README.md): detect and track objects and people.
- [face_recog](face_recog/README.md): match identities and store face records.
- [keypoint_det](keypoint_det/README.md): extract landmarks and recognize gestures.

Compose starts `cv` independently of the conversation API. For host development,
run `uv run --project backend python -m backend.cv` from the repository root.
The service uses port 8765; Docker exposes it through the frontend `/cv` proxy.

Use [CV tests](../tests/cv/README.md) to check changes. See
[runtime architecture](../../docs/architecture.md) for windows, worker processes,
and the relationship between preview and inference.
