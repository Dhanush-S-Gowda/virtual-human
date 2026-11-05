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

config["llm_api_key"] = os.environ.get("llm_api_key")

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
    "max_speech_segment_words": 8,
    "tts_device": "auto",
    "tts_max_concurrent_segments": 2,
    "tts_language": "en",
    "tts_supertonic_steps": 2,
}
config = {**DEFAULTS, **(config or {})}

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
