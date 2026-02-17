## Current State (updated 2026-02-16)

- **Build:** `build/` is current. 204/205 unit tests pass (1 disabled). 14 integration tests excluded via `--label-exclude integration`.
- **Dependencies:** All via CMake FetchContent (databento-cpp, libtorch, xgboost, GTest).
- **Phases 1-6 DONE:** book_builder, feature_encoder, oracle_labeler, trajectory_builder, MLP, GBT, CNN — full TDD cycles complete.
- **Phase 7 (integration-overfit):** Red + green exit 0. Refactor NOT yet done.
- **Phase 8 SKIPPED:** SSM model requires CUDA + Python. No GPU available.
- **Next task:** Run refactor for integration-overfit → breadcrumbs → ship.
- **Key entry point:** `cmake --build build -j12 && cd build && ctest --output-on-failure --label-exclude integration`

## Don't

- Don't verify the build or run tests unless asked or unless you changed code that affects them.
- Don't explore the codebase to "understand" it -- read `LAST_TOUCH.md` and directory `README.md` files instead.
- Don't read `PRD.md` unless you need requirements for a new feature. `LAST_TOUCH.md` has the current state.
- Don't check if dependencies are installed -- they are.
- Don't read source files to understand architecture -- read the `README.md` in each directory first.
- **Don't independently verify phase-agent work.** Each phase (red, green, refactor) verifies itself. Trust exit code + phase summary. Do NOT re-run tests, re-read logs, or re-check build output unless a phase failed.
- Don't read phase log files after a successful phase. Logs are for debugging failures only.
- Don't dump full logs into context. Use bounded inspection (`./tdd.sh watch <phase> --resolve` first).

## Breadcrumb Maintenance (MANDATORY)

After every session that changes the codebase, you MUST maintain these navigation files so the next agent starts fast:

1. **`LAST_TOUCH.md`** -- Update the "What to do next" and "Key files" sections. This is a cold-start briefing, not a journal. Keep it actionable and concise.
2. **`AGENTS.md` "Current State" section** -- Update build status, test counts, and next task.
3. **Directory `README.md` files** -- If you add/rename/delete files in a directory, update that directory's `README.md`. If a directory doesn't have one, create it.
4. **Spec docs in `docs/`** -- Archived automatically by `./tdd.sh ship`. The spec is deleted from the working tree but preserved in git history. Don't manually delete specs before shipping.

The goal: a new agent session should be able to orient itself by reading only `AGENTS.md` -> `LAST_TOUCH.md` -> relevant directory `README.md`, without grepping or exploring.

## How You Work: TDD Workflow (MANDATORY)

This project uses **strict red-green-refactor TDD with mandatory breadcrumbs**. You MUST follow this process for ALL new work. You do NOT implement code directly -- you orchestrate the TDD pipeline.

### Backend Selection (Claude or Codex)

Default backend is Claude Code:

```bash
./tdd.sh red docs/<feature>.md
```

To run the same phases with Codex CLI:

```bash
TDD_AGENT_BIN=codex ./tdd.sh red docs/<feature>.md
```

You can set this once per shell:

```bash
export TDD_AGENT_BIN=codex
```

### Step 1: Write a Spec

Read `LAST_TOUCH.md` to determine what needs to be built next. Then read `PRD.md` for requirements. Write a focused spec file to `docs/<feature>.md` describing:
- What to build (requirements, interfaces, expected behavior)
- Edge cases and error conditions
- Acceptance criteria

The spec should be scoped to a single deliverable unit of work. If the next task in `LAST_TOUCH.md` is large, break it into smaller specs and run the TDD cycle for each one sequentially.

### Step 2: RED -- Write Failing Tests

**You MUST run this command. Do NOT write tests yourself.**

```bash
./tdd.sh red docs/<feature>.md
```

This spawns a dedicated test-author agent that reads the spec and writes failing tests. It cannot touch implementation files.

**Output is compact by design.** The command returns a short phase summary, exit code, and log path. Full output is in `$TDD_LOG_DIR/red.log` (default `/tmp/tdd-<project>/`).

### Step 3: GREEN -- Implement to Pass

**You MUST run this command. Do NOT write implementation code yourself.**

```bash
./tdd.sh green
```

This spawns a dedicated implementation agent. Test files are OS-locked (read-only). The agent writes the minimum code to make all tests pass.

**Output is compact by design.** Full details are in `$TDD_LOG_DIR/green.log`.

### Step 4: REFACTOR -- Improve Quality

**You MUST run this command. Do NOT refactor code yourself.**

```bash
./tdd.sh refactor
```

This spawns a dedicated refactoring agent that improves code quality while keeping all tests green.

**Output is compact by design.** Full details are in `$TDD_LOG_DIR/refactor.log`.

### Step 5: BREADCRUMBS -- Update Navigation Docs

**You MUST run this command before shipping.**

```bash
./tdd.sh breadcrumbs docs/<feature>.md
```

This updates `CLAUDE.md`, `AGENTS.md`, `LAST_TOUCH.md`, and affected directory `README.md` files so the next session can cold-start quickly.

### Step 6: SHIP -- Commit, PR, Archive

After refactor, ship the results:

```bash
./tdd.sh ship docs/<feature>.md
```

This creates a feature branch (`tdd/<feature>`), commits all changes, opens a PR, and deletes the spec file from the working tree (preserved in git history). `ship` runs breadcrumbs first as a safety check.

Configure via env vars:
- `TDD_AUTO_MERGE=true` -- auto-merge the PR after creation
- `TDD_DELETE_BRANCH=true` -- delete the feature branch after merge
- `TDD_BASE_BRANCH=main` -- base branch for PRs

Or run all phases in one command: `./tdd.sh full docs/<feature>.md`

### Critical Rules

- **You are the orchestrator, not the implementor.** Steps 2-5 MUST use `./tdd.sh` commands.
- **Step 1 is the only step where you write files directly** (the spec).
- **One spec per cycle.** Don't try to spec everything at once.
- If a `./tdd.sh` phase fails, diagnose and re-run the phase. Do not manually patch implementation code outside the pipeline.
