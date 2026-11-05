"""Load backend settings from config.yaml."""

from pathlib import Path
from types import SimpleNamespace
import os
import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

CONFIG_FILE = Path(__file__).with_name("config.yaml")

with CONFIG_FILE.open(encoding="utf-8") as config_file:
    config = yaml.safe_load(config_file)

config["llm_api_key"] = os.environ.get("LLM_API_KEY", "not-needed")
config["tts_api_key"] = os.environ.get("TTS_API_KEY", "")

DEFAULTS = {
    "nemotron_url": "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime",
    "nemotron_model": "nemotron-3.5",
    "nemotron_language": "en-US",
    "nemotron_api_key": "",
    "nemotron_timeout": 120,
    "diagnostics_enabled": False,
    "faster_whisper_model": "base",
    "faster_whisper_device": "cpu",
    "faster_whisper_compute_type": "int8",
    "faster_whisper_download_root": ".cache/faster-whisper",
    "min_speech_segment_words": 8,
    "max_speech_segment_words": 8,
    "tts_device": "auto",
    "tts_model": "kokoro",
    "tts_api_key": "",
    "tts_max_concurrent_segments": 2,
    "tts_language": "en",
    "tts_supertonic_steps": 2,
}
config = {**DEFAULTS, **(config or {})}

for name in ("min_speech_segment_words", "max_speech_segment_words", "tts_max_concurrent_segments"):
    value = config[name]
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
if config["max_speech_segment_words"] < config["min_speech_segment_words"]:
    raise ValueError("max_speech_segment_words must be at least min_speech_segment_words.")

config["system_prompt"] = (
    (CONFIG_FILE.parent / config.pop("system_prompt_file", "llm/prompts/system.txt"))
    .read_text(encoding="utf-8")
    .strip()
)


def _namespace(value):
    """Expose nested provider configuration without a second config loader."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    return value


settings = SimpleNamespace(
    **{
        **config,
        "cv": _namespace(config["cv"]),
        "model_workers": _namespace(config.get("model_workers", {})),
    }
)
