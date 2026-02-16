# Claude Mathematics Kit — Workflow Instructions

## 8-Phase Pipeline
This project uses a formal mathematics verification pipeline:
```
SURVEY → SPECIFY → CONSTRUCT → FORMALIZE → PROVE → POLISH → AUDIT → LOG
```

## Phase Rules (MUST follow)

### SURVEY (read-only)
- Read Mathlib docs, existing formalizations, spec files
- Run `#check` / `#print` commands to explore Mathlib
- Use `./scripts/mathlib-search.sh` for targeted Mathlib searches
- Do NOT create or modify any files

### SPECIFY (spec files only)
- Write/edit files in `specs/` and `DOMAIN_CONTEXT.md`
- No `.lean` code

### CONSTRUCT (markdown only)
- Write construction documents in `specs/`
- No `.lean` code

### FORMALIZE (.lean files, ALL sorry)
- Write `.lean` definitions and theorem statements
- ALL proof bodies MUST be `sorry` — no real proofs
- Run `lake build` to verify types

### PROVE (fill sorrys only)
- Use **Edit** (not Write) on `.lean` files
- Replace `sorry` with real proofs
- Do NOT modify theorem signatures or definitions
- Spec files are READ-ONLY
- Use `./scripts/lean-error-classify.sh` to classify build errors
- Use `./scripts/lean-error-summarize.sh` for condensed error output
- Record failed approaches in DOMAIN_CONTEXT.md under `## DOES NOT APPLY`
- Generate MWEs in `scratch/MWE.lean` for opaque errors
- Create `REVISION.md` if construction is unprovable

### POLISH (style compliance)
- Use **Edit** (not Write) on `.lean` files (except `scratch/*.lean`)
- Add doc strings, module docstrings, copyright headers
- Fix formatting: line length, `Type*`, `fun` not `λ`
- Run `#lint` via `scratch/lint_check.lean`
- Flag naming convention violations in `CONSTRUCTION_LOG.md` (do NOT rename)
- Do NOT modify proof bodies or signatures

### AUDIT (read-only for .lean)
- All `.lean` files are READ-ONLY
- Write only `CONSTRUCTION_LOG.md` and `REVISION.md`
- Verify zero sorry/axiom/native_decide

### LOG (commit + PR)
- Commit results, create PR, archive

## Phase Output (Disk-Only Logging)

All `./math.sh` phase commands return **only** a compact summary on stdout: the last agent message (≤500 chars), exit code, and log path. Full output — including `lake build` errors, tactic state, and type mismatches — goes to `$MATH_LOG_DIR/{phase}.log` (defaults to `/tmp/math-<project-name>/`). If a phase fails or you need more detail, read the log:

```bash
# Examples:
cat $MATH_LOG_DIR/prove.log     # Full prove phase transcript
cat $MATH_LOG_DIR/formalize.log # Full formalize phase transcript
```

This prevents verbose Lean4 output from flooding the orchestrator's context window.

## Cross-Kit Subprocess Launching (Optional)

Math can optionally request work from other kits (TDD, Research) via the interop queue. Use this when your formalization work surfaces needs that belong to another domain.

**When to use:**
- Need TDD to build tooling or scripts that support the math pipeline (e.g., custom Lean4 linters, build helpers)
- Need Research to investigate an empirical question that informs a construction choice (e.g., "which Mathlib approach has fewer sorry dependencies?")
- Need a status check from another kit

**How to create a request:**

```bash
tools/kit request \
  --from math --from-phase <current_phase> \
  --to tdd --action tdd.full \
  --run-id <current_run_id> \
  --arg "docs/lean-error-parser.md" \
  --must-read "LAST_TOUCH.md" \
  --reasoning "Math needs a better error parser for opaque Lean4 type errors" \
  --json
```

**How to execute it:**

```bash
tools/pump --once --request <request_id> --json
```

The response lands in `interop/responses/<request_id>.json` with status `ok|blocked|failed`, child run pointers, and deliverables.

**Available cross-kit actions:**
- `tdd.red`, `tdd.green`, `tdd.refactor`, `tdd.ship`, `tdd.full` — TDD phases
- `research.survey`, `research.frame`, `research.run`, `research.read`, `research.cycle`, `research.full`, `research.status` — Research phases
- `math.status` — status check from another kit back to math

**Key parameters:**
- `--must-read`: Files the child agent MUST read for context
- `--allowed-path`: Glob patterns restricting what the child can read (isolation)
- `--deliverable`: Expected output globs
- `--reasoning`: 1-3 sentence justification (appears in the DAG and audit trail)

## Universal Rules
- **NEVER** use `axiom`, `unsafe`, `native_decide`, or `admit`
- **NEVER** use `chmod`, `sudo`, or permission-modifying commands
- **NEVER** use destructive git commands (`revert`, `checkout`, `restore`)
- **Don't independently verify kit sub-agent work.** Each phase (survey, specify, construct, formalize, prove, polish, audit) spawns a dedicated sub-agent that does its own verification. Trust the exit code and capsule. Do NOT re-run `lake build`, re-read logs, or otherwise duplicate work the sub-agent already did. Exit 0 = done. Exit 1 = read the capsule for the failure, don't grep the log.
- Don't read phase log files after a successful phase. Logs are for debugging failures only.
- Check current phase: `echo $MATH_PHASE`

## Breadcrumb Maintenance
After each significant action, update CONSTRUCTION_LOG.md with progress notes.

## Key Files
- `math.sh` — orchestrator (run phases, check status)
- `specs/` — specification and construction documents
- `results/` — archived construction results
- `DOMAIN_CONTEXT.md` — Mathlib mappings, domain knowledge, and negative knowledge (DOES NOT APPLY)
- `CONSTRUCTION_LOG.md` — audit trail
- `CONSTRUCTIONS.md` — program-mode construction queue (with dependency chains)
- `STYLE_GUIDE.md` — Mathlib4 style & review standards reference

## Utility Scripts
- `scripts/mathlib-search.sh` — search Mathlib source for defs/thms/instances
- `scripts/lean-error-classify.sh` — classify Lean4 errors (TYPE_MISMATCH, UNKNOWN_IDENT, etc.)
- `scripts/lean-error-summarize.sh` — condense Lean4 errors for agent consumption
- `scripts/lake-timed.sh` — `lake` wrapper that records build timing
- `scripts/mathlib-lint.sh` — standalone Mathlib style checker (copyright, docstrings, line length)
