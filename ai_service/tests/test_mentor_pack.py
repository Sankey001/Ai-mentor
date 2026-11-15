import json
from pathlib import Path

import pytest

from backend.services import media_processor, mentor_pack


def make_mentor(tmp_path, name):
    mentor_dir = tmp_path / "backend" / "input" / name
    mentor_dir.mkdir(parents=True)
    return mentor_dir


def test_a_new_mentor_is_recognised_by_its_folder(tmp_path):
    mentor_dir = make_mentor(tmp_path, "asha_dev-2")
    (mentor_dir / "talk.mp4").write_bytes(b"clip")

    assert media_processor.resolve_mentor_dir(" Asha_Dev-2 ", base_dir=str(tmp_path)) == str(
        mentor_dir
    )
    clips = media_processor.get_mentor_clips("asha_dev-2", base_dir=str(tmp_path))
    assert [Path(c).name for c in clips] == ["talk.mp4"]


@pytest.mark.parametrize("name", ["../input", "a/b", "a.b", "", None, "x" * 51, "nobody"])
def test_unsafe_or_missing_mentor_names_use_the_default_mentor(tmp_path, name):
    default_dir = make_mentor(tmp_path, "modi")

    assert media_processor.resolve_mentor_dir(name, base_dir=str(tmp_path)) == str(default_dir)


def test_mentor_without_any_folder_has_no_pack(tmp_path):
    (tmp_path / "backend" / "input").mkdir(parents=True)

    assert media_processor.resolve_mentor_dir("owl", base_dir=str(tmp_path)) is None
    assert mentor_pack.get_mentor_voice_sample("owl", base_dir=str(tmp_path)) is None
    assert mentor_pack.get_mentor_profile("owl", base_dir=str(tmp_path)) == {
        "persona": "",
        "expressiveness": 0.5,
    }


def test_voice_sample_is_found_in_the_mentor_folder(tmp_path):
    mentor_dir = make_mentor(tmp_path, "owl")
    assert mentor_pack.get_mentor_voice_sample("owl", base_dir=str(tmp_path)) is None

    (mentor_dir / "voice.mp3").write_bytes(b"mp3")
    assert Path(mentor_pack.get_mentor_voice_sample("owl", base_dir=str(tmp_path))).name == (
        "voice.mp3"
    )

    (mentor_dir / "voice.wav").write_bytes(b"wav")
    assert Path(mentor_pack.get_mentor_voice_sample("owl", base_dir=str(tmp_path))).name == (
        "voice.wav"
    )


def test_profile_reads_persona_and_expressiveness(tmp_path):
    mentor_dir = make_mentor(tmp_path, "owl")
    (mentor_dir / "mentor.json").write_text(
        json.dumps({"persona": "  Dry humour, short sentences.  ", "expressiveness": 0.8}),
        encoding="utf-8",
    )

    assert mentor_pack.get_mentor_profile("owl", base_dir=str(tmp_path)) == {
        "persona": "Dry humour, short sentences.",
        "expressiveness": 0.8,
    }


@pytest.mark.parametrize(
    "content, expected",
    [
        ("{not json", {"persona": "", "expressiveness": 0.5}),
        ("[1, 2]", {"persona": "", "expressiveness": 0.5}),
        (json.dumps({"persona": 42, "expressiveness": "loud"}), {"persona": "", "expressiveness": 0.5}),
        (json.dumps({"expressiveness": 7}), {"persona": "", "expressiveness": 1.0}),
        (json.dumps({"expressiveness": -3}), {"persona": "", "expressiveness": 0.0}),
        (json.dumps({"expressiveness": True}), {"persona": "", "expressiveness": 0.5}),
    ],
)
def test_profile_ignores_bad_values(tmp_path, content, expected):
    mentor_dir = make_mentor(tmp_path, "owl")
    (mentor_dir / "mentor.json").write_text(content, encoding="utf-8")

    assert mentor_pack.get_mentor_profile("owl", base_dir=str(tmp_path)) == expected


def test_profile_limits_persona_length(tmp_path):
    mentor_dir = make_mentor(tmp_path, "owl")
    (mentor_dir / "mentor.json").write_text(json.dumps({"persona": "a" * 5000}), encoding="utf-8")

    persona = mentor_pack.get_mentor_profile("owl", base_dir=str(tmp_path))["persona"]
    assert len(persona) == mentor_pack.MAX_PERSONA_CHARS


def test_persona_is_added_to_the_lesson_prompt_of_both_providers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from backend.schemas.lessons import LessonRequest
    import importlib

    # The package also exports provider instances under these names, so load the modules.
    base = importlib.import_module("backend.services.providers.base")
    gemini_provider = importlib.import_module("backend.services.providers.gemini_provider")
    groq_provider = importlib.import_module("backend.services.providers.groq_provider")

    mentor_dir = make_mentor(tmp_path, "owl")
    (mentor_dir / "mentor.json").write_text(
        json.dumps({"persona": "Calls every bug a gremlin."}), encoding="utf-8"
    )
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    prompts = {}

    def fake_gemini(system_instruction, _user_data, **_kwargs):
        prompts["gemini"] = system_instruction
        return SimpleNamespace(text="A lesson.")

    def fake_groq(system_instruction, _user_data, **_kwargs):
        prompts["groq"] = system_instruction
        message = SimpleNamespace(content="A lesson.")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(gemini_provider, "generate_gemini_content", fake_gemini)
    monkeypatch.setattr(groq_provider, "generate_groq_content", fake_groq)

    with_persona = LessonRequest(course="Python", topic="Loops", celebrity="owl")
    gemini_provider.GeminiProvider().generate_lesson_script(with_persona)
    groq_provider.GroqProvider().generate_lesson_script(with_persona)
    for prompt in prompts.values():
        assert "Calls every bug a gremlin." in prompt
        assert base.UNTRUSTED_INPUT_INSTRUCTION in prompt
        assert "do not claim to be the named person" in prompt

    without_persona = LessonRequest(course="Python", topic="Loops", celebrity="srk")
    gemini_provider.GeminiProvider().generate_lesson_script(without_persona)
    assert "personality" not in prompts["gemini"].lower()
