"""Object provider includes people and stable IDs for every counted instance."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.config import settings
from backend.cv.events import Frame
from backend.cv.object_det.yolo import YOLOProvider


def tensor(values):
    return SimpleNamespace(cpu=lambda: SimpleNamespace(tolist=lambda: values))


@pytest.mark.asyncio
async def test_all_classes_use_persistent_track_ids():
    provider = YOLOProvider(settings)
    model = MagicMock()
    model.track.return_value = [SimpleNamespace(
        names={0: "person", 1: "cup"},
        boxes=SimpleNamespace(
            xyxy=tensor([[0, 0, 10, 10], [20, 0, 30, 10], [40, 0, 50, 10]]),
            cls=tensor([0, 1, 1]), conf=tensor([.9, .8, .7]), id=tensor([10, 11, 12]),
        ),
    )]
    provider._model = model
    first = await provider.detect(Frame(0, 0., None))
    second = await provider.detect(Frame(1, .1, None))
    assert [obj.track_id for obj in first] == [10, 11, 12]
    assert [obj.label for obj in first] == ["person", "cup", "cup"]
    assert first == second
    assert model.track.call_args.kwargs["persist"] is True


@pytest.mark.asyncio
async def test_untracked_detections_are_not_counted_as_new_instances():
    provider = YOLOProvider(settings)
    provider._model = MagicMock()
    provider._model.track.return_value = [SimpleNamespace(boxes=SimpleNamespace(id=None))]
    assert await provider.detect(Frame(0, 0., None)) == ()
