"""Turn the still images in each mentor folder into short, gently moving clips.

Run it with the Python environment LivePortrait is installed in:

    python portrait_maker.py --input-dir /path/to/backend/input --liveportrait-dir /opt/LivePortrait

Every image in backend/input/<mentor>/ (any format FFmpeg or Pillow can read)
becomes auto_<name>_<hash>.mp4 next to it: the head moves slightly and the
eyes blink, so a flat picture reads as a 3D character. The lip-sync worker
then treats these like any other mentor clip. A clip is made once and remade
only when its image changes; clips whose image was deleted are removed.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

MIN_SIDE = 512
MAX_SIDE = 1280
FPS = 25
AUTO_PREFIX = "auto_"
NOT_IMAGES = {".mp4", ".mov", ".webm", ".mkv", ".json", ".txt", ".md", ".wav", ".mp3", ".m4a",
              ".flac", ".ogg", ".pkl", ".pt"}


def find_source_files(mentor_dir: str) -> list[str]:
    """Return files that may be images; the format is checked later by decoding them."""
    files = []
    for name in sorted(os.listdir(mentor_dir)):
        path = os.path.join(mentor_dir, name)
        if name.startswith((".", AUTO_PREFIX)) or not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() in NOT_IMAGES:
            continue
        files.append(path)
    return files


def _target_size(width: int, height: int) -> tuple[int, int]:
    scale = max(1.0, MIN_SIDE / min(width, height))
    scale = min(scale, MAX_SIDE / max(width, height)) if max(width, height) * scale > MAX_SIDE else scale
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


def _normalize_with_pillow(source: str, target: str) -> bool:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        import pillow_heif  # optional: HEIC/HEIF photos from phones

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        with Image.open(source) as image:
            image.seek(0)  # first frame of an animated image
            image = ImageOps.exif_transpose(image).convert("RGB")
            image = image.resize(_target_size(*image.size), Image.LANCZOS)
            image.save(target, "PNG")
        return True
    except Exception:
        return False


def _normalize_with_ffmpeg(source: str, target: str) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", source],
        capture_output=True, text=True,
    )
    try:
        width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    except ValueError:
        return False
    if width <= 0 or height <= 0:
        return False
    new_width, new_height = _target_size(width, height)
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", source, "-frames:v", "1",
         "-vf", f"scale={new_width}:{new_height}:flags=lanczos,format=rgb24", "-f", "image2",
         "-c:v", "png", target],
        capture_output=True,
    )
    return result.returncode == 0 and os.path.isfile(target)


def normalize_image(source: str, target: str) -> bool:
    """Decode an image of any supported format into an RGB PNG of a workable size."""
    if _normalize_with_pillow(source, target) or _normalize_with_ffmpeg(source, target):
        return True
    if os.path.exists(target):
        os.remove(target)
    return False


def extend_edges(image: str, target: str, share: float = 0.25) -> None:
    """Grow the picture by repeating its edge pixels outward.

    The face is cropped at a tilt; without this, a face near the picture's
    edge leaves black corners in the clip.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", image],
        check=True, capture_output=True, text=True,
    )
    width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    pad_x, pad_y = int(width * share / 2) * 2, int(height * share / 2) * 2
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", image, "-vf",
         f"pad={width + 2 * pad_x}:{height + 2 * pad_y}:{pad_x}:{pad_y},"
         f"fillborders=left={pad_x}:right={pad_x}:top={pad_y}:bottom={pad_y}:mode=smear",
         "-frames:v", "1", target],
        check=True,
    )


def clip_name(source: str, tag: str) -> str:
    """Name of the clip made from an image; it changes when the image or settings change."""
    digest = hashlib.sha1(tag.encode())
    with open(source, "rb") as image_file:
        digest.update(image_file.read())
    stem = re.sub(r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(source))[0].lower())
    return f"{AUTO_PREFIX}{stem.strip('_') or 'image'}_{digest.hexdigest()[:8]}.mp4"


def plan(mentor_dir: str, tag: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (images that still need a clip, generated clips that are no longer wanted)."""
    wanted = {os.path.join(mentor_dir, clip_name(source, tag)): source
              for source in find_source_files(mentor_dir)}
    todo = [(source, clip) for clip, source in wanted.items() if not os.path.exists(clip)]
    existing = glob.glob(os.path.join(glob.escape(mentor_dir), f"{AUTO_PREFIX}*.mp4"))
    return todo, sorted(clip for clip in existing if clip not in wanted)


def make_clip(source: str, clip: str, settings: dict) -> None:
    """Animate one image with LivePortrait and save it as a 25 fps H.264 clip."""
    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "portrait.png")
        decoded = os.path.join(tmp, "decoded.png")
        if not normalize_image(source, decoded):
            raise ValueError("not a readable image")
        extend_edges(decoded, image)
        subprocess.run(
            [settings["python"], "inference.py", "-s", image, "-d", settings["driving"],
             "-o", tmp, "--driving-multiplier", str(settings["multiplier"]),
             "--animation-region", settings["region"], "--no-flag-pasteback"],
            cwd=settings["liveportrait_dir"], check=True, capture_output=True,
        )
        results = [p for p in glob.glob(os.path.join(tmp, "*.mp4")) if not p.endswith("_concat.mp4")]
        if not results:
            raise RuntimeError("LivePortrait wrote no video")
        partial = clip + ".part.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", results[0], "-an", "-r", str(FPS),
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", partial],
            check=True,
        )
        os.replace(partial, clip)


def process_input_dir(input_dir: str, settings: dict) -> dict[str, int]:
    """Make missing clips and remove stale ones for every mentor folder."""
    report = {"made": 0, "failed": 0, "removed": 0}
    for mentor_dir in sorted(glob.glob(os.path.join(glob.escape(input_dir), "*", ""))):
        mentor = os.path.basename(mentor_dir.rstrip(os.sep))
        todo, stale = plan(mentor_dir, settings["tag"])
        for source, clip in todo:
            try:
                make_clip(source, clip, settings)
                report["made"] += 1
                print(f"[portrait] {mentor}: made a clip from {os.path.basename(source)}", flush=True)
            except Exception as error:
                report["failed"] += 1
                detail = getattr(error, "stderr", None) or str(error)
                if isinstance(detail, bytes):
                    detail = detail.decode(errors="replace")
                print(f"[portrait] {mentor}: skipped {os.path.basename(source)} "
                      f"({detail.strip().splitlines()[-1] if detail.strip() else error})", flush=True)
        for clip in stale:
            os.remove(clip)
            report["removed"] += 1
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--liveportrait-dir", required=True)
    parser.add_argument("--driving", help="motion source video; a calm built-in one by default")
    parser.add_argument("--multiplier", type=float, default=0.5, help="how strong the motion is")
    parser.add_argument("--region", default="all", choices=["all", "pose", "exp", "eyes"])
    args = parser.parse_args()

    liveportrait_dir = os.path.realpath(args.liveportrait_dir)
    driving = os.path.realpath(args.driving) if args.driving else os.path.join(
        liveportrait_dir, "assets", "examples", "driving", "d13.mp4")
    if not os.path.isfile(os.path.join(liveportrait_dir, "inference.py")) or not os.path.isfile(driving):
        raise SystemExit("LivePortrait folder or driving video not found.")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("FFmpeg is required.")

    settings = {
        "python": sys.executable,
        "liveportrait_dir": liveportrait_dir,
        "driving": driving,
        "multiplier": args.multiplier,
        "region": args.region,
        "tag": f"{os.path.basename(driving)}|{args.multiplier}|{args.region}|v2",
    }
    report = process_input_dir(os.path.realpath(args.input_dir), settings)
    print(f"[portrait] done: {report}", flush=True)


if __name__ == "__main__":
    main()
