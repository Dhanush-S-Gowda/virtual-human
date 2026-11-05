"""Application entry point. Run `uv run main.py` from this directory."""

import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from llm.openai import OpenAIProvider
from pipeline import ConversationManager, handle_websocket
from stt.faster_whisper import FasterWhisperProvider
from stt.nemotron import NemotronProvider
from tts.kokoro import KokoroProvider
from tts.supertonic import SupertonicProvider
from tts.voices import VOICES, DEFAULT_VOICES
from provider_process import ProviderProcess
from observability import ResourceReporter, setup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_providers():
    """Construct configured providers when the application starts."""
    try:
        llm = OpenAIProvider(
            settings.llm_url,
            settings.llm_model,
            api_key=settings.llm_api_key,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
            extra_body=settings.llm_extra_body,
            tool_mode=getattr(settings, "llm_tool_mode", "native"),
            plan_temperature=getattr(settings, "llm_plan_temperature", 0.2),
        )
        tts = {
            "kokoro": KokoroProvider,
            "supertonic": SupertonicProvider,
        }[settings.tts_provider]()
        stt = {"faster-whisper": FasterWhisperProvider,
               "nemotron": NemotronProvider}[settings.stt_provider]()
        if settings.tts_provider == "supertonic":
            tts = ProviderProcess("tts.supertonic", "SupertonicProvider", role="tts.supertonic")
        if settings.stt_provider == "faster-whisper":
            stt = ProviderProcess("stt.faster_whisper", "FasterWhisperProvider", role="stt.faster_whisper")
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {exc.args[0]}") from exc
    return SimpleNamespace(llm=llm, tts=tts, stt=stt)


def create_app(providers=None):
    @asynccontextmanager
    async def lifespan(application):
        setup("conversation_api")
        application.state.providers = providers or build_providers()
        resources = ResourceReporter("conversation_api")
        application.state.conversations = OrderedDict()
        application.state.conversation_manager = ConversationManager(
            application.state.providers.llm, application.state.conversations)
        try:
            for provider_name in ("stt", "tts"):
                provider = getattr(application.state.providers, provider_name)
                initialize = getattr(provider, "initialize", None)
                if initialize is not None:
                    logger.info("Loading selected %s provider model...", provider_name)
                    await initialize()
            logger.info(
                "Models ready and in use: LLM=%s/%s STT=%s/%s TTS=%s/%s",
                settings.llm_provider,
                settings.llm_model,
                settings.stt_provider,
                settings.nemotron_model if settings.stt_provider == "nemotron" else settings.faster_whisper_model,
                settings.tts_provider,
                settings.tts_model if settings.tts_provider == "kokoro" else "supertonic-3",
            )
            yield
        finally:
            resources.close()
            application.state.conversations.clear()
            for provider_name in ("tts", "stt"):
                close = getattr(getattr(application.state.providers, provider_name), "close", None)
                if close:
                    await close()

    application = FastAPI(
        title="Virtual Human Conversation Backend",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://[^/]+$",
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.get("/health")
    async def health():
        return {"status": "ok", "transport": "websocket", "endpoint": "/ws",
                "providers": {"stt": settings.stt_provider,
                              "stt_model": settings.nemotron_model if settings.stt_provider == "nemotron"
                              else settings.faster_whisper_model,
                              "llm": settings.llm_model, "tts": settings.tts_provider,
                              "tts_model": settings.tts_model if settings.tts_provider == "kokoro" else "supertonic-3",
                              "tts_voices": list(VOICES[settings.tts_provider]),
                              "tts_default_voice": DEFAULT_VOICES[settings.tts_provider]}}

    @application.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await handle_websocket(websocket)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
