"""The local demo must accept conversation sockets without a login gateway."""

from fastapi.testclient import TestClient

import main


def test_conversation_socket_is_available_without_identity_headers(monkeypatch):
    async def conversation(socket):
        await socket.accept()
        await socket.send_json({"type": "ready"})
        await socket.close()

    monkeypatch.setattr(main, "handle_websocket", conversation)
    with TestClient(main.create_app()).websocket_connect("/ws") as socket:
        assert socket.receive_json() == {"type": "ready"}
