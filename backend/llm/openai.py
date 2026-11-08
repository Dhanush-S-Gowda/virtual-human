"""Streaming client for OpenAI-compatible chat completion APIs."""

import json
import uuid
from time import perf_counter
from collections.abc import AsyncIterator
from typing import Any

import httpx

from diagnostics import record, stage
from .tools import ACTION_EXAMPLES, ACTION_PLAN_SCHEMA, validate_action_plan


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
        tool_mode: str = "native",
        plan_temperature: float = 0.5,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._extra_body = extra_body or {}
        if tool_mode not in {"native", "structured"}:
            raise ValueError("LLM tool mode must be native or structured")
        self.tool_mode = tool_mode
        self._plan_temperature = plan_temperature
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
        """Compatibility text stream for callers that do not execute tools."""
        async for event in self.stream_events(messages):
            if event["type"] == "text":
                yield event["text"]

    async def stream_events(self, messages, tools=None):
        """Keep tool calls and server usage separate from text sent to speech."""
        if tools and self.tool_mode == "structured":
            async for event in self._stream_action_plan(messages):
                yield event
            return
        payload: dict[str, Any] = {
            **self._extra_body,
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)

        request_id = str(uuid.uuid4())
        response_parts = []
        tool_calls = {}
        usage, timings = {}, {}
        started = perf_counter()
        first_token = last_token = None
        record("llm", "request", request_id=request_id, payload=payload)
        status = "ok"
        try:
            with stage("llm.http_stream", request_id=request_id):
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
                                if data.get("error"):
                                    raise ValueError(f"LLM server error: {data['error']}")
                                if isinstance(data.get("usage"), dict):
                                    usage.update(data["usage"])
                                if isinstance(data.get("timings"), dict):
                                    timings.update(data["timings"])
                                choices = data.get("choices", [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                for call in delta.get("tool_calls", ()):
                                    index = call.get("index", 0)
                                    if type(index) is not int or not 0 <= index < 8:
                                        raise ValueError("Invalid or excessive LLM tool calls.")
                                    target = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                    target["id"] = call.get("id") or target["id"]
                                    function = call.get("function", {})
                                    target["name"] += function.get("name") or ""
                                    target["arguments"] += function.get("arguments") or ""
                                    if len(target["arguments"]) > 4096:
                                        raise ValueError("LLM tool arguments exceed the size limit.")
                                if content or delta.get("tool_calls"):
                                    last_token = perf_counter()
                                    first_token = first_token or last_token
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
                                response_parts.append(content)
                                yield {"type": "text", "text": content}
            for index in sorted(tool_calls):
                call = tool_calls[index]
                yield {"type": "tool_call", **call, "id": call["id"] or f"call_{uuid.uuid4().hex}"}
            yield {"type": "usage", "usage": usage, "timings": timings,
                   "request_ms": (perf_counter() - started) * 1000,
                   "ttft_ms": (first_token - started) * 1000 if first_token else None,
                   "decode_ms": (last_token - first_token) * 1000 if first_token else 0}
        except BaseException:
            status = "error_or_cancelled"
            raise
        finally:
            record("llm", "response", request_id=request_id, status=status,
                   content="".join(response_parts))

    async def _stream_action_plan(self, messages):
        # Buffer structured output: partial JSON must never become speech/actions.
        instruction = (
            "Return a JSON plan with reply_kind, actions and speech. "
            "Use speaker_identity with actions [] for speaker identification; otherwise use conversation. "
            "Put allowed gesture, emotion and speed tags directly in speech as instructed. actions is [] except when calling rename_person to remember a self-introduced or explicitly requested name. "
            "speech is the brief spoken reply with optional allowed inline tags, without tool names or metadata. "
            "Actions accompany speech; after a tool failure, correct it or explain simply."
        )
        plan_messages = [dict(message) for message in messages]
        plan_messages[0] = {**plan_messages[0], "content": plan_messages[0]["content"] + "\n" + instruction}
        plan_messages[0]["content"] += "\nThe next exchanges are format examples, not real conversation history."
        examples = []
        for request, kind, speech, actions in ACTION_EXAMPLES:
            examples.extend([
                {"role": "user", "content": request},
                {"role": "assistant", "content": json.dumps({
                    "reply_kind": kind, "actions": [
                        {"name": name, "arguments": {"action": action}} for name, action in actions
                    ], "speech": speech})},
            ])
        plan_messages[1:1] = examples
        # Reuse the same SSE decoder and telemetry; this client has no structured
        # branch recursion because its mode is native and no native tools are sent.
        client = OpenAIProvider(
            self._base_url, self._model, api_key=self._api_key,
            temperature=self._plan_temperature, max_tokens=self._max_tokens,
            timeout=self._timeout.read, extra_body={**self._extra_body,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "avatar_action_plan", "strict": True, "schema": ACTION_PLAN_SCHEMA}}})
        for attempt in range(2):
            chunks = []
            size = 0
            async for event in client.stream_events(plan_messages):
                if event["type"] == "text":
                    chunks.append(event["text"])
                    size += len(event["text"])
                    if size > 16000:
                        raise ValueError("Action plan exceeds size limit.")
                elif event["type"] == "usage":
                    yield event
            try:
                plan = validate_action_plan(json.loads("".join(chunks)))
            except ValueError:
                if attempt:
                    raise ValueError("LLM returned an invalid or private action plan.") from None
                plan_messages.append({"role": "system", "content":
                    "Your previous plan was invalid. Return only the schema, supported actions, and natural "
                    "spoken words without track identifiers, tool names, evidence labels or implementation details."})
                continue
            yield {"type": "action_plan", **plan}
            return
