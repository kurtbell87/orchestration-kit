#!/usr/bin/env bash
# tdd-aliases.sh -- Source this in your shell
#
#   source /path/to/tdd-aliases.sh
#
# Then use:
#   tdd-red docs/my-feature.md
#   tdd-green
#   tdd-refactor
#   tdd-breadcrumbs docs/my-feature.md
#   tdd-ship docs/my-feature.md
#   tdd-full docs/my-feature.md
#   tddc-red docs/my-feature.md   # same phases via Codex backend
#   tdd-status
#   tdd-unlock

TDD_SCRIPT="./tdd.sh"

alias tdd-red='bash $TDD_SCRIPT red'
alias tdd-green='bash $TDD_SCRIPT green'
alias tdd-refactor='bash $TDD_SCRIPT refactor'
alias tdd-breadcrumbs='bash $TDD_SCRIPT breadcrumbs'
alias tdd-ship='bash $TDD_SCRIPT ship'
alias tdd-full='bash $TDD_SCRIPT full'
alias tddc-red='TDD_AGENT_BIN=codex bash $TDD_SCRIPT red'
alias tddc-green='TDD_AGENT_BIN=codex bash $TDD_SCRIPT green'
alias tddc-refactor='TDD_AGENT_BIN=codex bash $TDD_SCRIPT refactor'
alias tddc-breadcrumbs='TDD_AGENT_BIN=codex bash $TDD_SCRIPT breadcrumbs'
alias tddc-ship='TDD_AGENT_BIN=codex bash $TDD_SCRIPT ship'
alias tddc-full='TDD_AGENT_BIN=codex bash $TDD_SCRIPT full'

tdd-status() {
  echo "TDD Status"
  echo "==========="
  echo ""
  echo "Phase: ${TDD_PHASE:-not set}"
  echo "Agent backend: ${TDD_AGENT_BIN:-claude}"
  echo "Log dir: ${TDD_LOG_DIR:-/tmp/tdd-<project>}"
  echo ""
  echo "Test files:"
  find . -type f \( \
    -name "test_*.cpp" -o -name "test_*.py" -o -name "test_*.ts" -o -name "test_*.js" \
    -o -name "*_test.cpp" -o -name "*_test.py" -o -name "*_test.ts" -o -name "*_test.js" \
    -o -name "*.test.ts" -o -name "*.test.js" -o -name "*.spec.ts" -o -name "*.spec.js" \
  \) ! -path "*/build/*" ! -path "*/_deps/*" ! -path "*/.git/*" ! -path "*/node_modules/*" \
  | while read -r f; do
    if [[ ! -w "$f" ]]; then
      echo "  LOCKED  $f"
    else
      echo "  open    $f"
    fi
  done
  echo ""
  echo "Run 'tdd-red <spec>' to start a new cycle."
}

tdd-unlock() {
  echo "Emergency unlock -- restoring write permissions on all test files..."
  find . -type f \( \
    -name "test_*.cpp" -o -name "test_*.py" -o -name "test_*.ts" -o -name "test_*.js" \
    -o -name "*_test.cpp" -o -name "*_test.py" -o -name "*_test.ts" -o -name "*_test.js" \
    -o -name "*.test.ts" -o -name "*.test.js" -o -name "*.spec.ts" -o -name "*.spec.js" \
  \) ! -path "*/build/*" ! -path "*/_deps/*" ! -path "*/.git/*" ! -path "*/node_modules/*" \
    -exec chmod 644 {} \;
  # Unlock common test directory names
  for d in tests test python/tests src/tests __tests__ spec; do
    if [[ -d "$d" ]]; then
      find "$d" -type d -exec chmod 755 {} \; 2>/dev/null || true
    fi
  done
  echo "Done."
}
