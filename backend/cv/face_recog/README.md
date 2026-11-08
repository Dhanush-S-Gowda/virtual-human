# Face recognition and identity

These modules detect faces, compute embeddings, apply quality checks, match
stored identities, and estimate speaker identity. `service.py` coordinates the
provider. `store.py` owns identity persistence; `speaker.py` handles speaker
scoring. `insightface.py` is the InsightFace adapter.

Configure this subsystem under `cv.face`, `cv.identity`, and `cv.speaker` in
the active backend configuration. Runtime identity data belongs in the
configured data directory or Docker `cv-data` volume.

See [camera inference](../README.md) and [CV tests](../../tests/cv/README.md).
