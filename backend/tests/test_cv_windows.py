"""Window behavior with deterministic fake providers; no camera/model required."""

import asyncio
import unittest
from types import SimpleNamespace

from backend.cv.events import Frame
from backend.cv.face_recog.base import FaceRecognition
from backend.cv.keypoint_det.base import KeypointDetection
from backend.cv.object_det.base import ObjectDetection
from backend.cv.window_manager import WindowManager


class Objects:
    def __init__(self, gate=None):
        self.gate = gate
        self.calls = []

    async def initialize(self):
        pass

    async def detect(self, frame):
        self.calls.append(frame.frame_id)
        if self.gate:
            await self.gate.wait()
        return (
            ObjectDetection(1, "person", .9, (0, 0, 10, 10)),
            ObjectDetection(2, "cup", .9, (20, 0, 30, 10)),
            ObjectDetection(3, "cup", .9, (40, 0, 50, 10)),
        )


class Faces:
    def __init__(self):
        self.calls = []

    async def initialize(self):
        pass

    async def recognize(self, frame, person):
        self.calls.append((frame.frame_id, person.track_id))
        return FaceRecognition(person.track_id, "known", "Alex", .9)


class Points:
    def __init__(self):
        self.calls = []

    async def initialize(self):
        pass

    async def detect(self, frame, history):
        self.calls.append((frame.frame_id, history))
        return (KeypointDetection(1, "test", ((1., 2., .9),), ("wave",)),)


class WindowTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, **kwargs):
        return WindowManager(Objects(), Faces(), Points(), **kwargs)

    async def test_late_results_revise_original_frame_and_window(self):
        gate = asyncio.Event()
        manager = WindowManager(Objects(gate), Faces(), Points())
        manager.submit(Frame(1, .01, SimpleNamespace(shape=(10, 10, 3))))
        await asyncio.sleep(0)
        manager.advance(1.)
        initial = manager.drain_events()[0].summary
        self.assertEqual((initial.start, initial.end, initial.unique_objects), (0., 1., 0))
        self.assertEqual(initial.pending_tasks, 2)
        gate.set()
        await manager.wait_idle()
        revised = manager.drain_events()[0].summary
        self.assertGreater(revised.revision, initial.revision)
        self.assertEqual(revised.unique_objects, 3)
        self.assertEqual(dict(revised.object_counts), {"person": 1, "cup": 2})
        self.assertEqual(revised.people[0].display_name, "Alex")
        self.assertEqual(revised.people[0].gestures, ("wave",))
        self.assertEqual(revised.pending_tasks, 0)
        self.assertEqual(manager.get_window(0)[0].timestamp, .01)

    async def test_capture_rate_and_history_across_windows(self):
        manager = self.manager()
        for frame_id in range(101):
            manager.submit(Frame(frame_id, frame_id / 50, SimpleNamespace(shape=(10, 10, 3))))
            await manager.wait_idle()
        self.assertEqual(len(manager.object_provider.calls), 21)
        self.assertEqual(len(manager.keypoint_provider.calls), 21)
        self.assertEqual(manager.face_provider.calls, [(0, 1)])
        self.assertEqual(manager.get_window(1)[0].faces[0].display_name, "Alex")
        history = manager.keypoint_provider.calls[10][1]
        self.assertTrue(history)
        self.assertTrue(all(sample.timestamp < 1. for sample in history))
        self.assertEqual(manager.get_window(1)[0].frame_id, 50)

    async def test_busy_provider_does_not_accumulate_tasks(self):
        gate = asyncio.Event()
        manager = WindowManager(Objects(gate), Faces(), Points())
        for frame_id in range(50):
            manager.submit(Frame(frame_id, frame_id / 50, SimpleNamespace(shape=(10, 10, 3))))
            await asyncio.sleep(0)
        self.assertEqual(manager.object_provider.calls, [0])
        gate.set()
        await manager.wait_idle()

    async def test_unusable_face_is_retried_then_stops_after_capture(self):
        class RetryFaces(Faces):
            async def recognize(self, frame, person):
                self.calls.append((frame.frame_id, person.track_id))
                return FaceRecognition(person.track_id, needs_retry=len(self.calls) < 2)

        faces = RetryFaces()
        manager = WindowManager(Objects(), faces, Points())
        for index in range(31):
            manager.submit(Frame(index, index / 10, SimpleNamespace(shape=(10, 10, 3))))
            await manager.wait_idle()
        self.assertEqual(faces.calls, [(0, 1), (10, 1)])
        await manager.close()

    async def test_same_tracker_id_after_observed_absence_requires_fresh_face_matching(self):
        class Occluded(Objects):
            async def detect(self, frame):
                return () if frame.frame_id == 1 else await super().detect(frame)

        class ForgetfulFaces(Faces):
            def __init__(self):
                super().__init__()
                self.forgotten = []

            async def forget_track(self, track_id):
                self.forgotten.append(track_id)

        faces = ForgetfulFaces()
        manager = WindowManager(Occluded(), faces, Points())
        for index, timestamp in enumerate((0., .2, 1.2)):
            manager.submit(Frame(index, timestamp, SimpleNamespace(shape=(10, 10, 3))))
            await manager.wait_idle()
        self.assertEqual(faces.forgotten, [1])
        self.assertEqual(faces.calls, [(0, 1), (2, 1)])
        self.assertEqual(manager._track_generations[1], 2)
        await manager.close()

    async def test_slow_continuous_object_results_do_not_imply_a_person_left(self):
        manager = self.manager()
        for index, timestamp in enumerate((0., 1.2, 2.4)):
            manager.submit(Frame(index, timestamp, SimpleNamespace(shape=(10, 10, 3))))
            await manager.wait_idle()
        self.assertEqual(manager.face_provider.calls, [(0, 1)])
        await manager.close()

    async def test_expired_window_discards_late_results(self):
        gate = asyncio.Event()
        manager = WindowManager(Objects(gate), Faces(), Points(), retained_windows=2)
        manager.submit(Frame(0, 0., SimpleNamespace(shape=(10, 10, 3))))
        await asyncio.sleep(0)
        manager.advance(10.)
        gate.set()
        await manager.wait_idle()
        self.assertEqual(manager.discarded_results, 2)
        with self.assertRaises(KeyError):
            manager.get_window(0)
        self.assertLessEqual(len(manager.drain_events()), 2)

    async def test_errors_recorded_and_pipeline_continues(self):
        class Broken(Objects):
            async def detect(self, frame):
                raise RuntimeError("offline")
        manager = WindowManager(Broken(), Faces(), Points())
        manager.submit(Frame(0, 0., SimpleNamespace(shape=(10, 10, 3))))
        await manager.wait_idle()
        manager.advance(1.)
        summary = manager.drain_events()[0].summary
        self.assertIn("offline", summary.errors[0])
        self.assertEqual(summary.pending_tasks, 0)

    async def test_input_validation(self):
        manager = self.manager()
        manager.submit(Frame(1, 1., SimpleNamespace(shape=(10, 10, 3))))
        with self.assertRaises(ValueError):
            manager.submit(Frame(1, 1.1, SimpleNamespace(shape=(10, 10, 3))))
        with self.assertRaises(ValueError):
            manager.advance(.5)
        with self.assertRaises(ValueError):
            Frame(2, float("nan"), SimpleNamespace(shape=(10, 10, 3)))
        await manager.close()
        self.assertEqual(manager._windows[1].pending_tasks, 0)
        with self.assertRaises(RuntimeError):
            manager.submit(Frame(2, 1.2, SimpleNamespace(shape=(10, 10, 3))))

    async def test_network_delay_does_not_reassign_capture_window(self):
        manager = self.manager()
        manager.submit(Frame(0, .9, SimpleNamespace(shape=(10, 10, 3))))
        await manager.wait_idle()
        manager.advance(1.05)
        manager.submit(Frame(1, 1.01, SimpleNamespace(shape=(10, 10, 3))))
        await manager.wait_idle()
        manager.advance(2.)
        self.assertEqual(manager.get_window(0)[0].frame_id, 0)
        self.assertEqual(manager.get_window(1)[0].frame_id, 1)
        self.assertEqual(manager.get_window(1)[0].timestamp, 1.01)


if __name__ == "__main__":
    unittest.main()
