#!/usr/bin/env bash
# Show Pivot orchestration status: process, port, health, and recent log.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

echo "=== config ==="
echo "pivot:   http://$PIVOT_SERVE_HOST:$PIVOT_SERVE_PORT"
echo "daemon:  $PIVOT_DAEMON_URL"
echo "data:    $PIVOT_DATA_DIR"
echo "rules:   $PIVOT_RULES_PATH"
echo "store:   $PIVOT_STORE"
echo "token:   $PIVOT_TOKEN_PATH"

echo
echo "=== process ==="
if [ -f "$PIVOT_PID_FILE" ]; then
  serve_pid="$(cat "$PIVOT_PID_FILE")"
  if kill -0 "$serve_pid" 2>/dev/null; then
    echo "pid $serve_pid running"
    ps -p "$serve_pid" -o pid=,ppid=,etime=,command=
  else
    echo "pid file exists but process is gone"
  fi
else
  echo "no pid file ($PIVOT_PID_FILE)"
fi
if lsof -nP -iTCP:"$PIVOT_SERVE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  lsof -nP -iTCP:"$PIVOT_SERVE_PORT" -sTCP:LISTEN | tail -n +2
else
  echo "port $PIVOT_SERVE_PORT is not listening"
fi

echo
echo "=== health ==="
echo -n "healthz: "
curl -s "http://127.0.0.1:$PIVOT_SERVE_PORT/healthz" || true
echo
if [ -f "$PIVOT_TOKEN_PATH" ]; then
  pivot_token="$(cat "$PIVOT_TOKEN_PATH")"
  echo -n "rules:   "
  curl -s -H "Authorization: Bearer $pivot_token" \
    "http://127.0.0.1:$PIVOT_SERVE_PORT/v1/rules" | head -c 400 || true
  echo
  echo -n "tickets: "
  curl -s -H "Authorization: Bearer $pivot_token" \
    "http://127.0.0.1:$PIVOT_SERVE_PORT/v1/tickets?limit=3" | head -c 400 || true
  echo
else
  echo "token file missing; skipping authenticated checks"
fi

echo
echo "=== recent log ==="
if [ -f "$PIVOT_LOG_FILE" ]; then
  tail -n 12 "$PIVOT_LOG_FILE"
else
  echo "no log at $PIVOT_LOG_FILE"
fi
