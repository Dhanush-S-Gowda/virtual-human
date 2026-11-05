import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from schemas import SynthesizeResponse
from tts.supertonic import SupertonicProvider, _approximate_word_timings, _resolve_style


class SupertonicProviderTests(unittest.TestCase):
    def test_frontend_voice_styles_resolve_without_yaml_selection(self):
        self.assertEqual(_resolve_style("F1").name, "F1.json")
        self.assertEqual(_resolve_style("M1").name, "M1.json")
        with self.assertRaises(ValueError):
            _resolve_style("af_heart")

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
                result = await SupertonicProvider().synthesize_segment(" Hello ", "M1", 1.1)
                synthesize.assert_called_once_with("Hello", "M1", 1.1)
                return result

        self.assertEqual(asyncio.run(call_provider()), expected)


if __name__ == "__main__":
    unittest.main()
