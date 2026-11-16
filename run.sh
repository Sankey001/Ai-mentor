#!/usr/bin/env bash
# Start AI Mentor (after setup.sh): Ollama if needed, then the app. Stop with Ctrl+C.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
# Node.js installed per user by setup.sh (nvm), when there is no system one.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  set +u  # nvm does not work under `set -u`
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh" >/dev/null && nvm use --lts >/dev/null 2>&1 || true
  set -u
fi

if ! curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  if command -v ollama >/dev/null 2>&1; then
    echo "Starting Ollama..."
    nohup ollama serve > "${TMPDIR:-/tmp}/ollama.log" 2>&1 &
    sleep 3
  else
    echo "Ollama is not installed; local AI replies are off (run setup.sh)."
  fi
fi

exec ai_service/.venv/bin/python ai_service/start_app.py "$@"
