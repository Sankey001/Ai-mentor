"""Tests for the worker helpers and the app launcher (no GPU models needed)."""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR / "workers"))
sys.path.insert(0, str(SERVICE_DIR))

import face_worker  # noqa: E402
import voice_worker  # noqa: E402
import start_app  # noqa: E402
import worker_http  # noqa: E402


# ---------- worker_http ----------


def test_paths_must_stay_inside_the_allowed_folders(tmp_path):
    allowed = tmp_path / "media"
    allowed.mkdir()
    inside = allowed / "a.wav"
    inside.write_bytes(b"x")

    assert worker_http.check_path(str(inside), [str(allowed)]) == str(inside)
    assert worker_http.check_path(str(allowed / "new.mp4"), [str(allowed)], must_exist=False)
    for bad in [str(tmp_path / "other.wav"), str(allowed / ".." / "x.wav"), "", None, 5]:
        with pytest.raises(ValueError):
            worker_http.check_path(bad, [str(allowed)], must_exist=False)
    with pytest.raises(ValueError):
        worker_http.check_path(str(allowed / "missing.wav"), [str(allowed)])


def test_text_is_split_into_speakable_chunks():
    text = "First sentence here. Second one! Third one? " + "word " * 80 + "end."
    chunks = worker_http.split_text(text, max_chars=60)

    assert chunks[0] == "First sentence here. Second one! Third one?"
    assert all(len(chunk) <= 60 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()
    assert worker_http.split_text("   ", max_chars=60) == []


@pytest.fixture
def server():
    def echo(payload):
        if payload.get("bad"):
            raise ValueError("bad request")
        if payload.get("crash"):
            raise RuntimeError("model crashed")
        return {"echo": payload["value"]}

    httpd = worker_http.make_server(0, {"/echo": echo}, name="test")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_worker_server_answers_health_and_routes(server):
    assert call(server + "/health") == (200, {"ok": True, "worker": "test"})
    assert call(server + "/echo", {"value": 3}) == (200, {"echo": 3})


def test_worker_server_maps_errors_to_status_codes(server):
    assert call(server + "/echo", {"bad": True})[0] == 400
    assert call(server + "/echo", {"crash": True}) == (500, {"error": "RuntimeError"})
    assert call(server + "/echo", raw=b"{not json")[0] == 400
    assert call(server + "/echo", raw=b"[1, 2]")[0] == 400
    assert call(server + "/missing", {"value": 1})[0] == 404
    assert call(server + "/echo", raw=b"x" * (worker_http.MAX_BODY_BYTES + 1))[0] == 413


def test_worker_server_only_listens_locally():
    httpd = worker_http.make_server(0, {}, name="test")
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


# ---------- face_worker ----------


def test_avatar_cache_key_changes_when_a_clip_changes(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"one")
    first = face_worker.avatar_key([str(clip)])

    assert face_worker.avatar_key([str(clip)]) == first
    clip.write_bytes(b"three")
    assert face_worker.avatar_key([str(clip)]) != first


def test_frames_are_grouped_by_clip_and_play_forward_then_backward():
    owners = [0, 0, 0, 1, 1, 2]
    keep = [0, 2, 3, 4]  # frame 1 had no face; clip 2 had none at all

    assert face_worker.build_segments(owners, keep) == [[0, 1, 1, 0], [2, 3, 3, 2]]


def test_face_worker_imports_without_gpu_libraries():
    assert "torch" not in face_worker.__dict__


# ---------- start_app ----------


def test_env_file_is_parsed(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nA=1\nB = two words \nC=\"quoted\"\nD='single'\nbroken line\nE=\n",
        encoding="utf-8",
    )

    assert start_app.read_env(str(env)) == {
        "A": "1",
        "B": "two words",
        "C": "quoted",
        "D": "single",
        "E": "",
    }
    assert start_app.read_env(str(tmp_path / "missing")) == {}


def test_launch_plan_skips_workers_that_are_not_installed(tmp_path):
    python = tmp_path / "python"
    python.write_text("")
    musetalk = tmp_path / "MuseTalk"
    (musetalk / "scripts").mkdir(parents=True)

    plan, warnings = start_app.plan_processes(
        {"VOICE_WORKER_PYTHON": str(python)}, str(tmp_path), port=8000
    )

    assert [process.name for process in plan] == ["voice", "api"]
    assert any("face" in warning for warning in warnings)

    plan, warnings = start_app.plan_processes(
        {
            "VOICE_WORKER_PYTHON": str(python),
            "FACE_WORKER_PYTHON": str(python),
            "MUSETALK_DIR": str(musetalk),
        },
        str(tmp_path),
        port=8000,
    )
    assert [process.name for process in plan] == ["voice", "face", "api"]
    assert len(warnings) == 1 and "website" in warnings[0]

    frontend = tmp_path.parent / "frontend"
    (frontend / "node_modules").mkdir(parents=True)
    (frontend / ".env").write_text("")
    with_site, _warnings = start_app.plan_processes({}, str(tmp_path), port=8000)
    names = [process.name for process in with_site]
    assert names == (["api", "web"] if start_app.shutil.which("npm") else ["api"])
    face = plan[1]
    assert face.command[0] == str(python)
    assert "--musetalk-dir" in face.command
    assert plan[2].command[-2:] == ["--port", "8000"]


def test_crashing_process_is_restarted_only_a_few_times():
    policy = start_app.RestartPolicy(limit=3, window_seconds=60)
    now = time.monotonic()

    assert [policy.allow(now + n) for n in range(4)] == [True, True, True, False]
    assert policy.allow(now + 200)


def test_image_step_runs_only_when_liveportrait_is_installed(tmp_path):
    python = tmp_path / "python"
    python.write_text("")
    liveportrait = tmp_path / "LivePortrait"
    liveportrait.mkdir()

    env = {"PORTRAIT_PYTHON": str(python), "LIVEPORTRAIT_DIR": str(liveportrait)}
    assert start_app.portrait_command(env, str(tmp_path)) is None
    assert start_app.portrait_command({}, str(tmp_path)) is None

    (liveportrait / "inference.py").write_text("")
    command = start_app.portrait_command(env, str(tmp_path))
    assert command[0] == str(python)
    assert command[1].endswith("portrait_maker.py")
    assert command[command.index("--liveportrait-dir") + 1] == str(liveportrait)


def test_black_and_white_frames_are_detected():
    np = pytest.importorskip("numpy")  # present in the face worker's environment

    gray = np.full((8, 8, 3), 120, dtype=np.uint8)
    gray[..., 0] += 1  # compression noise
    colour = gray.copy()
    colour[..., 2] = 200

    assert face_worker.is_grayscale(gray)
    assert not face_worker.is_grayscale(colour)


def test_stopping_a_process_also_stops_what_it_started(tmp_path):
    import signal
    import subprocess

    marker = tmp_path / "child.pid"
    script = f"sleep 60 & echo $! > {marker}; wait"
    process = start_app.Process("web", ["sh", "-c", script], str(tmp_path))
    start_app.start(process)
    try:
        for _ in range(50):
            if marker.exists() and marker.read_text().strip():
                break
            time.sleep(0.1)
        child = int(marker.read_text())

        start_app.signal_group(process, signal.SIGTERM)
        process.popen.wait(timeout=5)
        for _ in range(50):
            if subprocess.run(["kill", "-0", str(child)], capture_output=True).returncode != 0:
                break
            time.sleep(0.1)
        assert subprocess.run(["kill", "-0", str(child)], capture_output=True).returncode != 0
    finally:
        start_app.signal_group(process, signal.SIGKILL)


def test_a_calm_or_lively_delivery_uses_the_standard_voice_model():
    assert voice_worker.wanted_model(0.5, turbo_allowed=True) == "turbo"
    assert voice_worker.wanted_model(0.52, turbo_allowed=True) == "turbo"
    assert voice_worker.wanted_model(0.2, turbo_allowed=True) == "standard"
    assert voice_worker.wanted_model(0.8, turbo_allowed=True) == "standard"
    assert voice_worker.wanted_model(0.5, turbo_allowed=False) == "standard"
    assert "torch" not in voice_worker.__dict__
