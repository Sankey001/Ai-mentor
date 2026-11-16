"""Live conversation with a mentor.

The mentor answers in a few short sentences. Each sentence is voiced and
lip-synced while the next one is being voiced, and is sent to the app as soon
as it is ready, so the mentor starts talking before the whole answer exists.

Every step has a backup, so a failure lowers quality instead of breaking the
conversation:
  reply: Gemini -> Groq -> a fixed apology
  voice: mentor's cloned voice -> stock voice -> text only
  face:  lip-synced video -> audio over the mentor's idle clip
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import uuid
from typing import AsyncIterator

import backend.voices as voices
from backend import config
from backend.config import AI_PROVIDER, LLM_TIMEOUT_SECONDS, OLLAMA_MODEL
from backend.services import media_workers, mentor_pack
from backend.services.media_processor import BASE_DIR, generate_tts, get_mentor_clips
from backend.services.providers.base import (
    UNTRUSTED_INPUT_INSTRUCTION,
    mentor_persona_instruction,
)
from backend.services.providers.gemini_provider import generate_gemini_content
from backend.services.providers.groq_provider import generate_groq_content
from backend.services.providers.ollama_provider import (
    generate_ollama_content,
    release_ollama_model,
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


def split_sentences(text: str, max_chars: int = 200) -> list[str]:
    """Split text into sentences, cutting any sentence longer than ``max_chars``."""
    pieces = []
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        words = sentence.split()
        while words:
            piece = words.pop(0)
            while words and len(piece) + 1 + len(words[0]) <= max_chars:
                piece += " " + words.pop(0)
            pieces.append(piece)
    return pieces


def write_reply(
    mentor: str,
    message: str,
    history: list[dict],
    lesson: dict | None = None,
    choice: tuple[str, str] | None = None,
    think: bool = False,
) -> tuple[str, str]:
    """Return (reply, provider).

    ``choice`` is the learner's (provider, model) pick. Without one, the
    AI_PROVIDER model (Gemini or a local Ollama model) is tried, then Groq. A local
    pick is never sent to an online API, so the conversation stays on this
    computer. ``think`` lets a local thinking model reason first. When every
    attempt fails, a fixed apology is returned.
    """
    if lesson and not message:
        task = (
            "Teach the lesson named in the JSON to the learner in five to seven short "
            "spoken sentences: say what it is, why it matters, and give one everyday example."
        )
    else:
        task = (
            "Reply to the learner's message in one to three short spoken sentences. "
            "When the JSON names a lesson, the learner is studying it; answer in that context."
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

    def ollama(model: str, thinking: bool = False):
        # The reasoning counts against the token budget, so thinking gets far more room.
        return (
            "ollama",
            lambda: generate_ollama_content(
                system_instruction,
                user_data,
                max_tokens=THINKING_TOKENS if thinking else 400,
                model=model,
                think=thinking,
                timeout=THINKING_TIMEOUT_SECONDS if thinking else None,
            ),
        )

    gemini = ("gemini", lambda: generate_gemini_content(system_instruction, user_data).text)
    groq = (
        "groq",
        lambda: generate_groq_content(
            system_instruction, user_data, max_tokens=400, timeout=LLM_TIMEOUT_SECONDS
        )
        .choices[0]
        .message.content,
    )

    if choice is None:
        attempts = [ollama(OLLAMA_MODEL) if AI_PROVIDER == "ollama" else gemini, groq]
    elif choice[0] == "ollama":
        # Thinking can use up its budget before answering; then the same local
        # model answers without it, still on this computer.
        attempts = [ollama(choice[1], True), ollama(choice[1])] if think else [ollama(choice[1])]
    elif choice[0] == "groq":
        attempts = [groq, gemini]
    else:
        attempts = [gemini, groq]

    for provider, attempt in attempts:
        try:
            reply = " ".join((attempt() or "").split())
            if reply:
                return reply, provider
            raise ValueError("empty reply")
        except Exception as error:
            log_event("WARN", f"{provider} reply failed", error=short_error(error))
    return FALLBACK_REPLY, "fallback"


def _clean_old_sessions() -> None:
    if not os.path.isdir(TALK_DIR):
        return
    cutoff = time.time() - SESSION_MAX_AGE_SECONDS
    for entry in os.scandir(TALK_DIR):
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry.path, ignore_errors=True)


def _url(path: str) -> str:
    return f"{MEDIA_URL}/{os.path.relpath(path, TALK_DIR).replace(os.sep, '/')}"


async def talk(
    mentor: str,
    message: str,
    history: list[dict],
    lesson: dict | None = None,
    choice: tuple[str, str] | None = None,
    think: bool = False,
) -> AsyncIterator[dict]:
    """Yield the reply text, then one chunk per spoken sentence, then "done".

    ``choice`` and ``think`` are the learner's picks; see ``write_reply``.
    """
    _clean_old_sessions()
    session_dir = os.path.join(TALK_DIR, uuid.uuid4().hex)
    os.makedirs(session_dir)

    reply, provider = await asyncio.to_thread(
        write_reply, mentor, message, history, lesson, choice, think
    )
    yield {"type": "reply", "text": reply, "provider": provider}

    # On one GPU, a local model left loaded starves the voice and face workers
    # (8 GB holds all three only barely). Unload it while they make the media.
    if provider == "ollama" and (config.VOICE_WORKER_URL or config.FACE_WORKER_URL):
        model = choice[1] if choice and choice[0] == "ollama" else OLLAMA_MODEL
        await asyncio.to_thread(release_ollama_model, model)

    teaching = bool(lesson) and not message
    sentences = split_sentences(reply)[: MAX_LESSON_SENTENCES if teaching else MAX_REPLY_SENTENCES]
    clips = get_mentor_clips(mentor)
    voice_sample = mentor_pack.get_mentor_voice_sample(mentor)
    expressiveness = mentor_pack.get_mentor_profile(mentor)["expressiveness"]
    stock_voice, rate, pitch = voices.resolve_mentor_voice(
        mentor, None, None, None, pack_voice=mentor_pack.get_mentor_stock_voice(mentor)
    )

    async def make_voice(index: int, sentence: str) -> tuple[str | None, str]:
        cloned = os.path.join(session_dir, f"{index}.wav")
        if voice_sample and await media_workers.speak(
            sentence, voice_sample, cloned, expressiveness=expressiveness
        ):
            return cloned, "cloned"
        stock = os.path.join(session_dir, f"{index}.mp3")
        try:
            await generate_tts(sentence, stock, voice_id=stock_voice, rate=rate, pitch=pitch)
            if os.path.exists(stock):
                return stock, "stock"
        except Exception as error:
            log_event("WARN", "Stock voice failed; sending text only", error=short_error(error))
        return None, "none"

    # Producer-consumer: the next sentence is voiced while this one is lip-synced.
    ready: asyncio.Queue = asyncio.Queue(maxsize=2)

    async def produce() -> None:
        try:
            for index, sentence in enumerate(sentences):
                await ready.put((index, sentence, *await make_voice(index, sentence)))
        finally:
            await ready.put(None)

    producer = asyncio.create_task(produce())
    try:
        while (item := await ready.get()) is not None:
            index, sentence, audio, voice = item
            chunk = {"type": "chunk", "index": index, "text": sentence, "voice": voice}
            if audio is None:
                chunk.update(kind="text", face="none")
            else:
                video = os.path.join(session_dir, f"{index}.mp4")
                if clips and await media_workers.animate(clips, audio, video, timeout=120):
                    chunk.update(kind="video", face="lipsync", url=_url(video))
                else:
                    chunk.update(kind="audio", face="idle", url=_url(audio))
            yield chunk
    finally:
        producer.cancel()
    yield {"type": "done"}
