# AI Mentor

A live AI mentor that teaches lessons and answers questions with a face, lip sync and a cloned voice, running on your own computer.

Specs, download sizes and what each part needs: [REQUIREMENTS.md](REQUIREMENTS.md)

## Install

One command downloads the project into `./Ai-mentor` and installs everything it needs. It asks before the large optional parts and can be run again safely.

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/Sankey001/Ai-mentor/main/setup.ps1 | iex
```

**Linux / macOS** (Terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/Sankey001/Ai-mentor/main/setup.sh | bash
```

## Run

```powershell
cd Ai-mentor
.\run.cmd      # Windows
./run.sh       # Linux / macOS
```

Open http://localhost:5173/mentor. API keys, models and model folders: http://localhost:5173/mentor-settings. Stop with `Ctrl+C`.

## Setup options

From inside the project: `.\setup.cmd -All` on Windows, `./setup.sh --all` on Linux and macOS.

| Windows       | Linux / macOS   | What it does                                                       |
| ------------- | --------------- | ------------------------------------------------------------------ |
| `-All`        | `--all`         | Also install lip sync and voice cloning, without asking            |
| `-LipSync`    | `--lipsync`     | Also install lip sync (NVIDIA GPU)                                 |
| `-Voice`      | `--voice`       | Also install voice cloning                                         |
| `-Model X`    | `--model X`     | Local AI model to download and use (default `gemma3`)              |
| `-ApiOnly`    | `--api-only`    | No local model; replies use your Gemini or Groq key                |
| `-Yes`        | `--yes`         | Ask nothing; install only what the options name                    |
| `-Dir PATH`   | `--dir PATH`    | Where to put the project (default `./Ai-mentor`)                   |
| `-SkipSystem` | `--skip-system` | Only check tools like Git, FFmpeg and Node.js; do not install them |
