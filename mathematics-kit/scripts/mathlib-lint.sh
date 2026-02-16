#!/usr/bin/env bash
# scripts/mathlib-lint.sh -- Standalone Mathlib style checker
#
# Usage:
#   ./scripts/mathlib-lint.sh [file.lean ...]
#   ./scripts/mathlib-lint.sh                    # check all project .lean files
#
# Exit code 1 if issues found.
# Output format: FILE:LINE: LEVEL: MESSAGE

set -euo pipefail

# Colors (disabled if not a terminal)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  YELLOW='\033[1;33m'
  NC='\033[0m'
else
  RED=''
  YELLOW=''
  NC=''
fi

issues_found=0

lint_file() {
  local file="$1"

  if [[ ! -f "$file" ]]; then
    echo -e "${RED}${file}:0: ERROR: file not found${NC}"
    issues_found=1
    return
  fi

  # ── 1. Copyright header (first line must be `/-`) ──
  local first_line
  first_line=$(head -1 "$file")
  if [[ "$first_line" != "/-" ]]; then
    echo -e "${RED}${file}:1: ERROR: missing copyright header (first line should be \`/-\`)${NC}"
    issues_found=1
  fi

  # ── 2. Module docstring (`/-!` must appear) ──
  if ! grep -qE '^\/-!' "$file"; then
    echo -e "${RED}${file}:0: ERROR: missing module docstring (\`/-! ... -/\`)${NC}"
    issues_found=1
  fi

  # ── 3. Line length <= 100 chars ──
  local lineno=0
  while IFS= read -r line; do
    lineno=$((lineno + 1))
    local len=${#line}
    if (( len > 100 )); then
      echo -e "${YELLOW}${file}:${lineno}: WARNING: line is ${len} chars (max 100)${NC}"
      issues_found=1
    fi
  done < "$file"

  # ── 4. `fun` not `λ` ──
  local lno=0
  while IFS= read -r line; do
    lno=$((lno + 1))
    if echo "$line" | grep -qE '\bλ\b'; then
      echo -e "${YELLOW}${file}:${lno}: WARNING: use \`fun\` not \`λ\`${NC}"
      issues_found=1
    fi
  done < "$file"

  # ── 5. `Type*` not `Type _` ──
  lno=0
  while IFS= read -r line; do
    lno=$((lno + 1))
    if echo "$line" | grep -qE 'Type _'; then
      echo -e "${YELLOW}${file}:${lno}: WARNING: use \`Type*\` not \`Type _\`${NC}"
      issues_found=1
    fi
  done < "$file"

  # ── 6. Doc strings on def/structure/class/instance ──
  # Heuristic: check if the line above a declaration ends with `-/`
  local prev_line=""
  lno=0
  while IFS= read -r line; do
    lno=$((lno + 1))
    # Match top-level declarations (not indented `def` inside where blocks)
    if echo "$line" | grep -qE '^\s*(def|structure|class|instance)\s+'; then
      # Check if previous non-blank line ends with -/
      local trimmed
      trimmed=$(echo "$prev_line" | sed 's/[[:space:]]*$//')
      if [[ "$trimmed" != *"-/" ]]; then
        local decl_kind
        decl_kind=$(echo "$line" | grep -oE '(def|structure|class|instance)' | head -1)
        echo -e "${YELLOW}${file}:${lno}: WARNING: ${decl_kind} may be missing doc string (no \`-/\` on preceding line)${NC}"
        issues_found=1
      fi
    fi
    # Track previous non-blank line
    if [[ -n "$(echo "$line" | tr -d '[:space:]')" ]]; then
      prev_line="$line"
    fi
  done < "$file"
}

# ── Collect files ──

files=()
if [[ $# -gt 0 ]]; then
  files=("$@")
else
  while IFS= read -r f; do
    files+=("$f")
  done < <(find . -type f -name "*.lean" \
    ! -path "*/.lake/*" \
    ! -path "*/lake-packages/*" \
    ! -path "*/.git/*" \
    ! -path "*/.elan/*" \
    ! -path "*/scratch/*" \
    2>/dev/null || true)
fi

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No .lean files found."
  exit 0
fi

for f in "${files[@]}"; do
  lint_file "$f"
done

if [[ "$issues_found" -eq 0 ]]; then
  echo "All files pass Mathlib style checks."
  exit 0
else
  exit 1
fi
