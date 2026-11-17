"""Local AI provider: a model served by Ollama on this computer (AI_PROVIDER=ollama).

Start Ollama and download the model once, for example:

    ollama pull qwen2.5:7b

Prompts and response checks are inherited from GeminiProvider; only the call to
the model differs.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from backend.config import (
    OLLAMA_GPU_LAYERS,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
    OLLAMA_URL,
)
from backend.services.providers.base import serialize_user_data
from backend.services.providers.gemini_provider import GeminiProvider

# Room for long lesson transcripts; Ollama's default context is smaller.
CONTEXT_TOKENS = 8192
# Keep the model in memory between calls so live mentor replies stay quick.
KEEP_ALIVE = "30m"

# Ollama constrains the reply to this JSON schema, so a small local model cannot
# return prose, code fences or the wrong top-level type.
RESPONSE_FORMATS: dict[str, Any] = {
    "object": {"type": "object"},
    "array": {"type": "array", "items": {"type": "object"}},
}


def generate_ollama_content(
    system_instruction: str | None,
    user_content: dict[str, Any] | str,
    *,
    expect: str = "text",
    max_tokens: int | None = None,
    model: str = OLLAMA_MODEL,
    think: bool = False,
    timeout: float | None = None,
) -> str:
    """Send one chat turn to Ollama and return the reply text.

    ``think`` lets a thinking model (see ``model_choices``) reason before it
    answers; Ollama keeps that reasoning out of the returned text. Only pass it
    for models that support thinking, and allow for the extra tokens it uses.
    """
    body = _chat_body(
        system_instruction, user_content, expect, max_tokens, model, think, stream=False
    )
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/chat", json=body, timeout=timeout or OLLAMA_TIMEOUT_SECONDS
        )
    except httpx.ConnectError as error:
        raise RuntimeError(
            f"Ollama is not running at {OLLAMA_URL}; start it with 'ollama serve'."
        ) from error
    if response.status_code == 404:
        raise RuntimeError(
            f"Ollama model {model!r} is not downloaded; run 'ollama pull {model}'."
        )
    response.raise_for_status()
    return response.json()["message"]["content"]


def stream_ollama_content(
    system_instruction: str | None,
    user_content: dict[str, Any] | str | list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    model: str = OLLAMA_MODEL,
    think: bool = False,
    timeout: float | None = None,
) -> Iterator[str]:
    """Yield the reply text piece by piece while Ollama writes it.

    ``user_content`` may be a ready list of chat messages. Ollama reuses the work
    done for the start of a prompt it has seen before, so put the part that stays
    the same between calls (such as a lesson script) first. Closing the iterator
    early stops the model.
    """
    body = _chat_body(
        system_instruction, user_content, "text", max_tokens, model, think, stream=True
    )
    try:
        with httpx.stream(
            "POST", f"{OLLAMA_URL}/api/chat", json=body, timeout=timeout or OLLAMA_TIMEOUT_SECONDS
        ) as response:
            if response.status_code == 404:
                raise RuntimeError(
                    f"Ollama model {model!r} is not downloaded; run 'ollama pull {model}'."
                )
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                text = (event.get("message") or {}).get("content") or ""
                if text:
                    yield text
                if event.get("done"):
                    return
    except httpx.ConnectError as error:
        raise RuntimeError(
            f"Ollama is not running at {OLLAMA_URL}; start it with 'ollama serve'."
        ) from error


def _chat_body(
    system_instruction: str | None,
    user_content: dict[str, Any] | str | list[dict[str, str]],
    expect: str,
    max_tokens: int | None,
    model: str,
    think: bool,
    stream: bool,
) -> dict[str, Any]:
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    if isinstance(user_content, list):
        messages += user_content
    else:
        if isinstance(user_content, dict):
            user_content = serialize_user_data(user_content)
        messages.append({"role": "user", "content": user_content})

    options: dict[str, Any] = {
        "num_ctx": CONTEXT_TOKENS,
        # Structured output works best close to deterministic.
        "temperature": 0.7 if expect == "text" else 0.2,
    }
    if max_tokens:
        options["num_predict"] = max_tokens
    if OLLAMA_GPU_LAYERS and model == OLLAMA_MODEL:
        # The same on every call: Ollama reloads a model whose layer count changes.
        options["num_gpu"] = OLLAMA_GPU_LAYERS

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        # Off by default: a thinking model (e.g. qwen3.5) would otherwise spend the
        # token budget reasoning and can return an empty answer. Others ignore False.
        "think": think,
        "keep_alive": KEEP_ALIVE,
        "options": options,
    }
    if expect in RESPONSE_FORMATS:
        body["format"] = RESPONSE_FORMATS[expect]
    return body


def loaded_model_gpu_bytes(model: str) -> int | None:
    """GPU memory the loaded ``model`` holds, 0 when it is not loaded, None if unknown."""
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/ps", timeout=5)
        response.raise_for_status()
        for item in response.json().get("models", []):
            if item.get("name") == model or item.get("model") == model:
                return int(item.get("size_vram") or 0)
        return 0
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def load_ollama_model(model: str) -> None:
    """Ask Ollama to load a model now, so the next reply does not wait for it."""
    body = _chat_body(None, [], "text", None, model, False, stream=False)
    try:
        httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=OLLAMA_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        pass  # the next reply loads it instead


def release_ollama_model(model: str) -> None:
    """Ask Ollama to unload a model now, freeing GPU memory for the voice and
    face workers; the next request loads it again (a few seconds)."""
    try:
        httpx.post(
            f"{OLLAMA_URL}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30
        )
    except httpx.HTTPError:
        pass  # Ollama unloads it on its own after KEEP_ALIVE anyway


class OllamaProvider(GeminiProvider):
    """Primary AI provider running a local model through Ollama."""

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return OLLAMA_MODEL

    def _generate(
        self, system_instruction: str, user_data: dict[str, Any], *, expect: str = "text"
    ) -> str:
        return generate_ollama_content(system_instruction, user_data, expect=expect)

    def _generate_prompt(self, prompt: str, *, expect: str = "text") -> str:
        return generate_ollama_content(None, prompt, expect=expect)
