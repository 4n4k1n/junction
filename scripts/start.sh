#!/bin/bash
# Starts the LearnLens backend and the Mac deploy agent together. Ctrl-C stops both.
#
#   ./scripts/start.sh            real run: Claude (needs ANTHROPIC_API_KEY) and the real board
#   ./scripts/start.sh --demo     saved AI answers + prevalidated sketch; still flashes the real board
#   ./scripts/start.sh --mock     deploy agent simulates the upload (no board needed)
#   ./scripts/start.sh --demo --mock   everything offline, nothing real: for rehearsing the flow
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${LEARNLENS_PORT:-8000}"
DEMO=0; MOCK=0
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    --mock) MOCK=1 ;;
    *) echo "unknown option: $arg (use --demo and/or --mock)"; exit 2 ;;
  esac
done

PY="$ROOT/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "backend venv missing. Run: cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "LearnLens"
if [ "$DEMO" = 1 ]; then
  export LEARNLENS_DEMO_MODE=1
  export LEARNLENS_TUTOR=mock   # no model client at all in demo mode
  echo "  AI:     DEMO MODE (saved answers, prevalidated sketch, no model calls)"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "  AI:     Claude (ANTHROPIC_API_KEY is set)"
else
  echo "  AI:     OFFLINE: no ANTHROPIC_API_KEY, so no vision and the template sketch. Use --demo for the saved answers."
fi
if [ "$MOCK" = 1 ]; then
  echo "  Board:  SIMULATED upload (--mock): no board will be flashed"
else
  echo "  Board:  real upload to an Arduino UNO Q on USB, after approval"
fi
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
[ -z "$IP" ] && IP="THIS-MAC-IP"
echo "  Phone:  set the backend URL in the app to http://$IP:$PORT"
echo

cd "$ROOT/backend" || exit 1
"$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --log-level warning &
BACKEND=$!
trap 'kill $BACKEND ${AGENT:-} 2>/dev/null; wait 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 40); do
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && break
  if ! kill -0 $BACKEND 2>/dev/null; then echo "backend failed to start (is port $PORT in use?)"; exit 1; fi
  sleep 0.25
done
if ! curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then echo "backend did not answer on port $PORT"; exit 1; fi
echo "backend ready: http://localhost:$PORT  (API docs: /docs)"

AGENT_ARGS=(--backend "http://localhost:$PORT")
[ "$MOCK" = 1 ] && AGENT_ARGS+=(--mock)
python3 "$ROOT/agent/learnlens_agent.py" "${AGENT_ARGS[@]}" &
AGENT=$!
wait $AGENT
