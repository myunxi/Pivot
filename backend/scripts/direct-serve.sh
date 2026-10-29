#!/usr/bin/env bash
# Manage the Tailscale Serve HTTPS front door for direct mode.
#
#   ./direct-serve.sh on      expose https://<machine>.<tailnet>.ts.net -> local daemon
#   ./direct-serve.sh off     remove all Tailscale Serve config for this machine
#   ./direct-serve.sh status  show the current Serve config
#
# Serve is tailnet-only. Never use `tailscale funnel`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale CLI not found" >&2
  exit 1
fi

case "${1:-status}" in
  on)
    echo "configuring Tailscale Serve https://<machine>.<tailnet>.ts.net -> http://127.0.0.1:$VICOA_DIRECT_PORT"
    tailscale serve --bg --https=443 "http://127.0.0.1:$VICOA_DIRECT_PORT"
    ;;
  off)
    echo "removing all Tailscale Serve config for this machine"
    echo "(this affects every Serve route on this node, not just direct mode)"
    tailscale serve reset
    ;;
  status)
    tailscale serve status
    ;;
  *)
    echo "usage: $0 {on|off|status}" >&2
    exit 2
    ;;
esac
