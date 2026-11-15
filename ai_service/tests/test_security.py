"""Security checks for the endpoints the browser can reach directly."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from backend import security
from backend.services import media_processor, mentor_studio

GOOD_ORIGIN = "http://localhost:5173"
EVIL_ORIGIN = "https://evil.example"


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "backend" / "input" / "asha").mkdir(parents=True)
    (tmp_path / "backend" / "input" / "asha" / "talk.mp4").write_bytes(b"clip")
    monkeypatch.setattr(media_processor, "BASE_DIR", str(tmp_path))
    security.reset_rate_limits()
    with patch.object(api, "check_ffmpeg", return_value=None), TestClient(api.app) as test_client:
        yield test_client
    security.reset_rate_limits()


def test_other_websites_cannot_change_anything(client):
    for method, path in [("POST", "/mentors"), ("PATCH", "/mentors/asha"),
                         ("DELETE", "/mentors/asha"), ("POST", "/talk")]:
        response = client.request(method, path, headers={"Origin": EVIL_ORIGIN})
        assert response.status_code == 403, (method, path)


def test_the_website_and_server_side_callers_are_allowed(client):
    assert client.delete("/mentors/ghost", headers={"Origin": GOOD_ORIGIN}).status_code == 404
    assert client.delete("/mentors/ghost").status_code == 404  # no Origin: curl, the Node API
    assert client.get("/mentors", headers={"Origin": EVIL_ORIGIN}).status_code == 200


def test_cors_headers_are_only_sent_to_known_origins(client):
    allowed = client.get("/mentors", headers={"Origin": GOOD_ORIGIN})
    refused = client.get("/mentors", headers={"Origin": EVIL_ORIGIN})

    assert allowed.headers["access-control-allow-origin"] == GOOD_ORIGIN
    assert "access-control-allow-origin" not in refused.headers


def test_extra_origins_can_be_configured(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://learn.example.com, https://b.example ")
    assert {"https://learn.example.com", "https://b.example"} <= security.allowed_origins()
    assert GOOD_ORIGIN in security.allowed_origins()


def test_uploads_are_refused_by_size_before_they_are_read(client):
    too_big = str(security.MAX_UPLOAD_BYTES + 1)
    response = client.post(
        "/mentors", content=b"x", headers={"Content-Length": too_big, "Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 413


def test_talk_is_rate_limited_per_client(client, monkeypatch):
    monkeypatch.setitem(security.RATE_LIMITS, "talk", (3, 60))
    statuses = [client.post("/talk", json={"mentor": "ghost", "message": "hi"}).status_code for _ in range(5)]

    assert statuses == [404, 404, 404, 429, 429]
    limited = client.post("/talk", json={"mentor": "ghost", "message": "hi"})
    assert int(limited.headers["retry-after"]) >= 1


def test_mentor_changes_are_rate_limited_separately(client, monkeypatch):
    monkeypatch.setitem(security.RATE_LIMITS, "studio", (2, 3600))
    statuses = [client.delete("/mentors/ghost").status_code for _ in range(3)]

    assert statuses == [404, 404, 429]
    assert client.get("/mentors").status_code == 200  # reading is not limited


def test_the_number_of_mentors_is_capped(client, monkeypatch):
    monkeypatch.setattr(mentor_studio, "MAX_MENTORS", 1)
    monkeypatch.setattr(mentor_studio, "is_image", lambda _path: True)

    response = client.post(
        "/mentors", data={"name": "second", "consent": "true"}, files=[("images", ("a.png", b"x"))]
    )

    assert response.status_code == 409
    assert "limit" in response.json()["detail"].lower()


def test_mentor_photos_are_served_as_inert_files(client, tmp_path):
    folder = tmp_path / "backend" / "input" / "asha"
    (folder / "image_1.png").write_bytes(b"png")

    response = client.get("/mentors/asha/images/image_1.png")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


@needs_ffmpeg
def test_vector_and_playlist_files_are_not_accepted_as_photos(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>1</script></svg>')
    playlist = tmp_path / "b.png"
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/x.ts\n#EXT-X-ENDLIST\n")
    photo = tmp_path / "c.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x64",
                    "-frames:v", "1", str(photo)], check=True)

    assert not mentor_studio.is_image(str(svg))
    assert not mentor_studio.is_image(str(playlist))
    assert mentor_studio.is_image(str(photo))
    assert mentor_studio.readable_image(str(svg)) is None


@needs_ffmpeg
def test_enormous_images_are_refused(tmp_path, monkeypatch):
    photo = tmp_path / "wide.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=white:size=300x100",
                    "-frames:v", "1", str(photo)], check=True)
    assert mentor_studio.is_image(str(photo))

    monkeypatch.setattr(mentor_studio, "MAX_IMAGE_SIDE", 200)
    assert not mentor_studio.is_image(str(photo))
    assert mentor_studio.readable_image(str(photo)) is None
