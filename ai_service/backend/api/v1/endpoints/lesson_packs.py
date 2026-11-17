"""Prepared lessons: queue them, follow progress, and fetch one to play."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.api.v1.endpoints.talk import TalkLesson, _known_mentor
from backend.services import lesson_packs

router = APIRouter()

MAX_LESSONS = 300


class PackLessons(BaseModel):
    mentor: str = Field(..., min_length=1, max_length=50)
    lessons: list[TalkLesson] = Field(..., min_length=1, max_length=MAX_LESSONS)
    # The lesson on screen and the one after it are prepared before a whole course.
    urgent: bool = False


class PackLesson(BaseModel):
    mentor: str = Field(..., min_length=1, max_length=50)
    lesson: TalkLesson


@router.post("/lesson-packs/prepare")
async def prepare_lessons(body: PackLessons):
    """Start (or continue) preparing lessons in the background."""
    mentor = _known_mentor(body.mentor)
    lessons = [lesson.model_dump() for lesson in body.lessons]
    lesson_packs.prepare(mentor, lessons, urgent=body.urgent)
    return lesson_packs.status(mentor, lessons)


@router.post("/lesson-packs/status")
def lessons_status(body: PackLessons):
    mentor = _known_mentor(body.mentor)
    return lesson_packs.status(mentor, [lesson.model_dump() for lesson in body.lessons])


@router.post("/lesson-packs/pack")
def lesson_pack(body: PackLesson):
    """The sentences of a prepared lesson, each with its clip; "sentences" only when ready."""
    return lesson_packs.Pack(_known_mentor(body.mentor), body.lesson.model_dump()).playable()


@router.delete("/lesson-packs/{mentor}/queue", status_code=204)
def stop_preparing(mentor: str):
    """Stop preparing this mentor's lessons; what is finished is kept."""
    lesson_packs.cancel(_known_mentor(mentor))


@router.get("/pack-media/{mentor}/{filename}")
def pack_media(mentor: str, filename: str):
    path = lesson_packs.media_path(_known_mentor(mentor), filename)
    if not path:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})
