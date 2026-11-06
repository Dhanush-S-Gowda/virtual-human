"""Bounded incremental gesture-tag parsing and TTS word-time alignment."""

from dataclasses import dataclass
from math import isfinite
import re
import json
from pathlib import Path

from tts.normalization import normalize_for_tts, sanitize_tts_characters


CATALOG = json.loads((Path(__file__).resolve().parents[1] / "TalkingHead/animation_catalog.json").read_text())
TAGS = {item["tag"]: item["tag"] for item in CATALOG}
_WORDS = re.compile(r"\w+(?:['’]\w+)*", re.UNICODE)


@dataclass(frozen=True)
class Cue:
    offset: int
    action: str


class InlineGestures:
    """Tags never enter displayed text; unknown/malformed bracket tags are omitted."""

    def __init__(self):
        self.pending = None
        self.offset = 0
        self.cues = []
        self.rejected = 0
        self._too_long = False
        self._pending_space = False

    def feed(self, text):
        output = []
        for char in text:
            if char == "[":
                if self.pending is not None:
                    self.rejected += 1
                self.pending = ""
                self._too_long = False
            elif self.pending is not None:
                if char == "]":
                    action = TAGS.get(self.pending.strip().casefold()) if not self._too_long else None
                    if action and len(self.cues) < 32:
                        self.cues.append(Cue(self.offset, action))
                    else:
                        self.rejected += 1
                    self.pending = None
                elif len(self.pending) < 48:
                    self.pending += char
                else:
                    self._too_long = True
            elif char != "]":
                safe = sanitize_tts_characters(char)
                for output_char in safe:
                    if output_char.isspace():
                        self._pending_space = self.offset > 0
                        continue
                    if self._pending_space:
                        if output_char not in ".,!?;:)]}":
                            output.append(" ")
                            self.offset += 1
                        self._pending_space = False
                    output.append(output_char)
                    self.offset += 1
        return "".join(output)

    def finish(self):
        if self.pending is not None:
            self.rejected += 1
        self.pending = None


def align_cues(text, cues, payload):
    """Resolve offsets to provider word times; omit cues without a following word."""
    spoken = normalize_for_tts(text)
    spoken_words = list(_WORDS.finditer(spoken))
    timings = []
    for position, (word, time) in enumerate(zip(payload.get("words", []), payload.get("wtimes", []))):
        if not isinstance(time, (int, float)) or not isfinite(time) or time < 0:
            continue
        timings.extend((token.group().casefold(), float(time), position) for token in _WORDS.finditer(word))
    expected = [word.group().casefold() for word in spoken_words]
    actual = [item[0] for item in timings]
    # Misaligned word lists must never silently dispatch a gesture at the wrong word.
    if expected != actual:
        return []
    output = []
    for cue in cues:
        prefix = normalize_for_tts(text[:cue.offset])
        index = len(_WORDS.findall(prefix))
        if index < len(timings):
            output.append({"action": cue.action, "at_ms": timings[index][1], "word_index": index,
                           **_cue_duration(payload, timings[index][2])})
    return output


def take_segment_cues(parser, start, text):
    selected, pending = [], []
    for cue in parser.cues:
        offset = max(0, cue.offset - start)
        if offset < len(text) and _WORDS.search(text, offset):
            selected.append(Cue(offset, cue.action))
        else:
            pending.append(cue)
    parser.cues = pending
    return selected


def _cue_duration(payload, index):
    durations = payload.get("wdurations", [])
    if index < len(durations):
        duration = durations[index]
        if isinstance(duration, (int, float)) and isfinite(duration) and duration > 0:
            return {"duration_ms": float(duration)}
    return {}


class StreamingCueAligner:
    """Align a segment's cues across successive chunks without replaying them."""

    def __init__(self, text, cues):
        self.expected = [word.casefold() for word in _WORDS.findall(normalize_for_tts(text))]
        self.cues = [(len(_WORDS.findall(normalize_for_tts(text[:cue.offset]))), cue.action)
                     for cue in cues]
        self.offset = 0
        self.valid = True

    def feed(self, payload):
        timings = []
        for position, (word, time) in enumerate(zip(payload.get("words", []), payload.get("wtimes", []))):
            if not isinstance(time, (int, float)) or not isfinite(time) or time < 0:
                self.valid = False
                return []
            timings.extend((token.casefold(), float(time), position) for token in _WORDS.findall(word))
        end = self.offset + len(timings)
        if not self.valid or self.expected[self.offset:end] != [item[0] for item in timings]:
            self.valid = False
            return []
        output = [{"action": action, "at_ms": timings[index - self.offset][1],
                   "word_index": index - self.offset,
                   **_cue_duration(payload, timings[index - self.offset][2])}
                  for index, action in self.cues if self.offset <= index < end]
        self.offset = end
        return output
