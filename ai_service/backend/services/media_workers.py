"""Clients for the warm voice and face workers.

Every call returns False instead of raising, so callers can fall back. A
worker that cannot be reached is skipped for a short time (circuit breaker),
so a conversation does not wait on a dead worker for every sentence.
"""

from __future__ import annotations

import os
import time

import httpx

import backend.config as config
from backend.utils.logging_utils import log_event, short_error

RETRY_AFTER_SECONDS = 20
_down_until: dict[str, float] = {}
_transport: httpx.AsyncBaseTransport | None = None  # replaced in tests


async def _call(name: str, url: str, path: str, payload: dict, timeout: float) -> bool:
    output = payload["output"]
    if not url or time.monotonic() < _down_until.get(url, 0):
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=_transport) as client:
            response = await client.post(url.rstrip("/") + path, json=payload)
        response.raise_for_status()
        if not os.path.isfile(output):
            raise RuntimeError(f"{name} worker returned no file")
        return True
    except httpx.TransportError as error:
        _down_until[url] = time.monotonic() + RETRY_AFTER_SECONDS
        log_event("WARN", f"{name} worker unreachable; using fallback", error=short_error(error))
    except Exception as error:
        log_event("WARN", f"{name} worker failed; using fallback", error=short_error(error))
    try:
        os.remove(output)
    except OSError:
        pass
    return False


async def speak(
    text: str, reference: str, output: str, expressiveness: float = 0.5, timeout: float | None = None
) -> bool:
    """Narrate ``text`` in the voice recorded in ``reference``."""
    payload = {"text": text, "reference": reference, "output": output, "expressiveness": expressiveness}
    return await _call("Voice", config.VOICE_WORKER_URL, "/speak", payload, timeout or 30 + len(text) / 5)


async def animate(clips: list[str], audio: str, output: str, timeout: float = 600) -> bool:
    """Render the mentor's clips with the mouth following ``audio``."""
    payload = {"clips": clips, "audio": audio, "output": output}
    return await _call("Face", config.FACE_WORKER_URL, "/animate", payload, timeout)


async def prepare(clips: list[str], timeout: float = 1200) -> bool:
    """Ask the face worker to get a mentor's clips ready, so its first reply is fast."""
    url = config.FACE_WORKER_URL
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=_transport) as client:
            return (await client.post(url.rstrip("/") + "/prepare", json={"clips": clips})).is_success
    except httpx.HTTPError as error:
        log_event("WARN", "Face worker could not prepare a mentor", error=short_error(error))
        return False


async def health() -> dict[str, bool]:
    """Report which workers answer their health check."""
    result = {}
    for name, url in (("voice", config.VOICE_WORKER_URL), ("face", config.FACE_WORKER_URL)):
        try:
            async with httpx.AsyncClient(timeout=2, transport=_transport) as client:
                result[name] = bool(url) and (await client.get(url.rstrip("/") + "/health")).is_success
        except httpx.HTTPError:
            result[name] = False
    return result
