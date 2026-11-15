from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import subprocess
import edge_tts

logger = logging.getLogger(__name__)

# Base directory for resolving assets (ai_service root)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ALLOWED_CELEBRITIES = {"modi", "owl", "salman", "srk"}
DEFAULT_MENTOR = "modi"
MENTOR_NAME_PATTERN = re.compile(r"[a-z0-9_-]{1,50}")

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

# Upper bound on clips joined for one lesson, so tiny clips cannot build a huge list.
MAX_SEQUENCE_CLIPS = 2000


def check_ffmpeg() -> None:
    """Verify that FFmpeg is available before starting the service."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger.info("FFmpeg is available.")
    except FileNotFoundError:
        logger.error(
            "FFmpeg is not installed or not available in PATH. "
            "Install FFmpeg before starting the AI service."
        )
        raise RuntimeError(
            "FFmpeg is required to start the AI service but was not found in PATH."
        )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "FFmpeg availability check failed with exit code %s.",
            exc.returncode,
        )
        raise RuntimeError(
            "FFmpeg is required to start the AI service but the availability check failed."
        ) from exc


async def generate_tts(
    text: str,
    output_file: str,
    voice_id: str = "en-US-GuyNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> None:
    """Generate audio narration using edge_tts."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice_id,
        rate=rate,
        pitch=pitch,
    )
    await communicate.save(output_file)


def get_celebrity_video(celebrity_name: str, base_dir: str | None = None) -> str:
    """Select the valid video path for the requested celebrity, or fall back to modi."""
    effective_base = base_dir or BASE_DIR
    input_video_dir = os.path.realpath(
        os.path.join(effective_base, "backend", "input")
    )

    name = celebrity_name.strip().lower() if isinstance(celebrity_name, str) else ""
    if name not in ALLOWED_CELEBRITIES:
        name = "modi"

    candidate = os.path.realpath(
        os.path.join(input_video_dir, f"{name}.mp4")
    )

    if (
        os.path.commonpath([input_video_dir, candidate]) == input_video_dir
        and os.path.isfile(candidate)
    ):
        print(f"[INFO] Renderer source selected | source={name}", flush=True)
        return candidate

    fallback = os.path.realpath(os.path.join(input_video_dir, "modi.mp4"))
    print("[INFO] Default renderer source selected | source=modi", flush=True)
    return fallback


def resolve_mentor_dir(mentor_name: str | None, base_dir: str | None = None) -> str | None:
    """Return the mentor's pack folder (input/<mentor>/), or None if there is none.

    A mentor is known when it is a built-in name or has its own folder. Any
    other name, including an unsafe one, resolves to the default mentor.
    """
    effective_base = base_dir or BASE_DIR
    input_video_dir = os.path.realpath(os.path.join(effective_base, "backend", "input"))

    name = mentor_name.strip().lower() if isinstance(mentor_name, str) else ""
    is_folder_mentor = bool(MENTOR_NAME_PATTERN.fullmatch(name)) and os.path.isdir(
        os.path.join(input_video_dir, name)
    )
    if name not in ALLOWED_CELEBRITIES and not is_folder_mentor:
        name = DEFAULT_MENTOR

    mentor_dir = os.path.realpath(os.path.join(input_video_dir, name))
    if (
        os.path.commonpath([input_video_dir, mentor_dir]) != input_video_dir
        or not os.path.isdir(mentor_dir)
    ):
        return None
    return mentor_dir


def get_mentor_clips(mentor_name: str, base_dir: str | None = None) -> list[str]:
    """Return the mentor's clip library (input/<mentor>/*.mp4), or [] if there is none."""
    clip_dir = resolve_mentor_dir(mentor_name, base_dir)
    if not clip_dir:
        return []

    clips = []
    for filename in sorted(os.listdir(clip_dir)):
        candidate = os.path.realpath(os.path.join(clip_dir, filename))
        if (
            filename.lower().endswith(".mp4")
            and os.path.commonpath([clip_dir, candidate]) == clip_dir
            and os.path.isfile(candidate)
        ):
            clips.append(candidate)
    return clips


def build_clip_sequence(
    clips: list[tuple[str, float]], target_seconds: float, seed: str
) -> list[str]:
    """Order (path, seconds) clips, repeating them until they cover the narration.

    The order is shuffled per pass but repeatable for one seed, and the same
    clip is never played twice in a row when there is more than one.
    """
    usable = [(path, seconds) for path, seconds in clips if seconds > 0]
    if not usable:
        raise ValueError("Clip library has no clips with a usable length")

    rng = random.Random(seed)
    sequence: list[str] = []
    total = 0.0
    while True:
        order = usable[:]
        rng.shuffle(order)
        if sequence and len(order) > 1 and order[0][0] == sequence[-1]:
            order.append(order.pop(0))
        for path, seconds in order:
            sequence.append(path)
            total += seconds
            if total >= target_seconds:
                return sequence
            if len(sequence) >= MAX_SEQUENCE_CLIPS:
                raise ValueError("Clip library is too short for this narration")


def write_concat_list(sequence: list[str], list_path: str) -> None:
    """Write an FFmpeg concat list for the ordered clips."""
    with open(list_path, "w", encoding="utf-8") as list_file:
        list_file.write("ffconcat version 1.0\n")
        for clip in sequence:
            escaped = clip.replace("'", "'\\''")
            list_file.write(f"file '{escaped}'\n")


def build_landscape_video_filter() -> str:
    """Return the shared uncropped 16:9 blurred-background composition."""
    return (
        f"[0:v]split=2[background][foreground];"
        f"[background]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},gblur=sigma=28[blurred];"
        f"[foreground]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease[main];"
        f"[blurred][main]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[video]"
    )


def build_avatar_normalization_args(input_video: str, output_path: str) -> list[str]:
    """Normalize a downloaded avatar video while preserving its own audio."""
    return [
        "ffmpeg", "-y", "-i", input_video, "-filter_complex", build_landscape_video_filter(),
        "-map", "[video]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium",
        "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", output_path,
    ]


def build_fallback_ffmpeg_args(input_video: str, audio_path: str, output_path: str) -> list[str]:
    """Create an uncropped 16:9 lesson video with a blurred background."""
    return [
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", input_video, "-i", audio_path,
        "-filter_complex", build_landscape_video_filter(), "-map", "[video]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac",
        "-movflags", "+faststart", "-shortest", output_path,
    ]


def build_clip_library_ffmpeg_args(
    concat_list: str,
    audio_path: str,
    output_path: str,
    duration_seconds: float | None = None,
) -> list[str]:
    """Join the mentor's clips under the narration on the shared 16:9 canvas.

    The clip list loops as a safety net; a known narration length ends the
    video exactly there, because -shortest alone overruns by a few seconds.
    """
    stop_at = ["-t", f"{duration_seconds:.3f}"] if duration_seconds and duration_seconds > 0 else []
    return [
        "ffmpeg", "-y", "-stream_loop", "-1", "-f", "concat", "-safe", "0", "-i", concat_list,
        "-i", audio_path, "-filter_complex", build_landscape_video_filter(),
        "-map", "[video]", "-map", "1:a:0", "-c:v", "libx264", "-preset", "medium",
        "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", "-shortest", *stop_at, output_path,
    ]


async def run_ffmpeg_command(args: list[str]) -> tuple[int, str]:
    """Run FFmpeg and always return its exit code and safely decoded diagnostics."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    error_message = stderr.decode(errors="replace") if stderr else ""
    return process.returncode, error_message


def probe_video_metadata(video_path: str) -> dict:
    """Return safe delivery metadata without exposing local paths."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,codec_name,avg_frame_rate,bit_rate", "-of", "json", video_path],
        check=True, capture_output=True, text=True,
    )
    stream = (json.loads(result.stdout).get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec": stream.get("codec_name") or "unknown",
        "frame_rate": stream.get("avg_frame_rate") or "unknown",
        "bit_rate": int(stream.get("bit_rate") or 0),
    }
