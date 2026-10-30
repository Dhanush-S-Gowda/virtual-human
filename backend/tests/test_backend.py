import asyncio
import json
from pathlib import Path
import sys
import unittest
from collections import OrderedDict
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config import settings
from llm.openai import OpenAIProvider
from main import create_app
from pipeline import _split_ready_segments, handle_websocket
from schemas import SynthesizeResponse
from tts.kokoro import validate_timestamps


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def health_check(self):
        return True

    async def stream_chat(self, messages) -> AsyncIterator[str]:
        self.calls.append(messages)
        if self.fail:
            raise httpx.ConnectError("offline")
        yield "Hello "
        yield "from provider."


class FakeTTS:
    async def synthesize_segment(self, text, voice, speed):
        return SynthesizeResponse(
            audio_base64="YQ==",
            audio_encoding="wav",
            words=[text],
            wtimes=[0],
            wdurations=[100],
        )


class ConcurrentFakeTTS(FakeTTS):
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def synthesize_segment(self, text, voice, speed):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.03 if text.startswith("First") else 0.005)
            return await super().synthesize_segment(text, voice, speed)
        finally:
            self.active -= 1


class FakeSTT:
    async def transcribe(self, audio, content_type):
        assert audio == b"sample"
        assert content_type == "audio/webm"
        return {"text": "Hello", "language": "en", "duration": 1.0}


class FakeWebSocket:
    def __init__(self, app, start, incoming=()):
        self.app = app
        self.start = start
        self.incoming = iter(incoming)
        self.events = []

    async def accept(self):
        return None

    async def receive_json(self):
        return self.start

    async def receive(self):
        return next(self.incoming)

    async def send_json(self, event):
        self.events.append(event)

    async def close(self, code=None):
        return None


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM()
        providers = SimpleNamespace(llm=self.llm, tts=FakeTTS(), stt=FakeSTT())
        self.app = create_app(providers)
        self.app.state.providers = providers
        self.app.state.conversations = OrderedDict()

    def run_audio_turn(self, session_id):
        websocket = FakeWebSocket(
            self.app,
            {"type": "start", "session_id": session_id, "mime_type": "audio/webm"},
            [
                {"type": "websocket.receive", "bytes": b"sample"},
                {"type": "websocket.receive", "text": '{"type":"end"}'},
            ],
        )
        asyncio.run(handle_websocket(websocket))
        return websocket.events

    def test_yaml_settings_are_loaded(self):
        self.assertEqual(settings.llm_provider, "openai")
        self.assertEqual(settings.tts_voice, "F1")
        self.assertEqual(settings.stt_provider, "faster-whisper")
        self.assertEqual(settings.faster_whisper_model, "base")
        self.assertEqual(settings.faster_whisper_device, "cpu")
        self.assertEqual(settings.faster_whisper_compute_type, "int8")
        self.assertEqual(settings.max_speech_segment_words, 8)
        self.assertEqual(settings.tts_max_concurrent_segments, 2)
        self.assertEqual(settings.tts_supertonic_steps, 2)
        self.assertEqual(len(settings.system_prompt.splitlines()), 25)

    def test_speech_segments_split_on_sentence_periods(self):
        segments, remainder = _split_ready_segments(
            "This is a short clause. This is another. Finally this ends.",
            force=True,
        )
        self.assertEqual(
            segments,
            ["This is a short clause.", "This is another.", "Finally this ends."],
        )
        self.assertEqual(remainder, "")

    def test_decimal_period_does_not_end_a_speech_segment(self):
        text = "I am Qwen3.5, a large language model. My name is Li Yangzhong."
        segments, remainder = _split_ready_segments(text)
        self.assertEqual(segments, ["I am Qwen3.5, a large language model."])
        self.assertEqual(remainder, "My name is Li Yangzhong.")

    def test_speech_segments_bound_unpunctuated_text_and_flush_final_text(self):
        unpunctuated = "word " * 41
        segments, remainder = _split_ready_segments(unpunctuated)
        self.assertTrue(segments)
        self.assertLessEqual(
            max(len(segment.split()) for segment in segments),
            settings.max_speech_segment_words,
        )
        self.assertLessEqual(max(map(len, segments)), 120)
        self.assertTrue(remainder)
        tail = remainder
        final, remainder = _split_ready_segments(tail, force=True)
        self.assertEqual(final, [tail.strip()])
        self.assertEqual(remainder, "")

    def test_openai_compatible_stream_and_health_check(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": settings.llm_model}]})
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text='data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
                "data: [DONE]\n\n",
            )

        real_async_client = httpx.AsyncClient

        def mock_async_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        async def exercise_provider():
            provider = OpenAIProvider(
                settings.llm_url,
                settings.llm_model,
                api_key=settings.llm_api_key,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout=settings.timeout,
                extra_body={"top_k": 20},
            )
            self.assertTrue(await provider.health_check())
            return [part async for part in provider.stream_chat([{"role": "user", "content": "Hi"}])]

        with patch("llm.openai.httpx.AsyncClient", side_effect=mock_async_client):
            parts = asyncio.run(exercise_provider())

        self.assertEqual(parts, ["Hello", " there"])
        completion_request = next(request for request in requests if request.url.path.endswith("/chat/completions"))
        payload = json.loads(completion_request.content)
        self.assertEqual(payload["model"], settings.llm_model)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["top_k"], 20)

    def test_websocket_history_isolation_and_prompt(self):
        first = self.run_audio_turn("a")
        second = self.run_audio_turn("a")
        third = self.run_audio_turn("b")

        self.assertIn("transcript", [event["type"] for event in first])
        self.assertIn("llm_delta", [event["type"] for event in first])
        self.assertIn("speech", [event["type"] for event in first])
        self.assertIn("metrics", [event["type"] for event in first])
        self.assertEqual(len(self.llm.calls[0]), 2)
        self.assertEqual(len(self.llm.calls[1]), 4)
        self.assertEqual(len(self.llm.calls[2]), 2)
        self.assertEqual(self.llm.calls[0][0]["content"], settings.system_prompt)
        self.assertEqual(first[-1]["text"], "Hello from provider.")
        self.assertEqual(second[-1]["text"], "Hello from provider.")
        self.assertEqual(third[-1]["text"], "Hello from provider.")

    def test_failed_llm_turn_does_not_commit_history(self):
        self.llm.fail = True
        websocket = FakeWebSocket(
            self.app,
            {"type": "start", "session_id": "a", "mime_type": "audio/webm"},
            [
                {"type": "websocket.receive", "bytes": b"sample"},
                {"type": "websocket.receive", "text": '{"type":"end"}'},
            ],
        )
        with self.assertLogs("pipeline", level="ERROR"):
            asyncio.run(handle_websocket(websocket))
        events = websocket.events
        self.assertTrue(any(event["type"] == "error" for event in events))

        self.llm.fail = False
        self.run_audio_turn("a")
        self.assertEqual(len(self.llm.calls[-1]), 2)

    def test_typed_speech_uses_same_websocket(self):
        websocket = FakeWebSocket(self.app, {"type": "start", "mode": "speak", "text": "Say this."})
        asyncio.run(handle_websocket(websocket))
        events = websocket.events
        speech = next(event for event in events if event["type"] == "speech")
        self.assertEqual(speech["text"], "Say this.")
        self.assertEqual(self.llm.calls, [])

    def test_tts_segments_run_concurrently_and_send_in_order(self):
        tts = ConcurrentFakeTTS()
        llm = FakeLLM()

        async def stream_chat(messages) -> AsyncIterator[str]:
            llm.calls.append(messages)
            yield "First sentence. Second sentence. Third sentence."

        llm.stream_chat = stream_chat
        providers = SimpleNamespace(llm=llm, tts=tts, stt=FakeSTT())
        app = create_app(providers)
        app.state.providers = providers
        app.state.conversations = OrderedDict()
        websocket = FakeWebSocket(
            app,
            {"type": "start", "session_id": "parallel", "text": "Speak this."},
        )

        asyncio.run(handle_websocket(websocket))

        speech_events = [event for event in websocket.events if event["type"] == "speech"]
        self.assertGreaterEqual(tts.max_active, 2)
        self.assertEqual([event["index"] for event in speech_events], [0, 1, 2])

    def test_health_and_timing_contract(self):
        self.assertTrue(any(getattr(route, "path", "") == "/health" for route in self.app.routes))
        self.assertTrue(any(getattr(route, "path", "") == "/ws" for route in self.app.routes))
        self.assertEqual(
            validate_timestamps([{"word": "hello", "start_time": 0.2, "end_time": 0.5}]),
            (["hello"], [200.0], [300.0]),
        )

    def test_measured_first_speech_latency_is_under_threshold(self):
        events = self.run_audio_turn("latency_test")
        metrics_event = next(e for e in events if e.get("type") == "metrics")
        metrics = metrics_event["metrics"]
        self.assertIsNotNone(metrics.get("first_speech_end_to_end_ms"))
        self.assertLess(metrics["first_speech_end_to_end_ms"], 3200.0)
        self.assertEqual(metrics["status"], "ok")


if __name__ == "__main__":
    unittest.main()

