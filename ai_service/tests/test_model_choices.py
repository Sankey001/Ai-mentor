"""The mentor chat's model picker: listing models and honouring the learner's pick."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import api
from backend import config
from backend.services import model_choices, talk

TAGS = {
    "models": [
        {"name": "qwen3.5:9b", "size": 6_600_000_000, "digest": "q1"},
        {"name": "gemma3:latest", "size": 3_300_000_000, "digest": "g1"},
    ]
}
CAPABILITIES = {"qwen3.5:9b": ["completion", "thinking"], "gemma3:latest": ["completion"]}


@pytest.fixture
def ollama_tags(monkeypatch):
    """Ollama is running with the models in TAGS; only qwen3.5 can think."""
    shown = []

    def get(url, timeout):
        return httpx.Response(200, json=TAGS, request=httpx.Request("GET", url))

    def post(url, json, timeout):
        shown.append(json["model"])
        body = {"capabilities": CAPABILITIES[json["model"]]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(model_choices.httpx, "get", get)
    monkeypatch.setattr(model_choices.httpx, "post", post)
    monkeypatch.setattr(model_choices, "_thinking_cache", {})
    return shown


@pytest.fixture
def ollama_down(monkeypatch):
    def get(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(model_choices.httpx, "get", get)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "real-key")
    monkeypatch.setattr(config, "GROQ_API_KEY", "your_groq_api_key")


def test_lists_local_models_and_apis(ollama_tags, keys):
    choices = model_choices.list_choices()

    local = [o for o in choices["options"] if o["source"] == "local"]
    assert [o["model"] for o in local] == ["gemma3:latest", "qwen3.5:9b"]
    assert local[0]["label"] == "gemma3:latest (3.3 GB)"
    api_options = {o["provider"]: o["available"] for o in choices["options"] if o["source"] == "api"}
    # A placeholder key from .env.example does not count as a key.
    assert api_options == {"gemini": True, "groq": False}


def test_reports_which_models_can_think_and_asks_ollama_once(ollama_tags, keys):
    first = model_choices.list_choices()
    model_choices.list_choices()

    thinking = {o["model"]: o["thinking"] for o in first["options"]}
    assert thinking["qwen3.5:9b"] is True
    assert thinking["gemma3:latest"] is False
    assert thinking[config.GEMINI_MODEL] is False
    # Capabilities are cached by digest, so a second listing asks nothing.
    assert sorted(ollama_tags) == ["gemma3:latest", "qwen3.5:9b"]
    assert model_choices.can_think(("ollama", "qwen3.5:9b")) is True
    assert model_choices.can_think(("ollama", "gemma3:latest")) is False
    assert model_choices.can_think(("gemini", config.GEMINI_MODEL)) is False
    assert model_choices.can_think(None) is False


def test_thinking_reply_turns_on_think_with_room_for_reasoning(monkeypatch):
    calls = []

    def ollama(system_instruction, user_data, **kwargs):
        calls.append(kwargs)
        return iter(["Answer."])

    monkeypatch.setattr(talk, "stream_ollama_content", ollama)

    talk.write_reply("ada", "hi", [], None, ("ollama", "qwen3.5:9b"), True)
    talk.write_reply("ada", "hi", [], None, ("ollama", "qwen3.5:9b"))

    assert [(c["think"], c["max_tokens"], c["timeout"]) for c in calls] == [
        (True, talk.THINKING_TOKENS, talk.THINKING_TIMEOUT_SECONDS),
        (False, 400, None),
    ]


def test_thinking_that_runs_out_of_room_answers_without_thinking(monkeypatch):
    calls = []

    def ollama(system_instruction, user_data, **kwargs):
        calls.append(kwargs["think"])
        return iter([""] if kwargs["think"] else ["Quick ", "answer."])

    def online(*_args, **_kwargs):
        raise AssertionError("a local pick must not reach an online API")

    monkeypatch.setattr(talk, "stream_ollama_content", ollama)
    monkeypatch.setattr(talk, "generate_gemini_content", online)
    monkeypatch.setattr(talk, "generate_groq_content", online)

    reply = talk.write_reply("ada", "hi", [], None, ("ollama", "qwen3.5:9b"), True)

    assert reply == ("Quick answer.", "ollama")
    assert calls == [True, False]


@pytest.mark.parametrize(
    ("worker_url", "provider", "fits", "released"),
    [
        ("http://127.0.0.1:8102", "ollama", False, ["qwen3.5:9b"]),
        # A model small enough to share the GPU with the workers stays loaded.
        ("http://127.0.0.1:8102", "ollama", True, []),
        ("", "ollama", False, []),  # no voice or face worker: keep the model loaded
        ("http://127.0.0.1:8102", "gemini", False, []),  # nothing local to unload
    ],
)
def test_only_a_large_local_model_is_unloaded_before_voice_and_face_work(
    monkeypatch, tmp_path, worker_url, provider, fits, released
):
    import asyncio

    calls = []
    monkeypatch.setattr(config, "FACE_WORKER_URL", worker_url)
    monkeypatch.setattr(talk, "TALK_DIR", str(tmp_path))
    monkeypatch.setattr(talk, "reply_pieces", lambda *_a, **_k: iter([(provider, "Hello.")]))
    monkeypatch.setattr(talk, "fits_beside_workers", lambda _model: fits)
    monkeypatch.setattr(talk, "release_ollama_model", calls.append)
    monkeypatch.setattr(talk, "get_mentor_clips", lambda _mentor: [])
    monkeypatch.setattr(talk.media_workers, "speak", lambda *_a, **_k: asyncio.sleep(0, False))
    monkeypatch.setattr(talk.media_workers, "animate", lambda *_a, **_k: asyncio.sleep(0, False))

    async def run():
        return [e async for e in talk.talk("ada", "hi", [], None, ("ollama", "qwen3.5:9b"))]

    events = asyncio.run(run())

    assert [e["provider"] for e in events if e["type"] == "reply"] == [provider]
    assert calls == released


@pytest.mark.parametrize(
    ("model", "loaded_bytes", "fits"),
    [
        ("gemma3:latest", 2_900_000_000, True),
        ("gemma3:latest", 0, True),  # not loaded yet: judged by its download size
        ("qwen3.5:9b", 5_600_000_000, False),
        ("qwen3.5:9b", None, False),
        ("unknown:1b", 0, False),
    ],
)
def test_a_model_fits_beside_the_workers_by_its_gpu_memory(
    ollama_tags, monkeypatch, model, loaded_bytes, fits
):
    monkeypatch.setattr(talk, "loaded_model_gpu_bytes", lambda _model: loaded_bytes)

    assert talk.fits_beside_workers(model) is fits


def test_talk_refuses_thinking_for_a_model_that_cannot_think(ollama_tags, keys, monkeypatch):
    monkeypatch.setattr(
        "backend.api.v1.endpoints.talk._known_mentor", lambda name: name.strip().lower()
    )
    client = TestClient(api.app)
    body = {"mentor": "ada", "message": "hi", "provider": "ollama", "think": True}

    response = client.post("/talk", json={**body, "model": "gemma3:latest"})

    assert response.status_code == 422
    assert "Thinking" in response.json()["detail"]


def test_no_local_models_when_ollama_is_down(ollama_down, keys):
    choices = model_choices.list_choices()

    assert all(option["source"] == "api" for option in choices["options"])
    with pytest.raises(ValueError, match="not running"):
        model_choices.validate_choice("ollama", "gemma3:latest")


def test_validate_choice(ollama_tags, keys):
    assert model_choices.validate_choice("ollama", "qwen3.5:9b") == ("ollama", "qwen3.5:9b")
    assert model_choices.validate_choice("gemini", None) == ("gemini", config.GEMINI_MODEL)
    with pytest.raises(ValueError, match="not installed"):
        model_choices.validate_choice("ollama", "llama9:999b")
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        model_choices.validate_choice("groq", None)


def test_local_pick_uses_that_model_and_never_goes_online(monkeypatch):
    calls = []

    def ollama(system_instruction, user_data, **kwargs):
        calls.append(kwargs["model"])
        raise RuntimeError("model crashed")

    def online(*_args, **_kwargs):
        raise AssertionError("a local pick must not reach an online API")

    monkeypatch.setattr(talk, "stream_ollama_content", ollama)
    monkeypatch.setattr(talk, "generate_gemini_content", online)
    monkeypatch.setattr(talk, "generate_groq_content", online)

    reply = talk.write_reply("ada", "hi", [], None, ("ollama", "qwen3.5:9b"))

    assert calls == ["qwen3.5:9b", "qwen3.5:9b"]  # tried twice, never online
    assert reply == (talk.FALLBACK_REPLY, "fallback")


def test_api_pick_tries_the_chosen_api_first(monkeypatch):
    monkeypatch.setattr(
        talk, "generate_gemini_content", lambda *_a, **_k: SimpleNamespace(text="From Gemini.")
    )

    def groq(*_args, **_kwargs):
        message = SimpleNamespace(content="From Groq.")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(talk, "generate_groq_content", groq)

    assert talk.write_reply("ada", "hi", [], None, ("groq", model_choices.GROQ_MODEL)) == (
        "From Groq.",
        "groq",
    )
    assert talk.write_reply("ada", "hi", [], None, ("gemini", config.GEMINI_MODEL)) == (
        "From Gemini.",
        "gemini",
    )


def test_models_endpoint(ollama_tags, keys):
    response = TestClient(api.app).get("/talk/models")

    assert response.status_code == 200
    assert response.json()["default"]["provider"] in model_choices.PROVIDERS


def test_talk_rejects_a_model_that_is_not_installed(ollama_tags, keys, monkeypatch):
    monkeypatch.setattr(
        "backend.api.v1.endpoints.talk._known_mentor", lambda name: name.strip().lower()
    )

    response = TestClient(api.app).post(
        "/talk",
        json={"mentor": "ada", "message": "hi", "provider": "ollama", "model": "nope:1b"},
    )

    assert response.status_code == 422
    assert "not installed" in response.json()["detail"]
