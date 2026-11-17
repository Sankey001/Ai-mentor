"""Lesson packs: a lesson written, voiced and lip-synced ahead of time.

A pack is a script plus one audio file and one talking clip per sentence. Making
it uses the GPU, in the background; playing it uses none, and starts at once.

Everything is stored under outputs/lesson_packs/<mentor>/ and named after what
it was made from, so a change re-makes only what it touches:

    scripts/<key>.json  key = lesson + the mentor's personality + the writing model
    media/<key>.wav     key = sentence + the mentor's voice
    media/<key>.mp4     key = that audio + the mentor's clips

A new personality rewrites scripts; sentences that come out the same keep their
audio. A new voice re-voices everything; new photos only re-make the clips.
Finished files are never made again, so preparation can stop and resume.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time

from backend import config
from backend.services import media_processor, mentor_pack, model_choices, talk
from backend.services.providers.ollama_provider import load_ollama_model, release_ollama_model
from backend.utils.logging_utils import log_event, short_error

MEDIA_URL = "/pack-media"
MEDIA_FILE = re.compile(r"^[0-9a-f]{16}\.(?:wav|mp3|mp4)$")
SCRIPT_VERSION = 1
MAX_SCRIPT_SENTENCES = 12
MIN_SCRIPT_SENTENCES = 3
FACE_TIMEOUT_SECONDS = 600
# Used for the time estimate until this run has measured its own.
TYPICAL_SCRIPT_SECONDS = 40.0
TYPICAL_SENTENCE_SECONDS = 7.0
TYPICAL_SENTENCES = 10
# Scripts are written a few lessons at a time, then recorded, so the first lessons
# of a long course are ready early and the writing model is not swapped in and
# out of the GPU for every lesson.
LESSONS_PER_ROUND = 6
MODEL_CHECK_SECONDS = 30

_queue: list[dict] = []  # lessons waiting, in order: {"mentor", "lesson", "urgent"}
_failed: dict[tuple[str, str], str] = {}  # (mentor, lesson id) -> why it could not be made
_job = {"task": None, "doing": "", "script_seconds": [], "sentence_seconds": [], "mentors": set()}
_model = {"checked": float("-inf"), "value": (None, "online")}


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]


def _stamp(path: str) -> list:
    stat = os.stat(path)
    return [os.path.basename(path), stat.st_size, stat.st_mtime_ns]


def mentor_dir(mentor: str) -> str:
    return os.path.join(media_processor.BASE_DIR, "outputs", "lesson_packs", mentor)


def _clean_lesson(lesson: dict) -> dict:
    return {name: str(lesson.get(name) or "").strip() for name in ("course", "module", "title")}


def lesson_id(lesson: dict) -> str:
    """Identify a lesson by its course, module and title."""
    return _key(_clean_lesson(lesson))


def script_model() -> tuple[tuple[str, str] | None, str]:
    """Return (choice, label) of the model that writes lesson scripts.

    Scripts are written ahead of time, so the better local model
    (LESSON_SCRIPT_MODEL) is used when it is installed, however slow it is.
    """
    if config.AI_PROVIDER != "ollama":
        return None, "online"
    if time.monotonic() - _model["checked"] > MODEL_CHECK_SECONDS:  # asking Ollama is slow
        _model["value"] = (None, "online")
        installed = {item["name"] for item in model_choices.local_models()}
        for name in (config.LESSON_SCRIPT_MODEL, config.OLLAMA_MODEL):
            if name in installed:
                _model["value"] = (("ollama", name), f"ollama:{name}")
                break
        _model["checked"] = time.monotonic()
    return _model["value"]


def _script_path(mentor: str, lesson: dict, persona: str, model_label: str) -> str:
    name = _key(SCRIPT_VERSION, _clean_lesson(lesson), persona, model_label)
    return os.path.join(mentor_dir(mentor), "scripts", f"{name}.json")


def _read_sentences(path: str) -> list[str] | None:
    """The sentences of a saved script, or None when there is no usable one."""
    try:
        with open(path, encoding="utf-8") as script_file:
            sentences = json.load(script_file)["sentences"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(sentences, list) and all(isinstance(s, str) and s for s in sentences):
        return sentences
    return None


class MentorSetup:
    """What a mentor's packs are made from: voice, clips, personality, writing model."""

    def __init__(self, mentor: str) -> None:
        self.mentor = mentor
        self.media = talk.MentorMedia(mentor)
        self.persona = mentor_pack.get_mentor_profile(mentor)["persona"]
        self.model_label = script_model()[1]
        self.folder = mentor_dir(mentor)
        if self.media.voice_sample:
            self.voice = ["cloned", _stamp(self.media.voice_sample), self.media.expressiveness]
        else:
            self.voice = ["stock", self.media.stock_voice, self.media.rate, self.media.pitch]
        self.clips = [_stamp(clip) for clip in self.media.clips]
        # Without a face worker a pack is audio only; with one, it needs its clips.
        self.wants_video = bool(config.FACE_WORKER_URL and self.media.clips)


class Pack:
    """Where one mentor's version of one lesson is stored, and how far it is."""

    def __init__(self, mentor: str | MentorSetup, lesson: dict) -> None:
        setup = mentor if isinstance(mentor, MentorSetup) else MentorSetup(mentor)
        self.mentor, self.media, self.folder = setup.mentor, setup.media, setup.folder
        self.voice, self.clips, self.wants_video = setup.voice, setup.clips, setup.wants_video
        self.model_label = setup.model_label
        self.lesson = _clean_lesson(lesson)
        self.id = lesson_id(lesson)
        self.script_path = _script_path(
            self.mentor, self.lesson, setup.persona, self.model_label
        )

    def sentences(self) -> list[str] | None:
        """The script's sentences, or None when it is not written yet."""
        return _read_sentences(self.script_path)

    def audio_path(self, sentence: str) -> str:
        """Where the sentence's audio goes, without its extension (.wav or .mp3)."""
        return os.path.join(self.folder, "media", _key(sentence, self.voice))

    def audio_file(self, sentence: str) -> str | None:
        base = self.audio_path(sentence)
        return next((base + ext for ext in (".wav", ".mp3") if os.path.isfile(base + ext)), None)

    def video_file(self, sentence: str) -> str:
        name = _key(os.path.basename(self.audio_path(sentence)), self.clips)
        return os.path.join(self.folder, "media", f"{name}.mp4")

    def sentence_ready(self, sentence: str) -> bool:
        if not self.audio_file(sentence):
            return False
        return not self.wants_video or os.path.isfile(self.video_file(sentence))

    def progress(self) -> tuple[int, int | None]:
        """Return (sentences ready, sentences in the script or None without a script)."""
        sentences = self.sentences()
        if sentences is None:
            return 0, None
        return sum(self.sentence_ready(sentence) for sentence in sentences), len(sentences)

    def state(self) -> str:
        done, total = self.progress()
        if total is not None and done == total:
            return "ready"
        if (self.mentor, self.id) in _failed:
            return "failed"
        if any(item["mentor"] == self.mentor and item["id"] == self.id for item in _queue):
            return "preparing"
        return "missing"

    def playable(self) -> dict:
        """What the classroom needs to play the lesson; "sentences" only when ready."""
        state, sentences = self.state(), self.sentences() or []
        result = {"id": self.id, "state": state, "model": self.model_label}
        if state != "ready":
            return result
        voice = self.voice[0]
        result["sentences"] = []
        for sentence in sentences:
            item = {"text": sentence, "voice": voice}
            if self.wants_video:
                path = self.video_file(sentence)
                item.update(kind="video", face="lipsync")
            else:
                path = self.audio_file(sentence)
                item.update(kind="audio", face="idle")
            item["url"] = f"{MEDIA_URL}/{self.mentor}/{os.path.basename(path)}"
            result["sentences"].append(item)
        return result


def script_text(mentor: str, lesson: dict) -> str | None:
    """The prepared script of a lesson, for answering questions about it."""
    persona = mentor_pack.get_mentor_profile(mentor)["persona"]
    sentences = _read_sentences(_script_path(mentor, lesson, persona, script_model()[1]))
    return " ".join(sentences) if sentences else None


def media_path(mentor: str, filename: str) -> str | None:
    """The file behind a pack URL, or None when there is no such file."""
    if not MEDIA_FILE.fullmatch(filename):
        return None
    path = os.path.join(mentor_dir(mentor), "media", filename)
    return path if os.path.isfile(path) else None


def folder_bytes(mentor: str) -> int:
    total = 0
    for folder, _dirs, files in os.walk(mentor_dir(mentor)):
        total += sum(os.path.getsize(os.path.join(folder, name)) for name in files)
    return total


def delete_mentor_packs(mentor: str) -> None:
    _queue[:] = [item for item in _queue if item["mentor"] != mentor]
    shutil.rmtree(mentor_dir(mentor), ignore_errors=True)


# ---------- making packs ----------


def write_script(pack: Pack, choice: tuple[str, str] | None) -> bool | None:
    """Write and save the lesson's script; False when no model could write it.

    Gives up and returns None as soon as a learner asks something: the script can
    be written again later, the learner should not wait for it.
    """
    reply, provider = "", "fallback"
    pieces = talk.reply_pieces(pack.mentor, "", [], pack.lesson, choice, prepared=True)
    try:
        for provider, piece in pieces:
            if talk.live_waiting:
                return None
            reply += piece
    finally:
        pieces.close()  # stops a local model that was cut short
    sentences = talk.split_sentences(reply)[:MAX_SCRIPT_SENTENCES]
    if provider == "fallback" or len(sentences) < MIN_SCRIPT_SENTENCES:
        return False
    os.makedirs(os.path.dirname(pack.script_path), exist_ok=True)
    temp = pack.script_path + ".tmp"
    with open(temp, "w", encoding="utf-8") as script_file:
        json.dump(
            {"lesson": pack.lesson, "model": pack.model_label, "sentences": sentences},
            script_file,
            ensure_ascii=False,
            indent=1,
        )
    os.replace(temp, pack.script_path)
    return True


async def render_sentence(pack: Pack, sentence: str) -> bool:
    """Make the sentence's audio and clip unless they exist; False if a worker failed."""
    os.makedirs(os.path.join(pack.folder, "media"), exist_ok=True)
    audio = pack.audio_file(sentence)
    if not audio:
        base = pack.audio_path(sentence)
        made, _kind = await pack.media.voice(
            sentence, base + ".tmp", fallback=not pack.media.voice_sample
        )
        if not made:
            return False
        audio = base + os.path.splitext(made)[1]
        os.replace(made, audio)
    if pack.wants_video and not os.path.isfile(pack.video_file(sentence)):
        video = pack.video_file(sentence)
        temp = video[:-4] + ".tmp.mp4"
        if not await pack.media.face(audio, temp, timeout=FACE_TIMEOUT_SECONDS):
            return False
        os.replace(temp, video)
    return True


def _average(samples: list[float], typical: float) -> float:
    return sum(samples) / len(samples) if samples else typical


def seconds_left() -> int:
    """Estimate how long the waiting lessons still need."""
    script = _average(_job["script_seconds"], TYPICAL_SCRIPT_SECONDS)
    sentence = _average(_job["sentence_seconds"], TYPICAL_SENTENCE_SECONDS)
    total = 0.0
    for item in list(_queue):
        done, count = Pack(item["mentor"], item["lesson"]).progress()
        total += (count - done) * sentence if count else script + TYPICAL_SENTENCES * sentence
    return round(total)


async def _write_scripts(batch: list[dict]) -> None:
    choice, _label = script_model()
    wrote_with_local_model = False
    for item in batch:
        pack = Pack(item["mentor"], item["lesson"])
        if item not in _queue or pack.sentences() is not None:
            continue
        written = None
        while written is None:  # None: a learner's question interrupted it; try again
            async with talk.gpu_turn():
                _job["doing"] = f"Writing “{pack.lesson['title']}”"
                started = time.monotonic()
                if await _swaps_models(choice):
                    # Two local models at once do not fit in memory.
                    await asyncio.to_thread(release_ollama_model, config.OLLAMA_MODEL)
                try:
                    written = await asyncio.to_thread(write_script, pack, choice)
                except Exception as error:
                    log_event("WARN", "Lesson script failed", error=short_error(error))
                    written = False
                if written is None:
                    await _make_room(choice)  # before the question is answered
            await asyncio.sleep(0)  # the learner's question takes the GPU first
        if written:
            _job["script_seconds"].append(time.monotonic() - started)
            wrote_with_local_model = wrote_with_local_model or bool(choice)
        else:
            _failed[(pack.mentor, pack.id)] = "The AI model could not write this lesson."
    if wrote_with_local_model:
        async with talk.gpu_turn():
            await _make_room(choice)


async def _swaps_models(choice: tuple[str, str] | None) -> bool:
    """True when scripts are written by a local model too large to stay loaded
    next to the one that answers questions and the voice and face workers."""
    if not choice or choice[1] == config.OLLAMA_MODEL or config.AI_PROVIDER != "ollama":
        return False
    return not await asyncio.to_thread(talk.fits_beside_workers, choice[1])


async def _make_room(choice: tuple[str, str] | None) -> None:
    """Take a large writing model off the GPU, and bring back the one that answers
    questions, before the voice and face workers or a live question need the GPU."""
    if await _swaps_models(choice):
        await asyncio.to_thread(release_ollama_model, choice[1])
        await asyncio.to_thread(load_ollama_model, config.OLLAMA_MODEL)


async def _render(item: dict) -> None:
    pack = Pack(item["mentor"], item["lesson"])
    for number, sentence in enumerate(pack.sentences() or [], start=1):
        if pack.sentence_ready(sentence):
            continue
        async with talk.gpu_turn():  # a learner's question goes between two sentences
            _job["doing"] = f"Recording “{pack.lesson['title']}” ({number})"
            started = time.monotonic()
            if not await render_sentence(pack, sentence):
                _failed[(pack.mentor, pack.id)] = "The voice or lip-sync worker is not available."
                return
            _job["sentence_seconds"].append(time.monotonic() - started)


async def _run() -> None:
    """Work through the queue: the lessons the learner is on first, then the rest."""
    try:
        while _queue:
            urgent = [item for item in _queue if item["urgent"]]
            batch = urgent or _queue[:LESSONS_PER_ROUND]
            await _write_scripts(batch)
            for item in batch:
                if item in _queue and (item["mentor"], item["id"]) not in _failed:
                    await _render(item)
                if item in _queue:  # still there unless it was cancelled meanwhile
                    _queue.remove(item)
                if not urgent and any(waiting["urgent"] for waiting in _queue):
                    break  # the learner opened another lesson: do that one first
        while _job["mentors"]:
            prune(_job["mentors"].pop())
    except Exception as error:  # never leave the queue stuck behind a crash
        log_event("WARN", "Lesson pack preparation stopped", error=short_error(error))
        _queue.clear()
    finally:
        _job["doing"] = ""
        _job["task"] = None


def prepare(mentor: str, lessons: list[dict], urgent: bool = False) -> None:
    """Queue lessons to be prepared in the background; ready ones are skipped.

    ``urgent`` lessons (the one on screen) go before everything else. The lessons
    asked for last go before those already waiting, in the order given, so the
    lessons that follow the one on screen are not stuck behind an older request.
    Must be called while the event loop is running.
    """
    setup, asked = MentorSetup(mentor), []
    for lesson in lessons:
        pack = Pack(setup, lesson)
        _failed.pop((mentor, pack.id), None)  # asking again is how a failure is retried
        if pack.state() == "ready":
            continue
        waiting = next(
            (item for item in _queue if item["mentor"] == mentor and item["id"] == pack.id), None
        )
        if waiting:
            waiting["urgent"] = waiting["urgent"] or urgent
        else:
            waiting = {"mentor": mentor, "id": pack.id, "lesson": pack.lesson, "urgent": urgent}
            _queue.append(waiting)
            _job["mentors"].add(mentor)
        if waiting not in asked:
            asked.append(waiting)
    _queue[:] = asked + [item for item in _queue if item not in asked]
    if _queue and _job["task"] is None:
        _job["script_seconds"], _job["sentence_seconds"] = [], []
        _job["task"] = asyncio.get_running_loop().create_task(_run())


def cancel(mentor: str) -> None:
    """Stop preparing a mentor's lessons after the sentence in progress."""
    _queue[:] = [item for item in _queue if item["mentor"] != mentor]


def status(mentor: str, lessons: list[dict]) -> dict:
    """How far the mentor's packs for these lessons are, for the progress display."""
    states, setup = [], MentorSetup(mentor)
    for lesson in lessons:  # answered in the order asked
        pack = Pack(setup, lesson)
        states.append(
            {"id": pack.id, "state": pack.state(), "error": _failed.get((mentor, pack.id), "")}
        )
    waiting = [item for item in _queue if item["mentor"] == mentor]
    return {
        "lessons": states,
        "ready": sum(item["state"] == "ready" for item in states),
        "total": len(states),
        "preparing": len(waiting),
        "doing": _job["doing"] if waiting else "",
        "seconds_left": seconds_left() if waiting else 0,
        "bytes": folder_bytes(mentor),
    }


def prune(mentor: str) -> None:
    """Delete scripts and media that no longer match the mentor (old voice, photos or
    personality), so packs do not grow without limit."""
    folder, setup = mentor_dir(mentor), MentorSetup(mentor)
    keep: set[str] = set()
    scripts = os.path.join(folder, "scripts")
    for name in os.listdir(scripts) if os.path.isdir(scripts) else []:
        path = os.path.join(scripts, name)
        try:
            with open(path, encoding="utf-8") as script_file:
                lesson = json.load(script_file)["lesson"]
            pack = Pack(setup, lesson)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if os.path.normcase(pack.script_path) != os.path.normcase(path):
            os.remove(path)
            continue
        for sentence in pack.sentences() or []:
            keep.add(os.path.basename(pack.audio_path(sentence)))
            keep.add(os.path.basename(pack.video_file(sentence))[:-4])
    media = os.path.join(folder, "media")
    for name in os.listdir(media) if os.path.isdir(media) else []:
        if name.split(".")[0] not in keep:
            os.remove(os.path.join(media, name))
