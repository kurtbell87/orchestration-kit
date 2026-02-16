#!/usr/bin/env bash
# math.sh -- Formal Mathematics Workflow Orchestrator for Claude Code
#
# Usage:
#   ./math.sh survey    <spec-file>       # Survey Mathlib, domain, existing formalizations
#   ./math.sh specify   <spec-file>       # Write precise property requirements (no Lean4)
#   ./math.sh construct <spec-file>       # Informal math: definitions, theorems, proof sketches
#   ./math.sh formalize <spec-file>       # Write .lean defs + theorems (all sorry)
#   ./math.sh prove     <spec-file>       # Fill sorrys via lake build loop
#   ./math.sh polish    <spec-file>       # Mathlib style compliance (doc strings, formatting, #lint)
#   ./math.sh audit     <spec-file>       # Verify coverage, zero sorry/axiom
#   ./math.sh log       <spec-file>       # Git commit + PR
#   ./math.sh full      <spec-file>       # Run all 8 phases with revision loop
#   ./math.sh program   [--max-cycles N]  # Auto-advance through CONSTRUCTIONS.md
#   ./math.sh status                      # Show sorry count, axiom count, build status
#   ./math.sh watch     [phase]           # Live-tail a running phase log
#
# Configure via environment variables or edit the defaults below.

set -euo pipefail

# Allow nested Claude Code sessions (sub-agents spawned by orchestrator).
unset CLAUDECODE 2>/dev/null || true

# ──────────────────────────────────────────────────────────────
# Configuration -- edit these to match your project
# ──────────────────────────────────────────────────────────────

LEAN_DIR="${LEAN_DIR:-.}"                          # Root of Lean4 project
SPEC_DIR="${SPEC_DIR:-specs}"                      # Spec & construction docs
RESULTS_DIR="${RESULTS_DIR:-results}"              # Archived results
PROMPT_DIR=".claude/prompts"                       # Phase-specific prompt files
HOOK_DIR=".claude/hooks"                           # Hook scripts

# Lean4 build command (default: summarized wrapper that auto-condenses errors)
LAKE_BUILD="${LAKE_BUILD:-./scripts/lake-summarized.sh build}"

# Revision limits
MAX_REVISIONS="${MAX_REVISIONS:-3}"                # Max revision cycles before giving up

# Program mode
MAX_PROGRAM_CYCLES="${MAX_PROGRAM_CYCLES:-20}"
CONSTRUCTIONS_FILE="${CONSTRUCTIONS_FILE:-CONSTRUCTIONS.md}"

# Log directory -- per-project isolation under /tmp
_project_name="$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")"
MATH_LOG_DIR="${MATH_LOG_DIR:-/tmp/math-${_project_name}}"
export MATH_LOG_DIR
mkdir -p "$MATH_LOG_DIR"

# Post-cycle PR settings
MATH_AUTO_MERGE="${MATH_AUTO_MERGE:-false}"
MATH_DELETE_BRANCH="${MATH_DELETE_BRANCH:-false}"
MATH_BASE_BRANCH="${MATH_BASE_BRANCH:-main}"

# ──────────────────────────────────────────────────────────────
# Colors
# ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

# ── Phase timing (R4) ──

phase_start_time=""

start_phase_timer() {
  phase_start_time=$(date +%s)
}

end_phase_timer() {
  local phase_name="$1"
  if [[ -z "$phase_start_time" ]]; then return; fi
  local end_time
  end_time=$(date +%s)
  local elapsed=$((end_time - phase_start_time))
  echo -e "${CYAN}Phase $phase_name completed in ${elapsed}s${NC}"

  # Append to metrics file
  local metrics_file="${RESULTS_DIR}/metrics.jsonl"
  mkdir -p "$RESULTS_DIR"
  echo "{\"phase\":\"$phase_name\",\"elapsed_seconds\":$elapsed,\"timestamp\":\"$(date -Iseconds)\"}" >> "$metrics_file"
}

validate_build_env() {
  # R3.5: Verify lake can resolve paths before attempting builds
  if ! lake env printPaths >/dev/null 2>&1; then
    echo -e "${RED}Error: 'lake env printPaths' failed.${NC}" >&2
    echo -e "The Lean4 toolchain may be misconfigured." >&2
    echo -e "Try: elan default leanprover/lean4:stable" >&2
    return 1
  fi
  # T1: Ensure summarized wrapper exists; fall back to raw lake build if missing
  if [[ ! -x "./scripts/lake-summarized.sh" ]]; then
    echo -e "${YELLOW}Warning: lake-summarized.sh not found. Using raw lake build.${NC}" >&2
    LAKE_BUILD="lake build"
  fi
}

find_lean_files() {
  find "$LEAN_DIR" -type f -name "*.lean" \
    ! -path "*/.lake/*" \
    ! -path "*/lake-packages/*" \
    ! -path "*/.git/*" \
    ! -path "*/.elan/*" \
    2>/dev/null || true
}

count_sorrys() {
  local total=0
  while IFS= read -r f; do
    local count
    count=$(grep -c '\bsorry\b' "$f" 2>/dev/null) || count=0
    total=$((total + count))
  done < <(find_lean_files)
  echo "$total"
}

check_axioms() {
  local total=0
  while IFS= read -r f; do
    for pattern in '\baxiom\b' '\bunsafe\b' '\bnative_decide\b' '\badmit\b'; do
      local count
      count=$(grep -c "$pattern" "$f" 2>/dev/null) || count=0
      total=$((total + count))
    done
  done < <(find_lean_files)
  echo "$total"
}

extract_theorem_signatures() {
  # Extract all theorem/lemma names from .lean files
  while IFS= read -r f; do
    grep -nE '^\s*(theorem|lemma|instance)\s+' "$f" 2>/dev/null | while IFS= read -r line; do
      echo "$f:$line"
    done
  done < <(find_lean_files)
}

construction_id_from_spec() {
  local spec="$1"
  basename "$spec" .md
}

results_dir_for_spec() {
  local spec="$1"
  local cid
  cid="$(construction_id_from_spec "$spec")"
  echo "$RESULTS_DIR/$cid"
}

# ── Locking ──

lock_spec() {
  local spec="$1"
  if [[ -f "$spec" ]]; then
    chmod 444 "$spec"
    echo -e "   ${YELLOW}locked:${NC} $spec"
  fi
  # Also lock construction docs in specs/
  local cid
  cid="$(construction_id_from_spec "$spec")"
  for f in "$SPEC_DIR"/construction-"$cid"*; do
    if [[ -f "$f" ]]; then
      chmod 444 "$f"
      echo -e "   ${YELLOW}locked:${NC} $f"
    fi
  done
  # R6.5: DOMAIN_CONTEXT.md is NOT locked during PROVE.
  # The prover may append negative knowledge to the "DOES NOT APPLY" section.
  # Hook enforcement prevents modification of other sections.
  if [[ "${MATH_PHASE:-}" != "prove" ]] && [[ -f "DOMAIN_CONTEXT.md" ]]; then
    chmod 444 "DOMAIN_CONTEXT.md"
    echo -e "   ${YELLOW}locked:${NC} DOMAIN_CONTEXT.md"
  elif [[ -f "DOMAIN_CONTEXT.md" ]]; then
    echo -e "   ${BLUE}unlocked:${NC} DOMAIN_CONTEXT.md (append-only for DOES NOT APPLY)"
  fi
}

unlock_spec() {
  local spec="$1"
  if [[ -f "$spec" ]]; then
    chmod 644 "$spec"
    echo -e "   ${BLUE}unlocked:${NC} $spec"
  fi
  local cid
  cid="$(construction_id_from_spec "$spec")"
  for f in "$SPEC_DIR"/construction-"$cid"*; do
    if [[ -f "$f" ]]; then
      chmod 644 "$f"
      echo -e "   ${BLUE}unlocked:${NC} $f"
    fi
  done
  if [[ -f "DOMAIN_CONTEXT.md" ]]; then
    chmod 644 "DOMAIN_CONTEXT.md" 2>/dev/null || true
    echo -e "   ${BLUE}unlocked:${NC} DOMAIN_CONTEXT.md"
  fi
}

lock_lean_files() {
  echo -e "${YELLOW}Locking .lean files...${NC}"
  local count=0
  while IFS= read -r f; do
    chmod 444 "$f"
    echo -e "   ${YELLOW}locked:${NC} $f"
    ((count++))
  done < <(find_lean_files)
  echo -e "   ${YELLOW}$count file(s) locked${NC}"
}

unlock_lean_files() {
  echo -e "${BLUE}Unlocking .lean files...${NC}"
  local count=0
  while IFS= read -r f; do
    chmod 644 "$f"
    ((count++))
  done < <(find_lean_files)
  echo -e "   ${BLUE}$count file(s) unlocked${NC}"
}

unlock_all() {
  # Restore write permissions on everything
  while IFS= read -r f; do
    chmod 644 "$f" 2>/dev/null || true
  done < <(find_lean_files)
  find "$SPEC_DIR" -type f -name "*.md" -exec chmod 644 {} \; 2>/dev/null || true
  find "$SPEC_DIR" -type d -exec chmod 755 {} \; 2>/dev/null || true
  if [[ -f "DOMAIN_CONTEXT.md" ]]; then
    chmod 644 "DOMAIN_CONTEXT.md" 2>/dev/null || true
  fi
  echo -e "   ${BLUE}all files unlocked${NC}"
}

ensure_hooks_executable() {
  if [[ -f "$HOOK_DIR/pre-tool-use.sh" ]]; then
    chmod +x "$HOOK_DIR/pre-tool-use.sh"
  fi
}

# ──────────────────────────────────────────────────────────────
# Status
# ──────────────────────────────────────────────────────────────

run_status() {
  echo ""
  echo -e "${BOLD}Mathematics Construction Status${NC}"
  echo -e "${BOLD}===============================${NC}"
  echo ""

  # Lean files
  local lean_count
  lean_count=$(find_lean_files | wc -l | tr -d ' ')
  echo -e "${CYAN}Lean4 Files:${NC} $lean_count"

  # Sorry count
  local sorry_count
  sorry_count=$(count_sorrys)
  if [[ "$sorry_count" -eq 0 ]]; then
    echo -e "${CYAN}Sorry Count:${NC} ${GREEN}0${NC}"
  else
    echo -e "${CYAN}Sorry Count:${NC} ${RED}$sorry_count${NC}"
  fi

  # Axiom/unsafe count
  local axiom_count
  axiom_count=$(check_axioms)
  if [[ "$axiom_count" -eq 0 ]]; then
    echo -e "${CYAN}Axiom/Unsafe:${NC} ${GREEN}0${NC}"
  else
    echo -e "${CYAN}Axiom/Unsafe:${NC} ${RED}$axiom_count${NC}"
  fi

  # Lake build status
  echo -n -e "${CYAN}Lake Build:${NC}  "
  if eval "$LAKE_BUILD" 2>&1 | tail -1 | grep -qi 'error'; then
    echo -e "${RED}FAIL${NC}"
  else
    echo -e "${GREEN}PASS${NC}"
  fi

  # Lock states
  echo ""
  echo -e "${CYAN}Lock States:${NC}"
  local locked_specs=0 locked_lean=0
  while IFS= read -r f; do
    if [[ ! -w "$f" ]]; then
      locked_specs=$((locked_specs + 1))
    fi
  done < <(find "$SPEC_DIR" -type f -name "*.md" 2>/dev/null || true)
  while IFS= read -r f; do
    if [[ ! -w "$f" ]]; then
      locked_lean=$((locked_lean + 1))
    fi
  done < <(find_lean_files)
  echo -e "  Spec files locked:  $locked_specs"
  echo -e "  Lean files locked:  $locked_lean"

  # Current phase
  echo ""
  echo -e "${CYAN}Phase:${NC} ${MATH_PHASE:-not set}"

  # Theorem list
  echo ""
  echo -e "${CYAN}Theorems:${NC}"
  extract_theorem_signatures | while IFS= read -r sig; do
    echo "  $sig"
  done
  echo ""

  # Revision status
  if [[ -f "REVISION.md" ]]; then
    echo -e "${YELLOW}REVISION.md exists${NC} — revision cycle pending"
    local restart_from
    restart_from=$(grep -m1 '^## restart_from:' "REVISION.md" 2>/dev/null | sed 's/^## restart_from:[[:space:]]*//' || echo "unknown")
    echo -e "  Restart from: ${BOLD}$restart_from${NC}"
  fi

  # Constructions queue
  if [[ -f "$CONSTRUCTIONS_FILE" ]]; then
    echo ""
    echo -e "${CYAN}Construction Queue ($CONSTRUCTIONS_FILE):${NC}"
    grep -E '^\| P[0-9]' "$CONSTRUCTIONS_FILE" 2>/dev/null | while IFS= read -r line; do
      echo "  $line"
    done
  fi

  echo ""
}

# ──────────────────────────────────────────────────────────────
# Phase Summary (disk-only output)
# ──────────────────────────────────────────────────────────────

_phase_summary() {
  # Extract the final agent message from the stream-json log and print a
  # compact summary.  This keeps the orchestrator's context window lean —
  # the full transcript stays on disk at $MATH_LOG_DIR/{phase}.log.
  local phase="$1"
  local exit_code="$2"
  local log="$MATH_LOG_DIR/${phase}.log"

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

run_survey() {
  local spec_file="${1:?Usage: math.sh survey <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${CYAN}======================================================${NC}"
  echo -e "${CYAN}  SURVEY PHASE -- Domain & Mathlib Reconnaissance${NC}"
  echo -e "${CYAN}======================================================${NC}"
  echo -e "  Spec: $spec_file"
  echo ""

  export MATH_PHASE="survey"
  start_phase_timer

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-survey.md")

## Context
- Spec: $spec_file
- Mathlib source: .lake/packages/mathlib/ (use ./scripts/mathlib-search.sh for searches)
- Domain context: DOMAIN_CONTEXT.md

Read the spec file first, then survey Mathlib and existing formalizations." \
    --allowed-tools "Read,Bash,Glob,Grep" \
    -p "Read the spec file at $spec_file, then survey Mathlib and existing formalizations for this domain." \
    > "$MATH_LOG_DIR/survey.log" 2>&1 || exit_code=$?

  _phase_summary "survey" "$exit_code"
  end_phase_timer "survey"
}

run_specify() {
  local spec_file="${1:?Usage: math.sh specify <spec-file>}"

  echo ""
  echo -e "${BLUE}======================================================${NC}"
  echo -e "${BLUE}  SPECIFY PHASE -- Property Requirements${NC}"
  echo -e "${BLUE}======================================================${NC}"
  echo -e "  Spec: $spec_file"
  echo ""

  mkdir -p "$SPEC_DIR"

  export MATH_PHASE="specify"
  start_phase_timer
  ensure_hooks_executable

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-specify.md")

## Context
- Spec: $spec_file (write here)
- Spec directory: $SPEC_DIR
- Domain context: DOMAIN_CONTEXT.md

Write precise property requirements to $spec_file. Update DOMAIN_CONTEXT.md with Mathlib mappings." \
    --allowed-tools "Read,Write,Edit,Bash,Glob,Grep" \
    -p "Write precise mathematical property requirements to $spec_file" \
    > "$MATH_LOG_DIR/specify.log" 2>&1 || exit_code=$?

  _phase_summary "specify" "$exit_code"
  end_phase_timer "specify"
}

run_construct() {
  local spec_file="${1:?Usage: math.sh construct <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${MAGENTA}======================================================${NC}"
  echo -e "${MAGENTA}  CONSTRUCT PHASE -- Informal Mathematics${NC}"
  echo -e "${MAGENTA}======================================================${NC}"
  echo -e "  Spec: $spec_file"
  echo ""

  export MATH_PHASE="construct"
  start_phase_timer
  ensure_hooks_executable

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-construct.md")

## Context
- Spec: $spec_file (READ-ONLY)
- Spec directory: $SPEC_DIR (write construction docs here)
- Domain context: DOMAIN_CONTEXT.md

Read the spec, then write an informal construction document with definitions, theorems, and proof sketches." \
    --allowed-tools "Read,Write,Edit,Bash,Glob,Grep" \
    -p "Read the spec at $spec_file, then write a construction document with definitions, theorems, and proof sketches." \
    > "$MATH_LOG_DIR/construct.log" 2>&1 || exit_code=$?

  _phase_summary "construct" "$exit_code"
  end_phase_timer "construct"
}

run_formalize() {
  local spec_file="${1:?Usage: math.sh formalize <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  # R3.5: Validate build environment before agent starts
  validate_build_env || exit 1

  echo ""
  echo -e "${YELLOW}======================================================${NC}"
  echo -e "${YELLOW}  FORMALIZE PHASE -- Lean4 Definitions + Sorry Theorems${NC}"
  echo -e "${YELLOW}======================================================${NC}"
  echo -e "  Spec: $spec_file"
  echo -e "  Build: $LAKE_BUILD"
  echo ""

  export MATH_PHASE="formalize"
  start_phase_timer
  ensure_hooks_executable

  # T3: Start checkpoint monitor
  local checkpoint_pid=""
  if [[ -x "./scripts/context-checkpoint.sh" ]]; then
    ./scripts/context-checkpoint.sh "$MATH_PHASE" --threshold 40 &
    checkpoint_pid=$!
  fi

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-formalize.md")

## Context
- Spec: $spec_file (READ-ONLY)
- Build: $LAKE_BUILD
- Domain context: DOMAIN_CONTEXT.md

Read the spec and construction docs, then write .lean files with ALL proof bodies as sorry. Verify with $LAKE_BUILD." \
    --allowed-tools "Read,Write,Edit,Bash,Glob,Grep" \
    -p "Read the spec and construction docs, then write Lean4 files with definitions and sorry theorems. Verify with '$LAKE_BUILD'." \
    > "$MATH_LOG_DIR/formalize.log" 2>&1 || exit_code=$?

  # T3: Stop checkpoint monitor
  if [[ -n "$checkpoint_pid" ]]; then
    kill "$checkpoint_pid" 2>/dev/null || true
    wait "$checkpoint_pid" 2>/dev/null || true
  fi
  rm -f "$MATH_LOG_DIR/.checkpoint-requested"

  _phase_summary "formalize" "$exit_code"
  end_phase_timer "formalize"
}

run_prove_monolithic() {
  local spec_file="${1:?Usage: math.sh prove <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  # R3.5: Validate build environment before agent starts
  validate_build_env || exit 1

  local lean_count
  lean_count=$(find_lean_files | wc -l | tr -d ' ')
  if [[ "$lean_count" -eq 0 ]]; then
    echo -e "${RED}Error: No .lean files found. Run 'math.sh formalize' first.${NC}" >&2
    exit 1
  fi

  local sorry_count
  sorry_count=$(count_sorrys)

  echo ""
  echo -e "${GREEN}======================================================${NC}"
  echo -e "${GREEN}  PROVE PHASE -- Filling Sorrys${NC}"
  echo -e "${GREEN}======================================================${NC}"
  echo -e "  Spec:    $spec_file ${YELLOW}(READ-ONLY)${NC}"
  echo -e "  Sorrys:  $sorry_count"
  echo -e "  Build:   $LAKE_BUILD"
  echo ""

  # OS-level enforcement: lock specs
  lock_spec "$spec_file"
  ensure_hooks_executable

  export MATH_PHASE="prove"
  start_phase_timer

  # Unlock specs on exit
  trap "unlock_spec '$spec_file' 2>/dev/null || true" EXIT

  # T3: Start checkpoint monitor
  local checkpoint_pid=""
  if [[ -x "./scripts/context-checkpoint.sh" ]]; then
    ./scripts/context-checkpoint.sh "$MATH_PHASE" --threshold 40 &
    checkpoint_pid=$!
  fi

  # T3: Inject checkpoint context if resuming from a previous checkpoint
  local checkpoint_context=""
  if [[ -n "${PROVE_CHECKPOINT:-}" && -f "${PROVE_CHECKPOINT}" ]]; then
    checkpoint_context="
## Checkpoint Recovery
Continuation of a previous session. Previous summary:
$(cat "$PROVE_CHECKPOINT")
Start from where the previous session left off. Do NOT re-attempt theorems listed as failed."
  fi

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-prove.md")

## Context
- Spec: $spec_file (READ-ONLY)
- Build: $LAKE_BUILD
- Domain context: DOMAIN_CONTEXT.md (append-only: DOES NOT APPLY section)
- Sorrys: $sorry_count
${checkpoint_context}

Read the .lean files and spec. Replace sorrys with real proofs using Edit. Run '$LAKE_BUILD' after each change." \
    --allowed-tools "Read,Edit,Bash,Glob,Grep,Write" \
    -p "Fill in all sorry placeholders with real Lean4 proofs. Use Edit to replace sorry. Run '$LAKE_BUILD' after each change. Current sorry count: $sorry_count" \
    > "$MATH_LOG_DIR/prove.log" 2>&1 || exit_code=$?

  # T3: Stop checkpoint monitor
  if [[ -n "$checkpoint_pid" ]]; then
    kill "$checkpoint_pid" 2>/dev/null || true
    wait "$checkpoint_pid" 2>/dev/null || true
  fi

  _phase_summary "prove" "$exit_code"
  end_phase_timer "prove"

  # T3: Checkpoint recovery — restart if context pressure triggered checkpoint
  if [[ -f "$MATH_LOG_DIR/.checkpoint-requested" ]]; then
    echo -e "${YELLOW}CHECKPOINT: Agent checkpointed due to context pressure.${NC}"
    rm -f "$MATH_LOG_DIR/.checkpoint-requested"
    local remaining
    remaining=$(count_sorrys)

    if [[ "$remaining" -gt 0 && -f "results/prove-checkpoint.md" ]]; then
      echo -e "${YELLOW}Restarting prove phase with checkpoint context...${NC}"
      PROVE_CHECKPOINT="results/prove-checkpoint.md" run_prove_monolithic "$spec_file"
    fi
  fi
}

run_prove_chunked() {
  local spec_file="${1:?Usage: math.sh prove <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  validate_build_env || exit 1

  local lean_count
  lean_count=$(find_lean_files | wc -l | tr -d ' ')
  if [[ "$lean_count" -eq 0 ]]; then
    echo -e "${RED}Error: No .lean files found. Run 'math.sh formalize' first.${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${GREEN}======================================================${NC}"
  echo -e "${GREEN}  PROVE PHASE (CHUNKED) -- Per-Batch Sorry Elimination${NC}"
  echo -e "${GREEN}======================================================${NC}"
  echo -e "  Spec:    $spec_file ${YELLOW}(READ-ONLY)${NC}"
  echo -e "  Build:   $LAKE_BUILD"
  echo ""

  lock_spec "$spec_file"
  ensure_hooks_executable
  export MATH_PHASE="prove"
  start_phase_timer

  trap "unlock_spec '$spec_file' 2>/dev/null || true" EXIT

  # Step 1: Enumerate and batch sorrys
  echo -e "${CYAN}Enumerating sorrys...${NC}"
  local sorry_data
  sorry_data=$(./scripts/enumerate-sorrys.sh "$LEAN_DIR")
  local total_sorrys
  total_sorrys=$(echo "$sorry_data" | grep -c '.' || echo 0)
  echo -e "  Found ${BOLD}${total_sorrys}${NC} sorry(s)"

  if [[ "$total_sorrys" -eq 0 ]]; then
    echo -e "${GREEN}No sorrys to prove!${NC}"
    end_phase_timer "prove"
    return 0
  fi

  local batches
  batches=$(echo "$sorry_data" | python3 scripts/batch-sorrys.py --batch-size 5)
  local batch_count
  batch_count=$(echo "$batches" | grep -c '.' || echo 0)
  echo -e "  Organized into ${BOLD}${batch_count}${NC} batch(es)"
  echo ""

  # Step 2: Process each batch as a fresh sub-agent
  local batch_num=0

  while IFS= read -r batch_json; do
    batch_num=$((batch_num + 1))
    local batch_files
    batch_files=$(echo "$batch_json" | python3 -c "import json,sys; [print(f) for f in json.load(sys.stdin)['files']]")
    local batch_sorrys
    batch_sorrys=$(echo "$batch_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['sorrys']))")
    local batch_theorems
    batch_theorems=$(echo "$batch_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for s in d['sorrys']:
    print(f\"  - {s['theorem']} ({s['file']}:{s['line']})\")
")

    echo -e "${GREEN}── Batch $batch_num/$batch_count ($batch_sorrys sorrys) ──${NC}"
    echo "$batch_theorems"
    echo ""

    local file_list
    file_list=$(echo "$batch_files" | tr '\n' ', ')

    local exit_code=0
    claude \
      --output-format stream-json \
      --max-turns 25 \
      --append-system-prompt "$(cat "$PROMPT_DIR/math-prove.md")

## Context (Batch $batch_num/$batch_count)
- Spec: $spec_file (READ-ONLY)
- Target files: $file_list
- Domain context: DOMAIN_CONTEXT.md (append-only: DOES NOT APPLY section)
- Build: $LAKE_BUILD
- Sorrys in this batch: $batch_sorrys

## Batch Targets
$batch_theorems

Focus ONLY on the sorrys listed above. Do not attempt sorrys in other files/batches.
Replace each sorry with a real proof using Edit. Run the build command after each change.
If a theorem is unprovable after 5 attempts, record it in DOMAIN_CONTEXT.md and move on." \
      --allowed-tools "Read,Edit,Bash,Glob,Grep,Write" \
      -p "Prove the following sorrys (batch $batch_num/$batch_count). Focus only on these targets:
$batch_theorems

Replace each sorry with a real proof using Edit. Run the build command after each change." \
      > "$MATH_LOG_DIR/prove-batch-${batch_num}.log" 2>&1 || exit_code=$?

    _phase_summary "prove-batch-${batch_num}" "$exit_code"
    echo ""
  done <<< "$batches"

  # Step 3: Final verification build
  echo -e "${CYAN}Final verification build...${NC}"
  local final_sorrys
  final_sorrys=$(count_sorrys)
  echo -e "  Remaining sorrys: ${BOLD}${final_sorrys}${NC}"

  if [[ "$final_sorrys" -gt 0 ]]; then
    echo -e "${YELLOW}Not all sorrys eliminated. ${final_sorrys} remain.${NC}"
  else
    echo -e "${GREEN}All sorrys eliminated!${NC}"
  fi

  eval "$LAKE_BUILD" > "$MATH_LOG_DIR/prove-final-build.log" 2>&1 || true

  end_phase_timer "prove"
}

run_prove() {
  local spec_file="${1:?Usage: math.sh prove <spec-file>}"

  if [[ -x "./scripts/enumerate-sorrys.sh" ]] && [[ -f "scripts/batch-sorrys.py" ]]; then
    run_prove_chunked "$spec_file"
  else
    run_prove_monolithic "$spec_file"
  fi
}

run_polish() {
  local spec_file="${1:?Usage: math.sh polish <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  # R3.5: Validate build environment before agent starts
  validate_build_env || exit 1

  local lean_count
  lean_count=$(find_lean_files | wc -l | tr -d ' ')
  if [[ "$lean_count" -eq 0 ]]; then
    echo -e "${RED}Error: No .lean files found. Run 'math.sh prove' first.${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${MAGENTA}======================================================${NC}"
  echo -e "${MAGENTA}  POLISH PHASE -- Mathlib Style Compliance${NC}"
  echo -e "${MAGENTA}======================================================${NC}"
  echo -e "  Spec:    $spec_file ${YELLOW}(READ-ONLY)${NC}"
  echo -e "  Build:   $LAKE_BUILD"
  echo ""

  # OS-level enforcement: lock specs
  lock_spec "$spec_file"
  ensure_hooks_executable

  # Ensure scratch directory exists for #lint
  mkdir -p scratch

  export MATH_PHASE="polish"
  start_phase_timer

  # Unlock specs and clean scratch on exit
  trap "unlock_spec '$spec_file' 2>/dev/null || true; rm -f scratch/lint_check.lean 2>/dev/null || true" EXIT

  # T3: Start checkpoint monitor
  local checkpoint_pid=""
  if [[ -x "./scripts/context-checkpoint.sh" ]]; then
    ./scripts/context-checkpoint.sh "$MATH_PHASE" --threshold 40 &
    checkpoint_pid=$!
  fi

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-polish.md")

## Context
- Spec: $spec_file (READ-ONLY)
- Build: $LAKE_BUILD
- Construction log: CONSTRUCTION_LOG.md (write naming warnings here)

Read the .lean files, apply Mathlib style fixes, run #lint, flag naming issues. Run '$LAKE_BUILD' after each change." \
    --allowed-tools "Read,Edit,Write,Bash,Glob,Grep" \
    -p "Polish all .lean files for Mathlib style compliance: add doc strings, module docstrings, copyright headers, fix formatting. Run '$LAKE_BUILD' after each change. Run #lint via scratch/lint_check.lean. Flag naming issues in CONSTRUCTION_LOG.md." \
    > "$MATH_LOG_DIR/polish.log" 2>&1 || exit_code=$?

  # T3: Stop checkpoint monitor
  if [[ -n "$checkpoint_pid" ]]; then
    kill "$checkpoint_pid" 2>/dev/null || true
    wait "$checkpoint_pid" 2>/dev/null || true
  fi
  rm -f "$MATH_LOG_DIR/.checkpoint-requested"

  _phase_summary "polish" "$exit_code"
  end_phase_timer "polish"
}

run_audit() {
  local spec_file="${1:?Usage: math.sh audit <spec-file>}"

  if [[ ! -f "$spec_file" ]]; then
    echo -e "${RED}Error: Spec file not found: $spec_file${NC}" >&2
    exit 1
  fi

  echo ""
  echo -e "${RED}======================================================${NC}"
  echo -e "${RED}  AUDIT PHASE -- Verification & Coverage Check${NC}"
  echo -e "${RED}======================================================${NC}"
  echo -e "  Spec:  $spec_file"
  echo ""

  # OS-level enforcement: lock all .lean files
  lock_lean_files
  lock_spec "$spec_file"
  ensure_hooks_executable

  export MATH_PHASE="audit"
  start_phase_timer

  # Unlock on exit
  trap "unlock_all 2>/dev/null || true" EXIT

  local sorry_count
  sorry_count=$(count_sorrys)
  local axiom_count
  axiom_count=$(check_axioms)

  local exit_code=0
  claude \
    --output-format stream-json \
    --append-system-prompt "$(cat "$PROMPT_DIR/math-audit.md")

## Context
- Spec: $spec_file (READ-ONLY)
- Build: $LAKE_BUILD
- Sorrys: $sorry_count | Axioms: $axiom_count
- Construction log: CONSTRUCTION_LOG.md (WRITE to this)

Run '$LAKE_BUILD', audit all .lean files, check spec coverage. Write results to CONSTRUCTION_LOG.md." \
    --allowed-tools "Read,Write,Edit,Bash,Glob,Grep" \
    -p "Audit the formalization. Run '$LAKE_BUILD', check for sorry/axiom, verify spec coverage. Write results to CONSTRUCTION_LOG.md." \
    > "$MATH_LOG_DIR/audit.log" 2>&1 || exit_code=$?

  _phase_summary "audit" "$exit_code"
  end_phase_timer "audit"
}

run_log() {
  local spec_file="${1:?Usage: math.sh log <spec-file>}"
  local cid
  cid="$(construction_id_from_spec "$spec_file")"
  local results_path
  results_path="$(results_dir_for_spec "$spec_file")"

  echo ""
  echo -e "${MAGENTA}======================================================${NC}"
  echo -e "${MAGENTA}  LOG PHASE -- Committing & Creating PR${NC}"
  echo -e "${MAGENTA}======================================================${NC}"
  echo ""

  # Ensure everything is unlocked for commit
  unlock_all 2>/dev/null || true

  # Archive results
  mkdir -p "$results_path"
  cp "$spec_file" "$results_path/spec.md" 2>/dev/null || true
  if [[ -f "CONSTRUCTION_LOG.md" ]]; then
    cp "CONSTRUCTION_LOG.md" "$results_path/audit.md" 2>/dev/null || true
  fi

  # Copy .lean files to results for archival
  while IFS= read -r f; do
    local dest="$results_path/lean/$(basename "$f")"
    mkdir -p "$(dirname "$dest")"
    cp "$f" "$dest" 2>/dev/null || true
  done < <(find_lean_files)

  # Create feature branch
  local branch="math/${cid}"
  git checkout -b "$branch" 2>/dev/null || git checkout "$branch"

  # Stage and commit
  git add -A
  git commit -m "math(${cid}): formally verified construction

Spec: ${spec_file}
Results: ${results_path}/
Sorry count: $(count_sorrys)
Axiom count: $(check_axioms)

SURVEY -> SPECIFY -> CONSTRUCT -> FORMALIZE -> PROVE -> POLISH -> AUDIT complete."

  # Push and create PR (skip if no remote)
  if git remote get-url origin &>/dev/null; then
    git push -u origin "$branch"

    local pr_url
    pr_url=$(gh pr create \
      --base "$MATH_BASE_BRANCH" \
      --title "math(${cid}): formally verified construction" \
      --body "$(cat <<EOF
## Construction: ${cid}

**Spec:** \`${spec_file}\`
**Results:** \`${results_path}/\`

### Phases completed
- [x] SURVEY — Mathlib & domain surveyed
- [x] SPECIFY — property requirements written
- [x] CONSTRUCT — informal construction designed
- [x] FORMALIZE — Lean4 definitions + sorry theorems
- [x] PROVE — all sorrys eliminated
- [x] POLISH — Mathlib style compliance
- [x] AUDIT — verification & coverage check

### Verification
- \`lake build\`: $(if eval "$LAKE_BUILD" 2>&1 | tail -1 | grep -qi error; then echo "FAIL"; else echo "PASS"; fi)
- Sorry count: $(count_sorrys)
- Axiom count: $(check_axioms)

---
*Generated by [claude-mathematics-kit](https://github.com/kurtbell87/claude-mathematics-kit)*
EOF
)")

    echo -e "  ${GREEN}PR created:${NC} $pr_url"

    if [[ "$MATH_AUTO_MERGE" == "true" ]]; then
      echo -e "  ${YELLOW}Auto-merging...${NC}"
      gh pr merge "$pr_url" --merge
      echo -e "  ${GREEN}Merged.${NC}"
      git checkout "$MATH_BASE_BRANCH"
      git pull
      if [[ "$MATH_DELETE_BRANCH" == "true" ]]; then
        git branch -d "$branch" 2>/dev/null || true
        echo -e "  ${GREEN}Branch deleted.${NC}"
      fi
    fi

    echo ""
    echo -e "${GREEN}======================================================${NC}"
    echo -e "${GREEN}  Logged! PR: $pr_url${NC}"
    echo -e "${GREEN}======================================================${NC}"
  else
    echo -e "  ${YELLOW}No git remote 'origin' configured — skipping push and PR.${NC}"
    echo -e "  ${GREEN}Changes committed locally on branch:${NC} $branch"
  fi
}

# ──────────────────────────────────────────────────────────────
# Full Cycle (with revision loop)
# ──────────────────────────────────────────────────────────────

run_full() {
  local spec_file="${1:?Usage: math.sh full <spec-file>}"
  local revision_count=0

  # R2.5: Initialize revision metrics
  local revision_log="${RESULTS_DIR}/revision-metrics.json"
  mkdir -p "$RESULTS_DIR"
  echo '{"revisions":[]}' > "$revision_log"

  echo -e "${BOLD}Running full construction cycle: SURVEY -> SPECIFY -> CONSTRUCT -> FORMALIZE -> PROVE -> POLISH -> AUDIT -> LOG${NC}"
  echo ""

  # SURVEY
  run_survey "$spec_file"
  echo -e "\n${YELLOW}--- Survey complete. Specifying... ---${NC}\n"

  # SPECIFY
  run_specify "$spec_file"
  echo -e "\n${YELLOW}--- Specify complete. Constructing... ---${NC}\n"

  # Revision loop: CONSTRUCT -> FORMALIZE -> PROVE -> AUDIT
  while true; do
    # CONSTRUCT
    run_construct "$spec_file"
    echo -e "\n${YELLOW}--- Construct complete. Formalizing... ---${NC}\n"

    # FORMALIZE
    run_formalize "$spec_file"
    echo -e "\n${YELLOW}--- Formalize complete. Proving... ---${NC}\n"

    # PROVE
    run_prove "$spec_file"
    echo -e "\n${YELLOW}--- Prove complete. Polishing... ---${NC}\n"

    # POLISH
    run_polish "$spec_file"
    echo -e "\n${YELLOW}--- Polish complete. Auditing... ---${NC}\n"

    # AUDIT
    run_audit "$spec_file"

    # Check for revision request
    if [[ -f "REVISION.md" ]]; then
      revision_count=$((revision_count + 1))

      if (( revision_count >= MAX_REVISIONS )); then
        echo -e "\n${RED}Max revisions reached ($MAX_REVISIONS). Stopping.${NC}"
        echo -e "${RED}Manual intervention needed. See REVISION.md.${NC}"
        return 1
      fi

      local restart_from
      restart_from=$(grep -m1 'restart_from:' "REVISION.md" 2>/dev/null | sed 's/.*restart_from:[[:space:]]*//' || echo "CONSTRUCT")

      echo -e "\n${YELLOW}======================================================${NC}"
      echo -e "${YELLOW}  REVISION $revision_count/$MAX_REVISIONS -- Restarting from $restart_from${NC}"
      echo -e "${YELLOW}======================================================${NC}\n"

      # R2.5: Record revision metrics
      python3 -c "
import json, datetime
try:
    with open('$revision_log') as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {'revisions': []}
data['revisions'].append({
    'cycle': $revision_count,
    'restart_from': '$restart_from',
    'timestamp': datetime.datetime.now().isoformat(),
})
with open('$revision_log', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true

      # Archive the revision
      mkdir -p "$RESULTS_DIR/revisions"
      cp "REVISION.md" "$RESULTS_DIR/revisions/revision-${revision_count}.md"
      rm "REVISION.md"

      # Restart from the appropriate phase
      case "$restart_from" in
        CONSTRUCT|construct)
          continue
          ;;
        FORMALIZE|formalize)
          # Skip CONSTRUCT, go straight to FORMALIZE
          run_formalize "$spec_file"
          echo -e "\n${YELLOW}--- Formalize complete. Proving... ---${NC}\n"
          run_prove "$spec_file"
          echo -e "\n${YELLOW}--- Prove complete. Polishing... ---${NC}\n"
          run_polish "$spec_file"
          echo -e "\n${YELLOW}--- Polish complete. Auditing... ---${NC}\n"
          run_audit "$spec_file"
          # Check again for revision
          if [[ -f "REVISION.md" ]]; then
            continue
          fi
          break
          ;;
        *)
          echo -e "${RED}Unknown restart_from: $restart_from. Defaulting to CONSTRUCT.${NC}"
          continue
          ;;
      esac
    else
      break
    fi
  done

  echo -e "\n${YELLOW}--- Audit complete. Logging... ---${NC}\n"

  # LOG
  run_log "$spec_file"

  echo ""
  echo -e "${BOLD}${GREEN}======================================================${NC}"
  echo -e "${BOLD}${GREEN}  Full construction cycle complete!${NC}"
  if (( revision_count > 0 )); then
    echo -e "${BOLD}${GREEN}  Revisions: $revision_count${NC}"
  fi
  echo -e "${BOLD}${GREEN}======================================================${NC}"

  # R4.2: Print final metrics summary
  print_final_metrics
}

print_final_metrics() {
  local metrics_file="${RESULTS_DIR}/metrics.jsonl"
  if [[ ! -f "$metrics_file" ]]; then return; fi

  echo ""
  echo -e "${BOLD}Pipeline Metrics${NC}"
  echo "┌──────────────┬──────────┬───────────────────────────────┐"
  echo "│ Phase        │ Duration │ Timestamp                     │"
  echo "├──────────────┼──────────┼───────────────────────────────┤"
  python3 -c "
import json
with open('$metrics_file') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        phase = d['phase'].upper().ljust(12)
        elapsed = f\"{d['elapsed_seconds']}s\".rjust(8)
        ts = d.get('timestamp', '').ljust(29)
        print(f'│ {phase} │ {elapsed} │ {ts} │')
" 2>/dev/null
  echo "└──────────────┴──────────┴───────────────────────────────┘"

  # Also print lake build timing if available
  local lake_timing="${RESULTS_DIR}/lake-timing.jsonl"
  if [[ -f "$lake_timing" ]]; then
    local total_lake
    total_lake=$(python3 -c "
import json
total = 0
with open('$lake_timing') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        d = json.loads(line)
        total += d.get('lake_build_seconds', 0)
print(total)
" 2>/dev/null)
    if [[ -n "$total_lake" && "$total_lake" != "0" ]]; then
      echo -e "  Total lake build time: ${BOLD}${total_lake}s${NC}"
    fi
  fi
}

# ──────────────────────────────────────────────────────────────
# Program Mode
# ──────────────────────────────────────────────────────────────

select_next_construction() {
  # R5.2: Use topological sort to select the next non-blocked construction
  local result
  result=$(python3 scripts/resolve-deps.py "$CONSTRUCTIONS_FILE" --next 2>/dev/null) || return 0
  if [[ -z "$result" ]]; then
    return 0
  fi
  # Parse JSON output
  local spec_file construction status
  spec_file=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['spec'])")
  construction=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])")
  status=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  echo "${spec_file}|${construction}|${status}"
}

resolve_construction_order() {
  # R5.2: Get all actionable constructions in dependency order
  python3 scripts/resolve-deps.py "$CONSTRUCTIONS_FILE" 2>/dev/null
}

register_proved_theorem() {
  # R5.3: After audit passes, record proved theorem imports in DOMAIN_CONTEXT.md
  local spec_file="$1"
  local cid
  cid="$(construction_id_from_spec "$spec_file")"

  local lean_files
  lean_files=$(find_lean_files | grep -i "$cid" || true)

  if [[ -n "$lean_files" ]]; then
    echo "" >> DOMAIN_CONTEXT.md
    echo "### Proved: $cid" >> DOMAIN_CONTEXT.md
    echo "Import with:" >> DOMAIN_CONTEXT.md
    while IFS= read -r f; do
      local import_path
      import_path=$(echo "$f" | sed 's|/|.|g' | sed 's|\.lean$||' | sed 's|^\./||')
      echo "  \`import $import_path\`" >> DOMAIN_CONTEXT.md
    done <<< "$lean_files"
  fi
}

mark_downstream_blocked() {
  # R5.4: Mark all downstream dependents of a failed construction as Blocked
  local spec_file="$1"
  local cid
  cid="$(construction_id_from_spec "$spec_file")"

  # Find the priority for this spec_file
  local priority
  priority=$(grep -oP 'P\d+(?=.*'"$spec_file"')' "$CONSTRUCTIONS_FILE" 2>/dev/null | head -1)
  if [[ -z "$priority" ]]; then
    return 0
  fi

  local blocked_json
  blocked_json=$(python3 scripts/resolve-deps.py "$CONSTRUCTIONS_FILE" --mark-blocked "$priority" 2>/dev/null) || return 0

  local blocked_list
  blocked_list=$(echo "$blocked_json" | python3 -c "import json,sys; [print(p) for p in json.load(sys.stdin).get('blocked',[])]" 2>/dev/null) || return 0

  while IFS= read -r bp; do
    if [[ -n "$bp" ]]; then
      # Find the spec_file for this priority and mark it blocked
      local bspec
      bspec=$(python3 -c "
import re
with open('$CONSTRUCTIONS_FILE') as f:
    for line in f:
        cells = [c.strip() for c in line.split('|')]
        cells = [c for c in cells if c]
        if len(cells) >= 3 and cells[0] == '$bp':
            print(cells[2].strip('\`'))
            break
" 2>/dev/null)
      if [[ -n "$bspec" ]]; then
        update_construction_status "$bspec" "Blocked"
        echo -e "  ${YELLOW}blocked:${NC} $bp ($bspec) — dependency failed"
      fi
    fi
  done <<< "$blocked_list"
}

reset_failed_constructions() {
  # R5.5: Reset FAILED/BLOCKED entries to "Not started" for resume
  # Preserves negative knowledge in DOMAIN_CONTEXT.md
  python3 -c "
import re

with open('$CONSTRUCTIONS_FILE') as f:
    content = f.read()

lines = content.split('\n')
updated = []
for line in lines:
    cells = line.split('|')
    modified = False
    for i, cell in enumerate(cells):
        stripped = cell.strip().lower()
        if stripped in ('blocked', 'failed'):
            cells[i] = ' Not started '
            modified = True
            break
    if modified:
        line = '|'.join(cells)
    updated.append(line)

with open('$CONSTRUCTIONS_FILE', 'w') as f:
    f.write('\n'.join(updated))
" 2>/dev/null || true
}

update_construction_status() {
  local spec_file="$1"
  local new_status="$2"
  # Update the status in CONSTRUCTIONS.md for the matching spec file
  if [[ -f "$CONSTRUCTIONS_FILE" ]]; then
    python3 -c "
import re

with open('$CONSTRUCTIONS_FILE') as f:
    content = f.read()

lines = content.split('\n')
updated = []
for line in lines:
    if '${spec_file}' in line and '|' in line:
        # Replace the status column
        cells = line.split('|')
        for i, cell in enumerate(cells):
            stripped = cell.strip().lower()
            if stripped in ('not started', 'specified', 'constructed', 'formalized', 'proved', 'audited', 'revision', 'blocked'):
                cells[i] = ' ${new_status} '
                break
        line = '|'.join(cells)
    updated.append(line)

with open('$CONSTRUCTIONS_FILE', 'w') as f:
    f.write('\n'.join(updated))
" 2>/dev/null || true
  fi
}

run_program() {
  local max_cycles="$MAX_PROGRAM_CYCLES"
  local resume=false

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max-cycles) max_cycles="$2"; shift 2 ;;
      --resume)     resume=true; shift ;;
      *)            echo -e "${RED}Unknown argument: $1${NC}" >&2; return 1 ;;
    esac
  done

  # R5.5: Resume from first FAILED/BLOCKED construction
  if $resume; then
    echo -e "${YELLOW}Resuming from first FAILED/BLOCKED construction...${NC}"
    echo -e "${YELLOW}Negative knowledge in DOMAIN_CONTEXT.md is preserved.${NC}"
    reset_failed_constructions
  fi

  echo ""
  echo -e "${BOLD}${CYAN}======================================================${NC}"
  echo -e "${BOLD}${CYAN}  PROGRAM MODE -- Auto-advancing Constructions${NC}"
  echo -e "${BOLD}${CYAN}======================================================${NC}"
  echo -e "  Max cycles:      $max_cycles"
  echo -e "  Max revisions:   $MAX_REVISIONS per construction"
  echo -e "  Constructions:   $CONSTRUCTIONS_FILE"
  echo ""

  if [[ ! -f "$CONSTRUCTIONS_FILE" ]]; then
    echo -e "${RED}Error: $CONSTRUCTIONS_FILE not found.${NC}" >&2
    echo -e "Create it from the template: cp templates/CONSTRUCTIONS.md ." >&2
    exit 1
  fi

  # SIGINT trap
  trap 'echo -e "\n${YELLOW}Program loop interrupted.${NC}"; exit 130' INT

  local cycle=0
  while (( cycle < max_cycles )); do
    cycle=$((cycle + 1))

    echo ""
    echo -e "${CYAN}── Cycle $cycle/$max_cycles ──${NC}"

    # Check for revision
    if [[ -f "REVISION.md" ]]; then
      echo -e "${YELLOW}REVISION.md exists — handle revision before continuing.${NC}"
      return 1
    fi

    # Select next construction
    local next
    next=$(select_next_construction)
    if [[ -z "$next" ]]; then
      echo -e "${GREEN}All constructions complete or blocked!${NC}"
      break
    fi

    local spec_file construction status
    IFS='|' read -r spec_file construction status <<< "$next"

    echo -e "${BOLD}Next:${NC} $construction"
    echo -e "${BOLD}Spec:${NC} $spec_file"
    echo -e "${BOLD}Status:${NC} $status"

    # Run the appropriate phases based on current status
    case "$status" in
      "not started")
        update_construction_status "$spec_file" "Specified"
        if run_full "$spec_file"; then
          update_construction_status "$spec_file" "Audited"
          register_proved_theorem "$spec_file"
        else
          echo -e "${RED}Construction failed for $spec_file${NC}"
          update_construction_status "$spec_file" "Blocked"
          mark_downstream_blocked "$spec_file"
          continue
        fi
        ;;
      "specified")
        # Skip survey+specify, start from construct
        run_construct "$spec_file"
        run_formalize "$spec_file"
        run_prove "$spec_file"
        run_polish "$spec_file"
        run_audit "$spec_file"
        if [[ ! -f "REVISION.md" ]]; then
          run_log "$spec_file"
          update_construction_status "$spec_file" "Audited"
          register_proved_theorem "$spec_file"
        else
          update_construction_status "$spec_file" "Revision"
        fi
        ;;
      "constructed")
        run_formalize "$spec_file"
        run_prove "$spec_file"
        run_polish "$spec_file"
        run_audit "$spec_file"
        if [[ ! -f "REVISION.md" ]]; then
          run_log "$spec_file"
          update_construction_status "$spec_file" "Audited"
          register_proved_theorem "$spec_file"
        else
          update_construction_status "$spec_file" "Revision"
        fi
        ;;
      "formalized")
        run_prove "$spec_file"
        run_polish "$spec_file"
        run_audit "$spec_file"
        if [[ ! -f "REVISION.md" ]]; then
          run_log "$spec_file"
          update_construction_status "$spec_file" "Audited"
          register_proved_theorem "$spec_file"
        else
          update_construction_status "$spec_file" "Revision"
        fi
        ;;
      "revision")
        # Re-run full cycle
        update_construction_status "$spec_file" "Not started"
        if run_full "$spec_file"; then
          update_construction_status "$spec_file" "Audited"
          register_proved_theorem "$spec_file"
        else
          update_construction_status "$spec_file" "Blocked"
          mark_downstream_blocked "$spec_file"
          continue
        fi
        ;;
    esac

    echo -e "\n${GREEN}Cycle $cycle complete.${NC}"
  done

  echo ""
  echo -e "${BOLD}${GREEN}======================================================${NC}"
  echo -e "${BOLD}${GREEN}  Program mode complete. Cycles run: $cycle${NC}"
  echo -e "${BOLD}${GREEN}======================================================${NC}"
}

# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

case "${1:-help}" in
  survey)     shift; run_survey "$@" ;;
  specify)    shift; run_specify "$@" ;;
  construct)  shift; run_construct "$@" ;;
  formalize)  shift; run_formalize "$@" ;;
  prove)
    shift
    if [[ "${1:-}" == "--monolithic" ]]; then
      shift
      run_prove_monolithic "$@"
    else
      run_prove "$@"
    fi
    ;;
  polish)     shift; run_polish "$@" ;;
  audit)      shift; run_audit "$@" ;;
  log)        shift; run_log "$@" ;;
  full)       shift; run_full "$@" ;;
  program)    shift; run_program "$@" ;;
  status)     run_status ;;
  watch)      shift; python3 scripts/math-watch.py "$@" ;;
  help|*)
    echo "Usage: math.sh <phase> [args]"
    echo ""
    echo "Phases (run individually):"
    echo "  survey    <spec-file>    Survey Mathlib, domain, existing formalizations"
    echo "  specify   <spec-file>    Write precise property requirements (no Lean4)"
    echo "  construct <spec-file>    Informal math: definitions, theorems, proof sketches"
    echo "  formalize <spec-file>    Write .lean defs + theorem stmts (all sorry)"
    echo "  prove     <spec-file>    Fill sorrys via lake build loop (spec locked)"
    echo "  polish    <spec-file>    Mathlib style compliance (doc strings, formatting, #lint)"
    echo "  audit     <spec-file>    Verify coverage, zero sorry/axiom (.lean locked)"
    echo "  log       <spec-file>    Git commit + PR"
    echo ""
    echo "Pipelines:"
    echo "  full      <spec-file>    Run all 8 phases with revision loop"
    echo "  program   [--max-cycles N] [--resume]  Auto-advance through CONSTRUCTIONS.md"
    echo ""
    echo "Utilities:"
    echo "  status                   Show sorry count, axiom count, build status"
    echo "  watch     [phase]        Live-tail a running phase (--resolve for summary)"
    echo ""
    echo "Environment:"
    echo "  LEAN_DIR='.'             Lean4 project root"
    echo "  SPEC_DIR='specs'         Spec & construction docs directory"
    echo "  LAKE_BUILD='lake build'  Build command"
    echo "  MATH_LOG_DIR='/tmp/math-<project>'  Log directory (auto-derived from repo name)"
    echo "  MAX_REVISIONS='3'        Max revision cycles per construction"
    echo "  MAX_PROGRAM_CYCLES='20'  Max cycles in program mode"
    echo "  MATH_AUTO_MERGE='false'  Auto-merge PR after creation"
    echo "  MATH_BASE_BRANCH='main'  Base branch for PRs"
    ;;
esac
