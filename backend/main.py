"""Application entry point. Run `uv run main.py` from this directory."""

import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from llm.openai import OpenAIProvider
from pipeline import handle_websocket
from stt.faster_whisper import FasterWhisperProvider
from tts.kokoro import KokoroProvider
from tts.supertonic import SupertonicProvider

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
        )
        tts = {
            "kokoro": KokoroProvider,
            "supertonic": SupertonicProvider,
        }[settings.tts_provider]()
        stt = {"faster-whisper": FasterWhisperProvider}[settings.stt_provider]()
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {exc.args[0]}") from exc
    return SimpleNamespace(llm=llm, tts=tts, stt=stt)


def create_app(providers=None):
    @asynccontextmanager
    async def lifespan(application):
        application.state.providers = providers or build_providers()
        application.state.conversations = OrderedDict()
        for provider_name in ("stt", "tts"):
            provider = getattr(application.state.providers, provider_name)
            initialize = getattr(provider, "initialize", None)
            if initialize is not None:
                logger.info("Loading selected %s provider model...", provider_name)
                await initialize()
        logger.info(
            "Models ready and in use: LLM=%s/%s STT=%s/%s TTS=%s/%s voice=%s",
            settings.llm_provider,
            settings.llm_model,
            settings.stt_provider,
            settings.faster_whisper_model,
            settings.tts_provider,
            "kokoro" if settings.tts_provider == "kokoro" else "supertonic-3",
            settings.tts_voice,
        )
        yield
        application.state.conversations.clear()

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
        return {"status": "ok", "transport": "websocket", "endpoint": "/ws"}

    @application.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await handle_websocket(websocket)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
