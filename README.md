# AI Mentor

A live AI mentor that teaches lessons and answers questions with a face, lip sync and a cloned voice, running on your own computer.

Specs, download sizes and what each part needs: [REQUIREMENTS.md](REQUIREMENTS.md)

## Run

```powershell
cd ai_service
.venv\Scripts\python start_app.py
```

Open http://localhost:5173/mentor. API keys, models and model folders: http://localhost:5173/mentor-settings. Stop with `Ctrl+C`.

## Install (once, Windows PowerShell)

### 1. App and a local AI model (about 5 GB)

```powershell
winget install --source winget astral-sh.uv
winget install --source winget Gyan.FFmpeg
winget install --source winget Ollama.Ollama
winget install --source winget OpenJS.NodeJS.LTS
```

Open a new terminal, then:

```powershell
$env:UV_NATIVE_TLS = "true"
git clone https://github.com/Sankey001/Ai-mentor.git
cd Ai-mentor
uv venv ai_service\.venv --python 3.13
uv pip install --python ai_service\.venv\Scripts\python.exe -r ai_service\backend\requirements.txt
npm ci --prefix frontend
copy frontend\mentor-preview.env.example frontend\.env
copy ai_service\backend\.env.example ai_service\backend\.env
ollama pull gemma3
```

### 2. Lip sync (optional, about 10 GB, NVIDIA GPU)

```powershell
git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git local\lip_sync\MuseTalk
uv venv local\lip_sync\.venv --python 3.10
uv pip install --python local\lip_sync\.venv\Scripts\python.exe --index-strategy unsafe-best-match -r ai_service\workers\requirements-lipsync.txt
uv pip install --python local\lip_sync\.venv\Scripts\python.exe --no-deps mmpose==1.1.0 json-tricks munkres xtcocotools
local\lip_sync\.venv\Scripts\python ai_service\workers\download_models.py lipsync
```

### 3. Your own voice (optional, about 12 GB, NVIDIA GPU)

```powershell
uv venv local\voice_clone\.venv --python 3.10
uv pip install --python local\voice_clone\.venv\Scripts\python.exe --index-strategy unsafe-best-match -r ai_service\workers\requirements-voice.txt
local\voice_clone\.venv\Scripts\python ai_service\workers\download_models.py voice
```

Everything in `local\` is found automatically and is never uploaded to git.
