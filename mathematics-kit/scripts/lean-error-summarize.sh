#!/usr/bin/env bash
# lean-error-summarize.sh — Condense Lean4 build errors for agent consumption
#
# Usage: lake build 2>&1 | ./scripts/lean-error-summarize.sh
#
# Strips Mathlib-internal type expansions beyond depth 2, extracts goal state,
# groups errors by file:line, and produces a compact summary.
# Output is capped at 40 lines.

set -euo pipefail

INPUT=$(cat)

if [[ -z "$INPUT" ]]; then
  exit 0
fi

echo "$INPUT" | python3 -c "
import sys
import re

text = sys.stdin.read()
MAX_LINES = 40

# Split into error blocks
error_pattern = re.compile(
    r'^(.+?):(\d+):(\d+):\s*error:\s*(.+?)(?=\n\S+:\d+:\d+:\s*(?:error|warning):|\Z)',
    re.MULTILINE | re.DOTALL
)

matches = error_pattern.findall(text)

if not matches:
    # If no structured errors, just print truncated output
    lines = text.strip().split('\n')
    for line in lines[:MAX_LINES]:
        print(line)
    if len(lines) > MAX_LINES:
        print(f'... ({len(lines) - MAX_LINES} more lines)')
    sys.exit(0)


def classify(body):
    body_lower = body.lower()
    if any(kw in body_lower for kw in ['universe level', 'universe inconsistency', 'universe mismatch']):
        return 'UNIVERSE_INCOMPAT'
    if 'type mismatch' in body_lower:
        if re.search(r'Type\s+\d+', body) or 'Sort' in body:
            return 'UNIVERSE_INCOMPAT'
        return 'TYPE_MISMATCH'
    if any(kw in body_lower for kw in ['unknown identifier', 'unknown constant']):
        return 'UNKNOWN_IDENT'
    if any(kw in body_lower for kw in [\"tactic '\", 'unsolved goals', 'no goals to close', 'tactic failed']):
        return 'TACTIC_FAIL'
    if any(kw in body_lower for kw in ['deterministic timeout', 'maximum recursion depth', '(timeout)', 'heartbeats']):
        return 'TIMEOUT'
    return 'OTHER'


def truncate_types(text, max_depth=2):
    \"\"\"Truncate deeply nested type expressions.\"\"\"
    result = []
    for line in text.split('\n'):
        # Count nesting indicators (@ symbols, deeply dotted names)
        dots = line.count('.')
        ats = line.count('@')
        if dots > 8 or ats > 3:
            # Truncate after the first meaningful part
            parts = line.split()
            if len(parts) > 4:
                line = ' '.join(parts[:4]) + ' ...'
        result.append(line)
    return '\n'.join(result)


def extract_goal(body):
    \"\"\"Extract goal state (line starting with ⊢).\"\"\"
    for line in body.split('\n'):
        stripped = line.strip()
        if stripped.startswith('⊢'):
            return stripped[:120]
    return None


def extract_expected_found(body):
    \"\"\"Extract expected/found from type mismatch errors.\"\"\"
    expected = None
    found = None
    lines = body.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('expected'):
            expected = stripped[:120]
        elif stripped.startswith('has type'):
            found = stripped[:120]
    return expected, found


output_lines = []
seen = set()

for filepath, line_num, col_num, error_body in matches:
    key = (filepath, line_num)
    if key in seen:
        continue
    seen.add(key)

    cls = classify(error_body)
    filepath_short = filepath.rsplit('/', 1)[-1] if '/' in filepath else filepath

    output_lines.append(f'[{cls}] {filepath_short}:{line_num}')

    # Extract key details based on class
    if cls == 'TYPE_MISMATCH':
        expected, found = extract_expected_found(error_body)
        if expected:
            output_lines.append(f'  {expected}')
        if found:
            output_lines.append(f'  {found}')
    elif cls == 'TACTIC_FAIL' or cls == 'OTHER':
        first_line = error_body.strip().split('\n')[0][:120]
        output_lines.append(f'  {first_line}')
    elif cls == 'UNKNOWN_IDENT':
        first_line = error_body.strip().split('\n')[0][:120]
        output_lines.append(f'  {first_line}')
    elif cls == 'TIMEOUT':
        output_lines.append(f'  Timeout — simplify proof or use more targeted tactics')
    elif cls == 'UNIVERSE_INCOMPAT':
        first_line = error_body.strip().split('\n')[0][:120]
        output_lines.append(f'  {first_line}')

    goal = extract_goal(error_body)
    if goal:
        output_lines.append(f'  Goal: {goal}')

    output_lines.append('')

# Cap output
for line in output_lines[:MAX_LINES]:
    print(line)

remaining = len(output_lines) - MAX_LINES
if remaining > 0:
    total_errors = len(seen)
    print(f'... ({remaining} more lines, {total_errors} total errors)')
"
