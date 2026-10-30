#!/usr/bin/env bash
# Manage launchd auto-start for direct mode.
#
#   ./direct-autostart.sh on      install a LaunchAgent that runs direct-up.sh at login
#   ./direct-autostart.sh off     remove the LaunchAgent
#   ./direct-autostart.sh status  show the LaunchAgent state
#
# The agent runs direct-up.sh once at login (RunAtLoad) with KeepAlive off:
# direct-up.sh exits once the daemon is ready, so a KeepAlive job would respawn
# it in a tight loop. The daemon already supervises the Next server, so the web
# front end restarts on its own; supervising the daemon process itself is a
# later enhancement.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

LABEL="com.pivot.direct"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LOG_FILE="$VICOA_DIRECT_LOG_DIR/autostart.log"

# launchd's default PATH is only /usr/bin:/bin:/usr/sbin:/sbin, which misses
# /usr/local/bin (tailscale) and /opt/homebrew/bin. direct-env.sh uses
# `tailscale status` to auto-detect the public URL, which feeds the WS
# allow-list for phone access, so the agent must see tailscale.
AGENT_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

case "${1:-status}" in
  on)
    mkdir -p "$VICOA_DIRECT_LOG_DIR" "$HOME/Library/LaunchAgents"
    # Idempotent: drop any existing install first.
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SCRIPT_DIR/direct-up.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>WorkingDirectory</key><string>$SCRIPT_DIR</string>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$AGENT_PATH</string>
  </dict>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
EOF
    chmod 644 "$PLIST"
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed LaunchAgent $LABEL"
    echo "  plist:  $PLIST"
    echo "  log:    $LOG_FILE"
    echo "  status: $SCRIPT_DIR/direct-autostart.sh status"
    ;;
  off)
    if [ -f "$PLIST" ] || launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    fi
    rm -f "$PLIST"
    echo "removed LaunchAgent $LABEL"
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      echo "LaunchAgent $LABEL: loaded"
      launchctl print "$DOMAIN/$LABEL" | grep -E '^[[:space:]]*(state|pid|last exit code) = ' || true
    else
      echo "LaunchAgent $LABEL: not loaded"
      if [ -f "$PLIST" ]; then
        echo "  (plist present but not loaded: $PLIST)"
      fi
    fi
    ;;
  *)
    echo "usage: $0 {on|off|status}" >&2
    exit 2
    ;;
esac
