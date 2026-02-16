#!/usr/bin/env bash
# lake-timed.sh — Wrapper around `lake` that records build timing
#
# Usage: ./scripts/lake-timed.sh build [args...]
#
# Passes all arguments to `lake`, records elapsed time to lake-timing.jsonl

start=$(date +%s)
lake "$@"
rc=$?
elapsed=$(($(date +%s) - start))
echo "{\"lake_args\":\"$*\",\"lake_build_seconds\":$elapsed,\"exit_code\":$rc,\"timestamp\":\"$(date -Iseconds)\"}" >> "${RESULTS_DIR:-.}/lake-timing.jsonl"
exit $rc
