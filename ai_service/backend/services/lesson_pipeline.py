from __future__ import annotations

import asyncio
import datetime
import os
import sys
import time
from typing import Any
from mutagen.mp3 import MP3
import requests
import cloudinary
import cloudinary.uploader

from backend.schemas.lessons import LessonRequest
from backend.services.job_tracker import (
    active_jobs,
    job_status,
    mark_job_failed,
    shutdown_failed_jobs,
    remove_failed_generation_cache,
)
from backend.services import media_processor, media_workers, mentor_pack
from backend.services.providers import manager as provider_manager
from backend.services.providers.validation import (
    ProviderGenerationError,
    validate_lesson_script,
)
import backend.avatar_service as avatar_module
import backend.voices as voices
from backend.utils.logging_utils import log_event, short_error
from backend.utils.subtitles import get_audio_duration, write_subtitles

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_callable(name: str, fallback: Any) -> Any:
    """Resolve callable from api module if patched during tests, else fallback."""
    api_module = sys.modules.get("api") or sys.modules.get("backend.api")
    if api_module is not None and hasattr(api_module, name):
        return getattr(api_module, name)
    return fallback


def _resolve_os() -> Any:
    """Resolve os module, respecting patches on api.os during tests."""
    api_module = sys.modules.get("api") or sys.modules.get("backend.api")
    if api_module is not None and hasattr(api_module, "os"):
        return getattr(api_module, "os")
    return os


def _resolve_asyncio() -> Any:
    """Resolve asyncio module, respecting patches on api.asyncio during tests."""
    api_module = sys.modules.get("api") or sys.modules.get("backend.api")
    if api_module is not None and hasattr(api_module, "asyncio"):
        return getattr(api_module, "asyncio")
    return asyncio


async def run_tracked_lesson(data: LessonRequest, base_filename: str) -> None:
    """Execute lesson generation workflow and ensure active tracking release."""
    try:
        await process_lesson(data, base_filename)
    finally:
        active_jobs.discard(base_filename)


async def process_lesson(data: LessonRequest, base_filename: str) -> None:
    """Orchestrate the end-to-end generation of a lesson."""
    eff_os = _resolve_os()
    eff_asyncio = _resolve_asyncio()
    mp3_class = _resolve_callable("MP3", MP3)
    requests_mod = _resolve_callable("requests", requests)
    subtitles_writer = _resolve_callable("write_subtitles", write_subtitles)
    cloudinary_mod = _resolve_callable("cloudinary", cloudinary)

    log_event(
        "QUEUE",
        "Lesson request accepted",
        job=base_filename,
        topic=data.topic,
        source=data.celebrity,
    )

    start_time = time.time()
    provider_used = "unknown"
    model_used = "unknown"
    audio_seconds = 0.0
    voice_used = "unknown"

    # 1️⃣ Initialize job_status entry if not present
    if base_filename not in job_status:
        job_status[base_filename] = {
            "status": "processing",
            "meta": {
                "timestamps": {
                    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            },
        }
    else:
        job_status[base_filename]["status"] = "processing"
        if "meta" not in job_status[base_filename]:
            job_status[base_filename]["meta"] = {"timestamps": {}}
        if "timestamps" not in job_status[base_filename]["meta"]:
            job_status[base_filename]["meta"]["timestamps"] = {}
        job_status[base_filename]["meta"]["timestamps"]["started_at"] = (
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        )

    try:
        log_event(
            "START",
            "Lesson generation started",
            job=base_filename,
            topic=data.topic,
            source=data.celebrity,
        )

        log_event("INFO", "User preferences", configured=bool(data.preferences))

        script_generator = _resolve_callable(
            "generate_lesson_script", provider_manager.generate_lesson_script
        )
        script, provider_used, model_used = script_generator(data)
        script = validate_lesson_script(script)
        log_event("INFO", "Script prepared", words=len(script.split()))

        # 2️⃣ Create Output Folders
        base_output_dir = eff_os.path.join(BASE_DIR, "outputs")
        text_dir = eff_os.path.join(base_output_dir, "text")
        audio_dir = eff_os.path.join(base_output_dir, "audio")
        video_dir = eff_os.path.join(base_output_dir, "video")

        eff_os.makedirs(text_dir, exist_ok=True)
        eff_os.makedirs(audio_dir, exist_ok=True)
        eff_os.makedirs(video_dir, exist_ok=True)

        text_path = eff_os.path.join(text_dir, f"{base_filename}.txt")
        audio_path = eff_os.path.join(audio_dir, f"{base_filename}.mp3")
        final_video = eff_os.path.join(video_dir, f"{base_filename}.mp4")

        # 3️⃣ Save Text to File
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(script)

        log_event("OK", "Transcript saved", file=os.path.basename(text_path))

        # 4️⃣ Convert Text to Speech (edge-tts)
        log_event("RUN", "Generating narration audio")
        try:
            if eff_os.path.exists(audio_path):
                eff_os.remove(audio_path)

            selected_voice, rate, pitch = voices.resolve_mentor_voice(
                data.celebrity,
                data.voice_id,
                data.speech_rate,
                data.speech_pitch,
                pack_voice=mentor_pack.get_mentor_stock_voice(data.celebrity),
            )

            # The mentor's own cloned voice comes first, unless the learner chose a voice.
            sample_fn = _resolve_callable(
                "get_mentor_voice_sample", mentor_pack.get_mentor_voice_sample
            )
            profile_fn = _resolve_callable("get_mentor_profile", mentor_pack.get_mentor_profile)
            speak_fn = _resolve_callable("speak_in_mentor_voice", media_workers.speak)
            voice_sample = None if data.voice_id else sample_fn(data.celebrity)
            if voice_sample and await speak_fn(
                script,
                voice_sample,
                audio_path,
                expressiveness=profile_fn(data.celebrity)["expressiveness"],
            ):
                voice_used = "cloned"
            else:
                voice_used = selected_voice
                tts_fn = _resolve_callable("generate_tts", media_processor.generate_tts)
                await tts_fn(
                    script, audio_path, voice_id=selected_voice, rate=rate, pitch=pitch
                )

            try:
                audio = mp3_class(audio_path)
                audio_seconds = audio.info.length
            except Exception as e:
                log_event("WARN", "Could not read audio duration", error=short_error(e))

            log_event("OK", "Narration audio ready", duration=f"{audio_seconds:.2f}s")

        except Exception as e:
            log_event("ERROR", "Narration generation failed", error=short_error(e))
            mark_job_failed(
                base_filename,
                e,
                failure_code="narration_failed",
            )
            return

        # 5️⃣ Try AI Avatar Video
        avatar_video_url = None
        renderer_used = "did"

        try:
            log_event("RUN", "Trying D-ID avatar renderer")

            create_avatar_fn = _resolve_callable(
                "create_avatar_video", avatar_module.create_avatar_video
            )
            avatar_video_url = await create_avatar_fn(audio_path)

            log_event("OK", "D-ID avatar render completed")

            video_response = await asyncio.to_thread(
                requests_mod.get,
                avatar_video_url,
                timeout=120,
            )
            video_response.raise_for_status()

            avatar_download = eff_os.path.join(
                video_dir, f"{base_filename}.avatar-source.mp4"
            )
            with open(avatar_download, "wb") as video_file:
                video_file.write(video_response.content)
            log_event(
                "OK",
                "Avatar video downloaded",
                file=os.path.basename(avatar_download),
            )

            norm_args_fn = _resolve_callable(
                "build_avatar_normalization_args",
                media_processor.build_avatar_normalization_args,
            )
            normalize_process = await eff_asyncio.create_subprocess_exec(
                *norm_args_fn(avatar_download, final_video),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _normalize_stdout, normalize_stderr = (
                await normalize_process.communicate()
            )
            try:
                if eff_os.path.exists(avatar_download):
                    eff_os.remove(avatar_download)
            except OSError as cleanup_error:
                log_event(
                    "WARN",
                    "Avatar source cleanup failed",
                    error=short_error(cleanup_error),
                )
            if normalize_process.returncode != 0:
                raise RuntimeError(normalize_stderr.decode(errors="replace"))
            if not eff_os.path.exists(final_video):
                raise RuntimeError(
                    "Avatar normalization completed without an output video"
                )
            log_event(
                "OK",
                "Avatar video normalized to landscape",
                file=os.path.basename(final_video),
            )

        except Exception as avatar_error:
            renderer_used = "ffmpeg_fallback"
            log_event(
                "WARN",
                "D-ID unavailable; using FFmpeg fallback",
                error=short_error(avatar_error),
            )
            log_event("RUN", "Starting FFmpeg fallback renderer")

            clips_fn = _resolve_callable("get_mentor_clips", media_processor.get_mentor_clips)
            try:
                mentor_clips = clips_fn(data.celebrity)
            except Exception as clip_error:
                mentor_clips = []
                log_event("WARN", "Clip library unavailable", error=short_error(clip_error))

            if await _render_lipsync(mentor_clips, audio_path, final_video, video_dir, base_filename):
                renderer_used = "lipsync"
            else:
                renderer_used, failure_code = await _render_from_clips(
                    data, mentor_clips, audio_path, audio_seconds, final_video, video_dir, base_filename
                )
                if failure_code:
                    mark_job_failed(base_filename, failure_code=failure_code)
                    return

        # 6.5 Generate subtitles (.vtt + .srt)
        vtt_path, srt_path = None, None
        try:
            vtt_path, srt_path = subtitles_writer(
                script, audio_path, eff_os.path.join(video_dir, base_filename)
            )
            log_event("OK", "Subtitle files created")
        except Exception as sub_err:
            log_event(
                "WARN",
                "Subtitle generation failed; video remains available",
                error=short_error(sub_err),
            )

        # 7️⃣ Upload to Cloudinary
        cloudinary_url = None
        try:
            log_event("RUN", "Uploading lesson video")
            upload_result = cloudinary_mod.uploader.upload(
                final_video,
                resource_type="video",
                folder="ai_mentor/videos",
                public_id=base_filename,
                overwrite=True,
                chunk_size=6000000,
            )
            cloudinary_url = upload_result.get("secure_url")
            log_event("OK", "Lesson video uploaded")
        except Exception as cloud_err:
            log_event(
                "WARN",
                "Video upload failed; using local proxy",
                error=short_error(cloud_err),
            )

        job_status[base_filename].update(
            {
                "status": "ready",
                "cloudinary_url": cloudinary_url,
            }
        )

        local_video_url = f"/video-stream/{base_filename}.mp4"
        local_vtt_url = f"/video-stream/{base_filename}.vtt" if vtt_path else None
        local_srt_url = f"/video-stream/{base_filename}.srt" if srt_path else None
        vtt_url, srt_url = None, None

        if vtt_path and srt_path:
            try:
                vtt_url = cloudinary_mod.uploader.upload(
                    vtt_path,
                    resource_type="raw",
                    folder="ai_mentor/subtitles",
                    public_id=f"{base_filename}.vtt",
                    overwrite=True,
                ).get("secure_url")
                srt_url = cloudinary_mod.uploader.upload(
                    srt_path,
                    resource_type="raw",
                    folder="ai_mentor/subtitles",
                    public_id=f"{base_filename}.srt",
                    overwrite=True,
                ).get("secure_url")
                log_event("OK", "Subtitle files uploaded")
            except Exception as sub_up_err:
                log_event(
                    "WARN",
                    "Subtitle upload failed; using local files",
                    error=short_error(sub_up_err),
                )

        subtitle_vtt_url = vtt_url or local_vtt_url
        subtitle_srt_url = srt_url or local_srt_url
        duration_ms = int((time.time() - start_time) * 1000)
        media_metadata = {}
        try:
            probe_fn = _resolve_callable(
                "probe_video_metadata", media_processor.probe_video_metadata
            )
            media_metadata = probe_fn(final_video)
        except Exception as probe_error:
            log_event(
                "WARN",
                "Video metadata probe failed",
                error=short_error(probe_error),
            )

        if base_filename not in shutdown_failed_jobs:
            if base_filename in job_status:
                job_status[base_filename]["status"] = "ready"
                job_status[base_filename]["cloudinary_url"] = cloudinary_url
                job_status[base_filename]["local_video_url"] = local_video_url
                job_status[base_filename]["subtitle_url"] = subtitle_vtt_url
                job_status[base_filename]["subtitle_vtt_url"] = subtitle_vtt_url
                job_status[base_filename]["subtitle_srt_url"] = subtitle_srt_url

                meta = job_status[base_filename].setdefault("meta", {})
                timestamps = meta.setdefault("timestamps", {})
                meta["provider"] = provider_used
                meta["model"] = model_used
                meta["duration_ms"] = duration_ms
                meta["audio_seconds"] = audio_seconds
                meta["word_count"] = len(script.split())
                meta["renderer"] = renderer_used
                meta["voice"] = voice_used
                meta["media"] = media_metadata
                timestamps["completed_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()

        log_event(
            "READY",
            "Lesson generation completed",
            job=base_filename,
            provider=provider_used,
            duration=f"{duration_ms / 1000:.1f}s",
            audio=f"{audio_seconds:.1f}s",
            words=len(script.split()),
            delivery="cloud" if cloudinary_url else "local",
        )

        # 8️⃣ Storage Cleanup
        if cloudinary_url:
            log_event("CLEAN", "Removing temporary media files")
            cleanup_files = [audio_path, final_video]
            if vtt_url and vtt_path:
                cleanup_files.append(vtt_path)
            if srt_url and srt_path:
                cleanup_files.append(srt_path)
            for local_file in cleanup_files:
                try:
                    if local_file and eff_os.path.exists(local_file):
                        eff_os.remove(local_file)
                        log_event(
                            "CLEAN",
                            "Temporary file removed",
                            file=os.path.basename(local_file),
                        )
                except Exception as cleanup_err:
                    log_event(
                        "WARN",
                        "Temporary file cleanup failed",
                        file=os.path.basename(local_file),
                        error=short_error(cleanup_err),
                    )
        else:
            log_event("WARN", "Local media retained because upload failed")
            log_event("INFO", "Local fallback files require later cleanup")

    except Exception as e:
        mark_job_failed(
            base_filename,
            e,
            failure_code=(
                "provider_failed"
                if isinstance(e, ProviderGenerationError)
                else "generation_failed"
            ),
        )
        log_event("ERROR", "Lesson generation failed", job=base_filename, error=short_error(e))


async def _render_lipsync(
    mentor_clips: list[str], audio_path: str, final_video: str, video_dir: str, base_filename: str
) -> bool:
    """Render the mentor's clips with a moving mouth via the face worker."""
    if not mentor_clips:
        return False
    animate_fn = _resolve_callable("animate_mentor", media_workers.animate)
    talking_video = os.path.join(video_dir, f"{base_filename}.talking.mp4")
    if not await animate_fn(mentor_clips, audio_path, talking_video):
        return False

    run_ffmpeg_fn = _resolve_callable("run_ffmpeg_command", media_processor.run_ffmpeg_command)
    returncode, error_msg = await run_ffmpeg_fn(
        media_processor.build_avatar_normalization_args(talking_video, final_video)
    )
    if os.path.exists(talking_video):
        os.remove(talking_video)
    if returncode != 0 or not os.path.exists(final_video):
        log_event("WARN", "Lip-synced video normalization failed", error=(error_msg or "")[-200:])
        return False
    log_event("OK", "Lip-synced mentor video ready")
    return True


async def _render_from_clips(
    data: LessonRequest,
    mentor_clips: list[str],
    audio_path: str,
    audio_seconds: float,
    final_video: str,
    video_dir: str,
    base_filename: str,
) -> tuple[str, str | None]:
    """Join the mentor's clips (or loop one clip) under the narration.

    Returns (renderer, failure_code); failure_code is None on success.
    """
    eff_os = _resolve_os()
    renderer_used = "ffmpeg_fallback"
    input_video = _resolve_callable(
        "get_celebrity_video", media_processor.get_celebrity_video
    )(data.celebrity)

    concat_list = None
    if mentor_clips:
        try:
            duration_fn = _resolve_callable("get_media_duration", get_audio_duration)
            sequence = media_processor.build_clip_sequence(
                [(clip, duration_fn(clip)) for clip in mentor_clips],
                audio_seconds,
                seed=base_filename,
            )
            concat_list = os.path.join(video_dir, f"{base_filename}.clips.txt")
            media_processor.write_concat_list(sequence, concat_list)
            renderer_used = "clips"
            log_event("INFO", "Clip library selected", clips=len(mentor_clips))
        except Exception as clip_error:
            concat_list = None
            log_event(
                "WARN", "Clip library unavailable; using single clip", error=short_error(clip_error)
            )

    if not concat_list and not eff_os.path.exists(input_video):
        log_event("ERROR", "Fallback source video not found", file=os.path.basename(input_video))
        return renderer_used, "source_video_missing"

    log_event("RUN", "Rendering lesson video with FFmpeg")
    if concat_list:
        ffmpeg_args = media_processor.build_clip_library_ffmpeg_args(
            concat_list, audio_path, final_video, duration_seconds=audio_seconds
        )
    else:
        ffmpeg_args = _resolve_callable(
            "build_fallback_ffmpeg_args", media_processor.build_fallback_ffmpeg_args
        )(input_video, audio_path, final_video)

    run_ffmpeg_fn = _resolve_callable("run_ffmpeg_command", media_processor.run_ffmpeg_command)
    ffmpeg_returncode, error_msg = await run_ffmpeg_fn(ffmpeg_args)
    if concat_list and os.path.exists(concat_list):
        os.remove(concat_list)
    if ffmpeg_returncode != 0:
        error_msg = error_msg or "FFmpeg exited without diagnostic output"
        log_event(
            "ERROR",
            "FFmpeg render failed",
            code=ffmpeg_returncode,
            error=short_error(RuntimeError(error_msg)),
        )
        return renderer_used, "ffmpeg_failed"
    if not eff_os.path.exists(final_video):
        log_event("ERROR", "FFmpeg completed without an output video")
        return renderer_used, "output_missing"

    log_event("OK", "FFmpeg video render completed")
    return renderer_used, None
