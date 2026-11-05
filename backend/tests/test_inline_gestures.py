import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_backend import FakeLLM, FakeSTT, FakeTTS, FakeWebSocket
from inline_gestures import TAGS, Cue, InlineGestures, align_cues, take_segment_cues
import pipeline


def test_actual_loaded_system_prompt_declares_every_allowed_inline_tag():
    from config import settings
    assert all(f"[{tag}]" in settings.system_prompt for tag in TAGS)


def test_chunked_tags_are_verified_and_never_displayed():
    parser = InlineGestures()
    text = "".join(parser.feed(part) for part in ["[greet", "ing]Hello! [", "clapping]Well ", "done. [unknown]", "[nod_yes"])
    parser.finish()
    assert text == "Hello! Well done."
    assert parser.cues == [Cue(0, "greeting"), Cue(6, "clapping")]
    assert parser.rejected == 2


def test_tag_whitespace_is_trimmed_and_unsupported_symbols_are_sanitized():
    parser = InlineGestures()
    text = "".join(parser.feed(part) for part in ["[ \t", "GREETing ", "]Hi 👋", " there! 💯"])
    assert text == "Hi there!"
    assert parser.cues == [Cue(0, "greeting")]
    assert parser.rejected == 0


def test_tag_between_segments_anchors_the_next_spoken_word():
    parser = InlineGestures()
    text = parser.feed("Hello! [enthusiasm] You did it!")
    assert take_segment_cues(parser, 0, "Hello!") == []
    assert take_segment_cues(parser, text.index("You"), "You did it!") == [Cue(0, "enthusiasm")]


def test_normalization_and_provider_words_define_cue_times():
    parser = InlineGestures()
    text = parser.feed("**Great!** 😊 [clapping]You did it.")
    cues = take_segment_cues(parser, 0, text)
    payload = {"words": ["Great!", "You", "did", "it."], "wtimes": [0, 400, 650, 800]}
    assert align_cues(text, cues, payload) == [{"action": "clapping", "at_ms": 400., "word_index": 1}]
    assert align_cues(text, cues, {"words": ["wrong"], "wtimes": [0]}) == []


def test_parser_is_bounded_and_tags_are_allowlisted():
    parser = InlineGestures()
    assert parser.feed("[greeting]Hi[" + "a" * 10000 + "]there") == "Hithere"
    assert parser.cues == [Cue(0, "greeting")]
    assert parser.rejected == 1
    assert parser.pending is None


@pytest.mark.asyncio
async def test_websocket_delivers_clean_text_audio_and_separate_word_timed_gestures(monkeypatch):
    class TaggedLLM(FakeLLM):
        async def stream_chat(self, messages):
            for chunk in ["[ greet", "ing ]Hello 👋", " there."]:
                yield chunk

    class TimedTTS(FakeTTS):
        async def synthesize_segment(self, text, voice, speed):
            assert "[" not in text and "]" not in text
            return SimpleNamespace(model_dump=lambda: {
                "audio_base64": "dGVzdA==", "words": ["Hello", "there."],
                "wtimes": [0, 300], "wdurations": [300, 200]})

    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=({"speaker": None}, {})))
    app = SimpleNamespace(state=SimpleNamespace(providers=SimpleNamespace(
        llm=TaggedLLM(), stt=FakeSTT(), tts=TimedTTS()), conversations=OrderedDict()))
    socket = FakeWebSocket(app, {"type": "start", "session_id": "inline-test", "voice": "af_heart"}, [
        {"type": "websocket.receive", "bytes": b"sample"},
        {"type": "websocket.receive", "text": '{"type":"end"}'}])
    await asyncio.wait_for(pipeline.handle_websocket(socket), 3)
    deltas = [event["text"] for event in socket.events if event["type"] == "llm_delta"]
    assert "".join(deltas) == "Hello there."
    speech = next(event for event in socket.events if event["type"] == "speech")
    assert speech["gestures"] == [{"action": "greeting", "at_ms": 0., "word_index": 0}]
