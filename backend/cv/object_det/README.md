# Object detection and tracking

`yolo.py` implements object detection. `tracking.py` and `state.py` maintain
tracks and current object state. `base.py` defines the provider contract.

Configure models, thresholds, and tracker settings under `cv.detection` and
`cv.tracking`. Downloaded YOLO weights use the model cache; Docker persists
these in `yolo-models`.

See [camera inference](../README.md) and [CV tests](../../tests/cv/README.md).
