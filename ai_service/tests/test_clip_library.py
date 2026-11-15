import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import api
from backend.services import lesson_pipeline, media_processor


def make_input_dir(tmp_path):
    input_dir = tmp_path / "backend" / "input"
    input_dir.mkdir(parents=True)
    return input_dir


def test_mentor_folder_returns_its_mp4_clips_sorted(tmp_path):
    input_dir = make_input_dir(tmp_path)
    clips = input_dir / "owl"
    clips.mkdir()
    (clips / "talk_b.mp4").write_bytes(b"b")
    (clips / "talk_a.MP4").write_bytes(b"a")
    (clips / "notes.txt").write_text("not a clip")

    result = media_processor.get_mentor_clips("Owl ", base_dir=str(tmp_path))

    assert [Path(p).name for p in result] == ["talk_a.MP4", "talk_b.mp4"]
    assert all(Path(p).is_absolute() for p in result)


def test_mentor_without_folder_has_no_clip_library(tmp_path):
    input_dir = make_input_dir(tmp_path)
    (input_dir / "owl.mp4").write_bytes(b"single")

    assert media_processor.get_mentor_clips("owl", base_dir=str(tmp_path)) == []


@pytest.mark.parametrize("name", ["../secrets", "unknown", "", None])
def test_unknown_or_unsafe_mentor_uses_the_default_mentor_folder(tmp_path, name):
    input_dir = make_input_dir(tmp_path)
    (tmp_path / "backend" / "secrets").mkdir()
    (tmp_path / "backend" / "secrets" / "leak.mp4").write_bytes(b"x")
    default_dir = input_dir / "modi"
    default_dir.mkdir()
    (default_dir / "idle.mp4").write_bytes(b"x")

    result = media_processor.get_mentor_clips(name, base_dir=str(tmp_path))

    assert [Path(p).name for p in result] == ["idle.mp4"]


def test_clip_sequence_covers_the_narration_and_is_repeatable():
    clips = [("a.mp4", 2.0), ("b.mp4", 3.0), ("c.mp4", 4.0)]
    durations = dict(clips)

    first = media_processor.build_clip_sequence(clips, 25.0, seed="job-1")
    second = media_processor.build_clip_sequence(clips, 25.0, seed="job-1")

    assert first == second
    assert sum(durations[c] for c in first) >= 25.0
    assert set(first) == {"a.mp4", "b.mp4", "c.mp4"}
    assert all(a != b for a, b in zip(first, first[1:]))


def test_clip_sequence_repeats_a_single_clip_and_handles_unknown_length():
    assert media_processor.build_clip_sequence([("a.mp4", 2.0)], 5.0, seed="s") == [
        "a.mp4",
        "a.mp4",
        "a.mp4",
    ]
    assert media_processor.build_clip_sequence([("a.mp4", 2.0)], 0.0, seed="s") == ["a.mp4"]


def test_clip_sequence_rejects_clips_without_a_usable_length():
    with pytest.raises(ValueError):
        media_processor.build_clip_sequence([], 5.0, seed="s")
    with pytest.raises(ValueError):
        media_processor.build_clip_sequence([("a.mp4", 0.0)], 5.0, seed="s")


def test_concat_list_escapes_quotes(tmp_path):
    list_file = tmp_path / "clips.txt"

    media_processor.write_concat_list(["/clips/a.mp4", "/clips/it's.mp4"], str(list_file))

    assert list_file.read_text(encoding="utf-8").splitlines() == [
        "ffconcat version 1.0",
        "file '/clips/a.mp4'",
        "file '/clips/it'\\''s.mp4'",
    ]


def test_clip_library_command_joins_clips_under_the_narration():
    args = media_processor.build_clip_library_ffmpeg_args("clips.txt", "lesson.mp3", "lesson.mp4")

    concat_at = args.index("concat")
    assert args[concat_at - 1] == "-f"
    assert args[args.index("-safe") + 1] == "0"
    assert args.index("clips.txt") < args.index("lesson.mp3")
    assert args[args.index("-filter_complex") + 1] == media_processor.build_landscape_video_filter()
    assert "1:a:0" in args
    assert "-shortest" in args
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-movflags") + 1] == "+faststart"
    assert args[-1] == "lesson.mp4"
    assert "-t" not in args


def test_clip_library_command_stops_at_the_narration_length_when_known():
    args = media_processor.build_clip_library_ffmpeg_args(
        "clips.txt", "lesson.mp3", "lesson.mp4", duration_seconds=11.04
    )

    assert args[args.index("-t") + 1] == "11.040"
    assert args[-1] == "lesson.mp4"


def run_fallback_pipeline(
    tmp_path, monkeypatch, job_id, *, animate=None, speak=None, voice_sample=None, **request_fields
):
    """Run the real pipeline with only TTS, D-ID, workers, FFmpeg and upload stubbed."""
    monkeypatch.setattr(lesson_pipeline, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        api, "generate_lesson_script", lambda _data: ("A short lesson.", "gemini", "test-model")
    )

    async def fake_tts(_text, output_file, **_kwargs):
        Path(output_file).write_bytes(b"audio")

    monkeypatch.setattr(api, "generate_tts", fake_tts)
    monkeypatch.setattr(
        api, "create_avatar_video", AsyncMock(side_effect=RuntimeError("avatar unavailable"))
    )
    monkeypatch.setattr(api, "animate_mentor", animate or AsyncMock(return_value=False))
    monkeypatch.setattr(api, "speak_in_mentor_voice", speak or AsyncMock(return_value=False))
    monkeypatch.setattr(api, "get_mentor_voice_sample", lambda _mentor: voice_sample)
    monkeypatch.setattr(
        api, "get_mentor_profile", lambda _mentor: {"persona": "", "expressiveness": 0.7}
    )

    clip_dir = tmp_path / "library"
    clip_dir.mkdir()
    clips = [str(clip_dir / "talk_1.mp4"), str(clip_dir / "talk_2.mp4")]
    for clip in clips:
        Path(clip).write_bytes(b"clip")
    monkeypatch.setattr(api, "get_mentor_clips", lambda _mentor: clips)
    monkeypatch.setattr(api, "get_media_duration", lambda _path: 2.0)

    commands = []

    async def fake_ffmpeg(command):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return 0, ""

    monkeypatch.setattr(api, "run_ffmpeg_command", fake_ffmpeg)
    monkeypatch.setattr(api, "write_subtitles", lambda *_args: (None, None))
    monkeypatch.setattr(api, "probe_video_metadata", lambda *_args: {})

    def fail_upload(*_args, **_kwargs):
        raise RuntimeError("upload unavailable")

    monkeypatch.setattr(api.cloudinary.uploader, "upload", fail_upload)

    request = api.LessonRequest(course="Python", topic="Loops", celebrity="owl", **request_fields)
    asyncio.run(api.process_lesson(request, job_id))
    return commands, clips


def video_bytes(tmp_path, job_id):
    return (tmp_path / "outputs" / "video" / f"{job_id}.mp4").read_bytes()


def test_pipeline_renders_from_the_clip_library_when_the_face_worker_is_down(
    tmp_path, monkeypatch
):
    job_id = "clip-library-job"

    commands, _clips = run_fallback_pipeline(tmp_path, monkeypatch, job_id)

    status = api.job_status[job_id]
    assert status["status"] == "ready"
    assert status["meta"]["renderer"] == "clips"
    assert len(commands) == 1
    assert "concat" in commands[0]
    assert video_bytes(tmp_path, job_id) == b"video"
    assert not list((tmp_path / "outputs" / "video").glob("*.clips.txt"))


def test_pipeline_uses_the_lip_synced_mentor_from_the_face_worker(tmp_path, monkeypatch):
    job_id = "lipsync-job"
    calls = []

    async def animate(clips, audio, output, **_kwargs):
        calls.append((clips, Path(audio).name))
        Path(output).write_bytes(b"talking mentor")
        return True

    commands, clips = run_fallback_pipeline(tmp_path, monkeypatch, job_id, animate=animate)

    status = api.job_status[job_id]
    assert status["status"] == "ready"
    assert status["meta"]["renderer"] == "lipsync"
    assert calls == [(clips, f"{job_id}.mp3")]
    assert len(commands) == 1
    assert "concat" not in commands[0]
    assert commands[0][commands[0].index("-i") + 1].endswith(f"{job_id}.talking.mp4")
    assert video_bytes(tmp_path, job_id) == b"video"
    assert not (tmp_path / "outputs" / "video" / f"{job_id}.talking.mp4").exists()


def test_pipeline_narrates_in_the_mentors_cloned_voice(tmp_path, monkeypatch):
    job_id = "cloned-voice-job"
    calls = []

    async def speak(text, reference, output, **kwargs):
        calls.append((text, reference, kwargs))
        Path(output).write_bytes(b"cloned audio")
        return True

    run_fallback_pipeline(
        tmp_path, monkeypatch, job_id, speak=speak, voice_sample="/packs/owl/voice.wav"
    )

    status = api.job_status[job_id]
    assert status["status"] == "ready"
    assert status["meta"]["voice"] == "cloned"
    assert calls == [("A short lesson.", "/packs/owl/voice.wav", {"expressiveness": 0.7})]
    assert (tmp_path / "outputs" / "audio" / f"{job_id}.mp3").read_bytes() == b"cloned audio"


def test_pipeline_uses_the_stock_voice_when_the_voice_worker_is_down(tmp_path, monkeypatch):
    job_id = "voice-down-job"

    run_fallback_pipeline(tmp_path, monkeypatch, job_id, voice_sample="/packs/owl/voice.wav")

    status = api.job_status[job_id]
    assert status["status"] == "ready"
    assert status["meta"]["voice"] == "en-GB-RyanNeural"
    assert (tmp_path / "outputs" / "audio" / f"{job_id}.mp3").read_bytes() == b"audio"


def test_pipeline_respects_a_voice_chosen_by_the_learner(tmp_path, monkeypatch):
    job_id = "learner-voice-job"
    speak = AsyncMock(return_value=True)

    run_fallback_pipeline(
        tmp_path,
        monkeypatch,
        job_id,
        speak=speak,
        voice_sample="/packs/owl/voice.wav",
        voice_id="en-IN-NeerjaNeural",
    )

    assert api.job_status[job_id]["meta"]["voice"] == "en-IN-NeerjaNeural"
    speak.assert_not_called()
