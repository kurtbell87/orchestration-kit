# PRD: Orchestration-Kit Orchestrator Monorepo

## Status

Active canonical PRD for this repository. This document supersedes prior MCP addendum notes by integrating the MCP layer into the main product requirements.

## 1) Objective

Provide a single orchestrator monorepo that composes:

- `tdd-kit`
- `research-kit`
- `mathematics-kit`

while enforcing:

- pointer-first handoffs (capsules/manifests/events)
- anti-bloat read budgets and hook enforcement
- observable run traces
- MCP access for Claude Code and Codex CLI
- global run observability across cloned projects

The orchestrator must wrap existing kit CLIs (`tools/kit`, `tools/pump`, `tools/query-log`) without changing their core behavior contracts.

## 2) Core Principles

1. Pointer-only boundaries:
   Cross-agent/tool boundaries pass run IDs and file paths, not full transcripts or large logs.
2. CLI-as-truth:
   Orchestration state is produced by `tools/*` and files under `runs/`, `interop/requests/`, `interop/responses/`.
3. Local-first security:
   MCP binds to localhost and requires bearer token auth.
4. Deterministic observability:
   Every run has a reproducible pointer set: capsule, manifest, events, logs.

## 3) Repository Shape

```text
orchestration-kit/
  .claude/                    # orchestrator hook + settings
  tdd-kit/
  research-kit/
  mathematics-kit/
  interop/
    requests/
    responses/
    schemas/
  runs/
    <run_id>/
      capsules/
      manifests/
      logs/
      events.jsonl
  mcp/
    server.py
    schema.json
  tools/
    bootstrap
    dashboard
    kit
    pump
    query-log
    smoke-run
    validate-capsules
    validate-manifests
    mcp-serve
    mcp-token
    spawn-claude-worker
    spawn-codex-worker
  docs/
    PRD_ORCHESTRATION_KIT.md
    MCP_SETUP.md
```

## 4) Contracts

### 4.1 Run Contract

A run is identified by `run_id` and emits:

- `runs/<run_id>/capsules/<kit>_<phase>.md`
- `runs/<run_id>/manifests/<kit>_<phase>.json`
- `runs/<run_id>/logs/<kit>_<phase>.log`
- `runs/<run_id>/events.jsonl`

Run metadata must include:

- `project_root`
- `orchestration_kit_root`
- `agent_runtime`
- `host`
- `pid`

### 4.2 Capsule Contract

Capsules are bounded summaries for cross-agent context transfer:

- max 30 lines
- no full transcripts
- no large embedded logs
- explicit evidence pointers

### 4.3 Manifest Contract

Manifest includes:

- run metadata
- tracked artifact index (bounded)
- truth pointers
- log pointers
- capsule pointer

### 4.4 Interop Request Contract

Requests are created at `interop/requests/<request_id>.json` and include:

- `from_kit`, optional `from_phase`, `to_kit`, `action`, `args`
- parent `run_id`
- `must_read` pointers
- `read_budget` (`max_files`, `max_total_bytes`, `allowed_paths`)
- expected deliverables

Routing policy: any `{from_kit, from_phase}` may call any `{to_kit, to_phase}`. No static adjacency restrictions are allowed.
When `from_phase` is omitted, pump execution infers it from parent run metadata/events.

### 4.5 Interop Response Contract

Responses are written to `interop/responses/<request_id>.json` and include:

- `status` (`ok|failed|blocked`)
- child run pointer set
- deliverables and short notes

## 5) Hook and Anti-Bloat Requirements

The orchestrator hook at `.claude/hooks/pre-tool-use.sh` must:

- preserve per-kit enforcement semantics (TDD/Research/Math)
- enforce large-read blocking unless allowlisted
- enforce unique-file and total-byte read budget limits
- avoid recursive re-entry when dispatching between orchestrator/kit hooks

## 6) MCP Layer Requirements

### 6.1 Endpoint and Auth

- URL: `http://127.0.0.1:7337/mcp` (defaults)
- Auth: `Authorization: Bearer <ORCHESTRATION_KIT_MCP_TOKEN>`
- Bind host default: `127.0.0.1`

### 6.2 MCP Tools

- `orchestrator.run`
- `orchestrator.request_create`
- `orchestrator.pump`
- `orchestrator.run_info`
- `orchestrator.query_log`

All tool outputs must remain pointer-oriented and bounded by `ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES`.

### 6.3 Cross-Spawn Wrappers

- `tools/spawn-claude-worker <request_id> [--project-root ...]`
- `tools/spawn-codex-worker <request_id> [--project-root ...]`

Wrappers must export required MCP env and execute exactly one request, with `tools/pump --once` fallback when direct CLI MCP invocation flags are unavailable.
Wrapper-attributed runtime identity must be surfaced via run metadata (`agent_runtime`).

### 6.4 Dashboard Layer Requirements

- `tools/dashboard` must support:
  - global project registration (`orchestration-kit` clone roots + owning project roots)
  - indexed run/query views across all registered projects
  - per-project filters
  - project listing and unregister operations
  - full index and project-scoped index refresh modes
  - run-thread exploration by `parent_run_id`
  - cross-phase edge summaries derived from request events
- Dashboard data must be derived from pointer artifacts (`runs/*/events.jsonl`, manifests, request/response files), not transcripts.
- Project-scoped refresh must not delete data for other indexed projects.
- Dashboard persistence defaults to `~/.orchestration-kit-dashboard`, supports `ORCHESTRATION_KIT_DASHBOARD_HOME` override, and may fallback to `/tmp/orchestration-kit-dashboard` when needed.

## 7) Testing and CI Requirements

Required coverage includes:

- hook enforcement and re-entry behavior
- capsule/manifest validators
- MCP auth + bounded output + pointer-only tool behavior
- cross-kit routing matrix + cycle coverage (`tdd -> research -> math -> tdd`)
- dashboard index coverage (multi-project + project-scoped refresh without data loss)
- end-to-end smoke flow (`tools/smoke-run`)

CI must run unit tests, MCP integration tests, smoke run, and validators.

## 8) Non-Goals (Current)

- distributed queueing/execution
- hosted multi-tenant auth and remote control plane
- transcript-based orchestration between agents

## 9) Definition of Done

The system is considered valid when:

- `tools/kit` produces capsule/manifest/events/logs per run
- `tools/pump` executes request/response handoffs across kits
- hook guardrails prevent uncontrolled large reads and unsafe phase writes
- MCP tools provide authenticated pointer-first access
- CI passes all required tests
