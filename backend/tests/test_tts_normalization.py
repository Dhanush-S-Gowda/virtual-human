"""LLM display output and provider-bound speech contain only supported text."""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_backend import FakeLLM, FakeTTS, FakeWebSocket
import pipeline
from tts.normalization import normalize_for_tts


@pytest.mark.parametrize(("source", "expected"), [
    ("Hello 👋, Alex! 😀", "Hello, Alex!"),
    ("Well done 👍🏽! 🇺🇸 👨‍👩‍👧‍👦", "Well done!"),
    ("1️⃣ Start here. ❤️ ©️", "Start here."),
    ("# Hello\n- **Welcome**\n- *back* to `Python`.", "Hello Welcome back to Python."),
    ("Read [the guide](https://example.com). ![logo](logo.png)", "Read the guide."),
    ("“Don’t”\u00a0worry…\n\tCafé &amp; tea.", '"Don\'t" worry… Café & tea.'),
    ("co\u200boperate\x00", "cooperate"),
    ("Version 3.5 costs $12.50; 20°C ± 2°C. π = 3.14.",
     "Version 3.5 costs 12.50; 20 C 2 C. π 3.14."),
    ("नमस्ते, José. こんにちは。", "नमस्ते, José. こんにちは。"),
    ("😀 👩🏽‍💻 🇮🇳 2️⃣ ❤️! ...", ""),
    ("", ""),
])
def test_normalize_spoken_text(source, expected):
    assert normalize_for_tts(source) == expected
    assert normalize_for_tts(expected) == expected


class CapturingTTS(FakeTTS):
    def __init__(self):
        self.calls = []

    async def synthesize_segment(self, text, voice, speed):
        self.calls.append(text)
        return await super().synthesize_segment(text, voice, speed)


def app_for_test(tmp_path, monkeypatch, llm=None):
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline, "_forward_transcript_to_cv", AsyncMock())
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=({"speaker": None}, {})))
    tts = CapturingTTS()
    return SimpleNamespace(state=SimpleNamespace(conversations=OrderedDict(),
        providers=SimpleNamespace(tts=tts, llm=llm or FakeLLM()))), tts


@pytest.mark.asyncio
async def test_direct_speech_is_normalized_before_provider_and_timing_text(tmp_path, monkeypatch):
    app, tts = app_for_test(tmp_path, monkeypatch)
    client = FakeWebSocket(app, {"type": "start", "mode": "speak", "text": "**Hello** 👋!"})
    await pipeline.handle_websocket(client)
    assert tts.calls == ["Hello!"]
    speech = next(event for event in client.events if event["type"] == "speech")
    assert speech["text"] == "Hello!"
    assert speech["words"] == ["Hello!"]


@pytest.mark.asyncio
async def test_emoji_only_direct_speech_does_not_call_tts(tmp_path, monkeypatch):
    app, tts = app_for_test(tmp_path, monkeypatch)
    client = FakeWebSocket(app, {"type": "start", "mode": "speak", "text": "👋🏽!"})
    await pipeline.handle_websocket(client)
    assert tts.calls == []
    assert client.events[-1]["type"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Hello 👋. **Welcome** back!", "😀! 👍🏽."])
async def test_streaming_sanitizes_display_history_and_speech_without_losing_order(tmp_path, monkeypatch, reply):
    llm = FakeLLM()

    async def stream(messages):
        # Include an emoji sequence split across arbitrary LLM token boundaries.
        for character in reply:
            yield character

    llm.stream_chat = stream
    app, tts = app_for_test(tmp_path, monkeypatch, llm)
    client = FakeWebSocket(app, {"type": "start", "session_id": "test", "text": "Hi"})
    await asyncio.wait_for(pipeline.handle_websocket(client), 2)
    speech = [event for event in client.events if event["type"] == "speech"]
    expected = ["Hello. Welcome back!"] if reply.startswith("Hello") else []
    assert tts.calls == expected
    assert [event["index"] for event in speech] == list(range(len(expected)))
    expected_display = "Hello. **Welcome** back!" if reply.startswith("Hello") else "!."
    assert "".join(event["text"] for event in client.events if event["type"] == "llm_delta") == expected_display
    assert client.events[-1] == {"type": "done", "text": expected_display}
    assert normalize_for_tts(app.state.conversations["test"].history[-1]["content"]) == normalize_for_tts(reply)
    metrics = next(event["metrics"] for event in client.events if event["type"] == "metrics")
    assert metrics["tts_segments"] == len(expected)
