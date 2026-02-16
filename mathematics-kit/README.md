# Claude Mathematics Kit

A toolkit that automates creating formally verified mathematical constructions using Claude Code and Lean4/Mathlib. Specify a domain + required properties, and an agent pipeline proposes a mathematical construction, then iteratively proves it correct via `lake build` cycles.

## Prerequisites

- **elan** / **Lean4**: Install via `curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh`
- **Claude Code**: `npm install -g @anthropic-ai/claude-code`
- **gh CLI** (optional): For the LOG phase PR creation

## Installation

```bash
cd your-lean-project
/path/to/mathematics-kit/install.sh
```

The installer will:
1. Copy all kit files (safe, won't overwrite existing)
2. Check for `lake` / `elan` toolchain
3. Initialize a Lean4+Mathlib project if no lakefile exists
4. Run `lake update` to fetch Mathlib
5. **Mathlib build caching**: Detects Mathlib dependency, checks toolchain version match, runs `lake exe cache get` for precompiled oleans
6. Verify `lake build` succeeds (incremental after cache)
7. Create `specs/` and `results/` directories

## 8-Phase Pipeline

```
SURVEY → SPECIFY → CONSTRUCT → FORMALIZE → PROVE → POLISH → AUDIT → LOG
  │         │          │           │          │        │        │      │
  │         │          │           │          │        │        │      └─ git commit + PR
  │         │          │           │          │        │        └─ .lean files LOCKED, review coverage
  │         │          │           │          │        └─ Mathlib style compliance (doc strings, #lint)
  │         │          │           │          └─ spec LOCKED, fill in sorrys via lake build loop
  │         │          │           └─ write .lean defs + theorems (all sorry)
  │         │          └─ informal math: definitions, theorems, proof sketches
  │         └─ precise property requirements (no Lean4 syntax)
  └─ survey Mathlib, domain literature, existing formalizations
```

### Phase Details

| Phase | Agent | Writes | Reads | Enforcement |
|-------|-------|--------|-------|-------------|
| **SURVEY** | Domain Surveyor | nothing (read-only) | Mathlib, specs, project | No file writes |
| **SPECIFY** | Spec Writer | `specs/*.md`, `DOMAIN_CONTEXT.md` | survey output | No `.lean` code |
| **CONSTRUCT** | Mathematician | `specs/construction-*.md` | specs, domain context | No `.lean` code |
| **FORMALIZE** | Lean4 Expert | `.lean` files (all `sorry`) | specs, construction docs | No real proofs |
| **PROVE** | Proof Engineer | `.lean` proofs (Edit only) | specs (locked), `.lean` | Spec is `chmod 444` |
| **POLISH** | Style Expert | `.lean` docs/formatting (Edit only) | `.lean`, `STYLE_GUIDE.md` | No proof/signature changes |
| **AUDIT** | Auditor | `CONSTRUCTION_LOG.md`, `REVISION.md` | everything (locked) | `.lean` files `chmod 444` |
| **LOG** | — | git commit + PR | — | — |

## Usage

### Individual Phases
```bash
./math.sh survey    specs/my-construction.md
./math.sh specify   specs/my-construction.md
./math.sh construct specs/my-construction.md
./math.sh formalize specs/my-construction.md
./math.sh prove     specs/my-construction.md
./math.sh polish    specs/my-construction.md
./math.sh audit     specs/my-construction.md
./math.sh log       specs/my-construction.md
```

### Full Pipeline
```bash
./math.sh full specs/my-construction.md
```

Runs all 8 phases with automatic revision loop support.

### Program Mode
```bash
# Edit CONSTRUCTIONS.md with your construction queue
./math.sh program
./math.sh program --max-cycles 10
./math.sh program --resume   # Resume from first FAILED/BLOCKED construction
```

Auto-advances through constructions listed in `CONSTRUCTIONS.md`. Supports dependency chains via the `Depends On` column — constructions are executed in topological order, and downstream constructions are automatically blocked when dependencies fail.

### Monitoring a Running Phase

Each phase streams structured JSON logs to `$MATH_LOG_DIR/{phase}.log` (defaults to `/tmp/math-<project-name>/`). The built-in `watch` command parses these into a live dashboard:

```bash
./math.sh watch prove               # Live-tail the prove phase
./math.sh watch prove --resolve     # One-shot summary
./math.sh watch prove --verbose     # Show build output (lake errors, tactic state)
./math.sh watch                     # Auto-detect the most recent phase
```

The dashboard shows elapsed time, model, tool call counts, files read/written/edited, agent narration, and build output.

Phase runners redirect all sub-agent output to disk only (`$MATH_LOG_DIR/{phase}.log`). Stdout receives a compact summary — the last agent message, exit code, and log path — so the orchestrator's context window stays clean. This is especially important for the mathematics kit, where `lake build` output and Lean4 type errors can be extremely verbose. If you need more detail, grep or read the log file directly.

#### What the Orchestrator Sees

Each `./math.sh` phase returns **only** a compact summary on stdout:

- **Phase output** goes to `$MATH_LOG_DIR/{phase}.log` (not stdout)
- **Stdout receives:** last agent message (≤500 chars) + exit code + log path
- **To get more detail:** grep or read the log file (pull-based)
- **The watch script** reads logs directly and is unaffected by this

This is intentional — it prevents `lake build` verbosity and Lean4 type expansion dumps from flooding the orchestrator's context window. The orchestrator should treat the summary as the primary signal and only pull from the log when diagnosing a failure.

### Utilities
```bash
./math.sh status                    # Sorry count, axiom count, build status
```

### Aliases
```bash
source math-aliases.sh
math-polish specs/my-construction.md  # Mathlib style compliance
math-status                         # Quick status
math-sorrys                         # Sorry count per file
math-axioms                         # Scan for axiom/unsafe/native_decide
math-unlock                         # Emergency: restore file permissions
```

## Defense-in-Depth (3 Layers)

| Layer | Mechanism | What it enforces |
|-------|-----------|------------------|
| **Prompts** | Phase-specific system prompts | Agent "wants" to stay in role |
| **OS permissions** | `chmod 444` on specs (PROVE) and `.lean` files (AUDIT) | OS blocks writes even if agent tries |
| **Hook** | `.claude/hooks/pre-tool-use.sh` | Blocks `axiom`, `unsafe`, `native_decide`, `chmod`/`sudo`, `git revert`, phase-specific writes |

### Universal Blocks (All Phases)
- `axiom` declarations
- `unsafe` code
- `native_decide` usage
- `admit` usage
- `chmod` / `sudo` / permission changes
- Destructive git commands (`revert`, `checkout`, `restore`, `stash`, `reset`)

## Revision Cycles

If the PROVE or AUDIT phase discovers the construction is unprovable, the agent creates `REVISION.md` with:
- `restart_from:` — which phase to restart from (CONSTRUCT or FORMALIZE)
- Problem description and evidence
- Suggested fix

The `full` pipeline automatically handles up to `MAX_REVISIONS` (default: 3) revision cycles. Previous attempts are archived in `results/revisions/`.

Errors are classified automatically (TYPE_MISMATCH, UNKNOWN_IDENT, TACTIC_FAIL, TIMEOUT, UNIVERSE_INCOMPAT) to guide the prover agent's strategy. Failed approaches are recorded in `DOMAIN_CONTEXT.md` under `## DOES NOT APPLY` to prevent re-attempting known-bad strategies across revision cycles.

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LEAN_DIR` | `.` | Lean4 project root |
| `SPEC_DIR` | `specs` | Spec & construction docs directory |
| `LAKE_BUILD` | `lake build` | Build command |
| `MATH_LOG_DIR` | `/tmp/math-<project>` | Log directory (auto-derived from repo name) |
| `MAX_REVISIONS` | `3` | Max revision cycles per construction |
| `MAX_PROGRAM_CYCLES` | `20` | Max cycles in program mode |
| `MATH_AUTO_MERGE` | `false` | Auto-merge PR after creation |
| `MATH_BASE_BRANCH` | `main` | Base branch for PRs |

## Utility Scripts

| Script | Description |
|--------|-------------|
| `scripts/mathlib-search.sh` | Search Mathlib source tree for definitions, theorems, and typeclasses |
| `scripts/lean-error-classify.sh` | Classify Lean4 build errors (TYPE_MISMATCH, UNKNOWN_IDENT, TACTIC_FAIL, TIMEOUT, UNIVERSE_INCOMPAT) |
| `scripts/lean-error-summarize.sh` | Condense Lean4 build errors — strips type expansions, extracts goals, caps at 40 lines |
| `scripts/lake-timed.sh` | `lake` wrapper that records build timing to `lake-timing.jsonl` |
| `scripts/mathlib-lint.sh` | Standalone Mathlib style checker (copyright, docstrings, line length, formatting) |
| `scripts/resolve-deps.py` | Topological sort of construction dependencies from CONSTRUCTIONS.md |

## Metrics

After a full pipeline run, `results/metrics.jsonl` contains per-phase timing data and `results/lake-timing.jsonl` contains per-build timing. A summary table is printed at the end of `./math.sh full`.

## File Structure

```
your-project/
├── math.sh                           # Main orchestrator
├── math-aliases.sh                   # Shell aliases
├── CLAUDE.md                         # Workflow instructions for Claude
├── CONSTRUCTIONS.md                  # Program-mode queue (with dependency chains)
├── CONSTRUCTION_LOG.md               # Audit trail
├── DOMAIN_CONTEXT.md                 # Domain knowledge + Mathlib mappings + negative knowledge
├── specs/                            # Spec & construction docs
│   ├── my-construction.md
│   └── construction-my-construction.md
├── results/                          # Archived results
│   ├── my-construction/
│   ├── metrics.jsonl                 # Phase timing data
│   ├── lake-timing.jsonl             # Build timing data
│   └── revision-metrics.json         # Revision cycle tracking
├── scripts/
│   ├── math-watch.py                 # Live monitoring
│   ├── mathlib-search.sh             # Mathlib source search
│   ├── lean-error-classify.sh        # Error classifier
│   ├── lean-error-summarize.sh       # Error summarizer
│   ├── lake-timed.sh                 # Timed lake wrapper
│   └── resolve-deps.py              # Dependency resolver
├── templates/
│   └── construction-spec.md          # Spec template
├── .claude/
│   ├── settings.json                 # Hook registration
│   ├── hooks/
│   │   └── pre-tool-use.sh           # Multi-phase enforcement
│   └── prompts/
│       ├── math-survey.md
│       ├── math-specify.md
│       ├── math-construct.md
│       ├── math-formalize.md
│       ├── math-prove.md
│       ├── math-polish.md
│       └── math-audit.md
└── lakefile.lean                     # Lean4 project config
```
