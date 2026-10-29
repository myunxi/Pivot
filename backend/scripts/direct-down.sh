#!/usr/bin/env bash
# Stop the script-managed direct daemon. The daemon also stops its supervised
# Next server and every headless runner it spawned.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

if [ ! -f "$VICOA_DIRECT_PID_FILE" ]; then
  echo "no script-managed daemon (missing $VICOA_DIRECT_PID_FILE)"
  if lsof -nP -iTCP:"$VICOA_DIRECT_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $VICOA_DIRECT_PORT is in use by:"
    lsof -nP -iTCP:"$VICOA_DIRECT_PORT" -sTCP:LISTEN
    echo "This instance was not started by direct-up.sh; stop it by its PID."
  fi
  exit 0
fi

daemon_pid="$(cat "$VICOA_DIRECT_PID_FILE")"
if ! kill -0 "$daemon_pid" 2>/dev/null; then
  echo "daemon pid $daemon_pid is not running; removing stale pid file"
  rm -f "$VICOA_DIRECT_PID_FILE"
  exit 0
fi

echo "stopping direct daemon pid $daemon_pid..."
kill -TERM "$daemon_pid" 2>/dev/null || true

for _ in $(seq 1 20); do
  if ! kill -0 "$daemon_pid" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

if kill -0 "$daemon_pid" 2>/dev/null; then
  echo "daemon did not exit in 10s; sending SIGKILL"
  kill -KILL "$daemon_pid" 2>/dev/null || true
  sleep 0.5
fi

rm -f "$VICOA_DIRECT_PID_FILE"
echo "direct daemon stopped"
echo "Tailscale Serve config is unchanged; turn it off with:"
echo "  $SCRIPT_DIR/direct-serve.sh off"
