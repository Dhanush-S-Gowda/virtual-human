import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROMPT = Path(__file__).parent / "llm" / "prompts" / "system.txt"

@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")
    tts_provider: str = os.getenv("TTS_PROVIDER", "kokoro")
    stt_provider: str = os.getenv("STT_PROVIDER", "whisper-http")
    llm_url: str = os.getenv("LLM_BASE_URL", "http://ollama:11434")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.5:2b")
    kokoro_url: str = os.getenv("KOKORO_URL", "http://kokoro:8880")
    stt_url: str = os.getenv("STT_BASE_URL", "http://stt-server:8001")
    enabled: bool = os.getenv("LLM_ENABLED", "true").lower() == "true"
    timeout: float = float(os.getenv("LLM_TIMEOUT", "120"))
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "256"))
    context_length: int = int(os.getenv("LLM_CONTEXT_LENGTH", "8192"))
    history_turns: int = int(os.getenv("LLM_HISTORY_TURNS", "8"))
    system_prompt: str = os.getenv("LLM_SYSTEM_PROMPT") or Path(os.getenv("LLM_SYSTEM_PROMPT_FILE", str(DEFAULT_PROMPT))).read_text(encoding="utf-8").strip()

settings = Settings()
