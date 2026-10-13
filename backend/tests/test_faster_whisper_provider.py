import asyncio
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stt.faster_whisper import FasterWhisperProvider


class FakeFasterWhisperModel:
    def transcribe(self, waveform, **options):
        self.waveform = waveform
        self.options = options
        return iter(
            [SimpleNamespace(text=" hello "), SimpleNamespace(text="world")]
        ), SimpleNamespace(duration=1.25, language="en")


class FasterWhisperProviderTests(unittest.TestCase):
    def test_provider_matches_shared_stt_input_and_output_contract(self):
        model = FakeFasterWhisperModel()
        waveform = object()
        with (
            patch("stt.faster_whisper._load_model", return_value=model),
            patch("stt.faster_whisper._decode_audio", return_value=waveform),
        ):
            result = asyncio.run(
                FasterWhisperProvider().transcribe(b"audio-bytes", "audio/webm")
            )

        self.assertEqual(
            result,
            {"text": "hello world", "language": "en", "duration": 1.25},
        )
        self.assertIs(model.waveform, waveform)
        self.assertEqual(model.options["beam_size"], 5)
        self.assertTrue(model.options["vad_filter"])


if __name__ == "__main__":
    unittest.main()
