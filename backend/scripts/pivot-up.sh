#!/usr/bin/env bash
# Start the Pivot orchestration layer (dispatch API + cron pump) on loopback.
#
# It reuses the direct-mode nonce by default, so direct-up.sh must have run
# first. Stop it with pivot-down.sh; do not use pkill or port-range kills.
set -euo pipefail
# Rules and ticket input files are private; keep everything this script
# creates readable only by the current user.
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

if [ ! -x "$VICOA_DIRECT_PYTHON" ]; then
  echo "python not found: $VICOA_DIRECT_PYTHON" >&2
  echo "Create the venv and install backend deps first." >&2
  exit 1
fi

if [ ! -f "$PIVOT_TOKEN_PATH" ]; then
  echo "token not found: $PIVOT_TOKEN_PATH" >&2
  echo "Start direct mode first: $SCRIPT_DIR/direct-up.sh" >&2
  exit 1
fi

if [ -f "$PIVOT_PID_FILE" ]; then
  existing_pid="$(cat "$PIVOT_PID_FILE" 2>/dev/null || true)"
  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "pivot serve already running: pid $existing_pid"
    echo "status: $SCRIPT_DIR/pivot-status.sh"
    exit 0
  fi
  rm -f "$PIVOT_PID_FILE"
fi

if lsof -nP -iTCP:"$PIVOT_SERVE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PIVOT_SERVE_PORT is already in use:" >&2
  lsof -nP -iTCP:"$PIVOT_SERVE_PORT" -sTCP:LISTEN >&2
  echo "Choose another PIVOT_SERVE_PORT or stop that process." >&2
  exit 1
fi

mkdir -p "$PIVOT_DATA_DIR" "$PIVOT_LOG_DIR"
chmod 700 "$PIVOT_DATA_DIR" "$PIVOT_LOG_DIR" 2>/dev/null || true
touch "$PIVOT_LOG_FILE"
chmod 600 "$PIVOT_LOG_FILE" 2>/dev/null || true

# Same detach trick as direct-up.sh: survive the launching terminal's process
# group, then stop only via the pid file.
detach_python='import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execv(sys.argv[1], sys.argv[1:])'

nohup "$VICOA_DIRECT_PYTHON" -c "$detach_python" \
  "$VICOA_DIRECT_PYTHON" -m pivot serve \
  >"$PIVOT_LOG_FILE" 2>&1 &
serve_pid=$!
echo "$serve_pid" >"$PIVOT_PID_FILE"

ready=0
for _ in $(seq 1 60); do
  if ! kill -0 "$serve_pid" 2>/dev/null; then
    break
  fi
  if curl -sf "http://127.0.0.1:$PIVOT_SERVE_PORT/healthz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done

if [ "$ready" != "1" ]; then
  echo "pivot serve failed to become ready; log:" >&2
  tail -n 30 "$PIVOT_LOG_FILE" >&2 || true
  kill -TERM "$serve_pid" 2>/dev/null || true
  rm -f "$PIVOT_PID_FILE"
  exit 1
fi

echo "pivot serve started"
echo "  pid:    $serve_pid"
echo "  local:  http://$PIVOT_SERVE_HOST:$PIVOT_SERVE_PORT"
echo "  daemon: $PIVOT_DAEMON_URL"
echo "  data:   $PIVOT_DATA_DIR"
echo "  rules:  $PIVOT_RULES_PATH"
echo "  log:    $PIVOT_LOG_FILE"
echo
echo "status: $SCRIPT_DIR/pivot-status.sh"
echo "stop:   $SCRIPT_DIR/pivot-down.sh"
