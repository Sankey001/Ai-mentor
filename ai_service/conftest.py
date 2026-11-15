import os

import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def reset_shutdown_state():
    from backend.services import job_tracker

    job_tracker.shutting_down = False
    job_tracker.active_jobs.clear()
    job_tracker.shutdown_failed_jobs.clear()
    yield
    job_tracker.shutting_down = False
    job_tracker.active_jobs.clear()
    job_tracker.shutdown_failed_jobs.clear()


@pytest.fixture(autouse=True)
def isolate_mentor_packs(monkeypatch, tmp_path_factory):
    """Keep tests independent of mentor packs added to the local input folder."""
    from backend.services import media_processor

    empty_base = tmp_path_factory.mktemp("no-mentor-packs")
    monkeypatch.setattr(media_processor, "BASE_DIR", str(empty_base))


@pytest.fixture(autouse=True)
def disable_media_workers(monkeypatch):
    """Keep tests from calling voice or face workers running on this machine."""
    import backend.config as config

    monkeypatch.setattr(config, "VOICE_WORKER_URL", "")
    monkeypatch.setattr(config, "FACE_WORKER_URL", "")


@pytest.fixture(autouse=True)
def reset_mentor_preparation_state():
    from backend.services import mentor_studio

    mentor_studio._states.clear()
    yield
    mentor_studio._states.clear()


@pytest.fixture(autouse=True)
def reset_rate_limits():
    from backend import security

    security.reset_rate_limits()
    yield
    security.reset_rate_limits()
