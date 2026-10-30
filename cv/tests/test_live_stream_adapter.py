import asyncio
import json
import pytest
from cv_assistant.integration.virtual_human_adapter import VirtualHumanAdapter
from cv_assistant.state import SceneState


@pytest.mark.asyncio
async def test_adapter_broadcast_frame():
    scene = SceneState()
    adapter = VirtualHumanAdapter(scene=scene, host="127.0.0.1", port=8777)
    
    # Verify has_clients initially False
    assert not adapter.has_clients()
    
    # Mock WebSocket client
    class MockWS:
        def __init__(self):
            self.sent = []
        async def send(self, data):
            self.sent.append(data)
            
    mock_ws = MockWS()
    adapter._clients.add(mock_ws)
    assert adapter.has_clients()
    
    test_jpeg = b"\xff\xd8\xff\xe0mock_jpeg_content"
    metadata = {
        "type": "frame",
        "seq": 42,
        "people": [{"track_id": 1, "bbox": [10, 20, 100, 200], "display_name": "Bablu"}]
    }
    
    await adapter.broadcast_frame(test_jpeg, metadata)
    assert len(mock_ws.sent) == 1
    
    packet = mock_ws.sent[0]
    json_len = int.from_bytes(packet[:4], byteorder="big")
    meta_json = json.loads(packet[4:4+json_len].decode("utf-8"))
    jpeg_payload = packet[4+json_len:]
    
    assert meta_json["seq"] == 42
    assert meta_json["people"][0]["display_name"] == "Bablu"
    assert jpeg_payload == test_jpeg
