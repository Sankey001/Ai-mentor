"""Settings page API: keys stay secret, values are checked, only this computer may use it."""

import pytest
from fastapi.testclient import TestClient

import api
from backend.api.v1.endpoints import settings as settings_endpoint
from backend.services import model_choices, settings_store

ENV = """# Primary AI provider
AI_PROVIDER=gemini
GEMINI_API_KEY=secret-gemini-key-1234
GROQ_API_KEY=your_groq_api_key
OLLAMA_MODEL=gemma3:latest
# keep this comment
CLOUDINARY_API_KEY=untouched
"""


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")
    monkeypatch.setattr(settings_store, "ENV_PATH", str(path))
    monkeypatch.setattr(model_choices, "local_models", lambda: [])
    return path


@pytest.fixture
def local():
    return TestClient(api.app, client=("127.0.0.1", 50000))


def test_keys_are_never_sent_back(env_file, local):
    response = local.get("/settings")

    assert response.status_code == 200
    values = response.json()["values"]
    assert values["GEMINI_API_KEY"] == {"set": True, "hint": "1234"}
    # The .env.example placeholder does not count as a key.
    assert values["GROQ_API_KEY"] == {"set": False, "hint": ""}
    assert "secret-gemini-key" not in response.text
    assert values["OLLAMA_MODEL"] == {"value": "gemma3:latest"}
    # OLLAMA_URL is not in the file, so the built-in default is shown.
    assert values["OLLAMA_URL"] == {"value": "http://127.0.0.1:11434"}


def test_saving_changes_only_those_lines(env_file, local, tmp_path):
    musetalk = tmp_path / "MuseTalk"
    musetalk.mkdir()

    response = local.put(
        "/settings",
        json={
            "changes": {
                "AI_PROVIDER": "ollama",
                "GROQ_API_KEY": "gsk_new_key_9876",
                "MUSETALK_DIR": str(musetalk),
            }
        },
    )

    assert response.status_code == 200
    text = env_file.read_text(encoding="utf-8")
    assert "AI_PROVIDER=ollama" in text
    assert "GROQ_API_KEY=gsk_new_key_9876" in text
    assert f"MUSETALK_DIR={musetalk}" in text or f'MUSETALK_DIR="{musetalk}"' in text
    assert "# keep this comment" in text
    assert "CLOUDINARY_API_KEY=untouched" in text
    assert "GEMINI_API_KEY=secret-gemini-key-1234" in text
    assert response.json()["values"]["GROQ_API_KEY"] == {"set": True, "hint": "9876"}


def test_empty_key_removes_it(env_file, local):
    local.put("/settings", json={"changes": {"GEMINI_API_KEY": ""}})

    assert settings_store.read_env_file(str(env_file))["GEMINI_API_KEY"] == ""


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"AI_PROVIDER": "openai"}, "gemini or ollama"),
        ({"OLLAMA_MODEL": "bad model name"}, "not a valid model"),
        ({"OLLAMA_URL": "file:///etc"}, "must look like"),
        ({"MUSETALK_DIR": "Z:/no/such/folder"}, "no folder"),
        ({"GEMINI_API_KEY": "abc\nINJECTED=1"}, "cannot be saved"),
        ({"CLOUDINARY_API_KEY": "x"}, "cannot be changed here"),
    ],
)
def test_bad_values_are_refused_and_nothing_is_written(env_file, local, changes, message):
    response = local.put("/settings", json={"changes": changes})

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert env_file.read_text(encoding="utf-8") == ENV


def test_ollama_models_folder_must_be_a_real_models_folder(env_file, local, tmp_path, monkeypatch):
    saved = {}
    monkeypatch.setattr(settings_store, "_set_user_env", lambda name, value: saved.update({name: value}))
    monkeypatch.setattr(settings_store.sys, "platform", "win32")
    not_models = tmp_path / "random"
    not_models.mkdir()

    refused = local.put("/settings", json={"changes": {"OLLAMA_MODELS": str(not_models)}})
    assert refused.status_code == 422
    assert saved == {}

    models = tmp_path / "Ollama_Models"
    (models / "blobs").mkdir(parents=True)
    (models / "manifests").mkdir()
    accepted = local.put("/settings", json={"changes": {"OLLAMA_MODELS": str(models)}})
    assert accepted.status_code == 200
    assert saved == {"OLLAMA_MODELS": str(models)}


def test_only_this_computer_may_use_settings(env_file):
    remote = TestClient(api.app, client=("192.168.1.20", 50000))

    assert remote.get("/settings").status_code == 403
    assert remote.put("/settings", json={"changes": {"AI_PROVIDER": "ollama"}}).status_code == 403
    assert env_file.read_text(encoding="utf-8") == ENV


def test_other_websites_cannot_change_settings(env_file, local):
    response = local.put(
        "/settings",
        json={"changes": {"AI_PROVIDER": "ollama"}},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert env_file.read_text(encoding="utf-8") == ENV


def test_local_client_list_is_loopback_only():
    assert settings_endpoint.LOCAL_CLIENTS == {"127.0.0.1", "::1", "localhost"}
