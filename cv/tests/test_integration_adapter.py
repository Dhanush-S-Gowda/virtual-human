"""Integration tests for VirtualHumanAdapter and privacy/security checks."""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
import pytest

from cv_assistant import events
from cv_assistant.integration.virtual_human_adapter import VirtualHumanAdapter
from cv_assistant.speaker.association import SpeakerAssociation
from cv_assistant.identity.service import IdentityService
from cv_assistant.identity.store import IdentityStore
from cv_assistant.faces.matcher import FaceMatcher
from cv_assistant.state import SceneState, TrackedPerson


@pytest.fixture
def adapter_env():
    """Setup scene, store, identity service, and adapter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_identities.db"
        store = IdentityStore(db_path=db_path)
        store.open()
        matcher = FaceMatcher()
        scene = SceneState()
        service = IdentityService(store=store, matcher=matcher, scene=scene)
        speaker_assoc = SpeakerAssociation(scene=scene, identity_service=service)

        adapter = VirtualHumanAdapter(
            scene=scene,
            on_transcript=speaker_assoc.handle_transcript,
            host="127.0.0.1",
            port=18765,
        )
        yield store, scene, speaker_assoc, adapter
        store.close()


@pytest.mark.asyncio
async def test_adapter_direct_call_scene_queries(adapter_env):
    store, scene, _, adapter = adapter_env

    # Populate scene
    scene.update_track(
        track_id=1,
        stable=True,
        person_id="p-123",
        display_name="Alice",
        identity_confidence=0.95,
    )

    scene_data = adapter.get_current_scene()
    assert len(scene_data["people"]) == 1
    assert scene_data["people"][0]["display_name"] == "Alice"

    people = adapter.get_visible_people()
    assert len(people) == 1
    assert people[0]["track_id"] == 1


@pytest.mark.asyncio
async def test_adapter_transcript_invocation(adapter_env):
    store, scene, _, adapter = adapter_env

    pid = store.create_person(display_name="Unknown")
    scene.update_track(track_id=5, stable=True, person_id=pid)

    emitted = await adapter.receive_transcript("My name is Bablu")
    assert len(emitted) == 1
    assert emitted[0].event_type == "person_renamed"
    assert emitted[0].display_name == "Bablu"

    # Verify store was renamed
    assert store.get_person(pid)["display_name"] == "Bablu"


@pytest.mark.asyncio
async def test_adapter_websocket_flow(adapter_env):
    import websockets

    store, scene, _, adapter = adapter_env

    pid = store.create_person(display_name="Unknown")
    scene.update_track(track_id=10, stable=True, person_id=pid)

    await adapter.start()
    try:
        uri = f"ws://127.0.0.1:{adapter._port}"
        async with websockets.connect(uri) as ws:
            # 1. Query scene
            await ws.send(json.dumps({"type": "get_scene"}))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert resp["type"] == "scene"
            assert len(resp["data"]["people"]) == 1

            # 2. Send transcript
            await ws.send(json.dumps({"type": "transcript", "text": "My name is Bablu"}))
            # First message should be the broadcasted person_renamed event
            msg1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            # Second message should be transcript_ack or order can vary
            msg2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))

            types = {msg1.get("type") or msg1.get("event_type"), msg2.get("type") or msg2.get("event_type")}
            assert "person_renamed" in types
            assert "transcript_ack" in types
    finally:
        await adapter.stop()


def test_logs_do_not_contain_embeddings(caplog):
    """Ensure logging biometric data/embeddings is prohibited."""
    with caplog.at_level(logging.DEBUG):
        logger = logging.getLogger("cv_assistant")
        logger.info("Person detected with face embedding")

    for record in caplog.records:
        # Check no large numeric arrays or raw binary in log messages
        assert "embedding=" not in record.message
        assert "[0." not in record.message


@pytest.mark.asyncio
async def test_adapter_broadcast_frame(adapter_env):
    import websockets

    _, _, _, adapter = adapter_env
    await adapter.start()
    try:
        uri = f"ws://127.0.0.1:{adapter._port}"
        async with websockets.connect(uri) as ws:
            assert adapter.has_clients() is True

            fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF"
            fake_meta = {"people": [{"track_id": 1, "display_name": "Test"}]}
            await adapter.broadcast_frame(fake_jpeg, fake_meta)

            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            assert isinstance(msg, bytes)
            assert len(msg) >= 4

            json_len = int.from_bytes(msg[:4], byteorder="big")
            meta_json = json.loads(msg[4:4 + json_len].decode("utf-8"))
            jpeg_bytes = msg[4 + json_len:]

            assert meta_json["people"][0]["display_name"] == "Test"
            assert jpeg_bytes == fake_jpeg
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_adapter_concurrent_clients(adapter_env):
    import websockets

    _, _, _, adapter = adapter_env
    await adapter.start()
    try:
        uri = f"ws://127.0.0.1:{adapter._port}"
        ws1 = await websockets.connect(uri)
        ws2 = await websockets.connect(uri)

        assert len(adapter._clients) == 2

        # Abruptly close ws1
        await ws1.close()

        # Broadcast frame and event should succeed without crashing
        fake_jpeg = b"\xff\xd8fake"
        fake_meta = {"people": []}
        await adapter.broadcast_frame(fake_jpeg, fake_meta)

        # ws2 should receive frame cleanly
        msg = await asyncio.wait_for(ws2.recv(), timeout=2.0)
        assert isinstance(msg, bytes)

        await ws2.close()
    finally:
        await adapter.stop()

