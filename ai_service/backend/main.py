from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import security
from backend.api.v1.router import api_v1_router
from backend.job_shutdown import wait_for_jobs_to_finish
from backend.services.job_tracker import (
    active_jobs,
    job_status,
    shutdown_failed_jobs,
)
import backend.services.job_tracker as job_tracker
from backend.services.media_processor import check_ffmpeg

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
base_output_path = os.path.join(BASE_DIR, "outputs")
video_output_path = os.path.join(base_output_path, "video")
text_output_path = os.path.join(base_output_path, "text")
talk_output_path = os.path.join(base_output_path, "talk")

os.makedirs(video_output_path, exist_ok=True)
os.makedirs(text_output_path, exist_ok=True)
os.makedirs(talk_output_path, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_ffmpeg()
    job_tracker.shutting_down = False
    yield

    job_tracker.shutting_down = True
    await wait_for_jobs_to_finish(
        active_jobs=active_jobs,
        job_status=job_status,
        shutdown_failed_jobs=shutdown_failed_jobs,
        timeout_seconds=0,
    )


def create_app() -> FastAPI:
    """FastAPI application factory with route mounts, middleware, and lifespan."""
    app = FastAPI(
        title="AI Lesson Generator",
        lifespan=lifespan,
    )

    # Only the website may call this service from a browser. Server-side callers
    # such as the Node API send no Origin header and are not affected.
    app.middleware("http")(security.guard)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(security.allowed_origins()),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static file routes
    app.mount(
        "/video-stream",
        StaticFiles(directory=video_output_path),
        name="video-stream",
    )
    app.mount(
        "/transcript-stream",
        StaticFiles(directory=text_output_path),
        name="transcript-stream",
    )

    app.mount(
        "/talk-media",
        StaticFiles(directory=talk_output_path),
        name="talk-media",
    )

    # Include v1 API routes
    app.include_router(api_v1_router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
