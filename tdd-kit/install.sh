#!/usr/bin/env bash
# install.sh -- Bootstrap the Claude TDD Kit into your project
#
# Usage:
#   curl -sL <url>/install.sh | bash
#   -- or --
#   cd your-project && /path/to/tdd-kit/install.sh
#
# This script copies the TDD workflow files into your project root.
# It will NOT overwrite existing files (safe to run multiple times).

set -euo pipefail

# ── Parse flags ──
UPGRADE=false
if [[ "${1:-}" == "--upgrade" ]]; then UPGRADE=true; shift; fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'
BOLD='\033[1m'

# Determine where the kit files live (same directory as this script)
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Target is the current working directory
TARGET_DIR="$(pwd)"

echo ""
echo -e "${BOLD}Claude TDD Kit -- Installer${NC}"
if [[ "$UPGRADE" == "true" ]]; then
  echo -e "${YELLOW}Mode: UPGRADE (machinery overwritten, config backed up, state untouched)${NC}"
fi
echo -e "Installing into: ${BLUE}$TARGET_DIR${NC}"
echo ""

# ── Helper: copy file if it doesn't exist (state files -- never touched on upgrade) ──
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

# ── Helper: machinery files -- overwritten + made executable on upgrade ──
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

# ── Helper: config files -- backed up + overwritten + made executable on upgrade ──
upgrade_config_executable() {
  upgrade_config "$1" "$2"
  chmod +x "$2"
}

# ── Core files (config: orchestrator, machinery: aliases & watch) ──
echo -e "${BOLD}Core orchestration:${NC}"
upgrade_config_executable "$KIT_DIR/tdd.sh"          "$TARGET_DIR/tdd.sh"
upgrade_machinery         "$KIT_DIR/tdd-aliases.sh"  "$TARGET_DIR/tdd-aliases.sh"
upgrade_machinery            "$KIT_DIR/scripts/tdd-watch.py"     "$TARGET_DIR/scripts/tdd-watch.py"
upgrade_machinery_executable "$KIT_DIR/scripts/test-summary.sh"  "$TARGET_DIR/scripts/test-summary.sh"

# ── Claude hooks & prompts (config: hook & settings, machinery: prompts) ──
echo ""
echo -e "${BOLD}Claude Code hooks & prompts:${NC}"
upgrade_config_executable "$KIT_DIR/.claude/hooks/pre-tool-use.sh"     "$TARGET_DIR/.claude/hooks/pre-tool-use.sh"
upgrade_machinery         "$KIT_DIR/.claude/prompts/tdd-red.md"        "$TARGET_DIR/.claude/prompts/tdd-red.md"
upgrade_machinery         "$KIT_DIR/.claude/prompts/tdd-green.md"      "$TARGET_DIR/.claude/prompts/tdd-green.md"
upgrade_machinery         "$KIT_DIR/.claude/prompts/tdd-refactor.md"   "$TARGET_DIR/.claude/prompts/tdd-refactor.md"
upgrade_machinery         "$KIT_DIR/.claude/prompts/tdd-breadcrumbs.md" "$TARGET_DIR/.claude/prompts/tdd-breadcrumbs.md"

echo ""
echo -e "${BOLD}Codex prompt pack:${NC}"
upgrade_machinery         "$KIT_DIR/.codex/prompts/tdd-red.md"        "$TARGET_DIR/.codex/prompts/tdd-red.md"
upgrade_machinery         "$KIT_DIR/.codex/prompts/tdd-green.md"      "$TARGET_DIR/.codex/prompts/tdd-green.md"
upgrade_machinery         "$KIT_DIR/.codex/prompts/tdd-refactor.md"   "$TARGET_DIR/.codex/prompts/tdd-refactor.md"
upgrade_machinery         "$KIT_DIR/.codex/prompts/tdd-breadcrumbs.md" "$TARGET_DIR/.codex/prompts/tdd-breadcrumbs.md"

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
  echo '        "matcher": "Read|Edit|Write|MultiEdit|Bash",'
  echo '        "hooks": [{"type": "command", "command": ".claude/hooks/pre-tool-use.sh"}]'
  echo '      }]'
  echo '    }'
  echo ""
else
  install_file "$KIT_DIR/.claude/settings.json" "$TARGET_DIR/.claude/settings.json"
fi

# ── Template files (state: never touched on upgrade) ──
echo ""
echo -e "${BOLD}Template files:${NC}"
install_file "$KIT_DIR/templates/LAST_TOUCH.md" "$TARGET_DIR/LAST_TOUCH.md"
install_file "$KIT_DIR/templates/PRD.md"        "$TARGET_DIR/PRD.md"

# ── CLAUDE.md handling ──
if [[ -f "$TARGET_DIR/CLAUDE.md" ]]; then
  echo ""
  echo -e "  ${YELLOW}exists:${NC}  CLAUDE.md"
  echo -e "  ${YELLOW}ACTION NEEDED:${NC} Append the TDD workflow to your CLAUDE.md."
  echo -e "  The snippet is at: ${BLUE}templates/CLAUDE.md.snippet${NC}"
  echo -e "  Or run: cat '$KIT_DIR/templates/CLAUDE.md.snippet' >> CLAUDE.md"
else
  cp "$KIT_DIR/templates/CLAUDE.md.snippet" "$TARGET_DIR/CLAUDE.md"
  echo -e "  ${GREEN}created:${NC} CLAUDE.md"
fi


# ── AGENTS.md handling (Codex CLI) ──
if [[ -f "$TARGET_DIR/AGENTS.md" ]]; then
  echo ""
  echo -e "  ${YELLOW}exists:${NC}  AGENTS.md"
  echo -e "  ${YELLOW}ACTION NEEDED:${NC} Append the TDD workflow to your AGENTS.md."
  echo -e "  The snippet is at: ${BLUE}templates/AGENTS.md.snippet${NC}"
  echo -e "  Or run: cat '$KIT_DIR/templates/AGENTS.md.snippet' >> AGENTS.md"
else
  cp "$KIT_DIR/templates/AGENTS.md.snippet" "$TARGET_DIR/AGENTS.md"
  echo -e "  ${GREEN}created:${NC} AGENTS.md"
fi

# ── Create docs/ directory for specs ──
mkdir -p "$TARGET_DIR/docs"
echo -e "  ${GREEN}ready:${NC}   docs/ (place your spec files here)"

# ── Done ──
echo ""
echo -e "${BOLD}${GREEN}Done!${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Edit ${BLUE}PRD.md${NC} with your project requirements"
echo -e "  2. Edit ${BLUE}LAST_TOUCH.md${NC} with your current project state"
echo -e "  3. Configure build/test commands in ${BLUE}tdd.sh${NC}:"
echo -e "     - Set ${BOLD}BUILD_CMD${NC} and ${BOLD}TEST_CMD${NC} for your project"
echo -e "     - Set ${BOLD}TEST_DIRS${NC} if your tests aren't in tests/"
echo -e "  4. Optional backend switch: set ${BOLD}TDD_AGENT_BIN=codex${NC} for Codex CLI"
echo -e "  5. Optionally source the aliases: ${BLUE}source tdd-aliases.sh${NC}"
echo -e "  6. Write your first spec: ${BLUE}docs/my-feature.md${NC}"
echo -e "  7. Run: ${BLUE}./tdd.sh red docs/my-feature.md${NC}"
echo ""
