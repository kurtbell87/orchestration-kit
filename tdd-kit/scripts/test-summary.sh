#!/usr/bin/env bash
# test-summary.sh — Run tests, emit only a compact summary to stdout.
# Full verbose output is saved to $TDD_LOG_DIR/test-output.log.
#
# Usage: ./scripts/test-summary.sh <command> [args...]
#
# What the agent sees:  exit code, pass/fail counts, failing test names, short errors.
# What it does NOT see: every passing test name, full tracebacks, warnings, timing.
# For detailed failure info: Read $TDD_LOG_DIR/test-output.log

set -uo pipefail

# Use TDD_LOG_DIR from environment (set by tdd.sh), fall back to per-project path.
if [[ -z "${TDD_LOG_DIR:-}" ]]; then
  _project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  _project_name="$(basename "$_project_root")"
  TDD_LOG_DIR="/tmp/tdd-${_project_name}"
fi
mkdir -p "$TDD_LOG_DIR"

FULL_LOG="$TDD_LOG_DIR/test-output.log"

# Run the real test command, capture everything
"$@" > "$FULL_LOG" 2>&1
rc=$?

# Parse and summarize
python3 - "$rc" "$FULL_LOG" <<'PYEOF'
import sys, re

rc = int(sys.argv[1])
log_path = sys.argv[2]

with open(log_path) as f:
    lines = f.readlines()

text = ''.join(lines)

# --- Framework detection ---

def detect():
    # Check specific frameworks first (vitest/jest/cargo/lake) before pytest,
    # because pytest markers like "passed" appear in other frameworks' output too.
    if any(k in text for k in ['vitest', 'VITE']):
        return 'vitest'
    if 'jest' in text.lower() and 'vitest' not in text.lower():
        return 'jest'
    if 'test result:' in text and ('ok' in text or 'FAILED' in text):
        return 'cargo'
    if any(k in text for k in ['lake', 'leanc', '.lean:']):
        return 'lake'
    if '::' in text or 'pytest' in text:
        return 'pytest'
    if re.search(r'\d+ passed', text) and re.search(r'in \d+\.\d+s', text):
        return 'pytest'
    if re.search(r'(ok|FAIL)\s+\S+\s+\d+\.\d+s', text):
        return 'go'
    return 'generic'

fw = detect()
out = []

# --- Pytest ---
if fw == 'pytest':
    # Final summary: "= 42 passed, 3 failed in 1.2s ="
    for line in lines:
        s = line.strip()
        if re.match(r'=+.*(?:passed|failed|error).*=+$', s):
            out.append(s)

    # FAILED test::name lines
    fails = [l.strip() for l in lines if l.strip().startswith('FAILED ')]
    if fails:
        out.append(f'\nFAILING ({len(fails)}):')
        out.extend(f'  {f}' for f in fails[:25])

    # Short assertion / import errors
    seen = set()
    errs = []
    for l in lines:
        s = l.strip()
        key = None
        if s.startswith('E ') and len(s) < 300:
            key = s
        elif 'ModuleNotFoundError' in s or 'ImportError' in s:
            key = s
        elif s.startswith('ERROR') and 'collecting' in s.lower():
            key = s
        if key and key not in seen:
            seen.add(key)
            errs.append(key)
    if errs:
        out.append('\nERRORS:')
        out.extend(f'  {e}' for e in errs[:15])

# --- Vitest / Jest ---
elif fw in ('vitest', 'jest'):
    for line in lines:
        s = line.strip()
        # vitest: "Test Files  1 failed | 1 passed (2)" / "Tests  2 failed | 5 passed (7)"
        # jest:   "Test Suites: 1 failed, 1 passed, 2 total" / "Tests: 2 failed, 5 passed, 7 total"
        if re.match(r'(Tests|Test Suites|Test Files)\s', s):
            out.append(s)
        elif re.match(r'Duration', s) or re.match(r'Time:', s):
            out.append(s)
    fails = [l.strip() for l in lines if re.match(r'\s*(FAIL\b|×|✕)', l.strip())]
    if fails:
        out.append(f'\nFAILING ({len(fails)}):')
        out.extend(f'  {f}' for f in fails[:25])
    errs = [l.strip() for l in lines
            if re.match(r'(Error|TypeError|ReferenceError|AssertionError)', l.strip())
            and len(l.strip()) < 300]
    if errs:
        out.append('\nERRORS:')
        out.extend(f'  {e}' for e in errs[:15])

# --- Cargo test ---
elif fw == 'cargo':
    for line in lines:
        s = line.strip()
        if s.startswith('test result:'):
            out.append(s)
    fails = [l.strip() for l in lines if '... FAILED' in l]
    if fails:
        out.append(f'\nFAILING ({len(fails)}):')
        out.extend(f'  {f}' for f in fails[:25])

# --- Go test ---
elif fw == 'go':
    for line in lines:
        s = line.strip()
        if s.startswith('FAIL') or s.startswith('ok'):
            out.append(s)
    fails = [l.strip() for l in lines if l.strip().startswith('--- FAIL')]
    if fails:
        out.append(f'\nFAILING ({len(fails)}):')
        out.extend(f'  {f}' for f in fails[:25])

# --- Lake (Lean 4) ---
elif fw == 'lake':
    errs = [l.rstrip() for l in lines if ': error:' in l.lower()]
    sorries = [l.rstrip() for l in lines if 'sorry' in l.lower() and 'warning' in l.lower()]
    if errs:
        out.append(f'{len(errs)} error(s):')
        out.extend(f'  {e[:200]}' for e in errs[:20])
    if sorries:
        out.append(f'{len(sorries)} sorry(s) remaining')
    if not errs and not sorries:
        out.append('Build succeeded, no sorries.')

# --- Generic fallback ---
else:
    out.extend(l.rstrip() for l in lines[-20:])

# Header
header = f'EXIT: {rc}'
if rc == 0 and not any('FAIL' in l.upper() for l in out):
    header += '  ALL PASSED'
print(header)
for l in out:
    print(l)
print(f'\nFull log: {log_path}')
PYEOF

exit "$rc"
