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
- Project-aware run metadata (`project_root`, `master_kit_root`, runtime host/pid attribution)
- Master hook enforcement across TDD, Research, and Math phases
- Bounded log querying via `tools/query-log`
- Local authenticated MCP server for Claude Code and Codex CLI
- Global run dashboard with per-project filtering via `tools/dashboard`

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
    dashboard
    kit
    pump
    query-log
    science-validation
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

Greenfield example — clone, install, and observe runs in real time:

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

3. Start the dashboard (auto-registers the current project):

```bash
tools/dashboard serve --port 7340
```

4. In a second terminal, kick off a run:

```bash
source .master-kit.env
tools/kit --json tdd red docs/my-feature.md
```

5. Open `http://127.0.0.1:7340` — the DAG updates automatically every 3 seconds while runs are active. Running nodes show elapsed time with a pulsing status indicator. Once all runs finish, polling stops.

6. (Optional) Start MCP server for IDE integration:

```bash
tools/mcp-serve
```

7. Verify orchestrator + MCP path:

```bash
tools/kit --json research status
```

## Validation

Run smoke + tests:

```bash
tools/smoke-run
python3 -m unittest discover -s tests -v
```

Run full high-trust orchestration + dashboard validation:

```bash
tools/science-validation --profile live --reset
```

## Main Workflows

### Run a kit phase with orchestration

```bash
tools/kit --json research status
tools/kit --json tdd red docs/my-feature.md
tools/kit --json math status
```

Each run produces pointers under `runs/<run_id>/`.

Run manifests/events also record attribution metadata (project root, master-kit root, runtime, host, pid), enabling global multi-project indexing.

### Create and execute an interop request

1. Create request:

```bash
tools/kit request \
  --from research \
  --from-phase status \
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

Requests are not restricted by source/target pair: any kit phase can route to any other kit phase (including self-routes), e.g. `tdd -> research -> math -> tdd`.
`--from-phase` is optional; when omitted, `tools/pump` infers it from the parent run metadata/events.

### Global dashboard (multi-project)

Register one or more cloned projects:

```bash
tools/dashboard register --master-kit-root /path/to/project-a/master-kit --project-root /path/to/project-a
tools/dashboard register --master-kit-root /path/to/project-b/master-kit --project-root /path/to/project-b
tools/dashboard projects
```

Build index and serve:

```bash
tools/dashboard index
tools/dashboard index --project-id <project_id>   # refresh one project (non-destructive to others)
tools/dashboard serve --host 127.0.0.1 --port 7340
tools/dashboard serve --project-id <project_id>
```

Open `http://127.0.0.1:7340` and filter by project to inspect active agents, run threads, and cross-phase edges.
In Thread Detail, click run artifact buttons to open capsules/manifests/logs/events; Markdown artifacts render directly in the UI.

The DAG auto-refreshes every 3 seconds while any run has `running` status. Running nodes display elapsed time and a pulsing status dot. Once all runs complete, auto-polling stops to avoid unnecessary traffic.

Always-on mode (single watchdog service):

```bash
tools/dashboard ensure-service
tools/dashboard service-status
```

`tools/kit`, `tools/pump`, and direct kit scripts (`tdd.sh`, `experiment.sh`, `math.sh`) automatically:

- register the current project with the global dashboard registry
- ensure the dashboard service is running
- refresh project-scoped index entries (for orchestrator paths)

Unregister a project:

```bash
tools/dashboard unregister --project-id <project_id>
```

Dashboard state location:

- default: `~/.master-kit-dashboard`
- env override: `MASTER_KIT_DASHBOARD_HOME=/path/to/state`
- fallback when home is not writable: `/tmp/master-kit-dashboard`

Dashboard server env defaults:

- `MASTER_KIT_DASHBOARD_HOST` (default `127.0.0.1`)
- `MASTER_KIT_DASHBOARD_PORT` (default `7340`)
- `MASTER_KIT_DASHBOARD_AUTOSTART` (default `1`; set `0` to disable auto-ensure)
- `MASTER_KIT_DASHBOARD_AUTO_INDEX` (default `1`; set `0` to disable auto-index refresh)
- `MASTER_KIT_DASHBOARD_ENSURE_WAIT_SECONDS` (default `1` for auto paths)

Dashboard API endpoints:

- `GET /api/projects`
- `GET /api/summary`
- `GET /api/dag`
- `GET /api/graph`
- `GET /api/active`
- `GET /api/runs`
- `GET /api/run`
- `GET /api/artifact` (project-scoped artifact fetch for dashboard viewer)
- `POST /api/refresh`

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

Wrapper options:

- `--project-root /path/to/master-kit`
- `--prefer-cli`

Wrappers fallback to `tools/pump --once --request <request_id> --json` when direct CLI MCP invocation flags are unavailable.
CLI-attempt toggles:

- `MASTER_KIT_SPAWN_TRY_CLAUDE=1`
- `MASTER_KIT_SPAWN_TRY_CODEX=1`
- `CODEX_SANDBOX_NETWORK_DISABLED=1` forces codex wrapper fallback to pump.

See full setup details in `docs/MCP_SETUP.md`.

## Tool Reference

- `tools/bootstrap`: monorepo bootstrap checks and optional smoke run.
- `install.sh`: one-command fresh-checkout installer (bootstrap + optional MCP env setup).
- `tools/kit`: run orchestration entrypoint plus `request` authoring helper.
- `tools/pump`: executes queued interop requests.
- `tools/query-log`: bounded log access helpers.
- `tools/dashboard`: global project registry + run index + local dashboard server + project-scoped refresh.
- `tools/dashboard ensure-service`: idempotently start/confirm the watchdog service.
- `tools/dashboard service-status`: health/status for the watchdog service.
- `tools/dashboard stop-service`: stop watchdog service using stored PID.
- `tools/science-validation`: end-to-end high-trust validation harness (3 kits + failure classes + dashboard checks).
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
python3 -m unittest tests.test_cross_phase_routing -v
python3 -m unittest tests.test_dashboard -v
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
- Full validation harness details: `docs/SCIENCE_VALIDATION.md`

## Docs Index

- Root overview: `README.md`
- Master PRD: `docs/PRD_MASTER_KIT.md`
- MCP setup: `docs/MCP_SETUP.md`
- Science validation: `docs/SCIENCE_VALIDATION.md`
- TDD kit: `claude-tdd-kit/README.md`
- Research kit: `claude-research-kit/README.md`
- Mathematics kit: `claude-mathematics-kit/README.md`
