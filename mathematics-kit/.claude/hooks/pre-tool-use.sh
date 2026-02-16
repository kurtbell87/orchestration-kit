#!/usr/bin/env bash
# .claude/hooks/pre-tool-use.sh
#
# Multi-phase enforcement for claude-mathematics-kit.
# MATH_PHASE env var is set by math.sh.
#
# Hook receives tool name and input via environment variables:
#   CLAUDE_TOOL_NAME  -- the tool being invoked (Edit, Write, Bash, etc.)
#   CLAUDE_TOOL_INPUT -- JSON string of the tool's input parameters

set -euo pipefail

PHASE="${MATH_PHASE:-}"

# If no phase is set, skip enforcement
if [[ -z "$PHASE" ]]; then
  exit 0
fi

INPUT="${CLAUDE_TOOL_INPUT:-}"
TOOL="${CLAUDE_TOOL_NAME:-}"

# ─────────────────────────────────────────────────────────────
# Layer 1: Universal blocks (all phases)
# ─────────────────────────────────────────────────────────────

# Block axiom, unsafe, native_decide, admit in any file write
if [[ "$TOOL" == "Edit" || "$TOOL" == "Write" || "$TOOL" == "MultiEdit" ]]; then
  if echo "$INPUT" | grep -qEi '\baxiom\b'; then
    echo "BLOCKED: 'axiom' declarations are forbidden in all phases." >&2
    echo "   Use standard Lean4 definitions and theorems only." >&2
    exit 1
  fi
  if echo "$INPUT" | grep -qEi '\bunsafe\b'; then
    echo "BLOCKED: 'unsafe' code is forbidden in all phases." >&2
    exit 1
  fi
  if echo "$INPUT" | grep -qEi '\bnative_decide\b'; then
    echo "BLOCKED: 'native_decide' is forbidden. Use 'decide' or proper tactics." >&2
    exit 1
  fi
  if echo "$INPUT" | grep -qEi '\badmit\b'; then
    echo "BLOCKED: 'admit' is forbidden. Use 'sorry' during FORMALIZE, real proofs during PROVE." >&2
    exit 1
  fi
fi

# Block permission escalation and dangerous git commands in Bash
if [[ "$TOOL" == "Bash" ]]; then
  if echo "$INPUT" | grep -qEi '(chmod|chown|sudo|doas)\b'; then
    echo "BLOCKED: Permission-modifying commands are not allowed." >&2
    echo "   File permissions are managed by math.sh." >&2
    exit 1
  fi
  if echo "$INPUT" | grep -qEi 'git\s+(revert|checkout|restore|stash|reset)\s'; then
    echo "BLOCKED: Destructive git commands are not allowed during math phases." >&2
    exit 1
  fi
  # R3.4: lake clean is forbidden — olean caches must be preserved
  if echo "$INPUT" | grep -qEi 'lake\s+clean'; then
    echo "BLOCKED: 'lake clean' is forbidden. Olean caches must be preserved." >&2
    exit 1
  fi
  # Block axiom/unsafe/native_decide/admit in bash commands (e.g., echo >> file.lean)
  if echo "$INPUT" | grep -qEi '(\.lean|lakefile)' && echo "$INPUT" | grep -qEi '\b(axiom|unsafe|native_decide|admit)\b'; then
    echo "BLOCKED: Cannot inject axiom/unsafe/native_decide/admit via shell." >&2
    exit 1
  fi
fi

# ─────────────────────────────────────────────────────────────
# Layer 2: Phase-specific enforcement
# ─────────────────────────────────────────────────────────────

# Patterns for file types
LEAN_PATTERN='\.lean"'
SPEC_PATTERN='(specs/|DOMAIN_CONTEXT\.md|construction-spec)'
CONSTRUCTION_DOC_PATTERN='(specs/construction-|specs/.*construction)'
LOG_PATTERN='(CONSTRUCTION_LOG\.md|REVISION\.md)'

case "$PHASE" in

  # ── SURVEY: Read-only. No file writes at all. ──
  survey)
    if [[ "$TOOL" == "Write" || "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      echo "BLOCKED: SURVEY phase is read-only. No file writes allowed." >&2
      echo "   Survey Mathlib and the codebase, report findings to stdout." >&2
      exit 1
    fi
    if [[ "$TOOL" == "Bash" ]]; then
      if echo "$INPUT" | grep -qEi '(>|>>|tee\s)'; then
        echo "BLOCKED: SURVEY phase is read-only. No file writes via shell." >&2
        exit 1
      fi
    fi
    ;;

  # ── SPECIFY: Write specs only. No .lean files. ──
  specify)
    if [[ "$TOOL" == "Write" || "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        echo "BLOCKED: SPECIFY phase cannot write .lean files." >&2
        echo "   Write specification documents only." >&2
        exit 1
      fi
    fi
    ;;

  # ── CONSTRUCT: Write markdown construction docs only. No .lean files. ──
  construct)
    if [[ "$TOOL" == "Write" || "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        echo "BLOCKED: CONSTRUCT phase cannot write .lean files." >&2
        echo "   Write construction documents (markdown) only." >&2
        exit 1
      fi
    fi
    ;;

  # ── FORMALIZE: Write .lean files, but ALL proof bodies must be sorry. ──
  formalize)
    # T1: Block raw lake build — force use of summarized wrapper
    if [[ "$TOOL" == "Bash" ]]; then
      if echo "$INPUT" | grep -qEi '^\s*lake\s+build' && ! echo "$INPUT" | grep -qEi 'lake-summarized'; then
        echo "BLOCKED: Use the build command from context (\$LAKE_BUILD) instead of raw 'lake build'." >&2
        echo "   The summarized wrapper saves context window tokens." >&2
        exit 1
      fi
    fi
    if [[ "$TOOL" == "Write" || "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      # Block writes to spec files
      if echo "$INPUT" | grep -qE "$SPEC_PATTERN"; then
        echo "BLOCKED: FORMALIZE phase cannot modify spec files." >&2
        exit 1
      fi
      # Block real proof tactics in .lean files
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        # Check for proof tactics (not sorry)
        # We allow: sorry, def, structure, inductive, theorem, lemma, instance,
        #           import, open, namespace, section, end, where, variable, #check, #print
        # We block: actual proof tactics being used as proof bodies
        if echo "$INPUT" | grep -qEi '\b(by\s+(simp|ring|omega|linarith|nlinarith|norm_num|exact|apply|intro|intros|cases|rcases|obtain|induction|rw|rfl|ext|funext|constructor|push_neg|by_contra|contradiction|trivial|assumption|refine|field_simp|positivity|gcongr|decide|norm_cast|aesop|tauto|Abel|group|show|have|calc|specialize|use|exists))\b'; then
          echo "BLOCKED: FORMALIZE phase must use 'sorry' for all proof bodies." >&2
          echo "   Write definitions and theorem statements only. Proofs come in PROVE phase." >&2
          exit 1
        fi
      fi
    fi
    ;;

  # ── PROVE: Edit-only for .lean files. Cannot touch signatures/definitions. ──
  prove)
    # T1: Block raw lake build — force use of summarized wrapper
    if [[ "$TOOL" == "Bash" ]]; then
      if echo "$INPUT" | grep -qEi '^\s*lake\s+build' && ! echo "$INPUT" | grep -qEi 'lake-summarized'; then
        echo "BLOCKED: Use the build command from context (\$LAKE_BUILD) instead of raw 'lake build'." >&2
        echo "   The summarized wrapper saves context window tokens." >&2
        exit 1
      fi
    fi
    if [[ "$TOOL" == "Write" ]]; then
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        echo "BLOCKED: PROVE phase uses Edit (not Write) for .lean files." >&2
        echo "   Replace sorry with proofs using Edit. Do not rewrite entire files." >&2
        exit 1
      fi
      # Block writes to spec files (OS-enforced too, but belt and suspenders)
      if echo "$INPUT" | grep -qE "$SPEC_PATTERN"; then
        echo "BLOCKED: Spec files are read-only during PROVE phase." >&2
        exit 1
      fi
      # R6.5: Block Write to DOMAIN_CONTEXT.md (only Edit append allowed)
      if echo "$INPUT" | grep -qE 'DOMAIN_CONTEXT\.md'; then
        echo "BLOCKED: Cannot overwrite DOMAIN_CONTEXT.md during PROVE. Use Edit to append." >&2
        exit 1
      fi
    fi
    if [[ "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      # Block edits to spec files (but NOT DOMAIN_CONTEXT.md — R6.5 allows append)
      if echo "$INPUT" | grep -qE "$SPEC_PATTERN"; then
        # Allow edits to DOMAIN_CONTEXT.md (for negative knowledge)
        if ! echo "$INPUT" | grep -qE 'DOMAIN_CONTEXT\.md'; then
          echo "BLOCKED: Spec files are read-only during PROVE phase." >&2
          exit 1
        fi
      fi
      # R2.4: Detect signature modification in .lean files
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        OLD_STR=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('old_string',''))" 2>/dev/null || true)
        if echo "$OLD_STR" | grep -qE '^\s*(theorem|lemma|def|structure|inductive)\s+' && \
           ! echo "$OLD_STR" | grep -qE '\bsorry\b'; then
          echo "BLOCKED: PROVE phase cannot modify theorem signatures or definitions." >&2
          echo "   Only proof bodies (sorry replacements) are allowed." >&2
          echo "   If the statement is wrong, create REVISION.md." >&2
          exit 1
        fi
      fi
    fi
    ;;

  # ── POLISH: Edit-only for .lean files. Cannot touch proofs or signatures. ──
  polish)
    if [[ "$TOOL" == "Write" ]]; then
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        # Allow Write to scratch/*.lean (for #lint)
        if ! echo "$INPUT" | grep -qE 'scratch/'; then
          echo "BLOCKED: POLISH phase uses Edit (not Write) for .lean files." >&2
          echo "   Exception: scratch/*.lean files are allowed." >&2
          exit 1
        fi
      fi
      # Block writes to spec files
      if echo "$INPUT" | grep -qE "$SPEC_PATTERN"; then
        echo "BLOCKED: POLISH phase cannot modify spec files." >&2
        exit 1
      fi
    fi
    if [[ "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      # Block edits to spec files (except DOMAIN_CONTEXT.md)
      if echo "$INPUT" | grep -qE "$SPEC_PATTERN"; then
        if ! echo "$INPUT" | grep -qE 'DOMAIN_CONTEXT\.md'; then
          echo "BLOCKED: POLISH phase cannot modify spec files." >&2
          exit 1
        fi
      fi
      # Detect signature modification in .lean files
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        OLD_STR=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('old_string',''))" 2>/dev/null || true)
        if echo "$OLD_STR" | grep -qE '^\s*(theorem|lemma|def|structure|inductive)\s+' && \
           ! echo "$OLD_STR" | grep -qE '\bsorry\b'; then
          echo "BLOCKED: POLISH phase cannot modify signatures or definitions." >&2
          echo "   Only documentation, formatting, and style changes are allowed." >&2
          exit 1
        fi
        # Block proof body modification (detect tactic keywords in old_string)
        if echo "$OLD_STR" | grep -qEi '\b(simp|ring|omega|linarith|nlinarith|norm_num|exact|apply|intro|intros|cases|rcases|obtain|induction|rw|rfl|ext|funext|constructor|push_neg|by_contra|contradiction|trivial|assumption|refine|field_simp|positivity|gcongr|decide|norm_cast|aesop|tauto|calc|specialize|use)\b'; then
          echo "BLOCKED: POLISH phase cannot modify proof bodies." >&2
          echo "   Only documentation, formatting, and style changes are allowed." >&2
          exit 1
        fi
      fi
    fi
    ;;

  # ── AUDIT: All .lean files locked. Can only write log and revision. ──
  audit)
    if [[ "$TOOL" == "Write" || "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
      # Block writes to .lean files
      if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
        echo "BLOCKED: AUDIT phase cannot modify .lean files (read-only)." >&2
        echo "   Write findings to CONSTRUCTION_LOG.md or REVISION.md only." >&2
        exit 1
      fi
      # Block writes to spec files
      if echo "$INPUT" | grep -qE "$SPEC_PATTERN"; then
        echo "BLOCKED: AUDIT phase cannot modify spec files." >&2
        exit 1
      fi
      # Allow only log and revision files
      if ! echo "$INPUT" | grep -qE "$LOG_PATTERN"; then
        echo "BLOCKED: AUDIT phase can only write CONSTRUCTION_LOG.md and REVISION.md." >&2
        exit 1
      fi
    fi
    ;;

  # ── LOG: No restrictions beyond universal blocks (git operations) ──
  log)
    ;;

esac

exit 0
