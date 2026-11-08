# Landmarks and camera gestures

`mediapipe.py` implements the landmark provider. `models.py` defines model
assets, and `gestures.py` evaluates landmark history for camera gestures.
`base.py` defines the provider contract.

Configure model selection and gesture thresholds under `cv.keypoints` and
`cv.gestures`. Downloaded models are runtime assets, not Python source.

See [camera inference](../README.md), [CV tests](../../tests/cv/README.md), and
[runtime architecture](../../../docs/architecture.md) for capture-time windows.
