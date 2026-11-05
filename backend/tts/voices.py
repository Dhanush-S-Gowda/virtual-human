"""Provider capabilities; the browser selects a voice for each recording."""

VOICES = {
    "kokoro": ("am_puck", "af_heart"),
    "supertonic": ("F1", "M1"),
}
DEFAULT_VOICES = {"kokoro": "af_heart", "supertonic": "F1"}


def resolve_voice(provider, voice=None):
    """Keep older clients usable while rejecting voices from another provider."""
    if provider not in VOICES:
        raise ValueError("Voice selection is unavailable for the configured TTS provider.")
    if voice is None:
        return DEFAULT_VOICES[provider]
    if not isinstance(voice, str) or voice not in VOICES[provider]:
        raise ValueError(f"Choose a {provider} voice: {', '.join(VOICES[provider])}.")
    return voice
