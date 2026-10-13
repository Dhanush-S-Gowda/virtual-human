import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from schemas import SynthesizeResponse
from tts.supertonic import SupertonicProvider, _approximate_word_timings, _resolve_style


class SupertonicProviderTests(unittest.TestCase):
    def test_kokoro_voice_uses_configured_f1_style_and_style_names_resolve(self):
        configured_style = _resolve_style("af_bella")
        named_style = _resolve_style("F1")
        self.assertEqual(configured_style.name, "F1.json")
        self.assertEqual(named_style, configured_style)

    def test_word_timings_cover_duration_and_match_word_count(self):
        words, starts, durations = _approximate_word_timings("Hello, talking head!", 2.0)
        self.assertEqual(words, ["Hello,", "talking", "head!"])
        self.assertEqual(len(starts), len(words))
        self.assertEqual(len(durations), len(words))
        self.assertEqual(starts[0], 0.0)
        self.assertAlmostEqual(sum(durations), 2000.0)

    def test_provider_preserves_existing_synthesis_call_contract(self):
        expected = SynthesizeResponse(
            audio_base64="YQ==",
            audio_encoding="wav",
            words=["Hello"],
            wtimes=[0],
            wdurations=[100],
            sample_rate=44100,
        )

        async def call_provider():
            with patch("tts.supertonic._synthesize", return_value=expected) as synthesize:
                result = await SupertonicProvider().synthesize_segment(" Hello ", "af_bella", 1.1)
                synthesize.assert_called_once_with("Hello", "af_bella", 1.1)
                return result

        self.assertEqual(asyncio.run(call_provider()), expected)


if __name__ == "__main__":
    unittest.main()
