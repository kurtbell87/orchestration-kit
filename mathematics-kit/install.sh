#!/usr/bin/env bash
# install.sh -- Bootstrap the Claude Mathematics Kit into your Lean4 project
#
# Usage:
#   cd your-lean-project && /path/to/claude-mathematics-kit/install.sh
#
# This script:
#   1. Copies kit files into your project root (safe, won't overwrite)
#   2. Checks for elan/lake toolchain
#   3. Initializes a Lean4+Mathlib project if needed
#   4. Creates specs/ and results/ directories
#   5. Handles existing CLAUDE.md

set -euo pipefail

# ── Parse flags ──
UPGRADE=false
if [[ "${1:-}" == "--upgrade" ]]; then UPGRADE=true; shift; fi

# IMPORTANT: This script NEVER runs `lake clean`. Olean caches are precious
# with Mathlib. If the user needs a clean build, that's a manual operation.

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# Determine where the kit files live (same directory as this script)
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Target is the current working directory
TARGET_DIR="$(pwd)"

echo ""
echo -e "${BOLD}Claude Mathematics Kit -- Installer${NC}"
if [[ "$UPGRADE" == "true" ]]; then
  echo -e "${YELLOW}Mode: UPGRADE (machinery overwritten, config backed up, state untouched)${NC}"
fi
echo -e "Installing into: ${BLUE}$TARGET_DIR${NC}"
echo ""

# ── Helper: state files -- never touched on upgrade ──
install_file() {
  local src="$1"
  local dest="$2"
  local dest_dir
  dest_dir="$(dirname "$dest")"

  mkdir -p "$dest_dir"

  if [[ -f "$dest" ]]; then
    echo -e "  ${YELLOW}exists:${NC}  $dest (skipped)"
  else
    cp "$src" "$dest"
    echo -e "  ${GREEN}created:${NC} $dest"
  fi
}

# ── Helper: copy file and make executable ──
install_executable() {
  local src="$1"
  local dest="$2"
  install_file "$src" "$dest"
  chmod +x "$dest"
}

# ── Helper: machinery files -- always overwritten on upgrade ──
upgrade_machinery() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ "$UPGRADE" == "true" ]]; then
    cp "$src" "$dest"
    echo -e "  ${GREEN}updated:${NC} $dest"
  elif [[ -f "$dest" ]]; then
    echo -e "  ${YELLOW}exists:${NC}  $dest (skipped)"
  else
    cp "$src" "$dest"
    echo -e "  ${GREEN}created:${NC} $dest"
  fi
}

upgrade_machinery_executable() {
  upgrade_machinery "$1" "$2"
  chmod +x "$2"
}

# ── Helper: config files -- backed up + overwritten on upgrade ──
upgrade_config() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ "$UPGRADE" == "true" && -f "$dest" ]]; then
    cp "$dest" "${dest}.bak"
    cp "$src" "$dest"
    echo -e "  ${GREEN}updated:${NC} $dest"
    echo -e "  ${YELLOW}backup:${NC}  ${dest}.bak (merge your config from here)"
  elif [[ -f "$dest" ]]; then
    echo -e "  ${YELLOW}exists:${NC}  $dest (skipped)"
  else
    cp "$src" "$dest"
    echo -e "  ${GREEN}created:${NC} $dest"
  fi
}

upgrade_config_executable() {
  upgrade_config "$1" "$2"
  chmod +x "$2"
}

# ──────────────────────────────────────────────────────────────
# Step 1: Check prerequisites
# ──────────────────────────────────────────────────────────────

echo -e "${BOLD}Prerequisites:${NC}"

# Check for elan / lake
HAVE_LAKE=true
if command -v lake &>/dev/null; then
  lake_version=$(lake --version 2>/dev/null | head -1 || echo "unknown")
  echo -e "  ${GREEN}found:${NC}   lake ($lake_version)"
elif command -v elan &>/dev/null; then
  elan_version=$(elan --version 2>/dev/null | head -1 || echo "unknown")
  echo -e "  ${GREEN}found:${NC}   elan ($elan_version)"
  echo -e "  ${YELLOW}note:${NC}    lake should be available via elan"
else
  HAVE_LAKE=false
  echo -e "  ${YELLOW}missing:${NC} lake / elan (Lean4 project setup will be skipped)"
  echo -e "  Install elan later: ${BOLD}curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh${NC}"
fi

# Check for claude
if command -v claude &>/dev/null; then
  echo -e "  ${GREEN}found:${NC}   claude"
else
  echo -e "  ${YELLOW}warning:${NC} claude CLI not found (needed to run phases)"
fi

# Check for gh
if command -v gh &>/dev/null; then
  echo -e "  ${GREEN}found:${NC}   gh"
else
  echo -e "  ${YELLOW}warning:${NC} gh CLI not found (needed for LOG phase PRs)"
fi

# ──────────────────────────────────────────────────────────────
# Step 2: Core kit files
# ──────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}Core orchestration:${NC}"
upgrade_config_executable    "$KIT_DIR/math.sh"          "$TARGET_DIR/math.sh"
upgrade_machinery            "$KIT_DIR/math-aliases.sh"  "$TARGET_DIR/math-aliases.sh"

echo ""
echo -e "${BOLD}Monitoring & utilities:${NC}"
upgrade_machinery            "$KIT_DIR/scripts/math-watch.py" "$TARGET_DIR/scripts/math-watch.py"
upgrade_machinery_executable "$KIT_DIR/scripts/mathlib-search.sh"       "$TARGET_DIR/scripts/mathlib-search.sh"
upgrade_machinery_executable "$KIT_DIR/scripts/lean-error-classify.sh"  "$TARGET_DIR/scripts/lean-error-classify.sh"
upgrade_machinery_executable "$KIT_DIR/scripts/lean-error-summarize.sh" "$TARGET_DIR/scripts/lean-error-summarize.sh"
upgrade_machinery_executable "$KIT_DIR/scripts/lake-timed.sh"           "$TARGET_DIR/scripts/lake-timed.sh"
upgrade_machinery_executable "$KIT_DIR/scripts/lake-summarized.sh"      "$TARGET_DIR/scripts/lake-summarized.sh"
upgrade_machinery_executable "$KIT_DIR/scripts/enumerate-sorrys.sh"    "$TARGET_DIR/scripts/enumerate-sorrys.sh"
upgrade_machinery_executable "$KIT_DIR/scripts/context-checkpoint.sh"  "$TARGET_DIR/scripts/context-checkpoint.sh"
upgrade_machinery            "$KIT_DIR/scripts/batch-sorrys.py"        "$TARGET_DIR/scripts/batch-sorrys.py"
upgrade_machinery_executable "$KIT_DIR/scripts/mathlib-lint.sh"        "$TARGET_DIR/scripts/mathlib-lint.sh"
upgrade_machinery            "$KIT_DIR/scripts/resolve-deps.py"        "$TARGET_DIR/scripts/resolve-deps.py"

# ──────────────────────────────────────────────────────────────
# Step 3: Claude hooks & prompts
# ──────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}Claude Code hooks & prompts:${NC}"
upgrade_config_executable    "$KIT_DIR/.claude/hooks/pre-tool-use.sh"     "$TARGET_DIR/.claude/hooks/pre-tool-use.sh"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-survey.md"    "$TARGET_DIR/.claude/prompts/math-survey.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-specify.md"   "$TARGET_DIR/.claude/prompts/math-specify.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-construct.md" "$TARGET_DIR/.claude/prompts/math-construct.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-formalize.md" "$TARGET_DIR/.claude/prompts/math-formalize.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-prove.md"     "$TARGET_DIR/.claude/prompts/math-prove.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-polish.md"    "$TARGET_DIR/.claude/prompts/math-polish.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/math-audit.md"     "$TARGET_DIR/.claude/prompts/math-audit.md"

# ── Settings (config) ──
if [[ "$UPGRADE" == "true" ]]; then
  upgrade_config "$KIT_DIR/.claude/settings.json" "$TARGET_DIR/.claude/settings.json"
elif [[ -f "$TARGET_DIR/.claude/settings.json" ]]; then
  echo ""
  echo -e "  ${YELLOW}exists:${NC}  .claude/settings.json"
  echo -e "  ${YELLOW}ACTION NEEDED:${NC} Merge the hook config manually. Add this to your settings.json:"
  echo ""
  echo '    "hooks": {'
  echo '      "PreToolUse": [{'
  echo '        "matcher": "Edit|Write|MultiEdit|Bash",'
  echo '        "hooks": [{"type": "command", "command": ".claude/hooks/pre-tool-use.sh"}]'
  echo '      }]'
  echo '    }'
  echo ""
else
  install_file "$KIT_DIR/.claude/settings.json" "$TARGET_DIR/.claude/settings.json"
fi

# ──────────────────────────────────────────────────────────────
# Step 4: Templates
# ──────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}Template files:${NC}"
install_file "$KIT_DIR/templates/construction-spec.md" "$TARGET_DIR/templates/construction-spec.md"
install_file "$KIT_DIR/templates/CONSTRUCTIONS.md"     "$TARGET_DIR/CONSTRUCTIONS.md"
install_file "$KIT_DIR/templates/CONSTRUCTION_LOG.md"  "$TARGET_DIR/CONSTRUCTION_LOG.md"
install_file "$KIT_DIR/templates/DOMAIN_CONTEXT.md"    "$TARGET_DIR/DOMAIN_CONTEXT.md"
install_file "$KIT_DIR/templates/REVISION.md"          "$TARGET_DIR/templates/REVISION.md"

# ──────────────────────────────────────────────────────────────
# Step 5: CLAUDE.md handling
# ──────────────────────────────────────────────────────────────

if [[ -f "$TARGET_DIR/CLAUDE.md" ]]; then
  echo ""
  echo -e "  ${YELLOW}exists:${NC}  CLAUDE.md"
  # Check if it already has math kit content
  if grep -q "Mathematics Kit" "$TARGET_DIR/CLAUDE.md" 2>/dev/null; then
    echo -e "  ${BLUE}already contains:${NC} Mathematics Kit section (skipped)"
  else
    echo -e "  ${YELLOW}ACTION NEEDED:${NC} Append the math workflow to your CLAUDE.md."
    echo -e "  The snippet is at: ${BLUE}$KIT_DIR/templates/CLAUDE.md.snippet${NC}"
    echo -e "  Or run: ${BOLD}cat '$KIT_DIR/templates/CLAUDE.md.snippet' >> CLAUDE.md${NC}"
  fi
else
  cp "$KIT_DIR/templates/CLAUDE.md.snippet" "$TARGET_DIR/CLAUDE.md"
  echo -e "  ${GREEN}created:${NC} CLAUDE.md"
fi

# ──────────────────────────────────────────────────────────────
# Step 6: Project directories
# ──────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}Project directories:${NC}"
mkdir -p "$TARGET_DIR/specs"
echo -e "  ${GREEN}ready:${NC}   specs/ (specification & construction docs)"
mkdir -p "$TARGET_DIR/results"
echo -e "  ${GREEN}ready:${NC}   results/ (archived construction results)"

# ──────────────────────────────────────────────────────────────
# Step 7: Lean4 project setup (requires lake)
# ──────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}Lean4 project:${NC}"

if [[ "$HAVE_LAKE" != "true" ]]; then
  echo -e "  ${YELLOW}skipped:${NC} lake not available — install elan, then re-run installer"
elif [[ -f "$TARGET_DIR/lakefile.lean" || -f "$TARGET_DIR/lakefile.toml" ]]; then
  echo -e "  ${GREEN}found:${NC}   lakefile exists"

  # Check if Mathlib is already a dependency
  if grep -q "Mathlib" "$TARGET_DIR/lakefile.lean" 2>/dev/null || \
     grep -q "Mathlib" "$TARGET_DIR/lakefile.toml" 2>/dev/null; then
    echo -e "  ${GREEN}found:${NC}   Mathlib dependency"
  else
    echo -e "  ${YELLOW}note:${NC}    Mathlib not found in lakefile"
    echo -e "  To add Mathlib, add this to your lakefile.lean:"
    echo ""
    echo '    require mathlib from git'
    echo '      "https://github.com/leanprover-community/mathlib4"'
    echo ""
    echo -e "  Then run: ${BOLD}lake update${NC}"
  fi
else
  echo -e "  ${YELLOW}No lakefile found.${NC}"

  # Determine project name from directory
  local_project_name=$(basename "$TARGET_DIR" | sed 's/[^a-zA-Z0-9]/_/g')

  echo -e "  Initializing Lean4 project: ${BOLD}$local_project_name${NC}"
  echo ""

  # Initialize Lean4 project with math template
  if lake init "$local_project_name" math 2>/dev/null; then
    echo -e "  ${GREEN}created:${NC} Lean4 project ($local_project_name)"
  else
    # Fallback: basic init
    lake init "$local_project_name" 2>/dev/null || {
      echo -e "  ${RED}Failed to initialize Lean4 project.${NC}" >&2
      echo -e "  Try manually: ${BOLD}lake init $local_project_name math${NC}" >&2
    }
  fi

  # Add Mathlib dependency if not present
  if [[ -f "$TARGET_DIR/lakefile.lean" ]] && ! grep -q "Mathlib" "$TARGET_DIR/lakefile.lean"; then
    echo "" >> "$TARGET_DIR/lakefile.lean"
    echo 'require mathlib from git' >> "$TARGET_DIR/lakefile.lean"
    echo '  "https://github.com/leanprover-community/mathlib4"' >> "$TARGET_DIR/lakefile.lean"
    echo -e "  ${GREEN}added:${NC}   Mathlib dependency to lakefile.lean"
  fi
fi

# ── Mathlib cache setup ──

setup_mathlib_cache() {
  # R3.1: Detect Mathlib dependency (case-insensitive)
  local HAS_MATHLIB=false
  if grep -qi "mathlib" "$TARGET_DIR/lakefile.lean" 2>/dev/null || \
     grep -qi "mathlib" "$TARGET_DIR/lakefile.toml" 2>/dev/null; then
    HAS_MATHLIB=true
  fi

  if [[ "$HAS_MATHLIB" == "true" ]]; then
    echo -e "  ${GREEN}found:${NC}   Mathlib dependency"

    # R3.3a: Toolchain version match check
    local project_tc="" mathlib_tc=""
    if [[ -f "$TARGET_DIR/lean-toolchain" ]]; then
      project_tc=$(cat "$TARGET_DIR/lean-toolchain" | tr -d '[:space:]')
    fi

    local mathlib_tc_path=""
    if [[ -f "$TARGET_DIR/.lake/packages/mathlib/lean-toolchain" ]]; then
      mathlib_tc_path="$TARGET_DIR/.lake/packages/mathlib/lean-toolchain"
    elif [[ -f "$TARGET_DIR/lake-packages/mathlib/lean-toolchain" ]]; then
      mathlib_tc_path="$TARGET_DIR/lake-packages/mathlib/lean-toolchain"
    fi

    if [[ -n "$mathlib_tc_path" ]]; then
      mathlib_tc=$(cat "$mathlib_tc_path" | tr -d '[:space:]')
      if [[ -n "$project_tc" && -n "$mathlib_tc" && "$project_tc" != "$mathlib_tc" ]]; then
        echo ""
        echo -e "  ${RED}ERROR: Toolchain mismatch detected.${NC}"
        echo -e "    Project:  $project_tc"
        echo -e "    Mathlib:  $mathlib_tc"
        echo ""
        echo -e "  Mathlib pins its toolchain version. Update your lean-toolchain to match:"
        echo -e "    cp $mathlib_tc_path ./lean-toolchain"
        echo -e "  Then re-run install.sh."
        exit 1
      fi
    fi

    # R3.3: Fetch precompiled oleans
    echo -e "  Fetching Mathlib precompiled oleans..."
    if lake exe cache get 2>&1; then
      echo -e "  ${GREEN}done:${NC}    lake exe cache get"
    else
      echo -e "  ${YELLOW}WARNING: lake exe cache get failed. Falling back to full build.${NC}"
      echo -e "  ${YELLOW}This will take 20-40 minutes.${NC}"
    fi
  fi

  # R3.2: Build once to populate cache
  echo ""
  echo -e "  Building Lean4 project (populating olean cache)..."
  echo -e "  This is a one-time cost. Subsequent builds will be incremental."
  if lake build 2>&1; then
    echo -e "  ${GREEN}done:${NC}    lake build succeeds"
  else
    echo -e "  ${YELLOW}warning:${NC} lake build had issues (check your lakefile configuration)"
  fi
}

# ── Update and build (if lake available) ──
if [[ "$HAVE_LAKE" == "true" ]]; then
  echo ""
  echo -e "${BOLD}Fetching dependencies:${NC}"
  echo -e "  Running ${BOLD}lake update${NC} (this may take a while for Mathlib)..."

  if lake update 2>&1; then
    echo -e "  ${GREEN}done:${NC}    lake update"
  else
    echo -e "  ${YELLOW}warning:${NC} lake update had issues (you may need to run it manually)"
  fi

  echo ""
  echo -e "${BOLD}Build setup:${NC}"
  setup_mathlib_cache
fi

# ──────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}Done!${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Write your first spec: ${BLUE}cp templates/construction-spec.md specs/my-construction.md${NC}"
echo -e "  2. Edit the spec with your domain and requirements"
echo -e "  3. Run the full pipeline: ${BLUE}./math.sh full specs/my-construction.md${NC}"
echo -e "  4. Or run phases individually:"
echo -e "     ${BLUE}./math.sh survey specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh specify specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh construct specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh formalize specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh prove specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh polish specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh audit specs/my-construction.md${NC}"
echo -e "     ${BLUE}./math.sh log specs/my-construction.md${NC}"
echo -e "  5. Check status: ${BLUE}./math.sh status${NC}"
echo -e "  6. Source aliases: ${BLUE}source math-aliases.sh${NC}"
echo -e "  7. Live monitor: ${BLUE}./math.sh watch prove${NC}"
echo ""
echo -e "For program mode (auto-advance through multiple constructions):"
echo -e "  1. Edit ${BLUE}CONSTRUCTIONS.md${NC} with your construction queue"
echo -e "  2. Run: ${BLUE}./math.sh program${NC}"
echo ""
