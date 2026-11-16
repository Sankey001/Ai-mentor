"""Download the model files the lip-sync and voice workers need, from huggingface.co.

Run it with the worker's own Python (both environments include huggingface_hub):

    local\\lip_sync\\.venv\\Scripts\\python ai_service\\workers\\download_models.py lipsync
    local\\voice_clone\\.venv\\Scripts\\python ai_service\\workers\\download_models.py voice

Only what the workers load is fetched: MuseTalk v1.5 (not v1 or the training-only
SyncNet) and Chatterbox Turbo plus the standard model it switches to for a calm
or lively voice. Files already downloaded are skipped, and an interrupted
download resumes.
"""

from __future__ import annotations

import argparse
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL = os.path.join(REPO_ROOT, "local")

# (repository, file, sub-folder of MuseTalk/models)
MUSETALK_FILES = [
    ("TMElyralab/MuseTalk", "musetalkV15/unet.pth", ""),
    ("TMElyralab/MuseTalk", "musetalkV15/musetalk.json", ""),
    ("stabilityai/sd-vae-ft-mse", "config.json", "sd-vae"),
    ("stabilityai/sd-vae-ft-mse", "diffusion_pytorch_model.bin", "sd-vae"),
    ("openai/whisper-tiny", "config.json", "whisper"),
    ("openai/whisper-tiny", "pytorch_model.bin", "whisper"),
    ("openai/whisper-tiny", "preprocessor_config.json", "whisper"),
    ("yzd-v/DWPose", "dw-ll_ucoco_384.pth", "dwpose"),
    ("ManyOtherFunctions/face-parse-bisent", "79999_iter.pth", "face-parse-bisent"),
    ("ManyOtherFunctions/face-parse-bisent", "resnet18-5c106cde.pth", "face-parse-bisent"),
    # Small fast decoder the face worker uses when present.
    ("madebyollin/taesd", "config.json", "taesd"),
    ("madebyollin/taesd", "diffusion_pytorch_model.safetensors", "taesd"),
]
CHATTERBOX_STANDARD = ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
                       "tokenizer.json", "conds.pt"]


def download_lipsync(musetalk_dir: str) -> None:
    from huggingface_hub import hf_hub_download

    models = os.path.join(musetalk_dir, "models")
    for repo, name, sub in MUSETALK_FILES:
        path = hf_hub_download(repo, name, local_dir=os.path.join(models, sub) if sub else models)
        print("ok", path, flush=True)


def download_voice() -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    print("ok", snapshot_download(
        "ResembleAI/chatterbox-turbo",
        allow_patterns=["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"],
    ), flush=True)
    for name in CHATTERBOX_STANDARD:
        print("ok", hf_hub_download("ResembleAI/chatterbox", name), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("worker", choices=["lipsync", "voice"])
    parser.add_argument("--musetalk-dir", default=os.path.join(LOCAL, "lip_sync", "MuseTalk"))
    args = parser.parse_args()

    # Keep the voice models inside the project folder, where start_app.py looks.
    os.environ.setdefault("HF_HOME", os.path.join(LOCAL, "models", "huggingface"))
    try:
        # Trust the system's certificates, so downloads work behind antivirus
        # HTTPS scanning or a company proxy.
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    if args.worker == "lipsync":
        download_lipsync(args.musetalk_dir)
    else:
        download_voice()
    print("done", flush=True)


if __name__ == "__main__":
    main()
