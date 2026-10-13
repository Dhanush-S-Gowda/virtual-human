"""Load backend settings from config.yaml."""

from pathlib import Path
from types import SimpleNamespace

import yaml

CONFIG_FILE = Path(__file__).with_name("config.yaml")

with CONFIG_FILE.open(encoding="utf-8") as config_file:
    config = yaml.safe_load(config_file)

DEFAULTS = {
    "faster_whisper_model": "base",
    "faster_whisper_device": "cpu",
    "faster_whisper_compute_type": "int8",
    "faster_whisper_download_root": ".cache/faster-whisper",
    "max_speech_segment_words": 10,
    "tts_device": "auto",
    "tts_max_concurrent_segments": 2,
    "tts_language": "en",
    "tts_supertonic_steps": 8,
    "tts_supertonic_voice_style": "supertonic/assets/voice_styles/F1.json",
}
config = {**DEFAULTS, **(config or {})}

config["system_prompt"] = (
    (CONFIG_FILE.parent / config.pop("system_prompt_file", "llm/prompts/system.txt"))
    .read_text(encoding="utf-8")
    .strip()
)

settings = SimpleNamespace(**config)
