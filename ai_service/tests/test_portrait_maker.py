"""Tests for turning mentor images into moving clips (no GPU model needed)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workers"))

import portrait_maker  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def make_image(path, size="320x240"):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}", "-frames:v", "1",
         str(path)],
        check=True,
    )


def image_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return tuple(int(v) for v in out.split(",")[:2])


def test_only_possible_images_are_picked_up(tmp_path):
    for name in ["face.webp", "Side View.JPG", "scan.heic", "noext", "talk.mp4", "mentor.json",
                 "voice.wav", "voice.mp3", "notes.txt", ".hidden.png", "auto_face_1234abcd.mp4"]:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "folder.png").mkdir()

    found = [Path(p).name for p in portrait_maker.find_source_files(str(tmp_path))]

    assert found == ["Side View.JPG", "face.webp", "noext", "scan.heic"]


@needs_ffmpeg
@pytest.mark.parametrize("extension", ["png", "jpg", "webp", "bmp", "tiff", "gif"])
def test_common_image_formats_are_normalised(tmp_path, extension):
    source = tmp_path / f"face.{extension}"
    make_image(source)
    target = tmp_path / "out.png"

    assert portrait_maker.normalize_image(str(source), str(target))

    width, height = image_size(target)
    assert min(width, height) >= portrait_maker.MIN_SIDE
    assert width % 2 == 0 and height % 2 == 0
    assert round(width / height, 1) == round(320 / 240, 1)


@needs_ffmpeg
def test_format_is_read_from_the_content_not_the_file_name(tmp_path):
    make_image(tmp_path / "real.webp")
    disguised = tmp_path / "photo.dat"
    shutil.copy(tmp_path / "real.webp", disguised)

    assert portrait_maker.normalize_image(str(disguised), str(tmp_path / "out.png"))


@needs_ffmpeg
def test_large_images_are_scaled_down(tmp_path):
    source = tmp_path / "big.png"
    make_image(source, size="4000x3000")
    target = tmp_path / "out.png"

    assert portrait_maker.normalize_image(str(source), str(target))
    assert max(image_size(target)) <= portrait_maker.MAX_SIDE


@needs_ffmpeg
def test_a_file_that_is_not_an_image_is_rejected(tmp_path):
    source = tmp_path / "broken.png"
    source.write_bytes(b"this is not an image")
    target = tmp_path / "out.png"

    assert not portrait_maker.normalize_image(str(source), str(target))
    assert not target.exists()


def test_clip_name_follows_the_image_content(tmp_path):
    image = tmp_path / "My Face (1).webp"
    image.write_bytes(b"one")
    first = portrait_maker.clip_name(str(image), "v1")

    assert first.startswith("auto_my_face_1_") and first.endswith(".mp4")
    assert portrait_maker.clip_name(str(image), "v1") == first
    assert portrait_maker.clip_name(str(image), "v2") != first
    image.write_bytes(b"two")
    assert portrait_maker.clip_name(str(image), "v1") != first


def test_plan_makes_missing_clips_and_drops_stale_ones(tmp_path):
    image = tmp_path / "face.png"
    image.write_bytes(b"img")
    done = tmp_path / "done.jpg"
    done.write_bytes(b"img2")
    (tmp_path / portrait_maker.clip_name(str(done), "v1")).write_bytes(b"clip")
    stale = tmp_path / "auto_gone_deadbeef.mp4"
    stale.write_bytes(b"old")
    own = tmp_path / "my_recording.mp4"
    own.write_bytes(b"mine")

    todo, stale_clips = portrait_maker.plan(str(tmp_path), "v1")

    assert todo == [(str(image), str(tmp_path / portrait_maker.clip_name(str(image), "v1")))]
    assert stale_clips == [str(stale)]


def test_each_mentor_folder_is_processed_and_one_bad_image_does_not_stop_the_rest(
    tmp_path, monkeypatch
):
    asha = tmp_path / "asha"
    asha.mkdir()
    (asha / "good.png").write_bytes(b"good")
    (asha / "bad.png").write_bytes(b"bad")
    (asha / "auto_old_00000000.mp4").write_bytes(b"old")
    bo = tmp_path / "bo"
    bo.mkdir()
    (bo / "talk.mp4").write_bytes(b"own clip")

    def fake_make_clip(source, clip, settings):
        if "bad" in source:
            raise RuntimeError("no face found")
        Path(clip).write_bytes(b"clip")

    monkeypatch.setattr(portrait_maker, "make_clip", fake_make_clip)

    report = portrait_maker.process_input_dir(str(tmp_path), settings={"tag": "v1"})

    assert report == {"made": 1, "failed": 1, "removed": 1}
    clips = sorted(p.name for p in asha.glob("*.mp4"))
    assert clips == [portrait_maker.clip_name(str(asha / "good.png"), "v1")]
    assert sorted(p.name for p in bo.iterdir()) == ["talk.mp4"]


@needs_ffmpeg
def test_edges_are_extended_without_black_borders(tmp_path):
    source = tmp_path / "white.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=white:size=200x100",
         "-frames:v", "1", str(source)],
        check=True,
    )
    target = tmp_path / "wide.png"

    portrait_maker.extend_edges(str(source), str(target))

    assert image_size(target) == (300, 148)
    corner = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(target), "-vf", "crop=4:4:0:0", "-f", "rawvideo",
         "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    assert min(corner) > 200
