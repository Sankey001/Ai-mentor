"""Lesson packs: lessons written, voiced and lip-synced ahead of time."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from backend import config
from backend.services import lesson_packs, media_processor, talk
from tests.test_talk import make_mentor

LOOPS = {"course": "Python", "module": "Basics", "title": "Loops"}
LISTS = {"course": "Python", "module": "Basics", "title": "Lists"}
SCRIPT = "A loop repeats code. It saves typing. Use it for lists. That is a loop."


@pytest.fixture
def packs(tmp_path, monkeypatch):
    """A mentor with a recorded voice, stubbed workers and a stubbed script writer."""
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(talk, "TALK_DIR", str(tmp_path / "outputs" / "talk"))
    monkeypatch.setattr(config, "FACE_WORKER_URL", "http://face")
    monkeypatch.setattr(config, "VOICE_WORKER_URL", "http://voice")
    monkeypatch.setattr(lesson_packs, "script_model", lambda: (None, "test-model"))
    monkeypatch.setattr(lesson_packs, "TYPICAL_SCRIPT_SECONDS", 10.0)
    monkeypatch.setattr(lesson_packs, "TYPICAL_SENTENCE_SECONDS", 2.0)
    make_mentor(tmp_path, "asha", voice=True, persona="Kind.")
    state = {"calls": [], "scripts": {}, "voice": True, "face": True, "written": []}

    def reply_pieces(mentor, message, history, lesson, choice, think=False, prepared=False):
        state["written"].append((lesson["title"], prepared))
        for word in state["scripts"].get(lesson["title"], SCRIPT).split(" "):
            yield "ollama", word + " "

    async def speak(text, reference, output, **_kwargs):
        state["calls"].append(("speak", text))
        if state["voice"]:
            Path(output).write_bytes(b"audio")
        return state["voice"]

    async def animate(clips, audio, output, **_kwargs):
        state["calls"].append(("animate", Path(audio).name))
        if state["face"]:
            Path(output).write_bytes(b"video")
        return state["face"]

    async def stock_tts(text, output, **_kwargs):
        state["calls"].append(("stock", text))
        Path(output).write_bytes(b"stock")

    monkeypatch.setattr(talk, "reply_pieces", reply_pieces)
    monkeypatch.setattr(talk.media_workers, "speak", speak)
    monkeypatch.setattr(talk.media_workers, "animate", animate)
    monkeypatch.setattr(talk, "generate_tts", stock_tts)
    lesson_packs._queue.clear()
    lesson_packs._failed.clear()
    yield state
    lesson_packs._queue.clear()
    lesson_packs._failed.clear()


def prepare(lessons, mentor="asha", urgent=False):
    """Queue the lessons and wait until the background work is done."""

    async def run():
        lesson_packs.prepare(mentor, lessons, urgent=urgent)
        if lesson_packs._job["task"]:
            await lesson_packs._job["task"]

    asyncio.run(run())


def calls(state, kind):
    return [text for name, text in state["calls"] if name == kind]


def test_a_lesson_is_written_voiced_and_lip_synced_once(packs, tmp_path):
    prepare([LOOPS])

    pack = lesson_packs.Pack("asha", LOOPS).playable()
    assert pack["state"] == "ready"
    assert [s["text"] for s in pack["sentences"]] == talk.split_sentences(SCRIPT)
    assert {(s["kind"], s["voice"], s["face"]) for s in pack["sentences"]} == {
        ("video", "cloned", "lipsync")
    }
    assert packs["written"] == [("Loops", True)]  # the longer, prepared kind of lesson
    media = tmp_path / "outputs" / "lesson_packs" / "asha" / "media"
    for sentence in pack["sentences"]:
        assert sentence["url"].startswith("/pack-media/asha/")
        assert (media / sentence["url"].rsplit("/", 1)[1]).read_bytes() == b"video"
    assert len(list(media.glob("*.wav"))) == len(list(media.glob("*.mp4"))) == 4
    assert not list(media.glob("*.tmp.*"))

    packs["calls"].clear()
    prepare([LOOPS])  # ready: nothing is made again

    assert packs["calls"] == [] and len(packs["written"]) == 1


def test_preparation_resumes_where_it_stopped(packs):
    packs["face"] = False  # the face worker goes away
    prepare([LOOPS])

    pack = lesson_packs.Pack("asha", LOOPS)
    assert pack.state() == "failed"
    assert "sentences" not in pack.playable()
    assert len(calls(packs, "speak")) == 1  # stopped at the first sentence

    packs["face"] = True
    packs["calls"].clear()
    prepare([LOOPS])

    assert lesson_packs.Pack("asha", LOOPS).state() == "ready"
    assert len(packs["written"]) == 1  # the script was kept
    assert len(calls(packs, "speak")) == 3  # the first sentence's audio was kept
    assert len(calls(packs, "animate")) == 4


def test_a_recorded_voice_is_never_replaced_by_the_stock_voice(packs):
    packs["voice"] = False
    prepare([LOOPS])

    assert lesson_packs.Pack("asha", LOOPS).state() == "failed"
    assert calls(packs, "stock") == []


def test_mentor_without_a_recording_gets_stock_voice_packs(packs, tmp_path):
    make_mentor(tmp_path, "bo")
    prepare([LOOPS], mentor="bo")

    pack = lesson_packs.Pack("bo", LOOPS).playable()
    assert pack["state"] == "ready"
    assert {s["voice"] for s in pack["sentences"]} == {"stock"}
    assert calls(packs, "speak") == []


def test_packs_are_audio_only_without_a_face_worker(packs, monkeypatch):
    monkeypatch.setattr(config, "FACE_WORKER_URL", "")
    prepare([LOOPS])

    pack = lesson_packs.Pack("asha", LOOPS).playable()
    assert pack["state"] == "ready"
    assert {(s["kind"], s["face"]) for s in pack["sentences"]} == {("audio", "idle")}
    assert all(s["url"].endswith(".wav") for s in pack["sentences"])


def test_only_changed_sentences_are_made_again_when_the_personality_changes(packs, tmp_path):
    prepare([LOOPS])
    packs["calls"].clear()
    make_profile = tmp_path / "backend" / "input" / "asha" / "mentor.json"
    make_profile.write_text('{"persona": "Stern."}')
    packs["scripts"]["Loops"] = "A loop repeats code. It saves typing. Be exact. That is a loop."

    assert lesson_packs.Pack("asha", LOOPS).state() == "missing"  # a new script is needed
    prepare([LOOPS])

    assert calls(packs, "speak") == ["Be exact."]
    assert len(calls(packs, "animate")) == 1
    # What the old script alone used ("Use it for lists.") is cleaned up.
    media = tmp_path / "outputs" / "lesson_packs" / "asha" / "media"
    assert len(list(media.glob("*.wav"))) == len(list(media.glob("*.mp4"))) == 4
    assert len(list((media.parent / "scripts").glob("*.json"))) == 1


def test_a_new_voice_recording_re_voices_but_keeps_the_script(packs, tmp_path):
    prepare([LOOPS])
    packs["calls"].clear()
    voice = tmp_path / "backend" / "input" / "asha" / "voice.wav"
    voice.write_bytes(b"a different recording")

    assert lesson_packs.Pack("asha", LOOPS).state() == "missing"
    prepare([LOOPS])

    assert len(packs["written"]) == 1
    assert len(calls(packs, "speak")) == len(calls(packs, "animate")) == 4


def test_new_clips_only_remake_the_video(packs, tmp_path):
    prepare([LOOPS])
    packs["calls"].clear()
    (tmp_path / "backend" / "input" / "asha" / "talk_1.mp4").write_bytes(b"new clip")

    prepare([LOOPS])

    assert calls(packs, "speak") == []
    assert len(calls(packs, "animate")) == 4


def test_the_lesson_on_screen_is_prepared_before_the_rest_of_the_course(packs):
    order = []
    render = lesson_packs._render

    async def render_and_note(item):
        order.append(item["lesson"]["title"])
        await render(item)

    async def run():
        with patch.object(lesson_packs, "_render", render_and_note):
            lesson_packs.prepare("asha", [LOOPS, LISTS])
            lesson_packs.prepare("asha", [LISTS], urgent=True)
            await lesson_packs._job["task"]

    asyncio.run(run())

    assert order == ["Lists", "Loops"]


def test_the_lessons_asked_for_last_go_before_those_already_waiting(packs):
    sets, dicts, files = ({**LOOPS, "title": title} for title in ("Sets", "Dicts", "Files"))

    async def run():
        async with talk.gpu_turn():  # nothing starts yet
            lesson_packs.prepare("asha", [LOOPS, LISTS, sets, dicts])  # an older request
            lesson_packs.prepare("asha", [files], urgent=True)  # the lesson now on screen
            lesson_packs.prepare("asha", [dicts, sets])  # the lessons that follow it
            waiting = [item["lesson"]["title"] for item in lesson_packs._queue]
            lesson_packs.cancel("asha")
        await lesson_packs._job["task"]
        return waiting

    # "Files" is urgent, so it is prepared first wherever it stands in the queue.
    assert asyncio.run(run()) == ["Dicts", "Sets", "Files", "Loops", "Lists"]


def test_preparation_waits_while_the_mentor_answers_a_question(packs):
    async def run():
        async with talk.gpu_turn():  # a live turn has the GPU
            lesson_packs.prepare("asha", [LOOPS])
            await asyncio.sleep(0.2)
            done_meanwhile = (list(packs["written"]), list(packs["calls"]))
        await lesson_packs._job["task"]
        return done_meanwhile

    assert asyncio.run(run()) == ([], [])
    assert lesson_packs.Pack("asha", LOOPS).state() == "ready"


def test_a_question_interrupts_a_script_being_written(packs, monkeypatch):
    pack = lesson_packs.Pack("asha", LOOPS)
    monkeypatch.setattr(talk, "live_waiting", 1)

    assert lesson_packs.write_script(pack, None) is None  # given up, not failed
    assert pack.sentences() is None

    monkeypatch.setattr(talk, "live_waiting", 0)
    assert lesson_packs.write_script(pack, None) is True
    assert pack.sentences() == talk.split_sentences(SCRIPT)


def test_a_large_writing_model_leaves_the_gpu_before_recording(packs, monkeypatch):
    order = []
    monkeypatch.setattr(config, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(
        lesson_packs, "script_model", lambda: (("ollama", "qwen3.5:9b"), "ollama:qwen3.5:9b")
    )
    monkeypatch.setattr(talk, "fits_beside_workers", lambda model: model != "qwen3.5:9b")
    monkeypatch.setattr(
        lesson_packs, "release_ollama_model", lambda model: order.append(("unload", model))
    )
    monkeypatch.setattr(
        lesson_packs, "load_ollama_model", lambda model: order.append(("load", model))
    )
    speak = talk.media_workers.speak

    async def speak_and_note(*args, **kwargs):
        order.append(("speak",))
        return await speak(*args, **kwargs)

    monkeypatch.setattr(talk.media_workers, "speak", speak_and_note)

    prepare([LOOPS])

    # The answering model makes way for the writing model, and comes back after.
    assert order[:4] == [
        ("unload", config.OLLAMA_MODEL),
        ("unload", "qwen3.5:9b"),
        ("load", config.OLLAMA_MODEL),
        ("speak",),
    ]


def test_status_reports_progress_time_left_and_size(packs):
    async def run():
        lesson_packs.prepare("asha", [LOOPS, LISTS])
        before = lesson_packs.status("asha", [LOOPS, LISTS])
        await lesson_packs._job["task"]
        return before

    before = asyncio.run(run())
    after = lesson_packs.status("asha", [LOOPS, LISTS])

    assert (before["ready"], before["total"], before["preparing"]) == (0, 2, 2)
    # Two lessons, each one script (10 s) and about ten sentences (2 s each).
    assert before["seconds_left"] == 60
    assert (after["ready"], after["preparing"], after["seconds_left"]) == (2, 0, 0)
    assert after["bytes"] > 0 and after["doing"] == ""
    assert [item["state"] for item in after["lessons"]] == ["ready", "ready"]
    assert [item["id"] for item in after["lessons"]] == [
        lesson_packs.lesson_id(LOOPS),
        lesson_packs.lesson_id(LISTS),
    ]


def test_an_unusable_script_is_not_saved(packs):
    packs["scripts"]["Loops"] = "Too short."
    prepare([LOOPS])

    (lesson,) = lesson_packs.status("asha", [LOOPS])["lessons"]
    assert lesson["state"] == "failed"
    assert "could not write" in lesson["error"]
    assert packs["calls"] == []


def test_a_question_about_a_prepared_lesson_gets_its_script(packs):
    assert lesson_packs.script_text("asha", LOOPS) is None
    prepare([LOOPS])

    assert lesson_packs.script_text("asha", LOOPS) == SCRIPT
    assert lesson_packs.script_text("asha", LISTS) is None


def test_deleting_a_mentor_deletes_its_packs(packs, tmp_path):
    prepare([LOOPS])

    lesson_packs.delete_mentor_packs("asha")

    assert not (tmp_path / "outputs" / "lesson_packs" / "asha").exists()


@pytest.fixture
def client():
    with patch.object(api, "check_ffmpeg", return_value=None), TestClient(api.app) as test_client:
        yield test_client


def test_pack_endpoints(packs, client):
    body = {"mentor": "asha", "lessons": [LOOPS], "urgent": True}

    started = client.post("/lesson-packs/prepare", json=body)
    assert started.status_code == 200 and started.json()["total"] == 1
    for _ in range(100):
        status = client.post("/lesson-packs/status", json=body).json()
        if status["ready"] == 1:
            break
    assert status["ready"] == 1

    pack = client.post("/lesson-packs/pack", json={"mentor": "asha", "lesson": LOOPS}).json()
    assert pack["state"] == "ready" and len(pack["sentences"]) == 4
    media = client.get(pack["sentences"][0]["url"])
    assert media.status_code == 200 and media.content == b"video"

    assert client.get("/pack-media/asha/voice.wav").status_code == 404
    assert client.get("/pack-media/asha/..%2F..%2Fx.mp4").status_code == 404
    assert client.get("/pack-media/ghost/0123456789abcdef.mp4").status_code == 404
    assert client.post("/lesson-packs/status", json={**body, "mentor": "ghost"}).status_code == 404
    assert client.post("/lesson-packs/status", json={**body, "lessons": []}).status_code == 422
    assert client.delete("/lesson-packs/asha/queue").status_code == 204


def test_talk_endpoint_sends_the_prepared_script_with_a_question(packs, client, monkeypatch):
    prepare([LOOPS])
    seen = []

    def reply_pieces(mentor, message, history, lesson, choice, think, script):
        seen.append(script)
        yield "gemini", "Sure."

    monkeypatch.setattr(talk, "reply_pieces", reply_pieces)
    body = {"mentor": "asha", "message": "Why?", "lesson": LOOPS}

    assert client.post("/talk", json=body).status_code == 200
    assert client.post("/talk", json={**body, "lesson": LISTS}).status_code == 200
    assert client.post("/talk", json={"mentor": "asha", "lesson": LOOPS}).status_code == 200

    # Only a question about a prepared lesson carries a script; teaching live does not.
    assert seen == [SCRIPT, None, None]
    assert os.path.isdir(talk.TALK_DIR)
