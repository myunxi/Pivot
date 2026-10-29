#!/usr/bin/env bash
# Start the direct-mode daemon (and optionally its supervised Next server).
set -euo pipefail
# Direct-mode data includes the token, prompts, and attachments. Keep every
# file this script creates private.
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

if [ ! -x "$VICOA_DIRECT_PYTHON" ]; then
  echo "python not found: $VICOA_DIRECT_PYTHON" >&2
  echo "Create the venv and install backend deps first." >&2
  exit 1
fi

if [ -f "$VICOA_DIRECT_PID_FILE" ]; then
  existing_pid="$(cat "$VICOA_DIRECT_PID_FILE" 2>/dev/null || true)"
  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "direct daemon already running: pid $existing_pid"
    echo "status: $SCRIPT_DIR/direct-status.sh --token"
    exit 0
  fi
  rm -f "$VICOA_DIRECT_PID_FILE"
fi

if lsof -nP -iTCP:"$VICOA_DIRECT_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $VICOA_DIRECT_PORT is already in use:" >&2
  lsof -nP -iTCP:"$VICOA_DIRECT_PORT" -sTCP:LISTEN >&2
  echo "Choose another VICOA_DIRECT_PORT or stop that process." >&2
  exit 1
fi

if [ "$VICOA_DIRECT_WITH_WEB" = "1" ]; then
  if [ ! -f "$VICOA_DIRECT_WEB_DIR/.next/BUILD_ID" ] &&
    [ ! -f "$VICOA_DIRECT_WEB_DIR/.next/standalone/server.js" ]; then
    echo "no direct web build in $VICOA_DIRECT_WEB_DIR" >&2
    echo "run: $SCRIPT_DIR/direct-build.sh" >&2
    exit 1
  fi
fi

data_dir="$(dirname "$VICOA_DIRECT_STORE")"
token_dir="$(dirname "$VICOA_DIRECT_TOKEN_PATH")"
mkdir -p "$data_dir" "$token_dir" "$VICOA_DIRECT_LOG_DIR"
chmod 700 "$data_dir" "$token_dir" "$VICOA_DIRECT_LOG_DIR" 2>/dev/null || true

# Harden data left by older starts (directories 0700, files 0600).
touch "$VICOA_DIRECT_LOG_FILE"
chmod 600 "$VICOA_DIRECT_LOG_FILE" "$VICOA_DIRECT_LOG_DIR/next.log" 2>/dev/null || true
chmod 600 "$VICOA_DIRECT_TOKEN_PATH" "$VICOA_DIRECT_STORE" \
  "$VICOA_DIRECT_STORE-wal" "$VICOA_DIRECT_STORE-shm" 2>/dev/null || true
if [ -d "$data_dir/attachments" ]; then
  find "$data_dir/attachments" -type d -exec chmod 700 {} + 2>/dev/null || true
  find "$data_dir/attachments" -type f -exec chmod 600 {} + 2>/dev/null || true
fi

args=(--port "$VICOA_DIRECT_PORT" --store "$VICOA_DIRECT_STORE" --token-path "$VICOA_DIRECT_TOKEN_PATH")
if [ -n "$VICOA_DIRECT_PUBLIC_URL" ]; then
  args+=(--public-url "$VICOA_DIRECT_PUBLIC_URL")
fi
if [ "$VICOA_DIRECT_WITH_WEB" = "1" ]; then
  args+=(--with-web --web-dir "$VICOA_DIRECT_WEB_DIR" --web-port "$VICOA_DIRECT_WEB_PORT")
fi
if [ "$VICOA_DIRECT_SHOW_TOKEN" = "1" ]; then
  args+=(--show-token)
fi

nohup "$VICOA_DIRECT_PYTHON" -m vicoa.cli direct "${args[@]}" >"$VICOA_DIRECT_LOG_FILE" 2>&1 &
daemon_pid=$!
echo "$daemon_pid" >"$VICOA_DIRECT_PID_FILE"

ready=0
for _ in $(seq 1 60); do
  if ! kill -0 "$daemon_pid" 2>/dev/null; then
    break
  fi
  if curl -sf "http://127.0.0.1:$VICOA_DIRECT_PORT/healthz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done

if [ "$ready" != "1" ]; then
  echo "direct daemon failed to become ready; log:" >&2
  tail -n 30 "$VICOA_DIRECT_LOG_FILE" >&2 || true
  kill -TERM "$daemon_pid" 2>/dev/null || true
  rm -f "$VICOA_DIRECT_PID_FILE"
  exit 1
fi

echo "direct daemon started"
echo "  pid:   $daemon_pid"
echo "  local: http://127.0.0.1:$VICOA_DIRECT_PORT"
echo "  store: $VICOA_DIRECT_STORE"
echo "  token: $VICOA_DIRECT_TOKEN_PATH"
echo "  log:   $VICOA_DIRECT_LOG_FILE"
if [ -n "$VICOA_DIRECT_PUBLIC_URL" ]; then
  echo "  public: $VICOA_DIRECT_PUBLIC_URL"
  if [ "$VICOA_DIRECT_SHOW_TOKEN" = "1" ] && [ -f "$VICOA_DIRECT_TOKEN_PATH" ]; then
    pair_token="$(cat "$VICOA_DIRECT_TOKEN_PATH")"
    echo "  pair:  ${VICOA_DIRECT_PUBLIC_URL%/}/#token=$pair_token"
  fi
  echo
  echo "Tailscale Serve (if not already configured):"
  echo "  $SCRIPT_DIR/direct-serve.sh on"
fi
echo
echo "status: $SCRIPT_DIR/direct-status.sh --token"
echo "stop:   $SCRIPT_DIR/direct-down.sh"
