#!/usr/bin/env bash
# enumerate-sorrys.sh — List all sorry locations with surrounding context
#
# Usage: ./scripts/enumerate-sorrys.sh [dir]
#
# Output: JSON lines, one per sorry
# {"file":"path/to/File.lean","line":42,"theorem":"my_theorem","theorem_start":38}

set -euo pipefail

LEAN_DIR="${1:-.}"

find "$LEAN_DIR" -type f -name "*.lean" \
  ! -path "*/.lake/*" \
  ! -path "*/lake-packages/*" \
  ! -path "*/.git/*" \
  ! -path "*/.elan/*" \
  ! -path "*/scratch/*" \
  2>/dev/null | while IFS= read -r file; do

  grep -n '\bsorry\b' "$file" 2>/dev/null | while IFS=: read -r lineno _; do
    # Look backwards for the enclosing theorem/lemma/def
    theorem_name="unknown"
    theorem_line="$lineno"
    for ((i=lineno; i>=1; i--)); do
      candidate=$(sed -n "${i}p" "$file")
      if echo "$candidate" | grep -qE '^\s*(theorem|lemma|instance)\s+'; then
        theorem_name=$(echo "$candidate" | grep -oE '(theorem|lemma|instance)\s+\S+' | head -1 | awk '{print $2}')
        theorem_line="$i"
        break
      fi
    done

    # Extract context: from theorem declaration to sorry line + 2
    end_line=$((lineno + 2))
    context=$(sed -n "${theorem_line},${end_line}p" "$file" | head -20)

    python3 -c "
import json
print(json.dumps({
    'file': '$file',
    'line': $lineno,
    'theorem': '$theorem_name',
    'theorem_start': $theorem_line,
}))
" 2>/dev/null || true
  done
done
