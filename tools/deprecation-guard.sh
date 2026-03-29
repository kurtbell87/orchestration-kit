#!/usr/bin/env bash
set -euo pipefail

TOOL_NAME="${1:-orchestration-kit}"
OVERRIDE="${ORCHESTRATION_KIT_ALLOW_LEGACY:-}"
OVERRIDE_LC="$(printf '%s' "$OVERRIDE" | tr '[:upper:]' '[:lower:]')"

case "$OVERRIDE_LC" in
  1|true|yes|on)
    echo "[deprecated] ${TOOL_NAME}: continuing because ORCHESTRATION_KIT_ALLOW_LEGACY=1 is set." >&2
    exit 0
    ;;
esac

cat >&2 <<EOF
[deprecated] orchestration-kit is retired as a live runtime.
[deprecated] ${TOOL_NAME} is blocked by default.
[deprecated] Use the active stack instead:
[deprecated]   - /Users/brandonbell/LOCAL_DEV/kenoma-kbus
[deprecated]   - /Users/brandonbell/LOCAL_DEV/kenoma-oracle-pod
[deprecated]   - /Users/brandonbell/LOCAL_DEV/tdd-kit
[deprecated]   - /Users/brandonbell/LOCAL_DEV/research-kit
[deprecated]   - /Users/brandonbell/LOCAL_DEV/mathematics-kit
[deprecated] For emergency legacy use only, set ORCHESTRATION_KIT_ALLOW_LEGACY=1 and re-run.
EOF
exit 86
