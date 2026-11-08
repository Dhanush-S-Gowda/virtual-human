"""Load backend settings from config.yaml."""

from pathlib import Path
from types import SimpleNamespace
import os
from math import isfinite
import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

CONFIG_FILE = Path(__file__).with_name("config.yaml")

with CONFIG_FILE.open(encoding="utf-8") as config_file:
    config = yaml.safe_load(config_file)

config["llm_api_key"] = os.environ.get("LLM_API_KEY", "not-needed")
config["tts_url"] = os.environ.get("TTS_URL", config.get("tts_url", "http://localhost:8880"))
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
    "first_speech_segment_words": 0,
    "kokoro_warmup_on_recording": False,
    "kokoro_warmup_idle_seconds": 15.0,
    "kokoro_warmup_timeout_seconds": 2.0,
    "tts_device": "auto",
    "tts_speed_min": 0.75,
    "tts_speed_max": 1.5,
    "tts_model": "kokoro",
    "tts_api_key": "",
    "tts_max_concurrent_segments": 2,
    "tts_language": "en",
    "tts_supertonic_steps": 2,
    "tts_volume_gain": 1.5,
    "gesture_min_duration_seconds": 2.0,
    "gesture_pacing": "speech",
    "cv_world_history_seconds": 10.0,
    "cv_world_history_windows": 0,
}
config = {**DEFAULTS, **(config or {})}

memory_defaults = {
    "enabled": True, "database": "data/conversation-memory.sqlite3", "max_turns": 2000,
    "semantic_enabled": True, "embedding_url": "http://127.0.0.1:11434",
    "embedding_model": "all-minilm", "embedding_timeout": 1.5,
    "top_k": 4, "candidate_count": 30, "context_chars": 6000,
}
if not isinstance(config.get("memory", {}), dict):
    raise ValueError("memory must be a configuration mapping.")
config["memory"] = {**memory_defaults, **config.get("memory", {})}
for name in ("enabled", "semantic_enabled"):
    if type(config["memory"][name]) is not bool:
        raise ValueError(f"memory.{name} must be a boolean.")
for name, maximum in (("max_turns", 10000), ("top_k", 20), ("candidate_count", 200), ("context_chars", 16000)):
    value = config["memory"][name]
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"memory.{name} must be an integer between 1 and {maximum}.")
if config["memory"]["top_k"] > config["memory"]["candidate_count"] or config["memory"]["context_chars"] < 1024:
    raise ValueError("Memory candidates must cover top_k; context_chars must be at least 1024.")
memory_timeout = config["memory"]["embedding_timeout"]
if type(memory_timeout) not in (int, float) or not isfinite(memory_timeout) or not .1 <= memory_timeout <= 3:
    raise ValueError("memory.embedding_timeout must be between 0.1 and 3 seconds.")
for name in ("database", "embedding_url", "embedding_model"):
    if not isinstance(config["memory"][name], str) or not config["memory"][name].strip():
        raise ValueError(f"memory.{name} must be a nonempty string.")

if config["gesture_pacing"] not in {"speech", "complete"}:
    raise ValueError("gesture_pacing must be speech or complete.")

for name in ("tts_speed", "tts_speed_min", "tts_speed_max"):
    value = config[name]
    if type(value) not in (int, float) or not isfinite(value) or not 0.25 <= value <= 4.0:
        raise ValueError(f"{name} must be finite and between 0.25 and 4.0.")
if not config["tts_speed_min"] <= config["tts_speed"] <= config["tts_speed_max"]:
    raise ValueError("tts_speed_min <= tts_speed <= tts_speed_max is required.")

history_seconds = config["cv_world_history_seconds"]
history_windows = config["cv_world_history_windows"]
if type(history_seconds) not in (int, float) or not isfinite(history_seconds) or history_seconds < 0:
    raise ValueError("cv_world_history_seconds must be finite and nonnegative.")
if type(history_windows) is not int or history_windows < 0:
    raise ValueError("cv_world_history_windows must be a nonnegative integer.")
if history_windows > config["cv_retained_windows"]:
    raise ValueError("Increase cv_retained_windows to cover cv_world_history_windows.")
if not history_windows and history_seconds > (config["cv_retained_windows"] - 1) * config["cv_window_seconds"]:
    raise ValueError("Increase cv_retained_windows to cover cv_world_history_seconds plus the open window.")

for name in ("tts_volume_gain", "gesture_min_duration_seconds"):
    value = config[name]
    if (type(value) not in (int, float) or not isfinite(value)
            or value < 0 or (name == "gesture_min_duration_seconds" and value == 0)):
        raise ValueError(f"{name} must be a finite {'positive' if name == 'gesture_min_duration_seconds' else 'nonnegative'} number.")

for name in ("min_speech_segment_words", "max_speech_segment_words", "tts_max_concurrent_segments"):
    value = config[name]
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
if config["max_speech_segment_words"] < config["min_speech_segment_words"]:
    raise ValueError("max_speech_segment_words must be at least min_speech_segment_words.")
if type(config["first_speech_segment_words"]) is not int or config["first_speech_segment_words"] < 0:
    raise ValueError("first_speech_segment_words must be a nonnegative integer.")
if type(config["kokoro_warmup_on_recording"]) is not bool:
    raise ValueError("kokoro_warmup_on_recording must be a boolean.")
for name in ("kokoro_warmup_idle_seconds", "kokoro_warmup_timeout_seconds"):
    value = config[name]
    if type(value) not in (int, float) or not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number.")

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
        "memory": _namespace(config["memory"]),
        "model_workers": _namespace(config.get("model_workers", {})),
    }
)
