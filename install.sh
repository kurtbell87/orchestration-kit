#!/usr/bin/env bash
# install.sh -- One-command setup for orchestration-kit.
#
# Modes:
#   Monorepo  — run from inside orchestration-kit/ (existing behavior).
#   Greenfield — run from a project root that contains orchestration-kit/ as a subdirectory.
#
# Detection: if CWD == script dir → monorepo, else → greenfield.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_DIR="$(pwd)"

CHECK_ONLY=0
SKIP_SMOKE=0
INSTALL_PYTHON=0
SETUP_MCP=1
WRITE_ENV_FILE=1
ENV_FILE_WRITTEN=0

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]            (monorepo mode)
       ./orchestration-kit/install.sh [options] (greenfield mode)

Options:
  --check-only        Run validation checks only (no chmod fixes, no smoke run).
  --skip-smoke        Skip tools/smoke-run.
  --install-python    Install requirements.txt if present.
  --no-mcp            Skip MCP token/env setup.
  --no-env-file       Do not write .orchestration-kit.env.
  -h, --help          Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --skip-smoke)
      SKIP_SMOKE=1
      shift
      ;;
    --install-python)
      INSTALL_PYTHON=1
      shift
      ;;
    --no-mcp)
      SETUP_MCP=0
      shift
      ;;
    --no-env-file)
      WRITE_ENV_FILE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

# ── Detect mode ──────────────────────────────────────────────────────────────

if [[ "$CURRENT_DIR" == "$SCRIPT_DIR" ]]; then
  MODE="monorepo"
  ROOT_DIR="$SCRIPT_DIR"
else
  MODE="greenfield"
  PROJECT_ROOT="$CURRENT_DIR"
  ORCHESTRATION_KIT_ROOT="$SCRIPT_DIR"
  # Compute the relative path from project root to orchestration-kit (for symlinks).
  MK_REL="$(python3 -c "import os; print(os.path.relpath('$ORCHESTRATION_KIT_ROOT', '$PROJECT_ROOT'))")"
fi

echo "[install] mode: $MODE"

# ── Detect git worktree (non-blocking warning) ───────────────────────────────

if [[ "$MODE" == "greenfield" ]]; then
  if git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree &>/dev/null; then
    _git_dir="$PROJECT_ROOT/.git"
    if [[ -f "$_git_dir" ]]; then
      echo ""
      echo "[install] WARNING: This appears to be a git worktree."
      echo "  For worktrees, 'tools/worktree-init' handles orchestration-kit/"
      echo "  symlink setup automatically. Consider using it instead:"
      echo "    orchestration-kit/tools/worktree-init"
      echo ""
      echo "  Continuing with standard greenfield install..."
      echo ""
    fi
  fi
fi

# ── Helper: create relative symlink (skip if target already exists) ──────────

link_rel() {
  local abs_target="$1" # absolute path to the real file
  local link_path="$2"  # absolute path where symlink is created
  if [[ -e "$link_path" || -L "$link_path" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$link_path")"
  local rel
  rel="$(python3 -c "import os; print(os.path.relpath('$abs_target', '$(dirname "$link_path")'))")"
  ln -s "$rel" "$link_path"
}

# Copy file if destination doesn't exist.
copy_if_missing() {
  local src="$1" dest="$2"
  if [[ ! -f "$dest" ]]; then
    cp "$src" "$dest"
    echo "[install]   created $(basename "$dest")"
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# MONOREPO MODE (original behaviour — unchanged)
# ══════════════════════════════════════════════════════════════════════════════

run_monorepo_install() {
  cd "$ROOT_DIR"

  BOOTSTRAP_CMD=("$ROOT_DIR/tools/bootstrap")
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    BOOTSTRAP_CMD+=("--check-only")
  fi
  if [[ "$SKIP_SMOKE" -eq 1 ]]; then
    BOOTSTRAP_CMD+=("--skip-smoke")
  fi
  if [[ "$INSTALL_PYTHON" -eq 1 ]]; then
    BOOTSTRAP_CMD+=("--install-python")
  fi

  printf '[install] running: %s\n' "${BOOTSTRAP_CMD[*]}"
  "${BOOTSTRAP_CMD[@]}"

  if [[ "$SETUP_MCP" -eq 1 && "$CHECK_ONLY" -eq 0 ]]; then
    mkdir -p "$ROOT_DIR/runs/mcp-logs"
    token="$("$ROOT_DIR/tools/mcp-token")"

    host="${ORCHESTRATION_KIT_MCP_HOST:-127.0.0.1}"
    port="${ORCHESTRATION_KIT_MCP_PORT:-7337}"
    max_output="${ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES:-32000}"

    if [[ "$WRITE_ENV_FILE" -eq 1 ]]; then
      env_file="$ROOT_DIR/.orchestration-kit.env"
      cat > "$env_file" <<ENV
export ORCHESTRATION_KIT_ROOT="$ROOT_DIR"
export ORCHESTRATION_KIT_MCP_HOST="$host"
export ORCHESTRATION_KIT_MCP_PORT="$port"
export ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES="$max_output"
export ORCHESTRATION_KIT_MCP_TOKEN="$token"

# Cloud preference: "local" (default), "cloud-first", or "cloud-always".
# "cloud-first"  — prefer cloud for jobs >10 min est. wall time (faster with more vCPUs).
# "cloud-always" — cloud for everything above the 10 min overhead floor.
# export ORCHESTRATION_KIT_CLOUD_PREFERENCE="local"
ENV
      chmod 600 "$env_file"
      echo "[install] wrote MCP env file: $env_file"
      echo "[install] next: source .orchestration-kit.env"
      ENV_FILE_WRITTEN=1
    else
      echo "[install] MCP configured (token stored in .mcp-token)."
      echo "[install] export these before running tools/mcp-serve:"
      echo "export ORCHESTRATION_KIT_ROOT=\"$ROOT_DIR\""
      echo "export ORCHESTRATION_KIT_MCP_HOST=\"$host\""
      echo "export ORCHESTRATION_KIT_MCP_PORT=\"$port\""
      echo "export ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES=\"$max_output\""
      echo "export ORCHESTRATION_KIT_MCP_TOKEN=\"\$(tools/mcp-token)\""
    fi
  fi

  echo "[install] ready"
  if [[ "$CHECK_ONLY" -eq 0 ]]; then
    echo "[install] quick start:"
    if [[ "$ENV_FILE_WRITTEN" -eq 1 ]]; then
      echo "  1. source .orchestration-kit.env"
      echo "  2. tools/mcp-serve"
      echo "  3. tools/kit --json research status"
    else
      echo "  1. tools/mcp-serve"
      echo "  2. tools/kit --json research status"
    fi
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# GREENFIELD MODE
# ══════════════════════════════════════════════════════════════════════════════

run_greenfield_install() {
  echo "[install] PROJECT_ROOT=$PROJECT_ROOT"
  echo "[install] ORCHESTRATION_KIT_ROOT=$ORCHESTRATION_KIT_ROOT"
  echo "[install] relative path: $MK_REL"

  # ── Step 1: Bootstrap orchestration-kit (validate + chmod, skip workspace seeding) ──
  BOOTSTRAP_CMD=("$ORCHESTRATION_KIT_ROOT/tools/bootstrap" "--greenfield")
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    BOOTSTRAP_CMD+=("--check-only")
  fi
  # Always skip smoke during bootstrap; we run our own at the end.
  BOOTSTRAP_CMD+=("--skip-smoke")
  if [[ "$INSTALL_PYTHON" -eq 1 ]]; then
    BOOTSTRAP_CMD+=("--install-python")
  fi

  printf '[install] running: %s\n' "${BOOTSTRAP_CMD[*]}"
  "${BOOTSTRAP_CMD[@]}"

  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "[install] check-only mode: skipping greenfield deployment"
    echo "[install] done"
    return 0
  fi

  # ── Step 1b: Ensure git repo + optional remote ────────────────────────────

  if ! git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree &>/dev/null; then
    echo "[install] initializing git repository"
    git -C "$PROJECT_ROOT" init --quiet
    git -C "$PROJECT_ROOT" commit --allow-empty -m "init" --quiet
  fi

  if ! git -C "$PROJECT_ROOT" remote get-url origin &>/dev/null; then
    echo ""
    echo "[install] No git remote 'origin' configured."
    echo "  The TDD kit's ship phase requires a remote to push to."
    echo ""
    read -rp "[install] Configure a git remote now? [y/N] " CONFIGURE_REMOTE
    if [[ "$CONFIGURE_REMOTE" =~ ^[Yy] ]]; then
      read -rp "[install] Remote URL: " REMOTE_URL
      if [[ -n "$REMOTE_URL" ]]; then
        git -C "$PROJECT_ROOT" remote add origin "$REMOTE_URL"
        echo "[install]   origin set to $REMOTE_URL"
      else
        echo "[install]   skipped (empty URL)"
      fi
    else
      echo "[install]   skipped — you can add one later: git remote add origin <url>"
    fi
  fi

  # ── Step 2: Deploy .claude/ ────────────────────────────────────────────────

  echo "[install] deploying .claude/ to project root"
  mkdir -p "$PROJECT_ROOT/.claude/hooks"
  mkdir -p "$PROJECT_ROOT/.claude/prompts"

  # settings.json — copy (not symlink) so the project can customise it.
  if [[ ! -f "$PROJECT_ROOT/.claude/settings.json" ]]; then
    cp "$ORCHESTRATION_KIT_ROOT/.claude/settings.json" "$PROJECT_ROOT/.claude/settings.json"
    echo "[install]   created .claude/settings.json"
  fi

  # Hook — symlink so git pull in orchestration-kit/ auto-updates.
  link_rel "$ORCHESTRATION_KIT_ROOT/.claude/hooks/pre-tool-use.sh" \
           "$PROJECT_ROOT/.claude/hooks/pre-tool-use.sh"

  # Prompts — symlink all 14 from the three kits.
  for prompt in "$ORCHESTRATION_KIT_ROOT"/tdd-kit/.claude/prompts/*.md; do
    [[ -f "$prompt" ]] || continue
    name="$(basename "$prompt")"
    link_rel "$prompt" "$PROJECT_ROOT/.claude/prompts/$name"
  done

  for prompt in "$ORCHESTRATION_KIT_ROOT"/research-kit/.claude/prompts/*.md; do
    [[ -f "$prompt" ]] || continue
    name="$(basename "$prompt")"
    link_rel "$prompt" "$PROJECT_ROOT/.claude/prompts/$name"
  done

  for prompt in "$ORCHESTRATION_KIT_ROOT"/mathematics-kit/.claude/prompts/*.md; do
    [[ -f "$prompt" ]] || continue
    name="$(basename "$prompt")"
    link_rel "$prompt" "$PROJECT_ROOT/.claude/prompts/$name"
  done

  echo "[install]   prompts linked"

  # ── Step 3: Symlink kit entry scripts + utility scripts ────────────────────

  KIT_DIR="$PROJECT_ROOT/.kit"
  mkdir -p "$KIT_DIR"

  echo "[install] linking kit scripts into .kit/"

  link_rel "$ORCHESTRATION_KIT_ROOT/tdd-kit/tdd.sh"               "$KIT_DIR/tdd.sh"
  link_rel "$ORCHESTRATION_KIT_ROOT/research-kit/experiment.sh"    "$KIT_DIR/experiment.sh"
  link_rel "$ORCHESTRATION_KIT_ROOT/mathematics-kit/math.sh"       "$KIT_DIR/math.sh"

  mkdir -p "$KIT_DIR/scripts"

  for script in "$ORCHESTRATION_KIT_ROOT"/tdd-kit/scripts/*; do
    [[ -f "$script" ]] || continue
    name="$(basename "$script")"
    link_rel "$script" "$KIT_DIR/scripts/$name"
  done

  for script in "$ORCHESTRATION_KIT_ROOT"/research-kit/scripts/*; do
    [[ -f "$script" ]] || continue
    name="$(basename "$script")"
    link_rel "$script" "$KIT_DIR/scripts/$name"
  done

  for script in "$ORCHESTRATION_KIT_ROOT"/mathematics-kit/scripts/*; do
    [[ -f "$script" ]] || continue
    name="$(basename "$script")"
    link_rel "$script" "$KIT_DIR/scripts/$name"
  done

  echo "[install]   utility scripts linked"

  # ── Step 4: Copy state templates + create working dirs ─────────────────────

  echo "[install] seeding state files and working dirs into .kit/"

  # TDD
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/tdd-kit/templates/LAST_TOUCH.md" "$KIT_DIR/LAST_TOUCH.md"
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/tdd-kit/templates/PRD.md"        "$KIT_DIR/PRD.md"
  mkdir -p "$KIT_DIR/docs"

  # Research
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/research-kit/templates/RESEARCH_LOG.md"  "$KIT_DIR/RESEARCH_LOG.md"
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/research-kit/templates/QUESTIONS.md"      "$KIT_DIR/QUESTIONS.md"
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/research-kit/templates/DOMAIN_PRIORS.md" "$KIT_DIR/DOMAIN_PRIORS.md"
  mkdir -p "$KIT_DIR/experiments" "$KIT_DIR/results" "$KIT_DIR/handoffs/completed"

  # Math
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/mathematics-kit/templates/CONSTRUCTIONS.md"     "$KIT_DIR/CONSTRUCTIONS.md"
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/mathematics-kit/templates/CONSTRUCTION_LOG.md"  "$KIT_DIR/CONSTRUCTION_LOG.md"
  copy_if_missing "$ORCHESTRATION_KIT_ROOT/mathematics-kit/templates/DOMAIN_CONTEXT.md"    "$KIT_DIR/DOMAIN_CONTEXT.md"
  mkdir -p "$KIT_DIR/specs"

  echo "[install]   state files seeded"

  # ── Step 4b: Create .gitignore ─────────────────────────────────────────────

  if [[ ! -f "$PROJECT_ROOT/.gitignore" ]]; then
    cat > "$PROJECT_ROOT/.gitignore" <<'GITIGNORE'
# Secrets & credentials
.env
.env.*
*.pem
*.key
credentials.json

# Orchestration-kit runtime
.orchestration-kit.env
.mcp-token

# OS files
.DS_Store
Thumbs.db

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
*.egg-info/

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# Build artifacts
build/
dist/
*.o
*.a
*.so
*.dylib

# Kit runtime artifacts
.kit/scripts/
.kit/tdd.sh
.kit/experiment.sh
.kit/math.sh
GITIGNORE
    echo "[install]   created .gitignore"
  else
    echo "[install]   .gitignore already exists, skipping"
  fi

  # ── Step 4c: Print LLM instructions for generated state files ──────────────

  echo ""
  echo "[install] === State File Instructions ==="
  echo ""
  echo "  .kit/PRD.md"
  echo "    → Fill in: project goal, success criteria, build phases, data contract."
  echo "    → Why: The TDD kit reads this to understand what to build and test."
  echo ""
  echo "  .kit/LAST_TOUCH.md"
  echo "    → Fill in: current phase, what was just done, what to do next."
  echo "    → Why: Continuity across sessions — every agent reads this first."
  echo ""
  echo "  .kit/DOMAIN_PRIORS.md"
  echo "    → Fill in: domain knowledge the research kit should assume (schemas, constants, gotchas)."
  echo "    → Why: Prevents the research agent from re-discovering known domain facts."
  echo ""
  echo "  .kit/RESEARCH_LOG.md, .kit/QUESTIONS.md"
  echo "    → Leave empty — auto-populated by the research kit's cycle."
  echo ""
  echo "  .kit/CONSTRUCTIONS.md, .kit/CONSTRUCTION_LOG.md, .kit/DOMAIN_CONTEXT.md"
  echo "    → Leave empty unless using the math kit."
  echo ""

  # ── Step 5: Generate combined CLAUDE.md ────────────────────────────────────

  if [[ ! -f "$PROJECT_ROOT/CLAUDE.md" ]]; then
    cp "$ORCHESTRATION_KIT_ROOT/templates/greenfield-CLAUDE.md" "$PROJECT_ROOT/CLAUDE.md"
    echo "[install]   created CLAUDE.md"
  else
    echo "[install]   CLAUDE.md already exists, skipping"
  fi

  # ── Step 6: Write .orchestration-kit.env ──────────────────────────────────────────

  if [[ "$WRITE_ENV_FILE" -eq 1 ]]; then
    env_file="$PROJECT_ROOT/.orchestration-kit.env"

    # MCP token (optional — only if tools/mcp-token exists and MCP not disabled).
    mcp_vars=""
    if [[ "$SETUP_MCP" -eq 1 ]]; then
      mkdir -p "$ORCHESTRATION_KIT_ROOT/runs/mcp-logs"
      token="$("$ORCHESTRATION_KIT_ROOT/tools/mcp-token")"
      host="${ORCHESTRATION_KIT_MCP_HOST:-127.0.0.1}"
      port="${ORCHESTRATION_KIT_MCP_PORT:-7337}"
      max_output="${ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES:-32000}"
      mcp_vars="$(cat <<MCPENV
export ORCHESTRATION_KIT_MCP_HOST="$host"
export ORCHESTRATION_KIT_MCP_PORT="$port"
export ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES="$max_output"
export ORCHESTRATION_KIT_MCP_TOKEN="$token"
MCPENV
)"
    fi

    cat > "$env_file" <<ENV
export PROJECT_ROOT="$PROJECT_ROOT"
export ORCHESTRATION_KIT_ROOT="$ORCHESTRATION_KIT_ROOT"
export KIT_STATE_DIR=".kit"
${mcp_vars}

# Cloud preference: "local" (default), "cloud-first", or "cloud-always".
# "cloud-first"  — prefer cloud for jobs >10 min est. wall time (faster with more vCPUs).
# "cloud-always" — cloud for everything above the 10 min overhead floor.
# export ORCHESTRATION_KIT_CLOUD_PREFERENCE="local"
ENV
    chmod 600 "$env_file"
    echo "[install] wrote env file: $env_file"
    ENV_FILE_WRITTEN=1
  fi

  # ── Step 7: Smoke test ─────────────────────────────────────────────────────

  if [[ "$SKIP_SMOKE" -eq 0 ]]; then
    echo "[install] running smoke test (greenfield)"
    export PROJECT_ROOT
    export ORCHESTRATION_KIT_ROOT
    "$ORCHESTRATION_KIT_ROOT/tools/smoke-run"
  else
    echo "[install] smoke test skipped"
  fi

  echo "[install] ready (greenfield)"
  echo "[install] quick start:"
  if [[ "$ENV_FILE_WRITTEN" -eq 1 ]]; then
    echo "  1. source .orchestration-kit.env"
    echo "  2. $MK_REL/tools/kit --json research status"
  else
    echo "  1. export PROJECT_ROOT=\"$PROJECT_ROOT\""
    echo "  2. export ORCHESTRATION_KIT_ROOT=\"$ORCHESTRATION_KIT_ROOT\""
    echo "  3. $MK_REL/tools/kit --json research status"
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════════════

if [[ "$MODE" == "greenfield" ]]; then
  run_greenfield_install
else
  run_monorepo_install
fi
