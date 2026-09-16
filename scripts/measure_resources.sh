#!/usr/bin/env bash
# Samples RSS memory and CPU usage of a running `watchtower --demo` process
# for a fixed duration, so resource usage is measured rather than assumed.
#
# Usage: ./scripts/measure_resources.sh [duration_seconds] [port] [scenario]
#   scenario: idle (default) | listen | scan

set -euo pipefail

DURATION="${1:-20}"
PORT="${2:-8098}"
SCENARIO="${3:-idle}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
PYTHON="${VENV_PY:-python3}"
[ -x "$PYTHON" ] || PYTHON="python3"

echo "Starting watchtower --demo on port $PORT for a ${DURATION}s sample (scenario=$SCENARIO)..."
cd "$REPO_ROOT"
DATA_DIR="$(mktemp -d)"
trap 'rm -rf "$DATA_DIR"' EXIT
"$PYTHON" -m watchtower --demo --port "$PORT" --data-dir "$DATA_DIR" >/tmp/watchtower_measure.log 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true; rm -rf "$DATA_DIR"' EXIT

sleep 1.5
if ! kill -0 "$PID" 2>/dev/null; then
  echo "Process failed to start; see /tmp/watchtower_measure.log"
  cat /tmp/watchtower_measure.log
  exit 1
fi

case "$SCENARIO" in
  listen)
    curl -s -X POST "http://127.0.0.1:$PORT/api/sdr/listen/start" \
      -H "Content-Type: application/json" \
      -d '{"frequency_mhz": 101.5, "mode": "wfm"}' >/dev/null
    ;;
  scan)
    curl -s -X POST "http://127.0.0.1:$PORT/api/sdr/scan/start" \
      -H "Content-Type: application/json" \
      -d '{"start_mhz": 88, "end_mhz": 108, "bin_khz": 25}' >/dev/null
    ;;
esac

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
echo "Watchtower process RSS (kB) at end of sample:"
ps -p "$PID" -o rss= 2>/dev/null || echo "(process already exited)"

echo
echo "Child processes (ffmpeg/rtl_power tone/sweep, if any) and combined RSS:"
CHILD_PIDS="$(pgrep -P "$PID" 2>/dev/null || true)"
TOTAL_RSS=0
for p in "$PID" $CHILD_PIDS; do
  RSS="$(ps -p "$p" -o rss= 2>/dev/null | tr -d ' ')"
  CMD="$(ps -p "$p" -o comm= 2>/dev/null || echo '?')"
  if [ -n "$RSS" ]; then
    echo "  pid=$p ($CMD): ${RSS} kB"
    TOTAL_RSS=$((TOTAL_RSS + RSS))
  fi
done
echo "Combined RSS: ${TOTAL_RSS} kB"

kill "$PID" 2>/dev/null || true
