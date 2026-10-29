#!/usr/bin/env bash
# Show the direct daemon, web, Tailscale Serve, and token status.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

show_token=0
if [ "${1:-}" = "--token" ]; then
  show_token=1
fi

echo "=== config ==="
echo "local:  http://127.0.0.1:$VICOA_DIRECT_PORT"
echo "web:    $VICOA_DIRECT_WEB_DIR (port $VICOA_DIRECT_WEB_PORT)"
echo "store:  $VICOA_DIRECT_STORE"
echo "token:  $VICOA_DIRECT_TOKEN_PATH"
if [ -n "$VICOA_DIRECT_PUBLIC_URL" ]; then
  echo "public: $VICOA_DIRECT_PUBLIC_URL"
fi

echo
echo "=== daemon ==="
if [ -f "$VICOA_DIRECT_PID_FILE" ]; then
  daemon_pid="$(cat "$VICOA_DIRECT_PID_FILE")"
  if kill -0 "$daemon_pid" 2>/dev/null; then
    echo "pid $daemon_pid running"
    daemon_public="$(
      ps -p "$daemon_pid" -o command= 2>/dev/null |
        sed -n 's/.*--public-url \([^ ]*\).*/\1/p'
    )"
    if [ -n "$daemon_public" ]; then
      echo "started with public: $daemon_public"
      if [ -n "$VICOA_DIRECT_PUBLIC_URL" ] &&
        [ "$daemon_public" != "$VICOA_DIRECT_PUBLIC_URL" ]; then
        echo "WARNING: daemon public origin differs from the current Tailscale DNS."
        echo "         Restart it with: $SCRIPT_DIR/direct-down.sh && $SCRIPT_DIR/direct-up.sh"
      fi
    fi
  else
    echo "pid file exists but process is gone"
  fi
else
  echo "no pid file ($VICOA_DIRECT_PID_FILE)"
fi
if lsof -nP -iTCP:"$VICOA_DIRECT_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  lsof -nP -iTCP:"$VICOA_DIRECT_PORT" -sTCP:LISTEN | tail -n +2
else
  echo "port $VICOA_DIRECT_PORT is not listening"
fi

echo
echo "=== health ==="
echo -n "local:  "
curl -s "http://127.0.0.1:$VICOA_DIRECT_PORT/healthz" || true
echo
if [ -n "$VICOA_DIRECT_PUBLIC_URL" ]; then
  echo -n "public:  "
  curl -s "$VICOA_DIRECT_PUBLIC_URL/healthz" || true
  echo
fi

echo
echo "=== Tailscale Serve ==="
tailscale serve status 2>&1 || true

echo
echo "=== Tailscale devices ==="
tailscale status 2>&1 | head -6 || true

if [ "$show_token" = "1" ] && [ -f "$VICOA_DIRECT_TOKEN_PATH" ] && [ -n "$VICOA_DIRECT_PUBLIC_URL" ]; then
  echo
  echo "=== pair link ==="
  pair_token="$(cat "$VICOA_DIRECT_TOKEN_PATH")"
  echo "${VICOA_DIRECT_PUBLIC_URL%/}/#token=$pair_token"
fi

echo
echo "=== recent daemon log ==="
if [ -f "$VICOA_DIRECT_LOG_FILE" ]; then
  tail -n 12 "$VICOA_DIRECT_LOG_FILE"
else
  echo "no log at $VICOA_DIRECT_LOG_FILE"
fi
