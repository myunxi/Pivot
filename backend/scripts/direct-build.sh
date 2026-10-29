#!/usr/bin/env bash
# Build apps/web for direct mode (same-origin daemon + Next).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=direct-env.sh
source "$SCRIPT_DIR/direct-env.sh"

if [ ! -d "$VICOA_DIRECT_WEB_DIR" ]; then
  echo "web dir not found: $VICOA_DIRECT_WEB_DIR" >&2
  exit 1
fi

cd "$VICOA_DIRECT_WEB_DIR"
if command -v pnpm >/dev/null 2>&1; then
  NEXT_PUBLIC_VICOA_DIRECT=1 pnpm build
elif command -v corepack >/dev/null 2>&1; then
  NEXT_PUBLIC_VICOA_DIRECT=1 corepack pnpm build
else
  echo "pnpm/corepack not found; install pnpm first" >&2
  exit 1
fi

echo
echo "Direct web build complete: $VICOA_DIRECT_WEB_DIR/.next"
echo "Start the daemon with: $SCRIPT_DIR/direct-up.sh"
