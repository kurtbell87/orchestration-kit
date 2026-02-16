#!/usr/bin/env bash
# .claude/hooks/pre-tool-use.sh
#
# Global anti-bloat guardrails + GREEN-phase test immutability enforcement.
#
# Hook receives tool name and input via environment variables:
#   CLAUDE_TOOL_NAME  -- the tool being invoked (Read, Edit, Write, Bash, etc.)
#   CLAUDE_TOOL_INPUT -- JSON string of the tool's input parameters

set -euo pipefail

# In orchestration-kit orchestration, delegate to the root dispatcher so global
# read-budget policies apply in addition to native TDD protections.
if [[ -n "${ORCHESTRATION_KIT_ROOT:-}" ]] && [[ "${MASTER_HOOK_ACTIVE:-0}" != "1" ]]; then
  MASTER_HOOK_PATH="${ORCHESTRATION_KIT_ROOT}/.claude/hooks/pre-tool-use.sh"
  if [[ -f "$MASTER_HOOK_PATH" ]]; then
    MASTER_HOOK_ACTIVE=1 "$MASTER_HOOK_PATH"
    exit $?
  fi
fi

TOOL_NAME="${CLAUDE_TOOL_NAME:-}"
TOOL_INPUT="${CLAUDE_TOOL_INPUT:-}"

MAX_READ_BYTES_RAW="${MAX_READ_BYTES:-200000}"
if [[ "$MAX_READ_BYTES_RAW" =~ ^[0-9]+$ ]]; then
  MAX_READ_BYTES="$MAX_READ_BYTES_RAW"
else
  MAX_READ_BYTES=200000
fi

READ_BUDGET_MAX_FILES_RAW="${READ_BUDGET_MAX_FILES:-0}"
if [[ "$READ_BUDGET_MAX_FILES_RAW" =~ ^[0-9]+$ ]]; then
  READ_BUDGET_MAX_FILES="$READ_BUDGET_MAX_FILES_RAW"
else
  READ_BUDGET_MAX_FILES=0
fi

READ_BUDGET_MAX_TOTAL_BYTES_RAW="${READ_BUDGET_MAX_TOTAL_BYTES:-0}"
if [[ "$READ_BUDGET_MAX_TOTAL_BYTES_RAW" =~ ^[0-9]+$ ]]; then
  READ_BUDGET_MAX_TOTAL_BYTES="$READ_BUDGET_MAX_TOTAL_BYTES_RAW"
else
  READ_BUDGET_MAX_TOTAL_BYTES=0
fi

# Comma/colon/semicolon-separated globs exempt from read limits.
# Users can extend this with READ_ALLOW_GLOBS or MUST_READ_ALLOWLIST.
DEFAULT_ALLOW_GLOBS="docs/*.md,PRD.md,LAST_TOUCH.md,CLAUDE.md,AGENTS.md,.claude/prompts/*.md,.codex/prompts/*.md,templates/*.md"
ALLOWLIST_RAW="${READ_ALLOW_GLOBS:-${MUST_READ_ALLOWLIST:-}},${DEFAULT_ALLOW_GLOBS}"

split_patterns() {
  printf '%s' "$1" | tr ',;:' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | sed '/^$/d'
}

is_path_allowed() {
  local candidate="$1"
  while IFS= read -r pattern; do
    if [[ -n "$pattern" && "$candidate" == $pattern ]]; then
      return 0
    fi
  done < <(split_patterns "$ALLOWLIST_RAW")
  return 1
}

extract_read_path() {
  python3 - "$TOOL_INPUT" <<'PY'
import json
import sys

raw = ""
if len(sys.argv) > 1:
    raw = sys.argv[1].strip()
if not raw:
    sys.exit(0)

try:
    payload = json.loads(raw)
except Exception:
    sys.exit(0)

for key in ("file_path", "path", "target_file", "filename"):
    value = payload.get(key)
    if isinstance(value, str) and value:
        print(value)
        sys.exit(0)
PY
}

normalize_path() {
  local requested="$1"
  local abs="$requested"
  if [[ "$abs" != /* ]]; then
    abs="$PWD/$abs"
  fi
  abs="$(cd "$(dirname "$abs")" 2>/dev/null && pwd)/$(basename "$abs")"
  printf '%s' "$abs"
}

read_budget_state_file() {
  local state_dir="${READ_BUDGET_STATE_DIR:-${TMPDIR:-/tmp}}"
  mkdir -p "$state_dir"

  local key
  key="${RUN_ID:-$PWD}"
  key="$(printf '%s' "$key" | tr '/ :\n\t' '_')"
  printf '%s/tdd-read-budget-%s.json' "$state_dir" "$key"
}

apply_global_read_guardrail() {
  if [[ "$TOOL_NAME" != "Read" ]]; then
    return
  fi

  if [[ "$MAX_READ_BYTES" -le 0 ]]; then
    return
  fi

  local requested_path
  requested_path="$(extract_read_path || true)"
  if [[ -z "$requested_path" ]]; then
    return
  fi

  local absolute_path
  absolute_path="$(normalize_path "$requested_path")"

  if [[ ! -f "$absolute_path" ]]; then
    return
  fi

  if is_path_allowed "$requested_path" || is_path_allowed "$absolute_path"; then
    return
  fi

  local size
  size="$(wc -c < "$absolute_path" | tr -d ' ')"

  if [[ "$size" -gt "$MAX_READ_BYTES" ]]; then
    echo "BLOCKED: Read over MAX_READ_BYTES=$MAX_READ_BYTES (requested: $size bytes)." >&2
    echo "   Use bounded reads (tail/grep) or add a narrow allowlist pattern." >&2
    exit 1
  fi
}

apply_read_budget_guardrail() {
  if [[ "$TOOL_NAME" != "Read" ]]; then
    return
  fi

  if [[ "$READ_BUDGET_MAX_FILES" -le 0 && "$READ_BUDGET_MAX_TOTAL_BYTES" -le 0 ]]; then
    return
  fi

  local requested_path
  requested_path="$(extract_read_path || true)"
  if [[ -z "$requested_path" ]]; then
    return
  fi

  local absolute_path
  absolute_path="$(normalize_path "$requested_path")"
  if [[ ! -f "$absolute_path" ]]; then
    return
  fi

  if is_path_allowed "$requested_path" || is_path_allowed "$absolute_path"; then
    return
  fi

  local size
  size="$(wc -c < "$absolute_path" | tr -d ' ')"

  local state_file
  state_file="$(read_budget_state_file)"

  local budget_result
  budget_result="$(python3 - "$state_file" "$absolute_path" "$size" "$READ_BUDGET_MAX_FILES" "$READ_BUDGET_MAX_TOTAL_BYTES" <<'PY'
import json
import os
import sys

state_path, read_path, size_raw, max_files_raw, max_bytes_raw = sys.argv[1:6]
size = int(size_raw)
max_files = int(max_files_raw)
max_bytes = int(max_bytes_raw)

state = {
    "read_paths": [],
    "total_bytes": 0,
}

if os.path.exists(state_path):
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
            if isinstance(loaded, dict):
                state.update(loaded)
    except Exception:
        pass

read_paths = state.get("read_paths", [])
if not isinstance(read_paths, list):
    read_paths = []

unique_paths = set(str(p) for p in read_paths)
prospective_unique = set(unique_paths)
prospective_unique.add(read_path)

current_total = int(state.get("total_bytes", 0) or 0)
prospective_total = current_total + size

if max_files > 0 and len(prospective_unique) > max_files:
    print(
        f"BLOCKED: Read budget exceeded (max_files={max_files}, requested_unique={len(prospective_unique)})."
    )
    print("   Use pointer files and bounded reads.")
    sys.exit(2)

if max_bytes > 0 and prospective_total > max_bytes:
    print(
        f"BLOCKED: Read budget exceeded (max_total_bytes={max_bytes}, requested_total={prospective_total})."
    )
    print("   Use pointer files and bounded reads.")
    sys.exit(3)

state["read_paths"] = sorted(prospective_unique)
state["total_bytes"] = prospective_total

with open(state_path, "w", encoding="utf-8") as fh:
    json.dump(state, fh)

print("OK")
PY
)" || {
    printf '%s\n' "$budget_result" >&2
    exit 1
  }
}

# Always apply global anti-bloat read guardrails.
apply_global_read_guardrail
apply_read_budget_guardrail

# Only enforce test immutability during GREEN phase.
if [[ "${TDD_PHASE:-}" != "green" ]]; then
  exit 0
fi

# Patterns that identify test files (broad coverage of conventions)
TEST_PATTERNS='(test_[^/]*\.|[^/]*_test\.|[^/]*\.test\.|[^/]*\.spec\.|/tests/|/test/|/__tests__/|/spec/)'

# --- Block 1: Permission escalation commands ---
if [[ "$TOOL_NAME" == "Bash" ]]; then
  INPUT="$TOOL_INPUT"

  # Block permission/ownership changes
  if echo "$INPUT" | grep -qEi '(chmod|chown|sudo|doas|install\s)'; then
    echo "BLOCKED: Permission-modifying commands are not allowed during GREEN phase." >&2
    echo "   Test files are read-only by design. Implement to satisfy them." >&2
    exit 1
  fi

  # Block git commands that could revert test files
  if echo "$INPUT" | grep -qEi 'git\s+(checkout|restore|stash|reset)\s'; then
    echo "BLOCKED: Git revert commands are not allowed during GREEN phase." >&2
    echo "   Test files must not be reverted or modified." >&2
    exit 1
  fi

  # Block unbounded log dumps during GREEN; these create token bloat loops.
  if echo "$INPUT" | grep -qEi 'cat\s+[^|;&]*tdd[^ ]*\.log'; then
    echo "BLOCKED: Full log dumps are not allowed during GREEN phase." >&2
    echo "   Use ./tdd.sh watch <phase> or bounded reads (tail/grep)." >&2
    exit 1
  fi

  # Block direct writes to test files via bash
  if echo "$INPUT" | grep -qE "$TEST_PATTERNS"; then
    if echo "$INPUT" | grep -qEi '(>|tee|sed\s+-i|awk.*-i|perl\s+-[pi]|mv\s|cp\s.*>|rm\s)'; then
      echo "BLOCKED: Cannot modify test files via shell commands during GREEN phase." >&2
      exit 1
    fi
  fi
fi

# --- Block 2: Direct file writes to test files ---
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" ]]; then
  if echo "$TOOL_INPUT" | grep -qE "$TEST_PATTERNS"; then
    echo "BLOCKED: Cannot edit test files during GREEN phase." >&2
    echo "   Tests are your specification. Implement code to satisfy them." >&2
    exit 1
  fi
fi

exit 0
