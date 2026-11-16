"""Voice worker: keeps Chatterbox loaded and speaks text in a mentor's recorded voice.

Run it with the Python environment Chatterbox is installed in:

    python voice_worker.py --port 8101 --allowed-root /path/to/ai_service

POST /speak {"text", "reference", "output", "expressiveness"} writes speech to
``output`` (.wav, or .mp3 through FFmpeg). Only clone the voice of a person who
has agreed to it.
"""

from __future__ import annotations

import argparse
import os
import subprocess

from worker_http import check_path, serve, split_text

PAUSE_SECONDS = 0.2
MAX_TEXT_CHARS = 5000


def load_model(device: str, prefer_turbo: bool):
    """Load Chatterbox Turbo, or the standard model if Turbo cannot load."""
    if prefer_turbo:
        try:
            from chatterbox.tts_turbo import ChatterboxTurboTTS

            return ChatterboxTurboTTS.from_pretrained(device=device), "turbo"
        except Exception as error:
            print(f"[voice] Turbo unavailable ({error!r}); using standard model", flush=True)
    from chatterbox.tts import ChatterboxTTS

    return ChatterboxTTS.from_pretrained(device=device), "standard"


def wanted_model(expressiveness: float, turbo_allowed: bool) -> str:
    """Turbo is faster but always speaks in a balanced style; a calm or lively
    delivery needs the standard model."""
    return "turbo" if turbo_allowed and abs(expressiveness - 0.5) < 0.05 else "standard"


class VoiceWorker:
    def __init__(self, roots: list[str], device: str, prefer_turbo: bool) -> None:
        import torch

        self.torch = torch
        self.roots = roots
        self.device = device
        self.turbo_allowed = prefer_turbo
        self.model, self.kind = load_model(device, prefer_turbo)
        self.turbo_allowed = self.kind == "turbo"
        self.voice_key = None

    def use_model(self, kind: str) -> None:
        """Swap to the other model when a mentor's delivery needs it (takes a few seconds)."""
        if kind == self.kind:
            return
        print(f"[voice] switching to the {kind} model", flush=True)
        self.model = None
        self.voice_key = None
        self.torch.cuda.empty_cache()
        self.model, self.kind = load_model(self.device, prefer_turbo=kind == "turbo")

    def speak(self, payload: dict) -> dict:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("text must be 1 to 5000 characters")
        reference = check_path(payload.get("reference"), self.roots)
        output = check_path(payload.get("output"), self.roots, must_exist=False)
        expressiveness = min(1.0, max(0.0, float(payload.get("expressiveness", 0.5))))

        self.use_model(wanted_model(expressiveness, self.turbo_allowed))

        # Reading the reference voice is the slow part, so do it once per voice.
        key = (reference, os.path.getmtime(reference), round(expressiveness, 2))
        if key != self.voice_key:
            self.model.prepare_conditionals(reference, exaggeration=expressiveness)
            self.voice_key = key

        torch = self.torch
        pause = torch.zeros(1, int(self.model.sr * PAUSE_SECONDS))
        pieces = []
        for chunk in split_text(text):
            if self.kind == "turbo":
                wav = self.model.generate(chunk)
            else:
                wav = self.model.generate(chunk, exaggeration=expressiveness)
            pieces += [wav.cpu(), pause]
        audio = torch.cat(pieces[:-1], dim=1)

        import torchaudio

        wav_path = output if output.endswith(".wav") else output + ".tmp.wav"
        torchaudio.save(wav_path, audio, self.model.sr)
        if wav_path != output:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", wav_path, "-q:a", "2", output],
                    check=True,
                )
            finally:
                os.remove(wav_path)
        return {"seconds": round(audio.shape[-1] / self.model.sr, 2), "model": self.kind}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--device", default=None, help="cuda or cpu; detected when omitted")
    parser.add_argument("--standard", action="store_true", help="skip Turbo, use the standard model")
    args = parser.parse_args()

    import torch

    device = args.device or (
        "cuda" if torch.cuda.is_available()
        else "mps" if torch.backends.mps.is_available()  # Apple silicon GPU
        else "cpu"
    )
    worker = VoiceWorker(args.allowed_root, device, prefer_turbo=not args.standard)
    print(f"[voice] {worker.kind} model loaded on {device}", flush=True)
    serve(args.port, {"/speak": worker.speak}, "voice")


if __name__ == "__main__":
    main()
