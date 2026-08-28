#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/assist.pid"
LOG_FILE="$SCRIPT_DIR/assist.log"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "ASSIST is already running (PID $OLD_PID). Use ./stop.sh first."
        exit 1
    fi
    echo "Stale PID file (PID $OLD_PID gone). Cleaning up."
    rm -f "$PID_FILE"
fi

cd "$SCRIPT_DIR"
if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
    ./venv/bin/pip install -q --upgrade pip
    ./venv/bin/pip install -q -r requirements.txt
fi
source venv/bin/activate

PORT="${ASSIST_PORT:-8787}"
echo "Starting ASSIST host on :$PORT ..."
nohup python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "ASSIST started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
echo "Open http://localhost:$PORT/ on this Mac, or http://<mac-lan-ip>:$PORT/ on your phone."
