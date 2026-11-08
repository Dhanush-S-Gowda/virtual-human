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

    monkeypatch.setattr(pipeline.settings, "gesture_min_duration_seconds", 1.25)
    monkeypatch.setattr(pipeline.settings, "tts_volume_gain", 1.8)
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
    assert speech["playback"] == {"gesture_min_duration_seconds": 1.25, "tts_volume_gain": 1.8, "gesture_pacing": "speech"}
    assert speech["gestures"] == [{"action": "greeting", "at_ms": 0., "word_index": 0, "duration_ms": 300.}]


def test_gesture_duration_uses_provider_word_interval_after_punctuation():
    from inline_gestures import StreamingCueAligner
    parser = InlineGestures()
    text = parser.feed("Hello. [clapping]Clap [laughing]laugh.")
    payload = {"words": ["Hello", ".", "Clap", "laugh", "."],
               "wtimes": [0, 200, 300, 1100, 1600],
               "wdurations": [200, 100, 800, 500, 100]}
    expected = [{"action": "clapping", "at_ms": 300., "word_index": 1, "duration_ms": 800.},
                {"action": "laughing", "at_ms": 1100., "word_index": 2, "duration_ms": 500.}]
    assert StreamingCueAligner(text, parser.cues).feed(payload) == expected
    assert align_cues(text, parser.cues, payload) == expected


@pytest.mark.asyncio
async def test_health_exposes_configurable_playback_settings(monkeypatch):
    import httpx
    from main import create_app
    monkeypatch.setattr(pipeline.settings, "gesture_min_duration_seconds", .75)
    monkeypatch.setattr(pipeline.settings, "tts_volume_gain", 2.0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["playback"] == {"gesture_min_duration_seconds": .75, "tts_volume_gain": 2.0, "gesture_pacing": "speech"}


@pytest.mark.parametrize("value, expected", [("0.1", .75), ("9", 1.5), ("1.2", 1.2), ("-3", .75)])
def test_inline_speed_is_clipped_and_hidden(value, expected):
    parser = InlineGestures(speed_min=.75, speed_max=1.5)
    text = parser.feed("[speed:" + value + "][greeting]Hi.")
    assert text == "Hi."
    assert list(parser.speed_segments(text, 0)) == [("Hi.", 0, expected)]
    assert parser.cues == [Cue(0, "greeting")]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "fast", "1.2.3", ""])
def test_invalid_speed_is_rejected_without_changing_rate(value):
    parser = InlineGestures()
    assert parser.feed("[speed:" + value + "]Hi.") == "Hi."
    assert parser.speed_changes == [(0, 1.0)]
    assert parser.rejected == 1


def test_chunked_speed_changes_keep_phrase_and_gesture_offsets():
    parser = InlineGestures()
    text = "".join(parser.feed(chunk) for chunk in ["[spe", "ed:0.8]Slow. ", "[speed:1.", "4][clapping]Fast."])
    assert text == "Slow. Fast."
    assert list(parser.speed_segments(text, 0)) == [("Slow.", 0, .8), ("Fast.", 6, 1.4)]
    assert take_segment_cues(parser, 0, "Slow.") == []
    assert take_segment_cues(parser, 6, "Fast.") == [Cue(0, "clapping")]


@pytest.mark.asyncio
async def test_inline_speed_reaches_tts_per_phrase(monkeypatch):
    class SpeedLLM(FakeLLM):
        async def stream_chat(self, messages):
            for chunk in ["[speed:0.", "1][greeting]Slow. [speed:9]Fast."]:
                yield chunk
    rates = []
    class SpeedTTS(FakeTTS):
        async def synthesize_segment(self, text, voice, speed):
            rates.append((text, speed))
            return await super().synthesize_segment(text, voice, speed)
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=({"speaker": None}, {})))
    app = SimpleNamespace(state=SimpleNamespace(providers=SimpleNamespace(
        llm=SpeedLLM(), stt=FakeSTT(), tts=SpeedTTS()), conversations=OrderedDict()))
    socket = FakeWebSocket(app, {"type": "start", "session_id": "speed-test", "voice": "af_heart"}, [
        {"type": "websocket.receive", "bytes": b"sample"},
        {"type": "websocket.receive", "text": '{"type":"end"}'}])
    await asyncio.wait_for(pipeline.handle_websocket(socket), 3)
    assert rates == [("Slow.", .75), ("Fast.", 1.5)]
    assert "".join(event["text"] for event in socket.events if event["type"] == "llm_delta") == "Slow. Fast."


@pytest.mark.parametrize("limits", [(float("nan"), 1.5), (1.5, .75), (.1, 1.5), (.75, 9)])
def test_invalid_speed_limits_are_rejected(limits):
    with pytest.raises(ValueError):
        InlineGestures(speed_min=limits[0], speed_max=limits[1])


def test_speed_tag_count_is_bounded_and_unclosed_tag_is_not_spoken():
    parser = InlineGestures()
    assert parser.feed("[speed:1.2]" * 1000 + "Hi.[speed:0.8") == "Hi."
    parser.finish()
    assert len(parser.speed_changes) == 64
    assert parser.rejected > 0
