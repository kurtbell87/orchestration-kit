#!/usr/bin/env bash
# context-checkpoint.sh — Monitor agent turns and signal checkpoint when threshold hit
#
# Usage: ./scripts/context-checkpoint.sh <phase> [--threshold N] &
#
# Runs in background. Monitors $MATH_LOG_DIR/<phase>.log for assistant turn count.
# When threshold is reached, creates $MATH_LOG_DIR/.checkpoint-requested.

set -uo pipefail

PHASE="${1:?Usage: context-checkpoint.sh <phase>}"
THRESHOLD=40

shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --threshold) THRESHOLD="$2"; shift 2 ;;
    *) shift ;;
  esac
done

LOG_DIR="${MATH_LOG_DIR:-/tmp/math-unknown}"
LOG_FILE="$LOG_DIR/${PHASE}.log"
CHECKPOINT_FILE="$LOG_DIR/.checkpoint-requested"

rm -f "$CHECKPOINT_FILE"

while true; do
  sleep 15

  if [[ ! -f "$LOG_FILE" ]]; then
    continue
  fi

  turns=$(grep -c '"type":"assistant"' "$LOG_FILE" 2>/dev/null || echo 0)

  if (( turns >= THRESHOLD )); then
    echo "{\"checkpoint\":true,\"turns\":$turns,\"threshold\":$THRESHOLD,\"timestamp\":\"$(date -Iseconds)\"}" \
      > "$CHECKPOINT_FILE"
    exit 0
  fi
done
