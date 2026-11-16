#!/usr/bin/env bash
# Install AI Mentor on Linux or macOS: tools, the app, a local AI model, and
# optionally lip sync (Linux + NVIDIA GPU) and voice cloning.
# Safe to run again: finished parts are skipped.
#
#   From anywhere (downloads the project into ./Ai-mentor):
#     curl -fsSL https://raw.githubusercontent.com/Sankey001/Ai-mentor/main/setup.sh | bash
#   From inside a downloaded copy:
#     bash setup.sh --all
#
# Options:
#   --dir PATH      where to put the project when downloading it (default ./Ai-mentor)
#   --model NAME    Ollama model for replies (default gemma3)
#   --lipsync       install lip sync (Linux with an NVIDIA GPU, ~10 GB)
#   --voice         install voice cloning (~12 GB; Apple silicon or NVIDIA recommended)
#   --all           both of the above
#   --api-only      skip Ollama and the local model (use a Gemini/Groq key instead)
#   --yes           do not ask; install only what the options name
#   --skip-system   do not install system tools (only check them)
set -euo pipefail

REPO_URL="https://github.com/Sankey001/Ai-mentor.git"
DIR=""; MODEL="gemma3"; MODEL_SET=0; LIPSYNC=0; VOICE=0; ALL=0; API_ONLY=0; YES=0; SKIP_SYSTEM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift ;;
    --model) MODEL="$2"; MODEL_SET=1; shift ;;
    --lipsync) LIPSYNC=1 ;;
    --voice) VOICE=1 ;;
    --all) ALL=1 ;;
    --api-only) API_ONLY=1 ;;
    --yes|-y) YES=1 ;;
    --skip-system) SKIP_SYSTEM=1 ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 1 ;;
  esac
  shift
done

export UV_SYSTEM_CERTS=true   # trust the system certificates (antivirus / proxy)
# Large GPU libraries (hundreds of MB each) need time on slower connections.
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}" UV_HTTP_RETRIES="${UV_HTTP_RETRIES:-5}"
export PATH="$HOME/.local/bin:$PATH"
OS="$(uname -s)"; ARCH="$(uname -m)"

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
die()  { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }
# A command counts only when it is a native one (not a Windows .exe seen from WSL).
has()  { local p; p="$(command -v "$1" 2>/dev/null)" || return 1; case "$p" in /mnt/*) return 1 ;; esac; }
ask()  {
  [ "$YES" = 1 ] && return 1
  [ -r /dev/tty ] || return 1
  local answer; read -r -p "    $1 [y/N] " answer < /dev/tty || return 1
  case "$answer" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; elif has sudo; then sudo "$@"; else die "need root rights for: $*"; fi; }
python_works() { [ -x "$1" ] && "$1" -c "$2" >/dev/null 2>&1; }
ollama_up() { curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; }

install_packages() {
  [ "$SKIP_SYSTEM" = 1 ] && die "missing: $* (install them, or run without --skip-system)"
  if [ "$OS" = Darwin ]; then brew install "$@"; return; fi
  if has apt-get; then as_root apt-get update -y && as_root apt-get install -y "$@"
  elif has dnf; then as_root dnf install -y "$@"
  elif has pacman; then as_root pacman -Sy --noconfirm "$@"
  elif has zypper; then as_root zypper install -y "$@"
  else die "install these with your package manager, then run setup again: $*"; fi
}

# ---------------------------------------------------------------- tools
step "Checking tools"
if [ "$OS" = Darwin ]; then
  if ! has brew; then
    [ "$SKIP_SYSTEM" = 1 ] && die "Homebrew is missing (https://brew.sh)"
    info "installing Homebrew"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty
  fi
  for prefix in /opt/homebrew /usr/local; do
    [ -x "$prefix/bin/brew" ] && eval "$("$prefix/bin/brew" shellenv)"
  done
fi

missing=()
for tool in git curl ffmpeg; do has "$tool" || missing+=("$tool"); done
if [ ${#missing[@]} -gt 0 ]; then info "installing ${missing[*]}"; install_packages "${missing[@]}"; fi
info "git, curl, ffmpeg: found"

if ! has uv; then
  info "installing uv"
  if [ "$OS" = Darwin ]; then install_packages uv; else curl -LsSf https://astral.sh/uv/install.sh | sh; fi
  has uv || die "uv was installed but is not on PATH; open a new terminal and run setup again"
fi
info "uv: found"

export NVM_DIR="$HOME/.nvm"
# nvm does not work under `set -u`, so it runs with that check switched off.
nvm_run() { set +u; "$@"; local status=$?; set -u; return $status; }
# A Node.js installed per user by an earlier run (nvm) is not on PATH in a new shell.
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && { nvm_run . "$NVM_DIR/nvm.sh"; nvm_run nvm use --lts >/dev/null 2>&1 || true; }

node_major() { node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'; }
if ! has node || ! has npm || [ "$(node_major)" -lt 20 ]; then
  [ "$SKIP_SYSTEM" = 1 ] && die "Node.js 20 or newer is needed"
  if [ "$OS" = Darwin ]; then
    install_packages node
  else
    # Per-user Node.js through nvm: no root rights needed.
    info "installing Node.js (LTS) with nvm"
    [ -s "$NVM_DIR/nvm.sh" ] || curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash >/dev/null
    # shellcheck disable=SC1091
    nvm_run . "$NVM_DIR/nvm.sh"
    nvm_run nvm install --lts >/dev/null
    nvm_run nvm use --lts >/dev/null
  fi
fi
has npm || die "npm is missing"
info "node $(node --version): found"

if [ "$API_ONLY" = 0 ] && ! has ollama; then
  [ "$SKIP_SYSTEM" = 1 ] && die "Ollama is missing (https://ollama.com), or use --api-only"
  info "installing Ollama"
  if [ "$OS" = Darwin ]; then install_packages ollama; else curl -fsSL https://ollama.com/install.sh | sh; fi
fi

# ---------------------------------------------------------------- project
step "Getting the project"
ROOT=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -f "$here/ai_service/start_app.py" ] && ROOT="$here"
fi
if [ -z "$ROOT" ]; then
  ROOT="${DIR:-$PWD/Ai-mentor}"
  if [ -f "$ROOT/ai_service/start_app.py" ]; then info "using the copy in $ROOT"
  else git clone --depth 1 "$REPO_URL" "$ROOT"; fi
fi
cd "$ROOT"
info "project: $ROOT"

# ---------------------------------------------------------------- what to install
GPU=0
if [ "$OS" = Linux ] && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  GPU=1
  info "NVIDIA GPU: $(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | head -1)"
fi
[ "$ALL" = 1 ] && { LIPSYNC=1; VOICE=1; }
if [ "$LIPSYNC" = 1 ] && [ "$GPU" = 0 ]; then
  warn "lip sync needs Linux or Windows with an NVIDIA GPU; skipping it"; LIPSYNC=0
fi
if [ "$OS" = Darwin ] && [ "$ARCH" != arm64 ] && [ "$VOICE" = 1 ]; then
  warn "voice cloning needs an Apple silicon Mac (PyTorch dropped Intel Macs); skipping it"; VOICE=0
fi
if [ "$ALL" = 0 ]; then
  if [ "$LIPSYNC" = 0 ] && [ "$GPU" = 1 ] && ask "Install lip sync (moving mouth, about 10 GB)?"; then LIPSYNC=1; fi
  if [ "$VOICE" = 0 ] && ! { [ "$OS" = Darwin ] && [ "$ARCH" != arm64 ]; }; then
    note=""; { [ "$GPU" = 0 ] && [ "$OS" != Darwin ]; } && note="; slow without an NVIDIA GPU"
    ask "Install voice cloning (mentors speak in a recorded voice, about 12 GB$note)?" && VOICE=1
  fi
fi

# ---------------------------------------------------------------- app
step "Installing the AI service (Python)"
APP_PY="ai_service/.venv/bin/python"
[ -x "$APP_PY" ] || uv venv ai_service/.venv --python 3.13
uv pip install --python "$APP_PY" -r ai_service/backend/requirements.txt

step "Installing the website (Node)"
if [ -d frontend/node_modules ]; then info "already installed"; else npm ci --prefix frontend; fi

step "Settings"
[ -f frontend/.env ] || { cp frontend/mentor-preview.env.example frontend/.env; info "created frontend/.env"; }
ENV_FILE="ai_service/backend/.env"
case "$MODEL" in *:*) MODEL_TAG="$MODEL" ;; *) MODEL_TAG="$MODEL:latest" ;; esac
NEW_ENV=0
[ -f "$ENV_FILE" ] || { cp ai_service/backend/.env.example "$ENV_FILE"; NEW_ENV=1; info "created $ENV_FILE"; }
if [ "$API_ONLY" = 1 ] && [ "$NEW_ENV" = 1 ]; then
  sed 's/^AI_PROVIDER=.*/AI_PROVIDER=gemini/' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  info "replies use Gemini: add your key at http://localhost:5173/mentor-settings"
fi
# Keep a default model chosen later in Settings, unless --model was given.
if [ "$NEW_ENV" = 1 ] || [ "$MODEL_SET" = 1 ]; then
  sed "s/^OLLAMA_MODEL=.*/OLLAMA_MODEL=$MODEL_TAG/" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
else
  configured="$(sed -n 's/^OLLAMA_MODEL=//p' "$ENV_FILE" | head -1)"
  [ -n "$configured" ] && MODEL_TAG="$configured"
fi

# ---------------------------------------------------------------- local AI model
if [ "$API_ONLY" = 0 ]; then
  step "Local AI model ($MODEL_TAG)"
  if ! ollama_up; then
    info "starting Ollama"
    nohup ollama serve > "${TMPDIR:-/tmp}/ollama.log" 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do ollama_up && break; sleep 1; done
  fi
  ollama_up || die "Ollama did not start (see ${TMPDIR:-/tmp}/ollama.log)"
  if curl -fsS http://127.0.0.1:11434/api/tags | grep -q "\"name\":\"$MODEL_TAG\""; then info "already downloaded"
  else ollama pull "$MODEL_TAG"; fi
fi

# ---------------------------------------------------------------- lip sync
if [ "$LIPSYNC" = 1 ]; then
  step "Lip sync (MuseTalk)"
  PY="local/lip_sync/.venv/bin/python"
  [ -d local/lip_sync/MuseTalk/musetalk ] || git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git local/lip_sync/MuseTalk
  if python_works "$PY" "import torch, mmcv, mmpose, diffusers, truststore; assert torch.cuda.is_available()"; then
    info "environment already works"
  else
    [ -x "$PY" ] || uv venv local/lip_sync/.venv --python 3.10
    uv pip install --python "$PY" --index-strategy unsafe-best-match -r ai_service/workers/requirements-lipsync.txt
    uv pip install --python "$PY" --no-deps mmpose==1.1.0 json-tricks munkres xtcocotools
  fi
  "$PY" ai_service/workers/download_models.py lipsync
fi

# ---------------------------------------------------------------- voice
if [ "$VOICE" = 1 ]; then
  step "Voice cloning (Chatterbox)"
  PY="local/voice_clone/.venv/bin/python"
  REQUIREMENTS="requirements-voice-cpu.txt"; [ "$GPU" = 1 ] && REQUIREMENTS="requirements-voice.txt"
  if python_works "$PY" "import torch, truststore, perth; from chatterbox.tts_turbo import ChatterboxTurboTTS; assert perth.PerthImplicitWatermarker"; then
    info "environment already works"
  else
    [ -x "$PY" ] || uv venv local/voice_clone/.venv --python 3.10
    uv pip install --python "$PY" --index-strategy unsafe-best-match -r "ai_service/workers/$REQUIREMENTS"
  fi
  "$PY" ai_service/workers/download_models.py voice
fi

# ---------------------------------------------------------------- done
step "Done"
info "Start AI Mentor:   ./run.sh        (in $ROOT)"
info "Then open:         http://localhost:5173/mentor"
info "Keys and models:   http://localhost:5173/mentor-settings"
