from __future__ import annotations

import json
import os
import re
from typing import Any

import backend.voices as voices
from backend.services import media_processor
from backend.services.media_processor import (
    MENTOR_NAME_PATTERN,
    get_mentor_clips,
    resolve_mentor_dir,
)

# A mentor pack is the folder input/<mentor>/ holding:
#   *.mp4        clips of the mentor talking and reacting
#   voice.wav    a clean recording of the mentor, used for voice cloning
#   mentor.json  {"persona": "...", "expressiveness": 0.0-1.0}
VOICE_SAMPLE_NAMES = ("voice.wav", "voice.mp3", "voice.m4a", "voice.flac")
PROFILE_NAME = "mentor.json"
MAX_PERSONA_CHARS = 1000
DEFAULT_EXPRESSIVENESS = 0.5


def get_mentor_voice_sample(mentor_name: str, base_dir: str | None = None) -> str | None:
    """Return the mentor's recorded voice sample, if their pack has one."""
    mentor_dir = resolve_mentor_dir(mentor_name, base_dir)
    if not mentor_dir:
        return None
    for filename in VOICE_SAMPLE_NAMES:
        candidate = os.path.realpath(os.path.join(mentor_dir, filename))
        if os.path.commonpath([mentor_dir, candidate]) == mentor_dir and os.path.isfile(candidate):
            return candidate
    return None


def list_mentors(base_dir: str | None = None) -> list[dict[str, Any]]:
    """Return the mentors that have at least one clip, with what their pack offers."""
    input_dir = os.path.join(base_dir or media_processor.BASE_DIR, "backend", "input")
    mentors = []
    for name in sorted(os.listdir(input_dir)) if os.path.isdir(input_dir) else []:
        if not MENTOR_NAME_PATTERN.fullmatch(name) or not os.path.isdir(
            os.path.join(input_dir, name)
        ):
            continue
        if not get_mentor_clips(name, base_dir):
            continue
        mentors.append(
            {
                "name": name,
                "voice": "cloned" if get_mentor_voice_sample(name, base_dir) else "stock",
                "persona": bool(get_mentor_profile(name, base_dir)["persona"]),
            }
        )
    return mentors


STOCK_RATE = re.compile(r"[+-](?:100|[1-9]?\d)%")
STOCK_PITCH = re.compile(r"[+-](?:100|[1-9]?\d)Hz")


def valid_stock_voice(voice: Any) -> bool:
    """True for {"voice_id", "rate", "pitch"} naming a listed voice and valid adjustments."""
    return (
        isinstance(voice, dict)
        and voice.get("voice_id") in voices.VOICE_REGISTRY
        and isinstance(voice.get("rate", voices.NEUTRAL_RATE), str)
        and isinstance(voice.get("pitch", voices.NEUTRAL_PITCH), str)
        and bool(STOCK_RATE.fullmatch(voice.get("rate", voices.NEUTRAL_RATE)))
        and bool(STOCK_PITCH.fullmatch(voice.get("pitch", voices.NEUTRAL_PITCH)))
    )


def get_mentor_stock_voice(mentor_name: str, base_dir: str | None = None) -> dict[str, str] | None:
    """Return the standard voice chosen for this mentor in mentor.json, if it is valid."""
    mentor_dir = resolve_mentor_dir(mentor_name, base_dir)
    if not mentor_dir:
        return None
    try:
        with open(os.path.join(mentor_dir, PROFILE_NAME), encoding="utf-8") as profile_file:
            voice = json.load(profile_file).get("voice")
    except (OSError, ValueError, AttributeError):
        return None
    if not valid_stock_voice(voice):
        return None
    return {
        "voice_id": voice["voice_id"],
        "rate": voice.get("rate", voices.NEUTRAL_RATE),
        "pitch": voice.get("pitch", voices.NEUTRAL_PITCH),
    }


def get_mentor_profile(mentor_name: str, base_dir: str | None = None) -> dict[str, Any]:
    """Return the mentor's persona text and speech expressiveness, with safe defaults."""
    profile = {"persona": "", "expressiveness": DEFAULT_EXPRESSIVENESS}
    mentor_dir = resolve_mentor_dir(mentor_name, base_dir)
    if not mentor_dir:
        return profile

    try:
        with open(os.path.join(mentor_dir, PROFILE_NAME), encoding="utf-8") as profile_file:
            data = json.load(profile_file)
    except (OSError, ValueError):
        return profile
    if not isinstance(data, dict):
        return profile

    persona = data.get("persona")
    if isinstance(persona, str):
        profile["persona"] = persona.strip()[:MAX_PERSONA_CHARS]

    expressiveness = data.get("expressiveness")
    if isinstance(expressiveness, (int, float)) and not isinstance(expressiveness, bool):
        profile["expressiveness"] = min(1.0, max(0.0, float(expressiveness)))
    return profile
