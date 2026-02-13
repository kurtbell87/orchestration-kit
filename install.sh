#!/usr/bin/env bash
# install.sh -- One-command setup for master-kit in a fresh checkout.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

CHECK_ONLY=0
SKIP_SMOKE=0
INSTALL_PYTHON=0
SETUP_MCP=1
WRITE_ENV_FILE=1
ENV_FILE_WRITTEN=0

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]

Options:
  --check-only        Run validation checks only (no chmod fixes, no smoke run).
  --skip-smoke        Skip tools/smoke-run.
  --install-python    Install requirements.txt if present.
  --no-mcp            Skip MCP token/env setup.
  --no-env-file       Do not write .master-kit.env.
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
  token="$($ROOT_DIR/tools/mcp-token)"

  host="${MASTER_KIT_MCP_HOST:-127.0.0.1}"
  port="${MASTER_KIT_MCP_PORT:-7337}"
  max_output="${MASTER_KIT_MCP_MAX_OUTPUT_BYTES:-32000}"

  if [[ "$WRITE_ENV_FILE" -eq 1 ]]; then
    env_file="$ROOT_DIR/.master-kit.env"
    cat > "$env_file" <<ENV
export MASTER_KIT_ROOT="$ROOT_DIR"
export MASTER_KIT_MCP_HOST="$host"
export MASTER_KIT_MCP_PORT="$port"
export MASTER_KIT_MCP_MAX_OUTPUT_BYTES="$max_output"
export MASTER_KIT_MCP_TOKEN="$token"
ENV
    chmod 600 "$env_file"
    echo "[install] wrote MCP env file: $env_file"
    echo "[install] next: source .master-kit.env"
    ENV_FILE_WRITTEN=1
  else
    echo "[install] MCP configured (token stored in .mcp-token)."
    echo "[install] export these before running tools/mcp-serve:"
    echo "export MASTER_KIT_ROOT=\"$ROOT_DIR\""
    echo "export MASTER_KIT_MCP_HOST=\"$host\""
    echo "export MASTER_KIT_MCP_PORT=\"$port\""
    echo "export MASTER_KIT_MCP_MAX_OUTPUT_BYTES=\"$max_output\""
    echo "export MASTER_KIT_MCP_TOKEN=\"\$(tools/mcp-token)\""
  fi
fi

echo "[install] ready"
if [[ "$CHECK_ONLY" -eq 0 ]]; then
  echo "[install] quick start:"
  if [[ "$ENV_FILE_WRITTEN" -eq 1 ]]; then
    echo "  1. source .master-kit.env"
    echo "  2. tools/mcp-serve"
    echo "  3. tools/kit --json research status"
  else
    echo "  1. tools/mcp-serve"
    echo "  2. tools/kit --json research status"
  fi
fi
