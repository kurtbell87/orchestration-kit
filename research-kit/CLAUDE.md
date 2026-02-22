@.kit/docs/research-engineering-practices.md
@.kit/RESEARCH_LOG.md
@.kit/QUESTIONS.md

## Current State (updated YYYY-MM-DD)

- **Infrastructure:** _e.g., Training pipeline working. Eval metrics implemented._
- **Experiments completed:** _e.g., 3 (2 refuted, 1 confirmed)._
- **Current question:** _e.g., P0 — Does reward shaping improve sample efficiency?_
- **Key entry point:** _e.g., `python train.py --config configs/baseline.yaml`_

## Don't

- Don't explore the codebase to "understand" it — read `RESEARCH_LOG.md` and directory `README.md` files instead.
- Don't read `QUESTIONS.md` unless you need the research agenda. `RESEARCH_LOG.md` has what's been tried.
- Don't run training or experiments outside the experiment pipeline.
- Don't check if dependencies are installed — they are.
- **Don't independently verify kit sub-agent work.** Each phase (survey, frame, run, read) spawns a dedicated sub-agent that does its own verification. Trust the exit code and capsule. Do NOT re-run experiments, re-read logs, re-check metrics, or otherwise duplicate work the sub-agent already did. Exit 0 = done. Exit 1 = read the capsule for the failure, don't grep the log.
- Don't read phase log files after a successful phase. Logs are for debugging failures only.

## Breadcrumb Maintenance (MANDATORY)

After every session that changes the codebase, you MUST maintain these navigation files:

1. **`RESEARCH_LOG.md`** — Append results of any completed experiment. This is the cumulative knowledge base.
2. **`QUESTIONS.md`** — Update question statuses and move answered questions to the answered section.
3. **`CLAUDE.md` "Current State" section** — Update experiment counts and current question.
4. **Directory `README.md` files** — If you add/rename/delete files in a directory, update that directory's README.md.

The goal: a new agent session should orient itself by reading only `CLAUDE.md` → `RESEARCH_LOG.md` → relevant directory `README.md`.

## How You Work: Research Experiment Workflow (MANDATORY)

This project uses a **strict SURVEY-FRAME-RUN-READ-LOG cycle** for all research experiments. You MUST follow this process. You do NOT implement experiments directly — you orchestrate the pipeline.

### Step 0: Identify the Question

Read `RESEARCH_LOG.md` and `QUESTIONS.md` to determine the next question to investigate. Pick the highest-priority open question.

### Step 1: SURVEY — Review Prior Work

```bash
./experiment.sh survey "your research question"
```

**Output is a compact summary only.** The command returns the last agent message (≤500 chars), exit code, and log path. Full output is in `$EXP_LOG_DIR/survey.log` — read it if you need more detail. (`EXP_LOG_DIR` defaults to `/tmp/exp-<project-name>/`.)

This spawns a dedicated survey agent that reviews:
- Prior experiments and their outcomes
- Existing codebase infrastructure
- Known failure modes and pitfalls

The survey agent produces a briefing document but does NOT design experiments or write code.

### Step 2: FRAME — Design the Experiment

```bash
./experiment.sh frame experiments/exp-NNN-name.md
```

**Output is a compact summary only.** The command returns the last agent message (≤500 chars), exit code, and log path. Full output is in `$EXP_LOG_DIR/frame.log` — read it if you need more detail.

This spawns a dedicated experiment design agent that:
- Reads the survey output
- Writes a rigorous experiment spec with falsifiable hypothesis and pre-committed success criteria
- Does NOT write any implementation code

**The spec becomes immutable once RUN begins.** Success criteria cannot be changed after seeing results.

### Step 3: RUN — Execute the Experiment

```bash
./experiment.sh run experiments/exp-NNN-name.md
```

**Output is a compact summary only.** The command returns the last agent message (≤500 chars), exit code, and log path. Full output is in `$EXP_LOG_DIR/run.log` — read it if you need more detail.

This spawns a dedicated execution agent. The experiment spec is OS-locked (read-only). The agent:
- Implements whatever code is needed
- Reproduces the baseline first
- Executes the full protocol
- Writes ALL metrics to `results/exp-NNN/metrics.json`
- Does NOT interpret results

**IMPORTANT: Run in background and block-wait.** Training can take a long time. Use `run_in_background: true`, then block-wait with `TaskOutput(block: true, timeout: 600000)`.

### Step 4: READ — Analyze Results

```bash
./experiment.sh read experiments/exp-NNN-name.md
```

**Output is a compact summary only.** The command returns the last agent message (≤500 chars), exit code, and log path. Full output is in `$EXP_LOG_DIR/read.log` — read it if you need more detail.

This spawns a dedicated analysis agent. Metrics are locked (read-only). The agent:
- Evaluates each success criterion (pass/fail, no partial credit)
- Renders a verdict: CONFIRMED, REFUTED, or INCONCLUSIVE
- Addresses EVERY metric in the spec
- Identifies confounds and alternative explanations
- Proposes follow-up experiments
- Updates RESEARCH_LOG.md

### Step 5: LOG — Commit Results

```bash
./experiment.sh log experiments/exp-NNN-name.md
```

Creates a feature branch, commits all results, and opens a PR.

### Shortcuts

```bash
# Frame through log (skip survey — use when you've already surveyed):
./experiment.sh cycle experiments/exp-NNN-name.md

# Full pipeline including survey:
./experiment.sh full "research question" experiments/exp-NNN-name.md
```

### Program Mode (Auto-advancing)

For multi-question research programs, use program mode instead of manual cycles:

```bash
# Auto-advance through all open questions:
./experiment.sh program

# Limit cycles:
./experiment.sh program --max-cycles 5

# Preview what would run (no execution):
./experiment.sh program --dry-run

# Check status at any time:
./experiment.sh status

# Generate synthesis report manually:
./experiment.sh synthesize
```

Program mode automatically:
- Picks the highest-priority unblocked question from QUESTIONS.md
- Runs a full SURVEY-FRAME-RUN-READ-LOG cycle
- Records results in `program_state.json`
- Stops when: all questions resolved, max cycles reached, GPU budget exhausted, or a handoff is emitted

### Handoff Protocol

When the READ agent detects that a research question requires infrastructure work (environment code changes, new dependencies, bug fixes), it creates `HANDOFF.md`. The program loop pauses until the handoff is resolved.

```bash
# After resolving the handoff:
./experiment.sh complete-handoff    # Archives to handoffs/completed/

# Resume the program loop:
./experiment.sh program
```

**Tower scope:** Research can change hyperparameters, algorithms, experiment scripts, and configs. Anything that could break other experiments if done wrong (shared env code, interfaces, dependencies) requires a handoff.

### Cross-Kit Subprocess Launching (Optional)

Research can optionally request work from other kits (TDD, Math) via the interop queue. Use this instead of (or alongside) the handoff mechanism when you want automated execution rather than a manual pause.

**When to use:**
- Need TDD to build or fix infrastructure code (new modules, bug fixes, dependency upgrades)
- Need Math to formalize or verify a mathematical construction referenced by your research
- Need a status check from another kit before deciding the next experiment

**How to create a request:**

```bash
tools/kit request \
  --from research --from-phase <current_phase> \
  --to tdd --action tdd.full \
  --run-id <current_run_id> \
  --arg "docs/fix-env-interface.md" \
  --must-read "LAST_TOUCH.md" \
  --reasoning "Research blocked: env.step() returns wrong shape, need TDD fix" \
  --json
```

**How to execute it:**

```bash
tools/pump --once --request <request_id> --json
```

The response lands in `interop/responses/<request_id>.json` with status `ok|blocked|failed`, child run pointers, and deliverables.

**Available cross-kit actions:**
- `tdd.red`, `tdd.green`, `tdd.refactor`, `tdd.ship`, `tdd.full` — TDD phases
- `math.survey`, `math.specify`, `math.construct`, `math.formalize`, `math.prove`, `math.full`, `math.status` — Math phases
- `research.status` — status check from another kit back to research

**Key parameters:**
- `--must-read`: Files the child agent MUST read for context
- `--allowed-path`: Glob patterns restricting what the child can read (isolation)
- `--deliverable`: Expected output globs (e.g., `"src/env/*.py"`)
- `--reasoning`: 1-3 sentence justification (appears in the DAG and audit trail)

**Cross-kit vs. handoff:** Use cross-kit requests for well-scoped, automatable tasks (e.g., "run TDD full on this spec"). Use the handoff mechanism when the work is ambiguous and needs human or orchestrator judgment.

### Critical Rules

- **You are the orchestrator, not the implementor.** Steps 1-4 MUST use `./experiment.sh` commands.
- **One experiment per cycle.** Don't batch multiple hypotheses.
- **Failure is a first-class outcome.** REFUTED experiments are valuable. Continue to the next question.
- **Always run ./experiment.sh with `run_in_background: true`**, then block-wait with `TaskOutput(block: true, timeout: 600000)`.
- **Never modify metrics after the RUN phase.** The numbers are sacred.
- **Never modify the spec after the FRAME phase.** The contract is sacred.
