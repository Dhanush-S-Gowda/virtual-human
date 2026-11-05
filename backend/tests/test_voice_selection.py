"""Frontend-selected voices are validated and forwarded without YAML voice state."""

from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_backend import FakeLLM, FakeTTS, FakeWebSocket
from config import settings
from main import create_app
import pipeline
from tts.voices import VOICES, resolve_voice


@pytest.mark.parametrize("provider,voice", [(p, v) for p, voices in VOICES.items() for v in voices])
@pytest.mark.parametrize("mode", ["speak", "conversation"])
@pytest.mark.asyncio
async def test_frontend_voice_is_used_for_synthesis(provider, voice, mode, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", provider)
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=(
        {"status": "unavailable", "people": [], "objects": [], "speaker": None}, {})))
    tts = FakeTTS()
    tts.synthesize_segment = AsyncMock(wraps=tts.synthesize_segment)
    app = SimpleNamespace(state=SimpleNamespace(conversations=OrderedDict(), providers=SimpleNamespace(
        llm=FakeLLM(), stt=None, tts=tts)))
    client = FakeWebSocket(app, {"type": "start", "mode": mode, "text": "Hello.", "voice": voice})
    await pipeline.handle_websocket(client)
    assert tts.synthesize_segment.await_args.args[1] == voice
    assert any(event.get("type") == "speech" for event in client.events)


@pytest.mark.parametrize("provider,voice", [("kokoro", "M1"), ("supertonic", "am_puck"),
                                           ("kokoro", ""), ("supertonic", "/tmp/style.json")])
def test_unsupported_and_cross_provider_voices_are_rejected(provider, voice):
    with pytest.raises(ValueError, match="Choose a"):
        resolve_voice(provider, voice)


@pytest.mark.parametrize("provider,default", [("kokoro", "af_heart"), ("supertonic", "F1")])
@pytest.mark.asyncio
async def test_health_advertises_only_active_provider_voices(provider, default, monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", provider)
    app = create_app()
    health = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/health")
    info = (await health())["providers"]
    assert info["tts_voices"] == list(VOICES[provider])
    assert info["tts_default_voice"] == default
    assert resolve_voice(provider) == default
