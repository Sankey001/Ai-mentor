import os

from dotenv import load_dotenv
if not os.getenv("PYTEST_CURRENT_TEST"):
    load_dotenv(override=False)

# Primary AI provider: "gemini" (default, needs GEMINI_API_KEY) or "ollama"
# (a model running locally through Ollama, no key needed).
AI_PROVIDER = (os.getenv("AI_PROVIDER", "gemini") or "gemini").strip().lower()
if AI_PROVIDER not in {"gemini", "ollama"}:
    raise ValueError(f"AI_PROVIDER must be 'gemini' or 'ollama', not {AI_PROVIDER!r}.")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Local models through Ollama (https://ollama.com), used when AI_PROVIDER=ollama.
OLLAMA_URL = (os.getenv("OLLAMA_URL", "http://127.0.0.1:11434") or "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b") or "qwen2.5:7b"

# LLM call timeout in seconds
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "60") or "60")
# A local model is slower, and its first call also loads it into memory.
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180") or "180")

# Optional: Fallback AI provider
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Optional: Cloudinary storage
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# D-ID AI Avatar
DID_API_KEY = os.getenv("DID_API_KEY")
DID_API_URL = os.getenv("DID_API_URL", "https://api.d-id.com")
DID_SOURCE_URL = os.getenv("DID_SOURCE_URL")

# Warm voice and face workers started by start_app.py. Empty disables a worker.
VOICE_WORKER_URL = os.getenv("VOICE_WORKER_URL", "http://127.0.0.1:8101")
FACE_WORKER_URL = os.getenv("FACE_WORKER_URL", "http://127.0.0.1:8102")

# Optional: LivePortrait, which turns uploaded mentor images into moving clips.
PORTRAIT_PYTHON = os.getenv("PORTRAIT_PYTHON", "")
LIVEPORTRAIT_DIR = os.getenv("LIVEPORTRAIT_DIR", "")

GROQ_ENABLED = bool(GROQ_API_KEY)

CLOUDINARY_ENABLED = all([
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
])


def validate_config():
    if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is required for the AI service.")


