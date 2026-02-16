#!/usr/bin/env bash
# math-aliases.sh -- Source this in your shell
#
#   source /path/to/math-aliases.sh
#
# Then use:
#   math-survey specs/my-construction.md
#   math-specify specs/my-construction.md
#   math-construct specs/my-construction.md
#   math-formalize specs/my-construction.md
#   math-prove specs/my-construction.md
#   math-polish specs/my-construction.md
#   math-audit specs/my-construction.md
#   math-log specs/my-construction.md
#   math-full specs/my-construction.md
#   math-status
#   math-unlock
#   math-sorrys
#   math-axioms

MATH_SCRIPT="./math.sh"

alias math-survey='bash $MATH_SCRIPT survey'
alias math-specify='bash $MATH_SCRIPT specify'
alias math-construct='bash $MATH_SCRIPT construct'
alias math-formalize='bash $MATH_SCRIPT formalize'
alias math-prove='bash $MATH_SCRIPT prove'
alias math-polish='bash $MATH_SCRIPT polish'
alias math-audit='bash $MATH_SCRIPT audit'
alias math-log='bash $MATH_SCRIPT log'
alias math-full='bash $MATH_SCRIPT full'
alias math-program='bash $MATH_SCRIPT program'

math-status() {
  bash $MATH_SCRIPT status
}

math-unlock() {
  echo "Emergency unlock -- restoring write permissions on all locked files..."

  # Unlock spec files
  find specs/ -type f -name "*.md" -exec chmod 644 {} \; 2>/dev/null || true
  find specs/ -type d -exec chmod 755 {} \; 2>/dev/null || true

  # Unlock .lean files
  find . -type f -name "*.lean" \
    ! -path "*/.git/*" \
    ! -path "*/.lake/*" \
    ! -path "*/lake-packages/*" \
    -exec chmod 644 {} \; 2>/dev/null || true

  # Unlock any locked directories
  find . -maxdepth 3 -type d \
    ! -path "*/.git/*" \
    ! -path "*/.lake/*" \
    ! -path "*/lake-packages/*" \
    -exec chmod 755 {} \; 2>/dev/null || true

  echo "Done. All project files unlocked."
}

math-sorrys() {
  echo "Sorry count across all .lean files:"
  echo "====================================="
  local total=0
  while IFS= read -r f; do
    local count
    count=$(grep -c '\bsorry\b' "$f" 2>/dev/null || echo 0)
    if [[ "$count" -gt 0 ]]; then
      echo "  $count  $f"
      total=$((total + count))
    fi
  done < <(find . -type f -name "*.lean" \
    ! -path "*/.lake/*" \
    ! -path "*/lake-packages/*" \
    ! -path "*/.git/*")
  echo "====================================="
  echo "Total: $total sorry(s)"
}

math-axioms() {
  echo "Axiom/unsafe/native_decide scan:"
  echo "=================================="
  local found=false
  while IFS= read -r f; do
    for pattern in '\baxiom\b' '\bunsafe\b' '\bnative_decide\b' '\badmit\b'; do
      local matches
      matches=$(grep -n "$pattern" "$f" 2>/dev/null || true)
      if [[ -n "$matches" ]]; then
        echo "  $f:"
        echo "$matches" | sed 's/^/    /'
        found=true
      fi
    done
  done < <(find . -type f -name "*.lean" \
    ! -path "*/.lake/*" \
    ! -path "*/lake-packages/*" \
    ! -path "*/.git/*")
  if ! $found; then
    echo "  Clean -- no axiom/unsafe/native_decide/admit found."
  fi
  echo "=================================="
}
