#!/usr/bin/env bash
# lake-summarized.sh — lake wrapper that auto-summarizes errors and records timing
#
# Usage: ./scripts/lake-summarized.sh build [args...]
#
# Behavior:
#   1. Runs `lake` with all arguments
#   2. Captures exit code
#   3. If build fails, pipes stderr through lean-error-summarize.sh
#   4. If build succeeds, emits only a one-line summary
#   5. Records timing to lake-timing.jsonl
#
# The agent NEVER sees raw Mathlib type expansions.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-.}"

start=$(date +%s)

# Capture both stdout and stderr
output=$(lake "$@" 2>&1)
rc=$?

elapsed=$(($(date +%s) - start))

# Record timing
echo "{\"lake_args\":\"$*\",\"lake_build_seconds\":$elapsed,\"exit_code\":$rc,\"timestamp\":\"$(date -Iseconds)\"}" \
  >> "${RESULTS_DIR}/lake-timing.jsonl" 2>/dev/null || true

if [[ $rc -eq 0 ]]; then
  # Success: count sorry warnings, emit one-line summary
  sorry_count=$(echo "$output" | grep -c "declaration uses 'sorry'" || true)
  warning_count=$(echo "$output" | grep -c "warning:" || true)

  if [[ "$sorry_count" -gt 0 ]]; then
    echo "BUILD OK | ${sorry_count} sorry warning(s) | ${elapsed}s"
  elif [[ "$warning_count" -gt 0 ]]; then
    echo "BUILD OK | ${warning_count} warning(s) | ${elapsed}s"
  else
    echo "BUILD OK | clean | ${elapsed}s"
  fi

  # Still show sorry warnings (these are useful signal, not noise)
  echo "$output" | grep "declaration uses 'sorry'" || true
else
  # Failure: summarize errors
  echo "BUILD FAILED | exit ${rc} | ${elapsed}s"
  echo ""

  if [[ -x "$SCRIPT_DIR/lean-error-summarize.sh" ]]; then
    echo "$output" | "$SCRIPT_DIR/lean-error-summarize.sh"
  else
    # Fallback: truncated raw output (last 30 lines)
    echo "$output" | tail -30
  fi
fi

exit $rc
