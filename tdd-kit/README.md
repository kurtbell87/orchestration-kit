# Claude TDD Kit

Strict red-green-refactor orchestration for AI coding agents with role separation, mandatory breadcrumbs before ship, test immutability in GREEN, compact phase summaries, and token-bloat guardrails.

Supports both:
- Claude Code CLI (`claude`)
- Codex CLI (`codex`)

## Why this kit exists

This kit is tuned for constrained context windows:
- Each phase writes full output to log files, while stdout returns only a compact summary.
- GREEN phase enforces immutable tests at OS + hook layers.
- Hook guardrails can block oversized reads and optionally enforce read budgets.
- Workflow guidance strongly discourages re-verification loops and unnecessary log reads.

## Phase model

| Phase | Command | Purpose |
|---|---|---|
| RED | `./tdd.sh red <spec-file>` | Write failing tests from spec |
| GREEN | `./tdd.sh green` | Implement to pass tests |
| REFACTOR | `./tdd.sh refactor` | Improve design while tests stay green |
| BREADCRUMBS | `./tdd.sh breadcrumbs <spec-file>` | Update CLAUDE/AGENTS/LAST_TOUCH/README docs |
| SHIP | `./tdd.sh ship <spec-file>` | Run breadcrumbs, then commit/create PR/archive spec |
| FULL | `./tdd.sh full <spec-file>` | Run RED -> GREEN -> REFACTOR -> SHIP |

`ship` enforces breadcrumbs automatically, so docs are updated before commit creation.

## Quick start

```bash
# In your project root
/path/to/tdd-kit/install.sh

# Configure project commands
$EDITOR tdd.sh

# Run one cycle
./tdd.sh red docs/my-feature.md
./tdd.sh green
./tdd.sh refactor
./tdd.sh breadcrumbs docs/my-feature.md
./tdd.sh ship docs/my-feature.md
```

## Claude vs Codex backend

Default backend is Claude:

```bash
./tdd.sh red docs/my-feature.md
```

Run the same workflow with Codex:

```bash
TDD_AGENT_BIN=codex ./tdd.sh red docs/my-feature.md
TDD_AGENT_BIN=codex ./tdd.sh green
TDD_AGENT_BIN=codex ./tdd.sh refactor
TDD_AGENT_BIN=codex ./tdd.sh breadcrumbs docs/my-feature.md
```

Optional shell-level default:

```bash
export TDD_AGENT_BIN=codex
```

## Logs, summaries, and watch mode

- Full phase logs: `$TDD_LOG_DIR/{red,green,refactor,breadcrumbs}.log`
- Full test output: `$TDD_LOG_DIR/test-output.log`
- Default log dir: `/tmp/tdd-<project>/`
- Phase stdout: compact summary only (for context efficiency)

Watch a running/finished phase:

```bash
./tdd.sh watch green
./tdd.sh watch refactor
./tdd.sh watch red --resolve
```

## Token-bloat guardrails

Hook protections are active through `.claude/hooks/pre-tool-use.sh`:

1. GREEN test immutability enforcement:
- Blocks direct test edits/writes.
- Blocks chmod/chown/sudo and revert-style git commands.

2. Global read guardrails (all phases):
- Blocks single-file reads over `MAX_READ_BYTES` (default: `200000`).
- Optional read budgets via:
  - `READ_BUDGET_MAX_FILES`
  - `READ_BUDGET_MAX_TOTAL_BYTES`
- Allowlist override via:
  - `READ_ALLOW_GLOBS`
  - `MUST_READ_ALLOWLIST`

Recommended strict profile for tight enterprise limits:

```bash
export MAX_READ_BYTES=120000
export READ_BUDGET_MAX_FILES=25
export READ_BUDGET_MAX_TOTAL_BYTES=350000
```

## Configuration

Main config in `tdd.sh`:

```bash
TEST_DIRS="tests"
SRC_DIR="src"
BUILD_CMD="npm run build"
TEST_CMD="npm test"
```

Useful environment variables:
- `TDD_AGENT_BIN` (`claude` or `codex`)
- `TDD_AGENT_EXTRA_ARGS` (extra flags passed to selected CLI)
- `PROMPT_DIR` (default `.claude/prompts`; auto-switches to `.codex/prompts` when present and using Codex)
- `TDD_LOG_DIR`
- `TDD_AUTO_MERGE`
- `TDD_DELETE_BRANCH`
- `TDD_BASE_BRANCH`

## Aliases

```bash
source tdd-aliases.sh

tdd-red docs/feature.md
tdd-green
tdd-refactor
tdd-breadcrumbs docs/feature.md
tdd-ship docs/feature.md

# Codex-mode aliases
tddc-red docs/feature.md
tddc-green
tddc-refactor
tddc-breadcrumbs docs/feature.md
tddc-ship docs/feature.md
```

## Installer behavior

`install.sh` copies kit files into your target project:
- Creates `tdd.sh`, `tdd-aliases.sh`, scripts, `.claude/prompts`, `.codex/prompts`, hook, templates.
- Leaves existing project state files intact (`PRD.md`, `LAST_TOUCH.md`, `CLAUDE.md`, `AGENTS.md`) unless missing.
- `--upgrade` mode updates machinery and backs up config where appropriate.

## Troubleshooting

- Tests left read-only after interruption:
```bash
tdd-unlock
```

- Hook not firing:
- Ensure `.claude/settings.json` contains `Read|Edit|Write|MultiEdit|Bash` matcher.
- Ensure hook is executable: `chmod +x .claude/hooks/pre-tool-use.sh`.

- Codex phase exits with disconnect/rollout-recorder errors:
- Check `CODEX_SANDBOX_NETWORK_DISABLED` (default: `0`). If it is `1` (or `true`), this shell cannot reach Codex APIs.
- Run in a non-isolated shell, or switch backend for the phase:
```bash
TDD_AGENT_BIN=claude ./tdd.sh <phase>
```

- GREEN appears to “fight” tests:
- Expected if agent tries to modify tests.
- Keep iterating implementation only; tests are the spec.
