#!/usr/bin/env bash
# mathlib-search.sh — Search Mathlib source for definitions, theorems, and typeclasses
#
# Usage:
#   ./scripts/mathlib-search.sh <query> [--defs] [--thms] [--instances] [--module <path>]
#
# Examples:
#   ./scripts/mathlib-search.sh "IsStoppingTime"
#   ./scripts/mathlib-search.sh "Filtration" --module MeasureTheory
#   ./scripts/mathlib-search.sh "condexp" --defs --thms

set -euo pipefail

usage() {
  echo "Usage: $0 <query> [--defs] [--thms] [--instances] [--module <path>]"
  echo ""
  echo "Search Mathlib source for definitions, theorems, and typeclasses."
  echo ""
  echo "Options:"
  echo "  --defs        Search for def, noncomputable def, abbrev"
  echo "  --thms        Search for theorem, lemma"
  echo "  --instances   Search for instance declarations"
  echo "  --module PATH Restrict search to Mathlib/<PATH>/ subtree"
  echo ""
  echo "If no --defs/--thms/--instances flags given, searches all."
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

QUERY="$1"
shift

SEARCH_DEFS=false
SEARCH_THMS=false
SEARCH_INSTANCES=false
MODULE_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --defs)      SEARCH_DEFS=true; shift ;;
    --thms)      SEARCH_THMS=true; shift ;;
    --instances) SEARCH_INSTANCES=true; shift ;;
    --module)    MODULE_PATH="$2"; shift 2 ;;
    -h|--help)   usage ;;
    *)           echo "Unknown option: $1" >&2; usage ;;
  esac
done

# If no specific flags, search all
if [[ "$SEARCH_DEFS" == "false" && "$SEARCH_THMS" == "false" && "$SEARCH_INSTANCES" == "false" ]]; then
  SEARCH_DEFS=true
  SEARCH_THMS=true
  SEARCH_INSTANCES=true
fi

# Locate Mathlib source tree
MATHLIB_ROOT=""
for candidate in .lake/packages/mathlib/Mathlib lake-packages/mathlib/Mathlib; do
  if [[ -d "$candidate" ]]; then
    MATHLIB_ROOT="$candidate"
    break
  fi
done

if [[ -z "$MATHLIB_ROOT" ]]; then
  echo "ERROR: Mathlib source tree not found." >&2
  echo "Checked: .lake/packages/mathlib/Mathlib, lake-packages/mathlib/Mathlib" >&2
  echo "Run 'lake update' first." >&2
  exit 1
fi

# Build search directory
SEARCH_DIR="$MATHLIB_ROOT"
if [[ -n "$MODULE_PATH" ]]; then
  SEARCH_DIR="$MATHLIB_ROOT/$MODULE_PATH"
  if [[ ! -d "$SEARCH_DIR" ]]; then
    echo "ERROR: Module path not found: $SEARCH_DIR" >&2
    echo "Available top-level modules:" >&2
    ls -1 "$MATHLIB_ROOT" | head -20 >&2
    exit 1
  fi
fi

# Build grep patterns
PATTERNS=()
if [[ "$SEARCH_DEFS" == "true" ]]; then
  PATTERNS+=("^(noncomputable )?def .*${QUERY}" "^abbrev .*${QUERY}")
fi
if [[ "$SEARCH_THMS" == "true" ]]; then
  PATTERNS+=("^theorem .*${QUERY}" "^lemma .*${QUERY}")
fi
if [[ "$SEARCH_INSTANCES" == "true" ]]; then
  PATTERNS+=("^instance .*${QUERY}")
fi

# Join patterns with |
COMBINED_PATTERN=$(IFS='|'; echo "${PATTERNS[*]}")

# Search, strip comment-only lines, deduplicate, sort by file path
grep -rnE "$COMBINED_PATTERN" "$SEARCH_DIR" --include="*.lean" 2>/dev/null \
  | grep -v '^\s*--' \
  | sort -t: -k1,1 -k2,2n \
  | uniq

exit 0
