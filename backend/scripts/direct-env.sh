#!/usr/bin/env bash
# Shared configuration for the direct-mode helper scripts.
#
# Override any value from the environment, e.g.:
#   VICOA_DIRECT_PORT=46040 VICOA_DIRECT_WEB_PORT=3002 ./direct-up.sh
#
# Long-term defaults use ~/.vicoa/direct. The temporary validation instance
# can be reproduced with:
#   VICOA_DIRECT_PORT=46040 VICOA_DIRECT_WEB_PORT=3002 \
#   VICOA_DIRECT_STORE=/tmp/vicoa-ts/store.db \
#   VICOA_DIRECT_TOKEN_PATH=/tmp/vicoa-ts/token ./direct-up.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

: "${VICOA_DIRECT_PORT:=8765}"
: "${VICOA_DIRECT_WEB_PORT:=3000}"
: "${VICOA_DIRECT_WEB_DIR:=$REPO_ROOT/apps/web}"
: "${VICOA_DIRECT_PYTHON:=$REPO_ROOT/backend/.venv/bin/python}"
: "${VICOA_DIRECT_STORE:=$HOME/.vicoa/direct/store.db}"
: "${VICOA_DIRECT_TOKEN_PATH:=$HOME/.vicoa/direct/token}"
: "${VICOA_DIRECT_LOG_DIR:=$HOME/.vicoa/direct/logs}"
: "${VICOA_DIRECT_PID_FILE:=$HOME/.vicoa/direct/daemon.pid}"
: "${VICOA_DIRECT_WITH_WEB:=1}"
: "${VICOA_DIRECT_SHOW_TOKEN:=0}"
: "${VICOA_DIRECT_PUBLIC_URL:=}"

if [ -z "$VICOA_DIRECT_PUBLIC_URL" ] && command -v tailscale >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    detected_dns="$(
      tailscale status --json 2>/dev/null |
        python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
    print((data.get("Self") or {}).get("DNSName", "").rstrip("."))
except Exception:
    pass' 2>/dev/null || true
    )"
    if [ -n "$detected_dns" ]; then
      VICOA_DIRECT_PUBLIC_URL="https://$detected_dns"
    fi
  fi
fi

export VICOA_DIRECT_PORT
export VICOA_DIRECT_WEB_PORT
export VICOA_DIRECT_WEB_DIR
export VICOA_DIRECT_PYTHON
export VICOA_DIRECT_STORE
export VICOA_DIRECT_TOKEN_PATH
export VICOA_DIRECT_LOG_DIR
export VICOA_DIRECT_PID_FILE
export VICOA_DIRECT_WITH_WEB
export VICOA_DIRECT_SHOW_TOKEN
export VICOA_DIRECT_PUBLIC_URL
export VICOA_DIRECT_LOG_FILE="$VICOA_DIRECT_LOG_DIR/daemon.log"
