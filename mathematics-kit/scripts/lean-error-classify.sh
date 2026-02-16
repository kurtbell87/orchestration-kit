#!/usr/bin/env bash
# lean-error-classify.sh — Classify Lean4 build errors
#
# Usage: lake build 2>&1 | ./scripts/lean-error-classify.sh
#
# Output: one line per error, format: CLASS|FILE|LINE|SUMMARY
# Classes: TYPE_MISMATCH, UNKNOWN_IDENT, TACTIC_FAIL, TIMEOUT, UNIVERSE_INCOMPAT, OTHER

set -euo pipefail

# Read all stdin
INPUT=$(cat)

if [[ -z "$INPUT" ]]; then
  exit 0
fi

# Parse error blocks and classify each
echo "$INPUT" | python3 -c "
import sys
import re

text = sys.stdin.read()

# Split into error blocks (each starts with a file:line:col pattern followed by 'error:')
error_pattern = re.compile(r'^(.+?):(\d+):\d+:\s*error:\s*(.+?)(?=\n\S+:\d+:\d+:\s*error:|\Z)', re.MULTILINE | re.DOTALL)

matches = error_pattern.findall(text)

if not matches:
    # Try simpler pattern for errors without file:line prefix
    simple_pattern = re.compile(r'error:\s*(.+?)(?=\nerror:|\Z)', re.MULTILINE | re.DOTALL)
    for m in simple_pattern.findall(text):
        summary = m.strip().split('\n')[0][:120]
        print(f'OTHER|unknown|0|{summary}')
    sys.exit(0)

for filepath, line_num, error_body in matches:
    error_body = error_body.strip()
    first_line = error_body.split('\n')[0].strip()
    summary = first_line[:120]

    # Classification order matters: UNIVERSE_INCOMPAT before TYPE_MISMATCH
    cls = 'OTHER'

    # UNIVERSE_INCOMPAT: universe-related errors (check before TYPE_MISMATCH)
    if any(kw in error_body.lower() for kw in [
        'universe level', 'universe inconsistency',
        'universe mismatch', 'universe constraint',
    ]):
        cls = 'UNIVERSE_INCOMPAT'
    elif 'type mismatch' in error_body.lower():
        # Check if it's actually a universe issue disguised as type mismatch
        if re.search(r'Type\s+\d+', error_body) or 'Sort' in error_body:
            # Heuristic: if expected/found types differ only in universe level
            if re.search(r'Type\s*(?:u|v|\d+)', error_body):
                cls = 'UNIVERSE_INCOMPAT'
            else:
                cls = 'TYPE_MISMATCH'
        else:
            cls = 'TYPE_MISMATCH'
    elif any(kw in error_body.lower() for kw in ['unknown identifier', 'unknown constant']):
        cls = 'UNKNOWN_IDENT'
    elif any(kw in error_body.lower() for kw in [
        \"tactic '\", 'unsolved goals', 'no goals to close', 'tactic failed',
    ]):
        cls = 'TACTIC_FAIL'
    elif any(kw in error_body.lower() for kw in [
        'deterministic timeout', 'maximum recursion depth', '(timeout)',
        'deep recursion was detected', 'heartbeats',
    ]):
        cls = 'TIMEOUT'

    filepath_short = filepath.rsplit('/', 1)[-1] if '/' in filepath else filepath
    print(f'{cls}|{filepath_short}|{line_num}|{summary}')
"
