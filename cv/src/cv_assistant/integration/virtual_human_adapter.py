"""Virtual Human adapter — integration bridge between CV and the assistant.

Provides:
- A WebSocket server for real-time event streaming.
- Methods for the assistant to query current scene state.
- A transcript receiver for name-binding integration.
- Start/stop lifecycle management.

The adapter has no hard dependency on avatar rendering or TTS internals.
It uses the same async event loop but communicates via WebSocket messages
to avoid blocking the assistant's audio pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from cv_assistant import events
from cv_assistant.config import settings
from cv_assistant.state import SceneState

logger = logging.getLogger(__name__)


class VirtualHumanAdapter:
    """Bridge between the CV subsystem and the virtual-human assistant.

    This adapter:
    - Exposes a WebSocket server for the assistant to connect to.
    - Receives STT transcript events from the assistant.
    - Sends CV events to connected clients.
    - Provides synchronous scene state queries.
    """

    def __init__(
        self,
        scene: SceneState,
        on_transcript: Callable[[str], list[events.CVEvent]] | None = None,
        on_detect_objects: Callable[[], list[dict[str, Any]]] | None = None,
        on_frame: Callable[[int, float, bytes], None] | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self._scene = scene
        self._on_transcript = on_transcript
        self._on_detect_objects = on_detect_objects
        self._on_frame = on_frame
        self._host = host or settings.integration.websocket_host
        self._port = port or settings.integration.websocket_port
        self._clients: set[Any] = set()
        self._server: Any = None
        self._event_queue: asyncio.Queue[events.CVEvent] = asyncio.Queue(maxsize=100)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket server and event broadcaster."""
        try:
            import websockets
            self._server = await websockets.serve(
                self._handle_client,
                self._host,
                self._port,
            )
            logger.info(
                "CV adapter WebSocket server listening on ws://%s:%d",
                self._host, self._port,
            )
        except ImportError:
            logger.warning(
                "websockets package not installed. "
                "CV adapter will work in direct-call mode only."
            )
        except Exception as exc:
            logger.error("Failed to start WebSocket server: %s", exc)

    async def stop(self) -> None:
        """Shut down the WebSocket server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()
        logger.info("CV adapter stopped.")

    def has_clients(self) -> bool:
        """Check if any WebSocket clients are currently connected."""
        return len(self._clients) > 0

    async def broadcast_frame(self, jpeg_bytes: bytes, metadata: dict[str, Any]) -> None:
        """Broadcast a synchronized video frame with detection metadata to clients."""
        if not self._clients:
            return
        meta_json = json.dumps(metadata).encode("utf-8")
        packet = len(meta_json).to_bytes(4, byteorder="big") + meta_json + jpeg_bytes
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send(packet)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    async def broadcast_metadata(self, metadata: dict[str, Any]) -> None:
        """Broadcast metadata JSON to connected clients."""
        if not self._clients:
            return
        msg = json.dumps(metadata)
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    # ── Event emission ──────────────────────────────────────────────────

    async def emit_event(self, event: events.CVEvent) -> None:
        """Broadcast a CV event to all connected clients."""
        msg = event.to_json()
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    async def broadcast_metadata(self, metadata: dict[str, Any]) -> None:
        """Broadcast metadata JSON to connected clients."""
        if not self._clients:
            return
        msg = json.dumps(metadata)
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    def emit_event_sync(self, event: events.CVEvent) -> None:
        """Queue an event for async broadcast (call from sync context)."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full. Dropping event: %s", event.event_type)

    async def drain_events(self) -> None:
        """Send all queued events to connected clients."""
        while not self._event_queue.empty():
            try:
                event = self._event_queue.get_nowait()
                await self.emit_event(event)
            except asyncio.QueueFull:
                break

    # ── Scene queries ───────────────────────────────────────────────────

    def get_current_scene(self) -> dict[str, Any]:
        """Return a compact JSON snapshot of the current scene."""
        return self._scene.get_current_scene()

    def get_visible_people(self) -> list[dict[str, Any]]:
        """Return the list of currently visible people."""
        return self._scene.get_visible_people()

    # ── Transcript receiver ─────────────────────────────────────────────

    async def receive_transcript(self, text: str) -> list[events.CVEvent]:
        """Process an STT transcript from the assistant.

        Delegates to the registered ``on_transcript`` handler (typically
        the speaker association module).
        """
        if self._on_transcript is not None:
            return self._on_transcript(text)
        return []

    # ── WebSocket handler ───────────────────────────────────────────────

    async def _handle_client(self, websocket: Any, *args: Any, **kwargs: Any) -> None:
        """Handle an incoming WebSocket connection."""
        self._clients.add(websocket)
        logger.info("Client connected to CV adapter. Total: %d", len(self._clients))
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await self._handle_binary_message(websocket, message)
                else:
                    try:
                        data = json.loads(message)
                        await self._handle_message(websocket, data)
                    except json.JSONDecodeError:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "message": "Invalid JSON",
                        }))
        except Exception as exc:
            logger.debug("Client disconnected: %s", exc)
        finally:
            self._clients.discard(websocket)
            logger.info("Client disconnected. Total: %d", len(self._clients))

    async def _handle_binary_message(self, websocket: Any, message: bytes) -> None:
        """Handle incoming binary frame packets from frontend.

        Protocol:
        - 4B Magic: b'FRM1' (0x46524D31)
        - 4B Seq: uint32 big-endian
        - 8B Timestamp: float64 big-endian (ms)
        - 4B Length: uint32 big-endian (JPEG payload size)
        - Raw JPEG bytes
        """
        if len(message) < 20:
            logger.warning("Dropped binary packet: too small (%d bytes)", len(message))
            return

        magic = message[:4]
        if magic != b"FRM1":
            logger.warning("Dropped binary packet: invalid magic %r", magic)
            return

        import struct
        seq, ts_ms, jpeg_len = struct.unpack(">IdI", message[4:20])
        if len(message) != 20 + jpeg_len:
            logger.warning(
                "Dropped binary packet: payload length mismatch (header %d, actual %d)",
                jpeg_len, len(message) - 20,
            )
            return

        jpeg_bytes = message[20:20 + jpeg_len]
        if self._on_frame is not None:
            self._on_frame(seq, ts_ms / 1000.0, jpeg_bytes)

    async def _handle_message(self, websocket: Any, data: dict[str, Any]) -> None:
        """Route incoming messages from clients."""
        msg_type = data.get("type", "")

        if msg_type == "get_scene":
            scene = self.get_current_scene()
            await websocket.send(json.dumps({"type": "scene", "data": scene}))

        elif msg_type == "get_people":
            people = self.get_visible_people()
            await websocket.send(json.dumps({"type": "people", "data": people}))

        elif msg_type == "transcript":
            text = data.get("text", "")
            results = await self.receive_transcript(text)
            for ev in results:
                await self.emit_event(ev)
            await websocket.send(json.dumps({
                "type": "transcript_ack",
                "events_emitted": len(results),
            }))

        elif msg_type == "detect_objects":
            if self._on_detect_objects is not None:
                objs = self._on_detect_objects()
                await websocket.send(json.dumps({"type": "objects", "data": objs}))
            else:
                scene = self.get_current_scene()
                await websocket.send(json.dumps({"type": "objects", "data": scene.get("objects", [])}))

        else:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            }))

    def detect_objects_oneshot(self) -> list[dict[str, Any]]:
        """Run or return one-shot object detection."""
        if self._on_detect_objects is not None:
            return self._on_detect_objects()
        return self.get_current_scene().get("objects", [])
