"""Delivery tags stay paragraph scoped across arbitrary token and audio chunks."""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_backend import FakeLLM, FakeWebSocket
import pipeline
from inline_gestures import Cue, InlineGestures, take_segment_cues
from speech_delivery import DeliveryPlanner, EMOTIONS, SPEEDS, PARAGRAPH_BREAK
from schemas import SynthesizeResponse


def plan(text, cues=(), planner=None):
    return [(text[span.start:span.end].strip(), span.emotion, span.rate)
            for span in (planner or DeliveryPlanner()).split(text, cues)]


def test_split_tags_are_removed_and_only_delivery_tags_change_style():
    parser = InlineGestures()
    text = "".join(parser.feed(part) for part in (
        "[hap", "py][fa", "st][clap", "ping]You won! [sad][slow]Sorry. [neutral][normal][/emotion]"))
    parser.finish()
    assert text == "You won! Sorry."
    assert [cue.action for cue in parser.cues] == ["clapping"]
    assert [cue.action for cue in parser.delivery_cues] == ["happy", "fast", "sad", "slow"]
    assert parser.rejected == 3
    assert plan(text, parser.delivery_cues) == [
        ("You won!", "happy", 1.25), ("Sorry.", "sad", .7)]


def test_paragraph_end_resets_both_properties_but_new_tag_only_changes_its_own_type():
    text = "You won, now breathe slowly. What next?"
    assert plan(text, [Cue(0, "happy"), Cue(0, "fast"), Cue(text.index("now"), "slow")]) == [
        ("You won,", "happy", 1.25), ("now breathe slowly. What next?", "happy", .7)]
    assert plan(text, [Cue(0, "sad"), Cue(0, "slow"), Cue(text.index("What"), PARAGRAPH_BREAK)]) == [
        ("You won, now breathe slowly.", "sad", .7), ("What next?", "neutral", 1.)]


def test_state_survives_internal_chunks_and_does_not_split_plain_sentences():
    planner = DeliveryPlanner()
    assert plan("A long sentence continues", [Cue(0, "sad"), Cue(0, "slow")], planner) == [
        ("A long sentence continues", "sad", .7)]
    assert plan("over several chunks. Then normal.", planner=planner) == [
        ("over several chunks. Then normal.", "sad", .7)]
    assert plan("New paragraph.", [Cue(0, PARAGRAPH_BREAK)], planner) == [
        ("New paragraph.", "neutral", 1.)]
    assert plan("Hello! How are you?") == [("Hello! How are you?", "neutral", 1.)]


def test_sentences_quotes_abbreviations_and_decimals_do_not_reset_paragraph_speed():
    text = 'Dr. Smith said "try 0.7 times." Great!'
    assert plan(text, [Cue(0, "slow"), Cue(text.index("Great"), "happy")]) == [
        ('Dr. Smith said "try 0.7 times."', "neutral", .7), ("Great!", "happy", .7)]


def test_loaded_prompt_lists_delivery_tags_defaults_and_scope():
    prompt = pipeline.settings.system_prompt
    assert all(f"[{tag}]" in prompt for tag in EMOTIONS | SPEEDS.keys())
    assert "Every paragraph starts" in prompt and "1×" in prompt
    assert "Separate paragraphs with a blank line" in prompt
    assert "across all sentences in that paragraph until a blank line" in prompt
    assert "Gesture tags cue their following phrase" in prompt
    assert "Never write neutral/normal tags or closing tags" in prompt
    assert "[neutral]" not in prompt and "[normal]" not in prompt and "[/emotion]" not in prompt


def test_delivery_tags_are_bounded_and_unclosed_tags_do_not_reach_speech():
    parser = InlineGestures()
    assert parser.feed("[happy]" * 40 + "Hello[fast") == "Hello"
    parser.finish()
    assert len(parser.delivery_cues) == 32
    assert parser.rejected == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("character_stream", [False, True])
async def test_pipeline_synthesizes_changed_speeds_and_delivers_emotions_per_audio_chunk(
        monkeypatch, tmp_path, character_stream):
    tagged = ("[happy][fast][clapping]We won! Yes indeed.\n\n"
              "[sad][slow]Take a breath. Stay here.\n\nBack to work.")

    class TaggedLLM(FakeLLM):
        async def stream_chat(self, messages):
            for delta in (tagged if character_stream else [tagged]):
                yield delta
                await asyncio.sleep(0)

    class ChunkedTTS:
        def __init__(self):
            self.calls = []

        async def stream_synthesize(self, text, voice, speed):
            self.calls.append((text, speed))
            for word in text.split():
                await asyncio.sleep(0)
                yield SynthesizeResponse(audio_base64="YQ==", audio_encoding="wav", words=[word],
                                         wtimes=[0], wdurations=[100 / speed])

    tts = ChunkedTTS()
    monkeypatch.setattr(pipeline, "current_world", AsyncMock(return_value=({"speaker": None}, {})))
    monkeypatch.setattr(pipeline, "_forward_transcript_to_cv", AsyncMock())
    monkeypatch.setattr(pipeline, "_METRICS_FILE", tmp_path / "metrics.csv")
    monkeypatch.setattr(pipeline.settings, "min_speech_segment_words", 3)
    monkeypatch.setattr(pipeline.settings, "max_speech_segment_words", 3)
    app = SimpleNamespace(state=SimpleNamespace(providers=SimpleNamespace(
        llm=TaggedLLM(), tts=tts), conversations=OrderedDict()))
    socket = FakeWebSocket(app, {"type": "start", "text": "Act this scene.", "speed": 1.})
    await pipeline.handle_websocket(socket)
    assert not [event for event in socket.events if event["type"] == "error"]
    packets = [event for event in socket.events if event["type"] == "speech"]
    words = [packet["words"][0] for packet in packets]
    assert words == ["We", "won!", "Yes", "indeed.", "Take", "a", "breath.", "Stay", "here.", "Back", "to", "work."]
    assert [packet["emotion"] for packet in packets] == ["happy"] * 4 + ["sad"] * 5 + ["neutral"] * 3
    speeds_by_word = {word: rate for text, rate in tts.calls for word in text.split()}
    assert speeds_by_word == dict(zip(words, [1.25] * 4 + [.7] * 5 + [1.] * 3))
    assert [cue["action"] for packet in packets for cue in packet["gestures"]] == ["clapping"]
    clean = "".join(event["text"] for event in socket.events if event["type"] == "llm_delta")
    assert clean == "We won! Yes indeed. Take a breath. Stay here. Back to work."
    assert "".join(event["raw_text"] for event in socket.events if event["type"] == "llm_delta") == tagged
    assert all("[" not in text for text, _ in tts.calls)
    assert all(packet["wdurations"][0] > 0 for packet in packets)


def test_sentence_and_markdown_closers_do_not_reset_paragraph_style():
    text = "**Great!** Now ordinary."
    assert plan(text, [Cue(0, "happy"), Cue(0, "fast")]) == [
        ("**Great!** Now ordinary.", "happy", 1.25)]


@pytest.mark.parametrize("separator", ["\n\n", "\n \n", "\r\n\r\n", "\r\r"])
@pytest.mark.parametrize("character_stream", [False, True])
def test_blank_lines_survive_whitespace_normalization_and_stream_boundaries(separator, character_stream):
    parser = InlineGestures()
    raw = "[sad][slow]First. Still sad." + separator + "[thinking]Now neutral."
    text = "".join(parser.feed(delta) for delta in (raw if character_stream else [raw]))
    assert plan(text, parser.delivery_cues) == [
        ("First. Still sad.", "sad", .7), ("Now neutral.", "neutral", 1.)]
    assert [cue.action for cue in parser.cues] == ["thinking"]


def test_single_line_break_keeps_style_but_blank_line_resets_before_new_tags():
    parser = InlineGestures()
    text = parser.feed("[sad][slow]First.\nSecond.\n\n[happy][fast]Third.")
    assert plan(text, parser.delivery_cues) == [
        ("First. Second.", "sad", .7), ("Third.", "happy", 1.25)]


def test_tag_on_empty_paragraph_does_not_leak_at_same_spoken_offset():
    parser = InlineGestures()
    text = parser.feed("[happy][fast]\n\nNo tags here.")
    assert plan(text, parser.delivery_cues) == [("No tags here.", "neutral", 1.)]


def test_new_reply_starts_with_defaults_even_if_previous_reply_had_no_final_punctuation():
    assert plan("Still upset", [Cue(0, "sad"), Cue(0, "slow")]) == [("Still upset", "sad", .7)]
    assert plan("Hello again.") == [("Hello again.", "neutral", 1.)]


def test_system_prompt_drama_example_has_correct_paragraph_scope():
    prompt = pipeline.settings.system_prompt
    example = prompt.split("Example drama response (four paragraphs; blank lines are intentional):\n", 1)[1]
    example = example.split("\n\nExample meaning:", 1)[0]
    assert len(example.split("\n\n")) == 4
    parser = InlineGestures()
    text = "".join(parser.feed(character) for character in example)
    spans = plan(text, parser.delivery_cues)
    assert [(emotion, rate) for _, emotion, rate in spans] == [
        ("sad", .7), ("neutral", 1.), ("happy", 1.25), ("angry", 1.)]
    assert all(len(words.split()) > 5 for words, _, _ in spans)
    assert [cue.action for cue in parser.cues] == ["thinking", "greeting", "talking"]
