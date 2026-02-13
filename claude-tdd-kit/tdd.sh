#!/usr/bin/env bash
# tdd.sh -- TDD Workflow Orchestrator for Claude Code
#
# Usage:
#   ./tdd.sh red   <spec-file>     # Write tests from spec
#   ./tdd.sh green                  # Implement to pass tests
#   ./tdd.sh refactor               # Refactor while keeping tests green
#   ./tdd.sh ship  <spec-file>      # Commit, create PR, archive spec
#   ./tdd.sh full  <spec-file>      # Run all four phases sequentially
#   ./tdd.sh watch [phase] [--resolve]  # Live-tail or summarize a phase log
#
# Configure via environment variables or edit the defaults below.

set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Configuration -- edit these to match your project
# ──────────────────────────────────────────────────────────────
TEST_DIRS="${TEST_DIRS:-tests}"                  # Space-separated test directories
SRC_DIR="${SRC_DIR:-src}"                        # Source/implementation directory
PROMPT_DIR=".claude/prompts"                     # Phase-specific prompt files
HOOK_DIR=".claude/hooks"                         # Hook scripts

# Test file patterns (find-compatible)
TEST_FILE_PATTERNS=(
  -name "test_*.cpp"
  -o -name "test_*.py"
  -o -name "test_*.ts"
  -o -name "test_*.js"
  -o -name "*_test.cpp"
  -o -name "*_test.py"
  -o -name "*_test.ts"
  -o -name "*_test.js"
  -o -name "*.test.ts"
  -o -name "*.test.js"
  -o -name "*.spec.ts"
  -o -name "*.spec.js"
)

# Build & test commands -- override these for your project
# These are injected into the agent's context so it knows how to build/test.
BUILD_CMD="${BUILD_CMD:-echo 'Set BUILD_CMD for your project'}"
TEST_CMD="${TEST_CMD:-echo 'Set TEST_CMD for your project'}"

# Log directory -- per-project isolation under /tmp
# Uses repo basename + short hash of absolute path to prevent collisions
# between projects with the same name in different locations.
# Override with TDD_LOG_DIR env var if needed.
_project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
_project_name="$(basename "$_project_root")"
_project_hash="$(printf '%s' "$_project_root" | shasum -a 256 | cut -c1-6)"
TDD_LOG_DIR="${TDD_LOG_DIR:-/tmp/tdd-${_project_name}-${_project_hash}}"
export TDD_LOG_DIR
mkdir -p "$TDD_LOG_DIR"

# Post-cycle PR settings
# Set TDD_AUTO_MERGE=true to auto-merge the PR after creation
# Set TDD_DELETE_BRANCH=true to delete the feature branch after merge
TDD_AUTO_MERGE="${TDD_AUTO_MERGE:-false}"
TDD_DELETE_BRANCH="${TDD_DELETE_BRANCH:-false}"
TDD_BASE_BRANCH="${TDD_BASE_BRANCH:-main}"

# ──────────────────────────────────────────────────────────────
# Colors
# ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

find_test_files() {
  find . -type f \( "${TEST_FILE_PATTERNS[@]}" \) \
    ! -path "*/build/*" \
    ! -path "*/_deps/*" \
    ! -path "*/.git/*" \
    ! -path "*/__pycache__/*" \
    ! -path "*/.venv/*" \
    ! -path "*/venv/*" \
    ! -path "*/node_modules/*" \
    ! -path "*/dist/*"
}

lock_tests() {
  echo -e "${YELLOW}Locking test files...${NC}"
  local count=0
  while IFS= read -r f; do
    chmod 444 "$f"
    echo -e "   ${YELLOW}locked:${NC} $f"
    ((count++))
  done < <(find_test_files)

  # Lock test directories to prevent new file creation
  for d in $TEST_DIRS; do
    if [[ -d "$d" ]]; then
      find "$d" -type d | while IFS= read -r dir; do
        chmod 555 "$dir"
      done
    fi
  done

  echo -e "   ${YELLOW}$count file(s) locked${NC}"
}

unlock_tests() {
  echo -e "${BLUE}Unlocking test files...${NC}"
  # Unlock directories first
  for d in $TEST_DIRS; do
    if [[ -d "$d" ]]; then
      find "$d" -type d -exec chmod 755 {} \; 2>/dev/null || true
    fi
  done

  local count=0
  while IFS= read -r f; do
    chmod 644 "$f"
    ((count++))
  done < <(find_test_files)
  echo -e "   ${BLUE}$count file(s) unlocked${NC}"
}

ensure_hooks_executable() {
  if [[ -f "$HOOK_DIR/pre-tool-use.sh" ]]; then
    chmod +x "$HOOK_DIR/pre-tool-use.sh"
  fi
}

_phase_summary() {
  # Extract the final agent message from the stream-json log and print a
  # compact summary.  This keeps the orchestrator's context window lean —
  # the full transcript stays on disk at $TDD_LOG_DIR/{phase}.log.
  local phase="$1"
  local exit_code="$2"
  local log="$TDD_LOG_DIR/${phase}.log"

  local summary
  summary=$(tail -20 "$log" 2>/dev/null | python3 -c "
import json, sys
texts = []
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
        if d.get('type') == 'assistant':
            for c in d.get('message', {}).get('content', []):
                if c.get('type') == 'text':
                    texts.append(c['text'])
    except Exception:
        pass
if texts:
    print(texts[-1][:500])
" 2>/dev/null)

  if [[ -n "$summary" ]]; then
    echo -e "${YELLOW}[${phase}]${NC} $summary"
  fi
  echo -e "${YELLOW}[${phase}]${NC} Phase complete (exit: $exit_code). Log: $log"
  return "$exit_code"
}

# ──────────────────────────────────────────────────────────────
# Phase Runners
# ──────────────────────────────────────────────────────────────

run_red() {
  local spec_file="${1:?Usage: tdd.sh red <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${RED}======================================================${NC}"
  echo -e "${RED}  TDD RED PHASE -- Writing Failing Tests${NC}"
  echo -e "${RED}======================================================${NC}"
  echo -e "  Spec:  $spec_file"
  echo -e "  Tests: $TEST_DIRS"
  echo ""

  # Ensure tests are writable for the test author
  unlock_tests 2>/dev/null || true

  export TDD_PHASE="red"

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/tdd-red.md")

## Context
- Design spec path: $spec_file
- Test directories: $TEST_DIRS
- Build command: $BUILD_CMD
- Test command: $TEST_CMD
- Existing test files: $(find_test_files | wc -l | tr -d ' ') file(s) in $TEST_DIRS (use Glob to discover)

Read the spec file first, then write your tests." \
    --allowed-tools "Read,Write,Edit,Bash" \
    -p "Read the spec file first, then write your tests." \
    > "$TDD_LOG_DIR/red.log" 2>&1 || exit_code=$?

  _phase_summary "red" "$exit_code"
}

run_green() {
  # Verify tests exist
  local test_count
  test_count=$(find_test_files | wc -l | tr -d ' ')
  if [[ "$test_count" -eq 0 ]]; then
    echo -e "${RED}Error: No test files found. Run 'tdd.sh red <spec>' first.${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${GREEN}======================================================${NC}"
  echo -e "${GREEN}  TDD GREEN PHASE -- Implementing to Pass${NC}"
  echo -e "${GREEN}======================================================${NC}"
  echo -e "  Source: $SRC_DIR"
  echo -e "  Tests:  $test_count file(s)"
  echo ""

  # OS-level enforcement
  lock_tests
  ensure_hooks_executable

  export TDD_PHASE="green"

  # Unlock on exit regardless of success/failure
  trap unlock_tests EXIT

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/tdd-green.md")

## Context
- Source directory: $SRC_DIR
- Test directories: $TEST_DIRS (READ-ONLY -- do not attempt to modify)
- Build command: $BUILD_CMD
- Test command: ./scripts/test-summary.sh $TEST_CMD
- Full test log: $TDD_LOG_DIR/test-output.log (Read this file for detailed failure tracebacks)
- Test files: $(find_test_files | wc -l | tr -d ' ') file(s) in $TEST_DIRS (use Glob to discover)

Start by reading the test files to understand what's expected, then implement iteratively. Always use the test command above — it prints a compact summary. If you need full tracebacks, Read $TDD_LOG_DIR/test-output.log." \
    --allowed-tools "Read,Write,Edit,Bash" \
    -p "Read the test files to understand what's expected, then implement iteratively." \
    > "$TDD_LOG_DIR/green.log" 2>&1 || exit_code=$?

  _phase_summary "green" "$exit_code"
}

run_refactor() {
  echo ""
  echo -e "${BLUE}======================================================${NC}"
  echo -e "${BLUE}  TDD REFACTOR PHASE -- Improving Quality${NC}"
  echo -e "${BLUE}======================================================${NC}"
  echo -e "  Tests: $TEST_DIRS"
  echo ""

  # Ensure tests are unlocked (refactor may touch test readability)
  unlock_tests 2>/dev/null || true

  export TDD_PHASE="refactor"

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/tdd-refactor.md")

## Context
- Source directory: $SRC_DIR
- Test directories: $TEST_DIRS
- Build command: $BUILD_CMD
- Test command: ./scripts/test-summary.sh $TEST_CMD
- Full test log: $TDD_LOG_DIR/test-output.log (Read this file for detailed failure tracebacks)

Start by running the full test suite to confirm your green baseline, then refactor. Always use the test command above — it prints a compact summary." \
    --allowed-tools "Read,Write,Edit,Bash" \
    -p "Run the full test suite to confirm your green baseline, then refactor." \
    > "$TDD_LOG_DIR/refactor.log" 2>&1 || exit_code=$?

  _phase_summary "refactor" "$exit_code"
}

run_ship() {
  # Ship the results of a TDD cycle: commit, PR, optionally merge.
  # Called with the spec file path so we can derive the branch name.
  local spec_file="${1:?Usage: run_ship <spec-file>}"
  local feature_name
  feature_name="$(basename "$spec_file" .md)"
  local branch="tdd/${feature_name}"

  echo ""
  echo -e "${YELLOW}======================================================${NC}"
  echo -e "${YELLOW}  SHIPPING -- commit, PR, archive spec${NC}"
  echo -e "${YELLOW}======================================================${NC}"
  echo ""

  # Create feature branch
  git checkout -b "$branch" 2>/dev/null || git checkout "$branch"

  # Stage all changes and commit
  git add -A
  git commit -m "feat(${feature_name}): implement via TDD cycle

Spec: ${spec_file}
Red-green-refactor complete. All tests passing."

  # Push and create PR
  git push -u origin "$branch"

  local pr_url
  pr_url=$(gh pr create \
    --base "$TDD_BASE_BRANCH" \
    --title "feat(${feature_name}): TDD cycle complete" \
    --body "## TDD Cycle: ${feature_name}

**Spec:** \`${spec_file}\`

### Phases completed
- [x] RED — failing tests written
- [x] GREEN — implementation passes all tests
- [x] REFACTOR — code quality improved

### Spec archived
The spec file has been deleted from the working tree. It is preserved in this branch's git history.

---
*Generated by [claude-tdd-kit](https://github.com/kurtbell87/claude-tdd-kit)*")

  echo -e "  ${GREEN}PR created:${NC} $pr_url"

  # Auto-merge if configured
  if [[ "$TDD_AUTO_MERGE" == "true" ]]; then
    echo -e "  ${YELLOW}Auto-merging...${NC}"
    gh pr merge "$pr_url" --merge
    echo -e "  ${GREEN}Merged.${NC}"

    # Return to base branch
    git checkout "$TDD_BASE_BRANCH"
    git pull

    # Delete branch if configured
    if [[ "$TDD_DELETE_BRANCH" == "true" ]]; then
      git branch -d "$branch" 2>/dev/null || true
      gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/${branch}" 2>/dev/null || true
      echo -e "  ${GREEN}Branch deleted.${NC}"
    fi
  fi

  # Archive: delete the spec file (it's preserved in git history)
  if [[ -f "$spec_file" ]]; then
    rm "$spec_file"
    git add "$spec_file"
    git commit -m "chore: archive spec ${spec_file}"
    git push
    echo -e "  ${GREEN}Spec archived:${NC} $spec_file removed (preserved in git history)"
  fi

  echo ""
  echo -e "${GREEN}======================================================${NC}"
  echo -e "${GREEN}  Shipped! PR: $pr_url${NC}"
  echo -e "${GREEN}======================================================${NC}"
}

run_full() {
  local spec_file="${1:?Usage: tdd.sh full <spec-file>}"

  echo -e "${YELLOW}Running full TDD cycle: RED -> GREEN -> REFACTOR -> SHIP${NC}"
  echo ""

  run_red "$spec_file"

  echo ""
  echo -e "${YELLOW}--- Red phase complete. Starting green phase... ---${NC}"
  echo ""

  run_green

  echo ""
  echo -e "${YELLOW}--- Green phase complete. Starting refactor phase... ---${NC}"
  echo ""

  run_refactor

  echo ""
  echo -e "${YELLOW}--- Refactor phase complete. Shipping... ---${NC}"
  echo ""

  run_ship "$spec_file"

  echo ""
  echo -e "${YELLOW}======================================================${NC}"
  echo -e "${YELLOW}  Full TDD cycle complete!${NC}"
  echo -e "${YELLOW}======================================================${NC}"
}

# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

case "${1:-help}" in
  red)      shift; run_red "$@" ;;
  green)    run_green ;;
  refactor) run_refactor ;;
  ship)     shift; run_ship "$@" ;;
  full)     shift; run_full "$@" ;;
  watch)    shift; python3 scripts/tdd-watch.py "$@" ;;
  help|*)
    echo "Usage: tdd.sh <phase> [args]"
    echo ""
    echo "Phases:"
    echo "  red   <spec-file>   Write failing tests from design spec"
    echo "  green               Implement minimum code to pass tests"
    echo "  refactor            Refactor while keeping tests green"
    echo "  ship  <spec-file>   Commit, create PR, archive spec"
    echo "  full  <spec-file>   Run all four phases (red -> green -> refactor -> ship)"
    echo "  watch [phase]       Live-tail a running phase (--resolve for summary)"
    echo ""
    echo "Environment:"
    echo "  TEST_DIRS='tests'           Test directories (space-separated)"
    echo "  SRC_DIR='src'               Source directory"
    echo "  BUILD_CMD='make'            Build command"
    echo "  TEST_CMD='make test'        Test runner command"
    echo "  TDD_LOG_DIR='/tmp/tdd-<project>'  Log directory (auto-derived from repo name)"
    echo "  TDD_AUTO_MERGE='false'      Auto-merge PR after creation"
    echo "  TDD_DELETE_BRANCH='false'   Delete feature branch after merge"
    echo "  TDD_BASE_BRANCH='main'      Base branch for PRs"
    ;;
esac
