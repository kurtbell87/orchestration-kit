# Project Instructions — Master-Kit (Greenfield)

## Available Kits

| Kit | Script | Phases |
|-----|--------|--------|
| **TDD** | `./tdd.sh` | red, green, refactor, ship, full |
| **Research** | `./experiment.sh` | survey, frame, run, read, log, cycle, full, program, status |
| **Math** | `./math.sh` | survey, specify, construct, formalize, prove, audit, log, full, program, status |

## Orchestrator (Advanced)

For cross-kit runs and interop, use the orchestrator:

```bash
source .master-kit.env
master-kit/tools/kit --json <kit> <phase> [args...]
master-kit/tools/kit --json research status
```

Run artifacts land in `master-kit/runs/<run_id>/` — capsules, manifests, logs, events.

## State Files (at project root)

| Kit | Read first |
|-----|-----------|
| TDD | `CLAUDE.md` → `LAST_TOUCH.md` → `PRD.md` |
| Research | `CLAUDE.md` → `RESEARCH_LOG.md` → `QUESTIONS.md` |
| Math | `CLAUDE.md` → `CONSTRUCTION_LOG.md` → `CONSTRUCTIONS.md` |

## Working Directories

- `docs/` — TDD specs
- `experiments/` — Research experiment specs
- `results/` — Research + Math results
- `specs/` — Math specification documents
- `handoffs/completed/` — Resolved research handoffs
- `scripts/` — Utility scripts (symlinked from master-kit)

## Don't

- Don't `cd` into `master-kit/` and run kit scripts from there — run from project root.
- Don't `cat` full log files — use `master-kit/tools/query-log`.
- Don't explore the codebase to "understand" it — read state files first.
- **Don't independently verify kit sub-agent work.** Each phase (red, green, refactor, run, prove, etc.) spawns a dedicated sub-agent that does its own verification. Trust the exit code and capsule. Do NOT re-run tests, re-read logs, re-check build output, or otherwise duplicate work the sub-agent already did. Exit 0 + capsule = done. Exit 1 = read the capsule for the failure, don't grep the log.
- Don't read phase log files to "see what happened" after a successful phase. The capsule is the summary. Logs are for debugging failures only.

## Breadcrumb Maintenance (MANDATORY)

After every session that changes the codebase, update:

1. **`LAST_TOUCH.md`** — Current state and what to do next (TDD).
2. **`RESEARCH_LOG.md`** — Append experiment results (Research).
3. **`CONSTRUCTION_LOG.md`** — Progress notes (Math).
4. **This file's "Current State" section** — Keep it current.

## Current State (updated YYYY-MM-DD)

- **Build:** _update after first TDD cycle_
- **Experiments completed:** _0_
- **Constructions completed:** _0_
- **Next task:** _Pick a kit and start your first cycle._
