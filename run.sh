#!/usr/bin/env bash
# Run Ask Nitin locally with one command:   ./run.sh
# Options:  PORT=8001 ./run.sh    RELOAD=1 ./run.sh (auto-reload on code changes)
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
PY="${PYTHON:-python3.12}"

if [ ! -x .venv/bin/python ]; then
  command -v "$PY" >/dev/null || { echo "Python 3.12 is required (or set PYTHON=/path/to/python3.12)"; exit 1; }
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Add your ANTHROPIC_API_KEY to it, then run ./run.sh again."
  exit 1
fi

echo "Ask Nitin → http://localhost:$PORT"
exec .venv/bin/uvicorn app:app --port "$PORT" --no-proxy-headers ${RELOAD:+--reload}
