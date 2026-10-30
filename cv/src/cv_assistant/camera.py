"""Camera capture service.

Provides a bounded-queue frame source that:
- Reads from a webcam (by device index) or a video file.
- Drops stale frames so slow inference never builds a backlog.
- Adds monotonic timestamps and sequence numbers.
- Handles camera disconnect/reconnect and graceful shutdown.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Frame:
    """A captured frame with metadata."""

    image: np.ndarray
    seq: int
    timestamp: float  # time.monotonic()
    width: int
    height: int


class CameraService:
    """Asynchronous camera reader with a bounded latest-frame queue.

    Parameters
    ----------
    device : int | str
        Webcam index or path to a video file.
    width, height : int
        Requested capture resolution (camera may clamp).
    fps : int
        Requested capture FPS.
    queue_size : int
        Maximum queued frames.  When full, the oldest frame is dropped.
    """

    def __init__(
        self,
        device: int | str = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        queue_size: int = 2,
    ) -> None:
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._queue: queue.Queue[Frame | None] = queue.Queue(maxsize=queue_size)
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._seq = 0
        self._is_video_file = isinstance(device, str) and not device.isdigit()

    # ── Public API ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the camera and begin capturing in a background thread."""
        if self._running.is_set():
            return
        self._open_capture()
        self._running.set()
        self._thread = threading.Thread(
            target=self._capture_loop, name="cv-camera", daemon=True
        )
        self._thread.start()
        logger.info(
            "Camera started: device=%s resolution=%dx%d fps=%d",
            self._device, self._width, self._height, self._fps,
        )

    def stop(self) -> None:
        """Signal shutdown and release the camera."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._release_capture()
        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        logger.info("Camera stopped.")

    def get_frame(self, timeout: float = 1.0) -> Frame | None:
        """Block up to *timeout* seconds for the next frame.

        Returns ``None`` if no frame is available or the service is stopped.
        """
        try:
            frame = self._queue.get(timeout=timeout)
            return frame
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def is_video_file(self) -> bool:
        return self._is_video_file

    # ── Internal ────────────────────────────────────────────────────────

    def _open_capture(self) -> None:
        device = self._device
        if isinstance(device, str) and device.isdigit():
            device = int(device)
        self._cap = cv2.VideoCapture(device)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera/video: {self._device}")
        if not self._is_video_file:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)

    def _release_capture(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _capture_loop(self) -> None:
        """Run in a background thread: read frames and push to the queue."""
        reconnect_delay = 1.0
        while self._running.is_set():
            if self._cap is None or not self._cap.isOpened():
                logger.warning("Camera lost. Attempting reconnect in %.1fs...", reconnect_delay)
                time.sleep(reconnect_delay)
                try:
                    self._release_capture()
                    self._open_capture()
                    logger.info("Camera reconnected.")
                    reconnect_delay = 1.0
                except Exception:
                    reconnect_delay = min(reconnect_delay * 2, 10.0)
                    continue

            ret, raw_frame = self._cap.read()
            if not ret:
                if self._is_video_file:
                    logger.info("End of video file.")
                    self._running.clear()
                    # Signal consumers
                    try:
                        self._queue.put_nowait(None)
                    except queue.Full:
                        pass
                    break
                # Camera read failure — try reconnect
                self._release_capture()
                continue

            self._seq += 1
            h, w = raw_frame.shape[:2]
            frame = Frame(
                image=raw_frame,
                seq=self._seq,
                timestamp=time.monotonic(),
                width=w,
                height=h,
            )

            # Bounded queue: drop oldest frame if full
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                pass  # Extremely unlikely after the drop above

            # Pace the capture thread to avoid busy-looping
            if not self._is_video_file and self._fps > 0:
                time.sleep(1.0 / self._fps)
