# Master-Kit — Orchestrator Instructions

## What This Is

A monorepo orchestrator wrapping three domain kits. You drive them through `tools/kit`, never by running kit scripts directly.

| Kit | Directory | Phases |
|-----|-----------|--------|
| **TDD** | `claude-tdd-kit/` | red, green, refactor, ship, full |
| **Research** | `claude-research-kit/` | survey, frame, run, read, log, cycle, full, program, status |
| **Math** | `claude-mathematics-kit/` | survey, specify, construct, formalize, prove, audit, log, full, program, status |

## How to Run Phases

```bash
tools/kit --json <kit> <phase> [args...]
```

Examples:
```bash
tools/kit --json tdd red docs/my-feature.md
tools/kit --json research status
tools/kit --json math survey specs/my-construction.md
```

Each run produces artifacts under `runs/<run_id>/`:
- `capsules/<kit>_<phase>.md` — 30-line max summary (read this first)
- `manifests/<kit>_<phase>.json` — metadata + artifact index
- `logs/<kit>_<phase>.log` — full output (use `tools/query-log` to read)
- `events.jsonl` — structured event stream

## Cross-Kit Handoffs

When one kit needs results from another, use the interop queue:

```bash
# 1. Create request
tools/kit request --from research --to math --action math.status \
  --run-id <parent_run_id> --json

# 2. Execute it
tools/pump --once --request <request_id> --json
```

Responses land in `interop/responses/<request_id>.json`.

## Reading Logs Without Blowing Context

Never `cat` a full log file. Use bounded access:

```bash
tools/query-log tail runs/<run_id>/logs/<kit>_<phase>.log 100
tools/query-log grep 'ERROR' runs/<run_id>/logs/<kit>_<phase>.log
```

## Key State Files (Per-Kit)

| Kit | State files to read first |
|-----|--------------------------|
| TDD | `claude-tdd-kit/CLAUDE.md` → `LAST_TOUCH.md` → `PRD.md` |
| Research | `claude-research-kit/CLAUDE.md` → `RESEARCH_LOG.md` → `QUESTIONS.md` |
| Math | `claude-mathematics-kit/CLAUDE.md` → `CONSTRUCTION_LOG.md` → `CONSTRUCTIONS.md` |

## Don't

- Don't `cd` into kit directories and run scripts directly — use `tools/kit`.
- Don't `cat` full log files — use `tools/query-log`.
- Don't dump transcripts or large outputs into capsules or interop requests — use file pointers.
- Don't skip reading capsules before reading logs. Capsules are the summary; logs are the detail.

## MCP Server (Optional)

```bash
source .master-kit.env
tools/mcp-serve
```

Exposes: `master.run`, `master.request_create`, `master.pump`, `master.run_info`, `master.query_log`

See `docs/MCP_SETUP.md` for client configuration.

## Validation

```bash
tools/smoke-run                              # end-to-end sanity check
tools/validate-capsules runs/<id>/capsules/   # capsule contract
tools/validate-manifests runs/<id>/manifests/ # manifest contract
```
