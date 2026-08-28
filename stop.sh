#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/assist.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Stopped ASSIST (PID $PID)."
    else
        echo "PID $PID not running."
    fi
    rm -f "$PID_FILE"
else
    echo "No PID file. Trying pkill..."
    pkill -f "uvicorn backend.main:app" && echo "Stopped via pkill." || echo "Nothing to stop."
fi
