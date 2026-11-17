"""Voice bank (phrases kept in the mentor's voice) and mentor kits (moving a mentor)."""

import asyncio
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from backend import config
from backend.services import lesson_packs, media_processor, mentor_kits, talk, voice_bank
from tests.test_lesson_packs import LOOPS, SCRIPT, calls, packs, prepare  # noqa: F401
from tests.test_talk import make_mentor

PHRASES = """# test phrases
## openers
Great question.
Let's break it down.

## closers
I hope that helps.

## terms
a loop
JSON
"""


@pytest.fixture
def bank(packs, tmp_path, monkeypatch):
    """The `packs` mentor with a short phrase list."""
    phrases = tmp_path / "phrases.txt"
    phrases.write_text(PHRASES, encoding="utf-8")
    monkeypatch.setattr(voice_bank, "PHRASES_FILE", str(phrases))
    monkeypatch.setattr(mentor_kits, "kits_dir", lambda: str(tmp_path / "mentor_kits"))
    return packs


def record(mentor="asha"):
    async def run():
        voice_bank.build(mentor)
        await voice_bank._build["task"]

    asyncio.run(run())


def live(question, reply, mentor="asha", packs_state=None):
    """One live answer whose model writes `reply`, using the mentor's voice bank."""
    prompts = []

    def model(*args, **kwargs):
        prompts.append(kwargs.get("openers"))
        for word in reply.split(" "):
            yield "ollama", word + " "

    async def run():
        with patch.object(talk, "reply_pieces", model):
            cache = voice_bank.LiveCache(mentor)
            return [e async for e in talk.talk(mentor, question, [], None, None, False, None, cache)]

    events = asyncio.run(run())
    return [e for e in events if e["type"] == "chunk"], prompts


def test_the_phrase_list_has_about_500_phrases_with_technical_words():
    phrases = voice_bank.load_phrases()
    texts = [text for _group, text in phrases]

    assert 450 <= len(phrases) <= 600
    assert len({voice_bank.normalize(text) for text in texts}) == len(texts)
    assert {group for group, _ in phrases} >= {"openers", "transitions", "closers", "terms"}
    # Each technical word is said inside two different sentences.
    assert sum("JSON" in text for text in texts) == 2
    assert all(text[-1] in ".?!" for text in texts)


def test_phrases_are_recorded_once_and_skipped_after(bank):
    record()

    status = voice_bank.status("asha")
    assert (status["ready"], status["total"], status["building"]) == (7, 7, False)
    assert len(calls(bank, "speak")) == len(calls(bank, "animate")) == 7
    assert "This is JSON." in calls(bank, "speak")
    assert status["bytes"] > 0

    bank["calls"].clear()
    record()
    assert bank["calls"] == []


def test_recording_waits_for_lessons_being_prepared(bank):
    async def run():
        lesson_packs._queue.append({"mentor": "asha", "id": "x", "lesson": LOOPS, "urgent": True})
        voice_bank.build("asha")
        await asyncio.sleep(0.1)
        waited = (list(bank["calls"]), voice_bank.status("asha")["waiting_for_lessons"])
        lesson_packs._queue.clear()
        await voice_bank._build["task"]
        return waited

    assert asyncio.run(run()) == ([], True)
    assert voice_bank.status("asha")["ready"] == 7


def test_a_worker_failure_stops_recording_and_says_why(bank):
    bank["face"] = False
    record()

    status = voice_bank.status("asha")
    assert status["ready"] == 0 and status["building"] is False
    assert "not available" in status["error"]


def test_a_live_answer_plays_recorded_phrases_at_once_and_keeps_new_sentences(bank):
    record()
    bank["calls"].clear()

    chunks, prompts = live("Why?", "Great question. Loops repeat code.")

    # The opener was recorded: played from the bank, without the GPU.
    assert [c["text"] for c in chunks] == ["Great question.", "Loops repeat code."]
    assert chunks[0]["url"].startswith("/pack-media/asha/")
    assert calls(bank, "speak") == ["Loops repeat code."]
    # The AI was told which recorded phrases it can begin with.
    assert prompts == [["Great question.", "Let's break it down."]]

    # The new sentence was kept, so the next answer that says it needs no GPU.
    bank["calls"].clear()
    chunks, _ = live("And?", "Great question. LOOPS repeat code.")
    assert bank["calls"] == []
    assert all(c["url"].startswith("/pack-media/asha/") for c in chunks)
    assert voice_bank.status("asha")["kept"] == 1


def test_curly_quotes_and_case_still_match():
    assert voice_bank.normalize("That’s  GREAT — ok.") == "that's great - ok."


def test_a_stand_in_voice_or_missing_clip_is_not_kept(bank):
    bank["voice"] = False  # cloning fails: the stock voice answers instead
    live("Why?", "Loops repeat code.")
    bank["voice"], bank["face"] = True, False  # lip-sync fails
    live("Why?", "Lists hold things.")

    assert voice_bank.texts("asha") == []


def test_a_teaching_turn_is_not_steered_to_an_opener(bank):
    record()

    async def run():
        seen = []

        def model(*args, **kwargs):
            seen.append(kwargs.get("openers"))
            yield "ollama", "Loops repeat code."

        with patch.object(talk, "reply_pieces", model):
            cache = voice_bank.LiveCache("asha")
            [e async for e in talk.talk("asha", "", [], LOOPS, None, False, None, cache)]
        return seen

    assert asyncio.run(run()) == [None]


def test_openers_are_part_of_the_prompt_only_for_questions(monkeypatch):
    seen = []

    def stream(system_instruction, messages, **_kwargs):
        seen.append(system_instruction)
        return iter(["Sure."])

    monkeypatch.setattr(talk, "stream_ollama_content", stream)
    list(talk.reply_pieces("ada", "Why?", [], None, ("ollama", "gemma3"), openers=["Good question."]))
    list(talk.reply_pieces("ada", "", [], LOOPS, ("ollama", "gemma3"), openers=["Good question."]))

    assert "word for word: Good question." in seen[0]
    assert "Good question." not in seen[1]


def test_lesson_pack_cleanup_keeps_the_voice_bank(bank):
    record()
    live("Why?", "Loops repeat code.")
    prepare([LOOPS])  # preparing lessons ends with a cleanup

    setup = lesson_packs.MentorSetup("asha")
    for text in voice_bank.texts("asha"):
        assert setup.sentence_ready(text), text


def test_media_made_before_content_fingerprints_is_renamed_not_remade(bank, tmp_path):
    prepare([LOOPS])
    setup = lesson_packs.MentorSetup("asha")
    old_voice = lesson_packs._voice_of(setup.media, lesson_packs._old_stamp)
    old_clips = [lesson_packs._old_stamp(clip) for clip in setup.media.clips]
    media = Path(setup.folder) / "media"
    # Put the pack back the way format 1 named its files.
    for sentence in talk.split_sentences(SCRIPT):
        new_audio = os.path.basename(setup.audio_path(sentence))
        new_video = os.path.basename(setup.video_file(sentence))
        old_audio = lesson_packs._key(sentence, old_voice)
        (media / f"{new_audio}.wav").rename(media / f"{old_audio}.wav")
        (media / new_video).rename(media / f"{lesson_packs._key(old_audio, old_clips)}.mp4")
    (Path(setup.folder) / lesson_packs.FORMAT_FILE).unlink()
    lesson_packs._migrated.clear()

    assert lesson_packs.Pack("asha", LOOPS).state() == "ready"
    assert json.loads((Path(setup.folder) / "format.json").read_text()) == {"version": 2}


# ---------- mentor kits ----------


def move_to_another_computer(tmp_path, monkeypatch):
    other = tmp_path / "other_pc" / "ai_service"
    (other / "backend" / "input").mkdir(parents=True)
    monkeypatch.setattr(media_processor, "BASE_DIR", str(other))
    monkeypatch.setattr(mentor_kits, "kits_dir", lambda: str(other.parent / "mentor_kits"))
    lesson_packs._migrated.clear()
    voice_bank._banks.clear()
    return other


def test_a_kit_moves_a_mentor_with_its_voice_bank_and_lessons(bank, tmp_path, monkeypatch):
    record()
    live("Why?", "Loops repeat code.")
    prepare([LOOPS])
    kit = mentor_kits.export_kit("asha")
    assert kit["path"] == str(tmp_path / "mentor_kits" / "asha.zip")

    other = move_to_another_computer(tmp_path, monkeypatch)
    kits = other.parent / "mentor_kits"
    kits.mkdir()
    (kits / "asha.zip").write_bytes(Path(kit["path"]).read_bytes())
    os.utime(other / "backend" / "input", (1, 1))  # dates differ on the new computer
    bank["calls"].clear()

    result = mentor_kits.import_waiting()

    assert [k["name"] for k in result["imported"]] == ["asha"] and result["failed"] == []
    assert (other / "backend" / "input" / "asha" / "voice.wav").exists()
    # Everything plays on the new computer without being made again.
    assert lesson_packs.Pack("asha", LOOPS).state() == "ready"
    assert voice_bank.status("asha")["ready"] == 7
    assert voice_bank.status("asha")["kept"] == 1
    chunks, _ = live("Why?", "Great question. Loops repeat code.")
    assert bank["calls"] == [] and len(chunks) == 2
    # A kit is imported once.
    assert mentor_kits.import_waiting()["imported"] == []


def test_importing_into_an_existing_mentor_keeps_its_own_files(bank, tmp_path, monkeypatch):
    record()
    kit = mentor_kits.export_kit("asha")
    persona = tmp_path / "backend" / "input" / "asha" / "mentor.json"
    persona.write_text('{"persona": "Changed here."}')
    voice_bank._banks.clear()

    result = mentor_kits.import_kit(kit["path"])

    assert result["new_mentor"] is False
    assert json.loads(persona.read_text()) == {"persona": "Changed here."}


def test_a_kit_never_unpacks_files_outside_its_folders(bank, tmp_path):
    kit = tmp_path / "evil.zip"
    with zipfile.ZipFile(kit, "w") as archive:
        archive.writestr("kit.json", json.dumps({"format": 1, "name": "eve"}))
        archive.writestr("mentor/talk.mp4", b"clip")
        archive.writestr("mentor/../../escape.mp4", b"x")
        archive.writestr("mentor/run.exe", b"x")
        archive.writestr("bank/media/../../../escape.wav", b"x")
        archive.writestr("bank/notes.txt", b"x")
        archive.writestr("other/file.mp4", b"x")

    result = mentor_kits.import_kit(str(kit))

    assert result == {"name": "eve", "new_mentor": True, "files_added": 1}
    assert sorted(os.listdir(tmp_path / "backend" / "input" / "eve")) == ["talk.mp4"]
    assert not list(tmp_path.rglob("escape*"))
    assert not list(tmp_path.rglob("run.exe"))


@pytest.mark.parametrize(
    ("info", "message"),
    [
        (None, "not a mentor kit"),
        ({"format": 9, "name": "eve"}, "different version"),
        ({"format": 1, "name": "../eve"}, "not valid"),
    ],
)
def test_a_bad_kit_is_refused(bank, tmp_path, info, message):
    kit = tmp_path / "bad.zip"
    with zipfile.ZipFile(kit, "w") as archive:
        if info:
            archive.writestr("kit.json", json.dumps(info))

    with pytest.raises(mentor_kits.KitError, match=message):
        mentor_kits.import_kit(str(kit))


def test_a_zip_bomb_is_refused(bank, tmp_path):
    kit = tmp_path / "bomb.zip"
    with zipfile.ZipFile(kit, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("kit.json", json.dumps({"format": 1, "name": "eve"}))
        archive.writestr("bank/media/0123456789abcdef.wav", b"\0" * (20 * 1024**2))

    with pytest.raises(mentor_kits.KitError, match="too large"):
        mentor_kits.import_kit(str(kit))


@pytest.fixture
def client():
    with patch.object(api, "check_ffmpeg", return_value=None), TestClient(api.app) as test_client:
        yield test_client


def test_voice_bank_and_kit_endpoints(bank, client, tmp_path):
    started = client.post("/mentors/asha/voice-bank")
    assert started.status_code == 200 and started.json()["total"] == 7
    for _ in range(200):
        status = client.get("/mentors/asha/voice-bank").json()
        if not status["building"]:
            break
    assert status["ready"] == 7

    assert client.delete("/mentors/asha/voice-bank/recording").status_code == 204
    exported = client.post("/mentors/asha/kit").json()
    assert exported["path"].endswith("asha.zip") and exported["bytes"] > 0
    assert client.get("/mentor-kits").json()["folder"] == str(tmp_path / "mentor_kits")
    # The kit this computer wrote is not imported back.
    assert client.post("/mentor-kits/import").json()["imported"] == []
    assert client.get("/mentors/ghost/voice-bank").status_code == 404
    assert client.post("/mentors/ghost/kit").status_code == 404
