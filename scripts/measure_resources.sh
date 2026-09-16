#!/usr/bin/env bash
# Samples RSS memory and CPU usage of a running `watchtower --demo` process
# for a fixed duration, so resource usage is measured rather than assumed.
#
# Usage: ./scripts/measure_resources.sh [duration_seconds] [port]

set -euo pipefail

DURATION="${1:-20}"
PORT="${2:-8098}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
PYTHON="${VENV_PY:-python3}"
[ -x "$PYTHON" ] || PYTHON="python3"

echo "Starting watchtower --demo on port $PORT for a ${DURATION}s sample..."
cd "$REPO_ROOT"
"$PYTHON" -m watchtower --demo --port "$PORT" >/tmp/watchtower_measure.log 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT

sleep 1.5
if ! kill -0 "$PID" 2>/dev/null; then
  echo "Process failed to start; see /tmp/watchtower_measure.log"
  cat /tmp/watchtower_measure.log
  exit 1
fi

echo "PID: $PID"
echo "time_s  rss_kb  %cpu"
END=$((SECONDS + DURATION))
while [ "$SECONDS" -lt "$END" ]; do
  if ! ps -p "$PID" -o etimes=,rss=,pcpu= 2>/dev/null; then
    echo "process exited early"
    break
  fi
  sleep 2
done

echo
echo "Peak RSS (kB) across the sample window:"
ps -p "$PID" -o rss= 2>/dev/null || echo "(process already exited)"

kill "$PID" 2>/dev/null || true
