"""Start the AI Mentor app with one command.

    .venv/bin/python start_app.py            # then open http://127.0.0.1:8000/app

Starts the API, the face worker (lip-sync) and the voice worker (cloned
voices), and restarts any of them that crash. A worker that is not installed,
or keeps crashing, is left off: the app keeps working with stock voices and
the mentor's idle clip instead.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field

SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_PORT = 8101
FACE_PORT = 8102


def read_env(path: str) -> dict[str, str]:
    """Read KEY=VALUE lines from a .env file; a missing file gives {}."""
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as env_file:
            lines = env_file.read().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


class RestartPolicy:
    """Allow at most ``limit`` restarts within ``window_seconds``."""

    def __init__(self, limit: int = 5, window_seconds: float = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self.times: list[float] = []

    def allow(self, now: float) -> bool:
        self.times = [t for t in self.times if now - t < self.window]
        if len(self.times) >= self.limit:
            return False
        self.times.append(now)
        return True


@dataclass
class Process:
    name: str
    command: list[str]
    cwd: str
    health_url: str | None = None
    policy: RestartPolicy = field(default_factory=RestartPolicy)
    popen: subprocess.Popen | None = None
    stopped: bool = False


def plan_processes(env: dict[str, str], service_dir: str, port: int, host: str = "127.0.0.1"):
    """Return (processes to start, warnings about workers that will stay off)."""
    plan, warnings = [], []
    workers = os.path.join(service_dir, "workers")

    voice_python = env.get("VOICE_WORKER_PYTHON", "")
    if os.path.isfile(voice_python):
        plan.append(Process(
            "voice",
            [voice_python, os.path.join(workers, "voice_worker.py"), "--port", str(VOICE_PORT),
             "--allowed-root", service_dir],
            workers,
            f"http://127.0.0.1:{VOICE_PORT}/health",
        ))
    else:
        warnings.append("voice worker is not set up (VOICE_WORKER_PYTHON); mentors use stock voices")

    face_python = env.get("FACE_WORKER_PYTHON", "")
    musetalk = env.get("MUSETALK_DIR", "")
    if os.path.isfile(face_python) and os.path.isdir(os.path.join(musetalk, "scripts")):
        plan.append(Process(
            "face",
            [face_python, os.path.join(workers, "face_worker.py"), "--port", str(FACE_PORT),
             "--musetalk-dir", musetalk,
             "--cache-dir", os.path.join(service_dir, "outputs", "avatar_cache"),
             "--allowed-root", service_dir,
             "--prepare-dir", os.path.join(service_dir, "backend", "input")],
            workers,
            f"http://127.0.0.1:{FACE_PORT}/health",
        ))
    else:
        warnings.append(
            "face worker is not set up (FACE_WORKER_PYTHON, MUSETALK_DIR); "
            "mentors talk over their idle clip"
        )

    plan.append(Process(
        "api",
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", host,
         "--port", str(port)],
        service_dir,
    ))
    # The website (original AI Mentor frontend) with the classroom and mentor studio pages.
    frontend = os.path.join(os.path.dirname(service_dir), "frontend")
    npm = shutil.which("npm")
    if npm and os.path.isdir(os.path.join(frontend, "node_modules")) and os.path.isfile(
        os.path.join(frontend, ".env")
    ):
        plan.append(Process("web", [npm, "run", "dev", "--", "--host", host], frontend))
    else:
        warnings.append("website is not set up (frontend/node_modules, frontend/.env); "
                        "use the basic page at /app")
    return plan, warnings


def portrait_command(env: dict[str, str], service_dir: str) -> list[str] | None:
    """Command that turns mentor images into moving clips, or None if it is not set up."""
    python = env.get("PORTRAIT_PYTHON", "")
    liveportrait = env.get("LIVEPORTRAIT_DIR", "")
    if not os.path.isfile(python) or not os.path.isfile(os.path.join(liveportrait, "inference.py")):
        return None
    return [python, os.path.join(service_dir, "workers", "portrait_maker.py"),
            "--input-dir", os.path.join(service_dir, "backend", "input"),
            "--liveportrait-dir", liveportrait]


def is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def start(process: Process) -> None:
    print(f"[app] starting {process.name}", flush=True)
    # Its own process group, so stopping it also stops anything it started (npm starts node).
    process.popen = subprocess.Popen(process.command, cwd=process.cwd, start_new_session=True)


def signal_group(process: Process, signum: int) -> None:
    try:
        os.killpg(process.popen.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def wait_until_healthy(process: Process, timeout: float) -> None:
    """Wait for a worker to load before starting the next, so GPU loading never overlaps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.popen.poll() is None:
        if is_healthy(process.health_url):
            print(f"[app] {process.name} worker ready", flush=True)
            return
        time.sleep(2)
    print(f"[app] {process.name} worker not ready yet; continuing", flush=True)


LOCAL_DIR = os.path.join(os.path.dirname(SERVICE_DIR), "local")


def local_defaults(env: dict[str, str], local_dir: str = LOCAL_DIR) -> dict[str, str]:
    """Fill worker settings left empty in .env from the single-folder layout:
    local/lip_sync/.venv + local/lip_sync/MuseTalk and local/voice_clone/.venv."""
    scripts, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    guesses = {
        "FACE_WORKER_PYTHON": os.path.join(local_dir, "lip_sync", ".venv", scripts, exe),
        "MUSETALK_DIR": os.path.join(local_dir, "lip_sync", "MuseTalk"),
        "VOICE_WORKER_PYTHON": os.path.join(local_dir, "voice_clone", ".venv", scripts, exe),
    }
    filled = dict(env)
    for key, path in guesses.items():
        if not filled.get(key) and os.path.exists(path):
            filled[key] = path
    return filled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1",
                        help="address the API and website listen on; 0.0.0.0 inside a container")
    args = parser.parse_args()

    # Stop cleanly, taking the workers down too, when asked to terminate.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    env = local_defaults(read_env(os.path.join(SERVICE_DIR, "backend", ".env")))
    # The voice worker's models live in local/models/huggingface unless HF_HOME says otherwise.
    local_models = os.path.join(LOCAL_DIR, "models", "huggingface")
    if os.path.isdir(local_models):
        os.environ.setdefault("HF_HOME", local_models)
    plan, warnings = plan_processes(env, SERVICE_DIR, args.port, args.host)
    for warning in warnings:
        print(f"[app] note: {warning}", flush=True)

    # New or changed mentor images become clips before anything uses the GPU.
    command = portrait_command(env, SERVICE_DIR)
    if command:
        print("[app] checking mentor images", flush=True)
        if subprocess.run(command).returncode != 0:
            print("[app] note: some mentor images could not be turned into clips", flush=True)
    else:
        print("[app] note: image-to-clip step is not set up (PORTRAIT_PYTHON, LIVEPORTRAIT_DIR); "
              "uploaded photos get a simple slow zoom", flush=True)

    # API first so the app opens at once; then the face worker, whose avatar
    # preparation briefly needs extra GPU memory, before the voice worker.
    ordered = sorted(plan, key=lambda p: ["api", "web", "face", "voice"].index(p.name))
    try:
        for process in ordered:
            start(process)
            if process.name == "api":
                print(f"[app] basic page: http://127.0.0.1:{args.port}/app", flush=True)
            elif process.name == "web":
                print("[app] open http://localhost:5173/mentor", flush=True)
            else:
                wait_until_healthy(process, timeout=900)

        while True:  # supervisor: restart anything that crashes
            time.sleep(2)
            for process in plan:
                if process.stopped or process.popen.poll() is None:
                    continue
                if process.policy.allow(time.monotonic()):
                    print(f"[app] {process.name} stopped (code {process.popen.returncode}); restarting",
                          flush=True)
                    start(process)
                else:
                    process.stopped = True
                    print(f"[app] {process.name} keeps crashing; leaving it off", flush=True)
    except KeyboardInterrupt:
        print("\n[app] shutting down", flush=True)
    finally:
        for process in plan:
            if process.popen:
                signal_group(process, signal.SIGTERM)
        for process in plan:
            if process.popen:
                try:
                    process.popen.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
                signal_group(process, signal.SIGKILL)  # anything in the group that ignored SIGTERM


if __name__ == "__main__":
    main()
