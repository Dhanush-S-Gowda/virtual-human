"""Native SSE tool fragments and server timing must not become spoken text."""

import json
from unittest.mock import patch

import httpx
import pytest

from backend.tests.test_backend import FakeLLM  # Adds the backend import path used by the application.
from llm.openai import OpenAIProvider
from llm.tools import TOOLS


@pytest.mark.asyncio
async def test_stream_assembles_native_tool_arguments_and_reads_terminal_usage():
    requests = []
    events = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {
            "name": "gesture", "arguments": '{"act'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
            "arguments": 'ion":"wave"}'}}]}}]},
        {"choices": [], "usage": {"completion_tokens": 12, "prompt_tokens": 100},
         "timings": {"prompt_ms": 300, "predicted_ms": 2000, "predicted_per_second": 5.5}},
    ]

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
                              + "data: [DONE]\n\n")

    original = httpx.AsyncClient
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler))):
        provider = OpenAIProvider("http://test.invalid/v1", "test")
        output = [event async for event in provider.stream_events([{"role": "user", "content": "Wave"}], TOOLS)]
    assert requests[0]["tools"] == TOOLS
    assert requests[0]["stream_options"]["include_usage"]
    assert output[0] == {"type": "tool_call", "id": "call-1", "name": "gesture",
                         "arguments": '{"action":"wave"}'}
    assert output[1]["usage"]["completion_tokens"] == 12
    assert output[1]["timings"]["predicted_per_second"] == 5.5
    assert not any(event["type"] == "text" for event in output)
