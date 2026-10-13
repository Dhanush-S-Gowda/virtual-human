"""Streaming client for OpenAI-compatible chat completion APIs."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class OpenAIProvider:
    """Call a chat completions API using its base URL and model name."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str = "not-needed",
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._extra_body = extra_body or {}
        self._timeout = httpx.Timeout(
            connect=10.0,
            read=timeout,
            write=30.0,
            pool=5.0,
        )

    def _headers(self, *, include_events: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if include_events and self._extra_body.get("enable_tools"):
            headers["X-Unsloth-Events"] = "1"
        return headers

    async def health_check(self) -> bool:
        """Return whether the configured model is listed by the API."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    headers=self._headers(),
                )
                response.raise_for_status()
                models = response.json().get("data", [])
                return any(item.get("id") == self._model for item in models)
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Yield content deltas from a server-sent event response."""
        payload: dict[str, Any] = {
            **self._extra_body,
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(include_events=True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_line = line[5:].strip()
                    if data_line == "[DONE]":
                        break
                    try:
                        data = json.loads(data_line)
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        content = choices[0].get("delta", {}).get("content", "")
                    except (
                        json.JSONDecodeError,
                        AttributeError,
                        TypeError,
                        IndexError,
                    ) as exc:
                        raise ValueError(
                            "Unexpected response format from OpenAI-compatible API"
                        ) from exc
                    if isinstance(content, str) and content:
                        yield content
