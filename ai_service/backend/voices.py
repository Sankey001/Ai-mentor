VOICE_REGISTRY = {
    "en-US-GuyNeural": {
        "voice_id": "en-US-GuyNeural",
        "name": "Guy",
        "gender": "Male",
        "language": "English",
        "accent": "US",
        "description": "Natural and professional male voice.",
    },
    "en-US-AriaNeural": {
        "voice_id": "en-US-AriaNeural",
        "name": "Aria",
        "gender": "Female",
        "language": "English",
        "accent": "US",
        "description": "Clear and expressive female voice.",
    },
    "en-GB-RyanNeural": {
        "voice_id": "en-GB-RyanNeural",
        "name": "Ryan",
        "gender": "Male",
        "language": "English",
        "accent": "UK",
        "description": "Friendly British male voice.",
    },
    "en-GB-SoniaNeural": {
        "voice_id": "en-GB-SoniaNeural",
        "name": "Sonia",
        "gender": "Female",
        "language": "English",
        "accent": "UK",
        "description": "Bright and approachable British female voice.",
    },
    "en-IN-PrabhatNeural": {
        "voice_id": "en-IN-PrabhatNeural",
        "name": "Prabhat",
        "gender": "Male",
        "language": "English",
        "accent": "India",
        "description": "Warm Indian male voice.",
    },
    "en-IN-NeerjaNeural": {
        "voice_id": "en-IN-NeerjaNeural",
        "name": "Neerja",
        "gender": "Female",
        "language": "English",
        "accent": "India",
        "description": "Professional Indian female voice.",
    },
    "es-ES-AlvaroNeural": {
        "voice_id": "es-ES-AlvaroNeural",
        "name": "Alvaro",
        "gender": "Male",
        "language": "Spanish",
        "accent": "Spain",
        "description": "Clear Spanish male voice.",
    },
    "es-ES-ElviraNeural": {
        "voice_id": "es-ES-ElviraNeural",
        "name": "Elvira",
        "gender": "Female",
        "language": "Spanish",
        "accent": "Spain",
        "description": "Professional Spanish female voice.",
    },
    "fr-FR-HenriNeural": {
        "voice_id": "fr-FR-HenriNeural",
        "name": "Henri",
        "gender": "Male",
        "language": "French",
        "accent": "France",
        "description": "Polite French male voice.",
    },
    "fr-FR-DeniseNeural": {
        "voice_id": "fr-FR-DeniseNeural",
        "name": "Denise",
        "gender": "Female",
        "language": "French",
        "accent": "France",
        "description": "Clear French female voice.",
    }
}

DEFAULT_VOICE_ID = "en-US-GuyNeural"

def get_voice(voice_id: str) -> str:
    """Validate and return the correct voice_id, falling back to default if invalid."""
    if not voice_id or voice_id not in VOICE_REGISTRY:
        return DEFAULT_VOICE_ID
    return voice_id

NEUTRAL_RATE = "+0%"
NEUTRAL_PITCH = "+0Hz"

# Each mentor's own voice, used when the learner has not chosen one.
MENTOR_VOICES = {
    "modi": {"voice_id": "en-IN-PrabhatNeural", "rate": "-5%", "pitch": "-2Hz"},
    "owl": {"voice_id": "en-GB-RyanNeural", "rate": "+0%", "pitch": "+0Hz"},
    "salman": {"voice_id": "en-IN-PrabhatNeural", "rate": "+5%", "pitch": "-6Hz"},
    "srk": {"voice_id": "en-IN-PrabhatNeural", "rate": "+0%", "pitch": "+3Hz"},
}


def resolve_mentor_voice(
    mentor: str | None,
    voice_id: str | None,
    rate: str | None,
    pitch: str | None,
    pack_voice: dict | None = None,
) -> tuple[str, str, str]:
    """Pick voice, rate and pitch: learner's choice, then the mentor's own, then default.

    ``pack_voice`` is the standard voice saved in the mentor's own folder; it
    comes before the built-in table.
    """
    name = mentor.strip().lower() if isinstance(mentor, str) else ""
    mentor_voice = pack_voice or MENTOR_VOICES.get(name, {})

    selected_voice = get_voice(voice_id or mentor_voice.get("voice_id"))
    if not rate or rate == NEUTRAL_RATE:
        rate = mentor_voice.get("rate", NEUTRAL_RATE)
    if not pitch or pitch == NEUTRAL_PITCH:
        pitch = mentor_voice.get("pitch", NEUTRAL_PITCH)
    return selected_voice, rate, pitch


def get_all_voices(language: str | None = None, gender: str | None = None) -> list:
    """Return available voices with metadata, optionally filtered.

    ``language`` and ``gender`` are matched case-insensitively and combine with
    AND. Values that match nothing yield an empty list rather than an error.
    """
    result = list(VOICE_REGISTRY.values())
    if language:
        result = [v for v in result if v["language"].casefold() == language.strip().casefold()]
    if gender:
        result = [v for v in result if v["gender"].casefold() == gender.strip().casefold()]
    return result
