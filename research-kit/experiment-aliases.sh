#!/usr/bin/env bash
# experiment-aliases.sh -- Source this in your shell
#
#   source experiment-aliases.sh

EXP_SCRIPT="./experiment.sh"

alias exp-survey='bash $EXP_SCRIPT survey'
alias exp-frame='bash $EXP_SCRIPT frame'
alias exp-run='bash $EXP_SCRIPT run'
alias exp-read='bash $EXP_SCRIPT read'
alias exp-log='bash $EXP_SCRIPT log'
alias exp-cycle='bash $EXP_SCRIPT cycle'
alias exp-full='bash $EXP_SCRIPT full'
alias exp-program='bash $EXP_SCRIPT program'
alias exp-synthesize='bash $EXP_SCRIPT synthesize'

exp-status() {
  bash $EXP_SCRIPT status
}

exp-unlock() {
  echo "Emergency unlock -- restoring write permissions..."
  find experiments/ -name "*.md" -exec chmod 644 {} \; 2>/dev/null || true
  find results/ -type f \( -name "*.json" -o -name "*.csv" \) -exec chmod 644 {} \; 2>/dev/null || true
  find results/ -type d -exec chmod 755 {} \; 2>/dev/null || true
  echo "Done."
}
