"""Face worker: keeps MuseTalk loaded and makes a mentor's mouth follow new audio.

Run it with the Python environment MuseTalk is installed in:

    python face_worker.py --musetalk-dir /opt/MuseTalk --cache-dir /path/cache \\
        --allowed-root /path/to/ai_service --prepare-dir /path/to/backend/input

Face detection and face masks are worked out once per mentor (the avatar) and
saved in the cache, so each request only runs the lip-sync model itself.
POST /animate {"clips", "audio", "output"} writes the talking video to ``output``.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from worker_http import check_path, serve

FPS = 25
MAX_FRAMES = 375  # 15 seconds of footage per mentor, shared between their clips
BATCH_SIZE = 8  # larger batches are no faster on a laptop GPU and need more memory
EXTRA_MARGIN = 10


def avatar_key(clips: list[str]) -> str:
    """Identify a set of clips; the key changes when any clip is replaced or edited."""
    stamp = [(os.path.realpath(c), os.path.getsize(c), os.stat(c).st_mtime_ns) for c in clips]
    return hashlib.sha1(json.dumps(["v2", MAX_FRAMES, stamp]).encode()).hexdigest()[:16]


def read_frames(clips: list[str], limit: int = MAX_FRAMES) -> tuple[list, list[int]]:
    """Read an equal share of frames from each clip; return (frames, clip number per frame)."""
    import cv2

    frames, owners = [], []
    per_clip = max(1, limit // len(clips))
    for number, clip in enumerate(clips):
        capture = cv2.VideoCapture(clip)
        for _ in range(per_clip):
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
            owners.append(number)
        capture.release()
    if frames and any(f.shape != frames[0].shape for f in frames):
        raise ValueError("all clips of a mentor must have the same resolution")
    return frames, owners


def is_grayscale(frame) -> bool:
    """True when a frame has (almost) no colour, such as a black-and-white photo."""
    spread = frame.max(axis=2).astype("int16") - frame.min(axis=2)
    return float(spread.mean()) < 3.0


def build_segments(owners: list[int], keep: list[int]) -> list[list[int]]:
    """Group the kept frames by clip; each group plays forward then backward.

    A reply stays inside one group, so the mentor changes pose between
    sentences and never in the middle of one.
    """
    groups: dict[int, list[int]] = {}
    for position, frame_index in enumerate(keep):
        groups.setdefault(owners[frame_index], []).append(position)
    return [group + group[::-1] for _, group in sorted(groups.items())]


def pick_encoder() -> list[str]:
    """Use the GPU video encoder when FFmpeg has one that works, else x264."""
    probe = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"]
    if subprocess.run(probe, capture_output=True).returncode == 0:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


class FaceWorker:
    def __init__(self, musetalk_dir: str, cache_dir: str, roots: list[str]) -> None:
        os.chdir(musetalk_dir)  # MuseTalk loads its models from relative paths
        sys.path.insert(0, musetalk_dir)
        import torch
        from transformers import WhisperModel
        from musetalk.utils.audio_processor import AudioProcessor
        from musetalk.utils.utils import load_all_model

        torch.backends.cudnn.benchmark = True  # pick the fastest kernels for this GPU
        self.torch = torch
        self.roots = roots
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        # MuseTalk loads its weights onto the GPU in full precision; load them on
        # the CPU instead and move them over in half precision, halving peak memory.
        torch_load = torch.load
        torch.load = lambda *args, **kwargs: torch_load(*args, **{**kwargs, "map_location": "cpu"})
        try:
            vae, unet, pe = load_all_model(
                unet_model_path="./models/musetalkV15/unet.pth",
                vae_type="sd-vae",
                unet_config="./models/musetalkV15/musetalk.json",
                device=torch.device("cpu"),
            )
        finally:
            torch.load = torch_load
        self.pe = pe.half().to(self.device)
        vae.vae = vae.vae.half().to(self.device)
        unet.model = unet.model.half().to(self.device)
        self.vae, self.unet = vae, unet
        self.dtype = unet.model.dtype
        self.timesteps = torch.tensor([0], device=self.device)
        self.audio_processor = AudioProcessor(feature_extractor_path="./models/whisper")
        self.whisper = WhisperModel.from_pretrained("./models/whisper")
        self.whisper = self.whisper.to(device=self.device, dtype=self.dtype).eval()
        self.fast_decoder = self._load_fast_decoder()
        self.encoder = pick_encoder()
        self.avatars: dict[str, dict] = {}
        self.failed: dict[str, str] = {}

    def _load_fast_decoder(self):
        """TAESD decodes the mouth images about 10x faster than the full VAE."""
        try:
            from diffusers import AutoencoderTiny

            decoder = AutoencoderTiny.from_pretrained("./models/taesd", torch_dtype=self.dtype)
            print("[face] fast decoder (TAESD) loaded", flush=True)
            return decoder.to(self.device)
        except Exception as error:
            print(f"[face] fast decoder unavailable ({error!r}); using the full VAE", flush=True)
            return None

    def decode_faces(self, latents):
        if self.fast_decoder is None:
            return self.vae.decode_latents(latents.to(dtype=self.vae.vae.dtype))
        images = self.fast_decoder.decode(latents.to(dtype=self.dtype)).sample
        images = (images / 2 + 0.5).clamp(0, 1).cpu().permute(0, 2, 3, 1).float().numpy()
        return (images * 255).round().astype("uint8")[..., ::-1]  # RGB to BGR

    # ---------- avatar preparation (once per mentor) ----------

    def _prepare(self, clips: list[str], cache_path: str) -> None:
        import cv2
        import gc
        import musetalk.utils.preprocessing as preprocessing
        from musetalk.utils.blending import get_image_prepare_material
        from musetalk.utils.face_parsing import FaceParsing

        frames, _owners = read_frames(clips)
        if not frames:
            raise ValueError("the mentor clips have no readable frames")
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, frame in enumerate(frames):
                paths.append(os.path.join(tmp, f"{index:08d}.png"))
                cv2.imwrite(paths[-1], frame)
            coords, _ = preprocessing.get_landmark_and_bbox(paths, 0)

        face_parsing = FaceParsing(left_cheek_width=90, right_cheek_width=90)
        data = {"keep": [], "coords": [], "latents": [], "masks": [], "mask_boxes": []}
        for index, (box, frame) in enumerate(zip(coords, frames)):
            if box == preprocessing.coord_placeholder:
                continue  # no face in this frame
            x1, y1, x2, y2 = (int(v) for v in box)
            y2 = min(y2 + EXTRA_MARGIN, frame.shape[0])
            crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
            mask, mask_box = get_image_prepare_material(
                frame, [x1, y1, x2, y2], fp=face_parsing, mode="jaw"
            )
            data["keep"].append(index)
            data["coords"].append((x1, y1, x2, y2))
            data["latents"].append(self.vae.get_latents_for_unet(crop).cpu())
            data["masks"].append(mask)
            data["mask_boxes"].append(mask_box)
        del face_parsing
        # Face detection models are only needed here; release their GPU memory.
        for name in ("model", "fa"):
            if hasattr(preprocessing, name):
                delattr(preprocessing, name)
        sys.modules.pop("musetalk.utils.preprocessing", None)
        gc.collect()
        self.torch.cuda.empty_cache()

        if not data["keep"]:
            raise ValueError("no face was found in the mentor clips")
        self.torch.save(data, cache_path)

    def avatar(self, clips: list[str]) -> dict:
        key = avatar_key(clips)
        if key in self.avatars:
            return self.avatars[key]
        if key in self.failed:
            raise ValueError(self.failed[key])
        cache_path = os.path.join(self.cache_dir, f"{key}.pt")
        try:
            if not os.path.exists(cache_path):
                started = time.time()
                print(f"[face] preparing avatar from {len(clips)} clip(s)...", flush=True)
                self._prepare(clips, cache_path)
                print(f"[face] avatar ready in {time.time() - started:.0f}s", flush=True)
            data = self.torch.load(cache_path)
            frames, owners = read_frames(clips)
            segments = build_segments(owners, data["keep"])
            frames = [frames[i] for i in data["keep"]]
        except Exception as error:  # remember it, so a bad mentor fails fast next time
            self.failed[key] = f"avatar could not be prepared: {error}"
            raise ValueError(self.failed[key]) from error

        avatar = {
            "frames": frames,
            "alphas": [(mask.astype("float32") / 255)[..., None] for mask in data["masks"]],
            "coords": data["coords"],
            "latents": data["latents"],
            "mask_boxes": data["mask_boxes"],
            "segments": segments,
            "grayscale": is_grayscale(frames[0]),
            "cursors": [0] * len(segments),
            "turn": 0,
        }
        self.avatars = {key: avatar}  # keep one mentor in memory at a time
        return avatar

    def prepare_folder(self, input_dir: str) -> None:
        """Prepare every mentor folder that has clips, so the first reply is fast."""
        for folder in sorted(glob.glob(os.path.join(input_dir, "*", ""))):
            clips = sorted(os.path.realpath(p) for p in glob.glob(os.path.join(folder, "*.mp4")))
            if not clips:
                continue
            try:
                self.avatar(clips)
                self._warm_up(clips)
                print(f"[face] {os.path.basename(folder.rstrip('/'))}: ready", flush=True)
            except Exception as error:
                print(f"[face] {os.path.basename(folder.rstrip('/'))}: skipped ({error})", flush=True)

    def _warm_up(self, clips: list[str]) -> None:
        """Run one second of silence through the model so the first reply is not slow."""
        with tempfile.TemporaryDirectory(dir=self.cache_dir) as tmp:
            silence = os.path.join(tmp, "silence.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=16000:cl=mono", "-t", "1", silence], check=True)
            self.animate({"clips": clips, "audio": silence, "output": os.path.join(tmp, "out.mp4")})

    def prepare(self, payload: dict) -> dict:
        """Get a mentor's clips ready ahead of the first conversation."""
        clips = payload.get("clips")
        if not isinstance(clips, list) or not clips or len(clips) > 50:
            raise ValueError("clips must be a list of 1 to 50 videos")
        clips = [check_path(c, self.roots) for c in clips]
        self.avatar(clips)
        self._warm_up(clips)
        return {"ready": True}

    # ---------- lip-sync (every request) ----------

    def animate(self, payload: dict) -> dict:
        clips = payload.get("clips")
        if not isinstance(clips, list) or not clips or len(clips) > 50:
            raise ValueError("clips must be a list of 1 to 50 videos")
        clips = [check_path(c, self.roots) for c in clips]
        audio = check_path(payload.get("audio"), self.roots)
        output = check_path(payload.get("output"), self.roots, must_exist=False)

        avatar = self.avatar(clips)
        started = time.time()
        torch = self.torch
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "audio.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", audio, "-ar", "16000", "-ac", "1", wav],
                           check=True)
            features, length = self.audio_processor.get_audio_feature(wav, weight_dtype=self.dtype)
            chunks = self.audio_processor.get_whisper_chunk(
                features, self.device, self.dtype, self.whisper, length, fps=FPS,
                audio_padding_length_left=2, audio_padding_length_right=2,
            )

        height, width = avatar["frames"][0].shape[:2]
        encoder = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{width}x{height}", "-r", str(FPS), "-i", "-", "-i", audio,
             "-map", "0:v", "-map", "1:a", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
             *self.encoder, "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             "-movflags", "+faststart", output],
            stdin=subprocess.PIPE,
        )

        # The GPU makes mouths in batches while other threads blend them into
        # the frames and a writer feeds the encoder in order.
        pending: queue.Queue = queue.Queue(maxsize=64)
        errors: list[BaseException] = []

        def write_frames() -> None:
            while (future := pending.get()) is not None:
                try:
                    if not errors:
                        encoder.stdin.write(future.result().tobytes())
                except BaseException as error:
                    errors.append(error)

        writer = threading.Thread(target=write_frames)
        writer.start()
        turn = avatar["turn"]
        segment = avatar["segments"][turn]
        start = avatar["cursors"][turn]
        try:
            with ThreadPoolExecutor(max_workers=3) as pool, torch.inference_mode():
                for first in range(0, len(chunks), BATCH_SIZE):
                    batch = chunks[first:first + BATCH_SIZE]
                    indexes = [segment[(start + first + i) % len(segment)] for i in range(len(batch))]
                    latents = torch.cat([avatar["latents"][i] for i in indexes]).to(
                        self.device, dtype=self.dtype
                    )
                    audio_features = self.pe(torch.stack(list(batch)).to(self.device))
                    predicted = self.unet.model(
                        latents, self.timesteps, encoder_hidden_states=audio_features
                    ).sample
                    faces = self.decode_faces(predicted)
                    for index, face in zip(indexes, faces):
                        pending.put(pool.submit(blend_face, avatar, index, face))
        finally:
            pending.put(None)
            writer.join()
            encoder.stdin.close()
            encoder.wait()
            torch.cuda.empty_cache()  # leave GPU memory free for the voice worker
        if errors or encoder.returncode != 0 or not os.path.exists(output):
            raise RuntimeError("video encoding failed")
        # This pose continues from here next time; the next reply uses the next pose.
        avatar["cursors"][turn] = (start + len(chunks)) % len(segment)
        avatar["turn"] = (turn + 1) % len(avatar["segments"])
        return {"frames": len(chunks), "seconds": round(time.time() - started, 2)}


def blend_face(avatar: dict, index: int, face):
    """Paste a generated mouth region back into its original frame.

    Same result as MuseTalk's get_image_blending, but works on the face area
    only, which is several times faster than converting whole frames.
    """
    import cv2
    import numpy as np

    frame = avatar["frames"][index]
    x1, y1, x2, y2 = avatar["coords"][index]
    xs, ys, xe, ye = avatar["mask_boxes"][index]
    height, width = frame.shape[:2]
    cx0, cy0, cx1, cy1 = max(xs, 0), max(ys, 0), min(xe, width), min(ye, height)

    out = frame.copy()
    region = out[cy0:cy1, cx0:cx1]
    patch = region.copy()
    if avatar.get("grayscale"):  # keep a black-and-white picture free of coloured lips
        face = cv2.cvtColor(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    patch[y1 - cy0:y2 - cy0, x1 - cx0:x2 - cx0] = cv2.resize(face, (x2 - x1, y2 - y1))
    alpha = avatar["alphas"][index][cy0 - ys:cy1 - ys, cx0 - xs:cx1 - xs]
    region[:] = (patch * alpha + region * (1 - alpha)).round().astype(np.uint8)
    return out


def main() -> None:
    # MuseTalk prints CJK brackets while preparing a face; on Windows the console
    # encoding (e.g. cp1252) cannot encode them and the print fails the avatar.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--musetalk-dir", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--prepare-dir", help="prepare every mentor folder here before serving")
    args = parser.parse_args()

    worker = FaceWorker(
        os.path.realpath(args.musetalk_dir),
        os.path.realpath(args.cache_dir),
        [os.path.realpath(root) for root in args.allowed_root],
    )
    print(f"[face] MuseTalk loaded on {worker.device}", flush=True)
    if args.prepare_dir:
        worker.prepare_folder(args.prepare_dir)
    serve(args.port, {"/animate": worker.animate, "/prepare": worker.prepare}, "face")


if __name__ == "__main__":
    main()
