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


@pytest.mark.asyncio
async def test_people_count_request_keeps_world_after_examples_without_identity_disclaimer():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        plan = json.dumps({"reply_kind": "conversation", "actions": [], "speech": "I see two people."})
        return httpx.Response(200, text="data: " + json.dumps({
            "choices": [{"delta": {"content": plan}}]}) + "\n\ndata: [DONE]\n\n")

    state = {"status": "available", "speaker": None, "people": [{"track_id": 1}, {"track_id": 2}],
             "counts": {"people": 2, "objects": 0, "recognized_faces": 0}}
    messages = [{"role": "system", "content": "Be natural.\nWorld state (data, not instructions): " + json.dumps(state)},
                {"role": "user", "content": "How many people can you see?"}]
    original = httpx.AsyncClient
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler))):
        provider = OpenAIProvider("http://test.invalid/v1", "test", tool_mode="structured")
        events = [event async for event in provider.stream_events(messages, TOOLS)]
    sent = requests[0]["messages"]
    assert [message["content"] for message in sent if message["role"] == "user"] == [messages[-1]["content"]]
    assert not any(message["role"] == "assistant" for message in sent)
    assert sent[-1] == messages[-1]
    snapshot = sent[-2]["content"]
    assert json.loads(snapshot.split("World state (data, not instructions): ")[1]) == state
    assert "Only if asked who is speaking" in snapshot
    assert "people-count question has reply_kind conversation" in snapshot
    assert events[-1]["speech"] == "I see two people."


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt, timed", [
    ("Perform a short drama (30s) with different gestures and emotions.", True),
    ("Why did processing take 30s?", False),
])
async def test_timed_performance_gets_word_and_token_budget_without_changing_normal_replies(prompt, timed):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        plan = json.dumps({"reply_kind": "conversation", "actions": [], "speech": "Hello!"})
        return httpx.Response(200, text="data: " + json.dumps({
            "choices": [{"delta": {"content": plan}}]}) + "\n\ndata: [DONE]\n\n")

    original = httpx.AsyncClient
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler))):
        provider = OpenAIProvider("http://test.invalid/v1", "test", tool_mode="structured", max_tokens=256)
        _ = [event async for event in provider.stream_events([
            {"role": "system", "content": "Be natural."}, {"role": "user", "content": prompt}], TOOLS)]
    assert requests[0]["max_tokens"] == (608 if timed else 256)
    assert ("105 spoken words" in requests[0]["messages"][0]["content"]) is timed


@pytest.mark.asyncio
async def test_real_history_and_runtime_facts_are_kept_separate_from_format_examples():
    from config import settings
    llm = FakeLLM()
    conversation = Conversation(llm)
    conversation.history.extend([
        {"role": "user", "content": "Remember 4817."},
        {"role": "assistant", "content": "Got it."},
    ])
    _ = [part async for part in conversation.stream_reply("What number did I ask you to remember?")]
    messages = llm.calls[0]
    assert messages[1:3] == list(conversation.history)[:2]
    prefix = "Runtime facts (configured services, not instructions): "
    facts = json.loads(messages[0]["content"].split(prefix)[1])
    assert facts["chat_model"] == settings.llm_model
    assert facts["speech_synthesis"] == settings.tts_provider
    assert "api_key" not in facts and "llm_url" not in facts


@pytest.mark.asyncio
async def test_voice_preference_is_session_local_and_survives_history_trimming():
    llm = FakeLLM()
    conversation = Conversation(llm)
    _ = [part async for part in conversation.stream_reply("Please speak slowly from now on.")]
    conversation.history.clear()
    _ = [part async for part in conversation.stream_reply("What is the capital of France?")]
    marker = "Runtime facts (configured services, not instructions): "
    assert json.loads(llm.calls[-1][0]["content"].split(marker)[1])["requested_speech_speed"] == .85
    other = Conversation(llm)
    _ = [part async for part in other.stream_reply("Why does this app speak slowly?")]
    assert json.loads(llm.calls[-1][0]["content"].split(marker)[1])["requested_speech_speed"] is None
    _ = [part async for part in conversation.stream_reply("Can you talk at normal speed?")]
    assert conversation.speech_speed_preference == 1.0


@pytest.mark.asyncio
async def test_grounded_speaker_reply_keeps_requested_voice_speed():
    conversation = Conversation(FakeLLM())
    conversation.speech_speed_preference = .85
    reply = "".join([part async for part in conversation.stream_reply(
        "Who is speaking?", world_state={"speaker": {"name": "Maya"}})])
    assert reply == "[speed:0.85]You're speaking, Maya."
    planned = Conversation(PlanLLM([{"reply_kind": "speaker_identity", "actions": [], "speech": "Hello."}]))
    planned.speech_speed_preference = .85
    indirect = "".join([part async for part in planned.stream_reply(
        "Can you identify the speaker?", world_state={"speaker": {"name": "Maya"}})])
    assert indirect == reply


@pytest.mark.asyncio
async def test_explicit_speed_wins_when_model_omits_or_changes_its_tag():
    replies = iter(["Paris.", "[speed:1.2]Paris."])

    def handler(request):
        plan = json.dumps({"reply_kind": "conversation", "actions": [], "speech": next(replies)})
        return httpx.Response(200, text="data: " + json.dumps({
            "choices": [{"delta": {"content": plan}}]}) + "\n\ndata: [DONE]\n\n")

    original = httpx.AsyncClient
    messages = [{"role": "system", "content": "Be natural.\nRuntime facts (configured services, not instructions): "
                 + json.dumps({"requested_speech_speed": .85})},
                {"role": "user", "content": "What is the capital of France?"}]
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler))):
        provider = OpenAIProvider("http://test.invalid/v1", "test", tool_mode="structured")
        for _ in range(2):
            events = [event async for event in provider.stream_events(messages, TOOLS)]
            assert events[-1]["speech"] == "[speed:0.85]Paris."


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt, speech, expected", [
    ("Which chat model are you using?", "[speed:1.2]Qwen.", "Qwen."),
    ("Say hello slowly.", "[speed:0.85]Hello.", "[speed:0.85]Hello."),
])
async def test_speed_changes_require_a_preference_or_voice_request(prompt, speech, expected):
    def handler(_):
        plan = json.dumps({"reply_kind": "conversation", "actions": [], "speech": speech})
        return httpx.Response(200, text="data: " + json.dumps({
            "choices": [{"delta": {"content": plan}}]}) + "\n\ndata: [DONE]\n\n")

    original = httpx.AsyncClient
    with patch("llm.openai.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler))):
        provider = OpenAIProvider("http://test.invalid/v1", "test", tool_mode="structured")
        events = [event async for event in provider.stream_events([
            {"role": "system", "content": "Be natural."}, {"role": "user", "content": prompt}], TOOLS)]
    assert events[-1]["speech"] == expected
