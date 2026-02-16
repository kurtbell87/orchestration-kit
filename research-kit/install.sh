#!/usr/bin/env bash
# install.sh -- Bootstrap the Claude Research Kit into your project
#
# Usage:
#   cd your-project && /path/to/research-kit/install.sh

set -euo pipefail

# ── Parse flags ──
UPGRADE=false
if [[ "${1:-}" == "--upgrade" ]]; then UPGRADE=true; shift; fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
BOLD='\033[1m'

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$(pwd)"

echo ""
echo -e "${BOLD}Claude Research Kit -- Installer${NC}"
if [[ "$UPGRADE" == "true" ]]; then
  echo -e "${YELLOW}Mode: UPGRADE (machinery overwritten, config backed up, state untouched)${NC}"
fi
echo -e "Installing into: ${BLUE}$TARGET_DIR${NC}"
echo ""

# ── Helper: state files -- never touched on upgrade ──
install_file() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]]; then
    echo -e "  ${YELLOW}exists:${NC}  $dest (skipped)"
  else
    cp "$src" "$dest"
    echo -e "  ${GREEN}created:${NC} $dest"
  fi
}

install_executable() {
  install_file "$1" "$2"
  chmod +x "$2"
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

echo -e "${BOLD}Core orchestration:${NC}"
upgrade_config_executable    "$KIT_DIR/experiment.sh"          "$TARGET_DIR/experiment.sh"
upgrade_machinery            "$KIT_DIR/experiment-aliases.sh"  "$TARGET_DIR/experiment-aliases.sh"
upgrade_machinery            "$KIT_DIR/scripts/experiment-watch.py" "$TARGET_DIR/scripts/experiment-watch.py"

echo ""
echo -e "${BOLD}Claude Code hooks & prompts:${NC}"
upgrade_config_executable    "$KIT_DIR/.claude/hooks/pre-tool-use.sh"   "$TARGET_DIR/.claude/hooks/pre-tool-use.sh"
upgrade_machinery            "$KIT_DIR/.claude/prompts/survey.md"       "$TARGET_DIR/.claude/prompts/survey.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/frame.md"        "$TARGET_DIR/.claude/prompts/frame.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/run.md"          "$TARGET_DIR/.claude/prompts/run.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/read.md"         "$TARGET_DIR/.claude/prompts/read.md"
upgrade_machinery            "$KIT_DIR/.claude/prompts/synthesize.md"  "$TARGET_DIR/.claude/prompts/synthesize.md"

# ── Settings (config) ──
if [[ "$UPGRADE" == "true" ]]; then
  upgrade_config "$KIT_DIR/.claude/settings.json" "$TARGET_DIR/.claude/settings.json"
elif [[ -f "$TARGET_DIR/.claude/settings.json" ]]; then
  echo ""
  echo -e "  ${YELLOW}exists:${NC}  .claude/settings.json"
  echo -e "  ${YELLOW}ACTION NEEDED:${NC} Merge the hook config manually."
else
  install_file "$KIT_DIR/.claude/settings.json" "$TARGET_DIR/.claude/settings.json"
fi

# ── Template files (state: never touched on upgrade) ──
echo ""
echo -e "${BOLD}Template files:${NC}"
install_file "$KIT_DIR/templates/RESEARCH_LOG.md"      "$TARGET_DIR/RESEARCH_LOG.md"
install_file "$KIT_DIR/templates/QUESTIONS.md"          "$TARGET_DIR/QUESTIONS.md"
install_file "$KIT_DIR/templates/experiment-spec.md"    "$TARGET_DIR/templates/experiment-spec.md"
install_file "$KIT_DIR/templates/HANDOFF.md"            "$TARGET_DIR/templates/HANDOFF.md"
install_file "$KIT_DIR/templates/DOMAIN_PRIORS.md"     "$TARGET_DIR/DOMAIN_PRIORS.md"

if [[ -f "$TARGET_DIR/CLAUDE.md" ]]; then
  echo ""
  echo -e "  ${YELLOW}exists:${NC}  CLAUDE.md"
  echo -e "  ${YELLOW}ACTION NEEDED:${NC} Append the research workflow snippet:"
  echo -e "    cat '$KIT_DIR/templates/CLAUDE.md.snippet' >> CLAUDE.md"
else
  cp "$KIT_DIR/templates/CLAUDE.md.snippet" "$TARGET_DIR/CLAUDE.md"
  echo -e "  ${GREEN}created:${NC} CLAUDE.md"
fi

mkdir -p "$TARGET_DIR/experiments"
mkdir -p "$TARGET_DIR/results"
mkdir -p "$TARGET_DIR/handoffs/completed"
echo -e "  ${GREEN}ready:${NC}   experiments/ (experiment specs go here)"
echo -e "  ${GREEN}ready:${NC}   results/ (experiment outputs go here)"
echo -e "  ${GREEN}ready:${NC}   handoffs/completed/ (archived handoffs go here)"

echo ""
echo -e "${BOLD}${GREEN}Done!${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Edit ${BLUE}QUESTIONS.md${NC} with your research questions"
echo -e "  2. Configure commands in ${BLUE}experiment.sh${NC}:"
echo -e "     - Set ${BOLD}TRAIN_CMD${NC}, ${BOLD}EVAL_CMD${NC}, ${BOLD}TEST_CMD${NC}"
echo -e "     - Set ${BOLD}SRC_DIR${NC}, ${BOLD}DATA_DIR${NC}"
echo -e "  3. Optionally source aliases: ${BLUE}source experiment-aliases.sh${NC}"
echo -e "  4. Run: ${BLUE}./experiment.sh survey \"your research question\"${NC}"
echo ""
