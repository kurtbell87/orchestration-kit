#!/usr/bin/env bash
# .claude/hooks/pre-tool-use.sh
#
# Blocks inappropriate file modifications during research experiment phases.
# The EXP_PHASE env var is set by experiment.sh.
#
# Hook receives tool name and input via environment variables:
#   CLAUDE_TOOL_NAME  -- the tool being invoked (Edit, Write, Bash, etc.)
#   CLAUDE_TOOL_INPUT -- JSON string of the tool's input parameters

set -euo pipefail

# In orchestration-kit orchestration, delegate to the root dispatcher so global
# read-budget policies apply in addition to native research protections.
if [[ -n "${ORCHESTRATION_KIT_ROOT:-}" ]] && [[ "${MASTER_HOOK_ACTIVE:-0}" != "1" ]]; then
  MASTER_HOOK_PATH="${ORCHESTRATION_KIT_ROOT}/.claude/hooks/pre-tool-use.sh"
  if [[ -f "$MASTER_HOOK_PATH" ]]; then
    MASTER_HOOK_ACTIVE=1 "$MASTER_HOOK_PATH"
    exit $?
  fi
fi

PHASE="${EXP_PHASE:-}"

# Only enforce during run, read, and synthesize phases.
if [[ "$PHASE" != "run" && "$PHASE" != "read" && "$PHASE" != "synthesize" ]]; then
  exit 0
fi

INPUT="$CLAUDE_TOOL_INPUT"

# ── Common: Block permission escalation in all enforced phases ──
if [[ "$CLAUDE_TOOL_NAME" == "Bash" ]]; then
  # Block permission/ownership changes
  if echo "$INPUT" | grep -qEi '(chmod|chown|sudo|doas|install\s)'; then
    echo "BLOCKED: Permission-modifying commands are not allowed during ${PHASE^^} phase." >&2
    echo "   File permissions are enforced by the experiment orchestrator." >&2
    exit 1
  fi

  # Block git commands that could revert protected files
  if echo "$INPUT" | grep -qEi 'git\s+(checkout|restore|stash|reset)\s'; then
    echo "BLOCKED: Git revert commands are not allowed during ${PHASE^^} phase." >&2
    echo "   Experiment specs and results must not be reverted or modified." >&2
    exit 1
  fi
fi

# ── RUN phase: Protect experiment specs and previous results ──
if [[ "$PHASE" == "run" ]]; then

  # Patterns that identify experiment spec files
  SPEC_PATTERNS='(experiments/.*\.md|/experiments/.*\.md)'
  # Patterns that identify previous result files (not the current experiment's output dir)
  PREV_RESULTS_PATTERNS='(results/exp-.*/(metrics|analysis|spec)\.(json|md|csv))'

  if [[ "$CLAUDE_TOOL_NAME" == "Bash" ]]; then
    # Block direct writes to spec files via bash
    if echo "$INPUT" | grep -qE "$SPEC_PATTERNS"; then
      if echo "$INPUT" | grep -qEi '(>|tee|sed\s+-i|awk.*-i|perl\s+-[pi]|mv\s|cp\s.*>|rm\s)'; then
        echo "BLOCKED: Cannot modify experiment specs via shell during RUN phase." >&2
        echo "   The experiment spec is your contract. Implement to satisfy it." >&2
        exit 1
      fi
    fi

    # Block writes to RESEARCH_LOG.md
    if echo "$INPUT" | grep -qE 'RESEARCH_LOG\.md'; then
      if echo "$INPUT" | grep -qEi '(>|tee|sed\s+-i|awk.*-i|perl\s+-[pi])'; then
        echo "BLOCKED: Cannot modify RESEARCH_LOG.md during RUN phase." >&2
        echo "   The READ agent updates the research log." >&2
        exit 1
      fi
    fi
  fi

  # Block direct file writes to experiment specs
  if [[ "$CLAUDE_TOOL_NAME" == "Edit" || "$CLAUDE_TOOL_NAME" == "Write" || "$CLAUDE_TOOL_NAME" == "MultiEdit" ]]; then
    if echo "$INPUT" | grep -qE "$SPEC_PATTERNS"; then
      echo "BLOCKED: Cannot edit experiment specs during RUN phase." >&2
      echo "   The spec is your contract. Implement and execute it as designed." >&2
      exit 1
    fi
    if echo "$INPUT" | grep -qE 'RESEARCH_LOG\.md'; then
      echo "BLOCKED: Cannot modify RESEARCH_LOG.md during RUN phase." >&2
      echo "   The READ agent updates the research log." >&2
      exit 1
    fi
  fi
fi

# ── READ phase: Protect metrics and source code ──
if [[ "$PHASE" == "read" ]]; then

  METRICS_PATTERNS='(results/.*metrics\.(json|csv)|results/.*config\.json)'
  SOURCE_PATTERNS='(\.py$|\.cpp$|\.cu$|\.h$|\.hpp$|\.c$|\.rs$|\.jl$|\.ts$|\.js$|\.yaml$|\.yml$|\.toml$)'
  # Allow writing to analysis.md and RESEARCH_LOG.md (the READ agent's job)

  if [[ "$CLAUDE_TOOL_NAME" == "Bash" ]]; then
    # Block writes to metrics files
    if echo "$INPUT" | grep -qE "$METRICS_PATTERNS"; then
      if echo "$INPUT" | grep -qEi '(>|tee|sed\s+-i|awk.*-i|perl\s+-[pi]|mv\s|cp\s.*>|rm\s)'; then
        echo "BLOCKED: Cannot modify metrics files during READ phase." >&2
        echo "   The numbers are sacred. Analyze them as-is." >&2
        exit 1
      fi
    fi

    # Block running training/evaluation (no re-running experiments)
    if echo "$INPUT" | grep -qEi '(python\s+train|python\s+eval|python\s+run|\.\/train|\.\/eval)'; then
      echo "BLOCKED: Cannot run training or evaluation during READ phase." >&2
      echo "   Analyze the existing results. If more data is needed, propose a follow-up experiment." >&2
      exit 1
    fi
  fi

  # Block direct writes to metrics files
  if [[ "$CLAUDE_TOOL_NAME" == "Edit" || "$CLAUDE_TOOL_NAME" == "Write" || "$CLAUDE_TOOL_NAME" == "MultiEdit" ]]; then
    if echo "$INPUT" | grep -qE "$METRICS_PATTERNS"; then
      echo "BLOCKED: Cannot edit metrics files during READ phase." >&2
      echo "   The numbers are sacred. Analyze them as-is." >&2
      exit 1
    fi
    # Block writes to source code during READ
    if echo "$INPUT" | grep -qE "$SOURCE_PATTERNS"; then
      echo "BLOCKED: Cannot modify source code during READ phase." >&2
      echo "   Your job is analysis, not implementation. Write to analysis.md instead." >&2
      exit 1
    fi
    # Block writes to experiment specs during READ
    if echo "$INPUT" | grep -qE '(experiments/.*\.md|/experiments/.*\.md)'; then
      echo "BLOCKED: Cannot modify experiment specs during READ phase." >&2
      echo "   You cannot retroactively change what success means." >&2
      exit 1
    fi
  fi
fi

# ── SYNTHESIZE phase: Only allow writes to SYNTHESIS.md ──
if [[ "$PHASE" == "synthesize" ]]; then

  if [[ "$CLAUDE_TOOL_NAME" == "Bash" ]]; then
    # Block all shell write operations
    if echo "$INPUT" | grep -qEi '(>|tee|sed\s+-i|awk.*-i|perl\s+-[pi]|mv\s|cp\s.*>|rm\s)'; then
      echo "BLOCKED: Shell write operations are not allowed during SYNTHESIZE phase." >&2
      echo "   Use the Write tool to create SYNTHESIS.md only." >&2
      exit 1
    fi
    # Block running training/evaluation
    if echo "$INPUT" | grep -qEi '(python\s+train|python\s+eval|python\s+run|\.\/train|\.\/eval)'; then
      echo "BLOCKED: Cannot run training or evaluation during SYNTHESIZE phase." >&2
      exit 1
    fi
  fi

  if [[ "$CLAUDE_TOOL_NAME" == "Edit" || "$CLAUDE_TOOL_NAME" == "Write" || "$CLAUDE_TOOL_NAME" == "MultiEdit" ]]; then
    if ! echo "$INPUT" | grep -qE 'SYNTHESIS\.md'; then
      echo "BLOCKED: During SYNTHESIZE phase, you may only write to SYNTHESIS.md." >&2
      echo "   All other files are read-only during synthesis." >&2
      exit 1
    fi
  fi
fi

exit 0
