# Orchestration-Kit — Orchestrator Instructions

## What This Is

A monorepo orchestrator wrapping three domain kits. You drive them through `tools/kit`, never by running kit scripts directly.

| Kit | Directory | Phases |
|-----|-----------|--------|
| **TDD** | `tdd-kit/` | red, green, refactor, ship, full, watch |
| **Research** | `research-kit/` | survey, frame, run, read, log, cycle, full, program, status |
| **Math** | `mathematics-kit/` | survey, specify, construct, formalize, prove, polish, audit, log, full, program, status |

## Greenfield Setup (REQUIRED before first run)

When using orchestration-kit from a parent project directory (greenfield mode), you **must run the installer first** to symlink kit scripts (`tdd.sh`, `experiment.sh`, `math.sh`) into the project root. Without this, `tools/kit` will fail with `FileNotFoundError: ./tdd.sh`.

```bash
cd <project-root> && echo "n" | ./orchestration-kit/install.sh --skip-smoke
source .orchestration-kit.env
```

This only needs to run once per project. It creates symlinks, deploys `.claude/` prompts/hooks, and writes `.orchestration-kit.env`.

## How to Run Phases

```bash
PROJECT_ROOT=<project-root> python3 tools/kit --json <kit> <phase> [args...]
```

Examples:
```bash
PROJECT_ROOT=/path/to/project python3 tools/kit --json tdd red docs/my-feature.md
PROJECT_ROOT=/path/to/project python3 tools/kit --json research status
PROJECT_ROOT=/path/to/project python3 tools/kit --json math survey specs/my-construction.md
```

**Always run these commands in the background** (use `run_in_background: true` on Bash tool calls) and check exit codes only. Do not pull stdout into your context window.

Each run produces artifacts under `runs/<run_id>/`:
- `capsules/<kit>_<phase>.md` — 30-line max summary (read this first)
- `manifests/<kit>_<phase>.json` — metadata + artifact index
- `logs/<kit>_<phase>.log` — full output (use `tools/query-log` to read)
- `events.jsonl` — structured event stream

## Cross-Kit Handoffs

When one kit needs results from another, use the interop queue:

```bash
# 1. Create request
tools/kit request --from research --from-phase status --to math --action math.status \
  --run-id <parent_run_id> --json

# 2. Execute it
tools/pump --once --request <request_id> --json
```

Responses land in `interop/responses/<request_id>.json`.
`--from-phase` is optional; if omitted, `tools/pump` infers it from the parent run metadata/events.

### Research → TDD Sub-Cycle (Common Pattern)

When a research phase discovers it needs **new code** (a tool, library, or test harness), it must not write code inline. Instead, spawn a TDD sub-cycle:

1. **Write a spec** in the project's TDD spec directory (e.g., `.kit/docs/<feature>.md`)
2. **Run TDD phases in background**, checking only exit codes:
   ```bash
   # All phases run in background — do NOT tail logs or read output
   .kit/tdd.sh red   .kit/docs/<feature>.md   # Sub-agent writes failing tests
   .kit/tdd.sh green                           # Sub-agent implements
   .kit/tdd.sh refactor                        # Sub-agent cleans up
   .kit/tdd.sh ship  .kit/docs/<feature>.md    # Sub-agent commits + PR
   ```
3. **Resume the research phase** once the TDD sub-cycle exits 0. The new code is now available as tested infrastructure.

**Key discipline**: The orchestrator never reads implementation files, test files, or build output from the TDD sub-cycle. Trust exit codes. The TDD sub-agents handle their own verification. This prevents context bloat in the orchestrator's window.

**When to trigger Research→TDD**:
- Research phase needs a data extraction tool (e.g., oracle expectancy from real data)
- Research phase needs a new analysis library or utility
- Research phase needs modifications to existing infrastructure (new fields, new APIs)
- Any code change that should be regression-tested

**When NOT to trigger** (stay in research kit):
- Python analysis scripts that are experiment-specific and disposable
- Configuration files or parameter sweeps
- Pure data analysis with no new C++/production code

## Global Dashboard (Optional)

```bash
tools/dashboard register --orchestration-kit-root "$(pwd)" --project-root "${PROJECT_ROOT:-$(pwd)}"
tools/dashboard index
tools/dashboard serve --host 127.0.0.1 --port 7340
```

Use project filtering in the UI to inspect active runs, run threads, and cross-phase edges.

## Reading Logs Without Blowing Context

Never `cat` a full log file. Use bounded access:

```bash
tools/query-log tail runs/<run_id>/logs/<kit>_<phase>.log 100
tools/query-log grep 'ERROR' runs/<run_id>/logs/<kit>_<phase>.log
```

## Key State Files (Per-Kit)

| Kit | State files to read first |
|-----|--------------------------|
| TDD | `tdd-kit/CLAUDE.md` → `LAST_TOUCH.md` → `PRD.md` |
| Research | `research-kit/CLAUDE.md` → `RESEARCH_LOG.md` → `QUESTIONS.md` |
| Math | `mathematics-kit/CLAUDE.md` → `CONSTRUCTION_LOG.md` → `CONSTRUCTIONS.md` |

## Don't

- Don't `cd` into kit directories and run scripts directly — use `tools/kit`.
- Don't `cat` full log files — use `tools/query-log`.
- Don't dump transcripts or large outputs into capsules or interop requests — use file pointers.
- Don't skip reading capsules before reading logs. Capsules are the summary; logs are the detail.
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

## MCP Server (Optional)

```bash
source .orchestration-kit.env
tools/mcp-serve
```

Exposes: `orchestrator.run`, `orchestrator.request_create`, `orchestrator.pump`, `orchestrator.run_info`, `orchestrator.query_log`, `kit.tdd`, `kit.research_cycle`, `kit.research_full`, `kit.research_program`, `kit.math`, `kit.status`, `kit.runs`, `kit.capsule`, `kit.research_status`, `kit.active`, `kit.kill`

### Process Visibility

- **`kit.active`** — List all background processes launched by the MCP server. Returns `run_id`, `pid`, `status` (running/ok/failed), and `exit_code` for each. Use this for immediate visibility without dashboard dependency.
- **`kit.kill`** — Terminate a background process by `run_id`. Takes optional `signal` (SIGTERM default, SIGKILL option). Only operates on processes tracked by `kit.active` (cannot kill arbitrary PIDs). Returns `already_finished` if process exited, `signal_sent` on success.

### Run Visibility at Launch

Runs are now upserted into the dashboard SQLite DB at launch time (not just at completion). This means `kit.runs` with `status="running"` will show runs immediately after they start, not only after they finish.

### Orphan Detection

`kit.runs` responses include an `is_orphaned` boolean for running runs on the local host. If the PID recorded at launch is no longer alive, `is_orphaned=true`. This detects runs whose process was killed without clean shutdown.

See `docs/MCP_SETUP.md` for client configuration.

## Git Worktree Setup

When working in a git worktree (created via `git worktree add`), the `orchestration-kit/` directory will be empty because it's tracked as a gitlink. Use `tools/worktree-init` to bootstrap:

```bash
git worktree add ../project-slug -b feat/my-feature main
cd ../project-slug
orchestration-kit/tools/worktree-init   # or: <path-to-main>/orchestration-kit/tools/worktree-init
source .orchestration-kit.env
```

This replaces the empty `orchestration-kit/` with a symlink to the main checkout's copy, runs the installer, and patches `.orchestration-kit.env` with the correct `PROJECT_ROOT`.

## Validation

```bash
tools/smoke-run                              # end-to-end sanity check
tools/validate-capsules runs/<id>/capsules/   # capsule contract
tools/validate-manifests runs/<id>/manifests/ # manifest contract
```
