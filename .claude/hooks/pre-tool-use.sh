#!/usr/bin/env bash
# Master pre-tool-use hook.
#
# Responsibilities:
# 1) Dispatch to kit-specific enforcement rules based on active phase env vars.
# 2) Apply global read guardrails (large file block + bounded read budget).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOL_NAME="${CLAUDE_TOOL_NAME:-}"
TOOL_INPUT="${CLAUDE_TOOL_INPUT:-}"
export MASTER_HOOK_ACTIVE=1
export ORCHESTRATION_KIT_ROOT="${ORCHESTRATION_KIT_ROOT:-$ROOT_DIR}"

debug_master_hook() {
  if [[ "${MASTER_HOOK_DEBUG:-0}" == "1" ]]; then
    echo "MASTER_HOOK: $*" >&2
  fi
}

debug_master_hook "enter"

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

DEFAULT_ALLOW_GLOBS="runs/*/capsules/*.md,runs/*/manifests/*.json,interop/requests/*.json,interop/responses/*.json"
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
  # Do not fail if path is missing; caller checks existence.
  abs="$(cd "$(dirname "$abs")" 2>/dev/null && pwd)/$(basename "$abs")"
  printf '%s' "$abs"
}

read_budget_state_file() {
  local state_dir="${READ_BUDGET_STATE_DIR:-${TMPDIR:-/tmp}}"
  mkdir -p "$state_dir"

  local key
  key="${RUN_ID:-${PARENT_RUN_ID:-$PWD}}"
  key="$(printf '%s' "$key" | tr '/ :\n\t' '_')"
  printf '%s/orchestration-kit-read-budget-%s.json' "$state_dir" "$key"
}

run_hook_if_exists() {
  local hook_path="$1"
  local hook_name="${2:-$hook_path}"
  if [[ -f "$hook_path" ]]; then
    debug_master_hook "dispatch $hook_name"
    MASTER_HOOK_ACTIVE=1 "$hook_path"
  fi
}

apply_global_read_guardrail() {
  if [[ "$TOOL_NAME" != "Read" ]]; then
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

  local size
  size="$(wc -c < "$absolute_path" | tr -d ' ')"

  if is_path_allowed "$requested_path" || is_path_allowed "$absolute_path"; then
    return
  fi

  if [[ "$size" -gt "$MAX_READ_BYTES" ]]; then
    echo "BLOCKED: Read of large file is over MAX_READ_BYTES=$MAX_READ_BYTES (requested: $size bytes)." >&2
    echo "   Use query approach (tail/grep/classify) or add pointer to must_read allowlist." >&2
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

  # Explicit allowlist pointers are exempt from budget accounting.
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
    print("   Use must_read pointers and query-by-pointer tools (tail/grep/classify).")
    sys.exit(2)

if max_bytes > 0 and prospective_total > max_bytes:
    print(
        f"BLOCKED: Read budget exceeded (max_total_bytes={max_bytes}, requested_total={prospective_total})."
    )
    print("   Use must_read pointers and query-by-pointer tools (tail/grep/classify).")
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

# Global guardrails first.
apply_global_read_guardrail
apply_read_budget_guardrail

# Phase-specific dispatch (reuse existing kit rules verbatim).
if [[ -n "${TDD_PHASE:-}" ]]; then
  run_hook_if_exists "$ORCHESTRATION_KIT_ROOT/tdd-kit/.claude/hooks/pre-tool-use.sh" "tdd"
fi

if [[ -n "${EXP_PHASE:-}" ]]; then
  run_hook_if_exists "$ORCHESTRATION_KIT_ROOT/research-kit/.claude/hooks/pre-tool-use.sh" "research"
fi

if [[ -n "${MATH_PHASE:-}" ]]; then
  run_hook_if_exists "$ORCHESTRATION_KIT_ROOT/mathematics-kit/.claude/hooks/pre-tool-use.sh" "math"
fi

exit 0
