"""Create and remove mentors from uploaded images and a voice recording.

An upload becomes a mentor pack in backend/input/<name>/. Turning the images
into moving clips and preparing the face for lip-sync then runs in the
background; the mentor list reports "preparing", "ready" or "failed".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
from typing import Any

import backend.config as config
import backend.voices as voices
from backend.services import media_processor, media_workers, mentor_pack
from backend.utils.logging_utils import log_event, short_error

MAX_IMAGES = 8
MAX_MENTORS = 50
MAX_IMAGE_SIDE = 12000
RASTER_ONLY = {"svg"}  # vector files can carry scripts and remote references
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VOICE_BYTES = 30 * 1024 * 1024
MAX_PERSONA_CHARS = mentor_pack.MAX_PERSONA_CHARS

# name -> {"state": "preparing" | "ready" | "failed", "detail": str}
_states: dict[str, dict[str, str]] = {}


class StudioError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _input_dir() -> str:
    return os.path.join(media_processor.BASE_DIR, "backend", "input")


def is_image(path: str) -> bool:
    """True when FFmpeg can read the file as a picture, whatever its format."""
    probe = subprocess.run(
        # Local files only: a crafted playlist must not make FFmpeg fetch URLs.
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        codec, width, height = probe.stdout.strip().split(",")[:3]
        width, height = int(width), int(height)
    except ValueError:
        return False
    if codec in RASTER_ONLY:
        return False
    return 0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE


def readable_image(path: str) -> str | None:
    """Return a path FFmpeg can read for this image, converting it to PNG if needed.

    Older FFmpeg builds cannot read some formats (HEIC, for one); Pillow can.
    Returns None when the file is not an image at all.
    """
    if is_image(path):
        return path
    try:
        from PIL import Image, ImageOps

        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        converted = os.path.splitext(path)[0] + ".png"
        with Image.open(path) as image:
            if max(image.size) > MAX_IMAGE_SIDE:
                return None
            image.seek(0)
            ImageOps.exif_transpose(image).convert("RGB").save(converted, "PNG")
    except Exception:
        return None
    if converted != path:
        os.remove(path)
    return converted if is_image(converted) else None


def convert_voice(source: str, target: str) -> bool:
    """Convert any audio recording to the WAV the voice worker reads."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-i", source,
         "-vn", "-ac", "1", "-ar", "24000", target],
        capture_output=True,
    )
    return result.returncode == 0 and os.path.isfile(target)


def _is_uploaded(name: str) -> bool:
    try:
        with open(os.path.join(_input_dir(), name, mentor_pack.PROFILE_NAME), encoding="utf-8") as f:
            return json.load(f).get("uploaded") is True
    except (OSError, ValueError, AttributeError):
        return False


def list_mentors() -> list[dict[str, Any]]:
    """Mentors that can be used, plus uploaded ones that are still being prepared."""
    mentors = {m["name"]: m for m in mentor_pack.list_mentors()}
    input_dir = _input_dir()
    for name in os.listdir(input_dir) if os.path.isdir(input_dir) else []:
        if name not in mentors and media_processor.MENTOR_NAME_PATTERN.fullmatch(name) and _is_uploaded(name):
            mentors[name] = {
                "name": name,
                "voice": "cloned" if mentor_pack.get_mentor_voice_sample(name) else "stock",
                "persona": bool(mentor_pack.get_mentor_profile(name)["persona"]),
            }
    result = []
    for name in sorted(mentors):
        has_clips = bool(media_processor.get_mentor_clips(name))
        state = _states.get(name, {}).get("state") or ("ready" if has_clips else "preparing")
        if state == "ready" and not has_clips:
            state = "failed"
        entry = {**mentors[name], "state": state, "uploaded": _is_uploaded(name)}
        if state == "failed":
            entry["detail"] = _states.get(name, {}).get("detail", "Preparation did not finish.")
        result.append(entry)
    return result


IMAGE_FILE = re.compile(r"image_(\d+)(\.[a-z0-9]{1,5})?")


def voice_settings(voice_id: str | None, rate: str | None, pitch: str | None) -> dict | None:
    """Validate a standard-voice choice; None when no voice was chosen. Raises StudioError."""
    if not voice_id and not rate and not pitch:
        return None
    settings = {
        "voice_id": voice_id or voices.DEFAULT_VOICE_ID,
        "rate": rate or voices.NEUTRAL_RATE,
        "pitch": pitch or voices.NEUTRAL_PITCH,
    }
    if not mentor_pack.valid_stock_voice(settings):
        raise StudioError(422, "Choose a listed voice, a speed like +10% and a pitch like -5Hz.")
    return settings


def _check_text(persona: str | None, expressiveness: float | None) -> None:
    if expressiveness is not None and not 0 <= expressiveness <= 1:
        raise StudioError(422, "Expressiveness must be between 0 and 1.")
    if persona is not None and len(persona) > MAX_PERSONA_CHARS:
        raise StudioError(422, f"The personality text must be at most {MAX_PERSONA_CHARS} characters.")


def _check_sizes(images: list[tuple[str, bytes]], voice: tuple[str, bytes] | None) -> None:
    if any(len(data) > MAX_IMAGE_BYTES for _, data in images):
        raise StudioError(413, "An image is larger than 15 MB.")
    if voice and len(voice[1]) > MAX_VOICE_BYTES:
        raise StudioError(413, "The voice recording is larger than 30 MB.")


def _stage_files(
    work_dir: str, images: list[tuple[str, bytes]], voice: tuple[str, bytes] | None, first_number: int
) -> None:
    """Write and check uploaded files in a staging folder; raises StudioError."""
    for number, (filename, data) in enumerate(images, first_number):
        extension = os.path.splitext(filename or "")[1].lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,5}", extension):
            extension = ""
        path = os.path.join(work_dir, f"image_{number}{extension}")
        with open(path, "wb") as image_file:
            image_file.write(data)
        if not readable_image(path):
            raise StudioError(422, f"{filename or 'A file'} is not an image this app can read.")
    if voice:
        raw = os.path.join(work_dir, ".voice-upload")
        with open(raw, "wb") as voice_file:
            voice_file.write(voice[1])
        converted = convert_voice(raw, os.path.join(work_dir, "voice.wav"))
        os.remove(raw)
        if not converted:
            raise StudioError(422, "The voice recording could not be read as audio.")


def _write_profile(folder: str, profile: dict) -> None:
    with open(os.path.join(folder, mentor_pack.PROFILE_NAME), "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def _read_profile(name: str) -> dict:
    try:
        with open(os.path.join(_input_dir(), name, mentor_pack.PROFILE_NAME), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _mentor_images(name: str) -> list[str]:
    folder = os.path.join(_input_dir(), name)
    images = [f for f in os.listdir(folder) if IMAGE_FILE.fullmatch(f)]
    return sorted(images, key=lambda f: int(IMAGE_FILE.fullmatch(f).group(1)))


def _editable_mentor(name: str) -> str:
    """Return the folder of a mentor that exists; raises StudioError 404."""
    folder = os.path.join(_input_dir(), name)
    if not media_processor.MENTOR_NAME_PATTERN.fullmatch(name) or not os.path.isdir(folder):
        raise StudioError(404, "Mentor not found.")
    return folder


def create_mentor(
    name: str,
    persona: str,
    expressiveness: float,
    images: list[tuple[str, bytes]],
    voice: tuple[str, bytes] | None,
    stock_voice: dict | None = None,
) -> None:
    """Validate an upload and save it as a new mentor pack; raises StudioError."""
    if not media_processor.MENTOR_NAME_PATTERN.fullmatch(name):
        raise StudioError(422, "Use 1 to 50 lowercase letters, digits, hyphens or underscores for the name.")
    _check_text(persona, expressiveness)
    if not 1 <= len(images) <= MAX_IMAGES:
        raise StudioError(422, f"Add between 1 and {MAX_IMAGES} images.")
    _check_sizes(images, voice)

    input_dir = _input_dir()
    final_dir = os.path.join(input_dir, name)
    if name in media_processor.ALLOWED_CELEBRITIES or os.path.exists(final_dir):
        raise StudioError(409, "A mentor with this name already exists.")
    if len(list_mentors()) >= MAX_MENTORS:
        raise StudioError(409, f"The limit of {MAX_MENTORS} mentors is reached. Remove one first.")

    # Build the pack in a hidden folder, so a rejected upload leaves nothing behind.
    work_dir = os.path.join(input_dir, f".upload-{uuid.uuid4().hex}")
    os.makedirs(work_dir)
    try:
        _stage_files(work_dir, images, voice, first_number=1)
        profile = {"persona": persona.strip(), "expressiveness": expressiveness, "uploaded": True}
        if stock_voice:
            profile["voice"] = stock_voice
        _write_profile(work_dir, profile)
        os.rename(work_dir, final_dir)
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    _states[name] = {"state": "preparing", "detail": "Waiting to start"}


def mentor_details(name: str) -> dict[str, Any]:
    """Everything the edit form needs about one mentor; raises StudioError."""
    _editable_mentor(name)
    profile = mentor_pack.get_mentor_profile(name)
    return {
        "name": name,
        "persona": profile["persona"],
        "expressiveness": profile["expressiveness"],
        "voice": mentor_pack.get_mentor_stock_voice(name),
        "own_voice": bool(mentor_pack.get_mentor_voice_sample(name)),
        "uploaded": _is_uploaded(name),
        "images": _mentor_images(name),
    }


def mentor_image_path(name: str, filename: str) -> str:
    """Path of one of the mentor's own photos; raises StudioError 404 for anything else."""
    folder = _editable_mentor(name)
    if not IMAGE_FILE.fullmatch(filename) or filename not in _mentor_images(name):
        raise StudioError(404, "Image not found.")
    return os.path.join(folder, filename)


def update_mentor(
    name: str,
    *,
    persona: str | None = None,
    expressiveness: float | None = None,
    stock_voice: dict | None = None,
    clear_stock_voice: bool = False,
    remove_images: list[str] | None = None,
    new_images: list[tuple[str, bytes]] | None = None,
    new_voice: tuple[str, bytes] | None = None,
    remove_voice: bool = False,
) -> bool:
    """Change an uploaded mentor. Returns True when its photos changed. Raises StudioError.

    Everything is checked before anything is changed, so a rejected edit leaves
    the mentor exactly as it was.
    """
    folder = _editable_mentor(name)
    if not _is_uploaded(name):
        raise StudioError(403, "Only mentors created by upload can be edited here.")
    remove_images, new_images = list(remove_images or []), list(new_images or [])
    _check_text(persona, expressiveness)
    _check_sizes(new_images, new_voice)

    current = _mentor_images(name)
    if any(filename not in current for filename in remove_images):
        raise StudioError(422, "Only this mentor's own photos can be removed.")
    remaining = len(current) - len(set(remove_images)) + len(new_images)
    if remaining < 1:
        raise StudioError(422, "A mentor needs at least one photo.")
    if remaining > MAX_IMAGES:
        raise StudioError(422, f"A mentor can have at most {MAX_IMAGES} photos.")

    work_dir = os.path.join(_input_dir(), f".upload-{uuid.uuid4().hex}")
    os.makedirs(work_dir)
    try:
        next_number = 1 + max(
            [int(IMAGE_FILE.fullmatch(f).group(1)) for f in current], default=0
        )
        _stage_files(work_dir, new_images, new_voice, first_number=next_number)

        # Nothing can be rejected from here on: apply the changes.
        for filename in remove_images:
            os.remove(os.path.join(folder, filename))
            stem = os.path.splitext(filename)[0]
            for clip in os.listdir(folder):
                if clip.startswith(f"auto_{stem}_") and clip.endswith(".mp4"):
                    os.remove(os.path.join(folder, clip))
        if remove_voice and not new_voice:
            sample = mentor_pack.get_mentor_voice_sample(name)
            if sample:
                os.remove(sample)
        for filename in os.listdir(work_dir):
            os.replace(os.path.join(work_dir, filename), os.path.join(folder, filename))

        profile = _read_profile(name)
        if persona is not None:
            profile["persona"] = persona.strip()
        if expressiveness is not None:
            profile["expressiveness"] = expressiveness
        if stock_voice:
            profile["voice"] = stock_voice
        elif clear_stock_voice:
            profile.pop("voice", None)
        profile["uploaded"] = True
        _write_profile(folder, profile)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    photos_changed = bool(remove_images or new_images)
    if photos_changed:
        _states[name] = {"state": "preparing", "detail": "Waiting to start"}
    return photos_changed


def delete_mentor(name: str) -> None:
    """Remove a mentor that was created by upload; raises StudioError."""
    if not media_processor.MENTOR_NAME_PATTERN.fullmatch(name) or not os.path.isdir(
        os.path.join(_input_dir(), name)
    ):
        raise StudioError(404, "Mentor not found.")
    if not _is_uploaded(name):
        raise StudioError(403, "Only mentors created by upload can be removed here.")
    shutil.rmtree(os.path.join(_input_dir(), name))
    _states.pop(name, None)
    from backend.services import lesson_packs  # imported here: it imports the talk service

    lesson_packs.delete_mentor_packs(name)


def portrait_command() -> list[str] | None:
    """Command that turns mentor images into moving clips, or None if it is not installed."""
    python, liveportrait = config.PORTRAIT_PYTHON, config.LIVEPORTRAIT_DIR
    if not os.path.isfile(python) or not os.path.isfile(os.path.join(liveportrait, "inference.py")):
        return None
    return [python, os.path.join(media_processor.BASE_DIR, "workers", "portrait_maker.py"),
            "--input-dir", _input_dir(), "--liveportrait-dir", liveportrait]


BASIC_CLIP_SECONDS = 6
NOT_IMAGES = {".mp4", ".json", ".wav", ".mp3", ".m4a", ".flac", ".txt"}


def make_basic_clips(name: str) -> None:
    """Make a slow zoom clip from each image with FFmpeg alone.

    Used when LivePortrait is not installed, so a mentor can still be created.
    The clips are named auto_*, so the full image step replaces them later.
    """
    mentor_dir = os.path.join(_input_dir(), name)
    frames = BASIC_CLIP_SECONDS * 25
    motion = (
        "scale=1024:1024:force_original_aspect_ratio=increase,crop=1024:1024,"
        f"zoompan=z='1+0.02*(1-cos(2*PI*on/{frames}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s=512x512:fps=25,format=yuv420p"
    )
    for filename in sorted(os.listdir(mentor_dir)):
        source = os.path.join(mentor_dir, filename)
        stem, extension = os.path.splitext(filename)
        if filename.startswith((".", "auto_")) or extension.lower() in NOT_IMAGES:
            continue
        if not os.path.isfile(source) or not is_image(source):
            continue
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-i", source,
             "-frames:v", str(frames), "-vf", motion,
             "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart",
             os.path.join(mentor_dir, f"auto_{stem}_basic.mp4")],
            capture_output=True,
        )


async def run_tool(command: list[str]) -> int:
    process = await asyncio.create_subprocess_exec(*command)
    return await process.wait()


_prepare_lock = asyncio.Lock()


async def prepare_mentor(name: str) -> None:
    """Animate a new mentor's images, then get the face worker ready for it."""
    try:
        command = portrait_command()
        async with _prepare_lock:  # one mentor at a time: the step is heavy on the GPU
            _states[name] = {"state": "preparing", "detail": "Adding motion to the images"}
            if command:
                await run_tool(command)
            else:  # LivePortrait is not installed: a slow zoom still gives the picture life
                await asyncio.to_thread(make_basic_clips, name)
            clips = media_processor.get_mentor_clips(name)
            if not clips:
                _states[name] = {
                    "state": "failed",
                    "detail": "No face could be animated. Use clear, front-facing photos of one person.",
                }
                return
            _states[name] = {"state": "preparing", "detail": "Preparing the face for lip-sync"}
            if not await media_workers.prepare(clips):
                log_event("WARN", "Face worker could not prepare the mentor; it will talk over its clip")
        _states[name] = {"state": "ready", "detail": ""}
    except Exception as error:
        log_event("ERROR", "Mentor preparation failed", error=short_error(error))
        _states[name] = {"state": "failed", "detail": "Preparation stopped unexpectedly."}
