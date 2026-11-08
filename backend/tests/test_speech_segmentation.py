"""Word limits must survive sentence boundaries and arbitrary LLM deltas."""

import pytest

from backend.tests.test_backend import FakeLLM
from config import settings
from pipeline import _split_ready_segments


@pytest.fixture(autouse=True)
def word_limits(monkeypatch):
    monkeypatch.setattr(settings, "min_speech_segment_words", 8)
    monkeypatch.setattr(settings, "max_speech_segment_words", 16)


def test_short_sentences_accumulate_until_minimum():
    assert _split_ready_segments("Hello! How are you? ") == ([], "Hello! How are you? ")
    text = "Hello! How are you? I am doing well. More"
    assert _split_ready_segments(text) == (["Hello! How are you? I am doing well."], "More")


def test_sentence_is_preferred_between_minimum_and_maximum():
    text = "one two three four five six seven eight nine. unfinished"
    assert _split_ready_segments(text) == (["one two three four five six seven eight nine."], "unfinished")


def test_maximum_is_enforced_without_sentence_punctuation():
    text = " ".join(f"word{index}" for index in range(33)) + " "
    ready, remaining = _split_ready_segments(text)
    assert [len(item.split()) for item in ready] == [16, 16]
    assert remaining == "word32 "


def test_partial_word_at_maximum_waits_for_following_whitespace():
    text = "word " * 15 + "incomp"
    assert _split_ready_segments(text) == ([], text)
    ready, remaining = _split_ready_segments(text + "lete next")
    assert ready == [("word " * 15 + "incomplete").strip()]
    assert remaining == "next"


def test_final_short_remainder_is_spoken_and_long_final_text_still_has_a_cap():
    assert _split_ready_segments("Hello!", force=True) == (["Hello!"], "")
    ready, remaining = _split_ready_segments("word " * 35, force=True)
    assert [len(item.split()) for item in ready] == [16, 16, 3]
    assert remaining == ""


def test_abbreviations_and_decimals_stay_inside_the_sentence():
    text = "I met our helpful friend named Dr. Smith today at 3.5 hours. Next"
    ready, remaining = _split_ready_segments(text)
    assert ready == ["I met our helpful friend named Dr. Smith today at 3.5 hours."]
    assert remaining == "Next"


def test_word_limits_can_be_changed_in_configuration(monkeypatch):
    monkeypatch.setattr(settings, "min_speech_segment_words", 3)
    monkeypatch.setattr(settings, "max_speech_segment_words", 5)
    assert _split_ready_segments("one. two three. four") == (["one. two three."], "four")
    assert _split_ready_segments("one two three four five six") == (["one two three four five"], "six")


def test_shorter_cap_releases_first_audio_segment_before_a_long_sentence_finishes(monkeypatch):
    monkeypatch.setattr(settings, "min_speech_segment_words", 8)
    monkeypatch.setattr(settings, "max_speech_segment_words", 8)
    ready, remaining = _split_ready_segments("one two three four five six seven eight nine ten still speaking")
    assert ready == ["one two three four five six seven eight"]
    assert remaining == "nine ten still speaking"


def test_character_by_character_stream_never_loses_or_splits_a_word():
    text = "Hello! How are you? I am doing well. " + " ".join(f"token{index}" for index in range(39))
    remaining, ready = "", []
    for character in text:
        chunks, remaining = _split_ready_segments(remaining + character)
        ready.extend(chunks)
    tail, remaining = _split_ready_segments(remaining, force=True)
    ready.extend(tail)
    assert " ".join(ready).split() == text.split()
    assert all(8 <= len(item.split()) <= 16 for item in ready[:-1])
    assert remaining == ""


def test_first_segment_is_shorter_but_later_segments_keep_regular_limits(monkeypatch):
    monkeypatch.setattr(settings, "first_speech_segment_words", 6)
    text = " ".join(f"word{index}" for index in range(40))
    ready, remaining = _split_ready_segments(text, force=True, first_segment=True)
    assert [len(segment.split()) for segment in ready] == [6, 16, 16, 2]
    assert " ".join(ready) == text
    assert remaining == ""


def test_short_first_segment_waits_for_complete_word_across_deltas(monkeypatch):
    monkeypatch.setattr(settings, "first_speech_segment_words", 6)
    text = "one two three four five incomp"
    assert _split_ready_segments(text, first_segment=True) == ([], text)
    ready, remaining = _split_ready_segments(text + "lete seven", first_segment=True)
    assert ready == ["one two three four five incomplete"]
    assert remaining == "seven"


def test_zero_disables_short_first_segment(monkeypatch):
    monkeypatch.setattr(settings, "first_speech_segment_words", 0)
    text = "one two three four five six seven "
    assert _split_ready_segments(text, first_segment=True) == ([], text)
