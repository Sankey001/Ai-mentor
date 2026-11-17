import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from backend.services import media_processor, mentor_studio, talk


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """Point the studio at an empty input folder and stub the media tools."""
    (tmp_path / "backend" / "input").mkdir(parents=True)
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    mentor_studio._states.clear()
    state = {"prepared": [], "bad_images": set(), "voice_ok": True}

    monkeypatch.setattr(
        mentor_studio, "is_image", lambda path: Path(path).read_bytes() not in state["bad_images"]
    )

    def convert_voice(source, target):
        if state["voice_ok"]:
            Path(target).write_bytes(b"wav:" + Path(source).read_bytes())
        return state["voice_ok"]

    monkeypatch.setattr(mentor_studio, "convert_voice", convert_voice)

    async def prepare(name):
        state["prepared"].append(name)

    monkeypatch.setattr(mentor_studio, "prepare_mentor", prepare)
    state["dir"] = tmp_path / "backend" / "input"
    yield state
    mentor_studio._states.clear()


@pytest.fixture
def client():
    with patch.object(api, "check_ffmpeg", return_value=None), TestClient(api.app) as test_client:
        yield test_client


def upload(client, name="asha", images=None, voice=(b"voice-bytes", "me.webm"), **fields):
    images = [(b"image-one", "front.heic")] if images is None else images
    files = [("images", (filename, data, "application/octet-stream")) for data, filename in images]
    if voice:
        files.append(("voice", (voice[1], voice[0], "application/octet-stream")))
    data = {"name": name, "persona": "Kind and patient.", "expressiveness": "0.7", "consent": "true"}
    data.update(fields)
    return client.post("/mentors", data=data, files=files)


def test_upload_creates_a_mentor_pack_and_starts_preparing(studio, client):
    response = upload(client, images=[(b"image-one", "Front View.heic"), (b"image-two", "side")])

    assert response.status_code == 201
    assert response.json() == {"name": "asha", "state": "preparing"}
    folder = studio["dir"] / "asha"
    assert sorted(p.name for p in folder.iterdir()) == [
        "image_1.heic",
        "image_2",
        "mentor.json",
        "voice.wav",
    ]
    assert (folder / "voice.wav").read_bytes() == b"wav:voice-bytes"
    assert json.loads((folder / "mentor.json").read_text()) == {
        "persona": "Kind and patient.",
        "expressiveness": 0.7,
        "uploaded": True,
    }
    assert studio["prepared"] == ["asha"]


def test_voice_is_optional(studio, client):
    assert upload(client, voice=None).status_code == 201
    assert not (studio["dir"] / "asha" / "voice.wav").exists()


@pytest.mark.parametrize(
    "fields, status",
    [
        ({"consent": "false"}, 422),
        ({"name": "../evil"}, 422),
        ({"name": "Has Space"}, 422),
        ({"name": "modi"}, 409),
        ({"expressiveness": "7"}, 422),
        ({"persona": "x" * 1001}, 422),
    ],
)
def test_bad_upload_fields_are_rejected(studio, client, fields, status):
    assert upload(client, **fields).status_code == status
    assert list(studio["dir"].iterdir()) == []


def test_upload_needs_at_least_one_image_and_at_most_eight(studio, client):
    assert upload(client, images=[]).status_code == 422
    assert upload(client, images=[(b"i", f"{n}.png") for n in range(9)]).status_code == 422
    assert list(studio["dir"].iterdir()) == []


def test_a_file_that_is_not_an_image_is_rejected(studio, client):
    studio["bad_images"].add(b"not-an-image")

    response = upload(client, images=[(b"image-one", "a.png"), (b"not-an-image", "b.png")])

    assert response.status_code == 422
    assert "b.png" in response.json()["detail"]
    assert list(studio["dir"].iterdir()) == []


def test_unreadable_voice_recording_is_rejected(studio, client):
    studio["voice_ok"] = False

    assert upload(client).status_code == 422
    assert list(studio["dir"].iterdir()) == []


def test_oversized_files_are_rejected(studio, client, monkeypatch):
    monkeypatch.setattr(mentor_studio, "MAX_IMAGE_BYTES", 10)

    assert upload(client, images=[(b"x" * 11, "big.png")]).status_code == 413
    assert list(studio["dir"].iterdir()) == []


def test_existing_mentor_is_not_overwritten(studio, client):
    assert upload(client).status_code == 201
    assert upload(client, images=[(b"other", "x.png")]).status_code == 409
    assert (studio["dir"] / "asha" / "image_1.heic").read_bytes() == b"image-one"


def test_mentor_list_shows_state_and_which_mentors_can_be_removed(studio, client):
    upload(client)
    builtin = studio["dir"] / "owl"
    builtin.mkdir()
    (builtin / "talk.mp4").write_bytes(b"clip")

    mentors = {m["name"]: m for m in client.get("/mentors").json()["mentors"]}

    assert mentors["asha"] == {
        "name": "asha", "voice": "cloned", "persona": True, "state": "preparing", "uploaded": True,
    }
    assert mentors["owl"] == {
        "name": "owl", "voice": "stock", "persona": False, "state": "ready", "uploaded": False,
    }


def test_only_uploaded_mentors_can_be_deleted(studio, client):
    upload(client)
    builtin = studio["dir"] / "owl"
    builtin.mkdir()
    (builtin / "talk.mp4").write_bytes(b"clip")

    assert client.delete("/mentors/owl").status_code == 403
    assert client.delete("/mentors/ghost").status_code == 404
    assert client.delete("/mentors/..%2Fowl").status_code == 404
    assert client.delete("/mentors/asha").status_code == 204
    assert not (studio["dir"] / "asha").exists()
    assert builtin.exists()


def test_a_mentor_that_is_still_preparing_cannot_be_talked_to(studio, client):
    upload(client)

    assert client.post("/talk", json={"mentor": "asha", "message": "hi"}).status_code == 404


# ---------- preparation (runs in the background after an upload) ----------


def run_prepare(tmp_path, monkeypatch, *, command=("tool",), makes_clip=True, face_ok=True):
    (tmp_path / "backend" / "input" / "asha").mkdir(parents=True)
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    mentor_studio._states.clear()
    monkeypatch.setattr(mentor_studio, "portrait_command", lambda: list(command) if command else None)
    calls = []

    async def run_tool(cmd):
        calls.append(("portrait", cmd))
        if makes_clip:
            (tmp_path / "backend" / "input" / "asha" / "auto_image_1_abcd1234.mp4").write_bytes(b"c")
        return 0

    async def prepare_face(clips, **_kwargs):
        calls.append(("face", [Path(c).name for c in clips]))
        return face_ok

    monkeypatch.setattr(mentor_studio, "run_tool", run_tool)
    monkeypatch.setattr(mentor_studio.media_workers, "prepare", prepare_face)
    asyncio.run(mentor_studio.prepare_mentor("asha"))
    return calls, mentor_studio._states["asha"]


def test_preparing_animates_the_images_then_warms_the_face_worker(tmp_path, monkeypatch):
    calls, state = run_prepare(tmp_path, monkeypatch)

    assert calls == [("portrait", ["tool"]), ("face", ["auto_image_1_abcd1234.mp4"])]
    assert state["state"] == "ready"


def test_mentor_is_still_usable_when_the_face_worker_is_down(tmp_path, monkeypatch):
    _calls, state = run_prepare(tmp_path, monkeypatch, face_ok=False)

    assert state["state"] == "ready"


def test_preparing_fails_clearly_when_no_face_could_be_animated(tmp_path, monkeypatch):
    _calls, state = run_prepare(tmp_path, monkeypatch, makes_clip=False)

    assert state["state"] == "failed"
    assert "face" in state["detail"].lower()


def test_simple_motion_is_used_when_the_image_step_is_not_installed(tmp_path, monkeypatch):
    made = []

    def basic(name):
        made.append(name)
        (tmp_path / "backend" / "input" / name / "auto_image_1_basic.mp4").write_bytes(b"c")

    monkeypatch.setattr(mentor_studio, "make_basic_clips", basic)

    calls, state = run_prepare(tmp_path, monkeypatch, command=None, makes_clip=False)

    assert made == ["asha"]
    assert calls == [("face", ["auto_image_1_basic.mp4"])]
    assert state["state"] == "ready"


def test_simple_motion_clip_is_made_from_an_image(tmp_path, monkeypatch):
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg not installed")
    mentor = tmp_path / "backend" / "input" / "asha"
    mentor.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=300x400", "-frames:v", "1",
         str(mentor / "image_1.webp")],
        check=True,
    )
    (mentor / "mentor.json").write_text("{}")
    (mentor / "voice.wav").write_bytes(b"not an image")
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))

    mentor_studio.make_basic_clips("asha")

    clips = sorted(p.name for p in mentor.glob("*.mp4"))
    assert clips == ["auto_image_1_basic.mp4"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "csv=p=0", str(mentor / clips[0])],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    assert probe[0] == "512,512"
    assert 5.5 < float(probe[1]) < 6.5


def test_an_image_ffmpeg_cannot_read_is_converted_with_pillow(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    source = tmp_path / "image_1.heic"
    image_module.new("RGB", (40, 30), "white").save(source, "PPM")  # stands in for an unreadable format
    readable = []
    monkeypatch.setattr(mentor_studio, "is_image", lambda path: path.endswith(".png"))

    result = mentor_studio.readable_image(str(source))

    assert result == str(tmp_path / "image_1.png")
    assert not source.exists()
    with image_module.open(result) as converted:
        assert converted.size == (40, 30)

    garbage = tmp_path / "image_2.heic"
    garbage.write_bytes(b"not an image")
    assert mentor_studio.readable_image(str(garbage)) is None
    assert readable == []


# ---------- teaching a lesson ----------


def test_talk_can_teach_a_lesson(tmp_path, monkeypatch, client):
    mentor = tmp_path / "backend" / "input" / "asha"
    mentor.mkdir(parents=True)
    (mentor / "talk.mp4").write_bytes(b"clip")
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(talk, "TALK_DIR", str(tmp_path / "outputs" / "talk"))
    prompts = []

    def gemini(system_instruction, user_data, **_kwargs):
        prompts.append((system_instruction, user_data))
        from types import SimpleNamespace

        return SimpleNamespace(text=" ".join(f"Point {n}." for n in range(12)))

    async def no_media(*_args, **_kwargs):
        return False

    async def no_tts(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(talk, "generate_gemini_content", gemini)
    monkeypatch.setattr(talk.media_workers, "speak", no_media)
    monkeypatch.setattr(talk.media_workers, "animate", no_media)
    monkeypatch.setattr(talk, "generate_tts", no_tts)
    lesson = {"course": "React Fundamentals", "module": "Module 1", "title": "What is React?"}

    response = client.post("/talk", json={"mentor": "asha", "lesson": lesson})

    assert response.status_code == 200
    events = [json.loads(l[6:]) for l in response.text.splitlines() if l.startswith("data: ")]
    assert len([e for e in events if e["type"] == "chunk"]) == talk.MAX_LESSON_SENTENCES
    system_prompt, user_data = prompts[0]
    assert "teach" in system_prompt.lower()
    assert user_data["lesson"] == lesson


def test_talk_needs_a_message_or_a_lesson(tmp_path, monkeypatch, client):
    mentor = tmp_path / "backend" / "input" / "asha"
    mentor.mkdir(parents=True)
    (mentor / "talk.mp4").write_bytes(b"clip")
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))

    assert client.post("/talk", json={"mentor": "asha"}).status_code == 422
    assert client.post("/talk", json={"mentor": "asha", "message": "  "}).status_code == 422
    bad_lesson = {"course": "x", "title": ""}
    assert client.post("/talk", json={"mentor": "asha", "lesson": bad_lesson}).status_code == 422


# ---------- editing a mentor and choosing its standard voice ----------


def edit(client, name="asha", images=(), voice=None, **fields):
    files = [("images", (filename, data, "application/octet-stream")) for data, filename in images]
    if voice:
        files.append(("voice", (voice[1], voice[0], "application/octet-stream")))
    return client.patch(f"/mentors/{name}", data=fields, files=files or None)


def profile(studio, name="asha"):
    return json.loads((studio["dir"] / name / "mentor.json").read_text())


def test_upload_can_choose_a_standard_voice(studio, client):
    response = upload(
        client, voice=None, voice_id="en-IN-NeerjaNeural", speech_rate="-10%", speech_pitch="+5Hz"
    )

    assert response.status_code == 201
    assert profile(studio)["voice"] == {
        "voice_id": "en-IN-NeerjaNeural", "rate": "-10%", "pitch": "+5Hz",
    }


@pytest.mark.parametrize(
    "fields",
    [{"voice_id": "not-a-voice"}, {"speech_rate": "fast"}, {"speech_pitch": "+500Hz"}],
)
def test_bad_standard_voice_settings_are_rejected(studio, client, fields):
    assert upload(client, **fields).status_code == 422
    assert list(studio["dir"].iterdir()) == []


def test_mentor_details_list_what_can_be_edited(studio, client):
    upload(client, images=[(b"one", "a.png"), (b"two", "b.webp")], voice_id="en-GB-RyanNeural")

    details = client.get("/mentors/asha").json()

    assert details == {
        "name": "asha",
        "persona": "Kind and patient.",
        "expressiveness": 0.7,
        "voice": {"voice_id": "en-GB-RyanNeural", "rate": "+0%", "pitch": "+0Hz"},
        "own_voice": True,
        "uploaded": True,
        "images": ["image_1.png", "image_2.webp"],
    }
    assert client.get("/mentors/ghost").status_code == 404
    assert client.get("/mentors/asha/images/image_1.png").content == b"one"
    for bad in ["voice.wav", "mentor.json", "..%2Fasha%2Fvoice.wav", "image_9.png"]:
        assert client.get(f"/mentors/asha/images/{bad}").status_code == 404


def test_text_settings_can_be_edited_without_preparing_again(studio, client):
    upload(client)
    studio["prepared"].clear()

    response = edit(
        client, persona="Now brisk and funny.", expressiveness="0.2",
        voice_id="en-US-AriaNeural", speech_rate="+15%", speech_pitch="-3Hz",
    )

    assert response.status_code == 200
    assert profile(studio) == {
        "persona": "Now brisk and funny.",
        "expressiveness": 0.2,
        "uploaded": True,
        "voice": {"voice_id": "en-US-AriaNeural", "rate": "+15%", "pitch": "-3Hz"},
    }
    assert studio["prepared"] == []
    assert sorted(p.name for p in (studio["dir"] / "asha").iterdir()) == [
        "image_1.heic", "mentor.json", "voice.wav",
    ]


def test_fields_that_are_not_sent_keep_their_value(studio, client):
    upload(client, voice_id="en-GB-RyanNeural")

    assert edit(client, persona="Only this changes.").status_code == 200

    saved = profile(studio)
    assert saved["persona"] == "Only this changes."
    assert saved["expressiveness"] == 0.7
    assert saved["voice"]["voice_id"] == "en-GB-RyanNeural"


def test_photos_can_be_added_and_removed(studio, client):
    upload(client, images=[(b"one", "a.png"), (b"two", "b.png")])
    folder = studio["dir"] / "asha"
    (folder / "auto_image_1_abcd1234.mp4").write_bytes(b"clip 1")
    (folder / "auto_image_2_basic.mp4").write_bytes(b"clip 2")
    studio["prepared"].clear()

    response = edit(client, images=[(b"three", "c.avif")], remove_images=["image_1.png"], consent="true")

    assert response.status_code == 200
    assert response.json()["state"] == "preparing"
    assert sorted(p.name for p in folder.iterdir()) == [
        "auto_image_2_basic.mp4", "image_2.png", "image_3.avif", "mentor.json", "voice.wav",
    ]
    assert studio["prepared"] == ["asha"]


def test_the_last_photo_cannot_be_removed(studio, client):
    upload(client)

    assert edit(client, remove_images=["image_1.heic"]).status_code == 422
    assert (studio["dir"] / "asha" / "image_1.heic").exists()


def test_only_the_mentors_own_photos_can_be_removed(studio, client):
    upload(client, images=[(b"one", "a.png"), (b"two", "b.png")])

    for bad in ["voice.wav", "../owl/talk.mp4", "image_7.png"]:
        assert edit(client, remove_images=[bad]).status_code == 422
    assert (studio["dir"] / "asha" / "voice.wav").exists()


def test_voice_recording_can_be_replaced_or_removed(studio, client):
    upload(client)
    folder = studio["dir"] / "asha"

    assert edit(client, voice=(b"new-voice", "new.m4a"), consent="true").status_code == 200
    assert (folder / "voice.wav").read_bytes() == b"wav:new-voice"

    assert edit(client, remove_voice="true").status_code == 200
    assert not (folder / "voice.wav").exists()


def test_new_photos_or_voice_need_permission_again(studio, client):
    upload(client)

    assert edit(client, images=[(b"x", "x.png")]).status_code == 422
    assert edit(client, voice=(b"v", "v.wav")).status_code == 422


def test_a_rejected_edit_changes_nothing(studio, client):
    upload(client)
    studio["bad_images"].add(b"not-an-image")
    before = profile(studio)

    response = edit(
        client, persona="Should not be saved.", consent="true",
        images=[(b"good", "g.png"), (b"not-an-image", "bad.png")],
    )

    assert response.status_code == 422
    assert profile(studio) == before
    assert sorted(p.name for p in (studio["dir"] / "asha").iterdir()) == [
        "image_1.heic", "mentor.json", "voice.wav",
    ]


def test_only_uploaded_mentors_can_be_edited(studio, client):
    builtin = studio["dir"] / "owl"
    builtin.mkdir()
    (builtin / "talk.mp4").write_bytes(b"clip")

    assert edit(client, name="owl", persona="x").status_code == 403
    assert edit(client, name="ghost", persona="x").status_code == 404


def test_the_chosen_standard_voice_is_used_in_conversation(tmp_path, monkeypatch):
    mentor = tmp_path / "backend" / "input" / "asha"
    mentor.mkdir(parents=True)
    (mentor / "talk.mp4").write_bytes(b"clip")
    (mentor / "mentor.json").write_text(
        json.dumps({"voice": {"voice_id": "en-GB-SoniaNeural", "rate": "-10%", "pitch": "+4Hz"}})
    )
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(talk, "TALK_DIR", str(tmp_path / "outputs" / "talk"))
    monkeypatch.setattr(talk, "reply_pieces", lambda *_args: iter([("gemini", "Hello.")]))
    used = []

    async def stock_tts(_text, output, **kwargs):
        used.append(kwargs)
        Path(output).write_bytes(b"audio")

    async def no_face(*_args, **_kwargs):
        return False

    monkeypatch.setattr(talk, "generate_tts", stock_tts)
    monkeypatch.setattr(talk.media_workers, "animate", no_face)

    async def collect():
        return [event async for event in talk.talk("asha", "hi", [])]

    asyncio.run(collect())

    assert used == [{"voice_id": "en-GB-SoniaNeural", "rate": "-10%", "pitch": "+4Hz"}]


@pytest.mark.parametrize(
    "voice",
    [{"voice_id": "nope"}, {"voice_id": "en-GB-SoniaNeural", "rate": "quick"}, "text", None],
)
def test_bad_voice_settings_in_a_mentor_file_are_ignored(tmp_path, voice):
    from backend.services import mentor_pack

    mentor = tmp_path / "backend" / "input" / "asha"
    mentor.mkdir(parents=True)
    (mentor / "mentor.json").write_text(json.dumps({"voice": voice}))

    assert mentor_pack.get_mentor_stock_voice("asha", base_dir=str(tmp_path)) is None
