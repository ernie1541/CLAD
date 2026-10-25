#!/usr/bin/env bash
# One command: ./start.sh   → sets up Python, installs deps once, opens Traveler in your browser.
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8765}"

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python environment (one time)…"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
  else
    PY=""
    for c in python3.12 python3.11 python3.10 python3; do
      if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
    if [ -z "$PY" ]; then echo "Install Python 3.10–3.12 (or uv: https://docs.astral.sh/uv/) and run again."; exit 1; fi
    "$PY" -m venv .venv
  fi
fi

if [ ! -f .venv/.deps-ok ] || [ requirements.txt -nt .venv/.deps-ok ]; then
  echo "Installing dependencies (CadQuery is large; first run takes a few minutes)…"
  if command -v uv >/dev/null 2>&1; then
    VIRTUAL_ENV="$PWD/.venv" uv pip install -r requirements.txt
  else
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q -r requirements.txt
  fi
  touch .venv/.deps-ok
fi

if [ -f .env ]; then set -a; . ./.env; set +a; fi

if command -v ollama >/dev/null 2>&1; then
  for m in "${ENGINEER_MODEL:-gemma4:26b}" "${SUB_ENGINEER_MODEL:-gemma4:e4b}"; do
    ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$m" || echo "Note: $m isn't pulled yet. Run: ollama pull $m"
  done
fi

URL="http://127.0.0.1:$PORT"
echo "Traveler → $URL   (Ctrl+C to stop)"
( sleep 2; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) >/dev/null 2>&1 &
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
