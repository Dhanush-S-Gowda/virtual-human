"""Temporal evidence rejects incidental motion and separates holds from waves."""

from dataclasses import replace
from math import sin, cos, pi
from types import SimpleNamespace
from copy import deepcopy

import pytest

from backend.config import settings
from backend.cv.keypoint_det.base import KeypointDetection, KeypointSample
from backend.cv.keypoint_det.gestures import GestureClassifier, hand_shape


def hand(shape="open_palm", offset=0., body_offset=0.):
    points = [[50., 80., 0.] for _ in range(21)]
    points[0] = [50., 90., 0.]
    for base, x, y in ((5, 35., 65.), (9, 45., 60.), (13, 55., 65.), (17, 65., 70.)):
        points[base] = [x, y, 0.]
        points[base + 1] = [x, 45., 0.]
        points[base + 3] = [x, 20. if shape == "open_palm" else y + 5., 0.]
    points[2], points[3], points[4] = [25., 75., 0.], [15., 65., 0.], [5., 55., 0.]
    if shape == "thumbs_up":
        points[2], points[3], points[4] = [25., 75., 0.], [25., 45., 0.], [25., 15., 0.]
    return KeypointDetection(42, "mediapipe_left_hand21",
                             tuple((x + offset + body_offset, y, z) for x, y, z in points),
                             bbox=(body_offset, 0., 200. + body_offset, 200.))


def classify_series(detections, times=None, config=None):
    # Retain proof of the original conservative configuration as well as the
    # natural-duration configuration tested below.
    baseline = deepcopy(settings)
    baseline.cv.gestures.confirmation_seconds = 2.
    baseline.cv.gestures.min_frames = 8
    baseline.cv.gestures.wave_min_reversals = 3
    classifier = GestureClassifier(config or baseline)
    history, result = [], []
    times = times if times is not None else [index / 10 for index in range(len(detections))]
    for index, (timestamp, detection) in enumerate(zip(times, detections)):
        result.append(classifier.classify(detection, timestamp, history))
        history.append(KeypointSample(index, timestamp, detection))
    return result


@pytest.mark.parametrize("shape", ["open_palm", "thumbs_up"])
def test_static_shapes_require_two_seconds_and_multiple_frames(shape):
    assert hand_shape(hand(shape).points) == shape
    result = classify_series([hand(shape) for _ in range(22)])
    assert all(not gestures for gestures in result[:20])
    assert result[20] == (shape,)
    assert result[21] == (shape,)


def test_repeated_wave_confirmed_but_not_mistaken_for_stop():
    result = classify_series([hand(offset=35 * sin(index / 10 * 2 * pi * 1.5)) for index in range(31)])
    assert not any(result[:20])
    assert any(gestures == ("wave",) for gestures in result[20:])
    assert not any("open_palm" in gestures for gestures in result)


def test_wave_rotating_around_stationary_wrist():
    detections = []
    for index in range(31):
        angle = .5 * sin(index / 10 * 2 * pi * 1.5)
        points = tuple((50 + (x - 50) * cos(angle) - (y - 90) * sin(angle),
                        90 + (x - 50) * sin(angle) + (y - 90) * cos(angle), z)
                       for x, y, z in hand().points)
        detections.append(replace(hand(), points=points))
    result = classify_series(detections)
    assert any(gestures == ("wave",) for gestures in result[20:])
    assert not any("open_palm" in gestures for gestures in result)


@pytest.mark.parametrize("offsets", [[index * 3 for index in range(31)], [0.] * 8])
def test_single_sweep_or_brief_palm_does_not_trigger(offsets):
    assert not any(classify_series([hand(offset=offset) for offset in offsets]))


def test_jitter_and_body_translation_are_not_waves():
    result = classify_series([hand(offset=(-1.) ** index, body_offset=index * 5.) for index in range(25)])
    assert result[-1] == ("open_palm",)
    assert not any("wave" in gestures for gestures in result)


def test_hand_shape_change_and_missing_frames_reset_confirmation():
    result = classify_series([hand() for _ in range(15)] + [hand("thumbs_up")] + [hand() for _ in range(15)])
    assert not any(result)
    result = classify_series([hand() for _ in range(20)], [i / 10 for i in range(10)] + [2 + i / 10 for i in range(10)])
    assert not any(result)


def test_tracks_and_hands_do_not_share_confirmation():
    history = [KeypointSample(i, i / 10, hand()) for i in range(21)]
    classifier = GestureClassifier(settings)
    assert classifier.classify(replace(hand(), track_id=99), 2.1, history) == ()
    assert classifier.classify(replace(hand(), schema="mediapipe_right_hand21"), 2.1, history) == ()


def test_history_duration_must_cover_confirmation():
    config = SimpleNamespace(**{**vars(settings), "cv_keypoint_history_seconds": 1.})
    with pytest.raises(ValueError, match="history"):
        GestureClassifier(config)


@pytest.mark.parametrize("shape", ["thumbs_up", "open_palm"])
def test_current_configuration_confirms_within_two_seconds_at_sparse_valid_captures(shape):
    result = classify_series([hand(shape) for _ in range(5)], [0., .4, .8, 1.2, 1.6], config=settings)
    assert not any(result[:3])
    assert result[3] == (shape,)


def test_current_configuration_confirms_a_two_second_wrist_wave():
    result = classify_series([hand(offset=35 * sin(index / 10 * 2 * pi * 1.5))
                              for index in range(21)], config=settings)
    assert any(gestures == ("wave",) for gestures in result)


def test_brief_uncertain_hand_does_not_reset_valid_evidence_or_count_as_evidence():
    uncertain = replace(hand(), points=())
    result = classify_series([hand(), hand(), uncertain, hand(), hand()],
                             [0., .4, .6, .8, 1.2], config=settings)
    assert result[-1] == ("open_palm",)
    assert not any(classify_series([hand(), uncertain, uncertain, hand()],
                                  [0., .4, .8, 1.2], config=settings))
