"""Normalize LLM text to characters shared by the display and speech paths."""

import html
import re
import unicodedata


# Keycaps include an ordinary digit, #, or *; remove the entire emoji sequence.
_KEYCAP = re.compile(r"[0-9#*]\ufe0f?\u20e3")
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf"
    "\u231a\u231b\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa"
    "\u2b05-\u2b07\u2b1b\u2b1c\u2b50\u2b55\u3030\u303d\u3297\u3299]"
)
_EMOJI_VARIANT = re.compile(r"[^\w\s]\ufe0f")
_LIST_PREFIX = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|>[ \t]*|[-*+][ \t]+|\d+[.)][ \t]+)")
_EMPHASIS = re.compile(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)")


def normalize_for_tts(text: str) -> str:
    """Remove emoji/formatting; preserve words, accents, numbers and punctuation.

    Do not expand numbers, dates or abbreviations: providers handle pronunciation,
    and rewriting those values here could change their meaning.
    """
    text = unicodedata.normalize("NFC", html.unescape(text))
    text = _KEYCAP.sub(" ", text)
    text = _EMOJI_VARIANT.sub(" ", text)
    text = _EMOJI.sub(" ", text)
    text = sanitize_tts_characters(text)
    text = re.sub(r"(?m)^[ \t]*```[^\n]*$", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^\n)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"(?m)^[ \t]*(?:[-*_][ \t]*){3,}$", " ", text)
    text = _LIST_PREFIX.sub("", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = _EMPHASIS.sub(r"\2", text)
    text = text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    # An emoji-only segment may leave punctuation but has nothing to pronounce.
    return text if any(character.isalnum() for character in text) else ""


def sanitize_tts_characters(text: str) -> str:
    """Keep lexical Unicode and punctuation; replace symbols and controls with spaces.

    This provider-neutral policy keeps Unicode letters, marks, numbers, and
    punctuation while removing controls, emoji, and other symbol categories.
    Replacing symbols with spaces prevents accidental word concatenation; spell
    them out in words when their meaning matters in speech. A provider can still
    impose language-specific limits on the retained letters.
    """
    output = []
    for character in text:
        codepoint = ord(character)
        if (0xfe00 <= codepoint <= 0xfe0f or 0xe0100 <= codepoint <= 0xe01ef
                or character == "\u20e3"):
            continue
        if character.isspace():
            output.append(character)
        elif unicodedata.category(character) == "Cf":
            continue
        elif unicodedata.category(character)[0] in {"L", "M", "N", "P"}:
            output.append(character)
        else:
            output.append(" ")
    return "".join(output)
