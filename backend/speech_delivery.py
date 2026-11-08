"""Paragraph-scoped facial emotion and synthesis speed, independent of TTS chunks."""

from dataclasses import dataclass

EMOTIONS = frozenset({"happy", "sad", "angry", "fear", "disgust", "love"})
SPEEDS = {"slow": 0.7, "fast": 1.25}
PARAGRAPH_BREAK = "paragraph_break"
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.", "e.g.", "i.e."}



@dataclass(frozen=True)
class DeliverySpan:
    start: int
    end: int
    emotion: str
    rate: float


class DeliveryPlanner:
    """Keep settings over sentences and TTS chunks; reset both at blank lines."""

    def __init__(self):
        self.emotion = "neutral"
        self.rate = 1.0

    def split(self, text, cues):
        # Preserve original tag/break order when several cues share an offset.
        events = sorted(cues, key=lambda cue: cue.offset)
        spans = []
        cursor = 0

        def append(end):
            nonlocal cursor
            if text[cursor:end].strip():
                if spans and (spans[-1].emotion, spans[-1].rate) == (self.emotion, self.rate):
                    previous = spans.pop()
                    spans.append(DeliverySpan(previous.start, end, self.emotion, self.rate))
                else:
                    spans.append(DeliverySpan(cursor, end, self.emotion, self.rate))
            cursor = end

        for cue in events:
            append(cue.offset)
            action = cue.action
            if action == PARAGRAPH_BREAK:
                self.emotion, self.rate = "neutral", 1.0
            elif action in EMOTIONS:
                self.emotion = action
            elif action in SPEEDS:
                self.rate = SPEEDS[action]
        append(len(text))
        return spans
