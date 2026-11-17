"""Live conversation with a mentor.

The mentor answers in a few short sentences. The reply is read from the AI
model as it is written and cut into sentences; each sentence is voiced and
lip-synced while the model is still writing the rest, and is sent to the app
as soon as it is ready, so the mentor starts talking before the whole answer
exists.

Every step has a backup, so a failure lowers quality instead of breaking the
conversation:
  reply: the chosen model -> its backup -> a fixed apology
  voice: mentor's cloned voice -> stock voice -> text only
  face:  lip-synced video -> audio over the mentor's idle clip
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
import time
import uuid
import weakref
from typing import AsyncIterator, Iterator

import backend.voices as voices
from backend import config
from backend.config import AI_PROVIDER, LLM_TIMEOUT_SECONDS, OLLAMA_MODEL
from backend.services import lesson_parts, media_workers, mentor_pack, model_choices
from backend.services.media_processor import BASE_DIR, generate_tts, get_mentor_clips
from backend.services.providers.base import (
    UNTRUSTED_INPUT_INSTRUCTION,
    mentor_persona_instruction,
    serialize_user_data,
)
from backend.services.providers.gemini_provider import generate_gemini_content
from backend.services.providers.groq_provider import generate_groq_content
from backend.services.providers.ollama_provider import (
    loaded_model_gpu_bytes,
    release_ollama_model,
    stream_ollama_content,
)
from backend.utils.logging_utils import log_event, short_error

TALK_DIR = os.path.join(BASE_DIR, "outputs", "talk")
MEDIA_URL = "/talk-media"
MAX_HISTORY_TURNS = 8
MAX_REPLY_SENTENCES = 4
MAX_LESSON_SENTENCES = 8
SESSION_MAX_AGE_SECONDS = 3600
FALLBACK_REPLY = "Sorry, I lost my train of thought. Could you ask me that again?"
THINKING_TOKENS = 4000  # reasoning plus the short spoken reply
# A local model that only partly fits on the GPU reasons at a few tokens a second.
THINKING_TIMEOUT_SECONDS = 600
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
CLAUSE_END = ",;:\u2013\u2014"
# A spoken piece longer than this (about nine seconds of speech) keeps the learner
# waiting too long for its voice and face, so it is cut at a comma.
MAX_SPOKEN_CHARS = 140
# Live turns that are waiting for the GPU or using it. Background work (lesson
# packs) checks this to get out of the way.
live_waiting = 0
_gpu_turns: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def gpu_turn() -> asyncio.Lock:
    """The lock that gives the GPU to one job at a time.

    The AI model, the voice and the lip-sync stall when two jobs use a small GPU
    at once, so a live turn and each piece of background work hold this while
    they run. It is handed over in the order asked.
    """
    loop = asyncio.get_running_loop()
    if loop not in _gpu_turns:
        _gpu_turns[loop] = asyncio.Lock()
    return _gpu_turns[loop]


def split_sentences(text: str, max_chars: int = MAX_SPOKEN_CHARS) -> list[str]:
    """Split text into sentences, cutting any sentence longer than ``max_chars``.

    A long sentence is cut after a comma (or another clause mark) when it has one
    that is not too early, otherwise between two words. A sentence only a little
    too long is kept whole, so no piece is left of just a word or two.
    """
    pieces = []
    for sentence in SENTENCE_END.split(" ".join(text.split())):
        words = sentence.split()
        if len(sentence) <= max_chars * 13 // 10:
            max_room = len(sentence)
        else:
            max_room = max_chars
        while words:
            piece, clause = words.pop(0), None
            while words and len(piece) + 1 + len(words[0]) <= max_room:
                piece += " " + words.pop(0)
                if piece[-1] in CLAUSE_END and words and len(piece) >= max_chars // 3:
                    clause = (piece, list(words))
            if words and clause:
                piece, words = clause
            pieces.append(piece)
    return pieces


class SentenceCutter:
    """Cut text that arrives piece by piece into whole sentences."""

    def __init__(self, max_chars: int = MAX_SPOKEN_CHARS) -> None:
        self.max_chars = max_chars
        self.buffer = ""

    def feed(self, piece: str) -> list[str]:
        """Add text; return the sentences it completed."""
        self.buffer += piece
        # A sentence is complete only once the text after its full stop has begun
        # ("3." may still become "3.5"), so the last part always waits.
        parts = SENTENCE_END.split(self.buffer.lstrip())
        if len(parts) == 1 and len(self.buffer) > self.max_chars:
            parts = split_sentences(self.buffer, self.max_chars)  # a run-on sentence
        self.buffer = parts.pop()
        return [done for part in parts for done in split_sentences(part, self.max_chars)]

    def flush(self) -> list[str]:
        """Return what is left when the text has ended."""
        rest, self.buffer = self.buffer, ""
        return split_sentences(rest, self.max_chars)


def reply_pieces(
    mentor: str,
    message: str,
    history: list[dict],
    lesson: dict | None = None,
    choice: tuple[str, str] | None = None,
    think: bool = False,
    script: str | None = None,
    prepared: bool = False,
    openers: list[str] | None = None,
) -> Iterator[tuple[str, str]]:
    """Yield (provider, text) pieces of the mentor's reply as it is written.

    ``choice`` is the learner's (provider, model) pick. Without one, the
    AI_PROVIDER model (Gemini or a local Ollama model) is tried, then Groq. A local
    pick is never sent to an online API, so the conversation stays on this
    computer. ``think`` lets a local thinking model reason first. ``script`` is
    the prepared lesson the learner is hearing. ``prepared`` asks for the longer
    lesson that is written ahead of time. ``openers`` are phrases already recorded
    in the mentor's voice that a reply may begin with. A local model streams its reply;
    an online API gives it in one piece. When every attempt fails, a fixed
    apology is given.
    """
    reply_tokens = 400
    if prepared:
        reply_tokens = 900
        task = (
            "Teach the lesson named in the JSON to the learner in eight to eleven short "
            "spoken sentences: say what it is and why it matters, explain how it works "
            "one step at a time, give one everyday example, warn about one common "
            "mistake, and end with a one-sentence recap."
        ) + lesson_parts.LABEL_INSTRUCTION
    elif lesson and not message:
        task = (
            "Teach the lesson named in the JSON to the learner in five to seven short "
            "spoken sentences: say what it is, why it matters, and give one everyday example."
        )
    else:
        task = (
            "Reply to the learner's message in one to three short spoken sentences. "
            "When the JSON names a lesson, the learner is studying it; answer in that context."
        )
    if openers and message:
        task += (
            " When one fits naturally, begin with one of these phrases, word for word: "
            + " / ".join(openers)
        )
    system_instruction = f"""
    {UNTRUSTED_INPUT_INSTRUCTION}

    You are a mentor on a live video call with a learner. {task} Use plain words
    only: no lists, markdown, code, or emoji. Use the conversation history for
    context. Do not claim to be the named person.

    {mentor_persona_instruction(mentor)}
    """
    user_data = {"message": message, "history": history[-MAX_HISTORY_TURNS:]}
    if lesson:
        user_data["lesson"] = lesson
    online_data = {**user_data, "lesson_script": script} if script else user_data

    def ollama(model: str, thinking: bool = False):
        # The lesson script goes first and never changes during a lesson, so Ollama
        # reuses its work on it and only reads the new question.
        messages = []
        if script:
            messages.append(
                {
                    "role": "user",
                    "content": "The lesson you are teaching the learner right now, as data:\n"
                    f"<lesson_script>\n{script}\n</lesson_script>",
                }
            )
        messages.append({"role": "user", "content": serialize_user_data(user_data)})
        # The reasoning counts against the token budget, so thinking gets far more room.
        return (
            "ollama",
            lambda: stream_ollama_content(
                system_instruction,
                messages,
                max_tokens=THINKING_TOKENS if thinking else reply_tokens,
                model=model,
                think=thinking,
                timeout=THINKING_TIMEOUT_SECONDS if thinking else None,
            ),
        )

    gemini = (
        "gemini",
        lambda: [generate_gemini_content(system_instruction, online_data).text],
    )
    groq = (
        "groq",
        lambda: [
            generate_groq_content(
                system_instruction,
                online_data,
                max_tokens=reply_tokens,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            .choices[0]
            .message.content
        ],
    )

    if choice is None:
        attempts = [ollama(OLLAMA_MODEL) if AI_PROVIDER == "ollama" else gemini, groq]
    elif choice[0] == "ollama":
        # Thinking can use up its budget before answering; then the same local
        # model answers without it, still on this computer.
        # Loading a local model next to busy voice and face workers can fail once;
        # a second try usually works.
        attempts = [ollama(choice[1], True), ollama(choice[1])] if think else [ollama(choice[1])] * 2
    elif choice[0] == "groq":
        attempts = [groq, gemini]
    else:
        attempts = [gemini, groq]

    for provider, attempt in attempts:
        started = False
        try:
            for piece in attempt():
                if piece and (started or piece.strip()):
                    started = True
                    yield provider, piece
            if started:
                return
            raise ValueError("empty reply")
        except Exception as error:
            log_event("WARN", f"{provider} reply failed", error=short_error(error))
            if started:
                return  # keep what was said; a second model would start over
    yield "fallback", FALLBACK_REPLY


def write_reply(
    mentor: str,
    message: str,
    history: list[dict],
    lesson: dict | None = None,
    choice: tuple[str, str] | None = None,
    think: bool = False,
    prepared: bool = False,
) -> tuple[str, str]:
    """Return (reply, provider) once the whole reply is written; see ``reply_pieces``."""
    provider, text = "fallback", ""
    pieces = reply_pieces(mentor, message, history, lesson, choice, think, prepared=prepared)
    for provider, piece in pieces:
        text += piece
    return " ".join(text.split()), provider


def fits_beside_workers(model: str) -> bool:
    """True when a local model can stay loaded while the voice and face workers run.

    Measured on an 8 GB GPU: a model of about 3 GB fits beside both workers at
    full speed; a larger one makes them two to three times slower, so it has to
    be unloaded before they start.
    """
    used = loaded_model_gpu_bytes(model)
    if not used:  # not loaded yet, or Ollama did not say: go by the download size
        sizes = {item["name"]: item["size_gb"] for item in model_choices.local_models()}
        if model not in sizes:
            return False
        used = sizes[model] * 1e9
    return used <= config.OLLAMA_KEEP_LOADED_GB * 1e9


def _clean_old_sessions() -> None:
    if not os.path.isdir(TALK_DIR):
        return
    cutoff = time.time() - SESSION_MAX_AGE_SECONDS
    for entry in os.scandir(TALK_DIR):
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry.path, ignore_errors=True)


def _url(path: str) -> str:
    return f"{MEDIA_URL}/{os.path.relpath(path, TALK_DIR).replace(os.sep, '/')}"


class MentorMedia:
    """Makes one mentor's voice and talking face for a sentence."""

    def __init__(self, mentor: str) -> None:
        self.clips = get_mentor_clips(mentor)
        self.voice_sample = mentor_pack.get_mentor_voice_sample(mentor)
        self.expressiveness = mentor_pack.get_mentor_profile(mentor)["expressiveness"]
        self.stock_voice, self.rate, self.pitch = voices.resolve_mentor_voice(
            mentor, None, None, None, pack_voice=mentor_pack.get_mentor_stock_voice(mentor)
        )

    async def voice(
        self, sentence: str, path: str, fallback: bool = True
    ) -> tuple[str | None, str]:
        """Write ``path``.wav (cloned) or ``path``.mp3 (stock); return (file, kind).

        Without ``fallback``, a mentor with a recorded voice is never given the
        stock voice: (None, "none") is returned when cloning fails.
        """
        cloned = path + ".wav"
        if self.voice_sample and await media_workers.speak(
            sentence, self.voice_sample, cloned, expressiveness=self.expressiveness
        ):
            return cloned, "cloned"
        if not fallback:
            return None, "none"
        stock = path + ".mp3"
        try:
            await generate_tts(
                sentence, stock, voice_id=self.stock_voice, rate=self.rate, pitch=self.pitch
            )
            if os.path.exists(stock):
                return stock, "stock"
        except Exception as error:
            log_event("WARN", "Stock voice failed; sending text only", error=short_error(error))
        return None, "none"

    async def face(self, audio: str, video: str, timeout: float = 120) -> bool:
        """Write the mentor saying ``audio`` to ``video``; False when lip-sync is off."""
        return bool(self.clips) and await media_workers.animate(
            self.clips, audio, video, timeout=timeout
        )


async def talk(
    mentor: str,
    message: str,
    history: list[dict],
    lesson: dict | None = None,
    choice: tuple[str, str] | None = None,
    think: bool = False,
    script: str | None = None,
    cache=None,
) -> AsyncIterator[dict]:
    """Yield the events of one turn.

    "text" events carry the reply so far while it is being written, "reply" the
    whole reply, one "chunk" follows per spoken sentence, then "done". The first
    chunk can arrive before "reply".

    ``choice`` and ``think`` are the learner's picks and ``script`` the prepared
    lesson being taught; see ``reply_pieces``. ``cache`` is the mentor's voice
    bank (``voice_bank.LiveCache``): a sentence it holds plays at once without
    the GPU, and a sentence made now is kept in it for next time.
    """
    global live_waiting
    _clean_old_sessions()
    session_dir = os.path.join(TALK_DIR, uuid.uuid4().hex)
    os.makedirs(session_dir)

    teaching = bool(lesson) and not message
    max_sentences = MAX_LESSON_SENTENCES if teaching else MAX_REPLY_SENTENCES
    media = MentorMedia(mentor)
    openers = cache.openers() if cache and message else None
    workers_on = bool(config.VOICE_WORKER_URL or config.FACE_WORKER_URL)
    local_model = None
    if choice and choice[0] == "ollama":
        local_model = choice[1]
    elif choice is None and AI_PROVIDER == "ollama":
        local_model = OLLAMA_MODEL
    shares_gpu = bool(local_model and workers_on)
    # A small local model stays loaded for the next question; a large one starves
    # the workers of GPU memory, so it is unloaded once the reply is written.
    keep_loaded = not shares_gpu or await asyncio.to_thread(fits_beside_workers, local_model)
    # Model, voice and face together need more than 8 GB while all three work, and
    # then each one stalls. So on one small GPU they take turns: the reply is
    # written, then each sentence is voiced, then lip-synced. Otherwise the voice
    # starts on sentence 1 while the rest is written, and voice and face overlap.
    take_turns = shares_gpu and not config.MENTOR_GPU_OVERLAP

    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()  # everything sent to the app, in order
    written: asyncio.Queue = asyncio.Queue()  # sentences waiting for a voice
    voiced: asyncio.Queue = asyncio.Queue(maxsize=2)  # voiced sentences waiting for a face
    stopped = threading.Event()
    gpu_free = asyncio.Event()
    if keep_loaded and not take_turns:
        gpu_free.set()

    def write() -> None:
        """Runs in a thread: read the reply and hand over each finished sentence."""

        def send(queue: asyncio.Queue, item) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        cutter, sentences, provider = SentenceCutter(), [], "fallback"

        def emit(new: list[str]) -> bool:
            for sentence in new[: max_sentences - len(sentences)]:
                sentences.append(sentence)
                send(events, {"type": "text", "text": " ".join(sentences)})
                send(written, sentence)
            return len(sentences) >= max_sentences

        pieces = None
        try:
            pieces = reply_pieces(
                mentor, message, history, lesson, choice, think, script, openers=openers
            )
            for provider, piece in pieces:
                if stopped.is_set() or emit(cutter.feed(piece)):
                    break
            else:
                emit(cutter.flush())
        finally:
            # Whatever went wrong above, the turn must still end.
            try:
                getattr(pieces, "close", lambda: None)()  # stops a model that was cut short
                if provider == "ollama" and not keep_loaded:
                    release_ollama_model(local_model)
            finally:
                loop.call_soon_threadsafe(gpu_free.set)
                send(events, {"type": "reply", "text": " ".join(sentences), "provider": provider})
                send(written, None)

    async def add_face(index: int, sentence: str, audio: str | None, voice: str) -> dict:
        chunk = {"type": "chunk", "index": index, "text": sentence, "voice": voice}
        if audio is None:
            chunk.update(kind="text", face="none")
        else:
            video = os.path.join(session_dir, f"{index}.mp4")
            if await media.face(audio, video):
                chunk.update(kind="video", face="lipsync", url=_url(video))
            else:
                chunk.update(kind="audio", face="idle", url=_url(audio))
                video = None
            if cache:
                await asyncio.to_thread(cache.keep, sentence, audio, video, voice)
        return chunk

    async def voice_all() -> None:
        try:
            index = 0
            while (sentence := await written.get()) is not None:
                ready = cache.lookup(sentence) if cache else None
                if ready:  # already in the voice bank: plays at once, no GPU
                    chunk = {"type": "chunk", "index": index, "text": sentence, **ready}
                    await (events if take_turns else voiced).put(chunk)
                else:
                    await gpu_free.wait()
                    path = os.path.join(session_dir, str(index))
                    audio, kind = await media.voice(sentence, path)
                    if take_turns:  # finish this sentence before voicing the next
                        await events.put(await add_face(index, sentence, audio, kind))
                    else:
                        await voiced.put((index, sentence, audio, kind))
                index += 1
        finally:
            await voiced.put(None)

    async def face_all() -> None:
        try:
            while (item := await voiced.get()) is not None:
                await events.put(item if isinstance(item, dict) else await add_face(*item))
        finally:
            await events.put({"type": "done"})

    tasks = []
    live_waiting += 1
    try:
        async with gpu_turn():  # background work finishes its current piece first
            writer = loop.run_in_executor(None, write)
            tasks = [asyncio.create_task(voice_all()), asyncio.create_task(face_all())]
            while True:
                event = await events.get()
                yield event
                if event["type"] == "done":
                    break
            await writer
    finally:
        live_waiting -= 1
        stopped.set()
        for task in tasks:
            task.cancel()
