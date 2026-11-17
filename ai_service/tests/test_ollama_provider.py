"""AI_PROVIDER=ollama: a local model replaces Gemini as the primary provider."""

import contextlib
import importlib
import json
import json as json_module
from types import SimpleNamespace

import httpx
import pytest

from backend.schemas.lessons import (
    ExplainCodeRequest,
    FlashcardRequest,
    LessonRequest,
    QuizRequest,
    SyllabusRequest,
)
from backend.services import talk
from backend.services.providers import manager

# The package re-exports a provider *instance* named ollama_provider, so load the module itself.
ollama_provider = importlib.import_module("backend.services.providers.ollama_provider")
OllamaProvider = ollama_provider.OllamaProvider
generate_ollama_content = ollama_provider.generate_ollama_content

QUIZ = {
    "questions": [
        {
            "question": f"Question {n}?",
            "options": ["A", "B", "C", "D"],
            "correct_index": 1,
            "explanation": "Because B.",
        }
        for n in range(4)
    ]
}


class FakeOllama:
    """Stands in for httpx.post to Ollama and records every request body."""

    def __init__(self, reply="A reply.", status=200):
        self.reply = reply
        self.status = status
        self.bodies = []

    def __call__(self, url, json, timeout):
        self.url = url
        self.bodies.append(json)
        request = httpx.Request("POST", url)
        body = {"message": {"role": "assistant", "content": self.reply}}
        return httpx.Response(self.status, json=body, request=request)

    @contextlib.contextmanager
    def stream(self, method, url, json, timeout):
        """Stands in for httpx.stream: the reply arrives one word per line."""
        self.url = url
        self.bodies.append(json)
        words = self.reply.split(" ")
        lines = [
            {"message": {"content": word + (" " if n < len(words) - 1 else "")}, "done": False}
            for n, word in enumerate(words)
        ] + [{"message": {"content": ""}, "done": True}]
        content = "".join(json_module.dumps(line) + "\n" for line in lines)
        self.response = httpx.Response(
            self.status, content=content.encode(), request=httpx.Request(method, url)
        )
        yield self.response


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(ollama_provider.httpx, "post", fake)
    monkeypatch.setattr(ollama_provider.httpx, "stream", fake.stream)
    return fake


@pytest.fixture
def use_ollama(monkeypatch):
    monkeypatch.setattr(manager, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(talk, "AI_PROVIDER", "ollama")


def test_request_sends_system_and_user_messages(ollama):
    reply = generate_ollama_content("Be brief.", {"topic": "loops"}, max_tokens=50)

    assert reply == "A reply."
    assert ollama.url.endswith("/api/chat")
    body = ollama.bodies[0]
    assert body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert json.loads(body["messages"][1]["content"]) == {"topic": "loops"}
    assert body["options"]["num_predict"] == 50
    assert body["think"] is False
    assert "format" not in body


@pytest.mark.parametrize(
    ("expect", "schema_type"), [("object", "object"), ("array", "array")]
)
def test_json_replies_are_constrained_by_a_schema(ollama, expect, schema_type):
    generate_ollama_content("Return JSON.", {}, expect=expect)

    assert ollama.bodies[0]["format"]["type"] == schema_type


def test_missing_model_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(ollama_provider.httpx, "post", FakeOllama(status=404))

    with pytest.raises(RuntimeError, match="ollama pull"):
        generate_ollama_content("x", {})


def test_ollama_not_running_gives_a_clear_error(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama_provider.httpx, "post", refuse)

    with pytest.raises(RuntimeError, match="ollama serve"):
        generate_ollama_content("x", {})


def test_lesson_script_reports_ollama_and_its_model(ollama):
    ollama.reply = "Loops repeat\ncode."
    request = LessonRequest(course="Python", topic="Loops", celebrity="Ada")

    script, provider, model = OllamaProvider().generate_lesson_script(request)

    assert (script, provider, model) == (
        "Loops repeat code.",
        "ollama",
        ollama_provider.OLLAMA_MODEL,
    )


def test_manager_uses_ollama_when_selected(use_ollama, ollama, monkeypatch):
    def gemini_must_not_run(*_args, **_kwargs):
        raise AssertionError("Gemini was called")

    monkeypatch.setattr(manager, "generate_gemini_content", gemini_must_not_run)

    ollama.reply = json.dumps(QUIZ)
    quiz = manager.generate_quiz(QuizRequest(lesson="Loops repeat code."))
    assert len(quiz.questions) == 4

    ollama.reply = json.dumps(
        [
            {"id": n, "front": f"Question {n}?", "back": "Answer.", "hint": "Hint"}
            for n in range(1, 6)
        ]
    )
    cards = manager.generate_flashcards(
        FlashcardRequest(transcript="Loops repeat code. " * 10, count=5)
    )
    assert [card.front for card in cards] == [f"Question {n}?" for n in range(1, 6)]

    ollama.reply = json.dumps({"title": "Module 1", "lessons": []})
    syllabus = manager.generate_syllabus(SyllabusRequest(course_title="Python"))
    assert syllabus["title"] == "Module 1"

    ollama.reply = json.dumps(
        {
            "summary": "Prints hi.",
            "explanation": [],
            "issues": [],
            "suggested_code": "",
            "time_complexity": "O(1)",
            "space_complexity": "O(1)",
        }
    )
    result = manager.explain_code(
        ExplainCodeRequest(code="print('hi')", language="python", mode="explain")
    )
    assert result["summary"] == "Prints hi."

    formats = [body.get("format", {}).get("type") for body in ollama.bodies]
    assert formats == ["object", "array", "object", "object"]


def test_mentor_reply_uses_ollama_when_selected(use_ollama, ollama, monkeypatch):
    monkeypatch.setattr(
        talk,
        "generate_gemini_content",
        lambda *_a, **_k: SimpleNamespace(text="from gemini"),
    )
    ollama.reply = "Loops repeat code."

    assert talk.write_reply("Ada", "What is a loop?", []) == (
        "Loops repeat code.",
        "ollama",
    )


def test_streamed_reply_arrives_piece_by_piece(ollama):
    ollama.reply = "Loops repeat code."
    messages = [{"role": "user", "content": "lesson"}, {"role": "user", "content": "question"}]

    pieces = list(ollama_provider.stream_ollama_content("Be brief.", messages, max_tokens=50))

    assert pieces == ["Loops ", "repeat ", "code."]
    body = ollama.bodies[0]
    assert body["stream"] is True and body["think"] is False
    assert body["keep_alive"] == ollama_provider.KEEP_ALIVE
    assert body["messages"] == [{"role": "system", "content": "Be brief."}, *messages]
    assert body["options"]["num_predict"] == 50


def test_streamed_reply_reports_a_missing_model(ollama):
    ollama.status = 404

    with pytest.raises(RuntimeError, match="not downloaded"):
        list(ollama_provider.stream_ollama_content("x", "hi"))


def test_gpu_memory_of_a_loaded_model(monkeypatch):
    def ps(url, timeout):
        models = [{"name": "gemma3:latest", "size_vram": 2_900_000_000}]
        return httpx.Response(200, json={"models": models}, request=httpx.Request("GET", url))

    monkeypatch.setattr(ollama_provider.httpx, "get", ps)

    assert ollama_provider.loaded_model_gpu_bytes("gemma3:latest") == 2_900_000_000
    assert ollama_provider.loaded_model_gpu_bytes("qwen3.5:9b") == 0
