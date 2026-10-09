import logging
from typing import Any
import httpx
from backend.config import settings
logger = logging.getLogger(__name__)
LLM_BASE_URL = settings.llm_url
LLM_MODEL = settings.llm_model
LLM_TIMEOUT = settings.timeout
LLM_TEMPERATURE = settings.temperature
LLM_MAX_TOKENS = settings.max_tokens

class OllamaProvider:
    """Thin async wrapper around the Ollama chat API."""

    def __init__(self) -> None:
        self._base_url = LLM_BASE_URL.rstrip("/")
        self._timeout = httpx.Timeout(
            connect=10.0,
            read=LLM_TIMEOUT,
            write=30.0,
            pool=5.0,
        )

    async def health_check(self) -> bool:
        """Return True if Ollama has the configured model installed."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(f"{self._base_url}/api/tags")
                r.raise_for_status()
                return any(model.get("name") == LLM_MODEL for model in r.json().get("models", []))
        except Exception:
            return False

    async def chat(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Send a list of messages and return the assistant reply text."""
        payload: dict[str, Any] = {
            "model": LLM_MODEL,
            "messages": messages,
            "options": {
                "temperature": LLM_TEMPERATURE,
                "num_predict": LLM_MAX_TOKENS,
                "num_ctx": settings.context_length,
            },
            "think": False,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        logger.info(
            "Ollama latency: total_ms=%.2f load_ms=%.2f prompt_ms=%.2f generation_ms=%.2f tokens=%s",
            data.get("total_duration", 0) / 1_000_000,
            data.get("load_duration", 0) / 1_000_000,
            data.get("prompt_eval_duration", 0) / 1_000_000,
            data.get("eval_duration", 0) / 1_000_000,
            data.get("eval_count", 0),
        )
        try:
            reply = data["message"]["content"].strip()
            if not reply:
                raise ValueError("Ollama returned an empty reply")
            return reply
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Unexpected response format from Ollama") from exc


