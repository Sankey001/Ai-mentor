import json
from pathlib import Path

import httpx
import pytest

from backend.services import media_workers


@pytest.fixture(autouse=True)
def reset_breaker(monkeypatch):
    media_workers._down_until.clear()
    monkeypatch.setattr(media_workers.config, "VOICE_WORKER_URL", "http://voice.test")
    monkeypatch.setattr(media_workers.config, "FACE_WORKER_URL", "http://face.test")
    yield
    media_workers._down_until.clear()


def use_transport(monkeypatch, handler):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(media_workers, "_transport", httpx.MockTransport(record))
    return requests


def writes_output(request):
    Path(json.loads(request.content)["output"]).write_bytes(b"media")
    return httpx.Response(200, json={"ok": True})


@pytest.mark.asyncio
async def test_speak_sends_the_text_and_voice_sample(tmp_path, monkeypatch):
    requests = use_transport(monkeypatch, writes_output)
    output = tmp_path / "line.wav"

    assert await media_workers.speak("Hi there.", "/packs/a/voice.wav", str(output), expressiveness=0.8)

    assert str(requests[0].url) == "http://voice.test/speak"
    assert json.loads(requests[0].content) == {
        "text": "Hi there.",
        "reference": "/packs/a/voice.wav",
        "output": str(output),
        "expressiveness": 0.8,
    }
    assert output.read_bytes() == b"media"


@pytest.mark.asyncio
async def test_animate_sends_the_clips_and_audio(tmp_path, monkeypatch):
    requests = use_transport(monkeypatch, writes_output)
    output = tmp_path / "line.mp4"

    assert await media_workers.animate(["/a/1.mp4", "/a/2.mp4"], "/t/line.wav", str(output))

    assert str(requests[0].url) == "http://face.test/animate"
    assert json.loads(requests[0].content) == {
        "clips": ["/a/1.mp4", "/a/2.mp4"],
        "audio": "/t/line.wav",
        "output": str(output),
    }


@pytest.mark.asyncio
async def test_unconfigured_worker_is_never_called(tmp_path, monkeypatch):
    requests = use_transport(monkeypatch, writes_output)
    monkeypatch.setattr(media_workers.config, "VOICE_WORKER_URL", "")

    assert not await media_workers.speak("Hi.", "/v.wav", str(tmp_path / "o.wav"))
    assert requests == []


@pytest.mark.asyncio
async def test_worker_error_falls_back_and_removes_partial_output(tmp_path, monkeypatch):
    output = tmp_path / "line.mp4"

    def fails(request):
        output.write_bytes(b"partial")
        return httpx.Response(500, json={"error": "face not found"})

    use_transport(monkeypatch, fails)

    assert not await media_workers.animate(["/a/1.mp4"], "/t/a.wav", str(output))
    assert not output.exists()


@pytest.mark.asyncio
async def test_worker_reply_without_a_file_counts_as_failure(tmp_path, monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(200, json={"ok": True}))

    assert not await media_workers.speak("Hi.", "/v.wav", str(tmp_path / "o.wav"))


@pytest.mark.asyncio
async def test_unreachable_worker_is_skipped_for_a_while(tmp_path, monkeypatch):
    def refused(request):
        raise httpx.ConnectError("connection refused")

    requests = use_transport(monkeypatch, refused)

    assert not await media_workers.speak("One.", "/v.wav", str(tmp_path / "1.wav"))
    assert not await media_workers.speak("Two.", "/v.wav", str(tmp_path / "2.wav"))
    assert len(requests) == 1

    media_workers._down_until["http://voice.test"] = 0
    assert not await media_workers.speak("Three.", "/v.wav", str(tmp_path / "3.wav"))
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_a_content_error_does_not_mark_the_worker_down(tmp_path, monkeypatch):
    requests = use_transport(monkeypatch, lambda request: httpx.Response(400, json={}))

    await media_workers.animate(["/a/1.mp4"], "/t/a.wav", str(tmp_path / "1.mp4"))
    await media_workers.animate(["/a/1.mp4"], "/t/a.wav", str(tmp_path / "2.mp4"))

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_health_reports_each_worker(monkeypatch):
    def health(request):
        if request.url.host == "voice.test":
            return httpx.Response(200, json={"ok": True})
        raise httpx.ConnectError("down")

    use_transport(monkeypatch, health)

    assert await media_workers.health() == {"voice": True, "face": False}
