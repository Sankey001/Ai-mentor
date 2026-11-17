import json
import os
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from backend.services import (
    lesson_packs,
    media_processor,
    media_workers,
    mentor_pack,
    mentor_studio,
    model_choices,
    talk,
)

router = APIRouter()

APP_PAGE = os.path.join(media_processor.BASE_DIR, "app", "index.html")


class TalkTurn(BaseModel):
    role: Literal["learner", "mentor"]
    text: str = Field(..., max_length=1000)


class TalkLesson(BaseModel):
    course: str = Field("", max_length=100)
    module: str = Field("", max_length=200)
    title: str = Field(..., min_length=1, max_length=200)


class TalkRequest(BaseModel):
    mentor: str = Field(..., min_length=1, max_length=50)
    message: str = Field("", max_length=500)
    lesson: TalkLesson | None = None
    history: list[TalkTurn] = Field(default_factory=list, max_length=40)
    # The learner's pick from GET /talk/models; omitted means the configured default.
    provider: Literal["ollama", "gemini", "groq"] | None = None
    model: str | None = Field(None, min_length=1, max_length=100)
    # Let a thinking model reason before it answers (slower, often better).
    think: bool = False

    @model_validator(mode="after")
    def needs_message_or_lesson(self):
        self.message = self.message.strip()
        if not self.message and self.lesson is None:
            raise ValueError("Send a message or a lesson to teach.")
        return self


def _known_mentor(name: str) -> str:
    name = name.strip().lower()
    if name not in {mentor["name"] for mentor in mentor_pack.list_mentors()}:
        raise HTTPException(status_code=404, detail="Mentor not found")
    return name


@router.post("/talk")
async def talk_to_mentor(body: TalkRequest):
    mentor = _known_mentor(body.mentor)
    history = [turn.model_dump() for turn in body.history]
    lesson = body.lesson.model_dump() if body.lesson else None
    choice = None
    if body.provider:
        try:
            choice = model_choices.validate_choice(body.provider, body.model)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    if body.think and not model_choices.can_think(choice):
        raise HTTPException(
            status_code=422,
            detail="Thinking needs a local model that supports it, such as qwen3.5.",
        )

    # A question about a prepared lesson is answered with its script in view.
    script = lesson_packs.script_text(mentor, lesson) if lesson and body.message else None

    async def events():
        async for event in talk.talk(
            mentor, body.message, history, lesson, choice, body.think, script
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/talk/models")
def talk_models():
    """Local Ollama models and online APIs the mentor chat can use."""
    return model_choices.list_choices()


@router.get("/talk/status")
async def talk_status():
    return await media_workers.health()


@router.get("/mentors")
def list_mentors():
    return {"mentors": mentor_studio.list_mentors()}


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"{upload.filename or 'A file'} is too large.")
    return data


@router.post("/mentors", status_code=201)
async def create_mentor(
    background_tasks: BackgroundTasks,
    name: str = Form(..., max_length=50),
    persona: str = Form("", max_length=5000),
    expressiveness: float = Form(0.5),
    consent: bool = Form(False),
    voice_id: str = Form("", max_length=60),
    speech_rate: str = Form("", max_length=10),
    speech_pitch: str = Form("", max_length=10),
    images: list[UploadFile] = File(default_factory=list),
    voice: UploadFile | None = File(None),
):
    if not consent:
        raise HTTPException(
            status_code=422,
            detail="Confirm that you have permission to use this person's face and voice.",
        )
    if len(images) > mentor_studio.MAX_IMAGES:
        raise HTTPException(status_code=422, detail=f"Add at most {mentor_studio.MAX_IMAGES} images.")
    name = name.strip().lower()
    image_files = [
        (image.filename or "", await _read_limited(image, mentor_studio.MAX_IMAGE_BYTES))
        for image in images
    ]
    voice_file = (
        (voice.filename or "", await _read_limited(voice, mentor_studio.MAX_VOICE_BYTES))
        if voice is not None
        else None
    )
    try:
        stock_voice = mentor_studio.voice_settings(voice_id, speech_rate, speech_pitch)
        mentor_studio.create_mentor(
            name, persona, expressiveness, image_files, voice_file, stock_voice
        )
    except mentor_studio.StudioError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error
    background_tasks.add_task(mentor_studio.prepare_mentor, name)
    return {"name": name, "state": "preparing"}


@router.get("/mentors/{name}")
def mentor_details(name: str):
    try:
        return mentor_studio.mentor_details(name.strip().lower())
    except mentor_studio.StudioError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error


@router.get("/mentors/{name}/images/{filename}")
def mentor_image(name: str, filename: str):
    try:
        return FileResponse(
            mentor_studio.mentor_image_path(name.strip().lower(), filename),
            # An uploaded file is only ever shown as a picture, never run as a page.
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
    except mentor_studio.StudioError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error


@router.patch("/mentors/{name}")
async def update_mentor(
    name: str,
    background_tasks: BackgroundTasks,
    persona: str | None = Form(None, max_length=5000),
    expressiveness: float | None = Form(None),
    voice_id: str | None = Form(None, max_length=60),
    speech_rate: str = Form("", max_length=10),
    speech_pitch: str = Form("", max_length=10),
    remove_images: list[str] = Form(default_factory=list),
    remove_voice: bool = Form(False),
    consent: bool = Form(False),
    images: list[UploadFile] = File(default_factory=list),
    voice: UploadFile | None = File(None),
):
    name = name.strip().lower()
    if (images or voice is not None) and not consent:
        raise HTTPException(
            status_code=422,
            detail="Confirm that you have permission to use this person's face and voice.",
        )
    if len(images) > mentor_studio.MAX_IMAGES:
        raise HTTPException(status_code=422, detail=f"Add at most {mentor_studio.MAX_IMAGES} images.")
    image_files = [
        (image.filename or "", await _read_limited(image, mentor_studio.MAX_IMAGE_BYTES))
        for image in images
    ]
    voice_file = (
        (voice.filename or "", await _read_limited(voice, mentor_studio.MAX_VOICE_BYTES))
        if voice is not None
        else None
    )
    try:
        photos_changed = mentor_studio.update_mentor(
            name,
            persona=persona,
            expressiveness=expressiveness,
            stock_voice=mentor_studio.voice_settings(voice_id, speech_rate, speech_pitch),
            clear_stock_voice=voice_id == "",
            remove_images=remove_images,
            new_images=image_files,
            new_voice=voice_file,
            remove_voice=remove_voice,
        )
    except mentor_studio.StudioError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error
    if photos_changed:
        background_tasks.add_task(mentor_studio.prepare_mentor, name)
    return {"name": name, "state": "preparing" if photos_changed else "ready"}


@router.delete("/mentors/{name}", status_code=204)
def delete_mentor(name: str):
    try:
        mentor_studio.delete_mentor(name.strip().lower())
    except mentor_studio.StudioError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error
    return Response(status_code=204)


@router.get("/mentors/{name}/idle.mp4")
def mentor_idle_clip(name: str):
    clips = media_processor.get_mentor_clips(_known_mentor(name))
    return FileResponse(clips[0], media_type="video/mp4")


@router.get("/app")
def app_page():
    return FileResponse(APP_PAGE, media_type="text/html")
