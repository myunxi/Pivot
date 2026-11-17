#!/usr/bin/env bash
# Stop the script-managed Pivot orchestration process.
#
# Only the exact PID recorded by pivot-up.sh is touched, and only after its
# command line is verified to be `pivot serve`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

if [ ! -f "$PIVOT_PID_FILE" ]; then
  echo "no script-managed pivot serve (missing $PIVOT_PID_FILE)"
  if lsof -nP -iTCP:"$PIVOT_SERVE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $PIVOT_SERVE_PORT is in use by:"
    lsof -nP -iTCP:"$PIVOT_SERVE_PORT" -sTCP:LISTEN
    echo "This instance was not started by pivot-up.sh; stop it by its PID."
  fi
  exit 0
fi

serve_pid="$(cat "$PIVOT_PID_FILE")"
if ! kill -0 "$serve_pid" 2>/dev/null; then
  echo "pivot serve pid $serve_pid is not running; removing stale pid file"
  rm -f "$PIVOT_PID_FILE"
  exit 0
fi

command_line="$(ps -p "$serve_pid" -o command= 2>/dev/null || true)"
if [[ "$command_line" != *"pivot"* || "$command_line" != *"serve"* ]]; then
  echo "pid $serve_pid does not look like pivot serve; refusing to kill it:" >&2
  ps -p "$serve_pid" -o pid=,command= >&2 || true
  exit 1
fi

echo "stopping pivot serve pid $serve_pid..."
kill -TERM "$serve_pid" 2>/dev/null || true

for _ in $(seq 1 20); do
  if ! kill -0 "$serve_pid" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

if kill -0 "$serve_pid" 2>/dev/null; then
  echo "pivot serve did not exit in 10s; sending SIGKILL"
  kill -KILL "$serve_pid" 2>/dev/null || true
  sleep 0.5
fi

rm -f "$PIVOT_PID_FILE"
echo "pivot serve stopped"
