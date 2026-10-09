from dataclasses import dataclass
from backend.config import settings
from backend.llm.base import LLMProvider
from backend.llm.ollama import OllamaProvider
from backend.tts.base import TTSProvider
from backend.tts.kokoro import KokoroProvider
from backend.stt.base import STTProvider
from backend.stt.whisper_http import WhisperHTTPProvider

LLM_PROVIDERS = {"ollama": OllamaProvider}
TTS_PROVIDERS = {"kokoro": KokoroProvider}
STT_PROVIDERS = {"whisper-http": WhisperHTTPProvider}

@dataclass
class Providers:
    llm: LLMProvider
    tts: TTSProvider
    stt: STTProvider

def build_providers() -> Providers:
    try:
        return Providers(LLM_PROVIDERS[settings.llm_provider](), TTS_PROVIDERS[settings.tts_provider](), STT_PROVIDERS[settings.stt_provider]())
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {exc.args[0]}") from exc
