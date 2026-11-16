# Requirements

What AI Mentor needs to run on your own computer, and how much it downloads. Sizes are measured on a working install (Windows 11, November 2026) and rounded.

## Computer

|               | Minimum (chat only)                               | Recommended (lip sync + own voice)                  |
| ------------- | ------------------------------------------------- | --------------------------------------------------- |
| System        | Windows 10/11, 64-bit                             | Windows 11, 64-bit                                  |
| Memory (RAM)  | 8 GB                                              | 16 GB; close games and other heavy apps first       |
| Graphics card | Not required (local models run slowly on the CPU) | NVIDIA, 8 GB video memory (tested: RTX 4060 Laptop) |
| NVIDIA driver | —                                                 | 550 or newer (CUDA 12.4)                            |
| Free disk     | 5 GB                                              | 30 GB, on an SSD                                    |
| Internet      | For installing only                               | For installing only                                 |

With 8 GB of video memory, `gemma3` (3.3 GB) runs fully on the GPU beside lip sync and voice, and replies in seconds. `qwen3.5:9b` (6.6 GB) only partly fits: replies take about a minute, and several minutes with "Think first".

## Downloads

| Part                                                                 | Needed for       | Download / disk |
| -------------------------------------------------------------------- | ---------------- | --------------- |
| App code (this repository)                                           | Everything       | 300 MB          |
| Website packages (`frontend`, npm)                                   | Everything       | 460 MB          |
| AI service Python packages (`ai_service/backend/requirements.txt`)   | Everything       | 100 MB          |
| FFmpeg                                                               | Everything       | 670 MB          |
| Ollama                                                               | Local AI replies | 2.8 GB          |
| A local model: `gemma3` / `qwen3.5:9b`                               | Local AI replies | 3.3 GB / 6.6 GB |
| Lip-sync environment (`ai_service/workers/requirements-lipsync.txt`) | Lip sync         | 5.9 GB          |
| Lip-sync models (MuseTalk v1.5)                                      | Lip sync         | 4.2 GB          |
| Voice environment (`ai_service/workers/requirements-voice.txt`)      | Own voice        | 5.3 GB          |
| Voice models (Chatterbox Turbo + standard)                           | Own voice        | 7.1 GB          |
| Python 3.10 / 3.13 (installed by uv)                                 | Everything       | 100 MB each     |

Totals, roughly:

| Setup                                                               | Disk  |
| ------------------------------------------------------------------- | ----- |
| Chat with a local model (step 1 of the README)                      | 8 GB  |
| + lip sync (step 2)                                                 | 18 GB |
| + own voice (step 3)                                                | 30 GB |
| Online API instead of a local model (Gemini or Groq key, no Ollama) | 2 GB  |

The two GPU environments each carry their own copy of PyTorch with CUDA (MuseTalk needs PyTorch 2.0, Chatterbox 2.6), which is most of their size.

## Where things are stored

| What                                   | Where                                                                             |
| -------------------------------------- | --------------------------------------------------------------------------------- |
| Settings and API keys                  | `ai_service/backend/.env` (never uploaded; edit at `/mentor-settings`)            |
| Lip-sync and voice environments        | `local/lip_sync`, `local/voice_clone`                                             |
| Voice models                           | `local/models/huggingface` (or `HF_HOME`)                                         |
| Local AI models                        | Ollama's models folder (`OLLAMA_MODELS`; shown and changed at `/mentor-settings`) |
| Your mentors (photos, voice recording) | `ai_service/backend/input/<mentor>` (never uploaded)                              |

## Python and Node packages

| File                                          | Python   | Contents                                                   |
| --------------------------------------------- | -------- | ---------------------------------------------------------- |
| `ai_service/backend/requirements.txt`         | 3.10+    | AI service (FastAPI, Gemini, Groq, text-to-speech)         |
| `ai_service/workers/requirements-lipsync.txt` | 3.10     | MuseTalk lip sync: PyTorch 2.0.1 (CUDA 11.8), mmcv, mmpose |
| `ai_service/workers/requirements-voice.txt`   | 3.10     | Chatterbox voice cloning: PyTorch 2.6.0 (CUDA 12.4)        |
| `frontend/package.json`                       | Node 20+ | Website                                                    |

The full learning platform (user and admin APIs, admin website) also needs PostgreSQL and the setup in [initalSetup.md](initalSetup.md).
