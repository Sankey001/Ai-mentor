"""The AI models the mentor chat can use: local Ollama models and online APIs.

The classroom lists these so a learner can pick where replies are written:
on this computer (Ollama) or by an online API (Gemini, Groq).
"""

from __future__ import annotations

import httpx

from backend import config

GROQ_MODEL = "llama-3.3-70b-versatile"
PROVIDERS = ("ollama", "gemini", "groq")

# Whether a local model can think step by step, by model digest. A model's
# capabilities only change when it is replaced, which changes the digest.
_thinking_cache: dict[str, bool] = {}


def _can_think(name: str, digest: str) -> bool:
    """True when Ollama lists "thinking" among the model's capabilities."""
    if digest and digest in _thinking_cache:
        return _thinking_cache[digest]
    try:
        response = httpx.post(f"{config.OLLAMA_URL}/api/show", json={"model": name}, timeout=5)
        response.raise_for_status()
        thinking = "thinking" in (response.json().get("capabilities") or [])
    except (httpx.HTTPError, ValueError):
        return False  # unknown for now; asked again on the next listing
    if digest:
        _thinking_cache[digest] = thinking
    return thinking


def _has_key(value: str | None) -> bool:
    """A key counts only when it is set and not the .env.example placeholder."""
    return bool(value) and not value.strip().lower().startswith("your_")


def local_models() -> list[dict]:
    """Models installed in Ollama, or [] when Ollama is not running."""
    try:
        response = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2)
        response.raise_for_status()
        models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return []
    return sorted(
        (
            {
                "name": item["name"],
                "size_gb": round(item.get("size", 0) / 1e9, 1),
                "thinking": _can_think(item["name"], item.get("digest", "")),
            }
            for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ),
        key=lambda item: item["name"],
    )


def default_choice() -> dict:
    """The provider and model used when the learner has not picked one."""
    if config.AI_PROVIDER == "ollama":
        return {"source": "local", "provider": "ollama", "model": config.OLLAMA_MODEL}
    return {"source": "api", "provider": "gemini", "model": config.GEMINI_MODEL}


def list_choices() -> dict:
    """Every model the chat can use, with whether it is usable right now."""
    options = [
        {
            "source": "local",
            "provider": "ollama",
            "model": item["name"],
            "label": f"{item['name']} ({item['size_gb']} GB)",
            "available": True,
            "thinking": item["thinking"],
        }
        for item in local_models()
    ]
    options += [
        {
            "source": "api",
            "provider": "gemini",
            "model": config.GEMINI_MODEL,
            "label": f"Gemini ({config.GEMINI_MODEL})",
            "available": _has_key(config.GEMINI_API_KEY),
            "thinking": False,
        },
        {
            "source": "api",
            "provider": "groq",
            "model": GROQ_MODEL,
            "label": "Groq (Llama 3.3 70B)",
            "available": _has_key(config.GROQ_API_KEY),
            "thinking": False,
        },
    ]
    return {"default": default_choice(), "options": options}


def can_think(choice: tuple[str, str] | None) -> bool:
    """True when the (provider, model) pick is a local model that can think."""
    if not choice or choice[0] != "ollama":
        return False
    return any(item["name"] == choice[1] and item["thinking"] for item in local_models())


def validate_choice(provider: str, model: str | None) -> tuple[str, str]:
    """Return (provider, model) for a learner's pick, or raise ValueError saying why not."""
    if provider == "ollama":
        installed = {item["name"] for item in local_models()}
        if not installed:
            raise ValueError("Ollama is not running, so no local model is available.")
        model = model or config.OLLAMA_MODEL
        if model not in installed:
            raise ValueError(f"The local model {model!r} is not installed in Ollama.")
        return provider, model
    if provider == "gemini":
        if not _has_key(config.GEMINI_API_KEY):
            raise ValueError("Gemini needs GEMINI_API_KEY in ai_service/backend/.env.")
        return provider, config.GEMINI_MODEL
    if provider == "groq":
        if not _has_key(config.GROQ_API_KEY):
            raise ValueError("Groq needs GROQ_API_KEY in ai_service/backend/.env.")
        return provider, GROQ_MODEL
    raise ValueError(f"Unknown provider {provider!r}.")
