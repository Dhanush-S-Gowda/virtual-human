"""Camera prompt compaction must retain observed changes and time ranges."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from world import compact_visual_history


def window(start, end, people=1, **fields):
    return {"window_id": start, "revision": 4, "closed": True,
            "start_seconds_ago": start, "end_seconds_ago": end,
            "counts": {"unique_people": people}, "gestures": [], **fields}


def test_unchanged_adjacent_windows_merge_without_mutating_source():
    history = {"requested_seconds": 10, "windows": [window(10, 9), window(9, 8), window(8, 7)]}
    original = deepcopy(history)
    result = compact_visual_history(history)
    assert history == original
    assert result["requested_seconds"] == 10
    assert result["windows"] == [{"start_seconds_ago": 10, "end_seconds_ago": 7,
                                  "counts": {"unique_people": 1}, "gestures": [], "window_count": 3}]


def test_changes_gaps_and_gesture_ages_remain_distinct():
    windows = [window(10, 9), window(9, 8, people=2), window(7, 6, people=2),
               window(6, 5, people=2, gestures=[{"action": "wave", "last_seen_seconds_ago": 5.5}]),
               window(5, 4, people=2, gestures=[{"action": "wave", "last_seen_seconds_ago": 4.5}])]
    result = compact_visual_history({"windows": windows})["windows"]
    assert len(result) == len(windows)
    assert result[-1]["gestures"][0]["last_seen_seconds_ago"] == 4.5


def test_pending_observations_and_missing_timestamps_do_not_merge():
    windows = [window(3, 2, pending_tasks=1), window(2, 1, pending_tasks=0),
               {"gestures": []}, {"gestures": []}]
    assert len(compact_visual_history({"windows": windows})["windows"]) == 4
