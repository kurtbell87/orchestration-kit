# Master-Kit

Master-Kit is a monorepo orchestrator for three domain kits:

- `claude-tdd-kit`
- `claude-research-kit`
- `claude-mathematics-kit`

It adds shared run orchestration, interop handoffs, anti-bloat guardrails, and an HTTP MCP server without replacing the kit-specific workflows.

## What It Provides

- Unified run orchestration via `tools/kit`
- Interop request/response queue via `tools/pump`
- Pointer-first observability artifacts in `runs/<run_id>/...`
- Master hook enforcement across TDD, Research, and Math phases
- Bounded log querying via `tools/query-log`
- Local authenticated MCP server for Claude Code and Codex CLI

## Core Design Invariants

- Pointer-only boundaries: cross-agent communication uses file pointers, not transcript dumps.
- Artifact-first truth: capsules, manifests, events, and logs are the source of truth.
- Read-budget enforcement: large reads are blocked unless allowlisted.
- Local security defaults: MCP binds to localhost and requires bearer token auth.

## Repository Layout

```text
master-kit/
  .claude/                  # master hook + shared Claude settings
  claude-tdd-kit/           # TDD kit (standalone-capable)
  claude-research-kit/      # Research kit (standalone-capable)
  claude-mathematics-kit/   # Lean math kit (standalone-capable)
  docs/
    PRD_MASTER_KIT.md
    MCP_SETUP.md
  interop/
    requests/
    responses/
    schemas/
  mcp/
    server.py
    schema.json
  runs/
    <run_id>/
      capsules/
      manifests/
      logs/
      events.jsonl
  tests/
  tools/
    bootstrap
    kit
    pump
    query-log
    smoke-run
    validate-capsules
    validate-manifests
    mcp-token
    mcp-serve
    spawn-claude-worker
    spawn-codex-worker
```

## Prerequisites

- `python3`
- `bash`/POSIX shell environment
- Optional CLIs for full workflows: `claude`, `codex`, `gh`, `lake`

## Quick Start

Greenfield example:

```bash
git clone https://github.com/kurtbell87/master-kit.git my-project
cd my-project
./install.sh
source .master-kit.env
```

1. One-command install/bootstrap:

```bash
./install.sh
```

2. Load MCP environment:

```bash
source .master-kit.env
```

3. Start MCP server:

```bash
tools/mcp-serve
```

4. Verify orchestrator + MCP path:

```bash
tools/kit --json research status
```

## Validation

Run smoke + tests:

```bash
tools/smoke-run
python3 -m unittest discover -s tests -v
```

## Main Workflows

### Run a kit phase with orchestration

```bash
tools/kit --json research status
tools/kit --json tdd red docs/my-feature.md
tools/kit --json math status
```

Each run produces pointers under `runs/<run_id>/`.

### Create and execute an interop request

1. Create request:

```bash
tools/kit request \
  --from research \
  --to math \
  --action math.status \
  --run-id <parent_run_id> \
  --must-read runs/<parent_run_id>/capsules/research_status.md \
  --must-read runs/<parent_run_id>/manifests/research_status.json \
  --allowed-path 'runs/*/capsules/*.md' \
  --allowed-path 'runs/*/manifests/*.json' \
  --deliverable 'runs/*/capsules/math_status.md' \
  --deliverable 'runs/*/manifests/math_status.json' \
  --json
```

2. Execute one request:

```bash
tools/pump --once --request <request_id> --json
```

### Query logs without large reads

```bash
tools/query-log tail runs/<run_id>/logs/research_status.log 120
tools/query-log grep 'BLOCKED:' runs/<run_id>/logs/research_status.log
```

## MCP Server

### Start server

```bash
export MASTER_KIT_ROOT="$(pwd)"
export MASTER_KIT_MCP_TOKEN="$(tools/mcp-token)"
tools/mcp-serve
```

Default endpoint:

- `http://127.0.0.1:7337/mcp`

### Exposed MCP tools

- `master.run`
- `master.request_create`
- `master.pump`
- `master.run_info`
- `master.query_log`

### Worker wrappers

- `tools/spawn-claude-worker <request_id>`
- `tools/spawn-codex-worker <request_id>`

Wrappers fallback to `tools/pump --once --request <request_id> --json` when direct CLI MCP invocation flags are unavailable.

See full setup details in `docs/MCP_SETUP.md`.

## Tool Reference

- `tools/bootstrap`: monorepo bootstrap checks and optional smoke run.
- `install.sh`: one-command fresh-checkout installer (bootstrap + optional MCP env setup).
- `tools/kit`: run orchestration entrypoint plus `request` authoring helper.
- `tools/pump`: executes queued interop requests.
- `tools/query-log`: bounded log access helpers.
- `tools/smoke-run`: end-to-end sanity flow (research -> request -> pump -> validate).
- `tools/validate-capsules`: capsule contract validator.
- `tools/validate-manifests`: manifest contract validator.
- `tools/mcp-token`: create/rotate local MCP token.
- `tools/mcp-serve`: start local MCP server.

## Testing and CI

Local verification:

```bash
python3 -m unittest tests.test_master_hook -v
python3 -m unittest tests.test_hook_reentry_guard -v
python3 -m unittest tests.test_validators -v
python3 -m unittest tests.test_mcp_server -v
tools/smoke-run
```

CI workflow: `.github/workflows/master-kit-smoke.yml`

## Troubleshooting

- `MASTER_KIT_MCP_TOKEN is required`: set token with `export MASTER_KIT_MCP_TOKEN="$(tools/mcp-token)"`.
- MCP tools unavailable in client: verify project/server MCP config points to `http://127.0.0.1:7337/mcp` and uses the same bearer token.
- Request pump fails with missing request: confirm file exists under `interop/requests/<request_id>.json`.
- Validation failures: inspect `runs/<run_id>/capsules/` and `runs/<run_id>/manifests/` first; do not start with full logs.

## Security Notes

- Keep `.mcp-token` and `.mcp.json` uncommitted.
- Keep MCP bound to localhost unless you add a hardened transport boundary.
- Use pointer outputs to avoid leaking large sensitive context into agent prompts.

## Documentation

- Product requirements: `docs/PRD_MASTER_KIT.md`
- MCP client/server setup: `docs/MCP_SETUP.md`

## Docs Index

- Root overview: `README.md`
- Master PRD: `docs/PRD_MASTER_KIT.md`
- MCP setup: `docs/MCP_SETUP.md`
- TDD kit: `claude-tdd-kit/README.md`
- Research kit: `claude-research-kit/README.md`
- Mathematics kit: `claude-mathematics-kit/README.md`
