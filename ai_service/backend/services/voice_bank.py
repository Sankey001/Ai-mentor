"""Voice bank: phrases a mentor has already said, kept and played again at once.

When a mentor is set up, about 500 phrases are recorded once in its voice, with
lip-sync (backend/data/phrases.txt): openers, transitions, checks, encouragement,
closers and short sentences with technical words. Every sentence the mentor
speaks in a live answer is kept too, so the bank grows with use and more of
each answer plays without waiting for the GPU.

A sentence is found by its text, ignoring case, spacing and curly quotes. The
bank lives in the same folder as the mentor's lesson packs and shares their
media (see ``lesson_packs``), so a phrase also said in a lesson is stored once.
The folder's ``bank.json`` lists what was kept and how often it was used.

Recording the phrases uses the GPU in the background. Lessons being prepared
and live questions go first.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time

from backend.services import lesson_packs, talk
from backend.utils.logging_utils import log_event, short_error

PHRASES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "phrases.txt"
)
BANK_FILE = "bank.json"
# Each technical word is recorded inside two of these, so it is heard in context.
TERM_SENTENCES = (
    "Let's talk about {term}.",
    "This is {term}.",
    "Now we need {term}.",
    "Think of {term} as a tool.",
    "That's where {term} comes in.",
    "Here's how {term} works.",
)
OPENER_GROUPS = ("openers",)
MAX_OPENERS = 16
TYPICAL_PHRASE_SECONDS = 5.0
LESSON_WAIT_SECONDS = 2.0

_banks: dict[str, dict] = {}  # mentor -> {"entries": {normal text: entry}}
_build = {"task": None, "queue": [], "doing": "", "seconds": [], "failed": {}}
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-"})


def normalize(text: str) -> str:
    """How sentences are matched: the same words in any case, spacing or quote style."""
    return " ".join(text.translate(_QUOTES).lower().split())


def load_phrases(path: str | None = None) -> list[tuple[str, str]]:
    """Return (group, phrase) pairs in recording order, without repeats."""
    phrases, seen, group, terms = [], set(), "", 0
    with open(path or PHRASES_FILE, encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line.startswith("## "):
                group = line[3:].strip()
                continue
            if not line or line.startswith("#"):
                continue
            if group == "terms":
                texts = [TERM_SENTENCES[(terms + shift) % len(TERM_SENTENCES)].format(term=line)
                         for shift in (0, 3)]
                terms += 1
            else:
                texts = [line]
            for text in texts:
                if normalize(text) not in seen:
                    seen.add(normalize(text))
                    phrases.append((group, text))
    return phrases


# ---------- the bank's list of what it holds ----------


def _bank_path(mentor: str) -> str:
    return os.path.join(lesson_packs.mentor_dir(mentor), BANK_FILE)


def _bank(mentor: str) -> dict:
    if mentor not in _banks:
        try:
            with open(_bank_path(mentor), encoding="utf-8") as bank_file:
                entries = json.load(bank_file).get("entries", {})
            if not isinstance(entries, dict):
                raise ValueError("entries must be an object")
        except (OSError, ValueError, AttributeError):
            entries = {}
        _banks[mentor] = {"entries": entries}
    return _banks[mentor]


def _save(mentor: str) -> None:
    path = _bank_path(mentor)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as bank_file:
        json.dump({"version": 1, "entries": _bank(mentor)["entries"]}, bank_file,
                  ensure_ascii=False, indent=0)
    os.replace(temp, path)


def forget(mentor: str) -> None:
    """Drop what is remembered about a mentor's bank (its files changed on disk)."""
    _banks.pop(mentor, None)
    _build["queue"][:] = [name for name in _build["queue"] if name != mentor]


def texts(mentor: str) -> list[str]:
    """Every sentence the bank holds for the mentor."""
    return [entry["text"] for entry in _bank(mentor)["entries"].values()
            if isinstance(entry, dict) and isinstance(entry.get("text"), str)]


def add(mentor: str, text: str, source: str) -> None:
    entries = _bank(mentor)["entries"]
    key = normalize(text)
    if key not in entries:
        entries[key] = {"text": text, "source": source, "uses": 0}
        _save(mentor)


def find(setup: lesson_packs.MentorSetup, sentence: str) -> str | None:
    """The text under which a sentence is stored ready to play, or None."""
    if setup.sentence_ready(sentence):  # also finds sentences said in a lesson
        return sentence
    entry = _bank(setup.mentor)["entries"].get(normalize(sentence))
    if isinstance(entry, dict) and setup.sentence_ready(entry.get("text", "")):
        return entry["text"]
    return None


class LiveCache:
    """What a live answer uses: ready phrases, and a place to keep new sentences."""

    def __init__(self, mentor: str) -> None:
        self.mentor = mentor
        self.setup = lesson_packs.MentorSetup(mentor)

    def openers(self) -> list[str]:
        """Recorded phrases an answer can begin with, so its first sentence is ready."""
        ready = [text for group, text in load_phrases() if group in OPENER_GROUPS
                 and self.setup.sentence_ready(text)]
        return ready[:MAX_OPENERS]

    def lookup(self, sentence: str) -> dict | None:
        """How to play a sentence already in the bank, or None."""
        text = find(self.setup, sentence)
        if text is None:
            return None
        entry = _bank(self.mentor)["entries"].get(normalize(text))
        if isinstance(entry, dict):
            entry["uses"] = int(entry.get("uses") or 0) + 1
            _save(self.mentor)
        return self.setup.chunk(text)

    def keep(self, sentence: str, audio: str, video: str | None, voice: str) -> None:
        """Keep a sentence just made for a live answer, if it is in the mentor's own
        voice (not a stand-in) and has its lip-sync clip when the mentor has one."""
        if voice != self.setup.voice[0] or (self.setup.wants_video and not video):
            return
        try:
            base = self.setup.audio_path(sentence)
            os.makedirs(os.path.dirname(base), exist_ok=True)
            target = base + os.path.splitext(audio)[1]
            if not os.path.exists(target):
                shutil.copyfile(audio, target)
            if video and not os.path.exists(self.setup.video_file(sentence)):
                shutil.copyfile(video, self.setup.video_file(sentence))
            add(self.mentor, sentence, "live")
        except OSError as error:
            log_event("WARN", "Could not keep a sentence in the voice bank", error=short_error(error))


# ---------- recording the phrases ----------


def status(mentor: str) -> dict:
    """How far the mentor's phrases are recorded, for the Studio."""
    setup = lesson_packs.MentorSetup(mentor)
    phrases = load_phrases()
    ready = sum(setup.sentence_ready(text) for _group, text in phrases)
    building = mentor in _build["queue"]
    per_phrase = (sum(_build["seconds"]) / len(_build["seconds"]) if _build["seconds"]
                  else TYPICAL_PHRASE_SECONDS)
    entries = _bank(mentor)["entries"].values()
    return {
        "ready": ready,
        "total": len(phrases),
        "kept": sum(1 for entry in entries if isinstance(entry, dict)
                    and entry.get("source") == "live"),
        "building": building,
        "waiting_for_lessons": building and any(lesson_packs._queue),
        "doing": _build["doing"] if building and _build["queue"][0] == mentor else "",
        "seconds_left": round((len(phrases) - ready) * per_phrase) if building else 0,
        "error": _build["failed"].get(mentor, ""),
        "bytes": lesson_packs.folder_bytes(mentor),
    }


async def _record(mentor: str) -> None:
    setup = lesson_packs.MentorSetup(mentor)
    phrases = load_phrases()
    for number, (_group, text) in enumerate(phrases, start=1):
        if mentor not in _build["queue"]:
            return  # stopped
        if setup.sentence_ready(text):
            add(mentor, text, "phrase")
            continue
        while lesson_packs._queue:  # the learner's lessons come first
            await asyncio.sleep(LESSON_WAIT_SECONDS)
        async with talk.gpu_turn():  # a live question goes between two phrases
            _build["doing"] = f"Recording phrase {number} of {len(phrases)}"
            started = time.monotonic()
            if not await lesson_packs.render_sentence(setup, text):
                _build["failed"][mentor] = "The voice or lip-sync worker is not available."
                return
            _build["seconds"].append(time.monotonic() - started)
        add(mentor, text, "phrase")


async def _run() -> None:
    try:
        while _build["queue"]:
            mentor = _build["queue"][0]
            try:
                await _record(mentor)
            except Exception as error:  # one mentor's failure does not stop the others
                log_event("WARN", "Voice bank recording stopped", error=short_error(error))
                _build["failed"][mentor] = "Recording stopped unexpectedly."
            if _build["queue"] and _build["queue"][0] == mentor:
                _build["queue"].pop(0)
    finally:
        _build["doing"] = ""
        _build["task"] = None


def build(mentor: str) -> None:
    """Record the mentor's phrases in the background; recorded ones are skipped.
    Must be called while the event loop is running."""
    _build["failed"].pop(mentor, None)
    if mentor not in _build["queue"]:
        _build["queue"].append(mentor)
    if _build["task"] is None:
        _build["seconds"] = []
        _build["task"] = asyncio.get_running_loop().create_task(_run())


def stop(mentor: str) -> None:
    """Stop recording the mentor's phrases after the one in progress."""
    _build["queue"][:] = [name for name in _build["queue"] if name != mentor]
