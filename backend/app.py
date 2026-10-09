import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from time import perf_counter
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from backend.api.routes import router
from backend.config import settings
from backend.providers import build_providers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app(providers=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.providers = providers or build_providers()
        app.state.conversations = OrderedDict()
        logger.info("Providers: LLM=%s model=%s TTS=%s STT=%s; system_prompt_lines=%d context=%d", settings.llm_provider, settings.llm_model, settings.tts_provider, settings.stt_provider, len(settings.system_prompt.splitlines()), settings.context_length)
        yield
        app.state.conversations.clear()

    app = FastAPI(title="Virtual Human HTTP Server", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://[^/]+$", allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])
    app.include_router(router)

    @app.middleware("http")
    async def latency(request: Request, call_next):
        stage = {"/chat": "LLM", "/synthesize": "TTS", "/transcribe": "STT"}.get(request.url.path)
        started, code = perf_counter(), 500
        try:
            response = await call_next(request)
            code = response.status_code
            return response
        finally:
            if stage and request.method == "POST":
                logger.info("Latency %s: status=%s elapsed_ms=%.2f", stage, code, (perf_counter()-started)*1000)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, exc):
        logger.warning("Provider request failed: %s", exc)
        code = 504 if isinstance(exc, httpx.TimeoutException) else 503 if isinstance(exc, httpx.ConnectError) else 502
        return JSONResponse(status_code=code, content={"detail": "Provider request failed. Check backend logs and service availability."})

    @app.exception_handler(ValueError)
    async def invalid_provider_response(request, exc):
        logger.exception("Invalid provider response")
        return JSONResponse(status_code=502, content={"detail": "Provider returned an invalid response."})
    return app

app = create_app()
