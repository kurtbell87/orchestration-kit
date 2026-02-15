# Project Instructions — Master-Kit (Greenfield)

## Available Kits

| Kit | Script | Phases |
|-----|--------|--------|
| **TDD** | `./tdd.sh` | red, green, refactor, ship, full, watch |
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

## Cross-Kit Interop (Advanced)

```bash
master-kit/tools/kit request --from research --from-phase status --to math --action math.status \
  --run-id <parent_run_id> --json
master-kit/tools/pump --once --request <request_id> --json
```

`--from-phase` is optional; if omitted, `master-kit/tools/pump` infers it from the parent run metadata/events.

## Global Dashboard (Optional)

```bash
master-kit/tools/dashboard register --master-kit-root ./master-kit --project-root "$(pwd)"
master-kit/tools/dashboard index
master-kit/tools/dashboard serve --host 127.0.0.1 --port 7340
```

Open `http://127.0.0.1:7340` to explore runs across projects and filter by project.

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
- **Don't independently verify kit sub-agent work.** Each phase spawns a dedicated sub-agent that does its own verification. Trust the exit code and capsule. Do NOT re-run tests, re-read logs, re-check build output, or otherwise duplicate work the sub-agent already did. Exit 0 + capsule = done. Exit 1 = read the capsule for the failure, don't grep the log.
- Don't read phase log files after a successful phase. Logs are for debugging failures only.

## Orchestrator Discipline (MANDATORY)

You are the orchestrator. Sub-agents do the work. Your job is to sequence phases and react to exit codes. Protect your context window.

1. **Run phases in background, check only the exit code.** Do not read the TaskOutput content — the JSON blob wastes context. Check `status: completed/failed` and `exit_code` only.
2. **Never run Bash for verification.** No `pytest`, `lake build`, `ls`, `cat`, `grep` to check what a sub-agent produced. If the phase exited 0, it worked.
3. **Never read implementation files** the sub-agents wrote (source code, test files, .lean files, experiment scripts). That is their domain. You read only state files (CLAUDE.md, LAST_TOUCH.md, RESEARCH_LOG.md, etc.).
4. **Chain phases by exit code only.** Exit 0 → next phase. Exit 1 → read the capsule (not the log), decide whether to retry or stop.
5. **Never read capsules after success.** Capsules exist for failure diagnosis and interop handoffs. A successful phase needs no capsule read.
6. **Minimize tool calls.** Each Bash call, Read, or Glob adds to your context. If the information isn't needed to decide the next action, don't fetch it.

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
