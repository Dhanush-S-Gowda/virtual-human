"""World grounding, tool boundaries, and model tool-result feedback."""

import json
import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_backend import FakeLLM, FakeTTS, FakeWebSocket
import world
import pipeline
import llm.tools as model_tools


@pytest.mark.asyncio
async def test_world_contains_counts_current_gestures_people_and_validated_speaker(monkeypatch):
    scene = {"frame_available": True, "source_id": "camera", "people": [
        {"track_id": 1, "generation": 10, "person_id": "dan", "display_name": "Dan", "actions": ["wave"]},
        {"track_id": 2, "generation": 20, "display_name": None}], "objects": [
        {"track_id": 3, "label": "cup"}, {"track_id": 4, "label": "cup"},
        {"track_id": 5, "label": "chair"}], "active_speaker": None}
    monkeypatch.setattr(world, "cv_request", AsyncMock(return_value={"data": scene}))
    hint = {"track_id": 1, "generation": 10, "source_id": "camera", "method": "single_visible",
            "display_name": "untrusted client name"}
    state, snapshot = await world.current_world(hint)
    assert len(state["objects"]) == 3
    assert state["people"][1]["name"] == "Unknown"
    assert state["speaker"]["name"] == "Dan"
    assert state["people"][0]["gestures"] == ["wave"]
    assert state["counts"] == {"people": 2, "objects": 3, "recognized_faces": 1}
    assert "generation" not in json.dumps(state)
    for change in ({"generation": 9}, {"source_id": "previous camera"}, {"track_id": True}):
        stale, _ = await world.current_world({**hint, **change})
        assert stale["speaker"] is None


@pytest.mark.asyncio
async def test_unavailable_camera_is_not_an_empty_room(monkeypatch):
    monkeypatch.setattr(world, "cv_request", AsyncMock(side_effect=OSError("offline")))
    state, _ = await world.current_world()
    assert state["status"] == "unavailable"
    monkeypatch.setattr(world, "cv_request", AsyncMock(return_value={"data": {
        "frame_available": True, "people": [], "objects": []}}))
    state, _ = await world.current_world()
    assert state["status"] == "available"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "action"), [("gesture", "greeting"), ("emotion", "happy")])
async def test_gesture_tools_are_rejected(name, action):
    sink = AsyncMock()
    result = await model_tools.execute_tool(name, {"action": action}, {}, sink)
    assert not result["ok"]
    sink.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), [
    ("gesture", {"action": "run_shell"}), ("emotion", {"action": "wave"}),
    ("gesture", {"action": "wave", "extra": True}), ("unknown", {}),
    ("rename_person", {"track_id": True, "name": "Alice"}),
    ("rename_person", {"track_id": 1, "name": "\n"}),
    ("rename_person", {"track_id": 1, "name": "Alice"}),
])
async def test_invalid_tools_and_invisible_rename_targets_are_rejected(name, args):
    sink = AsyncMock()
    assert not (await model_tools.execute_tool(name, args, {}, sink))["ok"]
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_request_preserves_camera_and_track_generation(monkeypatch):
    request = AsyncMock(return_value={"data": {"ok": True, "name": "Maya"}})
    monkeypatch.setattr(model_tools, "cv_request", request)
    scene = {"source_id": "camera", "people": [{"track_id": 7, "generation": 40}]}
    result = await model_tools.execute_tool("rename_person", {"track_id": 7, "name": "Maya"}, scene, AsyncMock())
    assert result["ok"]
    request.assert_awaited_once_with("rename_person", track_id=7, name="Maya",
                                    source_id="camera", generation=40)


@pytest.mark.asyncio
async def test_ambiguous_multi_person_rename_requires_speaker_or_explicit_target(monkeypatch):
    request = AsyncMock(return_value={"data": {"ok": True}})
    monkeypatch.setattr(model_tools, "cv_request", request)
    scene = {"source_id": "camera", "people": [
        {"track_id": 7, "generation": 40, "display_name": "Alex"},
        {"track_id": 8, "generation": 41, "display_name": "Sam"}]}
    args = {"track_id": 7, "name": "Maya"}
    assert not (await model_tools.execute_tool("rename_person", args, scene, AsyncMock(),
                                               user_text="Remember my name as Maya"))["ok"]
    request.assert_not_awaited()
    for context in ({"speaker": {"track_id": 7}}, {"user_text": "Rename track 7 to Maya"},
                    {"user_text": "Remember Alex as Maya"}):
        assert (await model_tools.execute_tool("rename_person", args, scene, AsyncMock(), **context))["ok"]


@pytest.mark.asyncio
async def test_model_sees_world_state_and_tool_result_before_its_spoken_conclusion(monkeypatch):
    class ToolLLM(FakeLLM):
        async def stream_events(self, messages, tools):
            self.calls.append(json.loads(json.dumps(messages)))
            if len(self.calls) == 1:
                yield {"type": "tool_call", "name": "rename_person", "arguments": '{"track_id":7,"name":"Maya"}', "id": "call-1"}
            else:
                assert messages[-1]["role"] == "tool"
                assert json.loads(messages[-1]["content"])["ok"]
                yield {"type": "text", "text": "Hello there."}

    llm, sink = ToolLLM(), AsyncMock()
    state = {"status": "available", "people": [{"track_id": 7, "name": "Maya"}],
             "objects": [{"track_id": 8, "label": "cup"}], "speaker": {"track_id": 7, "name": "Maya"}}
    monkeypatch.setattr(model_tools, "cv_request", AsyncMock(return_value={"data": {"ok": True}}))
    refreshed = {**state, "objects": [], "people": [{"track_id": 7, "name": "Maya", "gestures": []}]}
    fresh_world = AsyncMock(return_value=(refreshed, {"people": [{"track_id": 7}]}))
    monkeypatch.setattr(pipeline, "current_world", fresh_world)
    conversation = pipeline.Conversation(llm)
    reply = "".join([part async for part in conversation.stream_reply(
        "My name is Maya", world_state=state, scene={"people": [{"track_id": 7}]}, on_action=sink)])
    assert reply == "Hello there."
    assert "Maya" in llm.calls[0][-1]["content"] and "cup" in llm.calls[0][-1]["content"]
    assert "cup" not in llm.calls[1][1]["content"]
    assert llm.calls[0][0] == llm.calls[1][0]
    assert llm.calls[1][-2]["tool_calls"][0]["id"] == "call-1"
    assert len(llm.calls[0]) == 2  # Examples live in the concise system prompt.
    assert not any(message.get("tool_calls") for message in conversation.history)
    assert conversation.history[-1]["content"] == reply
    sink.assert_not_awaited()
    fresh_world.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_time_speaker_survives_slow_asr_when_person_has_left(tmp_path, monkeypatch):
    speaker = {"track_id": 1, "name": "Alex", "evidence": "push_to_talk+single_visible"}
    released = {"status": "available", "people": [{"track_id": 1, "name": "Alex"}],
                "objects": [], "speaker": speaker}
    latest = {"status": "available", "people": [], "objects": [], "speaker": None}
    snapshots = AsyncMock(side_effect=[(released, {}), (latest, {})])
    monkeypatch.setattr(pipeline, "current_world", snapshots)
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")

    async def transcribe(*args):
        await asyncio.sleep(0)  # Release-time CV query runs while ASR is pending.
        return {"text": "What do you see?"}

    llm = FakeLLM()
    app = SimpleNamespace(state=SimpleNamespace(conversations=OrderedDict(), providers=SimpleNamespace(
        llm=llm, tts=FakeTTS(), stt=SimpleNamespace(transcribe=transcribe))))
    client = FakeWebSocket(app, {"type": "start"}, [
        {"type": "websocket.receive", "bytes": b"sample"},
        {"type": "websocket.receive", "text": '{"type":"end","speaker":{"track_id":1}}'},
    ])
    await pipeline.handle_websocket(client)
    supplied = llm.calls[0][-1]["content"]
    assert "People now: 0" in supplied
    assert "Current speaker: P1: Alex" in supplied
    assert app.state.conversation_manager.recent_turns[-1]["status"] == "ok"


def test_conversation_manager_bounds_and_owns_its_timing_results():
    manager = pipeline.ConversationManager(FakeLLM())
    result = {"status": "ok", "llm_requests": [{"prompt_ms": 12}]}
    for index in range(130):
        manager.record_turn(str(index), result)
    result["llm_requests"][0]["prompt_ms"] = 99
    assert len(manager.recent_turns) == 128
    assert manager.recent_turns[0]["session_id"] == "2"
    assert manager.recent_turns[-1]["llm_requests"][0]["prompt_ms"] == 12
