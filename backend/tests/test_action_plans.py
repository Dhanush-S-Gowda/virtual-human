"""Structured plans never expose partial JSON or acknowledge failed actions."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.tests.test_backend import FakeLLM
from llm.openai import OpenAIProvider
from llm.tools import ACTION_EXAMPLES, TOOLS, action_example, validate_action_plan
from pipeline import Conversation, _is_current_speaker_question


@pytest.mark.asyncio
async def test_structured_stream_buffers_json_and_retries_private_speech():
    requests = []
    plans = iter([
        {"reply_kind": "conversation", "speech": "You are track 3.", "actions": []},
        {"reply_kind": "speaker_identity", "speech": "You're speaking, Dhanush.", "actions": []},
    ])

    def handler(request):
        requests.append(json.loads(request.content))
        encoded = json.dumps(next(plans))
        chunks = [encoded[:20], encoded[20:]]
        return httpx.Response(200, text="".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"
            for text in chunks) + "data: [DONE]\n\n")

    original = httpx.AsyncClient
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler))):
        provider = OpenAIProvider("http://test.invalid/v1", "test", tool_mode="structured")
        events = [event async for event in provider.stream_events(
            [{"role": "system", "content": "Be natural."}, {"role": "user", "content": "Who speaks?"}], TOOLS)]
    assert len(requests) == 2
    assert "tools" not in requests[0]
    assert requests[0]["response_format"]["json_schema"]["strict"]
    assert not any(event["type"] == "text" for event in events)
    assert events[-1] == {"type": "action_plan", "reply_kind": "speaker_identity",
                          "speech": "You're speaking, Dhanush.", "actions": []}


@pytest.mark.parametrize("plan", [
    {"speech": "Sure!", "actions": [{"name": "terminal", "arguments": {}}]},
    {"speech": "Sure!", "actions": [{"name": "gesture", "arguments": {"action": "dance"}}]},
    {"speech": "Sure!", "actions": [{"name": "rename_person", "arguments": {"track_id": True, "name": "Alex"}}]},
    {"speech": "Sure!", "actions": [], "extra": "field"},
    {"speech": "", "actions": []},
])
def test_unsafe_or_invalid_plans_are_rejected(plan):
    with pytest.raises(ValueError):
        validate_action_plan({"reply_kind": "conversation", **plan})


def test_examples_are_valid_plans_and_native_tools_have_matching_results():
    for _, kind, speech, actions in ACTION_EXAMPLES:
        validate_action_plan({"reply_kind": kind, "speech": speech, "actions": [
            {"name": name, "arguments": {"action": action}} for name, action in actions]})
    messages = action_example()
    calls = {call["id"]: call for message in messages for call in message.get("tool_calls", [])}
    results = {message["tool_call_id"] for message in messages if message["role"] == "tool"}
    assert set(calls) == results
    assert any("[clapping]" in (message.get("content") or "") for message in messages)
    assert not calls  # Body expression uses inline tags, not model tool calls.
    assert action_example() == messages
    assert action_example() is not messages


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["clapping", "enthusiasm", "thankful", "thinking"])
async def test_inline_gesture_speech_needs_no_tool(action):
    speech = f"[{action}]Congratulations!"
    llm = PlanLLM([{"speech": speech, "actions": []}])
    sink = AsyncMock()
    reply = "".join([part async for part in Conversation(llm).stream_reply("I passed!", on_action=sink)])
    assert reply == speech
    sink.assert_not_awaited()


class PlanLLM(FakeLLM):
    tool_mode = "structured"

    def __init__(self, plans):
        super().__init__()
        self.plans = iter(plans)

    async def stream_events(self, messages, tools):
        self.calls.append(list(messages))
        yield {"type": "action_plan", **next(self.plans)}


@pytest.mark.asyncio
async def test_model_chosen_action_dispatches_before_speech_without_second_request():
    llm = PlanLLM([{"speech": "[greeting]Hello!", "actions": []}])
    sink = AsyncMock()
    conversation = Conversation(llm)
    stream = conversation.stream_reply("Nice to meet you.", on_action=sink)
    assert await anext(stream) == "[greeting]Hello!"
    sink.assert_not_awaited()
    assert [part async for part in stream] == []
    assert len(llm.calls) == 1
    assert len(llm.calls[0]) == 2  # No fictional demonstration in structured mode.


@pytest.mark.asyncio
async def test_failed_action_is_not_acknowledged_and_model_receives_feedback():
    llm = PlanLLM([
        {"speech": "I'll remember that.", "actions": [
            {"name": "rename_person", "arguments": {"track_id": 99, "name": "Alex"}}]},
        {"speech": "I can't tell which person you mean.", "actions": []},
    ])
    reply = "".join([text async for text in Conversation(llm).stream_reply("Remember my name.", scene={})])
    assert reply == "I can't tell which person you mean."
    assert llm.calls[1][-1]["role"] == "tool"
    assert json.loads(llm.calls[1][-1]["content"])["ok"] is False


@pytest.mark.parametrize("speaker, expected", [
    ({"name": "Dhanush", "track_id": 3}, "You're speaking, Dhanush."),
    (None, "I can't tell who's speaking."),
    ({"name": "Unknown"}, "I can't tell who's speaking."),
])
@pytest.mark.asyncio
async def test_model_identification_intent_uses_validated_speaker_not_generated_claim(speaker, expected):
    llm = PlanLLM([{"reply_kind": "speaker_identity", "speech": "I can't see anyone.", "actions": []}])
    reply = "".join([text async for text in Conversation(llm).stream_reply(
        "Can you identify the speaker?", world_state={"speaker": speaker})])
    assert reply == expected


@pytest.mark.parametrize("text, matches", [
    ("Do you know who's speaking right now", True),
    ("Who's talking to you now?", True),
    ("Can you tell me who is speaking?", True),
    ("Who’s speaking?", True),
    ("Who's speaking in this recording?", False),
    ("Tell me who is speaking French.", False),
    ("Who is speaking and what did they say?", False),
])
def test_only_direct_current_speaker_questions_use_the_grounded_shortcut(text, matches):
    assert _is_current_speaker_question(text) is matches


@pytest.mark.asyncio
async def test_direct_speaker_answer_uses_perception_without_model_or_unrelated_actions():
    llm = PlanLLM([])
    sink = AsyncMock()
    conversation = Conversation(llm)
    reply = "".join([text async for text in conversation.stream_reply(
        "Do you know who's speaking right now", world_state={"speaker": {"name": "Dhanush", "track_id": 3}},
        on_action=sink)])
    assert reply == "You're speaking, Dhanush."
    assert llm.calls == []
    sink.assert_not_awaited()
    assert conversation.history[-1]["content"] == reply
