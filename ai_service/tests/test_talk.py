import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from backend.services import media_processor, talk


def make_mentor(tmp_path, name, *, clips=1, voice=False, persona=None):
    mentor_dir = tmp_path / "backend" / "input" / name
    mentor_dir.mkdir(parents=True)
    for index in range(clips):
        (mentor_dir / f"talk_{index}.mp4").write_bytes(b"clip")
    if voice:
        (mentor_dir / "voice.wav").write_bytes(b"voice")
    if persona:
        (mentor_dir / "mentor.json").write_text(json.dumps({"persona": persona}))
    return mentor_dir


@pytest.fixture
def mentors(tmp_path, monkeypatch):
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(talk, "TALK_DIR", str(tmp_path / "outputs" / "talk"))
    return tmp_path


@pytest.fixture
def replies(monkeypatch):
    """Stub both LLM providers; tests set the reply or make them fail."""
    state = {"gemini": "Loops repeat code. They save typing!", "groq": None, "prompts": []}

    def gemini(system_instruction, user_data, **_kwargs):
        state["prompts"].append((system_instruction, user_data))
        if state["gemini"] is None:
            raise RuntimeError("gemini down")
        return SimpleNamespace(text=state["gemini"])

    def groq(system_instruction, user_data, **_kwargs):
        if state["groq"] is None:
            raise RuntimeError("groq down")
        message = SimpleNamespace(content=state["groq"])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(talk, "generate_gemini_content", gemini)
    monkeypatch.setattr(talk, "generate_groq_content", groq)
    return state


@pytest.fixture
def media(monkeypatch):
    """Stub the voice worker, stock TTS and face worker; tests switch each on or off."""
    state = {"cloned": True, "stock": True, "face": True, "calls": []}

    async def speak(text, reference, output, **_kwargs):
        state["calls"].append(("speak", text))
        if state["cloned"]:
            Path(output).write_bytes(b"cloned")
        return state["cloned"]

    async def stock_tts(text, output, **_kwargs):
        state["calls"].append(("stock", text))
        if not state["stock"]:
            raise RuntimeError("no internet")
        Path(output).write_bytes(b"stock")

    async def animate(clips, audio, output, **_kwargs):
        state["calls"].append(("animate", Path(audio).name))
        if state["face"]:
            Path(output).write_bytes(b"video")
        return state["face"]

    monkeypatch.setattr(talk.media_workers, "speak", speak)
    monkeypatch.setattr(talk.media_workers, "animate", animate)
    monkeypatch.setattr(talk, "generate_tts", stock_tts)
    return state


def run_talk(mentor="asha", message="What is a loop?", history=None):
    async def collect():
        return [event async for event in talk.talk(mentor, message, history or [])]

    return asyncio.run(collect())


def chunks(events):
    return [event for event in events if event["type"] == "chunk"]


def reply_of(events):
    (reply,) = [event for event in events if event["type"] == "reply"]
    return reply


def test_sentences_are_split_for_speaking():
    assert talk.split_sentences("Hi there! How are you? Fine.") == [
        "Hi there!",
        "How are you?",
        "Fine.",
    ]
    long_sentence = "word " * 80
    pieces = talk.split_sentences(long_sentence, max_chars=100)
    assert all(len(piece) <= 100 for piece in pieces)
    assert " ".join(pieces).split() == long_sentence.split()
    assert talk.split_sentences("   ") == []


def test_reply_is_spoken_sentence_by_sentence_with_lip_sync(mentors, replies, media):
    make_mentor(mentors, "asha", voice=True, persona="Calls bugs gremlins.")

    events = run_talk()

    assert reply_of(events) == {
        "type": "reply",
        "text": "Loops repeat code. They save typing!",
        "provider": "gemini",
    }
    assert [event["text"] for event in events if event["type"] == "text"] == [
        "Loops repeat code.",
        "Loops repeat code. They save typing!",
    ]
    assert [(c["text"], c["kind"], c["voice"], c["face"]) for c in chunks(events)] == [
        ("Loops repeat code.", "video", "cloned", "lipsync"),
        ("They save typing!", "video", "cloned", "lipsync"),
    ]
    assert all(c["url"].startswith("/talk-media/") and c["url"].endswith(".mp4") for c in chunks(events))
    assert events[-1] == {"type": "done"}
    system_prompt, user_data = replies["prompts"][0]
    assert "Calls bugs gremlins." in system_prompt
    assert user_data == {"message": "What is a loop?", "history": []}


def test_media_files_are_written_where_the_urls_point(mentors, replies, media):
    make_mentor(mentors, "asha", voice=True)

    for chunk in chunks(run_talk()):
        relative = chunk["url"].removeprefix("/talk-media/")
        assert (Path(talk.TALK_DIR) / relative).read_bytes() == b"video"


def test_stock_voice_is_used_when_cloning_is_unavailable(mentors, replies, media):
    make_mentor(mentors, "asha", voice=True)
    media["cloned"] = False

    voices = {c["voice"] for c in chunks(run_talk())}

    assert voices == {"stock"}


def test_mentor_without_a_voice_sample_uses_the_stock_voice(mentors, replies, media):
    make_mentor(mentors, "asha")

    assert {c["voice"] for c in chunks(run_talk())} == {"stock"}
    assert not [call for call in media["calls"] if call[0] == "speak"]


def test_audio_is_sent_when_lip_sync_is_unavailable(mentors, replies, media):
    make_mentor(mentors, "asha", voice=True)
    media["face"] = False

    for chunk in chunks(run_talk()):
        assert chunk["kind"] == "audio"
        assert chunk["face"] == "idle"
        assert chunk["url"].endswith(".wav")


def test_text_is_still_sent_when_no_voice_works(mentors, replies, media):
    make_mentor(mentors, "asha", voice=True)
    media["cloned"] = False
    media["stock"] = False

    for chunk in chunks(run_talk()):
        assert chunk["kind"] == "text"
        assert "url" not in chunk
    assert not [call for call in media["calls"] if call[0] == "animate"]


def test_groq_answers_when_gemini_fails(mentors, replies, media):
    make_mentor(mentors, "asha")
    replies["gemini"] = None
    replies["groq"] = "Groq here."

    events = run_talk()

    assert reply_of(events)["provider"] == "groq"
    assert [c["text"] for c in chunks(events)] == ["Groq here."]


def test_mentor_apologises_when_every_llm_fails(mentors, replies, media):
    make_mentor(mentors, "asha")
    replies["gemini"] = None

    events = run_talk()

    assert reply_of(events) == {
        "type": "reply",
        "text": talk.FALLBACK_REPLY,
        "provider": "fallback",
    }
    assert chunks(events)


def test_empty_llm_reply_counts_as_a_failure(mentors, replies, media):
    make_mentor(mentors, "asha")
    replies["gemini"] = "   "
    replies["groq"] = "Backup answer."

    assert reply_of(run_talk())["provider"] == "groq"


def test_long_replies_are_cut_to_a_few_sentences(mentors, replies, media):
    make_mentor(mentors, "asha")
    replies["gemini"] = " ".join(f"Sentence {n}." for n in range(10))

    assert len(chunks(run_talk())) == talk.MAX_REPLY_SENTENCES


def test_history_is_trimmed_before_it_reaches_the_llm(mentors, replies, media):
    make_mentor(mentors, "asha")
    history = [{"role": "learner", "text": f"turn {n}"} for n in range(20)]

    run_talk(history=history)

    sent = replies["prompts"][0][1]["history"]
    assert sent == history[-talk.MAX_HISTORY_TURNS :]


def post_talk(client, body):
    response = client.post("/talk", json=body)
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return response, events


@pytest.fixture
def client():
    with patch.object(api, "check_ffmpeg", return_value=None), TestClient(api.app) as test_client:
        yield test_client


def test_talk_endpoint_streams_server_sent_events(mentors, replies, media, client):
    make_mentor(mentors, "asha", voice=True)

    response, events = post_talk(client, {"mentor": "asha", "message": "What is a loop?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    types = [event["type"] for event in events]
    assert types[0] == "text"
    assert sorted(types) == ["chunk", "chunk", "done", "reply", "text", "text"]
    assert types[-1] == "done"


@pytest.mark.parametrize(
    "body",
    [
        {"mentor": "asha", "message": ""},
        {"mentor": "asha", "message": "x" * 501},
        {"mentor": "a" * 51, "message": "hi"},
        {"mentor": "asha", "message": "hi", "history": [{"role": "system", "text": "x"}]},
        {"mentor": "asha", "message": "hi", "history": [{"role": "learner", "text": "x"}] * 41},
    ],
)
def test_talk_endpoint_rejects_bad_requests(mentors, client, body):
    assert client.post("/talk", json=body).status_code == 422


def test_unknown_mentor_is_rejected(mentors, client):
    make_mentor(mentors, "asha")

    assert client.post("/talk", json={"mentor": "ghost", "message": "hi"}).status_code == 404


def test_mentor_list_shows_only_ready_mentors(mentors, client):
    make_mentor(mentors, "asha", voice=True, persona="Kind.")
    make_mentor(mentors, "bo", clips=0)
    (mentors / "backend" / "input" / "loose.mp4").write_bytes(b"x")

    response = client.get("/mentors")

    assert response.json() == {
        "mentors": [
            {"name": "asha", "voice": "cloned", "persona": True, "state": "ready", "uploaded": False}
        ]
    }


def test_idle_clip_is_served_for_a_mentor(mentors, client):
    make_mentor(mentors, "asha")

    response = client.get("/mentors/asha/idle.mp4")

    assert response.status_code == 200
    assert response.content == b"clip"
    assert client.get("/mentors/ghost/idle.mp4").status_code == 404
    assert client.get("/mentors/..%2Fbackend/idle.mp4").status_code == 404


def test_voice_sample_is_never_served(mentors, client):
    make_mentor(mentors, "asha", voice=True)

    assert client.get("/mentors/asha/voice.wav").status_code == 404


def test_app_page_is_served(client):
    response = client.get("/app")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/talk" in response.text


def test_old_conversation_files_are_cleaned_up(mentors, replies, media):
    old = Path(talk.TALK_DIR) / "old-session"
    old.mkdir(parents=True)
    (old / "0.mp4").write_bytes(b"x")
    import os

    os.utime(old, (0, 0))
    make_mentor(mentors, "asha")

    run_talk()

    assert not old.exists()


def test_sentences_are_cut_while_the_reply_is_still_arriving():
    cutter = talk.SentenceCutter(max_chars=40)

    assert cutter.feed("Pi is 3.") == []  # may still become 3.14
    assert cutter.feed("14. It never") == ["Pi is 3.14."]
    assert cutter.feed(" ends! ") == ["It never ends!"]
    assert cutter.feed("word " * 12) == ["word word word word word word word word"]
    assert cutter.flush() == ["word word word word"]
    assert cutter.flush() == []


def test_first_sentence_is_voiced_while_the_rest_is_being_written(mentors, media, monkeypatch):
    import threading

    make_mentor(mentors, "asha", voice=True)
    first_sentence_voiced = threading.Event()
    speak = talk.media_workers.speak

    async def speak_and_tell(text, *args, **kwargs):
        first_sentence_voiced.set()
        return await speak(text, *args, **kwargs)

    def slow_model(*_args):
        yield "ollama", "Loops repeat code. They"
        # The model only goes on once the first sentence has reached the voice.
        assert first_sentence_voiced.wait(timeout=10)
        yield "ollama", " save typing!"

    monkeypatch.setattr(talk.media_workers, "speak", speak_and_tell)
    monkeypatch.setattr(talk, "reply_pieces", slow_model)

    events = run_talk()

    assert [c["text"] for c in chunks(events)] == ["Loops repeat code.", "They save typing!"]
    assert reply_of(events)["text"] == "Loops repeat code. They save typing!"


def test_the_model_is_stopped_once_enough_sentences_are_written(mentors, media, monkeypatch):
    make_mentor(mentors, "asha")
    state = {"asked": 0, "closed": False}

    def endless_model(*_args):
        try:
            while True:
                state["asked"] += 1
                yield "ollama", f"Sentence {state['asked']}. "
        finally:
            state["closed"] = True

    monkeypatch.setattr(talk, "reply_pieces", endless_model)

    events = run_talk()

    assert len(chunks(events)) == talk.MAX_REPLY_SENTENCES
    assert state["closed"] and state["asked"] <= talk.MAX_REPLY_SENTENCES + 1
    assert reply_of(events)["text"] == "Sentence 1. Sentence 2. Sentence 3. Sentence 4."


def test_lesson_script_goes_first_so_a_local_model_reuses_it(monkeypatch):
    calls = []

    def ollama(system_instruction, messages, **_kwargs):
        calls.append((system_instruction, messages))
        return iter(["Sure."])

    monkeypatch.setattr(talk, "stream_ollama_content", ollama)

    def ask(question):
        pieces = talk.reply_pieces(
            "ada", question, [], {"title": "Loops"}, ("ollama", "gemma3:latest"), False,
            "A loop repeats code.",
        )
        assert list(pieces) == [("ollama", "Sure.")]

    ask("Why?")
    ask("How?")

    (system_1, messages_1), (system_2, messages_2) = calls
    assert system_1 == system_2
    assert messages_1[0] == messages_2[0]
    assert "A loop repeats code." in messages_1[0]["content"]
    assert "Why?" in messages_1[1]["content"] and "How?" in messages_2[1]["content"]


def test_a_long_sentence_is_cut_at_a_comma_rather_than_mid_phrase():
    sentence = (
        "Think of React as a smart toolkit, one that lets you build screens piece by piece, "
        "instead of starting from scratch every single time you need a new page."
    )

    assert talk.split_sentences(sentence, max_chars=100) == [
        "Think of React as a smart toolkit, one that lets you build screens piece by piece,",
        "instead of starting from scratch every single time you need a new page.",
    ]
    # A comma right at the start is not worth a cut.
    assert talk.split_sentences("Well, " + "word " * 30, max_chars=100)[0].startswith("Well, word")
