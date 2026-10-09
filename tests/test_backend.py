import unittest
from types import SimpleNamespace
import httpx
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import settings
from backend.schemas import SynthesizeResponse
from backend.tts.kokoro import validate_timestamps

class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail = False
    async def health_check(self):
        return True
    async def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise httpx.ConnectError("offline")
        return "Hello from the provider."

class FakeTTS:
    async def synthesize(self, request):
        return SynthesizeResponse(audio_base64="YQ==", audio_encoding="wav", words=[request.text], wtimes=[0], wdurations=[100])

class FakeSTT:
    async def transcribe(self, body, content_type):
        assert b"sample" in body and "boundary=" in content_type
        return {"text": "Hello", "language": "en", "duration": 1.0}

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM()
        self.client = TestClient(create_app(SimpleNamespace(llm=self.llm, tts=FakeTTS(), stt=FakeSTT())))
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None, None, None)
    def chat(self, session, **kwargs):
        return self.client.post("/chat", json={"message": "hello", "session_id": session, **kwargs})
    def test_history_isolation_reset_and_prompt(self):
        self.assertEqual(self.chat("a").json()["history_length"], 2)
        self.assertEqual(self.chat("a").json()["history_length"], 4)
        self.assertEqual(self.chat("b").json()["history_length"], 2)
        self.assertEqual(len(self.llm.calls[1]), 4)
        self.assertEqual(len(self.llm.calls[2]), 2)
        self.assertEqual(len(self.llm.calls[0][0]["content"].splitlines()), 300)
        self.assertEqual(self.chat("a", reset_history=True).json()["history_length"], 2)
    def test_provider_failure_does_not_commit_history(self):
        self.llm.fail = True
        self.assertEqual(self.chat("a").status_code, 503)
        self.llm.fail = False
        self.assertEqual(self.chat("a").json()["history_length"], 2)
    def test_speech_contract(self):
        data = self.client.post("/synthesize", json={"text": "hello"}).json()
        self.assertEqual(data["audio_encoding"], "wav")
        self.assertEqual(len(data["words"]), len(data["wtimes"]))
        self.assertEqual(validate_timestamps([{"word": "hello", "start_time": 0.2, "end_time": 0.5}]), (["hello"], [200.0], [300.0]))
    def test_transcription_gateway_and_validation(self):
        result = self.client.post("/transcribe", files={"audio": ("sample.wav", b"sample", "audio/wav")})
        self.assertEqual(result.json()["text"], "Hello")
        self.assertEqual(self.client.post("/transcribe", content=b"bad").status_code, 415)
        self.assertEqual(self.chat("a", message=" ").status_code, 400)
    def test_http_only_health(self):
        self.assertEqual(self.client.get("/health").json()["transport"], "http")
        self.assertTrue(self.client.get("/llm/health").json()["available"])
        self.assertFalse(any(getattr(route, "path", "") == "/ws" for route in self.client.app.routes))

if __name__ == "__main__":
    unittest.main()
