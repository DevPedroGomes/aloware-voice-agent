#!/usr/bin/env bash
# One terminal for everything: admin console in the background, voice call in the foreground.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
LOG="data/runtime/api.log"

if [ ! -f .env ]; then
  echo "Missing .env. Run 'lk cloud auth' and then 'make env' first." >&2
  exit 1
fi
[ -d agent/.venv ] && [ -d admin/node_modules ] || { echo "Run 'make setup' first." >&2; exit 1; }
[ -f admin/dist/index.html ] || (cd admin && npm run build >/dev/null)

mkdir -p data/runtime
API_PID=""
if curl -sf "http://localhost:$PORT/api/config" >/dev/null 2>&1; then
  echo "Admin API already running on :$PORT, reusing it."
else
  (cd agent && exec .venv/bin/uvicorn voice_agent.api:app --port "$PORT" >"../$LOG" 2>&1) &
  API_PID=$!
  trap '[ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null || true; cleanup_jobs 2>/dev/null || true' EXIT TERM
  for _ in $(seq 1 50); do
    curl -sf "http://localhost:$PORT/api/config" >/dev/null 2>&1 && break
    kill -0 "$API_PID" 2>/dev/null || { echo "Admin API failed to start, see $LOG" >&2; exit 1; }
    sleep 0.2
  done
fi

cd agent
# `lk` picks its own default project unless LIVEKIT_* are in the environment.
set -a; . ../.env; set +a
# When a console session ends on its own, `lk` can leave the Python job behind.
cleanup_jobs() { pkill -f "$PWD/.venv/bin/python3 -m livekit.agents console" 2>/dev/null || true; }

if [ "${1:-}" = "web" ]; then
  cat <<MSG

  Admin console:  http://localhost:$PORT   (API log: $LOG)
  Open the "Agent console" link printed below and press Start to call Maya in the browser.
  This mode runs in a real room, so the office ambience and typing sounds play.

MSG
  lk agent dev agent.py
  exit 0
fi

cat <<MSG

  Admin console:  http://localhost:$PORT   (API log: $LOG)
  Maya calls the lead selected in the admin and speaks first.
  Ctrl+C ends the current call. Then Enter places a new one, Ctrl+C again quits.

MSG

while true; do
  trap ':' INT  # Ctrl+C reaches the console and ends the call, not this script
  lk agent console agent.py "$@" || true
  cleanup_jobs
  trap 'echo; exit 0' INT
  echo
  read -r -p "Call ended. Enter = new call with the latest admin config, Ctrl+C = quit. " _
done
