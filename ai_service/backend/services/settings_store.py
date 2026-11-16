"""Read and change the AI service's settings from the Settings page.

Most settings live in ``backend/.env``; saving rewrites only the lines it
changes and keeps comments and every other line. The folder Ollama keeps its
models in is Ollama's own setting (the ``OLLAMA_MODELS`` environment variable),
which on Windows is saved for the current user.

API keys are never sent back: the page only learns whether a key is set and its
last four characters. Changes take effect when the app (and, for the models
folder, Ollama) restarts.
"""

from __future__ import annotations

import os
import re
import sys

from backend import config
from backend.services import model_choices

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(config.__file__)), ".env")
DEFAULT_OLLAMA_MODELS = os.path.join(os.path.expanduser("~"), ".ollama", "models")
MAX_VALUE = 500

# name -> kind. "secret" values are write-only.
FIELDS = {
    "AI_PROVIDER": "provider",
    "OLLAMA_MODEL": "model",
    "OLLAMA_URL": "url",
    "GEMINI_MODEL": "model",
    "GEMINI_API_KEY": "secret",
    "GROQ_API_KEY": "secret",
    "MUSETALK_DIR": "dir",
    "FACE_WORKER_PYTHON": "file",
    "VOICE_WORKER_PYTHON": "file",
}
OLLAMA_MODELS = "OLLAMA_MODELS"
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


class SettingsError(ValueError):
    """A value the page sent cannot be saved; the message says why."""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def read_env_file(path: str | None = None) -> dict[str, str]:
    """KEY=VALUE pairs in the .env file (the first of any repeated key)."""
    values: dict[str, str] = {}
    try:
        with open(path or ENV_PATH, encoding="utf-8") as env_file:
            lines = env_file.read().splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values.setdefault(key.strip(), _unquote(value))
    return values


def _format(value: str) -> str:
    return f'"{value}"' if re.search(r"[\s#]", value) else value


def write_env_file(updates: dict[str, str], path: str | None = None) -> None:
    """Set each key in place (or append it), keeping all other lines as they are."""
    path = path or ENV_PATH
    try:
        with open(path, encoding="utf-8") as env_file:
            lines = env_file.read().splitlines()
    except FileNotFoundError:
        lines = []
    pending = dict(updates)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in pending:
            lines[index] = f"{key}={_format(pending.pop(key))}"
    if pending:
        lines.append("")
        lines += [f"{key}={_format(value)}" for key, value in pending.items()]
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as env_file:
        env_file.write("\n".join(lines) + "\n")
    os.replace(temp, path)


def _user_env(name: str) -> str | None:
    """The variable as saved for this Windows user (what a newly started Ollama sees)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0] or None
    except OSError:
        return None


def _set_user_env(name: str, value: str) -> None:
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    # Tell running programs (Explorer, the Ollama tray app on restart) it changed.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, None)


def ollama_models_dir() -> tuple[str, str]:
    """(folder, where the setting comes from) for Ollama's models."""
    saved = _user_env(OLLAMA_MODELS)
    if saved:
        return saved, "Set for this Windows user"
    if os.environ.get(OLLAMA_MODELS):
        return os.environ[OLLAMA_MODELS], "Set in this app's environment"
    return DEFAULT_OLLAMA_MODELS, "Ollama's default location"


def _is_models_dir(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "blobs")) and os.path.isdir(
        os.path.join(path, "manifests")
    )


def _path_info(path: str | None, kind: str) -> dict:
    exists = bool(path) and (os.path.isdir(path) if kind == "dir" else os.path.isfile(path))
    return {"value": path or "", "exists": exists}


def get_settings() -> dict:
    """Everything the Settings page shows. Secrets appear only as set/not set."""
    saved = read_env_file()
    values = {}
    for name, kind in FIELDS.items():
        value = saved.get(name, "")
        if kind == "secret":
            is_set = bool(value) and not value.lower().startswith("your_")
            values[name] = {"set": is_set, "hint": value[-4:] if is_set else ""}
        elif kind in ("dir", "file"):
            values[name] = _path_info(value, kind)
        else:
            # Not in the file: show the built-in default the app is using.
            values[name] = {"value": value or str(getattr(config, name, "") or "")}

    models_dir, source = ollama_models_dir()
    hf_home = os.environ.get("HF_HOME") or _user_env("HF_HOME") or ""
    return {
        "env_file": ENV_PATH,
        "values": values,
        "ollama_models": {
            "value": models_dir,
            "source": source,
            "exists": _is_models_dir(models_dir),
            "editable": sys.platform == "win32",
            "installed": [item["name"] for item in model_choices.local_models()],
        },
        "huggingface": _path_info(hf_home, "dir"),
    }


def _check(name: str, kind: str, value: str) -> str:
    value = value.strip()
    if len(value) > MAX_VALUE or any(char in value for char in "\r\n\0\""):
        raise SettingsError(f"{name} contains characters that cannot be saved.")
    if kind == "secret":
        return value  # "" clears the key
    if kind == "provider" and value not in ("gemini", "ollama"):
        raise SettingsError("AI_PROVIDER must be gemini or ollama.")
    if kind == "model" and not MODEL_NAME.fullmatch(value):
        raise SettingsError(f"{name} is not a valid model name.")
    if kind == "url" and not re.fullmatch(r"https?://[^\s/]+(:\d+)?/?", value):
        raise SettingsError(f"{name} must look like http://127.0.0.1:11434.")
    if kind == "dir" and value and not os.path.isdir(value):
        raise SettingsError(f"{name}: no folder at {value}.")
    if kind == "file" and value and not os.path.isfile(value):
        raise SettingsError(f"{name}: no file at {value}.")
    return value


def save_settings(changes: dict[str, str]) -> dict:
    """Validate every change, then save them all; returns the new settings."""
    unknown = set(changes) - set(FIELDS) - {OLLAMA_MODELS}
    if unknown:
        raise SettingsError(f"These settings cannot be changed here: {', '.join(sorted(unknown))}.")
    if not all(isinstance(value, str) for value in changes.values()):
        raise SettingsError("Every setting must be text.")

    env_updates = {
        name: _check(name, FIELDS[name], value) for name, value in changes.items() if name in FIELDS
    }
    models_dir = changes.get(OLLAMA_MODELS)
    if models_dir is not None:
        models_dir = _check(OLLAMA_MODELS, "dir", models_dir)
        if sys.platform != "win32":
            raise SettingsError("Set OLLAMA_MODELS in your shell profile on this system.")
        if not _is_models_dir(models_dir):
            raise SettingsError(
                f"{models_dir} is not an Ollama models folder (it needs 'blobs' and 'manifests')."
            )

    if env_updates:
        write_env_file(env_updates)
    if models_dir is not None:
        _set_user_env(OLLAMA_MODELS, models_dir)
    return get_settings()
